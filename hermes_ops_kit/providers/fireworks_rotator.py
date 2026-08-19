"""Hermes Ops Kit — Fireworks AI Provider Rotator

Thin wrapper over ``providers/_openai_compat_ops.OpenAICompatRotator`` — see
that module for the 8-branch validate ladder, two-phase smoke + rotate flow,
and redaction. Manual-new-key only (no admin key API — revoke is manual in the
Fireworks console).
"""

from __future__ import annotations

from ..providers._openai_compat_ops import OpenAICompatRotator  # pyright: ignore[reportMissingImports]


class FireworksRotator(OpenAICompatRotator):
    """Rotate Fireworks AI API keys (OpenAI-compatible)."""

    provider = "fireworks"
    provider_label = "Fireworks AI"
    api_ref = "hermes/fireworks/api_key"
    base_url_default = "https://api.fireworks.ai/inference/v1"
    base_url_env = "FIREWORKS_BASE_URL"
    chat_model = "accounts/fireworks/models/glm-5p2"
    env_key = "FIREWORKS_API_KEY"
