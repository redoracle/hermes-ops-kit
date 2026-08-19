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
