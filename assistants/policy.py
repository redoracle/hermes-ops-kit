"""Hermes Ops Kit — Assistant Policy Engine

Pre-flight checks before any task is delegated to a remote assistant.
Rejects tasks containing secrets, forbidden content, or requesting
blocked capabilities.

Spec section 17 — Default Assistant policy: read-only, no secrets, no shell.
"""

from __future__ import annotations

import re

from assistants.base import AssistantConfig, AssistantTask  # pyright: ignore[reportMissingImports]


# Patterns that must NEVER appear in a delegated task
FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    (r"sk-ant-[A-Za-z0-9-_]{20,}", "Anthropic API key"),
    (r"sk-[A-Za-z0-9-_]{20,}", "OpenAI/DeepSeek API key"),
    (r"AIza[0-9A-Za-z_-]{35}", "Gemini API key"),
    (r"ghp_[A-Za-z0-9]{36}", "GitHub token"),
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


def check_task(
    task: AssistantTask,
    config: AssistantConfig,
) -> tuple[bool, list[str]]:
    """Run all policy checks against a task.

    Returns (allowed, violations).
    """
    violations: list[str] = []

    # 1. Check for secret patterns in task text
    task_text = task.task.lower()
    for pattern, label in FORBIDDEN_PATTERNS:
        if re.search(pattern, task.task, re.IGNORECASE):
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
    if len(task.task.encode()) > config.max_prompt_bytes:
        violations.append(
            f"task size {len(task.task.encode())} > max {config.max_prompt_bytes} bytes"
        )

    # 6. Check constraints against security policy
    constraints = task.constraints or {}
    if constraints.get("no_secret_access") is False and not config.allow_secret_prompts:
        violations.append("secret access requested but not allowed by assistant policy")

    if constraints.get("no_file_write") is False and not config.allow_file_mutation:
        violations.append("file write requested but not allowed by assistant policy")

    if (
        constraints.get("no_shell_execution") is False
        and not config.allow_shell_execution
    ):
        violations.append(
            "shell execution requested but not allowed by assistant policy"
        )

    return len(violations) == 0, violations


def assert_allowed(task: AssistantTask, config: AssistantConfig) -> None:
    """Raise PolicyViolation if the task is not allowed."""
    allowed, violations = check_task(task, config)
    if not allowed:
        raise PolicyViolation(
            f"Task {task.task_id} blocked by assistant policy: {'; '.join(violations)}"
        )
