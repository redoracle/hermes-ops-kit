"""Hermes Ops Kit — DeepInfra Provider Rotator

Thin wrapper over ``providers/_openai_compat_ops.OpenAICompatRotator`` — see
that module for the 8-branch validate ladder, two-phase smoke + rotate flow,
and redaction. Manual-new-key only (no admin key API — revoke is manual in the
DeepInfra console).
"""

from __future__ import annotations

from providers._openai_compat_ops import OpenAICompatRotator  # pyright: ignore[reportMissingImports]


class DeepInfraRotator(OpenAICompatRotator):
    """Rotate DeepInfra API keys (OpenAI-compatible)."""

    provider = "deepinfra"
    provider_label = "DeepInfra"
    api_ref = "hermes/deepinfra/api_key"
    base_url_default = "https://api.deepinfra.com/v1/openai"
    base_url_env = "DEEPINFRA_BASE_URL"
    chat_model = "deepseek-ai/DeepSeek-V4-Flash"
    env_key = "DEEPINFRA_API_KEY"
