"""Audit writers resolve their files from the active profile at call time (S4.2)."""

from __future__ import annotations

import json

from hermes_ops_kit import ops_config_io
from hermes_ops_kit.audit import audit_log, ledger


def test_ledger_follows_the_active_profile(profiles):
    profiles.use("a")
    ledger.write_event("doctor_checked", {"who": "a"})
    profiles.use("b")
    ledger.write_event("doctor_checked", {"who": "b"})

    assert [e["who"] for e in ledger.tail_events()] == ["b"]
    assert [e["who"] for e in ledger.search_events(event_type="doctor_checked")] == [
        "b"
    ]
    profiles.use("a")
    assert [e["who"] for e in ledger.tail_events()] == ["a"]
    assert (profiles.path("a") / "ops-kit/audit/events.jsonl").is_file()
    assert (profiles.path("b") / "ops-kit/audit/events.jsonl").is_file()


def test_ledger_paths_are_per_profile_functions(profiles):
    profiles.use("a")
    assert ledger.audit_dir() == str(profiles.path("a") / "ops-kit/audit")
    assert ledger.audit_path() == str(profiles.path("a") / "ops-kit/audit/events.jsonl")
    profiles.use("b")
    assert ledger.audit_path() == str(profiles.path("b") / "ops-kit/audit/events.jsonl")


def test_rotation_audit_follows_the_active_profile(profiles):
    profiles.use("a")
    audit_log.write_audit_event("openai", "rotate", "ok")
    profiles.use("b")
    audit_log.write_audit_event("anthropic", "rotate", "ok")

    a_file = profiles.path("a") / "key-rotation-audit.jsonl"
    b_file = profiles.path("b") / "key-rotation-audit.jsonl"
    assert audit_log.audit_path() == str(b_file)
    assert [json.loads(x)["provider"] for x in a_file.read_text().splitlines()] == [
        "openai"
    ]
    assert [json.loads(x)["provider"] for x in b_file.read_text().splitlines()] == [
        "anthropic"
    ]


def test_explicit_home_override_still_redirects_the_audit_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    assert ledger.audit_path() == str(tmp_path / "ops-kit/audit/events.jsonl")
    assert audit_log.audit_path() == str(tmp_path / "key-rotation-audit.jsonl")


def test_import_time_aliases_remain_importable():
    assert isinstance(ledger.AUDIT_DIR, str) and isinstance(ledger.AUDIT_PATH, str)
    assert isinstance(audit_log.AUDIT_PATH, str)
