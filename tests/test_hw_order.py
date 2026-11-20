"""Panel categories, reordering (drag / keyboard / menu), `hw_order` config and the stable order of the pills."""
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hardware_monitor_widget import config as cfgmod
from hardware_monitor_widget import i18n
from hardware_monitor_widget.config import Config
from hardware_monitor_widget.curation import (CATEGORY_ORDER, MAX_HW_ORDER, Prefs, arrange_hardware, flatten_order,
                                              move_to, order_hardware, order_sensors)
from hardware_monitor_widget.panel import HardwarePanel
from hardware_monitor_widget.pins import MAX_PINS, toggle_bulk, toggle_pin
from hardware_monitor_widget.widget import TaskbarWidget
from tools.synthetic import load_real_snapshot, make_snapshot

CPU, GPU = "hw//amdcpu/0", "hw//gpu-amd/0"
RAM, VRAM = "hw//ram", "hw//vram"
NVME, HDD, SSD = "hw//nvme/2", "hw//hdd/1", "hw//ssd/0"
BOARD = "hw//lpc/nct6798d/0"


@pytest.fixture(scope="module")
def real():
    return load_real_snapshot()


@pytest.fixture
def panel(qapp):
    p = HardwarePanel(dark=True)
    yield p
    p.close()


@pytest.fixture
def realpanel(panel, real):
    panel.set_snapshot(real)
    return panel


def _tops(p):
    return [t.order_id for t in p._tops]


# ------------------------------------------------------------------ pure ordering
def test_default_order_of_the_real_machine(real):
    order = order_hardware(real)
    assert [c for c, _ in order] == ["cpu", "gpu", "ram", "storage", "mainboard"]
    ids = dict(order)
    assert ids["ram"][:2] == [RAM, VRAM] and ids["storage"] == [NVME, HDD, SSD]


def test_hw_order_reorders_ignores_unknown_and_appends_new():
    by_cat = {"cpu": ["c"], "gpu": ["g"], "storage": ["a", "b", "c2"], "network": ["n"]}
    got = arrange_hardware(by_cat, ["cat:storage", "c2", "zzz", "cat:nope", "a", "cat:cpu"])
    assert got == [("storage", ["c2", "a", "b"]), ("cpu", ["c"]), ("gpu", ["g"]), ("network", ["n"])]
    assert arrange_hardware(by_cat, []) == [("cpu", ["c"]), ("gpu", ["g"]), ("storage", ["a", "b", "c2"]),
                                            ("network", ["n"])]


def test_move_to_and_flatten_order():
    assert move_to(["a", "b", "c"], "a", 2) == ["b", "c", "a"]
    assert move_to(["a", "b", "c"], "c", 0) == ["c", "a", "b"]
    assert move_to(["a", "b", "c"], "b", 99) == ["a", "c", "b"]
    assert move_to(["a"], "x", 0) == ["a"]
    flat = flatten_order([("gpu", ["g"]), ("cpu", ["c1", "c2"])], ["old", "c1", "cat:cpu"])
    assert flat == ["cat:gpu", "g", "cat:cpu", "c1", "c2", "old"]
    assert len(flatten_order([("cpu", [f"h{i}" for i in range(500)])])) == MAX_HW_ORDER


def test_order_sensors_is_stable_whatever_the_pin_order(real):
    keys = [s.key for s in real.sensors if s.hw_id in (CPU, GPU, RAM, NVME)][:40]
    by_key = real.by_key()
    forward = order_sensors([by_key[k] for k in keys], real)
    backward = order_sensors([by_key[k] for k in reversed(keys)], real)
    assert [s.key for s in forward] == [s.key for s in backward]
    order = [s.hw_id for s in forward]
    assert order == sorted(order, key=lambda h: [CPU, GPU, RAM, NVME].index(h))


# ------------------------------------------------------------------ config
def test_hw_order_default_and_validation(tmp_path):
    c = Config(tmp_path / "c.json")
    assert c.get("hw_order") == [] and cfgmod.DEFAULTS["hw_order"] == []
    ok = ["cat:storage", NVME, "cat:cpu"]
    c.set("hw_order", ok)
    assert c.get("hw_order") == ok
    for bad in ("cat:storage", [1], [""], ["cat:nope"], ["not-a-hw-id"], ["cat:cpu", "cat:cpu"][:1] + [None],
                ["hw//" + "x" * 300], ["cat:" + "x" * 300], [f"hw//d{i}" for i in range(MAX_HW_ORDER + 1)]):
        with pytest.raises(ValueError):
            c.set("hw_order", bad)
    assert c.get("hw_order") == ok


