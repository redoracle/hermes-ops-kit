"""
Hermes Ops Kit — File Permission Enforcement

Ensures secret-bearing files and directories have safe permissions:
  - Directories: 0700 (owner-only)
  - Files:        0600 (owner-only read/write)

Used by the env render pipeline and bootstrap process.
"""

from __future__ import annotations

import os
import stat


def _mode_str(mode: int) -> str:
    return oct(stat.S_IMODE(mode))[2:]


def ensure_dir_700(path: str) -> None:
    """Create *path* if missing, then force 0o700 permissions."""
    os.makedirs(path, exist_ok=True)
    os.chmod(path, stat.S_IRWXU)


def ensure_file_600(path: str) -> None:
    """Force 0o600 permissions on *path* if it exists."""
    if os.path.exists(path):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def verify_permissions(path: str, expected_mode: int) -> bool:
    """Return True if *path* exists and its permission bits equal *expected_mode*."""
    if not os.path.exists(path):
        return False
    actual = stat.S_IMODE(os.stat(path).st_mode)
    return actual == expected_mode


def check_env_file(path: str) -> dict[str, str | bool]:
    """Return a diagnostic dict for a secret env file.

    Example:
        {"path": "/...", "exists": True, "mode": "600", "safe": True, "issue": ""}
    """
    result: dict[str, str | bool] = {
        "path": path,
        "exists": os.path.exists(path),
        "mode": "",
        "safe": False,
        "issue": "",
    }
    if not result["exists"]:
        result["issue"] = "file does not exist"
        return result

    actual = stat.S_IMODE(os.stat(path).st_mode)
    result["mode"] = _mode_str(actual)
    if actual != 0o600:
        result["issue"] = f"expected 600, got {result['mode']}"
    else:
        result["safe"] = True
    return result
