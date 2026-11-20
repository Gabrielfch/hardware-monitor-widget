"""Hardware panel: frameless card popup with source-status cards and the curated summary per hardware.

Look and behaviour adapted from the DevicePanel of Taskbar Battery Widget (MIT).

The panel shows only the curated summary (`curation.summary_sections`); every sensor lives in the separate
`DetailWindow`. It does not know `Config` (ADR-7): it receives `Prefs`, pins and values, and emits signals.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from . import custom_icons, dialogs, i18n, taskbar
from .sensor_names import display_labels, display_name, uniquify
from . import win_window as ww
from .colors import TASKBAR_BASE, band_color, row_background, section_header_color, text_color
from .curation import (Prefs, SummarySection, category_id, flatten_order, move_to, order_hardware,
                       section_title_key, summary_sections)
from .icons import (SENSOR_GLYPH, _icon_from, _P, _poly, _stroked, chevron_icon, draw_eye, draw_kind_icon,
                    eye_icon, icons_visible, moon_icon, play_icon, power_icon, refresh_icon,
                    sun_icon, warning_icon)
from .models import HW_KINDS, Sensor, Snapshot, SourceStatus, band_for, format_value, is_stale
from .pins import AUTO_PREFIX, resolve_auto

log = logging.getLogger(__name__)

SIZE_FRACTION = {"compact": 0.45, "normal": 0.62, "large": 0.80}     # of the available screen height
PANEL_SIZES = tuple(SIZE_FRACTION)
UI_SCALES = (90, 100, 115, 130)
HEIGHT_FRACTION = SIZE_FRACTION["normal"]
WIDTH_PER_HEIGHT = 0.62
MIN_WIDTH, MIN_HEIGHT = 340, 260
MAX_WIDTH_FRACTION = 0.4
ICON_PX, BTN_ICON_PX, BTN_PX = 20, 18, 32
MIN_TARGET_PX = 32      # touch targets never go below this, whatever the scale
ARM_FALLBACK_MS = 800
HIDE_CHECK_MS = 50
GAP = 8
DIM_ALPHA = 235      # stale values: icon and text of the rows (dim kept above 4.5:1; the text is also italic)

KIND_ORDER = ("temperature", "load", "level", "clock", "power", "voltage", "fan", "control",
              "flow", "data", "smalldata", "throughput", "factor")


def panel_dimensions(avail: QRect, size: str = "normal", scale: int = 100) -> tuple[int, int]:
    k = max(0.5, scale / 100)
    frac = SIZE_FRACTION.get(size, HEIGHT_FRACTION)
    height = min(avail.height(), max(round(MIN_HEIGHT * k), round(avail.height() * frac)))
    width = max(round(MIN_WIDTH * k),
                min(round(height * WIDTH_PER_HEIGHT * k), round(avail.width() * MAX_WIDTH_FRACTION)))
    return min(width, int(avail.width() * 0.9)), height


def place_popup(anchor: tuple, size: tuple, screen: tuple, edge: str = "bottom", gap: int = GAP) -> tuple:
    """Top-left of the popup: above a bottom taskbar, below a top one, beside a side one (clamped to screen)."""
    ax, ay, aw, ah = anchor
    pw, ph = size
    sx, sy, sw, sh = screen
    if edge == "top":
        x, y = ax + (aw - pw) // 2, ay + ah + gap
    elif edge == "left":
        x, y = ax + aw + gap, ay + (ah - ph) // 2
    elif edge == "right":
        x, y = ax - pw - gap, ay + (ah - ph) // 2
    else:
        x, y = ax + (aw - pw) // 2, ay - ph - gap
    return max(sx, min(x, sx + sw - pw)), max(sy, min(y, sy + sh - ph))


def hw_title(hw_kind: str) -> str:
    return i18n.tr("hw_" + hw_kind if hw_kind in HW_KINDS else "hw_other")


def section_title(sec: SummarySection) -> str:
    """Title of a summary section: the localized name for RAM / virtual memory, else the hardware name."""
    key = section_title_key(sec.hw_class)
    return i18n.tr(key) if key else (sec.hardware or hw_title(sec.hw_kind))


def sensor_status_text(s: Sensor, fahrenheit: bool, critical_mark: bool = True, with_range: bool = False) -> str:
    """Status line of a row: only the value (with "!" when critical); min / max live in `sensor_range_text`."""
    sep = i18n.decimal_sep()
    text = format_value(s.kind, s.value, fahrenheit, sep)
    if critical_mark and band_for(s.kind, s.value) == "red":
        text = "! " + text
    if with_range:
        rng = sensor_range_text(s, fahrenheit)
        if rng:
            text += " · " + rng
    return text


def sensor_range_text(s: Sensor, fahrenheit: bool) -> str:
    """"min X · max Y" ("" when the sensor has neither)."""
    if s.min is None and s.max is None:
        return ""
    sep = i18n.decimal_sep()
    return i18n.tr("min_max", min=format_value(s.kind, s.min, fahrenheit, sep),
                   max=format_value(s.kind, s.max, fahrenheit, sep))


def kind_label(kind: str) -> str:
    label = i18n.tr("kind_" + kind)
    return kind if label == "kind_" + kind else label


def sensor_a11y_name(s: Sensor, name: str | None = None) -> str:
    """Accessible name: hardware, shown (translated) sensor name and sensor type. Callers make it unique with
    `sensor_names.uniquify` when two sensors still collide."""
    return f"{s.hardware} · {name if name is not None else display_name(s)} ({kind_label(s.kind)})"


def covered_keys(pinned: list[str], snapshot: Snapshot) -> set[str]:
    """Keys shown as pills: explicit pins plus what the `auto:*` pins resolve to (same rule as `is_pinned`)."""
    out = {p for p in pinned if not p.startswith(AUTO_PREFIX)}
    for p in pinned:
        if p.startswith(AUTO_PREFIX):
            s = resolve_auto(p, snapshot)
            if s is not None:
                out.add(s.key)
    return out


def bulk_state_of(keys: list[str], covered: set[str]) -> str:
    n = sum(1 for k in keys if k in covered)
    if not keys or n == 0:
        return "none"
    return "all" if n == len(keys) else "some"


def _hex(c: QColor) -> str:
    return c.name()


def _css(c: QColor) -> str:
    """CSS colour that keeps the alpha channel."""
    return c.name() if c.alpha() == 255 else f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def _pm(kind: str, color: QColor, dpr: float, size: int = ICON_PX) -> QPixmap:
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    draw_kind_icon(p, kind, QRectF(0, 0, size, size), color)
    p.end()
    return pm


def kind_or_custom_pixmap(kind: str, color: QColor, dpr: float, size: int, custom: str | None) -> QPixmap | None:
    """Pixmap of a hardware/sensor glyph; the user's image when `custom` is usable. None: nothing to draw."""
    if custom:
        pm = custom_icons.pixmap_for(custom, size, dpr)
        if pm is not None:
            return pm
    if not icons_visible() and kind in HW_KINDS:
        return None
    return _pm(kind, color, dpr, size)


