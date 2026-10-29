"""Which ``providers:`` / ``custom_providers:`` block Hermes uses for a provider name.

One place for the lookup rule, so doctor, route verification, the route harness and the
Headroom overlay cannot disagree about which endpoint or key variable a route names.
Mirrors ``hermes_cli.providers.custom_provider_aliases`` in Hermes core (a contract test
compares the two against a real core).
"""

from __future__ import annotations

from typing import Any

# Key spellings Hermes accepts for the environment variable that holds the API key.
KEY_ENV_FIELDS = ("key_env", "api_key_env")


def endpoint(entry: dict) -> str:
    """The endpoint of a provider block, under the keys Hermes core accepts."""
    return str(entry.get("base_url") or entry.get("url") or entry.get("api") or "").strip()


def custom_provider_aliases(display_name: str, provider_key: str = "") -> set[str]:
    """Every identity Hermes core accepts for one ``custom_providers`` entry.

    ``custom:whiplash-local``, ``whiplash-local`` and ``Whiplash Local`` all name the same
    endpoint.
    """
    aliases: set[str] = set()
    for value in (display_name, provider_key):
        raw = str(value or "").strip().lower()
        if not raw:
            continue
        normalized = raw.replace(" ", "-")
        slug = normalized if normalized.startswith("custom:") else f"custom:{normalized}"
        aliases.update({raw, normalized, slug})
        if normalized.startswith("custom:"):
            suffix = normalized.split(":", 1)[1]
            if suffix:
                aliases.update({suffix, f"custom:{normalized}"})
    return aliases


def key_env_of(entry: dict) -> str:
    """The key environment variable a provider block names, or ``""``."""
    for field in KEY_ENV_FIELDS:
        value = str(entry.get(field) or "").strip()
        if value:
            return value
    return ""


def find_provider_block(
    hermes_cfg: dict, provider: str, *, need_endpoint: bool = False
) -> dict[str, Any]:
    """The block Hermes would use for ``provider``, or ``{}``.

    Same order as core: a ``providers.<name>`` block, then the ``custom_providers:`` list
    matched by alias. With ``need_endpoint`` a block without an endpoint is skipped (the
    Headroom overlay needs somewhere to send traffic; a key lookup does not).
    """
    hermes_cfg = hermes_cfg if isinstance(hermes_cfg, dict) else {}
    providers = hermes_cfg.get("providers")
    named = providers.get(provider) if isinstance(providers, dict) else None
    if isinstance(named, dict) and (endpoint(named) or not need_endpoint):
        return dict(named)
    requested = str(provider or "").strip().lower()
    if requested:
        for entry in hermes_cfg.get("custom_providers") or []:
            if not isinstance(entry, dict) or (need_endpoint and not endpoint(entry)):
                continue
            aliases = custom_provider_aliases(
                str(entry.get("name") or ""), str(entry.get("provider_key") or "")
            )
            if requested in aliases:
                return dict(entry)
    return {}


def provider_key_env(hermes_cfg: dict, provider: str) -> str:
    """The key environment variable Hermes config names for ``provider``, or ``""``."""
    return key_env_of(find_provider_block(hermes_cfg, provider))
