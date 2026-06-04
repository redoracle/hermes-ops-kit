"""
Hermes Ops Kit — Bitwarden CLI Client

Low-level, safe wrapper around the `bw` (Bitwarden CLI) binary.

Rules (spec section 11):
- Always subprocess.run with list args — never shell=True.
- Secrets via stdin or environment — never command-line arguments.
- Always timeout commands.
- Always redact stdout/stderr before logging.
- Always parse JSON output with strict schema.
- Block forbidden commands (export, import, share, send, serve 0.0.0.0).

Authentication modes (spec section 7):
- bitwarden_cli_password   — user/password bootstrap
- bitwarden_cli_api_key    — API key login (BW_CLIENTID + BW_CLIENTSECRET)
- bitwarden_cli_session    — existing BW_SESSION
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from security.redaction import redact


# ─── Forbidden Commands ──────────────────────────────────────────────

FORBIDDEN_COMMANDS: set[str] = {
    "export",
    "import",
    "share",
    "send",
}

FORBIDDEN_SERVE_HOSTS: set[str] = {"0.0.0.0", "::", "*"}

# Commands that must never have their full output logged
SENSITIVE_COMMANDS: set[str] = {
    "login",
    "unlock",
    "get",
    "edit",
    "create",
}


# ─── Exceptions ────────────────────────────────────────────────────────


class BitwardenCLIError(Exception):
    """Error from the Bitwarden CLI."""


class BitwardenCLIAuthError(BitwardenCLIError):
    """Authentication or unlock failure."""


class BitwardenCLITimeoutError(BitwardenCLIError):
    """CLI command timed out."""


# ─── Client ────────────────────────────────────────────────────────────


class BitwardenCLIClient:
    """Safe wrapper around the `bw` CLI binary."""

    def __init__(
        self,
        bw_bin: str = "bw",
        server_url: str | None = None,
        appdata_dir: str | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self.bw_bin = bw_bin
        self.server_url = server_url
        self.appdata_dir = appdata_dir
        self.timeout = timeout_seconds

    # ── Helpers ──────────────────────────────────────────────────

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.appdata_dir:
            # Normalize to an absolute path. A value like "$HOME/..." or "~/..."
            # left unexpanded is a *relative* path, so bw would create it under
            # the current working directory (this is how a literal "$HOME" data
            # dir once ended up committed inside the repo). expandvars handles
            # "$HOME", expanduser handles "~", abspath anchors anything relative.
            normalized = os.path.abspath(
                os.path.expanduser(os.path.expandvars(self.appdata_dir))
            )
            env["BITWARDENCLI_APPDATA_DIR"] = normalized
        return env

    def _run(
        self,
        args: list[str],
        *,
        stdin: str | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute `bw <args>` and return parsed JSON stdout.

        Raises BitwardenCLIError on non-zero exit or invalid JSON.
        """
        # Safety gate: reject forbidden commands
        cmd_name = args[0] if args else ""
        if cmd_name in FORBIDDEN_COMMANDS:
            raise BitwardenCLIError(f"forbidden bw command: {cmd_name}")
        if cmd_name == "serve":
            # Check for dangerous --hostname
            for i, arg in enumerate(args):
                if arg in ("--hostname", "-h") and i + 1 < len(args):
                    if args[i + 1] in FORBIDDEN_SERVE_HOSTS:
                        raise BitwardenCLIError(
                            f"bw serve with forbidden hostname: {args[i + 1]}"
                        )

        full_args = [self.bw_bin] + args
        timeout_s = timeout or self.timeout

        try:
            result = subprocess.run(
                full_args,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=self._env(),
                input=stdin,
            )
        except subprocess.TimeoutExpired:
            raise BitwardenCLITimeoutError(
                f"bw {' '.join(args[:3])} timed out after {timeout_s}s"
            )
        except FileNotFoundError:
            raise BitwardenCLIError(
                f"bw CLI not found at {self.bw_bin}. Install: brew install bitwarden-cli"
            )

        stderr = redact(result.stderr.strip())
        stdout = result.stdout.strip()

        if result.returncode != 0:
            raise BitwardenCLIError(
                f"bw {' '.join(args[:3])} failed (rc={result.returncode}): {stderr}"
            )

        if not stdout:
            return {}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # Some bw commands return plain text (e.g., `bw get password <id>`)
            return {"_raw": stdout}

    # ── Server Config ────────────────────────────────────────────

    def config_server(self) -> dict[str, Any]:
        """Set the Bitwarden server URL."""
        if not self.server_url:
            raise BitwardenCLIError("server_url is not configured")
        # First check what's already configured
        self._run(["config", "server", self.server_url])
        return {"server_url": self.server_url, "configured": True}

    # ── Status ────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return `bw status` as a dict (serverUrl, status, userEmail, ...)."""
        return self._run(["status"])

    def is_unlocked(self) -> bool:
        """Return True if the vault is currently unlocked."""
        try:
            s = self.status()
            return s.get("status") == "unlocked"
        except BitwardenCLIError:
            return False

    def is_authenticated(self) -> bool:
        """Return True if a user is logged in."""
        try:
            s = self.status()
            return s.get("status") in ("locked", "unlocked")
        except BitwardenCLIError:
            return False

    # ── Authentication Modes ──────────────────────────────────────

    def login_password(
        self,
        email: str,
        password: str,
    ) -> dict[str, Any]:
        """Mode: bitwarden_cli_password.  Login with email + master password.

        Password is passed via BW_PASSWORD env var to avoid shell exposure.
        """
        env = self._env()
        env["BW_PASSWORD"] = password
        try:
            result = subprocess.run(
                [self.bw_bin, "login", email, "--passwordenv", "BW_PASSWORD"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise BitwardenCLITimeoutError("bw login timed out")
        except FileNotFoundError:
            raise BitwardenCLIError(f"bw CLI not found at {self.bw_bin}")

        stderr = redact(result.stderr.strip())
        if result.returncode != 0:
            raise BitwardenCLIAuthError(f"bw login failed: {stderr}")

        # Parse BW_SESSION from output
        output = result.stdout.strip()
        if "BW_SESSION" in output:
            # Extract the session key line
            for line in output.split("\n"):
                if "BW_SESSION" in line:
                    return {"session": line.split("=")[-1].strip('"')}
        return {"logged_in": True}

    def login_apikey(
        self,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        """Mode: bitwarden_cli_api_key.  Login with API key credentials.

        Credentials passed via environment variables — never command args.
        """
        env = self._env()
        env["BW_CLIENTID"] = client_id
        env["BW_CLIENTSECRET"] = client_secret
        try:
            result = subprocess.run(
                [self.bw_bin, "login", "--apikey"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise BitwardenCLITimeoutError("bw login --apikey timed out")
        except FileNotFoundError:
            raise BitwardenCLIError(f"bw CLI not found at {self.bw_bin}")

        stderr = redact(result.stderr.strip())
        if result.returncode != 0:
            raise BitwardenCLIAuthError(f"bw login --apikey failed: {stderr}")

        output = result.stdout.strip()
        for line in output.split("\n"):
            if "BW_SESSION" in line:
                return {"session": line.split("=")[-1].strip('"')}
        return {"logged_in": True}

    def login_session(self, session: str) -> dict[str, Any]:
        """Mode: bitwarden_cli_session.  Validate an existing BW_SESSION."""
        env = self._env()
        env["BW_SESSION"] = session
        try:
            s = self._run(["status"])
            if s.get("status") != "unlocked":
                raise BitwardenCLIAuthError(
                    f"session is not unlocked: {s.get('status')}"
                )
            return {"session_valid": True, "status": s.get("status")}
        except json.JSONDecodeError:
            raise BitwardenCLIAuthError("invalid session: could not parse status")

    # ── Unlock / Lock ─────────────────────────────────────────────

    def unlock(self, password: str | None = None) -> str:
        """Unlock the vault and return BW_SESSION.

        Password is passed via BW_PASSWORD env var.
        """
        env = self._env()
        if password:
            env["BW_PASSWORD"] = password
            args = [self.bw_bin, "unlock", "--passwordenv", "BW_PASSWORD"]
        else:
            args = [self.bw_bin, "unlock"]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise BitwardenCLITimeoutError("bw unlock timed out")

        stderr = redact(result.stderr.strip())
        if result.returncode != 0:
            raise BitwardenCLIAuthError(f"bw unlock failed: {stderr}")

        output = result.stdout.strip()
        for line in output.split("\n"):
            if "BW_SESSION" in line:
                return line.split("=")[-1].strip('"')
        raise BitwardenCLIAuthError("unlock did not return BW_SESSION")

    def lock(self) -> None:
        """Lock the vault."""
        self._run(["lock"])

    def sync(self) -> dict[str, Any]:
        """Sync the vault (fetch latest from server)."""
        return self._run(["sync"])

    # ── Item CRUD ─────────────────────────────────────────────────

    def list_items(
        self,
        collection_id: str | None = None,
        organization_id: str | None = None,
        folder_id: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """List vault items.  Returns list of item dicts."""
        args = ["list", "items"]
        if collection_id:
            args.extend(["--collectionid", collection_id])
        if organization_id:
            args.extend(["--organizationid", organization_id])
        if folder_id:
            args.extend(["--folderid", folder_id])
        if search:
            args.extend(["--search", search])

        result = self._run(args)
        if isinstance(result, list):
            return result
        # bw may return {"_raw": ...} for empty results
        return []

    def get_item(
        self,
        item_id: str,
    ) -> dict[str, Any]:
        """Get a single item by ID."""
        return self._run(["get", "item", item_id])

    def get_password(self, item_id: str) -> str:
        """Get the password field of an item (returns plain text)."""
        result = self._run(["get", "password", item_id])
        return result.get("_raw", "") if isinstance(result, dict) else str(result)

    def get_username(self, item_id: str) -> str:
        """Get the username field of an item (returns plain text)."""
        result = self._run(["get", "username", item_id])
        return result.get("_raw", "") if isinstance(result, dict) else str(result)

    def create_item(
        self,
        item_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a new vault item from a JSON template.

        JSON is passed via stdin to avoid shell exposure.
        """
        encoded = json.dumps(item_json)
        return self._run(
            ["create", "item"],
            stdin=encoded,
        )

    def edit_item(
        self,
        item_id: str,
        item_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Edit an existing vault item.  JSON passed via stdin."""
        encoded = json.dumps(item_json)
        return self._run(
            ["edit", "item", item_id],
            stdin=encoded,
        )

    def delete_item(self, item_id: str) -> None:
        """Delete a vault item permanently."""
        self._run(["delete", "item", item_id])

    # ── Serve (local API) ─────────────────────────────────────────

    def serve(
        self,
        hostname: str = "127.0.0.1",
        port: int = 8087,
    ) -> subprocess.Popen:
        """Start `bw serve` on localhost.  Returns the Popen process.

        Only 127.0.0.1 is allowed. Caller must terminate the process.
        """
        if hostname != "127.0.0.1":
            raise BitwardenCLIError(
                f"bw serve is restricted to localhost, got {hostname}"
            )
        return subprocess.Popen(
            [self.bw_bin, "serve", "--hostname", hostname, "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env(),
        )

    # ── Encode ────────────────────────────────────────────────────

    def encode(self, raw: str) -> str:
        """Base64-encode a string (used for bw item field values)."""
        result = self._run(["encode"], stdin=raw)
        return result.get("_raw", "") if isinstance(result, dict) else str(result)
