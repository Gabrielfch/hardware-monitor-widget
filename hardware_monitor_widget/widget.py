"""Frameless, always-on-top pill bar docked to the taskbar (right or left end) or floating on a monitor.

Layout, painting, docking and placement adapted from Taskbar Battery Widget (MIT): one rounded pill per
pinned sensor (hardware icon + value + thin level bar), band-coloured, red outline when critical; by
default the sensors of one hardware share a single pill (one icon, several values: "grouped"). When
the pills do not fit in the free part of the taskbar they go compact (no bar, less padding) and then the
rest collapse into a "+K" pill.

The widget does not know the config (ADR-7): the controller pushes `PlacementSettings`, the UI scale and
an icon resolver, and persists whatever the widget reports through its signals.
"""
from __future__ import annotations

import html
import math
import os
import sys  # noqa: F401  (kept so tests can read widget.sys.platform)
import time
from dataclasses import dataclass, field, replace

import shiboken6
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from . import custom_icons, taskbar
from . import win_window as ww
from .colors import TASKBAR_BASE, band_color, pill_tint, text_color
from .config import FLOATING_ANCHORS, PILL_STYLES
from .curation import hw_class
from .i18n import decimal_sep, tr
from .icons import draw_kind_icon, icons_visible
from .models import Sensor, Snapshot, band_for, format_value, is_stale, source_reason
from .curation import order_sensors
from .pins import resolve_pins
from .sensor_names import display_name
from .placement import (DOCK_GAP, MAX_PILL_H, MAX_WIDTH_FRACTION, MIN_PILL_H,  # noqa: F401  (re-exported)
                        REASON_LEFT_NEEDS_CENTERED, REASON_NO_TASKBAR_FLOATING, Env, Placement,
                        PlacementSettings, ScreenInfo, choose_placement, compute_placement, docked_edge_left,
                        left_margin_phys, lookup_floating, max_pill_extent, pill_height_for, resolve_screen,
                        snap_anchor, taskbar_for_screen)

MARGIN = 2           # outer margin around the pills (logical px)
SPACING = 4          # gap between pills
PAD = 8              # horizontal padding inside a pill
PAD_COMPACT = 5      # padding in compact mode
ICON_GAP = 5         # gap between icon and text
ICON_GAP_COMPACT = 4
SEG_GAP = 9          # gap between the values of a grouped pill
SEG_GAP_COMPACT = 6
MIN_FONT_PX = 12
BAR_MIN_PILL_H = 24  # below this height the level bar is dropped
BAR_H = 3.0
TICK_MS = 500
SHIFT_POLL_MS = 100           # Shift polling while shown over a fullscreen app
DRAG_THRESHOLD = 4            # px of movement before a press in floating mode becomes a drag
POSITION_INTERVAL_S = 1.0     # the costly part of update_position runs at most once per second
REG_CACHE_TTL = 5.0           # s: Explorer\Advanced values (icon alignment, Widgets button) change rarely
FLOATING_BG_ALPHA = 245
FLOATING_BORDER_ALPHA = 60
DIM_ALPHA = 170               # stale values
UI_SCALE_MIN, UI_SCALE_MAX = 90, 130
EMPTY_KEY = "__empty__"
OVERFLOW_KEY = "__overflow__"
GROUP_PREFIX = "group:"
NEWLINE = chr(10)
BAND_RANK = {"grey": 0, "green": 1, "amber": 2, "orange": 3, "red": 4}
DEFAULT_PILL_H = 28
PAD_TIGHT = 3        # "compact" style: minimal horizontal padding
SOFT_RADIUS = 4.0    # soft style / hover corner radius
SOFT_ALPHA = 0.55    # soft style: fraction of the classic fill opacity
SEPARATOR_ALPHA = 70
# short name of a hardware class, shown instead of its icon when pill labels are on
HW_LABELS = {"cpu": "CPU", "gpu": "GPU", "ram": "RAM", "vram": "VMEM", "dimm": "RAM", "board": "MB",
             "storage": "DISK", "network": "NET"}


def pill_label(hw_id: str, hw_kind: str) -> str:
    """Abbreviation of a hardware (CPU, GPU, RAM, VMEM, DISK, NET, MB, ...)."""
    return HW_LABELS.get(hw_class(hw_id, hw_kind), "HW")


# --------------------------------------------------------------------------- screens
def _device_names(screens: list) -> list:
    """Windows device name (``\\\\.\\DISPLAYn``) of each QScreen, "" when unknown.

    QScreen.name() is a friendly name on recent Qt versions, so the screen is matched to its monitor by
    its native top-left corner (Qt does not scale screen positions); ties are broken by size.
    """
    devs = taskbar.monitor_devices()
    out = []
    for sc in screens:
        if not shiboken6.isValid(sc):       # destroyed meanwhile (unplug, RDP, headless CI)
            out.append("")
            continue
        g, dpr = sc.geometry(), sc.devicePixelRatio() or 1.0
        cands = [d for d in devs if d[1][:2] == (g.x(), g.y())]
        if len(cands) > 1:
            size = (round(g.width() * dpr), round(g.height() * dpr))
            cands = [d for d in cands if (d[1][2] - d[1][0], d[1][3] - d[1][1]) == size] or cands
        out.append(cands[0][0] if cands else "")
    return out


def screen_keys() -> list:
    """[(monitor_key, QScreen)] for every Qt screen, in QGuiApplication.screens() order.

    Keys come from taskbar.monitor_key(); duplicates (identical monitors) get "#n" suffixes assigned in
    a name-sorted order so they do not swap when Qt reorders the list.
    """
    screens = [sc for sc in QGuiApplication.screens() if shiboken6.isValid(sc)]
    devs = _device_names(screens)
    taken: list[str] = []
    keys: dict[int, str] = {}
    for idx in sorted(range(len(screens)), key=lambda i: devs[i] or screens[i].name()):
        sc = screens[idx]
        mf, md, sn = sc.manufacturer(), sc.model(), sc.serialNumber()
        # Qt gave nothing to identify the panel: use the Windows interface path (hashed) rather than DISPLAYn
        sid = "" if (mf or md or sn) else taskbar.monitor_stable_id(devs[idx])
        k = taskbar.monitor_key(mf, md, sn, sc.name(), taken, sid)
        taken.append(k)
        keys[idx] = k
    return [(keys[i], sc) for i, sc in enumerate(screens)]


def _rect_t(r: QRect) -> tuple:
    return (r.x(), r.y(), r.width(), r.height())


def screen_infos() -> list:
    """[(ScreenInfo, QScreen)] with logical geometry, primary flagged; name = Windows device name."""
    for _attempt in range(2):           # a screen can be destroyed while we read it: retry once
        try:
            primary = QGuiApplication.primaryScreen()
            pairs = [(k, sc) for k, sc in screen_keys() if shiboken6.isValid(sc)]
            devs = _device_names([sc for _, sc in pairs])
            return [(ScreenInfo(k, devs[i] or sc.name(), _rect_t(sc.geometry()),
                                _rect_t(sc.availableGeometry()), sc.devicePixelRatio() or 1.0,
                                sc == primary), sc)
                    for i, (k, sc) in enumerate(pairs)]
        except RuntimeError:
            continue
    return []


# --------------------------------------------------------------------------- pure layout
def pill_width(text_w: int, icon: int, extra: int = 0, pad: int = PAD, gap: int = ICON_GAP) -> int:
    return pad + icon + gap + text_w + extra + pad


