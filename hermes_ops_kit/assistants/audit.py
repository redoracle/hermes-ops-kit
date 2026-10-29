"""Hermes Ops Kit — Assistant Audit Logging

Writes sanitized JSONL audit events to ~/.hermes/assistants/audit.jsonl.
Never stores raw secrets.

Spec section 18 — Example event:
{
  "ts": "2026-06-03T08:00:00Z",
  "caller": "hermes-agent",
  "assistant": "assistant-id",
  "task_id": "...",
  "capability": "security_review",
  "status": "completed",
  "duration_ms": 1840,
  "request_fingerprint": "sha256:...",
  "result_fingerprint": "sha256:...",
  "policy": "read_only",
  "approval_required": false,
  "secrets_detected": false
}
"""

from __future__ import annotations

import json
import os
import time

from ..env.atomic_write import atomic_append  # pyright: ignore[reportMissingImports]
from .. import ops_config_io  # noqa: E402


def audit_path() -> str:
    """Assistant audit file of the active profile, resolved at call time."""
    return os.path.join(ops_config_io.home(), "assistants/audit.jsonl")


# Deprecated import-time alias (compatibility window); the kit uses audit_path().
AUDIT_PATH = audit_path()


def write_audit(
    assistant: str,
    task_id: str,
    capability: str,
    status: str,
    duration_ms: int = 0,
    request_fingerprint: str = "",
    result_fingerprint: str = "",
    approval_required: bool = False,
    secrets_detected: bool = False,
) -> None:
    """Append a sanitized assistant audit event."""
    path = audit_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "caller": "hermes-agent",
        "assistant": assistant,
        "task_id": task_id,
        "capability": capability,
        "status": status,
        "duration_ms": duration_ms,
        "request_fingerprint": request_fingerprint,
        "result_fingerprint": result_fingerprint,
        "policy": "read_only",
        "approval_required": approval_required,
        "secrets_detected": secrets_detected,
    }

    atomic_append(path, json.dumps(event))
