"""Vector icons drawn with QPainter (24x24 design grid, stroked outlines).

Style adapted from Taskbar Battery Widget (MIT): same stroke weight, round caps and joins.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap


def _P(x, y):
    return QPointF(x, y)


def _poly(p: QPainter, pts, close=False):
    path = QPainterPath(_P(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(_P(*pt))
    if close:
        path.closeSubpath()
    p.drawPath(path)


def _rr(p, x, y, w, h, r=2.0):
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _stroke_pen(color: QColor, width: float = 1.7) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def stroke_width_for(side_px: float) -> float:
    """Stroke (in 24-grid units) that keeps ~1.4 px lines: heavier on the small 16-18 px glyphs."""
    if side_px <= 18:
        return 2.2
    if side_px <= 22:
        return 1.9
    return 1.7


def _stroked(painter: QPainter, rect: QRectF, color: QColor, width: float | None = None) -> None:
    """Common set-up of the 24x24 stroked glyphs (caller must painter.restore())."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    side = min(rect.width(), rect.height())
    if width is None:
        width = stroke_width_for(side)
    painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    painter.scale(side / 24.0, side / 24.0)
    painter.setPen(_stroke_pen(color, width))
    painter.setBrush(Qt.BrushStyle.NoBrush)


# --------------------------------------------------------------------------- hardware kinds
def _cpu(p):
    """Chip: square body with two pins per side (kept sparse so it stays legible at 16 px)."""
    _rr(p, 6, 6, 12, 12, 2)
    for v in (9.5, 14.5):
        _poly(p, [(v, 2.5), (v, 6)])
        _poly(p, [(v, 18), (v, 21.5)])
        _poly(p, [(2.5, v), (6, v)])
        _poly(p, [(18, v), (21.5, v)])


def _gpu(p):
    """Card: case, one fan, two legs."""
    _rr(p, 2, 5.5, 20, 11.5, 2)
    p.drawEllipse(_P(12, 11.25), 3.3, 3.3)
    _poly(p, [(7, 17), (7, 20.5)])
    _poly(p, [(17, 17), (17, 20.5)])


def _ram(p):
    """Memory stick: body with three contact notches."""
    _rr(p, 2, 6, 20, 10, 1.5)
    for x in (7, 12, 17):
        _poly(p, [(x, 16), (x, 19.5)])


def _mainboard(p):
    _rr(p, 3.5, 3.5, 17, 17, 2)
    _rr(p, 7, 7, 5, 5, 1)
    _poly(p, [(15, 7), (17.5, 7)])
    _poly(p, [(15, 10), (17.5, 10)])
    _poly(p, [(7, 16), (17.5, 16)])


def _storage(p):
    _rr(p, 3, 6, 18, 12, 2.5)
    _poly(p, [(3, 14), (21, 14)])
    p.drawPoint(_P(7, 16.2))
    p.drawPoint(_P(10, 16.2))


def _network(p):
    p.drawEllipse(_P(12, 12), 9, 9)
    p.drawEllipse(_P(12, 12), 4, 9)
    _poly(p, [(3, 12), (21, 12)])


def _other(p):
    _rr(p, 5, 5, 14, 14, 3)
    p.drawEllipse(_P(12, 12), 2.4, 2.4)


def _thermometer(p):
    path = QPainterPath()
    path.moveTo(10, 14.27)
    path.lineTo(10, 5.5)
    path.arcTo(QRectF(10, 3.5, 4, 4), 180, -180)
    path.lineTo(14, 14.27)
    path.arcTo(QRectF(8.2, 13.7, 7.6, 7.6), 58, -296)
    path.closeSubpath()
    p.drawPath(path)
    _poly(p, [(12, 9), (12, 17.5)])


def _fan(p):
    p.drawEllipse(_P(12, 12), 1.6, 1.6)
    for i in range(3):
        a = math.radians(i * 120 - 90)
        cx, cy = 12 + 5.2 * math.cos(a), 12 + 5.2 * math.sin(a)
        p.drawEllipse(_P(cx, cy), 3.6, 3.6)


def _bolt(p):
    _poly(p, [(13.5, 2.5), (5.5, 13), (11.5, 13), (10.5, 21.5), (18.5, 10.5), (12.5, 10.5)], close=True)


def _chart(p):
    _poly(p, [(4, 19.5), (4, 4.5)])
    _poly(p, [(4, 19.5), (20, 19.5)])
    _poly(p, [(7, 15), (11, 10), (14, 13), (19, 6.5)])


