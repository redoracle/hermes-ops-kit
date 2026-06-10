"""Hermes Ops Kit — Policy Decision Helpers.

Convenience functions that combine policy checks with risk-level
assessment and audit logging. Used by commands and tools.
"""

from __future__ import annotations

from policy.engine import (  # pyright: ignore[reportMissingImports]
    PolicyDecision,
    check_assistant_delegate,
    check_key_rotation,
    check_obsidian_write,
    check_secret_backend,
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


def preflight_decision(
    *,
    dry_run: bool = False,
    force_scan: bool = False,
    exclude_plugins: set[str] | None = None,
) -> dict[str, object]:
    """Run preflight enforcement and return a structured decision.

    Convenience wrapper that combines scan → policy → enforcement into
    a single callable function for programmatic use.

    Args:
        dry_run: If True, preview only — don't modify config.
        force_scan: If True, skip cache and force fresh scan.
        exclude_plugins: Plugin IDs trusted by the caller and excluded from
                         enforcement decisions.

    Returns:
        Dict with ``ok``, ``decisions``, ``enforcement``, and ``details``
        keys — same structure as the JSON output of ``hermes-ops-kit preflight``.
    """

    # Capture the enforce module's JSON output
    from security.plugin_scanner.enforce import (  # pyright: ignore[reportMissingImports]
        get_enforcement_decisions,
        get_mcp_enforcement_decisions,
        apply_enforcement,
    )

    from security.plugin_scanner.scanner import scan_all

    results = scan_all(profile="startup", force=force_scan)
    if exclude_plugins:
        results = [r for r in results if r.plugin_name not in exclude_plugins]
    decisions = get_enforcement_decisions(results)
    mcp_decisions = get_mcp_enforcement_decisions()
    enforcement = apply_enforcement(
        decisions, mcp_decisions=mcp_decisions, dry_run=dry_run
    )

    return {
        "ok": decisions["ok"] and mcp_decisions["ok"],
        "decisions": {
            "allowed": decisions["allowed"],
            "approved": decisions["approved"],
            "deferred": decisions["deferred"],
            "blocked": decisions["blocked"],
        },
        "enforcement": enforcement,
        "mcp_decisions": mcp_decisions,
        "details": decisions["details"],
        "scan_duration_ms": decisions["scan_duration_ms"],
    }
