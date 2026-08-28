"""Hermes Ops Kit — provider/model catalog (single source of truth).

Every hardcoded per-provider model list and credential env-var name in the
kit derives from this module, so the catalogs shown by ``capabilities``, the
preferred models used by ``usage``/``health``, and the profiles written by
the route manager cannot silently diverge.  ``tests/test_provider_registry_sync.py``
pins the invariants.

Custom providers (``custom:<name>`` in ~/.hermes/config.yaml) are NOT listed
here: they are dynamic and must always be resolved from the live config, with
their credential taken from the provider's ``key_env``.
"""

from __future__ import annotations

# Canonical per-provider model catalogs (superset).  Curated rankings for the
# usage/health views live in usage_metrics_v2.PROVIDER_META but must be a
# subset of these lists (test-enforced).
PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"],
    "anthropic": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
    "github": [],  # CLI surface only; models route via copilot aliases
    "gemini": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-3.5-flash"],
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
    "nvidia": [
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-3-nano-30b-a3b",
        "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "meta/llama-4-maverick-17b-128e-instruct",
        "mistralai/mistral-nemotron",
    ],
    "fireworks": [
        "accounts/fireworks/models/glm-5p2",
        "accounts/fireworks/models/kimi-k2p6",
        "accounts/fireworks/models/kimi-k2p7-code",
    ],
    "deepinfra": [
        "deepseek-ai/DeepSeek-V4-Flash",
        "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "Qwen/Qwen3-30B",
    ],
}

# Provider → credential env vars (any one satisfies).  Custom providers
# resolve dynamically via their ``key_env`` in ~/.hermes/config.yaml.
PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "nvidia": ("NVIDIA_API_KEY",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "deepinfra": ("DEEPINFRA_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "zai": ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
    "github": ("GITHUB_TOKEN", "GH_TOKEN"),
    "copilot": ("GITHUB_TOKEN", "GH_TOKEN"),
}
