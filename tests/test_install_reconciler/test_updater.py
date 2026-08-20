"""Tests: preflight fast check (unit) and transactional updater (integration)."""

import subprocess
import venv
from pathlib import Path

import pytest

from hermes_ops_kit.install_reconciler.fastcheck import discover_in_process
from hermes_ops_kit.install_reconciler.updater import perform_update

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

NEW = OLD.replace(
    'hermes-drift = "bridge:main"', 'hermes-drift = "drift_sim.bridge:main"'
).replace('py-modules = ["bridge"]', "packages = ['drift_sim']")


# ---- fast check (unit, current interpreter) ----


def test_fastcheck_discovers_installed_ops_kit():
    """The running interpreter has ops-kit installed (editable in dev/CI)."""
    actual = discover_in_process()
    assert actual.distribution_present
    assert actual.version
    assert "hermes-ops-kit" in actual.console_scripts
    assert actual.fingerprint


def test_fastcheck_is_read_only_by_construction():
    import inspect

    from hermes_ops_kit.install_reconciler import fastcheck

    src = inspect.getsource(fastcheck)
    for banned in ("pip install", "uv pip", "subprocess.run", "urllib"):
        assert banned not in src, f"fast path must not use {banned}"


# ---- updater (integration, real venv + real git repo) ----

pytestmark_integration = pytest.mark.integration


def _git_repo(path: Path) -> None:
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)


def _commit(path: Path, msg: str) -> None:
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", msg], check=True)


@pytest.mark.integration
def test_updater_transaction_syncs_repairs_validates(tmp_path):
    """Old layout installed; git moves to new layout; update must sync,
    reconcile, and declare success only with a HEALTHY runtime."""
    venv.create(tmp_path / "venv", with_pip=True)
    python = tmp_path / "venv" / "bin" / "python"
    src = tmp_path / "src"
    src.mkdir()
    (src / "pyproject.toml").write_text(OLD)
    (src / "bridge.py").write_text("def main():\n    return 0\n")
    _git_repo(src)
    _commit(src, "feat: old layout")

    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "-e", str(src)],
        check=True,
        capture_output=True,
        timeout=300,
    )

    # Remote-ish update: new commit with repackaged layout
    pkg = src / "drift_sim"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bridge.py").write_text("def main():\n    return 0\n")
    (src / "bridge.py").unlink()
    (src / "pyproject.toml").write_text(NEW)
    _commit(src, "feat!: repackage")

    # A bare "git pull succeeded" update would leave the runtime broken —
    # prove it: entry-point load fails against the updated source.
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

    outcome = perform_update(
        source=str(src), target_python=str(python), package_name="drift-sim"
    )
    assert outcome.synced
    assert outcome.reconciled
    assert outcome.healthy
    assert outcome.success
    assert outcome.head_after  # local-only repo: HEAD was already new —
    # the transaction still had to reconcile the stale runtime against it

    # Runtime genuinely works post-update
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


@pytest.mark.integration
def test_updater_stops_on_dirty_tree(tmp_path):
    venv.create(tmp_path / "venv", with_pip=True)
    python = tmp_path / "venv" / "bin" / "python"
    src = tmp_path / "src"
    src.mkdir()
    (src / "pyproject.toml").write_text(OLD)
    (src / "bridge.py").write_text("def main():\n    return 0\n")
    _git_repo(src)
    _commit(src, "feat: old layout")
    (src / "local_changes.txt").write_text("dirty\n")  # untracked dirt

    outcome = perform_update(
        source=str(src), target_python=str(python), package_name="drift-sim"
    )
    assert not outcome.success
    assert outcome.stopped_at == "sync"
    assert "dirty" in outcome.reason
    assert not outcome.synced  # nothing mutated


def test_updater_dry_run_is_read_only(tmp_path):
    src = tmp_path / "not-a-repo"
    src.mkdir()
    (src / "pyproject.toml").write_text(OLD)
    outcome = perform_update(source=str(src), dry_run=True)
    assert not outcome.success
    assert outcome.dry_run
    # stopped before any mutation, no ledger pollution on dry-run
    assert outcome.stopped_at in ("source", "dry-run")
