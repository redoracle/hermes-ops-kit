"""gitleaks failures must never read as "no leaks"."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from hermes_ops_kit.security.plugin_scanner.categories import secrets

FAKE_PAT = "ghp_abc123" + "Q9xZ7mK2vB5nR8tY3wL6cD1fH4jS0p"


@pytest.fixture()
def plugin(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "plugin.yaml").write_text("name: plugin\nversion: 1.0.0\n")
    return root


def _fake_run(returncode=0, stdout="", stderr="", report=None):
    def run(cmd, **kwargs):
        if report is not None:
            path = cmd[cmd.index("--report-path") + 1]
            with open(path, "w") as f:
                f.write(report)
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return run


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_real_gitleaks_reports_a_planted_token(plugin):
    (plugin / "leak.py").write_text(f'TOKEN = "{FAKE_PAT}"\n')
    findings = secrets._run_gitleaks(str(plugin))
    assert [f["RuleID"] for f in findings] == ["github-pat"]
    assert FAKE_PAT not in str(findings)  # --redact keeps the secret out of the report


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_real_gitleaks_on_a_clean_plugin_returns_nothing(plugin):
    (plugin / "ok.py").write_text("x = 1\n")
    assert secrets._run_gitleaks(str(plugin)) == []


@pytest.mark.parametrize(
    "run",
    [
        _fake_run(returncode=126, stderr="Error: unknown flag: --format"),
        _fake_run(returncode=2, stderr="boom"),
        _fake_run(returncode=1, report="not json"),
        _fake_run(returncode=1, report='{"not": "a list"}'),
        _fake_run(returncode=1),  # leaks reported but no report file written
    ],
)
def test_failures_raise_instead_of_returning_an_empty_list(monkeypatch, plugin, run):
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(secrets.GitleaksError):
        secrets._run_gitleaks(str(plugin))


def test_timeout_raises(monkeypatch, plugin):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(secrets.GitleaksError, match="timed out"):
        secrets._run_gitleaks(str(plugin))


def test_a_failed_gitleaks_run_is_a_visible_finding_not_a_clean_scan(
    monkeypatch, plugin
):
    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(
        subprocess, "run", _fake_run(returncode=126, stderr="Error: unknown flag")
    )
    findings = secrets.run("plugin", str(plugin), use_gitleaks=True)
    errors = [f for f in findings if f.rule == "gitleaks:scanner-error"]
    assert len(errors) == 1
    assert errors[0].risk_level.value == "low"
    assert "built-in patterns only" in errors[0].message


def test_a_successful_run_adds_no_error_finding(monkeypatch, plugin):
    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=0))
    findings = secrets.run("plugin", str(plugin), use_gitleaks=True)
    assert not [f for f in findings if f.rule == "gitleaks:scanner-error"]


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
@pytest.mark.parametrize(
    "name,content",
    [
        (".gitleaks.toml", '[allowlist]\npaths = [".*"]\n'),
        (".gitleaks.toml", "this is [not valid toml"),
        (".gitleaksignore", ""),  # filled with the finding's fingerprint below
    ],
)
def test_a_plugin_cannot_switch_gitleaks_off(plugin, name, content):
    """The plugin is untrusted: its own gitleaks config, ignore file and inline allow
    comments must not change what is scanned."""
    (plugin / "leak.py").write_text(f'TOKEN = "{FAKE_PAT}"  # gitleaks:allow\n')
    if name == ".gitleaksignore":
        found = secrets._run_gitleaks(str(plugin))
        content = "\n".join(f["Fingerprint"] for f in found) + "\n"
    (plugin / name).write_text(content)
    findings = secrets._run_gitleaks(str(plugin))
    assert [f["RuleID"] for f in findings] == ["github-pat"]


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_finding_paths_are_relative_to_the_plugin(plugin):
    (plugin / "sub").mkdir()
    (plugin / "sub" / "leak.py").write_text(f'TOKEN = "{FAKE_PAT}"\n')
    findings = secrets._run_gitleaks(str(plugin))
    assert [f["File"] for f in findings] == ["sub/leak.py"]


def test_staging_skips_control_files_links_and_cache_dirs(tmp_path):
    src = tmp_path / "src"
    (src / "node_modules").mkdir(parents=True)
    (src / "node_modules" / "x.js").write_text("x")
    (src / "pkg").mkdir()
    (src / "pkg" / "a.py").write_text("a")
    (src / ".gitleaksignore").write_text("anything")
    (src / ".gitleaks.toml").write_text("anything")
    (src / "link.py").symlink_to(src / "pkg" / "a.py")
    dst = tmp_path / "dst"
    dst.mkdir()
    secrets._stage_for_gitleaks(str(src), str(dst))
    staged = sorted(str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file())
    assert staged == ["pkg/a.py"]


def test_staging_a_huge_plugin_is_an_error_not_a_clean_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "_GITLEAKS_STAGE_LIMIT", 10)
    src = tmp_path / "src"
    src.mkdir()
    (src / "big.txt").write_text("x" * 100)
    dst = tmp_path / "dst"
    dst.mkdir()
    with pytest.raises(secrets.GitleaksError, match="too large"):
        secrets._stage_for_gitleaks(str(src), str(dst))


def test_a_gitleaks_failure_is_not_cached(monkeypatch, tmp_path, plugin):
    """Cached, the failure would outlive a fixed gitleaks for the whole cache TTL."""
    from hermes_ops_kit import ops_config_io
    from hermes_ops_kit.security.plugin_scanner import cache, scanner
    from hermes_ops_kit.security.plugin_scanner.categories import policy as policy_cat

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(policy_cat, "_semgrep_available", lambda: False)
    monkeypatch.setattr(policy_cat, "_bandit_available", lambda: False)
    monkeypatch.setattr(secrets, "_gitleaks_available", lambda: True)
    (plugin / "__init__.py").write_text("x = 1\n")

    monkeypatch.setattr(
        subprocess, "run", _fake_run(returncode=126, stderr="Error: unknown flag")
    )
    result = scanner.scan_plugin(
        "plugin", str(plugin), profile="startup", use_gitleaks=True
    )
    assert any(f.rule == "gitleaks:scanner-error" for f in result.findings)
    assert cache.cache_stats()["total_entries"] == 0

    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=0))
    clean = scanner.scan_plugin(
        "plugin", str(plugin), profile="startup", use_gitleaks=True
    )
    assert not any(f.rule == "gitleaks:scanner-error" for f in clean.findings)
    assert cache.cache_stats()["total_entries"] == 1
