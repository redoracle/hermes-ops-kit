"""Hermes Ops Kit — DeepSeek Provider Rotator

Mode: manual-new-key + validation (spec section 19.5).

Flow:
  1. Accept new API key via stdin (not CLI args — avoids shell history leak).
  2. Validate key against DeepSeek /models and a minimal chat request.
  3. Store candidate key in Vaultwarden.
  4. Render temporary env and smoke-test.
  5. Activate env if smoke passes.
  6. Mark old key for manual revocation in provider console.
  7. Write sanitized audit + Obsidian note.
"""

from __future__ import annotations

import os
import sys
import time

from providers.base import BaseRotator  # pyright: ignore[reportMissingImports]
from security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]
from security.secret_backend import SecretWriteFailed  # pyright: ignore[reportMissingImports]

DEEPSEEK_API_REF = "hermes/deepseek/api_key"
DEEPSEEK_BASE_URL_REF = "hermes/deepseek/base_url"


class DeepSeekRotator(BaseRotator):
    """Rotate DeepSeek API keys.

    DeepSeek is OpenAI-compatible, so validation uses the openai SDK
    pointed at https://api.deepseek.com.
    """

    provider = "deepseek"

    def validate_new_key(self, key: str) -> bool:
        """Validate a candidate key against the DeepSeek API.

        Two checks: GET /models (needs valid auth) and a minimal chat request.
        """
        try:
            import openai  # pyright: ignore[reportMissingImports]
        except Exception:
            return False

        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

        try:
            client = openai.OpenAI(api_key=key, base_url=base_url, timeout=15)

            # 1. List models
            models = client.models.list()
            if not models.data:
                return False

            # 2. Minimal chat
            chat = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            return bool(chat.choices)

        except Exception:
            return False

    def smoke_test(self) -> tuple[bool, str]:
        """Run a smoke test against the currently active DeepSeek key.

        Returns (passed, detail_string).
        """
        secret = self.backend.get_secret(DEEPSEEK_API_REF)
        if not secret or not secret.value:
            return False, "No active API key found in Vaultwarden"

        try:
            import openai  # pyright: ignore[reportMissingImports]
        except Exception:
            return False, "openai SDK not available"

        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

        try:
            client = openai.OpenAI(api_key=secret.value, base_url=base_url, timeout=15)
            models = client.models.list()
            chat = client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": "Smoke test"}],
                max_tokens=5,
            )
            if models.data and chat.choices:
                return True, "smoke test passed: /models + chat OK"
            return False, "smoke test failed: empty response"
        except Exception as e:
            return False, f"smoke test failed: {e}"

    def rotate(self, candidate_key: str | None = None) -> dict:
        """Execute the DeepSeek rotation flow.

        If candidate_key is None, reads from stdin (prompt-based).
        """
        from audit.audit_log import audit_rotation_attempt  # pyright: ignore[reportMissingImports]
        from docs.obsidian_sink import write_rotation_note  # pyright: ignore[reportMissingImports]

        # ── 1. Acquire candidate key ──
        if not candidate_key:
            candidate_key = self._read_key_stdin()

        candidate_key = candidate_key.strip()
        if not candidate_key:
            return {"ok": False, "error": "No candidate key provided"}

        # ── 2. Get current key fingerprint ──
        old_secret = self.backend.get_secret(DEEPSEEK_API_REF)
        old_fp, old_l4 = (
            secret_fingerprint(old_secret.value) if old_secret else (None, None)
        )

        # ── 3. Validate candidate ──
        if not self.validate_new_key(candidate_key):
            audit_rotation_attempt(
                provider="deepseek",
                status="failed",
                old_fp=old_fp,
                env_keys_updated=[],
                obsidian_note_written=False,
            )
            return {"ok": False, "error": "Candidate key validation failed"}

        new_fp, new_l4 = secret_fingerprint(candidate_key)

        # ── 4. Store candidate in Vaultwarden ──
        try:
            self.backend.set_secret(
                DEEPSEEK_API_REF,
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

        # ── 5. Render env ──
        from env.render_env import render_env  # pyright: ignore[reportMissingImports]

        try:
            env_path = render_env(self.backend)
        except Exception as e:
            return {"ok": False, "error": f"Env render failed: {e}"}

        # ── 6. Smoke test with new key ──
        passed, detail = self.smoke_test()
        if not passed:
            audit_rotation_attempt(
                provider="deepseek",
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
                "env_rendered": env_path,
                "smoke_test": detail,
            }

        # ── 7. Audit ──
        audit_rotation_attempt(
            provider="deepseek",
            status="success",
            old_fp=old_fp,
            new_fp=new_fp,
            old_revoked=False,
            manual_action=True,
            env_keys_updated=["DEEPSEEK_API_KEY"],
            obsidian_note_written=True,
        )

        # ── 8. Obsidian note ──
        write_rotation_note(
            provider="deepseek",
            status="success",
            mode="manual-new-key",
            old_key=old_secret.value if old_secret else None,
            new_key=candidate_key,
            smoke_test="passed",
            old_revoked="manual action required",
            dry_run=False,
        )

        return {
            "ok": True,
            "provider": "deepseek",
            "operation": "rotation",
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
            "new_last4": new_l4,
            "env_rendered": env_path,
            "smoke_test": detail,
            "manual_action_required": True,
            "manual_action": "Revoke old key in DeepSeek console",
            "obsidian_note": "written",
        }

    # ── Helpers ──────────────────────────────────────────────────

    def _read_key_stdin(self) -> str:
        """Read candidate key from stdin with prompt suppression."""
        if sys.stdin.isatty():
            import getpass

            return getpass.getpass("Paste new DeepSeek API key: ")
        return sys.stdin.readline()
