"""The PunchTape logo: a strip of 8-level punched paper tape.

The holes are real. Each column is one character in 7-bit ASCII on tracks 1-7 with an
even-parity bit on track 8, laid out like genuine tape: tracks at a constant pitch, with
the small feed/sprocket hole between tracks 3 and 4. They spell PUNCHTAPE.

Two uses, one geometry:

* `svg(fill="currentColor", edge=None)` is inlined into the pages, so CSS colours it
  from the theme (`--nav-active` in the header, `--accent` on the login card) and it
  follows a custom accent colour live.
* `tools/make_logo.py` calls the same functions with the paper colours to write
  docs/img/logo.svg and logo.png for the README, where CSS variables don't reach.

The holes are cut out of one even-odd path rather than drawn over the tape, so they are
genuinely transparent and the page shows through them as it would through real tape.
A <mask> would be the obvious way to do that, but some renderers ignore masks.

`fill` and `edge` are written straight into the markup, so only ever pass a literal
from this module or "currentColor" -- never anything a user supplied.
"""

TAPE = "#dea965"         # manila / buff paper tape
EDGE = "#b58342"         # darker edge line

TEXT = "PUNCHTAPE"
PITCH = 44               # distance between tracks, and between columns
WIDTH = PITCH * 8.8      # tape width
R_DATA = PITCH * 0.29    # data hole radius
R_FEED = PITCH * 0.125   # feed hole radius
LINE = PITCH * 0.05      # edge line thickness
LEAD = TRAIL = 6         # blank leader columns either side

NCOLS = LEAD + len(TEXT) + TRAIL
LENGTH = PITCH * NCOLS
_TOP = (WIDTH - PITCH * 7) / 2
TRACK_Y = [_TOP + i * PITCH for i in range(8)]   # index 0 = track 8 (parity)
FEED_Y = (TRACK_Y[4] + TRACK_Y[5]) / 2


def parity_even(b):
    return bin(b).count("1") % 2


def char_rows(ch):
    """Hole rows for one character, track 8 (parity) first down to track 1 (bit 0)."""
    b = ord(ch)
    return [parity_even(b)] + [(b >> k) & 1 for k in (6, 5, 4, 3, 2, 1, 0)]


def holes():
    """Every hole as (cx, cy, r), feed holes included."""
    out = []
    for c in range(NCOLS):
        cx = PITCH * (c + 0.5)
        out.append((cx, FEED_Y, R_FEED))
        if LEAD <= c < LEAD + len(TEXT):
            for i, bit in enumerate(char_rows(TEXT[c - LEAD])):
                if bit:
                    out.append((cx, TRACK_Y[i], R_DATA))
    return out


def path_d():
    """The tape outline with every hole as an even-odd subpath."""
    def circle(cx, cy, r):
        return (f"M{cx - r:.2f} {cy:.2f}a{r:.2f} {r:.2f} 0 1 0 {2 * r:.2f} 0"
                f"a{r:.2f} {r:.2f} 0 1 0 {-2 * r:.2f} 0Z")

    return f"M0 0H{LENGTH:.0f}V{WIDTH:.0f}H0Z" + "".join(circle(*h) for h in holes())


def svg(fill=TAPE, edge=EDGE, sized=True, title="PunchTape"):
    """The logo as SVG markup.

    edge=None drops the darker edge lines, which is what you want when the whole strip
    is one colour. sized=False leaves off width/height so CSS can size it.
    """
    dims = f' width="{LENGTH:.0f}" height="{WIDTH:.0f}"' if sized else ""
    edges = ""
    if edge:
        edges = (f'\n  <rect width="{LENGTH:.0f}" height="{LINE:.2f}" fill="{edge}"/>'
                 f'\n  <rect y="{WIDTH - LINE:.2f}" width="{LENGTH:.0f}" '
                 f'height="{LINE:.2f}" fill="{edge}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LENGTH:.0f} '
        f'{WIDTH:.0f}"{dims} role="img" aria-label="{title}">'
        f'\n  <title>{title}</title>'
        f'\n  <desc>A strip of 8-level punched paper tape. The holes spell {TEXT} in '
        f'7-bit ASCII with an even-parity bit on track 8.</desc>'
        f'\n  <path d="{path_d()}" fill="{fill}" fill-rule="evenodd"/>'
        f'{edges}\n</svg>\n'
    )


# Inlined into every page; CSS gives it its colour, so it follows the theme.
INLINE = svg(fill="currentColor", edge=None, sized=False)
