"""Remaining operational paths follow the active profile at call time (S4.5)."""

from __future__ import annotations

import os

from hermes_ops_kit import hermes_export, hooks, ops_config_io
from hermes_ops_kit.install_reconciler import updater
from hermes_ops_kit.mcp_auditor import auditor
from hermes_ops_kit.providers import rotation_state_machine as rsm


def test_path_functions_follow_the_active_profile(profiles):
    for name in ("a", "b"):
        home = profiles.use(name)
        assert hermes_export.export_dir() == str(home / "cache/documents")
        assert updater.update_log_path() == str(home / "ops-kit/update_log.jsonl")
        assert rsm.checkpoint_dir() == str(home / "rotation_checkpoints")
        assert auditor.mcp_policy_path() == str(home / "mcp_policy.json")


def test_rotation_checkpoints_do_not_cross_profiles(profiles):
    profiles.use("a")
    rsm.RotationState(provider="openai", old_fingerprint="fp-a")._save_checkpoint()
    profiles.use("b")
    assert rsm.RotationState.load_checkpoint("openai") is None
    rsm.RotationState(provider="openai", old_fingerprint="fp-b")._save_checkpoint()

    profiles.use("a")
    assert rsm.RotationState.load_checkpoint("openai").old_fingerprint == "fp-a"
    profiles.use("b")
    assert rsm.RotationState.load_checkpoint("openai").old_fingerprint == "fp-b"
    assert (profiles.path("a") / "rotation_checkpoints/openai.json").is_file()
    assert (profiles.path("b") / "rotation_checkpoints/openai.json").is_file()


def test_mcp_policy_is_stored_per_profile(profiles):
    profiles.use("a")
    auditor._save_mcp_policy({"approved_servers": ["only-a"]})
    profiles.use("b")
    assert "only-a" not in str(auditor._load_mcp_policy())
    auditor._save_mcp_policy({"approved_servers": ["only-b"]})
    profiles.use("a")
    assert "only-a" in str(auditor._load_mcp_policy())
    assert "only-b" not in str(auditor._load_mcp_policy())
    assert (profiles.path("a") / "mcp_policy.json").is_file()
    assert (profiles.path("b") / "mcp_policy.json").is_file()


def test_permission_warnings_inspect_and_name_the_active_profile(profiles):
    a, b = profiles.use("a"), profiles.path("b")
    os.chmod(a, 0o700)
    os.chmod(b, 0o700)
    (a / ".env").write_text("X=1\n")
    os.chmod(a / ".env", 0o644)  # unsafe, in profile a only
    (b / ".env").write_text("X=1\n")
    os.chmod(b / ".env", 0o600)

    profiles.use("a")
    warnings_a = hooks._permission_warnings()
    assert any(str(a / ".env") in w and "chmod 600" in w for w in warnings_a)
    profiles.use("b")
    assert hooks._permission_warnings() == []


def test_explicit_home_override_still_redirects(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    assert hermes_export.export_dir() == str(tmp_path / "cache/documents")
    assert rsm.checkpoint_dir() == str(tmp_path / "rotation_checkpoints")
    assert auditor.mcp_policy_path() == str(tmp_path / "mcp_policy.json")


def test_import_time_aliases_remain_importable():
    assert isinstance(hermes_export.EXPORT_DIR, str)
    assert isinstance(updater.UPDATE_LOG, str)
    assert isinstance(rsm.CHECKPOINT_DIR, str)
    assert isinstance(auditor._MCP_POLICY_PATH, str)
