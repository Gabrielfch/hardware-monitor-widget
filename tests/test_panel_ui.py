import dataclasses

import pytest
from PySide6.QtWidgets import QToolButton

from hardware_monitor_widget import i18n
from hardware_monitor_widget.models import Snapshot, SourceStatus
from hardware_monitor_widget.panel import HardwarePanel, sensor_a11y_name
from hardware_monitor_widget.sensorhost import snapshot_from_message
from tools.synthetic import make_error_snapshot, make_snapshot


@pytest.fixture
def panel(qapp):
    p = HardwarePanel(dark=True)
    yield p
    p.close()


def _row(panel, suffix):
    return next(r for k, r in panel.rows.items() if k.endswith(suffix))


def test_sections_ordered_by_hardware_kind(panel):
    panel.set_snapshot(make_snapshot())
    assert list(panel.sections) == ["hw//amdcpu/0", "hw//gpu-amd/0", "hw//ram/0", "hw//nvme/0",
                                    "hw//lpc/nct6798d"]
    cpu = [r._sensor.kind for r in panel.rows.values() if panel.sections["hw//amdcpu/0"].isAncestorOf(r)]
    assert cpu == ["temperature", "load", "power"]


def test_rows_and_status_text(panel):
    panel.set_snapshot(make_snapshot())
    assert len(panel.rows) == 11 and len(panel.sections) == 5 and not panel.cards
    row = _row(panel, "temperature/2")
    assert row.status.full_text() == "62°C"                 # only the value; min / max on hover / focus / tooltip
    assert row.name.full_text() == "CPU (Tctl/Tdie)"
    assert row._range == "mín 38°C · máx 81°C" and "mín 38°C · máx 81°C" in row.toolTip()


def test_panel_shows_only_the_summary(panel):
    panel.set_snapshot(make_snapshot())
    keys = set(panel.rows)
    assert not any(k.endswith("temperature/3") or k.endswith("voltage/0") or k.endswith("clock/1") for k in keys)
    assert not any(k.endswith("hw//lpc/nct6798d/temperature/0") for k in keys)


def test_update_in_place_keeps_row_objects(panel):
    panel.set_snapshot(make_snapshot(cpu_temp=60.0))
    before, sections = dict(panel.rows), dict(panel.sections)
    panel.set_snapshot(make_snapshot(cpu_temp=90.0))
    assert panel.rows.keys() == before.keys()
    assert all(panel.rows[k] is before[k] for k in before)
    assert all(panel.sections[k] is sections[k] for k in sections)
    row = _row(panel, "temperature/2")
    assert row.status.full_text().startswith("! 90°C") and row._alert is True


def test_structure_change_rebuilds(panel):
    panel.set_snapshot(make_snapshot())
    old = dict(panel.rows)
    snap = make_snapshot()
    panel.set_snapshot(Snapshot(sensors=snap.sensors[:-1], statuses=snap.statuses))
    assert len(panel.rows) == 10 and all(panel.rows[k] is not old[k] for k in panel.rows)


def test_error_card_needs_admin(panel):
    panel.set_snapshot(make_error_snapshot("needs_admin"))
    assert len(panel.cards) == 1
    card = panel.cards[0]
    assert card.message == "Se necesitan permisos de administrador para las temperaturas de CPU"
    assert card.button.text() == "Reiniciar como administrador"
    got = []
    panel.restart_admin_requested.connect(lambda: got.append("admin"))
    card.button.click()
    assert got == ["admin"]


def test_error_card_retry_with_detail(panel):
    panel.set_snapshot(make_error_snapshot("driver_missing", "WinRing0 blocked"))
    card = panel.cards[0]
    assert "WinRing0 blocked" in card.message and card.button.text() == "Reintentar"
    got = []
    panel.retry_requested.connect(lambda: got.append("retry"))
    card.button.click()
    assert got == ["retry"]


def test_card_removed_when_source_recovers(panel):
    panel.set_snapshot(make_error_snapshot())
    assert panel.cards
    panel.set_snapshot(make_snapshot())
    assert not panel.cards and panel.rows


