"""Hermes Ops Kit — Operational and Security Plugin for Hermes Agent.

Provider routing, secret and key lifecycle, preflight plugin scanning,
MCP auditing, cost governance, diagnostics, and remote assistant delegation.

Install as a Hermes plugin or standalone Python package.
"""

from pathlib import Path

import argparse
import os
from . import ops_config_io  # noqa: E402

__version__ = "0.11.0"


def _ensure_image_gen_config() -> None:
    """Fill missing image defaults without replacing operator choices."""
    config_path = ops_config_io.hermes_config()
    if not os.path.isfile(config_path):
        return
    import logging
    try:
        config = ops_config_io.load_yaml_strict(config_path)
        section = config.setdefault("image_gen", {})
        if not isinstance(section, dict):
            raise ops_config_io.ConfigError("image_gen must be a mapping")
        if "provider" in section and "model" in section:
            return
        section.setdefault("provider", "ops-kit-router")
        section.setdefault("model", "auto")
        ops_config_io.save_yaml(config_path, config)
    except (ops_config_io.ConfigError, OSError) as exc:
        logging.getLogger(__name__).warning("Image defaults were not written: %s", exc)


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
        from .hooks import transform_tool_result, on_session_start

        ctx.register_hook("on_session_start", on_session_start)
        ctx.register_hook("transform_tool_result", transform_tool_result)
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
