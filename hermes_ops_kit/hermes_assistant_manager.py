#!/usr/bin/env python3
"""Hermes Ops Kit — Assistant Manager CLI.

Manages the Hermes Ops Kit assistant registry (assistants.yaml).
Safe by default: atomic writes, secret scanning, backups, file locking.

Usage:
    hermes-assistant-manager.py list [--json]
    hermes-assistant-manager.py get <id> [--json]
    hermes-assistant-manager.py template assistant-id [--write <id>]
    hermes-assistant-manager.py add <id> --display-name ... --type ... --role ... --transport ...
    hermes-assistant-manager.py set <id> <dot.path> <value>
    hermes-assistant-manager.py enable <id> | disable <id>
    hermes-assistant-manager.py remove <id> [--yes]
    hermes-assistant-manager.py validate
    hermes-assistant-manager.py doctor [--fix-permissions]
    hermes-assistant-manager.py backup | restore <file>
    hermes-assistant-manager.py ping <id> [--json]
"""

from __future__ import annotations


if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )

import argparse
import fcntl
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# ─── Constants ────────────────────────────────────────────────────────

from .ops_config_io import OPS_KIT_DIR as CONFIG_DIR  # noqa: E402

BACKUP_DIR = os.path.join(CONFIG_DIR, "backups")
DEFAULT_CONFIG = os.path.join(CONFIG_DIR, "assistants.yaml")
BUNDLED_CONFIG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "assistants.yaml"
)
LOCK_PATH = os.path.join(CONFIG_DIR, "assistants.yaml.lock")

VALID_TYPES = {
    "remote_hermes",
    "remote_agent",
    "local_agent",
    "a2a_agent",
    "mcp_agent",
    "custom",
}
VALID_ROLES = {
    "remote_worker",
    "reviewer",
    "profiler",
    "security_profiler",
    "researcher",
    "coding_assistant",
    "infra_assistant",
    "workspace_maintainer",
    "custom",
}
VALID_TRANSPORTS = {"openai_chat_completions", "a2a", "mcp", "http_json", "custom"}

EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_VALIDATION = 2
EXIT_SECRET = 3
EXIT_PERM = 4
EXIT_CONFIG = 5
EXIT_NOT_FOUND = 6
EXIT_EXISTS = 7
EXIT_NETWORK = 8
EXIT_LOCK = 9
EXIT_BACKUP = 10

# Pre-compiled format for speed
ASSISTANT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# ─── Secret scanner (uses shared security.redaction) ──────────────────

from .security.redaction import SECRET_PATTERNS  # noqa: E402  # pyright: ignore[reportMissingImports]

SUSPICIOUS_KEYS = {
    "api_key",
    "token",
    "password",
    "secret",
    "authorization",
    "bearer",
    "private_key",
    "client_secret",
    "session",
    "cookie",
}

# ─── Exceptions ───────────────────────────────────────────────────────


class AssistantManagerError(Exception):
    pass


class ValidationError(AssistantManagerError):
    pass


class SecretDetected(AssistantManagerError):
    pass


class PermissionError_(AssistantManagerError):
    pass


# ─── YAML Helpers ─────────────────────────────────────────────────────


def _load_yaml(path: str) -> dict[str, Any]:
    """Load assistants.yaml via the canonical loader (ruamel → PyYAML → JSON)."""
    from .ops_config_io import load_yaml

    if not os.path.exists(path):
        return {"version": 1, "assistants": {}}
    data = load_yaml(path)
    if not data:
        return {"version": 1, "assistants": {}}
    return data


def _save_yaml(path: str, data: dict[str, Any]) -> None:
    """Atomically save assistants.yaml (canonical writer, comment-preserving)."""
    from .ops_config_io import save_yaml

    save_yaml(path, data)


# ─── Secret Scanner ───────────────────────────────────────────────────


def _scan_secrets(data: dict[str, Any]) -> list[str]:
    """Scan rendered YAML for raw secrets. Returns list of violations."""
    violations: list[str] = []

    def _scan_value(value: Any, path: str) -> None:
        if isinstance(value, str):
            for pattern, label in SECRET_PATTERNS:
                if re.search(pattern, value, re.IGNORECASE):
                    violations.append(f"{label} detected at {path}")
            # Check suspicious keys with non-env-var values
            if path.split(".")[-1] in SUSPICIOUS_KEYS and not path.endswith("_env"):
                if len(value) > 5 and not value.startswith("$"):
                    violations.append(
                        f"suspicious key value at {path} (use *_env instead)"
                    )

    def _walk(d: Any, parent_path: str) -> None:
        if isinstance(d, dict):
            for k, v in d.items():
                p = f"{parent_path}.{k}" if parent_path else k
                _walk(v, p)
        elif isinstance(d, list):
            for i, item in enumerate(d):
                _walk(item, f"{parent_path}[{i}]")
        else:
            _scan_value(d, parent_path)

    _walk(data, "")
    return violations