# --------------------------------------------------------------------------- glyphs drawn here
def draw_gear(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Settings gear: ring, centre dot and eight teeth."""
    import math
    _stroked(painter, rect, color)
    painter.drawEllipse(_P(12, 12), 5.6, 5.6)
    painter.drawPoint(_P(12, 12))
    for i in range(8):
        a = math.radians(i * 45)
        c, s = math.cos(a), math.sin(a)
        _poly(painter, [(12 + 7.6 * c, 12 + 7.6 * s), (12 + 10 * c, 12 + 10 * s)])
    painter.restore()


def draw_list(painter: QPainter, rect: QRectF, color: QColor, _flag: bool = True) -> None:
    """Bulleted list ("view all")."""
    _stroked(painter, rect, color)
    for y in (6.5, 12, 17.5):
        painter.drawPoint(_P(4.5, y))
        _poly(painter, [(9, y), (20.5, y)])
    painter.restore()


def draw_bulk(painter: QPainter, rect: QRectF, color: QColor, state: str = "none") -> None:
    """Pin-all control: slashed eye (none), open eye (all), half-filled eye with a dash below (some)."""
    draw_eye(painter, rect, color, state != "none")
    if state == "some":
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(rect.width(), rect.height())
        painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
        painter.scale(side / 24.0, side / 24.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(12, 12), 3.2, 3.2)               # filled pupil: not the empty "all" eye
        painter.drawRoundedRect(QRectF(7.5, 20.2, 9, 2.2), 1.1, 1.1)   # "partial" dash
        painter.restore()


def draw_unit(painter: QPainter, rect: QRectF, color: QColor, fahrenheit: bool = False) -> None:
    """Active temperature unit (°C / °F) as bold text, as heavy as the stroked glyphs of the other buttons."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    f = QFont(QApplication.font())
    f.setPixelSize(max(9, round(rect.height() * 0.78)))
    f.setWeight(QFont.Weight.Bold)
    painter.setFont(f)
    painter.setPen(color)
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "°F" if fahrenheit else "°C")
    painter.restore()


def unit_icon(size: int, fahrenheit: bool, color: QColor) -> QIcon:
    return _icon_from(draw_unit, size, color, fahrenheit)


def gear_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_gear, size, color)


def list_icon(size: int, color: QColor) -> QIcon:
    return _icon_from(draw_list, size, color)


def bulk_icon(size: int, state: str, color: QColor) -> QIcon:
    return _icon_from(lambda p, r, c, _f: draw_bulk(p, r, c, state), size, color)


class _ElidedLabel(QLabel):
    """Single-line label that elides only what does not fit."""

    def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight):
        super().__init__()
        self._full = text
        self._mode = mode
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        super().setText(text)

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:
        if text == self._full:
            return
        self._full = text
        self._elide()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def _elide(self) -> None:
        w, text = self.width(), self._full
        if w > 8 and QFontMetrics(self.font()).horizontalAdvance(text) > w:
            text = QFontMetrics(self.font()).elidedText(text, self._mode, w)
        if text != self.text():
            super().setText(text)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._elide()


class SensorRow(QFrame):
    """One sensor card: glyph, name, value line in band colour, pin (eye) button.

    The value line shows only the value; min / max are in the tooltip and appear as a second line while the
    row has keyboard focus or the mouse over it."""

    def __init__(self, panel: "HardwarePanel", sensor: Sensor, label: str | None = None, a11y: str | None = None):
        super().__init__()
        self.setObjectName("row")
        self._panel = panel
        self.key = sensor.key
        self._sensor = sensor
        self._alert: bool | None = None
        self._pinned: bool | None = None
        self._status = ""
        self._range = ""
        self._range_shown = False
        self._hover = False
        self._focus = False
        px = panel.px
        h = QHBoxLayout(self)
        h.setContentsMargins(px(8), px(4), px(4), px(4))
        h.setSpacing(px(8))
        self.icon = QLabel()
        self.icon.setFixedSize(px(ICON_PX), px(ICON_PX))
        h.addWidget(self.icon)
        cell = QWidget()
        cell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        cv = QVBoxLayout(cell)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self._base = display_name(sensor)
        self._label = label or self._base      # a type suffix when two sensors of a section share the name
        self.name = _ElidedLabel(self._label)
        cv.addWidget(self.name)
        self.status = _ElidedLabel("")
        cv.addWidget(self.status)
        self.range = _ElidedLabel("")           # min / max: only while hovered or focused
        self.range.setStyleSheet(f"color: {_hex(band_color('grey', panel.dark))}; background: transparent;")
        self.range.hide()
        cv.addWidget(self.range)
        h.addWidget(cell, 1)
        self.eye = QToolButton()
        self.eye.setFixedSize(panel.btn_px, panel.btn_px)
        self.eye.setCheckable(True)
        self.eye.setAutoRaise(True)
        self.eye.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.eye.setIconSize(QSize(px(BTN_ICON_PX), px(BTN_ICON_PX)))
        self.eye.setCursor(Qt.CursorShape.PointingHandCursor)
        full_name = a11y or sensor_a11y_name(sensor, self._base)
        self.eye.setAccessibleName(i18n.tr("pin_name", name=full_name))
        self.eye.clicked.connect(lambda _c=False: panel.pin_toggled.emit(self._sensor))
        self.eye.installEventFilter(self)
        h.addWidget(self.eye)
        self.setAccessibleName(full_name)
        glyph = SENSOR_GLYPH.get(sensor.kind, "chart" if sensor.unit else sensor.hw_kind)
        self._glyph = glyph
        self._custom = custom_icons.file_for("", "", sensor.key)      # sensor-level image only
        self._stale: bool | None = None
        self._color = ""
        self.apply(sensor, True, True)

    # ---- min / max line: hover or keyboard focus
    def _sync_range(self) -> None:
        want = bool(self._range) and (self._hover or self._focus)
        if want:
            self.range.setText(self._range)
        if want != self._range_shown:
            self._range_shown = want
            self.range.setVisible(want)

    def set_hover(self, value: bool) -> None:
        if value != self._hover:
            self._hover = value
            self._sync_range()

    def set_focus_within(self, value: bool) -> None:
        if value != self._focus:
            self._focus = value
            self._sync_range()

    def _cursor_inside(self) -> bool:
        return self.rect().contains(self.mapFromGlobal(QCursor.pos()))

    def enterEvent(self, ev) -> None:
        super().enterEvent(ev)
        self.set_hover(True)

    def leaveEvent(self, ev) -> None:
        super().leaveEvent(ev)
        self.set_hover(self._cursor_inside())

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._hover = self._focus = False
        self._sync_range()

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.eye:
            t = ev.type()
            if t == QEvent.Type.FocusIn:
                self.set_focus_within(True)
            elif t == QEvent.Type.FocusOut:
                self.set_focus_within(False)
        return super().eventFilter(obj, ev)

    def tooltip_text(self, stale: bool) -> str:
        """Full name, original name when it was translated, min / max and the stale notice."""
        lines = [self._label]
        if self._sensor.name != self._base:
            lines.append(i18n.tr("sensor_orig_name", name=self._sensor.name))
        if self._range:
            lines.append(self._range)
        if stale:
            lines.append(i18n.tr("stale_tip"))
        return "\n".join(lines)

    def apply(self, sensor: Sensor, pinned: bool, force: bool = False, stale: bool = False) -> None:
        """Refresh values in place; touches a widget only when its content changed."""
        panel = self._panel
        dark = panel.dark
        self._sensor = sensor
        band = band_for(sensor.kind, sensor.value)
        text = sensor_status_text(sensor, panel.fahrenheit)
        rng = sensor_range_text(sensor, panel.fahrenheit)
        alert = band == "red"
        color = band_color(band, dark)
        if stale:
            color = QColor(color)
            color.setAlpha(DIM_ALPHA)
        if force or alert != self._alert:
            self._alert = alert
            border = f"2px solid {_hex(band_color('red', dark))}" if alert else "2px solid transparent"
            self.setStyleSheet(f"QFrame#row {{ background: {_hex(row_background(dark, alert))}; "
                               f"border: {border}; border-radius: 6px; }}")
        range_changed = rng != self._range
        if force or range_changed or stale != self._stale:
            self._range = rng
            self.setToolTip(self.tooltip_text(stale))
            if self._range_shown:
                self._sync_range()
        if force or range_changed or text != self._status or stale != self._stale:
            self._status = text
            self.status.setText(text)
            desc = text + (" · " + rng if rng else "") + (" · " + i18n.tr("critical") if alert else "")
            self.setAccessibleDescription(desc + (" · " + i18n.tr("stale_tip") if stale else ""))
        css = _css(color)
        if force or css != self._color:
            self._color = css
            self.status.setStyleSheet(f"color: {css}; background: transparent;"
                                      + (" font-style: italic;" if stale else ""))
            pm = kind_or_custom_pixmap(self._glyph, color, panel.devicePixelRatioF() or 1.0,
                                       panel.px(ICON_PX), self._custom)
            if pm is not None:
                self.icon.setPixmap(pm)
        self._stale = stale
        if force or pinned != self._pinned:
            self._pinned = pinned
            self.eye.setChecked(pinned)
            col = text_color(dark) if pinned else band_color("grey", dark)
            self.eye.setIcon(eye_icon(panel.px(BTN_ICON_PX), pinned, col))
            self.eye.setToolTip(i18n.tr("pin_on" if pinned else "pin_off"))


