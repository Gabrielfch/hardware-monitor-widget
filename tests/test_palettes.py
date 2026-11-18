"""Colour palettes, custom colours with contrast correction, appearance wiring and the colours dialog."""
import pytest
from PySide6.QtGui import QColor, QGuiApplication

from hardware_monitor_widget import appearance, colors, icons
from hardware_monitor_widget.custom_colors_dialog import CustomColorsDialog

BANDS = ("green", "amber", "orange", "red", "grey")
HOSTILE = (
    {"ok": "#FFFF66", "warn": "#FFFF99", "crit": "#202040"},
    {"ok": "#808080", "warn": "#777777", "crit": "#999999"},
    {"ok": "#000000", "warn": "#FFFFFF", "crit": "#7F7F7F"},
    {"ok": "#101010", "warn": "#111111", "crit": "#121212"},
    {"ok": "#FFFFFF", "warn": "#FEFEFE", "crit": "#FDFDFD"},
)


@pytest.fixture(autouse=True)
def _reset():
    colors.set_palette("classic")
    icons.set_icon_style("outline")
    yield
    colors.set_palette("classic")
    icons.set_icon_style("outline")


def _assert_readable():
    for dark in (True, False):
        for bg in colors.pill_backgrounds(dark).values():
            for band in BANDS:
                assert colors.contrast_ratio(colors.band_color(band, dark), bg) >= 4.5, (band, dark)


@pytest.mark.parametrize("name", colors.PALETTES)
def test_every_palette_defines_all_bands(name):
    colors.set_palette(name, {"ok": "#00AAFF", "warn": "#FFFF00", "crit": "#FF0000"})
    assert colors.get_palette() == name
    for band in BANDS:
        for dark in (True, False):
            assert colors.band_color(band, dark).isValid()


def test_palette_names():
    assert colors.PALETTES == ("classic", "colorblind", "pastel", "high_contrast", "mono", "custom")


@pytest.mark.parametrize("name", ("classic", "colorblind", "pastel", "high_contrast", "mono"))
def test_builtin_palettes_are_readable(name):
    colors.set_palette(name)
    _assert_readable()


@pytest.mark.parametrize("custom", HOSTILE)
def test_custom_palette_is_readable_even_with_hostile_colours(custom):
    colors.set_palette("custom", custom)
    assert colors.get_palette() == "custom"
    _assert_readable()


@pytest.mark.parametrize("custom", (None, {}, {"ok": "nonsense"}, {"ok": 5, "warn": "#12"}, "x"))
def test_custom_palette_with_garbage_falls_back_to_defaults(custom):
    colors.set_palette("custom", custom)
    _assert_readable()
    assert colors.band_color("green", True).isValid()


def test_custom_colour_that_is_already_readable_is_kept():
    colors.set_palette("custom", {"ok": "#6EDC78", "warn": "#F5C842", "crit": "#FF9999"})
    assert colors.band_color("green", True).name().upper() == "#6EDC78"


def test_band_color_follows_active_palette_and_unknown_falls_back():
    classic = colors.band_color("green", True).name()
    colors.set_palette("colorblind")
    assert colors.band_color("green", True).name() != classic
    colors.set_palette("nonsense")
    assert colors.get_palette() == "classic" and colors.band_color("green", True).name() == classic
    assert colors.band_color("no-such-band", True) == colors.band_color("grey", True)


def test_mono_keeps_red_distinct():
    colors.set_palette("mono")
    assert colors.band_color("red", True) != colors.band_color("green", True)
    assert colors.band_color("green", True) == colors.band_color("amber", True)


def test_ensure_contrast_darkens_light_yellow_on_light_bg():
    bg = colors.pill_backgrounds(False)["normal"]
    yellow = QColor(255, 255, 100)
    assert colors.contrast_ratio(yellow, bg) < 3
    assert colors.contrast_ratio(colors.ensure_contrast(yellow, bg, 4.5), bg) >= 4.5
    ok = QColor(0, 0, 0)
    assert colors.ensure_contrast(ok, bg) == ok


def test_ensure_contrast_lightens_on_dark_bg():
    bg = colors.pill_backgrounds(True)["normal"]
    out = colors.ensure_contrast(QColor(20, 20, 60), bg, 4.5)
    assert colors.contrast_ratio(out, bg) >= 4.5


