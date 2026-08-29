"""Hermes Ops Kit — Fireworks AI Provider Rotator

Thin wrapper over ``providers/_openai_compat_ops.OpenAICompatRotator`` — see
that module for the 8-branch validate ladder, two-phase smoke + rotate flow,
and redaction. Manual-new-key only (no admin key API — revoke is manual in the
Fireworks console).
"""

from __future__ import annotations

from ..providers._openai_compat_ops import OpenAICompatRotator  # pyright: ignore[reportMissingImports]
from hermes_ops_kit.provider_catalog import PROVIDER_BASE_URLS  # noqa: E402


class FireworksRotator(OpenAICompatRotator):
    """Rotate Fireworks AI API keys (OpenAI-compatible)."""

    provider = "fireworks"
    provider_label = "Fireworks AI"
    api_ref = "hermes/fireworks/api_key"
    # derive from provider_catalog (single source of truth)
    _catalog_default, base_url_env = PROVIDER_BASE_URLS["fireworks"]
    base_url_default = _catalog_default
    chat_model = "accounts/fireworks/models/glm-5p2"
    from ..provider_catalog import PROVIDER_ENV_KEYS  # noqa: E402

    env_key = PROVIDER_ENV_KEYS["fireworks"][0]
