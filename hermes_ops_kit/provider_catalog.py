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
    # image-generation backends (not LLM text providers)
    "fal": ("FAL_KEY",),
    "cloudflare": ("CLOUDFLARE_API_TOKEN",),
}


# Provider API endpoints: (default base URL, env var that overrides it).
# Adapters (_openai_compat_ops base_url_default/base_url_env) and the
# usage/health probes must both derive from this table.
PROVIDER_BASE_URLS: dict[str, tuple[str, str]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_BASE_URL"),
    "anthropic": ("https://api.anthropic.com", "ANTHROPIC_BASE_URL"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_BASE_URL"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_BASE_URL"),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "FIREWORKS_BASE_URL"),
    "deepinfra": ("https://api.deepinfra.com/v1/openai", "DEEPINFRA_BASE_URL"),
}


def provider_base_url(provider: str) -> str:
    """Effective base URL for a provider (env override wins over default)."""
    import os

    default, env_var = PROVIDER_BASE_URLS[provider]
    return os.environ.get(env_var, default)


# Provider name normalization: external/config names → canonical key.
# Single copy — usage_metrics_v2._PROVIDER_NORMALIZE and commands.py doctor
# normalization derive from this (identity mappings omitted).
PROVIDER_ALIASES: dict[str, str] = {
    "copilot": "github",
    "openai-api": "openai",
    "anthropic-api": "anthropic",
}


def normalize_provider(provider: str) -> str:
    """Canonical provider key for an external/config name."""
    return PROVIDER_ALIASES.get(provider, provider)


def key_envs_for(provider: str) -> tuple[str, ...]:
    """All credential env vars for a provider, in precedence order."""
    return PROVIDER_ENV_KEYS.get(normalize_provider(provider.lower()), ())


def first_available_key(
    provider: str, env_vars: dict[str, str] | None = None
) -> str | None:
    """First set credential env var for *provider*, or None.

    Callers that only checked the FIRST env var misreported alias-only
    environments (GOOGLE_API_KEY-only, GH_TOKEN-only, ZAI_API_KEY-only)
    as credential-less — this checks every declared alias.
    """
    import os

    env = os.environ if env_vars is None else env_vars
    for key in key_envs_for(provider):
        if str(env.get(key, "")).strip():
            return key
    return None


def has_credential(provider: str, env_vars: dict[str, str] | None = None) -> bool:
    """True when *provider* has any declared credential env var set."""
    return first_available_key(provider, env_vars) is not None


# GitHub Copilot model catalog (GH_COPILOT_STUDIO curation — not queryable
# via API). Single copy; usage_metrics_v2 derives from it.
COPILOT_MODELS: dict[str, list[str]] = {
    "anthropic": ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"],
    "openai": [
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2",
        "gpt-5.2-codex",
        "gpt-5.3-codex",
        "gpt-5.4",
        "gpt-5.5",
    ],
    "google": ["gemini-2.5-pro", "gemini-3.5-flash", "gemini-3.1-pro"],
    "github": ["raptor-mini"],
}
