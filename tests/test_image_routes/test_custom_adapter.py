"""Tests for CustomImageAdapter (OpenAI-compatible custom providers)."""

from __future__ import annotations

import base64
import os
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from hermes_ops_kit import ops_config_io


def _make_fake_openai() -> ModuleType:
    """Create a minimal fake openai module for tests."""
    mock_openai = ModuleType("openai")
    mock_client_class = MagicMock()
    mock_openai.OpenAI = mock_client_class
    return mock_openai


class TestCustomImageAdapter:
    """Test CustomImageAdapter for freeLLM and similar OpenAI-compatible image endpoints."""

    def test_is_available_with_credential(self) -> None:
        """Adapter is available when key and base_url are set."""
        from hermes_ops_kit.image_routes.adapters.custom_image import CustomImageAdapter

        adapter = CustomImageAdapter(
            provider_name="freellm",
            base_url="http://172.14.0.10:3001/v1",
            key_env="FREELLM_API_KEY",
        )

        with patch.dict(os.environ, {"FREELLM_API_KEY": "test-key"}):
            assert adapter.is_available() is True

    def test_is_available_missing_key(self, tmp_path, monkeypatch) -> None:
        """Adapter is unavailable when key is missing."""
        from hermes_ops_kit.image_routes.adapters.custom_image import CustomImageAdapter

        adapter = CustomImageAdapter(
            provider_name="freellm",
            base_url="http://172.14.0.10:3001/v1",
            key_env="FREELLM_API_KEY",
        )

        # clear=True also removes HERMES_HOME: pin the home so the kit never reads ~/.hermes
        monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
        with patch.dict(os.environ, {"FREELLM_API_KEY": ""}, clear=True):
            assert adapter.is_available() is False

    def test_is_available_missing_base_url(self) -> None:
        """Adapter is unavailable when base_url is empty."""
        from hermes_ops_kit.image_routes.adapters.custom_image import CustomImageAdapter

        adapter = CustomImageAdapter(
            provider_name="freellm",
            base_url="",
            key_env="FREELLM_API_KEY",
        )

        with patch.dict(os.environ, {"FREELLM_API_KEY": "test-key"}):
            assert adapter.is_available() is False

    def test_generate_returns_envelope(self, tmp_path: str, monkeypatch) -> None:
        """Successful generation returns image path in envelope."""
        from hermes_ops_kit.image_routes.adapters.custom_image import CustomImageAdapter

        adapter = CustomImageAdapter(
            provider_name="freellm",
            base_url="http://172.14.0.10:3001/v1",
            key_env="FREELLM_API_KEY",
            default_model="@cf/black-forest-labs/flux-1-schnell",
        )

        # Create a fake PNG
        png_data = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()

        mock_response = MagicMock()
        mock_image = MagicMock()
        mock_image.b64_json = png_data
        mock_response.data = [mock_image]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        # Patch openai module in sys.modules so import inside generate() finds it
        fake_openai = _make_fake_openai()
        fake_openai.OpenAI.return_value = mock_client
        monkeypatch.setitem(sys.modules, "openai", fake_openai)

        with (
            patch.dict(os.environ, {"FREELLM_API_KEY": "test-key"}),
            patch(
                "hermes_ops_kit.image_routes.adapters.custom_image.ensure_cache_dir"
            ) as mock_cache,
            patch(
                "hermes_ops_kit.image_routes.adapters.custom_image.make_output_path",
                return_value=str(tmp_path / "generated.png"),
            ),
            patch.object(adapter, "_start_timer") as mock_start,
        ):
            mock_cache.return_value = str(tmp_path)
            mock_start.return_value = 0.0

            result = adapter.generate(prompt="a red cube", model="@cf/black-forest-labs/flux-1-schnell")

        assert result["ok"] is True
        assert result["provider"] == "freellm"
        assert result["model"] == "@cf/black-forest-labs/flux-1-schnell"
        assert result["image_path"] is not None
        assert result["image_paths"]
        assert len(result["image_paths"]) == 1

    def test_generate_missing_key_returns_error(self, tmp_path, monkeypatch) -> None:
        """Missing key returns MissingAPIKey error."""
        from hermes_ops_kit.image_routes.adapters.custom_image import CustomImageAdapter

        adapter = CustomImageAdapter(
            provider_name="freellm",
            base_url="http://172.14.0.10:3001/v1",
            key_env="FREELLM_API_KEY",
        )

        monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
        with patch.dict(os.environ, {"FREELLM_API_KEY": ""}, clear=True):
            result = adapter.generate(prompt="a red cube")

        assert result["ok"] is False
        assert result["error_type"] == "MissingAPIKey"
        assert "FREELLM_API_KEY" in result["error"]

    def test_generate_api_error_returned(self, monkeypatch) -> None:
        """API errors are caught and returned in envelope."""
        from hermes_ops_kit.image_routes.adapters.custom_image import CustomImageAdapter

        adapter = CustomImageAdapter(
            provider_name="freellm",
            base_url="http://172.14.0.10:3001/v1",
            key_env="FREELLM_API_KEY",
        )

        mock_client = MagicMock()
        mock_client.images.generate.side_effect = Exception("429 rate limit")

        fake_openai = _make_fake_openai()
        fake_openai.OpenAI.return_value = mock_client
        monkeypatch.setitem(sys.modules, "openai", fake_openai)

        with (
            patch.dict(os.environ, {"FREELLM_API_KEY": "test-key"}),
            patch("hermes_ops_kit.image_routes.adapters.custom_image.ensure_cache_dir"),
        ):
            result = adapter.generate(prompt="a red cube")

        assert result["ok"] is False
        assert "rate limit" in result["error"].lower()


class TestRouterCustomDispatch:
    """Test router dispatch for custom:* providers."""

    def test_router_dispatches_custom_provider(self) -> None:
        """Router handles custom:freellm route config."""
        from hermes_ops_kit.image_routes.router import _get_adapter

        route_config = {
            "provider": "custom:freellm",
            "base_url": "http://172.14.0.10:3001/v1",
            "key_env": "FREELLM_API_KEY",
            "model": "@cf/black-forest-labs/flux-1-schnell",
            "label": "freeLLM flux schnell",
            "cost_class": "free",
            "priority": 15,
        }

        adapter = _get_adapter("custom:freellm", route_config)

        assert adapter.provider_name == "freellm"
        assert adapter._base_url == "http://172.14.0.10:3001/v1"
        assert adapter._key_env == "FREELLM_API_KEY"

    def test_router_unknown_provider_raises(self) -> None:
        """Router raises ValueError for unknown providers."""
        from hermes_ops_kit.image_routes.router import _get_adapter

        with pytest.raises(ValueError, match="Unknown image provider"):
            _get_adapter("nonexistent", {})


class TestHealthcheckCustom:
    """Test healthcheck handles custom providers correctly."""

    def test_healthcheck_shows_custom_route(self) -> None:
        """Healthcheck classifies custom routes by credential presence."""
        from hermes_ops_kit.image_routes.router import healthcheck

        # This test verifies the healthcheck doesn't crash on custom providers
        # The actual result depends on configured routes in image_routes.yaml
        result = healthcheck()
        assert "routes" in result
        assert "any_available" in result
