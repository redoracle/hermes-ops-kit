"""Hermes Ops Kit — Cloudflare Workers AI Image Generation Adapter.

Uses the Cloudflare Workers AI REST API for free-tier cloud image
generation (10k neurons/day shared) — e.g. @cf/black-forest-labs/flux-1-schnell
and @cf/stabilityai/stable-diffusion-xl-base-1.0.

Requires: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID environment variables.
Optional: CLOUDFLARE_AI_BASE_URL (defaults to
https://api.cloudflare.com/client/v4).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from ...env.loader import get_secret  # noqa: E402
from ...security.url_policy import origin_of
from ...provider_catalog import provider_key  # noqa: E402
from ...image_routes.adapters.base import (
    download_image,
    BaseImageAdapter,
    ensure_cache_dir,
    make_output_path,
    build_envelope,
)

DEFAULT_BASE_URL = "https://api.cloudflare.com/client/v4"


class CloudflareImageAdapter(BaseImageAdapter):
    provider_name = "cloudflare"
    default_model = "@cf/black-forest-labs/flux-1-schnell"

    def is_available(self) -> bool:
        """Check CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are set."""
        return bool(
            provider_key("cloudflare") and get_secret("CLOUDFLARE_ACCOUNT_ID")
        )

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        aspect_ratio: str = "landscape",
        num_images: int = 1,
        image_path: str | None = None,
        **kwargs,
    ) -> dict:
        if image_path:
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error="Cloudflare Workers AI adapter does not support "
                "image-to-image editing. Use the Gemini or OpenAI adapter "
                "for image modification.",
                error_type="UnsupportedFeature",
            )
        api_token = provider_key("cloudflare")
        account_id = get_secret("CLOUDFLARE_ACCOUNT_ID")
        if not api_token or not account_id:
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error="CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID not set "
                "in environment",
                error_type="MissingAPIKey",
            )

        model_id = model or self.default_model
        base_url = (get_secret("CLOUDFLARE_AI_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        run_url = f"{base_url}/accounts/{account_id}/ai/run/{model_id}"
        start = self._start_timer()
        image_paths = []
        ensure_cache_dir()

        try:
            for _ in range(min(num_images, 4)):
                payload = json.dumps({"prompt": prompt}).encode()
                req = urllib.request.Request(
                    run_url,
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {api_token}",
                        "Content-Type": "application/json",
                        # JSON response so we get base64 instead of raw bytes
                        "Accept": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = resp.read()

                path = make_output_path(self.provider_name, "png")
                content_type = resp.headers.get("Content-Type", "")
                if content_type.startswith("image/"):
                    # Raw image bytes
                    with open(path, "wb") as f:
                        f.write(body)
                else:
                    result = json.loads(body).get("result", {})
                    img_data = result.get("image", "")
                    if img_data.startswith("http"):
                        self._download_image(
                            img_data, path, allow_origins=(origin_of(base_url),)
                        )
                    else:
                        with open(path, "wb") as f:
                            f.write(base64.b64decode(img_data))
                os.chmod(path, 0o644)
                image_paths.append(path)

        except urllib.error.HTTPError as e:
            from ...security.redaction import redact

            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model_id,
                error=redact(f"HTTP {e.code} from Workers AI: {detail}"),
                error_type="HTTPError",
                duration_ms=self._elapsed_ms(start),
            )
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
                error="No images generated. Workers AI returned no image data.",
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
            },
        )

    @staticmethod
    def _download_image(
        url: str,
        path: str,
        max_bytes: int = 25 * 1024 * 1024,
        allow_origins: tuple[tuple[str, int] | None, ...] = (),
    ) -> None:
        """Download a provider-returned image URL (http(s) only, size-capped)."""
        download_image(
            url, path, timeout=120, max_bytes=max_bytes, allow_origins=allow_origins
        )
