"""Unit tests: evaluator finding codes and overall classification.

Covers the failure scenarios at data level (no venv, fast):
same-version scripts drift, topology drift, corrupted wrapper, dependency
drift, disallowed origin, interpreter mismatch, multiple installations,
EntryPoint.load() failure, probe failure.
"""

from hermes_ops_kit.install_reconciler.evaluator import evaluate
from hermes_ops_kit.install_reconciler.fingerprint import (
    actual_fingerprint,
    expected_fingerprint,
)
from hermes_ops_kit.install_reconciler.state import (
    ActualInstallation,
    ConsoleScript,
    ExpectedInstallation,
    Finding,
    HealthStatus,
    InstallationMode,
    PluginEntryPoint,
    RuntimeContext,
    Severity,
)


def _ctx(tmp_path=None, mode=InstallationMode.EDITABLE):
    return RuntimeContext(
        python_executable="/venv/bin/python",
        environment_prefix="/venv",
        base_prefix="/usr",
        python_version="3.12.6",
        plugin_source=str(tmp_path) if tmp_path else "",
        installation_mode=mode,
    )


def _expected(**kw) -> ExpectedInstallation:
    e = ExpectedInstallation(
        package_name="hermes-ops-kit",
        version="0.4.0",
        console_scripts={"hermes-ops-kit": "hermes_ops_kit.bridge:main"},
        plugin_entry_points={"hermes-ops-kit": "hermes_ops_kit"},
        dependencies=["requests>=2.31"],
        build_backend="hatchling.build",
        packages=["hermes_ops_kit"],
        pyproject_path="/repo/pyproject.toml",
    )
    for k, v in kw.items():
        setattr(e, k, v)
    e.fingerprint = expected_fingerprint(e)
    return e


def _actual(expected: ExpectedInstallation, **kw) -> ActualInstallation:
    a = ActualInstallation(
        distribution_present=True,
        distribution_name=expected.package_name,
        version=expected.version,
        dist_info_path="/venv/lib/site-packages/hermes_ops_kit-0.4.0.dist-info",
        is_editable=True,
        origin_url="file:///repo",
        console_scripts={
            n: ConsoleScript(
                name=n,
                entry=v,
                script_path=f"/venv/bin/{n}",
                shebang="#!/venv/bin/python3.12",
                load_ok=True,
            )
            for n, v in expected.console_scripts.items()
        },
        plugin_entry_points={
            n: PluginEntryPoint(name=n, entry=v, load_ok=True)
            for n, v in expected.plugin_entry_points.items()
        },
        requires=list(expected.dependencies),
    )
    for k, v in kw.items():
        setattr(a, k, v)
    a.fingerprint = actual_fingerprint(a)
    return a


def _codes(report):
    return [f.code for f in report.findings]


def test_healthy(tmp_path):
    pkg = tmp_path / "hermes_ops_kit"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bridge.py").write_text("def main(): pass\n")
    report = evaluate(_actual(_expected()), _expected(), _ctx(tmp_path))
    assert report.overall is HealthStatus.HEALTHY
    assert report.findings == []


def test_same_version_console_entrypoint_drift(tmp_path):
    """Scenario 2: version unchanged, project.scripts changed (the incident)."""
    exp = _expected()
    act = _actual(exp)
    act.console_scripts["hermes-ops-kit"].entry = "bridge:main"
    act.console_scripts["hermes-ops-kit"].load_ok = False
    act.console_scripts[
        "hermes-ops-kit"
    ].load_error = "ModuleNotFoundError: No module named 'bridge'"
    act.fingerprint = actual_fingerprint(act)
    report = evaluate(act, exp, _ctx(tmp_path))
    codes = _codes(report)
    assert "CONSOLE_ENTRYPOINT_DRIFT" in codes
    assert "RUNTIME_PROBE_FAILURE" in codes
    assert "PACKAGING_METADATA_DRIFT" in codes
    assert report.overall is HealthStatus.DIAGNOSE_ONLY


def test_plugin_entrypoint_drift(tmp_path):
    exp = _expected()
    act = _actual(exp)
    act.plugin_entry_points["hermes-ops-kit"].entry = "bridge"
    act.plugin_entry_points["hermes-ops-kit"].load_ok = False
    act.fingerprint = actual_fingerprint(act)
    report = evaluate(act, exp, _ctx(tmp_path))
    assert "PLUGIN_ENTRYPOINT_DRIFT" in _codes(report)


def test_editable_topology_drift_same_entrypoint(tmp_path):
    """Scenario 3: entry-points unchanged, package layout moved."""
    (tmp_path / "hermes_ops_kit").mkdir()
    # module hermes_ops_kit.bridge missing in source layout
    exp = _expected()
    act = _actual(exp)
    report = evaluate(act, exp, _ctx(tmp_path))
    assert "EDITABLE_TOPOLOGY_DRIFT" in _codes(report)


def test_generated_executable_missing(tmp_path):
    """Scenario 4-adjacent: dist-info fine, generated executable gone."""
    exp = _expected()
    act = _actual(exp)
    act.console_scripts["hermes-ops-kit"].script_path = None
    act.console_scripts["hermes-ops-kit"].shebang = ""
    report = evaluate(act, exp, _ctx(tmp_path))
    assert "GENERATED_EXECUTABLE_DRIFT" in _codes(report)


