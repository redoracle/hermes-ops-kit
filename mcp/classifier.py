"""Hermes Ops Kit — MCP Tool Classifier.

Capability detection from tool name, description, and schema.
Metadata prompt-injection scanner.
"""

from __future__ import annotations

import re
from typing import Any

# ─── Capability keywords ──────────────────────────────────────────

CAPABILITY_KEYWORDS: dict[str, list[str]] = {
    "write_files": [
        "write",
        "create",
        "update",
        "patch",
        "delete",
        "move",
        "rename",
        "edit",
        "save",
        "upload",
    ],
    "read_files": [
        "read",
        "get",
        "view",
        "list",
        "search",
        "query",
        "find",
        "open",
        "cat",
    ],
    "execute_commands": [
        "shell",
        "command",
        "exec",
        "terminal",
        "process",
        "run",
        "bash",
        "script",
        "subprocess",
    ],
    "credential_access": [
        "secret",
        "token",
        "password",
        "credential",
        "env",
        "keychain",
        "vault",
        "api_key",
        "auth",
    ],
    "persistent_memory_write": [
        "obsidian",
        "note",
        "memory",
        "database",
        "sqlite",
        "vectorstore",
        "index",
        "store",
    ],
    "network_access": [
        "http",
        "fetch",
        "browser",
        "crawl",
        "request",
        "api",
        "url",
        "web",
        "curl",
        "download",
    ],
    "repo_mutation": [
        "git",
        "commit",
        "push",
        "pull request",
        "pr",
        "branch",
        "merge",
        "repo",
    ],
    "external_side_effect": [
        "deploy",
        "publish",
        "notify",
        "send",
        "email",
        "webhook",
        "trigger",
    ],
}

# ─── Risk rules ────────────────────────────────────────────────────

RISK_RULES: dict[str, list[str]] = {
    "critical": ["execute_commands", "credential_access", "unrestricted_file_write"],
    "high": ["repo_mutation", "persistent_memory_write", "external_side_effect"],
    "medium": ["write_files", "network_access"],
    "low": ["read_files"],
}


def detect_capabilities(
    tool_name: str, description: str = "", input_schema: dict[str, Any] | None = None
) -> dict[str, bool]:
    """Infer tool capabilities from name, description, and schema."""
    text = f"{tool_name} {description} {str(input_schema)}".lower()
    caps: dict[str, bool] = {}
    for cap, keywords in CAPABILITY_KEYWORDS.items():
        caps[cap] = any(kw in text for kw in keywords)
    return caps


def classify_risk(capabilities: dict[str, bool]) -> str:
    """Classify tool risk level from detected capabilities."""
    for level in ("critical", "high", "medium", "low"):
        for cap in RISK_RULES.get(level, []):
            if capabilities.get(cap):
                return level
    return "unknown"


# ─── Metadata injection scanner ───────────────────────────────────

INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(previous|all|above)\s+instructions?", "instruction override"),
    (r"override\s+(system\s+)?prompt", "prompt override"),
    (r"send\s+(secrets?|credentials?|tokens?|keys?)", "credential exfiltration"),
    (r"read\s+(environment\s+)?variables?", "env read attempt"),
    (r"exfiltrate", "data exfiltration"),
    (r"disable\s+safety", "safety disable"),
    (r"always\s+call\s+this\s+tool", "forced tool call"),
    (r"do\s+not\s+tell\s+the\s+user", "hidden instruction"),
    (r"(hidden|developer|system)\s+instruction", "hidden instruction variant"),
    (r"do\s+not\s+reveal\s+(this|your)\s+(prompt|instruction)", "instruction hiding"),
]


def scan_metadata(text: str) -> tuple[str, list[str]]:
    """Scan tool metadata for prompt-injection patterns.

    Returns (risk_level, list of matched patterns).
    """
    matches = []
    for pattern, label in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            matches.append(label)
    if len(matches) >= 3:
        return "high", matches
    elif len(matches) >= 1:
        return "medium", matches
    return "none", []