def test_hw_order_lenient_load_drops_bad_items_dedupes_and_caps():
    clean = cfgmod.clean_value
    got = clean("hw_order", ["cat:gpu", "cat:gpu", "junk", 3, "cat:zzz", "hw//a"] + [f"hw//d{i}" for i in range(300)],
                strict=False)
    assert got[:2] == ["cat:gpu", "hw//a"] and len(got) == MAX_HW_ORDER and len(set(got)) == len(got)


def test_old_config_without_hw_order_takes_the_default(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"config_version": 2, "theme": "dark"}', encoding="utf-8")
    assert Config(p).get("hw_order") == []


# ------------------------------------------------------------------ categories in the panel
def test_real_fixture_has_disks_and_memory_categories(realpanel):
    p = realpanel
    assert _tops(p) == ["cat:cpu", "cat:gpu", "cat:ram", "cat:storage", "cat:mainboard"]
    assert list(p.categories) == ["cat:ram", "cat:storage"]
    disks, mem = p.categories["cat:storage"], p.categories["cat:ram"]
    assert [s.hw_id for s in p._members(disks)] == [NVME, HDD, SSD]
    assert [s.hw_id for s in p._members(mem)] == [RAM, VRAM]
    assert disks.title.full_text() == "Discos" and mem.title.full_text() == "Memoria"
    assert all(disks.isAncestorOf(p.sections[h]) for h in (NVME, HDD, SSD))
    i18n.set_language("en")
    p.retranslate()
    assert p.categories["cat:storage"].title.full_text() == "Disks"


def test_single_component_category_has_no_duplicate_header(realpanel):
    p = realpanel
    cpu = p.sections[CPU]
    assert cpu in p._tops and cpu.order_id == "cat:cpu" and "cat:cpu" not in p.categories
    assert cpu.title.full_text() == p.sections[CPU].title.full_text() and cpu.detail_btn is not None


def test_categories_fold_and_persist_the_state(realpanel):
    p = realpanel
    got = []
    p.collapsed_changed.connect(got.append)
    disks = p.categories["cat:storage"]
    disks.chevron.click()
    assert disks.collapsed and not disks.body.isVisibleTo(p) and got[-1] == ["cat:storage"]
    disks.chevron.click()
    assert not disks.collapsed and got[-1] == []
    p.set_collapsed(["cat:ram", NVME])
    assert p.categories["cat:ram"].collapsed and p.sections[NVME].collapsed
    assert not p.categories["cat:storage"].collapsed
    sec = p.sections[HDD]          # a component folds on its own inside its category
    sec.chevron.click()
    assert sec.collapsed and not p.categories["cat:storage"].collapsed and set(got[-1]) == {"cat:ram", NVME, HDD}


def test_category_eye_is_tristate_and_pins_every_component(realpanel, real):
    p = realpanel
    disks = p.categories["cat:storage"]
    keys = [s.key for s in p.summary_sensors("cat:storage")]
    assert len(keys) == 12 and keys == [k for h in (NVME, HDD, SSD) for k in p.sections[h].keys]
    assert disks.bulk.property("bulkState") == "none"
    p.set_pinned([keys[0]])
    assert disks.bulk.property("bulkState") == "some" and not disks.bulk.isChecked()
    assert "1" in disks.bulk.toolTip() and "12" in disks.bulk.toolTip()
    p.set_pinned(keys)
    assert disks.bulk.property("bulkState") == "all" and disks.bulk.isChecked()
    assert "categoría" in disks.bulk.accessibleName() and "todos fijados" in disks.bulk.accessibleName()
    got = []
    p.bulk_pin_requested.connect(got.append)
    disks.bulk.click()
    assert got == ["cat:storage"]
    # the real pins module does the work: pin all, then unpin all
    pins, skipped = toggle_bulk([], p.summary_sensors("cat:storage"), real)
    assert skipped == 0 and pins == keys
    p.set_pinned(pins)
    assert all(p.sections[h].bulk.property("bulkState") == "all" for h in (NVME, HDD, SSD))
    pins, _ = toggle_bulk(pins, p.summary_sensors("cat:storage"), real)
    p.set_pinned(pins)
    assert disks.bulk.property("bulkState") == "none"


