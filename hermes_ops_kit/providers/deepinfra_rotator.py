"""Hermes Ops Kit — DeepInfra Provider Rotator

Thin wrapper over ``providers/_openai_compat_ops.OpenAICompatRotator`` — see
that module for the 8-branch validate ladder, two-phase smoke + rotate flow,
and redaction. Manual-new-key only (no admin key API — revoke is manual in the
DeepInfra console).
"""

from __future__ import annotations

from ..providers._openai_compat_ops import OpenAICompatRotator  # pyright: ignore[reportMissingImports]
from hermes_ops_kit.provider_catalog import PROVIDER_BASE_URLS  # noqa: E402


class DeepInfraRotator(OpenAICompatRotator):
    """Rotate DeepInfra API keys (OpenAI-compatible)."""

    provider = "deepinfra"
    provider_label = "DeepInfra"
    api_ref = "hermes/deepinfra/api_key"
    # derive from provider_catalog (single source of truth)
    _catalog_default, base_url_env = PROVIDER_BASE_URLS["deepinfra"]
    base_url_default = _catalog_default
    chat_model = "deepseek-ai/DeepSeek-V4-Flash"
    from ..provider_catalog import PROVIDER_ENV_KEYS  # noqa: E402

    env_key = PROVIDER_ENV_KEYS["deepinfra"][0]
