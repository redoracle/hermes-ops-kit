#!/usr/bin/env python3
"""
Hermes Ops Kit — Main CLI Entry Point

Unified CLI for all AI provider wrappers. Routes to provider-specific adapters.

Usage:
    hermes-ops-kit invoke --provider openai --operation chat --prompt "..."
    hermes-ops-kit capabilities
    hermes-ops-kit health
"""

if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )

import argparse
import json
import subprocess
import sys
import time

from ._subprocess import module_file, run_module
from .provider_catalog import PROVIDER_MODELS

PROVIDERS = {
    "openai": "providers.openai_adapter",
    "anthropic": "providers.claude_adapter",
    "github": "providers.github_adapter",
    "gemini": "providers.gemini_adapter",
    "deepseek": "providers.deepseek_adapter",
    "nvidia": "providers.nvidia_adapter",
    "fireworks": "providers.fireworks_adapter",
    "deepinfra": "providers.deepinfra_adapter",
}

CAPABILITIES = {
    "openai": {
        "surfaces": ["api"],
        "operations": ["chat", "extract", "review", "models"],
        "requires_approval_for": [],
        "models": list(PROVIDER_MODELS['openai']),
    },
    "anthropic": {
        "surfaces": ["api", "cli"],
        "operations": ["api_chat", "api_extract", "review", "analyze", "readonly"],
        "requires_approval_for": ["file_edit", "shell_execute"],
        "models": list(PROVIDER_MODELS['anthropic']),
    },
    "github": {
        "surfaces": ["cli"],
        "operations": [
            "pr_list",
            "pr_view",
            "pr_diff",
            "issue_list",
            "ci_status",
            "search_code",
            "read_file",
        ],
        "requires_approval_for": ["pr_create", "pr_merge", "issue_create"],
        "models": [],
    },
    "gemini": {
        "surfaces": ["api", "cli"],
        "operations": ["generate", "grounded", "cli_plan", "models"],
        "requires_approval_for": [],
        "models": list(PROVIDER_MODELS['gemini']),
    },
    "deepseek": {
        "surfaces": ["api"],
        "operations": ["chat", "extract", "review", "models"],
        "requires_approval_for": [],
        "models": list(PROVIDER_MODELS['deepseek']),
    },
    "nvidia": {
        "surfaces": ["api"],
        "operations": ["chat", "extract", "review", "models"],
        "requires_approval_for": [],
        "models": list(PROVIDER_MODELS['nvidia']),
    },
    "fireworks": {
        "surfaces": ["api"],
        "operations": ["chat", "extract", "review", "models"],
        "requires_approval_for": [],
        "models": list(PROVIDER_MODELS['fireworks']),
    },
    "deepinfra": {
        "surfaces": ["api"],
        "operations": ["chat", "extract", "review", "models"],
        "requires_approval_for": [],
        "models": list(PROVIDER_MODELS['deepinfra']),
    },
}


def cmd_health():
    """Health check endpoint."""
    print(
        json.dumps(
            {
                "status": "ok",
                "version": __import__("hermes_ops_kit").__version__,
                "providers": {
                    p: module_file(module).is_file() for p, module in PROVIDERS.items()
                },
                "timestamp": int(time.time()),
            },
            indent=2,
        )
    )


_SECURITY_CAPABILITIES = {
    "uses_mcp_vault": True,
    "returns_raw_secrets": False,
    "redacts_stdout": True,
    "redacts_stderr": True,
}


def cmd_capabilities(provider: str | None = None):
    """Return capabilities for all or specific provider."""
    if provider:
        caps = CAPABILITIES.get(provider, {"error": f"Unknown provider: {provider}"})
        caps["provider"] = provider
        caps["security"] = dict(_SECURITY_CAPABILITIES)
        print(json.dumps(caps, indent=2))
    else:
        result = {}
        for p, caps in CAPABILITIES.items():
            caps["provider"] = p
            caps["installed"] = module_file(PROVIDERS.get(p, "")).is_file()
            caps["security"] = dict(_SECURITY_CAPABILITIES)
            result[p] = caps
        print(json.dumps(result, indent=2))


