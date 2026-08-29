"""Hermes Ops Kit — FAL.ai Image Generation Adapter.

Uses FAL.ai REST API for cloud image generation.
Supports Flux, Stable Diffusion, and other models via FAL queue API.

Requires: FAL_KEY environment variable
"""

from __future__ import annotations

import json
import os
import urllib.request


from ...provider_catalog import first_available_key, has_credential, key_envs_for  # noqa: E402
from ...image_routes.adapters.base import (
    BaseImageAdapter,
    load_dotenv,
    ensure_cache_dir,
    make_output_path,
    build_envelope,
    ASPECT_RATIO_MAP,
)


class FALImageAdapter(BaseImageAdapter):
    provider_name = "fal"
    default_model = "fal-ai/flux-2-pro"

    def is_available(self) -> bool:
        """Check FAL_KEY is set and FAL API is reachable."""
        load_dotenv()
        return has_credential("fal")

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
                error="FAL.ai adapter does not support image-to-image editing. "
                "Use the Gemini or OpenAI adapter for image modification.",
                error_type="UnsupportedFeature",
            )
        load_dotenv()
        api_key = os.environ.get(first_available_key("fal") or "")
        if not api_key:
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error=f"No {self.provider_name} credential set (any of: {', '.join(key_envs_for('fal'))})",
                error_type="MissingAPIKey",
            )

        model_id = model or self.default_model
        ratio = ASPECT_RATIO_MAP.get(aspect_ratio, "16:9")
        start = self._start_timer()
        image_paths = []
        ensure_cache_dir()

        try:
            for _ in range(min(num_images, 4)):
                # FAL queue API: submit → poll → download
                submit_url = f"https://queue.fal.run/{model_id}"
                payload = json.dumps(
                    {
                        "prompt": prompt,
                        "image_size": ratio,
                        "num_images": 1,
                    }
                ).encode()

                # Submit
                req = urllib.request.Request(
                    submit_url,
                    data=payload,
                    headers={
                        "Authorization": f"Key {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    submit_result = json.loads(resp.read())

                # Poll for result (FAL returns request_id, check status endpoint)
                request_id = submit_result.get("request_id", "")
                if not request_id:
                    # Some models return direct results
                    img_url = (
                        submit_result.get("images", [{}])[0].get("url", "")
                        if submit_result.get("images")
                        else submit_result.get("image", {}).get("url", "")
                    )
                    if img_url:
                        path = make_output_path(self.provider_name, "png")
                        self._download_image(img_url, path)
                        image_paths.append(path)
                    continue

                # Poll for completion with exponential backoff
                status_url = (
                    f"https://queue.fal.run/{model_id}/requests/{request_id}/status"
                )
                import time

                delay = 1.0  # start with 1s; first check is immediate
                for _ in range(40):  # ~120s max wait
                    # Check immediately before sleeping (except first iteration)
                    req2 = urllib.request.Request(
                        status_url,
                        headers={"Authorization": f"Key {api_key}"},
                    )
                    with urllib.request.urlopen(req2, timeout=10) as resp2:
                        status = json.loads(resp2.read())
                    st = status.get("status", "")
                    if st == "COMPLETED":
                        img_url = (
                            status.get("images", [{}])[0].get("url", "")
                            if status.get("images")
                            else status.get("image", {}).get("url", "")
                        )
                        if img_url:
                            path = make_output_path(self.provider_name, "png")
                            self._download_image(img_url, path)
                            image_paths.append(path)
                        break
                    elif st in ("FAILED", "CANCELLED"):
                        # Capture failure reason for diagnostics
                        failure_detail = json.dumps(status, default=str)[:500]
                        raise RuntimeError(
                            f"FAL request {request_id} status={st}: {failure_detail}"
                        )
                    time.sleep(delay)
                    delay = min(delay * 1.5, 8.0)  # exponential backoff, cap at 8s

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
                error="No images generated. FAL queue may have timed out or returned no results.",
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
    def _download_image(url: str, path: str) -> None:
        """Download an image from a URL to a local path."""
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes-OpsKit/0.1"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(path, "wb") as f:
                f.write(resp.read())
        os.chmod(path, 0o644)
