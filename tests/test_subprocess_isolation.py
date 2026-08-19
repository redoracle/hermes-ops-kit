"""-P isolation security tests (plan amendment A).

Package modules spawned via ``-P -m`` from a hostile cwd:
1. sentinel-shadow — a cwd directory/file named like a package top-level
   must never satisfy an import;
2. contamination — stray ``requests.py``/``yaml.py`` in the cwd must not
   leak into the module's dependency imports.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from hermes_ops_kit._subprocess import module_command  # noqa: E402


def _run_from_cwd(cwd: Path, module: str, *args: str) -> subprocess.CompletedProcess:
    cmd, env = module_command(module, list(args))
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(cwd), env=env, timeout=180
    )


def test_cwd_shadow_cannot_satisfy_imports(tmp_path):
    """Poisoned schemas/ (dir + module) in cwd must be inert under -P."""
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "__init__.py").write_text(
        'raise RuntimeError("SHADOWED-SCHEMAS")\n'
    )
    (tmp_path / "schemas.py").write_text('raise RuntimeError("SHADOWED-SCHEMAS")\n')
    r = _run_from_cwd(tmp_path, "bridge", "capabilities")
    assert r.returncode == 0, r.stderr
    assert "SHADOWED-SCHEMAS" not in r.stdout + r.stderr


def test_cwd_contamination_modules_inert(tmp_path):
    """Stray requests.py / yaml.py in cwd must never be imported."""
    for name in ("requests", "yaml"):
        (tmp_path / f"{name}.py").write_text(
            f'raise RuntimeError("CONTAMINATED-{name}")\n'
        )
    r = _run_from_cwd(tmp_path, "usage_metrics_v2", "--json")
    assert r.returncode == 0, r.stderr
    assert "CONTAMINATED" not in r.stdout + r.stderr
