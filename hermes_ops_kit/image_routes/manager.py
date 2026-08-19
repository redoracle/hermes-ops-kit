"""Hermes Ops Kit — Image Route Manager CLI.

Manages image generation routes: list, doctor, test, set-default, set-route.

Usage:
    hermes-ops-kit image routes                   # Show all image routes
    hermes-ops-kit image doctor                   # Validate config + backend health
    hermes-ops-kit image test "prompt"            # Test generation with default route
    hermes-ops-kit image set-default local        # Change default route
    hermes-ops-kit image set-route fast gemini gemini-2.5-flash-image
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
import sys


from ..ui.console import Console

OPS_KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
DEPLOYED_CONFIG = os.path.join(HERMES_HOME, "ops-kit", "image_routes.yaml")
BUNDLED_CONFIG = os.path.join(OPS_KIT_DIR, "config", "image_routes.yaml")


def _config_path() -> str:
    """Return the active config path (deployed preferred)."""
    return DEPLOYED_CONFIG if os.path.exists(DEPLOYED_CONFIG) else BUNDLED_CONFIG


def _load_config() -> dict:
    """Load the image_routes.yaml configuration.

    Auto-seeds the deployed file from the bundled default on first run.
    """
    if not os.path.exists(DEPLOYED_CONFIG):
        if os.path.exists(BUNDLED_CONFIG):
            _seed_config()
        else:
            return {}

    path = DEPLOYED_CONFIG

    # Read file content first so we can attempt YAML then JSON parsing.
    try:
        with open(path, "r") as f:
            content = f.read()
    except Exception:
        return {}

    # Try YAML parsing if PyYAML is available; on parse error fall back to JSON.
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        try:
            return _yaml.safe_load(content) or {}
        except Exception:
            # YAML parse failed; fall through to JSON fallback below
            pass
    except ImportError:
        # PyYAML not installed — try JSON below
        pass

    # JSON fallback
    try:
        return json.loads(content) or {}
    except Exception:
        return {}


def _seed_config() -> None:
    """Copy the bundled image_routes.yaml to the deployed location on first run."""
    os.makedirs(os.path.dirname(DEPLOYED_CONFIG), exist_ok=True)
    with open(BUNDLED_CONFIG, "r") as src:
        with open(DEPLOYED_CONFIG, "w") as dst:
            dst.write(src.read())
    os.chmod(DEPLOYED_CONFIG, 0o600)


def _save_config(config: dict) -> None:
    """Save config to the deployed path."""
    target = os.path.join(HERMES_HOME, "ops-kit", "image_routes.yaml")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        with open(tmp, "w") as f:
            _yaml.safe_dump(config, f, indent=2, sort_keys=False)
    except ImportError:
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


def cmd_routes(_args: argparse.Namespace) -> None:
    """Display all image generation routes."""
    con = Console()
    config = _load_config()
    if not config:
        con.print_error("No image routes configured.")
        con.print(f"  Expected: {DEPLOYED_CONFIG}")
        con.print(f"  Bundled:  {BUNDLED_CONFIG}")
        return

    from ..image_routes.router import healthcheck

    hc = healthcheck()
    routes = config.get("routes", {})

    con.print(con.header("=== IMAGE ROUTES ==="))
    con.print(f"  default: {con.bold(config.get('default_route', 'local'))}")
    con.print(f"  prefer_local: {config.get('policies', {}).get('prefer_local', True)}")
    con.print()

    STATUS_LABELS = {
        "ready": con.green("READY"),
        "offline": con.red("OFFLINE"),  # service not running (e.g. ComfyUI)
        "no-key": con.orange("NO KEY"),  # env var not set — user must configure
        "no-quota": con.yellow("NO QUOTA"),  # key exists but API rejected it
    }

    for key, cfg in routes.items():
        hr = hc.get("routes", {}).get(key, {})
        raw_status = hr.get("status", "offline")
        status = STATUS_LABELS.get(raw_status, con.red(raw_status.upper()))
        default_marker = con.bold(" ★") if key == config.get("default_route") else ""
        con.print(
            f"  {key:<10s} {cfg.get('provider', '?'):<16s} "
            f"{cfg.get('model', '?'):<28s} {status:<26s} "
            f"{cfg.get('cost_class', '?'):<10s} {cfg.get('label', '')}{default_marker}"
        )

    con.print()
    con.print("Policies:")
    policies = config.get("policies", {})
    for pk, pv in policies.items():
        con.print(f"  {pk}: {pv}")


def cmd_doctor(_args: argparse.Namespace) -> None:
    """Validate image route configuration and backend health."""
    con = Console()
    issues = []
    warnings = []

    config = _load_config()
    if not config:
        issues.append(
            ("config_missing", f"No image_routes.yaml found at {DEPLOYED_CONFIG}")
        )
    else:
        routes = config.get("routes", {})
        if not routes:
            issues.append(("no_routes", "No image routes configured"))
        default = config.get("default_route", "")
        if default and default not in routes:
            issues.append(
                ("invalid_default", f"Default route '{default}' not found in routes")
            )

    # Check backend availability
    from ..image_routes.router import healthcheck

    hc = healthcheck()
    any_available = hc.get("any_available", False)

    if not any_available:
        issues.append(("no_backends", "No image generation backends are available"))
    else:
        for key, info in hc.get("routes", {}).items():
            if not info.get("available"):
                provider = info.get("provider", key)
                warnings.append(
                    (
                        f"backend_offline_{key}",
                        f"Backend '{key}' ({provider}) is offline",
                    )
                )

    if issues:
        for code, msg in issues:
            con.print(f"{con.red('✘')} {code}: {msg}")
    if warnings:
        for code, msg in warnings:
            con.print(f"{con.yellow('⚠')} {code}: {msg}")
    if not issues and not warnings:
        con.print(f"{con.green('✓')} All image routes healthy")
        for key, info in hc.get("routes", {}).items():
            if info.get("available"):
                con.print(f"  {key}: {info.get('provider')}:{info.get('model')}")

    con.print(f"\nConfig: {_config_path()}")
    con.print(
        f"Output:  {os.path.expanduser(config.get('policies', {}).get('output_dir', '~/.hermes/cache/images'))}"
    )


def cmd_test(args: argparse.Namespace) -> None:
    """Test image generation with the default or specified route."""
    con = Console()
    from ..image_routes.router import generate

    prompt = args.prompt
    route = args.route or None
    aspect = args.aspect_ratio or "landscape"
    num = args.num_images or 1
    image_path = getattr(args, "image", None) or None
    edit_mode = (
        "edit_background" if getattr(args, "edit_background", False) else "generate"
    )

    con.print(f"Generating image via route '{con.bold(route or 'default')}'...")
    preview = prompt[:100] + ("..." if len(prompt) > 100 else "")
    con.print(f"  Prompt: {preview}")
    if image_path:
        con.print(f"  Reference image: {image_path}")
    if edit_mode == "edit_background":
        con.print("  Mode: preserve subject, replace background")
    con.print(f"  Aspect: {aspect}  Images: {num}")
    con.print()

    result = generate(
        prompt=prompt,
        route_name=route,
        aspect_ratio=aspect,
        num_images=num,
        image_path=image_path,
        edit_mode=edit_mode,
        preserve_subject=edit_mode == "edit_background",
    )

    if result.get("ok"):
        paths = result.get("image_paths", [])
        con.print(f"{con.green('✓')} Image generated successfully")
        con.print(f"  Provider: {result.get('provider')}")
        con.print(f"  Model:    {result.get('model')}")
        con.print(f"  Duration: {result.get('duration_ms')}ms")
        con.print(f"  Caption:  {result.get('caption', '')}")
        if result.get("attempts"):
            skipped = [
                f"{a['route']} ({a.get('error', '')[:60]})" for a in result["attempts"]
            ]
            con.print(f"  Skipped: {con.dim(', '.join(skipped))}")
        for i, p in enumerate(paths):
            size = os.path.getsize(p) if os.path.exists(p) else 0
            con.print(f"  Image {i + 1}:  {p} ({size} bytes)")
    else:
        con.print(f"{con.red('✘')} Generation failed")
        error_msg = result.get("error", "")
        # Truncate very long error messages (Google SDK embeds verbose quota details)
        if len(error_msg) > 500:
            error_msg = error_msg[:500] + "..."
        con.print(f"  Error: {error_msg}")
        con.print(f"  Type:  {result.get('error_type')}")
        if result.get("attempts"):
            con.print("  Routes tried:")
            for a in result["attempts"]:
                route = a.get("route", "?")
                provider = a.get("provider", "?")
                err = a.get("error", "")[:120]
                con.print(f"    {con.dim(f'{route} ({provider}): {err}')}")


def cmd_set_default(args: argparse.Namespace) -> None:
    """Set the default image generation route."""
    config = _load_config()
    route_name = args.route_name

    if route_name not in config.get("routes", {}):
        print(f"Unknown route: {route_name}")
        print(f"Available: {list(config.get('routes', {}).keys())}")
        sys.exit(1)

    config["default_route"] = route_name
    _save_config(config)
    print(f"Default image route set to: {route_name}")


def cmd_set_route(args: argparse.Namespace) -> None:
    """Update a specific image route's provider/model."""
    config = _load_config()
    route_name = args.route_name

    if route_name not in config.get("routes", {}):
        print(f"Unknown route: {route_name}")
        print(f"Available: {list(config.get('routes', {}).keys())}")
        sys.exit(1)

    if args.provider:
        config["routes"][route_name]["provider"] = args.provider
    if args.model:
        config["routes"][route_name]["model"] = args.model

    _save_config(config)
    print(f"Route '{route_name}' updated:")
    print(f"  provider: {config['routes'][route_name].get('provider')}")
    print(f"  model:    {config['routes'][route_name].get('model')}")


