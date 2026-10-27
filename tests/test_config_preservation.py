from unittest.mock import Mock

import pytest

from hermes_ops_kit import _ensure_image_gen_config, ops_config_io, tools
from hermes_ops_kit.image_routes import router


@pytest.mark.parametrize(
    "text",
    [
        "image_gen: {provider: chosen}\nother: 42\n",
        "# preserve this comment\nimage_gen:\n  provider: chosen\nother: 42\n",
    ],
)
def test_setup_preserves_existing_values_and_structure(tmp_path, monkeypatch, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    _ensure_image_gen_config()
    config = ops_config_io.load_yaml_strict(str(path))
    assert config["image_gen"] == {"provider": "chosen", "model": "auto"}
    assert config["other"] == 42
    if text.startswith("#"):
        assert "# preserve this comment" in path.read_text()


@pytest.mark.parametrize(
    "text", ["image_gen: [", "image_gen: bad\n", "image_gen: {}\nimage_gen: {}\n"]
)
def test_invalid_config_is_not_replaced(tmp_path, monkeypatch, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    _ensure_image_gen_config()
    assert path.read_text() == text


def test_unknown_route_and_provider_fail_before_transport(monkeypatch):
    adapter = Mock()
    monkeypatch.setattr(router, "_get_adapter", adapter)
    monkeypatch.setattr(
        router,
        "_load_config",
        lambda: {"default_route": "paid", "routes": {"paid": {"provider": "openai"}}},
    )
    assert router.generate("test", route_name="typo")["error_type"] == "ConfigError"
    assert '"ok": false' in tools.ai_image_generate(
        {"prompt": "test", "provider": "typo"}
    )
    adapter.assert_not_called()


def test_generation_uses_one_configuration_snapshot(monkeypatch):
    config = {
        "default_route": "fast",
        "routes": {"fast": {"provider": "gemini"}},
        "policies": {"prefer_local": False},
    }
    load = Mock(return_value=config)
    monkeypatch.setattr(router, "_load_config", load)
    backend = Mock()
    backend.is_available.return_value = True
    backend.generate.return_value = {"ok": True}
    monkeypatch.setattr(router, "_get_adapter", lambda *args: backend)
    assert router.generate("test")["ok"]
    load.assert_called_once()


def test_provider_selection_and_generation_share_snapshot(monkeypatch):
    load = Mock(
        side_effect=[
            {
                "default_route": "fast",
                "routes": {"fast": {"provider": "gemini"}},
                "policies": {"prefer_local": False},
            },
            {"default_route": "fast", "routes": {"fast": {"provider": "openai"}}},
        ]
    )
    monkeypatch.setattr(router, "_load_config", load)
    backend = Mock()
    backend.generate.return_value = {"ok": True}
    select = Mock(return_value=backend)
    monkeypatch.setattr(router, "_get_adapter", select)
    assert '"ok": true' in tools.ai_image_generate(
        {"prompt": "test", "provider": "gemini"}
    )
    load.assert_called_once()
    assert select.call_args.args[0] == "gemini"
