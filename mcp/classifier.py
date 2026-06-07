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
        "activate",
        "deactivate",
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
        "export",
        "check",
        "status",
        "recent",
        "log",
        "return",
        "retrieve",
        "fetch",
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
    """Infer tool capabilities from name, description, and schema.

    Uses custom boundary matching that treats ``_``, whitespace, and
    string edges as word separators.  This avoids false positives like
    ``script`` matching inside ``transcript`` or ``exec`` inside
    ``execution``, while still correctly matching snake_case identifiers
    (``get_transcript`` → ``get``).

    Tool names that begin with a read-only verb prefix (``get_``,
    ``list_``, ``find_``, ``search_``, ``read_``, ``fetch_``, ``view_``)
    suppress mutation-related capabilities — these prefixes strongly
    signal read-only operations, even when the description mentions
    repo terms contextually (e.g. "get_issue" whose description notes
    it returns the "git branch name").
    """
    _READ_PREFIXES = ("get_", "list_", "find_", "search_", "read_", "fetch_", "view_")
    _MUTATION_CAPS = {"repo_mutation", "write_files", "external_side_effect"}

    text = f"{tool_name} {description} {str(input_schema)}".lower()
    caps: dict[str, bool] = {}
    for cap, keywords in CAPABILITY_KEYWORDS.items():
        for kw in keywords:
            # Boundary: start-of-string, underscore, or whitespace before;
            # end-of-string, underscore, or whitespace after.
            pat = r"(?:^|[\s_])" + re.escape(kw) + r"(?:$|[\s_])"
            if re.search(pat, text):
                caps[cap] = True
                break
        else:
            caps[cap] = False

    # Suppress mutation capabilities for read-prefixed tools.
    # Descriptions often mention contextual repo terms (branch, PR, git)
    # that describe the input domain, not the tool's own side effects.
    name_lower = tool_name.lower()
    if any(name_lower.startswith(p) for p in _READ_PREFIXES):
        for cap in _MUTATION_CAPS:
            if caps.get(cap):
                caps[cap] = False

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