def layout_pills(widths: list[int], pill_h: int, vertical: bool = False,
                 spacing: int = SPACING, margin: int = MARGIN, heights: list[int] | None = None):
    """Return ([(x, y, w, h), ...], (total_w, total_h)) for pills in a row (or a column).

    `heights` (column only) gives each pill its own height, e.g. a grouped pill with one line per value."""
    if not widths:
        return [], (0, 0)
    rects = []
    if vertical:
        w = max(widths)
        y = margin
        for i, _ in enumerate(widths):
            h = heights[i] if heights else pill_h
            rects.append((margin, y, w, h))
            y += h + spacing
        return rects, (w + 2 * margin, y - spacing + margin)
    x = margin
    for w in widths:
        rects.append((x, margin, w, pill_h))
        x += w + spacing
    return rects, (x - spacing + margin, pill_h + 2 * margin)


def total_extent(extents: list[int], spacing: int = SPACING, margin: int = MARGIN) -> int:
    return 2 * margin + sum(extents) + spacing * max(0, len(extents) - 1) if extents else 0


def fit_count(extents: list[int], limit: int | None, overflow_extent: int,
              spacing: int = SPACING, margin: int = MARGIN) -> int:
    """How many leading pills fit in `limit` px along the main axis (the rest go to a "+K" pill).

    Returns len(extents) when everything fits (or limit is None); otherwise at least 1.
    """
    n = len(extents)
    if limit is None or n <= 1:
        return n

    def total(k: int, extra: bool) -> int:
        return total_extent(extents[:k] + ([overflow_extent] if extra else []), spacing, margin)

    if total(n, False) <= limit:
        return n
    for k in range(n - 1, 0, -1):
        if total(k, True) <= limit:
            return k
    return 1


def fit_pills(full: list[int], compact: list[int], limit: int | None, overflow_extent: int,
              spacing: int = SPACING, margin: int = MARGIN) -> tuple[bool, int]:
    """(compact mode?, number of pills shown): normal layout if it fits, else compact, else compact + "+K"."""
    n = len(full)
    if limit is None or total_extent(full, spacing, margin) <= limit:
        return False, n
    if total_extent(compact, spacing, margin) <= limit:
        return True, n
    return True, fit_count(compact, limit, overflow_extent, spacing, margin)


def pill_index_at(rects: list[tuple], x: float, y: float) -> int:
    for i, (rx, ry, rw, rh) in enumerate(rects):
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return i
    return -1


def pill_fraction(sensor: Sensor) -> float | None:
    """Fill of the pill bar (0..1): temperature over its limit (default 100), percentages over 100; others none."""
    v = sensor.value
    if v is None:
        return None
    if sensor.kind == "temperature":
        lim = sensor.limit if sensor.limit and sensor.limit > 0 else 100.0
    elif sensor.kind in ("load", "level"):
        lim = 100.0
    else:
        return None
    return max(0.0, min(1.0, v / lim))


def normalize_ui_scale(pct) -> int:
    """UI scale in percent: an int clamped to 90..130; anything else counts as 100."""
    if isinstance(pct, bool) or not isinstance(pct, int):
        return 100
    return max(UI_SCALE_MIN, min(UI_SCALE_MAX, pct))


def scaled_pill_h(base: int, pct: int, bar_thickness: float | None = None) -> int:
    """Pill height for the UI scale `pct`. On a taskbar it never exceeds the bar thickness minus the outer
    margins (so at 130 % a small bar limits the pills: they simply stop growing), and never goes below
    what the placement itself asked for because of that limit."""
    h = round(base * pct / 100)
    if bar_thickness is not None and pct > 100:
        h = min(h, max(base, int(bar_thickness) - 2 * MARGIN))
    return max(1, h)


def tooltip_html(text: str) -> str:
    """Plain tooltip text -> escaped rich text, so sensor names are never parsed as markup."""
    body = html.escape(text, quote=True).replace("\n", "<br>")
    return f"<p style='white-space:pre'>{body}</p>"


def reserve_text(kind: str, fahrenheit: bool) -> str:
    """Widest usual text of a sensor kind, so a pill keeps its width while digits change."""
    if kind == "temperature":
        return "212°F" if fahrenheit else "100°C"
    if kind in ("load", "level", "control", "humidity"):
        return "100%"
    return ""


def empty_tooltip(pinned: list[str], snapshot: Snapshot) -> str:
    """Why the placeholder pill has no value."""
    if not pinned:
        return tr("pill_no_pins")
    if source_reason(snapshot) == "needs_admin":
        return tr("pill_no_data_admin")
    if not snapshot.sensors and not snapshot.statuses and not snapshot.taken_at:
        return tr("pill_loading")
    return tr("pill_no_data")


def warning_text(reason: str) -> str:
    """User-facing text of a `placement_warning` reason ("" when there is none)."""
    key = f"pos_warn_{reason}"
    text = tr(key)
    return "" if text == key else text


@dataclass
class Pill:
    key: str
    hw_kind: str
    kind: str
    text: str
    tooltip: str
    band: str = "grey"
    frac: float | None = None
    alert: bool = False
    a11y: str = ""
    value: float | None = None
    stale: bool = False
    reserve: str = ""           # text whose width is the minimum width of the value
    overflow: bool = False
    hw_id: str = ""
    hardware: str = ""
    line: str = ""              # one tooltip line (translated name, value, min / max) when part of a group
    brief: str = ""             # accessible "name value" when part of a group
    segments: list["Pill"] = field(default_factory=list)   # grouped pill: one entry per value
    label: str = ""             # abbreviation of the hardware (CPU, GPU, ...)

    @property
    def has_value(self) -> bool:
        if self.segments:
            return any(s.has_value for s in self.segments)
        return self.value is not None


def build_pill(sensor: Sensor, fahrenheit: bool = False, reason: str = "ok", sep: str | None = None,
               stale: bool = False) -> Pill:
    sep = decimal_sep() if sep is None else sep
    plain = format_value(sensor.kind, sensor.value, fahrenheit, sep)
    band = band_for(sensor.kind, sensor.value, sensor.key)
    alert = band == "red"
    text = ("! " + plain) if alert else plain
    lines = [sensor.hardware, f"{sensor.name}: {plain}"]
    line = f"{display_name(sensor)}: {plain}"
    if sensor.min is not None or sensor.max is not None:
        line += " · " + tr("min_max", min=format_value(sensor.kind, sensor.min, fahrenheit, sep),
                           max=format_value(sensor.kind, sensor.max, fahrenheit, sep))
    if sensor.value is None:
        line += " · " + (tr("pill_no_data_admin") if reason == "needs_admin" else tr("no_data"))
    if sensor.min is not None or sensor.max is not None:
        lines.append(tr("min_max", min=format_value(sensor.kind, sensor.min, fahrenheit, sep),
                        max=format_value(sensor.kind, sensor.max, fahrenheit, sep)))
    if sensor.value is None:
        lines.append(tr("pill_no_data_admin") if reason == "needs_admin" else tr("no_data"))
    if stale:
        lines.insert(0, tr("stale_tip"))
    a11y = f"{sensor.hardware} {sensor.name} {plain}" + (", " + tr("critical") if alert else "")
    brief = f"{display_name(sensor)} {plain}" + (", " + tr("critical") if alert else "")
    return Pill(sensor.key, sensor.hw_kind, sensor.kind, text, "\n".join(lines), band,
                pill_fraction(sensor), alert, a11y, sensor.value, stale, reserve_text(sensor.kind, fahrenheit),
                hw_id=sensor.hw_id, hardware=sensor.hardware, line=line, brief=brief,
                label=pill_label(sensor.hw_id, sensor.hw_kind))


