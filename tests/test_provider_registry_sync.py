"""Hermes Ops Kit — cross-registry provider drift detection.

Asserts the provider lists across the independent registration sites stay in
sync. The Tier-2 audit found drift (nvidia missing from route_verifier, doctor
fallback hardcoded, etc.); this test prevents recurrence by pinning the
invariants:

  - bridge.PROVIDERS (dispatch) == usage_metrics_v2.PROVIDERS (health/usage UI)
  - every routable provider has a rotator in hermes_key_rotate.PROVIDER_ROTATORS
    (gemini routes via the google rotator — the only alias)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hermes_ops_kit.bridge as bridge  # pyright: ignore[reportMissingImports]
import hermes_ops_kit.usage_metrics_v2 as um  # pyright: ignore[reportMissingImports]
import hermes_ops_kit.hermes_key_rotate as hkr  # pyright: ignore[reportMissingImports]

# gemini is the user-facing provider name; its rotator is the Google rotator.
# Every other provider's rotator key matches its dispatch key.
_ROTOR_ALIASES = {"gemini": "google"}


def test_bridge_dispatch_matches_usage_registry() -> None:
    """The bridge dispatch registry and the usage/health registry must agree."""
    assert set(bridge.PROVIDERS.keys()) == set(um.PROVIDERS)


def test_every_provider_has_a_rotator() -> None:
    """Every routable provider must have a registered rotator (or a known alias)."""
    missing = []
    for provider in um.PROVIDERS:
        rotator_key = _ROTOR_ALIASES.get(provider, provider)
        if rotator_key not in hkr.PROVIDER_ROTATORS:
            missing.append(f"{provider} (looked for rotator '{rotator_key}')")
    assert not missing, f"Providers without a rotator: {missing}"


def test_usage_meta_covers_every_provider() -> None:
    """Every provider in the registry must have PROVIDER_META + PROVIDER_NAMES + DISPLAY_ORDER."""
    for p in um.PROVIDERS:
        assert p in um.PROVIDER_NAMES, f"{p} missing from PROVIDER_NAMES"
        assert p in um.PROVIDER_META, f"{p} missing from PROVIDER_META"
    assert set(um.DISPLAY_ORDER) == set(um.PROVIDERS), (
        "DISPLAY_ORDER must list exactly PROVIDERS"
    )


def test_bridge_capabilities_cover_every_provider() -> None:
    """Every dispatchable provider must have a CAPABILITIES entry."""
    for p in bridge.PROVIDERS:
        assert p in bridge.CAPABILITIES, f"{p} missing from CAPABILITIES"


def test_capabilities_models_match_catalog() -> None:
    """CAPABILITIES model lists must be exactly the provider_catalog lists."""
    from hermes_ops_kit.provider_catalog import PROVIDER_MODELS

    for p, meta in bridge.CAPABILITIES.items():
        assert meta["models"] == PROVIDER_MODELS.get(p, []), (
            f"{p}: CAPABILITIES models drifted from provider_catalog"
        )


def test_preferred_models_subset_of_catalog() -> None:
    """PROVIDER_META preferred_models must not name models outside the catalog."""
    from hermes_ops_kit.provider_catalog import PROVIDER_MODELS

    for p, meta in um.PROVIDER_META.items():
        catalog = set(PROVIDER_MODELS.get(p, []))
        # nvidia preferred list omits the vendor prefix used in the catalog
        if p == "nvidia":
            catalog |= {m.split("/", 1)[1] for m in catalog if "/" in m}
        # github/copilot routes openai+anthropic models through its CLI surface
        if p == "github":
            catalog |= set(PROVIDER_MODELS.get("openai", [])) | set(
                PROVIDER_MODELS.get("anthropic", [])
            )
        unknown = [m for m in meta["preferred_models"] if m not in catalog]
        assert not unknown, f"{p}: preferred models not in catalog: {unknown}"


def test_builtin_profile_models_in_catalog() -> None:
    """BUILTIN_PROFILES must only reference catalog models."""
    from hermes_ops_kit.provider_catalog import PROVIDER_MODELS
    from hermes_ops_kit.hermes_route_manager import BUILTIN_PROFILES

    for name, profile in BUILTIN_PROFILES.items():
        pairs = [(profile["primary"]["provider"], profile["primary"]["model"])]
        pairs.append((profile["utility"]["provider"], profile["utility"]["model"]))
        for fb in profile.get("fallbacks", []):
            pairs.append((fb["provider"], fb["model"]))
        for provider, model in pairs:
            if provider in ("copilot",):
                provider = "github"
            catalog = set(PROVIDER_MODELS.get(provider, []))
            if provider == "nvidia" and "/" in model:
                catalog |= {m.split("/", 1)[1] for m in catalog if "/" in m}
            if catalog:
                assert model in catalog, (
                    f"profile {name}: {provider}/{model} not in catalog"
                )


def test_base_urls_derive_from_catalog() -> None:
    """Adapter/rotator base_url defaults must equal provider_catalog."""
    import importlib

    from hermes_ops_kit.provider_catalog import PROVIDER_BASE_URLS

    for provider in ("deepseek", "nvidia", "fireworks", "deepinfra"):
        for suffix in ("adapter", "rotator"):
            mod = importlib.import_module(
                f"hermes_ops_kit.providers.{provider}_{suffix}"
            )
            default, env_var = PROVIDER_BASE_URLS[provider]
            assert getattr(mod, "base_url_default", default) == default, (
                f"{provider}_{suffix}.base_url_default drifted"
            )
            assert getattr(mod, "base_url_env", env_var) == env_var, (
                f"{provider}_{suffix}.base_url_env drifted"
            )


def test_copilot_models_single_copy() -> None:
    """usage_metrics_v2 must use provider_catalog.COPILOT_MODELS."""
    from hermes_ops_kit.provider_catalog import COPILOT_MODELS

    assert isinstance(COPILOT_MODELS, dict) and COPILOT_MODELS["github"] == [
        "raptor-mini"
    ]
    import inspect

    import hermes_ops_kit.usage_metrics_v2 as umv2

    src = inspect.getsource(umv2)
    assert '"raptor-mini"' not in src.replace(
        'from .provider_catalog import', ''
    ), "copilot model list duplicated in usage_metrics_v2"


def test_budget_provider_classes_in_catalog() -> None:
    """budget.yaml provider groupings must reference catalog providers."""
    from hermes_ops_kit.cost_governor.budget import DEFAULT_BUDGET
    from hermes_ops_kit.provider_catalog import PROVIDER_MODELS

    known = set(PROVIDER_MODELS)
    for classes in DEFAULT_BUDGET["provider_classes"].values():
        unknown = [p for p in classes if p not in known]
        assert not unknown, f"budget provider_classes unknown: {unknown}"


def test_providers_subset_of_catalog() -> None:
    """Every probed provider must exist in provider_catalog.PROVIDER_ENV_KEYS.

    usage_metrics_v2.PROVIDERS is the *probed* subset (openrouter/zai/copilot
    are catalog-only); this pins name drift — a provider renamed or typo'd in
    either place fails here.
    """
    from hermes_ops_kit.provider_catalog import PROVIDER_ENV_KEYS

    unknown = [p for p in um.PROVIDERS if p not in PROVIDER_ENV_KEYS]
    assert not unknown, f"PROVIDERS not backed by provider_catalog: {unknown}"
