from unittest.mock import Mock

import pytest

from hermes_ops_kit.image_routes import router
from hermes_ops_kit import tools
from hermes_ops_kit.hooks import transform_tool_result


@pytest.mark.parametrize("path", ["https://example.invalid/image.png", ".env"])
def test_reference_rejected_before_backend(tmp_path, monkeypatch, path):
    if path == ".env":
        file = tmp_path / path
        file.write_text("synthetic")
        path = str(file)
    adapter = Mock()
    monkeypatch.setattr(router, "_get_adapter", adapter)
    assert not router.generate("test", image_path=path)["ok"]
    adapter.assert_not_called()


def test_unsupported_edit_is_not_silently_generated(tmp_path, monkeypatch):
    image = tmp_path / "image.png"
    image.write_bytes(b"synthetic")
    monkeypatch.setattr(
        router,
        "_load_config",
        lambda: {
            "default_route": "quality",
            "routes": {"quality": {"provider": "openai"}},
        },
    )
    adapter = Mock()
    monkeypatch.setattr(router, "_get_adapter", adapter)
    assert (
        router.generate("edit", image_path=str(image))["error_type"]
        == "UnsupportedFeature"
    )
    adapter.assert_not_called()


def test_owned_image_output_is_redacted(monkeypatch):
    secret = "sk-" + "X" * 40
    monkeypatch.setattr(
        router, "generate", lambda **kwargs: {"ok": False, "error": secret}
    )
    assert secret not in tools.ai_image_generate({"prompt": "test"})
    assert secret not in transform_tool_result("test", {"nested": secret})
    assert transform_tool_result("test", object()) is None


# --- model override is scoped to the caller-selected route (F8) -----------------


def _routes(**overrides):
    cfg = {
        "default_route": "quality",
        "policies": {"prefer_local": False, "allow_cloud_fallback": True},
        "routes": {
            "local": {"provider": "local-comfyui", "model": "flux-local", "priority": 1},
            "quality": {"provider": "openai", "model": "gpt-image-2", "priority": 2},
            "fast": {"provider": "gemini", "model": "gemini-fast", "priority": 3},
            "alt": {"provider": "gemini", "model": "gemini-alt", "priority": 4},
        },
    }
    cfg["policies"].update(overrides.pop("policies", {}))
    cfg.update(overrides)
    return cfg


class _Adapters:
    """Fake adapters: per-provider availability and result; records generate() models."""

    def __init__(self, available=None, fail=()):
        self.available = available or {}
        self.fail = set(fail)
        self.calls = []

    def __call__(self, provider, route_cfg):
        outer = self

        class Adapter:
            def is_available(self):
                return outer.available.get(provider, True)

            def generate(self, **kwargs):
                outer.calls.append((provider, kwargs["model"]))
                if provider in outer.fail:
                    return {"ok": False, "error": "boom", "error_type": "RateLimit"}
                return {"ok": True, "provider": provider, "image_paths": []}

        return Adapter()


def _run(monkeypatch, config, adapters, **kwargs):
    monkeypatch.setattr(router, "_load_config", lambda: config)
    monkeypatch.setattr(router, "_get_adapter", adapters)
    return router.generate("prompt", **kwargs)


def test_override_applies_to_the_initial_route(monkeypatch):
    adapters = _Adapters()
    assert _run(monkeypatch, _routes(), adapters, model="custom-model")["ok"]
    assert adapters.calls == [("openai", "custom-model")]


def test_override_is_not_sent_to_cross_provider_fallback(monkeypatch):
    adapters = _Adapters(fail={"openai"})
    assert _run(monkeypatch, _routes(), adapters, model="custom-model")["ok"]
    assert adapters.calls == [("openai", "custom-model"), ("gemini", "gemini-fast")]


def test_override_is_not_sent_to_same_provider_fallback(monkeypatch):
    adapters = _Adapters(fail={"gemini"})
    config = _routes(default_route="fast")
    # fast (gemini) fails; the next candidate alt is also gemini but a different route.
    _run(monkeypatch, config, adapters, model="custom-model")
    assert adapters.calls[0] == ("gemini", "custom-model")
    assert all(model != "custom-model" for _, model in adapters.calls[1:])
    assert ("gemini", "gemini-alt") in adapters.calls


def test_override_is_not_sent_when_unavailable_route_falls_back(monkeypatch):
    adapters = _Adapters(available={"openai": False})
    assert _run(monkeypatch, _routes(), adapters, model="custom-model")["ok"]
    assert adapters.calls == [("gemini", "gemini-fast")]


def test_override_is_not_sent_to_local_first_substitute(monkeypatch):
    config = _routes(policies={"prefer_local": True})
    adapters = _Adapters()
    assert _run(monkeypatch, config, adapters, model="custom-model")["ok"]
    assert adapters.calls == [("local-comfyui", "flux-local")]


def test_override_resumes_on_selected_route_after_local_substitute_fails(monkeypatch):
    config = _routes(policies={"prefer_local": True})
    adapters = _Adapters(fail={"local-comfyui"})
    assert _run(monkeypatch, config, adapters, model="custom-model")["ok"]
    assert adapters.calls[0] == ("local-comfyui", "flux-local")
    assert ("openai", "custom-model") in adapters.calls


def test_multiple_fallbacks_each_use_their_configured_model(monkeypatch):
    adapters = _Adapters(fail={"openai", "gemini"})
    result = _run(monkeypatch, _routes(), adapters, model="custom-model")
    assert result["ok"] is True  # the local route is last in the chain and succeeds
    assert adapters.calls == [
        ("openai", "custom-model"),
        ("gemini", "gemini-fast"),
        ("gemini", "gemini-alt"),
        ("local-comfyui", "flux-local"),
    ]


def test_exhausted_fallbacks_terminate_without_looping(monkeypatch):
    adapters = _Adapters(fail={"openai", "gemini", "local-comfyui"})
    result = _run(monkeypatch, _routes(), adapters, model="custom-model")
    assert result["ok"] is False
    assert len(adapters.calls) <= 4  # each route visited at most once


def test_hard_config_error_does_not_fall_back(monkeypatch):
    class HardError(_Adapters):
        def __call__(self, provider, route_cfg):
            class Adapter:
                def is_available(self):
                    return True

                def generate(_self, **kwargs):
                    return {"ok": False, "error": "no key", "error_type": "MissingAPIKey"}

            return Adapter()

    result = _run(monkeypatch, _routes(), HardError(), model="custom-model")
    assert result["error_type"] == "MissingAPIKey"


def test_background_edit_passes_the_override_only_to_the_selected_route(tmp_path, monkeypatch):
    seen = {}

    def fake_edit_background(source_path, background_prompt, generate_background):
        seen["result"] = generate_background("a beach")
        return seen["result"]

    monkeypatch.setattr(
        "hermes_ops_kit.image_routes.background_edit.edit_background",
        fake_edit_background,
    )
    adapters = _Adapters(fail={"openai"})
    config = _routes()
    image = tmp_path / "in.png"
    image.write_bytes(b"synthetic")
    monkeypatch.setattr(router, "_load_config", lambda: config)
    monkeypatch.setattr(router, "_get_adapter", adapters)
    router.generate("p", image_path=str(image), edit_mode="edit_background", model="custom-model")
    assert adapters.calls[0] == ("openai", "custom-model")
    assert all(model != "custom-model" for _, model in adapters.calls[1:])
