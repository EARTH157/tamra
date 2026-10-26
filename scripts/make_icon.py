# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow"]
# ///
"""Render packaging/tamra.ico (the exe and installer icon) and packaging/tamra.png (the Linux
window and menu icon, 256 px) from the logo in ui/src/logo.svg.

Run: uv run scripts/make_icon.py. Pillow is a build-time tool here, not a Tamra dependency.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "packaging" / "tamra.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
SCALE = 8  # supersampling; each size is drawn large and downscaled for smooth edges

TILE, WHITE, BLUE = "#111827", "#ffffff", "#60a5fa"
# ui/src/logo.svg on its 140-unit grid, with the 8.16667-wide strokes as filled boxes.
BOXES = [
    (WHITE, (31.2083, 31.2083, 53.6667, 39.375)),  # [ top
    (WHITE, (31.2083, 31.2083, 39.375, 108.792)),  # [ side
    (WHITE, (31.2083, 100.625, 53.6667, 108.792)),  # [ bottom
    (WHITE, (86.3333, 31.2083, 108.792, 39.375)),  # ] top
    (WHITE, (100.625, 31.2083, 108.792, 108.792)),  # ] side
    (WHITE, (86.3333, 100.625, 108.792, 108.792)),  # ] bottom
    (BLUE, (55.7083, 51.625, 84.2917, 59.7917)),  # T bar
    (BLUE, (65.9167, 51.625, 74.0833, 90.4167)),  # T stem
]


def render(size: int) -> Image.Image:
    big = size * SCALE
    k = big / 140
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=32.0833 * k, fill=TILE)
    for color, (x0, y0, x1, y1) in BOXES:
        draw.rectangle((x0 * k, y0 * k, x1 * k - 1, y1 * k - 1), fill=color)
    return im.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    images = [render(s) for s in SIZES]
    big = images[-1]
    assert big.getpixel((0, 0))[3] == 0, "corner should be transparent"
    assert big.getpixel((128, 220))[:3] == (0x11, 0x18, 0x27), "below the T should be the tile"
    assert big.getpixel((128, 140))[:3] == (0x60, 0xA5, 0xFA), "T stem should be blue"
    assert big.getpixel((64, 128))[:3] == (0xFF, 0xFF, 0xFF), "[ side should be white"
    big.save(OUT, sizes=[im.size for im in images], append_images=images[:-1])
    print(f"wrote {OUT}")
    big.save(OUT.with_suffix(".png"))
    print(f"wrote {OUT.with_suffix('.png')}")


if __name__ == "__main__":
    main()
