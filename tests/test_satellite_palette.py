"""Tests for satellite_palette Lambda."""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image

from lambdas.satellite_palette.handler import (
    extract_palette,
    generate_swatch_png,
    rgb_to_hex,
)


def make_test_image(colors, size=(100, 100)):
    """Create a test image with horizontal color bands."""
    width, height = size
    img = Image.new("RGB", (width, height))

    band_height = height // len(colors)
    for i, color in enumerate(colors):
        y_start = i * band_height
        y_end = y_start + band_height if i < len(colors) - 1 else height
        for x in range(width):
            for y in range(y_start, y_end):
                img.putpixel((x, y), color)

    return img


def test_extract_palette_returns_5_to_7_colors():
    """From a test image with 5 color bands, palette should have 5-7 colors."""
    colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (128, 0, 128),
    ]
    img = make_test_image(colors, size=(100, 100))
    palette = extract_palette(img, n_colors=6)

    assert 5 <= len(palette) <= 7, f"Expected 5-7 colors, got {len(palette)}"


def test_extract_palette_order_by_prominence():
    """80% red + 20% blue should have red first."""
    width, height = 100, 100
    img = Image.new("RGB", (width, height))

    # 80 rows red, 20 rows blue
    for x in range(width):
        for y in range(80):
            img.putpixel((x, y), (255, 0, 0))
        for y in range(80, 100):
            img.putpixel((x, y), (0, 0, 255))

    palette = extract_palette(img, n_colors=2)

    assert len(palette) >= 2, f"Expected at least 2 colors, got {len(palette)}"
    # First color should be closer to red than to blue
    r, g, b = palette[0]
    assert r > b, f"Expected red-dominant first color, got ({r}, {g}, {b})"


def test_rgb_to_hex():
    """Test hex conversion."""
    assert rgb_to_hex((255, 0, 0)) == "#FF0000"
    assert rgb_to_hex((0, 128, 255)) == "#0080FF"


def test_generate_swatch_png():
    """Returns valid PNG bytes that can be opened as PIL Image."""
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    png_bytes = generate_swatch_png(colors)

    assert isinstance(png_bytes, bytes)
    assert len(png_bytes) > 0

    # Should be openable as a PIL Image
    img = Image.open(io.BytesIO(png_bytes))
    assert img.format == "PNG"
    assert img.size == (800, 200)
