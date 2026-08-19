"""Safe subprocess helpers for running ops-kit modules.

All ops-kit subprocesses run as ``python -P -m hermes_ops_kit.<module>``
with the package root (the directory *containing* ``hermes_ops_kit``)
prepended to ``PYTHONPATH``:

* ``-P`` (Python 3.11+) keeps the current working directory off
  ``sys.path``, so a stray ``bridge.py``/``schemas.py`` in the cwd can
  never shadow ops-kit modules.
* ``PYTHONPATH`` is rebuilt from absolute inherited entries only —
  relative or empty entries resolve against the cwd and would defeat
  ``-P``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

__all__ = ["package_root", "module_file", "module_command", "run_module"]

_PACKAGE = "hermes_ops_kit"


def package_root() -> str:
    """Directory containing the ``hermes_ops_kit`` package (repo/plugin root)."""
    return str(Path(__file__).resolve().parent.parent)


def module_file(module: str) -> Path:
    """Filesystem path of a package module (for existence checks)."""
    base = Path(__file__).resolve().parent
    return base.joinpath(*module.split(".")).with_suffix(".py")


def _qualified(module: str) -> str:
    if module == _PACKAGE or module.startswith(_PACKAGE + "."):
        return module
    return f"{_PACKAGE}.{module}"


def module_command(
    module: str, args: Sequence[str] = ()
) -> tuple[list[str], dict[str, str]]:
    """Build ``(cmd, env)`` for ``python -P -m hermes_ops_kit.<module>``."""
    cmd = [sys.executable, "-P", "-m", _qualified(module), *args]
    env = dict(os.environ)
    entries = [package_root()]
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if entry and os.path.isabs(entry) and entry not in entries:
            entries.append(entry)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return cmd, env


def run_module(
    module: str, args: Sequence[str] = (), **kwargs
) -> subprocess.CompletedProcess:
    """Run a package module as a subprocess (drop-in for ``subprocess.run``)."""
    cmd, env = module_command(module, args)
    return subprocess.run(cmd, env=env, **kwargs)
