"""Hermes Ops Kit — Config-Driven Assistant Delegation Tool

Generic delegation entry point for ANY remote Hermes assistant.
All assistant-specific behavior is driven by AssistantConfig from assistants.yaml.

Usage:
    from hermes_ops_kit.assistants.tool import ai_assistant_delegate, build_tool_meta

    # Delegate to any assistant by ID
    result = ai_assistant_delegate(
        "<assistant-id>",
        task="Review this design for security risks.",
        capability="security_review",
    )

    # Build dynamic tool metadata for Hermes tool registry
    meta = build_tool_meta("<assistant-id>")
"""

from __future__ import annotations

import time
import uuid

from ..assistants.base import AssistantTask  # pyright: ignore[reportMissingImports]
from ..assistants.client import AssistantClient, AssistantClientError  # pyright: ignore[reportMissingImports]
from ..assistants.policy import PolicyViolation  # pyright: ignore[reportMissingImports]
from ..assistants.registry import get_assistant  # pyright: ignore[reportMissingImports]
from ..assistants.result_sanitizer import sanitize_result  # pyright: ignore[reportMissingImports]
from ..security.redaction import redact  # pyright: ignore[reportMissingImports]


# ── Dynamic Tool Metadata ──────────────────────────────────────────


def build_tool_meta(assistant_id: str) -> dict:
    """Build tool metadata dynamically from the assistant's config.

    Called at tool registration time (not import time) so the config
    is always fresh.  Safe for Hermes tool registry consumption.
    """
    config = get_assistant(assistant_id)
    if not config:
        return {"name": f"ai_{assistant_id}_delegate", "error": "not in registry"}

    all_caps = [c["id"] for c in config.capabilities]

    return {
        "name": config.tool_name or f"ai_{assistant_id}_delegate",
        "category": "assistant",
        "assistant": assistant_id,
        "description": (
            f"Delegates a bounded read-only task from {config.orchestrator_name} "
            f"to {config.display_name}."
        ),
        "safe_by_default": True,
        "requires_approval": False,
        "can_mutate_files": config.allow_file_mutation,
        "can_execute_shell": config.allow_shell_execution,
        "can_access_network": True,
        "can_access_repo": config.allow_repo_write,
        "uses_remote_agent": True,
        "uses_secret": config.api_key_env,
        "timeout_seconds": config.max_timeout_seconds,
        "input_schema": {
            "type": "object",
            "required": ["assistant_id", "task"],
            "properties": {
                "assistant_id": {"type": "string", "default": assistant_id},
                "task": {"type": "string"},
                "capability": {"type": "string", "enum": all_caps},
                "context": {"type": "object"},
                "constraints": {"type": "object"},
            },
        },
    }


# ── Delegation Entry Point ─────────────────────────────────────────


def ai_assistant_delegate(
    assistant_id: str,
    task: str,
    capability: str = "review",
    context: dict | None = None,
    constraints: dict | None = None,
) -> dict:
    """Delegate a bounded task to any remote Hermes assistant.

    This is the generic entry point.  All assistant-specific behavior
    is read from the assistant's entry in config/assistants.yaml.

    Args:
        assistant_id: The assistant ID (e.g. "<assistant-id>").
        task: The task description in natural language.
        capability: The capability to invoke (must be in allowlist).
        context: Optional context dict (caller, repo, mode, etc.).
        constraints: Optional constraints (timeout, security flags).

    Returns a JSON-serializable dict with ok, assistant, task_id, result.
    """
    start = time.time()

    # Load config
    config = get_assistant(assistant_id)
    if not config:
        return {
            "ok": False,
            "error": f"Assistant '{assistant_id}' not found in registry",
            "assistant": assistant_id,
        }

    if not config.enabled:
        return {
            "ok": False,
            "error": f"Assistant '{assistant_id}' is disabled",
            "assistant": assistant_id,
        }

    # Build task
    task_id = f"asstask_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    assistant_task = AssistantTask(
        task_id=task_id,
        assistant_id=assistant_id,
        capability=capability,
        task=task,
        context=context
        or {
            "caller": config.orchestrator_name.lower(),
            "mode": "read_only",
        },
        constraints=constraints
        or {
            "no_secret_access": True,
            "no_env_dump": True,
            "no_file_write": not config.allow_file_mutation,
            "no_shell_execution": not config.allow_shell_execution,
            "no_network_scan": True,
            "timeout_seconds": config.max_timeout_seconds,
        },
    )

    # Delegate
    client = AssistantClient(config)

    try:
        result = client.delegate(assistant_task)
    except PolicyViolation as e:
        return {
            "ok": False,
            "assistant": assistant_id,
            "task_id": task_id,
            "error": f"Policy blocked: {redact(str(e))}",
            "duration_ms": int((time.time() - start) * 1000),
        }
    except AssistantClientError as e:
        return {
            "ok": False,
            "assistant": assistant_id,
            "task_id": task_id,
            "error": redact(str(e)),
            "duration_ms": int((time.time() - start) * 1000),
        }

    # Sanitize and return
    output = {
        "ok": result.ok,
        "assistant": result.assistant,
        "task_id": result.task_id,
        "transport": result.transport,
        "duration_ms": result.duration_ms,
        "result": result.result,
        "warnings": result.warnings,
    }

    # Audit
    _write_audit(assistant_id, task_id, capability, result.ok, result.duration_ms)

    return sanitize_result(output)


def _write_audit(
    assistant_id: str,
    task_id: str,
    capability: str,
    success: bool,
    duration_ms: int,
) -> None:
    """Write sanitized audit event."""
    from ..assistants.audit import write_audit  # pyright: ignore[reportMissingImports]

    write_audit(
        assistant=assistant_id,
        task_id=task_id,
        capability=capability,
        status="completed" if success else "failed",
        duration_ms=duration_ms,
    )
