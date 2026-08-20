"""Evaluate actual vs expected installation state → HealthReport.

Pure function: no subprocess, no filesystem mutation, no repair. The
``repairable`` flag on findings is a *classification* only — M2 decides
whether a repair is actually safe to apply.
"""

from __future__ import annotations

from .state import (
    CONSOLE_ENTRYPOINT_DRIFT,
    DEPENDENCY_DECLARATION_DRIFT,
    DISTRIBUTION_METADATA_DRIFT,
    EDITABLE_TOPOLOGY_DRIFT,
    GENERATED_EXECUTABLE_DRIFT,
    PATH_SHADOWED_EXECUTABLE,
    INTERPRETER_MISMATCH,
    MISSING_DISTRIBUTION,
    MULTIPLE_INSTALLATIONS,
    PACKAGING_METADATA_DRIFT,
    PLUGIN_ENTRYPOINT_DRIFT,
    RUNTIME_PROBE_FAILURE,
    SOURCE_ORIGIN_ALLOWED,
    SOURCE_ORIGIN_DISALLOWED,
    ActualInstallation,
    ExpectedInstallation,
    Finding,
    HealthReport,
    HealthStatus,
    RuntimeContext,
    Severity,
)


def _entry_module(entry: str) -> str:
    return entry.split(":", 1)[0].strip()


def _module_in_source(module: str, source: str, packages: list[str]) -> bool | None:
    """Best-effort check that an entry-point module exists in the source.

    Returns None when undecidable (no source or no packages declared).
    """
    if not source:
        return None
    from pathlib import Path

    root = Path(source)
    parts = module.split(".")
    matched_pkg = False
    for pkg in packages:
        pkg_parts = pkg.split("/")
        base = pkg_parts[-1]
        if parts[0] != base:
            continue
        matched_pkg = True
        rel = Path(*pkg_parts[:-1], *parts)
        if (root / rel).with_suffix(".py").is_file():
            return True
        if (root / rel).is_dir():
            return True
    # Top-level matched a declared package but the module is not there:
    # topology changed under the same entry-point name.
    return False if matched_pkg else None


