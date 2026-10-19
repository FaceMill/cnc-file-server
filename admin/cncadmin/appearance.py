"""Colour themes and the optional logo (Settings → Appearance).

Themes are fixed palettes rendered as CSS variables; the only free input is an
optional accent colour, validated as #rrggbb. The logo is a PNG or JPEG checked by
its content (never SVG, which can carry scripts) and stored in config/.
"""
import os
import re

from . import config

ACCENT_RE = re.compile(r"#[0-9a-fA-F]{6}")
LOGO_MAX_BYTES = 1024 * 1024
LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg"}

_LIGHT = {
    "fg": "#1d2329", "muted": "#5f6b76", "line": "#d9dee3", "bg": "#f6f7f9", "card": "#ffffff",
    "accent": "#0b5cad", "accent-fg": "#ffffff", "ok": "#1d7a3a", "bad": "#b42318", "warn": "#9a6700",
    "ok-bg": "#e3f4e8", "bad-bg": "#fde8e6", "warn-bg": "#fff4d6", "idle-bg": "#eef1f4",
    "flash-ok-fg": "#14532d", "flash-bad-fg": "#7a1a12", "banner-fg": "#5c4100", "banner-border": "#f0d58a",
    "header-bg": "#16212c", "header-fg": "#ffffff", "nav-fg": "#c9d4de", "nav-active": "#4ea1f3",
    "input-bg": "#ffffff", "input-border": "#b9c2cb", "btn2-bg": "#e7ebef",
    "code-bg": "#0f1720", "code-fg": "#d6e2ee", "view-bg": "#fbfcfd",
}

THEMES = {
    "default": {"label": "Blue (default)", "vars": {}},
    "slate": {"label": "Slate", "vars": {"accent": "#0f766e", "header-bg": "#334155", "nav-fg": "#cbd5e1",
                                          "nav-active": "#5eead4", "bg": "#f1f5f9"}},
    "forest": {"label": "Forest", "vars": {"accent": "#2f6b2f", "header-bg": "#1f3a24", "nav-fg": "#c7dcc8",
                                            "nav-active": "#8fd18f", "bg": "#f3f7f2"}},
    "safety": {"label": "Safety orange", "vars": {"accent": "#c2410c", "header-bg": "#262626", "nav-fg": "#d6d3d1",
                                                   "nav-active": "#fb923c", "bg": "#f7f6f4"}},
    "dark": {"label": "Dark", "vars": {
        "fg": "#e6e9ec", "muted": "#9aa5b1", "line": "#2f3842", "bg": "#12161b", "card": "#1b2128",
        "accent": "#3b8beb", "ok": "#5fd38a", "bad": "#ff7b72", "warn": "#e3b341",
        "ok-bg": "#173b25", "bad-bg": "#4a1d1a", "warn-bg": "#3d3010", "idle-bg": "#262d35",
        "flash-ok-fg": "#b9f0cc", "flash-bad-fg": "#ffd2cd", "banner-fg": "#f5dc9b", "banner-border": "#6b5414",
        "header-bg": "#0b0e12", "nav-fg": "#9aa5b1", "nav-active": "#3b8beb",
        "input-bg": "#12161b", "input-border": "#3a4550", "btn2-bg": "#2a323b",
        "code-bg": "#0b0e12", "code-fg": "#d6e2ee", "view-bg": "#12161b"}},
    "contrast": {"label": "High contrast", "vars": {
        "fg": "#000000", "muted": "#333333", "line": "#000000", "bg": "#ffffff", "accent": "#0033cc",
        "ok": "#005a1e", "bad": "#a00000", "warn": "#6b4700", "header-bg": "#000000", "nav-fg": "#ffffff",
        "nav-active": "#ffd400", "input-border": "#000000", "btn2-bg": "#dddddd"}},
}

DEFAULTS = {"theme": "default", "accent": "", "logo": "", "logo_version": 0}


def normalize(a):
    return {**DEFAULTS, **{k: v for k, v in (a or {}).items() if k in DEFAULTS}}


def validate(a):
    if a["theme"] not in THEMES:
        raise ValueError("Appearance: unknown colour theme")
    if a["accent"] and not ACCENT_RE.fullmatch(a["accent"]):
        raise ValueError("Appearance: accent colour must look like #1a2b3c")
    if a["logo"] not in ("",) + tuple(LOGO_TYPES):
        raise ValueError("Appearance: invalid logo type")
    if not isinstance(a["logo_version"], int):
        raise ValueError("Appearance: invalid logo version")


def _readable_on(hex_colour):
    """White or black text, whichever reads better on the given background."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#000000" if lum > 0.6 else "#ffffff"


def css_vars(a):
    """{name: value} for :root. Values only come from THEMES or a validated hex colour."""
    v = {**_LIGHT, **THEMES.get(a["theme"], THEMES["default"])["vars"]}
    if a["accent"] and ACCENT_RE.fullmatch(a["accent"]):
        v["accent"] = a["accent"]
        v["nav-active"] = a["accent"]
    v["accent-fg"] = _readable_on(v["accent"])
    return v


def swatches():
    """Preview colours per theme for the Settings page."""
    out = {}
    for key, t in THEMES.items():
        v = {**_LIGHT, **t["vars"]}
        out[key] = {"label": t["label"], "header": v["header-bg"], "accent": v["accent"], "bg": v["bg"], "card": v["card"]}
    return out


# --- logo ---------------------------------------------------------------------

def logo_path(ext):
    return os.path.join(config.CONFIG_DIR, f"logo.{ext}")


def detect_image(data):
    """'png' or 'jpg' from the file's first bytes, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None


def save_logo(data):
    if not data:
        raise ValueError("Choose a PNG or JPEG file.")
    if len(data) > LOGO_MAX_BYTES:
        raise ValueError("The logo must be 1 MB or smaller.")
    ext = detect_image(data)
    if not ext:
        raise ValueError("The logo must be a PNG or JPEG image (SVG isn't allowed).")
    for old in LOGO_TYPES:
        if os.path.exists(logo_path(old)):
            os.remove(logo_path(old))
    tmp = logo_path(ext) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, logo_path(ext))
    return ext


def remove_logo():
    for ext in LOGO_TYPES:
        if os.path.exists(logo_path(ext)):
            os.remove(logo_path(ext))
