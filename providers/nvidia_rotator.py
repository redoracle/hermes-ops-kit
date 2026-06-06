"""Hermes Ops Kit — NVIDIA NIM Provider Rotator

Mode: manual-new-key + validation.

NVIDIA NIM is OpenAI-compatible, so validation uses the openai SDK
pointed at https://integrate.api.nvidia.com/v1.

Flow:
  1. Accept new API key via stdin (not CLI args — avoids shell history leak).
  2. Validate key against NVIDIA NIM /v1/models and a minimal chat request.
  3. Store candidate key in Vaultwarden.
  4. Render temporary env and smoke-test.
  5. Activate env if smoke passes.
  6. Mark old key for manual revocation in NVIDIA Build console.
  7. Write sanitized audit + Obsidian note.
"""

from __future__ import annotations

import os
import sys
import time

from providers.base import BaseRotator  # pyright: ignore[reportMissingImports]
from security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]
from security.secret_backend import (  # pyright: ignore[reportMissingImports]
    SecretWriteFailed,
    ValidationReason,
    ValidationResult,
)

NVIDIA_API_REF = "hermes/nvidia/api_key"
NVIDIA_BASE_URL_REF = "hermes/nvidia/base_url"

# Default NIM base URL (serverless NVIDIA API endpoint)
NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
# A known-available model for validation/smoke probes
NVIDIA_PROBE_MODEL = "nvidia/nemotron-3-nano-30b-a3b"


