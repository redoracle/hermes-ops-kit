"""Hermes Ops Kit — Plugin Tool Handlers.

Thin wrappers that adapt existing bridge functions to the Hermes
plugin tool interface. All handlers:
  - accept args: dict
  - accept **kwargs (for future Hermes API compatibility)
  - return JSON string
  - catch exceptions
  - never print or return raw secrets
"""

from __future__ import annotations

import json

from security.redaction import redact  # pyright: ignore[reportMissingImports]


def ai_provider_invoke(args: dict, **kwargs) -> str:
    """Invoke an AI provider through Hermes Ops Kit (runs bridge.py as a subprocess)."""
    try:
        import subprocess, sys, os

        provider = args["provider"]
        operation = args["operation"]
        prompt = args["prompt"]
        model = args.get("model", "")

        script_dir = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(script_dir, "bridge.py")

        cmd = [
            sys.executable,
            script,
            "invoke",
            "--provider",
            provider,
            "--operation",
            operation,
            "--prompt",
            prompt,
        ]
        if model:
            cmd.extend(["--model", model])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return redact(result.stdout)
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "tool": "ai_provider_invoke",
                "error": redact(str(exc)),
            },
            ensure_ascii=False,
        )


def ai_bridge_invoke(args: dict, **kwargs) -> str:
    """Deprecated compatibility alias for ai_provider_invoke."""
    return ai_provider_invoke(args, **kwargs)


def ai_image_generate(args: dict, **kwargs) -> str:
    """Generate images through the Hermes Ops Kit image router."""
    try:
        from image_routes.router import generate, route_for_provider  # pyright: ignore[reportMissingImports]

        route_name = args.get("route")
        provider = args.get("provider")
        if not route_name and provider:
            route_name = route_for_provider(provider)

        result = generate(
            prompt=args["prompt"],
            route_name=route_name,
            aspect_ratio=args.get("aspect_ratio", "landscape"),
            num_images=args.get("num_images", 1),
            image_path=args.get("image_path"),
            model=args.get("model"),
            edit_mode=args.get("edit_mode", "generate"),
            preserve_subject=args.get("preserve_subject", False),
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "tool": "ai_image_generate",
                "error": redact(str(exc)),
            },
            ensure_ascii=False,
        )


def ai_assistant_delegate(args: dict, **kwargs) -> str:
    """Delegate a bounded task to a remote Hermes assistant."""
    try:
        from assistants.tool import ai_assistant_delegate as delegate  # pyright: ignore[reportMissingImports]

        result = delegate(
            assistant_id=args["assistant_id"],
            capability=args["capability"],
            task=args["task"],
            context=args.get("context"),
            constraints=args.get("constraints"),
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "tool": "ai_assistant_delegate",
                "error": redact(str(exc)),
            },
            ensure_ascii=False,
        )


def ai_usage_metrics(args: dict, **kwargs) -> str:
    """Return usage, health, limits, and warnings."""
    try:
        import subprocess, sys, os

        script_dir = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(script_dir, "usage_metrics_v2.py")

        mode = args.get("mode", "json")
        provider = args.get("provider")

        cmd = [sys.executable, script]
        if mode == "json":
            cmd.append("--json")
        elif mode == "compact":
            cmd.append("--compact")
        if provider:
            cmd.extend(["-p", provider])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return redact(result.stdout)
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "tool": "ai_usage_metrics",
                "error": redact(str(exc)),
            },
            ensure_ascii=False,
        )


def ai_key_rotate(args: dict, **kwargs) -> str:
    """Run key rotation workflows."""
    try:
        import subprocess, sys, os

        script_dir = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(script_dir, "hermes_key_rotate.py")

        provider = args["provider"]
        mode = args["mode"]

        cmd = [sys.executable, script, "--provider", provider]
        if mode == "dry_run":
            cmd.append("--dry-run")
        elif mode == "status":
            cmd.append("--status")
        elif mode == "render_env":
            cmd.append("--render-env")
        elif mode == "doctor":
            cmd.append("--doctor-secrets")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return redact(result.stdout)
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "tool": "ai_key_rotate",
                "error": redact(str(exc)),
            },
            ensure_ascii=False,
        )


def ai_secret_backend_status(args: dict, **kwargs) -> str:
    """Return Vaultwarden secret backend health."""
    try:
        import subprocess, sys, os

        script_dir = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(script_dir, "hermes_key_rotate.py")

        mode = args.get("mode", "health")
        cmd = [sys.executable, script]

        if mode == "doctor":
            cmd.append("--doctor-secrets")
        elif mode == "list_refs":
            cmd.append("--secret-backend")
            cmd.append("vaultwarden")
            cmd.append("--list-refs")
        else:
            cmd.append("--healthcheck")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return redact(result.stdout)
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "tool": "ai_secret_backend_status",
                "error": redact(str(exc)),
            },
            ensure_ascii=False,
        )
