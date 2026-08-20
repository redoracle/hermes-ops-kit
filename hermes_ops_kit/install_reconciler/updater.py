"""Transactional updater — sync source, then reconcile the runtime.

"An update did not succeed because git pull returned 0. It succeeded
only when the runtime Python Hermes will use is coherent with the
updated source."

Flow::

    LOCK (install-reconciler advisory lock)
      → capture previous state (git SHA, dist metadata, runtime ctx)
      → sync source (fetch + ff-only pull; STOP on dirty tree)
      → inspect (doctor)
      → reconcile (repair, planner-gated)
      → runtime validate (doctor again — must be HEALTHY)
      → record outcome (JSONL, no secrets)
    UNLOCK

No destructive git operations: never ``reset --hard``, never force pull.
If validation fails the host is left in an EXPLICIT degraded state with
a recorded reason — automatic rollback is only attempted when the
working tree is still clean and the previous SHA is reachable.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..ops_config_io import OPS_KIT_DIR
from .cli import run_install_doctor
from .repair import LOCK_NAME, _repair_locked, _snapshot
from .state import HealthReport, HealthStatus
from ..security.lockfile import LockTimeoutError, provider_lock

UPDATE_LOG = os.path.join(OPS_KIT_DIR, "update_log.jsonl")


@dataclass
class UpdateOutcome:
    """Result of an update transaction — never fakes success."""

    synced: bool = False
    reconciled: bool = False
    healthy: bool = False
    dry_run: bool = False
    previous: dict = field(default_factory=dict)
    head_before: str = ""
    head_after: str = ""
    stopped_at: str = ""
    reason: str = ""
    report: HealthReport | None = None

    @property
    def success(self) -> bool:
        return self.healthy

    def to_dict(self) -> dict:
        d = dict(vars(self))
        d["report"] = self.report.to_dict() if self.report else None
        d.pop("previous", None)  # keep the ledger slim
        return {k: v for k, v in d.items() if v != "" or k == "reason"}


def _git(source: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", source, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _is_clean(source: str) -> bool:
    return _git(source, "status", "--porcelain").stdout.strip() == ""


def _head(source: str) -> str:
    proc = _git(source, "rev-parse", "HEAD")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _record(entry: dict) -> None:
    """Append an outcome to the update ledger. No secrets ever logged."""
    try:
        os.makedirs(OPS_KIT_DIR, exist_ok=True)
        entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        with open(UPDATE_LOG, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # ledger is best-effort; never blocks the transaction


def perform_update(
    source: str | None = None,
    target_python: str | None = None,
    dry_run: bool = False,
    package_name: str = "hermes-ops-kit",
) -> UpdateOutcome:
    """Run the full update transaction. Read-only when dry_run."""
    from .._subprocess import package_root

    src = source or package_root()
    outcome = UpdateOutcome(dry_run=dry_run)

    before = run_install_doctor(
        target_python=target_python, source_root=src, package_name=package_name
    )
    outcome.report = before
    outcome.previous = _snapshot(before)
    outcome.head_before = outcome.previous.get("git_sha", _head(src))

    if not Path(src, ".git").exists():
        outcome.stopped_at = "source"
        outcome.reason = f"{src} is not a git checkout"
        _record(outcome.to_dict())
        return outcome

    if not _is_clean(src):
        outcome.stopped_at = "sync"
        outcome.reason = "working tree dirty — refusing to update (no force ops)"
        _record(outcome.to_dict())
        return outcome

    if dry_run:
        outcome.stopped_at = "dry-run"
        outcome.reason = "no changes applied"
        return outcome

    try:
        with provider_lock(LOCK_NAME, timeout=120.0):
            # ---- sync source (ff-only, never destructive) ----
            fetch = _git(src, "fetch", "--tags", "--quiet")
            if fetch.returncode != 0:
                outcome.stopped_at = "sync"
                outcome.reason = f"git fetch rc={fetch.returncode}"
                _record(outcome.to_dict())
                return outcome
            pull = _git(src, "pull", "--ff-only", "--quiet")
            upstream = _git(
                src, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
            )
            has_upstream = upstream.returncode == 0
            if pull.returncode != 0 and has_upstream:
                outcome.stopped_at = "sync"
                outcome.reason = f"git pull --ff-only rc={pull.returncode}: {pull.stderr.strip()[:200]}"
                _record(outcome.to_dict())
                return outcome
            # No upstream configured: a local-only checkout is its own
            # source of truth — HEAD already IS the synced state.
            outcome.synced = True
            outcome.head_after = _head(src)

            # ---- inspect ----
            report = run_install_doctor(
                target_python=target_python,
                source_root=src,
                package_name=package_name,
            )
            outcome.report = report
            if report.overall is HealthStatus.HEALTHY:
                outcome.reconciled = True
                outcome.healthy = True
                _record(outcome.to_dict())
                return outcome

            # ---- reconcile (planner-gated repair; lock already held) ----
            repair_outcome = _repair_locked(report, package_name=package_name)
            outcome.reconciled = repair_outcome.success

            # ---- runtime validate ----
            after = run_install_doctor(
                target_python=target_python,
                source_root=src,
                package_name=package_name,
            )
            outcome.healthy = after.overall is HealthStatus.HEALTHY
            outcome.report = after

            if not outcome.healthy:
                # Explicit degraded state + best-effort safe rollback
                outcome.stopped_at = "validate"
                outcome.reason = (
                    f"runtime not HEALTHY after reconcile: "
                    f"{after.overall.value} "
                    f"[{', '.join(f.code for f in after.findings)}]"
                )
                if (
                    outcome.head_before
                    and _is_clean(src)
                    and outcome.head_after != outcome.head_before
                ):
                    co = _git(src, "checkout", "--quiet", outcome.head_before)
                    if co.returncode == 0:
                        outcome.reason += (
                            f" — rolled back to {outcome.head_before[:12]}"
                        )
                _record(outcome.to_dict())
                return outcome

            _record(outcome.to_dict())
            return outcome
    except LockTimeoutError as exc:
        outcome.stopped_at = "lock"
        outcome.reason = str(exc)
        _record(outcome.to_dict())
        return outcome