# ─── Validation ────────────────────────────────────────────────────────


@dataclass
class ValError:
    code: str
    path: str
    message: str
    severity: str = "error"


def _validate(data: dict[str, Any]) -> list[ValError]:
    """Validate assistants.yaml structure."""
    errors: list[ValError] = []

    if "assistants" not in data:
        errors.append(
            ValError("missing_key", "assistants", "Top-level 'assistants' key missing")
        )
        return errors

    for aid, adata in data.get("assistants", {}).items():
        prefix = f"assistants.{aid}"

        # Assistant ID
        if not ASSISTANT_ID_RE.match(aid):
            errors.append(
                ValError("invalid_id", prefix, f"Invalid assistant ID: {aid}")
            )

        # Required fields
        for field in ["enabled", "display_name", "type", "role", "transport"]:
            if field not in adata:
                errors.append(
                    ValError(
                        "missing_required",
                        f"{prefix}.{field}",
                        f"Missing required field: {field}",
                    )
                )

        # Enum validation
        if adata.get("type") not in VALID_TYPES | {None}:
            errors.append(
                ValError(
                    "invalid_enum",
                    f"{prefix}.type",
                    f"Invalid type: {adata.get('type')}",
                )
            )
        if adata.get("role") not in VALID_ROLES | {None}:
            errors.append(
                ValError(
                    "invalid_enum",
                    f"{prefix}.role",
                    f"Invalid role: {adata.get('role')}",
                )
            )
        if adata.get("transport") not in VALID_TRANSPORTS | {None}:
            errors.append(
                ValError(
                    "invalid_enum",
                    f"{prefix}.transport",
                    f"Invalid transport: {adata.get('transport')}",
                )
            )

        # Endpoint: env var names must be valid
        endpoint = adata.get("endpoint", {})
        for env_key in ["base_url_env", "api_key_env", "model_env", "health_url_env"]:
            val = endpoint.get(env_key, "")
            if val and not ENV_VAR_RE.match(val):
                errors.append(
                    ValError(
                        "invalid_env_var",
                        f"{prefix}.endpoint.{env_key}",
                        f"Invalid env var name: {val}",
                    )
                )

        # Security booleans
        security = adata.get("security", {})
        for bool_key in [
            "require_vpn",
            "require_token",
            "require_tls",
            "allow_secret_prompts",
            "allow_env_requests",
            "allow_file_mutation",
            "allow_shell_execution",
            "allow_network_scan",
            "allow_repo_write",
            "sanitize_input",
            "sanitize_output",
        ]:
            if bool_key in security and not isinstance(security[bool_key], bool):
                errors.append(
                    ValError(
                        "type_error",
                        f"{prefix}.security.{bool_key}",
                        f"Must be boolean: {bool_key}",
                    )
                )

        # Capabilities must be list of strings
        caps = adata.get("capabilities", [])
        if not isinstance(caps, list):
            errors.append(
                ValError("type_error", f"{prefix}.capabilities", "Must be a list")
            )
        else:
            for c in caps:
                if not isinstance(c, (str, dict)):
                    errors.append(
                        ValError(
                            "type_error",
                            f"{prefix}.capabilities",
                            f"Must be string or dict, got {type(c).__name__}",
                        )
                    )

        # Duplicate capability check
        if isinstance(caps, list):
            cap_ids = [c if isinstance(c, str) else c.get("id", "") for c in caps]
            if len(cap_ids) != len(set(cap_ids)):
                errors.append(
                    ValError(
                        "duplicates", f"{prefix}.capabilities", "Duplicate capabilities"
                    )
                )

    return errors


# ─── File Locking ──────────────────────────────────────────────────────


def _acquire_lock(timeout: int = 10) -> bool:
    """Acquire advisory lock on assistants.yaml. Returns True if acquired."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            time.sleep(0.2)
    return False


def _release_lock() -> None:
    """Release advisory lock."""
    try:
        if os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)
    except OSError:
        pass


# ─── Backup Manager ────────────────────────────────────────────────────


def _backup(config_path: str) -> str:
    """Create timestamped backup. Returns backup path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"assistants.yaml.{ts}.bak")
    shutil.copy2(config_path, backup_path)
    _prune_backups()
    return backup_path


def _prune_backups(keep: int = 20) -> None:
    """Keep only the most recent N backups."""
    if not os.path.exists(BACKUP_DIR):
        return
    backups = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.endswith(".bak")],
        reverse=True,
    )
    for old in backups[keep:]:
        os.remove(os.path.join(BACKUP_DIR, old))


# ─── Output Helpers ────────────────────────────────────────────────────