def test_pin_toggle_signal_and_eye_state(panel):
    panel.set_snapshot(make_snapshot())
    panel.set_pinned(["auto:cpu_temp"])
    cpu, fan = _row(panel, "temperature/2"), _row(panel, "gpu-amd/0/fan/0")
    assert cpu.eye.isChecked() and not fan.eye.isChecked()
    got = []
    panel.pin_toggled.connect(got.append)
    fan.eye.click()
    assert [s.key for s in got] == [fan._sensor.key]
    panel.set_pinned(["auto:cpu_temp", fan._sensor.key])
    assert fan.eye.isChecked()


def test_collapse_signal_and_restore(panel):
    panel.set_snapshot(make_snapshot())
    got = []
    panel.collapsed_changed.connect(got.append)
    sec = panel.sections["hw//ram/0"]
    sec.chevron.click()
    assert sec.collapsed and not sec.body.isVisibleTo(panel) and got[-1] == ["hw//ram/0"]
    panel.set_collapsed(["hw//gpu-amd/0"])
    assert panel.sections["hw//gpu-amd/0"].collapsed and not sec.collapsed


def test_fahrenheit_updates_in_place(panel):
    panel.set_snapshot(make_snapshot(cpu_temp=60.0))
    row = _row(panel, "temperature/2")
    panel.set_fahrenheit(True)
    assert row.status.full_text().startswith("140°F") and _row(panel, "temperature/2") is row


def test_buttons_have_accessible_names(panel):
    panel.set_snapshot(make_snapshot())
    for b in panel.findChildren(QToolButton):
        assert b.accessibleName() or b.text()


def _stale_snapshot(reason="stale", detail="sin respuesta"):
    snap = make_snapshot()
    return Snapshot(snap.sensors, {"native": SourceStatus("native", True, reason, detail)}, snap.taken_at)


def test_degraded_status_with_sensors_shows_card_from_real_message(panel):
    msg = {"type": "snapshot", "t": 1, "hardware": [{"id": "/gpu-amd/0", "name": "RX", "type": "GpuAmd"}],
           "sensors": [{"id": "/gpu-amd/0/temperature/0", "hw": "/gpu-amd/0", "name": "GPU Core",
                        "type": "Temperature", "value": 52.0, "min": 40.0, "max": 60.0}]}
    snap = snapshot_from_message(msg, {"admin": False, "pawnio": True})
    panel.set_snapshot(snap)
    assert len(panel.cards) == 1 and len(panel.rows) == 1
    assert panel.cards[0].button.text() == "Reiniciar como administrador"


def test_driver_missing_card_has_retry_and_detail(panel):
    snap = make_snapshot()
    panel.set_snapshot(Snapshot(snap.sensors, {"native": SourceStatus("native", True, "driver_missing", "PawnIO")}))
    card = panel.cards[0]
    assert "PawnIO" in card.message and card.button.text() == "Reintentar" and card.action == "retry"
    assert len(panel.rows) == 11


def test_stale_card_and_dimmed_rows(panel):
    panel.set_snapshot(_stale_snapshot())
    card = panel.cards[0]
    assert card.message == "Datos no actualizados: sin respuesta" and card.button.text() == "Reintentar"
    row = _row(panel, "temperature/2")
    assert row._stale is True and "rgba(" in row._color and "Datos no actualizados" in row.toolTip()
    panel.set_snapshot(make_snapshot())
    row = _row(panel, "temperature/2")
    assert not panel.cards and row._stale is False and "rgba(" not in row._color


def test_ok_status_has_no_card(panel):
    panel.set_snapshot(make_snapshot())
    assert not panel.cards


def test_critical_marker_and_accessible_description(panel):
    panel.set_snapshot(make_snapshot(cpu_temp=90.0))
    row = _row(panel, "temperature/2")
    assert row.status.full_text().startswith("! 90°C") and "crítico" in row.accessibleDescription()


def test_accessible_names_are_unique_and_descriptive(panel):
    panel.set_snapshot(make_snapshot())
    names = [r.accessibleName() for r in panel.rows.values()]
    assert len(set(names)) == len(names)
    eyes = [r.eye.accessibleName() for r in panel.rows.values()]
    assert len(set(eyes)) == len(eyes)
    gpu = _row(panel, "gpu-amd/0/temperature/0")
    assert gpu.accessibleName() == "AMD Radeon RX 6800 · Temperatura del núcleo (temperatura)"
    assert sensor_a11y_name(gpu._sensor) in gpu.eye.accessibleName()


