"""Hermes Ops Kit — Anthropic Provider Rotator

Mode: partial-auto / admin-hybrid (spec section 19.2).

Flow:
  1. Check whether Anthropic Admin API is available (admin key in Vaultwarden).
  2. If auto: create candidate API key via Admin API.
  3. If manual: accept key via stdin.
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

from providers.base import BaseRotator  # pyright: ignore[reportMissingImports]
from security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]
from security.secret_backend import SecretWriteFailed  # pyright: ignore[reportMissingImports]

ANTHROPIC_API_KEY_REF = "hermes/anthropic/api_key"
ANTHROPIC_ADMIN_KEY_REF = "hermes/anthropic/admin_key"
ANTHROPIC_WORKSPACE_ID_REF = "hermes/anthropic/workspace_id"
ANTHROPIC_API_KEY_ID_REF = "hermes/anthropic/api_key_id"

SUPPORTED_MODES = ["validate-only", "manual-new-key", "deactivate-old", "admin-hybrid"]


class AnthropicRotator(BaseRotator):
    """Rotate Anthropic API keys using Admin API (hybrid mode)."""

    provider = "anthropic"

    def validate_new_key(self, key: str) -> bool:
        """Validate a candidate Anthropic API key."""
        try:
            import anthropic  # pyright: ignore[reportMissingImports]
        except Exception:
            return False

        try:
            client = anthropic.Anthropic(api_key=key, timeout=15)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=5,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return bool(response.content)
        except Exception:
            return False

    def smoke_test(self) -> tuple[bool, str]:
        """Smoke test the active Anthropic key."""
        secret = self.backend.get_secret(ANTHROPIC_API_KEY_REF)
        if not secret or not secret.value:
            return False, "No active API key"

        try:
            import anthropic  # pyright: ignore[reportMissingImports]
        except Exception:
            return False, "anthropic SDK not available"

        try:
            client = anthropic.Anthropic(api_key=secret.value, timeout=15)
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=5,
                messages=[{"role": "user", "content": "Smoke test"}],
            )
            return True, "smoke test passed"
        except Exception as e:
            return False, f"smoke test failed: {e}"

    def _has_admin_key(self) -> bool:
        admin = self.backend.get_secret(ANTHROPIC_ADMIN_KEY_REF)
        return admin is not None and bool(admin.value)

    def _create_api_key(self, admin_key: str, workspace_id: str) -> str | None:
        """Create a new API key via Anthropic Admin API.

        Returns the new key string or None.
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
        try:
            resp = requests.post(
                f"https://api.anthropic.com/v1/organizations/workspaces/{workspace_id}/api_keys",
                headers=headers,
                json={"name": f"hermes-key-{int(time.time())}"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return data.get("key") or data.get("secret")
        except Exception:
            pass
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

    def rotate(self, candidate_key: str | None = None) -> dict:
        """Execute Anthropic rotation flow."""
        from audit.audit_log import audit_rotation_attempt  # pyright: ignore[reportMissingImports]
        from docs.obsidian_sink import write_rotation_note  # pyright: ignore[reportMissingImports]

        old_secret = self.backend.get_secret(ANTHROPIC_API_KEY_REF)
        old_fp, _ = secret_fingerprint(old_secret.value) if old_secret else (None, None)

        # Determine mode
        mode = "manual-new-key"
        if self._has_admin_key():
            mode = "admin-hybrid"
            workspace = self.backend.get_secret(ANTHROPIC_WORKSPACE_ID_REF)
            admin = self.backend.get_secret(ANTHROPIC_ADMIN_KEY_REF)
            if workspace and admin:
                created = self._create_api_key(admin.value, workspace.value)
                if created:
                    candidate_key = created

        # Fallback: manual
        if not candidate_key:
            candidate_key = self._read_key_stdin()

        candidate_key = candidate_key.strip()
        if not self.validate_new_key(candidate_key):
            return {"ok": False, "error": "Candidate key validation failed"}

        new_fp, new_l4 = secret_fingerprint(candidate_key)

        # Store
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
            return {"ok": False, "error": f"Store failed: {e}"}

        # Render env
        from env.render_env import render_env  # pyright: ignore[reportMissingImports]

        try:
            env_path = render_env(self.backend)
        except Exception as e:
            return {"ok": False, "error": f"Env render failed: {e}"}

        # Smoke test
        passed, detail = self.smoke_test()
        if not passed:
            return {"ok": False, "error": f"Smoke test failed: {detail}"}

        # Archive old key if admin-hybrid
        old_archived = False
        if mode == "admin-hybrid":
            old_meta = self.backend.get_metadata(ANTHROPIC_API_KEY_REF)
            admin = self.backend.get_secret(ANTHROPIC_ADMIN_KEY_REF)
            if old_meta and old_meta.item_id and admin:
                old_archived = self._archive_old_key(admin.value, old_meta.item_id)

        # Audit
        audit_rotation_attempt(
            provider="anthropic",
            status="success",
            old_fp=old_fp,
            new_fp=new_fp,
            old_revoked=old_archived,
            manual_action=not old_archived,
            env_keys_updated=["ANTHROPIC_API_KEY"],
            obsidian_note_written=True,
        )

        write_rotation_note(
            provider="anthropic",
            status="success",
            mode=mode,
            old_key=old_secret.value if old_secret else None,
            new_key=candidate_key,
            smoke_test="passed",
            old_revoked="yes" if old_archived else "manual action required",
            dry_run=False,
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
