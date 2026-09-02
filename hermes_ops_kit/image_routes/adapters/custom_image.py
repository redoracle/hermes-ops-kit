"""Hermes Ops Kit — Custom (OpenAI-compatible) Image Generation Adapter.

Supports any provider that exposes an OpenAI-compatible /v1/images/generations
endpoint (e.g. FreeLLMAPI, local proxies, custom deployments).

Configuration comes from the route config in image_routes.yaml:
  - base_url: the API base URL (required)
  - key_env: env var name holding the API key (default: CUSTOM_API_KEY)
  - model: model ID (passed through to the API)
"""

from __future__ import annotations

import base64
import os

from .base import BaseImageAdapter, build_envelope, ensure_cache_dir, load_dotenv, make_output_path

# Size mapping — OpenAI-compatible image generation standard
_SIZE_MAP = {
    "landscape": "1024x1792",
    "square": "1024x1024",
    "portrait": "1792x1024",
}


class CustomImageAdapter(BaseImageAdapter):
    """Image adapter for OpenAI-compatible custom endpoints."""

    default_model: str = ""

    def __init__(
        self,
        provider_name: str,
        base_url: str,
        key_env: str = "CUSTOM_API_KEY",
        default_model: str = "",
    ) -> None:
        self._provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._key_env = key_env
        self.default_model = default_model or provider_name

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def is_available(self) -> bool:
        """Check that the API key is set and base_url is configured."""
        load_dotenv()
        key = os.environ.get(self._key_env, "").strip()
        return bool(key) and bool(self._base_url)

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        aspect_ratio: str = "landscape",
        num_images: int = 1,
        image_path: str | None = None,
        **kwargs,
    ) -> dict:
        load_dotenv()

        api_key = os.environ.get(self._key_env, "").strip()
        if not api_key:
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error=f"No credential set for env var {self._key_env}",
                error_type="MissingAPIKey",
            )

        if not self._base_url:
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error=f"No base_url configured for {self.provider_name}",
                error_type="ConfigError",
            )

        import openai  # pyright: ignore[reportMissingImports]

        model_id = model or self.default_model
        start = self._start_timer()
        image_paths = []
        ensure_cache_dir()

        size = _SIZE_MAP.get(aspect_ratio, "1024x1024")

        try:
            client = openai.OpenAI(api_key=api_key, base_url=self._base_url)

            for _ in range(min(num_images, 4)):
                response = client.images.generate(
                    model=model_id,
                    prompt=prompt,
                    size=size,
                    n=1,
                    response_format="b64_json",
                )

                data = getattr(response, "data", None) or []
                if not data:
                    continue

                first = data[0]
                b64 = getattr(first, "b64_json", None)
                url = getattr(first, "url", None)

                if b64:
                    raw = base64.b64decode(b64)
                    path = make_output_path(self.provider_name, "png")
                    with open(path, "wb") as f:
                        f.write(raw)
                    os.chmod(path, 0o644)
                    image_paths.append(path)
                elif url:
                    path = make_output_path(self.provider_name, "png")
                    self._download_image(url, path)
                    image_paths.append(path)

        except Exception as e:
            from ...security.redaction import redact

            return build_envelope(
                False,
                provider=self.provider_name,
                model=model_id,
                error=redact(str(e)),
                error_type=type(e).__name__,
                duration_ms=self._elapsed_ms(start),
            )

        if not image_paths:
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model_id,
                error="No images were generated.",
                error_type="NoImageData",
                duration_ms=self._elapsed_ms(start),
            )

        return build_envelope(
            True,
            provider=self.provider_name,
            model=model_id,
            image_paths=image_paths,
            caption=f"Generated with {self.provider_name} ({model_id})",
            duration_ms=self._elapsed_ms(start),
            extra={
                "aspect_ratio": aspect_ratio,
                "num_requested": num_images,
                "num_generated": len(image_paths),
                "size": size,
                "base_url": self._base_url,
            },
        )

    @staticmethod
    def _download_image(url: str, path: str, max_bytes: int = 25 * 1024 * 1024) -> None:
        """Download an image from a URL to a local path."""
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "Hermes-OpsKit/0.1"})
        tmp = path + ".download"
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(tmp, "wb") as f:
                    received = 0
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > max_bytes:
                            raise ValueError(f"Image exceeds {max_bytes // (1024 * 1024)}MB cap")
                        f.write(chunk)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
