"""Reproduce the existing violet briefcase as a transparent Windows ICO.

Pillow is already a runtime dependency. Geometry and color match favicon.svg;
the supersampled master keeps the handle and band legible at small sizes.
No timestamps, external images, fonts or network resources enter the output.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "web" / "jobfinder.ico"


def build_icon(destination: Path = DEFAULT_OUTPUT) -> Path:
    scale = 48
    image = Image.new("RGBA", (24 * scale, 24 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    violet = (99, 102, 241, 255)
    draw.rounded_rectangle(
        (7 * scale, 2 * scale, 17 * scale, 10 * scale),
        radius=3 * scale,
        outline=violet,
        width=2 * scale,
    )
    draw.rounded_rectangle(
        (3 * scale, 7 * scale, 21 * scale, 20 * scale), radius=2 * scale, fill=violet
    )
    # SVG's 40% white band composited over the opaque violet body.
    band = (*(round(channel * 0.6 + 255 * 0.4) for channel in violet[:3]), 255)
    draw.rectangle((3 * scale, 11 * scale, 21 * scale, 13 * scale), fill=band)
    master = image.resize((256, 256), Image.Resampling.LANCZOS)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    master.save(destination, format="ICO", sizes=[(size, size) for size in ICON_SIZES])
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    print(build_icon(parser.parse_args().output))


if __name__ == "__main__":
    main()
