"""Render media/logo.svg into a Windows multi-resolution media/DLC.ico.

Usage (from repo root):
    venv/Scripts/python build/make_icon.py        # Windows
    venv/bin/python build/make_icon.py            # macOS/Linux

Re-run whenever logo.svg changes. Commit the generated .ico so CI doesn't
need cairosvg/Cairo on the Windows runner.
"""
from __future__ import annotations

import io
from pathlib import Path

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "media" / "logo.svg"
ICO = ROOT / "media" / "DLC.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> Image.Image:
    png_bytes = cairosvg.svg2png(
        url=str(SVG),
        output_width=size,
        output_height=size,
    )
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def main() -> None:
    if not SVG.is_file():
        raise SystemExit(f"Source SVG not found: {SVG}")

    images = [render(s) for s in SIZES]
    base = images[-1]
    base.save(ICO, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print(f"Wrote {ICO} ({len(SIZES)} resolutions: {', '.join(map(str, SIZES))})")


if __name__ == "__main__":
    main()
