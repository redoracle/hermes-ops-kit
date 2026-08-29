"""Hermes Ops Kit — Operational and Security Plugin for Hermes Agent.

Provider routing, secret and key lifecycle, preflight plugin scanning,
MCP auditing, cost governance, diagnostics, and remote assistant delegation.

Install as a Hermes plugin or standalone Python package.
"""

from pathlib import Path

import argparse
import os
from hermes_ops_kit import ops_config_io  # noqa: E402

__version__ = "0.5.8"


def _ensure_image_gen_config() -> None:
    """Auto-configure image_gen.provider/model in ~/.hermes/config.yaml.

    Called once per plugin load, only writes when the keys are missing
    (idempotent on re-run).  Uses a safe line-edit so existing YAML
    formatting is preserved.
    """
    config_path = os.path.join(ops_config_io.HERMES_HOME, "config.yaml")
    if not os.path.isfile(config_path):
        return

    try:
        with open(config_path, "r") as f:
            lines = f.readlines()
    except Exception:
        return

    image_gen_idx: int | None = None
    block_end_idx: int | None = None
    has_provider = False
    has_model = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "image_gen:":
            image_gen_idx = i
            continue
        if image_gen_idx is not None:
            indent = len(line) - len(line.lstrip())
            if indent == 0 and stripped:
                # Next top-level key — block ended at line i
                block_end_idx = i
                break
            if "provider:" in stripped:
                has_provider = True
            if "model:" in stripped:
                has_model = True

    # Both present — nothing to do
    if image_gen_idx is not None and has_provider and has_model:
        return

    # Build the two lines to inject
    inject_lines = []
    if not has_provider:
        inject_lines.append("  provider: ops-kit-router\n")
    if not has_model:
        inject_lines.append("  model: auto\n")

    if image_gen_idx is not None:
        # Insert inside the existing image_gen: block
        insert_at = block_end_idx if block_end_idx is not None else len(lines)
        for j, il in enumerate(inject_lines):
            lines.insert(insert_at + j, il)
    else:
        # No image_gen: block — append at end
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append("image_gen:\n")
        lines.extend(inject_lines)

    try:
        tmp = config_path + ".tmp"
        with open(tmp, "w") as f:
            f.writelines(lines)
        os.chmod(tmp, 0o600)
        os.replace(tmp, config_path)
    except Exception:
        pass  # config write failed — user can set manually


def register(ctx):
    """Register all tools, CLI commands, hooks, and skills with Hermes.

    Called by the Hermes plugin loader at startup.
    Gracefully degrades if any optional dependency is missing.
    """
    # ── Tools ──
    from . import schemas, tools

    tool_registry = [
        ("ai_provider_invoke", schemas.AI_PROVIDER_INVOKE, tools.ai_provider_invoke),
        ("ai_bridge_invoke", schemas.AI_BRIDGE_INVOKE, tools.ai_bridge_invoke),
        ("ai_image_generate", schemas.AI_IMAGE_GENERATE, tools.ai_image_generate),
        (
            "ai_assistant_delegate",
            schemas.AI_ASSISTANT_DELEGATE,
            tools.ai_assistant_delegate,
        ),
        ("ai_usage_metrics", schemas.AI_USAGE_METRICS, tools.ai_usage_metrics),
        ("ai_key_rotate", schemas.AI_KEY_ROTATE, tools.ai_key_rotate),
        (
            "ai_secret_backend_status",
            schemas.AI_SECRET_BACKEND_STATUS,
            tools.ai_secret_backend_status,
        ),
    ]

    for name, schema, handler in tool_registry:
        try:
            ctx.register_tool(
                name=name,
                toolset="hermes-ops-kit",
                schema=schema,
                handler=handler,
            )
        except Exception:
            pass  # tool already registered or Hermes API mismatch

    # ── CLI subcommand ──
    try:
        from .commands import handle_ops_kit_command

        def _ops_kit_cli_setup(parser) -> None:
            """Argparse setup for the ``hermes ops-kit`` subcommand (v0.20 API)."""
            parser.add_argument(
                "rest",
                nargs=argparse.REMAINDER,
                help="Subcommand and arguments passed to hermes-ops-kit",
            )

        def _ops_kit_cli_handler(args) -> int:
            """Dispatch ``hermes ops-kit <subcommand>`` to the ops-kit CLI."""
            rest = list(getattr(args, "rest", None) or [])
            return handle_ops_kit_command(rest)

        try:
            # Hermes Agent v0.20+: setup_fn(subparser) + handler_fn(namespace)
            ctx.register_cli_command(
                "ops-kit",
                "Hermes Ops Kit controls",
                _ops_kit_cli_setup,
                _ops_kit_cli_handler,
            )
        except TypeError:
            # Older Hermes: register_cli_command(name, handler)
            ctx.register_cli_command("ops-kit", handle_ops_kit_command)

        def _ops_kit_slash_handler(raw_args) -> str | None:
            """Slash-command adapter: core passes a raw string, ops-kit wants a list."""
            if isinstance(raw_args, str):
                rest = raw_args.split()
            else:
                rest = list(raw_args or [])
            handle_ops_kit_command(rest)
            return None

        ctx.register_command(
            "ops-kit",
            _ops_kit_slash_handler,
            description="Hermes Ops Kit controls",
        )
    except Exception:
        pass

    # ── Hooks ──
    try:
        from .hooks import on_post_tool_call, on_session_start

        ctx.register_hook("on_session_start", on_session_start)
        ctx.register_hook("post_tool_call", on_post_tool_call)
    except Exception:
        pass

    # ── Image Gen Provider ──
    try:
        from .image_routes.hermes_provider import OpsKitRouterProvider

        ctx.register_image_gen_provider(OpsKitRouterProvider())
        _ensure_image_gen_config()
    except Exception:
        pass  # not inside Hermes runtime, or provider API changed

    # ── Skill ──
    skills_dir = Path(__file__).parent / "skills" / "hermes-ops-kit"
    skill_path = skills_dir / "SKILL.md"
    if skill_path.exists():
        try:
            ctx.register_skill("hermes-ops-kit", skill_path)
        except Exception:
            pass
