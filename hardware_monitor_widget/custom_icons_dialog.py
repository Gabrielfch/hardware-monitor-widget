"""Dialog to pick custom icon images per hardware kind, per hardware and per sensor."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from . import custom_icons, strings_icons
from .dialogs import app_dialog_guard
from .models import HW_KINDS

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.ico *.gif *.webp *.svg)"
_PREVIEW = 28


class _Preview(QWidget):
    """Draws the default glyph of a hardware kind, or the custom image when one is set."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._file: str | None = None
        self.setFixedSize(QSize(_PREVIEW, _PREVIEW))

    def set_file(self, filename: str | None) -> None:
        self._file = filename
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if not (self._file and custom_icons.draw_custom_icon(p, rect, self._file)):
            try:
                from .icons import draw_kind_icon
                draw_kind_icon(p, self._kind, rect, self.palette().windowText().color())
            except Exception:
                pass
        p.end()


class CustomIconsDialog(QDialog):
    """`hw_entries` / `sensor_entries`: iterables of ``(opaque key, display name, hardware kind)``."""

    def __init__(self, by_kind: dict, by_hw: dict, by_sensor: dict, hw_entries=(), sensor_entries=(),
                 tr=strings_icons.tr, parent=None):
        super().__init__(parent)
        self._tr = tr
        self._by_kind = self._clean(by_kind)
        self._by_hw = self._clean(by_hw)
        self._by_sensor = self._clean(by_sensor)
        self.setWindowTitle(tr("custom_icons_title"))
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        self._notice = QLabel()
        self._notice.setWordWrap(True)
        blocker = custom_icons.import_blocker()
        if blocker:
            self._notice.setText(tr("custom_icons_need_admin" if blocker == custom_icons.ERR_NOT_ELEVATED
                                    else "custom_icons_untrusted_dir"))
        self._notice.setVisible(bool(blocker))
        lay.addWidget(self._notice)
        tabs = QTabWidget(self)
        lay.addWidget(tabs)
        self._rows: list[tuple[dict, str, _Preview, QPushButton]] = []
        tabs.addTab(self._build_tab(self._by_kind, [(k, tr("hw_" + k), k) for k in HW_KINDS]),
                    tr("custom_icons_tab_kind"))
        tabs.addTab(self._build_tab(self._by_hw, list(hw_entries or [])), tr("custom_icons_tab_hw"))
        tabs.addTab(self._build_tab(self._by_sensor, list(sensor_entries or [])), tr("custom_icons_tab_sensor"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @staticmethod
    def _clean(m) -> dict:
        return {k: v for k, v in (m or {}).items() if isinstance(k, str) and custom_icons.safe_name(v)}

    def _build_tab(self, target: dict, entries) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        col = QVBoxLayout(body)
        for key, label, kind in entries:
            row = QHBoxLayout()
            default = _Preview(kind)
            default.setAccessibleName(f"{label} - {self._tr('custom_icons_default')}")
            row.addWidget(default)
            name = QLabel(label)
            name.setMinimumWidth(140)
            row.addWidget(name, 1)
            custom = _Preview(kind)
            custom.setAccessibleName(f"{label} - {self._tr('custom_icons_custom')}")
            custom.set_file(target.get(key))
            row.addWidget(custom)
            choose = QPushButton(self._tr("custom_icons_choose"))
            remove = QPushButton(self._tr("custom_icons_remove"))
            choose.setAccessibleName(f"{self._tr('custom_icons_choose')}: {label}")
            remove.setAccessibleName(f"{self._tr('custom_icons_remove')}: {label}")
            name.setBuddy(choose)
            remove.setEnabled(key in target)
            choose.clicked.connect(lambda _=False, t=target, k=key, c=custom, r=remove: self._choose(t, k, c, r))
            remove.clicked.connect(lambda _=False, t=target, k=key, c=custom, r=remove: self._remove(t, k, c, r))
            row.addWidget(choose)
            row.addWidget(remove)
            col.addLayout(row)
            self._rows.append((target, key, custom, remove))
        col.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _total(self) -> int:
        return len(self._by_kind) + len(self._by_hw) + len(self._by_sensor)

    def _warn(self, code: str) -> None:
        with app_dialog_guard():
            QMessageBox.warning(self, self._tr("custom_icons_title"), self._tr(code))

    def _choose(self, target: dict, key: str, preview: _Preview, remove: QPushButton) -> None:
        blocked = custom_icons.import_blocker()
        if blocked:
            self._warn(blocked)
            return
        if key not in target and self._total() >= custom_icons.MAX_FILES:
            self._warn("custom_icons_limit")
            return
        with app_dialog_guard():
            path, _ = QFileDialog.getOpenFileName(self, self._tr("custom_icons_choose"), "", IMAGE_FILTER)
        if not path:
            return
        try:
            name = custom_icons.import_image_busy(path)
        except custom_icons.IconImportError as e:
            self._warn(e.code)
            return
        target[key] = name
        preview.set_file(name)
        remove.setEnabled(True)

    def _remove(self, target: dict, key: str, preview: _Preview, remove: QPushButton) -> None:
        target.pop(key, None)
        preview.set_file(None)
        remove.setEnabled(False)

    def result_maps(self) -> tuple[dict, dict, dict]:
        return dict(self._by_kind), dict(self._by_hw), dict(self._by_sensor)