def test_category_bulk_respects_the_pin_limit_and_shows_the_notice(realpanel, real):
    p = realpanel
    filler = [f"x/{i}" for i in range(MAX_PINS - 5)]
    pins, skipped = toggle_bulk(filler, p.summary_sensors("cat:storage"), real, MAX_PINS)
    assert skipped == 7 and len(pins) == MAX_PINS
    p.set_pinned(pins)
    p.set_bulk_notice("cat:storage", skipped)
    notice = p.categories["cat:storage"].notice
    assert not notice.isHidden() and "7" in notice.text()
    p.set_pinned(pins[:-1])       # the pins changed: the "not added" notice is no longer true
    assert notice.isHidden()


def test_hidden_hardware_and_empty_categories_are_not_shown(realpanel, real):
    p = realpanel
    p.set_prefs(Prefs(hidden_hardware=frozenset({NVME, HDD})))
    assert _tops(p) == ["cat:cpu", "cat:gpu", "cat:ram", "cat:storage", "cat:mainboard"]
    assert list(p.sections).count(SSD) == 1 and NVME not in p.sections
    assert "cat:storage" not in p.categories and p.sections[SSD].order_id == "cat:storage"   # one left: no header
    p.set_prefs(Prefs(hidden_hardware=frozenset({NVME, HDD, SSD})))
    assert "cat:storage" not in _tops(p)


def test_header_accessibility_and_unique_names(realpanel):
    p = realpanel
    names = []
    for sec in [*p.categories.values(), *p.sections.values()]:
        btns = [sec.chevron, sec.bulk] + ([sec.detail_btn] if sec.detail_btn is not None else [])
        for b in btns:
            assert b.accessibleName() and b.toolTip()
            names.append(b.accessibleName())
        assert "Alt" in sec.chevron.accessibleDescription()
    assert len(set(names)) == len(names)
    assert p.categories["cat:ram"].title.accessibleName() == "Memoria (categoría)"
    assert p.categories["cat:ram"].detail_btn is None


def test_tab_order_follows_the_display_order(realpanel):
    p = realpanel
    chain, w = [], p._toolbar["theme"]
    for _ in range(1200):
        w = w.nextInFocusChain()
        if w is p._toolbar["theme"]:
            break
        chain.append(w)
    disks, nvme, hdd = p.categories["cat:storage"], p.sections[NVME], p.sections[HDD]
    first_row = next(r for r in p.rows.values() if nvme.isAncestorOf(r))
    assert chain.index(disks.chevron) < chain.index(disks.bulk) < chain.index(nvme.chevron) \
        < chain.index(first_row.eye) < chain.index(hdd.chevron)


# ------------------------------------------------------------------ reordering
def test_drop_category_reorders_saves_and_rebuilds(realpanel):
    p = realpanel
    got = []
    p.hw_order_changed.connect(got.append)
    assert p.drop_item("cat:storage", 0)
    assert _tops(p) == ["cat:storage", "cat:cpu", "cat:gpu", "cat:ram", "cat:mainboard"]
    assert got[-1][:2] == ["cat:storage", NVME] and p.hw_order() == got[-1]
    assert not p.drop_item("cat:storage", 0)        # no change: nothing emitted
    assert len(got) == 1
    assert p.drop_item("cat:cpu", 99)
    assert _tops(p)[-1] == "cat:cpu"


def test_drop_component_stays_inside_its_category(realpanel):
    p = realpanel
    assert p.drop_item(SSD, 0)
    assert [s.hw_id for s in p._members(p.categories["cat:storage"])] == [SSD, NVME, HDD]
    assert _tops(p) == ["cat:cpu", "cat:gpu", "cat:ram", "cat:storage", "cat:mainboard"]
    assert p.drop_item(VRAM, 0)
    assert [s.hw_id for s in p._members(p.categories["cat:ram"])] == [VRAM, RAM]
    assert not p.drop_item("hw//nope", 0)


def test_saved_order_is_restored_and_new_hardware_goes_last(real, panel):
    panel.set_hw_order(["cat:mainboard", "cat:storage", SSD, NVME, "hw//gone", "cat:bogus"])
    panel.set_snapshot(real)
    assert _tops(panel) == ["cat:mainboard", "cat:storage", "cat:cpu", "cat:gpu", "cat:ram"]
    assert [s.hw_id for s in panel._members(panel.categories["cat:storage"])] == [SSD, NVME, HDD]


