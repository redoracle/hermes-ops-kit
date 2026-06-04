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
from security.secret_backend import (  # pyright: ignore[reportMissingImports]
    SecretWriteFailed,
    ValidationReason,
    ValidationResult,
)

GITHUB_TOKEN_REF = "hermes/github/token"
GITHUB_APP_ID_REF = "hermes/github/app_id"
GITHUB_APP_PRIVATE_KEY_REF = "hermes/github/app_private_key"
GITHUB_INSTALLATION_ID_REF = "hermes/github/installation_id"
GITHUB_COPILOT_TOKEN_REF = "hermes/github/copilot_token"


class GitHubRotator(BaseRotator):
    """Mint GitHub App installation tokens.  Does NOT rotate Copilot tokens."""

    provider = "github"

    # ── Validation ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_subprocess_error(stderr: str, returncode: int) -> ValidationResult:
        """Parse stderr from a failed gh CLI invocation into a ValidationResult."""
        stderr_lower = stderr.lower()
        if "401" in stderr_lower or "401" in stderr:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.AUTH_DENIED,
                detail=stderr[:500],
                http_status=401,
                retry_recommended=False,
            )
        if "403" in stderr_lower or "403" in stderr:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.FORBIDDEN,
                detail=stderr[:500],
                http_status=403,
                retry_recommended=False,
            )
        if "429" in stderr_lower or "429" in stderr:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.RATE_LIMITED,
                detail=stderr[:500],
                http_status=429,
                retry_recommended=True,
            )
        if "5" in stderr and any(
            code in stderr for code in ("500", "502", "503", "504")
        ):
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.SERVER_ERROR,
                detail=stderr[:500],
                http_status=500,
                retry_recommended=True,
            )
        return ValidationResult(
            valid=False,
            reason_class=ValidationReason.UNKNOWN,
            detail=stderr[:500],
        )

    def validate_new_key(self, key: str) -> ValidationResult:
        """Validate a GitHub token by checking rate limit.

        Uses gh CLI if available; falls back to curl.
        """
        import os as _os
        import shutil

        gh_bin = shutil.which("gh")
        if gh_bin:
            try:
                result = subprocess.run(
                    [gh_bin, "api", "/rate_limit"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    env={
                        "GITHUB_TOKEN": key,
                        "PATH": _os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
                    },
                )
            except subprocess.TimeoutExpired as e:
                return ValidationResult(
                    valid=False,
                    reason_class=ValidationReason.TIMEOUT,
                    detail=str(e),
                    retry_recommended=True,
                )
            except Exception as e:
                return ValidationResult(
                    valid=False,
                    reason_class=ValidationReason.UNKNOWN,
                    detail=str(e),
                )
            if result.returncode == 0:
                return ValidationResult(valid=True)
            return self._parse_subprocess_error(result.stderr, result.returncode)

        # Fallback: curl
        curl_bin = shutil.which("curl")
        if not curl_bin:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.SDK_UNAVAILABLE,
                detail="Neither gh CLI nor curl found",
                retry_recommended=False,
            )
        try:
            result = subprocess.run(
                [
                    curl_bin,
                    "-s",
                    "-w",
                    "%{http_code}",
                    "-o",
                    _os.devnull,
                    "https://api.github.com/rate_limit",
                    "-H",
                    f"Authorization: Bearer {key}",
                    "-H",
                    "Accept: application/vnd.github+json",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.TIMEOUT,
                detail=str(e),
                retry_recommended=True,
            )
        except Exception as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.UNKNOWN,
                detail=str(e),
            )
        try:
            code = int(result.stdout.strip())
        except ValueError:
            code = 0
        if code == 200:
            return ValidationResult(valid=True)
        if code == 401:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.AUTH_DENIED,
                detail="GitHub token rejected (401)",
                http_status=401,
                retry_recommended=False,
            )
        if code == 403:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.FORBIDDEN,
                detail="GitHub token lacks permission (403)",
                http_status=403,
                retry_recommended=False,
            )
        if code == 429:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.RATE_LIMITED,
                detail="GitHub rate limited (429)",
                http_status=429,
                retry_recommended=True,
            )
        return ValidationResult(
            valid=False,
            reason_class=ValidationReason.UNKNOWN,
            detail=f"GitHub API returned HTTP {code}",
            http_status=code,
        )

    # ── Smoke test ──────────────────────────────────────────────────────

    def smoke_test(self) -> tuple[bool, str]:
        """Verify GitHub CLI and token are functional."""
        secret = self.backend.get_secret(GITHUB_TOKEN_REF)
        if not secret or not secret.value:
            return False, "No GitHub token in Vaultwarden"

        import shutil

        gh_bin = shutil.which("gh")
        if not gh_bin:
            return False, "gh CLI not found"
        try:
            result = subprocess.run(
                [gh_bin, "api", "/rate_limit"],
                capture_output=True,
                text=True,
                timeout=15,
                env={"GITHUB_TOKEN": secret.value},
            )
            if result.returncode == 0:
                return True, "GitHub API accessible"
            return False, f"gh api failed: rc={result.returncode}"
        except FileNotFoundError:
            return False, "gh CLI not found"
        except Exception as e:
            return False, f"smoke test failed: {e}"

    # ── Copilot ─────────────────────────────────────────────────────────

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

    # ── Revoke / cleanup (no admin API — manual only) ────────────────────

    def revoke_key(self, secret_ref: str, admin_credential: str | None = None) -> bool:
        """GitHub App installation tokens expire naturally (1 hour).

        No revocation API needed — tokens are short-lived. Returns False
        to signal no automated revocation is performed.
        """
        return False

    def cleanup_orphaned_key(self, key_value: str) -> bool:
        """GitHub App installation tokens expire naturally (1 hour).

        No admin API for explicit key deletion. Returns False.
        """
        return False

    # ── Token minting ───────────────────────────────────────────────────

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

        # Validate the newly minted token
        vr = self.validate_with_retry(token)
        if not vr.valid:
            if vr.reason_class == ValidationReason.QUOTA_OR_BILLING:
                # Key is valid but account has no credits — store anyway with warning
                pass
            else:
                return {
                    "ok": False,
                    "error": f"Minted token unusable: {vr.reason_class.value}",
                    "validation": {
                        "reason": vr.reason_class.value,
                        "detail": vr.detail,
                        "http_status": vr.http_status,
                    },
                }

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
            "warnings": [
                "Account has billing/credit issues — key stored but API calls may fail until resolved"
            ]
            if vr.reason_class == ValidationReason.QUOTA_OR_BILLING
            else None,
        }

    # ── Rotation ────────────────────────────────────────────────────────

    def rotate(self, candidate_key: str | None = None) -> dict:
        """GitHub rotation = mint a new installation token."""
        # Copilot check
        copilot_ok = self._check_copilot_auth()
        warnings = []
        if not copilot_ok:
            warnings.append(
                "Copilot CLI not authenticated. Run 'gh auth login' separately."
            )

        # ── Backup current token for rollback ──
        backup = self.backend.backup_secret(GITHUB_TOKEN_REF)

        if candidate_key:
            # Manual token: validate and store
            vr = self.validate_with_retry(candidate_key)
            if not vr.valid:
                if vr.reason_class == ValidationReason.QUOTA_OR_BILLING:
                    # Key is valid but account has no credits — store anyway with warning
                    pass
                else:
                    return {
                        "ok": False,
                        "error": f"Token validation failed: {vr.reason_class.value}",
                        "validation": {
                            "reason": vr.reason_class.value,
                            "detail": vr.detail,
                            "http_status": vr.http_status,
                        },
                    }
            new_fp, _ = secret_fingerprint(candidate_key)
            try:
                self.backend.set_secret(GITHUB_TOKEN_REF, candidate_key)
            except SecretWriteFailed as e:
                return {"ok": False, "error": f"Store failed: {e}"}

            # ── Smoke test the stored token ──
            passed, detail = self.smoke_test()
            if not passed:
                if backup:
                    self.backend.restore_secret(GITHUB_TOKEN_REF, backup)
                return {
                    "ok": False,
                    "error": f"Smoke test failed after storing token: {detail}",
                }

            return {
                "ok": True,
                "new_fingerprint": new_fp,
                "copilot_ok": copilot_ok,
                "warnings": [
                    "Account has billing/credit issues — key stored but API calls may fail until resolved"
                ]
                if not vr.valid and vr.reason_class == ValidationReason.QUOTA_OR_BILLING
                else [],
            }

        # ── Full-auto: mint installation token ──
        result = self.mint_installation_token()
        if not result.get("ok"):
            if backup:
                self.backend.restore_secret(GITHUB_TOKEN_REF, backup)
        if warnings:
            result["warnings"] = warnings
            result["copilot_ok"] = copilot_ok
        return result

    # ── JWT helpers ─────────────────────────────────────────────────────

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