def _output(result: dict, as_json: bool = False) -> None:
    if as_json:
        if "version" not in result:
            from .ui.json_output import VERSION

            result["version"] = VERSION
            result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            result.setdefault("warnings", [])
            result.setdefault("errors", [])
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_human(result)


def _print_human(result: dict) -> None:
    if result.get("ok") is False:
        # Check for nested error inside .result (envelope pattern)
        error = result.get("error")
        if error is None and isinstance(result.get("result"), dict):
            error = result["result"].get("error", "unknown")
        print(f"ERROR: {error or 'unknown'}")
        return
    r = result.get("result", result)
    if "assistants" in r:
        r = r["assistants"]
    if isinstance(r, list):
        # Assistant list — render a human-readable table
        enabled_count = sum(1 for a in r if a.get("enabled"))
        disabled_count = len(r) - enabled_count
        print(f"\nASSISTANTS · {enabled_count} enabled · {disabled_count} disabled\n")
        for item in r:
            if not isinstance(item, dict) or "id" not in item:
                continue
            icon = "●" if item.get("enabled") else "○"
            caps = item.get("capabilities", [])
            cap_str = " · ".join(
                c if isinstance(c, str) else c.get("id", "?") for c in caps[:5]
            )
            if len(caps) > 5:
                cap_str += f" +{len(caps) - 5} more"
            print(f"  {icon} {item['id']}")
            for k in ("display_name", "role", "transport"):
                if k in item:
                    print(f"    {k:<10s} {item[k]}")
            print(f"    {'caps':<10s} {cap_str if cap_str else 'none'}")
            print()
    elif isinstance(r, dict):
        print(json.dumps(r, indent=2, ensure_ascii=False))


# ─── Assistant Manager ─────────────────────────────────────────────────


class AssistantManager:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self._data: dict[str, Any] | None = None

    @property
    def data(self) -> dict[str, Any]:
        if self._data is None:
            self._data = _load_yaml(self.config_path)
            self._data.setdefault("version", 1)
            self._data.setdefault("assistants", {})
        return self._data

    def _reload(self) -> None:
        self._data = None

    def _save(self, *, backup: bool = True) -> None:
        if backup and os.path.exists(self.config_path):
            _backup(self.config_path)
        violations = _scan_secrets(self.data)
        if violations:
            raise SecretDetected(f"Secrets detected: {'; '.join(violations)}")
        errors = _validate(self.data)
        critical = [e for e in errors if e.severity == "error"]
        if critical:
            raise ValidationError(
                f"Validation failed ({len(critical)} errors): {critical[0].message}"
            )

        if not _acquire_lock():
            raise AssistantManagerError(
                "Could not acquire lock — is another instance running?"
            )
        try:
            _save_yaml(self.config_path, self.data)
        finally:
            _release_lock()
        self._reload()

    # ── CRUD ───────────────────────────────────────────────────────

    def list(self, **filters) -> list[dict]:
        result = []
        for aid, adata in self.data["assistants"].items():
            if filters.get("enabled") is True and not adata.get("enabled"):
                continue
            if filters.get("enabled") is False and adata.get("enabled"):
                continue
            if "role" in filters and adata.get("role") != filters["role"]:
                continue
            if "type" in filters and adata.get("type") != filters["type"]:
                continue
            item = {"id": aid, **adata}
            result.append(item)
        return result

    def get(self, assistant_id: str) -> dict | None:
        adata = self.data["assistants"].get(assistant_id)
        if adata is None:
            return None
        return {"id": assistant_id, **adata}

    def exists(self, assistant_id: str) -> bool:
        return assistant_id in self.data["assistants"]

    def add(self, assistant_id: str, config: dict) -> None:
        if self.exists(assistant_id):
            raise AssistantManagerError(
                f"Assistant '{assistant_id}' already exists. Use --replace to overwrite."
            )
        if not ASSISTANT_ID_RE.match(assistant_id):
            raise ValidationError(f"Invalid assistant ID: {assistant_id}")
        self.data["assistants"][assistant_id] = config
        self._save()

    def remove(self, assistant_id: str) -> None:
        if not self.exists(assistant_id):
            raise AssistantManagerError(f"Assistant '{assistant_id}' not found")
        del self.data["assistants"][assistant_id]
        self._save()

    def enable(self, assistant_id: str) -> None:
        if not self.exists(assistant_id):
            raise AssistantManagerError(f"Assistant '{assistant_id}' not found")
        self.data["assistants"][assistant_id]["enabled"] = True
        # Also update metadata timestamp if present
        meta = self.data["assistants"][assistant_id].get("metadata", {})
        if isinstance(meta, dict):
            meta["updated_at"] = datetime.now().isoformat()
        self._save()

    def disable(self, assistant_id: str) -> None:
        if not self.exists(assistant_id):
            raise AssistantManagerError(f"Assistant '{assistant_id}' not found")
        self.data["assistants"][assistant_id]["enabled"] = False
        meta = self.data["assistants"][assistant_id].get("metadata", {})
        if isinstance(meta, dict):
            meta["updated_at"] = datetime.now().isoformat()
        self._save()

    def set(self, assistant_id: str, dot_path: str, value: Any) -> None:
        if not self.exists(assistant_id):
            raise AssistantManagerError(f"Assistant '{assistant_id}' not found")
        parts = dot_path.split(".")
        target = self.data["assistants"][assistant_id]
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        # Type inference
        if isinstance(value, str):
            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            elif value.isdigit():
                value = int(value)
        target[parts[-1]] = value
        meta = self.data["assistants"][assistant_id].get("metadata", {})
        if isinstance(meta, dict):
            meta["updated_at"] = datetime.now().isoformat()
        self._save()

    def unset(self, assistant_id: str, dot_path: str) -> None:
        if not self.exists(assistant_id):
            raise AssistantManagerError(f"Assistant '{assistant_id}' not found")
        parts = dot_path.split(".")
        target = self.data["assistants"][assistant_id]
        for part in parts[:-1]:
            if part not in target:
                return
            target = target[part]
        if parts[-1] in target:
            del target[parts[-1]]
        self._save()

    def validate(self) -> tuple[list[ValError], list[str]]:
        errors = _validate(self.data)
        secrets = _scan_secrets(self.data)
        return errors, secrets

    def doctor(self) -> dict:
        result: dict = {
            "ok": True,
            "config": self.config_path,
            "assistants": {},
            "warnings": [],
        }

        # Config file
        if not os.path.exists(self.config_path):
            result["ok"] = False
            result["warnings"].append(f"Config not found: {self.config_path}")
            return result

        # Permissions
        mode = os.stat(self.config_path).st_mode & 0o777
        if mode > 0o600:
            result["warnings"].append(
                f"Config permissions too broad: {oct(mode)} (expected 600)"
            )

        # Validate
        errors, secrets = self.validate()
        if errors:
            result["warnings"].append(f"{len(errors)} validation error(s)")
        if secrets:
            result["ok"] = False
            result["warnings"].append(f"{len(secrets)} secret violation(s)")

        # Per assistant
        for aid, adata in self.data["assistants"].items():
            a_result: dict = {
                "enabled": adata.get("enabled", False),
                "status": "ok",
                "warnings": [],
            }

            # Check referenced env vars exist
            endpoint = adata.get("endpoint", {})
            for env_key in ["base_url_env", "api_key_env", "model_env"]:
                env_val = endpoint.get(env_key, "")
                if env_val and not os.environ.get(env_val):
                    a_result["warnings"].append(f"Env var {env_val} not set")

            if a_result["warnings"]:
                a_result["status"] = "warnings"
            result["assistants"][aid] = a_result

        return result


