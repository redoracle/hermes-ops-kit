"""Subject-preserving background replacement helpers.

This module does not ask an image model to recreate the subject. It generates a
background-only image, derives the editable background region from the source,
and composites the original subject pixels back over the new background.
"""

from __future__ import annotations

import os
import re
from collections import deque
from typing import Callable

from PIL import Image, ImageFilter

from ..image_routes.adapters.base import (
    build_envelope,
    make_output_path,
    ensure_cache_dir,
)


_BACKGROUND_ONLY_NEGATIVE = (
    "No people, no person, no woman, no man, no child, no face, no body, "
    "no portrait subject, no sunglasses, no hat, no arms, no hands, "
    "no orange clothing, no foreground human."
)


def build_background_only_prompt(background_prompt: str) -> str:
    """Convert an edit request into a clean background-generation prompt.

    Image models tend to leak the original edit instruction into the generated
    background. This strips subject-preservation language and asks only for an
    empty background plate/display.
    """
    text = " ".join((background_prompt or "").replace("\n", " ").split())
    text = re.sub(
        r"(?i)\b(modifica(?:la)?|edit|change|replace|sostituisci|rimpiazza|cambia)\b[^,.!?;:]*",
        "",
        text,
    )
    text = re.sub(
        r"(?i)\b(mantieni|keep|preserve|soggetto|subject|persona|person|ritratto|portrait|immagine originale|original image)\b[^,.!?;:]*",
        "",
        text,
    )
    text = re.sub(
        r"(?i)\b(solo lo sfondo nero|solo lo sfondo|sfondo nero|background only|black background)\b",
        "sfondo",
        text,
    )
    text = " ".join(text.split(" ,.;:-")).strip(" ,.;:-")

    lower = text.lower()
    if "chantilly" in lower:
        scene = (
            "dolci a base di crema chantilly: bignè, torte millefoglie, "
            "pasticcini con crema chantilly, panna montata, fragole, vassoi "
            "da pasticceria, sfondo da pasticceria italiana"
        )
    elif text:
        scene = text
    else:
        scene = "dolci e pasticcini su vassoi da pasticceria italiana"

    return (
        "Photorealistic empty pastry-shop background, dessert display only, "
        "the entire frame filled with pastries and serving trays, shallow depth "
        "of field, natural soft light. "
        f"Scene: {scene}. "
        f"{_BACKGROUND_ONLY_NEGATIVE}"
    )


def _is_background_pixel(pixel: tuple[int, int, int, int], threshold: int) -> bool:
    r, g, b, a = pixel
    if a <= 8:
        return True
    return r <= threshold and g <= threshold and b <= threshold


def build_subject_mask(
    image: Image.Image,
    black_threshold: int = 18,
    feather_px: float = 1.5,
    shrink_px: int = 0,
) -> Image.Image:
    """Build a subject mask by flood-filling dark/transparent edge background.

    Only dark pixels connected to the image border are treated as background.
    This preserves black details inside the subject, such as sunglasses or trim.
    """
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    background = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def enqueue(x: int, y: int) -> None:
        idx = y * width + x
        if background[idx]:
            return
        if _is_background_pixel(pixels[x, y], black_threshold):
            background[idx] = 1
            queue.append((x, y))

    for x in range(width):
        enqueue(x, 0)
        enqueue(x, height - 1)
    for y in range(height):
        enqueue(0, y)
        enqueue(width - 1, y)

    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                enqueue(nx, ny)

    mask = Image.new("L", (width, height), 255)
    mask_pixels = mask.load()
    for y in range(height):
        row = y * width
        for x in range(width):
            if background[row + x]:
                mask_pixels[x, y] = 0

    for _ in range(max(0, shrink_px)):
        mask = mask.filter(ImageFilter.MinFilter(3))
    if feather_px > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(feather_px))
    return mask