class SourceCard(QFrame):
    """Status card for a sensor source that is not available, with its action button."""

    def __init__(self, panel: "HardwarePanel", status: SourceStatus):
        super().__init__()
        self.setObjectName("row")
        self.source = status.source
        self.reason = status.reason
        dark = panel.dark
        px = panel.px
        amber = band_color("amber", dark)
        self.setStyleSheet(f"QFrame#row {{ background: {_hex(row_background(dark))}; "
                           f"border: 2px solid transparent; border-radius: 6px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(px(8), px(6), px(6), px(6))
        outer.setSpacing(px(6))
        h = QHBoxLayout()
        h.setSpacing(px(8))
        outer.addLayout(h)
        icon = QLabel()
        icon.setPixmap(warning_icon(px(ICON_PX), amber).pixmap(px(ICON_PX), px(ICON_PX)))
        icon.setFixedSize(px(ICON_PX), px(ICON_PX))
        h.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        self.message = self.message_for(status)
        self.label = QLabel(self.message)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.label.setStyleSheet(f"color: {_hex(amber)}; background: transparent;")
        h.addWidget(self.label, 1)
        admin = status.reason == "needs_admin"
        self.action = "restart_admin" if admin else "retry"
        text = i18n.tr(self.action)
        self.button = QToolButton()
        self.button.setText(text)
        self.button.setIcon(refresh_icon(px(BTN_ICON_PX), text_color(dark)) if not admin
                            else play_icon(px(BTN_ICON_PX), text_color(dark)))
        self.button.setIconSize(QSize(px(BTN_ICON_PX), px(BTN_ICON_PX)))
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setAccessibleName(text)
        self.button.setObjectName("cardBtn")
        self.button.setMinimumHeight(panel.btn_px)
        self.button.clicked.connect(
            lambda _c=False: (panel.restart_admin_requested if admin else panel.retry_requested).emit())
        outer.addWidget(self.button, 0, Qt.AlignmentFlag.AlignRight)   # below the text: long messages keep the width
        self.setAccessibleName(self.message)

    @staticmethod
    def message_for(status: SourceStatus) -> str:
        if status.reason == "needs_admin":
            return i18n.tr("status_needs_admin")
        if status.reason in ("stale", "error"):
            if status.detail:
                return i18n.tr("status_stale", detail=status.detail)
            return i18n.tr("status_stale_plain")
        if status.detail:
            return i18n.tr("status_error", detail=status.detail)
        return i18n.tr("status_error_plain")


class _HeaderBar(QWidget):
    """Header of a section or category: a click toggles it, a drag past the threshold reorders it and the
    context menu (also the Menu key on a focused button) offers "Move up" / "Move down"."""

    def __init__(self, owner: "Section"):
        super().__init__()
        self._owner = owner
        self._press: QPoint | None = None
        self._dragging = False

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.globalPosition().toPoint()
            self._dragging = False
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._press is None or not ev.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(ev)
            return
        gp = ev.globalPosition().toPoint()
        threshold = QGuiApplication.styleHints().startDragDistance()
        if not self._dragging and (gp - self._press).manhattanLength() > threshold:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._owner._panel._drag_begin(self._owner)
        if self._dragging:
            self._owner._panel._drag_update(self._owner, gp)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton or self._press is None:
            super().mouseReleaseEvent(ev)
            return
        was, self._press, self._dragging = self._dragging, None, False
        self.unsetCursor()
        if was:
            self._owner._panel._drag_end(self._owner, ev.globalPosition().toPoint(), True)
        else:
            self._owner.set_collapsed(not self._owner.collapsed, notify=True)

    def cancel_drag(self) -> None:
        self._press, self._dragging = None, False
        self.unsetCursor()

    def contextMenuEvent(self, ev) -> None:
        self._owner.show_menu(ev.globalPos(), keyboard=ev.reason() == ev.Reason.Keyboard)
        ev.accept()


class Section(QWidget):
    """Collapsible block of the panel: header (chevron, icon, name, pin-all control, detail button) and its
    content. With `category` it is the header of a category (no detail button; its body holds the sections of
    its components). `order_id` is what the user reorders: the hw_id of a component inside a category, the
    `cat:<category>` id of a category (also of a category with a single component, shown without a header)."""

    def __init__(self, panel: "HardwarePanel", sec: SummarySection, title: str, collapsed: bool,
                 order_id: str | None = None, category: bool = False):
        super().__init__()
        self.hw_id = sec.hw_id
        self.order_id = order_id or sec.hw_id
        self.is_category = category
        self.keys = [s.key for s in sec.sensors]
        self._panel = panel
        self._title = title
        self._collapsed = collapsed
        self._bulk_state = ""
        self._bulk_n = -1
        self._bulk_p = -1
        dark = panel.dark
        px, btn = panel.px, panel.btn_px
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(px(4))
        head = _HeaderBar(self)
        self._head = head
        hl = QHBoxLayout(head)
        hl.setContentsMargins(px(4), px(4), px(2), px(2))
        hl.setSpacing(px(4))
        self.chevron = QToolButton()
        self.chevron.setFixedSize(btn, btn)
        self.chevron.setAutoRaise(True)
        self.chevron.setIconSize(QSize(px(BTN_ICON_PX - 2), px(BTN_ICON_PX - 2)))
        self.chevron.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chevron.clicked.connect(lambda _c=False: self.set_collapsed(not self._collapsed, notify=True))
        hl.addWidget(self.chevron)
        color = section_header_color(dark)
        self.icon: QLabel | None = None
        pm = kind_or_custom_pixmap(sec.hw_kind, color, panel.devicePixelRatioF() or 1.0, px(ICON_PX),
                                   custom_icons.file_for(sec.hw_kind, sec.hw_id))
        if pm is not None:
            self.icon = QLabel()
            self.icon.setPixmap(pm)
            self.icon.setFixedSize(px(ICON_PX), px(ICON_PX))
            hl.addWidget(self.icon)
        self.title = _ElidedLabel(title, Qt.TextElideMode.ElideMiddle)     # keeps the end: "(WI-FI)"
        f = QFont(self.title.font())
        f.setBold(True)
        self.title.setFont(f)
        self.title.setStyleSheet(f"color: {_hex(color)}; background: transparent;")
        self.title.setToolTip(title)
        name = i18n.tr("cat_header_name", name=title) if category else title
        self.title.setAccessibleName(name)
        self.title.setAccessibleDescription(name)
        hl.addWidget(self.title, 1)
        self.bulk = QToolButton()
        self.bulk.setObjectName("bulkBtn")
        self.bulk.setFixedSize(btn, btn)
        self.bulk.setCheckable(True)
        self.bulk.setAutoRaise(True)
        self.bulk.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.bulk.setIconSize(QSize(px(BTN_ICON_PX), px(BTN_ICON_PX)))
        self.bulk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bulk.clicked.connect(lambda _c=False: panel._on_bulk_clicked(self.hw_id))
        hl.addWidget(self.bulk)
        self.detail_btn: QToolButton | None = None
        if not category:
            self.detail_btn = QToolButton()
            self.detail_btn.setObjectName("sectionDetailBtn")
            self.detail_btn.setFixedSize(btn, btn)
            self.detail_btn.setAutoRaise(True)
            self.detail_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.detail_btn.setIconSize(QSize(px(BTN_ICON_PX), px(BTN_ICON_PX)))
            self.detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.detail_btn.setIcon(list_icon(px(BTN_ICON_PX), text_color(dark)))
            tip = i18n.tr("section_detail_tip", name=title)
            self.detail_btn.setToolTip(tip)
            self.detail_btn.setAccessibleName(tip)
            self.detail_btn.clicked.connect(lambda _c=False: panel._request_detail(self.hw_id))
            hl.addWidget(self.detail_btn)
        for b in (self.chevron, self.bulk, self.detail_btn):     # Alt+Up / Alt+Down move the focused header
            if b is not None:
                b.installEventFilter(self)
        self.chevron.setAccessibleDescription(i18n.tr("reorder_hint"))
        v.addWidget(head)
        self.notice = QLabel()
        self.notice.setObjectName("bulkNotice")
        self.notice.setContentsMargins(px(8), 0, px(8), 0)
        self.notice.setStyleSheet(f"color: {_hex(band_color('amber', dark))}; background: transparent;")
        self.notice.hide()
        v.addWidget(self.notice)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(px(10) if category else 0, 0, 0, 0)
        self.body_layout.setSpacing(px(4))
        v.addWidget(self.body)
        self._color = color
        self.set_collapsed(collapsed, notify=False)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def eventFilter(self, obj, ev) -> bool:
        if (ev.type() == QEvent.Type.KeyPress and ev.modifiers() & Qt.KeyboardModifier.AltModifier
                and ev.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down)):
            self._panel.move_item(self.order_id, -1 if ev.key() == Qt.Key.Key_Up else 1, focus=True)
            return True
        return super().eventFilter(obj, ev)

    def build_menu(self, parent: QWidget | None = None, focus: bool = False) -> QMenu:
        """Context menu of the header: Move up / Move down (disabled at the ends)."""
        menu = QMenu(parent or self)
        for label, delta in ((i18n.tr("move_up"), -1), (i18n.tr("move_down"), 1)):
            act = menu.addAction(label)
            act.setEnabled(self._panel.can_move(self.order_id, delta))
            act.triggered.connect(lambda _c=False, d=delta: self._panel.move_item(self.order_id, d, focus=focus))
        return menu

    def show_menu(self, global_pos: QPoint, keyboard: bool = False) -> None:
        menu = self.build_menu(self._panel, focus=keyboard)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(global_pos)

    def set_collapsed(self, value: bool, notify: bool = False) -> None:
        self._collapsed = bool(value)
        self.body.setVisible(not self._collapsed)
        self.chevron.setIcon(chevron_icon(self._panel.px(BTN_ICON_PX - 2), not self._collapsed, self._color))
        label = i18n.tr("cat_header_name", name=self._title) if self.is_category else self._title
        tip = i18n.tr("expand" if self._collapsed else "collapse", name=label)
        self.chevron.setToolTip(tip)
        self.chevron.setAccessibleName(tip)
        if notify:
            self._panel._on_section_toggled(self.hw_id, self._collapsed)

    def set_bulk(self, state: str, n: int, pinned: int | None = None) -> None:
        """Pin-all control: icon, tooltip and accessible name follow none / some / all ("p of n" when some)."""
        if pinned is None:
            pinned = n if state == "all" else 0
        want_checked = state == "all"
        if self.bulk.isChecked() != want_checked:
            self.bulk.blockSignals(True)
            self.bulk.setChecked(want_checked)
            self.bulk.blockSignals(False)
        if state == self._bulk_state and n == self._bulk_n and pinned == self._bulk_p:
            return
        self._bulk_state, self._bulk_n, self._bulk_p = state, n, pinned
        panel = self._panel
        self.bulk.setIcon(bulk_icon(panel.px(BTN_ICON_PX), state, text_color(panel.dark)))
        self.bulk.setAccessibleName(i18n.tr("cat_bulk_name" if self.is_category else "bulk_name",
                                            name=self._title, state=i18n.tr("bulk_state_" + state)))
        if state == "some":
            self.bulk.setToolTip(i18n.tr("bulk_tip_some", p=pinned, n=n, name=self._title))
        else:
            self.bulk.setToolTip(i18n.tr("bulk_tip_unpin" if state == "all" else "bulk_tip_pin",
                                         n=n, name=self._title))
        self.bulk.setProperty("bulkState", state)

    def set_notice(self, skipped: int) -> None:
        if skipped > 0:
            self.notice.setText(i18n.tr("bulk_skipped", n=skipped))
            self.notice.setAccessibleName(i18n.tr("bulk_skipped_name", n=skipped))
            self.notice.show()
        else:
            self.notice.hide()