def test_row_tooltip_has_full_name_original_name_and_range(panel):
    panel.set_snapshot(make_snapshot())
    row = _row(panel, "temperature/2")
    lines = row.toolTip().splitlines()
    assert lines == ["CPU (Tctl/Tdie)", "Nombre original: Core (Tctl/Tdie)", "mín 38°C · máx 81°C"]
    assert "mín" in row.accessibleDescription()


def test_decimal_comma_in_spanish_and_point_in_english(panel):
    panel.set_snapshot(make_snapshot())
    row = _row(panel, "power/0")
    assert row.status.full_text().startswith("65,2 W")
    i18n.set_language("en")
    panel.set_snapshot(make_snapshot())
    assert _row(panel, "power/0").status.full_text().startswith("65.2 W")


def test_loading_text_until_first_snapshot(panel):
    assert panel._empty is not None and panel._empty.text() == "Leyendo sensores…"
    panel.set_snapshot(Snapshot(statuses={"native": SourceStatus("native", True, "ok")}, taken_at=1.0))
    assert panel._empty.text() == "Aún no hay sensores disponibles"


def test_row_apply_does_not_restyle_when_unchanged(panel, monkeypatch):
    panel.set_snapshot(make_snapshot())
    row = _row(panel, "temperature/2")
    calls = {"status_css": 0, "row_css": 0, "pix": 0, "text": 0}
    monkeypatch.setattr(row.status, "setStyleSheet", lambda *_a: calls.__setitem__("status_css", calls["status_css"] + 1))
    monkeypatch.setattr(row, "setStyleSheet", lambda *_a: calls.__setitem__("row_css", calls["row_css"] + 1))
    monkeypatch.setattr(row.icon, "setPixmap", lambda *_a: calls.__setitem__("pix", calls["pix"] + 1))
    monkeypatch.setattr(row.status, "setText", lambda *_a: calls.__setitem__("text", calls["text"] + 1))
    row.apply(row._sensor, True)
    row.apply(row._sensor, True)
    assert calls == {"status_css": 0, "row_css": 0, "pix": 0, "text": 0}
    row.apply(dataclasses.replace(row._sensor, value=63.0), True)       # same band, new text only
    assert calls == {"status_css": 0, "row_css": 0, "pix": 0, "text": 1}
    row.apply(dataclasses.replace(row._sensor, value=80.0), True)       # new colour band
    assert calls["status_css"] == 1 and calls["pix"] == 1


def test_open_with_mouse_leaves_no_focus_ring(panel):
    panel.set_snapshot(make_snapshot())
    from PySide6.QtCore import QRect
    panel.show_at(QRect(100, 100, 40, 30))
    assert not any(b.hasFocus() for b in panel.findChildren(QToolButton))


def test_tab_order_theme_toolbar_then_rows(panel):
    panel.set_snapshot(make_snapshot())
    theme, quit_, refresh = panel._toolbar["theme"], panel._toolbar["quit"], panel._toolbar["refresh"]
    chain, w = [], theme
    for _ in range(400):
        w = w.nextInFocusChain()
        if w is theme:
            break
        chain.append(w)
    eye = next(iter(panel.rows.values())).eye
    assert chain.index(refresh) < chain.index(quit_) < chain.index(eye)


# ---------------------------------------------------------------------------------------- phase 2 summary
from PySide6.QtCore import QRect  # noqa: E402

from hardware_monitor_widget.curation import Prefs  # noqa: E402
from hardware_monitor_widget.pins import MAX_PINS, toggle_bulk  # noqa: E402
from hardware_monitor_widget.panel import panel_dimensions  # noqa: E402
from tools.synthetic import load_real_snapshot  # noqa: E402


@pytest.fixture(scope="module")
def real():
    return load_real_snapshot()


def _titles(panel):
    return [s.title.full_text() for s in panel.sections.values()]


def test_real_snapshot_shows_the_31_summary_sensors(panel, real):
    panel.set_snapshot(real)
    assert len(panel.rows) == 31
    counts = [len(s.keys) for s in panel.sections.values()]
    assert counts == [4, 7, 3, 3, 4, 4, 4, 2]       # CPU, GPU, memory x2, disks x3, board


