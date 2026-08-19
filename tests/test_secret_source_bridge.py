"""Tests for security/secret_source_bridge.py — core SecretSource provenance query."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes_ops_kit.security import secret_source_bridge as ssb  # pyright: ignore[reportMissingImports]


def test_core_secret_sources_returns_dict_never_raises():
    # In ops-kit standalone hermes_cli is not importable → {}. In-Hermes it may
    # return a populated dict. Either way it must be a dict and never raise.
    r = ssb.core_secret_sources()
    assert isinstance(r, dict)


def test_core_secret_sources_empty_when_hermes_cli_unavailable(monkeypatch):
    # Force hermes_cli to be absent — the bridge must degrade to {}.
    import builtins

    real_import = builtins.__import__

    def _fail(name, *a, **k):
        if name.startswith("hermes_cli"):
            raise ImportError("forced unavailable")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fail)
    assert ssb.core_secret_sources() == {}