class HardwarePanel(QFrame):
    pin_toggled = Signal(object)            # Sensor
    bulk_pin_requested = Signal(str)        # hw_id or "cat:<category>": pin / unpin every summary sensor of it
    detail_requested = Signal(str)          # hw_id, or "" for the whole detail window
    settings_requested = Signal()           # gear button of the bottom bar
    display_settings_requested = Signal()   # "Display settings" shortcut of the empty state
    restart_admin_requested = Signal()
    retry_requested = Signal()
    refresh_requested = Signal()
    theme_toggle_requested = Signal()
    fahrenheit_toggled = Signal(bool)
    startup_toggled = Signal(bool)          # kept for compatibility: the bottom bar no longer has this button
    collapsed_changed = Signal(object)      # list[str] of collapsed hw_ids and category ids
    hw_order_changed = Signal(object)       # list[str]: the new hw_order (categories and components)
    quit_requested = Signal()

    def __init__(self, parent=None, dark: bool | None = None):
        # Not a Qt.Popup: "click outside" is emulated with the application-state check below.
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("HardwarePanel")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(i18n.tr("app_name"))
        self._dark = (not taskbar.taskbar_uses_light_theme()) if dark is None else bool(dark)
        self._snapshot = Snapshot()
        self._prefs = Prefs()
        self._pinned: list[str] = []
        self._collapsed: set[str] = set()
        self._fahrenheit = False
        self._startup = False
        self._size = "normal"
        self._scale = 100
        self._base_font = QFont(QApplication.font())
        self._sig: tuple | None = None
        self._summary: dict[str, list[str]] = {}
        self._notices: dict[str, int] = {}
        self.rows: dict[str, SensorRow] = {}
        self.cards: list[SourceCard] = []
        self.sections: dict[str, Section] = {}        # component sections by hw_id
        self.categories: dict[str, Section] = {}      # category headers by "cat:<category>" (2+ components)
        self._tops: list[Section] = []                # top-level blocks in display order
        self._tree: list[tuple[str, list[str]]] = []  # displayed [(category, [hw_id, ...])]
        self._hw_order: list[str] = []
        self._drag_item: Section | None = None
        self._indicator: QFrame | None = None
        self._empty: QLabel | None = None
        self.empty_buttons: dict[str, QToolButton] = {}
        self._armed = False
        self._show_gen = 0
        self._screen_avail: QRect | None = None
        self._edge = "bottom"
        self._posted_hwnd = 0
        self.hidden_at: float | None = None
        self._settings_view = None           # SettingsView, created on the first show_settings()
        self._settings_model = None
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state_changed)
            app.focusWindowChanged.connect(self._on_focus_window_changed)
        dialogs.hub.opened.connect(self._on_dialog_opened)
        dialogs.hub.closed.connect(self._on_dialog_closed)

        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.viewport().setAutoFillBackground(False)
        self._toolbar: dict[str, QToolButton] = {}
        self._lay = QVBoxLayout(self)
        self._lay.addWidget(self._build_topbar())
        self._stack = QStackedWidget(self)       # page 0: summary, page 1: Settings
        self._stack.addWidget(self._scroll)
        self._lay.addWidget(self._stack, 1)
        self._lay.addWidget(self._build_toolbar())
        self._apply_metrics()
        self._apply_style()
        self._fit(self._primary_avail())
        self.set_snapshot(self._snapshot)       # shows "Reading sensors..." until the first snapshot

    # ------------------------------------------------------------------ metrics
    def px(self, n: float) -> int:
        """`n` logical pixels at the current interface scale."""
        return max(1, round(n * self._scale / 100))

    @property
    def btn_px(self) -> int:
        return max(MIN_TARGET_PX, self.px(BTN_PX))

    @property
    def size_name(self) -> str:
        return self._size

    @property
    def scale_pct(self) -> int:
        return self._scale

    def set_metrics(self, size: str, scale_pct: int) -> None:
        """Panel size (compact / normal / large) and interface scale in percent (90 / 100 / 115 / 130)."""
        size = size if size in SIZE_FRACTION else "normal"
        scale = int(scale_pct) if isinstance(scale_pct, (int, float)) and 50 <= scale_pct <= 200 else 100
        if (size, scale) == (self._size, self._scale):
            return
        self._size, self._scale = size, scale
        self._apply_metrics()
        if self._settings_view is not None:
            self._settings_view.set_scale(scale / 100.0)
        self._fit(self._screen_avail or self._primary_avail())
        self._sig = None
        self.set_snapshot(self._snapshot)

    def _apply_metrics(self) -> None:
        f = QFont(self._base_font)
        if f.pointSizeF() > 0:
            f.setPointSizeF(f.pointSizeF() * self._scale / 100)
        elif f.pixelSize() > 0:
            f.setPixelSize(max(1, round(f.pixelSize() * self._scale / 100)))
        self.setFont(f)
        m = self.px(6)
        self._lay.setContentsMargins(m, m, m, m)
        self._lay.setSpacing(self.px(4))
        for b in self._toolbar.values():
            b.setFixedSize(self.btn_px, self.btn_px)
            b.setIconSize(QSize(self.px(BTN_ICON_PX), self.px(BTN_ICON_PX)))

    # ------------------------------------------------------------------ public API
    @property
    def dark(self) -> bool:
        return self._dark

    @property
    def fahrenheit(self) -> bool:
        return self._fahrenheit

    def set_dark(self, dark: bool) -> None:
        if bool(dark) != self._dark:
            self._dark = bool(dark)
            self._apply_style()
            if self._settings_view is not None:
                self._settings_view.set_dark(self._dark)
            self._sig = None
            self.set_snapshot(self._snapshot)

    def refresh_appearance(self) -> None:
        """Palette, icon style or custom icons changed: redraw rows, headers and Settings."""
        self._refresh_toolbar()
        self._sig = None
        self.set_snapshot(self._snapshot)
        self.refresh_settings()

    def set_prefs(self, prefs: Prefs) -> None:
        """Hidden hardware, enabled categories and per-sensor overrides of the summary."""
        self._prefs = prefs if isinstance(prefs, Prefs) else Prefs()
        self.set_snapshot(self._snapshot)

    def set_pinned(self, pinned: list[str]) -> None:
        pinned = list(pinned)
        if pinned != self._pinned:
            for hw_id in list(self._notices):       # "N not added" is only true for the pins it was made with
                self.set_bulk_notice(hw_id, 0)
        self._pinned = pinned
        self._update_in_place()

    def set_fahrenheit(self, value: bool) -> None:
        if bool(value) != self._fahrenheit:
            self._fahrenheit = bool(value)
            b = self._toolbar["fahrenheit"]
            b.blockSignals(True)
            b.setChecked(self._fahrenheit)
            b.blockSignals(False)
            self._refresh_toolbar()
            self._update_in_place()

    def set_start_with_windows(self, value: bool) -> None:
        """Compatibility no-op: "Start with Windows" moved to Settings (the bottom bar has no button)."""
        self._startup = bool(value)

    def set_collapsed(self, hw_ids) -> None:
        self._collapsed = {x for x in (hw_ids or ()) if isinstance(x, str)}
        for sec in self._blocks():
            sec.set_collapsed(sec.hw_id in self._collapsed)

    def _blocks(self) -> list[Section]:
        return [*self.categories.values(), *self.sections.values()]

    def hw_order(self) -> list[str]:
        return list(self._hw_order)

    def set_hw_order(self, order) -> None:
        """Saved order of categories and components (`cat:<category>` and hw_id entries; [] = default)."""
        order = [x for x in (order or ()) if isinstance(x, str)]
        if order != self._hw_order:
            self._hw_order = order
            self._sig = None
            self.set_snapshot(self._snapshot)

    def set_snapshot(self, snapshot: Snapshot) -> None:
        """Show `snapshot`; rebuilds only when the structure changed, otherwise updates values in place."""
        self._snapshot = snapshot
        by_id = {s.hw_id: s for s in summary_sections(snapshot, self._prefs)}
        layout = [(cat, [h for h in ids if h in by_id]) for cat, ids in order_hardware(snapshot, self._hw_order)]
        layout = [(cat, ids) for cat, ids in layout if ids]
        self._tree = layout
        sections = [by_id[h] for _c, ids in layout for h in ids]
        bad = self._bad_statuses(snapshot)
        loading = self._is_loading(snapshot)
        self._summary = {s.hw_id: [x.key for x in s.sensors] for s in sections}
        for cat, ids in layout:
            self._summary[category_id(cat)] = [k for h in ids for k in self._summary[h]]
        sig = (self._dark, i18n.get_language(), loading, self._size, self._scale, bool(snapshot.sensors),
               icons_visible(), custom_icons.maps_version(),
               tuple((s.source, s.reason, s.detail) for s in bad),
               tuple((s.hw_id, s.hw_kind, s.hw_class, s.hardware, tuple(self._summary[s.hw_id]))
                     for s in sections),
               tuple((c, tuple(ids)) for c, ids in layout))
        if sig != self._sig and self._drag_item is None:        # never rebuild under a header being dragged
            self._sig = sig
            self._rebuild(sections, bad, loading)
        else:
            self._update_in_place()

    @staticmethod
    def _is_loading(snapshot: Snapshot) -> bool:
        """No snapshot received yet (the default empty one)."""
        return not snapshot.sensors and not snapshot.statuses and not snapshot.taken_at

    def row_keys(self) -> list[str]:
        return list(self.rows)

    def summary_sensors(self, hw_id: str) -> list[Sensor]:
        """Current summary sensors of `hw_id` (or of every component of a `cat:<category>`), in panel order:
        what the pin-all control acts on."""
        by_key = self._snapshot.by_key()
        return [by_key[k] for k in self._summary.get(hw_id, ()) if k in by_key]

    def set_bulk_notice(self, hw_id: str, skipped: int) -> None:
        """Show "N not added" next to the pin-all control of `hw_id` (0 clears it)."""
        if skipped > 0:
            self._notices[hw_id] = int(skipped)
        else:
            self._notices.pop(hw_id, None)
        sec = self.sections.get(hw_id) or self.categories.get(hw_id)
        if sec is not None:
            sec.set_notice(self._notices.get(hw_id, 0))

    def retranslate(self) -> None:
        """Re-read every text after a language change."""
        self.setAccessibleName(i18n.tr("app_name"))
        self._title.setText(i18n.tr("panel_title"))
        self._refresh_toolbar()
        self._sig = None
        self.set_snapshot(self._snapshot)
        self.refresh_settings()

    # ------------------------------------------------------------------ settings pages
    @property
    def in_settings(self) -> bool:
        return self._settings_view is not None and self._stack.currentWidget() is self._settings_view

    @property
    def settings_view(self):
        return self._settings_view

    def set_settings_model(self, model) -> None:
        """The Settings tree (built by the controller); an open page re-reads it."""
        self._settings_model = model
        if self._settings_view is not None:
            self._settings_view.set_model(model)

    def show_settings(self, page_id: str | None = None, direct: bool = False) -> None:
        """Replace the summary by Settings page `page_id` (default: the root). With `direct` Back returns to the
        summary instead of the Settings root."""
        if self._settings_model is None:
            return
        if self._settings_view is None:
            from .settings_view import SettingsView
            v = SettingsView(self._settings_model, self._dark, scale=self._scale / 100.0)
            v.leave_requested.connect(self.show_summary)
            v.layout_changed.connect(self._on_settings_layout)
            v.rebuilt.connect(self._chain_settings_tab_order)
            self._settings_view = v
            self._stack.addWidget(v)
        self._settings_view.show_page(page_id or "root", direct=direct)
        self._stack.setCurrentWidget(self._settings_view)
        self._chain_settings_tab_order()
        if self.isVisible():
            self._settings_view.focus_first()

    def show_summary(self) -> None:
        """Back to the summary (from any Settings page)."""
        was = self.in_settings
        if self._settings_view is not None:
            self._stack.setCurrentWidget(self._scroll)
            self._settings_view.reset()
        if was:
            self._apply_tab_order()
            if self.isVisible():
                self._toolbar["settings"].setFocus(Qt.FocusReason.OtherFocusReason)

    show_devices = show_summary

    def refresh_settings(self) -> None:
        """Re-read the values shown on the open Settings page (no-op on the summary)."""
        if self.in_settings:
            self._settings_view.refresh()

    def _on_settings_layout(self) -> None:
        if self.in_settings:
            self._fit(self._screen_avail or self._primary_avail())

    def _on_gear(self) -> None:
        if self.in_settings:
            self.show_summary()
        else:
            self.settings_requested.emit()

    def _chain_settings_tab_order(self) -> None:
        if self._settings_view is None:
            return
        order = self._settings_view.focus_order() + list(self._toolbar.values())
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)

    def show_at(self, anchor) -> None:
        """Open the panel next to `anchor` (QPoint or QRect, global coordinates)."""
        rect = QRect(anchor, QSize(1, 1)) if isinstance(anchor, QPoint) else QRect(anchor)
        point = QPoint(anchor) if isinstance(anchor, QPoint) else rect.center()
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        geo, avail = screen.geometry(), screen.availableGeometry()
        self._screen_avail = avail
        self._fit(avail)
        c = rect.center()
        dist = {"top": c.y() - geo.top(), "bottom": geo.bottom() - c.y(),
                "left": c.x() - geo.left(), "right": geo.right() - c.x()}
        edge = min(dist, key=dist.get)
        x, y = place_popup((rect.x(), rect.y(), rect.width(), rect.height()), (self.width(), self.height()),
                           (avail.x(), avail.y(), avail.width(), avail.height()), edge=edge)
        self._edge = edge
        self.move(x, y)
        self.show()
        self.raise_()
        self._set_topmost(True)
        self.activateWindow()
        self._foreground()
        if self.in_settings:
            self._settings_view.focus_first()
        else:
            self._focus_panel()
        self._show_gen += 1
        gen = self._show_gen
        QTimer.singleShot(ARM_FALLBACK_MS, lambda: self._arm_fallback(gen, 0))

    def _focus_panel(self) -> None:
        """Initial focus on the panel itself so no button shows a focus ring after a mouse open."""
        fw = self.focusWidget()
        if fw is not None and fw is not self:
            fw.clearFocus()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------ build
    @staticmethod
    def _bad_statuses(snapshot: Snapshot) -> list[SourceStatus]:
        """Sources that need a card: anything not "ok", also when degraded (available with sensors)."""
        return [st for st in snapshot.statuses.values() if not st.available or st.reason not in ("ok", "")]

    def _clear_content(self) -> None:
        old = self._scroll.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        self.rows, self.cards, self.sections, self._empty = {}, [], {}, None
        self.categories, self._tops, self._indicator = {}, [], None
        self.empty_buttons = {}

    def _rebuild(self, sections: list[SummarySection], bad: list[SourceStatus], loading: bool = False) -> None:
        bar = self._scroll.verticalScrollBar()
        pos = bar.value()
        self._clear_content()
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(self.px(4))
        for st in bad:
            card = SourceCard(self, st)
            self.cards.append(card)
            v.addWidget(card)
        every = [s for sec_data in sections for s in sec_data.sensors]
        a11y_names = dict(zip((s.key for s in every), uniquify([sensor_a11y_name(s) for s in every])))
        by_id = {s.hw_id: s for s in sections}
        for cat, ids in self._tree:
            cid = category_id(cat)
            if len(ids) == 1:           # one component: its header is the category header (no duplicate)
                top = self._make_section(by_id[ids[0]], a11y_names, cid)
            else:
                members = [by_id[h] for h in ids]
                top = Section(self, SummarySection(cid, cat, "", "", [s for m in members for s in m.sensors]),
                              i18n.tr("cat_" + cat), cid in self._collapsed, order_id=cid, category=True)
                self.categories[cid] = top
                top.set_notice(self._notices.get(cid, 0))
                for m in members:
                    top.body_layout.addWidget(self._make_section(m, a11y_names, m.hw_id))
            self._tops.append(top)
            v.addWidget(top)
        if not sections:
            if loading:
                v.addWidget(self._make_empty(i18n.tr("loading")))
            elif not self._snapshot.sensors:
                if not bad:
                    v.addWidget(self._make_empty(i18n.tr("empty")))
            else:
                v.addWidget(self._make_empty(i18n.tr("summary_empty"), actions=True))
        v.addStretch(1)
        self._indicator = QFrame(content)       # drop line shown while a header is dragged
        self._indicator.setObjectName("dropLine")
        self._indicator.setFixedHeight(max(2, self.px(3)))
        self._indicator.setStyleSheet(f"QFrame#dropLine {{ background: {_hex(band_color('amber', self._dark))}; "
                                      f"border: none; border-radius: 1px; }}")
        self._indicator.hide()
        self._scroll.setWidget(content)
        self._update_in_place(force=True)
        self._apply_tab_order()
        bar.setValue(pos)

    def _make_section(self, sec_data: SummarySection, a11y_names: dict[str, str], order_id: str) -> Section:
        sec = Section(self, sec_data, section_title(sec_data), sec_data.hw_id in self._collapsed, order_id=order_id)
        self.sections[sec_data.hw_id] = sec
        sec.set_notice(self._notices.get(sec_data.hw_id, 0))
        labels = display_labels(sec_data.sensors)
        for s in sec_data.sensors:
            row = SensorRow(self, s, labels[s.key], a11y_names[s.key])
            self.rows[s.key] = row
            sec.body_layout.addWidget(row)
        return sec

    def _make_empty(self, text: str, actions: bool = False) -> QWidget:
        """Empty-state message; with `actions` also the shortcuts to the detail window and Display settings."""
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(self.px(6))
        self._empty = QLabel(text)
        self._empty.setWordWrap(True)
        self._empty.setContentsMargins(self.px(8), self.px(8), self.px(8), self.px(2))
        self._empty.setStyleSheet(f"color: {_hex(band_color('grey', self._dark))}; background: transparent;")
        self._empty.setAccessibleName(text)
        bl.addWidget(self._empty)
        if actions:
            col = text_color(self._dark)
            for name, label, icon, sig in (
                    ("detail", i18n.tr("detail_btn"), list_icon(self.px(BTN_ICON_PX), col),
                     lambda _c=False: self._request_detail("")),
                    ("display", i18n.tr("open_display_settings"), gear_icon(self.px(BTN_ICON_PX), col),
                     lambda _c=False: self.display_settings_requested.emit())):
                b = QToolButton()
                b.setObjectName("cardBtn")
                b.setText(label)
                b.setIcon(icon)
                b.setIconSize(QSize(self.px(BTN_ICON_PX), self.px(BTN_ICON_PX)))
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                b.setMinimumHeight(self.btn_px)
                b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setAccessibleName(label)
                b.clicked.connect(sig)
                self.empty_buttons[name] = b
                bl.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        return box

    def _update_in_place(self, force: bool = False) -> None:
        snap = self._snapshot
        by_key = snap.by_key()
        stale = is_stale(snap)
        covered = covered_keys(self._pinned, snap)
        for key, row in self.rows.items():
            s = by_key.get(key)
            if s is not None:
                row.apply(s, key in covered, force, stale)
        for sec in self._blocks():
            state = bulk_state_of(sec.keys, covered)
            sec.set_bulk(state, len(sec.keys), sum(1 for k in sec.keys if k in covered))
            if state == "all" and self._notices.get(sec.hw_id):
                self.set_bulk_notice(sec.hw_id, 0)      # nothing left to add

    def _apply_tab_order(self) -> None:
        """Tab order: theme button, bottom toolbar, then the cards / empty actions and rows (top to bottom)."""
        names = ("theme", "refresh", "fahrenheit", "detail", "settings", "quit")
        chain: list[QWidget] = [self._toolbar[n] for n in names]
        chain += [c.button for c in self.cards]
        chain += list(self.empty_buttons.values())
        for top in self._tops:
            members = [top] if top.hw_id in self.sections else [top, *self._members(top)]
            for sec in members:
                chain += [sec.chevron, sec.bulk] + ([sec.detail_btn] if sec.detail_btn is not None else [])
                if not sec.is_category:
                    chain += [r.eye for r in self.rows.values() if sec.isAncestorOf(r)]
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)

    def _on_section_toggled(self, hw_id: str, collapsed: bool) -> None:
        (self._collapsed.add if collapsed else self._collapsed.discard)(hw_id)
        self.collapsed_changed.emit(sorted(self._collapsed))

    def _on_bulk_clicked(self, hw_id: str) -> None:
        self.set_bulk_notice(hw_id, 0)
        self.bulk_pin_requested.emit(hw_id)
        self._update_in_place()         # the control follows the real pin state, not the click

    # ------------------------------------------------------------------ reordering (drag, keyboard, menu)
    def _members(self, box: Section) -> list[Section]:
        """Component sections inside the category header `box`."""
        ids = next((ids for cat, ids in self._tree if category_id(cat) == box.order_id), [])
        return [self.sections[h] for h in ids if h in self.sections]

    def _sibling_ids(self, order_id: str) -> list[str]:
        """Ids among which `order_id` is ordered: every category, or the components of its category."""
        if order_id.startswith("cat:"):
            return [category_id(c) for c, _ids in self._tree]
        for _cat, ids in self._tree:
            if order_id in ids:
                return list(ids)
        return []

    def can_move(self, order_id: str, delta: int) -> bool:
        ids = self._sibling_ids(order_id)
        return order_id in ids and 0 <= ids.index(order_id) + delta < len(ids)

    def move_item(self, order_id: str, delta: int, focus: bool = False) -> bool:
        """Keyboard / menu alternative to dragging: one step up (-1) or down (+1) among its siblings."""
        if not self.can_move(order_id, delta):
            return False
        ids = self._sibling_ids(order_id)
        return self.drop_item(order_id, ids.index(order_id) + delta, focus)

    def drop_item(self, order_id: str, index: int, focus: bool = False) -> bool:
        """Put `order_id` (a category or a component) at position `index` among its siblings, save the order
        (`hw_order_changed`) and redraw. False when nothing changes."""
        ids = self._sibling_ids(order_id)
        new = move_to(ids, order_id, index)
        if not ids or new == ids:
            return False
        if order_id.startswith("cat:"):
            by_cat = {category_id(c): (c, comps) for c, comps in self._tree}
            tree = [by_cat[i] for i in new]
        else:
            tree = [(c, new if order_id in comps else comps) for c, comps in self._tree]
        self._hw_order = flatten_order(tree, self._hw_order)
        self._sig = None
        self.set_snapshot(self._snapshot)
        self.hw_order_changed.emit(list(self._hw_order))
        if focus:
            self._focus_header(order_id)
        return True

    def _focus_header(self, order_id: str) -> None:
        sec = next((t for t in self._tops if t.order_id == order_id), None) or self.sections.get(order_id)
        if sec is not None:
            sec.chevron.setFocus(Qt.FocusReason.OtherFocusReason)

    def _sibling_blocks(self, item: Section) -> list[Section]:
        if item.order_id.startswith("cat:"):
            return list(self._tops)
        return [self.sections[h] for h in self._sibling_ids(item.order_id) if h in self.sections]

    def _drop_slot(self, item: Section, gpos: QPoint) -> int:
        """Final position of `item` among its siblings if dropped at `gpos` (global)."""
        content = self._scroll.widget()
        if content is None:
            return 0
        y = content.mapFromGlobal(gpos).y()
        slot = 0
        for w in self._sibling_blocks(item):
            if w is not item and w.mapTo(content, QPoint(0, 0)).y() + w.height() / 2 < y:
                slot += 1
        return slot

    def _drag_begin(self, item: Section) -> None:
        self._drag_item = item

    def _drag_update(self, item: Section, gpos: QPoint) -> None:
        """Show where `item` would land (a line between its siblings) and scroll near the edges."""
        content = self._scroll.widget()
        if content is None or self._indicator is None:
            return
        bar, vp = self._scroll.verticalScrollBar(), self._scroll.viewport()
        vy = vp.mapFromGlobal(gpos).y()
        if vy < self.px(24):
            bar.setValue(bar.value() - self.px(20))
        elif vy > vp.height() - self.px(24):
            bar.setValue(bar.value() + self.px(20))
        others = [w for w in self._sibling_blocks(item) if w is not item]
        if not others:
            self._indicator.hide()
            return
        slot = self._drop_slot(item, gpos)
        ref = others[min(slot, len(others) - 1)]
        top = ref.mapTo(content, QPoint(0, 0))
        y = top.y() if slot < len(others) else top.y() + ref.height()
        h = self._indicator.height()
        self._indicator.setGeometry(top.x(), max(0, y - h // 2 - 2), ref.width(), h)
        self._indicator.show()
        self._indicator.raise_()

    def _drag_end(self, item: Section, gpos: QPoint, commit: bool = True) -> None:
        slot = self._drop_slot(item, gpos) if commit else -1
        self._cancel_drag()
        if slot >= 0:
            self.drop_item(item.order_id, slot)
        self.set_snapshot(self._snapshot)       # a rebuild may have been deferred while dragging

    def _cancel_drag(self) -> None:
        item, self._drag_item = self._drag_item, None
        if self._indicator is not None:
            self._indicator.hide()
        if item is not None:
            try:
                item._head.cancel_drag()
            except RuntimeError:
                pass        # the header was deleted meanwhile

    def _request_detail(self, hw_id: str) -> None:
        """Ask for the detail window (opening it hides the panel, as the window takes the focus)."""
        self.detail_requested.emit(hw_id)
        self.hide()

    # ------------------------------------------------------------------ chrome
    def _build_topbar(self) -> QWidget:
        w = QWidget(self)
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 4, 2, 0)
        self._title = QLabel(i18n.tr("panel_title"))
        f = QFont(self._title.font())
        f.setBold(True)
        self._title.setFont(f)
        h.addWidget(self._title, 1)
        b = self._tool_button("themeBtn")
        b.clicked.connect(lambda _c=False: self.theme_toggle_requested.emit())
        self._toolbar["theme"] = b
        h.addWidget(b)
        return w

    def _tool_button(self, name: str = "") -> QToolButton:
        b = QToolButton(self)
        b.setFixedSize(BTN_PX, BTN_PX)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        b.setIconSize(QSize(BTN_ICON_PX, BTN_ICON_PX))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        if name:
            b.setObjectName(name)
        return b

    def _build_toolbar(self) -> QWidget:
        w = QWidget(self)
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(4)
        specs = (("refresh", False, lambda _c=False: self.refresh_requested.emit()),
                 ("fahrenheit", True, lambda c=False: self._on_fahrenheit(c)),
                 ("detail", False, lambda _c=False: self._request_detail("")),
                 ("settings", False, lambda _c=False: self._on_gear()),
                 ("quit", False, lambda _c=False: self.quit_requested.emit()))
        for name, checkable, slot in specs:
            b = self._tool_button(name + "Btn")
            b.setCheckable(checkable)
            (b.toggled if checkable else b.clicked).connect(slot)
            self._toolbar[name] = b
            if name == "quit":
                h.addStretch(1)
            h.addWidget(b)
        self._refresh_toolbar()
        return w

    def _on_fahrenheit(self, value: bool) -> None:
        self._fahrenheit = bool(value)
        self._refresh_toolbar()
        self._update_in_place()
        self.fahrenheit_toggled.emit(self._fahrenheit)

    def _refresh_toolbar(self) -> None:
        t, col, ic = self._toolbar, text_color(self._dark), self.px(BTN_ICON_PX)
        labels = {"refresh": i18n.tr("refresh"),
                  "fahrenheit": i18n.tr("celsius" if self._fahrenheit else "fahrenheit"),
                  "detail": i18n.tr("detail_btn"), "settings": i18n.tr("settings_btn"),
                  "quit": i18n.tr("quit"),
                  "theme": i18n.tr("theme_to_light" if self._dark else "theme_to_dark")}
        for name, b in t.items():
            b.setToolTip(labels[name])
            b.setAccessibleName(labels[name])
        t["refresh"].setIcon(refresh_icon(ic, col))
        t["fahrenheit"].setIcon(unit_icon(ic, self._fahrenheit, col))     # shows the active unit
        t["detail"].setIcon(list_icon(ic, col))
        t["settings"].setIcon(gear_icon(ic, col))
        t["quit"].setIcon(power_icon(ic, col))
        t["theme"].setIcon((moon_icon if self._dark else sun_icon)(ic, col))

    def _apply_style(self) -> None:
        dark = self._dark
        base = QColor(*TASKBAR_BASE[dark])
        text = text_color(dark)
        edge = QColor(70, 70, 70) if dark else QColor(190, 190, 190)
        hover = "rgba(255,255,255,40)" if dark else "rgba(0,0,0,30)"
        focus = band_color("amber", dark).name()
        checked = "rgba(255,255,255,70)" if dark else "rgba(0,0,0,55)"
        handle = "rgba(255,255,255,90)" if dark else "rgba(0,0,0,90)"
        self.setStyleSheet(
            f"QFrame#HardwarePanel {{ background: {_hex(base)}; border: 1px solid {_hex(edge)}; "
            f"border-radius: 6px; }}"
            f"QLabel {{ background: transparent; color: {_hex(text)}; }}"
            f"QToolButton {{ background: transparent; color: {_hex(text)}; border: 2px solid transparent; "
            f"border-radius: 5px; padding: 2px; }}"
            f"QToolButton:hover {{ background: {hover}; }}"
            f"QToolButton:focus {{ border: 2px solid {focus}; }}"
            f"QToolButton:checked {{ background: {checked}; }}"
            f"QToolButton#fahrenheitBtn:checked {{ background: transparent; }}"
            f"QToolButton#fahrenheitBtn:checked:hover {{ background: {hover}; }}"
            f"QToolButton#cardBtn {{ border: 1px solid {_hex(edge)}; padding: 3px 8px; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {handle}; min-height: 24px; "
            f"border-radius: 4px; margin: 1px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}"
            f"QMenu {{ background: {_hex(base)}; color: {_hex(text)}; border: 1px solid {_hex(edge)}; }}"
            f"QMenu::item {{ padding: 5px 18px; }}"
            f"QMenu::item:selected {{ background: {checked}; }}"
            f"QMenu::item:disabled {{ color: {_hex(band_color('grey', dark))}; }}"
            f"QToolTip {{ background: {_hex(base)}; color: {_hex(text)}; "
            f"border: 1px solid {_hex(edge)}; padding: 3px; }}")
        self._title.setStyleSheet(f"color: {_hex(section_header_color(dark))}; background: transparent;")
        self._refresh_toolbar()

    @staticmethod
    def _primary_avail() -> QRect:
        s = QGuiApplication.primaryScreen()
        return s.availableGeometry() if s is not None else QRect(0, 0, 1920, 1040)

    def _fit(self, avail: QRect) -> None:
        w, h = panel_dimensions(avail, self._size, self._scale)
        self.setFixedSize(w, h)

    # ------------------------------------------------------------------ click-outside trick
    def _foreground(self) -> None:
        self._posted_hwnd = int(self.winId())
        ww._foreground(self._posted_hwnd)

    def _app_active(self) -> bool:
        return QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive

    def _set_topmost(self, on: bool) -> None:
        try:
            hwnd = int(self.winId())
            (ww._assert_topmost if on else ww._drop_topmost)(hwnd)
        except Exception:  # noqa: BLE001
            log.debug("could not change the topmost state of the panel", exc_info=True)

    def _on_app_state_changed(self, state) -> None:
        if not self.isVisible():
            return
        if state == Qt.ApplicationState.ApplicationActive:
            self._armed = True
        elif self._armed and not dialogs.dialog_open():
            self.hidden_at = time.monotonic()
            QTimer.singleShot(HIDE_CHECK_MS, self._hide_if_inactive)

    # ---- application dialogs (see dialogs.py): the panel must survive a native dialog taking the focus
    def _on_dialog_opened(self) -> None:
        if self.isVisible():
            self._set_topmost(False)        # a native dialog is not a QWindow: lower the band explicitly

    def _on_dialog_closed(self) -> None:
        if self.isVisible():
            QTimer.singleShot(HIDE_CHECK_MS, self._after_dialog)

    def _after_dialog(self) -> None:
        if not self.isVisible() or dialogs.dialog_open():
            return
        if self._app_active():
            self._armed = True
            self._set_topmost(True)
            self.activateWindow()
        else:
            self.hidden_at = time.monotonic()
            self.hide()

    def _on_focus_window_changed(self, w=None) -> None:
        if not self.isVisible():
            return
        if self._app_active():
            self._armed = True
            if w is not None:
                if w is self.windowHandle():
                    self._set_topmost(True)
                elif (w.flags() & Qt.WindowType.WindowType_Mask) != Qt.WindowType.Popup:
                    self._set_topmost(False)    # another window of the app: let it be drawn above the panel
        elif self._armed and not dialogs.dialog_open():
            self.hidden_at = time.monotonic()
            QTimer.singleShot(HIDE_CHECK_MS, self._hide_if_inactive)

    def _arm_fallback(self, gen: int, stage: int) -> None:
        """If SetForegroundWindow was denied the activation never arrives: retry, then arm anyway."""
        if gen != self._show_gen or not self.isVisible() or self._armed:
            return
        if stage == 0:
            self.activateWindow()
            self._foreground()
            QTimer.singleShot(ARM_FALLBACK_MS, lambda: self._arm_fallback(gen, 1))
            return
        self._armed = True
        if not self._app_active() and not dialogs.dialog_open():
            self.hidden_at = time.monotonic()
            self.hide()

    def _hide_if_inactive(self) -> None:
        if self.isVisible() and self._armed and not self._app_active() and not dialogs.dialog_open():
            self.hide()

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._armed = True
            if self.isVisible():
                self._set_topmost(True)

    def showEvent(self, ev) -> None:
        self._armed = self._app_active()
        super().showEvent(ev)

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._cancel_drag()
        self.hidden_at = time.monotonic()
        if self.in_settings:        # the next opening shows the summary again (unless Settings is asked for)
            self.show_summary()
        hwnd, self._posted_hwnd = self._posted_hwnd, 0
        if hwnd:
            ww._post_null(hwnd)

    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        if self.in_settings and (key in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace, Qt.Key.Key_Back)
                                 or (key == Qt.Key.Key_Left and ev.modifiers() & Qt.KeyboardModifier.AltModifier)):
            self._settings_view.go_back()       # one level up; from the root page: the summary
            return
        if key == Qt.Key.Key_Escape:
            if self._drag_item is not None:     # Esc cancels a drag first
                self._cancel_drag()
                return
            self.close()
            return
        super().keyPressEvent(ev)
