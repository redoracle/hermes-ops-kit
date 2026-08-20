"""Integration tests: repair end-to-end in a real venv.

Covers: repair fixes the reproduced incident and reinspection confirms
HEALTHY; second repair is a no-op (idempotence); installer failure never
declares success.
"""

import os
import subprocess
import venv
from pathlib import Path

import pytest

from hermes_ops_kit.install_reconciler.cli import run_install_doctor
from hermes_ops_kit.install_reconciler.repair import perform_repair
from hermes_ops_kit.install_reconciler.state import HealthStatus

pytestmark = pytest.mark.integration

OLD = """\
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


def _setup(tmp_path: Path, script_name: str = "hermes-drift") -> tuple[Path, Path]:
    """Venv + old-layout editable install, then source repackaged
    without reinstall — the incident state."""
    venv_dir = tmp_path / "venv"
    venv.create(venv_dir, with_pip=True)
    python = venv_dir / "bin" / "python"
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text(OLD.replace("hermes-drift", script_name))
    (source / "bridge.py").write_text("def main():\n    return 0\n")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "-e", str(source)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    # git-pull equivalent: repackage without reinstall
    pkg = source / "drift_sim"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bridge.py").write_text("def main():\n    return 0\n")
    (source / "bridge.py").unlink()
    (source / "pyproject.toml").write_text(
        OLD.replace("hermes-drift", script_name)
        .replace('bridge:main', 'drift_sim.bridge:main')
        .replace('py-modules = ["bridge"]', "packages = ['drift_sim']")
    )
    return python, source


def test_repairs_incident_and_reevaluates(tmp_path):
    python, source = _setup(tmp_path)

    before = run_install_doctor(str(python), str(source), package_name="drift-sim")
    assert before.overall is not HealthStatus.HEALTHY

    outcome = perform_repair(before, package_name="drift-sim")

    assert outcome.plan is not None and outcome.plan.safe_to_apply
    assert outcome.install is not None and outcome.install.ok
    assert outcome.changed
    assert outcome.after is not None
    assert outcome.after.overall is HealthStatus.HEALTHY, [
        f.code for f in outcome.after.findings
    ]
    assert outcome.success

    # The runtime genuinely works now (EntryPoint.load() succeeds)
    fixed = subprocess.run(
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
    assert fixed.returncode == 0, fixed.stderr


def test_repair_is_idempotent(tmp_path):
    python, source = _setup(tmp_path)

    first = run_install_doctor(str(python), str(source), package_name="drift-sim")
    outcome1 = perform_repair(first, package_name="drift-sim")
    assert outcome1.success

    # Second doctor --repair: no-op on healthy runtime
    second = run_install_doctor(str(python), str(source), package_name="drift-sim")
    assert second.overall is HealthStatus.HEALTHY
    outcome2 = perform_repair(second, package_name="drift-sim")
    assert outcome2.success
    assert not outcome2.changed
    assert outcome2.install is None  # nothing executed


def test_repair_recreates_missing_generated_wrapper(tmp_path):
    """A wrapper missing from the target venv must not be masked by PATH."""
    python, source = _setup(tmp_path)
    first = run_install_doctor(str(python), str(source), package_name="drift-sim")
    assert perform_repair(first, package_name="drift-sim").success

    wrapper = python.parent / "hermes-drift"
    wrapper.unlink()
    before = run_install_doctor(str(python), str(source), package_name="drift-sim")
    assert "GENERATED_EXECUTABLE_DRIFT" in [f.code for f in before.findings]

    outcome = perform_repair(before, package_name="drift-sim")
    assert outcome.success
    assert wrapper.is_file()
    assert outcome.after is not None
    assert outcome.after.overall is HealthStatus.HEALTHY


def test_repair_replaces_recognized_legacy_path_shim(tmp_path, monkeypatch):
    """The Whiplash launcher class must be found and fixed automatically."""
    python, source = _setup(tmp_path, script_name="hermes-route-manager")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "-e", str(source)],
        check=True,
        capture_output=True,
        timeout=300,
    )

    shadow_dir = tmp_path / "shadow"
    shadow_dir.mkdir()
    shadow = shadow_dir / "hermes-route-manager"
    shadow.write_text(
        '#!/usr/bin/env bash\nexec /usr/bin/python3 '
        '"/home/test/.hermes/plugins/hermes-ops-kit/hermes_route_manager.py" "$@"\n'
    )
    shadow.chmod(0o700)
    monkeypatch.setenv("PATH", f"{shadow_dir}{os.pathsep}{os.environ['PATH']}")

    before = run_install_doctor(str(python), str(source), package_name="drift-sim")
    finding = next(f for f in before.findings if f.code == "PATH_SHADOWED_EXECUTABLE")
    assert before.overall is HealthStatus.REPAIRABLE
    assert finding.repairable

    outcome = perform_repair(before, package_name="drift-sim")
    assert outcome.success
    assert shadow.is_symlink()
    assert os.path.realpath(shadow) == os.path.realpath(str(python.parent / shadow.name))


def test_installer_failure_never_fakes_success(tmp_path, monkeypatch):
    python, source = _setup(tmp_path)

    from hermes_ops_kit.install_reconciler import repair as repair_mod

    def _boom(target_python, source, editable, installer=None):
        from hermes_ops_kit.install_reconciler.installer_adapter import InstallResult

        return InstallResult(
            installer="pip", argv=["pip"], returncode=1, stdout="", stderr="boom"
        )

    monkeypatch.setattr(repair_mod, "run_install", _boom)
    report = run_install_doctor(str(python), str(source), package_name="drift-sim")
    outcome = perform_repair(report, package_name="drift-sim")
    assert not outcome.success
    assert outcome.changed is False
    assert "rc=1" in outcome.failure_reason()
