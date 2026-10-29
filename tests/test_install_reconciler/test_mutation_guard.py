"""Managed or ambiguous runtimes are never mutated directly (F3, S6.1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_ops_kit import ops_config_io
from hermes_ops_kit.install_reconciler import installer_adapter, repair, updater
from hermes_ops_kit.install_reconciler.installer_adapter import run_install
from hermes_ops_kit.install_reconciler.planner import RepairPlan, plan_repair
from hermes_ops_kit.install_reconciler.state import (
    HealthReport,
    HealthStatus,
    InstallationMode,
    RuntimeContext,
)


def _python(root: Path) -> str:
    (root / "bin").mkdir(parents=True)
    python = root / "bin" / "python"
    python.write_text("#!/bin/sh\n")
    return str(python)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("HERMES_AGENT_SOURCE", "HERMES_INSTALL_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path / ".hermes"))
    return tmp_path


@pytest.fixture
def launched(monkeypatch):
    """Record every subprocess.run; nothing really runs."""
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.fixture
def managed_python(machine):
    return _python(machine / ".hermes/installs/abc/environments/def/venv")


@pytest.fixture
def standalone_python(machine):
    return _python(machine / "projects/app/.venv")


def _report(python: str, role: str, reason: str = "") -> HealthReport:
    return HealthReport(
        overall=HealthStatus.REPAIRABLE,
        runtime=RuntimeContext(
            python_executable=python,
            plugin_source="/repo",
            installation_mode=InstallationMode.EDITABLE,
            runtime_role=role,
            ownership_reason=reason,
        ),
    )


# --- run_install: the single choke point --------------------------------------------


def test_run_install_refuses_a_managed_target_and_launches_nothing(
    managed_python, launched
):
    result = run_install(managed_python, "/repo", editable=True, installer="pip")
    assert result.refused and not result.ok
    assert result.argv == [] and launched == []
    assert (
        "managed by Hermes" in result.stderr
        and "hermes plugins update" in result.stderr
    )


def test_run_install_refuses_an_unresolvable_target(machine, launched):
    result = run_install(str(machine / "nope/bin/python"), "/repo", editable=False)
    assert result.refused and launched == []
    assert "unclear ownership" in result.stderr


def test_a_hand_built_safe_plan_cannot_bypass_the_guard(managed_python, launched):
    """A stale or forged plan says safe_to_apply; run_install decides for itself."""
    plan = RepairPlan(target_python=managed_python, source="/repo", safe_to_apply=True)
    result = run_install(plan.target_python, plan.source, editable=True)
    assert result.refused and launched == []


def test_a_standalone_target_installs_packaging_only(standalone_python, launched):
    result = run_install(standalone_python, "/repo", editable=True, installer="pip")
    assert result.ok and not result.refused
    assert "--no-deps" in launched[0]
    uv = run_install(standalone_python, "/repo", editable=False, installer="uv")
    assert uv.ok and "--no-deps" in launched[1]


def test_a_core_refusal_to_scope_the_environment_is_a_refusal_result(
    standalone_python, launched, fake_core, profiles, monkeypatch
):
    def refuse(*args, **kwargs):
        raise fake_core.UnscopedSecretError("", "no scope under multiplexing")

    monkeypatch.setattr(fake_core.local_module, "served_profile_child_env", refuse)
    profiles.use("a")
    result = run_install(standalone_python, "/repo", editable=False, installer="pip")
    assert result.refused and launched == []
    assert "profile-scoped environment" in result.stderr


# --- planner -----------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["managed", "unknown"])
def test_planner_refuses_managed_and_ambiguous_runtimes(role):
    plan = plan_repair(_report("/x/bin/python", role, "evidence"))
    assert not plan.safe_to_apply
    assert (
        "hermes plugins update" in plan.safety_reason
        and "evidence" in plan.safety_reason
    )


def test_planner_does_not_refuse_a_standalone_runtime_for_ownership():
    plan = plan_repair(_report("/x/bin/python", "standalone"))
    assert "native maintenance" not in plan.safety_reason
    assert "managed by Hermes" not in plan.safety_reason


# --- repair -----------------------------------------------------------------------------


def test_repair_of_a_managed_target_changes_nothing(
    managed_python, launched, monkeypatch
):
    called = []
    monkeypatch.setattr(repair, "run_install", lambda *a, **k: called.append(a))
    outcome = repair._repair_locked(
        _report(managed_python, "managed", "core install state")
    )
    assert not outcome.changed and outcome.install is None
    assert outcome.plan is not None and not outcome.plan.safe_to_apply
    assert called == [] and launched == []


# --- update ------------------------------------------------------------------------------


def test_update_refuses_before_any_source_synchronisation(
    managed_python, machine, launched, monkeypatch
):
    source = machine / "src"
    (source / ".git").mkdir(parents=True)
    report = _report(managed_python, "managed", "core install state")
    monkeypatch.setattr(updater, "run_install_doctor", lambda **kw: report)

    def forbidden(*args, **kwargs):
        raise AssertionError("update touched the source before checking ownership")

    monkeypatch.setattr(updater, "_git", forbidden)
    monkeypatch.setattr(updater, "_head", lambda *a, **k: "abc")
    monkeypatch.setattr(updater, "run_install", forbidden)

    outcome = updater.perform_update(source=str(source), target_python=managed_python)
    assert outcome.stopped_at == "ownership" and not outcome.synced
    assert "managed by Hermes" in outcome.reason
    # only the read-only snapshot ran: no fetch, pull, checkout or install
    assert all(
        cmd[:2] == ["git", "-C"] and cmd[-2:] == ["rev-parse", "HEAD"]
        for cmd in launched
    )
    ledger = Path(updater.update_log_path()).read_text()
    assert (
        '"stopped_at": "ownership"' in ledger
    )  # the refusal is recorded, nothing else


def test_update_dry_run_also_reports_the_refusal(managed_python, machine, monkeypatch):
    source = machine / "src"
    (source / ".git").mkdir(parents=True)
    report = _report(managed_python, "unknown", "target interpreter not found")
    monkeypatch.setattr(updater, "run_install_doctor", lambda **kw: report)
    monkeypatch.setattr(updater, "_head", lambda *a, **k: "abc")
    outcome = updater.perform_update(source=str(source), dry_run=True)
    assert outcome.stopped_at == "ownership"


def test_the_installer_module_exports_the_refused_return_code():
    assert installer_adapter.REFUSED_RETURNCODE < 0


# --- review follow-up: repair re-derives ownership before its first mutation ---------------


def test_repair_re_derives_ownership_and_ignores_a_report_that_claims_standalone(
    managed_python, launched, monkeypatch
):
    """A forged or stale report says standalone; the interpreter itself is managed."""
    replaced, installed = [], []
    monkeypatch.setattr(repair.os, "replace", lambda *a, **k: replaced.append(a))
    monkeypatch.setattr(repair, "run_install", lambda *a, **k: installed.append(a))
    monkeypatch.setattr(
        repair,
        "plan_repair",
        lambda report: RepairPlan(
            target_python=managed_python, source="/repo", safe_to_apply=True
        ),
    )
    forged = _report(managed_python, "standalone")
    outcome = repair._repair_locked(forged)
    assert not outcome.changed and outcome.install is None
    assert not outcome.plan.safe_to_apply
    assert "managed by Hermes" in outcome.plan.safety_reason
    assert replaced == [] and installed == [] and launched == []