def evaluate(
    actual: ActualInstallation,
    expected: ExpectedInstallation,
    context: RuntimeContext,
    allowed_sources: list[str] | None = None,
) -> HealthReport:
    report = HealthReport(runtime=context, actual=actual, expected=expected)
    findings = report.findings

    if actual.probe_error:
        findings.append(
            Finding(
                code=RUNTIME_PROBE_FAILURE,
                severity=Severity.ERROR,
                observed=actual.probe_error,
                expected="target runtime must be probeable",
                evidence=context.python_executable,
            )
        )
        report.overall = HealthStatus.DIAGNOSE_ONLY
        return report

    if not expected.pyproject_path or not expected.version:
        findings.append(
            Finding(
                code=PACKAGING_METADATA_DRIFT,
                severity=Severity.ERROR,
                observed=f"pyproject at {expected.pyproject_path or '<unknown>'}",
                expected="readable pyproject.toml declaring version/scripts",
            )
        )
        report.overall = HealthStatus.DIAGNOSE_ONLY
        if not actual.distribution_present:
            findings.append(_missing_distribution(actual, expected))
        return report

    if not actual.distribution_present:
        findings.append(_missing_distribution(actual, expected))
        report.overall = HealthStatus.REPAIRABLE
        return report

    if actual.extra_distributions:
        findings.append(
            Finding(
                code=MULTIPLE_INSTALLATIONS,
                severity=Severity.WARNING,
                observed=f"{1 + len(actual.extra_distributions)} matching dist-info",
                expected="exactly one distribution",
                evidence=actual.dist_info_path,
            )
        )

    if actual.version != expected.version:
        findings.append(
            Finding(
                code=DISTRIBUTION_METADATA_DRIFT,
                severity=Severity.WARNING,
                observed=f"installed {actual.version}",
                expected=f"source {expected.version}",
                evidence=actual.dist_info_path,
                repairable=True,
            )
        )

    # Entry-point drift (the original incident class)
    exp_scripts = expected.console_scripts
    act_scripts = {n: s.entry for n, s in actual.console_scripts.items()}
    for name, entry in exp_scripts.items():
        if act_scripts.get(name) != entry:
            findings.append(
                Finding(
                    code=CONSOLE_ENTRYPOINT_DRIFT,
                    severity=Severity.ERROR,
                    observed=f"{name} = {act_scripts.get(name, '<missing>')}",
                    expected=f"{name} = {entry}",
                    evidence=actual.dist_info_path,
                    repairable=True,
                )
            )
    for name in act_scripts:
        if name not in exp_scripts:
            findings.append(
                Finding(
                    code=CONSOLE_ENTRYPOINT_DRIFT,
                    severity=Severity.WARNING,
                    observed=f"{name} = {act_scripts[name]}",
                    expected="<not declared by source>",
                    repairable=True,
                )
            )

    exp_plugins = expected.plugin_entry_points
    act_plugins = {n: p.entry for n, p in actual.plugin_entry_points.items()}
    for name, entry in exp_plugins.items():
        if act_plugins.get(name) != entry:
            findings.append(
                Finding(
                    code=PLUGIN_ENTRYPOINT_DRIFT,
                    severity=Severity.ERROR,
                    observed=f"{name} = {act_plugins.get(name, '<missing>')}",
                    expected=f"{name} = {entry}",
                    repairable=True,
                )
            )

    # Generated executables (supplementary evidence: missing/stale wrappers)
    for name, script in actual.console_scripts.items():
        if script.script_path is None:
            findings.append(
                Finding(
                    code=GENERATED_EXECUTABLE_DRIFT,
                    severity=Severity.ERROR,
                    observed=f"no generated executable for '{name}' on PATH/scripts dir",
                    expected=f"executable for '{name}'",
                    repairable=True,
                )
            )
        if script.path_shadow_path:
            findings.append(
                Finding(
                    code=PATH_SHADOWED_EXECUTABLE,
                    severity=Severity.ERROR,
                    observed=f"'{name}' resolves to {script.path_shadow_path}",
                    expected=script.script_path or f"runtime wrapper for '{name}'",
                    evidence=script.path_shadow_path,
                    repairable=script.path_shadow_repairable,
                )
            )
        elif script.shebang and not _shebang_targets(
            script.shebang, context.python_executable
        ):
            findings.append(
                Finding(
                    code=INTERPRETER_MISMATCH,
                    severity=Severity.ERROR,
                    observed=f"wrapper shebang {script.shebang!r}",
                    expected=f"shebang targeting {context.python_executable}",
                    evidence=script.script_path,
                )
            )

    # Runtime probe failures — the runtime authority
    for name, script in actual.console_scripts.items():
        if script.load_ok is False:
            findings.append(
                Finding(
                    code=RUNTIME_PROBE_FAILURE,
                    severity=Severity.ERROR,
                    observed=f"console script '{name}' entry-point load failed",
                    expected="EntryPoint.load() succeeds in target runtime",
                    evidence=script.load_error,
                )
            )
    for name, plugin in actual.plugin_entry_points.items():
        if plugin.load_ok is False:
            findings.append(
                Finding(
                    code=RUNTIME_PROBE_FAILURE,
                    severity=Severity.ERROR,
                    observed=f"plugin entry-point '{name}' load failed",
                    expected="EntryPoint.load() succeeds in target runtime",
                    evidence=plugin.load_error,
                )
            )

    # Editable topology drift: same entry-points but modules moved under
    # a different package layout in the source (repackage class).
    if actual.is_editable and context.plugin_source:
        # NOTE: iterate pairs — script and plugin entry-points may share
        # the same name ("hermes-ops-kit") and a dict merge would drop one.
        for _name, entry in [*(exp_scripts.items()), *(exp_plugins.items())]:
            module = _entry_module(entry)
            ok = _module_in_source(module, context.plugin_source, expected.packages)
            if ok is False:
                findings.append(
                    Finding(
                        code=EDITABLE_TOPOLOGY_DRIFT,
                        severity=Severity.ERROR,
                        observed=f"module '{module}' not found in source layout",
                        expected=f"'{module}' under packages {expected.packages}",
                        evidence=context.plugin_source,
                        repairable=True,
                    )
                )

    # Dependency declaration drift (base deps only; extras excluded)
    from .fingerprint import _is_extra, _normalize_req

    installed_base = {_normalize_req(r) for r in actual.requires if not _is_extra(r)}
    declared_base = {_normalize_req(d) for d in expected.dependencies}
    if installed_base != declared_base:
        findings.append(
            Finding(
                code=DEPENDENCY_DECLARATION_DRIFT,
                severity=Severity.WARNING,
                observed=f"base deps only installed: {sorted(installed_base - declared_base)}",
                expected=f"base deps not installed: {sorted(declared_base - installed_base)}",
                repairable=False,
            )
        )

    # Packaging metadata drift via ABI fingerprint (same version, changed ABI)
    if actual.fingerprint and actual.fingerprint != expected.fingerprint:
        findings.append(
            Finding(
                code=PACKAGING_METADATA_DRIFT,
                severity=Severity.ERROR,
                observed=f"installed ABI {actual.fingerprint[:12]}",
                expected=f"source ABI {expected.fingerprint[:12]}",
                repairable=True,
            )
        )

    # Source origin policy (never inferred from hostname/OS)
    if allowed_sources is not None:
        origin = actual.origin_url or ""
        if any(origin.startswith(a) for a in allowed_sources if a):
            findings.append(
                Finding(
                    code=SOURCE_ORIGIN_ALLOWED,
                    severity=Severity.INFO,
                    observed=origin,
                )
            )
        else:
            findings.append(
                Finding(
                    code=SOURCE_ORIGIN_DISALLOWED,
                    severity=Severity.ERROR,
                    observed=origin or "<no direct_url origin>",
                    expected=f"origin under {allowed_sources}",
                )
            )

    report.overall = _classify(findings)
    return report


