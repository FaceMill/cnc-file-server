#!/usr/bin/env python3
"""Generate the PunchTape logo (docs/img/logo.svg and logo.png).

The holes are real. Each column is one character in 7-bit ASCII on tracks 1-7 with an
even-parity bit on track 8, laid out like genuine 8-level paper tape: tracks at a
constant pitch, with the small feed/sprocket hole between tracks 3 and 4. Feed a photo
of it to a tape reader and it says PUNCHTAPE.

The holes are punched fully transparent, so whatever is behind the logo shows through
them exactly as it would through real tape. That is also why one file works on both the
light and the dark admin themes.

    python3 tools/make_logo.py [output_dir]      # default: docs/img

Needs Pillow for the PNG. The SVG is written with no dependencies at all.
"""
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "img")

TAPE = (222, 170, 101)   # manila / buff paper tape
EDGE = (181, 131, 66)    # darker edge line

PITCH = 44               # distance between tracks, and between columns
WIDTH = PITCH * 8.8      # tape width
R_DATA = PITCH * 0.29    # data hole radius
R_FEED = PITCH * 0.125   # feed hole radius
LINE = PITCH * 0.05      # edge line thickness
LEAD = TRAIL = 6         # blank leader columns either side

TEXT = "PUNCHTAPE"
SS = 6                   # PNG supersample factor


def parity_even(b):
    return bin(b).count("1") % 2


def char_rows(ch):
    """Hole rows for one character, track 8 (parity) first down to track 1 (bit 0)."""
    b = ord(ch)
    return [parity_even(b)] + [(b >> k) & 1 for k in (6, 5, 4, 3, 2, 1, 0)]


def geometry():
    """(column count, total length, track y positions, feed y position)."""
    ncols = LEAD + len(TEXT) + TRAIL
    top = (WIDTH - PITCH * 7) / 2
    ys = [top + i * PITCH for i in range(8)]
    return ncols, PITCH * ncols, ys, (ys[4] + ys[5]) / 2


def holes():
    """Every hole as (cx, cy, r)."""
    ncols, _, ys, y_feed = geometry()
    out = []
    for c in range(ncols):
        cx = PITCH * (c + 0.5)
        out.append((cx, y_feed, R_FEED))
        if LEAD <= c < LEAD + len(TEXT):
            for i, bit in enumerate(char_rows(TEXT[c - LEAD])):
                if bit:
                    out.append((cx, ys[i], R_DATA))
    return out


def write_svg(path):
    _, length, _, _ = geometry()

    def circle(cx, cy, r):
        return (f"M{cx - r:.2f} {cy:.2f}a{r:.2f} {r:.2f} 0 1 0 {2 * r:.2f} 0"
                f"a{r:.2f} {r:.2f} 0 1 0 {-2 * r:.2f} 0Z")

    # One even-odd path: outer rectangle plus a subpath per hole. Works everywhere,
    # unlike <mask>, which some renderers ignore.
    d = f"M0 0H{length:.0f}V{WIDTH:.0f}H0Z" + "".join(circle(*h) for h in holes())
    hexc = lambda c: "#%02x%02x%02x" % c  # noqa: E731
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {length:.0f} '
        f'{WIDTH:.0f}" width="{length:.0f}" height="{WIDTH:.0f}" '
        f'role="img" aria-label="PunchTape">\n'
        f'  <title>PunchTape</title>\n'
        f'  <desc>A strip of 8-level punched paper tape. The holes spell PUNCHTAPE in '
        f'7-bit ASCII with an even-parity bit on track 8.</desc>\n'
        f'  <path d="{d}" fill="{hexc(TAPE)}" fill-rule="evenodd"/>\n'
        f'  <rect width="{length:.0f}" height="{LINE:.2f}" fill="{hexc(EDGE)}"/>\n'
        f'  <rect y="{WIDTH - LINE:.2f}" width="{length:.0f}" height="{LINE:.2f}" '
        f'fill="{hexc(EDGE)}"/>\n'
        f'</svg>\n'
    )
    with open(path, "w") as fh:
        fh.write(svg)
    print(f"  {path}")


def write_png(path):
    from PIL import Image, ImageDraw

    _, length, _, _ = geometry()
    W, H = int(length), int(WIDTH)
    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(1, int(LINE * SS))
    d.rectangle([0, 0, W * SS, H * SS], fill=TAPE + (255,))
    d.rectangle([0, 0, W * SS, lw], fill=EDGE + (255,))
    d.rectangle([0, H * SS - lw, W * SS, H * SS], fill=EDGE + (255,))
    for cx, cy, r in holes():
        d.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS],
                  fill=(0, 0, 0, 0))
    img.resize((W, H), Image.LANCZOS).save(path)
    print(f"  {path}  {W}x{H}")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    write_svg(os.path.join(OUT, "logo.svg"))
    write_png(os.path.join(OUT, "logo.png"))