def test_ram_and_virtual_memory_are_separate_sections(panel, real):
    panel.set_snapshot(real)
    titles = _titles(panel)
    assert titles.index("Memoria") + 1 == titles.index("Memoria virtual")
    i18n.set_language("en")
    panel.retranslate()
    assert "Virtual memory" in _titles(panel)


def test_each_disk_is_its_own_section_and_dimms_are_absent(panel, real):
    panel.set_snapshot(real)
    assert sum(1 for s in panel.sections.values() if s.keys and "storage" in _kind_of(real, s.keys[0])) == 3
    assert not any("dimm" in h for h in panel.sections)
    assert not any("Total Activity" in r._sensor.name for r in panel.rows.values())


def _kind_of(snap, key):
    return next(s.hw_kind for s in snap.sensors if s.key == key)


def test_hidden_hardware_disappears_and_all_hidden_gives_empty_state(panel, real):
    ids = {s.hw_id for s in real.sensors}
    panel.set_snapshot(real)
    board = next(s.hw_id for s in real.sensors if "lpc" in s.hw_id)
    panel.set_prefs(Prefs(hidden_hardware=frozenset(ids - {board})))
    assert list(panel.sections) == [board] and len(panel.rows) == 2
    panel.set_prefs(Prefs(hidden_hardware=frozenset(ids)))
    assert not panel.sections and panel._empty is not None
    assert "resumen" in panel._empty.text()
    assert set(panel.empty_buttons) == {"detail", "display"}


def test_empty_state_actions(panel, real):
    panel.set_snapshot(real)
    panel.set_prefs(Prefs(categories=frozenset()))
    got = []
    panel.detail_requested.connect(lambda hw: got.append(("detail", hw)))
    panel.display_settings_requested.connect(lambda: got.append("display"))
    panel.empty_buttons["display"].click()
    panel.empty_buttons["detail"].click()
    assert got == ["display", ("detail", "")]


def test_overrides_update_the_summary(panel, real):
    panel.set_snapshot(real)
    cpu_core = next(s for s in real.sensors if s.hw_kind == "cpu" and s.name == "Core #1" and s.kind == "clock")
    panel.set_prefs(Prefs(overrides={cpu_core.key: True}))
    assert cpu_core.key in panel.rows and len(panel.rows) == 32


def test_bulk_state_follows_pins_and_click_emits(panel):
    panel.set_snapshot(make_snapshot())
    sec = panel.sections["hw//amdcpu/0"]
    assert sec.bulk.property("bulkState") == "none" and not sec.bulk.isChecked()
    panel.set_pinned([sec.keys[0]])
    assert sec.bulk.property("bulkState") == "some" and not sec.bulk.isChecked()
    panel.set_pinned(list(sec.keys))
    assert sec.bulk.property("bulkState") == "all" and sec.bulk.isChecked()
    got = []
    panel.bulk_pin_requested.connect(got.append)
    sec.bulk.click()
    assert got == ["hw//amdcpu/0"]
    assert sec.bulk.isChecked()                  # the control follows the real pins, not the click
    assert [s.key for s in panel.summary_sensors("hw//amdcpu/0")] == sec.keys


def test_bulk_state_counts_auto_pins(panel):
    panel.set_snapshot(make_snapshot())
    panel.set_pinned(["auto:cpu_temp"])
    assert panel.sections["hw//amdcpu/0"].bulk.property("bulkState") == "some"


def test_bulk_roundtrip_with_pins_module(panel, real):
    panel.set_snapshot(real)
    hw = next(s.hw_id for s in real.sensors if s.hw_kind == "cpu")
    sensors = panel.summary_sensors(hw)
    pins, skipped = toggle_bulk([], sensors, real)
    panel.set_pinned(pins)
    assert skipped == 0 and panel.sections[hw].bulk.property("bulkState") == "all"
    pins, _ = toggle_bulk(pins, sensors, real)
    panel.set_pinned(pins)
    assert panel.sections[hw].bulk.property("bulkState") == "none"


def test_bulk_notice_for_not_added_sensors(panel, real):
    panel.set_snapshot(real)
    hw = next(iter(panel.sections))
    sec = panel.sections[hw]
    panel.set_bulk_notice(hw, 3)
    assert not sec.notice.isHidden() and sec.notice.text() == "3 no añadidos"
    assert "3" in sec.notice.accessibleName()
    panel.set_snapshot(real)                      # survives in-place updates
    assert not panel.sections[hw].notice.isHidden()
    panel.set_bulk_notice(hw, 0)
    assert sec.notice.isHidden()
    assert MAX_PINS == 32


