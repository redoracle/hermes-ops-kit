from pathlib import Path

from PIL import Image, ImageDraw

from image_routes.background_edit import (
    build_background_only_prompt,
    build_composite_masks,
    build_subject_mask,
    composite_background,
    decontaminate_dark_edge,
)


def _close_rgb(
    actual: tuple[int, int, int], expected: tuple[int, int, int], delta: int = 2
) -> bool:
    return all(abs(a - e) <= delta for a, e in zip(actual, expected))


def test_subject_mask_preserves_internal_black_details():
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((5, 5, 14, 14), fill=(220, 180, 140, 255))
    draw.rectangle((8, 8, 11, 10), fill=(0, 0, 0, 255))

    mask = build_subject_mask(img, black_threshold=18, feather_px=0)

    assert mask.getpixel((0, 0)) == 0
    assert mask.getpixel((6, 6)) == 255
    assert mask.getpixel((9, 9)) == 255


def test_background_prompt_strips_subject_edit_language():
    prompt = build_background_only_prompt(
        "Modifica solo lo sfondo nero dell'immagine originale, sostituendolo "
        "con dolci a base di crema chantilly. Mantieni il soggetto identico."
    )

    lower = prompt.lower()
    assert "chantilly" in lower
    assert "pasticceria" in lower
    assert "modifica" not in lower
    assert "immagine originale" not in lower
    assert "mantieni" not in lower
    assert "soggetto identico" not in lower
    assert "no people" in lower
    assert "no person" in lower


def test_composite_replaces_only_background(tmp_path: Path):
    source = Image.new("RGBA", (40, 40), (0, 0, 0, 255))
    draw = ImageDraw.Draw(source)
    draw.rectangle((10, 10, 29, 29), fill=(220, 180, 140, 255))
    draw.rectangle((17, 17, 22, 20), fill=(0, 0, 0, 255))

    background = Image.new("RGBA", (40, 40), (255, 0, 0, 255))
    source_path = tmp_path / "source.png"
    background_path = tmp_path / "background.png"
    output_path = tmp_path / "output.png"
    source.save(source_path)
    background.save(background_path)

    composite_background(str(source_path), str(background_path), str(output_path))
    result = Image.open(output_path).convert("RGBA")

    assert result.getpixel((0, 0))[:3] == (255, 0, 0)
    assert _close_rgb(result.getpixel((25, 25))[:3], (220, 180, 140))
    assert result.getpixel((19, 18))[:3] == (0, 0, 0)


def test_dark_edge_cleanup_preserves_internal_black_detail():
    source = Image.new("RGBA", (30, 30), (0, 0, 0, 255))
    draw = ImageDraw.Draw(source)
    draw.rectangle((8, 8, 21, 21), fill=(220, 180, 140, 255))
    draw.rectangle((8, 8, 8, 21), fill=(5, 5, 5, 255))
    draw.rectangle((13, 13, 16, 15), fill=(0, 0, 0, 255))

    cleanup_mask, _core_mask, _feathered_mask = build_composite_masks(source)
    clean = decontaminate_dark_edge(source, cleanup_mask)

    assert clean.getpixel((8, 14))[:3] != (5, 5, 5)
    assert clean.getpixel((13, 14))[:3] == (0, 0, 0)
