"""Hermes Ops Kit — OpenAI Image Generation Adapter.

Uses DALL-E 3 or gpt-image-2 via the openai SDK.
Returns local file paths (downloads from OpenAI URL, or decodes b64_json).

Model IDs:
  - dall-e-3: widely available, 1024x1024 to 1792x1024
  - gpt-image-2: latest model, returns b64_json
Pricing: paid, varies by model and resolution
"""

from __future__ import annotations

import base64
import os
import urllib.request


from ...provider_catalog import first_available_key, has_credential, key_envs_for  # noqa: E402

from ...image_routes.adapters.base import (
    BaseImageAdapter,
    load_dotenv,
    ensure_cache_dir,
    make_output_path,
    build_envelope,
)


# Size mapping for DALL-E 3 / gpt-image-2
_SIZE_MAP = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}

# Legacy DALL-E 3 sizes (different aspect ratio dimensions)
_DALLE_SIZE_MAP = {
    "landscape": "1792x1024",
    "square": "1024x1024",
    "portrait": "1024x1792",
}


class OpenAIImageAdapter(BaseImageAdapter):
    provider_name = "openai"
    default_model = "gpt-image-2"

    def is_available(self) -> bool:
        """Check that OPENAI_API_KEY is set and the SDK is importable."""
        load_dotenv()
        if not has_credential("openai"):
            return False
        try:
            import openai  # noqa: F401  # pyright: ignore[reportMissingImports]

            return True
        except ImportError:
            return False

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        aspect_ratio: str = "landscape",
        num_images: int = 1,
        image_path: str | None = None,
        **kwargs,
    ) -> dict:
        import openai  # pyright: ignore[reportMissingImports]

        load_dotenv()
        api_key = os.environ.get(first_available_key("openai") or "")
        if not api_key:
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error=f"No {self.provider_name} credential set (any of: {', '.join(key_envs_for('openai'))})",
                error_type="MissingAPIKey",
            )

        model_id = model or self.default_model
        start = self._start_timer()
        image_paths = []
        ensure_cache_dir()

        # Choose size map based on model
        size_map = _DALLE_SIZE_MAP if model_id == "dall-e-3" else _SIZE_MAP
        size = size_map.get(aspect_ratio, "1024x1024")

        try:
            client = openai.OpenAI(api_key=api_key)

            for _ in range(min(num_images, 4)):
                # Use client.images.generate for all models (same pattern as
                # Hermes native OpenAIImageGenProvider)
                payload = {
                    "model": model_id,
                    "prompt": prompt,
                    "size": size,
                    "n": 1,
                }
                # quality only supported by dall-e-3 and gpt-image models (different values)
                if model_id == "dall-e-3":
                    payload["quality"] = "standard"
                elif model_id.startswith("gpt-image"):
                    payload["quality"] = "auto"
                response = client.images.generate(**payload)

                data = getattr(response, "data", None) or []
                if not data:
                    continue

                first = data[0]
                b64 = getattr(first, "b64_json", None)
                url = getattr(first, "url", None)

                if b64:
                    # Decode base64 and save locally (gpt-image-2 returns this)
                    raw = base64.b64decode(b64)
                    path = make_output_path(self.provider_name, "png")
                    with open(path, "wb") as f:
                        f.write(raw)
                    os.chmod(path, 0o644)
                    image_paths.append(path)
                elif url:
                    # Download from URL (DALL-E 3 returns this)
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
                error="No images were generated. The API returned no image data.",
                error_type="NoImageData",
                duration_ms=self._elapsed_ms(start),
            )

        return build_envelope(
            True,
            provider=self.provider_name,
            model=model_id,
            image_paths=image_paths,
            caption=f"Generated with {model_id}",
            duration_ms=self._elapsed_ms(start),
            extra={
                "aspect_ratio": aspect_ratio,
                "num_requested": num_images,
                "num_generated": len(image_paths),
                "size": size,
            },
        )

    @staticmethod
    def _download_image(url: str, path: str, max_bytes: int = 25 * 1024 * 1024) -> None:
        """Download an image from a URL to a local path, streaming in chunks.

        Enforces a size cap to prevent memory exhaustion.
        """
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
                            raise ValueError(
                                f"Image exceeds {max_bytes // (1024 * 1024)}MB cap"
                            )
                        f.write(chunk)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
