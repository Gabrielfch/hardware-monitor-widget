"""Controller + SettingsActions wiring (no real network, no real startup entry, no dialogs)."""
import json
import logging
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint

from hardware_monitor_widget import app as app_mod
from hardware_monitor_widget import (appearance, colors, custom_icons, i18n, icons, release_info, settings_actions,
                                     startup, updater)
from hardware_monitor_widget.config import Config
from hardware_monitor_widget.models import Sensor, Snapshot

from tools.synthetic import load_real_snapshot


def real_snapshot() -> Snapshot:
    return load_real_snapshot()


@pytest.fixture
def ctl(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = Config(tmp_path / "config.json")
    state = {"enabled": False}
    monkeypatch.setattr(startup, "is_enabled", lambda: state["enabled"])
    monkeypatch.setattr(startup, "set_enabled", lambda v: state.update(enabled=bool(v)))
    monkeypatch.setattr(app_mod.TrayController, "notify", lambda self, t, b, info=True: notes.append(b))
    notes: list = []
    c = app_mod.Controller(qapp, cfg, None)
    c.notes, c.startup_state = notes, state
    c.actions._opener = lambda p: c.opened.append(p)
    c.opened = []
    yield c
    c.panel.hide()
    if c.detail is not None:
        c.detail.close()
    colors.set_palette("classic")
    icons.set_icon_style("outline")
    custom_icons.set_maps({}, {}, {})
    appearance.apply_color_scheme("auto")
    logging.getLogger().setLevel(logging.WARNING)
    i18n.set_language("es")


def test_right_click_opens_panel_like_left_click(ctl):
    ctl.widget.context_menu_requested.emit(QPoint(1, 1))
    assert ctl.panel.isVisible() and not ctl.panel.in_settings
    ctl.panel.hide()
    ctl.panel.hidden_at = None
    ctl.tray._on_activated(ctl.tray._tray.ActivationReason.Context)
    assert ctl.panel.isVisible() and not ctl.panel.in_settings


def test_gear_opens_settings_and_back_returns_to_summary(ctl):
    ctl.open_panel()
    ctl.panel._toolbar["settings"].click()
    assert ctl.panel.in_settings and ctl.panel.settings_view.page_id == "root"
    ctl.panel.settings_view.go_back()
    assert not ctl.panel.in_settings
    ctl.panel.hide()


def test_display_shortcut_opens_display_page_directly(ctl):
    ctl.panel.display_settings_requested.emit()
    assert ctl.panel.in_settings and ctl.panel.settings_view.page_id == "display"
    ctl.panel.settings_view.go_back()
    assert not ctl.panel.in_settings


def test_hiding_the_panel_resets_to_summary(ctl):
    ctl.open_panel("root")
    ctl.panel.hide()
    assert not ctl.panel.in_settings


def test_theme_palette_style_scale_size_language_persist_and_apply(ctl):
    a = ctl.actions
    a.callbacks().set_theme("dark")
    assert ctl.cfg.get("theme") == "dark" and ctl.panel.dark
    a.set_palette("pastel")
    assert ctl.cfg.get("color_palette") == "pastel" and colors.get_palette() == "pastel"
    a.set_icon_style("filled")
    assert icons.get_icon_style() == "filled"
    a.set_ui_scale(115)
    a.set_panel_size("large")
    assert ctl.panel.scale_pct == 115 and ctl.panel.size_name == "large" and ctl.widget.ui_scale() == 115
    a.set_language("en")
    assert i18n.get_language() == "en" and ctl.panel._toolbar["settings"].accessibleName() == "Settings"
    saved = json.loads(Path(ctl.cfg.path).read_text(encoding="utf-8"))
    assert saved["language"] == "en" and saved["ui_scale"] == 115 and saved["panel_size"] == "large"


def test_custom_palette_cancelled_changes_nothing(ctl, monkeypatch):
    monkeypatch.setattr(settings_actions.QDialog, "DialogCode", settings_actions.QDialog.DialogCode)
    ctl.actions.open_custom_colors = lambda: False
    ctl.actions.set_palette("custom")
    assert ctl.cfg.get("color_palette") == "classic"


def test_hardware_visibility_categories_and_reset(ctl):
    snap = real_snapshot()
    ctl.on_snapshot(snap)
    ctl.panel.set_snapshot(snap)        # a hidden panel only receives snapshots when it opens
    a = ctl.actions
    hw = a.list_hardware()
    assert len(hw) >= 8
    hw_id = next(h for h, _n, cls in hw if cls == "cpu")
    a.set_hardware_visible(hw_id, False)
    assert hw_id in ctl.cfg.get("hidden_hardware") and hw_id not in ctl.panel.sections
    a.set_hardware_visible(hw_id, True)
    assert hw_id in ctl.panel.sections
    a.set_category("fans", False)
    assert "fans" not in ctl.cfg.get("summary_categories") and not a.category_enabled("fans")
    a.set_category("fans", True)
    sensor = next(s for s in snap.sensors if "CPU Core #1" in s.name)
    ctl.on_override_changed(sensor.key, True)
    assert a.has_overrides() and sensor.key in ctl.panel.rows
    a.reset_summary()
    assert not a.has_overrides() and sensor.key not in ctl.panel.rows


def test_override_limit_does_not_raise(ctl):
    ctl.on_snapshot(real_snapshot())
    ctl.cfg.set("summary_overrides", {f"k{i}": True for i in range(1000)})
    sensor = next(s for s in ctl.snapshot.sensors if "CPU Core #1" in s.name)
    ctl.on_override_changed(sensor.key, True)       # 1001st entry: ValueError is caught and reported
    assert ctl.notes


def test_bulk_pin_roundtrip_and_notice(ctl):
    snap = real_snapshot()
    ctl.on_snapshot(snap)
    ctl.panel.set_snapshot(snap)
    hw_id = next(i for i, s in ctl.panel.sections.items() if i.startswith("hw/") and "nvme" in i)
    keys = [s.key for s in ctl.panel.summary_sensors(hw_id)]
    ctl.on_bulk_requested(hw_id)
    assert all(k in ctl.cfg.get("pinned") for k in keys) == (len(ctl.cfg.get("pinned")) <= 32)
    ctl.cfg.set("pinned", [f"x{i}" for i in range(32)])
    ctl.on_bulk_requested(hw_id)
    assert ctl.panel.sections[hw_id].notice.isVisibleTo(ctl.panel) or ctl.panel._notices.get(hw_id)


def test_detail_single_instance_persists_geometry_and_toggled(ctl):
    ctl.on_snapshot(real_snapshot())
    ctl.open_detail("")
    d = ctl.detail
    ctl.open_detail("")
    assert ctl.detail is d and d.isVisible() and not ctl.panel.isVisible()
    d.toggled_changed.emit(["hw:abc"])
    d.geometry_changed.emit({"x": 10, "y": 10, "w": 700, "h": 500})
    assert ctl.cfg.get("detail_toggled") == ["hw:abc"] and ctl.cfg.get("detail_geometry")["w"] == 700
    d.close()


def test_detail_only_updates_when_visible(ctl):
    ctl.open_detail("")
    ctl.detail.close()
    rebuilds = ctl.detail.rebuild_count
    ctl.on_snapshot(real_snapshot())
    assert ctl.detail.rebuild_count == rebuilds


def test_floating_moves_and_warnings_are_persisted(ctl):
    ctl.widget.floating_moved.emit("aa", {"anchor": "top-left", "dx": 1, "dy": 2}, False)
    ctl.widget.floating_moved.emit("aa", {"anchor": "top-right", "dx": 0, "dy": 0}, True)
    assert ctl.cfg.get("floating_positions")["aa"]["anchor"] == "top-left"
    assert ctl.cfg.get("fullscreen_floating_positions")["aa"]["anchor"] == "top-right"
    ctl.widget.monitor_changed.emit("bb")
    ctl.widget.fullscreen_placement_changed.emit("floating")
    assert ctl.cfg.get("placement_monitor") == "bb" and ctl.cfg.get("fullscreen_placement") == "floating"
    ctl.widget.placement_warning.emit("left_needs_centered_icons")
    ctl.widget.placement_warning.emit("left_needs_centered_icons")
    assert len(ctl.notes) == 1


def test_placement_choices(ctl):
    a = ctl.actions
    a.set_placement("floating")
    assert ctl.cfg.get("placement_mode") == "floating" and a.get_placement() == "floating"
    assert ctl.widget.placement_settings().mode == "floating"
    a.set_placement("left")
    assert a.get_placement() == "left" and ctl.cfg.get("taskbar_anchor") == "left"
    a.set_show = a.set_bool("show_in_fullscreen", True)
    assert ctl.widget.placement_settings().show_in_fullscreen
    ctl.cfg.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 0, "dy": 0}})
    a.reset_position()
    assert ctl.cfg.get("floating_positions") == {}


