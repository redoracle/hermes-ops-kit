"""Security-layer files resolve from the active profile at call time (S4.4)."""

from __future__ import annotations

import os

import pytest

from hermes_ops_kit import ops_config_io
from hermes_ops_kit.security import lockfile
from hermes_ops_kit.security.plugin_scanner import bootstrap, cache, enforce, policy


@pytest.fixture(autouse=True)
def _no_policy_env_override(monkeypatch):
    monkeypatch.delenv("HERMES_PLUGIN_POLICY_PATH", raising=False)


def test_each_path_function_follows_the_active_profile(profiles):
    profiles.use("a")
    a = profiles.path("a")
    assert lockfile.lock_dir() == str(a / "locks")
    assert cache.cache_db_path() == str(a / "ops-kit/plugin_scanner_cache.db")
    assert enforce.hermes_config_path() == str(a / "config.yaml")
    assert policy.plugin_policy_path() == str(a / "ops-kit/plugin_policy.json")
    assert policy.plugin_audit_path() == str(a / "ops-kit/plugin_policy_audit.jsonl")
    assert bootstrap.hermes_home() == a
    assert bootstrap.ops_kit_dir() == a / "ops-kit"
    assert bootstrap.report_dir() == a / "ops-kit/reports"
    assert bootstrap.scanner_config_path() == a / "ops-kit/plugin_scanner.yaml"

    profiles.use("b")
    b = profiles.path("b")
    assert lockfile.lock_dir() == str(b / "locks")
    assert cache.cache_db_path() == str(b / "ops-kit/plugin_scanner_cache.db")
    assert enforce.hermes_config_path() == str(b / "config.yaml")
    assert policy.plugin_policy_path() == str(b / "ops-kit/plugin_policy.json")
    assert bootstrap.scanner_config_path() == b / "ops-kit/plugin_scanner.yaml"


def test_plugin_policy_env_override_beats_the_profile_and_is_read_at_call_time(
    profiles, monkeypatch, tmp_path
):
    profiles.use("a")
    override = tmp_path / "custom_policy.json"
    monkeypatch.setenv("HERMES_PLUGIN_POLICY_PATH", str(override))
    assert policy.plugin_policy_path() == str(override)
    assert policy.plugin_audit_path() == str(tmp_path / "plugin_policy_audit.jsonl")
    monkeypatch.delenv("HERMES_PLUGIN_POLICY_PATH")
    assert policy.plugin_policy_path() == str(
        profiles.path("a") / "ops-kit/plugin_policy.json"
    )


def test_plugin_approvals_do_not_cross_profiles(profiles):
    profiles.use("a")
    policy.approve_plugin("only-in-a")
    profiles.use("b")
    assert "only-in-a" not in policy.get_policy()["approved_plugins"]
    policy.approve_plugin("only-in-b")
    profiles.use("a")
    approved = policy.get_policy()["approved_plugins"]
    assert "only-in-a" in approved and "only-in-b" not in approved
    assert (profiles.path("a") / "ops-kit/plugin_policy.json").is_file()
    assert (profiles.path("b") / "ops-kit/plugin_policy.json").is_file()
    assert (profiles.path("a") / "ops-kit/plugin_policy_audit.jsonl").is_file()


def test_scanner_cache_database_is_created_in_the_active_profile(profiles):
    profiles.use("a")
    cache._ensure_db().close()
    profiles.use("b")
    cache._ensure_db().close()
    assert (profiles.path("a") / "ops-kit/plugin_scanner_cache.db").is_file()
    assert (profiles.path("b") / "ops-kit/plugin_scanner_cache.db").is_file()


def test_provider_lock_files_are_per_profile(profiles):
    profiles.use("a")
    os.makedirs(lockfile.lock_dir(), exist_ok=True)
    profiles.use("b")
    assert lockfile.lock_dir() == str(profiles.path("b") / "locks")
    assert not os.path.exists(lockfile.lock_dir())  # a's directory did not leak into b


def test_explicit_home_override_still_redirects_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    assert lockfile.lock_dir() == str(tmp_path / "locks")
    assert enforce.hermes_config_path() == str(tmp_path / "config.yaml")
    assert bootstrap.report_dir() == tmp_path / "ops-kit/reports"


def test_import_time_aliases_remain_importable():
    assert isinstance(lockfile.LOCK_DIR, str)
    assert isinstance(cache.CACHE_DB_PATH, str)
    assert isinstance(enforce.HERMES_CONFIG_PATH, str)
    assert isinstance(policy.PLUGIN_POLICY_PATH, str)
    assert isinstance(policy.PLUGIN_AUDIT_PATH, str)
    for name in ("HERMES_HOME", "OPS_KIT_DIR", "REPORT_DIR", "SCANNER_CONFIG_PATH"):
        assert getattr(bootstrap, name) is not None
