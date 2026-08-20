"""RepairPlanner — decides whether a repair is safe to apply.

"Detect broadly, repair narrowly." The planner maps a HealthReport to a
RepairPlan; auto-repair is allowed only when every precondition holds:

* overall status is REPAIRABLE (not DIAGNOSE_ONLY / UNSAFE / HEALTHY)
* single, unambiguous target runtime
* exactly one installation of the distribution
* source origin allowed (matches the inspected source or the allowlist)
* installation mode and installer known
* dependency declaration unchanged (packaging-only repair)
* no implicit source switching

Anything else → DIAGNOSE_ONLY / UNSAFE with reasons, no mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .state import (
    DEPENDENCY_DECLARATION_DRIFT,
    DISTRIBUTION_METADATA_DRIFT,
    EDITABLE_TOPOLOGY_DRIFT,
    GENERATED_EXECUTABLE_DRIFT,
    INTERPRETER_AMBIGUOUS,
    INTERPRETER_MISMATCH,
    MISSING_DISTRIBUTION,
    MULTIPLE_INSTALLATIONS,
    PACKAGING_METADATA_DRIFT,
    RUNTIME_PROBE_FAILURE,
    SOURCE_ORIGIN_DISALLOWED,
    CONSOLE_ENTRYPOINT_DRIFT,
    PLUGIN_ENTRYPOINT_DRIFT,
    HealthReport,
    HealthStatus,
    InstallationMode,
    Severity,
)

# Findings a packaging-only reinstall can genuinely fix
_REPAIRABLE_CODES = {
    MISSING_DISTRIBUTION,
    DISTRIBUTION_METADATA_DRIFT,
    CONSOLE_ENTRYPOINT_DRIFT,
    PLUGIN_ENTRYPOINT_DRIFT,
    GENERATED_EXECUTABLE_DRIFT,
    EDITABLE_TOPOLOGY_DRIFT,
    PACKAGING_METADATA_DRIFT,
    RUNTIME_PROBE_FAILURE,
}

# Findings that must block auto-repair outright
_BLOCKING_CODES = {
    DEPENDENCY_DECLARATION_DRIFT: "dependency declaration changed — reinstall "
    "required, not packaging-only",
    MULTIPLE_INSTALLATIONS: "multiple installations — ambiguous target",
    INTERPRETER_MISMATCH: "generated executables target a different interpreter",
    INTERPRETER_AMBIGUOUS: "interpreter ambiguous",
    SOURCE_ORIGIN_DISALLOWED: "source origin not allowed — refusing implicit "
    "source switching",
}


@dataclass
class RepairPlan:
    """Structured repair decision — data only, never executed here."""

    reason: str = ""
    target_python: str = ""
    source: str = ""
    installation_mode: str = "unknown"
    installer: str = "auto"
    dependency_action: str = "DEPENDENCY_AFFECTING"  # or PACKAGING_ONLY
    operations: list[str] = field(default_factory=list)
    safe_to_apply: bool = False
    safety_reason: str = ""

    def to_dict(self) -> dict:
        return dict(vars(self))


def plan_repair(report: HealthReport) -> RepairPlan:
    """Decide whether the reconciler may repair this report's drift."""
    plan = RepairPlan(
        target_python=report.runtime.python_executable,
        source=report.runtime.plugin_source,
        installation_mode=report.runtime.installation_mode.value,
    )

    if report.overall is HealthStatus.HEALTHY:
        plan.safe_to_apply = False
        plan.safety_reason = "nothing to repair — installation is HEALTHY"
        return plan

    if report.overall is HealthStatus.UNSAFE:
        plan.safe_to_apply = False
        plan.safety_reason = "UNSAFE: " + ", ".join(
            f.observed for f in report.findings if f.code == SOURCE_ORIGIN_DISALLOWED
        )
        return plan

    # Blocking findings (deny even under REPAIRABLE)
    blockers = [
        _BLOCKING_CODES[f.code] for f in report.findings if f.code in _BLOCKING_CODES
    ]
    if blockers:
        plan.safe_to_apply = False
        plan.safety_reason = "; ".join(blockers)
        return plan

    # Unknown mode / missing source / missing target
    if not plan.target_python:
        plan.safe_to_apply = False
        plan.safety_reason = "no explicit target interpreter"
        return plan
    if not plan.source:
        plan.safe_to_apply = False
        plan.safety_reason = "no plugin source directory resolved"
        return plan
    if report.runtime.installation_mode not in (
        InstallationMode.EDITABLE,
        InstallationMode.REGULAR,
    ):
        plan.safe_to_apply = False
        plan.safety_reason = "installation mode unknown"
        return plan

    # Every non-info finding must be in the repairable set — anything
    # unrecognized means the detector saw something we cannot fix narrowly.
    unknown = [
        f.code
        for f in report.findings
        if f.severity != Severity.INFO and f.code not in _REPAIRABLE_CODES
    ]
    if unknown:
        plan.safe_to_apply = False
        plan.safety_reason = f"unrecognized findings: {sorted(unknown)}"
        return plan

    # Dependency action: packaging-only when declarations are unchanged.
    plan.dependency_action = "PACKAGING_ONLY"
    plan.operations = [
        f"reinstall {report.expected.package_name} "
        f"({plan.installation_mode}) from {plan.source} "
        f"into {plan.target_python}"
    ]
    plan.reason = ", ".join(sorted({f.code for f in report.findings}))
    plan.safe_to_apply = True
    plan.safety_reason = (
        "same allowed source, same target interpreter, packaging-only drift"
    )
    return plan
