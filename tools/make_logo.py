#!/usr/bin/env python3
"""Write docs/img/logo.svg and logo.png from the geometry in punchtape/logo.py.

The admin pages inline the same logo with fill="currentColor" so it takes its colour
from the chosen theme. These files are for the README and anywhere else CSS variables
don't reach, so they carry the paper colours instead.

    python3 tools/make_logo.py [output_dir]      # default: docs/img

Pillow is needed for the PNG only; the SVG is written with no dependencies.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load admin/punchtape/logo.py on its own. Importing the package would pull in
# punchtape/__init__.py, and with it Flask, which this script has no use for.
_spec = importlib.util.spec_from_file_location(
    "punchtape_logo", os.path.join(ROOT, "admin", "punchtape", "logo.py"))
logo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(logo)

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "img")
SS = 6   # PNG supersample factor


def write_svg(path):
    with open(path, "w") as fh:
        fh.write(logo.svg())
    print(f"  {path}")


def write_png(path):
    from PIL import Image, ImageDraw

    def rgb(h):
        return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))

    W, H = int(logo.LENGTH), int(logo.WIDTH)
    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(1, int(logo.LINE * SS))
    d.rectangle([0, 0, W * SS, H * SS], fill=rgb(logo.TAPE) + (255,))
    d.rectangle([0, 0, W * SS, lw], fill=rgb(logo.EDGE) + (255,))
    d.rectangle([0, H * SS - lw, W * SS, H * SS], fill=rgb(logo.EDGE) + (255,))
    for cx, cy, r in logo.holes():
        d.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS],
                  fill=(0, 0, 0, 0))
    img.resize((W, H), Image.LANCZOS).save(path)
    print(f"  {path}  {W}x{H}")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    write_svg(os.path.join(OUT, "logo.svg"))
    write_png(os.path.join(OUT, "logo.png"))