def test_log_debug_applies_live_and_persists(ctl):
    a = ctl.actions
    a.set_log_debug(True)
    assert logging.getLogger().level == logging.DEBUG and ctl.cfg.get("log_debug") is True
    a.set_log_debug(False)
    assert logging.getLogger().level == logging.INFO


def test_open_log_creates_it_and_opens_folder(ctl):
    ctl.actions.open_log()
    log_file = ctl.opened[-1]
    assert log_file.name == "app.log" and log_file.exists()
    ctl.actions.open_logs_folder()
    assert ctl.opened[-1] == log_file.parent


def test_startup_reflects_real_state(ctl):
    a = ctl.actions
    a.set_startup(True)
    assert ctl.cfg.get("start_with_windows") is True and ctl.startup_state["enabled"]
    boom = lambda v: (_ for _ in ()).throw(OSError("x"))        # noqa: E731
    startup.set_enabled = boom
    a.set_startup(False)                                         # failure: real state kept, user told
    assert ctl.cfg.get("start_with_windows") is True and ctl.notes


def test_about_callbacks(ctl, qapp):
    from PySide6.QtWidgets import QApplication
    cb = ctl.actions.callbacks()
    info = cb.tech_info()
    assert ": " in info
    cb.copy_tech_info()
    assert QApplication.clipboard().text() == info


