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
    # ── LLM / Image Provider Keys (mirror Hermes core agent/redact.py) ──
    # Fireworks AI — prefixes added to core redactor in v0.19.0 (Quicksilver).
    (r"fw-[A-Za-z0-9]{30,}", "<FIREWORKS_KEY_REDACTED>"),  # Fireworks API key
    (r"fw_[A-Za-z0-9]{30,}", "<FIREWORKS_KEY_REDACTED>"),  # Fireworks API key
    (r"fpk_[A-Za-z0-9]{30,}", "<FIREWORKS_KEY_REDACTED>"),  # Fireworks project key
    (r"xai-[A-Za-z0-9]{30,}", "<XAI_KEY_REDACTED>"),  # xAI (Grok) API key
    (r"fal_[A-Za-z0-9_-]{10,}", "<FAL_KEY_REDACTED>"),  # Fal.ai (image adapter)
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
    # ── JSON / form body fields carrying secrets ──
    # Catches opaque tokens with no vendor prefix (e.g. DeepInfra keys) in JSON
    # values like {"api_key":"..."}. Mirrors core agent/redact.py _SENSITIVE_BODY_KEYS.
    (
        r'("(?:api_key|apikey|api-key|access_token|refresh_token|id_token|token|secret|client_secret|password|authorization)"\s*:\s*")[^"]*"',
        r'\1<REDACTED>"',
    ),
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


def sanitize_url_for_display(url: str) -> str:
    """Strip userinfo, query, and fragment from a URL for terminal display.

    Prevents credential leaks when a ``base_url`` from config carries embedded
    credentials (``user:pass@host`` in the netloc, ``?api_key=sk-...`` in the
    query string).  Fragments are stripped as a defence-in-depth measure
    (rarely sensitive but never useful in diagnostic output).

    Returns ``<url-redacted>`` on parse failure — never the raw URL.
    Callers that need the raw URL for actual HTTP requests must call this
    ONLY for the display/log copy, not for the connection copy.
    """
    if not url:
        return url
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.split("@")[-1]
        sanitized = parsed._replace(netloc=netloc, query="", fragment="")
        return urlunparse(sanitized)
    except Exception:
        return "<url-redacted>"


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