# ─── Templates ─────────────────────────────────────────────────────────

ASSISTANT_TEMPLATE = {
    "enabled": True,
    "display_name": "Assistant Profiler ☁️",
    "type": "remote_hermes",
    "role": "security_profiler",
    "transport": "openai_chat_completions",
    "future_transport": "a2a",
    "endpoint": {
        "base_url_env": "ASSISTANT_API_BASE",
        "api_key_env": "ASSISTANT_API_KEY",
        "model_env": "ASSISTANT_MODEL",
        "default_model": "hermes-agent",
    },
    "security": {
        "network_zone": "tailnet",
        "require_vpn": True,
        "require_token": True,
        "require_tls": False,
        "allow_secret_prompts": False,
        "allow_env_requests": False,
        "allow_file_mutation": False,
        "allow_shell_execution": False,
        "allow_network_scan": False,
        "allow_repo_write": False,
        "sanitize_input": True,
        "sanitize_output": True,
    },
    "policy": {
        "max_timeout_seconds": 180,
        "max_prompt_bytes": 50000,
        "max_response_bytes": 200000,
        "max_parallel_tasks": 2,
        "max_retries": 1,
        "require_approval_for": [
            "file_write",
            "shell_execute",
            "repo_mutation",
            "network_scan",
            "credential_access",
            "restricted_serving",
        ],
    },
    "capabilities": [
        {
            "id": "profile_contact",
            "description": "Create/update contact person profiles",
            "safe_by_default": True,
        },
        {
            "id": "profile_company",
            "description": "Create/update company profiles",
            "safe_by_default": True,
        },
        {
            "id": "log_interaction",
            "description": "Log meetings, calls, interactions",
            "safe_by_default": True,
        },
        {
            "id": "enrich_profile",
            "description": "Enrich profiles from authorized context",
            "safe_by_default": False,
        },
        {
            "id": "link_entities",
            "description": "Link people, companies, projects via wikilinks",
            "safe_by_default": True,
        },
        {
            "id": "maintain_workspace",
            "description": "Workspace maintenance: dedup, index, validate",
            "safe_by_default": True,
        },
        {
            "id": "security_profile",
            "description": "Security profiles: risk, exposure, trust maps",
            "safe_by_default": False,
        },
        {
            "id": "restricted_profile",
            "description": "Restricted dossiers (auth-gated serving)",
            "safe_by_default": False,
        },
        {
            "id": "serve_restricted_profile",
            "description": "Serve restricted data (requires auth + audit)",
            "safe_by_default": False,
        },
    ],
    "blocked_capabilities": [
        "secret_storage",
        "env_dump",
        "credential_access",
        "destructive_shell",
        "production_deploy",
        "unauthorized_scan",
        "code_generation",
        "code_review",
        "repo_mutation",
    ],
    "metadata": {
        "owner": "operator",
        "tags": [
            "assistant/assistant-id",
            "role/security-profiler",
            "transport/openai-compatible",
        ],
    },
}


