"""Hermes Ops Kit — Portable Hermes Plugin.

Multi-provider AI orchestration with key rotation, usage metrics,
and remote assistant delegation — backed by Vaultwarden secrets.

Install as a Hermes plugin or standalone Python package.
"""

from pathlib import Path

# ── sys.path priming ────────────────────────────────────────────────
# Hermes loads plugins via importlib without adding the plugin directory
# to sys.path.  Ops-kit uses absolute intra-package imports (e.g.
# ``from security.redaction import redact``) throughout — those need
# the ops-kit directory on sys.path to resolve.  Prime it at module
# level so it's in effect before ``register()`` imports ``tools`` et al.
import os as _os
import sys as _sys

_OPS_KIT_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _OPS_KIT_DIR not in _sys.path:
    _sys.path.insert(0, _OPS_KIT_DIR)


def _ensure_image_gen_config() -> None:
    """Auto-configure image_gen.provider/model in ~/.hermes/config.yaml.

    Called once per plugin load, only writes when the keys are missing
    (idempotent on re-run).  Uses a safe line-edit so existing YAML
    formatting is preserved.
    """
    config_path = _os.path.expanduser("~/.hermes/config.yaml")
    if not _os.path.isfile(config_path):
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
        _os.chmod(tmp, 0o600)
        _os.replace(tmp, config_path)
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
