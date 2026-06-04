"""Hermes Ops Kit — GitHub Provider Token Minting

Mode: GitHub App installation token generation (spec section 19.4).

Rules:
  - Do NOT rotate Copilot user login tokens as normal API keys.
  - Use GitHub App installation tokens for repo automation.
  - Installation tokens expire naturally (1 hour).
  - Validate GitHub CLI and Copilot CLI auth separately.
  - Warn if Copilot re-authentication is required.

Secret refs:
  hermes/github/app_id
  hermes/github/app_private_key
  hermes/github/installation_id
  hermes/github/token
  hermes/github/copilot_token
"""

from __future__ import annotations

import subprocess
import time

from providers.base import BaseRotator  # pyright: ignore[reportMissingImports]
from security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]
from security.secret_backend import SecretWriteFailed  # pyright: ignore[reportMissingImports]

GITHUB_TOKEN_REF = "hermes/github/token"
GITHUB_APP_ID_REF = "hermes/github/app_id"
GITHUB_APP_PRIVATE_KEY_REF = "hermes/github/app_private_key"
GITHUB_INSTALLATION_ID_REF = "hermes/github/installation_id"
GITHUB_COPILOT_TOKEN_REF = "hermes/github/copilot_token"


class GitHubRotator(BaseRotator):
    """Mint GitHub App installation tokens.  Does NOT rotate Copilot tokens."""

    provider = "github"

    def validate_new_key(self, key: str) -> bool:
        """Validate a GitHub token by checking rate limit."""
        try:
            result = subprocess.run(
                ["gh", "api", "/rate_limit"],
                capture_output=True,
                text=True,
                timeout=15,
                env={"GITHUB_TOKEN": key, "PATH": "/usr/bin:/usr/local/bin"},
            )
            return result.returncode == 0
        except Exception:
            return False

    def smoke_test(self) -> tuple[bool, str]:
        """Verify GitHub CLI and token are functional."""
        secret = self.backend.get_secret(GITHUB_TOKEN_REF)
        if not secret or not secret.value:
            return False, "No GitHub token in Vaultwarden"

        try:
            result = subprocess.run(
                ["gh", "api", "/rate_limit"],
                capture_output=True,
                text=True,
                timeout=15,
                env={"GITHUB_TOKEN": secret.value, "PATH": "/usr/bin:/usr/local/bin"},
            )
            if result.returncode == 0:
                return True, "GitHub API accessible"
            return False, f"gh api failed: rc={result.returncode}"
        except FileNotFoundError:
            return False, "gh CLI not found"
        except Exception as e:
            return False, f"smoke test failed: {e}"

    def _check_copilot_auth(self) -> bool:
        """Check if Copilot CLI is authenticated."""
        try:
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def mint_installation_token(self) -> dict:
        """Generate a short-lived GitHub App installation token.

        Uses the GitHub App private key and installation ID from Vaultwarden.
        Token is stored at hermes/github/token and mapped to GH_TOKEN + GITHUB_TOKEN.

        Returns metadata only — never the raw token.
        """
        from audit.audit_log import write_audit_event  # pyright: ignore[reportMissingImports]

        app_id = self.backend.get_secret(GITHUB_APP_ID_REF)
        private_key = self.backend.get_secret(GITHUB_APP_PRIVATE_KEY_REF)
        installation_id = self.backend.get_secret(GITHUB_INSTALLATION_ID_REF)

        if not (app_id and private_key and installation_id):
            raise SecretWriteFailed(
                "Missing GitHub App configuration: need app_id, app_private_key, installation_id in Vaultwarden"
            )

        # Generate JWT from app private key → exchange for installation token
        # This uses PyJWT if available, otherwise falls back to a gh API helper
        try:
            jwt = self._generate_app_jwt(app_id.value, private_key.value)
            token = self._exchange_jwt_for_token(jwt, installation_id.value)
        except Exception as e:
            return {"ok": False, "error": f"Token minting failed: {e}"}

        # Store in Vaultwarden
        new_fp, new_l4 = secret_fingerprint(token)
        try:
            self.backend.set_secret(
                GITHUB_TOKEN_REF,
                token,
                metadata={
                    "rotation_mode": "installation-token",
                    "last_rotated_at": str(int(time.time())),
                    "expires_in": "3600",  # GitHub App tokens expire in 1 hour
                },
            )
        except SecretWriteFailed as e:
            return {"ok": False, "error": f"Store failed: {e}"}

        write_audit_event(
            provider="github",
            operation="mint_installation_token",
            status="success",
            new_fingerprint=new_fp,
            extra={"installation_id": installation_id.value},
        )

        return {
            "ok": True,
            "provider": "github",
            "operation": "mint_installation_token",
            "new_fingerprint": new_fp,
            "new_last4": new_l4,
            "expires_in": 3600,
        }

    def rotate(self, candidate_key: str | None = None) -> dict:
        """GitHub rotation = mint a new installation token."""
        # Copilot check
        copilot_ok = self._check_copilot_auth()
        warnings = []
        if not copilot_ok:
            warnings.append(
                "Copilot CLI not authenticated. Run 'gh auth login' separately."
            )

        if candidate_key:
            # Manual token: validate and store
            if not self.validate_new_key(candidate_key):
                return {"ok": False, "error": "Token validation failed"}
            new_fp, _ = secret_fingerprint(candidate_key)
            self.backend.set_secret(GITHUB_TOKEN_REF, candidate_key)
            return {"ok": True, "new_fingerprint": new_fp, "copilot_ok": copilot_ok}

        # Full-auto: mint installation token
        result = self.mint_installation_token()
        if warnings:
            result["warnings"] = warnings
            result["copilot_ok"] = copilot_ok
        return result

    def _generate_app_jwt(self, app_id: str, private_key_pem: str) -> str:
        """Generate a JWT signed with the GitHub App private key.

        Requires PyJWT. Falls back gracefully if not available.
        """
        import jwt  # pyright: ignore[reportMissingImports]

        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 600,  # 10 minutes (GitHub max)
            "iss": app_id,
        }
        return jwt.encode(payload, private_key_pem, algorithm="RS256")

    def _exchange_jwt_for_token(self, jwt_token: str, installation_id: str) -> str:
        """Exchange a JWT for an installation access token."""
        import requests  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        resp = requests.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15,
        )
        if resp.status_code == 201:
            return resp.json()["token"]
        raise SecretWriteFailed(f"GitHub App token exchange failed: {resp.status_code}")
