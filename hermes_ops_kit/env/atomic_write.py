"""
Hermes Ops Kit — Atomic File Write

Safe atomic write sequence for secret-bearing files:
  1. Write content to <path>.tmp
  2. chmod 0600
  3. fsync
  4. rename to final path
  5. verify permissions

Never logs file content.
"""

from __future__ import annotations

import os
import stat


def atomic_write(
    path: str,
    content: str,
    mode: int = 0o600,
) -> None:
    """Atomically write *content* to *path* with safe permissions.

    Uses temp file + rename to prevent partial reads.
    """
    tmp_path = path + ".tmp"

    # 1. Write temp file
    with open(tmp_path, "w") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    # 2. Set permissions on temp file
    os.chmod(tmp_path, mode)

    # 3. Atomic rename
    os.rename(tmp_path, path)

    # 4. Verify
    actual = stat.S_IMODE(os.stat(path).st_mode)
    if actual != mode:
        os.chmod(path, mode)


def atomic_append(path: str, line: str) -> None:
    """Append a single line to *path* atomically when possible.

    For JSONL audit files. Falls back to append + fsync.
    """
    with open(path, "a") as f:
        f.write(line)
        if not line.endswith("\n"):
            f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def atomic_write_json(path: str, data: object, mode: int = 0o600) -> None:
    """Atomically serialize *data* as JSON to *path* (temp → chmod → rename).

    For state/checkpoint JSON files where a partial write on crash must
    never be observable.
    """
    import json
    import tempfile

    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)),
        prefix=f".{os.path.basename(path)}.",
        text=True,
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
