"""Detail window: every sensor in a hardware > group > sensor tree, with search, pin and summary checkboxes.

A separate resizable top-level window (the panel only shows the curated summary). Like the panel it does not
know `Config` (ADR-7): it receives snapshot, pins and `Prefs`, and emits signals the controller persists.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QPersistentModelIndex, QPointF, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPainterPath, QPen,
                           QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import custom_icons, i18n, taskbar
from .colors import TASKBAR_BASE, band_color, section_header_color, text_color
from .curation import DetailHardware, DetailTree, Prefs, detail_tree, in_summary, section_title_key
from .icons import icons_visible
from .models import Sensor, Snapshot, band_for, format_value
from .panel import bulk_state_of, covered_keys, hw_title, kind_label, kind_or_custom_pixmap, sensor_a11y_name
from .sensor_names import display_labels, display_name, uniquify

log = logging.getLogger(__name__)

MIN_WIDTH, MIN_HEIGHT = 560, 360
DEFAULT_SIZE = (780, 600)
MAX_TOGGLED = 300
ICON_PX = 18
GEOMETRY_DELAY_MS = 400

COL_NAME, COL_VALUE, COL_MIN, COL_MAX, COL_PIN, COL_SUMMARY = range(6)
_CHECK_COLS = (COL_PIN, COL_SUMMARY)
CHECK_PX = 16
_COL_KEYS = ("col_name", "col_value", "col_min", "col_max", "col_pinned", "col_summary")

ROLE_ID = Qt.ItemDataRole.UserRole          # node id ("hw:<id>", "grp:<id>:<group>", "modules", sensor key)
ROLE_KIND = Qt.ItemDataRole.UserRole + 1    # "hw" | "grp" | "modules" | "sensor"
ROLE_TEXT = Qt.ItemDataRole.UserRole + 2    # lower-cased search text
ROLE_LOCKED = Qt.ItemDataRole.UserRole + 3  # per column: the check box is shown disabled (hidden hardware)

MODULES_ID = "modules"
_CHECKED, _UNCHECKED, _PARTIAL = (Qt.CheckState.Checked, Qt.CheckState.Unchecked,
                                  Qt.CheckState.PartiallyChecked)
_BULK_TO_QT = {"none": _UNCHECKED, "some": _PARTIAL, "all": _CHECKED}


def check_colors(dark: bool) -> dict[str, QColor]:
    """Colours of the painted check boxes. Every border keeps >= 3:1 against `field` (the tree background);
    on a selected row the border is the text colour."""
    return {
        "field": QColor(44, 44, 44) if dark else QColor(255, 255, 255),
        "border": QColor(160, 160, 160) if dark else QColor(105, 105, 105),
        "hover": QColor(235, 235, 235) if dark else QColor(30, 30, 30),
        "disabled": QColor(125, 125, 125) if dark else QColor(140, 140, 140),
        "fill": band_color("green", dark),
        "mark": QColor(20, 20, 20) if dark else QColor(255, 255, 255),
        "focus": band_color("amber", dark),
        "text": text_color(dark),
    }


def _is_locked(index) -> bool:
    return bool(index.data(ROLE_LOCKED))


class _CheckDelegate(QStyledItemDelegate):
    """Paints the pin / summary check boxes (unchecked, checked tick, partial dash, disabled dashed) with a clear
    border in both themes, centred in the cell; a click anywhere in the cell toggles it."""

    def __init__(self, tree: "_Tree"):
        super().__init__(tree)
        self._tree = tree
        self._dark = True
        self._press: QPersistentModelIndex | None = None

    def set_dark(self, dark: bool) -> None:
        self._dark = bool(dark)

    def _has_box(self, index) -> bool:
        return index.column() in _CHECK_COLS and index.data(Qt.ItemDataRole.CheckStateRole) is not None

    def paint(self, painter, option, index) -> None:
        if not self._has_box(index):
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        item = self._tree.itemFromIndex(index)
        state = {_CHECKED: "checked", _PARTIAL: "partial"}.get(item.checkState(index.column()) if item else None,
                                                             "off")
        S = QStyle.StateFlag
        locked = _is_locked(index) or not (option.state & S.State_Enabled)
        side = CHECK_PX
        r = option.rect
        box = QRectF(r.x() + (r.width() - side) / 2, r.y() + (r.height() - side) / 2, side, side)
        paint_check(painter, box, state, self._dark, enabled=not locked,
                    hover=bool(option.state & S.State_MouseOver), selected=bool(option.state & S.State_Selected),
                    focus=bool(option.state & S.State_HasFocus) and index.column() == COL_PIN)

    def editorEvent(self, event, model, option, index) -> bool:
        if not self._has_box(index):
            return super().editorEvent(event, model, option, index)
        t = event.type()
        if _is_locked(index) or not (index.flags() & Qt.ItemFlag.ItemIsEnabled):
            return t in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick)
        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._press = QPersistentModelIndex(index)
            return False
        if t == QEvent.Type.MouseButtonDblClick:
            return True                     # a double click must not expand the node nor toggle twice
        if t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            pressed, self._press = self._press, None
            if pressed is not None and pressed == QPersistentModelIndex(index) \
                    and option.rect.contains(event.position().toPoint()):
                item = self._tree.itemFromIndex(index)
                if item is not None:
                    col = index.column()
                    item.setCheckState(col, _UNCHECKED if item.checkState(col) == _CHECKED else _CHECKED)
                return True
        return False


def paint_check(painter: QPainter, box: QRectF, state: str, dark: bool, enabled: bool = True,
                hover: bool = False, selected: bool = False, focus: bool = False) -> None:
    """One check box: `state` is "off" | "checked" | "partial"."""
    c = check_colors(dark)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    r = box.adjusted(0.8, 0.8, -0.8, -0.8)
    on = state != "off"
    if not enabled:
        border = c["disabled"]
    elif selected:
        border = c["text"]
    elif hover:
        border = c["hover"]
    else:
        border = c["border"]
    pen = QPen(border, 2.0 if hover and enabled else 1.6)
    if not enabled:
        pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    if on:
        painter.setBrush(c["fill"] if enabled else c["disabled"])
        if enabled:
            painter.setPen(QPen(c["fill"] if not selected else c["text"], 1.6))
    else:
        painter.setBrush(c["field"])
    painter.drawRoundedRect(r, 3, 3)
    if on:
        k = box.width() / 16.0
        mark = QPen(c["mark"], 2.0 * max(k, 0.75))
        mark.setCapStyle(Qt.PenCapStyle.RoundCap)
        mark.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(mark)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        o = box.topLeft()
        if state == "checked":
            path = QPainterPath(o + QPointF(4 * k, 8.4 * k))
            path.lineTo(o + QPointF(7 * k, 11.2 * k))
            path.lineTo(o + QPointF(12 * k, 5.2 * k))
            painter.drawPath(path)
        else:
            painter.drawLine(o + QPointF(4.4 * k, 8 * k), o + QPointF(11.6 * k, 8 * k))
    if focus:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(c["focus"], 2.0))
        painter.drawRoundedRect(box.adjusted(-3, -3, 3, 3), 5, 5)
    painter.restore()


def default_expanded(kind: str) -> bool:
    """Hardware nodes start expanded; groups and the memory-modules node start collapsed."""
    return kind == "hw"


class _Tree(QTreeWidget):
    """Space toggles "pinned" of the current row, Ctrl+Space toggles "in summary"."""

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            item = self.currentItem()
            col = COL_SUMMARY if ev.modifiers() & Qt.KeyboardModifier.ControlModifier else COL_PIN
            if item is not None and item.data(col, Qt.ItemDataRole.CheckStateRole) is not None \
                    and not item.data(col, ROLE_LOCKED):
                st = item.checkState(col)
                item.setCheckState(col, _UNCHECKED if st == _CHECKED else _CHECKED)
            ev.accept()
            return
        super().keyPressEvent(ev)


class DetailWindow(QWidget):
    pin_toggled = Signal(object)                    # Sensor
    bulk_pin_toggled = Signal(object)               # list[Sensor] (a hardware, group or modules node)
    summary_override_changed = Signal(str, bool)    # sensor key, wanted "in summary"
    toggled_changed = Signal(object)                # list[str] node ids that differ from the default expansion
    geometry_changed = Signal(object)               # {"x", "y", "w", "h"}

    def __init__(self, parent=None, dark: bool | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("DetailWindow")
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.resize(*DEFAULT_SIZE)
        self._dark = (not taskbar.taskbar_uses_light_theme()) if dark is None else bool(dark)
        self._snapshot = Snapshot()
        self._prefs = Prefs()
        self._pinned: list[str] = []
        self._fahrenheit = False
        self._toggled: set[str] = set()
        self._dirty = True
        self._sig: tuple | None = None
        self._updating = False          # programmatic item changes (never reach the signals)
        self._programmatic = False      # programmatic expand / collapse
        self._items: dict[str, QTreeWidgetItem] = {}        # sensor key -> leaf
        self._nodes: dict[str, QTreeWidgetItem] = {}        # node id -> hardware / group / modules node
        self._node_keys: dict[str, list[str]] = {}          # node id -> sensor keys below it
        self._cache: dict[str, tuple] = {}                  # sensor key -> last rendered value texts
        self._a11y: dict[str, str] = {}                     # sensor key -> unique accessible name
        self._geometry_pending = False
        self.rebuild_count = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setObjectName("detailSearch")
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        top.addWidget(self.search, 1)
        self.expand_btn = QPushButton()
        self.expand_btn.setObjectName("expandAllBtn")
        self.expand_btn.clicked.connect(lambda _c=False: self._expand_all(True))
        top.addWidget(self.expand_btn)
        self.collapse_btn = QPushButton()
        self.collapse_btn.setObjectName("collapseAllBtn")
        self.collapse_btn.clicked.connect(lambda _c=False: self._expand_all(False))
        top.addWidget(self.collapse_btn)
        lay.addLayout(top)

        self.tree = _Tree()
        self.tree.setObjectName("detailTree")
        self.tree.setColumnCount(len(_COL_KEYS))
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setIconSize(QSize(ICON_PX, ICON_PX))
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col, w in ((COL_VALUE, 130), (COL_MIN, 90), (COL_MAX, 90), (COL_PIN, 92), (COL_SUMMARY, 84)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            hdr.resizeSection(col, w)
        self._delegate = _CheckDelegate(self.tree)
        self._delegate.set_dark(self._dark)
        for col in _CHECK_COLS:
            self.tree.setItemDelegateForColumn(col, self._delegate)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemExpanded.connect(lambda it: self._on_expanded(it, True))
        self.tree.itemCollapsed.connect(lambda it: self._on_expanded(it, False))
        self.message = QLabel()                 # "No results" / "No sensors yet"
        self.message.setObjectName("detailMessage")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.message)
        lay.addWidget(self.stack, 1)

        self.notice = QLabel()
        self.notice.setObjectName("detailNotice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        lay.addWidget(self.notice)
        bottom = QHBoxLayout()
        self.hint = QLabel()
        self.hint.setObjectName("detailHint")
        bottom.addWidget(self.hint, 1)
        self.count = QLabel()
        self.count.setObjectName("detailCount")
        bottom.addWidget(self.count)
        lay.addLayout(bottom)

        find = QShortcut(QKeySequence("Ctrl+F"), self)
        find.setContext(Qt.ShortcutContext.WindowShortcut)
        find.activated.connect(self.focus_search)
        self._geo_timer = QTimer(self)
        self._geo_timer.setSingleShot(True)
        self._geo_timer.setInterval(GEOMETRY_DELAY_MS)
        self._geo_timer.timeout.connect(self._emit_geometry)
        self.setTabOrder(self.search, self.expand_btn)
        self.setTabOrder(self.expand_btn, self.collapse_btn)
        self.setTabOrder(self.collapse_btn, self.tree)
        self._apply_style()
        self._apply_texts()
        self._show_message(i18n.tr("empty"))

    # ------------------------------------------------------------------ public API
    @property
    def dark(self) -> bool:
        return self._dark

    def set_snapshot(self, snapshot: Snapshot) -> None:
        """Stores the snapshot; the tree is touched only while the window is visible (rebuilt only when the set
        of sensors changed, otherwise values are updated in place)."""
        self._snapshot = snapshot
        if not self.isVisible():
            self._dirty = True
            return
        self._apply_snapshot()

    def set_pinned(self, pinned: list[str]) -> None:
        pinned = list(pinned)
        if pinned != self._pinned:
            self.set_notice("")             # "N not added" only describes the pins it was made with
        self._pinned = pinned
        if not self.isVisible():
            self._dirty = True
            return
        self._refresh_pins()

    def set_prefs(self, prefs: Prefs) -> None:
        self._prefs = prefs if isinstance(prefs, Prefs) else Prefs()
        if not self.isVisible():
            self._dirty = True
            return
        self._refresh_summary()

    def set_fahrenheit(self, value: bool) -> None:
        if bool(value) != self._fahrenheit:
            self._fahrenheit = bool(value)
            if not self.isVisible():
                self._dirty = True
                return
            self._cache.clear()
            self._update_values()

    def set_dark(self, dark: bool) -> None:
        if bool(dark) != self._dark:
            self._dark = bool(dark)
            self._delegate.set_dark(self._dark)
            self._apply_style()
            self._sig = None
            self._dirty = True
            if self.isVisible():
                self._apply_snapshot()

    def set_toggled(self, ids) -> None:
        """Node ids whose expansion differs from the default (restored from the config)."""
        self._toggled = {x for x in (ids or ()) if isinstance(x, str)}
        if len(self._toggled) > MAX_TOGGLED:
            self._toggled = set(sorted(self._toggled)[:MAX_TOGGLED])
        if not self.search.text().strip():
            self._apply_expansion()

    def toggled(self) -> list[str]:
        return sorted(self._toggled)

    def set_notice(self, text: str) -> None:
        """Message under the tree (for example "3 not added"); "" hides it."""
        self.notice.setText(text)
        self.notice.setAccessibleName(text)
        self.notice.setVisible(bool(text))

    def retranslate(self) -> None:
        self._apply_texts()
        self._sig = None
        self._dirty = True
        if self.isVisible():
            self._apply_snapshot()
        else:
            self._show_message(i18n.tr("empty"))

    def focus_search(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def show_for(self, hw_id: str | None = None) -> None:
        """Open (or raise) the window; with `hw_id` the hardware is expanded, selected and scrolled to the top."""
        if not self.isVisible():
            self._place_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()
        if hw_id:
            self.search.clear()
            item = self._nodes.get("hw:" + hw_id)
            if item is not None:
                parent = item.parent()
                self._programmatic = True
                try:
                    if parent is not None:
                        parent.setExpanded(True)
                    item.setExpanded(True)
                finally:
                    self._programmatic = False
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop)
                self.tree.setFocus(Qt.FocusReason.OtherFocusReason)

    def current_geometry(self) -> dict:
        g = self.geometry()
        return {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}

    def restore_geometry(self, geo) -> None:
        """Apply a saved {"x","y","w","h"} (ignored when malformed; moved onto a screen when off-screen)."""
        try:
            x, y, w, h = (int(geo[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            return
        w, h = max(MIN_WIDTH, min(w, 8000)), max(MIN_HEIGHT, min(h, 8000))
        self.resize(w, h)
        screen = QGuiApplication.screenAt(self.geometry().translated(x - self.x(), y - self.y()).center())
        if screen is not None:
            self.move(x, y)

    # ------------------------------------------------------------------ build
    def _apply_snapshot(self) -> None:
        self._dirty = False
        tree = detail_tree(self._snapshot)
        sig = self._signature(tree)
        if sig != self._sig:
            self._sig = sig
            self._rebuild(tree)
        self._update_values()
        self._refresh_pins()
        self._refresh_summary()

    def _signature(self, tree: DetailTree) -> tuple:
        def hw_sig(hw: DetailHardware):
            return (hw.hw_id, hw.hw_kind, hw.hw_class, hw.hardware, tuple(s.key for s in hw.loose),
                    tuple((g.id, g.count, tuple(s.key for s in g.sensors)) for g in hw.groups))
        return (self._dark, i18n.get_language(), icons_visible(), custom_icons.maps_version(),
                tuple(hw_sig(h) for h in tree.hardware), tuple(hw_sig(h) for h in tree.modules))

    def _rebuild(self, tree: DetailTree) -> None:
        self.rebuild_count += 1
        bar = self.tree.verticalScrollBar()
        pos = bar.value()
        cur = self.tree.currentItem()
        cur_id = cur.data(0, ROLE_ID) if cur is not None else None
        self._programmatic = self._updating = True
        try:
            self.tree.clear()
            self._items, self._nodes, self._node_keys, self._cache = {}, {}, {}, {}
            every = [s for hw in (*tree.hardware, *tree.modules)
                     for s in (*hw.loose, *(x for g in hw.groups for x in g.sensors))]
            self._a11y = dict(zip((s.key for s in every), uniquify([sensor_a11y_name(s) for s in every])))
            for hw in tree.hardware:
                self._add_hardware(self.tree.invisibleRootItem(), hw)
            if tree.modules:
                root = self._add_node(self.tree.invisibleRootItem(), "modules", MODULES_ID,
                                      i18n.tr("hw_dimms", n=len(tree.modules)), "ram", "", "")
                keys: list[str] = []
                for hw in tree.modules:
                    keys += self._add_hardware(root, hw)
                self._node_keys[MODULES_ID] = keys
            self.count.setText(i18n.tr("detail_count", n=len(self._items)))
            self._apply_expansion(inner=True)
        finally:
            self._programmatic = self._updating = False
        if cur_id is not None:
            item = self._items.get(cur_id)
            if item is None:
                item = self._nodes.get(cur_id)
            if item is not None:
                self.tree.setCurrentItem(item)
        bar.setValue(pos)
        self._apply_filter()

    def _add_node(self, parent: QTreeWidgetItem, kind: str, node_id: str, title: str, icon_kind: str,
                  custom: str | None, search: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [title, "", "", "", "", ""])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
        item.setData(0, ROLE_ID, node_id)
        item.setData(0, ROLE_KIND, kind)
        item.setData(0, ROLE_TEXT, (title + " " + search).lower())
        item.setToolTip(COL_NAME, title)
        if kind in ("hw", "modules") and icon_kind:
            pm = kind_or_custom_pixmap(icon_kind, section_header_color(self._dark),
                                       self.devicePixelRatioF() or 1.0, ICON_PX, custom)
            if pm is not None:
                item.setIcon(COL_NAME, QIcon(pm))
        item.setCheckState(COL_PIN, _UNCHECKED)
        tip = i18n.tr("detail_pin_group_tip", name=title)
        item.setToolTip(COL_PIN, tip)
        item.setData(COL_PIN, Qt.ItemDataRole.AccessibleTextRole, i18n.tr("detail_pinned_a11y", name=title))
        item.setData(COL_PIN, Qt.ItemDataRole.AccessibleDescriptionRole, tip)
        font = item.font(COL_NAME)
        font.setBold(True)
        item.setFont(COL_NAME, font)
        self._nodes[node_id] = item
        self._node_keys.setdefault(node_id, [])
        return item

    def _add_hardware(self, parent: QTreeWidgetItem, hw: DetailHardware) -> list[str]:
        key = section_title_key(hw.hw_class)
        title = i18n.tr(key) if key else (hw.hardware or hw_title(hw.hw_kind))
        hw_node_id = "hw:" + hw.hw_id
        item = self._add_node(parent, "hw", hw_node_id, title, hw.hw_kind,
                              custom_icons.file_for(hw.hw_kind, hw.hw_id),
                              hw.hardware + " " + hw_title(hw.hw_kind))
        keys: list[str] = []
        labels = display_labels(hw.loose)
        for s in hw.loose:
            self._add_leaf(item, s, labels[s.key])
            keys.append(s.key)
        for g in hw.groups:
            label = i18n.tr("grp_" + g.id, n=g.count)
            gid = f"grp:{hw.hw_id}:{g.id}"
            gitem = self._add_node(item, "grp", gid, label, "", None, "")
            gkeys = []
            labels = display_labels(g.sensors)
            for s in g.sensors:
                self._add_leaf(gitem, s, labels[s.key])
                gkeys.append(s.key)
            self._node_keys[gid] = gkeys
            keys += gkeys
        self._node_keys[hw_node_id] = keys
        return keys

    def _add_leaf(self, parent: QTreeWidgetItem, s: Sensor, label: str | None = None) -> None:
        shown = display_name(s)
        label = label or shown
        item = QTreeWidgetItem(parent, [label, "", "", "", "", ""])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
        item.setData(0, ROLE_ID, s.key)
        item.setData(0, ROLE_KIND, "sensor")
        item.setData(0, ROLE_TEXT, f"{shown} {s.name} {s.kind} {kind_label(s.kind)}".lower())   # both names
        full = self._a11y.get(s.key) or sensor_a11y_name(s, shown)
        item.setToolTip(COL_NAME, full if s.name == shown else full + "\n" + i18n.tr("sensor_orig_name", name=s.name))
        item.setData(COL_NAME, Qt.ItemDataRole.AccessibleTextRole, full)
        for col in (COL_VALUE, COL_MIN, COL_MAX):
            item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setCheckState(COL_PIN, _UNCHECKED)
        item.setCheckState(COL_SUMMARY, _UNCHECKED)
        pin_tip = i18n.tr("detail_pin_tip", name=full)
        sum_tip = i18n.tr("detail_summary_tip", name=full)
        item.setToolTip(COL_PIN, pin_tip)
        item.setToolTip(COL_SUMMARY, sum_tip)
        item.setData(COL_PIN, Qt.ItemDataRole.AccessibleTextRole, i18n.tr("detail_pinned_a11y", name=full))
        item.setData(COL_PIN, Qt.ItemDataRole.AccessibleDescriptionRole, pin_tip)
        item.setData(COL_SUMMARY, Qt.ItemDataRole.AccessibleTextRole, i18n.tr("detail_summary_a11y", name=full))
        item.setData(COL_SUMMARY, Qt.ItemDataRole.AccessibleDescriptionRole, sum_tip)
        self._items[s.key] = item

    # ------------------------------------------------------------------ in-place updates
    def _update_values(self) -> None:
        by_key = self._snapshot.by_key()
        sep, fahr, dark = i18n.decimal_sep(), self._fahrenheit, self._dark
        self._updating = True
        try:
            for key, item in self._items.items():
                s = by_key.get(key)
                if s is None:
                    continue
                band = band_for(s.kind, s.value)
                value = format_value(s.kind, s.value, fahr, sep)
                if band == "red":
                    value = "! " + value
                texts = (value, format_value(s.kind, s.min, fahr, sep), format_value(s.kind, s.max, fahr, sep),
                         band)
                old = self._cache.get(key)
                if old == texts:
                    continue
                self._cache[key] = texts
                for col, text in zip((COL_VALUE, COL_MIN, COL_MAX), texts):
                    if old is None or old[col - COL_VALUE] != text:
                        item.setText(col, text)
                if old is None or old[3] != band:
                    item.setForeground(COL_VALUE, QBrush(band_color(band, dark)))
                item.setData(COL_VALUE, Qt.ItemDataRole.AccessibleDescriptionRole,
                             value + (" · " + i18n.tr("critical") if band == "red" else ""))
        finally:
            self._updating = False

    @staticmethod
    def _set_state(item: QTreeWidgetItem, col: int, state: Qt.CheckState) -> None:
        if item.checkState(col) != state:
            item.setCheckState(col, state)

    def _refresh_pins(self) -> None:
        covered = covered_keys(self._pinned, self._snapshot)
        self._updating = True
        try:
            for key, item in self._items.items():
                self._set_state(item, COL_PIN, _CHECKED if key in covered else _UNCHECKED)
            for node_id, item in self._nodes.items():
                self._set_state(item, COL_PIN, _BULK_TO_QT[bulk_state_of(self._node_keys.get(node_id, []), covered)])
        finally:
            self._updating = False

    def _refresh_summary(self) -> None:
        by_key = self._snapshot.by_key()
        self._updating = True
        try:
            hidden_hw = self._prefs.hidden_hardware
            for key, item in self._items.items():
                s = by_key.get(key)
                if s is None:
                    continue
                hidden = s.hw_id in hidden_hw
                self._set_state(item, COL_SUMMARY,
                                _CHECKED if not hidden and in_summary(s, self._prefs) else _UNCHECKED)
                if bool(item.data(COL_SUMMARY, ROLE_LOCKED)) != hidden:
                    item.setData(COL_SUMMARY, ROLE_LOCKED, hidden)
                    full = self._a11y.get(key) or sensor_a11y_name(s)
                    tip = i18n.tr("detail_summary_hidden_tip") if hidden else i18n.tr("detail_summary_tip", name=full)
                    item.setToolTip(COL_SUMMARY, tip)
                    item.setData(COL_SUMMARY, Qt.ItemDataRole.AccessibleDescriptionRole, tip)
        finally:
            self._updating = False

    # ------------------------------------------------------------------ user input
    def _sensors_of(self, node_id: str) -> list[Sensor]:
        by_key = self._snapshot.by_key()
        return [by_key[k] for k in self._node_keys.get(node_id, ()) if k in by_key]

    def _on_item_changed(self, item: QTreeWidgetItem, col: int) -> None:
        if self._updating or col not in (COL_PIN, COL_SUMMARY):
            return
        if item.data(col, ROLE_LOCKED):
            return
        kind, node_id = item.data(0, ROLE_KIND), item.data(0, ROLE_ID)
        if col == COL_PIN:
            if kind == "sensor":
                s = self._snapshot.by_key().get(node_id)
                if s is not None:
                    self.pin_toggled.emit(s)
            else:
                sensors = self._sensors_of(node_id)
                if sensors:
                    self.bulk_pin_toggled.emit(sensors)
            self._refresh_pins()            # the displayed state is always the real one
        elif kind == "sensor":
            self.summary_override_changed.emit(node_id, item.checkState(COL_SUMMARY) == _CHECKED)
            self._refresh_summary()

    def _on_expanded(self, item: QTreeWidgetItem, expanded: bool) -> None:
        if self._programmatic or self.search.text().strip():
            return
        node_id, kind = item.data(0, ROLE_ID), item.data(0, ROLE_KIND)
        if expanded != default_expanded(kind):
            self._toggled.add(node_id)
        else:
            self._toggled.discard(node_id)
        self.toggled_changed.emit(sorted(self._toggled))

    def _expansion_wanted(self, node_id: str, kind: str) -> bool:
        return default_expanded(kind) != (node_id in self._toggled)

    def _apply_expansion(self, inner: bool = False) -> None:
        """Expand / collapse every node from the default plus the saved differences."""
        was = self._programmatic
        self._programmatic = True
        try:
            for node_id, item in self._nodes.items():
                item.setExpanded(self._expansion_wanted(node_id, item.data(0, ROLE_KIND)))
        finally:
            self._programmatic = was if inner else False

    def _expand_all(self, expand: bool) -> None:
        """Expand / collapse every node; remembered like a manual change unless a search is active."""
        filtering = bool(self.search.text().strip())
        self._programmatic = True
        try:
            for item in self._nodes.values():
                if not item.isHidden():
                    item.setExpanded(expand)
        finally:
            self._programmatic = False
        if filtering:
            return
        self._toggled = {nid for nid, it in self._nodes.items()
                         if it.isExpanded() != default_expanded(it.data(0, ROLE_KIND))}
        self.toggled_changed.emit(sorted(self._toggled))

    # ------------------------------------------------------------------ search
    def _apply_filter(self) -> None:
        query = self.search.text().strip().lower()
        root = self.tree.invisibleRootItem()
        self._programmatic = True
        try:
            if not query:
                self._unhide(root)
                self._apply_expansion(inner=True)
                visible = root.childCount() > 0
            else:
                visible = False
                for i in range(root.childCount()):
                    v, _own, _inside = self._filter(root.child(i), query, False)
                    visible = visible or v
        finally:
            self._programmatic = False
        if visible:
            self.stack.setCurrentWidget(self.tree)
        elif self.search.text().strip():
            self._show_message(i18n.tr("detail_no_results"))
        else:
            self._show_message(i18n.tr("empty"))

    def _unhide(self, item: QTreeWidgetItem) -> None:
        for i in range(item.childCount()):
            child = item.child(i)
            child.setHidden(False)
            self._unhide(child)

    def _filter(self, item: QTreeWidgetItem, query: str, ancestor_match: bool) -> tuple[bool, bool, bool]:
        """-> (visible, matched itself, a descendant matched itself). Matching an ancestor shows the subtree."""
        own = query in item.data(0, ROLE_TEXT)
        match = ancestor_match or own
        any_visible = inside = False
        for i in range(item.childCount()):
            v, child_own, child_inside = self._filter(item.child(i), query, match)
            any_visible = any_visible or v
            inside = inside or child_own or child_inside
        visible = match or any_visible
        item.setHidden(not visible)
        if item.childCount() and visible:
            if inside or (own and item.data(0, ROLE_KIND) in ("hw", "modules")):
                item.setExpanded(True)
        return visible, own, inside

    def _show_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.setAccessibleName(text)
        self.stack.setCurrentWidget(self.message)

    # ------------------------------------------------------------------ chrome
    def _apply_texts(self) -> None:
        self.setWindowTitle(i18n.tr("detail_title"))
        self.setAccessibleName(i18n.tr("detail_title"))
        self.search.setPlaceholderText(i18n.tr("detail_search_ph"))
        self.search.setAccessibleName(i18n.tr("detail_search_name"))
        self.tree.setAccessibleName(i18n.tr("detail_tree_name"))
        self.tree.setHeaderLabels([i18n.tr(k) for k in _COL_KEYS])
        for col in (COL_VALUE, COL_MIN, COL_MAX, COL_PIN, COL_SUMMARY):
            self.tree.headerItem().setTextAlignment(col, Qt.AlignmentFlag.AlignCenter)
        self.expand_btn.setText(i18n.tr("detail_expand_all"))
        self.expand_btn.setAccessibleName(i18n.tr("detail_expand_all"))
        self.collapse_btn.setText(i18n.tr("detail_collapse_all"))
        self.collapse_btn.setAccessibleName(i18n.tr("detail_collapse_all"))
        self.hint.setText(i18n.tr("detail_hint"))
        self.hint.setAccessibleName(i18n.tr("detail_hint"))
        self.count.setText(i18n.tr("detail_count", n=len(self._items)))

    def _apply_style(self) -> None:
        dark = self._dark
        base = QColor(*TASKBAR_BASE[dark])
        text = text_color(dark)
        edge = QColor(70, 70, 70) if dark else QColor(190, 190, 190)
        field = QColor(44, 44, 44) if dark else QColor(255, 255, 255)
        sel = QColor(60, 90, 140) if dark else QColor(200, 220, 245)
        hover = "rgba(255,255,255,26)" if dark else "rgba(0,0,0,18)"
        focus = band_color("amber", dark).name()
        muted = QColor(170, 170, 170) if dark else QColor(90, 90, 90)
        handle = "rgba(255,255,255,90)" if dark else "rgba(0,0,0,90)"
        self.setStyleSheet(
            f"QWidget#DetailWindow {{ background: {base.name()}; }}"
            f"QLabel {{ background: transparent; color: {text.name()}; }}"
            f"QLabel#detailHint, QLabel#detailCount {{ color: {muted.name()}; }}"
            f"QLabel#detailNotice {{ color: {band_color('amber', dark).name()}; }}"
            f"QLineEdit {{ background: {field.name()}; color: {text.name()}; border: 1px solid {edge.name()}; "
            f"border-radius: 5px; padding: 5px 8px; }}"
            f"QLineEdit:focus {{ border: 2px solid {focus}; padding: 4px 7px; }}"
            f"QPushButton {{ background: transparent; color: {text.name()}; border: 1px solid {edge.name()}; "
            f"border-radius: 5px; padding: 5px 10px; min-height: 18px; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
            f"QPushButton:focus {{ border: 2px solid {focus}; padding: 4px 9px; }}"
            f"QTreeWidget {{ background: {field.name()}; color: {text.name()}; border: 1px solid {edge.name()}; "
            f"border-radius: 5px; outline: 0; }}"
            f"QTreeWidget::item {{ padding: 3px 2px; }}"
            f"QTreeWidget::item:hover {{ background: {hover}; }}"
            f"QTreeWidget::item:selected {{ background: {sel.name()}; color: {text.name()}; }}"
            f"QTreeWidget:focus {{ border: 2px solid {focus}; }}"
            f"QHeaderView::section {{ background: {base.name()}; color: {muted.name()}; border: 0; "
            f"border-bottom: 1px solid {edge.name()}; padding: 5px 6px; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {handle}; min-height: 24px; border-radius: 4px; "
            f"margin: 1px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}"
            f"QToolTip {{ background: {base.name()}; color: {text.name()}; border: 1px solid {edge.name()}; "
            f"padding: 3px; }}")

    # ------------------------------------------------------------------ window events
    def _place_on_screen(self) -> None:
        """Keep a restored position that is on a screen; otherwise centre on the primary screen."""
        if QGuiApplication.screenAt(self.geometry().center()) is None:
            s = QGuiApplication.primaryScreen()
            if s is not None:
                g = s.availableGeometry()
                self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def _emit_geometry(self) -> None:
        if self._geometry_pending:
            self._geometry_pending = False
            self.geometry_changed.emit(self.current_geometry())

    def _schedule_geometry(self) -> None:
        if self.isVisible():
            self._geometry_pending = True
            self._geo_timer.start()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._schedule_geometry()

    def moveEvent(self, ev) -> None:
        super().moveEvent(ev)
        self._schedule_geometry()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if self._dirty:
            self._apply_snapshot()
            self._refresh_pins()

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._geo_timer.stop()
        self._emit_geometry()

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            if self.search.text():
                self.search.clear()
                self.search.setFocus()
            else:
                self.close()
            return
        super().keyPressEvent(ev)
