"""Theme and band colours (pure, no widgets): readable on dark and light taskbars.

Adapted from Taskbar Battery Widget (MIT). Every band colour keeps >= 4.5:1 contrast against the
pill backgrounds below (normal / hover / alert tints composited over the taskbar base colour).
"""
from __future__ import annotations

from PySide6.QtGui import QColor

# band -> (dark theme rgb, light theme rgb). Every colour keeps >= 4.5:1 contrast against the pill
# backgrounds below.
_CLASSIC = {
    "green": ((110, 220, 120), (17, 97, 40)),
    "amber": ((245, 200, 66), (114, 74, 0)),
    "orange": ((255, 158, 72), (139, 58, 0)),
    "red": ((255, 153, 153), (165, 24, 24)),
    "grey": ((182, 182, 182), (82, 82, 82)),
}
# Colour-blind safe (Okabe-Ito based): ok = blue, warn = yellow, orange, crit = vermillion.
_COLORBLIND = {
    "green": ((115, 196, 240), (0, 82, 150)),
    "amber": ((240, 228, 66), (96, 80, 0)),
    "orange": ((245, 172, 45), (118, 62, 0)),
    "red": ((255, 165, 120), (145, 50, 0)),
    "grey": ((182, 182, 182), (82, 82, 82)),
}
_PASTEL = {
    "green": ((168, 230, 180), (22, 92, 52)),
    "amber": ((255, 236, 150), (105, 78, 0)),
    "orange": ((255, 200, 150), (130, 62, 8)),
    "red": ((255, 170, 185), (150, 30, 56)),
    "grey": ((195, 195, 205), (78, 78, 88)),
}
_HIGH_CONTRAST = {
    "green": ((0, 255, 90), (0, 90, 0)),
    "amber": ((255, 255, 0), (92, 62, 0)),
    "orange": ((255, 165, 30), (130, 44, 0)),
    "red": ((255, 155, 155), (160, 0, 0)),
    "grey": ((255, 255, 255), (0, 0, 0)),
}
# Monochrome: neutral bands; red stays so a critical reading is still flagged.
_MONO = {
    "green": ((235, 235, 235), (40, 40, 40)),
    "amber": ((235, 235, 235), (40, 40, 40)),
    "orange": ((235, 235, 235), (40, 40, 40)),
    "red": _CLASSIC["red"],
    "grey": _CLASSIC["grey"],
}

PALETTES = ("classic", "colorblind", "pastel", "high_contrast", "mono", "custom")
_BUILTIN = {"classic": _CLASSIC, "colorblind": _COLORBLIND, "pastel": _PASTEL,
            "high_contrast": _HIGH_CONTRAST, "mono": _MONO}
CUSTOM_KEYS = ("ok", "warn", "crit")
CUSTOM_DEFAULTS = {"ok": "#6EDC78", "warn": "#F5C842", "crit": "#FF9999"}
MIN_CONTRAST = 4.5

_PALETTE = _CLASSIC          # active palette (band -> (dark rgb, light rgb))
_active = "classic"

# Pill background tints: (taskbar base, (rgb, alpha) normal, hover, alert, alert+hover) per theme.
TASKBAR_BASE = {True: (32, 32, 32), False: (243, 243, 243)}
_TINTS = {
    True: {"normal": ((255, 255, 255), 24), "hover": ((255, 255, 255), 44),
           "low": ((255, 105, 105), 38), "low_hover": ((255, 105, 105), 64)},
    False: {"normal": ((0, 0, 0), 22), "hover": ((0, 0, 0), 32),
            "low": ((176, 0, 0), 30), "low_hover": ((176, 0, 0), 52)},
}


def pill_tint(kind: str, dark: bool) -> QColor:
    """Translucent fill painted for a pill (kind: normal | hover | low | low_hover)."""
    (r, g, b), a = _TINTS[bool(dark)][kind]
    return QColor(r, g, b, a)


