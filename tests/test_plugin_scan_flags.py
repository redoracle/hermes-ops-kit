"""`plugin scan` must reject options it does not understand instead of ignoring them."""

from __future__ import annotations

import pytest

from hermes_ops_kit.security.plugin_scanner import cli


@pytest.fixture()
def no_scan(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("scan ran")

    monkeypatch.setattr(cli, "_resolve_plugin_path", boom, raising=False)
    return calls


@pytest.mark.parametrize(
    "args",
    [
        ["--bogus"],
        ["--no-behavior"],
        ["--profile"],
        ["--plugin"],
        ["--category"],
        ["extra"],
    ],
)
def test_unknown_or_incomplete_options_are_refused(args, capsys, no_scan):
    assert cli.handle_plugin(["scan", *args]) == 2
    out = capsys.readouterr().out
    assert "unknown or incomplete option" in out and "Accepted:" in out
    assert no_scan == []


def test_documented_flags_are_still_parsed(capsys):
    """Every flag that worked before still gets past the parser (empty plugin dir)."""
    code = cli.handle_plugin(
        [
            "scan",
            "--profile",
            "ci",
            "--category",
            "secrets",
            "--json",
            "--force",
            "--no-semgrep",
            "--no-bandit",
            "--no-gitleaks",
            "--verbose",
        ]
    )
    assert code in (0, 1)
    assert "unknown or incomplete" not in capsys.readouterr().out


@pytest.fixture()
def leaky_home(tmp_path, monkeypatch):
    """A temporary Hermes home holding one plugin with a shell-execution finding."""
    from hermes_ops_kit import ops_config_io

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    plugin = tmp_path / "plugins" / "demo-leaky"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: demo-leaky\nversion: 1.0.0\n")
    (plugin / "__init__.py").write_text("import os\nos.system('echo hi')\n")
    return tmp_path


def _scan(capsys, *extra):
    cli.handle_plugin(
        [
            "scan",
            "--plugin",
            "demo-leaky",
            "--force",
            "--no-semgrep",
            "--no-bandit",
            "--no-gitleaks",
            *extra,
        ]
    )
    return capsys.readouterr().out


def test_summary_prints_one_line_per_plugin(leaky_home, capsys):
    out = _scan(capsys, "--summary")
    assert "FINDINGS" in out  # the table header
    assert any(
        line.split()[1:2] == ["demo-leaky"] and line.split()[-1].isdigit()
        for line in out.splitlines()
    )  # one row for the plugin, ending in its findings count
    assert "[shell-execution]" not in out


def test_explain_shows_remediation_and_the_commands_to_act(leaky_home, capsys):
    out = _scan(capsys, "--explain")
    assert "[shell-execution]" in out
    assert "plugin override demo-leaky shell-execution allow" in out
    assert "plugin approve --finding demo-leaky:policy:shell-execution:" in out


def test_tool_selects_exactly_the_named_tools(leaky_home, monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        raise RuntimeError("stop")

    monkeypatch.setattr(cli, "scan_plugin", lambda **kw: fake(**kw))
    cli.handle_plugin(
        ["scan", "--plugin", "demo-leaky", "--tool", "semgrep", "--tool", "bandit"]
    )
    assert (seen["use_semgrep"], seen["use_bandit"], seen["use_gitleaks"]) == (
        True,
        True,
        False,
    )


def test_unsupported_tool_is_an_error(capsys, no_scan):
    assert cli.handle_plugin(["scan", "--tool", "trufflehog"]) == 2
    assert "Supported: semgrep, bandit, gitleaks" in capsys.readouterr().out


def test_cache_stats_reports_the_database(leaky_home, capsys):
    import json

    assert cli.handle_plugin(["cache", "stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["result"]
    assert data["total_entries"] == 0 and data["db_path"].startswith(str(leaky_home))
    assert "size_bytes" in data


def test_rules_list_counts_bundled_rules(capsys):
    import json

    assert cli.handle_plugin(["rules", "list", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert set(result["files"]) >= {"hermes-critical.yaml", "hermes-warning.yaml"}
    assert all(n > 0 for n in result["files"].values())
    assert set(result["tools"]) == {"semgrep", "gitleaks", "bandit"}


def test_config_show_reports_the_profiles_that_run(leaky_home, capsys):
    import json

    assert cli.handle_plugin(["config", "show", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert {"startup", "install", "update", "manual", "ci"} <= set(result["profiles"])
    assert result["profiles"]["manual"]["block_on"] == ["critical"]
    assert result["config_file_is_read"] is False
    assert result["config_file"].startswith(str(leaky_home))


def test_config_without_show_is_a_usage_error(capsys):
    assert cli.handle_plugin(["config", "set", "a", "b"]) == 2
    assert "plugin config show" in capsys.readouterr().out


def test_rules_update_now_lists_the_bundled_files(capsys):
    import json

    assert cli.handle_plugin(["rules", "update", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["custom_rules"] == [
        "hermes-critical.yaml",
        "hermes-warning.yaml",
    ]


def test_rules_update_hint_names_a_directory_that_exists(capsys):
    import json
    import os

    assert cli.handle_plugin(["rules", "update", "--json"]) == 0
    message = json.loads(capsys.readouterr().out)["result"]["message"]
    path = message.split("--config ")[1].split("`")[0]
    assert os.path.isdir(path) and os.listdir(path)


@pytest.mark.parametrize(
    "args",
    [
        ["approve", "--all", "-h"],
        ["approve", "foo", "-h"],
        ["approve", "foo", "--help"],
        ["revoke", "--all", "--help"],
        ["disable", "foo", "-h"],
        ["enable", "foo", "-h"],
        ["block", "foo", "-h"],
        ["override", "foo", "rule", "allow", "-h"],
        ["policy", "import", "f.json", "--apply", "-h"],
    ],
)
def test_help_anywhere_never_changes_plugin_state(args, capsys, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a policy mutation ran")

    for name in (
        "approve_all",
        "approve_plugin",
        "approve_finding",
        "approve_category",
        "revoke_all",
        "revoke_plugin",
        "revoke_finding",
        "disable_plugin",
        "enable_plugin",
        "block_plugin",
        "set_rule_override",
        "remove_rule_override",
    ):
        monkeypatch.setattr(cli, name, boom, raising=False)
    assert cli.handle_plugin(args) == 0
    assert "Plugin Security Scanner" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["approve", "--all", "--dry-run"],
        ["revoke", "--all", "--dry-run"],
        ["approve", "foo", "-x"],
        ["approve", "foo", "--category"],
        ["approve", "foo", "--notes", "--all"],
        ["disable", "foo", "--force"],
        ["override", "foo", "--remove", "--bogus"],
        ["cache", "show", "--bogus"],
        ["rules", "list", "--bogus"],
        ["policy", "--bogus"],
    ],
)
def test_unknown_plugin_flags_are_refused_before_anything_changes(
    args, capsys, monkeypatch
):
    """`plugin approve --all --dry-run` used to approve everything: there is no such option."""

    def boom(*_a, **_k):
        raise AssertionError("a policy mutation ran")

    for name in ("approve_all", "approve_plugin", "revoke_all", "disable_plugin"):
        monkeypatch.setattr(cli, name, boom, raising=False)
    assert cli.handle_plugin(args) == 2
