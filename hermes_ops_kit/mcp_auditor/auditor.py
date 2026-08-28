"""Hermes Ops Kit — MCP Auditor.

Discovers configured MCP servers, inventories tools, classifies risks.
Reads from ~/.hermes/config.yaml for mcp_servers declarations and mcp_policy.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


from ..mcp_auditor.classifier import detect_capabilities, classify_risk, scan_metadata  # pyright: ignore[reportMissingImports]
from hermes_ops_kit import ops_config_io  # noqa: E402


def _load_hermes_mcp_config() -> dict[str, Any]:
    """Extract mcp_servers from Hermes config, failing closed if malformed."""
    config_path = os.path.join(ops_config_io.HERMES_HOME, "config.yaml")
    if not os.path.exists(config_path):
        return {}
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        with open(config_path) as f:
            cfg = _yaml.safe_load(f) or {}
    except Exception as exc:
        raise RuntimeError(f"Cannot load Hermes MCP config: {exc}") from exc
    if not isinstance(cfg, dict):
        raise RuntimeError("Hermes config root must be a mapping")
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise RuntimeError("Hermes config 'mcp_servers' must be a mapping")
    return servers


_MCP_POLICY_PATH = os.path.join(ops_config_io.HERMES_HOME, "mcp_policy.json")


def _validate_mcp_policy(policy: Any) -> dict[str, Any]:
    """Validate MCP approval policy shape before using it."""
    if not isinstance(policy, dict):
        raise RuntimeError("MCP policy root must be an object")
    validated = dict(policy)
    for key in ("approved_tools", "approved_servers"):
        value = validated.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise RuntimeError(f"MCP policy '{key}' must be a list of strings")
        validated[key] = value
    return validated


def _load_mcp_policy() -> dict[str, Any]:
    """Load MCP policy from ~/.hermes/mcp_policy.json.

    Also checks ~/.hermes/config.yaml for an inline mcp_policy section
    (legacy / YAML-native storage) for backwards compatibility.
    """
    # Primary: standalone JSON policy file
    if os.path.exists(_MCP_POLICY_PATH):
        try:
            with open(_MCP_POLICY_PATH) as f:
                policy = json.load(f)
            return _validate_mcp_policy(policy)
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"Cannot load MCP policy: {exc}") from exc
    # Fallback: inline mcp_policy in config.yaml
    config_path = os.path.join(ops_config_io.HERMES_HOME, "config.yaml")
    if os.path.exists(config_path):
        try:
            import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

            with open(config_path) as f:
                cfg = _yaml.safe_load(f) or {}
            policy = cfg.get("mcp_policy", {})
            return _validate_mcp_policy(policy)
        except Exception as exc:
            raise RuntimeError(f"Cannot load inline MCP policy: {exc}") from exc
    return {}


def _save_mcp_policy(policy: dict[str, Any]) -> None:
    """Durably persist the authoritative MCP policy with mode 0600."""
    policy_dir = os.path.dirname(_MCP_POLICY_PATH)
    os.makedirs(policy_dir, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mcp-policy-", suffix=".json", dir=policy_dir)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(policy, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _MCP_POLICY_PATH)
        os.chmod(_MCP_POLICY_PATH, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _is_approved(full_name: str, server_id: str, policy: dict[str, Any]) -> bool:
    """Check if a tool is approved via mcp_policy."""
    approved_tools: list[str] = policy.get("approved_tools", [])
    approved_servers: list[str] = policy.get("approved_servers", [])
    if server_id in approved_servers:
        return True
    if full_name in approved_tools:
        return True
    # Wildcard match: "mcp_obsidian-mcp-vault_*" matches all tools of a server
    for entry in approved_tools:
        if entry.endswith("_*") and full_name.startswith(entry[:-2]):
            return True
    return False


def _detect_transport(server_cfg: dict) -> str:
    if server_cfg.get("url"):
        return "http"
    if server_cfg.get("command"):
        return "stdio"
    return "unknown"


def _parse_tools_config(tools_cfg: Any) -> list[dict[str, str]]:
    """Normalise the ``tools`` key from an MCP server config entry.

    Supports three shapes found in the wild:

    * ``tools: [tool1, tool2]`` — flat list of names
    * ``tools: [{name: tool1, desc: ...}, ...]`` — list of dicts
    * ``tools: {include: [tool1, ...], exclude: [tool3, ...]}`` — include/exclude
    * Missing / ``None`` — returns an empty list
    """
    if tools_cfg is None:
        return []
    if isinstance(tools_cfg, list):
        result: list[dict[str, str]] = []
        for item in tools_cfg:
            if isinstance(item, str):
                result.append({"name": item, "desc": ""})
            elif isinstance(item, dict):
                result.append(
                    {
                        "name": item.get("name", ""),
                        "desc": item.get("desc", item.get("description", "")),
                    }
                )
        return result
    if isinstance(tools_cfg, dict):
        # include/exclude shape
        include = tools_cfg.get("include", [])
        if isinstance(include, list):
            return _parse_tools_config(include)
        return []
    return []


# ── MCP protocol discovery ───────────────────────────────────────────

_MCP_INIT_REQUEST = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "hermes-ops-kit-mcp-auditor", "version": "1.0.0"},
        },
    }
)

_MCP_TOOLS_LIST_REQUEST = json.dumps(
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
)


def _discover_stdio_tools(
    command: str, args: list[str] | None = None, env: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """Launch an MCP stdio server and fetch its tool list via JSON-RPC.

    Returns a list of ``{name, desc}`` dicts.  Never raises — returns an
    empty list on any failure.
    """
    cmd_parts = [command] + (args or [])
    try:
        proc = subprocess.Popen(
            cmd_parts,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env={**os.environ, **(env or {})},
        )
    except (OSError, FileNotFoundError, PermissionError):
        return []

    try:
        # initialize
        if proc.stdin is None:
            return []
        proc.stdin.write(_MCP_INIT_REQUEST + "\n")
        proc.stdin.flush()
        # read the initialize response
        init_line = _read_jsonrpc_line(proc)
        if init_line is None:
            return []

        # tools/list
        proc.stdin.write(_MCP_TOOLS_LIST_REQUEST + "\n")
        proc.stdin.flush()
        tools_line = _read_jsonrpc_line(proc)
        if tools_line is None:
            return []

        result = _parse_tools_result(tools_line)
        return result
    except (BrokenPipeError, OSError):
        return []
    finally:
        _safe_terminate(proc)


def _load_oauth_token(server_id: str) -> str | None:
    """Load an OAuth access token for *server_id* from the MCP token store.

    Returns the ``access_token`` string, or ``None`` when no token is found,
    the token is expired, or the file is unreadable.
    """
    # Defense in depth: server_id comes from ~/.hermes/config.yaml (trusted),
    # but constrain it to a single path component so a malformed name cannot
    # traverse outside the token store (e.g. "../../.ssh/...").
    token_dir = os.path.join(ops_config_io.HERMES_HOME, "mcp-tokens")
    token_path = os.path.realpath(os.path.join(token_dir, f"{server_id}.json"))
    if os.path.dirname(token_path) != os.path.realpath(token_dir):
        return None
    if not os.path.exists(token_path):
        return None
    try:
        with open(token_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    token = data.get("access_token")
    if not token:
        return None
    # Respect expiration with a 60 s grace period
    expires_at = data.get("expires_at")
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        if time.time() > expires_at - 60:
            return None  # expired (or expires within 60 s)
    return token


def _discover_http_tools(
    url: str, headers: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """Fetch the tool list from an HTTP MCP server via JSON-RPC POST.

    Returns a list of ``{name, desc}`` dicts.  Never raises — returns an
    empty list on any failure.

    *headers* is an optional dict of extra HTTP headers (e.g. Authorization).
    """
    try:
        # initialize
        init_resp = _http_jsonrpc(url, _MCP_INIT_REQUEST, headers=headers)
        if init_resp is None:
            return []

        # tools/list
        tools_resp = _http_jsonrpc(url, _MCP_TOOLS_LIST_REQUEST, headers=headers)
        if tools_resp is None:
            return []

        return _parse_tools_result(tools_resp)
    except Exception:
        return []


def _parse_sse_data(raw: str) -> str | None:
    """Extract the JSON payload from an SSE (Server-Sent Events) stream.

    SSE lines use ``data: <json>`` format.  Returns the first ``data:``
    line body that parses as valid JSON, or ``None``.
    """
    for line in raw.splitlines():
        if line.startswith("data:"):
            candidate = line[len("data:") :].strip()
            if candidate:
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    continue
    return None


def _http_jsonrpc(
    url: str, request_body: str, headers: dict[str, str] | None = None
) -> str | None:
    """Send a JSON-RPC request to an HTTP MCP endpoint.

    Returns the raw JSON response body text (SSE envelopes are unwrapped
    automatically), or ``None`` on failure.

    *headers* is an optional dict of extra HTTP headers to merge
    (e.g. ``{"Authorization": "Bearer <token>"}`` for OAuth endpoints).
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None

    merged = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if headers:
        merged.update(headers)
    req = urllib.request.Request(
        url,
        data=request_body.encode("utf-8"),
        headers=merged,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            # Handle SSE (text/event-stream) responses — extract the
            # JSON payload from ``data: {...}`` lines.
            if "text/event-stream" in resp.headers.get("Content-Type", ""):
                sse_json = _parse_sse_data(raw)
                if sse_json is not None:
                    return sse_json
            return raw
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _read_jsonrpc_line(proc: subprocess.Popen) -> str | None:
    """Read one JSON-RPC line from a subprocess stdout with a timeout."""
    import select

    stdout = proc.stdout
    if stdout is None:
        return None

    deadline = time.monotonic() + 10  # 10 s timeout
    buf = ""
    while time.monotonic() < deadline:
        remaining = max(0, deadline - time.monotonic())
        try:
            ready, _, _ = select.select([stdout], [], [], remaining)
        except (ValueError, OSError):
            return None
        if not ready:
            return None
        try:
            chunk = stdout.readline()
        except (ValueError, OSError):
            return None
        if not chunk:
            return None
        buf += chunk
        if buf.strip():
            try:
                json.loads(buf.strip())
                return buf.strip()
            except json.JSONDecodeError:
                continue  # wait for more data
    return None


def _parse_tools_result(raw: str) -> list[dict[str, str]]:
    """Parse a JSON-RPC ``tools/list`` response into ``[{name, desc}]``."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    tools = data.get("result", {}).get("tools", [])
    if not isinstance(tools, list):
        return []
    result: list[dict[str, str]] = []
    for t in tools:
        if isinstance(t, str):
            result.append({"name": t, "desc": ""})
        elif isinstance(t, dict):
            result.append(
                {
                    "name": t.get("name", ""),
                    "desc": t.get("description", t.get("desc", "")),
                }
            )
    return result


def _safe_terminate(proc: subprocess.Popen) -> None:
    """Terminate a subprocess, ignoring errors."""
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except (OSError, BrokenPipeError):
            pass
    try:
        proc.wait(timeout=3)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def inventory_servers() -> list[dict[str, Any]]:
    """Discover configured MCP servers from Hermes config."""
    mcp_cfg = _load_hermes_mcp_config()
    servers = []
    for name, cfg in mcp_cfg.items():
        servers.append(
            {
                "server_id": name,
                "transport": _detect_transport(cfg),
                "command": cfg.get("command", "")[:50] if cfg.get("command") else "",
                "url": cfg.get("url", "")[:50] if cfg.get("url") else "",
                "enabled": cfg.get("enabled", True),
                "env_keys": list(cfg.get("env", {}).keys())
                if isinstance(cfg.get("env"), dict)
                else [],
                "tools": [],
                "risk": "unknown",
                "warnings": [],
            }
        )
    return servers


def audit_tool(
    server_id: str,
    tool_name: str,
    description: str = "",
    input_schema: dict | None = None,
) -> dict[str, Any]:
    """Audit a single MCP tool."""
    full_name = f"mcp_{server_id}_{tool_name}"
    caps = detect_capabilities(tool_name, description, input_schema)
    risk = classify_risk(caps)
    inj_risk, inj_matches = scan_metadata(f"{tool_name} {description}")

    reasons = []
    for cap, detected in caps.items():
        if detected:
            reasons.append(cap.replace("_", " "))
    if inj_matches:
        reasons.append(f"injection_risk:{inj_risk}")

    approval = risk in ("medium", "high", "critical") or inj_risk in (
        "medium",
        "high",
    )
    blocked = risk == "critical"

    return {
        "server_id": server_id,
        "tool_name": tool_name,
        "full_name": full_name,
        "description": description[:100] if description else "",
        "capabilities": caps,
        "risk": risk,
        "injection_risk": inj_risk,
        "injection_matches": inj_matches,
        "approval_required": approval,
        "blocked": blocked,
        "reasons": reasons,
    }


def run_audit(*, dynamic_discovery: bool = True) -> dict[str, Any]:
    """Run full MCP audit: servers + tools + risks.

    Interactive audits attempt dynamic MCP protocol discovery first. Preflight
    callers must pass ``dynamic_discovery=False`` so auditing cannot execute or
    contact an unaudited server before deciding whether it may load.
    """
    servers = inventory_servers()
    tools = []
    risks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    policy = _load_mcp_policy()

    _RISK_ORDER = {"critical": 5, "high": 4, "medium": 3, "low": 2, "unknown": 1}

    for srv in servers:
        sid = srv["server_id"]
        cfg = _load_hermes_mcp_config().get(sid, {})

        # ── Phase 1: dynamic discovery (always attempted first) ────────
        discovered: list[dict[str, str]] = []
        transport = srv["transport"]
        if dynamic_discovery and transport == "stdio":
            discovered = _discover_stdio_tools(
                cfg.get("command", ""),
                cfg.get("args"),
                cfg.get("env"),
            )
        elif dynamic_discovery and transport == "http":
            http_headers: dict[str, str] | None = None
            if cfg.get("auth") == "oauth":
                token = _load_oauth_token(sid)
                if token:
                    http_headers = {"Authorization": f"Bearer {token}"}
            discovered = _discover_http_tools(cfg.get("url", ""), headers=http_headers)

        # ── Phase 2: static config is only a fallback ──────────────────
        static_tools = _parse_tools_config(cfg.get("tools"))

        if discovered:
            # Dynamic discovery succeeded — always use the real tool list.
            # Static config declarations are ignored for audit purposes
            # (the audit shows what the server actually exposes).
            known_tools = discovered
        elif static_tools:
            # Dynamic discovery failed — fall back to static declarations.
            known_tools = static_tools
        else:
            known_tools = []

        # ── Phase 3: audit each tool ───────────────────────────────────
        server_risk_level = 1  # unknown
        for t in known_tools:
            tname = t.get("name", t) if isinstance(t, dict) else t
            if not isinstance(tname, str):
                continue
            tdesc = (
                t.get("desc", t.get("description", "")) if isinstance(t, dict) else ""
            )
            tool_audit = audit_tool(sid, tname, tdesc)

            # Approval may permit MEDIUM/HIGH tools, but CRITICAL remains blocked.
            if _is_approved(tool_audit["full_name"], sid, policy):
                tool_audit["approved"] = True
                if tool_audit["risk"] != "critical":
                    tool_audit["blocked"] = False
                    tool_audit["approval_required"] = False

            tools.append(tool_audit)
            srv["tools"].append(tool_audit)

            # Track max risk for server-level rollup
            server_risk_level = max(
                server_risk_level, _RISK_ORDER.get(tool_audit["risk"], 1)
            )

            if tool_audit["risk"] in ("high", "critical", "medium"):
                risks.append(tool_audit)
            if tool_audit["injection_risk"] in ("medium", "high"):
                warnings.append(
                    {
                        "tool": tool_audit["full_name"],
                        "issue": f"metadata injection risk: {tool_audit['injection_risk']}",
                        "matches": tool_audit["injection_matches"],
                    }
                )

        # ── Roll up server-level risk ──────────────────────────────────
        srv["risk"] = {v: k for k, v in _RISK_ORDER.items()}.get(
            server_risk_level, "unknown"
        )

    return {
        "ok": True,
        "servers": servers,
        "tools": tools,
        "risks": risks,
        "warnings": warnings,
    }


def approve_server(server_id: str) -> dict[str, Any]:
    """Approve all tools from an MCP server (atomic whitelist)."""
    policy = _load_mcp_policy()
    approved_servers: list[str] = list(policy.get("approved_servers", []))
    if server_id not in approved_servers:
        approved_servers.append(server_id)
    policy["approved_servers"] = approved_servers
    _save_mcp_policy(policy)
    return {"ok": True, "action": "approved_server", "server_id": server_id}


def approve_tool(full_name: str) -> dict[str, Any]:
    """Approve a single MCP tool."""
    policy = _load_mcp_policy()
    approved_tools: list[str] = list(policy.get("approved_tools", []))
    if full_name not in approved_tools:
        approved_tools.append(full_name)
    policy["approved_tools"] = approved_tools
    _save_mcp_policy(policy)
    return {"ok": True, "action": "approved_tool", "tool": full_name}


def revoke_all() -> dict[str, Any]:
    """Remove all MCP policy approvals."""
    policy = _load_mcp_policy()
    before = len(policy.get("approved_tools", [])) + len(
        policy.get("approved_servers", [])
    )
    _save_mcp_policy({})
    return {"ok": True, "action": "revoked_all", "entries_removed": before}


def show_policy() -> dict[str, Any]:
    """Show current MCP policy."""
    policy = _load_mcp_policy()
    return {"ok": True, "policy": policy}