DRAWERS = {"cpu": _cpu, "gpu": _gpu, "ram": _ram, "mainboard": _mainboard, "storage": _storage,
           "network": _network, "other": _other, "thermometer": _thermometer, "fan": _fan,
           "bolt": _bolt, "chart": _chart}

# sensor kind -> glyph used in the panel rows
SENSOR_GLYPH = {"temperature": "thermometer", "fan": "fan", "power": "bolt", "voltage": "bolt",
                "load": "chart", "level": "chart", "control": "fan"}


# --------------------------------------------------------------------------- icon styles
# Only the hardware-kind glyphs follow the style; the sensor glyphs (thermometer, fan, ...) and the
# toolbar glyphs always keep the outline look so the panel rows stay legible.
HARDWARE_KINDS = ("cpu", "gpu", "ram", "mainboard", "storage", "network", "other")
ICON_STYLES = ("outline", "filled", "simple", "none")
_style = "outline"


def set_icon_style(style: str) -> None:
    """Select the active hardware-icon style (unknown values fall back to "outline")."""
    global _style
    _style = style if style in ICON_STYLES else "outline"


def get_icon_style() -> str:
    return _style


def icons_visible() -> bool:
    """False when the active style is "none" (callers drop the icon and its gap)."""
    return _style != "none"


def _is_styled(kind: str) -> bool:
    return kind in HARDWARE_KINDS or kind not in DRAWERS


def _clear_line(p, pts, width=1.6):
    """Punch a line out of an offscreen filled glyph."""
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(_stroke_pen(QColor(0, 0, 0), width))
    p.setBrush(Qt.BrushStyle.NoBrush)
    _poly(p, pts)
    p.restore()


def _clear_shape(p, fn):
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0))
    fn(p)
    p.restore()


def _pins(p, c, lines, width=2.0):
    p.save()
    p.setPen(_stroke_pen(c, width))
    for line in lines:
        _poly(p, line)
    p.restore()


def _f_cpu(p, c):
    _rr(p, 5.5, 5.5, 13, 13, 2.5)
    lines = []
    for v in (9.5, 14.5):
        lines += [[(v, 2.5), (v, 6)], [(v, 18), (v, 21.5)], [(2.5, v), (6, v)], [(18, v), (21.5, v)]]
    _pins(p, c, lines)
    _clear_shape(p, lambda q: _rr(q, 9, 9, 6, 6, 1))


def _f_gpu(p, c):
    _rr(p, 2, 5.5, 20, 11.5, 2)
    _pins(p, c, [[(7, 17), (7, 20.5)], [(17, 17), (17, 20.5)]])
    _clear_shape(p, lambda q: q.drawEllipse(_P(12, 11.25), 3.6, 3.6))


def _f_ram(p, c):
    _rr(p, 2, 6, 20, 10, 1.5)
    _pins(p, c, [[(x, 16), (x, 19.5)] for x in (7, 12, 17)])

    def holes(q):
        for x in (4.5, 10.5, 16.5):
            _rr(q, x, 9, 3, 4, 0.6)
    _clear_shape(p, holes)


def _f_mainboard(p, c):
    _rr(p, 3.5, 3.5, 17, 17, 2)
    _clear_shape(p, lambda q: _rr(q, 6.5, 6.5, 6, 6, 1))
    _clear_line(p, [(15, 7.5), (17.5, 7.5)])
    _clear_line(p, [(15, 10.5), (17.5, 10.5)])
    _clear_line(p, [(7, 16), (17.5, 16)])


def _f_storage(p, c):
    _rr(p, 3, 6, 18, 12, 2.5)
    _clear_line(p, [(3, 13.5), (21, 13.5)], 1.4)

    def dots(q):
        for x in (7, 10):
            q.drawEllipse(_P(x, 15.8), 0.9, 0.9)
    _clear_shape(p, dots)


def _f_network(p, c):
    p.drawEllipse(_P(12, 12), 9.5, 9.5)
    _clear_line(p, [(2, 12), (22, 12)], 1.5)
    _clear_shape(p, lambda q: q.drawEllipse(_P(12, 12), 1.4, 9.6))


def _f_other(p, c):
    _rr(p, 3.5, 3.5, 17, 17, 4)
    _clear_shape(p, lambda q: q.drawEllipse(_P(12, 12), 3, 3))


def _f_thermometer(p, c):
    _thermometer(p)
    p.drawEllipse(_P(12, 17.5), 3.8, 3.8)


def _f_fan(p, c):
    _fan(p)
    _clear_shape(p, lambda q: q.drawEllipse(_P(12, 12), 1.6, 1.6))


