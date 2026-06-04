"""Hermes Ops Kit — Unified Audit Ledger.

Single audit trail for all bridge events:
  route_changed, assistant_called, key_rotated, secret_backend_checked,
  obsidian_note_written, provider_failed, fallback_used, policy_denied.

All events → ~/.hermes/ops-kit/audit/events.jsonl
No raw secrets stored.
"""

from __future__ import annotations

import json
import os
import time

AUDIT_DIR = os.path.expanduser("~/.hermes/ops-kit/audit")
AUDIT_PATH = os.path.join(AUDIT_DIR, "events.jsonl")

VALID_EVENT_TYPES = {
    "route_changed",
    "assistant_called",
    "assistant_restricted_served",
    "key_rotated",
    "secret_backend_checked",
    "obsidian_note_written",
    "provider_failed",
    "fallback_used",
    "policy_denied",
    "config_changed",
    "assistant_added",
    # Route observability (Phase 4)
    "route_config_loaded",
    "route_selected",
    "route_invocation_started",
    "route_invocation_completed",
    "aux_route_selected",
    "fallback_route_selected",
    "native_fast_path_selected",
    "image_route_selected",
    "assistant_route_selected",
    "route_bypass_detected",
    "assistant_removed",
    "rotation_started",
    "rotation_completed",
    "doctor_checked",
    # MCP auditor
    "mcp_audit_started",
    "mcp_audit_completed",
    "mcp_tool_risk_detected",
    "mcp_policy_generated",
    "mcp_policy_violation",
    "mcp_metadata_injection_detected",
    # Vault scheduler
    "obsidian_schedule_installed",
    "obsidian_schedule_removed",
    "obsidian_schedule_triggered",
    "assistant_tasks_started",
    "assistant_tasks_completed",
    "assistant_tasks_failed",
    # Cost governor
    "budget_checked",
    "budget_warning",
    "budget_throttle_enabled",
    "budget_provider_blocked",
    "budget_route_rerouted",
    "budget_override_created",
    "budget_override_expired",
}


def write_event(event_type: str, data: dict | None = None, **kwargs) -> None:
    """Write a single audit event to the ledger.

    Args:
        event_type: One of the VALID_EVENT_TYPES.
        data: Optional event-specific dict.
        **kwargs: Additional fields merged into the event.
    """
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(
            f"Invalid event type: {event_type}. Valid: {sorted(VALID_EVENT_TYPES)}"
        )

    os.makedirs(AUDIT_DIR, exist_ok=True)

    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": event_type,
    }
    if data:
        event.update(data)
    event.update(kwargs)

    with open(AUDIT_PATH, "a") as f:
        f.write(json.dumps(event, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def tail_events(limit: int = 20) -> list[dict]:
    """Return the most recent N events."""
    if not os.path.exists(AUDIT_PATH):
        return []
    events = []
    with open(AUDIT_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events[-limit:]


def search_events(
    event_type: str | None = None,
    assistant: str | None = None,
    provider: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Search audit events with optional filters."""
    if not os.path.exists(AUDIT_PATH):
        return []
    results = []
    with open(AUDIT_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type and evt.get("type") != event_type:
                continue
            if assistant and evt.get("assistant") != assistant:
                continue
            if provider and evt.get("provider") != provider:
                continue
            if since and evt.get("ts", "") < since:
                continue
            results.append(evt)
            if len(results) >= limit:
                break
    return results


def count_events(event_type: str | None = None, since: str | None = None) -> int:
    """Count matching events."""
    return len(search_events(event_type=event_type, since=since, limit=10000))
