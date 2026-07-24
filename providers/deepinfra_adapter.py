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

import os
import sys

# Prime sys.path so absolute intra-package imports resolve when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers._openai_compat_ops import OpenAICompatAdapter, run_cli  # pyright: ignore[reportMissingImports]


class DeepInfraAdapter(OpenAICompatAdapter):
    """DeepInfra — OpenAI-compatible adapter (aggregator-style gateway)."""

    provider = "deepinfra"
    provider_label = "DeepInfra"
    base_url_default = "https://api.deepinfra.com/v1/openai"
    base_url_env = "DEEPINFRA_BASE_URL"
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
