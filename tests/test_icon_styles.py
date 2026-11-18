"""Icon styles for the hardware glyphs and the settings page glyphs."""
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from hardware_monitor_widget import icons

HW = icons.HARDWARE_KINDS
KINDS = sorted(icons.DRAWERS) + ["unknown_kind"]
PAGE_GLYPHS = ("back", "chevron_right", "chevron_up", "chevron_down", "display", "personalization",
               "position", "image", "logs", "info", "update")


@pytest.fixture(autouse=True)
def _reset_style():
    icons.set_icon_style("outline")
    yield
    icons.set_icon_style("outline")


def render(kind, size, style=None, color=QColor(40, 200, 90)):
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    icons.draw_kind_icon(p, kind, QRectF(0, 0, size, size), color, None, style)
    p.end()
    return img


def painted(img):
    return sum(1 for y in range(img.height()) for x in range(img.width()) if img.pixelColor(x, y).alpha() > 0)


def test_default_style_and_invalid_value():
    assert icons.get_icon_style() == "outline"
    assert icons.ICON_STYLES == ("outline", "filled", "simple", "none")
    icons.set_icon_style("simple")
    assert icons.get_icon_style() == "simple"
    icons.set_icon_style("retro")           # not offered here
    assert icons.get_icon_style() == "outline"
    icons.set_icon_style(None)
    assert icons.get_icon_style() == "outline"
    icons.set_icon_style("none")
    assert not icons.icons_visible()
    icons.set_icon_style("outline")
    assert icons.icons_visible()


@pytest.mark.parametrize("style", icons.ICON_STYLES)
@pytest.mark.parametrize("size", [16, 20, 32])
def test_every_style_draws_every_kind(qapp, style, size):
    for kind in KINDS:
        img = render(kind, size, style)
        if style == "none" and (kind in HW or kind == "unknown_kind"):
            assert painted(img) == 0, kind
        else:
            assert painted(img) > 0, (style, kind, size)


@pytest.mark.parametrize("style", ["filled", "simple"])
def test_styles_differ_from_outline_for_hardware(qapp, style):
    for kind in HW:
        assert render(kind, 24, style) != render(kind, 24, "outline"), kind


def test_filled_has_more_ink_than_outline(qapp):
    for kind in HW:
        assert painted(render(kind, 32, "filled")) > painted(render(kind, 32, "outline")), kind


def test_active_style_is_used_when_style_omitted(qapp):
    icons.set_icon_style("none")
    assert painted(render("cpu", 20)) == 0
    icons.set_icon_style("filled")
    assert render("cpu", 20) == render("cpu", 20, "filled")


def test_none_style_keeps_sensor_glyphs(qapp):
    icons.set_icon_style("none")
    for kind in ("thermometer", "fan", "bolt", "chart"):
        assert painted(render(kind, 18)) > 0


def test_style_does_not_change_sensor_glyphs(qapp):
    for kind in ("thermometer", "fan", "bolt", "chart"):
        assert render(kind, 18, "filled") == render(kind, 18, "outline")


def test_legible_at_16px(qapp):
    """Every hardware glyph keeps a minimum amount of ink and spans most of the 16 px box."""
    for style in ("outline", "filled", "simple"):
        for kind in HW:
            img = render(kind, 16, style, QColor(255, 255, 255))
            assert painted(img) >= 30, (style, kind)
            xs = [x for x in range(16) for y in range(16) if img.pixelColor(x, y).alpha() > 60]
            assert max(xs) - min(xs) >= 11, (style, kind)


def test_accent_colour_is_used(qapp):
    for style in ("outline", "filled", "simple"):
        img = render("gpu", 24, style, QColor(10, 20, 250))
        assert any(img.pixelColor(x, y).blue() == 250 and img.pixelColor(x, y).red() == 10
                   for y in range(24) for x in range(24)), style


def test_unknown_kind_follows_style(qapp):
    assert painted(render("whatever", 24, "none")) == 0
    assert render("whatever", 24, "filled") == render("other", 24, "filled")


def test_kind_pixmap_and_icon_follow_active_style(qapp):
    icons.set_icon_style("none")
    pm = icons.kind_pixmap("cpu", 16, QColor(255, 255, 255))
    assert painted(pm.toImage()) == 0
    icons.set_icon_style("outline")
    assert painted(icons.kind_pixmap("cpu", 16, QColor(255, 255, 255)).toImage()) > 0


@pytest.mark.parametrize("name", PAGE_GLYPHS)
@pytest.mark.parametrize("size", [16, 20, 32])
def test_page_glyphs_render(qapp, name, size):
    icon = icons.glyph_icon(name, size, QColor(255, 255, 255))
    assert not icon.isNull()
    assert painted(icon.pixmap(size, size).toImage()) > 0


def test_unknown_glyph_is_empty(qapp):
    assert icons.glyph_icon("nope", 16, QColor(0, 0, 0)).isNull()


def test_page_glyphs_are_distinct(qapp):
    images = {n: icons.glyph_icon(n, 24, QColor(255, 255, 255)).pixmap(24, 24).toImage() for n in PAGE_GLYPHS}
    names = list(images)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert images[a] != images[b], (a, b)