def test_section_detail_button_and_toolbar_emit(panel):
    panel.set_snapshot(make_snapshot())
    got = []
    panel.detail_requested.connect(got.append)
    panel.sections["hw//gpu-amd/0"].detail_btn.click()
    panel._toolbar["detail"].click()
    assert got == ["hw//gpu-amd/0", ""]


def test_toolbar_has_settings_gear_and_no_startup_button(panel):
    assert set(panel._toolbar) == {"theme", "refresh", "fahrenheit", "detail", "settings", "quit"}
    got = []
    panel.settings_requested.connect(lambda: got.append("settings"))
    panel._toolbar["settings"].click()
    assert got == ["settings"]
    panel.set_start_with_windows(True)           # compatibility no-op


def test_header_buttons_have_unique_accessible_names_and_tooltips(panel, real):
    panel.set_snapshot(real)
    names = []
    for sec in panel.sections.values():
        for b in (sec.chevron, sec.bulk, sec.detail_btn):
            assert b.accessibleName() and b.toolTip()
            names.append(b.accessibleName())
    assert len(set(names)) == len(names)


def test_tab_order_includes_section_controls(panel):
    panel.set_snapshot(make_snapshot())
    sec = panel.sections["hw//amdcpu/0"]
    chain, w = [], panel._toolbar["theme"]
    for _ in range(600):
        w = w.nextInFocusChain()
        if w is panel._toolbar["theme"]:
            break
        chain.append(w)
    assert chain.index(sec.chevron) < chain.index(sec.bulk) < chain.index(sec.detail_btn) \
        < chain.index(_row(panel, "temperature/2").eye)


def test_metrics_resize_and_scale_keep_touch_targets(panel):
    avail = QRect(0, 0, 1920, 1040)
    w_c, h_c = panel_dimensions(avail, "compact")
    w_n, h_n = panel_dimensions(avail, "normal")
    w_l, h_l = panel_dimensions(avail, "large")
    assert h_c < h_n < h_l and w_c <= w_n < w_l
    assert (w_n, h_n) == panel_dimensions(avail)
    panel.set_snapshot(make_snapshot())
    panel.set_metrics("large", 90)
    assert panel.size_name == "large" and panel.scale_pct == 90
    for sec in panel.sections.values():
        for b in (sec.chevron, sec.bulk, sec.detail_btn):
            assert b.width() >= 32 and b.height() >= 32
    assert all(r.eye.width() >= 32 for r in panel.rows.values())
    assert all(b.width() >= 32 for b in panel._toolbar.values())


def test_metrics_scale_changes_font_and_rebuilds(panel):
    panel.set_snapshot(make_snapshot())
    before = panel.font().pointSizeF()
    old = dict(panel.rows)
    panel.set_metrics("normal", 130)
    assert panel.font().pointSizeF() == pytest.approx(before * 1.3)
    assert all(panel.rows[k] is not old[k] for k in old)
    old = dict(panel.rows)
    panel.set_metrics("normal", 130)             # same metrics: nothing rebuilt
    assert all(panel.rows[k] is old[k] for k in old)
    panel.set_metrics("bogus", 7)
    assert (panel.size_name, panel.scale_pct) == ("normal", 100)


def test_icon_style_none_drops_header_icons(panel):
    from hardware_monitor_widget import icons
    panel.set_snapshot(make_snapshot())
    assert all(s.icon is not None for s in panel.sections.values())
    icons.set_icon_style("none")
    try:
        panel.set_snapshot(make_snapshot())
        assert all(s.icon is None for s in panel.sections.values())
    finally:
        icons.set_icon_style("outline")
    panel.set_snapshot(make_snapshot())
    assert all(s.icon is not None for s in panel.sections.values())


def test_retranslate_updates_texts(panel):
    panel.set_snapshot(make_snapshot())
    i18n.set_language("en")
    panel.retranslate()
    assert panel._toolbar["detail"].toolTip() == "View all"
    assert panel._toolbar["settings"].accessibleName() == "Settings"
    assert "pinned" in panel.sections["hw//amdcpu/0"].bulk.accessibleName()


