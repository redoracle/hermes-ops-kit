"""Hermes Ops Kit — MCP Auditor.

Discovers configured MCP servers, inventories tools, classifies risks.
Reads from ~/.hermes/config.yaml for mcp_servers declarations and mcp_policy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.classifier import detect_capabilities, classify_risk, scan_metadata  # pyright: ignore[reportMissingImports]


def _load_hermes_mcp_config() -> dict[str, Any]:
    """Extract mcp_servers from Hermes config."""
    config_path = os.path.expanduser("~/.hermes/config.yaml")
    if not os.path.exists(config_path):
        return {}
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        with open(config_path) as f:
            cfg = _yaml.safe_load(f) or {}
        return cfg.get("mcp_servers", {})
    except Exception:
        return {}


_MCP_POLICY_PATH = os.path.expanduser("~/.hermes/mcp_policy.json")


def _load_mcp_policy() -> dict[str, Any]:
    """Load MCP policy from ~/.hermes/mcp_policy.json.

    Also checks ~/.hermes/config.yaml for an inline mcp_policy section
    (legacy / YAML-native storage) for backwards compatibility.
    """
    # Primary: standalone JSON policy file
    if os.path.exists(_MCP_POLICY_PATH):
        try:
            with open(_MCP_POLICY_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    # Fallback: inline mcp_policy in config.yaml
    config_path = os.path.expanduser("~/.hermes/config.yaml")
    if os.path.exists(config_path):
        try:
            import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

            with open(config_path) as f:
                cfg = _yaml.safe_load(f) or {}
            return cfg.get("mcp_policy", {})
        except Exception:
            pass
    return {}


def _save_mcp_policy(policy: dict[str, Any]) -> None:
    """Persist MCP policy to ~/.hermes/mcp_policy.json (no YAML dependency).

    Also attempts to sync into config.yaml when PyYAML is available."""
    # Atomic JSON write
    tmp = _MCP_POLICY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(policy, f, indent=2, sort_keys=True)
    os.replace(tmp, _MCP_POLICY_PATH)

    # Best-effort sync to config.yaml when PyYAML is available
    config_path = os.path.expanduser("~/.hermes/config.yaml")
    if os.path.exists(config_path):
        try:
            import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

            with open(config_path) as f:
                cfg = _yaml.safe_load(f) or {}
            cfg["mcp_policy"] = policy
            tmp_yaml = config_path + ".tmp"
            with open(tmp_yaml, "w") as f:
                _yaml.dump(
                    cfg,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            os.replace(tmp_yaml, config_path)
        except Exception:
            pass  # JSON file is authoritative; YAML sync is best-effort


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


def _discover_http_tools(url: str) -> list[dict[str, str]]:
    """Fetch the tool list from an HTTP MCP server via JSON-RPC POST.

    Returns a list of ``{name, desc}`` dicts.  Never raises — returns an
    empty list on any failure.
    """
    try:
        # initialize
        init_resp = _http_jsonrpc(url, _MCP_INIT_REQUEST)
        if init_resp is None:
            return []

        # tools/list
        tools_resp = _http_jsonrpc(url, _MCP_TOOLS_LIST_REQUEST)
        if tools_resp is None:
            return []

        return _parse_tools_result(tools_resp)
    except Exception:
        return []


def _http_jsonrpc(url: str, request_body: str) -> str | None:
    """Send a JSON-RPC request to an HTTP MCP endpoint.

    Returns the raw response body text, or None on failure.
    """
    req = urllib.request.Request(
        url,
        data=request_body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
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

    approval = risk in ("high", "critical") or inj_risk == "high"
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


def run_audit() -> dict[str, Any]:
    """Run full MCP audit: servers + tools + risks."""
    servers = inventory_servers()
    tools = []
    risks = []
    warnings = []
    policy = _load_mcp_policy()

    for srv in servers:
        sid = srv["server_id"]
        cfg = _load_hermes_mcp_config().get(sid, {})

        # ── Resolve tool list (config or dynamic discovery) ──────────
        known_tools = _parse_tools_config(cfg.get("tools"))

        if not known_tools:
            # Dynamic discovery via MCP protocol
            transport = srv["transport"]
            if transport == "stdio":
                known_tools = _discover_stdio_tools(
                    cfg.get("command", ""),
                    cfg.get("args"),
                    cfg.get("env"),
                )
            elif transport == "http":
                known_tools = _discover_http_tools(cfg.get("url", ""))
            # Fallback: hardcoded well-known servers
            if not known_tools and sid == "obsidian-mcp-vault":
                known_tools = [
                    {"name": "read_note", "desc": "Read a note from the vault"},
                    {"name": "write_note", "desc": "Create or overwrite a note"},
                    {"name": "edit_note", "desc": "In-place edit of a note"},
                    {"name": "search_notes", "desc": "Search notes with filters"},
                    {"name": "delete_note", "desc": "Delete a note from the vault"},
                    {"name": "batch", "desc": "Execute multiple vault operations"},
                    {"name": "append_note", "desc": "Append content to a note"},
                    {"name": "move_note", "desc": "Move or rename a note"},
                ]

        for t in known_tools:
            tname = t.get("name", t) if isinstance(t, dict) else t
            if not isinstance(tname, str):
                continue
            tdesc = (
                t.get("desc", t.get("description", "")) if isinstance(t, dict) else ""
            )
            tool_audit = audit_tool(sid, tname, tdesc)

            # Apply mcp_policy: approved tools bypass blocking/approval
            if _is_approved(tool_audit["full_name"], sid, policy):
                tool_audit["blocked"] = False
                tool_audit["approval_required"] = False
                tool_audit["approved"] = True

            tools.append(tool_audit)
            srv["tools"].append(tool_audit)

            if tool_audit["risk"] in ("high", "critical"):
                risks.append(tool_audit)
            if tool_audit["injection_risk"] in ("medium", "high"):
                warnings.append(
                    {
                        "tool": tool_audit["full_name"],
                        "issue": f"metadata injection risk: {tool_audit['injection_risk']}",
                        "matches": tool_audit["injection_matches"],
                    }
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
