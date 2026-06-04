"""Hermes Ops Kit — Gemini Image Generation Adapter.

Uses Gemini 2.5 Flash Image ("Nano Banana") via the google.genai SDK.
Native multimodal output: response_modalities=["IMAGE"].

Model ID: gemini-2.5-flash-image (GA stable)
Pricing: ~$0.039 per image (1,290 output tokens)
Free tier: available via AI Studio/Gemini API
"""

from __future__ import annotations

import base64
import os
import sys

# Allow importing from parent package
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from image_routes.adapters.base import (
    BaseImageAdapter,
    load_dotenv,
    make_output_path,
    build_envelope,
    ASPECT_RATIO_MAP,
)


class GeminiImageAdapter(BaseImageAdapter):
    provider_name = "gemini"
    default_model = "gemini-2.5-flash-image"

    def is_available(self) -> bool:
        """Check that GEMINI_API_KEY is set and the SDK is importable."""
        load_dotenv()
        if not os.environ.get("GEMINI_API_KEY"):
            return False
        try:
            from google import genai  # noqa: F401  # pyright: ignore[reportAttributeAccessIssue]

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
        from google import genai  # pyright: ignore[reportAttributeAccessIssue]
        from google.genai import types  # pyright: ignore[reportMissingImports]

        load_dotenv()
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error="GEMINI_API_KEY not set in environment",
                error_type="MissingAPIKey",
            )

        model_id = model or self.default_model
        ratio = ASPECT_RATIO_MAP.get(aspect_ratio, "16:9")

        client = genai.Client(api_key=api_key)

        start = self._start_timer()
        image_paths = []
        caption = ""

        # Ensure output directory exists
        from image_routes.adapters.base import ensure_cache_dir

        ensure_cache_dir()

        try:
            # Build contents list — include reference image if provided
            contents: list = []
            if image_path and os.path.exists(os.path.expanduser(image_path)):
                import mimetypes

                resolved = os.path.expanduser(image_path)
                mime_type, _ = mimetypes.guess_type(resolved)
                if not mime_type or not mime_type.startswith("image/"):
                    mime_type = "image/png"
                with open(resolved, "rb") as img_file:
                    image_bytes = img_file.read()
                contents.append(
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                )
                # Use multi-image instruction when num_images > 1
                if num_images > 1:
                    full_prompt = (
                        f"Generate {num_images} variations of the following edit. "
                        f"Each image should be a distinct variation of the modification:\n\n{prompt}"
                    )
                    contents.append(full_prompt)
                else:
                    contents.append(prompt)
            else:
                # Text-only prompt (with multi-image instruction if needed)
                full_prompt = prompt
                if num_images > 1:
                    full_prompt = (
                        f"Generate {num_images} variations of the following description. "
                        f"Each image should be a distinct variation:\n\n{prompt}"
                    )
                contents = [full_prompt]

            response = client.models.generate_content(
                model=model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio=ratio),
                ),
            )

            # Extract image parts from response
            text_parts = []
            for part in (
                response.candidates[0].content.parts if response.candidates else []
            ):
                if hasattr(part, "text") and part.text:
                    text_parts.append(part.text)
                if hasattr(part, "inline_data") and part.inline_data:
                    raw_data = part.inline_data.data
                    mime = getattr(part.inline_data, "mime_type", "image/png")
                    ext = mime.split("/")[-1] if "/" in mime else "png"
                    # Handle both raw bytes and base64-encoded strings
                    if isinstance(raw_data, (bytes, bytearray)):
                        img_bytes = raw_data
                    elif isinstance(raw_data, str):
                        img_bytes = base64.b64decode(raw_data)
                    else:
                        raise TypeError(
                            f"Unexpected inline_data type: {type(raw_data).__name__}"
                        )
                    path = make_output_path(self.provider_name, ext)
                    with open(path, "wb") as f:
                        f.write(img_bytes)
                    os.chmod(path, 0o644)
                    image_paths.append(path)

            caption = (
                " ".join(text_parts).strip()
                if text_parts
                else f"Generated with {model_id}"
            )

            if not image_paths:
                return build_envelope(
                    False,
                    provider=self.provider_name,
                    model=model_id,
                    error="No image data in Gemini response. The model may have returned text-only output.",
                    error_type="NoImageData",
                    duration_ms=self._elapsed_ms(start),
                )

        except Exception as e:
            from security.redaction import redact

            return build_envelope(
                False,
                provider=self.provider_name,
                model=model_id,
                error=redact(str(e)),
                error_type=type(e).__name__,
                duration_ms=self._elapsed_ms(start),
            )

        return build_envelope(
            True,
            provider=self.provider_name,
            model=model_id,
            image_paths=image_paths,
            caption=caption,
            duration_ms=self._elapsed_ms(start),
            extra={
                "aspect_ratio": aspect_ratio,
                "num_requested": num_images,
                "num_generated": len(image_paths),
            },
        )
