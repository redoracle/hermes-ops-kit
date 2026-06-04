"""
Hermes Ops Kit — Secret Scanner

Scans content for raw secret patterns before writing to Obsidian,
audit logs, chat transcripts, or any external sink.

Usage:
    from security.secret_scanner import scan_for_secrets

    clean, violations = scan_for_secrets(content)
    if not clean:
        raise UnsafeSecretWriteError(violations)
"""

from __future__ import annotations

import re

from security.redaction import SECRET_PATTERNS


def scan_for_secrets(content: str) -> tuple[bool, list[str]]:
    """Return (clean, violations).

    clean       = True if no secret patterns were detected.
    violations  = human-readable list of what was found (without the raw secret).
    """
    if not content:
        return True, []

    violations: list[str] = []
    for pattern, replacement in SECRET_PATTERNS:
        matches = re.findall(pattern, content)
        if matches:
            violations.append(
                f"pattern matched ({replacement}): {len(matches)} occurrence(s)"
            )

    return len(violations) == 0, violations


# ─── Content-level blocks that should never be written to Obsidian ───

FORBIDDEN_BLOCK_PATTERNS: list[tuple[str, str]] = [
    (r'^\s*[A-Z_]+_KEY\s*=\s*["\']?\S+', "env KEY assignment"),
    (r'^\s*[A-Z_]+_TOKEN\s*=\s*["\']?\S+', "env TOKEN assignment"),
    (r'^\s*[A-Z_]+_PASSWORD\s*=\s*["\']?\S+', "env PASSWORD assignment"),
    (r'^\s*[A-Z_]+_SECRET\s*=\s*["\']?\S+', "env SECRET assignment"),
    (r"Authorization:\s*\S", "Authorization header"),
    (r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----", "PEM private key"),
]


def scan_for_forbidden_blocks(content: str) -> tuple[bool, list[str]]:
    """Check for entire forbidden content blocks (not just token patterns)."""
    if not content:
        return True, []

    violations: list[str] = []
    for pattern, label in FORBIDDEN_BLOCK_PATTERNS:
        if re.search(pattern, content, re.MULTILINE):
            violations.append(f"forbidden block: {label}")

    return len(violations) == 0, violations


def assert_clean(content: str, sink: str = "unknown") -> None:
    """Raise UnsafeSecretWriteError if *content* contains secrets or forbidden blocks.

    This is the single gate called before any Obsidian or audit write.
    """
    try:
        from secret_backend import UnsafeSecretWriteError  # pyright: ignore[reportMissingImports]
    except ImportError:
        from security.secret_backend import UnsafeSecretWriteError  # pyright: ignore[reportMissingImports]

    clean, violations = scan_for_secrets(content)
    if not clean:
        raise UnsafeSecretWriteError(
            f"raw secrets detected in {sink} payload: {violations}"
        )
    clean, violations = scan_for_forbidden_blocks(content)
    if not clean:
        raise UnsafeSecretWriteError(
            f"forbidden content blocks in {sink} payload: {violations}"
        )
