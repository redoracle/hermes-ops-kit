"""Hermes Ops Kit — DeepSeek Provider Rotator

Thin wrapper over ``providers/_openai_compat_ops.OpenAICompatRotator`` — see
that module for the 8-branch validate ladder, two-phase smoke + rotate flow,
and redaction. Manual-new-key only (no admin key API — revoke is manual in the
DeepSeek console).
"""

from __future__ import annotations

from ..providers._openai_compat_ops import OpenAICompatRotator  # pyright: ignore[reportMissingImports]


class DeepSeekRotator(OpenAICompatRotator):
    """Rotate DeepSeek API keys (OpenAI-compatible)."""

    provider = "deepseek"
    provider_label = "DeepSeek"
    api_ref = "hermes/deepseek/api_key"
    base_url_default = "https://api.deepseek.com"
    base_url_env = "DEEPSEEK_BASE_URL"
    chat_model = "deepseek-v4-flash"
    env_key = "DEEPSEEK_API_KEY"
