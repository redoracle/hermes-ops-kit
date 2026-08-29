"""Hermes Ops Kit — OpenAI Provider Rotator

Mode: full-auto via project service account rotation (spec section 19.1).

Flow:
  1. Read OpenAI admin key from Vaultwarden.
  2. Create new project service account or project API key.
  3. Receive unredacted key once — store candidate in Vaultwarden.
  4. Render temporary env.
  5. Smoke-test /models and a minimal chat request.
  6. Activate generated env.
  7. Delete old service account/key if supported.
  8. Write sanitized audit + Obsidian note.

Requires OPENAI_ADMIN_KEY in Vaultwarden for full-auto mode.
Without it, falls back to manual-new-key mode (same as DeepSeek).
"""

from __future__ import annotations

import sys
import time

from ..providers.base import BaseRotator  # pyright: ignore[reportMissingImports]
from ..security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]
from ..security.secret_backend import (  # pyright: ignore[reportMissingImports]
    SecretWriteFailed,
    ValidationReason,
    ValidationResult,
)

OPENAI_API_KEY_REF = "hermes/openai/api_key"
OPENAI_ADMIN_KEY_REF = "hermes/openai/admin_key"
OPENAI_PROJECT_ID_REF = "hermes/openai/project_id"

from ..provider_catalog import PROVIDER_ENV_KEYS  # noqa: E402


