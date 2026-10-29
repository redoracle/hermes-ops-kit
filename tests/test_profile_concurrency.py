"""Concurrent profiles in one process stay isolated end to end (F2, S5.3).

Each worker runs as a different profile in its own context, the way routed
profiles do inside Hermes: ``get_hermes_home()`` and the secret scope are
context-local. Everything the kit stores or reads for a profile must land in
and come from that profile only, and the shared process environment must not move.
"""

from __future__ import annotations

import contextvars
import json
import os
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from hermes_ops_kit import _subprocess, ops_config_io, provider_catalog
from hermes_ops_kit.assistants import task_store
from hermes_ops_kit.audit import ledger
from hermes_ops_kit.env import loader
from hermes_ops_kit.providers import rotation_state_machine as rsm
from hermes_ops_kit.security.plugin_scanner import policy

PROFILES = ("alpha", "beta", "gamma")
ITERATIONS = 20


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """A fake core whose active profile is a ContextVar, plus one home per profile."""
    active: contextvars.ContextVar[str] = contextvars.ContextVar("active_profile")
    homes = {}
    for name in PROFILES:
        home = tmp_path / name
        home.mkdir()
        (home / ".env").write_text(f"SECRET=secret-{name}\nOPENAI_API_KEY=key-{name}\n")
        homes[name] = home

    constants = types.ModuleType("hermes_constants")
    setattr(constants, "get_hermes_home", lambda: homes[active.get()])

    scope = types.ModuleType("agent.secret_scope")
    setattr(
        scope, "UnscopedSecretError", type("UnscopedSecretError", (RuntimeError,), {})
    )
    setattr(scope, "current_secret_scope", lambda: {"bound": active.get()})
    setattr(scope, "is_multiplex_active", lambda: True)
    setattr(scope, "get_secret", lambda name, default=None: default)

    local = types.ModuleType("tools.environments.local")

    def served_profile_child_env(
        base=None, *, target_home=None, inherit_credentials=False
    ):
        env = {"PATH": os.environ.get("PATH", ""), "HERMES_HOME": str(target_home)}
        if inherit_credentials:  # the target profile's own secrets, never a sibling's
            env.update(loader.parse_env_file(str(Path(target_home) / ".env")))
        return env

    setattr(local, "served_profile_child_env", served_profile_child_env)

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", ops_config_io._IMPORT_TIME_HOME)
    monkeypatch.delenv("HERMES_PLUGIN_POLICY_PATH", raising=False)
    monkeypatch.setenv("LAUNCH_ONLY_SECRET", "launch-secret")
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)
    monkeypatch.setitem(sys.modules, "tools.environments.local", local)
    return types.SimpleNamespace(active=active, homes=homes)


def _work(routed, name, barrier, errors, seen):
    routed.active.set(name)
    barrier.wait(timeout=30)
    try:
        for i in range(ITERATIONS):
            ledger.write_event("doctor_checked", {"who": name, "i": i})
            task_store.create_task(f"{name}-{i}", "assistant", "review")
            rsm.RotationState(
                provider="openai", old_fingerprint=name
            )._save_checkpoint()
            if i == 0:
                policy.approve_plugin(f"plugin-of-{name}")
            env = _subprocess.child_env()
            seen.append(
                {
                    "profile": name,
                    "secret": loader.get_secret("SECRET"),
                    "key": provider_catalog.provider_key("openai"),
                    "launch": loader.get_secret("LAUNCH_ONLY_SECRET"),
                    "child_home": env["HERMES_HOME"],
                    "child_secret": env.get("SECRET"),
                    "child_key": env.get("OPENAI_API_KEY"),
                    "child_launch": env.get("LAUNCH_ONLY_SECRET"),
                }
            )
            threading.Event().wait(0.0005)  # let the other profiles interleave
    except BaseException as exc:  # noqa: BLE001 - surfaced by the test body
        errors.append((name, repr(exc)))


def test_three_profiles_running_concurrently_stay_isolated(routed):
    before = dict(os.environ)
    barrier = threading.Barrier(len(PROFILES))
    errors, seen, threads = [], [], []
    for name in PROFILES:
        context = contextvars.copy_context()
        thread = threading.Thread(
            target=context.run, args=(_work, routed, name, barrier, errors, seen)
        )
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert errors == []
    assert len(seen) == len(PROFILES) * ITERATIONS

    # every read, in every iteration, answered for its own profile
    for item in seen:
        name = item["profile"]
        assert item["secret"] == f"secret-{name}"
        assert item["key"] == f"key-{name}"
        assert item["launch"] is None  # a global launch secret is never served
        assert item["child_home"] == str(routed.homes[name])
        assert item["child_secret"] == f"secret-{name}"
        assert item["child_key"] == f"key-{name}"
        assert item["child_launch"] is None

    # everything stored landed in its own profile, and only there
    for name, home in routed.homes.items():
        events = [
            json.loads(line)
            for line in (home / "ops-kit/audit/events.jsonl").read_text().splitlines()
        ]
        assert {e["who"] for e in events} == {name}
        assert len(events) == ITERATIONS
        db = home / "assistants/tasks.sqlite"
        assert db.is_file()
        policy_file = json.loads((home / "ops-kit/plugin_policy.json").read_text())
        assert list(policy_file["approved_plugins"]) == [f"plugin-of-{name}"]
        checkpoint = json.loads((home / "rotation_checkpoints/openai.json").read_text())
        assert checkpoint["old_fingerprint"] == name

    # nothing leaked into the shared process environment
    assert dict(os.environ) == before


def test_a_child_process_resolves_the_same_profile_as_its_parent(routed):
    for name in ("alpha", "beta"):
        routed.active.set(name)
        env = _subprocess.child_env()
        out = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import os; print(os.environ['HERMES_HOME'], os.environ.get('SECRET'))",
            ],
            env={**env, "PYTHONPATH": ""},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert out.stdout.split() == [str(routed.homes[name]), f"secret-{name}"], (
            out.stderr
        )