def _shebang_targets(shebang: str, python_executable: str) -> bool:
    """True if the wrapper shebang resolves to the target interpreter.

    pip writes the exact interpreter (often versioned, e.g.
    ``#!/venv/bin/python3.12``), so compare resolved real paths rather
    than literal strings.
    """
    import os

    if not shebang.startswith("#!"):
        return True  # not a Python wrapper (shell shim etc.) — no authority
    interp = shebang[2:].strip().split()[0] if shebang[2:].strip() else ""
    if not interp:
        return True
    # Shell trampolines (uv `#!/bin/sh` exec wrappers, install.sh bash
    # shims) are not Python wrappers — the runtime probe is the authority.
    if os.path.basename(interp) not in ("python",) and not os.path.basename(
        interp
    ).startswith("python"):
        return True
    if not interp:
        return True
    a, b = os.path.realpath(interp), os.path.realpath(python_executable)
    if a == b:
        return True
    # pip writes the versioned interpreter (python3.12) while the context
    # may hold the unversioned one (python) in the same bin dir — same env.
    da, db = os.path.dirname(a), os.path.dirname(b)
    return (
        da == db
        and os.path.basename(a).startswith("python")
        and os.path.basename(b).startswith("python")
    )


def _missing_distribution(
    actual: ActualInstallation, expected: ExpectedInstallation
) -> Finding:
    return Finding(
        code=MISSING_DISTRIBUTION,
        severity=Severity.ERROR,
        observed=f"{expected.package_name or 'distribution'} not installed",
        expected=f"{expected.package_name} {expected.version}".strip(),
        repairable=True,
    )


def _classify(findings: list[Finding]) -> HealthStatus:
    if any(f.code == SOURCE_ORIGIN_DISALLOWED for f in findings):
        return HealthStatus.UNSAFE
    if any(f.code == RUNTIME_PROBE_FAILURE for f in findings):
        return (
            HealthStatus.DIAGNOSE_ONLY if len(findings) > 1 else HealthStatus.REPAIRABLE
        )
    if any(f.severity == Severity.ERROR for f in findings):
        if all(
            f.repairable
            for f in findings
            if f.severity in (Severity.ERROR, Severity.WARNING)
        ):
            return HealthStatus.REPAIRABLE
        return HealthStatus.DIAGNOSE_ONLY
    if any(f.severity == Severity.WARNING for f in findings):
        return (
            HealthStatus.REPAIRABLE
            if all(f.repairable for f in findings)
            else HealthStatus.DIAGNOSE_ONLY
        )
    return HealthStatus.HEALTHY