def test_value_change_on_real_snapshot_does_not_recreate_widgets(panel, real):
    panel.set_snapshot(real)
    rows, secs = dict(panel.rows), dict(panel.sections)
    changed = [dataclasses.replace(s, value=s.value * 1.01) if s.value is not None else s
               for s in real.sensors]
    panel.set_snapshot(dataclasses.replace(real, sensors=changed))
    assert all(panel.rows[k] is rows[k] for k in rows) and all(panel.sections[k] is secs[k] for k in secs)


# ---------------------------------------------------------------------------------------- UI review fixes
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QColor, QFocusEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hardware_monitor_widget import colors  # noqa: E402
from hardware_monitor_widget.models import Sensor  # noqa: E402
from hardware_monitor_widget.panel import bulk_icon, DIM_ALPHA  # noqa: E402


def test_summary_rows_show_only_the_value(panel, real):
    panel.set_snapshot(real)
    assert panel.rows and all("mín" not in r.status.full_text() and "máx" not in r.status.full_text()
                              for r in panel.rows.values())
    assert all(r.range.isHidden() for r in panel.rows.values())


def test_min_max_second_line_on_hover_and_focus(panel):
    panel.set_snapshot(make_snapshot())
    panel.show()
    row, other = _row(panel, "temperature/2"), _row(panel, "gpu-amd/0/fan/0")
    assert row.range.isHidden()
    row.set_hover(True)
    assert not row.range.isHidden() and row.range.full_text() == "mín 38°C · máx 81°C" and other.range.isHidden()
    row.set_hover(False)
    assert row.range.isHidden()
    QApplication.sendEvent(other.eye, QFocusEvent(QEvent.Type.FocusIn))
    assert not other.range.isHidden()
    QApplication.sendEvent(other.eye, QFocusEvent(QEvent.Type.FocusOut))
    assert other.range.isHidden()


def test_range_line_updates_in_place_while_shown(panel):
    panel.set_snapshot(make_snapshot())
    row = _row(panel, "temperature/2")
    row.set_hover(True)
    row.apply(dataclasses.replace(row._sensor, max=90.0), True)
    assert row.range.full_text() == "mín 38°C · máx 90°C" and _row(panel, "temperature/2") is row


def test_real_names_are_translated_and_original_is_in_the_tooltip(panel, real):
    panel.set_snapshot(real)
    shown = {r._sensor.name: r.name.full_text() for r in panel.rows.values()}
    expected = {"Core (Tctl/Tdie)": "CPU (Tctl/Tdie)", "CPU Total": "Carga total",
                "Cores (Average)": "Frecuencia media", "GPU Hot Spot": "Punto más caliente",
                "GPU Fan": "Ventilador", "GPU Package": "Potencia", "GPU Memory Used": "VRAM usada",
                "Used Space": "Espacio usado", "Read Activity": "Lectura", "Write Activity": "Escritura"}
    for name, want in expected.items():
        assert shown[name].startswith(want), (name, shown[name])
    row = next(r for r in panel.rows.values() if r._sensor.name == "GPU Hot Spot")
    assert "GPU Hot Spot" in row.toolTip()


def test_names_stay_readable_in_english(panel, real):
    i18n.set_language("en")
    panel.set_snapshot(real)
    assert any(r.name.full_text() == "Hot spot" for r in panel.rows.values())
    assert not any(r.name.full_text().startswith("GPU ") for r in panel.rows.values())


def test_duplicate_names_in_a_section_get_the_type(panel, real):
    panel.set_snapshot(real)
    for sec in panel.sections.values():
        labels = [panel.rows[k].name.full_text() for k in sec.keys]
        assert len(set(labels)) == len(labels), labels
    snap = make_snapshot()
    extra = Sensor("hw//gpu-amd/0/load/9", "native", "AMD Radeon RX 6800", "hw//gpu-amd/0", "gpu", "load",
                   "GPU Core", 5.0)
    panel.set_snapshot(Snapshot(snap.sensors + [extra], snap.statuses, snap.taken_at))
    labels = {k: panel.rows[k].name.full_text() for k in panel.sections["hw//gpu-amd/0"].keys}
    assert labels["hw//gpu-amd/0/temperature/0"] == "Temperatura del núcleo"
    assert labels["hw//gpu-amd/0/load/9"].startswith("Carga del núcleo")


