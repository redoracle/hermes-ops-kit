"""Hermes Ops Kit — Image Generation Router.

Core dispatch logic:
  1. Load image_routes.yaml config
  2. Resolve route (by name, or default)
  3. Check local availability; fallback to cloud if needed
  4. Dispatch to the appropriate image adapter
  5. Return standardized result envelope
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

OPS_KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_ROUTES_CONFIG = os.path.join(OPS_KIT_DIR, "config", "image_routes.yaml")

HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
DEPLOYED_CONFIG = os.path.join(HERMES_HOME, "ops-kit", "image_routes.yaml")


def _load_config() -> dict:
    """Load image_routes.yaml from deployed path, falling back to bundled."""
    for path in (DEPLOYED_CONFIG, IMAGE_ROUTES_CONFIG):
        if os.path.exists(path):
            try:
                import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

                with open(path) as f:
                    return _yaml.safe_load(f) or {}
            except ImportError:
                pass
    return {}


def _expand_path(path: str) -> str:
    """Expand ~ and env vars in a path."""
    return os.path.expanduser(os.path.expandvars(path))


def resolve_route(route_name: str | None = None) -> tuple[str, dict]:
    """Resolve the image generation route.

    Args:
        route_name: Name of the route to use (e.g. 'local', 'fast', 'quality').
                    If None, uses the default_route from config.

    Returns:
        (route_key, route_config) tuple.
    """
    config = _load_config()
    routes = config.get("routes", {})

    if not routes:
        raise ValueError("No image routes configured in image_routes.yaml")

    if route_name and route_name in routes:
        return route_name, routes[route_name]

    # Use default (shipped config uses "local" as preferred default)
    default = config.get("default_route", "local")
    if default in routes:
        return default, routes[default]

    # Last resort: first available route
    first = next(iter(routes.keys()))
    return first, routes[first]


def route_for_provider(provider: str) -> str | None:
    """Return the first configured route name for a provider."""
    config = _load_config()
    for name, route in (config.get("routes", {}) or {}).items():
        if route.get("provider") == provider:
            return name
    return None


def _get_adapter(provider: str, route_config: dict) -> Any:
    """Instantiate the correct adapter for the provider."""
    if provider == "local-comfyui":
        from image_routes.adapters.local_comfyui import LocalComfyUIAdapter

        return LocalComfyUIAdapter(
            endpoint=route_config.get("endpoint", "http://127.0.0.1:8188"),
            workflow=route_config.get("workflow", ""),
        )
    elif provider == "gemini":
        from image_routes.adapters.gemini_image import GeminiImageAdapter

        return GeminiImageAdapter()
    elif provider == "openai":
        from image_routes.adapters.openai_image import OpenAIImageAdapter

        return OpenAIImageAdapter()
    elif provider == "fal":
        from image_routes.adapters.fal_image import FALImageAdapter

        return FALImageAdapter()
    else:
        raise ValueError(f"Unknown image provider: {provider}")


def _fallback_route(config: dict, current_route_key: str) -> tuple[str, dict] | None:
    """Find the next best fallback route by priority.

    Args:
        config: Parsed image_routes.yaml
        current_route_key: The route that just failed

    Returns:
        (route_key, route_config) or None if no fallback available.
    """
    policies = config.get("policies", {})
    if not policies.get("allow_cloud_fallback", True):
        return None

    routes = config.get("routes", {})
    current_priority = routes.get(current_route_key, {}).get("priority", 999)

    # Find next route with higher priority number (lower priority)
    candidates = sorted(
        [(k, v) for k, v in routes.items() if k != current_route_key],
        key=lambda x: x[1].get("priority", 999),
    )

    for key, cfg in candidates:
        if cfg.get("priority", 999) > current_priority:
            return key, cfg

    # If no higher-priority route found, take the closest
    if candidates:
        return candidates[0]

    return None


def generate(
    prompt: str,
    route_name: str | None = None,
    aspect_ratio: str = "landscape",
    num_images: int = 1,
    image_path: str | None = None,
    **kwargs,
) -> dict:
    """Generate an image via the configured route.

    This is the main entry point. It handles:
      - Route resolution
      - Local availability checks
      - Cloud fallback
      - Adapter dispatch
      - Standardized result envelope

    Args:
        prompt: Image generation prompt
        route_name: Route to use (None = default)
        aspect_ratio: landscape | square | portrait
        num_images: Number of images to generate

    Returns:
        Standardized image result dict.
    """
    config = _load_config()
    policies = config.get("policies", {})

    # Resolve initial route
    try:
        key, route_cfg = resolve_route(route_name)
    except ValueError as e:
        return {
            "ok": False,
            "provider": "ops-kit-router",
            "operation": "generate_image",
            "type": "image",
            "error": str(e),
            "error_type": "ConfigError",
            "image_path": None,
            "image_paths": [],
            "duration_ms": 0,
        }

    provider = route_cfg.get("provider", "")
    model = route_cfg.get("model", "")
    model_override = kwargs.get("model")
    edit_mode = kwargs.get("edit_mode", "generate")
    preserve_subject = bool(kwargs.get("preserve_subject", False))
    prefer_local = policies.get("prefer_local", True)

    if image_path and (edit_mode == "edit_background" or preserve_subject):
        from image_routes.background_edit import edit_background

        def generate_background(background_prompt: str) -> dict:
            return generate(
                prompt=background_prompt,
                route_name=route_name,
                aspect_ratio=aspect_ratio,
                num_images=1,
                image_path=None,
                model=model_override,
                edit_mode="generate",
                preserve_subject=False,
            )

        return edit_background(
            source_path=image_path,
            background_prompt=prompt,
            generate_background=generate_background,
        )

    # Try local first if preferred and a different route is requested
    if prefer_local and provider != "local-comfyui":
        local_route = config.get("routes", {}).get("local", {})
        if local_route:
            local_adapter = _get_adapter("local-comfyui", local_route)
            local_available = local_adapter.is_available()
            if local_available:
                # Use local instead — update key for fallback tracking
                key = "local"
                provider = "local-comfyui"
                model = local_route.get("model", "flux-local")
                route_cfg = local_route

    # Dispatch to adapter with cycle detection
    attempts = []
    visited_keys: set[str] = set()
    while True:
        visited_keys.add(key)

        try:
            adapter = _get_adapter(provider, route_cfg)
        except ValueError as e:
            return {
                "ok": False,
                "provider": provider,
                "operation": "generate_image",
                "type": "image",
                "error": str(e),
                "error_type": "UnknownProvider",
                "image_path": None,
                "image_paths": [],
                "duration_ms": 0,
            }

        # Check availability
        if not adapter.is_available():
            attempts.append(
                {
                    "route": key,
                    "provider": provider,
                    "error": f"{provider} is not available",
                }
            )

            # Try fallback (skip already-visited routes to prevent cycles)
            fallback = _fallback_route(config, key)
            while fallback and fallback[0] in visited_keys:
                fallback = _fallback_route(config, fallback[0])
            if fallback:
                key, route_cfg = fallback
                provider = route_cfg.get("provider", "")
                model = route_cfg.get("model", "")
                continue
            else:
                return {
                    "ok": False,
                    "provider": provider,
                    "operation": "generate_image",
                    "type": "image",
                    "error": f"No image generation backend available. Attempted: {attempts}",
                    "error_type": "NoBackendAvailable",
                    "image_path": None,
                    "image_paths": [],
                    "attempts": attempts,
                    "duration_ms": 0,
                }

        # Generate
        result = adapter.generate(
            prompt=prompt,
            model=model_override or model,
            aspect_ratio=aspect_ratio,
            num_images=num_images,
            image_path=image_path,
        )
        if attempts:
            result["attempts"] = attempts
        return result


def healthcheck() -> dict:
    """Check availability of all configured image routes."""
    config = _load_config()
    routes = config.get("routes", {})
    policies = config.get("policies", {})

    results = {}
    for key, cfg in routes.items():
        provider = cfg.get("provider", "")
        try:
            adapter = _get_adapter(provider, cfg)
            available = adapter.is_available()
        except Exception:
            available = False

        results[key] = {
            "provider": provider,
            "model": cfg.get("model", ""),
            "label": cfg.get("label", ""),
            "cost_class": cfg.get("cost_class", "unknown"),
            "available": available,
            "priority": cfg.get("priority", 999),
        }

    return {
        "default_route": config.get("default_route", "local"),
        "prefer_local": policies.get("prefer_local", True),
        "cloud_fallback": policies.get("allow_cloud_fallback", True),
        "routes": results,
        "any_available": any(r["available"] for r in results.values()),
    }
