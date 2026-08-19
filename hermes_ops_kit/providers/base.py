"""Hermes Ops Kit — Provider Rotator Base Class

Abstract base for all provider key rotators.
Depends ONLY on SecretBackend — never on Vaultwarden internals.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..security.secret_backend import (  # pyright: ignore[reportMissingImports]
    RETRYABLE_REASONS,
    SecretBackend,
    ValidationResult,
)

if TYPE_CHECKING:
    pass


class BaseRotator(ABC):
    """Abstract provider key rotator.

    Subclasses implement provider-specific rotation logic.
    All rotators depend only on the SecretBackend protocol.
    """

    # Retry configuration for transient validation failures
    MAX_RETRIES: int = 3
    BASE_DELAY_SECONDS: float = 1.0
    MAX_DELAY_SECONDS: float = 30.0

    provider: str  # e.g. "openai", "deepseek"

    def __init__(self, backend: SecretBackend) -> None:
        self.backend = backend

    @abstractmethod
    def validate_new_key(self, key: str) -> ValidationResult:
        """Validate a candidate key against the provider API.

        Returns a structured ValidationResult — never a bare bool.
        Subclasses must parse provider-specific errors into typed
        ValidationReason values so callers can distinguish transient
        failures from permanent ones.
        """
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

        Must follow the two-phase rotation pattern:
        1. Backup old key for rollback
        2. Acquire or create candidate key
        3. Validate candidate (with retry for transient failures)
        4. Store candidate in secret backend
        5. Render env atomically
        6. Smoke test
        7. Activate only if smoke passes
        8. Revoke old key (if supported)
        9. Audit
        """
        ...

    def validate_with_retry(self, key: str) -> ValidationResult:
        """Call validate_new_key() with exponential backoff on transient failures.

        Retries only for transient reason classes (network, timeout,
        rate-limit, server-error).  Permanent failures (auth_denied,
        forbidden, invalid_format) are returned immediately.
        """
        last_result: ValidationResult | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            result = self.validate_new_key(key)
            if result.valid or result.reason_class not in RETRYABLE_REASONS:
                return result
            last_result = result
            if attempt < self.MAX_RETRIES:
                delay = self.BASE_DELAY_SECONDS * (2**attempt)
                if result.retry_after_seconds:
                    delay = max(delay, result.retry_after_seconds)
                time.sleep(min(delay, self.MAX_DELAY_SECONDS))
        # Exhausted retries — return last transient failure
        assert last_result is not None
        return last_result

    def get_current_fingerprint(self, secret_ref: str) -> tuple[str, str]:
        """Get the fingerprint of the current active secret."""
        from ..security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]

        secret = self.backend.get_secret(secret_ref)
        if secret:
            return secret_fingerprint(secret.value)
        return ("unknown", "")

    def revoke_key(self, secret_ref: str, admin_credential: str | None = None) -> bool:
        """Revoke/delete an old key after successful rotation.

        Returns True if revocation succeeded or was not needed.
        Providers without revocation support return False (manual action).
        """
        return False

    def cleanup_orphaned_key(self, key_value: str) -> bool:
        """Delete a just-created key that failed validation.

        Best-effort — only applies to providers with admin API support.
        Called when auto-creation succeeds but the resulting key is unusable.
        Returns True if the orphan was cleaned up (or no cleanup needed).
        """
        return False
