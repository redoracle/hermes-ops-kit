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
