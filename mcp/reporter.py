"""Hermes Ops Kit — MCP Auditor Reporter.

Formatted output for MCP audit results.
"""

from __future__ import annotations

import json
from typing import Any


def fmt_audit(result: dict[str, Any], as_json: bool = False) -> str:
    """Format MCP audit result."""
    if as_json:
        return json.dumps(result, indent=2, default=str)

    lines = []
    servers = result.get("servers", [])
    risks = result.get("risks", [])
    warnings = result.get("warnings", [])
    status = "WARNING" if risks else "READY"

    lines.append(f"MCP AUDIT · {status}\n")

    # Servers
    if servers:
        lines.append("SERVERS")
        for s in servers:
            icon = "●" if s.get("enabled") else "○"
            tool_count = len(s.get("tools", []))
            lines.append(
                f"  {icon} {s['server_id']:<25s} {s['transport']:<8s} tools {tool_count}  risk {s.get('risk', '?')}"
            )
        lines.append("")

    # Risks
    if risks:
        lines.append("RISKS")
        for r in risks:
            reasons = ", ".join(r.get("reasons", []))
            if r.get("approved"):
                action = "approved ✓"
            elif r.get("blocked"):
                action = "blocked"
            else:
                action = "approval required"
            lines.append(f"  ⚠ {r['full_name']}  risk={r['risk']}  {action}")
            lines.append(f"    reasons: {reasons[:80]}")
        lines.append("")

    # Warnings
    if warnings:
        for w in warnings:
            lines.append(f"  ⚠ {w['tool']}: {w['issue']}")
        lines.append("")

    if not risks and not warnings:
        lines.append("  ✓ No risks detected")

    lines.append("")
    lines.append("NEXT")
    lines.append("  hermes-ops-kit mcp list")
    lines.append("  hermes-ops-kit mcp risks")

    return "\n".join(lines)
