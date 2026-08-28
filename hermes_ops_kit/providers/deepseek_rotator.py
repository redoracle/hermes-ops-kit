"""Hermes Ops Kit — DeepSeek Provider Rotator

Thin wrapper over ``providers/_openai_compat_ops.OpenAICompatRotator`` — see
that module for the 8-branch validate ladder, two-phase smoke + rotate flow,
and redaction. Manual-new-key only (no admin key API — revoke is manual in the
DeepSeek console).
"""

from __future__ import annotations

from ..providers._openai_compat_ops import OpenAICompatRotator  # pyright: ignore[reportMissingImports]
from hermes_ops_kit.provider_catalog import PROVIDER_BASE_URLS  # noqa: E402


class DeepSeekRotator(OpenAICompatRotator):
    """Rotate DeepSeek API keys (OpenAI-compatible)."""

    provider = "deepseek"
    provider_label = "DeepSeek"
    api_ref = "hermes/deepseek/api_key"
    # derive from provider_catalog (single source of truth)
    _catalog_default, base_url_env = PROVIDER_BASE_URLS["deepseek"]
    base_url_default = _catalog_default
    chat_model = "deepseek-v4-flash"
    env_key = "DEEPSEEK_API_KEY"
