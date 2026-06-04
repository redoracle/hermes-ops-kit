"""Hermes Ops Kit — Provider Rotator Base Class

Abstract base for all provider key rotators.
Depends ONLY on SecretBackend — never on Vaultwarden internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from security.secret_backend import SecretBackend  # pyright: ignore[reportMissingImports]


class BaseRotator(ABC):
    """Abstract provider key rotator.

    Subclasses implement provider-specific rotation logic.
    All rotators depend only on the SecretBackend protocol.
    """

    provider: str  # e.g. "openai", "deepseek"

    def __init__(self, backend: SecretBackend) -> None:
        self.backend = backend

    @abstractmethod
    def validate_new_key(self, key: str) -> bool:
        """Validate a candidate key against the provider API."""
        ...

    @abstractmethod
    def smoke_test(self) -> tuple[bool, str]:
        """Run a smoke test against the active provider credential.

        Returns (passed, detail).
        """
        ...

    @abstractmethod
    def rotate(self, candidate_key: str | None = None) -> dict:
        """Execute the rotation flow for this provider.

        Must follow the two-phase rotation pattern (spec section 17):
        1. Store candidate
        2. Smoke test
        3. Activate only if smoke passes
        4. Mark old for revocation
        """
        ...

    def get_current_fingerprint(self, secret_ref: str) -> tuple[str, str]:
        """Get the fingerprint of the current active secret."""
        from security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]

        secret = self.backend.get_secret(secret_ref)
        if secret:
            return secret_fingerprint(secret.value)
        return ("unknown", "")
