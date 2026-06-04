"""Hermes Ops Kit — Policy Decision Helpers.

Convenience functions that combine policy checks with risk-level
assessment and audit logging. Used by commands and tools.
"""

from __future__ import annotations

from policy.engine import (  # pyright: ignore[reportMissingImports]
    PolicyDecision,
    allow,
    deny,
    require_approval,
    check_assistant_delegate,
    check_key_rotation,
    check_obsidian_write,
    check_secret_backend,
    scan_for_secrets,
)


def can_delegate_to_assistant(
    assistant_id: str,
    task: str,
    capability: str = "",
    constraints: dict | None = None,
) -> PolicyDecision:
    """Full delegation check: secrets, capability, constraints."""
    decision = check_assistant_delegate(task, capability, constraints)
    if not decision.allowed:
        _log_denial("assistant_delegate", decision.reason, assistant_id)
    return decision


def can_rotate_key(provider: str, mode: str) -> PolicyDecision:
    """Full key rotation check."""
    decision = check_key_rotation(mode)
    if not decision.allowed:
        _log_denial("key_rotation", decision.reason, provider)
    return decision


def can_write_to_obsidian(content: str, note_path: str = "") -> PolicyDecision:
    """Full Obsidian write check."""
    decision = check_obsidian_write(content)
    if not decision.allowed:
        _log_denial("obsidian_write", decision.reason, note_path)
    return decision


def is_secret_backend_safe(url: str, tls_ok: bool) -> PolicyDecision:
    """Full secret backend security check."""
    return check_secret_backend(url, tls_ok)


def _log_denial(rule: str, reason: str, context: str = "") -> None:
    """Log a policy denial to the unified audit ledger."""
    try:
        from audit.ledger import write_event  # pyright: ignore[reportMissingImports]

        write_event(
            "policy_denied",
            {
                "rule": rule,
                "reason": reason,
                "context": context,
            },
        )
    except Exception:
        pass