class OpenAIRotator(BaseRotator):
    """Rotate OpenAI API keys using project service accounts."""

    provider = "openai"

    # ── Validation ──────────────────────────────────────────────────────

    def validate_new_key(self, key: str) -> ValidationResult:
        """Validate a candidate key against the OpenAI API.

        Returns a structured ValidationResult with typed reason codes
        so callers can distinguish transient failures from permanent ones.
        """
        try:
            import openai  # pyright: ignore[reportMissingImports]
        except Exception:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.SDK_UNAVAILABLE,
                detail="openai SDK not installed",
                retry_recommended=False,
            )

        try:
            client = openai.OpenAI(api_key=key, timeout=15)
            models = client.models.list()
            if not models.data:
                return ValidationResult(
                    valid=False,
                    reason_class=ValidationReason.UNKNOWN,
                    detail="/models returned empty data",
                )
            return ValidationResult(valid=True)
        except openai.AuthenticationError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.AUTH_DENIED,
                detail=str(e),
                http_status=401,
                retry_recommended=False,
            )
        except openai.RateLimitError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.RATE_LIMITED,
                detail=str(e),
                http_status=429,
                retry_recommended=True,
            )
        except openai.APITimeoutError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.TIMEOUT,
                detail=str(e),
                retry_recommended=True,
            )
        except openai.APIConnectionError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.NETWORK_ERROR,
                detail=str(e),
                retry_recommended=True,
            )
        except openai.InternalServerError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.SERVER_ERROR,
                detail=str(e),
                http_status=500,
                retry_recommended=True,
            )
        except Exception as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.UNKNOWN,
                detail=str(e),
            )

    # ── Smoke test ──────────────────────────────────────────────────────

    def smoke_test(self) -> tuple[bool, str]:
        """Smoke test the active OpenAI key."""
        secret = self.backend.get_secret(OPENAI_API_KEY_REF)
        if not secret or not secret.value:
            return False, "No active API key"

        try:
            import openai  # pyright: ignore[reportMissingImports]
        except Exception:
            return False, "openai SDK not available"

        try:
            client = openai.OpenAI(api_key=secret.value, timeout=15)
            models = client.models.list()
            chat = client.chat.completions.create(
                model="gpt-5.4-mini",
                messages=[{"role": "user", "content": "Smoke test"}],
                max_tokens=5,
            )
            if models.data and chat.choices:
                return True, "smoke test passed"
            return False, "empty response"
        except Exception as e:
            return False, f"smoke test failed: {e}"

    # ── Admin key helpers ───────────────────────────────────────────────

    def _has_admin_key(self) -> bool:
        """Check if an admin key is available for full-auto rotation."""
        admin = self.backend.get_secret(OPENAI_ADMIN_KEY_REF)
        return admin is not None and bool(admin.value)

    def _create_service_account_key(
        self, admin_key: str, project_id: str
    ) -> str | None:
        """Create a new project API key via the OpenAI admin API.

        NOTE: OpenAI deprecated the REST API for key creation in 2025-2026.
        The old endpoints (/v1/organization/projects/{id}/api_keys) return 404/405.
        New keys must be created in the dashboard (https://platform.openai.com/api-keys).
        This method returns None to trigger manual fallback.
        The admin key can still list and delete existing keys.
        """
        try:
            import requests  # pyright: ignore[reportMissingImports,reportMissingModuleSource]
        except Exception:
            return None

        # Try the known endpoints — both are deprecated but may still work
        # for some account tiers
        urls = [
            f"https://api.openai.com/v1/organization/projects/{project_id}/api_keys",
            "https://api.openai.com/v1/organization/api_keys",
        ]
        headers = {
            "Authorization": f"Bearer {admin_key}",
            "Content-Type": "application/json",
        }
        for url in urls:
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json={"name": f"hermes-key-{int(time.time())}"},
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    key = data.get("secret") or data.get("key", {}).get("secret")
                    if key:
                        return key
            except Exception:
                continue
        return None

    def _delete_old_key(self, admin_key: str, old_api_key_id: str) -> bool:
        """Delete old API key via admin API."""
        if not old_api_key_id:
            return False
        try:
            import requests  # pyright: ignore[reportMissingImports,reportMissingModuleSource]
        except Exception:
            return False

        headers = {"Authorization": f"Bearer {admin_key}"}
        try:
            resp = requests.delete(
                f"https://api.openai.com/v1/api_keys/{old_api_key_id}",
                headers=headers,
                timeout=15,
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ── Revoke / cleanup (admin API support) ─────────────────────────────

    def revoke_key(self, secret_ref: str, admin_credential: str | None = None) -> bool:
        """Revoke/delete an old key after successful rotation.

        Delegates to _delete_old_key using the admin key.
        """
        if not self._has_admin_key():
            return False
        admin = self.backend.get_secret(OPENAI_ADMIN_KEY_REF)
        if not admin:
            return False
        old_meta = self.backend.get_metadata(secret_ref)
        if not old_meta or not old_meta.item_id:
            return False
        return self._delete_old_key(admin.value, old_meta.item_id)

    def cleanup_orphaned_key(self, key_value: str) -> bool:
        """Delete a just-created key that failed validation.

        Attempts to match the key via the OpenAI API keys list endpoint
        and delete it. Best-effort — returns False if cleanup is not
        possible.
        """
        if not key_value:
            return False
        if not self._has_admin_key():
            return False
        admin = self.backend.get_secret(OPENAI_ADMIN_KEY_REF)
        if not admin:
            return False
        try:
            import requests  # pyright: ignore[reportMissingImports,reportMissingModuleSource]
        except Exception:
            return False
        headers = {"Authorization": f"Bearer {admin.value}"}
        try:
            resp = requests.get(
                "https://api.openai.com/v1/api_keys",
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            for k in data.get("data", []):
                if (
                    k.get("secret") == key_value
                    or k.get("key", {}).get("secret") == key_value
                ):
                    kid = k.get("id")
                    if kid:
                        return self._delete_old_key(admin.value, kid)
            return False
        except Exception:
            return False

    # ── Rotation ────────────────────────────────────────────────────────

    def rotate(self, candidate_key: str | None = None) -> dict:
        """Execute OpenAI rotation flow."""
        from ..audit.audit_log import audit_rotation_attempt  # pyright: ignore[reportMissingImports]

        old_secret = self.backend.get_secret(OPENAI_API_KEY_REF)
        old_fp, _ = secret_fingerprint(old_secret.value) if old_secret else (None, None)

        # ── Backup current secret for rollback ──
        backup = self.backend.backup_secret(OPENAI_API_KEY_REF)

        # ── Full-auto path: use admin key ──
        auto_created = False
        if self._has_admin_key() and not candidate_key:
            admin = self.backend.get_secret(OPENAI_ADMIN_KEY_REF)
            project = self.backend.get_secret(OPENAI_PROJECT_ID_REF)
            if admin and project:
                candidate_key = self._create_service_account_key(
                    admin.value, project.value
                )
                if candidate_key:
                    auto_created = True

        # ── Fallback: manual key ──
        if not candidate_key:
            candidate_key = self._read_key_stdin()

        candidate_key = candidate_key.strip()

        # ── Validate with retry ──
        vr = self.validate_with_retry(candidate_key)
        if not vr.valid:
            if vr.reason_class == ValidationReason.QUOTA_OR_BILLING:
                # Key is valid but account has no credits — store anyway with warning
                pass
            else:
                # Clean up auto-created key that failed validation
                if auto_created:
                    self.cleanup_orphaned_key(candidate_key)
                return {
                    "ok": False,
                    "error": f"Candidate key unusable: {vr.reason_class.value}",
                    "validation": {
                        "reason": vr.reason_class.value,
                        "detail": vr.detail,
                        "http_status": vr.http_status,
                    },
                }

        new_fp, new_l4 = secret_fingerprint(candidate_key)

        # ── Store ──
        try:
            self.backend.set_secret(
                OPENAI_API_KEY_REF,
                candidate_key,
                metadata={
                    "rotation_mode": "full-auto" if self._has_admin_key() else "manual",
                    "last_rotated_at": str(int(time.time())),
                    "old_fingerprint": old_fp or "none",
                },
            )
        except SecretWriteFailed as e:
            if auto_created:
                self.cleanup_orphaned_key(candidate_key)
            return {"ok": False, "error": f"Store failed: {e}"}

        # ── Smoke test (before rendering env, so bad key never reaches .env.generated) ──
        passed, detail = self.smoke_test()
        if not passed:
            if backup:
                self.backend.restore_secret(OPENAI_API_KEY_REF, backup)
            return {"ok": False, "error": f"Smoke test failed: {detail}"}

        # ── Render env (only after smoke test passes) ──
        from ..env.render_env import render_env  # pyright: ignore[reportMissingImports]

        try:
            env_path = render_env(self.backend)
        except Exception as e:
            if backup:
                self.backend.restore_secret(OPENAI_API_KEY_REF, backup)
                render_env(self.backend)  # re-render with rolled-back key
            return {"ok": False, "error": f"Env render failed: {e}"}

        # ── Revoke old key if full-auto ──
        old_revoked = False
        if self._has_admin_key() and old_secret:
            old_meta = self.backend.get_metadata(OPENAI_API_KEY_REF)
            if old_meta and old_meta.item_id:
                admin = self.backend.get_secret(OPENAI_ADMIN_KEY_REF)
                if admin:
                    old_revoked = self._delete_old_key(admin.value, old_meta.item_id)

        # ── Audit ──
        audit_rotation_attempt(
            provider="openai",
            status="success",
            old_fp=old_fp,
            new_fp=new_fp,
            old_revoked=old_revoked,
            manual_action=not old_revoked,
            env_keys_updated=list(PROVIDER_ENV_KEYS["openai"]),
        )

        return {
            "ok": True,
            "provider": "openai",
            "operation": "rotation",
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
            "new_last4": new_l4,
            "old_revoked": old_revoked,
            "env_rendered": env_path,
            "warnings": [
                "Account has billing/credit issues — key stored but API calls may fail until resolved"
            ]
            if vr.reason_class == ValidationReason.QUOTA_OR_BILLING
            else [],
        }

    def _read_key_stdin(self) -> str:
        if sys.stdin.isatty():
            import getpass

            return getpass.getpass("Paste new OpenAI API key: ")
        return sys.stdin.readline()
