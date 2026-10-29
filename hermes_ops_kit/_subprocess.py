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

Children that act for a Hermes profile get ``child_env()``: inside Hermes it is
core's served-profile environment (launch-profile credentials scrubbed for a
routed profile, the profile's own overlaid); standalone it is the parent's
environment. ``HERMES_HOME`` is always passed, so the child resolves the same
profile. Tools that need no credentials get ``minimal_env()``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

from . import ops_config_io

__all__ = [
    "package_root",
    "module_file",
    "module_command",
    "run_module",
    "child_env",
    "minimal_env",
]

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


# Variables a credential-free tool (scanner, linters) legitimately needs. Proxy
# variables are excluded: they may embed credentials.
_MINIMAL_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
    "TEMP", "TMP", "TERM", "SYSTEMROOT", "COMSPEC", "PATHEXT",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
)  # fmt: skip


def child_env(
    extra: Mapping[str, str] | None = None, *, credentials: bool = True
) -> dict[str, str]:
    """Environment for a child that acts for the active Hermes profile.

    Embedded in Hermes: core's ``served_profile_child_env`` for the active home
    (``credentials`` chooses whether the profile's own secrets are overlaid).
    A missing core helper or an unscoped read under multiplexing raises; it
    never falls back to the launch profile's environment. Standalone: a copy
    of ``os.environ``. ``HERMES_HOME`` is always set to the active home.
    """
    active_home = ops_config_io.home()
    if ops_config_io.core_loaded():
        build = ops_config_io.native(
            "tools.environments.local", "served_profile_child_env"
        )
        env = dict(build(target_home=active_home, inherit_credentials=credentials))
    else:
        env = dict(os.environ)
    env["HERMES_HOME"] = active_home
    if extra:
        env.update(extra)
    return env


def minimal_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """A credential-free environment: only what a plain tool needs to run."""
    env = {key: os.environ[key] for key in _MINIMAL_KEYS if key in os.environ}
    if extra:
        env.update(extra)
    return env


def module_command(
    module: str, args: Sequence[str] = ()
) -> tuple[list[str], dict[str, str]]:
    """Build ``(cmd, env)`` for ``python -P -m hermes_ops_kit.<module>``."""
    cmd = [sys.executable, "-P", "-m", _qualified(module), *args]
    env = child_env()
    entries = [package_root()]
    for entry in env.get("PYTHONPATH", "").split(os.pathsep):
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
