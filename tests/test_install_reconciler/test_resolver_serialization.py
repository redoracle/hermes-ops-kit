"""Unit tests: expected-state resolution and JSON serialization."""

import json

from hermes_ops_kit.install_reconciler.resolver import resolve_expected_state
from hermes_ops_kit.install_reconciler.state import (
    SCHEMA_VERSION,
    ActualInstallation,
    ExpectedInstallation,
    HealthReport,
    HealthStatus,
    RuntimeContext,
)

PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "hermes-ops-kit"
version = "0.4.0"
dependencies = ["requests>=2.31", "PyYAML>=6.0"]

[project.scripts]
hermes-ops-kit = "hermes_ops_kit.bridge:main"

[project.entry-points."hermes_agent.plugins"]
hermes-ops-kit = "hermes_ops_kit"

[tool.hatch.build.targets.wheel]
packages = ["hermes_ops_kit"]
"""


def test_resolver_reads_repo_pyproject():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    exp = resolve_expected_state(root / "pyproject.toml")
    assert exp.version
    assert exp.console_scripts["hermes-ops-kit"] == "hermes_ops_kit.bridge:main"
    assert exp.plugin_entry_points["hermes-ops-kit"] == "hermes_ops_kit"
    assert exp.build_backend == "hatchling.build"
    assert "hermes_ops_kit" in exp.packages
    assert exp.fingerprint


def test_resolver_hatch_packages(tmp_path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text(PYPROJECT)
    exp = resolve_expected_state(pp)
    assert exp.packages == ["hermes_ops_kit"]
    assert exp.dependencies == ["requests>=2.31", "PyYAML>=6.0"]


def test_resolver_setuptools_packages(tmp_path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text(
        PYPROJECT.replace(
            '[tool.hatch.build.targets.wheel]\npackages = ["hermes_ops_kit"]',
            "[tool.setuptools]\npackages = ['hermes_ops_kit']",
        )
    )
    exp = resolve_expected_state(pp)
    assert exp.packages == ["hermes_ops_kit"]


def test_resolver_missing_pyproject(tmp_path):
    exp = resolve_expected_state(tmp_path / "nope.toml")
    assert exp.version == ""
    assert exp.console_scripts == {}


def test_resolver_unparseable_pyproject(tmp_path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text("not [ valid toml ===")
    exp = resolve_expected_state(pp)
    assert exp.version == ""


def _report() -> HealthReport:
    return HealthReport(
        overall=HealthStatus.REPAIRABLE,
        runtime=RuntimeContext(python_executable="/venv/bin/python"),
        actual=ActualInstallation(distribution_present=True, version="0.3.0"),
        expected=ExpectedInstallation(version="0.4.0"),
    )


def test_report_json_shape():
    d = _report().to_dict()
    assert set(d) == {
        "schema_version",
        "overall",
        "runtime",
        "actual",
        "expected",
        "findings",
    }
    assert d["schema_version"] == SCHEMA_VERSION == 1
    assert d["overall"] == "REPAIRABLE"


def test_report_json_round_trip():
    d = _report().to_dict()
    text = json.dumps(d)
    assert json.loads(text) == d


def test_console_script_serialization():
    from hermes_ops_kit.install_reconciler.state import ConsoleScript

    s = ConsoleScript(
        name="x",
        entry="m:a",
        script_path="/bin/x",
        shebang="#!/venv/bin/python",
        load_ok=True,
    )
    assert s.to_dict() == {
        "name": "x",
        "entry": "m:a",
        "script_path": "/bin/x",
        "shebang": "#!/venv/bin/python",
        "load_ok": True,
        "load_error": "",
    }
