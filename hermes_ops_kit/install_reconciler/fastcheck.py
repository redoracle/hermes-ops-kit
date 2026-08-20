"""Preflight fast check — read-only install sanity in the current runtime.

Designed for Hermes boot preflight: no pip, no uv, no network, no
subprocess runtime probe — only in-process ``importlib.metadata``,
filesystem checks and the ABI fingerprint. When drift is found, the
caller should point the operator at ``hermes-ops-kit install doctor``.
"""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

from .._subprocess import package_root
from .evaluator import evaluate
from .fingerprint import actual_fingerprint
from .resolver import resolve_expected_state
from .state import (
    ActualInstallation,
    ConsoleScript,
    HealthReport,
    InstallationMode,
    PluginEntryPoint,
    RuntimeContext,
)


def discover_in_process(package_name: str = "hermes-ops-kit") -> ActualInstallation:
    """Cheap discovery in the CURRENT interpreter — no subprocess probe.

    Entry-point load results are left unknown (``load_ok=None``): the
    authoritative probe lives in ``discovery.discover_actual_state`` and
    is deliberately not run on the fast path.
    """
    from importlib.metadata import distribution

    actual = ActualInstallation(distribution_name=package_name)
    try:
        dist = distribution(package_name)
    except Exception:  # noqa: BLE001 — PackageNotFoundError and friends
        return actual

    actual.distribution_present = True
    actual.version = dist.version
    actual.dist_info_path = str(getattr(dist, "_path", ""))
    actual.requires = [str(r) for r in dist.requires or []]

    du = dist.read_text("direct_url.json")
    if du:
        import json

        try:
            j = json.loads(du)
            actual.direct_url = du
            actual.origin_url = j.get("url", "")
            actual.is_editable = bool(j.get("dir_info", {}).get("editable"))
        except ValueError:
            pass

    scripts_dir = sysconfig.get_path("scripts") or ""
    for e in dist.entry_points:
        if e.group == "console_scripts":
            script = ConsoleScript(name=e.name, entry=e.value)
            if scripts_dir:
                cand = Path(scripts_dir) / e.name
                if cand.is_file():
                    script.script_path = str(cand)
            actual.console_scripts[e.name] = script
        elif e.group == "hermes_agent.plugins":
            actual.plugin_entry_points[e.name] = PluginEntryPoint(
                name=e.name, entry=e.value
            )

    actual.fingerprint = actual_fingerprint(actual)
    return actual


def fast_install_check(
    package_name: str = "hermes-ops-kit",
    source_root: str | None = None,
) -> HealthReport:
    """Metadata + fingerprint + filesystem sanity for preflight.

    Never mutates anything and never shells out. The returned report is
    a full HealthReport; entry-point load findings are absent by design
    (unknown, not asserted healthy).
    """
    source = source_root or package_root()
    context = RuntimeContext(
        python_executable=sys.executable,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        plugin_source=source,
        installation_mode=InstallationMode.UNKNOWN,
    )
    actual = discover_in_process(package_name)
    context.installation_mode = (
        InstallationMode.EDITABLE if actual.is_editable else InstallationMode.REGULAR
    )
    expected = resolve_expected_state(Path(source) / "pyproject.toml", package_name)
    return evaluate(actual, expected, context)