def test_parse_hex_defaults_on_invalid():
    assert colors.parse_hex("#102030") == (16, 32, 48)
    assert colors.parse_hex("zzz", "#010203") == (1, 2, 3)
    assert colors.parse_hex("#GGGGGG", "#010203") == (1, 2, 3)


# ---------------------------------------------------------------- appearance
class _Cfg:
    def __init__(self, **kw):
        self.d = kw

    def get(self, key):
        return self.d.get(key)


def test_apply_appearance_pushes_palette_and_icon_style(qapp):
    appearance.apply_appearance(_Cfg(color_palette="pastel", icon_style="filled", theme="auto", language="es"))
    assert colors.get_palette() == "pastel" and icons.get_icon_style() == "filled"
    appearance.apply_appearance(_Cfg(color_palette="custom", custom_colors={"ok": "#FFFF66"},
                                     icon_style="none", theme="auto"))
    assert colors.get_palette() == "custom" and icons.get_icon_style() == "none"
    _assert_readable()


def test_apply_appearance_tolerates_missing_values(qapp):
    appearance.apply_appearance(_Cfg())
    assert colors.get_palette() == "classic" and icons.get_icon_style() == "outline"


def test_apply_appearance_accepts_a_dict(qapp):
    appearance.apply_appearance({"color_palette": "mono", "icon_style": "simple", "theme": "auto"})
    assert colors.get_palette() == "mono" and icons.get_icon_style() == "simple"


def test_apply_color_scheme_pins_and_releases(qapp, monkeypatch):
    """The offscreen platform ignores colour schemes, so record what is requested instead."""
    from PySide6.QtCore import Qt

    class Hints:
        scheme = None

        def setColorScheme(self, value):
            self.scheme = value

    hints = Hints()
    monkeypatch.setattr(QGuiApplication, "styleHints", staticmethod(lambda: hints))
    for theme, expected in (("dark", Qt.ColorScheme.Dark), ("light", Qt.ColorScheme.Light),
                            ("auto", Qt.ColorScheme.Unknown), (None, Qt.ColorScheme.Unknown)):
        appearance.apply_color_scheme(theme)
        assert hints.scheme == expected


def test_apply_color_scheme_never_raises(qapp, monkeypatch):
    def boom():
        raise RuntimeError("no hints")
    monkeypatch.setattr(QGuiApplication, "styleHints", staticmethod(boom))
    appearance.apply_color_scheme("dark")


# ---------------------------------------------------------------- dialog
def test_dialog_defaults_and_edit(qapp):
    dlg = CustomColorsDialog(None)
    assert dlg.colors() == colors.CUSTOM_DEFAULTS
    dlg.set_color("ok", "#112233")
    dlg.set_color("nope", "#112233")
    dlg.set_color("warn", "not a colour")
    out = dlg.colors()
    assert out["ok"] == "#112233" and out["warn"] == colors.CUSTOM_DEFAULTS["warn"] and "nope" not in out
    dlg.reset()
    assert dlg.colors() == colors.CUSTOM_DEFAULTS
    dlg.close()


def test_dialog_loads_current_and_ignores_invalid(qapp):
    dlg = CustomColorsDialog({"ok": "#abcdef", "warn": "zzz", "crit": 7})
    c = dlg.colors()
    assert c["ok"] == "#ABCDEF" and c["warn"] == colors.CUSTOM_DEFAULTS["warn"]
    assert c["crit"] == colors.CUSTOM_DEFAULTS["crit"]
    dlg.close()


def test_dialog_preview_is_readable_with_hostile_colours(qapp):
    dlg = CustomColorsDialog({"ok": "#FFFF66", "warn": "#FFFF99", "crit": "#202040"})
    import re
    for lbl, band, kind, dark in dlg._pills:
        m = re.search(r"background: rgb\((\d+),(\d+),(\d+)\); color: rgb\((\d+),(\d+),(\d+)\)", lbl.styleSheet())
        bg, fg = QColor(*map(int, m.groups()[:3])), QColor(*map(int, m.groups()[3:]))
        assert colors.contrast_ratio(fg, bg) >= 4.5
    dlg.close()


def test_strings_have_es_en_parity_and_dialog_keys():
    from hardware_monitor_widget.strings_appearance import STRINGS
    assert set(STRINGS["es"]) == set(STRINGS["en"])
    for name in colors.PALETTES:
        assert f"palette_{name}" in STRINGS["en"]
    for name in icons.ICON_STYLES:
        assert f"icon_style_{name}" in STRINGS["en"]
