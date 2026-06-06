"""
Hermes Ops Kit — Shared Secret Redaction

Single source of truth for secret pattern detection and redaction.
Used by all provider adapters, audit logging, Obsidian sink, and the key rotation CLI.

Rules:
- Never log raw secrets to stdout, stderr, audit JSONL, Obsidian, or chat transcripts.
- Redaction format: <REDACTED:SECRET_NAME> or a generic replacement string.
- Apply redact() to any string before it leaves the process boundary.
"""

from __future__ import annotations

import re

# ─── Secret Patterns ────────────────────────────────────────────────
#
# Each tuple is (regex_pattern, replacement_string).
# Patterns are tested in order; first match wins.
# Keep provider-specific patterns first (more specific), generic patterns last.

SECRET_PATTERNS: list[tuple[str, str]] = [
    # ── Provider API Keys ──
    (r"nvapi-[A-Za-z0-9_-]{40,}", "<NVIDIA_KEY_REDACTED>"),
    (r"sk-ant-[A-Za-z0-9-_]{20,}", "<ANTHROPIC_KEY_REDACTED>"),
    (r"sk-[A-Za-z0-9-_]{20,}", "<OPENAI_KEY_REDACTED>"),
    (r"AIza[0-9A-Za-z_-]{35}", "<GEMINI_KEY_REDACTED>"),
    (r"ghp_[A-Za-z0-9]{36}", "<GITHUB_TOKEN_REDACTED>"),
    (r"gho_[A-Za-z0-9]{36}", "<GITHUB_TOKEN_REDACTED>"),
    (r"ghu_[A-Za-z0-9]{36}", "<GITHUB_TOKEN_REDACTED>"),
    (r"github_pat_[A-Za-z0-9_]{40,}", "<GITHUB_TOKEN_REDACTED>"),
    # ── Vaultwarden / Bitwarden Secrets ──
    (r"BW_SESSION=[A-Za-z0-9+/=]{20,}", "BW_SESSION=<REDACTED>"),
    (r"BW_CLIENTSECRET=[A-Za-z0-9]{20,}", "BW_CLIENTSECRET=<REDACTED>"),
    (r"BW_PASSWORD=[^\s]{1,}", "BW_PASSWORD=<REDACTED>"),
    (r"VAULTWARDEN_PASSWORD=[^\s]{1,}", "VAULTWARDEN_PASSWORD=<REDACTED>"),
    (r"BW_CLIENTID=[^\s]{1,}", "BW_CLIENTID=<REDACTED>"),
    # ── Assistant / Assistant Token Patterns ──
    (r"ASSISTANT_API_KEY=[A-Za-z0-9]{20,}", "ASSISTANT_API_KEY=<REDACTED>"),
    (
        r"hermes-assistant-token:\s*[A-Za-z0-9]{20,}",
        "hermes-assistant-token: <REDACTED>",
    ),
    # ── Service Account / Private Key Patterns ──
    (
        r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----[^-]*-----END\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
        "<PRIVATE_KEY_REDACTED>",
    ),
    (r'"private_key"\s*:\s*"[^"]*"', '"private_key":"<PRIVATE_KEY_REDACTED>"'),
    # ── Generic Bearer / API Key Headers ──
    (r"Bearer\s+[A-Za-z0-9_\-\.]+", "Bearer <TOKEN_REDACTED>"),
    (r"x-api-key:\s*[A-Za-z0-9_\-\.]+", "x-api-key: <KEY_REDACTED>"),
    (r"Authorization:\s*[A-Za-z0-9_\-\.]+", "Authorization: <REDACTED>"),
    # ── .env / Shell Assignment Patterns ──
    (
        r"(?:^|\n)([A-Z_]+_KEY|[A-Z_]+_TOKEN|[A-Z_]+_PASSWORD|[A-Z_]+_SECRET)=[^\n]+",
        r"\1=<REDACTED>",
    ),
]


def redact(text: str) -> str:
    """Redact all known secret patterns from *text*.

    Returns the redacted string.  If *text* is falsy (None, empty),
    it is returned unchanged.
    """
    if not text:
        return text
    for pattern, replacement in SECRET_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text
