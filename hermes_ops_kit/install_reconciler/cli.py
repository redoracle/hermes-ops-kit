"""``install doctor`` orchestrator — read-only, action-oriented output.

Answers the operator's real questions: which runtime am I using, what is
installed, where did it come from, is it coherent, why not, is it
repairable? Technical detail lives behind ``--verbose`` / ``--json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from .._subprocess import package_root
from .discovery import discover_actual_state, resolve_runtime_context
from .evaluator import evaluate
from .resolver import expected_from_dist_info, resolve_expected_state
from .state import HealthReport, HealthStatus


def run_install_doctor(
    target_python: str | None = None,
    source_root: str | None = None,
    allowed_sources: list[str] | None = None,
    package_name: str = "hermes-ops-kit",
) -> HealthReport:
    """Discover + resolve + evaluate. No side effects."""
    source = source_root or package_root()
    context = resolve_runtime_context(target_python, source)
    actual = discover_actual_state(context, package_name=package_name)

    # Source mode: an operator/explicit source checkout with a pyproject.
    # The default package_root() of a pip/PyPI install is site-packages —
    # wheels may ship a pyproject.toml there, but it is not a source
    # declaration. Only trust pyproject when it comes with a repo (.git)
    # or an explicit source_root.
    pyproject = Path(source) / "pyproject.toml"
    source_mode = pyproject.is_file() and (
        bool(source_root) or Path(source, ".git").exists()
    )
    if source_mode:
        expected = resolve_expected_state(pyproject, package_name=package_name)
    elif actual.distribution_present and actual.dist_info_path:
        # Artifact mode (normal user, pip-installed): the dist-info of the
        # installed artifact is the declaration of record.
        expected = expected_from_dist_info(
            actual.dist_info_path, package_name=package_name
        )
    else:
        expected = resolve_expected_state(pyproject, package_name=package_name)
    return evaluate(actual, expected, context, allowed_sources=allowed_sources)


def format_report(report: HealthReport, verbose: bool = False) -> str:
    """Human-readable, concise; details only with --verbose."""
    ctx = report.runtime
    act = report.actual
    lines: list[str] = []
    icon = {
        HealthStatus.HEALTHY: "✅",
        HealthStatus.REPAIRABLE: "🛠",
        HealthStatus.DIAGNOSE_ONLY: "⚠️",
        HealthStatus.UNSAFE: "⛔",
    }[report.overall]

    lines.append(f"{icon} Installation: {report.overall.value}")
    lines.append(f"  Runtime: {ctx.python_executable} (Python {ctx.python_version})")
    if act.distribution_present:
        origin = act.origin_url or "unknown origin"
        mode = "editable" if act.is_editable else "installed"
        lines.append(f"  Installed: {act.distribution_name} {act.version} ({mode})")
        lines.append(f"  Origin: {origin}")
        if verbose:
            lines.append(f"  dist-info: {act.dist_info_path}")
    else:
        lines.append(f"  Installed: {report.expected.package_name} NOT FOUND")

    if report.findings:
        lines.append("  Issues:")
        for f in report.findings:
            line = f"    [{f.code}] {f.observed}"
            if f.expected:
                line += f" — expected: {f.expected}"
            if verbose and f.evidence:
                line += f" (evidence: {f.evidence})"
            if f.repairable:
                line += " [repairable]"
            lines.append(line)
        if not verbose:
            lines.append("  Run with --verbose or --json for details.")
    else:
        lines.append("  No issues — runtime matches the source declarations.")

    if report.overall == HealthStatus.REPAIRABLE:
        lines.append("  Next step: hermes-ops-kit install doctor --repair")
    elif report.overall in (HealthStatus.DIAGNOSE_ONLY, HealthStatus.UNSAFE):
        lines.append(
            "  Next step: review the findings above; automatic repair withheld."
        )
    return "\n".join(lines)


def print_json(report: HealthReport) -> None:
    print(json.dumps(report.to_dict(), indent=2))
