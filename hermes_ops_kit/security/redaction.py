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

# Word boundary that also holds after a JSON-escaped newline/tab (``\\n`` glues the next
# word to the letter ``n``, so a plain ``\\b`` would miss text on the following line).
_WB = r"(?:\b|(?<=\\[nrt]))"

# Skip text that is already a redaction placeholder, keeping redact() idempotent
# and stopping scan_for_secrets() from flagging redacted output.
_NOT_PLACEHOLDER = r"(?!<[A-Z_]*REDACTED>)"

# Right-hand side of an assignment: JSON-embedded (\"..\" / \'..\'), "double" or
# 'single' quoted with backslash escapes, a bare token (any non-space character, so
# ``/ + = _ -`` are covered), or an unterminated quote followed by a bare run.
_ASSIGNED_VALUE = (
    r"(?:\\\"(?:(?!\\\").)*\\\""
    r"|\\'(?:(?!\\').)*\\'"
    r"|\"(?:\\.|[^\"\\])*\""
    r"|'(?:\\.|[^'\\])*'"
    r"|[\"'\\]*[^\s\"'\\]+)"
)

# A header credential: stops at whitespace, quotes, separators or a backslash so it
# also works inside quoted shell arguments and JSON-embedded text.
_HEADER_VALUE = r"[^\s\"',;\\]+"

SECRET_PATTERNS: list[tuple[str, str]] = [
    # ── Provider API Keys ──
    (r"nvapi-[A-Za-z0-9_-]{40,}", "<NVIDIA_KEY_REDACTED>"),
    (r"sk-ant-[A-Za-z0-9-_]{20,}", "<ANTHROPIC_KEY_REDACTED>"),
    (r"sk-[A-Za-z0-9-_]{20,}", "<OPENAI_KEY_REDACTED>"),
    (r"AIza[0-9A-Za-z_-]{35}", "<GEMINI_KEY_REDACTED>"),
    # Open-ended lengths: GitHub App installation tokens (ghs_) move to a longer
    # stateless format (~520 chars), so fixed-length patterns would leak the tail.
    (r"ghp_[A-Za-z0-9]{36,}", "<GITHUB_TOKEN_REDACTED>"),
    (r"gho_[A-Za-z0-9]{36,}", "<GITHUB_TOKEN_REDACTED>"),
    (r"ghu_[A-Za-z0-9]{36,}", "<GITHUB_TOKEN_REDACTED>"),
    (r"ghs_[A-Za-z0-9_-]{36,}", "<GITHUB_TOKEN_REDACTED>"),
    (r"ghr_[A-Za-z0-9_-]{36,}", "<GITHUB_TOKEN_REDACTED>"),
    (r"github_pat_[A-Za-z0-9_]{40,}", "<GITHUB_TOKEN_REDACTED>"),
    # ── LLM / Image Provider Keys (mirror Hermes core agent/redact.py) ──
    # Fireworks AI — prefixes added to core redactor in v0.19.0 (Quicksilver).
    (r"fw-[A-Za-z0-9]{30,}", "<FIREWORKS_KEY_REDACTED>"),  # Fireworks API key
    (r"fw_[A-Za-z0-9]{30,}", "<FIREWORKS_KEY_REDACTED>"),  # Fireworks API key
    (r"fpk_[A-Za-z0-9]{30,}", "<FIREWORKS_KEY_REDACTED>"),  # Fireworks project key
    (r"xai-[A-Za-z0-9]{30,}", "<XAI_KEY_REDACTED>"),  # xAI (Grok) API key
    (r"fal_[A-Za-z0-9_-]{10,}", "<FAL_KEY_REDACTED>"),  # Fal.ai (image adapter)
    # ── Vaultwarden / Bitwarden Secrets + assistant key ──
    # Optional ``export``; value may be bare, "double"/'single' quoted (with escapes),
    # or JSON-embedded (\"...\"). Already-redacted placeholders are skipped so
    # redact() is idempotent and scan_for_secrets() does not flag redacted text.
    (
        r"(?i)" + _WB + r"((?:export\s+)?(?:BW_SESSION|BW_CLIENTSECRET|BW_PASSWORD|"
        r"VAULTWARDEN_PASSWORD|BW_CLIENTID|ASSISTANT_API_KEY)\s*=\s*+)"
        + _NOT_PLACEHOLDER
        + _ASSIGNED_VALUE,
        r"\1<REDACTED>",
    ),
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
        r'("(?i:api_key|apikey|api-key|access_token|refresh_token|id_token|token|secret|client_secret|password|authorization|bw_session|bw_clientsecret|vaultwarden_password|assistant_api_key|[A-Z][A-Z0-9_]*_(?:API_KEY|TOKEN|PASSWORD|SECRET))"\s*:\s*")[^"]*"',
        r'\1<REDACTED>"',
    ),
    # ── Auth headers / cookies / URL credentials ──
    # Authorization first, so a following Bearer pattern sees the placeholder, not
    # the credential. The scheme group is possessive: a placeholder after it must
    # not backtrack into treating the scheme word itself as the credential.
    (
        r"(?i)(" + _WB + r"authorization\s*[:=]\s*+(?:(?:bearer|basic|digest|token|negotiate)\s+)?+)"
        + _NOT_PLACEHOLDER
        + _HEADER_VALUE,
        r"\1<TOKEN_REDACTED>",
    ),
    (
        r"(?i)" + _WB + r"Bearer\s+" + _NOT_PLACEHOLDER + r"[A-Za-z0-9_\-\.~+/]+=*",
        "Bearer <TOKEN_REDACTED>",
    ),
    (
        r"(?i)(" + _WB + r"x-api-key\s*[:=]\s*+)" + _NOT_PLACEHOLDER + _HEADER_VALUE,
        r"\1<KEY_REDACTED>",
    ),
    (
        r"(?i)(" + _WB + r"(?:set-)?cookie\s*:\s*+)" + _NOT_PLACEHOLDER + r"[^\r\n\"\\]+",
        r"\1<COOKIE_REDACTED>",
    ),
    (
        r"(\b[A-Za-z][A-Za-z0-9+.\-]*://)(?!<)[^\s/@:\"'\\]+:[^\s/@\"'\\]+@",
        r"\1<USERINFO_REDACTED>@",
    ),
    # ── .env / Shell Assignment Patterns ──
    (
        r"(?m)^(\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:_KEY|_TOKEN|_PASSWORD|_SECRET)\s*=\s*+)"
        + _NOT_PLACEHOLDER
        + r"[^\r\n]+",
        r"\1<REDACTED>",
    ),
    # Same assignments inside JSON-serialized text, where a newline is the two
    # characters ``\\n`` and the value ends at the closing escaped quote.
    (
        r"(?<=\\n)(\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:_KEY|_TOKEN|_PASSWORD|_SECRET)\s*=\s*+)"
        + _NOT_PLACEHOLDER
        + _ASSIGNED_VALUE,
        r"\1<REDACTED>",
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
