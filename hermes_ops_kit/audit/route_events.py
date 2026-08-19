"""Hermes Ops Kit — Route Audit Event Emitters.

Convenience functions that write route-specific audit events through
``audit.ledger.write_event()``.  All events land in
``~/.hermes/ops-kit/audit/events.jsonl``.

These functions are safe to call from any ops-kit module — they never
raise on audit failure (a debug log is emitted instead).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def _safe_emit(event_type: str, **kwargs: Any) -> None:
    """Emit an audit event, never raising on failure."""
    try:
        from ..audit.ledger import write_event

        write_event(event_type, data=kwargs)
    except Exception:
        logger.debug("route_events: failed to emit %s", event_type, exc_info=True)


# ── Config lifecycle ────────────────────────────────────────────────────


def emit_route_config_loaded(
    primary_provider: str = "",
    primary_model: str = "",
    aux_count: int = 0,
    fallback_count: int = 0,
) -> None:
    """Emitted once when route configuration is loaded."""
    _safe_emit(
        "route_config_loaded",
        primary_provider=primary_provider,
        primary_model=primary_model,
        aux_count=aux_count,
        fallback_count=fallback_count,
    )


# ── Route selection ─────────────────────────────────────────────────────


def emit_route_selected(
    route_type: str,
    provider: str,
    model: str,
    reason: str = "",
) -> None:
    """A route was selected for a task (primary, aux, fallback, image, assistant)."""
    _safe_emit(
        "route_selected",
        route_type=route_type,
        provider=provider,
        model=model,
        reason=reason,
    )


def emit_aux_route_selected(
    task: str,
    configured_provider: str,
    configured_model: str,
    actual_provider: str,
    actual_model: str,
    reason: str = "",
) -> None:
    """An auxiliary route was resolved for a specific task.

    *configured_* is what config.yaml says; *actual_* is what was used.
    When they differ, a bypass or fallback occurred.
    """
    _safe_emit(
        "aux_route_selected",
        task=task,
        configured_provider=configured_provider,
        configured_model=configured_model,
        actual_provider=actual_provider,
        actual_model=actual_model,
        reason=reason,
    )


def emit_fallback_route_selected(
    provider: str,
    model: str,
    reason: str = "",
) -> None:
    """A fallback provider was activated."""
    _safe_emit(
        "fallback_route_selected",
        provider=provider,
        model=model,
        reason=reason,
    )


def emit_native_fast_path_selected(
    route_name: str,
    reason: str = "",
) -> None:
    """Native (multimodal) fast path was used instead of aux vision LLM."""
    _safe_emit(
        "native_fast_path_selected",
        route=route_name,
        reason=reason,
    )


def emit_image_route_selected(
    route_name: str,
    provider: str,
    model: str,
) -> None:
    """An image generation route was selected."""
    _safe_emit(
        "image_route_selected",
        route=route_name,
        provider=provider,
        model=model,
    )


def emit_assistant_route_selected(
    assistant_id: str,
    capability: str = "",
) -> None:
    """An assistant delegation route was selected."""
    _safe_emit(
        "assistant_route_selected",
        assistant=assistant_id,
        capability=capability,
    )


def emit_route_bypass_detected(
    route_name: str,
    configured_provider: str,
    actual_provider: str,
    reason: str = "",
) -> None:
    """A configured route was bypassed at runtime."""
    _safe_emit(
        "route_bypass_detected",
        route=route_name,
        configured_provider=configured_provider,
        actual_provider=actual_provider,
        reason=reason,
    )


# ── Invocation lifecycle (start/completed pairs) ───────────────────────


def emit_route_invocation_started(
    route_type: str,
    provider: str,
    model: str,
) -> str:
    """Begin tracking a route invocation.  Returns an invocation_id."""
    invocation_id = f"{route_type}-{time.time_ns()}"
    _safe_emit(
        "route_invocation_started",
        invocation_id=invocation_id,
        route_type=route_type,
        provider=provider,
        model=model,
    )
    return invocation_id


def emit_route_invocation_completed(
    invocation_id: str,
    duration_ms: int = 0,
    status: str = "ok",
) -> None:
    """Complete a route invocation."""
    _safe_emit(
        "route_invocation_completed",
        invocation_id=invocation_id,
        duration_ms=duration_ms,
        status=status,
    )


__all__ = [
    "emit_route_config_loaded",
    "emit_route_selected",
    "emit_aux_route_selected",
    "emit_fallback_route_selected",
    "emit_native_fast_path_selected",
    "emit_image_route_selected",
    "emit_assistant_route_selected",
    "emit_route_bypass_detected",
    "emit_route_invocation_started",
    "emit_route_invocation_completed",
]
