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


# ─── Secret Classification ──────────────────────────────────────────────


from enum import Enum  # noqa: E402 — kept near the class that uses it


class SecretClass(str, Enum):
    """Classification for secrets stored in the backend.

    Determines whether a secret is safe to render into runtime env files.
    """

    RUNTIME = "runtime"  # Safe to render into .env.generated (api_key, token)
    ADMIN = "admin"  # Must NEVER render (admin_key, admin_secret)
    CONFIG = "config"  # Non-secret metadata (project_id, base_url, workspace_id)


# ─── Validation Result ──────────────────────────────────────────────────


class ValidationReason(str, Enum):
    """Structured reason codes for key validation outcomes.

    Distinguishes transient failures (safe to retry) from permanent ones.
    """

    OK = "ok"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    AUTH_DENIED = "auth_denied"  # 401 — key is invalid/revoked
    RATE_LIMITED = "rate_limited"  # 429
    FORBIDDEN = "forbidden"  # 403 — scope/permission issue
    QUOTA_OR_BILLING = "quota_or_billing"  # 400/402 — no credits, billing issue
    SERVER_ERROR = "server_error"  # 5xx
    INVALID_FORMAT = "invalid_format"  # Key doesn't match expected pattern
    SDK_UNAVAILABLE = "sdk_unavailable"  # Python SDK not installed
    UNKNOWN = "unknown"


# Reason classes that are safe to retry (transient failures)
RETRYABLE_REASONS: frozenset[ValidationReason] = frozenset(
    {
        ValidationReason.NETWORK_ERROR,
        ValidationReason.TIMEOUT,
        ValidationReason.RATE_LIMITED,
        ValidationReason.SERVER_ERROR,
    }
)


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a candidate key against a provider API.

    Never contains raw key material.  Suitable for logging and audit.
    """

    valid: bool
    reason_class: ValidationReason = ValidationReason.OK
    detail: str = ""
    http_status: int = 0
    retry_recommended: bool = False
    retry_after_seconds: int = 0

    @property
    def is_transient(self) -> bool:
        """True if this failure is likely transient and retry may help."""
        return self.reason_class in RETRYABLE_REASONS


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
    secret_class: str = SecretClass.RUNTIME.value
    renderable_to_env: bool = True
    rotation_supported: str = "manual"  # "auto", "hybrid", or "manual"
    revocation_supported: bool = False


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