def _f_bolt(p, c):
    _bolt(p)


_FILLED = {"cpu": _f_cpu, "gpu": _f_gpu, "ram": _f_ram, "mainboard": _f_mainboard, "storage": _f_storage,
           "network": _f_network, "other": _f_other, "thermometer": _f_thermometer, "fan": _f_fan,
           "bolt": _f_bolt}


def _draw_filled(painter: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    """Solid silhouette with the inner details punched out (drawn offscreen so Clear is safe)."""
    w, h = max(1, math.ceil(rect.width())), max(1, math.ceil(rect.height()))
    try:
        dpr = float(painter.device().devicePixelRatioF()) or 1.0
    except Exception:  # noqa: BLE001 - devices without a ratio
        dpr = 1.0
    img = QImage(max(1, round(w * dpr)), max(1, round(h * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    c = QColor(color)
    alpha = c.alphaF()
    c.setAlpha(255)
    q = QPainter(img)
    q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    side = min(w, h)
    q.translate((w - side) / 2, (h - side) / 2)
    q.scale(side / 24.0, side / 24.0)
    q.setPen(Qt.PenStyle.NoPen)
    q.setBrush(c)
    (_FILLED.get(kind) or _f_other)(q, c)
    q.end()
    painter.save()
    painter.setOpacity(painter.opacity() * alpha)
    painter.drawImage(QPointF(rect.center().x() - w / 2, rect.center().y() - h / 2), img)
    painter.restore()


# simple: chunkier strokes and fewer details (still legible at 16 px)
def _sm_cpu(p):
    _rr(p, 6, 6, 12, 12, 2)
    _poly(p, [(12, 2.5), (12, 6)])
    _poly(p, [(12, 18), (12, 21.5)])
    _poly(p, [(2.5, 12), (6, 12)])
    _poly(p, [(18, 12), (21.5, 12)])


def _sm_gpu(p):
    _rr(p, 2.5, 5.5, 19, 11.5, 2.5)
    p.drawEllipse(_P(12, 11.25), 2.6, 2.6)


def _sm_ram(p):
    _rr(p, 2.5, 6.5, 19, 9.5, 1.5)
    for x in (8, 16):
        _poly(p, [(x, 16), (x, 19.5)])


def _sm_mainboard(p):
    _rr(p, 3.5, 3.5, 17, 17, 2.5)
    _rr(p, 8, 8, 8, 8, 1.5)


def _sm_storage(p):
    _rr(p, 3, 6.5, 18, 11, 3)
    _poly(p, [(7, 14), (11, 14)])


def _sm_network(p):
    p.drawEllipse(_P(12, 12), 9, 9)
    _poly(p, [(3, 12), (21, 12)])


_SIMPLE = {**DRAWERS, "cpu": _sm_cpu, "gpu": _sm_gpu, "ram": _sm_ram, "mainboard": _sm_mainboard,
           "storage": _sm_storage, "network": _sm_network}


def draw_kind_icon(painter: QPainter, kind: str, rect: QRectF, color: QColor, width: float | None = None,
                   style: str | None = None) -> None:
    """Glyph of a hardware kind / glyph name ("cpu", "gpu", "ram", "thermometer", ...).

    Hardware kinds follow `style` (default: the active one); the "none" style draws nothing for them."""
    st = style if style in ICON_STYLES else _style
    if not _is_styled(kind):
        st = "outline"
    if st == "none":
        return
    if st == "filled":
        _draw_filled(painter, kind, rect, color)
        return
    if st == "simple":
        if width is None:
            width = 2.6 if min(rect.width(), rect.height()) <= 22 else 2.3
        _stroked(painter, rect, color, width)
        _SIMPLE.get(kind, _other)(painter)
    else:
        _stroked(painter, rect, color, width)
        DRAWERS.get(kind, _other)(painter)
    painter.restore()


def kind_pixmap(kind: str, size: int, color: QColor, dpr: float = 1.0) -> QPixmap:
    px = max(1, int(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    draw_kind_icon(p, kind, QRectF(0, 0, size, size), color)
    p.end()
    return pm


# --------------------------------------------------------------------------- toolbar glyphs
def draw_eye(painter: QPainter, rect: QRectF, color: QColor, open: bool = True) -> None:
    """Eye (pinned) or eye with a slash (not pinned)."""
    _stroked(painter, rect, color)
    path = QPainterPath(_P(2, 12))
    path.cubicTo(6, 5, 18, 5, 22, 12)
    path.cubicTo(18, 19, 6, 19, 2, 12)
    painter.drawPath(path)
    if open:
        painter.drawEllipse(_P(12, 12), 3.2, 3.2)
    else:
        _poly(painter, [(4, 4), (20, 20)])
    painter.restore()


def draw_refresh(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Circular arrow: a 270 degree arc with an arrow head at its end."""
    _stroked(painter, rect, color)
    path = QPainterPath()
    path.arcMoveTo(QRectF(4, 4, 16, 16), 50)
    path.arcTo(QRectF(4, 4, 16, 16), 50, 270)
    painter.drawPath(path)
    end = path.currentPosition()
    tang = (-math.sin(math.radians(320)), -math.cos(math.radians(320)))
    for sign in (-1, 1):
        a = math.radians(sign * 38)
        bx = -(tang[0] * math.cos(a) - tang[1] * math.sin(a))
        by = -(tang[0] * math.sin(a) + tang[1] * math.cos(a))
        _poly(painter, [(end.x(), end.y()), (end.x() + 4.5 * bx, end.y() + 4.5 * by)])
    painter.restore()


def draw_power(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Power symbol: open ring with a bar."""
    _stroked(painter, rect, color)
    path = QPainterPath()
    path.arcMoveTo(QRectF(4.5, 5, 15, 15), 120)
    path.arcTo(QRectF(4.5, 5, 15, 15), 120, 300)
    painter.drawPath(path)
    _poly(painter, [(12, 2.8), (12, 11)])
    painter.restore()


def draw_sun(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    painter.drawEllipse(_P(12, 12), 4.2, 4.2)
    for i in range(8):
        a = math.radians(i * 45.0)
        _poly(painter, [(12 + 7.2 * math.cos(a), 12 + 7.2 * math.sin(a)),
                        (12 + 10 * math.cos(a), 12 + 10 * math.sin(a))])
    painter.restore()


def draw_moon(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    outer = QPainterPath()
    outer.addEllipse(QRectF(3.5, 3.5, 17, 17))
    cut = QPainterPath()
    cut.addEllipse(QRectF(8.5, 0.5, 15, 15))
    painter.drawPath(outer.subtracted(cut))
    painter.restore()


def draw_chevron_right(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    _poly(painter, [(9, 5), (16, 12), (9, 19)])
    painter.restore()


def draw_chevron_down(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    _poly(painter, [(5, 9), (12, 16), (19, 9)])
    painter.restore()


def draw_play(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    _poly(painter, [(8, 5), (19, 12), (8, 19)], close=True)
    painter.restore()


def draw_windows(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Four-pane window glyph (start with Windows)."""
    _stroked(painter, rect, color)
    for x, y in ((3.5, 3.5), (13, 3.5), (3.5, 13), (13, 13)):
        _rr(painter, x, y, 7.5, 7.5, 1.2)
    painter.restore()


def windows_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_windows, size, color)


def draw_warning(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    _poly(painter, [(12, 3.5), (21.5, 20), (2.5, 20)], close=True)
    _poly(painter, [(12, 10), (12, 14.5)])
    painter.drawPoint(_P(12, 17.2))
    painter.restore()


def draw_fahrenheit(painter: QPainter, rect: QRectF, color: QColor, flag: bool = True) -> None:
    """Degree glyph + C or F (flag True = the panel shows Fahrenheit)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    f = QFont("Segoe UI")
    f.setPixelSize(max(8, int(rect.height() * 0.62)))
    f.setWeight(QFont.Weight.DemiBold)
    painter.setFont(f)
    painter.setPen(color)
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "°F" if flag else "°C")
    painter.restore()


def _icon_from(draw, size: int, color: QColor, flag: bool = True) -> QIcon:
    icon = QIcon()
    for scale in (1, 2):
        px = max(1, int(size)) * scale
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(scale)
        p = QPainter(pm)
        draw(p, QRectF(0, 0, px / scale, px / scale), color, flag)
        p.end()
        icon.addPixmap(pm)
    return icon


def eye_icon(size: int, open: bool, color: QColor) -> QIcon:
    return _icon_from(draw_eye, size, color, open)


def refresh_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_refresh, size, color)


def power_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_power, size, color)


def sun_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_sun, size, color)


def moon_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_moon, size, color)


def play_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_play, size, color)


def warning_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_warning, size, color)


def chevron_icon(size: int, expanded: bool, color: QColor) -> QIcon:
    return _icon_from(draw_chevron_down if expanded else draw_chevron_right, size, color)


def fahrenheit_icon(size: int, fahrenheit: bool, color: QColor) -> QIcon:
    return _icon_from(draw_fahrenheit, size, color, fahrenheit)


def kind_icon(kind: str, size: int, color: QColor) -> QIcon:
    return _icon_from(lambda p, r, c, _f: draw_kind_icon(p, kind, r, c), size, color)


# --------------------------------------------------------------------------- settings page glyphs
def draw_chevron_up(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    _poly(painter, [(5, 15), (12, 8), (19, 15)])
    painter.restore()


def draw_back(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Left arrow."""
    _stroked(painter, rect, color)
    _poly(painter, [(20, 12), (5, 12)])
    _poly(painter, [(11, 6), (5, 12), (11, 18)])
    painter.restore()


def draw_display(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Monitor with a stand."""
    _stroked(painter, rect, color)
    _rr(painter, 3, 4.5, 18, 12, 2)
    _poly(painter, [(12, 16.5), (12, 20)])
    _poly(painter, [(8, 20), (16, 20)])
    painter.restore()


def draw_palette(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    painter.drawEllipse(_P(12, 12), 9, 9)
    for x, y in ((8, 10), (12, 7.5), (16, 10), (8.5, 15)):
        painter.drawPoint(_P(x, y))
    painter.restore()


def draw_pin(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    _poly(painter, [(12, 21), (6.5, 12.5), (6, 9.5), (8, 5.5), (12, 4), (16, 5.5), (18, 9.5), (17.5, 12.5)],
          close=True)
    painter.drawEllipse(_P(12, 9.5), 2.2, 2.2)
    painter.restore()


def draw_image(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Picture frame with a sun and a mountain."""
    _stroked(painter, rect, color)
    _rr(painter, 3.5, 4.5, 17, 15, 2)
    painter.drawEllipse(_P(9, 10), 1.8, 1.8)
    _poly(painter, [(4, 17.5), (9.5, 13), (13, 16), (16, 13.5), (20, 17)])
    painter.restore()


def draw_doc(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    _stroked(painter, rect, color)
    _rr(painter, 5, 3, 14, 18, 2)
    for y, x2 in ((9, 15.5), (13, 15.5), (17, 12.5)):
        _poly(painter, [(8.5, y), (x2, y)])
    painter.restore()


def draw_info(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Circle with an "i"."""
    _stroked(painter, rect, color)
    painter.drawEllipse(_P(12, 12), 9, 9)
    _poly(painter, [(12, 11), (12, 17)])
    painter.drawPoint(_P(12, 7.6))
    painter.restore()


def draw_update(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Arrow pointing down into a tray (download / update)."""
    _stroked(painter, rect, color)
    _poly(painter, [(12, 3.5), (12, 14.5)])
    _poly(painter, [(7.5, 10.5), (12, 15), (16.5, 10.5)])
    _poly(painter, [(4, 15.5), (4, 19.5), (20, 19.5), (20, 15.5)])
    painter.restore()


_GLYPHS = {"chevron_right": draw_chevron_right, "chevron_down": draw_chevron_down,
           "chevron_up": draw_chevron_up, "back": draw_back, "display": draw_display,
           "personalization": draw_palette, "position": draw_pin, "image": draw_image, "logs": draw_doc,
           "info": draw_info, "update": draw_update}


def glyph_icon(name: str, size: int, color: QColor) -> QIcon:
    """QIcon of a settings glyph ("back", "chevron_right", "display", ...); empty if unknown."""
    draw = _GLYPHS.get(name)
    return _icon_from(draw, size, color, True) if draw is not None else QIcon()


def make_tray_icon(text: str, dark: bool, accent: QColor | None = None, muted: bool = False) -> QIcon:
    """CPU chip with a short label (e.g. the CPU temperature) inside; "" draws the bare chip.

    `muted` draws everything in grey (stale data)."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fg = QColor(255, 255, 255) if dark else QColor(32, 32, 32)
    if muted:
        fg, accent = QColor(140, 140, 140), None
    pen = QPen(fg, 4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(12, 12, 40, 40), 8, 8)
    for v in (22, 32, 42):
        p.drawLine(_P(v, 3), _P(v, 12))
        p.drawLine(_P(v, 52), _P(v, 61))
        p.drawLine(_P(3, v), _P(12, v))
        p.drawLine(_P(52, v), _P(61, v))
    if text:
        f = QFont("Segoe UI")
        f.setPixelSize(22 if len(text) <= 2 else 18)
        f.setBold(True)
        p.setFont(f)
        p.setPen(accent or fg)
        p.drawText(QRectF(12, 12, 40, 40), int(Qt.AlignmentFlag.AlignCenter), text)
    p.end()
    return QIcon(pm)
