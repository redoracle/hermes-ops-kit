"""`plugin policy export` / `import`: validated, dry-run by default, escalation-gated."""

from __future__ import annotations

import json
import os

import pytest

from hermes_ops_kit.security.plugin_scanner import cli, policy


@pytest.fixture()
def home(tmp_path, monkeypatch):
    from hermes_ops_kit import ops_config_io

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_PLUGIN_POLICY_PATH", raising=False)
    return tmp_path


def _write(path, **overrides):
    data = policy._default_policy() | overrides
    path.write_text(json.dumps(data))
    return str(path)


def _current():
    return policy._load_policy()


def test_export_round_trips_through_import(home, capsys):
    policy.approve_plugin("alpha")
    assert cli.handle_plugin(["policy", "export"]) == 0
    exported = capsys.readouterr().out
    backup = home / "backup.json"
    backup.write_text(exported)
    result = policy.import_policy(str(backup))
    assert (
        result["added"] == {}
        and result["removed"] == {}
        and result["escalations"] == []
    )


def test_dry_run_writes_nothing(home):
    before = _current()
    f = _write(home / "in.json", disabled_plugins=["x"])
    result = policy.import_policy(f)
    assert result["added"] == {"disabled_plugins": ["x"]} and result["applied"] is False
    assert _current() == before


def test_restrictive_import_applies_with_audit_and_no_contents(home):
    f = _write(home / "in.json", blocked_plugins=["evil"])
    result = policy.import_policy(f, apply=True)
    assert result["applied"] and _current()["blocked_plugins"] == ["evil"]
    audit = [json.loads(line) for line in open(policy.plugin_audit_path())]
    entry = [e for e in audit if e["action"] == "policy_import"][0]
    assert entry["source"] == "in.json" and entry["escalations"] == 0
    assert "evil" not in json.dumps(entry)


@pytest.mark.parametrize(
    "change",
    [
        {"approved_plugins": ["p"]},
        {"approved_findings": ["p:code:r:abc"]},
        {"approved_categories": ["code"]},
        {"rule_overrides": {"p": {"shell-execution": "allow"}}},
    ],
)
def test_import_that_loosens_the_scanner_needs_confirmation(home, change):
    f = _write(home / "in.json", **change)
    assert policy.import_policy(f)["escalations"]
    with pytest.raises(policy.PolicyImportError, match="allow-approvals"):
        policy.import_policy(f, apply=True)
    assert _current() == policy._default_policy()
    assert policy.import_policy(f, apply=True, allow_approvals=True)["applied"]


def test_lifting_a_block_counts_as_loosening(home):
    policy.block_plugin("evil")
    f = _write(home / "in.json")
    assert any(
        "lifted blocked_plugins" in e for e in policy.import_policy(f)["escalations"]
    )


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"version": 1}',
        '{"version": 2, "approved_plugins": "x"}',
        '{"version": 2, "rule_overrides": {"p": {"r": "bogus"}}}',
    ],
)
def test_invalid_files_are_refused(home, content):
    f = home / "bad.json"
    f.write_text(content)
    with pytest.raises(policy.PolicyImportError):
        policy.import_policy(str(f), apply=True, allow_approvals=True)
    assert _current() == policy._default_policy()


def test_symlink_and_oversized_files_are_refused(home, monkeypatch):
    real = home / "real.json"
    _write(real)
    link = home / "link.json"
    os.symlink(real, link)
    with pytest.raises(policy.PolicyImportError, match="regular file"):
        policy.import_policy(str(link))
    monkeypatch.setattr(policy, "MAX_POLICY_IMPORT_BYTES", 10)
    with pytest.raises(policy.PolicyImportError, match="larger"):
        policy.import_policy(str(real))


def test_cli_exit_codes(home, capsys):
    good = _write(home / "in.json", approved_plugins=["p"])
    assert cli.handle_plugin(["policy", "import"]) == 2
    assert cli.handle_plugin(["policy", "import", good, "--bogus"]) == 2
    assert cli.handle_plugin(["policy", "import", good]) == 0  # dry run
    assert (
        cli.handle_plugin(["policy", "import", good, "--apply"]) == 1
    )  # needs confirm
    assert (
        cli.handle_plugin(["policy", "import", good, "--apply", "--allow-approvals"])
        == 0
    )
    assert "p" in _current()["approved_plugins"]


