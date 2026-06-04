"""Hermes Ops Kit — Portable Hermes Plugin.

Multi-provider AI orchestration with key rotation, usage metrics,
and remote assistant delegation — backed by Vaultwarden secrets.

Install as a Hermes plugin or standalone Python package.
"""

from pathlib import Path


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

        ctx.register_cli_command("ops-kit", handle_ops_kit_command)
        ctx.register_command(
            "ops-kit", handle_ops_kit_command, help="Hermes Ops Kit controls"
        )
    except Exception:
        pass

    # ── Hooks ──
    try:
        from .hooks import on_startup, on_post_tool_call

        ctx.register_hook("startup", on_startup)
        ctx.register_hook("post_tool_call", on_post_tool_call)
    except Exception:
        pass

    # ── Skill ──
    skills_dir = Path(__file__).parent / "skills" / "hermes-ops-kit"
    skill_path = skills_dir / "SKILL.md"
    if skill_path.exists():
        try:
            ctx.register_skill("hermes-ops-kit", skill_path)
        except Exception:
            pass
