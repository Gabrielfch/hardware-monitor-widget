"""Application wiring: monitor -> pills / panel / tray."""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QPoint, QRect, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from . import custom_icons, i18n, startup, taskbar
from .appearance import apply_appearance, apply_color_scheme
from .config import APP_DIR_NAME, Config, is_reparse_point
from .curation import Prefs, toggle_override
from .detail_window import DetailWindow
from .models import Snapshot, band_for, is_stale
from .panel import HardwarePanel
from .pins import MAX_PINS, resolve_auto, toggle_bulk, toggle_pin_result
from .placement import PlacementSettings
from .settings_actions import SettingsActions, UpdateController
from .settings_model import build_settings_model
from .tray import TrayController
from .widget import TaskbarWidget, warning_text

log = logging.getLogger(__name__)

THEME_REFRESH_MS = 5000
SETTINGS_REFRESH_MS = 250       # settings page refresh at most 4 times per second
PANEL_REOPEN_GRACE_S = 0.3


def local_app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_DIR_NAME


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    base = local_app_dir()
    d = base / "logs"
    try:
        if is_reparse_point(base) or is_reparse_point(d):
            # a junction/symlink here could redirect our writes somewhere else
            log.warning("log directory is a link/junction; file logging disabled")
            return
        d.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(d / "app.log", maxBytes=512_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
    except OSError:
        pass


def install_excepthooks() -> None:
    """Route uncaught exceptions to the log (under pythonw there is no stderr)."""
    def hook(exc_type, exc, tb) -> None:
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_hook(args) -> None:
        name = args.thread.name if args.thread is not None else "?"
        log.critical("uncaught exception in thread %s", name,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thread_hook


def apply_log_level(cfg) -> None:
    """Root logger level from the config: DEBUG when "Debug level" is on, else INFO (applies live)."""
    logging.getLogger().setLevel(logging.DEBUG if cfg.get("log_debug") else logging.INFO)


class Controller(QObject):
    """Owns the widgets and routes signals between them, the config and the (lazily imported) sensor monitor."""

    def __init__(self, app: QApplication, cfg: Config, monitor=None):
        super().__init__(app)
        self.app, self.cfg, self.monitor = app, cfg, monitor
        self.snapshot = Snapshot()
        self.lock: QLockFile | None = None   # single-instance lock, released before a relaunch
        self.warned: set[str] = set()        # one-per-session notices
        self.detail: DetailWindow | None = None
        apply_appearance(cfg)                # palette, icon style, language, colour scheme
        self._apply_icon_maps()
        app.setApplicationName(i18n.tr("app_name"))
        self.widget = TaskbarWidget(cfg.get("pinned"))
        self.panel = HardwarePanel(dark=self._dark())
        self.tray = TrayController()
        self._icon_text = ""
        self._taskbar_light = taskbar.taskbar_uses_light_theme()     # cached; refreshed by sync_theme
        self.widget.set_theme(cfg.get("theme"))
        self.widget.set_fahrenheit(bool(cfg.get("fahrenheit")))
        self.widget.set_ui_scale(cfg.get("ui_scale"))
        self.widget.set_grouped(bool(cfg.get("group_pills")))
        self.widget.set_icon_resolver(custom_icons.file_for)
        self.widget.set_placement_settings(self._placement())
        p = self.panel
        p.set_fahrenheit(bool(cfg.get("fahrenheit")))
        p.set_pinned(cfg.get("pinned"))
        p.set_collapsed(cfg.get("collapsed"))
        p.set_metrics(cfg.get("panel_size"), cfg.get("ui_scale"))
        p.set_prefs(self._prefs())

        self.updates = UpdateController(cfg, self)
        self.actions = SettingsActions(self, self.updates, local_app_dir() / "logs")
        self.settings_model = build_settings_model(self.actions.callbacks())
        p.set_settings_model(self.settings_model)
        self._refresh_pending = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(SETTINGS_REFRESH_MS)
        self._refresh_timer.timeout.connect(self._refresh_tick)
        self.updates.state_changed.connect(self.request_settings_refresh)
        self.updates.quit_requested.connect(self.quit)
        self.updates.notify_requested.connect(lambda title, body: self.tray.notify(title, body))

        w = self.widget
        w.clicked.connect(lambda: self.open_panel())
        w.context_menu_requested.connect(lambda _pos: self.open_panel())     # right click = left click
        w.floating_moved.connect(self.on_floating_moved)
        w.monitor_changed.connect(lambda key: self._persist("placement_monitor", key))
        w.fullscreen_placement_changed.connect(lambda v: self._persist("fullscreen_placement", v))
        w.placement_warning.connect(self.on_placement_warning)
        self.tray.open_requested.connect(lambda: self.open_panel())
        p.pin_toggled.connect(self.on_pin_toggled)
        p.bulk_pin_requested.connect(self.on_bulk_requested)
        p.detail_requested.connect(self.open_detail)
        p.settings_requested.connect(lambda: self.open_panel("root"))
        p.display_settings_requested.connect(lambda: self.open_panel("display", direct=True))
        p.refresh_requested.connect(self.refresh)
        p.theme_toggle_requested.connect(lambda: self.set_theme("light" if p.dark else "dark"))
        p.fahrenheit_toggled.connect(self.set_fahrenheit)
        p.collapsed_changed.connect(lambda ids: self._persist("collapsed", list(ids)))
        p.restart_admin_requested.connect(self.restart_as_admin)
        p.retry_requested.connect(self.refresh)
        p.quit_requested.connect(self.quit)
        if monitor is not None:
            monitor.snapshot_ready.connect(self.on_snapshot)
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(THEME_REFRESH_MS)
        self._theme_timer.timeout.connect(self.sync_theme)

    # ---- config -> view models
    def _prefs(self) -> Prefs:
        c = self.cfg
        return Prefs(frozenset(c.get("hidden_hardware")), frozenset(c.get("summary_categories")),
                     dict(c.get("summary_overrides")))

    def _placement(self) -> PlacementSettings:
        c = self.cfg
        return PlacementSettings(
            mode=c.get("placement_mode"), anchor=c.get("taskbar_anchor"), monitor=c.get("placement_monitor"),
            floating_positions=c.get("floating_positions"), show_in_fullscreen=bool(c.get("show_in_fullscreen")),
            fullscreen_placement=c.get("fullscreen_placement"),
            fullscreen_positions=c.get("fullscreen_floating_positions"))

    def _persist(self, key: str, value) -> bool:
        try:
            self.cfg.set(key, value)
            return True
        except ValueError:
            log.warning("config rejected %s", key)
            return False

    def notify(self, text: str) -> None:
        self.tray.notify(i18n.tr("app_name"), text)

    def request_settings_refresh(self) -> None:
        """Refresh the open Settings page, coalesced to at most 4 Hz (download progress, moves)."""
        if not self.panel.in_settings:
            return
        if self._refresh_timer.isActive():
            self._refresh_pending = True
            return
        self.panel.refresh_settings()
        self._refresh_timer.start()

    def _refresh_tick(self) -> None:
        if self._refresh_pending:
            self._refresh_pending = False
            if self.panel.in_settings:
                self.panel.refresh_settings()
                self._refresh_timer.start()

    # ---- lifecycle
    def start(self) -> None:
        if self.tray.available():
            self.tray.show()
        self.widget.start()
        self._theme_timer.start()
        self.updates.start_auto()
        if self.monitor is not None:
            self.monitor.start()

    def quit(self) -> None:
        self.updates.cancel()
        if self.monitor is not None:
            self.monitor.stop()
        self.widget.stop()
        self.panel.hide()
        if self.detail is not None:
            self.detail.close()
        self.tray.hide()
        self.app.quit()

    # ---- data
    def on_snapshot(self, snapshot) -> None:
        self.snapshot = snapshot
        self.widget.set_snapshot(snapshot)
        if self.panel.isVisible():
            self.panel.set_snapshot(snapshot)
        if self.detail is not None:
            self.detail.set_snapshot(snapshot)      # only touches the tree while the window is visible
        s = resolve_auto("auto:cpu_temp", snapshot)
        text = ""
        if s is not None and s.value is not None:
            v = s.value * 9 / 5 + 32 if self.cfg.get("fahrenheit") else s.value
            text = f"{v:.0f}"
        band = band_for("temperature", s.value) if s else "grey"
        self.tray.set_icon_state(text, band, not self._taskbar_light,
                                 stale=is_stale(snapshot))

    def refresh(self) -> None:
        if self.monitor is not None:
            self.monitor.refresh()

    def restart_as_admin(self) -> None:
        from . import elevation
        if elevation.relaunch_as_admin():
            # free the instance lock first so the elevated copy can take it, then shut down
            if self.lock is not None:
                self.lock.unlock()
            self.quit()

    # ---- panel
    def open_panel(self, page: str | None = None, direct: bool = False) -> None:
        """Toggle the panel (summary). With `page` open Settings on that page instead (also when already open)."""
        p = self.panel
        if p.isVisible():
            if page is None:
                p.hide()
            else:
                self.actions.invalidate_caches()
                p.show_settings(page, direct)
            return
        if p.hidden_at and time.monotonic() - p.hidden_at < PANEL_REOPEN_GRACE_S:
            return      # this click is the one that closed it (focus loss)
        p.set_snapshot(self.snapshot)
        p.set_pinned(self.cfg.get("pinned"))
        if page is not None:
            self.actions.invalidate_caches()
            p.show_settings(page, direct)
        if self.widget.isVisible():
            anchor = QRect(self.widget.mapToGlobal(QPoint(0, 0)), self.widget.size())
        else:
            g = self.tray.geometry()
            anchor = g if g.isValid() and not g.isEmpty() else QCursor.pos()
        p.show_at(anchor)

    # ---- detail window (one instance, created on first use)
    def _ensure_detail(self) -> DetailWindow:
        if self.detail is None:
            d = DetailWindow(dark=self._dark())
            d.set_fahrenheit(bool(self.cfg.get("fahrenheit")))
            d.set_pinned(self.cfg.get("pinned"))
            d.set_prefs(self._prefs())
            d.set_toggled(self.cfg.get("detail_toggled"))
            d.restore_geometry(self.cfg.get("detail_geometry"))
            d.pin_toggled.connect(self.on_pin_toggled)
            d.bulk_pin_toggled.connect(self.on_bulk_sensors)
            d.summary_override_changed.connect(self.on_override_changed)
            d.toggled_changed.connect(lambda ids: self._persist("detail_toggled", list(ids)))
            d.geometry_changed.connect(lambda geo: self._persist("detail_geometry", dict(geo)))
            self.detail = d
        return self.detail

    def open_detail(self, hw_id: str = "") -> None:
        d = self._ensure_detail()
        d.set_snapshot(self.snapshot)
        self.panel.hide()
        d.show_for(hw_id or None)

    # ---- pins and summary overrides
    def _set_pins(self, pins: list[str]) -> bool:
        if not self._persist("pinned", pins):
            return False
        self.widget.set_pinned(pins)
        self.panel.set_pinned(pins)
        if self.detail is not None:
            self.detail.set_pinned(pins)
        return True

    def on_pin_toggled(self, sensor) -> None:
        pins, rejected = toggle_pin_result(list(self.cfg.get("pinned")), sensor, self.snapshot)
        if rejected:
            self.notify(i18n.tr("integ_pin_limit", n=MAX_PINS))
        elif not self._set_pins(pins):
            self.notify(i18n.tr("integ_save_error"))

    def _bulk(self, sensors) -> int | None:
        """Pin/unpin `sensors`; returns how many were not added, or None when the pins could not be saved."""
        pins, skipped = toggle_bulk(list(self.cfg.get("pinned")), list(sensors), self.snapshot, MAX_PINS)
        if not self._set_pins(pins):
            return None
        return skipped

    def on_bulk_requested(self, hw_id: str) -> None:
        skipped = self._bulk(self.panel.summary_sensors(hw_id))
        if skipped is None:
            self.panel.set_bulk_notice(hw_id, 0)
            self.notify(i18n.tr("integ_save_error"))
            return
        self.panel.set_bulk_notice(hw_id, skipped)

    def on_bulk_sensors(self, sensors) -> None:
        skipped = self._bulk(sensors)
        if skipped is None:
            if self.detail is not None:
                self.detail.set_notice(i18n.tr("integ_save_error"))
            return
        if self.detail is not None:
            self.detail.set_notice(i18n.tr("bulk_skipped", n=skipped) if skipped else "")

    def on_override_changed(self, key: str, want: bool) -> None:
        sensor = self.snapshot.by_key().get(key)
        if sensor is None:
            return
        overrides = toggle_override(self.cfg.get("summary_overrides"), sensor, bool(want), self._prefs())
        if self._persist("summary_overrides", overrides):
            self.prefs_changed()
        else:
            self.notify(i18n.tr("integ_save_error"))

    def prefs_changed(self) -> None:
        """Hidden hardware, categories or overrides changed."""
        prefs = self._prefs()
        self.panel.set_prefs(prefs)
        if self.detail is not None:
            self.detail.set_prefs(prefs)
        self.panel.refresh_settings()

    # ---- placement
    def on_floating_moved(self, key: str, entry: dict, fullscreen: bool) -> None:
        name = "fullscreen_floating_positions" if fullscreen else "floating_positions"
        positions = dict(self.cfg.get(name))
        positions[key] = dict(entry)
        # the widget emits floating_moved before monitor_changed: use its final monitor choice
        if not fullscreen and self.widget.placement_settings().monitor == "primary":
            positions.pop("primary", None)
        self._persist(name, positions)
        self.actions.invalidate_caches()
        self.request_settings_refresh()

    def on_placement_warning(self, reason: str) -> None:
        text = warning_text(reason)
        if text and reason not in self.warned:
            self.warned.add(reason)
            self.notify(text)

    def placement_changed(self) -> None:
        self.actions.invalidate_caches()
        self.widget.set_placement_settings(self._placement())
        self.panel.refresh_settings()

    # ---- appearance
    def _apply_icon_maps(self) -> None:
        c = self.cfg
        custom_icons.set_maps(c.get("custom_icons_kind"), c.get("custom_icons_hw"), c.get("custom_icons_sensor"))

    def _redraw(self) -> None:
        self.widget.refresh_style()
        self.panel.refresh_appearance()
        if self.detail is not None:
            self.detail.retranslate()       # rebuilds the tree with the new colours / icons
        self.tray.retranslate()

    def appearance_changed(self) -> None:
        """Palette, icon style or colour scheme changed."""
        apply_appearance(self.cfg)
        self._redraw()

    def icons_changed(self) -> None:
        self._apply_icon_maps()
        self._redraw()

    def metrics_changed(self) -> None:
        self.panel.set_metrics(self.cfg.get("panel_size"), self.cfg.get("ui_scale"))
        self.widget.set_ui_scale(self.cfg.get("ui_scale"))
        self.panel.refresh_settings()

    def language_changed(self) -> None:
        i18n.apply_language_setting(self.cfg.get("language"))
        self.app.setApplicationName(i18n.tr("app_name"))
        self.panel.retranslate()
        if self.detail is not None:
            self.detail.retranslate()
        self.tray.retranslate()
        self.widget.set_snapshot(self.snapshot)     # pill texts and tooltips are rebuilt

    def apply_log_level(self) -> None:
        apply_log_level(self.cfg)

    # ---- simple settings
    def set_fahrenheit(self, value: bool) -> None:
        if not self._persist("fahrenheit", bool(value)):
            self.notify(i18n.tr("integ_save_error"))
            self.panel.refresh_settings()       # put the control back to the real value
            return
        self.widget.set_fahrenheit(bool(value))
        self.panel.set_fahrenheit(bool(value))
        if self.detail is not None:
            self.detail.set_fahrenheit(bool(value))
        self.panel.refresh_settings()

    def set_startup(self, value: bool) -> None:
        self.actions.set_startup(value)
        self.panel.refresh_settings()

    def set_theme(self, theme: str) -> None:
        if not self._persist("theme", theme):
            return
        self.widget.set_theme(theme)
        apply_color_scheme(theme)
        self.panel.set_dark(self._dark())
        if self.detail is not None:
            self.detail.set_dark(self._dark())
        self.panel.refresh_settings()

    def _dark(self) -> bool:
        theme = self.cfg.get("theme")
        if theme in ("light", "dark"):
            return theme == "dark"
        return not taskbar.taskbar_uses_light_theme()

    def sync_theme(self) -> None:
        self._taskbar_light = taskbar.taskbar_uses_light_theme()
        self.widget.set_light_theme(self._taskbar_light)
        self.panel.set_dark(self._dark())
        if self.detail is not None:
            self.detail.set_dark(self._dark())


def _startup_command(argv: list[str]) -> int | None:
    """``--enable-startup`` / ``--disable-startup``: change the start-with-Windows setting and exit (no UI).
    Used by the installer. Returns the exit code, or None when neither flag is present."""
    for flag, value in (("--enable-startup", True), ("--disable-startup", False)):
        if flag in argv:
            try:
                startup.set_enabled(value)
            except OSError:
                return 1
            return 0
    return None


def main() -> int:
    argv = sys.argv
    code = _startup_command(argv)
    if code is not None:
        return code
    elevated = "--elevated" in argv
    app = QApplication.instance() or QApplication([argv[0] if argv else "hardware_monitor_widget"])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(i18n.tr("app_name"))

    d = local_app_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    lock = QLockFile(str(d / "app.lock"))
    lock.setStaleLockTime(0)
    # The elevated copy waits for the previous instance to release the lock; a normal start does not wait.
    if not lock.tryLock(15000 if elevated else 0):
        return 0        # another instance is running: exit without any UAC prompt

    monitor = None
    try:
        # CPU temperatures (PawnIO) need admin: relaunch elevated once; if UAC is declined (or the
        # elevated copy still is not admin) we keep running unelevated and the panel shows the
        # "needs admin" card. --no-elevate skips this (development).
        from . import elevation
        if "--no-elevate" not in argv and not elevation.is_admin() and not elevated:
            lock.unlock()       # let the elevated copy take the lock
            if elevation.relaunch_as_admin():
                return 0
            if not lock.tryLock(0):
                return 0

        setup_logging()
        install_excepthooks()
        log.info("starting (admin=%s, relaunched=%s, frozen=%s)", elevation.is_admin(), elevated,
                 bool(getattr(sys, "frozen", False)))

        cfg = Config()
        apply_log_level(cfg)
        # imported lazily: the UI modules above must stay importable without the sensor backend
        from .monitor import HardwareMonitor
        monitor = HardwareMonitor(int(cfg.get("interval_ms")), app)
        controller = Controller(app, cfg, monitor)
        controller.lock = lock
        controller.start()
        return app.exec()
    finally:
        if monitor is not None:
            monitor.stop()
        lock.unlock()