def build_group(segs: list[Pill]) -> Pill:
    """One pill for several sensors of the same hardware: a single icon and one value per sensor."""
    first = segs[0]
    stale = any(s.stale for s in segs)
    lines = ([tr("stale_tip")] if stale else []) + [first.hardware] + [s.line for s in segs]
    band = max((s.band for s in segs if s.has_value), key=lambda b: BAND_RANK.get(b, 0), default="grey")
    a11y = f"{first.hardware}: " + ", ".join(s.brief for s in segs)
    return Pill(GROUP_PREFIX + (first.hw_id or first.hw_kind), first.hw_kind, "", " ".join(s.text for s in segs),
                NEWLINE.join(lines), band, None, any(s.alert for s in segs), a11y,
                next((s.value for s in segs if s.value is not None), None), stale, "",
                hw_id=first.hw_id, hardware=first.hardware, segments=list(segs), label=first.label)


def group_by_hardware(pills: list[Pill]) -> list[Pill]:
    """Group pills by hardware, in order of first appearance (the caller passes them in display order, so
    the groups follow the panel and the values of a group keep their stable component order)."""
    order: list[str] = []
    groups: dict[str, list[Pill]] = {}
    for p in pills:
        k = p.hw_id or p.hw_kind
        if k not in groups:
            order.append(k)
            groups[k] = []
        groups[k].append(p)
    return [build_group(groups[k]) for k in order]


