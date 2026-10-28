"""Hermes Ops Kit — Assistant Policy Engine

Pre-flight checks before any task is delegated to a remote assistant.
Rejects tasks containing secrets, forbidden content, or requesting
blocked capabilities.

Spec section 17 — Default Assistant policy: read-only, no secrets, no shell.
"""

from __future__ import annotations

import re
import json

from ..security.secret_scanner import scan_for_secrets

from ..assistants.base import AssistantConfig, AssistantTask  # pyright: ignore[reportMissingImports]


# Patterns that must NEVER appear in a delegated task
FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    (r"sk-ant-[A-Za-z0-9-_]{20,}", "Anthropic API key"),
    (r"sk-[A-Za-z0-9-_]{20,}", "OpenAI/DeepSeek API key"),
    (r"AIza[0-9A-Za-z_-]{35}", "Gemini API key"),
    (r"gh[pou]_[A-Za-z0-9]{36,}", "GitHub token"),
    (r"gh[sr]_[A-Za-z0-9_-]{36,}", "GitHub App token"),
    (r"Authorization:\s*\S", "Authorization header"),
    (r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----", "Private key"),
    (r"BW_SESSION=", "Bitwarden session"),
    (r"VAULTWARDEN_PASSWORD=", "Vaultwarden password"),
    (r"\.env", ".env reference"),  # weak signal — flag for review
]


# Request intents that must be rejected
FORBIDDEN_INTENTS: list[str] = [
    "dump environment",
    "print secrets",
    "rotate credentials",
    "disable security",
    "run destructive command",
    "delete files",
    "push to git",
    "deploy to production",
    "scan network",
    "exfiltrate files",
]


class PolicyViolation(Exception):
    """A task was blocked by the assistant policy engine."""


# Reserved constraint -> (violation message when a caller tries to relax it,
# whether the assistant config permits relaxing it).
_RESERVED_CONSTRAINTS = (
    "no_secret_access",
    "no_env_dump",
    "no_file_write",
    "no_shell_execution",
    "no_network_scan",
)
_RELAX_MESSAGES = {
    "no_secret_access": "secret access requested but not allowed by assistant policy",
    "no_env_dump": "environment dump requested but not allowed by assistant policy",
    "no_file_write": "file write requested but not allowed by assistant policy",
    "no_shell_execution": "shell execution requested but not allowed by assistant policy",
    "no_network_scan": "network scan requested but not allowed by assistant policy",
}


def _normalize_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def effective_constraints(
    task: AssistantTask, config: AssistantConfig
) -> tuple[dict, list[str]]:
    """Derive the one constraint object that is both evaluated and transmitted.

    Reserved flags start from the configured restriction. A caller may only
    tighten (``True``); relaxing (``False``) is honored solely where the config
    permits it. Reserved flags must be exact booleans under their canonical
    name, so ``0``/``None``/``"false"`` and alias spellings can neither relax a
    restriction nor reach the remote worker. Other keys pass through unchanged.
    """
    effective: dict = {
        "no_secret_access": True,
        "no_env_dump": True,
        "no_file_write": not config.allow_file_mutation,
        "no_shell_execution": not config.allow_shell_execution,
        "no_network_scan": True,
    }
    may_relax = {
        "no_secret_access": config.allow_secret_prompts,
        "no_file_write": config.allow_file_mutation,
        "no_shell_execution": config.allow_shell_execution,
    }
    violations: list[str] = []
    raw = task.constraints or {}
    if not isinstance(raw, dict):
        return effective, ["constraints must be an object"]
    for key, value in raw.items():
        if key not in _RESERVED_CONSTRAINTS:
            if _normalize_key(key) in _RESERVED_CONSTRAINTS:
                violations.append(
                    f"reserved constraint must use its canonical name: {key!r}"
                )
            else:
                effective[key] = value
            continue
        if type(value) is not bool:
            violations.append(
                f"constraint '{key}' must be a boolean, got {type(value).__name__}"
            )
        elif value:
            effective[key] = True
        elif effective[key] and not may_relax.get(key, False):
            violations.append(_RELAX_MESSAGES[key])
        else:
            effective[key] = False
    return effective, violations


def serialize_task(task: AssistantTask, config: AssistantConfig) -> str:
    """One canonical envelope for policy evaluation and transmission."""
    constraints, _ = effective_constraints(task, config)
    return json.dumps({
        "task_id": task.task_id, "assistant": config.id,
        "capability": task.capability, "priority": "normal", "task": task.task,
        "context": task.context or {"caller": config.orchestrator_name.lower()},
        "constraints": constraints,
    }, indent=2)


def check_task(
    task: AssistantTask,
    config: AssistantConfig,
    *, envelope: str | None = None,
) -> tuple[bool, list[str]]:
    """Run all policy checks against a task.

    Returns (allowed, violations).
    """
    violations: list[str] = []
    envelope = envelope if envelope is not None else serialize_task(task, config)
    _, detected = scan_for_secrets(envelope)
    violations.extend(detected)

    # 1. Check for secret patterns in task text
    task_text = task.task.lower()
    for pattern, label in FORBIDDEN_PATTERNS:
        if re.search(pattern, envelope, re.IGNORECASE):
            violations.append(f"forbidden content: {label}")

    # 2. Check for forbidden intents
    for intent in FORBIDDEN_INTENTS:
        if intent in task_text:
            violations.append(f"forbidden intent: {intent}")

    # 3. Check capability is allowed
    allowed_cap_ids = {c.get("id", "") for c in config.capabilities}
    if task.capability not in allowed_cap_ids:
        violations.append(
            f"capability '{task.capability}' not in allowlist: {sorted(allowed_cap_ids)}"
        )

    # 4. Check capability is not blocked
    if task.capability in config.blocked_capabilities:
        violations.append(f"capability '{task.capability}' is blocked")

    # 5. Size checks
    if len(envelope.encode()) > config.max_prompt_bytes:
        violations.append(
            f"envelope size {len(envelope.encode())} > max {config.max_prompt_bytes} bytes"
        )

    # 6. Check constraints against security policy
    _, constraint_violations = effective_constraints(task, config)
    violations.extend(constraint_violations)

    return len(violations) == 0, violations


def assert_allowed(task: AssistantTask, config: AssistantConfig, *, envelope: str | None = None) -> None:
    """Raise PolicyViolation if the task is not allowed."""
    allowed, violations = check_task(task, config, envelope=envelope)
    if not allowed:
        raise PolicyViolation(
            f"Task {task.task_id} blocked by assistant policy: {'; '.join(violations)}"
        )
