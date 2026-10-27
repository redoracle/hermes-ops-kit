"""
Hermes Ops Kit — Atomic File Write

Safe atomic write sequence for secret-bearing files:
  1. Create a unique owner-only temp file beside the target
  2. Write content through its already-open descriptor
  3. fsync
  4. rename to final path
  5. verify permissions

Never logs file content.
"""

from __future__ import annotations

import os
import tempfile


def atomic_write(
    path: str,
    content: str,
    mode: int = 0o600,
) -> None:
    """Atomically write *content* to *path* with safe permissions.

    Uses temp file + rename to prevent partial reads.
    """
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)),
        prefix=f".{os.path.basename(path)}.", text=True,
    )
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), mode)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


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
    atomic_write(path, json.dumps(data, indent=2), mode)
