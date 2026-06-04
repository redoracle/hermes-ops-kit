"""
Hermes Ops Kit — Secret Backend Interface

Defines the abstract protocol that all secret backends must implement.
Provider rotators depend ONLY on this interface — never on Vaultwarden internals.

See spec sections 9 and 10.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# ─── Error Hierarchy (spec section 23) ────────────────────────────────


class HermesKeyRotateError(Exception):
    """Base exception for all key rotation errors."""


class SecretBackendError(HermesKeyRotateError):
    """Generic secret backend error."""


class VaultwardenAuthError(SecretBackendError):
    """Authentication to Vaultwarden failed."""


class VaultwardenUnlockError(SecretBackendError):
    """Vaultwarden vault unlock failed."""


class VaultwardenTLSError(SecretBackendError):
    """TLS validation for Vaultwarden endpoint failed."""


class VaultwardenUnavailable(SecretBackendError):
    """Vaultwarden backend is unreachable."""


class SecretNotFound(SecretBackendError):
    """Requested secret does not exist in the backend."""


class SecretWriteFailed(SecretBackendError):
    """Could not write secret to backend."""


class InsecureSecretBackendError(SecretBackendError):
    """Backend transport is insecure (HTTP, invalid TLS, public bind)."""


class EnvRenderError(HermesKeyRotateError):
    """Environment file rendering failed."""


class SmokeTestFailed(HermesKeyRotateError):
    """Provider smoke test did not pass — do not activate new credential."""


class ProviderRotationError(HermesKeyRotateError):
    """Provider-specific rotation logic failed."""


class UnsafeSecretWriteError(HermesKeyRotateError):
    """Attempted to write raw secrets to an unsafe sink (Obsidian, audit, etc.)."""


# ─── Data Classes ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class SecretValue:
    """A secret value retrieved from the backend."""

    name: str
    value: str
    version: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SecretMetadata:
    """Metadata about a secret (no raw value).  Safe for logs and Obsidian."""

    name: str
    fingerprint: str | None
    last4: str | None
    updated_at: str | None
    version: str | None
    provider: str | None = None
    item_id: str | None = None


class SecretBackend(Protocol):
    """Protocol that every secret backend must satisfy.

    Provider rotators import this protocol and call these methods.
    The real implementation is VaultwardenSecretBackend.
    """

    def get_secret(self, name: str) -> SecretValue | None:
        """Retrieve a secret by its internal ref name (e.g. 'hermes/openai/api_key')."""
        ...

    def set_secret(
        self,
        name: str,
        value: str,
        metadata: dict[str, Any] | None = None,
    ) -> SecretMetadata:
        """Store (create or update) a secret.  Returns metadata only — never the raw value."""
        ...

    def delete_secret(self, name: str) -> None:
        """Delete a secret.  Non-destructive backends may soft-delete / archive."""
        ...

    def backup_secret(self, name: str) -> SecretValue | None:
        """Capture current secret for rollback before a rotation."""
        ...

    def restore_secret(self, name: str, previous: SecretValue) -> SecretMetadata:
        """Restore a previous secret version after a failed rotation."""
        ...

    def list_secret_refs(self, prefix: str | None = None) -> list[str]:
        """List all internal ref names, optionally filtered by *prefix*."""
        ...

    def get_metadata(self, name: str) -> SecretMetadata | None:
        """Return metadata for *name* without retrieving the raw value."""
        ...

    def healthcheck(self) -> dict[str, Any]:
        """Return backend health status.

        Must include at minimum: {"ok": bool, "backend": str, ...}
        """
        ...
