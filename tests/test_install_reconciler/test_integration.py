"""Integration tests: reproduce the original incident in a real venv.

Scenario 1 (old layout → new layout without reinstall) and scenario 10
(healthy editable + .py-only change → no drift). These are the only
tests that create real virtualenvs and run real pip installs — keep
this file small so the suite stays fast.
"""

import subprocess
import venv
from pathlib import Path

import pytest

from hermes_ops_kit.install_reconciler.cli import run_install_doctor
from hermes_ops_kit.install_reconciler.state import HealthStatus

pytestmark = pytest.mark.integration

OLD_PYPROJECT = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "drift-sim"
version = "0.1.0"

[project.scripts]
hermes-drift = "bridge:main"

[tool.setuptools]
py-modules = ["bridge"]
"""


def _make_venv(tmp_path: Path) -> Path:
    venv_dir = tmp_path / "venv"
    venv.create(venv_dir, with_pip=True)
    return venv_dir / "bin" / "python"


def _install(python: Path, source: Path) -> None:
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "-e", str(source)],
        check=True,
        capture_output=True,
        timeout=300,
    )


def test_old_layout_to_new_layout_without_reinstall(tmp_path):
    """The exact incident: editable install of bridge:main, source
    repackaged to hermes_ops_kit/ layout + new entry-point, NO reinstall."""
    python = _make_venv(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text(OLD_PYPROJECT)
    (source / "bridge.py").write_text("def main():\n    return 0\n")

    _install(python, source)

    # Old runtime works before the change
    probe = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "from importlib.metadata import entry_points;"
            "e=[x for x in entry_points(group='console_scripts') if x.name=='hermes-drift'][0];"
            "e.load(); print('ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr

    # ---- git pull equivalent: source changes, no reinstall ----
    pkg = source / "drift_sim"
    pkg.mkdir()
    (pkg / "bridge.py").write_text("def main():\n    return 0\n")
    (pkg / "__init__.py").write_text("")
    (source / "bridge.py").unlink()
    (source / "pyproject.toml").write_text(
        OLD_PYPROJECT.replace(
            'hermes-drift = "bridge:main"', 'hermes-drift = "drift_sim.bridge:main"'
        ).replace('py-modules = ["bridge"]', "packages = ['drift_sim']")
    )

    report = run_install_doctor(str(python), str(source), package_name="drift-sim")
    codes = [f.code for f in report.findings]
    assert "CONSOLE_ENTRYPOINT_DRIFT" in codes
    assert "RUNTIME_PROBE_FAILURE" in codes
    assert report.overall is HealthStatus.DIAGNOSE_ONLY

    # Runtime is genuinely broken — prove the incident is real
    broken = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "from importlib.metadata import entry_points;"
            "e=[x for x in entry_points(group='console_scripts') if x.name=='hermes-drift'][0];"
            "e.load()",
        ],
        capture_output=True,
        text=True,
    )
    assert broken.returncode != 0
    assert "No module named" in broken.stderr


def test_healthy_editable_py_only_change_no_drift(tmp_path):
    """Scenario 10: editable install coherent; touching only .py files
    must NOT produce drift findings."""
    python = _make_venv(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        OLD_PYPROJECT.replace('py-modules = ["bridge"]', "packages = ['bridge_pkg']")
    )
    pkg = source / "bridge_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bridge.py").write_text("def main():\n    return 0\n")
    (source / "pyproject.toml").write_text(
        (source / "pyproject.toml")
        .read_text()
        .replace(
            'hermes-drift = "bridge:main"', 'hermes-drift = "bridge_pkg.bridge:main"'
        )
    )

    _install(python, source)

    # Implementation-only change: edit a .py, metadata untouched
    (pkg / "bridge.py").write_text(
        "def main():\n    return 1  # changed implementation\n"
    )

    report = run_install_doctor(str(python), str(source), package_name="drift-sim")
    assert report.overall is HealthStatus.HEALTHY
    assert report.findings == []
