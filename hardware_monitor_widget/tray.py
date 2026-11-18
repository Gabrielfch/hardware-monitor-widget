"""System tray icon (CPU chip with the CPU temperature). No native menu: any click opens the panel."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QSystemTrayIcon

from .colors import band_color
from .i18n import tr
from .icons import make_tray_icon


class TrayController(QObject):
    open_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dark = True
        self._text = ""
        self._band = "grey"
        self._stale = False
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip(tr("app_name"))
        # No context menu: on Windows the right click then arrives as `Context`, handled like the left click.
        self._tray.activated.connect(self._on_activated)
        self._update_icon()

    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def geometry(self):
        return self._tray.geometry()

    def set_icon_state(self, text: str, band: str, dark: bool, stale: bool = False) -> None:
        """`stale`: the values are the last known ones; the icon turns grey."""
        if (text, band, dark, stale) != (self._text, self._band, self._dark, self._stale):
            self._text, self._band, self._dark, self._stale = text, band, dark, stale
            self._update_icon()

    def retranslate(self) -> None:
        self._update_icon()

    def notify(self, title: str, body: str, info: bool = True) -> None:
        icon = QSystemTrayIcon.MessageIcon.Information if info else QSystemTrayIcon.MessageIcon.Warning
        self._tray.showMessage(title, body, icon, 8000)

    def _update_icon(self) -> None:
        accent = band_color(self._band, self._dark) if self._band != "grey" else None
        self._tray.setIcon(make_tray_icon(self._text, self._dark, accent, muted=self._stale))
        parts = [tr("app_name")]
        if self._text:
            parts.append(self._text)
        if self._stale:
            parts.append(tr("stale_tip"))
        self._tray.setToolTip(" · ".join(parts))

    def _on_activated(self, reason) -> None:
        R = QSystemTrayIcon.ActivationReason
        if reason in (R.Trigger, R.Context):
            self.open_requested.emit()
