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

from providers.base import BaseRotator  # pyright: ignore[reportMissingImports]
from security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]
from security.secret_backend import SecretWriteFailed  # pyright: ignore[reportMissingImports]

OPENAI_API_KEY_REF = "hermes/openai/api_key"
OPENAI_ADMIN_KEY_REF = "hermes/openai/admin_key"
OPENAI_PROJECT_ID_REF = "hermes/openai/project_id"


class OpenAIRotator(BaseRotator):
    """Rotate OpenAI API keys using project service accounts."""

    provider = "openai"

    def validate_new_key(self, key: str) -> bool:
        """Validate a candidate key against OpenAI API."""
        try:
            import openai  # pyright: ignore[reportMissingImports]
        except Exception:
            return False

        try:
            client = openai.OpenAI(api_key=key, timeout=15)
            models = client.models.list()
            return len(models.data) > 0
        except Exception:
            return False

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

    def _has_admin_key(self) -> bool:
        """Check if an admin key is available for full-auto rotation."""
        admin = self.backend.get_secret(OPENAI_ADMIN_KEY_REF)
        return admin is not None and bool(admin.value)

    def _create_service_account_key(
        self, admin_key: str, project_id: str
    ) -> str | None:
        """Create a new project API key via the OpenAI admin API.

        This is a best-effort operation — the OpenAI Admin API for key
        management may require specific endpoints/scopes.
        """
        try:
            import requests  # pyright: ignore[reportMissingImports,reportMissingModuleSource]
        except Exception:
            return None

        url = f"https://api.openai.com/v1/organization/projects/{project_id}/api_keys"
        headers = {
            "Authorization": f"Bearer {admin_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                url,
                headers=headers,
                json={"name": f"hermes-key-{int(time.time())}"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return data.get("secret") or data.get("key", {}).get("secret")
        except Exception:
            pass
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

    def rotate(self, candidate_key: str | None = None) -> dict:
        """Execute OpenAI rotation flow."""
        from audit.audit_log import audit_rotation_attempt  # pyright: ignore[reportMissingImports]
        from docs.obsidian_sink import write_rotation_note  # pyright: ignore[reportMissingImports]

        old_secret = self.backend.get_secret(OPENAI_API_KEY_REF)
        old_fp, _ = secret_fingerprint(old_secret.value) if old_secret else (None, None)

        # ── Full-auto path: use admin key ──
        if self._has_admin_key() and not candidate_key:
            admin = self.backend.get_secret(OPENAI_ADMIN_KEY_REF)
            project = self.backend.get_secret(OPENAI_PROJECT_ID_REF)
            if admin and project:
                candidate_key = self._create_service_account_key(
                    admin.value, project.value
                )

        # ── Fallback: manual key ──
        if not candidate_key:
            candidate_key = self._read_key_stdin()

        candidate_key = candidate_key.strip()
        if not self.validate_new_key(candidate_key):
            return {"ok": False, "error": "Candidate key validation failed"}

        new_fp, new_l4 = secret_fingerprint(candidate_key)

        # Store
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

        # Revoke old key if full-auto
        old_revoked = False
        if self._has_admin_key() and old_secret:
            old_meta = self.backend.get_metadata(OPENAI_API_KEY_REF)
            if old_meta and old_meta.item_id:
                admin = self.backend.get_secret(OPENAI_ADMIN_KEY_REF)
                if admin:
                    old_revoked = self._delete_old_key(admin.value, old_meta.item_id)

        # Audit
        audit_rotation_attempt(
            provider="openai",
            status="success",
            old_fp=old_fp,
            new_fp=new_fp,
            old_revoked=old_revoked,
            manual_action=not old_revoked,
            env_keys_updated=["OPENAI_API_KEY"],
            obsidian_note_written=True,
        )

        # Obsidian
        write_rotation_note(
            provider="openai",
            status="success",
            mode="full-auto" if self._has_admin_key() else "manual",
            old_key=old_secret.value if old_secret else None,
            new_key=candidate_key,
            smoke_test="passed",
            old_revoked="yes" if old_revoked else "manual action required",
            dry_run=False,
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
        }

    def _read_key_stdin(self) -> str:
        if sys.stdin.isatty():
            import getpass

            return getpass.getpass("Paste new OpenAI API key: ")
        return sys.stdin.readline()