def test_keyboard_alt_up_down_moves_the_focused_header_and_keeps_focus(realpanel):
    p = realpanel
    p.show()
    got = []
    p.hw_order_changed.connect(got.append)
    gpu = p.sections[GPU]
    QTest.keyClick(gpu.chevron, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert _tops(p)[:2] == ["cat:gpu", "cat:cpu"] and len(got) == 1
    assert p.focusWidget() is p.sections[GPU].chevron          # focus follows the moved header
    QTest.keyClick(p.sections[GPU].chevron, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)    # already first
    assert len(got) == 1
    QTest.keyClick(p.categories["cat:storage"].bulk, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert _tops(p) == ["cat:gpu", "cat:cpu", "cat:ram", "cat:mainboard", "cat:storage"]
    QTest.keyClick(p.sections[SSD].detail_btn, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert [s.hw_id for s in p._members(p.categories["cat:storage"])] == [NVME, SSD, HDD]
    QTest.keyClick(p.sections[SSD].chevron, Qt.Key.Key_Up)         # plain arrows do not move anything
    assert [s.hw_id for s in p._members(p.categories["cat:storage"])] == [NVME, SSD, HDD]


def test_context_menu_has_move_up_and_down_with_disabled_ends(realpanel):
    p = realpanel
    cpu = p.sections[CPU]
    menu = cpu.build_menu()
    assert [a.text() for a in menu.actions()] == ["Subir", "Bajar"]
    assert [a.isEnabled() for a in menu.actions()] == [False, True]
    menu.actions()[1].trigger()
    assert _tops(p)[:2] == ["cat:gpu", "cat:cpu"]
    i18n.set_language("en")
    menu = p.sections[CPU].build_menu()
    assert [a.text() for a in menu.actions()] == ["Move up", "Move down"]
    last = p.categories["cat:storage"] if _tops(p)[-1] == "cat:storage" else p.sections[BOARD]
    assert [a.isEnabled() for a in last.build_menu().actions()] == [True, False]
    menu = p.sections[NVME].build_menu()
    assert [a.isEnabled() for a in menu.actions()] == [False, True]


def test_drag_with_the_mouse_shows_the_indicator_and_drops(realpanel):
    p = realpanel
    p.setFixedSize(420, 2400)
    p.show()
    QApplication.processEvents()
    got = []
    p.hw_order_changed.connect(got.append)
    mb = p.sections[BOARD]
    head = mb._head
    press = QPoint(40, head.height() // 2)
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    def send(kind, local_or_global, buttons=Qt.MouseButton.LeftButton):
        gp = local_or_global
        lp = head.mapFromGlobal(gp)
        ev = QMouseEvent(kind, QPointF(lp), QPointF(gp), Qt.MouseButton.LeftButton
                         if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton, buttons,
                         Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(head, ev)

    g0 = head.mapToGlobal(press)
    send(QEvent.Type.MouseButtonPress, g0)
    cpu = p.sections[CPU]
    target = cpu.mapToGlobal(QPoint(40, 2))                       # top edge of the first block
    send(QEvent.Type.MouseMove, g0 + QPoint(0, -40))
    assert p._drag_item is mb and p._indicator.isVisible()
    send(QEvent.Type.MouseMove, target)
    assert p._indicator.isVisible()
    send(QEvent.Type.MouseButtonRelease, target, Qt.MouseButton.NoButton)
    assert _tops(p)[0] == "cat:mainboard" and got and got[-1][0] == "cat:mainboard"
    assert p._drag_item is None and not p._indicator.isVisible()


def test_click_on_header_still_toggles_and_escape_cancels_a_drag(realpanel):
    p = realpanel
    p.show()
    QApplication.processEvents()
    ram = p.categories["cat:ram"]
    QTest.mouseClick(ram._head, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(60, ram._head.height() // 2))
    assert ram.collapsed
    before = p.hw_order()
    p._drag_begin(p.sections[CPU])
    p.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert p._drag_item is None and p.isVisible() and p.hw_order() == before


def test_snapshot_updates_do_not_rebuild_under_a_drag(realpanel, real):
    p = realpanel
    item = p.sections[CPU]
    p._drag_begin(item)
    p.set_prefs(Prefs(hidden_hardware=frozenset({GPU})))        # structure change while dragging: deferred
    assert GPU in p.sections and p.sections[CPU] is item
    p._cancel_drag()
    p.set_snapshot(real)
    assert GPU not in p.sections


# ------------------------------------------------------------------ pills
def _pill_hw(w):
    return [p.hw_id for p in w.pills()]


@pytest.mark.parametrize("grouped", [True, False])
def test_toggling_a_pin_never_reorders_the_pills(qapp, real, grouped):
    keys = {h: [s.key for s in real.sensors if s.hw_id == h and s.kind in ("temperature", "load")]
            for h in (CPU, GPU, RAM, NVME)}
    w = TaskbarWidget([])
    w.set_grouped(grouped)
    w.set_snapshot(real)
    pins = []
    for h in (NVME, GPU, CPU, RAM):             # pinned in a different order from the panel
        pins.append(keys[h][0])
        w.set_pinned(pins)
    expected = [CPU, GPU, RAM, NVME]
    assert _pill_hw(w) == expected
    before = w.pill_keys()
    by_key = real.by_key()
    for k in list(pins) * 2:                    # unpin and pin again, every sensor, twice
        pins = toggle_pin(pins, by_key[k], real)
        w.set_pinned(pins)
        pins = toggle_pin(pins, by_key[k], real)
        w.set_pinned(pins)
        assert _pill_hw(w) == expected and w.pill_keys() == before
    w.close()


def test_values_inside_a_group_keep_a_stable_order(qapp, real):
    cpu = [s for s in real.sensors if s.hw_id == CPU and s.kind in ("temperature", "load", "power")]
    first = [s.key for s in cpu]
    w = TaskbarWidget(list(reversed(first)))
    w.set_snapshot(real)
    a = [s.key for s in w.pills()[0].segments]
    w.set_pinned(first)
    b = [s.key for s in w.pills()[0].segments]
    assert a == b and len(a) == len(first)
    w.set_pinned([k for k in first if k != a[0]] + [a[0]])      # unpin and pin the first one again
    assert [s.key for s in w.pills()[0].segments] == a
    w.close()


def test_hw_order_sets_the_pill_order_live_grouped_and_ungrouped(qapp):
    snap = make_snapshot()
    w = TaskbarWidget(["auto:cpu_temp", "auto:gpu_temp", "auto:ram_load", "hw//nvme/0/temperature/0",
                       "hw//lpc/nct6798d/temperature/0"])
    w.set_snapshot(snap)
    assert [p.hw_kind for p in w.pills()] == ["cpu", "gpu", "ram", "storage", "mainboard"]
    w.set_hw_order(["cat:mainboard", "cat:storage", "cat:ram"])
    assert [p.hw_kind for p in w.pills()] == ["mainboard", "storage", "ram", "cpu", "gpu"]
    w.set_grouped(False)
    assert [p.hw_kind for p in w.pills()] == ["mainboard", "storage", "ram", "cpu", "gpu"]
    w.set_grouped(True)
    w.set_hw_order([])
    assert [p.hw_kind for p in w.pills()] == ["cpu", "gpu", "ram", "storage", "mainboard"]
    w.close()


def test_memory_and_virtual_memory_are_separate_but_contiguous_pills(qapp, real):
    pins = [next(s.key for s in real.sensors if s.hw_id == h and s.kind == "load") for h in (VRAM, NVME, RAM, CPU)]
    w = TaskbarWidget(pins)
    w.set_snapshot(real)
    assert _pill_hw(w) == [CPU, RAM, VRAM, NVME]
    w.set_hw_order(["cat:storage", "cat:ram", VRAM, RAM])
    assert _pill_hw(w) == [NVME, VRAM, RAM, CPU]
    w.close()


# ------------------------------------------------------------------ controller wiring
@pytest.fixture
def ctl(qapp, monkeypatch, tmp_path):
    from hardware_monitor_widget import app as app_mod
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = Config(tmp_path / "config.json")
    c = app_mod.Controller(qapp, cfg, None)
    yield c
    c.panel.hide()
    c.widget.close()
    i18n.set_language("es")


def test_reordering_in_the_panel_saves_and_redraws_the_bar(ctl, real):
    ctl.on_snapshot(real)
    ctl.panel.set_snapshot(real)
    keys = [next(s.key for s in real.sensors if s.hw_id == h and s.kind == "load") for h in (CPU, NVME)]
    ctl._set_pins(keys)
    assert _pill_hw(ctl.widget) == [CPU, NVME]
    assert ctl.panel.drop_item("cat:storage", 0)
    assert ctl.cfg.get("hw_order")[0] == "cat:storage"
    assert _pill_hw(ctl.widget) == [NVME, CPU]                  # live, no restart
    assert ctl.panel.move_item("cat:cpu", -1)                   # keyboard path
    assert _pill_hw(ctl.widget) == [CPU, NVME]
    assert not ctl.panel.move_item("cat:cpu", -1)               # already first: nothing changes
    assert ctl.panel.move_item("cat:storage", -1)
    assert _pill_hw(ctl.widget) == [NVME, CPU]
    ctl.cfg.save()
    again = Config(ctl.cfg.path)
    assert again.get("hw_order") == ctl.cfg.get("hw_order")