def test_unknown_keys_are_reported_and_not_stored(home):
    f = home / "in.json"
    f.write_text(json.dumps(policy._default_policy() | {"trusted": ["x"], "extra": 1}))
    result = policy.import_policy(str(f), apply=True)
    assert result["ignored_keys"] == ["extra", "trusted"]
    assert "trusted" not in policy._load_policy() and "trusted" not in json.loads(
        open(policy.plugin_policy_path()).read()
    )


def test_the_dry_run_labels_what_would_loosen_the_scanner(home, capsys):
    f = _write(home / "in.json", approved_plugins=["p"])
    assert cli.handle_plugin(["policy", "import", f]) == 0
    assert "LOOSENS: approved_plugins: p" in capsys.readouterr().out


def test_a_symlinked_or_missing_file_is_refused_without_a_race(home):
    real = home / "real.json"
    _write(real)
    link = home / "link.json"
    os.symlink(real, link)
    for target in (link, home / "missing.json", home):
        with pytest.raises(policy.PolicyImportError):
            policy.import_policy(str(target))


def test_a_missing_file_is_reported_as_missing(home, capsys):
    assert cli.handle_plugin(["policy", "import", str(home / "backup.json")]) == 1
    captured = capsys.readouterr()
    assert "not found: backup.json" in captured.out + captured.err


GOOD_ID = "demo:secrets:base-secret-pattern:b05338d22a17"


@pytest.mark.parametrize(
    "bad",
    [
        "demo-leaky:policy:shell-execution:x",
        "x",
        "a:b:c",
        "demo:Secrets:r:b05338d22a17",
        "demo:secrets:r:B05338D22A17",
        "../x:secrets:r:b05338d22a17",
    ],
)
def test_approving_a_malformed_finding_id_is_refused(home, bad, capsys):
    assert cli.handle_plugin(["approve", "--finding", bad]) == 2
    assert policy.get_policy()["approved_findings"] == []


@pytest.mark.parametrize(
    "good",
    [
        GOOD_ID,
        "demo:policy:gitleaks:github-pat:b05338d22a17",
        "my.plugin@2:secrets:r:0123456789ab",
        "hermes_*",
    ],
)
def test_real_shaped_finding_ids_are_accepted(home, good):
    assert cli.handle_plugin(["approve", "--finding", good]) == 0
    assert good in policy.get_policy()["approved_findings"]


def test_the_id_a_scan_produces_is_accepted(home):
    from hermes_ops_kit.security.plugin_scanner.findings import (
        Finding,
        RiskLevel,
        Severity,
    )

    finding = Finding(
        id="",
        plugin_name="demo",
        category="secrets",
        rule="gitleaks:github-pat",
        severity=Severity.ERROR,
        risk_level=RiskLevel.CRITICAL,
        file_path="a.py",
        line=3,
    )
    assert policy.is_finding_id(finding.id)


def test_revoking_a_finding_that_is_not_approved_says_so(home, capsys):
    assert cli.handle_plugin(["revoke", "--finding", GOOD_ID]) == 1
    captured = capsys.readouterr()
    assert "not approved" in captured.out + captured.err


def test_revoking_an_approved_finding_still_works(home):
    cli.handle_plugin(["approve", "--finding", GOOD_ID])
    assert cli.handle_plugin(["revoke", "--finding", GOOD_ID]) == 0
    assert policy.get_policy()["approved_findings"] == []


def test_the_policy_layer_refuses_a_malformed_id_too(home):
    with pytest.raises(ValueError):
        policy.approve_finding("nonsense")


def test_revoking_a_finding_with_an_unreadable_policy_reports_it_without_a_traceback(
    home, capsys
):
    """The policy read used to sit outside the try, so a corrupt file gave a raw traceback."""
    policy_file = home / "ops-kit" / "plugin_policy.json"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text("{not json")
    assert cli.handle_plugin(["revoke", "--finding", GOOD_ID]) == 1
    captured = capsys.readouterr()
    assert "Revoke failed" in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_the_json_mode_error_for_an_unreadable_policy_is_an_envelope(home, capsys):
    policy_file = home / "ops-kit" / "plugin_policy.json"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text("{not json")
    assert cli.handle_plugin(["revoke", "--finding", GOOD_ID, "--json"]) == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False
    assert "Cannot load plugin policy" in json.dumps(envelope)