def test_settings_model_pages_render_with_real_callbacks(ctl):
    ctl.on_snapshot(real_snapshot())
    for page in ("display", "personalization", "position", "custom_icons", "logs", "updates", "about"):
        ctl.panel.show_settings(page, direct=True)
        assert ctl.panel.settings_view.page_id == page
        assert ctl.panel.settings_view.rows()


# ------------------------------------------------------------------ updates
def test_updates_disabled_means_no_network(ctl, monkeypatch):
    calls = []
    monkeypatch.setattr(updater, "fetch_latest", lambda *a, **k: calls.append(1))
    assert not release_info.is_configured()
    ctl.updates.check_now()
    ctl.updates.start_auto()
    ctl.updates.auto_tick()
    assert calls == [] and ctl.updates.state == "error" and ctl.updates.error_key == "upd_err_disabled"
    assert not ctl.actions.callbacks().can_install_update()


def _release(version="9.9.9"):
    return updater.Release(version=version, tag="v" + version, page_url="https://github.com/x/y/releases/tag/v" + version)


def test_manual_check_runs_off_thread_and_reports(ctl, monkeypatch, qapp):
    import threading
    monkeypatch.setattr(updater, "is_enabled", lambda: True)
    where = []
    monkeypatch.setattr(updater, "check", lambda: (where.append(threading.current_thread()), _release())[1])
    ctl.updates.check_now()
    assert ctl.updates.state == "checking"
    for _ in range(200):
        qapp.processEvents()
        if ctl.updates.state != "checking":
            break
        threading.Event().wait(0.01)
    assert ctl.updates.state == "available" and where[0] is not threading.main_thread()
    assert "9.9.9" in ctl.updates.text()


def test_install_needs_installed_mode_and_confirmation(ctl, monkeypatch):
    ctl.updates.release = _release()
    ctl.updates.state = "available"
    monkeypatch.setattr(updater, "install_mode", lambda: "portable")
    assert not ctl.updates.can_install()
    opened = []
    monkeypatch.setattr(ctl.updates, "open_release_page", lambda: opened.append(1))
    ctl.updates.install()                       # not installable: falls back to the release page
    assert opened == [1] and ctl.updates.state == "available"
    monkeypatch.setattr(updater.Release, "can_install", property(lambda self: True))
    monkeypatch.setattr(updater, "install_mode", lambda: "installed")
    started = []
    monkeypatch.setattr(updater, "prepare_update", lambda *a, **k: started.append(1))
    ctl.updates._confirm_install = lambda v: False       # user declines
    ctl.updates.install()
    assert started == [] and ctl.updates.state == "available"


def test_auto_check_only_notifies(ctl, monkeypatch, qapp):
    import threading
    monkeypatch.setattr(updater, "is_enabled", lambda: True)
    monkeypatch.setattr(updater, "check", lambda: _release())
    monkeypatch.setattr(updater, "launch_installer", lambda *a, **k: pytest.fail("must not install"))
    got = []
    ctl.updates.notify_requested.connect(lambda t, b: got.append(b))
    ctl.cfg.set("update_check_auto", True)
    ctl.updates.auto_tick()
    for _ in range(200):
        qapp.processEvents()
        if got:
            break
        threading.Event().wait(0.01)
    assert len(got) == 1 and ctl.updates.state == "available"
    assert ctl.cfg.get("update_last_check") > 0
    ctl.updates.auto_tick()                      # less than 24 h later: nothing happens
    assert not ctl.updates.busy


def test_duplicate_sensor_names_get_a_type_suffix(ctl):
    from hardware_monitor_widget.curation import Prefs

    def s(kind):
        return Sensor(key=f"k/{kind}", source="native", hardware="CPU", hw_id="hw//amdcpu/0", hw_kind="cpu",
                      kind=kind, name="Core #1", value=1.0)
    sensors = [s("clock"), s("power"), s("voltage")]          # same translated name ("Núcleo #1"), forced in
    ctl.panel.set_prefs(Prefs(overrides={x.key: True for x in sensors}))
    ctl.panel.set_snapshot(Snapshot(sensors=sensors, taken_at=1.0))
    labels = [r.name.full_text() for r in ctl.panel.rows.values()]
    assert any("reloj" in x for x in labels) and any("potencia" in x for x in labels)
    assert all(" · " in x for x in labels)


