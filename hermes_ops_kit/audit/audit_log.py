"""
Hermes Ops Kit — Audit Logger

Writes sanitized JSONL audit events to ~/.hermes/key-rotation-audit.jsonl.
Never logs raw secret values.
"""

from __future__ import annotations

import json
import os
import time

from ..env.atomic_write import atomic_append  # pyright: ignore[reportMissingImports]
from hermes_ops_kit import ops_config_io  # noqa: E402


AUDIT_PATH = os.path.join(ops_config_io.HERMES_HOME, "key-rotation-audit.jsonl")


def write_audit_event(
    provider: str,
    operation: str,
    status: str,
    old_fingerprint: str | None = None,
    new_fingerprint: str | None = None,
    extra: dict | None = None,
) -> None:
    """Append a sanitized audit event to the JSONL audit file."""
    event: dict = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": provider,
        "operation": operation,
        "status": status,
    }
    if old_fingerprint:
        event["old_fingerprint"] = old_fingerprint
    if new_fingerprint:
        event["new_fingerprint"] = new_fingerprint
    if extra:
        event.update(extra)

    atomic_append(AUDIT_PATH, json.dumps(event))


def write_rotation_phase_event(
    provider: str,
    phase: str,
    status: str,
    old_fingerprint: str | None = None,
    new_fingerprint: str | None = None,
    error: str | None = None,
    duration_ms: int = 0,
) -> None:
    """Write a phase-tracked rotation audit event.

    Used by RotationRunner to log each phase transition.
    operation is set to "rotation.<phase>" for filtering.
    """
    write_audit_event(
        provider=provider,
        operation=f"rotation.{phase}",
        status=status,
        old_fingerprint=old_fingerprint,
        new_fingerprint=new_fingerprint,
        extra={
            "phase": phase,
            "duration_ms": duration_ms,
            "error": error,
        },
    )


def audit_rotation_attempt(
    provider: str,
    status: str,
    old_fp: str | None = None,
    new_fp: str | None = None,
    old_revoked: bool = False,
    manual_action: bool = False,
    env_keys_updated: list[str] | None = None,
) -> None:
    """Write a rotation audit event (spec section 24 format)."""
    write_audit_event(
        provider=provider,
        operation="rotation",
        status=status,
        old_fingerprint=old_fp,
        new_fingerprint=new_fp,
        extra={
            "old_key_revoked": old_revoked,
            "manual_action_required": manual_action,
            "env_keys_updated": env_keys_updated or [],
        },
    )
