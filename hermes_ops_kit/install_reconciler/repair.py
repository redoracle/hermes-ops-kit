"""Repair orchestration — snapshot, install, MANDATORY reinspection.

An installer exiting 0 does NOT mean the repair succeeded. Success is
declared only when the post-repair discover → evaluate → probe cycle
reports HEALTHY. The repair is idempotent: a second ``--repair`` on a
healthy runtime is a no-op.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from ..security.lockfile import LockTimeoutError, provider_lock
from .cli import run_install_doctor
from .installer_adapter import InstallResult, run_install
from .planner import RepairPlan, plan_repair
from .state import HealthReport, HealthStatus, InstallationMode

LOCK_NAME = "install-reconciler"
LOCK_TIMEOUT = 120.0


@dataclass
class RepairOutcome:
    """Result of a repair attempt — never fakes success."""

    changed: bool = False
    healthy: bool = False
    plan: RepairPlan | None = None
    before: HealthReport | None = None
    after: HealthReport | None = None
    install: InstallResult | None = None
    snapshot: dict = field(default_factory=dict)
    lock_error: str = ""

    @property
    def success(self) -> bool:
        return self.healthy

    def failure_reason(self) -> str:
        if self.lock_error:
            return self.lock_error
        if self.install is not None and not self.install.ok:
            return (
                f"installer {self.install.installer} rc={self.install.returncode}: "
                f"{self.install.summary()}"
            )
        if self.after is not None:
            codes = ", ".join(f.code for f in self.after.findings)
            return f"reinspection not healthy: {self.after.overall.value} [{codes}]"
        return "unknown"


def _snapshot(report: HealthReport) -> dict:
    """Pre-repair state capture (for diagnosis/rollback in M3)."""
    snap: dict = {
        "target_python": report.runtime.python_executable,
        "installation_mode": report.runtime.installation_mode.value,
        "installed_version": report.actual.version,
        "origin_url": report.actual.origin_url,
        "overall": report.overall.value,
    }
    source = report.runtime.plugin_source
    if source:
        try:
            sha = subprocess.run(
                ["git", "-C", source, "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if sha.returncode == 0:
                snap["git_sha"] = sha.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return snap


def perform_repair(
    report: HealthReport,
    package_name: str = "hermes-ops-kit",
    verbose: bool = False,
) -> RepairOutcome:
    """Repair only when the planner says it is safe. Always reinspect."""
    outcome = RepairOutcome(before=report)

    if report.overall is HealthStatus.HEALTHY:
        outcome.healthy = True
        return outcome  # idempotent no-op

    plan = plan_repair(report)
    outcome.plan = plan
    if not plan.safe_to_apply:
        return outcome

    outcome.snapshot = _snapshot(report)

    try:
        with provider_lock(LOCK_NAME, timeout=LOCK_TIMEOUT):
            editable = report.runtime.installation_mode is InstallationMode.EDITABLE
            outcome.install = run_install(
                plan.target_python, plan.source, editable=editable
            )
            if not outcome.install.ok:
                return outcome  # no false success
            outcome.changed = True
    except LockTimeoutError as exc:
        outcome.lock_error = f"another install/repair holds the lock: {exc}"
        return outcome

    # MANDATORY reinspection — fresh discover + evaluate + probe
    after = run_install_doctor(
        target_python=plan.target_python,
        source_root=plan.source,
        package_name=package_name,
    )
    outcome.after = after
    outcome.healthy = after.overall is HealthStatus.HEALTHY
    return outcome