def test_accessible_names_unique_with_real_fixture(panel, real):
    panel.set_snapshot(real)
    names = [r.accessibleName() for r in panel.rows.values()]
    assert len(set(names)) == len(names)
    eyes = [r.eye.accessibleName() for r in panel.rows.values()]
    assert len(set(eyes)) == len(eyes)
    row = next(r for r in panel.rows.values() if r._sensor.name == "CPU Total")
    assert "Carga total" in row.accessibleName() and "(carga)" in row.accessibleName()
    assert row._sensor.hardware in row.eye.accessibleName()


def test_a11y_names_get_an_index_when_everything_collides(panel):
    s1 = Sensor("hw//gpu-amd/0/load/1", "native", "GPU", "hw//gpu-amd/0", "gpu", "load", "GPU Core", 1.0)
    s2 = dataclasses.replace(s1, key="hw//gpu-amd/0/load/2")
    panel.set_snapshot(Snapshot([s1, s2], {"native": SourceStatus("native", True, "ok")}, 1.0))
    names = sorted(r.accessibleName() for r in panel.rows.values())
    assert names == ["GPU · Carga del núcleo (carga) (1)", "GPU · Carga del núcleo (carga) (2)"]


def test_bulk_some_glyph_differs_and_tooltip_counts(panel):
    panel.set_snapshot(make_snapshot())
    col = colors.text_color(True)
    imgs = {s: bulk_icon(18, s, col).pixmap(18, 18).toImage() for s in ("none", "some", "all")}
    assert imgs["some"] != imgs["all"] and imgs["some"] != imgs["none"] and imgs["all"] != imgs["none"]
    sec = panel.sections["hw//amdcpu/0"]
    panel.set_pinned([sec.keys[0]])
    assert sec.bulk.toolTip().startswith(f"1 de {len(sec.keys)} en la barra")


def test_not_added_notice_clears_when_pins_change(panel, real):
    panel.set_snapshot(real)
    hw = next(iter(panel.sections))
    panel.set_pinned(["a"])
    panel.set_bulk_notice(hw, 2)
    panel.set_pinned(["a"])                         # same list: the notice stays
    assert not panel.sections[hw].notice.isHidden()
    panel.set_pinned(["a", "b"])
    assert panel.sections[hw].notice.isHidden() and not panel._notices


def test_long_section_title_is_elided_in_the_middle_with_full_tooltip(panel):
    name = "Intel(R) Wi-Fi 6E AX211 160MHz Wireless Network Adapter Extended Name (WI-FI)"
    s = Sensor("hw//nic/0/throughput/0", "native", name, "hw//nic/0", "network", "throughput", "Upload Speed", 1e5)
    panel.set_snapshot(Snapshot([s], {"native": SourceStatus("native", True, "ok")}, 1.0))
    sec = panel.sections["hw//nic/0"]
    panel.show()
    sec.title.setFixedWidth(260)
    QApplication.processEvents()
    assert sec.title.text().endswith("(WI-FI)") and "…" in sec.title.text()
    assert sec.title.toolTip() == name and sec.title.accessibleDescription() == name


def test_secondary_texts_have_4_5_contrast_in_both_themes(real):
    for dark in (True, False):
        grey = colors.band_color("grey", dark)
        for alert in (False, True):
            assert colors.contrast_ratio(grey, colors.row_background(dark, alert)) >= 4.5
        assert colors.contrast_ratio(grey, QColor(*colors.TASKBAR_BASE[dark])) >= 4.5
        for band in ("green", "amber", "orange", "red", "grey"):          # stale (dimmed) values too
            c = QColor(colors.band_color(band, dark))
            c.setAlpha(DIM_ALPHA)
            bg = colors.row_background(dark)
            assert colors.contrast_ratio(colors.composite(c, bg), bg) >= 4.5, (dark, band)


def test_fahrenheit_button_shows_active_unit_without_a_heavier_checked_background(panel):
    panel.set_snapshot(make_snapshot())
    b = panel._toolbar["fahrenheit"]
    assert b.objectName() == "fahrenheitBtn"
    assert "QToolButton#fahrenheitBtn:checked { background: transparent; }" in panel.styleSheet()
    assert b.toolTip() == "Mostrar °F"
    panel.set_fahrenheit(True)
    assert b.isChecked() and b.toolTip() == "Mostrar °C"
