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
from dataclasses import dataclass
from pathlib import Path

INSTALL_TIMEOUT = 600


@dataclass
class InstallResult:
    installer: str
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

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
) -> list[str]:
    """Canonical argv for reinstalling *source* into *target_python*."""
    canonical_source = str(Path(source).resolve())
    if installer == "uv":
        argv = ["uv", "pip", "install", "-p", str(Path(target_python).resolve())]
        if editable:
            argv.append("--editable")
        argv.append(canonical_source)
        return argv
    argv = [
        str(Path(target_python).resolve()),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
    ]
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
    structured result carries the exit code for the caller to verify."""
    chosen = installer or select_installer()
    argv = build_install_argv(chosen, target_python, source, editable)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT,
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
