"""Unit tests: RepairPlanner safety gating and InstallerAdapter argv."""

from hermes_ops_kit.install_reconciler.installer_adapter import build_install_argv
from hermes_ops_kit.install_reconciler.planner import plan_repair
from hermes_ops_kit.install_reconciler.state import (
    ActualInstallation,
    ConsoleScript,
    ExpectedInstallation,
    Finding,
    HealthReport,
    HealthStatus,
    InstallationMode,
    PluginEntryPoint,
    RuntimeContext,
    Severity,
)


def _expected() -> ExpectedInstallation:
    return ExpectedInstallation(
        package_name="hermes-ops-kit",
        version="0.4.0",
        console_scripts={"hermes-ops-kit": "hermes_ops_kit.bridge:main"},
        plugin_entry_points={"hermes-ops-kit": "hermes_ops_kit"},
        dependencies=["requests>=2.31"],
        packages=["hermes_ops_kit"],
        pyproject_path="/repo/pyproject.toml",
    )


def _ctx() -> RuntimeContext:
    return RuntimeContext(
        python_executable="/venv/bin/python",
        plugin_source="/repo",
        installation_mode=InstallationMode.EDITABLE,
    )


def _actual() -> ActualInstallation:
    return ActualInstallation(
        distribution_present=True,
        version="0.4.0",
        is_editable=True,
        origin_url="file:///repo",
        console_scripts={
            "hermes-ops-kit": ConsoleScript(
                name="hermes-ops-kit",
                entry="bridge:main",
                script_path="/venv/bin/hermes-ops-kit",
                shebang="#!/venv/bin/python3.12",
                load_ok=False,
            )
        },
        plugin_entry_points={
            "hermes-ops-kit": PluginEntryPoint(
                name="hermes-ops-kit", entry="hermes_ops_kit"
            )
        },
        requires=["requests>=2.31"],
    )


def _report(overall=HealthStatus.REPAIRABLE, findings=None) -> HealthReport:
    return HealthReport(
        overall=overall,
        runtime=_ctx(),
        actual=_actual(),
        expected=_expected(),
        findings=findings or [],
    )


def test_healthy_never_repairs():
    plan = plan_repair(_report(HealthStatus.HEALTHY))
    assert not plan.safe_to_apply
    assert "HEALTHY" in plan.safety_reason


def test_unsafe_never_repairs():
    plan = plan_repair(
        _report(
            HealthStatus.UNSAFE,
            [Finding(code="SOURCE_ORIGIN_DISALLOWED", severity=Severity.ERROR)],
        )
    )
    assert not plan.safe_to_apply
    assert "UNSAFE" in plan.safety_reason


def test_dependency_drift_denies_packaging_only_repair():
    plan = plan_repair(
        _report(
            findings=[
                Finding(code="DEPENDENCY_DECLARATION_DRIFT", severity=Severity.WARNING)
            ]
        )
    )
    assert not plan.safe_to_apply
    assert "dependency" in plan.safety_reason


def test_multiple_installations_denied():
    plan = plan_repair(
        _report(
            findings=[Finding(code="MULTIPLE_INSTALLATIONS", severity=Severity.WARNING)]
        )
    )
    assert not plan.safe_to_apply
    assert "ambiguous" in plan.safety_reason


def test_interpreter_mismatch_denied():
    plan = plan_repair(
        _report(
            findings=[Finding(code="INTERPRETER_MISMATCH", severity=Severity.ERROR)]
        )
    )
    assert not plan.safe_to_apply


def test_missing_source_denied():
    r = _report()
    r.runtime.plugin_source = ""
    plan = plan_repair(r)
    assert not plan.safe_to_apply
    assert "source" in plan.safety_reason


def test_unknown_mode_denied():
    r = _report()
    r.runtime.installation_mode = InstallationMode.UNKNOWN
    plan = plan_repair(r)
    assert not plan.safe_to_apply
    assert "mode" in plan.safety_reason


def test_unrecognized_finding_denied():
    plan = plan_repair(
        _report(findings=[Finding(code="SOMETHING_NEW", severity=Severity.ERROR)])
    )
    assert not plan.safe_to_apply
    assert "SOMETHING_NEW" in plan.safety_reason


def test_typical_drift_is_safe_and_packaging_only():
    plan = plan_repair(
        _report(
            findings=[
                Finding(
                    code="CONSOLE_ENTRYPOINT_DRIFT",
                    severity=Severity.ERROR,
                    repairable=True,
                ),
                Finding(code="RUNTIME_PROBE_FAILURE", severity=Severity.ERROR),
            ]
        )
    )
    assert plan.safe_to_apply
    assert plan.dependency_action == "PACKAGING_ONLY"
    assert plan.target_python == "/venv/bin/python"
    assert plan.source == "/repo"
    assert plan.operations


# ---- InstallerAdapter ----


def test_uv_argv_is_explicit_no_shell():
    argv = build_install_argv("uv", "/venv/bin/python", "/repo", editable=True)
    assert argv[:5] == ["uv", "pip", "install", "-p", "/venv/bin/python"]
    assert argv[5] == "--editable"
    assert "/repo" in argv


def test_pip_argv_uses_target_module():
    argv = build_install_argv("pip", "/venv/bin/python3.12", "/repo", editable=False)
    assert argv[0] == "/venv/bin/python3.12"
    assert argv[1:4] == ["-m", "pip", "install"]
    assert "--editable" not in argv
    assert argv[-1] == "/repo"


def test_argv_never_contains_shell_or_sudo_tokens():
    for installer in ("uv", "pip"):
        for editable in (True, False):
            argv = build_install_argv(installer, "/venv/bin/python", "/repo", editable)
            flat = " ".join(argv)
            assert "sudo" not in flat
            assert "|" not in flat and ";" not in flat and "&&" not in flat
