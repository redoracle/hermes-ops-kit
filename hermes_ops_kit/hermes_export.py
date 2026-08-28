#!/usr/bin/env python3
"""Hermes Ops Kit — Export Center.

Structured exports of bridge data: usage reports, security briefings,
audit logs, and task reports. Output to ~/.hermes/cache/documents/.

Usage:
    hermes-export report usage [--format md|json|html]
    hermes-export report security --assistant assistant-id
    hermes-export contact-briefing person "John Doe" [--format md]
    hermes-export contact-briefing company "Acme Corp"
    hermes-export audit --since 7d [--format json]
    hermes-export list
"""

from __future__ import annotations


if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )

import argparse
import json
import os
import time
from datetime import datetime, timedelta

from ._subprocess import run_module
from hermes_ops_kit import ops_config_io  # noqa: E402

EXPORT_DIR = os.path.join(ops_config_io.HERMES_HOME, "cache/documents")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def _ensure_dir() -> str:
    os.makedirs(EXPORT_DIR, exist_ok=True)
    return EXPORT_DIR


def _write_file(filename: str, content: str) -> str:
    path = os.path.join(_ensure_dir(), filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def _run_bridge_script(script: str, *args: str) -> dict:
    module = script.removesuffix(".py").replace("/", ".").replace("-", "_")
    r = run_module(
        module,
        list(args) + ["--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": "JSON parse failed", "stdout": r.stdout[:500]}


# ── Report: Usage ─────────────────────────────────────────────────


def export_usage_report(fmt: str = "md") -> str:
    data = _run_bridge_script("usage_metrics_v2.py")

    if fmt == "json":
        return _write_file("hermes-usage-report.json", json.dumps(data, indent=2))

    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    lines = [
        "# Hermes Ops Kit — Usage Report",
        f"**Generated:** {ts}",
        "",
        "## Routes",
    ]

    # Providers
    for provider in ["github", "gemini", "openai", "anthropic", "deepseek"]:
        pd = data.get(provider, {})
        status = pd.get("status", "unknown")
        latency = pd.get("api_latency_ms", "?")
        lines.append(f"- **{provider}:** {status} ({latency}ms)")

    # Assistants
    asst = data.get("_assistants", {})
    if asst:
        lines.append("")
        lines.append("## Assistants")
        for aid, ad in asst.items():
            lines.append(
                f"- **{aid}:** {ad.get('status', '?')} ({ad.get('display_name', aid)})"
            )

    # Warnings
    warnings = data.get("_warnings", data.get("warnings", []))
    if warnings:
        lines.append("")
        lines.append("## Warnings")
        for w in warnings:
            lines.append(f"- ⚠ {w}")

    content = "\n".join(lines) + "\n"
    if fmt == "html":
        content = f"<html><body><pre>{content}</pre></body></html>"
        return _write_file("hermes-usage-report.html", content)
    return _write_file("hermes-usage-report.md", content)


# ── Report: Security ──────────────────────────────────────────────


def export_security_report(assistant: str = "generic") -> str:
    data = _run_bridge_script(
        "hermes-assistant-manager.py",
        "--config",
        os.path.join(PROJECT_DIR, "config", "assistants.yaml"),
        "get",
        assistant,
    )
    asst_data = data.get("assistant", data.get("result", {}))

    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    lines = [
        f"# Security Report — {assistant}",
        f"**Generated:** {ts}",
        "",
        f"## Assistant: {asst_data.get('display_name', assistant)}",
        f"- Role: {asst_data.get('role', '?')}",
        f"- Transport: {asst_data.get('transport', '?')}",
        f"- Enabled: {asst_data.get('enabled', False)}",
        "",
        "## Capabilities",
    ]
    for cap in asst_data.get("capabilities", []):
        cid = cap.get("id", cap) if isinstance(cap, dict) else cap
        safe = "✅" if (isinstance(cap, dict) and cap.get("safe_by_default")) else "❌"
        lines.append(f"- {safe} {cid}")

    lines.append("")
    lines.append("## Blocked")
    for b in asst_data.get("blocked_capabilities", []):
        lines.append(f"- ❌ {b}")

    lines.append("")
    lines.append("## Security Policy")
    sec = asst_data.get("security", {})
    for k, v in sec.items():
        if isinstance(v, bool):
            lines.append(f"- {k}: {'✅' if v else '❌'}")

    content = "\n".join(lines) + "\n"
    return _write_file(f"hermes-security-report-{assistant}.md", content)


# ── Vault Briefing ────────────────────────────────────────────────


def export_contact_briefing(entity_type: str, entity_name: str, fmt: str = "md") -> str:
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    slug = entity_name.lower().replace(" ", "-")

    lines = [
        f"# Vault Briefing — {entity_name}",
        f"**Type:** {entity_type}",
        f"**Generated:** {ts}",
        "",
        "## Summary",
        f"Briefing for {entity_type}: **{entity_name}**.",
        "",
        "## Request Profile",
        "Run the following to delegate to Assistant Profiler:",
        "",
        "```python",
        "from assistants.tool import ai_assistant_delegate",
        "",
        "result = ai_assistant_delegate(",
        '    "generic",',
        f'    task="Prepare briefing for {entity_type}: {entity_name}",',
        '    capability="profile_update",',
        f'    context={{"caller": "hermes-agent", "entity_type": "{entity_type}", "entity_name": "{entity_name}"}},',
        ")",
        "```",
        "",
        "## Assistant Vault Paths",
    ]

    content = "\n".join(lines) + "\n"
    return _write_file(f"hermes-briefing-{slug}.md", content)


# ── Audit Export ──────────────────────────────────────────────────


def export_audit(since_days: int = 7, fmt: str = "json") -> str:
    cutoff = (datetime.now(datetime.UTC) - timedelta(days=since_days)).strftime(
        "%Y-%m-%d"
    )  # pyright: ignore[reportAttributeAccessIssue]

    try:
        from .audit.ledger import search_events  # pyright: ignore[reportMissingImports]

        events = search_events(since=cutoff, limit=500)
    except Exception:
        events = []

    if fmt == "json":
        return _write_file(
            f"hermes-audit-{since_days}d.json",
            json.dumps(events, indent=2, default=str),
        )

    lines = [
        f"# Audit Export — Last {since_days} Days",
        f"**Events:** {len(events)}",
        "",
        "| Time | Type | Details |",
        "|------|------|---------|",
    ]
    for evt in events:
        ts = evt.get("ts", "?")[:19]
        etype = evt.get("type", "?")
        details = ", ".join(
            f"{k}={v}" for k, v in evt.items() if k not in ("ts", "type")
        )[:80]
        lines.append(f"| {ts} | {etype} | {details} |")

    return _write_file(f"hermes-audit-{since_days}d.md", "\n".join(lines) + "\n")


def cmd_list() -> None:
    """List previously generated exports."""
    if not os.path.exists(EXPORT_DIR):
        print("No exports yet")
        return
    files = sorted(os.listdir(EXPORT_DIR), reverse=True)
    if not files:
        print("No exports yet")
        return
    print(f"EXPORTS · {EXPORT_DIR}\n")
    for f in files[:20]:
        path = os.path.join(EXPORT_DIR, f)
        size = os.path.getsize(path)
        print(f"  {f:<50s} {size:>6d} bytes")


# ─── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Ops Kit — Export Center")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # report
    report = sub.add_parser("report")
    report.add_argument("report_type", choices=["usage", "security"])
    report.add_argument("--format", choices=["md", "json", "html"], default="md")
    report.add_argument("--assistant", default="generic")

    # contact-briefing
    briefing = sub.add_parser("contact-briefing")
    briefing.add_argument("entity_type", choices=["person", "company"])
    briefing.add_argument("entity_name")
    briefing.add_argument("--format", choices=["md", "json"], default="md")

    # audit
    audit = sub.add_parser("audit")
    audit.add_argument("--since", default="7d")
    audit.add_argument("--format", choices=["md", "json"], default="json")

    # list
    sub.add_parser("list")

    args = parser.parse_args()

    if args.command == "report":
        if args.report_type == "usage":
            path = export_usage_report(args.format)
        else:
            path = export_security_report(args.assistant)
    elif args.command == "contact-briefing":
        path = export_contact_briefing(args.entity_type, args.entity_name, args.format)
    elif args.command == "audit":
        days = int(args.since.replace("d", "")) if args.since.endswith("d") else 7
        path = export_audit(days, args.format)
    elif args.command == "list":
        cmd_list()
        return

    print(f"MEDIA:{path}")


if __name__ == "__main__":
    main()
