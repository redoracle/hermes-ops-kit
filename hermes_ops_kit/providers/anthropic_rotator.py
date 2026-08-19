"""Hermes Ops Kit — Anthropic Provider Rotator

Mode: partial-auto / admin-hybrid (spec section 19.2).

Admin API capabilities depend on account tier:
  - Key listing (GET /v1/organizations/api_keys): available for org accounts
  - Key deactivation/archive (POST /v1/api_keys/{id}/archive): available
  - Key creation (POST /v1/organizations/workspaces/{id}/api_keys): may
    require elevated admin privileges not available on all accounts.
    Falls back gracefully to manual mode.

Flow:
  1. Check whether Anthropic Admin API is available (admin key in Vaultwarden).
  2. If auto-create succeeds: create candidate API key via Admin API.
  3. If auto-create fails (404, 403): fall back to manual stdin prompt.
  4. Store candidate in Vaultwarden.
  5. Render temporary env.
  6. Smoke-test Messages API.
  7. Activate generated env.
  8. Set old key inactive/archive if Admin API supports it.
  9. Otherwise mark manual action required.
 10. Write sanitized audit + Obsidian note.
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

ANTHROPIC_API_KEY_REF = "hermes/anthropic/api_key"
ANTHROPIC_ADMIN_KEY_REF = "hermes/anthropic/admin_key"
ANTHROPIC_WORKSPACE_ID_REF = "hermes/anthropic/workspace_id"
ANTHROPIC_API_KEY_ID_REF = "hermes/anthropic/api_key_id"

SUPPORTED_MODES = ["validate-only", "manual-new-key", "deactivate-old", "admin-hybrid"]


class AnthropicRotator(BaseRotator):
    """Rotate Anthropic API keys using Admin API (hybrid mode)."""

    provider = "anthropic"

    # ── Validation ──────────────────────────────────────────────────────

    def validate_new_key(self, key: str) -> ValidationResult:
        """Validate a candidate Anthropic API key.

        Returns a structured ValidationResult with typed reason codes
        so callers can distinguish transient failures from permanent ones.
        """
        try:
            import anthropic  # pyright: ignore[reportMissingImports]
        except Exception:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.SDK_UNAVAILABLE,
                detail="anthropic SDK not installed",
                retry_recommended=False,
            )

        try:
            client = anthropic.Anthropic(
                api_key=key,
                base_url="https://api.anthropic.com",  # explicit — never trust env ANTHROPIC_BASE_URL
                timeout=15,
            )
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=5,
                messages=[{"role": "user", "content": "Hi"}],
            )
            if response.content:
                return ValidationResult(valid=True)
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.UNKNOWN,
                detail="empty response content",
            )
        except anthropic.AuthenticationError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.AUTH_DENIED,
                detail=str(e),
                http_status=401,
                retry_recommended=False,
            )
        except anthropic.BadRequestError as e:
            # HTTP 400 — may be credit balance, invalid model, or malformed request
            msg = str(e).lower()
            if "credit balance" in msg or "billing" in msg:
                return ValidationResult(
                    valid=False,
                    reason_class=ValidationReason.QUOTA_OR_BILLING,
                    detail=f"Anthropic account has no credits — key IS valid but cannot make API calls: {e}",
                    http_status=400,
                    retry_recommended=False,
                )
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.UNKNOWN,
                detail=str(e),
                http_status=400,
            )
        except anthropic.RateLimitError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.RATE_LIMITED,
                detail=str(e),
                http_status=429,
                retry_recommended=True,
            )
        except anthropic.APITimeoutError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.TIMEOUT,
                detail=str(e),
                retry_recommended=True,
            )
        except anthropic.APIConnectionError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.NETWORK_ERROR,
                detail=str(e),
                retry_recommended=True,
            )
        except anthropic.InternalServerError as e:
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
        """Smoke test the active Anthropic key.

        Quota/billing errors are treated as a pass (key is valid) with warning.
        """
        secret = self.backend.get_secret(ANTHROPIC_API_KEY_REF)
        if not secret or not secret.value:
            return False, "No active API key"

        try:
            import anthropic  # pyright: ignore[reportMissingImports]
        except Exception:
            return False, "anthropic SDK not available"

        try:
            client = anthropic.Anthropic(
                api_key=secret.value,
                base_url="https://api.anthropic.com",  # explicit — never trust env
                timeout=15,
            )
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=5,
                messages=[{"role": "user", "content": "Smoke test"}],
            )
            return True, "smoke test passed"
        except anthropic.BadRequestError as e:
            msg = str(e).lower()
            if "credit balance" in msg or "billing" in msg:
                return (
                    True,
                    "smoke test passed with warning: account has no credits (key is valid)",
                )
            return False, f"smoke test failed: {e}"
        except Exception as e:
            return False, f"smoke test failed: {e}"

    # ── Admin key helpers ───────────────────────────────────────────────

    def _has_admin_key(self) -> bool:
        admin = self.backend.get_secret(ANTHROPIC_ADMIN_KEY_REF)
        return admin is not None and bool(admin.value)

    def _create_api_key(self, admin_key: str, workspace_id: str) -> str | None:
        """Create a new API key via Anthropic Admin API.

        Returns the new key string or None if creation is not supported
        (e.g., 404 endpoint not available, 403 insufficient privileges).
        The caller falls back to manual stdin prompt when None is returned.
        """
        try:
            import requests  # pyright: ignore[reportMissingImports,reportMissingModuleSource]
        except Exception:
            return None

        headers = {
            "x-api-key": admin_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        # Try workspace-scoped endpoint first, then org-level
        urls = [
            f"https://api.anthropic.com/v1/organizations/workspaces/{workspace_id}/api_keys",
            "https://api.anthropic.com/v1/organizations/api_keys",
        ]
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
                    key = data.get("key") or data.get("secret") or data.get("api_key")
                    if key:
                        return key
                # 404/405 = endpoint not available, 403 = insufficient privileges
                # Silently fall through to next URL or return None for manual mode
            except Exception:
                continue
        return None

    def _archive_old_key(self, admin_key: str, old_key_id: str) -> bool:
        """Archive/deactivate old API key via Admin API."""
        if not old_key_id:
            return False

        try:
            import requests  # pyright: ignore[reportMissingImports,reportMissingModuleSource]
        except Exception:
            return False

        headers = {
            "x-api-key": admin_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            resp = requests.post(
                f"https://api.anthropic.com/v1/api_keys/{old_key_id}/archive",
                headers=headers,
                timeout=15,
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ── Revoke / cleanup (admin API support) ─────────────────────────────

    def revoke_key(self, secret_ref: str, admin_credential: str | None = None) -> bool:
        """Revoke/archive an old key after successful rotation.

        Delegates to _archive_old_key using the admin key.
        """
        if not self._has_admin_key():
            return False
        admin = self.backend.get_secret(ANTHROPIC_ADMIN_KEY_REF)
        if not admin:
            return False
        old_meta = self.backend.get_metadata(secret_ref)
        if not old_meta or not old_meta.item_id:
            return False
        return self._archive_old_key(admin.value, old_meta.item_id)

    def cleanup_orphaned_key(self, key_value: str) -> bool:
        """Delete a just-created key that failed validation.

        Attempts to match the key via the Anthropic API keys list endpoint
        and archive it. Best-effort — returns False if cleanup is not
        possible.
        """
        if not key_value or not self._has_admin_key():
            return False
        admin = self.backend.get_secret(ANTHROPIC_ADMIN_KEY_REF)
        if not admin:
            return False
        try:
            import requests  # pyright: ignore[reportMissingImports,reportMissingModuleSource]
        except Exception:
            return False
        headers = {
            "x-api-key": admin.value,
            "anthropic-version": "2023-06-01",
        }
        try:
            # Anthropic API keys list may require workspace context
            workspace = self.backend.get_secret(ANTHROPIC_WORKSPACE_ID_REF)
            if workspace:
                resp = requests.get(
                    f"https://api.anthropic.com/v1/organizations/workspaces/{workspace.value}/api_keys",
                    headers=headers,
                    timeout=15,
                )
            else:
                resp = requests.get(
                    "https://api.anthropic.com/v1/api_keys",
                    headers=headers,
                    timeout=15,
                )
            if resp.status_code != 200:
                return False
            for k in resp.json().get("data", []):
                if k.get("key") == key_value or k.get("secret") == key_value:
                    kid = k.get("id")
                    if kid:
                        return self._archive_old_key(admin.value, kid)
            return False
        except Exception:
            return False

    # ── Rotation ────────────────────────────────────────────────────────

    def rotate(self, candidate_key: str | None = None) -> dict:
        """Execute Anthropic rotation flow."""
        from ..audit.audit_log import audit_rotation_attempt  # pyright: ignore[reportMissingImports]

        old_secret = self.backend.get_secret(ANTHROPIC_API_KEY_REF)
        old_fp, _ = secret_fingerprint(old_secret.value) if old_secret else (None, None)

        # ── Backup current secret for rollback ──
        backup = self.backend.backup_secret(ANTHROPIC_API_KEY_REF)

        # Determine mode
        mode = "manual-new-key"
        auto_created = False
        if self._has_admin_key():
            mode = "admin-hybrid"
            workspace = self.backend.get_secret(ANTHROPIC_WORKSPACE_ID_REF)
            admin = self.backend.get_secret(ANTHROPIC_ADMIN_KEY_REF)
            if workspace and admin:
                created = self._create_api_key(admin.value, workspace.value)
                if created:
                    candidate_key = created
                    auto_created = True

        # Fallback: manual
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
                ANTHROPIC_API_KEY_REF,
                candidate_key,
                metadata={
                    "rotation_mode": mode,
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
                self.backend.restore_secret(ANTHROPIC_API_KEY_REF, backup)
            return {"ok": False, "error": f"Smoke test failed: {detail}"}

        # ── Render env (only after smoke test passes) ──
        from ..env.render_env import render_env  # pyright: ignore[reportMissingImports]

        try:
            env_path = render_env(self.backend)
        except Exception as e:
            if backup:
                self.backend.restore_secret(ANTHROPIC_API_KEY_REF, backup)
                render_env(self.backend)  # re-render with rolled-back key
            return {"ok": False, "error": f"Env render failed: {e}"}

        # ── Archive old key if admin-hybrid ──
        old_archived = False
        if mode == "admin-hybrid":
            old_meta = self.backend.get_metadata(ANTHROPIC_API_KEY_REF)
            admin = self.backend.get_secret(ANTHROPIC_ADMIN_KEY_REF)
            if old_meta and old_meta.item_id and admin:
                old_archived = self._archive_old_key(admin.value, old_meta.item_id)

        # ── Audit ──
        audit_rotation_attempt(
            provider="anthropic",
            status="success",
            old_fp=old_fp,
            new_fp=new_fp,
            old_revoked=old_archived,
            manual_action=not old_archived,
            env_keys_updated=["ANTHROPIC_API_KEY"],
        )

        return {
            "ok": True,
            "provider": "anthropic",
            "operation": "rotation",
            "mode": mode,
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
            "new_last4": new_l4,
            "old_archived": old_archived,
            "env_rendered": env_path,
        }

    def _read_key_stdin(self) -> str:
        if sys.stdin.isatty():
            import getpass

            return getpass.getpass("Paste new Anthropic API key: ")
        return sys.stdin.readline()