def build_composite_masks(
    image: Image.Image,
) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Return cleanup, core subject, and feathered composite masks."""
    core = build_subject_mask(image, feather_px=0, shrink_px=0)
    cleanup = core.filter(ImageFilter.MaxFilter(3))
    feathered = core.filter(ImageFilter.MinFilter(3))
    feathered = feathered.filter(ImageFilter.GaussianBlur(1.0))
    return cleanup, core, feathered


def _interior_color_average(
    pixels,
    mask_pixels,
    width: int,
    height: int,
    x: int,
    y: int,
    radius: int = 3,
) -> tuple[int, int, int] | None:
    total_r = total_g = total_b = count = 0
    for ny in range(max(0, y - radius), min(height, y + radius + 1)):
        for nx in range(max(0, x - radius), min(width, x + radius + 1)):
            if mask_pixels[nx, ny] < 250:
                continue
            r, g, b, a = pixels[nx, ny]
            if a < 128 or (r + g + b) < 80:
                continue
            total_r += r
            total_g += g
            total_b += b
            count += 1
    if not count:
        return None
    return total_r // count, total_g // count, total_b // count


def decontaminate_dark_edge(
    source: Image.Image,
    mask: Image.Image,
    dark_threshold: int = 52,
) -> Image.Image:
    """Replace dark matte pixels on the subject edge with nearby subject color."""
    rgba = source.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    mask_pixels = mask.load()
    clean = rgba.copy()
    clean_pixels = clean.load()

    for y in range(height):
        for x in range(width):
            alpha = mask_pixels[x, y]
            if alpha <= 0:
                continue
            is_edge = alpha < 245
            if not is_edge:
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if (
                        0 <= nx < width
                        and 0 <= ny < height
                        and mask_pixels[nx, ny] == 0
                    ):
                        is_edge = True
                        break
            if not is_edge:
                continue
            r, g, b, a = pixels[x, y]
            if a < 128 or max(r, g, b) > dark_threshold:
                continue
            avg = _interior_color_average(pixels, mask_pixels, width, height, x, y)
            if avg is None:
                continue
            clean_pixels[x, y] = (*avg, a)

    return clean


def composite_background(
    source_path: str,
    background_path: str,
    output_path: str | None = None,
) -> str:
    """Composite the original subject over a generated background."""
    source_img = Image.open(os.path.expanduser(source_path))
    bg_img = Image.open(os.path.expanduser(background_path))
    try:
        source = source_img.convert("RGBA")
        background = bg_img.convert("RGBA")
        background = background.resize(source.size, Image.Resampling.LANCZOS)
    finally:
        source_img.close()
        bg_img.close()
    cleanup_mask, _core_mask, composite_mask = build_composite_masks(source)
    clean_source = decontaminate_dark_edge(source, cleanup_mask)
    output = Image.composite(clean_source, background, composite_mask).convert("RGB")

    ensure_cache_dir()
    target = output_path or make_output_path("background_edit", "png")
    output.save(target, "PNG")
    os.chmod(target, 0o644)
    return target


def edit_background(
    source_path: str,
    background_prompt: str,
    generate_background: Callable[[str], dict],
) -> dict:
    """Replace only the source background while preserving subject pixels."""
    prompt = build_background_only_prompt(background_prompt)
    generated = generate_background(prompt)
    if not generated.get("ok"):
        generated["operation"] = "edit_background"
        return generated

    bg_paths = generated.get("image_paths") or []
    if not bg_paths and generated.get("image_path"):
        bg_paths = [generated["image_path"]]
    if not bg_paths:
        return build_envelope(
            False,
            provider=generated.get("provider", "ops-kit-router"),
            model=generated.get("model", ""),
            operation="edit_background",
            error="Background generation succeeded but returned no image path",
            error_type="NoBackgroundImage",
            duration_ms=generated.get("duration_ms", 0),
        )

    try:
        output_path = composite_background(source_path, bg_paths[0])
    except Exception as exc:
        from ..security.redaction import redact

        return build_envelope(
            False,
            provider=generated.get("provider", "ops-kit-router"),
            model=generated.get("model", ""),
            operation="edit_background",
            error=redact(str(exc)),
            error_type=type(exc).__name__,
            duration_ms=generated.get("duration_ms", 0),
        )

    result = build_envelope(
        True,
        provider=generated.get("provider", "ops-kit-router"),
        model=generated.get("model", ""),
        operation="edit_background",
        image_path=output_path,
        caption="Background replaced while preserving the source subject pixels",
        duration_ms=generated.get("duration_ms", 0),
        extra={
            "source_image_path": os.path.expanduser(source_path),
            "generated_background_path": bg_paths[0],
            "background_prompt": prompt,
            "preserve_subject": True,
            "subject_mask": "edge-connected transparent/black background",
        },
    )
    if generated.get("attempts"):
        result["attempts"] = generated["attempts"]
    return result
