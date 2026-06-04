"""Hermes Ops Kit — Image Generation Router Provider.

Bridges the Hermes ``image_generate`` tool to hermes-ops-kit's image routing.
Reads ``image_routes.yaml`` and dispatches to the configured backend:

  - local-comfyui   (local ComfyUI instance)
  - gemini          (Gemini 2.5 Flash Image / "Nano Banana")
  - openai          (DALL-E 3 or gpt-image-2)
  - fal             (FAL.ai Flux/Stable Diffusion)

Configure in ``~/.hermes/config.yaml``::

    image_gen:
      provider: ops-kit-router
      model: auto

The provider calls ``hermes-ops-kit image test`` as a subprocess and parses
the JSON result into Hermes's native image_gen response format.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    success_response,
)

logger = logging.getLogger(__name__)

# ── Model catalog ──────────────────────────────────────────────────────

_MODELS: Dict[str, Dict[str, Any]] = {
    "auto": {
        "display": "Auto (image_routes.yaml default)",
        "speed": "varies",
        "strengths": "Respects image_routes.yaml routing — local-first with cloud fallback.",
    },
    "local": {
        "display": "Local ComfyUI (flux-local)",
        "speed": "~30-90s",
        "strengths": "Private, no API key needed, no rate limits.",
    },
    "fast": {
        "display": "Gemini 2.5 Flash Image (Nano Banana)",
        "speed": "~3-10s",
        "strengths": "Fast cloud generation, ~$0.04/image, 10 aspect ratios.",
    },
    "quality": {
        "display": "OpenAI gpt-image-2 / DALL-E 3",
        "speed": "~15-120s",
        "strengths": "Highest fidelity, strong prompt adherence, paid.",
    },
    "fallback": {
        "display": "FAL.ai (Flux 2 Pro)",
        "speed": "~10-30s",
        "strengths": "Cloud fallback, Flux quality, paid.",
    },
}

DEFAULT_MODEL = "auto"


def _find_ops_kit_bridge() -> Optional[str]:
    """Locate the hermes-ops-kit bridge.py script."""
    candidates = [
        os.path.expanduser("~/.hermes/plugins/hermes-ops-kit/bridge.py"),
        os.path.expanduser("~/GIT/INFRA/tools/hermes-ops-kit/bridge.py"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _find_ops_kit_manager() -> Optional[str]:
    """Locate the hermes-ops-kit image_routes/manager.py script."""
    candidates = [
        os.path.expanduser("~/.hermes/plugins/hermes-ops-kit/image_routes/manager.py"),
        os.path.expanduser("~/GIT/INFRA/tools/hermes-ops-kit/image_routes/manager.py"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _find_ops_kit_dir() -> Optional[str]:
    """Locate the hermes-ops-kit package directory."""
    manager = _find_ops_kit_manager()
    if manager:
        return os.path.dirname(os.path.dirname(manager))
    bridge = _find_ops_kit_bridge()
    if bridge:
        return os.path.dirname(bridge)
    return None


def _is_background_edit_request(prompt: str, kwargs: Dict[str, Any]) -> bool:
    """Infer subject-preserving background edit intent."""
    edit_mode = kwargs.get("edit_mode")
    if edit_mode == "edit_background" or kwargs.get("preserve_subject") is True:
        return True

    text = (prompt or "").lower()
    has_background = any(token in text for token in ("sfondo", "background"))
    has_edit = any(
        token in text
        for token in (
            "modifica",
            "modificala",
            "sostituisci",
            "rimpiazza",
            "cambia",
            "aggiungi",
            "solo lo sfondo",
            "only the background",
        )
    )
    return has_background and has_edit


# ── Provider ───────────────────────────────────────────────────────────


class OpsKitRouterProvider(ImageGenProvider):
    """Hermes image_gen provider that dispatches through ops-kit's image router."""

    @property
    def name(self) -> str:
        return "ops-kit-router"

    @property
    def display_name(self) -> str:
        return "Ops Kit Router"

    def is_available(self) -> bool:
        """Check that ops-kit bridge or manager is available."""
        return _find_ops_kit_bridge() is not None or _find_ops_kit_manager() is not None

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": "varies",
            }
            for model_id, meta in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Ops Kit Router",
            "badge": "router",
            "tag": "Multi-backend router: local ComfyUI → Gemini Image → OpenAI → FAL",
            "env_vars": [
                {
                    "key": "GEMINI_API_KEY",
                    "prompt": "Gemini API key (for fast cloud generation)",
                    "url": "https://aistudio.google.com/apikey",
                },
                {
                    "key": "OPENAI_API_KEY",
                    "prompt": "OpenAI API key (for high quality generation)",
                    "url": "https://platform.openai.com/api-keys",
                },
                {
                    "key": "FAL_KEY",
                    "prompt": "FAL.ai API key (for cloud fallback)",
                    "url": "https://fal.ai/dashboard/keys",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="ops-kit-router",
                aspect_ratio=aspect,
            )

        ops_kit_dir = _find_ops_kit_dir()
        if not ops_kit_dir:
            return error_response(
                error=(
                    "hermes-ops-kit image router not found. "
                    "Ensure hermes-ops-kit is installed in ~/.hermes/plugins/ "
                    "or ~/GIT/INFRA/tools/hermes-ops-kit/"
                ),
                error_type="missing_dependency",
                provider="ops-kit-router",
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Resolve the route name from config
        route_name = self._resolve_route_name()

        # Extract optional reference image from kwargs (for image-to-image).
        reference_image = (
            kwargs.get("image")
            or kwargs.get("image_path")
            or kwargs.get("reference_image")
        )
        if reference_image and isinstance(reference_image, str):
            reference_image = os.path.expanduser(reference_image)

        route_for_generate = route_name if route_name and route_name != "auto" else None
        preserve_subject = bool(
            reference_image and _is_background_edit_request(prompt, kwargs)
        )

        if ops_kit_dir not in sys.path:
            sys.path.insert(0, ops_kit_dir)

        try:
            from image_routes.router import generate as ops_generate

            data = ops_generate(
                prompt=prompt,
                route_name=route_for_generate,
                aspect_ratio=aspect,
                num_images=1,
                image_path=reference_image,
                edit_mode="edit_background" if preserve_subject else "generate",
                preserve_subject=preserve_subject,
            )
        except Exception as exc:
            return error_response(
                error=f"Failed to run ops-kit image router: {exc}",
                error_type="router_error",
                provider="ops-kit-router",
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Convert ops-kit envelope → Hermes image_gen response
        return self._convert_result(data, prompt, aspect)

    def _resolve_route_name(self) -> Optional[str]:
        """Read the configured route from image_gen config.

        Precedence:
        1. ``image_gen.ops_kit_router.route`` in config.yaml
        2. ``image_gen.model`` when it matches a route name
        3. None → use default from image_routes.yaml
        """
        try:
            from hermes_cli.config import load_config

            cfg = load_config()
            section = cfg.get("image_gen") if isinstance(cfg, dict) else {}
        except Exception:
            return None

        if not isinstance(section, dict):
            return None

        # Check provider-specific config
        router_cfg = section.get("ops_kit_router")
        if isinstance(router_cfg, dict):
            route = router_cfg.get("route")
            if route:
                return route

        # Check if the top-level model matches a known route
        model = section.get("model")
        if model and model in _MODELS and model != "auto":
            return model

        return None

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """Try to extract a JSON object from mixed output."""
        # Find the first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def _convert_result(
        data: dict,
        prompt: str,
        aspect_ratio: str,
    ) -> Dict[str, Any]:
        """Convert ops-kit image result envelope → Hermes image_gen response."""
        provider = data.get("provider", "ops-kit-router")
        model = data.get("model", "unknown")

        if data.get("ok"):
            image_path = data.get("image_path") or (
                data.get("image_paths", [None])[0] if data.get("image_paths") else None
            )
            if not image_path:
                return error_response(
                    error="Image generated but no file path returned.",
                    error_type="empty_response",
                    provider="ops-kit-router",
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                )

            _caption = data.get("caption", f"Generated via {provider}:{model}")
            extra = data.get("extra", {})
            extra["route_provider"] = provider
            extra["route_model"] = model

            return success_response(
                image=str(image_path),
                model=model,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                provider="ops-kit-router",
                extra=extra,
            )
        else:
            return error_response(
                error=data.get("error", "Unknown image generation error"),
                error_type=data.get("error_type", "provider_error"),
                provider="ops-kit-router",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )


# ── Plugin entry point ─────────────────────────────────────────────────


def register(ctx) -> None:
    """Wire ``OpsKitRouterProvider`` into the Hermes image_gen registry."""
    ctx.register_image_gen_provider(OpsKitRouterProvider())
