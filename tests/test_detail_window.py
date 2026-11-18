import dataclasses
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hardware_monitor_widget import i18n
from hardware_monitor_widget.curation import Prefs, detail_tree, summary_sections
from hardware_monitor_widget.detail_window import (COL_MAX, COL_MIN, COL_NAME, COL_PIN, COL_SUMMARY, COL_VALUE,
                                                   DetailWindow, MIN_HEIGHT, MIN_WIDTH, ROLE_KIND, ROLE_TEXT)
from hardware_monitor_widget.models import Snapshot
from tools.synthetic import load_real_snapshot, make_snapshot

CHECKED, UNCHECKED, PARTIAL = Qt.CheckState.Checked, Qt.CheckState.Unchecked, Qt.CheckState.PartiallyChecked


@pytest.fixture(scope="module")
def real():
    return load_real_snapshot()


@pytest.fixture
def win(qapp, real):
    w = DetailWindow(dark=True)
    w.set_snapshot(real)
    w.show()
    yield w
    w.close()


def _top(w):
    root = w.tree.invisibleRootItem()
    return [root.child(i) for i in range(root.childCount())]


def _children(item):
    return [item.child(i) for i in range(item.childCount())]


def _text(item):
    return item.text(COL_NAME)


def _sensor(real, hw_kind, kind, name):
    return next(s for s in real.sensors if s.hw_kind == hw_kind and s.kind == kind and s.name == name)


def _visible_leaves(w):
    return [k for k, it in w._items.items() if not it.isHidden()]


# ---------------------------------------------------------------------------------------- window and tree
def test_window_is_a_resizable_top_level_with_minimum_size(win):
    assert win.windowFlags() & Qt.WindowType.Window
    assert (win.minimumWidth(), win.minimumHeight()) == (MIN_WIDTH, MIN_HEIGHT) == (560, 360)
    win.resize(300, 200)
    assert win.width() >= 560 and win.height() >= 360
    win.resize(1000, 700)
    assert (win.width(), win.height()) == (1000, 700)


def test_columns_and_headers(win):
    assert [win.tree.headerItem().text(c) for c in range(6)] == \
        ["Nombre", "Valor", "Mín", "Máx", "En la barra", "Resumen"]


def test_tree_contains_every_sensor_in_the_detail_tree(win, real):
    assert set(win._items) == {s.key for s in real.sensors}
    assert len(win._items) > 150


def test_hardware_order_and_titles_match_the_summary_sections(win, real):
    tops = [_text(i) for i in _top(win) if i.data(0, ROLE_KIND) == "hw"]
    expected = []
    for hw in detail_tree(real).hardware:
        expected.append({"ram": "Memoria", "vram": "Memoria virtual"}.get(hw.hw_class, hw.hardware))
    assert tops == expected
    assert "Memoria" in tops and "Memoria virtual" in tops


def test_cpu_has_cores_group_with_six_cores(win):
    node = win._nodes["grp:hw//amdcpu/0:cores"]
    assert _text(node) == "Núcleos (6)"
    assert node.parent() is win._nodes["hw:hw//amdcpu/0"]
    assert node.childCount() > 6          # the per-thread loads live inside the group


def test_named_groups_exist(win):
    labels = {_text(n) for n in win._nodes.values()}
    for want in ("Direct3D", "Controles de ventilador", "Tensiones y corrientes", "Umbrales"):
        assert want in labels, want


def test_memory_modules_node_with_timings(win):
    root = win._nodes["modules"]
    assert _text(root) == "Módulos de memoria (2)"
    dimms = _children(root)
    assert len(dimms) == 2
    assert any(_text(g) == "Temporizaciones" for d in dimms for g in _children(d))
    assert not root.isExpanded()          # collapsed by default
    assert not any(k for k in win._node_keys["modules"] if k not in win._items)


