#!/usr/bin/env python3
"""
Hermes Ops Kit — Main CLI Entry Point

Unified CLI for all AI provider wrappers. Routes to provider-specific adapters.

Usage:
    hermes-ops-kit invoke --provider openai --operation chat --prompt "..."
    hermes-ops-kit capabilities
    hermes-ops-kit health
"""

import argparse
import json
import subprocess
import sys
import os
import time

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROVIDERS = {
    "openai": os.path.join(BRIDGE_DIR, "providers", "openai_adapter.py"),
    "anthropic": os.path.join(BRIDGE_DIR, "providers", "claude_adapter.py"),
    "github": os.path.join(BRIDGE_DIR, "providers", "github_adapter.py"),
    "gemini": os.path.join(BRIDGE_DIR, "providers", "gemini_adapter.py"),
    "deepseek": os.path.join(BRIDGE_DIR, "providers", "deepseek_adapter.py"),
}

CAPABILITIES = {
    "openai": {
        "surfaces": ["api"],
        "operations": ["chat", "extract", "review", "models"],
        "requires_approval_for": [],
        "models": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"],
    },
    "anthropic": {
        "surfaces": ["api", "cli"],
        "operations": ["api_chat", "api_extract", "review", "analyze", "readonly"],
        "requires_approval_for": ["file_edit", "shell_execute"],
        "models": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
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
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-3.5-flash"],
    },
    "deepseek": {
        "surfaces": ["api"],
        "operations": ["chat", "extract", "review", "models"],
        "requires_approval_for": [],
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
    },
}


def cmd_health():
    """Health check endpoint."""
    print(
        json.dumps(
            {
                "status": "ok",
                "version": "0.1.0",
                "providers": {
                    p: os.path.exists(script) and os.access(script, os.X_OK)
                    for p, script in PROVIDERS.items()
                },
                "timestamp": int(time.time()),
            },
            indent=2,
        )
    )


def cmd_capabilities(provider: str | None = None):
    """Return capabilities for all or specific provider."""
    if provider:
        caps = CAPABILITIES.get(provider, {"error": f"Unknown provider: {provider}"})
        caps["provider"] = provider
        caps["security"] = {
            "uses_mcp_vault": True,
            "returns_raw_secrets": False,
            "redacts_stdout": True,
            "redacts_stderr": True,
        }
        print(json.dumps(caps, indent=2))
    else:
        result = {}
        for p, caps in CAPABILITIES.items():
            caps["provider"] = p
            caps["installed"] = os.path.exists(PROVIDERS.get(p, ""))
            caps["security"] = {
                "uses_mcp_vault": True,
                "returns_raw_secrets": False,
                "redacts_stdout": True,
                "redacts_stderr": True,
            }
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

    script = PROVIDERS[provider]
    if not os.path.exists(script):
        print(
            json.dumps({"ok": False, "error": f"Provider adapter not found: {script}"})
        )
        sys.exit(1)

    # Build command — use the same Python that runs this bridge
    cmd = [sys.executable, script, "--operation", operation]
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
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=kwargs.get("timeout", 120) + 10
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
    asst = sub.add_parser(
        "assistants", help="Manage remote assistants", aliases=["assistant"]
    )
    asst.add_argument(
        "assistant_action",
        nargs="?",
        default="list",
        choices=["list", "ping", "delegate"],
    )
    asst.add_argument("assistant_args", nargs="*", default=[])
    sub.add_parser("audit", help="Audit trail").add_argument(
        "audit_action", nargs="?", default="tail", choices=["tail", "search", "export"]
    )
    mcp = sub.add_parser("mcp", help="MCP tool auditor")
    mcp.add_argument(
        "mcp_action",
        nargs="?",
        default="audit",
        choices=["audit", "list", "risks", "export", "approve", "revoke", "policy"],
    )
    mcp.add_argument("mcp_args", nargs=argparse.REMAINDER, default=[])
    sub.add_parser("budget", help="Cost governor").add_argument(
        "budget_action",
        nargs="?",
        default="status",
        choices=["status", "check-route", "policy"],
    )
    sub.add_parser("maintenance", help="Assistant task scheduler").add_argument(
        "maintenance_action", nargs="?", default="profiles", choices=["profiles", "run"]
    )
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

    args = parser.parse_args()

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
        "assistants",
        "assistant",
        "audit",
        "mcp",
        "budget",
        "maintenance",
        "image",
        "route-test",
    ):
        # Delegate to commands.py plugin handler
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from commands import handle_ops_kit_command  # pyright: ignore[reportMissingImports]

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
        }
        attr = action_map.get(args.command, "")
        if attr and hasattr(args, attr):
            cmd_args.append(getattr(args, attr, ""))
        if args.command in ("assistants", "assistant"):
            cmd_args.extend(getattr(args, "assistant_args", []))
        if args.command == "mcp":
            cmd_args.extend(getattr(args, "mcp_args", []))
        if args.command == "image":
            cmd_args.extend(getattr(args, "image_args", []))
        if args.command == "route-test":
            if getattr(args, "fallback", False):
                cmd_args.append("--fallback")
            if getattr(args, "json", False):
                cmd_args.append("--json")
        sys.exit(handle_ops_kit_command(cmd_args))


if __name__ == "__main__":
    main()
