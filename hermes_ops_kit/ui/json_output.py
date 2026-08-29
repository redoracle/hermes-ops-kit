"""Hermes Ops Kit — Standard JSON output envelope.

Every --json mode across all tools must use this envelope:

    {"ok": true, "command": "list", "version": "<kit version>",
     "timestamp": "2026-06-03T12:00:00Z", "result": {},
     "warnings": [], "errors": []}

On error:
    {"ok": false, ..., "errors": [{"code":"...", "message":"...", "path":"...", "hint":"..."}]}
"""

from __future__ import annotations

import time
from typing import Any

try:  # kit version — keep the envelope version in lockstep with releases
    from hermes_ops_kit import __version__ as VERSION  # noqa: E402
except Exception:  # pragma: no cover
    VERSION = "0.0.0"


def ok_envelope(
    command: str, result: Any, warnings: list[str] | None = None
) -> dict[str, Any]:
    """Build a success envelope."""
    return {
        "ok": True,
        "command": command,
        "version": VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "result": result,
        "warnings": warnings or [],
        "errors": [],
    }


def error_envelope(
    command: str,
    errors: list[dict[str, str]],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build an error envelope."""
    return {
        "ok": False,
        "command": command,
        "version": VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "result": None,
        "warnings": warnings or [],
        "errors": errors,
    }


def error_item(
    code: str, message: str, path: str = "", hint: str = ""
) -> dict[str, str]:
    """Build a single error item."""
    item: dict[str, str] = {"code": code, "message": message}
    if path:
        item["path"] = path
    if hint:
        item["hint"] = hint
    return item