def test_default_expansion_hardware_open_groups_closed(win):
    assert all(win._nodes[n].isExpanded() for n in win._nodes if n.startswith("hw:") and
               not win._nodes[n].parent())
    assert not any(win._nodes[n].isExpanded() for n in win._nodes if n.startswith("grp:"))


def test_values_use_decimal_comma_and_critical_marker(win, real):
    cpu_load = _sensor(real, "cpu", "load", "CPU Total")
    item = win._items[cpu_load.key]
    assert item.text(COL_VALUE).endswith("%")
    snap = dataclasses.replace(real, sensors=[dataclasses.replace(s, value=97.0) if s.key == cpu_load.key else s
                                              for s in real.sensors])
    win.set_snapshot(snap)
    assert win._items[cpu_load.key].text(COL_VALUE) == "! 97%"
    assert "crítico" in item.data(COL_VALUE, Qt.ItemDataRole.AccessibleDescriptionRole)


def test_none_values_show_dash(win, real):
    none_sensor = next((s for s in real.sensors if s.value is None), None)
    if none_sensor is not None:
        assert win._items[none_sensor.key].text(COL_VALUE) == "—"


# ---------------------------------------------------------------------------------------- live updates (D4)
def test_tick_updates_values_in_place_without_rebuilding(win, real):
    key = _sensor(real, "cpu", "load", "CPU Total").key
    item = win._items[key]
    builds = win.rebuild_count
    win.tree.verticalScrollBar().setValue(5)
    win.tree.setCurrentItem(item)
    win.search.setText("cpu")
    snap = dataclasses.replace(real, sensors=[dataclasses.replace(s, value=11.0) if s.key == key else s
                                              for s in real.sensors])
    win.set_snapshot(snap)
    assert win.rebuild_count == builds
    assert win._items[key] is item and item.text(COL_VALUE) == "11%"
    assert win.tree.currentItem() is item and win.search.text() == "cpu"


def test_set_cell_text_only_when_changed(win, real, monkeypatch):
    from PySide6.QtWidgets import QTreeWidgetItem
    calls = []
    orig = QTreeWidgetItem.setText
    monkeypatch.setattr(QTreeWidgetItem, "setText", lambda self, col, text: (calls.append(col), orig(self, col, text))[1])
    win.set_snapshot(real)
    assert calls == []


def test_rebuild_only_when_the_key_set_changes(win, real):
    builds = win.rebuild_count
    win.set_snapshot(dataclasses.replace(real, sensors=list(real.sensors)))
    assert win.rebuild_count == builds
    win.tree.verticalScrollBar().setValue(8)
    win.set_snapshot(dataclasses.replace(real, sensors=real.sensors[:-1]))
    assert win.rebuild_count == builds + 1
    assert len(win._items) == len(real.sensors) - 1


def test_rebuild_keeps_filter_and_selection(win, real):
    win.search.setText("fan")
    key = next(k for k in win._items if not win._items[k].isHidden())
    win.tree.setCurrentItem(win._items[key])
    other = next(s for s in real.sensors if s.key != key and "fan" not in s.name.lower()
                 and s.kind != "fan")
    win.set_snapshot(dataclasses.replace(real, sensors=[s for s in real.sensors if s.key != other.key]))
    assert win.search.text() == "fan"
    assert win.tree.currentItem() is win._items[key]
    assert not win._items[key].isHidden()


def test_hidden_window_does_not_touch_the_tree(qapp, real):
    w = DetailWindow(dark=True)
    w.set_snapshot(real)
    assert w.rebuild_count == 0 and not w._items
    w.show()
    assert w.rebuild_count == 1 and w._items
    w.hide()
    builds = w.rebuild_count
    w.set_snapshot(dataclasses.replace(real, sensors=real.sensors[:-3]))
    assert w.rebuild_count == builds
    w.show()
    assert w.rebuild_count == builds + 1
    w.close()