def cmd_export(args: argparse.Namespace) -> None:
    """Export full image route configuration as JSON."""
    config = _load_config()
    from ..image_routes.router import healthcheck

    hc = healthcheck()

    output = {
        "config": config,
        "health": hc,
    }

    # Always emit the JSON export (the CLI flag previously toggled identical behavior)
    print(json.dumps(output, indent=2))


def handle_image_command(args: list[str]) -> int:
    """Entry point for `hermes-ops-kit image ...` subcommand.

    Args:
        args: Subcommand arguments (e.g. ['routes'], ['test', '--prompt', '...'])

    Returns:
        Exit code (0 = success).
    """
    parser = argparse.ArgumentParser(
        prog="hermes-ops-kit image",
        description="Hermes Ops Kit — Image Generation Route Manager",
    )
    sub = parser.add_subparsers(dest="subcommand")

    # routes
    sub.add_parser("routes", help="Show all image generation routes")

    # doctor
    sub.add_parser("doctor", help="Validate image route config and backend health")

    # test
    test_p = sub.add_parser("test", help="Test image generation")
    test_p.add_argument("prompt", help="Image generation prompt")
    test_p.add_argument(
        "--route", "-r", default=None, help="Route to use (default: auto)"
    )
    test_p.add_argument(
        "--aspect-ratio",
        "-a",
        default="landscape",
        choices=["landscape", "square", "portrait"],
    )
    test_p.add_argument("--num-images", "-n", type=int, default=1)
    test_p.add_argument(
        "--image",
        "-i",
        default=None,
        help="Reference image path for image-to-image generation",
    )
    test_p.add_argument(
        "--edit-background",
        action="store_true",
        help="Preserve the reference subject and replace only the background",
    )

    # set-default
    setdef = sub.add_parser(
        "set-default", help="Set the default image generation route"
    )
    setdef.add_argument(
        "route_name", help="Route name (local, fast, quality, fallback)"
    )

    # set-route
    setrt = sub.add_parser("set-route", help="Update a specific image route")
    setrt.add_argument("route_name", help="Route name")
    setrt.add_argument("provider", nargs="?", default=None, help="New provider")
    setrt.add_argument("model", nargs="?", default=None, help="New model")

    # export
    exp = sub.add_parser("export", help="Export image route config as JSON")
    exp.add_argument("--json", action="store_true", help="JSON output")

    parsed = parser.parse_args(args)

    if not parsed.subcommand:
        parser.print_help()
        return 1

    handlers = {
        "routes": cmd_routes,
        "doctor": cmd_doctor,
        "test": cmd_test,
        "set-default": cmd_set_default,
        "set-route": cmd_set_route,
        "export": cmd_export,
    }

    handler = handlers.get(parsed.subcommand)
    if handler:
        handler(parsed)

    return 0


if __name__ == "__main__":
    sys.exit(handle_image_command(sys.argv[1:]))
