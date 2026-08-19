"""
Hermes Ops Kit — Vaultwarden / Bitwarden Secret Backend

Implements the SecretBackend protocol using the Bitwarden CLI client.
This is the canonical Hermes secret store — NOT mcp-vault, NOT Obsidian.

Authentication modes (spec section 7):
  - bitwarden_cli_password   — user/password from ~/.hermes/.env
  - bitwarden_cli_api_key    — BW_CLIENTID + BW_CLIENTSECRET
  - bitwarden_cli_session    — existing BW_SESSION

Item naming convention (spec section 12):
  Hermes/<Provider>/<KEY_NAME>    e.g. Hermes/OpenAI/API_KEY

Transport security (spec section 8):
  - https:// required (allow_insecure_http must be explicitly True)
  - TLS verification required (allow_insecure_tls must be explicitly True)
  - bw serve is restricted to 127.0.0.1
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from ..security.bitwarden_cli_client import (  # pyright: ignore[reportMissingImports]
    BitwardenCLIClient,
    BitwardenCLIAuthError,
    BitwardenCLIError,
)
from ..security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]
from ..security.redaction import redact  # pyright: ignore[reportMissingImports]
from ..security.secret_backend import (  # pyright: ignore[reportMissingImports]
    InsecureSecretBackendError,
    SecretBackend,
    SecretMetadata,
    SecretValue,
    SecretWriteFailed,
    VaultwardenAuthError,
    VaultwardenUnavailable,
    VaultwardenUnlockError,
)


# ─── Item Name Mapping ──────────────────────────────────────────────────

# These are the default Vaultwarden item name conventions.
# Override by setting the HERMES_ITEM_PREFIX environment variable or by
# providing a custom mapping at runtime. See docs/hermes-compatibility.md
# for configuration options.
#
# Prefix for all Hermes items in Vaultwarden
HERMES_ITEM_PREFIX = "Hermes"

# Supported internal ref → Vaultwarden item name mapping
INTERNAL_REF_MAP: dict[str, str] = {
    # OpenAI
    "hermes/openai/api_key": f"{HERMES_ITEM_PREFIX}/OpenAI/API_KEY",
    "hermes/openai/admin_key": f"{HERMES_ITEM_PREFIX}/OpenAI/ADMIN_KEY",
    "hermes/openai/project_id": f"{HERMES_ITEM_PREFIX}/OpenAI/PROJECT_ID",
    "hermes/openai/service_account_id": f"{HERMES_ITEM_PREFIX}/OpenAI/SERVICE_ACCOUNT_ID",
    # Anthropic
    "hermes/anthropic/api_key": f"{HERMES_ITEM_PREFIX}/Anthropic/API_KEY",
    "hermes/anthropic/admin_key": f"{HERMES_ITEM_PREFIX}/Anthropic/ADMIN_KEY",
    "hermes/anthropic/workspace_id": f"{HERMES_ITEM_PREFIX}/Anthropic/WORKSPACE_ID",
    "hermes/anthropic/api_key_id": f"{HERMES_ITEM_PREFIX}/Anthropic/API_KEY_ID",
    # Google / Gemini
    "hermes/google/gemini_api_key": f"{HERMES_ITEM_PREFIX}/Google/GEMINI_API_KEY",
    "hermes/google/api_key_id": f"{HERMES_ITEM_PREFIX}/Google/API_KEY_ID",
    "hermes/google/project_id": f"{HERMES_ITEM_PREFIX}/Google/PROJECT_ID",
    "hermes/google/project_number": f"{HERMES_ITEM_PREFIX}/Google/PROJECT_NUMBER",
    "hermes/google/application_credentials_json": f"{HERMES_ITEM_PREFIX}/Google/APPLICATION_CREDENTIALS_JSON",
    # GitHub
    "hermes/github/app_id": f"{HERMES_ITEM_PREFIX}/GitHub/APP_ID",
    "hermes/github/app_private_key": f"{HERMES_ITEM_PREFIX}/GitHub/APP_PRIVATE_KEY",
    "hermes/github/installation_id": f"{HERMES_ITEM_PREFIX}/GitHub/INSTALLATION_ID",
    "hermes/github/token": f"{HERMES_ITEM_PREFIX}/GitHub/TOKEN",
    "hermes/github/copilot_token": f"{HERMES_ITEM_PREFIX}/GitHub/COPILOT_TOKEN",
    # DeepSeek
    "hermes/deepseek/api_key": f"{HERMES_ITEM_PREFIX}/DeepSeek/API_KEY",
    "hermes/deepseek/base_url": f"{HERMES_ITEM_PREFIX}/DeepSeek/BASE_URL",
    "hermes/deepseek/anthropic_base_url": f"{HERMES_ITEM_PREFIX}/DeepSeek/ANTHROPIC_BASE_URL",
    "hermes/deepseek/default_model": f"{HERMES_ITEM_PREFIX}/DeepSeek/DEFAULT_MODEL",
    "hermes/deepseek/reasoning_model": f"{HERMES_ITEM_PREFIX}/DeepSeek/REASONING_MODEL",
    # NVIDIA NIM
    "hermes/nvidia/api_key": f"{HERMES_ITEM_PREFIX}/NVIDIA/API_KEY",
    "hermes/nvidia/base_url": f"{HERMES_ITEM_PREFIX}/NVIDIA/BASE_URL",
    # Assistants — Assistant
    "hermes/assistants/assistant-id/api_base": f"{HERMES_ITEM_PREFIX}/Assistants/Assistant/API_BASE",
    "hermes/assistants/assistant-id/api_key": f"{HERMES_ITEM_PREFIX}/Assistants/Assistant/API_KEY",
    "hermes/assistants/assistant-id/model": f"{HERMES_ITEM_PREFIX}/Assistants/Assistant/MODEL",
    "hermes/assistants/assistant-id/timeout_seconds": f"{HERMES_ITEM_PREFIX}/Assistants/Assistant/TIMEOUT_SECONDS",
}

# Reverse map: Vaultwarden item name → internal ref
ITEM_NAME_TO_REF: dict[str, str] = {v: k for k, v in INTERNAL_REF_MAP.items()}


def internal_ref_to_item_name(ref: str) -> str:
    """Map an internal ref to its Vaultwarden item name."""
    return INTERNAL_REF_MAP.get(ref, f"{HERMES_ITEM_PREFIX}/{ref}")


def item_name_to_internal_ref(name: str) -> str | None:
    """Map a Vaultwarden item name back to an internal ref."""
    return ITEM_NAME_TO_REF.get(name)


def _classify_ref(name: str) -> str:
    """Derive the secret class (admin/config/runtime) from the internal ref path.

    Admin refs: refs ending in 'admin_key', 'admin_secret', 'service_account_json'.
    Config refs: project_id, workspace_id, base_url, model names, etc.
    Everything else: runtime.
    """
    parts = name.split("/")
    if len(parts) < 3:
        return "runtime"
    key_name = parts[-1]
    if key_name in (
        "admin_key",
        "admin_secret",
        "admin_token",
        "service_account_json",
    ):
        return "admin"
    if key_name in (
        "project_id",
        "project_number",
        "workspace_id",
        "app_id",
        "installation_id",
        "base_url",
        "anthropic_base_url",
        "default_model",
        "reasoning_model",
        "timeout_seconds",
        "api_key_id",
    ):
        return "config"
    return "runtime"


def _renderable_to_env(name: str, secret_class: str | None = None) -> bool:
    """Determine whether a secret ref is safe to render into .env.generated.

    Admin-class secrets are NEVER renderable.
    Runtime and config secrets are renderable by default.
    """
    sc = secret_class or _classify_ref(name)
    return sc != "admin"


# ─── Backend Implementation ─────────────────────────────────────────────


class VaultwardenSecretBackend(SecretBackend):
    """Vaultwarden-backed secret store.

    Uses the Bitwarden CLI (`bw`) to communicate with a self-hosted
    Vaultwarden instance at `<vaultwarden-url>/`.
    """

    def __init__(
        self,
        server_url: str,
        mode: str = "bitwarden_cli_password",
        appdata_dir: str | None = None,
        user: str | None = None,
        password: str | None = None,
        bw_client_id: str | None = None,
        bw_client_secret: str | None = None,
        bw_password: str | None = None,
        bw_session: str | None = None,
        timeout_seconds: int = 15,
        verify_tls: bool = True,
        allow_insecure_http: bool = False,
    ) -> None:
        # ── Transport validation ──
        if not server_url.startswith("https://") and not allow_insecure_http:
            raise InsecureSecretBackendError(
                f"Secret backend URL must use https://, got {server_url}. "
                "Set allow_insecure_http=True only for local development."
            )
        if not verify_tls:
            raise InsecureSecretBackendError(
                "TLS verification must be enabled for the secret backend."
            )

        self.server_url = server_url
        self.mode = mode
        self.user = user
        self.password = password
        self.bw_client_id = bw_client_id
        self.bw_client_secret = bw_client_secret
        self.bw_password = bw_password
        self.bw_session = bw_session
        self.verify_tls = verify_tls
        self.allow_insecure_http = allow_insecure_http

        _bw_timeout_raw = os.environ.get("BW_TIMEOUT")
        if _bw_timeout_raw is not None:
            try:
                bw_timeout = int(_bw_timeout_raw)
            except ValueError:
                raise ValueError(
                    f"BW_TIMEOUT must be an integer, got {_bw_timeout_raw!r}"
                )
        else:
            bw_timeout = timeout_seconds
        self._client = BitwardenCLIClient(
            bw_bin="bw",
            server_url=server_url,
            appdata_dir=appdata_dir or os.environ.get("BITWARDENCLI_APPDATA_DIR"),
            timeout_seconds=bw_timeout,
        )
        self._session: str | None = bw_session
        self._authenticated: bool = False
        self._unlocked: bool = False
        self._items_cache: list[dict[str, Any]] | None = None

    # ── Healthcheck ───────────────────────────────────────────────

    def healthcheck(self) -> dict[str, Any]:
        """Return comprehensive backend health status."""
        result: dict[str, Any] = {
            "backend": "vaultwarden",
            "server_url": self.server_url,
            "mode": self.mode,
            "tls_configured": self.server_url.startswith("https://"),
            "tls_verification": self.verify_tls,
            "timestamp": int(time.time()),
        }

        try:
            # If BW_SESSION is in env, use session mode; otherwise check status
            if self.mode == "bitwarden_cli_session" and self._session:
                self._client.login_session(self._session)
        except Exception:
            pass

        try:
            status = self._client.status()
            result["server_status"] = status.get("status", "unknown")
            result["server_email"] = status.get("userEmail", "unknown")
            result["server_ok"] = True
        except BitwardenCLIError as e:
            result["server_ok"] = False
            result["server_error"] = redact(str(e))

        result["authenticated"] = self._client.is_authenticated()
        result["unlocked"] = self._client.is_unlocked()

        result["ok"] = bool(
            result.get("server_ok")
            and result.get("authenticated")
            and result.get("unlocked")
        )
        return result

    # ── Auth / Unlock / Lock ──────────────────────────────────────

    def authenticate(self) -> str | None:
        """Log into Vaultwarden based on the configured auth mode.

        Returns BW_SESSION if a session was obtained.
        """
        if self._authenticated and self._session:
            return self._session

        try:
            if self.mode == "bitwarden_cli_session" and self.bw_session:
                self._client.login_session(self.bw_session)
                self._session = self.bw_session
            elif self.mode == "bitwarden_cli_api_key":
                if not self.bw_client_id or not self.bw_client_secret:
                    raise VaultwardenAuthError(
                        "bitwarden_cli_api_key mode requires BW_CLIENTID and BW_CLIENTSECRET"
                    )
                result = self._client.login_apikey(
                    self.bw_client_id, self.bw_client_secret
                )
                self._session = result.get("session")
            elif self.mode == "bitwarden_cli_password":
                if not self.user or not self.password:
                    raise VaultwardenAuthError(
                        "bitwarden_cli_password mode requires VAULTWARDEN_USER and VAULTWARDEN_PASSWORD"
                    )
                self._client.config_server()
                if self._client.is_authenticated():
                    self._session = None  # already logged in; will unlock later
                else:
                    result = self._client.login_password(self.user, self.password)
                    self._session = result.get("session")
            else:
                raise VaultwardenAuthError(f"unknown auth mode: {self.mode}")

            self._authenticated = True
            return self._session
        except BitwardenCLIAuthError as e:
            raise VaultwardenAuthError(redact(str(e))) from e
        except BitwardenCLIError as e:
            raise VaultwardenUnavailable(redact(str(e))) from e

    def unlock(self) -> str:
        """Unlock the vault. Returns the BW_SESSION.

        Caches the unlock state so bulk operations (like render-env)
        don't call `bw status` for every secret.
        """
        if self._unlocked and self._session:
            return self._session  # pyright: ignore[reportReturnType]

        if self._session and self._client.is_unlocked():
            self._unlocked = True
            return self._session  # pyright: ignore[reportReturnType]

        try:
            self._session = self._client.unlock(self.password)
            self._unlocked = True
            return self._session  # pyright: ignore[reportReturnType]
        except BitwardenCLIAuthError as e:
            raise VaultwardenUnlockError(redact(str(e))) from e

    def lock(self) -> None:
        """Lock the vault and clear the session."""
        try:
            self._client.lock()
        except BitwardenCLIError:
            pass
        self._session = None
        self._unlocked = False

    def sync(self) -> None:
        """Sync vault data from the server."""
        try:
            self._client.sync()
            self._invalidate_cache()  # items may have changed
        except BitwardenCLIError as e:
            raise VaultwardenUnavailable(redact(str(e))) from e

    # ── Secret CRUD ───────────────────────────────────────────────

    def _find_item_by_name(self, item_name: str) -> dict[str, Any] | None:
        """Find a vault item by its name field.

        Caches the full item list so render-env (which fetches 20+ secrets
        in sequence) makes one `bw list items` call instead of 20.
        The cache is invalidated on any write (set/delete/backup/restore).
        """
        if self._items_cache is None:
            self._items_cache = self._client.list_items()
        for item in self._items_cache:
            if item.get("name") == item_name:
                return item
        return None

    def _invalidate_cache(self) -> None:
        """Drop the cached item list after a mutation."""
        self._items_cache = None

    def _item_to_secret_value(self, item: dict[str, Any], ref: str) -> SecretValue:
        """Extract secret value from a vault item dict."""
        # The secret is stored in the login password field, a secure note, or custom fields
        login = item.get("login", {})
        value = login.get("password", "")

        # If no password, check notes (secure notes)
        if not value:
            value = item.get("notes", "")

        # If still nothing, check custom fields
        if not value:
            for field in item.get("fields", []):
                if field.get("name") == "secret_value":
                    value = field.get("value", "")
                    break

        # Extract custom metadata
        meta: dict[str, Any] = {}
        for field in item.get("fields", []):
            name = field.get("name", "")
            if name in (
                "provider",
                "env_key",
                "rotation_mode",
                "last_rotated_at",
                "fingerprint",
                "last4",
                "status",
                "manual_revoke_required",
            ):
                meta[name] = field.get("value", "")

        return SecretValue(
            name=ref,
            value=value,
            version=item.get("revisionDate"),
            metadata=meta if meta else None,
        )

    def get_secret(self, name: str) -> SecretValue | None:
        """Retrieve a secret by its internal ref name."""
        self.authenticate()
        self.unlock()

        item_name = internal_ref_to_item_name(name)
        item = self._find_item_by_name(item_name)
        if not item:
            return None

        return self._item_to_secret_value(item, name)

    def set_secret(
        self,
        name: str,
        value: str,
        metadata: dict[str, Any] | None = None,
    ) -> SecretMetadata:
        """Store (create or update) a secret. Returns metadata only."""
        self.authenticate()
        self.unlock()
        self.sync()
        self._invalidate_cache()

        item_name = internal_ref_to_item_name(name)
        existing = self._find_item_by_name(item_name)
        fp, last4 = secret_fingerprint(value)

        # Build the item JSON template
        # Provider tag extracted from the ref: hermes/<provider>/...
        parts = name.split("/")
        provider = parts[1] if len(parts) > 1 else "unknown"

        # Derive classification from ref path + explicit metadata override
        secret_class = (metadata or {}).get("secret_class") or _classify_ref(name)
        renderable = _renderable_to_env(name, secret_class)

        fields: list[dict[str, Any]] = [
            {"name": "provider", "value": provider, "type": 0},
            {"name": "fingerprint", "value": fp, "type": 0},
            {"name": "last4", "value": last4, "type": 0},
            {"name": "secret_class", "value": secret_class, "type": 0},
            {
                "name": "renderable_to_env",
                "value": "true" if renderable else "false",
                "type": 0,
            },
            {
                "name": "rotation_mode",
                "value": metadata.get("rotation_mode", "") if metadata else "",
                "type": 0,
            },
            {"name": "last_rotated_at", "value": str(int(time.time())), "type": 0},
            {"name": "status", "value": "active", "type": 0},
        ]

        if metadata:
            for k, v in metadata.items():
                if k not in {"rotation_mode", "secret_class"}:
                    fields.append({"name": k, "value": str(v), "type": 0})

        item_json: dict[str, Any] = {
            "organizationId": None,
            "collectionIds": None,
            "folderId": None,
            "type": 1,  # 1 = secure note
            "name": item_name,
            "notes": value,  # store secret in secure note body
            "favorite": False,
            "fields": fields,
        }

        try:
            if existing:
                item_json.pop("organizationId", None)
                item_json.pop("collectionIds", None)
                self._client.edit_item(existing["id"], item_json)
                item_id = existing["id"]
            else:
                result = self._client.create_item(item_json)
                item_id = result.get("id", "")

            return SecretMetadata(
                name=name,
                fingerprint=fp,
                last4=last4,
                updated_at=str(int(time.time())),
                version=None,
                provider=provider,
                item_id=item_id,
                secret_class=secret_class,
                renderable_to_env=renderable,
            )
        except BitwardenCLIError as e:
            raise SecretWriteFailed(redact(str(e))) from e

    def delete_secret(self, name: str) -> None:
        """Delete a secret from the vault."""
        self.authenticate()
        self.unlock()

        item_name = internal_ref_to_item_name(name)
        item = self._find_item_by_name(item_name)
        if not item:
            return  # idempotent

        try:
            self._client.delete_item(item["id"])
            self._invalidate_cache()
        except BitwardenCLIError as e:
            raise SecretWriteFailed(redact(str(e))) from e

    # ── Rollback Support ──────────────────────────────────────────

    def backup_secret(self, name: str) -> SecretValue | None:
        """Capture the current secret for potential rollback.

        Returns the current SecretValue so the caller can store it
        in the rollback metadata.  Called BEFORE set_secret during rotation.
        """
        return self.get_secret(name)

    def restore_secret(self, name: str, previous: SecretValue) -> SecretMetadata:
        """Restore a previous secret version (rollback).

        Called when a smoke test fails after a rotation.
        Overwrites the current item with the previous value and metadata.
        """
        self.authenticate()
        self.unlock()
        self.sync()

        fp, _ = secret_fingerprint(previous.value)
        return self.set_secret(
            name,
            previous.value,
            metadata={
                "rotation_mode": "rollback",
                "last_rotated_at": str(int(time.time())),
                "rollback_fingerprint": fp,
                "previous_metadata": json.dumps(previous.metadata or {}),
            },
        )

    def list_secret_refs(self, prefix: str | None = None) -> list[str]:
        """List all internal ref names."""
        self.authenticate()
        self.unlock()

        items = self._client.list_items()
        refs: list[str] = []
        for item in items:
            name = item.get("name", "")
            ref = item_name_to_internal_ref(name)
            if ref:
                if prefix is None or ref.startswith(prefix):
                    refs.append(ref)

        return sorted(refs)

    def get_metadata(self, name: str) -> SecretMetadata | None:
        """Return metadata without retrieving the raw secret value."""
        self.authenticate()
        self.unlock()

        item_name = internal_ref_to_item_name(name)
        item = self._find_item_by_name(item_name)
        if not item:
            return None

        parts = name.split("/")
        provider = parts[1] if len(parts) > 1 else None

        # Extract fingerprint/last4 from custom fields
        meta: dict[str, str] = {}
        for field in item.get("fields", []):
            fname = field.get("name", "")
            if fname in ("fingerprint", "last4", "updated_at", "provider"):
                meta[fname] = field.get("value", "")

        return SecretMetadata(
            name=name,
            fingerprint=meta.get("fingerprint"),
            last4=meta.get("last4"),
            updated_at=meta.get("updated_at"),
            version=item.get("revisionDate"),
            provider=provider,
            item_id=item.get("id"),
        )