def cmd_invoke(provider: str, operation: str, **kwargs):
    """Invoke a provider operation."""
    if provider not in PROVIDERS:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Unknown provider: {provider}. Available: {list(PROVIDERS.keys())}",
                }
            )
        )
        sys.exit(1)

    adapter = PROVIDERS[provider]
    adapter_file = module_file(adapter)
    if not adapter_file.is_file():
        print(
            json.dumps(
                {"ok": False, "error": f"Provider adapter not found: {adapter_file}"}
            )
        )
        sys.exit(1)

    # Build command — run the adapter as a package module (-P: cwd off sys.path)
    cmd = ["--operation", operation]
    # Adapter-common args
    common_args = {
        "prompt",
        "model",
        "max_tokens",
        "system",
        "schema",
        "files",
        "repo",
        "pr",
        "limit",
        "state",
        "query",
        "path",
        "title",
        "body",
        "base",
        "head",
        "ref",
        "require_approval",
    }
    # workdir only for CLI providers
    if provider in ("anthropic", "gemini"):
        common_args.add("workdir")
    for key, value in kwargs.items():
        if value is not None and value is not False and key in common_args:
            k = key.replace("_", "-")
            cmd.extend([f"--{k}", str(value)])

    start = time.time()
    try:
        result = run_module(
            adapter,
            cmd,
            capture_output=True,
            text=True,
            timeout=kwargs.get("timeout", 120) + 10,
        )
    except subprocess.TimeoutExpired:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Bridge invocation timed out",
                    "provider": provider,
                }
            )
        )
        sys.exit(1)

    # Pass through provider output
    try:
        parsed = json.loads(result.stdout)
        parsed["bridge_duration_ms"] = int((time.time() - start) * 1000)
        print(json.dumps(parsed, indent=2))
    except json.JSONDecodeError:
        print(result.stdout or result.stderr)
    sys.exit(result.returncode)


