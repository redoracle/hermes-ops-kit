#!/usr/bin/env python3
"""Hermes Ops Kit — DeepInfra Provider Adapter

OpenAI-compatible (https://api.deepinfra.com/v1/openai). Thin wrapper over
``providers/_openai_compat_ops.OpenAICompatAdapter`` — see that module for the
chat/extract/review/models operations, output envelope, and redaction contract.

Usage:
    python3 deepinfra_adapter.py --operation chat --prompt "..." [--model deepseek-ai/DeepSeek-V4-Flash]
    python3 deepinfra_adapter.py --operation extract --prompt "..." --schema '{"type":"object",...}'
    python3 deepinfra_adapter.py --operation review --prompt "..." --files '[{"path":"src/auth.py","content":"..."}]'
"""

if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )


from ..providers._openai_compat_ops import OpenAICompatAdapter, run_cli  # pyright: ignore[reportMissingImports]
from hermes_ops_kit.provider_catalog import PROVIDER_BASE_URLS  # noqa: E402


class DeepInfraAdapter(OpenAICompatAdapter):
    """DeepInfra — OpenAI-compatible adapter (aggregator-style gateway)."""

    provider = "deepinfra"
    provider_label = "DeepInfra"
    # derive from provider_catalog (single source of truth)
    _catalog_default, base_url_env = PROVIDER_BASE_URLS["deepinfra"]
    base_url_default = _catalog_default
    api_key_env = "DEEPINFRA_API_KEY"
    timeout_env = "DEEPINFRA_TIMEOUT"
    allowed_models = [
        "deepseek-ai/DeepSeek-V4-Flash",  # default; fast general chat
        "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "Qwen/Qwen3-30B",
    ]
    default_model = "deepseek-ai/DeepSeek-V4-Flash"


if __name__ == "__main__":
    run_cli(DeepInfraAdapter)
