"""Hermes Ops Kit — Plugin Scanner: Approval Policy.

Manages plugin_policy.json for approve/revoke/disable/enable/block
decisions. Follows the same atomic-write pattern as mcp/auditor.py.

Policy file: ~/.hermes/ops-kit/plugin_policy.json
Audit trail: ~/.hermes/ops-kit/plugin_policy_audit.jsonl
"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time
from typing import Any

from security.plugin_scanner.findings import (  # pyright: ignore[reportMissingImports]
    RiskLevel,
    Severity,
)


# ── Constants ────────────────────────────────────────────────────────

# Allow tests to override the policy path via env var to avoid
# wiping the production policy file during test runs.
_POLICY_PATH_ENV = "HERMES_PLUGIN_POLICY_PATH"
PLUGIN_POLICY_PATH = os.environ.get(
    _POLICY_PATH_ENV,
    os.path.expanduser("~/.hermes/ops-kit/plugin_policy.json"),
)
PLUGIN_AUDIT_PATH = os.path.join(
    os.path.dirname(PLUGIN_POLICY_PATH),
    "plugin_policy_audit.jsonl",
)


# ── Policy I/O ───────────────────────────────────────────────────────


def _load_policy() -> dict[str, Any]:
    """Load and validate plugin policy, failing closed on malformed content."""
    if os.path.exists(PLUGIN_POLICY_PATH):
        try:
            with open(PLUGIN_POLICY_PATH) as f:
                return _validate_policy(json.load(f))
        except (json.JSONDecodeError, OSError, RuntimeError) as exc:
            raise RuntimeError(f"Cannot load plugin policy: {exc}") from exc
    return _default_policy()


def policy_fingerprint(plugin_name: str) -> str:
    """Return a stable digest of policy state that can affect a plugin scan."""
    policy = _load_policy()
    relevant = {
        "plugin": plugin_name,
        "rule_overrides": policy.get("rule_overrides", {}).get(plugin_name, {}),
    }
    encoded = json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _default_policy() -> dict[str, Any]:
    """Default policy structure."""
    return {
        "version": 2,
        "approved_plugins": [],
        "approved_findings": [],
        "approved_categories": [],
        "disabled_plugins": [],
        "blocked_plugins": [],
        "rule_overrides": {},  # {plugin_name: {rule: action}}
    }


def _validate_policy(policy: Any) -> dict[str, Any]:
    """Validate policy shape and add missing backwards-compatible defaults."""
    if not isinstance(policy, dict):
        raise RuntimeError("Plugin policy root must be an object")

    validated = _default_policy()
    validated.update(policy)
    for key in (
        "approved_plugins",
        "approved_findings",
        "approved_categories",
        "disabled_plugins",
        "blocked_plugins",
    ):
        value = validated.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise RuntimeError(f"Plugin policy '{key}' must be a list of strings")

    overrides = validated.get("rule_overrides")
    if not isinstance(overrides, dict):
        raise RuntimeError("Plugin policy 'rule_overrides' must be an object")
    for plugin_name, plugin_overrides in overrides.items():
        if not isinstance(plugin_name, str) or not isinstance(plugin_overrides, dict):
            raise RuntimeError("Plugin policy rule overrides must map plugin names to objects")
        if not all(
            isinstance(rule, str)
            and isinstance(action, str)
            and action in _VALID_OVERRIDE_ACTIONS
            for rule, action in plugin_overrides.items()
        ):
            raise RuntimeError(
                f"Plugin policy contains invalid rule overrides for '{plugin_name}'"
            )
    return validated


# ── Rule Override Logic ───────────────────────────────────────────────

# Valid override actions and downgrade targets
_VALID_OVERRIDE_ACTIONS: frozenset[str] = frozenset(
    {
        "allow",  # Skip this finding entirely (whitelist the rule for this plugin)
        "skip",  # Same as allow — skip this rule for this plugin
        "downgrade:warning",
        "downgrade:info",
    }
)
_DOWNGRADE_SEVERITY: dict[str, Severity] = {
    "downgrade:warning": Severity.WARNING,
    "downgrade:info": Severity.INFO,
}
_DOWNGRADE_RISK: dict[str, RiskLevel] = {
    Severity.WARNING: RiskLevel.MEDIUM,
    Severity.INFO: RiskLevel.LOW,
}


def get_rule_overrides(plugin_name: str) -> dict[str, str]:
    """Get all rule overrides for a plugin.

    Returns:
        {rule_name: action}  e.g. {"prompt-injection-system": "allow"}
    """
    policy = _load_policy()
    return policy.get("rule_overrides", {}).get(plugin_name, {})


def set_rule_override(
    plugin_name: str, rule: str, action: str, notes: str = ""
) -> dict[str, Any]:
    """Set a rule override for a plugin.

    Args:
        plugin_name: Plugin to override.
        rule: Rule name (e.g., "prompt-injection-system", "network-access").
        action: One of "allow", "skip", "downgrade:warning", "downgrade:info".
        notes: Optional audit notes.

    Returns:
        Updated policy dict.
    """
    if action not in _VALID_OVERRIDE_ACTIONS:
        raise ValueError(
            f"Invalid override action: {action}. "
            f"Valid: {', '.join(sorted(_VALID_OVERRIDE_ACTIONS))}"
        )

    policy = _load_policy()
    policy.setdefault("rule_overrides", {})
    policy["rule_overrides"].setdefault(plugin_name, {})
    policy["rule_overrides"][plugin_name][rule] = action

    _save_policy(policy)
    _audit_log(
        {
            "action": "rule_override_set",
            "plugin": plugin_name,
            "rule": rule,
            "override": action,
            "notes": notes,
        }
    )
    return policy


def remove_rule_override(plugin_name: str, rule: str | None = None) -> dict[str, Any]:
    """Remove rule override(s) for a plugin.

    Args:
        plugin_name: Plugin whose overrides to remove.
        rule: Specific rule to remove, or None to remove all overrides
              for this plugin.

    Returns:
        Updated policy dict.
    """
    policy = _load_policy()
    overrides = policy.get("rule_overrides", {})
    if plugin_name not in overrides:
        return policy

    changed = False
    if rule is None:
        del overrides[plugin_name]
        changed = True
    elif rule in overrides[plugin_name]:
        del overrides[plugin_name][rule]
        changed = True
        if not overrides[plugin_name]:
            del overrides[plugin_name]

    if changed:
        _save_policy(policy)
        _audit_log(
            {
                "action": "rule_override_removed",
                "plugin": plugin_name,
                "rule": rule or "__all__",
            }
        )
    return policy


def apply_rule_overrides(
    findings: list[Any],
    plugin_name: str,
) -> tuple[list[Any], int]:
    """Apply rule overrides to a list of findings.

    Override actions:
        "allow" / "skip":  Remove the finding entirely
        "downgrade:warning": Reduce severity to WARNING, risk to MEDIUM
        "downgrade:info":    Reduce severity to INFO, risk to LOW

    Returns:
        (filtered_findings, skipped_count)
    """
    overrides = get_rule_overrides(plugin_name)
    if not overrides:
        return findings, 0

    skipped = 0
    result: list[Any] = []
    for f in findings:
        rule = getattr(f, "rule", "")
        if rule in overrides:
            action = overrides[rule]
            if action in ("allow", "skip"):
                skipped += 1
                continue
            elif action.startswith("downgrade:"):
                new_sev = _DOWNGRADE_SEVERITY.get(action)
                if new_sev is not None:
                    f.severity = new_sev
                    f.risk_level = _DOWNGRADE_RISK.get(new_sev, RiskLevel.LOW)
                    f.message += f" [override: {action}]"
        result.append(f)

    return result, skipped


def _save_policy(policy: dict[str, Any]) -> None:
    """Atomic write of plugin policy (tmp + os.replace)."""
    policy_dir = os.path.dirname(PLUGIN_POLICY_PATH)
    os.makedirs(policy_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".plugin-policy-", dir=policy_dir)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(policy, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PLUGIN_POLICY_PATH)
        os.chmod(PLUGIN_POLICY_PATH, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _audit_log(entry: dict[str, Any]) -> None:
    """Append an audit entry to the JSONL audit trail."""
    os.makedirs(os.path.dirname(PLUGIN_AUDIT_PATH), exist_ok=True)
    entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(PLUGIN_AUDIT_PATH, flags, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.chmod(PLUGIN_AUDIT_PATH, 0o600)
    except OSError:
        pass


# ── Approval Logic ───────────────────────────────────────────────────


def is_approved(
    plugin_name: str,
    finding_id: str = "",
    category: str = "",
) -> tuple[bool, str]:
    """Check if a plugin or finding is approved.

    Matching priority (first match wins):
      1. blocked_plugins → False
      2. disabled_plugins → False
      3. approved_plugins → True (plugin-level)
      4. approved_findings → True (finding-level, with wildcard)
      5. approved_categories → True (category-level)
      6. Default → False

    Returns:
        (is_approved, match_reason)
    """
    policy = _load_policy()

    # 1. Explicitly blocked
    if plugin_name in policy.get("blocked_plugins", []):
        return False, "plugin_blocked"

    # 2. Explicitly disabled
    if plugin_name in policy.get("disabled_plugins", []):
        return False, "plugin_disabled"

    # 3. Plugin-level approval
    if plugin_name in policy.get("approved_plugins", []):
        return True, "plugin_approved"

    # 4. Finding-level approval (with wildcard)
    for entry in policy.get("approved_findings", []):
        # Wildcard: "hermes-*" matches "hermes-plugins:..."
        if entry.endswith("_*"):
            prefix = entry[:-2]
            if finding_id.startswith(prefix) or plugin_name.startswith(prefix):
                return True, "wildcard_finding"
        # Exact match
        if entry == finding_id:
            return True, "finding_approved"

    # 5. Category-level approval
    plugin_category = f"{plugin_name}:{category}"
    if plugin_category in policy.get("approved_categories", []):
        return True, "category_approved"

    return False, "not_approved"


def needs_approval(risk_level: str) -> bool:
    """Check if a risk level requires approval.

    Medium+ requires approval by default.
    """
    return risk_level in ("medium", "high", "critical")


def should_block(risk_level: str) -> bool:
    """Check if a risk level should be blocked.

    Critical is always blocked. High is blocked unless approved.
    """
    return risk_level == "critical"


def should_disable(risk_level: str, is_approved_flag: bool) -> bool:
    """Check if a plugin should be disabled.

    Medium+ unapproved plugins are disabled by default.
    """
    if is_approved_flag:
        return False
    return risk_level in ("medium", "high", "critical")


# ── Policy Mutations ─────────────────────────────────────────────────


def approve_plugin(plugin_name: str, notes: str = "") -> dict[str, Any]:
    """Approve an entire plugin (all findings)."""
    policy = _load_policy()
    changed = False

    if plugin_name not in policy["approved_plugins"]:
        policy["approved_plugins"].append(plugin_name)
        changed = True

    # Remove from disabled/blocked if present
    for key in ("disabled_plugins", "blocked_plugins"):
        if plugin_name in policy[key]:
            policy[key].remove(plugin_name)
            changed = True

    if changed:
        _save_policy(policy)
        _audit_log(
            {
                "action": "plugin_approved",
                "plugin": plugin_name,
                "notes": notes,
            }
        )

    return policy


def approve_finding(finding_id: str, notes: str = "") -> dict[str, Any]:
    """Approve a single finding by its ID."""
    policy = _load_policy()
    if finding_id not in policy["approved_findings"]:
        policy["approved_findings"].append(finding_id)
        _save_policy(policy)
        _audit_log(
            {
                "action": "finding_approved",
                "finding": finding_id,
                "notes": notes,
            }
        )
    return policy


def approve_category(
    plugin_name: str, category: str, notes: str = ""
) -> dict[str, Any]:
    """Approve all findings of a category for a plugin."""
    policy = _load_policy()
    plugin_category = f"{plugin_name}:{category}"
    if plugin_category not in policy["approved_categories"]:
        policy["approved_categories"].append(plugin_category)
        _save_policy(policy)
        _audit_log(
            {
                "action": "category_approved",
                "plugin": plugin_name,
                "category": category,
                "notes": notes,
            }
        )
    return policy


def approve_all(notes: str = "") -> dict[str, Any]:
    """Approve all currently installed plugins."""
    policy = _load_policy()
    # Discover installed plugins
    plugins = _discover_plugins()
    changed = False
    for pname in plugins:
        if pname not in policy["approved_plugins"]:
            policy["approved_plugins"].append(pname)
            changed = True
        for key in ("disabled_plugins", "blocked_plugins"):
            if pname in policy[key]:
                policy[key].remove(pname)
                changed = True
    if changed:
        _save_policy(policy)
        _audit_log(
            {
                "action": "approve_all",
                "plugins": plugins,
                "notes": notes,
            }
        )
    return policy


def revoke_plugin(plugin_name: str) -> dict[str, Any]:
    """Revoke approval for a plugin."""
    policy = _load_policy()
    changed = False
    for key in ("approved_plugins", "disabled_plugins"):
        if plugin_name in policy[key]:
            policy[key].remove(plugin_name)
            changed = True

    # Capture original lengths before filtering to avoid a second
    # _load_policy() call — we compare against what we loaded.
    original_findings_count = len(policy["approved_findings"])
    original_categories_count = len(policy["approved_categories"])

    # Remove related finding and category approvals
    policy["approved_findings"] = [
        f for f in policy["approved_findings"] if not f.startswith(plugin_name + ":")
    ]
    policy["approved_categories"] = [
        c for c in policy["approved_categories"] if not c.startswith(plugin_name + ":")
    ]

    findings_stripped = len(policy["approved_findings"]) != original_findings_count
    categories_stripped = (
        len(policy["approved_categories"]) != original_categories_count
    )

    if changed or findings_stripped or categories_stripped:
        _save_policy(policy)
        _audit_log(
            {
                "action": "plugin_revoked",
                "plugin": plugin_name,
            }
        )
    return policy


def revoke_finding(finding_id: str) -> dict[str, Any]:
    """Revoke approval for a single finding."""
    policy = _load_policy()
    if finding_id in policy["approved_findings"]:
        policy["approved_findings"].remove(finding_id)
        _save_policy(policy)
        _audit_log(
            {
                "action": "finding_revoked",
                "finding": finding_id,
            }
        )
    return policy


def revoke_all() -> dict[str, Any]:
    """Revoke all approvals."""
    policy = _default_policy()
    _save_policy(policy)
    _audit_log({"action": "revoke_all"})
    return policy


def disable_plugin(plugin_name: str, reason: str = "") -> dict[str, Any]:
    """Explicitly disable a plugin."""
    policy = _load_policy()
    if plugin_name not in policy["disabled_plugins"]:
        policy["disabled_plugins"].append(plugin_name)
        # Remove from approved
        if plugin_name in policy["approved_plugins"]:
            policy["approved_plugins"].remove(plugin_name)
        _save_policy(policy)
        _audit_log(
            {
                "action": "plugin_disabled",
                "plugin": plugin_name,
                "reason": reason,
            }
        )
    return policy


def enable_plugin(plugin_name: str) -> dict[str, Any]:
    """Re-enable a previously disabled plugin."""
    policy = _load_policy()
    changed = False
    for key in ("disabled_plugins", "blocked_plugins"):
        if plugin_name in policy[key]:
            policy[key].remove(plugin_name)
            changed = True
    if changed:
        _save_policy(policy)
        _audit_log(
            {
                "action": "plugin_enabled",
                "plugin": plugin_name,
            }
        )
    return policy


def block_plugin(plugin_name: str, reason: str = "") -> dict[str, Any]:
    """Permanently block a plugin."""
    policy = _load_policy()
    if plugin_name not in policy["blocked_plugins"]:
        policy["blocked_plugins"].append(plugin_name)
        # Remove from other lists
        for key in ("approved_plugins", "disabled_plugins"):
            if plugin_name in policy[key]:
                policy[key].remove(plugin_name)
        _save_policy(policy)
        _audit_log(
            {
                "action": "plugin_blocked",
                "plugin": plugin_name,
                "reason": reason,
            }
        )
    return policy


# ── Helpers ──────────────────────────────────────────────────────────


def _discover_plugins() -> list[str]:
    """Discover installed plugin names."""
    plugins: list[str] = []
    locations = [
        os.path.expanduser("~/.hermes/plugins"),
        os.path.expanduser("~/.hermes/skills"),
    ]
    for loc in locations:
        if os.path.isdir(loc):
            try:
                for entry in sorted(os.listdir(loc)):
                    if not entry.startswith(".") and not entry.startswith("_"):
                        if os.path.isdir(os.path.join(loc, entry)):
                            plugins.append(entry)
            except OSError:
                pass
    return plugins


def get_policy() -> dict[str, Any]:
    """Get the current policy (read-only)."""
    return _load_policy()


def get_plugin_status(plugin_name: str, risk_level: str = "none") -> dict[str, Any]:
    """Get the combined approval status for a plugin.

    Returns a dict with keys: status, reason, action.
    status is one of: enabled, disabled, blocked, unknown.
    """
    policy = _load_policy()

    if plugin_name in policy.get("blocked_plugins", []):
        return {"status": "blocked", "reason": "plugin_blocked", "action": "block"}

    approved, reason = is_approved(plugin_name)

    if approved:
        return {"status": "enabled", "reason": reason, "action": "allow"}

    if should_block(risk_level):
        return {"status": "blocked", "reason": f"risk_{risk_level}", "action": "block"}

    if should_disable(risk_level, approved):
        return {"status": "disabled", "reason": reason, "action": "disable"}

    return {"status": "enabled", "reason": "risk_low_or_none", "action": "allow"}