def main():
    # Load ~/.hermes/.env(+.env.generated) once for every subcommand so
    # provider adapters (invoke) see credentials regardless of the ambient
    # shell; previously only the assistants path loaded dotenv.
    try:
        from .env.loader import load_dotenv  # pyright: ignore[reportMissingImports]

        load_dotenv()
    except Exception:
        pass
    parser = argparse.ArgumentParser(
        description="Hermes Ops Kit — Multi-Provider CLI Wrapper"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # health
    sub.add_parser("health", help="Health check")

    # capabilities
    caps = sub.add_parser("capabilities", help="List provider capabilities")
    caps.add_argument("--provider", choices=list(PROVIDERS.keys()), default=None)

    # invoke
    invoke = sub.add_parser("invoke", help="Invoke a provider operation")
    invoke.add_argument("--provider", required=True, choices=list(PROVIDERS.keys()))
    invoke.add_argument("--operation", required=True)
    invoke.add_argument("--prompt", default=None)
    invoke.add_argument("--model", default=None)
    invoke.add_argument("--max_tokens", type=int, default=None)
    invoke.add_argument("--system", default=None)
    invoke.add_argument("--schema", default=None)
    invoke.add_argument("--files", default=None)
    invoke.add_argument("--workdir", default=".")
    invoke.add_argument("--repo", default=None)
    invoke.add_argument("--pr", default=None)
    invoke.add_argument("--limit", type=int, default=None)
    invoke.add_argument("--state", default=None)
    invoke.add_argument("--query", default=None)
    invoke.add_argument("--path", default=None)
    invoke.add_argument("--timeout", type=int, default=120)

    # ── New plugin commands ──
    sub.add_parser("doctor", help="Full system diagnostic")
    sub.add_parser("status", help="Quick health overview")
    usage_p = sub.add_parser("usage", help="Usage metrics (args passed through)")
    usage_p.add_argument("usage_args", nargs=argparse.REMAINDER, default=[])
    rot = sub.add_parser("rotate", help="Key rotation (args passed through)")
    rot.add_argument("rotate_args", nargs=argparse.REMAINDER, default=[])
    asst = sub.add_parser(
        "assistants", help="Manage remote assistants", aliases=["assistant"]
    )
    asst.add_argument(
        "assistant_action",
        nargs="?",
        default="list",
        choices=["list", "ping", "delegate"],
    )
    asst.add_argument("assistant_args", nargs=argparse.REMAINDER, default=[])
    audit = sub.add_parser("audit", help="Audit trail")
    audit.add_argument(
        "audit_action", nargs="?", default="tail", choices=["tail", "search", "export"]
    )
    audit.add_argument("audit_args", nargs=argparse.REMAINDER, default=[])
    mcp = sub.add_parser(
        "mcp",
        help="MCP tool security auditor",
        description="Discover, audit, and approve MCP server tools with risk classification.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  hermes-ops-kit mcp                       # full audit\n"
            "  hermes-ops-kit mcp list                  # compact server summary\n"
            "  hermes-ops-kit mcp tools                 # all tools, all servers\n"
            "  hermes-ops-kit mcp tools --server n8n    # tools for one server\n"
            "  hermes-ops-kit mcp risks                 # medium+ risk summary\n"
            "  hermes-ops-kit mcp approve --server n8n  # whitelist all tools\n"
            "  hermes-ops-kit mcp approve --all          # whitelist everything\n"
            "  hermes-ops-kit mcp revoke                 # clear all approvals\n"
            "  hermes-ops-kit mcp policy                 # show current policy\n"
            "  hermes-ops-kit mcp export                 # machine-readable JSON"
        ),
    )
    mcp.add_argument(
        "mcp_action",
        nargs="?",
        default="audit",
        choices=[
            "audit",
            "list",
            "tools",
            "risks",
            "export",
            "approve",
            "revoke",
            "policy",
        ],
    )
    mcp.add_argument("mcp_args", nargs=argparse.REMAINDER, default=[])
    budget = sub.add_parser("budget", help="Cost governor")
    budget.add_argument(
        "budget_action",
        nargs="?",
        default="status",
        choices=["status", "check-route", "policy"],
    )
    budget.add_argument("budget_args", nargs=argparse.REMAINDER, default=[])
    maintenance = sub.add_parser("maintenance", help="Assistant task scheduler")
    maintenance.add_argument(
        "maintenance_action", nargs="?", default="profiles", choices=["profiles", "run"]
    )
    maintenance.add_argument("maintenance_args", nargs=argparse.REMAINDER, default=[])
    img = sub.add_parser("image", help="Image generation route manager")
    img.add_argument(
        "image_action",
        nargs="?",
        default="routes",
        choices=["routes", "doctor", "test", "set-default", "set-route", "export"],
    )
    img.add_argument("image_args", nargs=argparse.REMAINDER, default=[])
    rt = sub.add_parser("route-test", help="Deterministic route verification")
    rt.add_argument("--fallback", action="store_true", help="Fallback chain cascade")
    rt.add_argument("--json", action="store_true", help="JSON output")

    hr = sub.add_parser("headroom", help="Headroom proxy route overlay")
    hr.add_argument(
        "headroom_args",
        nargs=argparse.REMAINDER,
        default=[],
        help="status, doctor, up, down, enable, disable, reconcile, stats, export",
    )

    install = sub.add_parser("install", help="First-install setup and repair")
    install.add_argument(
        "install_args",
        nargs=argparse.REMAINDER,
        default=[],
        help="setup, doctor, or repair arguments",
    )

    # Plugin security scanner
    pl = sub.add_parser("plugin", help="Plugin security scanner")
    pl.add_argument(
        "plugin_action",
        nargs="?",
        default="scan",
        choices=[
            "scan",
            "policy",
            "approve",
            "revoke",
            "override",
            "disable",
            "enable",
            "block",
            "cache",
            "rules",
        ],
    )
    pl.add_argument("plugin_args", nargs=argparse.REMAINDER, default=[])

    # Plugin security preflight
    pf = sub.add_parser("preflight", help="Scan + enforce plugin security before boot")
    pf.add_argument(
        "--dry-run", action="store_true", help="Preview without modifying config"
    )
    pf.add_argument("--json", action="store_true", help="Machine-readable output")
    pf.add_argument(
        "--force", action="store_true", help="Force fresh scan (skip cache)"
    )

    # Routes / Profile manager
    routes_p = sub.add_parser("routes", help="LLM route and profile manager")
    routes_p.add_argument("routes_args", nargs=argparse.REMAINDER, default=[])
    sub.add_parser("route-manager", help="Alias for routes").add_argument(
        "routes_args", nargs=argparse.REMAINDER, default=[]
    )

    # Skill factory
    skill_p = sub.add_parser("skill", help="Generate and validate skills")
    skill_p.add_argument("skill_args", nargs=argparse.REMAINDER, default=[])
    sub.add_parser("skill-factory", help="Alias for skill").add_argument(
        "skill_args", nargs=argparse.REMAINDER, default=[]
    )
    sub.add_parser("skills", help="Alias for skill").add_argument(
        "skill_args", nargs=argparse.REMAINDER, default=[]
    )

    # Export center
    export_p = sub.add_parser("export", help="Export reports and audit logs")
    export_p.add_argument("export_args", nargs=argparse.REMAINDER, default=[])

    # Delegated subcommands accept passthrough flags (usage/rotate/mcp evolve
    # independently of this dispatcher); native commands stay strict.
    args, unknown = parser.parse_known_args()
    if unknown and args.command not in (
        "doctor", "status", "usage", "rotate", "assistants", "assistant",
        "audit", "mcp", "budget", "maintenance", "image", "headroom",
        "install", "route-test", "preflight", "plugin",
        "routes", "route-manager", "skill", "skill-factory", "skills", "export",
    ):
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")

    if args.command == "health":
        cmd_health()
    elif args.command == "capabilities":
        cmd_capabilities(args.provider if hasattr(args, "provider") else None)
    elif args.command == "invoke":
        kwargs = {
            k: v
            for k, v in vars(args).items()
            if k not in ("command", "provider", "operation") and v is not None
        }
        cmd_invoke(args.provider, args.operation, **kwargs)
    elif args.command in (
        "doctor",
        "status",
        "usage",
        "rotate",
        "assistants",
        "assistant",
        "audit",
        "mcp",
        "budget",
        "maintenance",
        "image",
        "headroom",
        "install",
        "route-test",
        "preflight",
        "plugin",
        "routes",
        "route-manager",
        "skill",
        "skill-factory",
        "skills",
        "export",
    ):
        # Delegate to commands.py plugin handler
        from .commands import handle_ops_kit_command  # pyright: ignore[reportMissingImports]

        cmd_args = [args.command]
        # Map command to its action arg name (not always f"{cmd}_action")
        action_map = {
            "assistants": "assistant_action",
            "assistant": "assistant_action",
            "audit": "audit_action",
            "mcp": "mcp_action",
            "budget": "budget_action",
            "maintenance": "maintenance_action",
            "image": "image_action",
            "plugin": "plugin_action",
        }
        attr = action_map.get(args.command, "")
        if attr and hasattr(args, attr):
            action_val = getattr(args, attr, "")
            if action_val:
                cmd_args.append(action_val)
        if args.command in ("assistants", "assistant"):
            cmd_args.extend(getattr(args, "assistant_args", []))
        if args.command in ("routes", "route-manager"):
            cmd_args.extend(getattr(args, "routes_args", []))
        if args.command in ("skill", "skill-factory", "skills"):
            cmd_args.extend(getattr(args, "skill_args", []))
        if args.command == "export":
            cmd_args.extend(getattr(args, "export_args", []))
        if args.command == "budget":
            cmd_args.extend(getattr(args, "budget_args", []))
        if args.command == "maintenance":
            cmd_args.extend(getattr(args, "maintenance_args", []))
        if args.command == "audit":
            cmd_args.extend(getattr(args, "audit_args", []))
        if args.command == "mcp":
            cmd_args.extend(getattr(args, "mcp_args", []))
        if args.command == "plugin":
            cmd_args.extend(getattr(args, "plugin_args", []))
        if args.command == "rotate":
            cmd_args.extend(getattr(args, "rotate_args", []))
        if args.command == "usage":
            cmd_args.extend(getattr(args, "usage_args", []))
        if args.command == "image":
            cmd_args.extend(getattr(args, "image_args", []))
        if args.command == "headroom":
            cmd_args.extend(getattr(args, "headroom_args", []))
        if args.command == "install":
            cmd_args.extend(getattr(args, "install_args", []))
        if args.command == "route-test":
            if getattr(args, "fallback", False):
                cmd_args.append("--fallback")
            if getattr(args, "json", False):
                cmd_args.append("--json")
        if args.command == "preflight":
            if getattr(args, "dry_run", False):
                cmd_args.append("--dry-run")
            if getattr(args, "json", False):
                cmd_args.append("--json")
            if getattr(args, "force", False):
                cmd_args.append("--force")
        cmd_args.extend(unknown)
        sys.exit(handle_ops_kit_command(cmd_args))


if __name__ == "__main__":
    main()
