"""Assistant task store and audit resolve their files from the active profile (S4.3)."""

from __future__ import annotations

import json

from hermes_ops_kit import ops_config_io
from hermes_ops_kit.assistants import audit, task_store


def test_task_store_is_per_profile(profiles):
    profiles.use("a")
    task_store.create_task("t-a", "assistant", "review")
    profiles.use("b")
    task_store.create_task("t-b", "assistant", "review")

    assert task_store.get_task("t-a") is None
    assert task_store.get_task("t-b")["task_id"] == "t-b"
    assert [t["task_id"] for t in task_store.list_tasks()] == ["t-b"]
    profiles.use("a")
    assert [t["task_id"] for t in task_store.list_tasks()] == ["t-a"]
    assert (profiles.path("a") / "assistants/tasks.sqlite").is_file()
    assert (profiles.path("b") / "assistants/tasks.sqlite").is_file()


def test_assistant_audit_is_per_profile(profiles):
    profiles.use("a")
    audit.write_audit("asst", "t1", "review", "ok")
    profiles.use("b")
    audit.write_audit("asst", "t2", "review", "ok")

    def task_ids(name):
        text = (profiles.path(name) / "assistants/audit.jsonl").read_text()
        return [json.loads(line)["task_id"] for line in text.splitlines()]

    assert task_ids("a") == ["t1"]
    assert task_ids("b") == ["t2"]
    assert audit.audit_path() == str(profiles.path("b") / "assistants/audit.jsonl")


def test_explicit_home_override_still_redirects(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    assert task_store.db_path() == str(tmp_path / "assistants/tasks.sqlite")
    assert audit.audit_path() == str(tmp_path / "assistants/audit.jsonl")


def test_import_time_aliases_remain_importable():
    assert isinstance(task_store.DB_PATH, str)
    assert isinstance(audit.AUDIT_PATH, str)
