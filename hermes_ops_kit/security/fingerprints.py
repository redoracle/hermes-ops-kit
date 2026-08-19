"""
Hermes Ops Kit — Secret Fingerprints

Deterministic, non-reversible secret identifiers for audit logs and Obsidian notes.

Usage:
    fp, last4 = secret_fingerprint(api_key)
    # fp   = "sha256:8c21f3a9b722"
    # last4 = "Ab7Q"
"""

from __future__ import annotations

import hashlib


def secret_fingerprint(value: str) -> tuple[str, str]:
    """Return a (fingerprint, last4) tuple for a secret string.

    fingerprint = "sha256:" + first 12 hex chars of SHA-256
    last4       = last 4 characters of the raw value

    Never log the raw value.  Use fingerprint + last4 in audit entries.
    The fingerprint is NOT suitable as authentication.
    """
    if not value:
        return "sha256:empty", ""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    last4 = value[-4:] if len(value) >= 4 else value
    return f"sha256:{digest}", last4
