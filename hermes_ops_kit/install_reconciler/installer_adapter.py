"""InstallerAdapter — run pip/uv repairs as structured argv commands.

Rules (non-negotiable):
* argv arrays only — never ``shell=True``
* never sudo
* target interpreter explicit via ``-p <python>`` (uv) or ``-m`` (pip)
* source canonicalized to an absolute path (no implicit source switching)
* structured result: returncode + captured stdout/stderr verified by caller
"""

from __future__ import annotations

import shutil
import subprocess
import os
from dataclasses import dataclass
from pathlib import Path
from .._subprocess import child_env
from .discovery import classify_ownership
from .state import mutation_refusal

INSTALL_TIMEOUT = 600
REFUSED_RETURNCODE = -2


@dataclass
class InstallResult:
    installer: str
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    refused: bool = False  # nothing was launched: the guard declined to mutate

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.refused

    def summary(self) -> str:
        tail = (self.stderr or self.stdout).strip().splitlines()
        return tail[-1] if tail else ""


def select_installer() -> str:
    """Prefer uv when available; fall back to the target's own pip."""
    return "uv" if shutil.which("uv") else "pip"


def build_install_argv(
    installer: str,
    target_python: str,
    source: str,
    editable: bool,
    no_deps: bool = True,
) -> list[str]:
    """Canonical argv for reinstalling *source* into *target_python*.

    ``no_deps`` (default) keeps a reinstall packaging-only: the installer must
    not resolve, install or upgrade dependencies.
    """
    canonical_source = str(Path(source).resolve())
    # Do not resolve the interpreter: venv/bin/python is normally a symlink
    # to the base interpreter. Resolving it would install into the base
    # environment, while the doctor correctly probes the venv path.
    canonical_python = os.path.abspath(os.path.expanduser(target_python))
    if installer == "uv":
        argv = ["uv", "pip", "install", "-p", canonical_python]
        if no_deps:
            argv.append("--no-deps")
        if editable:
            argv.append("--editable")
        argv.append(canonical_source)
        return argv
    argv = [
        canonical_python,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
    ]
    if no_deps:
        argv.append("--no-deps")
    if editable:
        argv.append("--editable")
    argv.append(canonical_source)
    return argv


def run_install(
    target_python: str,
    source: str,
    editable: bool,
    installer: str | None = None,
) -> InstallResult:
    """Execute the reinstall. Never raises on installer failure — the
    structured result carries the exit code for the caller to verify.

    This is the single choke point for environment mutation, so it re-derives
    ownership itself at call time: a stale or hand-built plan cannot reach a
    Hermes-managed or ambiguous environment. A refusal is a result (``refused``),
    and nothing is launched.
    """
    chosen = installer or select_installer()

    def refused(reason: str) -> InstallResult:
        return InstallResult(
            installer=chosen,
            argv=[],
            returncode=REFUSED_RETURNCODE,
            stdout="",
            stderr=f"refused: {reason}",
            refused=True,
        )

    ownership = classify_ownership(target_python)
    reason = mutation_refusal(ownership.role, ownership.reason)
    if reason:
        return refused(reason)
    argv = build_install_argv(chosen, target_python, source, editable)
    try:
        env = child_env(credentials=False)
    except RuntimeError as exc:  # core refused to scope the environment (or lacks the API)
        return refused(f"no profile-scoped environment available ({exc})")
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT,
            env=env,
        )
        return InstallResult(
            installer=chosen,
            argv=argv,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return InstallResult(
            installer=chosen,
            argv=argv,
            returncode=-1,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
        )
