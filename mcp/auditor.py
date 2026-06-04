"""Hermes Ops Kit — MCP Auditor.

Discovers configured MCP servers, inventories tools, classifies risks.
Reads from ~/.hermes/config.yaml for mcp_servers declarations and mcp_policy.
"""

from __future__ import annotations

import json
import os
import sys
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

        # Try to discover tools from config or known patterns
        known_tools = cfg.get("tools", [])
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
