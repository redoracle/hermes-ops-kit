"""Tests for env/render_env.py — env_projection load + provenance annotation."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from env import render_env  # pyright: ignore[reportMissingImports]


class _FakeBackend:
    """Minimal SecretBackend for rendering — only get_secret + get_metadata are used."""

    def __init__(self, secrets: dict[str, str]):
        self._secrets = secrets

    def get_secret(self, name: str):
        v = self._secrets.get(name)

        class _V:
            def __init__(self, val):
                self.value = val

        return _V(v) if v else None

    def get_metadata(self, name: str):
        class _M:
            renderable_to_env = True
            secret_class = "runtime"

        return _M()


def test_env_projection_loaded_from_yaml():
    # Latent-bug fix: env_projection.yaml entries are now loaded (previously
    # only deny_render was parsed; FIREWORKS/DEEPINFRA were never rendered).
    assert "FIREWORKS_API_KEY" in render_env.ENV_PROJECTION
    assert render_env.ENV_PROJECTION["FIREWORKS_API_KEY"] == "hermes/fireworks/api_key"
    assert "DEEPINFRA_API_KEY" in render_env.ENV_PROJECTION


def test_render_env_annotates_vaultwarden_source():
    backend = _FakeBackend({"hermes/fireworks/api_key": "fw-AAAAAAAAAA"})
    content = render_env.render_env_content(backend)
    assert 'FIREWORKS_API_KEY="fw-AAAAAAAAAA"' in content
    assert "source=vaultwarden:hermes/fireworks/api_key" in content


def test_render_env_core_conflict_annotation(monkeypatch):
    # When core's SecretSource also provides a var, the conflict is annotated.
    monkeypatch.setattr(
        "security.secret_source_bridge.core_secret_sources",
        lambda: {"OPENAI_API_KEY": "Bitwarden"},
    )
    backend = _FakeBackend({"hermes/openai/api_key": "sk-xxxxxxxxxxxxxxxx"})
    content = render_env.render_env_content(backend)
    assert "also core:Bitwarden" in content
    assert "core wins at runtime" in content


def test_render_env_no_core_annotation_when_standalone():
    # Standalone (core unavailable): no "also core:" annotation.
    backend = _FakeBackend({"hermes/openai/api_key": "sk-xxxxxxxxxxxxxxxx"})
    content = render_env.render_env_content(backend)
    assert "also core:" not in content