def test_fahrenheit_updates_in_place(win, real):
    temp = _sensor(real, "cpu", "temperature", "Core (Tctl/Tdie)")
    item = win._items[temp.key]
    builds = win.rebuild_count
    win.set_fahrenheit(True)
    assert item.text(COL_VALUE).endswith("°F") and win.rebuild_count == builds
    assert item.text(COL_MIN).endswith("°F") or item.text(COL_MIN) == "—"


# ---------------------------------------------------------------------------------------- pin checkboxes (D3)
def test_leaf_pin_checkbox_emits_and_follows_set_pinned(win, real):
    s = _sensor(real, "cpu", "load", "CPU Total")
    item = win._items[s.key]
    got = []
    win.pin_toggled.connect(got.append)
    item.setCheckState(COL_PIN, CHECKED)
    assert [x.key for x in got] == [s.key]
    assert item.checkState(COL_PIN) == UNCHECKED          # the controller has not confirmed it
    win.set_pinned([s.key])
    assert item.checkState(COL_PIN) == CHECKED
    got.clear()
    item.setCheckState(COL_PIN, UNCHECKED)
    assert [x.key for x in got] == [s.key]


def test_set_pinned_never_emits_and_marks_auto_pins(win, real):
    got = []
    win.pin_toggled.connect(got.append)
    win.bulk_pin_toggled.connect(got.append)
    win.set_pinned(["auto:cpu_temp", "auto:gpu_load"])
    assert got == []
    s = _sensor(real, "cpu", "temperature", "Core (Tctl/Tdie)")
    assert win._items[s.key].checkState(COL_PIN) == CHECKED


def test_hardware_node_is_tristate_and_pins_in_bulk(win, real):
    cpu = win._nodes["hw:hw//amdcpu/0"]
    keys = win._node_keys["hw:hw//amdcpu/0"]
    assert cpu.checkState(COL_PIN) == UNCHECKED
    win.set_pinned([keys[0]])
    assert cpu.checkState(COL_PIN) == PARTIAL
    got = []
    win.bulk_pin_toggled.connect(got.append)
    cpu.setCheckState(COL_PIN, CHECKED)                   # a click on the partial box checks it
    assert [s.key for s in got[0]] == keys
    win.set_pinned(keys)
    assert cpu.checkState(COL_PIN) == CHECKED
    assert win._nodes["grp:hw//amdcpu/0:cores"].checkState(COL_PIN) == CHECKED
    got.clear()
    cpu.setCheckState(COL_PIN, UNCHECKED)
    assert len(got) == 1


def test_group_node_pins_only_its_sensors(win):
    group = win._nodes["grp:hw//amdcpu/0:cores"]
    gkeys = win._node_keys["grp:hw//amdcpu/0:cores"]
    got = []
    win.bulk_pin_toggled.connect(got.append)
    group.setCheckState(COL_PIN, CHECKED)
    assert [s.key for s in got[0]] == gkeys
    win.set_pinned(gkeys[:2])
    assert group.checkState(COL_PIN) == PARTIAL
    assert win._nodes["hw:hw//amdcpu/0"].checkState(COL_PIN) == PARTIAL


def test_modules_node_covers_the_dimms(win):
    keys = win._node_keys["modules"]
    assert keys and set(keys) <= set(win._items)
    win.set_pinned(keys)
    assert win._nodes["modules"].checkState(COL_PIN) == CHECKED


# ---------------------------------------------------------------------------------------- summary checkbox (D3)
def test_summary_column_reflects_prefs_and_emits_wanted_state(win, real):
    in_sum = _sensor(real, "cpu", "load", "CPU Total")
    out = _sensor(real, "cpu", "voltage", "Core (SVI2 TFN)")
    assert win._items[in_sum.key].checkState(COL_SUMMARY) == CHECKED
    assert win._items[out.key].checkState(COL_SUMMARY) == UNCHECKED
    got = []
    win.summary_override_changed.connect(lambda k, v: got.append((k, v)))
    win._items[out.key].setCheckState(COL_SUMMARY, CHECKED)
    win._items[in_sum.key].setCheckState(COL_SUMMARY, UNCHECKED)
    assert got == [(out.key, True), (in_sum.key, False)]
    assert win._items[out.key].checkState(COL_SUMMARY) == UNCHECKED     # until the prefs change
    win.set_prefs(Prefs(overrides={out.key: True, in_sum.key: False}))
    assert win._items[out.key].checkState(COL_SUMMARY) == CHECKED
    assert win._items[in_sum.key].checkState(COL_SUMMARY) == UNCHECKED
    assert got[-1] == (in_sum.key, False)


