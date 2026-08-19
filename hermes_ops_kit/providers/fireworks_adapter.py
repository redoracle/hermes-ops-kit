#!/usr/bin/env python3
"""Hermes Ops Kit — Fireworks AI Provider Adapter

OpenAI-compatible (https://api.fireworks.ai/inference/v1). Thin wrapper over
``providers/_openai_compat_ops.OpenAICompatAdapter`` — see that module for the
chat/extract/review/models operations, output envelope, and redaction contract.

Usage:
    python3 fireworks_adapter.py --operation chat --prompt "..." [--model accounts/fireworks/models/glm-5p2]
    python3 fireworks_adapter.py --operation extract --prompt "..." --schema '{"type":"object",...}'
    python3 fireworks_adapter.py --operation review --prompt "..." --files '[{"path":"src/auth.py","content":"..."}]'
"""

if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )


from ..providers._openai_compat_ops import OpenAICompatAdapter, run_cli  # pyright: ignore[reportMissingImports]


class FireworksAdapter(OpenAICompatAdapter):
    """Fireworks AI — OpenAI-compatible adapter."""

    provider = "fireworks"
    provider_label = "Fireworks AI"
    base_url_default = "https://api.fireworks.ai/inference/v1"
    base_url_env = "FIREWORKS_BASE_URL"
    api_key_env = "FIREWORKS_API_KEY"
    timeout_env = "FIREWORKS_TIMEOUT"
    allowed_models = [
        "accounts/fireworks/models/glm-5p2",  # default; fast general chat
        "accounts/fireworks/models/kimi-k2p6",
        "accounts/fireworks/models/kimi-k2p7-code",
    ]
    default_model = "accounts/fireworks/models/glm-5p2"


if __name__ == "__main__":
    run_cli(FireworksAdapter)
