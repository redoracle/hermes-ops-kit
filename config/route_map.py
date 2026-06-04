"""Canonical AUX route definitions — single source of truth.

Shared by:
  - hermes_route_manager.py  (CLI: set-aux, apply-profile)
  - usage_metrics_v2.py      (display: build_routes)
  - route_runtime_harness.py  (verify: build_report)

Hermes config keys follow the ``auxiliary.<task>`` schema in
``~/.hermes/config.yaml``.  Short keys are the ops-kit display names.
"""

from __future__ import annotations

# Canonical mapping: short_key → hermes_config_key (bare, no "auxiliary." prefix)
AUX_ROUTE_TABLE: dict[str, str] = {
    "vision": "vision",
    "web": "web_extract",
    "compression": "compression",
    "approval": "approval",
    "skills": "skills_hub",
    "mcp": "mcp",
    "title": "title_generation",
    "triage": "triage_specifier",
}

# Ordered for display — the order rows appear in hermes-usage and route-test
AUX_SHORT_KEYS: list[str] = list(AUX_ROUTE_TABLE.keys())


def aux_config_key(short_key: str) -> str:
    """Return the bare Hermes config key for a short key.

    >>> aux_config_key("vision")
    'vision'
    >>> aux_config_key("web")
    'web_extract'
    """
    return AUX_ROUTE_TABLE[short_key]


def aux_hermes_path(short_key: str) -> str:
    """Return the full dotted path in ~/.hermes/config.yaml.

    >>> aux_hermes_path("vision")
    'auxiliary.vision'
    >>> aux_hermes_path("title")
    'auxiliary.title_generation'
    """
    return f"auxiliary.{AUX_ROUTE_TABLE[short_key]}"


def aux_display_triples() -> list[tuple[str, str, str]]:
    """Return (aux_role, bare_config_key, short_key) triples for display.

    The *bare_config_key* is the key inside the ``auxiliary`` dict of
    ``~/.hermes/config.yaml`` (e.g. ``"vision"``, ``"web_extract"``).
    It is NOT the dotted path — callers use it as
    ``aux_cfg.get(bare_key, {})``.
    """
    return [(f"aux_{sk}", AUX_ROUTE_TABLE[sk], sk) for sk in AUX_SHORT_KEYS]


def aux_harness_triples() -> list[tuple[str, str, str]]:
    """Return (short_key, hermes_bare_key, hermes_dotted_path) triples.

    Compatible with the existing ``AUX_MAP`` shape in route_runtime_harness.py.
    """
    return [(sk, AUX_ROUTE_TABLE[sk], aux_hermes_path(sk)) for sk in AUX_SHORT_KEYS]


__all__ = [
    "AUX_ROUTE_TABLE",
    "AUX_SHORT_KEYS",
    "aux_config_key",
    "aux_hermes_path",
    "aux_display_triples",
    "aux_harness_triples",
]