def test_summary_column_matches_summary_sections(win, real):
    prefs = Prefs()
    expected = {s.key for sec in summary_sections(real, prefs) for s in sec.sensors}
    checked = {k for k, it in win._items.items() if it.checkState(COL_SUMMARY) == CHECKED}
    assert checked == expected and len(expected) == 31


def test_nodes_have_no_summary_checkbox(win):
    for item in win._nodes.values():
        assert item.data(COL_SUMMARY, Qt.ItemDataRole.CheckStateRole) is None


# ---------------------------------------------------------------------------------------- search (D2)
def test_search_filters_case_insensitively_by_sensor_name(win, real):
    win.search.setText("CPU TOTAL")
    visible = _visible_leaves(win)
    assert visible and all("carga total" in win._items[k].text(COL_NAME).lower() or "cpu total" in win._items[k].data(0, ROLE_TEXT) for k in visible)
    assert win.stack.currentWidget() is win.tree


def test_search_by_hardware_name_shows_whole_hardware(win, real):
    name = next(h.hardware for h in detail_tree(real).hardware if h.hw_class == "gpu")
    win.search.setText(name.lower())
    gpu_keys = set(win._node_keys[f"hw:{next(s.hw_id for s in real.sensors if s.hw_kind == 'gpu')}"])
    assert gpu_keys <= set(_visible_leaves(win))
    assert not any(k in _visible_leaves(win) for k in win._node_keys["hw:hw//amdcpu/0"])


def test_search_by_group_title_and_kind(win):
    win.search.setText("núcleos")
    assert win._nodes["grp:hw//amdcpu/0:cores"].isHidden() is False
    assert set(win._node_keys["grp:hw//amdcpu/0:cores"]) <= set(_visible_leaves(win))
    win.search.setText("ventilador")
    leaves = _visible_leaves(win)
    assert leaves and all("/fan/" in k or "/control/" in k for k in leaves)   # fans + "Controles de ventilador"


def test_matches_are_expanded_and_clearing_restores_the_previous_state(win):
    win.tree.collapseItem(win._nodes["hw:hw//amdcpu/0"])
    toggled_before = win.toggled()
    assert "hw:hw//amdcpu/0" in toggled_before
    win.search.setText("core #1")
    group = win._nodes["grp:hw//amdcpu/0:cores"]
    assert group.isExpanded() and win._nodes["hw:hw//amdcpu/0"].isExpanded()
    assert win.toggled() == toggled_before                # search expansion is not remembered
    win.search.clear()
    assert not win._nodes["hw:hw//amdcpu/0"].isExpanded()
    assert not group.isExpanded()
    assert not any(it.isHidden() for it in win._items.values())


def test_no_results_message_and_recovery(win):
    win.search.setText("zzzz-no-such-sensor")
    assert win.stack.currentWidget() is win.message and win.message.text() == "Sin resultados"
    win.search.clear()
    assert win.stack.currentWidget() is win.tree


def test_ctrl_f_focuses_search(win):
    from PySide6.QtGui import QKeySequence, QShortcut
    win.tree.setFocus()
    shortcuts = [sc for sc in win.findChildren(QShortcut) if sc.key() == QKeySequence("Ctrl+F")]
    assert len(shortcuts) == 1
    shortcuts[0].activated.emit()
    assert win.focusWidget() is win.search


def test_escape_clears_search_then_closes(win):
    win.search.setText("cpu")
    QTest.keyClick(win.search, Qt.Key.Key_Escape)
    assert win.search.text() == "" and win.isVisible()
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert not win.isVisible()