def test_interpreter_mismatch(tmp_path):
    """Scenario 7: wrapper shebang targets a different interpreter."""
    exp = _expected()
    act = _actual(exp)
    act.console_scripts["hermes-ops-kit"].shebang = "#!/other/venv/bin/python"
    report = evaluate(act, exp, _ctx(tmp_path))
    assert "INTERPRETER_MISMATCH" in _codes(report)
    assert report.overall is HealthStatus.DIAGNOSE_ONLY


def test_versioned_shebang_is_not_mismatch(tmp_path):
    """pip writes /venv/bin/python3.12 — same interpreter, not drift."""
    exp = _expected()
    act = _actual(exp)
    act.console_scripts["hermes-ops-kit"].shebang = "#!/venv/bin/python3.12"
    report = evaluate(act, exp, _ctx(tmp_path))
    assert "INTERPRETER_MISMATCH" not in _codes(report)


def test_dependency_declaration_drift(tmp_path):
    """Scenario 5: [project.dependencies] changed → repair must be denied."""
    exp = _expected(dependencies=["requests>=2.31", "httpx>=0.27"])
    act = _actual(_expected())  # installed state predates the dep change
    report = evaluate(act, exp, _ctx(tmp_path))
    codes = _codes(report)
    assert "DEPENDENCY_DECLARATION_DRIFT" in codes
    dep = next(f for f in report.findings if f.code == "DEPENDENCY_DECLARATION_DRIFT")
    assert dep.repairable is False


def test_dependency_drift_blocks_repairable_classification(tmp_path):
    exp = _expected(dependencies=["requests>=2.31", "httpx>=0.27"])
    act = _actual(_expected())
    report = evaluate(act, exp, _ctx(tmp_path))
    assert report.overall is HealthStatus.DIAGNOSE_ONLY


def test_source_origin_disallowed_is_unsafe(tmp_path):
    """Scenario 6: origin not allowlisted → UNSAFE, never auto-switch source."""
    exp = _expected()
    act = _actual(exp)
    report = evaluate(act, exp, _ctx(tmp_path), allowed_sources=["file:///repo"])
    assert "SOURCE_ORIGIN_ALLOWED" in _codes(report)
    act.origin_url = "file:///elsewhere"
    report = evaluate(act, exp, _ctx(tmp_path), allowed_sources=["file:///repo"])
    assert "SOURCE_ORIGIN_DISALLOWED" in _codes(report)
    assert report.overall is HealthStatus.UNSAFE


def test_policy_not_inferred_without_allowlist(tmp_path):
    """No allowlist → no origin findings (hostname never consulted)."""
    exp = _expected()
    act = _actual(exp)
    act.origin_url = "file:///somewhere/else"
    report = evaluate(act, exp, _ctx(tmp_path))
    assert not any("SOURCE_ORIGIN" in c for c in _codes(report))


def test_multiple_installations(tmp_path):
    """Scenario 8: multiple dist-info candidates."""
    exp = _expected()
    act = _actual(exp)
    act.extra_distributions = ["<multiple dist-info matched>"]
    report = evaluate(act, exp, _ctx(tmp_path))
    assert "MULTIPLE_INSTALLATIONS" in _codes(report)


def test_runtime_probe_failure(tmp_path):
    """Scenario 9: EntryPoint.load() fails in the target runtime."""
    exp = _expected()
    act = _actual(exp)
    act.console_scripts["hermes-ops-kit"].load_ok = False
    act.console_scripts[
        "hermes-ops-kit"
    ].load_error = "ModuleNotFoundError: No module named 'bridge'"
    report = evaluate(act, exp, _ctx(tmp_path))
    assert "RUNTIME_PROBE_FAILURE" in _codes(report)


def test_probe_error_short_circuits(tmp_path):
    exp = _expected()
    act = _actual(exp)
    act.probe_error = "TimeoutExpired"
    report = evaluate(act, exp, _ctx(tmp_path))
    assert report.overall is HealthStatus.DIAGNOSE_ONLY
    assert _codes(report) == ["RUNTIME_PROBE_FAILURE"]


def test_missing_distribution(tmp_path):
    exp = _expected()
    report = evaluate(ActualInstallation(), exp, _ctx(tmp_path))
    assert _codes(report) == ["MISSING_DISTRIBUTION"]
    assert report.overall is HealthStatus.REPAIRABLE


def test_version_drift_repairable(tmp_path):
    exp = _expected()
    act = _actual(exp)
    act.version = "0.3.0"
    report = evaluate(act, exp, _ctx(tmp_path))
    assert "DISTRIBUTION_METADATA_DRIFT" in _codes(report)
    assert report.overall is HealthStatus.REPAIRABLE


def test_finding_is_pure_data():
    f = Finding(code="X", severity=Severity.ERROR)
    d = f.to_dict()
    assert d == {
        "code": "X",
        "severity": "error",
        "observed": "",
        "expected": "",
        "evidence": "",
        "repairable": False,
    }