class NvidiaRotator(BaseRotator):
    """Rotate NVIDIA NIM API keys.

    NVIDIA NIM is OpenAI-compatible, so validation uses the openai SDK
    pointed at https://integrate.api.nvidia.com/v1.

    Rotation is manual-new-key only — NVIDIA has no admin key management
    API. Old keys must be revoked manually in the NVIDIA Build console.
    """

    provider = "nvidia"

    # ── Validation ──────────────────────────────────────────────────────

    def validate_new_key(self, key: str) -> ValidationResult:
        """Validate a candidate key against the NVIDIA NIM API.

        Uses the openai SDK pointed at the NVIDIA NIM base URL.
        Two checks: GET /v1/models (needs valid auth) and a minimal chat request.

        Returns a structured ValidationResult with typed reason codes.
        """
        try:
            import openai  # pyright: ignore[reportMissingImports]
        except Exception:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.SDK_UNAVAILABLE,
                detail="openai SDK not installed (needed for NVIDIA NIM API compatibility)",
                retry_recommended=False,
            )

        base_url = os.environ.get("NVIDIA_BASE_URL", NVIDIA_DEFAULT_BASE_URL)

        try:
            client = openai.OpenAI(api_key=key, base_url=base_url, timeout=15)

            # 1. List models
            models = client.models.list()
            if not models.data:
                return ValidationResult(
                    valid=False,
                    reason_class=ValidationReason.UNKNOWN,
                    detail="/v1/models returned empty data",
                )

            # 2. Minimal chat
            chat = client.chat.completions.create(
                model=NVIDIA_PROBE_MODEL,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            if chat.choices:
                return ValidationResult(valid=True)
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.UNKNOWN,
                detail="empty chat response",
            )

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
        except openai.PermissionDeniedError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.QUOTA_OR_BILLING,
                detail=str(e),
                http_status=403,
                retry_recommended=False,
            )
        except getattr(openai, "APIError", Exception) as e:
            # Map billing/quota HTTP responses (402) or billing-related messages
            # to QUOTA_OR_BILLING so callers (rotate) can treat them specially.
            status = getattr(e, "http_status", 0) or 0
            msg = str(e)
            if status == 402 or "billing" in msg.lower() or "payment" in msg.lower():
                return ValidationResult(
                    valid=False,
                    reason_class=ValidationReason.QUOTA_OR_BILLING,
                    detail=msg,
                    http_status=status or 402,
                    retry_recommended=False,
                )
            # Fall through to a generic unknown for other API errors
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.UNKNOWN,
                detail=msg,
                http_status=status,
            )
        except Exception as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.UNKNOWN,
                detail=str(e),
            )

    # ── Smoke test ──────────────────────────────────────────────────────

    def smoke_test(self) -> tuple[bool, str]:
        """Run a smoke test against the currently active NVIDIA NIM key.

        Returns (passed, detail_string).
        """
        secret = self.backend.get_secret(NVIDIA_API_REF)
        if not secret or not secret.value:
            return False, "No active API key found in Vaultwarden"

        try:
            import openai  # pyright: ignore[reportMissingImports]
        except Exception:
            return False, "openai SDK not available"

        base_url = os.environ.get("NVIDIA_BASE_URL", NVIDIA_DEFAULT_BASE_URL)

        try:
            client = openai.OpenAI(api_key=secret.value, base_url=base_url, timeout=15)
            models = client.models.list()
            chat = client.chat.completions.create(
                model=NVIDIA_PROBE_MODEL,
                messages=[{"role": "user", "content": "Smoke test"}],
                max_tokens=5,
            )
            if models.data and chat.choices:
                return True, "smoke test passed: /v1/models + chat OK"
            return False, "smoke test failed: empty response"
        except Exception as e:
            return False, f"smoke test failed: {e}"

    # ── Revoke / cleanup (no admin API — manual only) ────────────────────

    def revoke_key(self, secret_ref: str, admin_credential: str | None = None) -> bool:
        """NVIDIA NIM has no admin key management API.

        Rotation is manual-new-key only — old key must be revoked
        manually in the NVIDIA Build console (https://build.nvidia.com).
        """
        return False

    def cleanup_orphaned_key(self, key_value: str) -> bool:
        """NVIDIA NIM has no admin API for key deletion.

        Orphaned keys must be cleaned up manually in the NVIDIA Build console.
        """
        return False

    # ── Rotation ────────────────────────────────────────────────────────

    def rotate(self, candidate_key: str | None = None) -> dict:
        """Execute the NVIDIA NIM rotation flow.

        If candidate_key is None, reads from stdin (prompt-based).
        """
        from audit.audit_log import audit_rotation_attempt  # pyright: ignore[reportMissingImports]

        # ── 1. Acquire candidate key ──
        if not candidate_key:
            candidate_key = self._read_key_stdin()

        candidate_key = candidate_key.strip()
        if not candidate_key:
            return {"ok": False, "error": "No candidate key provided"}

        # ── 2. Get current key fingerprint ──
        old_secret = self.backend.get_secret(NVIDIA_API_REF)
        old_fp, old_l4 = (
            secret_fingerprint(old_secret.value) if old_secret else (None, None)
        )

        # ── Backup current secret for rollback ──
        backup = self.backend.backup_secret(NVIDIA_API_REF)
        if backup is None:
            return {
                "ok": False,
                "error": "Backup failed: could not read current secret for rollback",
            }

        # ── 3. Validate with retry ──
        vr = self.validate_with_retry(candidate_key)
        if not vr.valid:
            if vr.reason_class == ValidationReason.QUOTA_OR_BILLING:
                # Key is valid but account has no credits — store anyway with warning
                pass
            else:
                audit_rotation_attempt(
                    provider="nvidia",
                    status="failed",
                    old_fp=old_fp,
                    env_keys_updated=[],
                )
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

        # ── 4. Store candidate in Vaultwarden ──
        try:
            self.backend.set_secret(
                NVIDIA_API_REF,
                candidate_key,
                metadata={
                    "rotation_mode": "manual-new-key",
                    "last_rotated_at": str(int(time.time())),
                    "old_fingerprint": old_fp or "none",
                    "old_last4": old_l4 or "",
                },
            )
        except SecretWriteFailed as e:
            return {"ok": False, "error": f"Failed to store candidate: {e}"}

        # ── 5. Smoke test (before rendering env, so bad key never reaches .env.generated) ──
        passed, detail = self.smoke_test()
        if not passed:
            if backup:
                self.backend.restore_secret(NVIDIA_API_REF, backup)
            audit_rotation_attempt(
                provider="nvidia",
                status="smoke_test_failed",
                old_fp=old_fp,
                new_fp=new_fp,
                old_revoked=False,
                manual_action=True,
                env_keys_updated=[],
            )
            return {
                "ok": False,
                "error": f"Smoke test failed: {detail}",
                "smoke_test": detail,
            }

        # ── 6. Render env (only after smoke test passes) ──
        from env.render_env import render_env  # pyright: ignore[reportMissingImports]

        try:
            env_path = render_env(self.backend)
        except Exception as e:
            if backup:
                try:
                    self.backend.restore_secret(NVIDIA_API_REF, backup)
                    render_env(self.backend)  # re-render with rolled-back key
                except Exception as rollback_err:
                    return {
                        "ok": False,
                        "error": f"Env render failed: {e}",
                        "rollback_error": str(rollback_err),
                    }
            return {"ok": False, "error": f"Env render failed: {e}"}

        # ── 7. Audit ──
        audit_rotation_attempt(
            provider="nvidia",
            status="success",
            old_fp=old_fp,
            new_fp=new_fp,
            old_revoked=False,
            manual_action=True,
            env_keys_updated=["NVIDIA_API_KEY"],
        )

        return {
            "ok": True,
            "provider": "nvidia",
            "operation": "rotation",
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
            "new_last4": new_l4,
            "env_rendered": env_path,
            "smoke_test": detail,
            "warnings": [
                "Account has billing/credit issues — key stored but API calls may fail until resolved"
            ]
            if vr.reason_class == ValidationReason.QUOTA_OR_BILLING
            else [],
        }

    # ── Helpers ──────────────────────────────────────────────────

    def _read_key_stdin(self) -> str:
        """Read candidate key from stdin with prompt suppression."""
        if sys.stdin.isatty():
            import getpass

            return getpass.getpass("Paste new NVIDIA NIM API key: ")
        return sys.stdin.readline()