# ---------------------------------------------------------------------------------------- keyboard / a11y (D5)
def test_space_toggles_pin_and_ctrl_space_toggles_summary(win, real):
    s = _sensor(real, "cpu", "load", "CPU Total")
    item = win._items[s.key]
    win.tree.setCurrentItem(item)
    pins, overrides = [], []
    win.pin_toggled.connect(pins.append)
    win.summary_override_changed.connect(lambda k, v: overrides.append((k, v)))
    QTest.keyClick(win.tree, Qt.Key.Key_Space)
    QTest.keyClick(win.tree, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
    assert [x.key for x in pins] == [s.key]
    assert overrides == [(s.key, False)]                  # it was in the summary


def test_space_on_a_hardware_node_pins_in_bulk(win):
    node = win._nodes["hw:hw//amdcpu/0"]
    win.tree.setCurrentItem(node)
    got = []
    win.bulk_pin_toggled.connect(got.append)
    QTest.keyClick(win.tree, Qt.Key.Key_Space)
    assert len(got) == 1 and got[0]
    got.clear()
    QTest.keyClick(win.tree, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)   # no summary box on nodes
    assert got == []


def test_arrow_keys_navigate_and_expand(win):
    win.tree.setCurrentItem(win._nodes["hw:hw//amdcpu/0"])
    QTest.keyClick(win.tree, Qt.Key.Key_Left)
    assert not win._nodes["hw:hw//amdcpu/0"].isExpanded()
    QTest.keyClick(win.tree, Qt.Key.Key_Right)
    assert win._nodes["hw:hw//amdcpu/0"].isExpanded()
    QTest.keyClick(win.tree, Qt.Key.Key_Down)
    assert win.tree.currentItem() is not win._nodes["hw:hw//amdcpu/0"]


def test_every_element_has_an_accessible_name(win, real):
    assert win.accessibleName() and win.search.accessibleName() and win.tree.accessibleName()
    assert win.expand_btn.accessibleName() and win.collapse_btn.accessibleName()
    s = _sensor(real, "cpu", "load", "CPU Total")
    item = win._items[s.key]
    for col in (COL_NAME, COL_PIN, COL_SUMMARY):
        assert item.data(col, Qt.ItemDataRole.AccessibleTextRole)
    assert all(it.data(COL_NAME, Qt.ItemDataRole.AccessibleTextRole) for it in win._items.values())
    for node in win._nodes.values():
        assert node.data(COL_PIN, Qt.ItemDataRole.AccessibleTextRole)


def test_tab_order_search_buttons_tree(win):
    chain, w = [], win.search
    for _ in range(20):
        w = w.nextInFocusChain()
        chain.append(w)
    assert chain.index(win.expand_btn) < chain.index(win.collapse_btn) < chain.index(win.tree)


# ---------------------------------------------------------------------------------------- state persistence (D1)
def test_expansion_changes_emit_toggled_changed_and_restore(win):
    got = []
    win.toggled_changed.connect(got.append)
    win.tree.expandItem(win._nodes["grp:hw//amdcpu/0:cores"])
    win.tree.collapseItem(win._nodes["hw:hw//gpu-amd/0" if "hw:hw//gpu-amd/0" in win._nodes else
                                     next(n for n in win._nodes if n.startswith("hw:") and "gpu" in n)])
    assert got[-1] and "grp:hw//amdcpu/0:cores" in got[-1]
    state = win.toggled()
    win.set_toggled([])
    assert not win._nodes["grp:hw//amdcpu/0:cores"].isExpanded()
    win.set_toggled(state)
    assert win._nodes["grp:hw//amdcpu/0:cores"].isExpanded()
    # unchanged state is dropped again
    win.tree.collapseItem(win._nodes["grp:hw//amdcpu/0:cores"])
    assert "grp:hw//amdcpu/0:cores" not in win.toggled()


def test_toggled_ids_survive_a_rebuild_and_junk_is_ignored(win, real):
    win.set_toggled(["grp:hw//amdcpu/0:cores", 5, None, "ghost"])
    assert win._nodes["grp:hw//amdcpu/0:cores"].isExpanded()
    win.set_snapshot(dataclasses.replace(real, sensors=real.sensors[:-1]))
    assert win._nodes["grp:hw//amdcpu/0:cores"].isExpanded()


def test_expand_and_collapse_all_buttons(win):
    win.collapse_btn.click()
    assert not any(n.isExpanded() for n in win._nodes.values())
    win.expand_btn.click()
    assert all(n.isExpanded() for n in win._nodes.values())


def test_geometry_is_emitted_on_hide_and_restorable(qapp, real):
    w = DetailWindow(dark=True)
    w.set_snapshot(real)
    got = []
    w.geometry_changed.connect(got.append)
    w.show()
    w.resize(900, 640)
    w.move(40, 50)
    w.hide()
    assert got and got[-1]["w"] == 900 and got[-1]["h"] == 640
    other = DetailWindow(dark=True)
    other.restore_geometry(got[-1])
    assert (other.width(), other.height()) == (900, 640)
    other.restore_geometry({"x": "a"})                    # malformed: ignored
    other.restore_geometry({"x": 0, "y": 0, "w": 10, "h": 10})
    assert other.width() >= MIN_WIDTH and other.height() >= MIN_HEIGHT
    w.close()
    other.close()


def test_show_for_selects_and_scrolls_to_the_hardware(win):
    node = win._nodes["hw:hw//lpc/nct6798d/0"]
    win.search.setText("zzz")
    win.show_for("hw//lpc/nct6798d/0")
    assert win.search.text() == "" and win.tree.currentItem() is node and node.isExpanded()
    dimm = next(n for n in win._nodes if n.startswith("hw:") and "dimm" in n)
    win.show_for(dimm[3:])
    assert win.tree.currentItem() is win._nodes[dimm] and win._nodes["modules"].isExpanded()
    win.show_for(None)
    assert win.isVisible()


def test_notice_label(win):
    win.set_notice("3 no añadidos")
    assert not win.notice.isHidden() and win.notice.accessibleName() == "3 no añadidos"
    win.set_notice("")
    assert win.notice.isHidden()


def test_retranslate_to_english(win):
    i18n.set_language("en")
    win.retranslate()
    assert win.tree.headerItem().text(COL_PIN) == "In taskbar"
    assert _text(win._nodes["grp:hw//amdcpu/0:cores"]) == "Cores (6)"
    assert "Virtual memory" in [_text(i) for i in _top(win)]
    win.search.setText("zzzz")
    assert win.message.text() == "No results"


def test_set_dark_rebuilds_with_new_colors(win):
    builds = win.rebuild_count
    win.set_dark(False)
    assert win.rebuild_count == builds + 1 and not win.dark


def test_empty_snapshot_shows_message(qapp):
    w = DetailWindow(dark=True)
    w.set_snapshot(Snapshot())
    w.show()
    assert w.stack.currentWidget() is w.message and w.message.text() == "Aún no hay sensores disponibles"
    w.close()


def test_works_with_the_synthetic_snapshot(qapp):
    w = DetailWindow(dark=False)
    w.set_snapshot(make_snapshot())
    w.show()
    assert len(w._items) == 15
    w.close()


def test_performance_of_a_large_snapshot(qapp):
    from hardware_monitor_widget.models import Sensor
    sensors = [Sensor(f"hw//amdcpu/0/load/{i}", "native", "CPU", "hw//amdcpu/0", "cpu", "load", f"CPU Core #{i}",
                      float(i % 100)) for i in range(2000)]
    w = DetailWindow(dark=True)
    w.show()
    t0 = time.perf_counter()
    w.set_snapshot(Snapshot(sensors=sensors, statuses={}, taken_at=1.0))
    first = time.perf_counter() - t0
    t0 = time.perf_counter()
    w.set_snapshot(Snapshot(sensors=[dataclasses.replace(s, value=(s.value + 1) % 100) for s in sensors],
                            statuses={}, taken_at=2.0))
    tick = time.perf_counter() - t0
    assert tick < 1.0 and first < 10.0
    w.close()


# ---------------------------------------------------------------------------------------- UI review fixes
from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402

from hardware_monitor_widget import colors  # noqa: E402
from hardware_monitor_widget.detail_window import ROLE_LOCKED, check_colors, paint_check  # noqa: E402


def test_names_are_translated_and_the_original_is_searchable(win, real):
    s = _sensor(real, "cpu", "temperature", "Core (Tctl/Tdie)")
    item = win._items[s.key]
    assert item.text(COL_NAME) == "CPU (Tctl/Tdie)"
    assert "Core (Tctl/Tdie)" in item.toolTip(COL_NAME)
    for query in ("Cores (Average)", "frecuencia media"):          # original and translated name
        win.search.setText(query)
        avg = _sensor(real, "cpu", "clock", "Cores (Average)")
        assert not win._items[avg.key].isHidden(), query
    win.search.setText("punto más caliente")
    hot = _sensor(real, "gpu", "temperature", "GPU Hot Spot")
    assert not win._items[hot.key].isHidden()
    win.search.setText("gpu hot spot")
    assert not win._items[hot.key].isHidden()


def test_accessible_names_are_unique_across_the_tree(win, real):
    names = [it.data(COL_NAME, Qt.ItemDataRole.AccessibleTextRole) for it in win._items.values()]
    assert len(names) > 150 and len(set(names)) == len(names)
    d3d = [n for n in names if "D3D Copy" in n]
    assert len(d3d) >= 2 and len(set(d3d)) == len(d3d)
    pins = [it.data(COL_PIN, Qt.ItemDataRole.AccessibleTextRole) for it in win._items.values()]
    assert len(set(pins)) == len(pins)


def test_siblings_with_equal_translated_names_show_the_type(win):
    group = win._nodes["grp:hw//amdcpu/0:cores"]
    labels = [_text(group.child(i)) for i in range(group.childCount())]
    assert len(set(labels)) == len(labels)


def test_pinned_column_is_called_in_the_taskbar(win):
    assert win.tree.headerItem().text(COL_PIN) == "En la barra"
    item = next(iter(win._items.values()))
    assert item.data(COL_PIN, Qt.ItemDataRole.AccessibleTextRole).startswith("En la barra: ")
    assert "barra" in item.toolTip(COL_PIN)


def _hide_hardware(win, real, hw_id):
    win.set_prefs(Prefs(hidden_hardware=frozenset({hw_id})))


def test_hidden_hardware_shows_a_disabled_unchecked_summary_box(win, real):
    s = _sensor(real, "cpu", "load", "CPU Total")
    item = win._items[s.key]
    assert item.checkState(COL_SUMMARY) == CHECKED and not item.data(COL_SUMMARY, ROLE_LOCKED)
    _hide_hardware(win, real, s.hw_id)
    assert item.checkState(COL_SUMMARY) == UNCHECKED and item.data(COL_SUMMARY, ROLE_LOCKED)
    assert item.toolTip(COL_SUMMARY) == "Componente oculto en Ajustes > Visualización"
    other = _sensor(real, "gpu", "load", "GPU Core")
    assert win._items[other.key].checkState(COL_SUMMARY) == CHECKED
    assert not win._items[other.key].data(COL_SUMMARY, ROLE_LOCKED)
    got = []
    win.summary_override_changed.connect(lambda k, v: got.append((k, v)))
    item.setCheckState(COL_SUMMARY, CHECKED)                      # a locked box never reaches the controller
    win.tree.setCurrentItem(item)
    QTest.keyClick(win.tree, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
    assert got == []
    win.set_prefs(Prefs())
    assert item.checkState(COL_SUMMARY) == CHECKED and not item.data(COL_SUMMARY, ROLE_LOCKED)
    assert "resumen" in item.toolTip(COL_SUMMARY)


def test_clicking_a_cell_toggles_its_box_and_locked_cells_ignore_clicks(win, real):
    s = _sensor(real, "cpu", "load", "CPU Total")
    item = win._items[s.key]
    win.tree.scrollToItem(item)
    QApplication.processEvents()
    pins = []
    win.pin_toggled.connect(pins.append)
    rect = win.tree.visualRect(win.tree.indexFromItem(item, COL_PIN))
    QTest.mouseClick(win.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert [x.key for x in pins] == [s.key]
    overrides = []
    win.summary_override_changed.connect(lambda k, v: overrides.append((k, v)))
    _hide_hardware(win, real, s.hw_id)
    rect = win.tree.visualRect(win.tree.indexFromItem(item, COL_SUMMARY))
    QTest.mouseClick(win.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert overrides == []


def test_check_box_borders_have_3_to_1_contrast_in_both_themes():
    for dark in (True, False):
        c = check_colors(dark)
        for name in ("border", "hover", "disabled", "fill"):
            assert colors.contrast_ratio(c[name], c["field"]) >= 3.0, (dark, name)
        assert colors.contrast_ratio(c["mark"], c["fill"]) >= 4.5


def _render(state, dark, **kw):
    img = QImage(32, 32, QImage.Format.Format_ARGB32)
    img.fill(check_colors(dark)["field"])
    p = QPainter(img)
    paint_check(p, QRectF(8, 8, 16, 16), state, dark, **kw)
    p.end()
    return img


def _count(img, color, tol=12, lo=0, hi=None):
    n = 0
    for y in range(lo, hi or img.height()):
        for x in range(lo, hi or img.width()):
            p = img.pixelColor(x, y)
            if abs(p.red() - color.red()) + abs(p.green() - color.green()) + abs(p.blue() - color.blue()) <= tol:
                n += 1
    return n


def test_check_boxes_draw_distinct_states_and_marks_in_both_themes():
    for dark in (True, False):
        off, on, part = _render("off", dark), _render("checked", dark), _render("partial", dark)
        dis = _render("off", dark, enabled=False)
        assert off != on and on != part and off != part and dis != off
        c = check_colors(dark)
        assert _count(on, c["mark"], lo=10, hi=22) >= 8 and _count(part, c["mark"], lo=10, hi=22) >= 6      # tick / dash marks
        assert _count(on, c["fill"]) > 60 and _count(off, c["border"]) >= 20       # fill / visible border


def test_notice_clears_when_the_pins_change(win):
    win.set_pinned(["a"])
    win.set_notice("3 no añadidos")
    win.set_pinned(["a"])
    assert not win.notice.isHidden()
    win.set_pinned(["a", "b"])
    assert win.notice.isHidden()


def test_tree_paints_checked_and_partial_boxes_from_the_real_state(win, real):
    s = _sensor(real, "cpu", "load", "CPU Total")
    item = win._items[s.key]
    win.set_pinned([s.key])
    win.tree.scrollToItem(item)
    QApplication.processEvents()
    c = check_colors(True)
    pin = win.tree.viewport().grab(win.tree.visualRect(win.tree.indexFromItem(item, COL_PIN))).toImage()
    assert _count(pin, c["fill"]) > 60                                   # pinned: filled box with a tick
    off = win.tree.viewport().grab(win.tree.visualRect(win.tree.indexFromItem(item, COL_SUMMARY))).toImage()
    assert _count(off, c["fill"]) > 60                                   # in the summary by default
    cpu = win._nodes["hw:hw//amdcpu/0"]
    assert cpu.checkState(COL_PIN) == PARTIAL
    part = win.tree.viewport().grab(win.tree.visualRect(win.tree.indexFromItem(cpu, COL_PIN))).toImage()
    assert _count(part, c["fill"]) > 60