# ─── Discover ──────────────────────────────────────────────────────────

AGENT_CARD_ALIASES = {"hermes-agent", "remote-hermes"}


def _discover_assistant(url: str, assistant_id: str | None = None) -> dict:
    """Fetch /.well-known/agent.json from a remote assistant and return parsed card."""
    import urllib.request

    # Normalize URL
    base = url.rstrip("/")
    agent_url = f"{base}/.well-known/agent.json"

    try:
        req = urllib.request.Request(agent_url)
        resp = urllib.request.urlopen(req, timeout=10)
        card = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:  # pyright: ignore[reportAttributeAccessIssue]
        return {
            "ok": False,
            "error": f"HTTP {e.code}: agent.json not found at {agent_url}",
        }
    except urllib.error.URLError as e:  # pyright: ignore[reportAttributeAccessIssue]
        return {"ok": False, "error": f"Connection failed: {e.reason}"}
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Invalid JSON at {agent_url}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

    # Extract assistant metadata from agent card
    card_id = card.get("id", card.get("name", ""))
    display = card.get("displayName", card.get("name", card_id))
    _description = card.get("description", "")
    caps = card.get("capabilities", card.get("skills", []))
    if isinstance(caps, dict):
        caps = list(caps.keys())
    caps_list = [c if isinstance(c, str) else c.get("id", str(c)) for c in caps]
    transport = card.get(
        "defaultTransport", card.get("transport", "openai_chat_completions")
    )

    # Build assistant config from agent card
    _aid: str = (assistant_id or card_id).lower().replace(" ", "-")
    config = {
        "enabled": True,
        "display_name": display or assistant_id,
        "type": card.get("type", "remote_hermes"),
        "role": card.get("role", "remote_worker"),
        "transport": transport,
        "endpoint": {
            "base_url_env": f"{_aid.upper()}_API_BASE",
            "api_key_env": f"{_aid.upper()}_API_KEY",
            "model_env": f"{_aid.upper()}_MODEL",
            "default_model": card.get("defaultModel", "hermes-agent"),
        },
        "security": card.get(
            "security",
            {"network_zone": "unknown", "require_vpn": True, "require_token": True},
        ),
        "policy": card.get("policy", {}),
        "capabilities": caps_list,
        "metadata": {
            "discovered_from": agent_url,
            "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }

    return {"ok": True, "card": card, "assistant_id": _aid, "config": config}


# ─── Ping ──────────────────────────────────────────────────────────────


def _ping_assistant(config_path: str, assistant_id: str, timeout: int = 15) -> dict:
    """Ping a Hermes assistant by ID."""
    # Load env into os.environ so endpoint env vars are available.
    # .env is loaded first, then .env.generated on top.
    from .env.loader import load_dotenv

    load_dotenv()

    mgr = AssistantManager(config_path)
    _aid = mgr.get(assistant_id)
    if not _aid:
        return {"ok": False, "error": f"Assistant '{assistant_id}' not found in config"}

    endpoint = _aid.get("endpoint", {})
    base_url_env = endpoint.get("base_url_env", "ASSISTANT_API_BASE")
    api_key_env = endpoint.get("api_key_env", "ASSISTANT_API_KEY")
    model = endpoint.get("default_model", "hermes-agent")

    base_url = os.environ.get(base_url_env, "")
    api_key = os.environ.get(api_key_env, "")

    if not base_url:
        return {"ok": False, "error": f"{base_url_env} not set"}
    if not api_key:
        return {"ok": False, "error": f"{api_key_env} not set"}

    import urllib.request

    start = time.time()
    try:
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "user", "content": "Return exactly: ASSISTANT_OK"}
                ],
                "max_tokens": 10,
                "stream": False,
            }
        ).encode()

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = json.loads(resp.read().decode())
        duration = int((time.time() - start) * 1000)
        text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {
            "ok": True,
            "assistant": assistant_id,
            "latency_ms": duration,
            "response": text.strip(),
            "model": body.get("model", model),
        }
    except Exception as e:
        return {
            "ok": False,
            "assistant": assistant_id,
            "error": str(e)[:200],
            "duration_ms": int((time.time() - start) * 1000),
        }


