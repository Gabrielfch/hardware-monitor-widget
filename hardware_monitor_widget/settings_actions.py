"""What the Settings pages do: implements `SettingsCallbacks` on top of the config and the controller (ADR-7).

`UpdateController` runs update checks / downloads on background threads (the GUI never blocks) and exposes a
small state machine; `SettingsActions` is the only place (besides the controller) that writes the config.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from . import custom_icons, release_info, startup, updater
from .config import FLOATING_ANCHORS, is_reparse_point
from .curation import SUMMARY_CATEGORIES, Prefs, summary_sections
from .dialogs import app_dialog_guard
from .i18n import tr
from .models import Snapshot
from .placement import lookup_floating
from .settings_model import DEFAULT_FLOATING_ANCHOR, SettingsCallbacks

log = logging.getLogger(__name__)

AUTO_FIRST_DELAY_MS = 60_000
AUTO_TICK_MS = 6 * 3600 * 1000
STATE_IDLE, STATE_CHECKING, STATE_AVAILABLE = "idle", "checking", "available"
CACHE_TTL_S = 5.0     # startup / screens / taskbar lookups used by the Settings pages
STATE_UP_TO_DATE, STATE_DOWNLOADING, STATE_READY, STATE_ERROR = "up_to_date", "downloading", "ready", "error"


def open_path(path: Path) -> None:
    """Open a folder in Explorer or a file in Notepad (a .log usually has no associated program)."""
    try:
        if path.is_dir():
            subprocess.Popen(["explorer.exe", str(path)], close_fds=True)
        else:
            try:
                subprocess.Popen(["notepad.exe", str(path)], close_fds=True)
            except OSError:
                os.startfile(str(path))      # noqa: S606 - our own log file
    except OSError:
        log.warning("could not open %s", path.name)


def _ask(title: str, text: str) -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, text,
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    with app_dialog_guard():
        return box.exec() == QMessageBox.StandardButton.Yes


# ======================================================================= updates
class _Bridge(QObject):
    """Signals emitted from worker threads and delivered (queued) in the Qt thread."""
    checked = Signal(object)          # Release | None
    check_failed = Signal(str)        # i18n key
    progress = Signal(int, int)
    prepared = Signal(object)         # Path of the verified installer
    prepare_failed = Signal(str)
    launch_ready = Signal(object)
    launch_failed = Signal(str)


def _spawn(fn) -> threading.Thread:
    t = threading.Thread(target=fn, daemon=True, name="hmw-update")
    t.start()
    return t


class UpdateController(QObject):
    """idle / checking / available / up_to_date / downloading / ready / error. Never installs by itself."""

    state_changed = Signal()
    quit_requested = Signal()          # the installer was started: the app must close now
    notify_requested = Signal(str, str)  # title, body (automatic check found a release)

    def __init__(self, cfg, parent=None, confirm_install=None, confirm_launch=None):
        super().__init__(parent)
        self._cfg = cfg
        self.state = STATE_IDLE
        self.error_key = ""
        self.release: updater.Release | None = None
        self._busy = False
        self._silent = False
        self._auto = False          # the running check was started by the automatic timer
        self._checking = False
        self._pct = -1
        self._notified: str | None = None
        self._cancel = threading.Event()
        self._confirm_install = confirm_install or (
            lambda version: _ask(tr("upd_confirm_title"), tr("upd_confirm", version=version)))
        self._confirm_launch = confirm_launch or (
            lambda: _ask(tr("upd_confirm_title"), tr("integ_confirm_launch")))
        b = self._bridge = _Bridge(self)
        b.checked.connect(self._on_checked)
        b.check_failed.connect(self._on_check_failed)
        b.progress.connect(self._on_progress)
        b.prepared.connect(self._on_prepared)
        b.prepare_failed.connect(self._on_prepare_failed)
        b.launch_ready.connect(self._on_launch_ready)
        b.launch_failed.connect(self._on_prepare_failed)
        self._timer = QTimer(self)
        self._timer.setInterval(AUTO_TICK_MS)
        self._timer.timeout.connect(self.auto_tick)

    # ---- state
    def _set_state(self, state: str, error_key: str = "") -> None:
        self.state, self.error_key = state, error_key
        self.state_changed.emit()

    def text(self) -> str:
        v = self.release.version if self.release else ""
        if self.state == STATE_CHECKING:
            return tr("upd_checking")
        if self.state == STATE_UP_TO_DATE:
            return tr("upd_uptodate")
        if self.state == STATE_AVAILABLE:
            return tr("upd_new", version=v)
        if self.state == STATE_DOWNLOADING:
            text = tr("upd_downloading", version=v)
            return f"{text} {self._pct}%" if self._pct >= 0 else text
        if self.state == STATE_READY:
            return tr("upd_launching")
        if self.state == STATE_ERROR:
            return tr(self.error_key or "upd_err_network")
        return ""

    def snapshot(self) -> tuple[str, str]:
        return self.state, self.text()

    def can_install(self) -> bool:
        r = self.release
        return bool(r and self.state == STATE_AVAILABLE and updater.install_mode() == "installed" and r.can_install)

    @property
    def busy(self) -> bool:
        return self._busy

    # ---- check
    def check_now(self) -> None:
        if self._busy:
            if self._checking and self._silent:     # a background check is running: make it visible
                self._silent = False
                self._set_state(STATE_CHECKING)
            return
        if not updater.is_enabled():
            self._set_state(STATE_ERROR, "upd_err_disabled")      # no network at all
            return
        self._start_check(silent=False)

    def _start_check(self, silent: bool) -> None:
        self._busy, self._silent, self._auto, self._checking = True, silent, silent, True
        if not silent:
            self._set_state(STATE_CHECKING)
        bridge = self._bridge

        def work() -> None:
            try:
                bridge.checked.emit(updater.check())
            except updater.UpdateError as exc:
                log.info("update check failed (%s)", exc.key)
                bridge.check_failed.emit(exc.key)
            except Exception:  # noqa: BLE001
                log.info("update check failed (unexpected)")
                bridge.check_failed.emit("upd_err_network")
        _spawn(work)

    def _on_checked(self, rel) -> None:
        silent, self._busy, self._checking = self._silent, False, False
        if self._auto:
            updater.record_check(self._cfg)
        if rel is None:
            if not silent:
                self.release = None
                self._set_state(STATE_UP_TO_DATE)
            return
        self.release = rel
        self._set_state(STATE_AVAILABLE)
        if silent and self._notified != rel.version:
            self._notified = rel.version
            self.notify_requested.emit(tr("upd_notify_title"), tr("upd_notify_body", version=rel.version))

    def _on_check_failed(self, key: str) -> None:
        silent, self._busy, self._checking = self._silent, False, False
        if not silent:
            self._set_state(STATE_ERROR, key)

    # ---- automatic check (opt-in; only notifies)
    def start_auto(self) -> None:
        """First evaluation 60 s after start, then every 6 h. Does nothing (no network) when not configured."""
        if not release_info.is_configured():
            return
        _spawn(self._cleanup)
        QTimer.singleShot(AUTO_FIRST_DELAY_MS, self.auto_tick)
        self._timer.start()

    @staticmethod
    def _cleanup() -> None:
        try:
            n = updater.cleanup_old_updates()
            if n:
                log.info("removed %d stale update folder(s)", n)
        except Exception:  # noqa: BLE001
            log.info("stale update cleanup failed")

    def auto_tick(self) -> None:
        if self._busy or not updater.should_auto_check(self._cfg):
            return
        self._start_check(silent=True)

    # ---- install (explicit consent, installed mode only)
    def open_release_page(self) -> None:
        if self.release is not None:
            QDesktopServices.openUrl(QUrl(self.release.page_url))

    def install(self) -> None:
        rel = self.release
        if rel is None or self._busy:
            return
        if not self.can_install():
            self.open_release_page()
            return
        if not self._confirm_install(rel.version):
            return
        self._busy = True
        self._cancel.clear()
        self._pct = -1
        self._set_state(STATE_DOWNLOADING)
        bridge, cancel = self._bridge, self._cancel

        def work() -> None:
            try:
                bridge.prepared.emit(updater.prepare_update(rel, progress=bridge.progress.emit, cancel=cancel))
            except updater.UpdateError as exc:
                bridge.prepare_failed.emit(exc.key)
            except Exception:  # noqa: BLE001
                log.info("update download failed (unexpected)")
                bridge.prepare_failed.emit("upd_err_network")
        _spawn(work)

    def cancel(self) -> None:
        self._cancel.set()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            pct = min(100, done * 100 // total)
            if pct != self._pct and self.state == STATE_DOWNLOADING:
                self._pct = pct
                self.state_changed.emit()

    def _fail(self, key: str) -> None:
        self._busy = False
        self._set_state(STATE_ERROR, key)

    def _on_prepare_failed(self, key: str) -> None:
        log.info("update not installed (%s)", key)
        self._fail(key)

    def _on_prepared(self, path) -> None:
        path = Path(path)
        if self._cancel.is_set() or not self._confirm_launch():     # second consent after verification
            updater.discard_update(path)
            self._fail("upd_err_cancelled")
            return
        self._set_state(STATE_READY)
        bridge = self._bridge

        def work() -> None:                         # full re-hash off the UI thread
            try:
                updater.reverify_installer(path)
                bridge.launch_ready.emit(path)
            except updater.UpdateError as exc:
                bridge.launch_failed.emit(exc.key)
            except Exception:  # noqa: BLE001
                bridge.launch_failed.emit("upd_err_hash")
        _spawn(work)

    def _on_launch_ready(self, path) -> None:
        path = Path(path)
        try:
            updater.launch_installer(path, rehash=False)
        except updater.UpdateError as exc:
            updater.discard_update(path)
            self._fail(exc.key)
            return
        except OSError as exc:
            self._fail("upd_err_blocked" if updater.is_policy_block(exc) else "upd_err_launch")
            return
        log.info("installer launched")
        self._busy = False
        self.quit_requested.emit()


# ======================================================================= settings
BOOL_KEYS = ("fahrenheit", "show_in_fullscreen", "group_pills", "pill_labels", "pill_bars", "stack_pills")


class SettingsActions:
    """`ctl` is the Controller: it owns `cfg`, `widget`, `panel`, `snapshot`, `monitor`, and the apply hooks
    (`prefs_changed`, `appearance_changed`, `metrics_changed`, `language_changed`, `placement_changed`,
    `set_theme`, `set_fahrenheit`, `open_detail`, `apply_log_level`, `notify`)."""

    def __init__(self, ctl, updates: UpdateController, logs_dir: Path, opener=None, confirm=None):
        self.ctl = ctl
        self.cfg = ctl.cfg
        self.updates = updates
        self.logs_dir = Path(logs_dir)
        self._opener = opener or open_path
        self._confirm = confirm or _ask
        self._cache: dict[str, tuple[float, object]] = {}

    def invalidate_caches(self) -> None:
        self._cache.clear()

    def _cached(self, name: str, fn):
        """`fn()` memoized for CACHE_TTL_S (the Settings page refreshes often; these lookups are slow)."""
        now = time.monotonic()
        hit = self._cache.get(name)
        if hit is not None and now - hit[0] < CACHE_TTL_S:
            return hit[1]
        value = fn()
        self._cache[name] = (now, value)
        return value

    # ---- helpers
    def _set(self, key: str, value) -> bool:
        try:
            self.cfg.set(key, value)
            return True
        except ValueError:
            log.warning("config rejected %s", key)
            self.ctl.notify(tr("integ_save_error"))
            return False

    def callbacks(self) -> SettingsCallbacks:
        return SettingsCallbacks(
            set_bool=self.set_bool, get_bool=self.get_bool,
            list_hardware=self.list_hardware, hardware_visible=self.hardware_visible,
            set_hardware_visible=self.set_hardware_visible, category_enabled=self.category_enabled,
            set_category=self.set_category, open_detail=self.open_detail, reset_summary=self.reset_summary,
            has_overrides=self.has_overrides,
            get_theme=lambda: self.cfg.get("theme"), set_theme=self.ctl.set_theme,
            get_palette=lambda: self.cfg.get("color_palette"), set_palette=self.set_palette,
            open_custom_colors=self.open_custom_colors,
            get_icon_style=lambda: self.cfg.get("icon_style"), set_icon_style=self.set_icon_style,
            get_panel_size=lambda: self.cfg.get("panel_size"), set_panel_size=self.set_panel_size,
            get_pill_style=lambda: self.cfg.get("pill_style"), set_pill_style=self.set_pill_style,
            get_ui_scale=lambda: self.cfg.get("ui_scale"), set_ui_scale=self.set_ui_scale,
            get_language=lambda: self.cfg.get("language"), set_language=self.set_language,
            get_placement=self.get_placement, set_placement=self.set_placement,
            get_floating_anchor=self.get_floating_anchor, set_floating_anchor=self.set_floating_anchor,
            get_monitor=lambda: self.cfg.get("placement_monitor"), set_monitor=self.set_monitor,
            get_fullscreen_placement=lambda: self.cfg.get("fullscreen_placement"),
            set_fullscreen_placement=self.set_fullscreen_placement, reset_position=self.reset_position,
            list_screens=lambda: self._cached("screens", self.ctl.widget.list_screens), current_key=self.ctl.widget.current_screen_key,
            icons_centered=self.ctl.widget.icons_centered,
            monitor_has_taskbar=lambda: self._cached("taskbar", self.ctl.widget.monitor_has_taskbar),
            open_custom_icons=self.open_custom_icons, clear_custom_icons=self.clear_custom_icons,
            open_log=self.open_log, open_logs_folder=self.open_logs_folder,
            get_log_debug=lambda: bool(self.cfg.get("log_debug")), set_log_debug=self.set_log_debug,
            startup_enabled=lambda: self._cached("startup", startup.is_enabled), set_startup=self.set_startup,
            update_state=self.updates.snapshot, check_updates=self.updates.check_now,
            can_install_update=self.updates.can_install, install_update=self.updates.install,
            open_release_page=self.updates.open_release_page,
            get_update_auto=lambda: bool(self.cfg.get("update_check_auto")), set_update_auto=self.set_update_auto,
            tech_info=self.tech_info, copy_tech_info=self.copy_tech_info, open_notices=self.open_notices)

    # ---- display
    def get_bool(self, key: str) -> bool:
        return bool(self.cfg.get(key)) if key in BOOL_KEYS else False

    def set_bool(self, key: str, value: bool) -> None:
        if key == "fahrenheit":
            self.ctl.set_fahrenheit(bool(value))
        elif key == "show_in_fullscreen" and self._set(key, bool(value)):
            self.ctl.placement_changed()
        elif key == "group_pills" and self._set(key, bool(value)):
            self.ctl.widget.set_grouped(bool(value))
        elif key == "pill_labels" and self._set(key, bool(value)):
            self.ctl.widget.set_pill_labels(bool(value))
        elif key == "pill_bars" and self._set(key, bool(value)):
            self.ctl.widget.set_pill_bars(bool(value))
        elif key == "stack_pills" and self._set(key, bool(value)):
            self.ctl.widget.set_stacked(bool(value))

    def set_pill_style(self, style: str) -> None:
        if self._set("pill_style", style):
            self.ctl.widget.set_pill_style(style)

    def list_hardware(self) -> list:
        """[(hw_id, name, hw_class)] of the hardware that has (or could have) summary sensors."""
        from .curation import section_title_key
        from .panel import hw_title
        prefs = Prefs(overrides=dict(self.cfg.get("summary_overrides")))
        out, seen = [], {}
        for sec in summary_sections(self.ctl.snapshot, prefs):
            key = section_title_key(sec.hw_class)
            name = tr(key) if key else (sec.hardware or hw_title(sec.hw_kind))
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > 1:
                name = f"{name} #{seen[name]}"      # same name twice: tell them apart
            out.append((sec.hw_id, name, sec.hw_class))
        return out

    def hardware_visible(self, hw_id: str) -> bool:
        return hw_id not in self.cfg.get("hidden_hardware")

    def set_hardware_visible(self, hw_id: str, visible: bool) -> None:
        hidden = [h for h in self.cfg.get("hidden_hardware") if h != hw_id]
        if not visible:
            hidden.append(hw_id)
        if self._set("hidden_hardware", hidden):
            self.ctl.prefs_changed()

    def category_enabled(self, cat: str) -> bool:
        return cat in self.cfg.get("summary_categories")

    def set_category(self, cat: str, on: bool) -> None:
        cur = set(self.cfg.get("summary_categories"))
        (cur.add if on else cur.discard)(cat)
        if self._set("summary_categories", [c for c in SUMMARY_CATEGORIES if c in cur]):
            self.ctl.prefs_changed()

    def open_detail(self) -> None:
        self.ctl.open_detail("")

    def has_overrides(self) -> bool:
        return bool(self.cfg.get("summary_overrides"))

    def reset_summary(self) -> None:
        if self._set("summary_overrides", {}):
            self.ctl.prefs_changed()

    # ---- personalization
    def open_custom_colors(self) -> bool:
        from .custom_colors_dialog import CustomColorsDialog
        dlg = CustomColorsDialog(self.cfg.get("custom_colors"))
        with app_dialog_guard():
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
        if not accepted or not self._set("custom_colors", dlg.colors()):
            return False
        if self.cfg.get("color_palette") == "custom":
            self.ctl.appearance_changed()
        return True

    def set_palette(self, name: str) -> None:
        if name == "custom" and not self.open_custom_colors():
            return
        if self._set("color_palette", name):
            self.ctl.appearance_changed()

    def set_icon_style(self, style: str) -> None:
        if self._set("icon_style", style):
            self.ctl.appearance_changed()

    def set_panel_size(self, size: str) -> None:
        if self._set("panel_size", size):
            self.ctl.metrics_changed()

    def set_ui_scale(self, pct: int) -> None:
        if self._set("ui_scale", int(pct)):
            self.ctl.metrics_changed()

    def set_language(self, lang: str) -> None:
        if self._set("language", lang):
            self.ctl.language_changed()

    # ---- position
    def get_placement(self) -> str:
        if self.cfg.get("placement_mode") == "floating":
            return "floating"
        return "left" if self.cfg.get("taskbar_anchor") == "left" else "right"

    def set_placement(self, choice: str) -> None:
        if choice == "floating":
            ok = self._set("placement_mode", "floating")
            if ok and "floating_hint" not in self.ctl.warned:
                self.ctl.warned.add("floating_hint")
                self.ctl.notify(tr("pos_floating_hint"))
        else:
            ok = self._set("placement_mode", "taskbar") and self._set("taskbar_anchor",
                                                                    "left" if choice == "left" else "right")
            if ok and choice == "left" and self.ctl.widget.icons_centered() is not True:
                self.ctl.warned.add("left_needs_centered_icons")      # explicit pick: warn now, once
                self.ctl.notify(tr("pos_warn_left_needs_centered_icons"))
        if ok:
            self.ctl.placement_changed()

    def get_floating_anchor(self) -> str:
        w = self.ctl.widget
        key = w.current_screen_key() or ""
        anchor, _dx, _dy = lookup_floating(self.cfg.get("floating_positions") or {}, key,
                                           w.placement_settings().monitor)
        return anchor or DEFAULT_FLOATING_ANCHOR

    def set_floating_anchor(self, anchor: str) -> None:
        if anchor not in FLOATING_ANCHORS:
            return
        if self.cfg.get("placement_mode") != "floating" and self._set("placement_mode", "floating"):
            self.ctl.placement_changed()
        self.ctl.widget.set_floating_anchor(anchor)      # reports floating_moved -> persisted by the controller

    def set_monitor(self, key: str) -> None:
        if self._set("placement_monitor", str(key)):
            self.ctl.placement_changed()

    def set_fullscreen_placement(self, choice: str) -> None:
        if self._set("fullscreen_placement", str(choice)):
            self.ctl.placement_changed()

    def reset_position(self) -> None:
        """Forget the stored floating spots of the current monitor (normal and fullscreen)."""
        key = self.ctl.widget.current_screen_key()
        drop = {"primary", str(key) if key is not None else "primary"}
        for name in ("floating_positions", "fullscreen_floating_positions"):
            positions = {k: v for k, v in (self.cfg.get(name) or {}).items() if k not in drop}
            self._set(name, positions)
        self.ctl.placement_changed()

    # ---- custom icons
    def open_custom_icons(self) -> None:
        from .custom_icons_dialog import CustomIconsDialog
        snap: Snapshot = self.ctl.snapshot
        hw, seen = [], set()
        for s in snap.sensors:
            if s.hw_id not in seen:
                seen.add(s.hw_id)
                hw.append((s.hw_id, s.hardware or s.hw_id, s.hw_kind))
        prefs = Prefs(overrides=dict(self.cfg.get("summary_overrides")))
        sensors = [(s.key, f"{sec.hardware} · {s.name}", s.hw_kind)
                   for sec in summary_sections(snap, prefs) for s in sec.sensors]
        dlg = CustomIconsDialog(self.cfg.get("custom_icons_kind"), self.cfg.get("custom_icons_hw"),
                                self.cfg.get("custom_icons_sensor"), hw, sensors, tr=tr)
        with app_dialog_guard():
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
        if accepted:
            by_kind, by_hw, by_sensor = dlg.result_maps()
            ok = (self._set("custom_icons_kind", by_kind) and self._set("custom_icons_hw", by_hw)
                  and self._set("custom_icons_sensor", by_sensor))
            if not ok:
                with app_dialog_guard():
                    QMessageBox.warning(None, tr("custom_icons_title"), tr("integ_save_error"))
            self.ctl.icons_changed()
        self._cleanup_icons()

    def clear_custom_icons(self) -> None:
        if not (self.cfg.get("custom_icons_kind") or self.cfg.get("custom_icons_hw")
                or self.cfg.get("custom_icons_sensor")):
            return
        if not self._confirm(tr("custom_icons_title"), tr("integ_clear_icons_confirm")):
            return
        for key in ("custom_icons_kind", "custom_icons_hw", "custom_icons_sensor"):
            self._set(key, {})
        self.ctl.icons_changed()
        self._cleanup_icons()

    def _cleanup_icons(self) -> None:
        if not getattr(self.cfg, "loaded_ok", True):
            return      # config fell back to defaults after a load error: the real references are unknown
        used = set()
        for key in ("custom_icons_kind", "custom_icons_hw", "custom_icons_sensor"):
            used |= set(self.cfg.get(key).values())
        custom_icons.cleanup_unused(used)

    # ---- logs
    def open_log(self) -> None:
        d = self.logs_dir
        if is_reparse_point(d):
            return
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / "app.log").touch(exist_ok=True)
        except OSError:
            log.warning("could not create the log file")
            return
        self._opener(d / "app.log")

    def open_logs_folder(self) -> None:
        d = self.logs_dir
        if is_reparse_point(d):
            return
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        self._opener(d)

    def set_log_debug(self, on: bool) -> None:
        if self._set("log_debug", bool(on)):
            self.ctl.apply_log_level()

    # ---- startup
    def set_startup(self, on: bool) -> None:
        try:
            startup.set_enabled(bool(on))
        except Exception:  # noqa: BLE001 - schtasks / registry failures: report, keep the real state
            log.warning("could not change start with Windows", exc_info=True)
            self.ctl.notify(tr("integ_startup_error"))
        self.invalidate_caches()
        self._set("start_with_windows", bool(startup.is_enabled()))

    # ---- updates
    def set_update_auto(self, on: bool) -> None:
        self._set("update_check_auto", bool(on))

    # ---- about
    def tech_info(self) -> str:
        from . import about
        return about.tech_info(getattr(self.ctl.monitor, "hello", None))

    def copy_tech_info(self) -> None:
        QApplication.clipboard().setText(self.tech_info())

    def open_notices(self) -> None:
        from . import about
        about.show_licenses()
