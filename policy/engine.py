"""Hermes Ops Kit — Policy Engine.

Centralized decision engine for all security policy questions.
Evaluates declarative rules from policy/rules.yaml.
"""

from __future__ import annotations

import os
import re
from typing import Any

# ─── Load rules ───────────────────────────────────────────────────

_RULES: dict[str, Any] | None = None


def _load_rules() -> dict[str, Any]:
    global _RULES
    if _RULES is not None:
        return _RULES
    paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.yaml"),
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

                with open(p) as f:
                    loaded = _yaml.safe_load(f) or {}
                _RULES = loaded if isinstance(loaded, dict) else {}
                return _RULES
            except Exception:
                pass
    _RULES = {}
    return _RULES


def reload_rules() -> None:
    global _RULES
    _RULES = None


# ─── Decision Types ───────────────────────────────────────────────


class PolicyDecision:
    """Result of a policy check."""

    def __init__(self, allowed: bool, reason: str = "", require_approval: bool = False):
        self.allowed = allowed
        self.reason = reason
        self.require_approval = require_approval

    def __bool__(self) -> bool:
        return self.allowed


def allow(reason: str = "") -> PolicyDecision:
    return PolicyDecision(True, reason)


def deny(reason: str) -> PolicyDecision:
    return PolicyDecision(False, reason)


def require_approval(reason: str) -> PolicyDecision:
    return PolicyDecision(True, reason, require_approval=True)


# ─── Secret Scanning ──────────────────────────────────────────────


def scan_for_secrets(content: str) -> tuple[bool, list[str]]:
    """Scan content for raw secrets. Returns (clean, violations)."""
    rules = _load_rules()
    patterns = rules.get("secrets", {}).get("patterns", [])
    violations = []
    for p in patterns:
        try:
            if re.search(p["regex"], content, re.IGNORECASE):
                violations.append(f"secret pattern detected: {p['name']}")
        except re.error:
            pass
    return len(violations) == 0, violations


def scan_suspicious_keys(data: dict[str, Any]) -> list[str]:
    """Check for suspicious key names in a dict."""
    rules = _load_rules()
    suspicious = set(rules.get("secrets", {}).get("suspicious_key_names", []))
    violations = []

    def _walk(d: Any, path: str) -> None:
        if isinstance(d, dict):
            for k, v in d.items():
                if k in suspicious and not k.endswith("_env"):
                    violations.append(
                        f"suspicious key '{k}' at {path}.{k} (use {k}_env instead)"
                    )
                _walk(v, f"{path}.{k}" if path else k)
        elif isinstance(d, list):
            for i, item in enumerate(d):
                _walk(item, f"{path}[{i}]")

    _walk(data, "")
    return violations


# ─── Policy Checks ────────────────────────────────────────────────


def check_assistant_delegate(
    task: str,
    capability: str = "",
    constraints: dict[str, Any] | None = None,
) -> PolicyDecision:
    """Check if a task can be delegated to a remote assistant."""
    rules = _load_rules().get("rules", {}).get("assistant_delegate", {})

    # Deny if contains forbidden patterns
    clean, violations = scan_for_secrets(task)
    if not clean:
        return deny(f"Secret detected in task: {'; '.join(violations)}")

    deny_if = rules.get("deny_if_contains", [])
    if "private_key" in deny_if and "-----BEGIN" in task:
        return deny("Private key detected in task")

    # Require approval for restricted actions
    require_approval_for = rules.get("require_approval_for", [])
    if constraints:
        for constraint_key in require_approval_for:
            if constraints.get(constraint_key) is False:
                return require_approval(
                    f"Constraint '{constraint_key}' requires approval"
                )

    if capability in require_approval_for:
        return require_approval(f"Capability '{capability}' requires approval")

    return allow("Task passed policy checks")


def check_key_rotation(mode: str) -> PolicyDecision:
    """Check if a key rotation operation is allowed."""
    rules = _load_rules().get("rules", {}).get("key_rotation", {})

    allowed = rules.get("allow", [])
    if mode in allowed:
        return allow(f"Mode '{mode}' is allowed")

    require_approval_for = rules.get("require_approval_for", [])
    if mode in require_approval_for:
        return require_approval(f"Mode '{mode}' requires approval")

    return deny(f"Mode '{mode}' is not permitted")


def check_obsidian_write(content: str) -> PolicyDecision:
    """Check if content is safe to write to Obsidian."""
    clean, violations = scan_for_secrets(content)
    if not clean:
        return deny(f"Secrets detected: {'; '.join(violations)}")
    return allow("Content is safe for Obsidian")


def check_secret_backend(url: str, tls_ok: bool) -> PolicyDecision:
    """Check secret backend transport security."""
    rules = _load_rules().get("rules", {}).get("secret_backend", {})
    deny_conditions = rules.get("deny_if", [])

    if "http_transport" in deny_conditions and url.startswith("http://"):
        return deny("HTTP transport is not allowed for secret backend")
    if "tls_verification_failed" in deny_conditions and not tls_ok:
        return deny("TLS verification failed for secret backend")

    return allow("Secret backend transport is secure")


def check_plugin_security(risk_level: str, is_approved: bool) -> PolicyDecision:
    """Check if a plugin should be allowed based on its security scan result.

    Args:
        risk_level: The scanned risk level (none, low, medium, high, critical).
        is_approved: Whether the plugin/finding is explicitly approved.

    Returns:
        PolicyDecision indicating allow/deny/require_approval.
    """
    rules = _load_rules().get("rules", {}).get("plugin_security", {})

    # Critical risk → always deny unless explicitly in blocked-override list
    if risk_level == "critical":
        deny_conditions = rules.get("deny_if", [])
        if "risk_level_critical" in deny_conditions:
            return deny("Plugin has critical risk findings — blocked by policy")
        # Even without explicit deny rule, critical should block
        return deny("Plugin has critical risk findings")

    # High risk → deny unless approved
    if risk_level == "high":
        require_approval_for = rules.get("require_approval_for", [])
        if "risk_level_high" in require_approval_for and not is_approved:
            return require_approval("Plugin has high risk findings — approval required")
        if is_approved:
            return allow("High-risk plugin explicitly approved")

    # Medium risk → require approval
    if risk_level == "medium":
        require_approval_for = rules.get("require_approval_for", [])
        if "risk_level_medium" in require_approval_for and not is_approved:
            return require_approval(
                "Plugin has medium risk findings — approval required"
            )
        if is_approved:
            return allow("Medium-risk plugin explicitly approved")

    # Low/none → allow
    allowed = rules.get("allow", [])
    if "risk_level_low" in allowed and risk_level == "low":
        return allow("Low risk — allowed by policy")
    if "risk_level_none" in allowed and risk_level == "none":
        return allow("No risk — allowed by policy")

    # Default: low/none are fine, everything else needs approval
    if risk_level in ("none", "low"):
        return allow(f"Risk level '{risk_level}' — allowed by default")

    return require_approval(f"Risk level '{risk_level}' — requires approval")