def test_escape_goes_back_then_closes_and_dialog_guard_keeps_panel(ctl, qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from hardware_monitor_widget.dialogs import app_dialog_guard
    ctl.open_panel("root")
    esc = lambda: qapp.sendEvent(ctl.panel, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))  # noqa: E731
    esc()
    assert ctl.panel.isVisible() and not ctl.panel.in_settings
    ctl.panel._armed = True
    with app_dialog_guard():
        ctl.panel._on_app_state_changed(Qt.ApplicationState.ApplicationInactive)
        ctl.panel._hide_if_inactive()
        assert ctl.panel.isVisible()
    esc()
    assert not ctl.panel.isVisible()


def test_fahrenheit_save_failure_keeps_ui_and_notifies(ctl, monkeypatch):
    monkeypatch.setattr(ctl, "_persist", lambda k, v: False)
    ctl.set_fahrenheit(True)
    assert ctl.notes and not ctl.cfg.get("fahrenheit")


def test_bulk_save_failure_shows_error_not_skipped(ctl, monkeypatch):
    snap = real_snapshot()
    ctl.on_snapshot(snap)
    ctl.panel.set_snapshot(snap)
    hw_id = next(i for i, s in ctl.panel.sections.items() if i.startswith("hw/") and "nvme" in i)
    monkeypatch.setattr(ctl, "_persist", lambda k, v: False)
    ctl.on_bulk_requested(hw_id)
    assert ctl.notes and not ctl.panel._notices.get(hw_id)


def test_single_pin_at_the_limit_warns(ctl):
    snap = real_snapshot()
    ctl.on_snapshot(snap)
    ctl.cfg.set("pinned", [f"x{i}" for i in range(32)])
    ctl.on_pin_toggled(snap.sensors[0])
    assert len(ctl.cfg.get("pinned")) == 32 and ctl.notes and "32" in ctl.notes[-1]


def test_floating_moved_uses_final_monitor_choice(ctl):
    ctl.cfg.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 0, "dy": 0}})
    ctl.widget._ps = __import__("dataclasses").replace(ctl.widget._ps, monitor="bb")
    ctl.widget.floating_moved.emit("bb", {"anchor": "top-right", "dx": 0, "dy": 0}, False)
    assert "primary" in ctl.cfg.get("floating_positions")         # new choice is not "primary": legacy kept
    ctl.widget._ps = __import__("dataclasses").replace(ctl.widget._ps, monitor="primary")
    ctl.widget.floating_moved.emit("aa", {"anchor": "top-right", "dx": 0, "dy": 0}, False)
    assert "primary" not in ctl.cfg.get("floating_positions")


def test_manual_check_during_silent_check_becomes_visible(ctl, monkeypatch):
    u = ctl.updates
    u._busy, u._silent, u._checking = True, True, True
    u.check_now()
    assert u.state == "checking" and not u._silent


def test_duplicate_hardware_names_are_numbered(ctl):
    names = [n for _i, n, _c in ctl.actions.list_hardware()]
    assert len(names) == len(set(names))


def test_settings_lookups_are_cached(ctl, monkeypatch):
    calls = []
    monkeypatch.setattr(startup, "is_enabled", lambda: calls.append(1) or False)
    cb = ctl.actions.callbacks()
    cb.startup_enabled()
    cb.startup_enabled()
    assert len(calls) == 1
    ctl.actions.invalidate_caches()
    cb.startup_enabled()
    assert len(calls) == 2


def test_settings_refresh_is_coalesced(ctl):
    ctl.open_panel("root")
    n = []
    ctl.panel.refresh_settings = lambda: n.append(1)
    for _ in range(50):
        ctl.request_settings_refresh()
    assert len(n) == 1 and ctl._refresh_pending


def test_icon_cleanup_skipped_when_config_not_loaded_ok(ctl, monkeypatch):
    called = []
    monkeypatch.setattr(custom_icons, "cleanup_unused", lambda used: called.append(used))
    ctl.cfg.loaded_ok = False
    ctl.actions._cleanup_icons()
    assert called == []
    ctl.cfg.loaded_ok = True
    ctl.actions._cleanup_icons()
    assert called


def test_group_pills_toggle_persists_and_applies_live(ctl):
    a = ctl.actions
    assert a.get_bool("group_pills") is True and ctl.widget.is_grouped()
    a.set_bool("group_pills", False)
    assert ctl.cfg.get("group_pills") is False and not ctl.widget.is_grouped()
    saved = json.loads(Path(ctl.cfg.path).read_text(encoding="utf-8"))
    assert saved["group_pills"] is False
    a.set_bool("group_pills", True)
    assert ctl.widget.is_grouped()
