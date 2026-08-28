"""Hermes Ops Kit — Base Image Generation Adapter.

Abstract interface for image generation backends.
All adapters must implement generate() and return the standard envelope.
"""

from __future__ import annotations

import os
import threading
import uuid
import time
from abc import ABC, abstractmethod
from typing import Any

from ...env import loader as _env_loader
from ...ops_config_io import HERMES_HOME


IMAGE_CACHE_DIR = os.path.join(HERMES_HOME, "cache", "images")
_ENV_LOADED = False
_ENV_LOCK = threading.Lock()


def load_dotenv() -> None:
    """Load environment variables from ~/.hermes/.env and ~/.hermes/.env.generated.

    Delegates to the kit's sole env parser (env/loader.py): generated wins
    over .env, real env vars are never clobbered.

    Called automatically by adapters before API key checks.
    Thread-safe: only loads once per process; safe to call repeatedly.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    with _ENV_LOCK:
        if _ENV_LOADED:
            return
        _env_loader.load_dotenv()
        _ENV_LOADED = True


# Standard aspect ratio mapping (provider-agnostic)
ASPECT_RATIO_MAP = {
    "landscape": "16:9",
    "square": "1:1",
    "portrait": "9:16",
}


def ensure_cache_dir() -> str:
    """Create the image cache directory if it doesn't exist."""
    os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
    return IMAGE_CACHE_DIR


def make_output_path(provider: str, ext: str = "png") -> str:
    """Generate a timestamped, unique output file path.

    Non-deterministic: uses current time and a random UUID.
    """
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = str(uuid.uuid4())[:8]
    return os.path.join(IMAGE_CACHE_DIR, f"{provider}_{ts}_{uid}.{ext}")


def build_envelope(
    ok: bool,
    provider: str,
    model: str,
    operation: str = "generate_image",
    image_path: str | None = None,
    image_paths: list[str] | None = None,
    caption: str = "",
    error: str = "",
    error_type: str = "",
    warnings: list[str] | None = None,
    duration_ms: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict:
    """Build the standardized image generation result envelope.

    Returns a dict compatible with Hermes image_gen result handling.
    When ok=True, MUST include image_path (primary) or image_paths (multi).
    """
    result: dict[str, Any] = {
        "ok": ok,
        "provider": provider,
        "operation": operation,
        "type": "image",
        "duration_ms": duration_ms,
    }

    if ok:
        paths = image_paths or ([image_path] if image_path else [])
        result.update(
            {
                "image_path": paths[0] if paths else None,
                "image_paths": paths,
                "mime_type": "image/png",
                "caption": caption,
                "model": model,
                "error": None,
                "error_type": None,
            }
        )
        if extra:
            result["extra"] = extra
    else:
        result.update(
            {
                "image_path": None,
                "image_paths": [],
                "mime_type": None,
                "caption": "",
                "model": model,
                "error": error or "Unknown error",
                "error_type": error_type or "GenerationError",
            }
        )

    if warnings:
        result["warnings"] = warnings

    return result


class BaseImageAdapter(ABC):
    """Abstract base for image generation backends.

    Subclasses must:
      - Set `provider_name` and `default_model`
      - Implement `generate(prompt, model, aspect_ratio, num_images) -> dict`
      - Optionally implement `is_available() -> bool`
    """

    provider_name: str = ""
    default_model: str = ""

    def is_available(self) -> bool:
        """Check if this adapter's backend is reachable.

        Override in subclasses for actual health checks.
        """
        return True

    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: str | None = None,
        aspect_ratio: str = "landscape",
        num_images: int = 1,
        image_path: str | None = None,
        **kwargs: Any,
    ) -> dict:
        """Generate an image. Returns the standard envelope dict.

        If image_path is provided and the adapter supports it,
        the image is used as reference for image-to-image generation.
        """
        ...

    def _start_timer(self) -> float:
        return time.time()

    def _elapsed_ms(self, start: float) -> int:
        return int((time.time() - start) * 1000)