# ─── CLI ───────────────────────────────────────────────────────────────


def resolve_config_path(args: argparse.Namespace) -> str:
    if getattr(args, "config", None):
        return args.config
    env_path = os.environ.get("HERMES_ASSISTANTS_CONFIG")
    if env_path:
        return env_path
    from .ops_config_io import deployed_or_bundled

    return deployed_or_bundled("assistants.yaml")


def _add_global_flags(p: argparse.ArgumentParser) -> None:
    """Add global flags to a (sub)parser (--json, --quiet, …).

    NOTE: --config is intentionally NOT here — it is added only to the main
    parser so it always works when placed before the subcommand.  argparse
    cannot resolve the same store-value flag at both levels (the subparser's
    copy shadows the parent value back to None).
    """
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p.add_argument("--no-backup", action="store_true", help="Skip backup before write")
    p.add_argument("--allow-plugin-config-write", action="store_true")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Ops Kit — Assistant Manager")
    # --config is added ONLY to the main parser (not via _add_global_flags and
    # not on subparsers) so that it always works when placed before the
    # subcommand.  argparse cannot resolve the same store-value flag at both
    # the parent and subparser level — the subparser's copy shadows the parent
    # value back to None.
    parser.add_argument("--config", help="Path to assistants.yaml")
    _add_global_flags(parser)

    sub = parser.add_subparsers(dest="command", help="Command")
    sub.required = True

    # list
    list_p = sub.add_parser("list")
    _add_global_flags(list_p)
    list_p.add_argument("--enabled", action="store_true", default=None)
    list_p.add_argument(
        "--disabled", action="store_true", default=None, dest="show_disabled"
    )
    list_p.add_argument("--role")
    list_p.add_argument("--type", dest="filter_type")
    list_p.add_argument("--transport")

    # get
    get_p = sub.add_parser("get")
    _add_global_flags(get_p)
    get_p.add_argument("assistant_id")
    get_p.add_argument("--yaml", action="store_true")

    # template
    tmpl_p = sub.add_parser("template")
    _add_global_flags(tmpl_p)
    tmpl_p.add_argument(
        "template_type",
        choices=["assistant-id", "generic", "remote_hermes", "security_profiler"],
    )
    tmpl_p.add_argument("--write", dest="write_id")

    # add
    add_p = sub.add_parser("add")
    _add_global_flags(add_p)
    add_p.add_argument("assistant_id")
    add_p.add_argument("--display-name", required=True)
    add_p.add_argument("--type", required=True, dest="atype")
    add_p.add_argument("--role", required=True)
    add_p.add_argument("--transport", required=True)
    add_p.add_argument("--base-url-env")
    add_p.add_argument("--api-key-env")
    add_p.add_argument("--model-env")
    add_p.add_argument("--default-model", default="hermes-agent")
    add_p.add_argument("--capability", action="append", dest="capabilities", default=[])
    add_p.add_argument("--vault-root")
    add_p.add_argument("--replace", action="store_true")

    # set / unset
    set_p = sub.add_parser("set")
    _add_global_flags(set_p)
    set_p.add_argument("assistant_id")
    set_p.add_argument("dot_path", nargs="?")
    set_p.add_argument("value", nargs="?")

    unset_p = sub.add_parser("unset")
    _add_global_flags(unset_p)
    unset_p.add_argument("assistant_id")
    unset_p.add_argument("dot_path")

    # enable / disable
    en_p = sub.add_parser("enable")
    _add_global_flags(en_p)
    en_p.add_argument("assistant_id")
    dis_p = sub.add_parser("disable")
    _add_global_flags(dis_p)
    dis_p.add_argument("assistant_id")

    # remove
    rm_p = sub.add_parser("remove")
    _add_global_flags(rm_p)
    rm_p.add_argument("assistant_id")
    rm_p.add_argument("--yes", action="store_true")

    # validate
    val_p = sub.add_parser("validate")
    _add_global_flags(val_p)

    # doctor
    doc_p = sub.add_parser("doctor")
    _add_global_flags(doc_p)
    doc_p.add_argument("--fix-permissions", action="store_true")

    # backup / restore
    bak_p = sub.add_parser("backup")
    _add_global_flags(bak_p)
    rest_p = sub.add_parser("restore")
    _add_global_flags(rest_p)
    rest_p.add_argument("backup_file")

    # ping
    ping_p = sub.add_parser("ping")
    _add_global_flags(ping_p)
    ping_p.add_argument("assistant_id")
    ping_p.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("ASSISTANT_TIMEOUT_SECONDS", "15")),
    )  # env knob is the documented default; CLI flag overrides

    # discover
    disc_p = sub.add_parser("discover")
    _add_global_flags(disc_p)
    disc_p.add_argument("url", help="Assistant base URL (e.g. <assistant-url>)")
    disc_p.add_argument(
        "--assistant-id",
        help="Assistant ID to register as (default: derived from agent card)",
    )
    disc_p.add_argument(
        "--preview",
        action="store_true",
        help="Fetch and show agent card without importing",
    )

    args = parser.parse_args()

    if args.command in ("add", "set", "unset", "enable", "disable", "remove"):
        if args.dry_run:
            _output(
                {"ok": True, "dry_run": True, "command": args.command},
                as_json=args.json,
            )
            return

    config_path = resolve_config_path(args)

    # Check plugin config write protection
    if config_path == BUNDLED_CONFIG and args.command in (
        "add",
        "set",
        "unset",
        "enable",
        "disable",
        "remove",
    ):
        if not getattr(args, "allow_plugin_config_write", False):
            print(
                "ERROR: Refusing to modify bundled plugin config. Use --config or --allow-plugin-config-write",
                file=sys.stderr,
            )
            sys.exit(EXIT_PERM)

    try:
        mgr = AssistantManager(config_path)

        if args.command == "list":
            filters = {}
            if args.enabled is True:
                filters["enabled"] = True
            if getattr(args, "show_disabled", False):
                filters["enabled"] = False
            if args.role:
                filters["role"] = args.role
            if getattr(args, "filter_type", None):
                filters["type"] = args.filter_type
            result = mgr.list(**filters)
            if args.json:
                _output({"ok": True, "command": "list", "result": result}, as_json=True)
            else:
                _print_human({"assistants": result})

        elif args.command == "get":
            result = mgr.get(args.assistant_id)
            if result is None:
                _output(
                    {"ok": False, "error": f"Not found: {args.assistant_id}"},
                    as_json=args.json,
                )
                sys.exit(EXIT_NOT_FOUND)
            if getattr(args, "yaml", False):
                import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

                print(
                    _yaml.safe_dump(
                        {
                            args.assistant_id: {
                                k: v for k, v in result.items() if k != "id"
                            }
                        },
                        indent=2,
                    )
                )
            else:
                _output(
                    {"ok": True, "command": "get", "assistant": result},
                    as_json=args.json,
                )

        elif args.command == "template":
            template = (
                ASSISTANT_TEMPLATE
                if args.template_type in ("assistant-id", "security_profiler")
                else {
                    "enabled": True,
                    "display_name": "",
                    "type": "remote_hermes",
                    "role": "reviewer",
                    "transport": "openai_chat_completions",
                    "endpoint": {},
                    "security": {},
                    "policy": {},
                    "capabilities": [],
                }
            )
            if args.write_id:
                if mgr.exists(args.write_id) and not getattr(args, "replace", False):
                    print(f"ERROR: '{args.write_id}' already exists", file=sys.stderr)
                    sys.exit(EXIT_EXISTS)
                mgr.add(args.write_id, template)
                print(f"Created assistant: {args.write_id}")
            else:
                import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

                print(
                    _yaml.safe_dump(
                        {args.write_id or args.template_type: template}, indent=2
                    )
                )

        elif args.command == "add":
            if not ASSISTANT_ID_RE.match(args.assistant_id):
                print(
                    f"ERROR: Invalid assistant ID: {args.assistant_id}", file=sys.stderr
                )
                sys.exit(EXIT_VALIDATION)

            config = {
                "enabled": True,
                "display_name": args.display_name,
                "type": args.atype,
                "role": args.role,
                "transport": args.transport,
                "endpoint": {
                    "base_url_env": args.base_url_env or "",
                    "api_key_env": args.api_key_env or "",
                    "model_env": args.model_env or "",
                    "default_model": args.default_model,
                },
                "security": {
                    "network_zone": "unknown",
                    "require_vpn": True,
                    "require_token": True,
                    "require_tls": True,
                    "allow_secret_prompts": False,
                    "allow_env_requests": False,
                    "allow_file_mutation": False,
                    "allow_shell_execution": False,
                    "allow_network_scan": False,
                    "allow_repo_write": False,
                    "sanitize_input": True,
                    "sanitize_output": True,
                },
                "policy": {
                    "max_timeout_seconds": 120,
                    "max_prompt_bytes": 50000,
                    "max_response_bytes": 200000,
                    "max_parallel_tasks": 1,
                    "max_retries": 1,
                    "require_approval_for": [
                        "file_write",
                        "shell_execute",
                        "repo_mutation",
                        "network_scan",
                        "credential_access",
                    ],
                },
                "capabilities": [
                    {"id": c, "description": "", "safe_by_default": True}
                    for c in args.capabilities
                ],
            }
            if args.replace:
                mgr.data["assistants"][args.assistant_id] = config
                mgr._save()
            else:
                mgr.add(args.assistant_id, config)
            print(f"Added assistant: {args.assistant_id}")

        elif args.command == "set":
            mgr.set(args.assistant_id, args.dot_path, args.value)
            print(f"Set {args.assistant_id}.{args.dot_path} = {args.value}")

        elif args.command == "unset":
            mgr.unset(args.assistant_id, args.dot_path)
            print(f"Unset {args.assistant_id}.{args.dot_path}")

        elif args.command == "enable":
            mgr.enable(args.assistant_id)
            print(f"Enabled: {args.assistant_id}")

        elif args.command == "disable":
            mgr.disable(args.assistant_id)
            print(f"Disabled: {args.assistant_id}")

        elif args.command == "remove":
            if not args.yes:
                print(f"Remove '{args.assistant_id}'? Use --yes to confirm.")
                sys.exit(1)
            mgr.remove(args.assistant_id)
            print(f"Removed: {args.assistant_id}")

        elif args.command == "validate":
            errors, secrets = mgr.validate()
            all_ok = len(errors) == 0 and len(secrets) == 0
            _output(
                {
                    "ok": all_ok,
                    "command": "validate",
                    "validation_errors": [
                        {"code": e.code, "path": e.path, "message": e.message}
                        for e in errors
                    ],
                    "secret_violations": secrets,
                },
                as_json=args.json,
            )

        elif args.command == "doctor":
            result = mgr.doctor()
            if args.fix_permissions and os.path.exists(config_path):
                os.chmod(config_path, 0o600)
                result["permissions_fixed"] = True
            _output(result, as_json=args.json)

        elif args.command == "backup":
            path = _backup(config_path)
            print(f"Backup: {path}")

        elif args.command == "restore":
            backup_path = args.backup_file
            if not os.path.isabs(backup_path):
                backup_path = os.path.join(BACKUP_DIR, backup_path)
            if not os.path.exists(backup_path):
                print(f"ERROR: Backup not found: {backup_path}", file=sys.stderr)
                sys.exit(EXIT_BACKUP)
            # Validate before restore
            backup_data = _load_yaml(backup_path)
            violations = _scan_secrets(backup_data)
            if violations:
                print(
                    f"ERROR: Secrets in backup: {'; '.join(violations)}",
                    file=sys.stderr,
                )
                sys.exit(EXIT_SECRET)
            _backup(config_path)
            shutil.copy2(backup_path, config_path)
            print(f"Restored from: {backup_path}")

        elif args.command == "ping":
            result = _ping_assistant(
                config_path, args.assistant_id, timeout=args.timeout
            )
            _output(
                {"ok": result.get("ok", False), "command": "ping", "result": result},
                as_json=args.json,
            )

        elif args.command == "discover":
            dry = getattr(args, "preview", False) or getattr(args, "dry_run", False)
            result = _discover_assistant(args.url, getattr(args, "assistant_id", None))
            if not result.get("ok"):
                _output(result, as_json=args.json)
                sys.exit(EXIT_NETWORK)
            aid = result["assistant_id"]
            cfg = result["config"]
            _output(
                {
                    "ok": True,
                    "command": "discover",
                    "assistant_id": aid,
                    "card": result["card"],
                },
                as_json=args.json,
            )
            if not dry:
                if mgr.exists(aid):
                    print(
                        f"Assistant '{aid}' already exists. Use --replace to overwrite.",
                        file=sys.stderr,
                    )
                else:
                    mgr.add(aid, cfg)
                    print(f"Imported assistant: {aid} ({cfg['display_name']})")

    except SecretDetected as e:
        _output(
            {"ok": False, "error": str(e), "code": "secret_detected"}, as_json=args.json
        )
        sys.exit(EXIT_SECRET)
    except ValidationError as e:
        _output(
            {"ok": False, "error": str(e), "code": "validation_error"},
            as_json=args.json,
        )
        sys.exit(EXIT_VALIDATION)
    except AssistantManagerError as e:
        _output({"ok": False, "error": str(e)}, as_json=args.json)
        sys.exit(EXIT_GENERAL)
    except Exception as e:
        _output({"ok": False, "error": str(e)}, as_json=args.json)
        sys.exit(EXIT_GENERAL)


if __name__ == "__main__":
    main()