class TaskbarWidget(QWidget):
    """Public API for the controller (T8):

    Slots/methods: set_snapshot, set_pinned, set_hw_order, set_fahrenheit, set_theme, set_light_theme,
    set_placement_settings(PlacementSettings), set_ui_scale(pct), set_icon_resolver(fn),
    set_floating_anchor(anchor), refresh_style(), start(), stop(), update_position(force=True),
    current_screen_key(), list_screens(), monitor_has_taskbar(), icons_centered().
    Signals: clicked, context_menu_requested(QPoint), floating_moved(key, entry, fullscreen),
    monitor_changed(key), fullscreen_placement_changed(value), placement_warning(reason).
    """

    clicked = Signal()                          # a pill was left-clicked (not dragged)
    context_menu_requested = Signal(QPoint)     # right click: the controller opens the panel
    floating_moved = Signal(str, dict, bool)    # monitor key, {anchor, dx, dy}, dropped over a fullscreen app
    monitor_changed = Signal(str)               # a drop on another monitor changed the monitor choice
    fullscreen_placement_changed = Signal(str)  # a drop over a fullscreen app switched it to "floating"
    placement_warning = Signal(str)             # e.g. "left_needs_centered_icons": the left anchor fell back

    def __init__(self, pinned: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._pinned = list(pinned or [])
        self._snapshot = Snapshot()
        self._fahrenheit = False
        self._theme = "auto"
        self._light = taskbar.taskbar_uses_light_theme()
        self._ps = PlacementSettings()
        self._ui_pct = 100
        self._icon_resolver = None          # fn(hw_kind, hw_id, sensor_key) -> file name | None
        self._hw_order: list[str] = []      # panel order (config hw_order): categories and components
        self._grouped = True                # one pill per hardware (several values) instead of one per sensor
        self._style = "transparent"         # classic | soft | transparent | compact
        self._labels = False                # abbreviations instead of icons
        self._bars = True                   # level bars under the values (never in "compact")
        self._stack = False                 # floating: one component per row, values in columns
        self._stack_lead = 0                # stacked: width of the (common) icon / label column
        self._stack_cols: list[int] = []    # stacked: width of each value column
        self._hidden: list[Pill] = []       # sensor pills collapsed into the "+K" pill
        self._all: list[Pill] = []          # every pill (before overflow)
        self._pills: list[Pill] = []        # pills drawn (possibly ending with the "+K" pill)
        self._rects: list[tuple] = []
        self._hover = -1
        self._base_h = DEFAULT_PILL_H       # pill height the placement asked for (before the UI scale)
        self._thickness: float | None = None    # logical taskbar thickness (None: floating)
        self._pill_h = DEFAULT_PILL_H
        self._pill_scale = 1.0              # effective pill height / base height
        self._vertical = False
        self._floating = False              # last applied placement was floating (draws a backdrop, drags)
        self._started = False
        self._styled = False
        self._last_geom = None
        self._stack_limit: int | None = None  # stacked: max height (logical px) = the screen's available area
        self._limit: int | None = None      # max extent along the bar (logical px); None = unlimited
        self._compact = False
        self._last_pos = 0.0
        self._nb_cache: tuple | None = None
        self._reg_cache: dict = {}
        self._screens_cache: list | None = None     # [(ScreenInfo, QScreen)], dropped on screen signals
        self._watched: list = []
        self._n_screens = 1
        self._cur_screen: ScreenInfo | None = None
        self._cur_qscreen = None
        self._last_reason = "ok"
        self._press_global: QPoint | None = None
        self._press_origin = QPoint()
        self._dragging = False
        self._press_idx = -1
        self._fs_override = False           # fullscreen app on this monitor: the "in fullscreen" spot is in use
        self._passive = False               # shown over a fullscreen app: click-through unless Shift is held
        self._shift_held = False
        self.setAccessibleName(tr("app_name"))
        self._zguard = ww._ZGuard(lambda: int(self.winId()) if self.isVisible() else 0)
        self.destroyed.connect(self._zguard.stop)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._shift_timer = QTimer(self)
        self._shift_timer.setInterval(SHIFT_POLL_MS)
        self._shift_timer.timeout.connect(self._shift_tick)
        self._rebuild()
        self._connect_screen_signals()

    # ------------------------------------------------------------------ public API
    def set_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        before = self.size()
        self._rebuild()
        if self._started:
            self.update_position(force=self.size() != before)

    def set_pinned(self, pinned: list[str]) -> None:
        self._pinned = list(pinned)
        self._rebuild()
        if self._started:
            self.update_position()

    def set_hw_order(self, order: list[str]) -> None:
        """Order of categories and components (the panel's): pills follow it, not the order of pinning."""
        order = [x for x in (order or ()) if isinstance(x, str)]
        if order != self._hw_order:
            self._hw_order = order
            self._rebuild()
            if self._started:
                self.update_position()

    def set_grouped(self, value: bool) -> None:
        """One pill per hardware (True) or one pill per pinned sensor (False)."""
        if bool(value) != self._grouped:
            self._grouped = bool(value)
            self._rebuild()
            if self._started:
                self.update_position()

    def is_grouped(self) -> bool:
        return self._grouped

    def _refit(self) -> None:
        self._rebuild()
        self._last_geom = None
        if self._started:
            self.update_position()

    def set_pill_style(self, style: str) -> None:
        """classic | soft | transparent | compact; anything else is ignored."""
        if style in PILL_STYLES and style != self._style:
            self._style = style
            self._refit()

    def pill_style(self) -> str:
        return self._style

    def set_pill_labels(self, value: bool) -> None:
        if bool(value) != self._labels:
            self._labels = bool(value)
            self._refit()

    def pill_labels(self) -> bool:
        return self._labels

    def set_pill_bars(self, value: bool) -> None:
        if bool(value) != self._bars:
            self._bars = bool(value)
            self.update()

    def pill_bars(self) -> bool:
        return self._bars and self._style != "compact"

    def set_stacked(self, value: bool) -> None:
        if bool(value) != self._stack:
            self._stack = bool(value)
            self._refit()

    def is_stacked(self) -> bool:
        """Stacking is on and applies now (floating bar; a horizontal or vertical taskbar never stacks)."""
        return self._stack and self._floating and not self._vertical

    def set_fahrenheit(self, value: bool) -> None:
        if bool(value) != self._fahrenheit:
            self._fahrenheit = bool(value)
            self._rebuild()

    def set_theme(self, theme: str) -> None:
        """Config theme (auto | light | dark); only affects a floating bar (a docked one follows the taskbar)."""
        if theme != self._theme:
            self._theme = theme
            self.update()

    def set_light_theme(self, light: bool) -> None:
        if light != self._light:
            self._light = light
            self.update()

    def set_menu(self, _menu) -> None:
        """Kept for compatibility: the widget no longer opens a menu (right click -> context_menu_requested)."""

    def set_stack_limit(self, limit: int | None) -> None:
        """Max height (logical px) of the stacked bar; rows that do not fit go to a final "+N" pill."""
        limit = None if limit is None else max(0, int(limit))
        if limit != self._stack_limit:
            self._stack_limit = limit
            if self.is_stacked():
                self._relayout()

    def set_extent_limit(self, limit: int | None) -> None:
        """Max length (logical px) of the bar along the taskbar; pills compact and overflow to "+K"."""
        limit = None if limit is None else max(0, int(limit))
        if limit != self._limit:
            self._limit = limit
            self._relayout()

    def set_placement_settings(self, settings: PlacementSettings) -> None:
        """New placement choices (copied: the widget never mutates the caller's object)."""
        self._ps = replace(settings,
                           floating_positions=dict(settings.floating_positions or {}),
                           fullscreen_positions=dict(settings.fullscreen_positions or {}))
        self._last_geom = None
        self._last_reason = "ok"
        if self._started:
            self.update_position()

    def placement_settings(self) -> PlacementSettings:
        return replace(self._ps, floating_positions=dict(self._ps.floating_positions),
                       fullscreen_positions=dict(self._ps.fullscreen_positions))

    def set_ui_scale(self, pct: int) -> None:
        """Interface scale in percent (90..130): pill height, font, icons and paddings follow it."""
        pct = normalize_ui_scale(pct)
        if pct == self._ui_pct:
            return
        self._ui_pct = pct
        self._apply_metrics()
        self._relayout()
        self._last_geom = None
        if self._started:
            self.update_position()

    def ui_scale(self) -> int:
        return self._ui_pct

    def set_icon_resolver(self, fn) -> None:
        """`fn(hw_kind, hw_id, sensor_key) -> file name | None` picks a custom icon (None: the built-in one)."""
        self._icon_resolver = fn
        self.refresh_style()

    def refresh_style(self) -> None:
        """Re-measure and repaint after the icon style, palette or custom icons changed."""
        self._relayout()
        if self._started:
            self.update_position()

    def start(self) -> None:
        self._started = True
        self._timer.start()
        self._zguard.start()
        self.update_position()

    def stop(self) -> None:
        self._started = False
        self._timer.stop()
        self._zguard.stop()
        self._set_passive(False)
        self.hide()

    def pill_texts(self) -> list[str]:
        return [p.text for p in self._pills]

    def pill_bands(self) -> list[str]:
        return [p.band for p in self._pills]

    def pill_keys(self) -> list[str]:
        return [p.key for p in self._pills]

    def pills(self) -> list[Pill]:
        return list(self._pills)

    def hidden_pills(self) -> list[Pill]:
        """Sensor pills collapsed into the "+K" pill."""
        return list(self._hidden)

    def is_compact(self) -> bool:
        return self._compact

    def pill_height(self) -> int:
        return self._pill_h

    def pill_dark(self) -> bool:
        """Dark look: pills sit on the taskbar so they follow it; a floating bar follows the app theme."""
        if self._floating and self._theme in ("light", "dark"):
            return self._theme == "dark"
        return not self._light

    def current_screen_key(self) -> str | None:
        """Key of the monitor the widget currently resolves to (None before the first placement)."""
        return self._cur_screen.key if self._cur_screen is not None else None

    def list_screens(self) -> list[tuple[str, str]]:
        """[(monitor key, label)] for the monitor choice; a monitor without a taskbar says so."""
        pairs = self._screens()
        infos = taskbar.get_taskbar_infos()
        names = {i.name for i, _ in pairs}
        out = []
        for n, (info, _q) in enumerate(pairs, 1):
            label = tr("pos_monitor_primary") if info.primary else tr("pos_monitor_label", n=n)
            if taskbar_for_screen(info, infos, names) is None:
                label += tr("pos_monitor_no_taskbar")
            out.append((info.key, label))
        return out

    def monitor_has_taskbar(self) -> bool:
        """Does the monitor the widget resolves to have a taskbar (True when unknown)?"""
        pairs = self._screens()
        sinfo = self._cur_screen or resolve_screen(self._ps.monitor, [p[0] for p in pairs])
        if sinfo is None:
            return True
        return taskbar_for_screen(sinfo, taskbar.get_taskbar_infos(), {i.name for i, _ in pairs}) is not None

    def icons_centered(self) -> bool | None:
        return self._reg(taskbar.taskbar_icons_centered)

    def set_floating_anchor(self, anchor: str) -> None:
        """Keyboard alternative to dragging: park the bar on `anchor` of the current monitor (no offset)."""
        if anchor not in FLOATING_ANCHORS:
            return
        if self._cur_screen is None:
            self.update_position()
        key = self.current_screen_key()
        if key is None:
            return
        self._save_floating(key, {"anchor": anchor, "dx": 0, "dy": 0})

    # ------------------------------------------------------------------ model -> pills
    def _rebuild(self) -> None:
        sensors = resolve_pins(self._pinned, self._snapshot)
        if len(sensors) > 1:       # display order, not pin order: toggling a pin never reorders the others
            sensors = order_sensors(sensors, self._snapshot, self._hw_order)
        reason = source_reason(self._snapshot)
        stale = is_stale(self._snapshot)
        sep = decimal_sep()
        pills = [build_pill(s, self._fahrenheit, reason, sep, stale) for s in sensors]
        if pills and self._grouped:
            pills = group_by_hardware(pills)
        if not pills:
            tip = empty_tooltip(self._pinned, self._snapshot)
            pills = [Pill(EMPTY_KEY, "cpu", "", "—", tip, a11y=tip, stale=stale)]
        self._all = pills
        desc = " · ".join(p.a11y for p in pills)
        self.setAccessibleDescription(desc + (" · " + tr("stale_tip") if stale else ""))
        self._relayout()

    def _apply_metrics(self) -> bool:
        """Effective pill height / scale from the placement's base height and the UI scale; True if changed."""
        h = scaled_pill_h(self._base_h, self._ui_pct, self._thickness)
        scale = h / self._base_h if self._base_h else 1.0
        changed = h != self._pill_h or abs(scale - self._pill_scale) > 1e-6
        self._pill_h, self._pill_scale = h, scale
        return changed

    def _spacing(self) -> int:
        column = self._vertical or self.is_stacked()
        return {"classic": SPACING, "soft": SPACING, "transparent": 2 if column else 7,
                "compact": 1 if column else 3}[self._style]

    def _pad(self, compact: bool) -> int:
        if self._style == "compact":
            return max(2, round(PAD_TIGHT * self._pill_scale))
        return max(3 if compact else 4, round((PAD_COMPACT if compact else PAD) * self._pill_scale))

    def _gap(self, compact: bool) -> int:
        return max(3, round((ICON_GAP_COMPACT if compact else ICON_GAP) * self._pill_scale))

    def _font(self) -> QFont:
        f = QFont("Segoe UI")
        f.setPixelSize(max(MIN_FONT_PX, round(self._pill_h * 0.45)))
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def _label_font(self) -> QFont:
        f = QFont("Segoe UI")
        f.setPixelSize(max(9, round(self._pill_h * 0.36)))
        f.setWeight(QFont.Weight.Bold)
        return f

    def _lead_label(self, pill: Pill) -> str:
        return pill.label if self._labels and not pill.overflow and pill.key != EMPTY_KEY else ""

    def _lead_w(self, p: Pill, fl: QFontMetrics) -> int:
        """Width of the icon / abbreviation column of a pill (0: none)."""
        if p.overflow:
            return 0
        label = self._lead_label(p)
        if label:
            return fl.horizontalAdvance(label)
        return self._icon_size() if self._has_icon(p) else 0

    def _stack_metrics(self, fm: QFontMetrics) -> None:
        """Stacked layout: common icon column and per-index value columns, so every row lines up."""
        fl = QFontMetrics(self._label_font())
        self._stack_lead = max((self._lead_w(p, fl) for p in self._all), default=0)
        cols: list[int] = []
        for p in self._all:
            ws = self._seg_widths(p, fm) if p.segments else [max(fm.horizontalAdvance(p.text),
                                                                  fm.horizontalAdvance(p.reserve))]
            for i, w in enumerate(ws):
                if i < len(cols):
                    cols[i] = max(cols[i], w)
                else:
                    cols.append(w)
        self._stack_cols = cols

    def _icon_size(self) -> int:
        return max(12, round(self._pill_h * 0.58))

    def _custom_file(self, pill: Pill) -> str | None:
        """User image for this pill (sensor > hardware > kind) if one is set and loads; else None."""
        if pill.overflow or pill.key == EMPTY_KEY:
            return None
        fn = self._icon_resolver or custom_icons.file_for
        try:
            name = fn(pill.hw_kind, pill.hw_id, pill.key)
        except Exception:
            return None
        if not name or not custom_icons.usable(name):
            return None
        return name

    def _has_icon(self, pill: Pill) -> bool:
        """False for style "none" without a custom image: no icon and no gap."""
        return icons_visible() or self._custom_file(pill) is not None

    def _seg_gap(self, compact: bool) -> int:
        return max(5, round((SEG_GAP_COMPACT if compact else SEG_GAP) * self._pill_scale))

    @staticmethod
    def _seg_widths(p: Pill, fm: QFontMetrics) -> list[int]:
        """Width of each value of a grouped pill (stable: never below the widest usual text of its kind)."""
        return [max(fm.horizontalAdvance(s.text), fm.horizontalAdvance(s.reserve)) for s in p.segments]

    def _pill_w(self, p: Pill, fm: QFontMetrics, compact: bool) -> int:
        pad = self._pad(compact)
        lead = None
        if self.is_stacked() and not p.overflow:
            cols = self._stack_cols
            text_w = sum(cols) + self._seg_gap(compact) * max(0, len(cols) - 1)
            lead = self._stack_lead
        elif p.segments:
            widths = self._seg_widths(p, fm)
            # a column puts one value per line (the widest one sets the width); a row puts them side by side
            text_w = max(widths) if self._vertical else sum(widths) + self._seg_gap(compact) * (len(widths) - 1)
        else:
            text_w = max(fm.horizontalAdvance(p.text), fm.horizontalAdvance(p.reserve))
        if p.overflow:
            return 2 * pad + text_w
        if lead is None:
            lead = self._lead_w(p, QFontMetrics(self._label_font()))
        if not lead:
            return pill_width(text_w, 0, 0, pad, 0)
        return pill_width(text_w, lead, 0, pad, self._gap(compact))

    def _pill_ext(self, p: Pill) -> int:
        """Height of a pill in a column: a grouped pill has one line per value."""
        return self._pill_h * max(1, len(p.segments)) if self._vertical else self._pill_h

    def _overflow_pill(self, hidden: list[Pill]) -> Pill:
        lines = [f"{p.a11y}" for p in hidden]
        tip = tr("pill_more", n=len(hidden)) + NEWLINE + NEWLINE.join(lines)
        return Pill(OVERFLOW_KEY, "", "", f"+{len(hidden)}", tip, a11y=tip, overflow=True,
                    stale=any(p.stale for p in hidden))

    def _relayout(self) -> None:
        fm = QFontMetrics(self._font())
        allp = self._all
        stacked = self.is_stacked()
        spacing = self._spacing()
        if stacked:
            self._stack_metrics(fm)
        full = [self._pill_w(p, fm, False) for p in allp]
        compact = [self._pill_w(p, fm, True) for p in allp]
        total = sum(len(p.segments) or 1 for p in allp)
        probe = Pill(OVERFLOW_KEY, "", "", f"+{total}", "", overflow=True)
        if stacked:             # one component per row; the bar grows downwards up to the available height
            comp, ov_ext = False, self._pill_h
            count = fit_count([self._pill_ext(p) for p in allp], self._stack_limit, ov_ext, spacing)
        elif self._vertical:    # a column: the main axis is the pill height, widths do not matter
            ext = [self._pill_ext(p) for p in allp]
            comp, count = False, fit_count(ext, self._limit, self._pill_h, spacing)
            ov_ext = self._pill_h
        else:
            comp, count = fit_pills(full, compact, self._limit, self._pill_w(probe, fm, True), spacing)
            ov_ext = self._pill_w(probe, fm, True)
        shown = allp[:count]
        widths = list((compact if comp else full)[:count])
        heights = [self._pill_ext(p) for p in shown]
        hidden = [s for p in allp[count:] for s in (p.segments or [p])]
        if not stacked and self._limit is not None and count == 1 and len(shown[0].segments) > 1:
            # a lone group that does not fit: keep the leading values that do (never a half group otherwise)
            g = shown[0]
            segs = list(g.segments)
            more = count < len(allp)

            def extent(gr: Pill) -> int:
                return self._pill_ext(gr) if self._vertical else self._pill_w(gr, fm, comp)

            while len(segs) > 1 and total_extent(
                    [extent(build_group(segs))] + ([ov_ext] if more or len(segs) < len(g.segments) else []),
                    spacing) > self._limit:
                segs.pop()
            if len(segs) < len(g.segments):
                hidden = g.segments[len(segs):] + hidden
                trimmed = build_group(segs)
                shown, widths, heights = [trimmed], [self._pill_w(trimmed, fm, comp)], [self._pill_ext(trimmed)]
        shown = list(shown)
        if hidden:
            ov = self._overflow_pill(hidden)
            shown.append(ov)
            widths.append(self._pill_w(ov, fm, comp))
            heights.append(self._pill_h)
        self._compact = comp
        self._pills = shown
        self._hidden = hidden
        if stacked and widths:  # every row as wide as the widest, so the columns line up
            widths = [max(widths)] * len(widths)
        self._rects, (tw, th) = layout_pills(widths, self._pill_h, self._vertical or stacked, spacing,
                                             heights=heights)
        size = QSize(max(tw, 1), max(th, 1))
        if size != self.size():
            self.setFixedSize(size)
            self._last_geom = None
        if self._hover >= len(self._pills):
            self._hover = -1
        self.update()

    # ------------------------------------------------------------------ positioning
    def _screens(self) -> list:
        """[(ScreenInfo, QScreen)]; cached and rebuilt only after a screen signal."""
        cache = self._screens_cache
        if cache is not None:
            # a screen can be destroyed before its removal signal reaches us (unplug, RDP, tests)
            current = QGuiApplication.screens()
            if any(not shiboken6.isValid(sc) or sc not in current for _, sc in cache):
                cache = None
        if cache is None:
            cache = self._screens_cache = screen_infos()
        return cache

    def _reg(self, fn):
        """fn() with a ~5 s cache (registry reads); keyed by the function object."""
        now = time.monotonic()
        hit = self._reg_cache.get(fn)
        if hit is not None and now - hit[0] < REG_CACHE_TTL:
            return hit[1]
        val = fn()
        self._reg_cache[fn] = (now, val)
        return val

    def _connect_screen_signals(self) -> None:
        app = QGuiApplication.instance()
        if app is None:
            return
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screen_removed)
        app.primaryScreenChanged.connect(self._on_screens_changed)
        for sc in QGuiApplication.screens():
            self._watch_screen(sc)

    def _watch_screen(self, sc) -> None:
        if any(sc == w for w in self._watched):
            return
        self._watched.append(sc)
        sc.geometryChanged.connect(self._on_screens_changed)
        sc.availableGeometryChanged.connect(self._on_screens_changed)

    def _on_screen_added(self, sc) -> None:
        self._watch_screen(sc)
        self._on_screens_changed()

    def _on_screen_removed(self, sc) -> None:
        keep = []
        for w in self._watched:
            if w == sc:
                try:
                    w.geometryChanged.disconnect(self._on_screens_changed)
                    w.availableGeometryChanged.disconnect(self._on_screens_changed)
                except (RuntimeError, TypeError):
                    pass
            else:
                keep.append(w)
        self._watched = keep
        if self._cur_qscreen is not None and self._cur_qscreen == sc:
            self._cur_qscreen = None
        self._on_screens_changed()

    def _on_screens_changed(self, *_args) -> None:
        self._screens_cache = None
        self._last_geom = None
        if not self._started:
            return
        self.update_position()
        QTimer.singleShot(0, self._reposition_later)     # the screen list settles after the signal

    def _reposition_later(self) -> None:
        try:
            self._last_geom = None
            if self._started:
                self.update_position()
        except RuntimeError:        # widget already deleted
            pass

    def _neighbours(self, info, now: float) -> list:
        """Rects of other taskbar widgets, enumerated at most once per POSITION_INTERVAL_S."""
        key = (tuple(info.rect), tuple(info.tray_rect), info.device_name)
        c = self._nb_cache
        if c is not None and c[0] == key and now - c[1] < POSITION_INTERVAL_S:
            return c[2]
        rects = taskbar.neighbour_widget_rects(info, os.getpid())
        self._nb_cache = (key, now, rects)
        return rects

    def _is_floating(self) -> bool:
        return self._ps.mode == "floating"

    def _build_placement(self, choice: str, screen: ScreenInfo, fallback: bool = False) -> Placement:
        positions = self._ps.floating_positions
        if self._is_floating():
            anchor, dx, dy = lookup_floating(positions, screen.key, choice)
            return Placement("floating", "right", choice, anchor, dx, dy)
        anchor = "left" if self._ps.anchor == "left" else "right"
        if fallback:    # taskbar mode on a monitor without a taskbar: saved floating spot of that monitor
            fa, dx, dy = lookup_floating(positions, screen.key, choice)
            return Placement("taskbar", anchor, choice, fa, dx, dy)
        return Placement("taskbar", anchor, choice)

    def _build_env(self, info, screen: ScreenInfo, placement: Placement, fallback: bool, now: float) -> Env:
        fullscreen = False
        if self._pills:
            if self._n_screens > 1:
                fullscreen = taskbar.is_fullscreen_on_monitor(screen.name)
            else:       # single monitor: the global verdict alone decides
                if info is not None:
                    mon = info.monitor_rect
                else:
                    gx, gy, gw, gh = screen.geometry
                    d = screen.dpr or 1.0
                    mon = (round(gx * d), round(gy * d), round((gx + gw) * d), round((gy + gh) * d))
                fullscreen = taskbar.is_fullscreen_foreground(mon)
        centered, widgets = True, False
        dock = dock_left = None
        dpr = screen.dpr or 1.0
        if info is not None and placement.mode == "taskbar":
            centered = self._reg(taskbar.taskbar_icons_centered)
            if placement.anchor == "left":
                widgets = self._reg(taskbar.taskbar_widgets_button)
            if info.edge in (taskbar.EDGE_TOP, taskbar.EDGE_BOTTOM):
                # share the strip with other taskbar widgets (e.g. Taskbar Battery Widget)
                nbs = self._neighbours(info, now)
                edge = taskbar.docked_edge(info.tray_rect[0], nbs)
                dock = edge if edge != info.tray_rect[0] else None
                start = info.rect[0] + left_margin_phys(info, dpr, widgets)
                left = docked_edge_left(start, nbs)
                dock_left = left if left != start else None
        return Env(info=info, fullscreen=fullscreen, has_pills=bool(self._pills),
                   screen_name=screen.name, screen_origin=screen.geometry[:2], dpr=dpr,
                   size=(self.width(), self.height()), available=screen.available, icons_centered=centered,
                   widgets_button=widgets, floating_fallback=fallback,
                   show_in_fullscreen=bool(self._ps.show_in_fullscreen),
                   dock_edge=dock, dock_edge_left=dock_left)

    def _apply_screen(self, qscreen) -> None:
        """Bind the window to `qscreen` before moving it (per-monitor DPI)."""
        if qscreen is None or qscreen == self._cur_qscreen:
            return
        self._cur_qscreen = qscreen
        wh = self.windowHandle()
        if wh is not None:
            if wh.screen() != qscreen:
                wh.setScreen(qscreen)
        elif self.screen() != qscreen:
            self.setScreen(qscreen)

    @staticmethod
    def _left_button_down() -> bool:
        return bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    def _cancel_drag(self) -> None:
        """Forget a drag/press whose release never arrived (button released elsewhere, window hidden)."""
        was = self._dragging
        self._press_global, self._dragging, self._press_idx = None, False, -1
        if was:
            self._last_geom = None      # the window was moved by hand: force it back to its place
            self.update()

    def _tick(self) -> None:
        self.update_position(force=False)

    def update_position(self, force: bool = True) -> None:
        """Place the bar. The costly part (taskbar query, neighbour enumeration, placement) runs at most
        once per POSITION_INTERVAL_S unless `force`; in between the timer only re-asserts the z-order."""
        if self._dragging or self._press_global is not None:
            if not self._left_button_down():
                self._cancel_drag()         # stuck drag: the release was lost
            elif self._dragging:
                return
        now = time.monotonic()
        if not force and now - self._last_pos < POSITION_INTERVAL_S:
            if self.isVisible():
                ww._assert_topmost(int(self.winId()))
            return
        self._last_pos = now
        pairs = self._screens()
        self._n_screens = len(pairs)
        choice = self._ps.monitor if isinstance(self._ps.monitor, str) and self._ps.monitor else "primary"
        sinfo = resolve_screen(choice, [p[0] for p in pairs])   # missing monitor -> primary (settings untouched)
        if sinfo is None or not self._pills:
            self._set_visible(False)
            return
        qscreen = next(q for i, q in pairs if i is sinfo)
        self._cur_screen = sinfo
        self.set_stack_limit(qscreen.availableGeometry().height())
        floating = self._is_floating()
        infos = [] if floating else taskbar.get_taskbar_infos()
        tinfo = None if floating else taskbar_for_screen(sinfo, infos, {i.name for i, _ in pairs})
        fallback = not floating and tinfo is None       # a monitor without a taskbar: float on it
        placement = self._build_placement(choice, sinfo, fallback)
        env = self._build_env(tinfo, sinfo, placement, fallback, now)
        fs_placement = choose_placement(env, placement, self._ps.fullscreen_placement,
                                        self._ps.fullscreen_positions, sinfo.key, choice)
        self._fs_override = fs_placement is not placement
        if self._fs_override:       # float on this monitor while the fullscreen app is up; back afterwards
            placement, floating, tinfo = fs_placement, True, None
            env = replace(env, info=None, floating_fallback=False)
        res = compute_placement(env, placement)
        self._note_reason(res.reason)
        floating = floating or res.reason == REASON_NO_TASKBAR_FLOATING
        if not res.visible:
            self._set_floating(floating)
            self._set_visible(False)
            return
        self._apply_screen(qscreen)
        size_before = (self.width(), self.height())
        self._set_floating(floating)
        if (self.width(), self.height()) != size_before:    # stacking switched with the mode
            env = replace(env, size=(self.width(), self.height()))
            res = compute_placement(env, placement)
        thickness = None
        if tinfo is not None and not floating:      # a real taskbar: the pills may not outgrow it
            dpr = env.dpr or 1.0
            thickness = (taskbar.rect_w(tinfo.rect) if res.vertical else taskbar.rect_h(tinfo.rect)) / dpr
        self._base_h, self._thickness = res.pill_h or DEFAULT_PILL_H, thickness
        changed = self._apply_metrics()
        limit = res.max_extent
        if changed or res.vertical != self._vertical or limit != self._limit:
            self._vertical, self._limit = res.vertical, limit
            self._relayout()
            env = replace(env, size=(self.width(), self.height()))      # size changed; nothing else re-read
            res = compute_placement(env, placement)
        x, y = res.rect[0], res.rect[1]
        key = (x, y, self.width(), self.height(), sinfo.name, placement.mode, res.reason)
        if self._last_geom != key:
            self._last_geom = key
            self.move(x, y)
        self._set_visible(True)
        self._set_passive(res.fullscreen_passive and self.isVisible())
        if self.isVisible():
            ww._assert_topmost(int(self.winId()))

    def _note_reason(self, reason: str) -> None:
        """Emit placement_warning once each time the placement starts needing a fallback."""
        if reason == REASON_LEFT_NEEDS_CENTERED and self._last_reason != reason:
            self.placement_warning.emit(reason)
        self._last_reason = reason

    def _set_floating(self, floating: bool) -> None:
        if self._floating != floating:
            self._floating = floating
            if self._stack:
                self._relayout()
            if floating:
                self.setCursor(Qt.CursorShape.SizeAllCursor)    # the whole bar is a drag handle
            else:
                self.unsetCursor()
            self.update()

    def _set_visible(self, vis: bool) -> None:
        if vis and not self.isVisible():
            self.show()
        elif not vis and self.isVisible():
            self._set_passive(False)
            self.hide()

    # ------------------------------------------------------------------ fullscreen passive mode
    def _interactive(self) -> bool:
        return not self._passive or self._shift_held

    def is_passive(self) -> bool:
        """Shown over a fullscreen app: mouse passes through unless Shift is held."""
        return self._passive

    def _set_passive(self, on: bool) -> None:
        """Over a fullscreen app the widget ignores the mouse (click-through) unless Shift is held."""
        if on == self._passive:
            return
        self._passive = on
        self._shift_held = False
        self.update()
        QToolTip.hideText()
        if self._hover != -1:
            self._hover = -1
        if self._dragging or self._press_global is not None:
            self._cancel_drag()
        if on:
            ww.set_click_through(int(self.winId()), True)
            self._shift_timer.start()
        else:
            self._shift_timer.stop()
            ww.set_click_through(int(self.winId()), False)

    def _shift_tick(self) -> None:
        if not self._passive:
            self._shift_timer.stop()
            return
        down = ww.shift_down()
        if down == self._shift_held:
            return
        self._shift_held = down
        ww.set_click_through(int(self.winId()), not down)
        if not down:
            QToolTip.hideText()
            self._hover = -1
            if self._dragging or self._press_global is not None:
                self._cancel_drag()
            self.update()

    # ------------------------------------------------------------------ window events
    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._styled:
            self._styled = True
            ww._apply_ex_styles(int(self.winId()))
        ww._assert_topmost(int(self.winId()))

    def hideEvent(self, e) -> None:
        if self._dragging or self._press_global is not None:
            self._cancel_drag()         # hidden mid-drag: the release can no longer reach us
        super().hideEvent(e)

    def closeEvent(self, e) -> None:
        self._timer.stop()
        self._shift_timer.stop()
        self._zguard.stop()
        super().closeEvent(e)

    # ------------------------------------------------------------------ painting
    def _text_color(self, pill: Pill, fg: QColor, dark: bool) -> QColor:
        if pill.overflow or not pill.has_value:
            return band_color("grey", dark)
        if pill.band == "grey":
            return fg                   # neutral kinds (clock, power, ...): plain text colour
        return band_color(pill.band, dark)

    def _paint_bar(self, p: QPainter, color: QColor, pill: Pill, bx: float, y: float, bw: float, h: float) -> None:
        """Thin level bar at the bottom of a pill (or of one line); dropped in compact mode / short pills."""
        if pill.frac is None or h < BAR_MIN_PILL_H or self._compact or not self.pill_bars():
            return
        by = y + h - 5.0
        track = QColor(color)
        track.setAlpha(55 if not pill.stale else 35)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(bx, by, bw, BAR_H), BAR_H / 2, BAR_H / 2)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(bx, by, max(BAR_H, bw * pill.frac), BAR_H), BAR_H / 2, BAR_H / 2)

    def _paint_segments(self, p: QPainter, pill: Pill, x: float, y: float, w: float, tx: float, row_h: float,
                        pad: int, fg: QColor, dark: bool) -> None:
        """Values of a grouped pill: side by side in a row, one per line in a column; each with its own
        band colour and its own thin bar under it. Stacked: every row uses the shared column widths."""
        stacked = self.is_stacked()
        widths = list(self._stack_cols) if stacked else self._seg_widths(pill, QFontMetrics(self._font()))
        segs = (pill.segments or [pill]) if stacked else pill.segments
        align = Qt.AlignmentFlag.AlignRight if stacked else Qt.AlignmentFlag.AlignHCenter
        gap = self._seg_gap(self._compact)
        sx = tx
        for n, (seg, sw) in enumerate(zip(segs, widths)):
            color = QColor(self._text_color(seg, fg, dark))
            if pill.stale:
                color.setAlpha(DIM_ALPHA)
            if self._vertical:
                sy, area_w = y + n * row_h, w - (tx - x) - pad
                rect = QRectF(tx, sy, area_w, row_h)
            else:
                sy, area_w = y, sw
                rect = QRectF(sx, sy, sw, row_h)
            p.setPen(color)
            p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | align), seg.text)
            self._paint_bar(p, color, replace(seg, stale=pill.stale), rect.x(), sy, area_w, row_h)
            sx += sw + gap

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dark = self.pill_dark()
        fg = text_color(dark)
        if self._floating:      # backdrop so the pills read over any wallpaper
            bg = QColor(*TASKBAR_BASE[dark])
            bg.setAlpha(FLOATING_BG_ALPHA)
            edge = QColor(fg)
            edge.setAlpha(FLOATING_BORDER_ALPHA)
            pen = QPen(edge)
            pen.setWidthF(1.0)
            p.setPen(pen)
            p.setBrush(bg)
            rad = min(self.height() / 2, self._pill_h / 2 + MARGIN)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        p.setFont(self._font())
        icon = self._icon_size()
        pad = self._pad(self._compact)
        gap = self._gap(self._compact)
        style = self._style
        stacked = self.is_stacked()
        lfont, lfm = self._label_font(), QFontMetrics(self._label_font())
        sep = QColor(fg)
        sep.setAlpha(SEPARATOR_ALPHA)
        for i, (pill, (x, y, w, h)) in enumerate(zip(self._pills, self._rects)):
            r = QRectF(x, y, w, h)
            rad = min(h, self._pill_h) / 2.5
            if style != "classic":
                rad = min(rad, SOFT_RADIUS)
            color = QColor(self._text_color(pill, fg, dark))
            icon_color = QColor(fg)
            if pill.stale:
                color.setAlpha(DIM_ALPHA)
                icon_color.setAlpha(DIM_ALPHA)
            hover = i == self._hover and self._interactive()
            fill = pill_tint(("low_hover" if hover else "low") if pill.alert else ("hover" if hover else "normal"),
                             dark)
            p.setPen(Qt.PenStyle.NoPen)
            boxed = style in ("classic", "soft")
            if boxed or hover:      # transparent styles only tint the pill under the mouse
                if style == "soft":
                    fill = QColor(fill)
                    fill.setAlpha(round(fill.alpha() * SOFT_ALPHA))
                p.setBrush(fill)
                p.drawRoundedRect(r, rad, rad)
            if style == "transparent" and not self._vertical and not stacked and i < len(self._rects) - 1:
                lx = x + w + self._spacing() / 2.0      # hairline between this pill and the next
                p.setPen(QPen(sep, 1.0))
                p.drawLine(QPointF(lx, y + h * 0.2), QPointF(lx, y + h * 0.8))
                p.setPen(Qt.PenStyle.NoPen)
            if boxed and pill.alert:   # non-colour cues for "critical": 1 px outline and the "!" in the text
                pen = QPen(color)
                pen.setWidthF(1.0)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
            if pill.overflow:
                p.setPen(color)
                p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), pill.text)
                continue
            custom = self._custom_file(pill)
            ix = x + pad
            tx = ix
            row_h = self._pill_h if pill.segments and self._vertical else h
            label = self._lead_label(pill)
            if label:           # abbreviation (CPU, GPU, ...) in place of the icon
                lw = self._stack_lead if stacked else lfm.horizontalAdvance(label)
                p.setFont(lfont)
                p.setPen(icon_color)
                p.drawText(QRectF(ix, y, lw, row_h), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                           label)
                p.setFont(self._font())
                tx = ix + lw + gap
            elif custom is not None or icons_visible():
                ir = QRectF(ix, y + (row_h - icon) / 2, icon, icon)
                drawn = custom is not None and custom_icons.draw_custom_icon(p, ir, custom, icon_color.alphaF())
                if not drawn:
                    draw_kind_icon(p, pill.hw_kind, ir, icon_color)
                tx = ix + (self._stack_lead if stacked else icon) + gap
            elif stacked and self._stack_lead:
                tx = ix + self._stack_lead + gap
            if pill.segments or stacked:
                self._paint_segments(p, pill, x, y, w, tx, row_h, pad, fg, dark)
                continue
            p.setPen(color)
            p.drawText(QRectF(tx, y, w - (tx - x) - pad, h),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter), pill.text)
            if pill.frac is not None:
                self._paint_bar(p, color, pill, x + pad, y, w - 2 * pad, h)

        p.end()

    # ------------------------------------------------------------------ interaction
    @staticmethod
    def _drag_threshold() -> int:
        try:
            return max(DRAG_THRESHOLD, int(QGuiApplication.styleHints().startDragDistance()))
        except Exception:
            return DRAG_THRESHOLD

    def _clamp_drag(self, pos: QPoint, cursor: QPoint) -> QPoint:
        """Keep the dragged window inside the available area of the monitor under the cursor."""
        screen = self._cur_screen
        if self._is_floating() and not self._fs_override:   # free to cross monitors; a fallback / fullscreen spot stays on its monitor
            for info, _q in self._screens():
                gx, gy, gw, gh = info.geometry
                if gx <= cursor.x() < gx + gw and gy <= cursor.y() < gy + gh:
                    screen = info
                    break
        if screen is None:
            return pos
        ax, ay, aw, ah = screen.available
        x = max(ax, min(pos.x(), ax + aw - self.width()))
        y = max(ay, min(pos.y(), ay + ah - self.height()))
        return QPoint(x, y)

    def mouseMoveEvent(self, e) -> None:
        if not self._interactive():
            return
        if (self._press_global is not None and self._floating
                and e.buttons() & Qt.MouseButton.LeftButton):
            delta = e.globalPosition().toPoint() - self._press_global
            if not self._dragging and math.hypot(delta.x(), delta.y()) > self._drag_threshold():
                self._dragging = True
                QToolTip.hideText()
                self._hover = -1
                self.update()
            if self._dragging:
                self.move(self._clamp_drag(self._press_origin + delta, e.globalPosition().toPoint()))
                return
        idx = pill_index_at(self._rects, e.position().x(), e.position().y())
        if idx != self._hover:
            self._hover = idx
            QToolTip.hideText()
            self.update()

    def leaveEvent(self, _e) -> None:
        QToolTip.hideText()
        if self._hover != -1:
            self._hover = -1
            self.update()

    def mousePressEvent(self, e) -> None:
        QToolTip.hideText()
        if not self._interactive():
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._press_origin = self.pos()
            self._dragging = False
            self._press_idx = pill_index_at(self._rects, e.position().x(), e.position().y())
        elif e.button() == Qt.MouseButton.RightButton:
            self.context_menu_requested.emit(e.globalPosition().toPoint())

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            dragged, idx = self._dragging, self._press_idx
            self._press_global, self._dragging, self._press_idx = None, False, -1
            if dragged:
                self._finish_drag()
                return
            if idx >= 0 and pill_index_at(self._rects, e.position().x(), e.position().y()) == idx:
                self.clicked.emit()
                return
        super().mouseReleaseEvent(e)

    # ---- drop: snap to an anchor and tell the controller
    def _save_floating(self, key: str, entry: dict, new_choice: str | None = None) -> None:
        """Remember `entry` for monitor `key` (and a new monitor choice) and report it; applied at once."""
        positions = dict(self._ps.floating_positions)
        positions[key] = dict(entry)
        choice = self._ps.monitor
        final = new_choice if new_choice is not None else choice
        if final == "primary":
            positions.pop("primary", None)      # stale legacy entry would make the lookup ambiguous
        self._ps = replace(self._ps, floating_positions=positions, monitor=final)
        self._last_geom = None
        self.floating_moved.emit(key, dict(entry), False)
        if final != choice:
            self.monitor_changed.emit(final)
        self.update_position()

    def _finish_drag(self) -> None:
        """Snap the dropped widget to the nearest anchor of the monitor under it and remember it."""
        if self._fs_override and self._cur_screen is not None:
            self._finish_fullscreen_drag(self._cur_screen)
            return
        pairs = self._screens()
        centre = self.geometry().center()
        target = None
        taskbar_mode = not self._is_floating()      # no-taskbar fallback: stay on that monitor, mode untouched
        for info, _q in pairs:
            gx, gy, gw, gh = info.geometry
            if not taskbar_mode and gx <= centre.x() < gx + gw and gy <= centre.y() < gy + gh:
                target = info
                break
        if target is None:
            target = self._cur_screen or resolve_screen("primary", [i for i, _ in pairs])
        if target is None:
            return
        anchor, dx, dy = snap_anchor((self.x(), self.y(), self.width(), self.height()), target.available)
        choice = self._ps.monitor
        new_choice = choice if taskbar_mode else ("primary" if (choice == "primary" and target.primary)
                                                  else target.key)
        self._save_floating(target.key, {"anchor": anchor, "dx": dx, "dy": dy}, new_choice)

    def _finish_fullscreen_drag(self, screen: ScreenInfo) -> None:
        """Drop over a fullscreen app: remember the spot for this monitor in the fullscreen positions
        only (the normal position is untouched). A fixed-anchor choice becomes "floating" with the new spot."""
        anchor, dx, dy = snap_anchor((self.x(), self.y(), self.width(), self.height()), screen.available)
        entry = {"anchor": anchor, "dx": dx, "dy": dy}
        positions = dict(self._ps.fullscreen_positions)
        positions[screen.key] = entry
        changed = self._ps.fullscreen_placement != "floating"
        self._ps = replace(self._ps, fullscreen_positions=positions, fullscreen_placement="floating")
        self._last_geom = None
        self.floating_moved.emit(screen.key, dict(entry), True)
        if changed:
            self.fullscreen_placement_changed.emit("floating")
        self.update_position()

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ToolTip:
            idx = pill_index_at(self._rects, e.pos().x(), e.pos().y())
            if idx >= 0 and self._interactive():
                QToolTip.showText(e.globalPos(), tooltip_html(self._pills[idx].tooltip), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)
