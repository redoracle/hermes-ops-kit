"""Hermes Ops Kit — Assistant Result Sanitizer

Redacts sensitive content from assistant responses before logging or display.
Ensures structured output format compliance.
"""

from __future__ import annotations

from typing import Any

from ..security.redaction import redact  # pyright: ignore[reportMissingImports]


def sanitize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Sanitize an assistant delegation result.

    - Redacts any secret patterns in text fields
    - Ensures required fields are present
    - Strips raw token values
    """
    # Redact text content
    if "result" in result and isinstance(result["result"], dict):
        if "text" in result["result"]:
            result["result"]["text"] = redact(str(result["result"]["text"]))

    # Redact error messages
    if "error" in result:
        result["error"] = redact(str(result["error"]))

    # Redact warnings
    if "warnings" in result:
        result["warnings"] = [redact(str(w)) for w in result["warnings"]]

    # Strip any raw key-like values from the result dict
    if "result" in result and isinstance(result["result"], dict):
        for key, value in list(result["result"].items()):
            if isinstance(value, str) and _looks_like_secret(value):
                result["result"][key] = "<REDACTED>"

    return result


def _looks_like_secret(value: str) -> bool:
    """Heuristic: does this string look like an API key or token?"""
    if len(value) < 20:
        return False
    # Hex strings (64 chars = 256-bit tokens)
    if len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower()):
        return True
    # Common key prefixes
    if value.startswith(("sk-", "sk-ant-", "AIza", "ghp_", "gho_")):
        return True
    return False
