"""Hermes Ops Kit — route_verifier credential recognition.

Regression test for the v0.19.0 audit drift fix: nvidia and zai must be
recognized as credential-bearing providers for AUX routes (previously
nvidia was missing from route_verifier's env_map, so an nvidia AUX route
falsely reported "no credential" even with NVIDIA_API_KEY set).

Run with: python3 -m pytest tests/test_route_verifier_creds.py -v
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import route_verifier  # pyright: ignore[reportMissingImports]
from config.route_map import AUX_SHORT_KEYS, aux_config_key  # pyright: ignore[reportMissingImports]
from ops_config_io import save_yaml  # pyright: ignore[reportMissingImports]


def _write_config_with_aux(path: str, provider: str, model: str) -> None:
    """Write a config.yaml whose first AUX route points at *provider*."""
    aux: dict = {}
    for sk in AUX_SHORT_KEYS:
        aux[aux_config_key(sk)] = {"provider": "gemini", "model": "gemini-2.5-flash"}
    first = aux_config_key(AUX_SHORT_KEYS[0])
    aux[first] = {"provider": provider, "model": model}
    save_yaml(path, {"auxiliary": aux})


def test_nvidia_aux_with_credential_is_not_a_gap(tmp_path, monkeypatch) -> None:
    cfg = str(tmp_path / "config.yaml")
    _write_config_with_aux(cfg, "nvidia", "nemotron-3-nano-30b-a3b")
    monkeypatch.setattr(
        route_verifier, "_load_env", lambda: {"NVIDIA_API_KEY": "nvapi-abc123def456"}
    )
    gaps = route_verifier.check_credential_gaps(cfg)
    assert not [g for g in gaps if g.get("provider") == "nvidia"]


def test_nvidia_aux_without_credential_is_a_gap(tmp_path, monkeypatch) -> None:
    cfg = str(tmp_path / "config.yaml")
    _write_config_with_aux(cfg, "nvidia", "nemotron-3-nano-30b-a3b")
    monkeypatch.setattr(route_verifier, "_load_env", lambda: {})
    gaps = route_verifier.check_credential_gaps(cfg)
    assert [g for g in gaps if g.get("provider") == "nvidia"]


def test_zai_aux_with_credential_is_not_a_gap(tmp_path, monkeypatch) -> None:
    cfg = str(tmp_path / "config.yaml")
    _write_config_with_aux(cfg, "zai", "glm-4.6")
    monkeypatch.setattr(
        route_verifier, "_load_env", lambda: {"GLM_API_KEY": "zai-key-value"}
    )
    gaps = route_verifier.check_credential_gaps(cfg)
    assert not [g for g in gaps if g.get("provider") == "zai"]
