#!/usr/bin/env python3
"""Hermes Ops Kit — DeepSeek Provider Adapter

OpenAI-compatible (https://api.deepseek.com). Thin wrapper over
``providers/_openai_compat_ops.OpenAICompatAdapter`` — see that module for the
chat/extract/review/models operations, output envelope, and redaction contract.

DeepSeek-specific: ``deepseek-reasoner`` rejects temperature and lacks JSON mode,
so extraction is redirected onto ``deepseek-v4-flash`` with a warning (via the
``supports_temperature`` / ``extract_model`` / ``extract_warning`` hooks).

Usage:
    python3 deepseek_adapter.py --operation chat --prompt "..." [--model deepseek-v4-flash]
    python3 deepseek_adapter.py --operation extract --prompt "..." --schema '{"type":"object",...}'
    python3 deepseek_adapter.py --operation review --prompt "..." --files '[{"path":"src/auth.py","content":"..."}]'
"""

if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )


from ..providers._openai_compat_ops import OpenAICompatAdapter, run_cli  # pyright: ignore[reportMissingImports]
from hermes_ops_kit.provider_catalog import PROVIDER_BASE_URLS  # noqa: E402


class DeepSeekAdapter(OpenAICompatAdapter):
    """DeepSeek — OpenAI-compatible adapter with reasoner-divergence hooks."""

    provider = "deepseek"
    provider_label = "DeepSeek"
    # derive from provider_catalog (single source of truth)
    _catalog_default, base_url_env = PROVIDER_BASE_URLS["deepseek"]
    base_url_default = _catalog_default
    api_key_env = "DEEPSEEK_API_KEY"
    timeout_env = "DEEPSEEK_TIMEOUT"
    allowed_models = [
        "deepseek-v4-flash",  # fast/cheap general chat; temperature + JSON output
        "deepseek-v4-pro",  # most capable
        "deepseek-chat",  # legacy stable alias
        "deepseek-reasoner",  # legacy reasoning alias; no temperature / JSON mode
    ]
    default_model = "deepseek-v4-flash"

    @classmethod
    def supports_temperature(cls, model: str) -> bool:
        # deepseek-reasoner ignores/rejects temperature.
        return "reasoner" not in model

    @classmethod
    def extract_model(cls, model: str) -> str:
        # JSON mode is unsupported on deepseek-reasoner → force onto the flash chat model.
        return "deepseek-v4-flash" if "reasoner" in model else model


if __name__ == "__main__":
    run_cli(DeepSeekAdapter)