def composite(fg: QColor, bg: QColor) -> QColor:
    """`fg` (with its alpha) painted over the opaque colour `bg`."""
    a = fg.alpha() / 255
    return QColor(*(round(bg.red() + (fg.red() - bg.red()) * a),
                    round(bg.green() + (fg.green() - bg.green()) * a),
                    round(bg.blue() + (fg.blue() - bg.blue()) * a)))


def pill_backgrounds(dark: bool) -> dict[str, QColor]:
    """Opaque colour each pill background ends up as over the taskbar base colour."""
    base = QColor(*TASKBAR_BASE[bool(dark)])
    return {k: composite(pill_tint(k, dark), base) for k in _TINTS[bool(dark)]}


def band_color(band: str, dark: bool) -> QColor:
    return QColor(*_PALETTE.get(band, _PALETTE["grey"])[0 if dark else 1])


def luminance(c: QColor) -> float:
    def ch(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast_ratio(a: QColor, b: QColor) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ensure_contrast(color: QColor, bg: QColor, min_ratio: float = 3.0) -> QColor:
    """`color` unchanged if it already has `min_ratio` contrast against `bg`; otherwise pushed towards
    white (dark bg) or black (light bg) until it does."""
    if contrast_ratio(color, bg) >= min_ratio:
        return QColor(color)
    target = (255, 255, 255) if luminance(bg) < 0.4 else (0, 0, 0)
    base = (color.red(), color.green(), color.blue())
    out = QColor(*target)
    for step in range(1, 21):
        t = step / 20
        cand = QColor(*(round(b + (g - b) * t) for b, g in zip(base, target)))
        if contrast_ratio(cand, bg) >= min_ratio:
            return cand
        out = cand
    return out


def parse_hex(value, default: str = "#000000") -> tuple[int, int, int]:
    s = value if isinstance(value, str) and len(value) == 7 and value[0] == "#" else default
    try:
        return int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)
    except ValueError:
        return parse_hex(default)


def _mix(a, b, t: float = 0.5):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def custom_palette(custom) -> dict:
    """Palette built from the 3 user colours (ok / warn / crit); every band is pushed until it reaches
    4.5:1 against every pill background of both themes, so hostile choices stay readable."""
    custom = custom if isinstance(custom, dict) else {}
    rgb = {k: parse_hex(custom.get(k), CUSTOM_DEFAULTS[k]) for k in CUSTOM_KEYS}
    bands = {"green": rgb["ok"], "amber": rgb["warn"], "orange": _mix(rgb["warn"], rgb["crit"]),
             "red": rgb["crit"]}
    out = {"grey": _CLASSIC["grey"]}
    for band, c in bands.items():
        variants = []
        for dark in (True, False):
            col = QColor(*c)
            for bg in pill_backgrounds(dark).values():
                col = ensure_contrast(col, bg, MIN_CONTRAST)
            variants.append((col.red(), col.green(), col.blue()))
        out[band] = tuple(variants)
    return out


def set_palette(name: str, custom=None) -> None:
    """Select the active colour palette (unknown names fall back to classic)."""
    global _PALETTE, _active
    if name == "custom":
        _PALETTE, _active = custom_palette(custom), "custom"
    elif name in _BUILTIN:
        _PALETTE, _active = _BUILTIN[name], name
    else:
        _PALETTE, _active = _CLASSIC, "classic"


def get_palette() -> str:
    return _active


def text_color(dark: bool) -> QColor:
    return QColor(255, 255, 255) if dark else QColor(20, 20, 20)


def section_header_color(dark: bool) -> QColor:
    """Panel section header colour (the green of the "Connected" header in the reference look)."""
    return band_color("green", dark)


def row_background(dark: bool, alert: bool = False) -> QColor:
    """Opaque background of a panel row card (same as the pill background)."""
    return pill_backgrounds(dark)["low" if alert else "normal"]
