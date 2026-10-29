"""Resolved-dependency health and prerequisite findings (F7, S6.2)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from hermes_ops_kit.install_reconciler.evaluator import evaluate
from hermes_ops_kit.install_reconciler.fastcheck import fast_install_check
from hermes_ops_kit.install_reconciler.planner import plan_repair
from hermes_ops_kit.install_reconciler.state import (
    DEPENDENCY_CHECK_UNAVAILABLE,
    DEPENDENCY_DECLARATION_DRIFT,
    INCOMPATIBLE_DEPENDENCY,
    MISSING_DEPENDENCY,
    HealthStatus,
    Severity,
)
from tests.test_install_reconciler.test_evaluator import (
    _actual,
    _codes,
    _ctx,
    _expected,
)

LINUX_312 = {
    "implementation_name": "cpython",
    "implementation_version": "3.12.4",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_release": "6.1.0",
    "platform_system": "Linux",
    "platform_version": "#1 SMP",
    "python_full_version": "3.12.4",
    "platform_python_implementation": "CPython",
    "python_version": "3.12",
    "sys_platform": "linux",
}


@pytest.fixture
def source(tmp_path):
    pkg = tmp_path / "hermes_ops_kit"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bridge.py").write_text("def main(): pass\n")
    return tmp_path


def _report(source, requires, installed, markers=LINUX_312, declared=None):
    # ``declared`` is what pyproject.toml lists (base dependencies, never extras);
    # ``requires`` is what the installed distribution's metadata says.
    expected = _expected(dependencies=list(requires if declared is None else declared))
    actual = _actual(
        expected,
        requires=list(requires),
        installed_distributions=installed,
        marker_environment=markers,
    )
    return evaluate(actual, expected, _ctx(source))


def test_satisfied_requirements_are_healthy(source):
    report = _report(source, ["requests>=2.31"], {"requests": "2.33.0"})
    assert report.overall is HealthStatus.HEALTHY and report.findings == []


def test_a_missing_dependency_is_reported_and_blocks_health(source):
    report = _report(source, ["requests>=2.31"], {"other": "1.0"})
    finding = next(f for f in report.findings if f.code == MISSING_DEPENDENCY)
    assert finding.severity is Severity.ERROR and not finding.repairable
    assert "requests" in finding.observed
    assert report.overall is HealthStatus.DIAGNOSE_ONLY
    # distinct from declaration drift: the declaration itself is unchanged
    assert DEPENDENCY_DECLARATION_DRIFT not in _codes(report)


def test_an_incompatible_version_is_reported(source):
    report = _report(source, ["requests>=2.31"], {"requests": "2.10.0"})
    finding = next(f for f in report.findings if f.code == INCOMPATIBLE_DEPENDENCY)
    assert "2.10.0" in finding.observed and ">=2.31" in finding.expected


def test_package_names_are_matched_canonically(source):
    report = _report(source, ["Google_Auth>=2"], {"google-auth": "2.4"})
    assert report.overall is HealthStatus.HEALTHY


def test_markers_are_evaluated_against_the_target_not_the_host(source):
    requires = ["tomli>=2; python_version < '3.11'"]
    old_target = {**LINUX_312, "python_version": "3.10"}
    assert MISSING_DEPENDENCY in _codes(
        _report(source, requires, {"x": "1"}, old_target)
    )
    # the host runs 3.12, so a host-based evaluation would wrongly skip it above
    assert sys.version_info >= (3, 11)
    assert (
        _report(source, requires, {"x": "1"}, LINUX_312).overall is HealthStatus.HEALTHY
    )


def test_a_requirement_for_another_platform_is_not_required(source):
    requires = ["pywin32>=300; sys_platform == 'win32'"]
    assert _report(source, requires, {"x": "1"}).overall is HealthStatus.HEALTHY


def test_unselected_extras_are_not_required(source):
    requires = ["pytest>=7; extra == 'dev'", "requests>=2.31"]
    report = _report(
        source, requires, {"requests": "2.33.0"}, declared=["requests>=2.31"]
    )
    assert report.overall is HealthStatus.HEALTHY


def test_without_an_installed_package_observation_nothing_is_asserted(source):
    report = _report(source, ["requests>=2.31"], {})
    assert not {MISSING_DEPENDENCY, INCOMPATIBLE_DEPENDENCY} & set(_codes(report))


def test_a_missing_packaging_library_is_a_finding_not_a_crash(source, monkeypatch):
    monkeypatch.setitem(sys.modules, "packaging.requirements", None)  # import fails
    report = _report(source, ["requests>=2.31"], {"other": "1"})
    finding = next(f for f in report.findings if f.code == DEPENDENCY_CHECK_UNAVAILABLE)
    assert finding.severity is Severity.INFO and "packaging" in finding.observed
    assert MISSING_DEPENDENCY not in _codes(report)  # unknown, not asserted either way


def test_the_planner_will_not_attempt_a_packaging_only_fix(source):
    report = _report(source, ["requests>=2.31"], {"other": "1"})
    report.runtime.runtime_role = "standalone"  # ownership would otherwise refuse first
    plan = plan_repair(report)
    assert not plan.safe_to_apply and "cannot fix" in plan.safety_reason


def test_json_keeps_its_shape_and_exposes_only_findings(source):
    report = _report(source, ["requests>=2.31"], {"other": "1"})
    document = json.loads(json.dumps(report.to_dict()))
    assert sorted(document) == [
        "actual",
        "expected",
        "findings",
        "overall",
        "runtime",
        "schema_version",
    ]
    assert "installed_distributions" not in document["actual"]
    assert "marker_environment" not in document["actual"]
    assert MISSING_DEPENDENCY in [f["code"] for f in document["findings"]]


def test_the_fast_check_judges_dependencies_without_any_subprocess(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("fast_install_check must not spawn processes")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    report = fast_install_check()
    assert report.actual.installed_distributions  # collected in-process
    assert report.actual.marker_environment["sys_platform"] == sys.platform
    assert not {MISSING_DEPENDENCY, INCOMPATIBLE_DEPENDENCY} & set(_codes(report))


# --- prerequisite failures are findings: JSON, display, health and exit status agree ---


@pytest.fixture
def doctor(tmp_path, monkeypatch, source, capsys):
    """Run `install doctor` against a healthy report with controllable prerequisites."""
    import shutil

    from hermes_ops_kit import commands, ops_config_io
    from hermes_ops_kit.install_reconciler import cli as reconciler_cli

    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text("A=1\n")
    env_file.chmod(0o600)
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(home))

    healthy = evaluate(_actual(_expected()), _expected(), _ctx(source))
    assert healthy.overall is HealthStatus.HEALTHY
    monkeypatch.setattr(reconciler_cli, "run_install_doctor", lambda **kw: healthy)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    class Doctor:
        env_file_path = env_file

        def run(self, *flags):
            code = commands._handle_install(["doctor", *flags])
            return code, capsys.readouterr().out

    return Doctor()


def test_all_prerequisites_passing_exits_zero_and_json_is_healthy(doctor):
    code, out = doctor.run("--json")
    document = json.loads(out)
    assert code == 0 and document["overall"] == "HEALTHY"
    assert document["findings"] == []


def test_a_failed_prerequisite_is_in_the_json_and_the_exit_status_agrees(
    doctor, monkeypatch
):
    import shutil

    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "bw" else "/usr/bin/x"
    )
    code, out = doctor.run("--json")
    document = json.loads(out)  # still a single pure JSON document
    prerequisite = [
        f for f in document["findings"] if f["code"] == "PREREQUISITE_FAILED"
    ]
    assert [f["observed"] for f in prerequisite] == ["bw CLI: not found"]
    assert document["overall"] != "HEALTHY" and code == 1


def test_a_failed_prerequisite_appears_in_the_human_view_too(doctor, monkeypatch):
    doctor.env_file_path.chmod(0o644)  # unsafe permissions
    code, out = doctor.run()
    assert code == 1
    assert "❌" in out and "PREREQUISITE_FAILED" in out
    assert "Installation: DIAGNOSE_ONLY" in out


def test_the_exit_status_is_derived_from_the_reported_health(doctor, monkeypatch):
    import shutil

    for which in (lambda name: f"/usr/bin/{name}", lambda name: None):
        monkeypatch.setattr(shutil, "which", which)
        code, out = doctor.run("--json")
        assert (code == 0) == (json.loads(out)["overall"] == "HEALTHY")


# --- review follow-ups: malformed observations never crash or borrow the host ---------------


def test_an_incomplete_marker_environment_is_unknown_not_the_hosts(source):
    report = _report(
        source,
        ["tomli>=2; python_version < '3.11'"],
        {"x": "1"},
        {"python_version": "3.10"},
    )
    finding = next(f for f in report.findings if f.code == DEPENDENCY_CHECK_UNAVAILABLE)
    assert "incomplete" in finding.observed
    assert MISSING_DEPENDENCY not in _codes(report)


@pytest.mark.parametrize(
    "requires,installed",
    [
        (
            [None, 5, "requests>=2.31"],
            {"requests": "2.33.0"},
        ),  # non-string requirements
        (["requests>=2.31"], {"requests": 2, 3: "1"}),  # non-string version / name
        (["not a requirement!!"], {"requests": "2.33.0"}),  # unparsable
    ],
)
def test_malformed_probe_data_never_crashes_the_evaluation(source, requires, installed):
    report = _report(source, [r for r in requires if isinstance(r, str)], installed)
    report.actual.requires = requires  # what the probe really handed over
    from hermes_ops_kit.install_reconciler.depcheck import check_dependencies

    problems, why = check_dependencies(requires, installed, LINUX_312)  # must not raise
    assert isinstance(problems, list)
    assert why is None


def test_a_refused_dry_run_update_exits_non_zero(monkeypatch, capsys):
    from hermes_ops_kit import commands
    from hermes_ops_kit.install_reconciler import updater

    def outcome(stopped_at):
        return updater.UpdateOutcome(dry_run=True, stopped_at=stopped_at, reason="why")

    monkeypatch.setattr(updater, "perform_update", lambda **kw: outcome("ownership"))
    assert commands._handle_install(["update", "--dry-run"]) == 1
    monkeypatch.setattr(updater, "perform_update", lambda **kw: outcome("dry-run"))
    assert commands._handle_install(["update", "--dry-run"]) == 0
