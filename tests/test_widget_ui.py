import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from hardware_monitor_widget.colors import band_color, contrast_ratio, pill_backgrounds
from hardware_monitor_widget.config import DEFAULTS
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication

from hardware_monitor_widget import custom_icons, i18n, icons, taskbar
from hardware_monitor_widget import win_window as ww
from hardware_monitor_widget.models import Snapshot, SourceStatus
from hardware_monitor_widget.placement import PlacementSettings, ScreenInfo
from hardware_monitor_widget.tray import TrayController
from hardware_monitor_widget.widget import (TaskbarWidget, fit_count, fit_pills, layout_pills, max_pill_extent,
                                            normalize_ui_scale, pill_fraction, scaled_pill_h, warning_text)
from tools.synthetic import make_snapshot


@pytest.fixture
def widget(qapp):
    w = TaskbarWidget(list(DEFAULTS["pinned"]))
    yield w
    w.close()


def test_default_pill_texts_and_bands(widget):
    widget.set_snapshot(make_snapshot(cpu_temp=62.0, gpu_temp=71.0, ram=48.0))
    assert widget.pill_texts() == ["62°C", "23%", "71°C", "38%", "48%"]
    assert widget.pill_bands() == ["amber", "green", "amber", "green", "green"]


def test_critical_band_is_alert(widget):
    widget.set_snapshot(make_snapshot(cpu_temp=90.0))
    assert widget.pill_bands()[0] == "red" and widget.pills()[0].alert
    assert widget.pill_texts()[0] == "! 90°C" and "crítico" in widget.accessibleDescription()


def test_empty_state_shows_dash(widget):
    assert widget.pill_texts() == ["—"]
    widget.set_pinned([])
    assert widget.pill_texts() == ["—"] and widget.pill_keys() == ["__empty__"]


def test_fahrenheit(widget):
    widget.set_snapshot(make_snapshot(cpu_temp=60.0))
    widget.set_fahrenheit(True)
    assert widget.pill_texts()[0] == "140°F"


def test_bar_fraction_uses_limit_or_100():
    snap = make_snapshot(cpu_temp=47.5)
    cpu = next(s for s in snap.sensors if s.name == "Core (Tctl/Tdie)")
    assert pill_fraction(cpu) == pytest.approx(0.5)            # limit 95
    gpu = next(s for s in snap.sensors if s.name == "GPU Core" and s.kind == "temperature")
    assert pill_fraction(gpu) == pytest.approx(0.71)            # default 100
    clock = next(s for s in snap.sensors if s.kind == "clock")
    assert pill_fraction(clock) is None


def test_tooltip_contains_hardware_name_value_minmax(widget):
    widget.set_snapshot(make_snapshot())
    tip = widget.pills()[0].tooltip
    assert "AMD Ryzen 7 5800X" in tip and "Core (Tctl/Tdie)" in tip and "62°C" in tip and "mín 38°C" in tip


def _mouse(widget, etype, pos, button, buttons):
    return QMouseEvent(etype, pos, widget.mapToGlobal(pos), button, buttons, Qt.KeyboardModifier.NoModifier)


def test_click_emits_signal(widget):
    got = []
    widget.clicked.connect(lambda: got.append(1))
    widget.set_snapshot(make_snapshot())
    x, y, w, h = widget._rects[0]
    pos = QPointF(x + w / 2, y + h / 2)
    L = Qt.MouseButton.LeftButton
    widget.mousePressEvent(_mouse(widget, QEvent.Type.MouseButtonPress, pos, L, L))
    widget.mouseReleaseEvent(_mouse(widget, QEvent.Type.MouseButtonRelease, pos, L, Qt.MouseButton.NoButton))
    assert got == [1]


def test_layout_pills_row():
    rects, size = layout_pills([50, 60], 28)
    assert rects[1][0] == 2 + 50 + 4 and size == (2 + 50 + 4 + 60 + 2, 32)


def test_floating_without_taskbar_shows(widget):
    widget.start()
    widget.set_snapshot(make_snapshot())
    assert widget.isVisible()
    widget.stop()


@pytest.mark.parametrize("dark", [True, False])
def test_band_colours_meet_contrast(dark):
    for bg in pill_backgrounds(dark).values():
        for band in ("green", "amber", "orange", "red", "grey"):
            assert contrast_ratio(band_color(band, dark), bg) >= 4.5


def _many(widget, n):
    snap = make_snapshot()
    keys = [s.key for s in snap.sensors][:n]
    widget.set_pinned(keys)
    widget.set_snapshot(snap)
    return snap


def test_fit_count_pure():
    ext = [60] * 6
    assert fit_count(ext, None, 30) == 6
    assert fit_count(ext, 4 + 6 * 60 + 5 * 4, 30) == 6
    k = fit_count(ext, 200, 30)
    assert 4 + 60 * k + 4 * (k - 1) + 4 + 30 <= 200 and k == 2
    assert fit_count(ext, 10, 30) == 1


def test_fit_pills_prefers_compact_before_overflow():
    full, compact = [80] * 5, [60] * 5
    assert fit_pills(full, compact, None, 30) == (False, 5)
    assert fit_pills(full, compact, 4 + 5 * 80 + 16, 30) == (False, 5)
    assert fit_pills(full, compact, 4 + 5 * 60 + 16, 30) == (True, 5)
    comp, n = fit_pills(full, compact, 200, 30)
    assert comp and n < 5


def test_max_pill_extent_limits():
    # 1920 px bar, tray at 1700: 40 % cap = 768; centred icons stop at the middle of the bar
    assert max_pill_extent(0, 1920, 1700, False) == 768
    assert max_pill_extent(0, 1920, 1700, True) == 736 and max_pill_extent(0, 1920, 1100, True) == 136
    assert max_pill_extent(0, 1920, 300, False) == 296
    assert max_pill_extent(0, 1920, 0, False) == 0


def test_overflow_pill_and_tooltip(widget):
    _many(widget, 8)
    full_w = widget.width()
    widget.set_extent_limit(260)
    pills = widget.pills()
    assert pills[-1].overflow and pills[-1].text == f"+{len(widget.hidden_pills())}"
    assert len(pills) - 1 + len(widget.hidden_pills()) == 8 and widget.width() <= 260 < full_w
    tip = pills[-1].tooltip
    assert all(p.a11y in tip for p in widget.hidden_pills())
    widget.set_extent_limit(None)
    assert not widget.pills()[-1].overflow and len(widget.pills()) == 8 and not widget.is_compact()


def test_compact_mode_before_overflow(widget):
    _many(widget, 5)
    normal = widget.width()
    widget.set_extent_limit(normal - 10)
    assert widget.is_compact() and not widget.hidden_pills() and widget.width() < normal


def test_pill_width_is_stable_across_digits(widget):
    widget.set_snapshot(make_snapshot(cpu_temp=9.0))
    w1 = widget._rects[0][2]
    widget.set_snapshot(make_snapshot(cpu_temp=64.0))
    assert widget._rects[0][2] == w1
    widget.set_snapshot(make_snapshot(cpu_temp=100.0))
    assert widget._rects[0][2] >= w1


def test_no_value_pill_and_causes(widget):
    snap = make_snapshot()
    nul = [s if s.kind != "temperature" else s.__class__(**{**s.__dict__, "value": None}) for s in snap.sensors]
    admin = Snapshot(nul, {"native": SourceStatus("native", True, "needs_admin", "x")}, 1.0)
    widget.set_snapshot(admin)
    p = widget.pills()[0]
    assert p.text == "—" and not p.has_value and "permisos de administrador" in p.tooltip
    widget.set_pinned([])
    assert widget.pills()[0].tooltip == "Ningún sensor fijado. Abre el panel y pulsa el ojo"
    widget.set_pinned(list(DEFAULTS["pinned"]))
    widget.set_snapshot(Snapshot(nul[:0], {"native": SourceStatus("native", True, "needs_admin")}, 1.0))
    assert widget.pills()[0].tooltip.startswith("Sin datos: se necesitan permisos de administrador")


def test_stale_pills_dim_and_tooltip(widget):
    snap = make_snapshot()
    widget.set_snapshot(Snapshot(snap.sensors, {"native": SourceStatus("native", True, "stale", "x")}, 1.0))
    assert all(p.stale for p in widget.pills()) and widget.pills()[0].tooltip.startswith("Datos no actualizados")
    assert "Datos no actualizados" in widget.accessibleDescription()
    widget.grab()       # painting stale pills must not fail


def test_accessible_description_is_a_list(widget):
    widget.set_snapshot(make_snapshot())
    desc = widget.accessibleDescription()
    assert desc.count(" · ") == 4 and "AMD Ryzen 7 5800X Core (Tctl/Tdie) 62°C" in desc


def test_decimal_comma_in_pills(widget):
    keys = [s.key for s in make_snapshot().sensors if s.kind == "power"]
    widget.set_pinned(keys)
    widget.set_snapshot(make_snapshot())
    assert widget.pill_texts() == ["65,2 W"]
    i18n.set_language("en")
    widget.set_snapshot(make_snapshot())
    assert widget.pill_texts() == ["65.2 W"]


def test_update_position_throttled_and_neighbours_cached(widget, monkeypatch):
    info = taskbar.TaskbarInfo(rect=(0, 1040, 1920, 1080), tray_rect=(1700, 1040, 1920, 1080), edge="bottom",
                               autohide=False, monitor_rect=(0, 0, 1920, 1080))
    calls = {"info": 0, "nb": 0}

    def get_info():
        calls["info"] += 1
        return info

    def nb(_info, _pid):
        calls["nb"] += 1
        return []

    monkeypatch.setattr(taskbar, "get_taskbar_infos", lambda: [get_info()])
    monkeypatch.setattr(taskbar, "neighbour_widget_rects", nb)
    monkeypatch.setattr(taskbar, "taskbar_icons_centered", lambda: True)
    widget.set_snapshot(make_snapshot())
    widget.update_position()
    assert calls == {"info": 1, "nb": 1}
    for _ in range(5):
        widget._tick()
    assert calls == {"info": 1, "nb": 1}            # throttled: nothing re-queried within a second
    widget.update_position()                        # forced, but the neighbour list is cached
    assert calls == {"info": 2, "nb": 1}
    widget._last_pos = 0.0
    widget._tick()
    assert calls["info"] == 3


def test_docked_limit_never_exceeds_bar_share(widget, monkeypatch):
    info = taskbar.TaskbarInfo(rect=(0, 1040, 1000, 1080), tray_rect=(800, 1040, 1000, 1080), edge="bottom",
                               autohide=False, monitor_rect=(0, 0, 1000, 1080))
    monkeypatch.setattr(taskbar, "get_taskbar_infos", lambda: [info])
    monkeypatch.setattr(taskbar, "neighbour_widget_rects", lambda *_a: [])
    monkeypatch.setattr(taskbar, "taskbar_icons_centered", lambda: False)
    _many(widget, 12)
    widget.update_position()
    assert widget.width() <= 400 and widget.hidden_pills()


def test_tray_any_click_opens_panel_and_has_no_menu(qapp):
    tray = TrayController()
    got = []
    tray.open_requested.connect(lambda: got.append(1))
    R = tray._tray.ActivationReason
    tray._on_activated(R.Trigger)
    tray._on_activated(R.Context)
    assert got == [1, 1] and tray._tray.contextMenu() is None
    tray.set_icon_state("62", "amber", True)
    assert tray._tray.toolTip() == "Monitor de hardware · 62"
    tray.set_icon_state("62", "amber", True, stale=True)
    assert tray._tray.toolTip() == "Monitor de hardware · 62 · Datos no actualizados"


# =========================================================================== placement, scale, icons (T5)
D1, D2 = "\\\\.\\DISPLAY1", "\\\\.\\DISPLAY2"
P = ScreenInfo("aaa", D1, (0, 0, 1920, 1080), (0, 0, 1920, 1040), 1.0, True)
B = ScreenInfo("bbb", D2, (1920, 0, 1280, 1024), (1920, 0, 1280, 984), 1.0, False)
BAR = dict(rect=(0, 1040, 1920, 1080), tray_rect=(1700, 1040, 1920, 1080), edge="bottom", autohide=False,
           monitor_rect=(0, 0, 1920, 1080), device_name=D1)


class Fakes:
    """Handles of the fakes a placement test may flip."""
    def __init__(self):
        self.click_through = []
        self.shift = False
        self.fullscreen = False
        self.neighbours = []


@pytest.fixture
def fake(qapp, monkeypatch):
    e = Fakes()
    monkeypatch.setattr(taskbar, "is_fullscreen_foreground", lambda *_a: e.fullscreen)
    monkeypatch.setattr(taskbar, "is_fullscreen_on_monitor", lambda name: e.fullscreen)
    monkeypatch.setattr(taskbar, "taskbar_icons_centered", lambda: True)
    monkeypatch.setattr(taskbar, "taskbar_widgets_button", lambda: False)
    monkeypatch.setattr(taskbar, "neighbour_widget_rects", lambda *_a: list(e.neighbours))
    monkeypatch.setattr(ww, "set_click_through", lambda hwnd, on: e.click_through.append(on) or True)
    monkeypatch.setattr(ww, "shift_down", lambda: e.shift)
    return e


@pytest.fixture
def placed(fake, monkeypatch):
    made = []

    def make(settings=None, screens=(P, B), bar=None, pinned=None):
        wd = TaskbarWidget(pinned if pinned is not None else list(DEFAULTS["pinned"]))
        real = QGuiApplication.primaryScreen()
        shown = list(screens)
        wd._screens = lambda: [(s, real) for s in shown]
        if bar is not None:
            info = taskbar.TaskbarInfo(**{**BAR, **bar})
            monkeypatch.setattr(taskbar, "get_taskbar_infos", lambda: [info])
        wd.set_snapshot(make_snapshot())
        if settings is not None:
            wd.set_placement_settings(settings)
        wd.start()
        made.append(wd)
        return wd

    yield make
    for wd in made:
        wd.stop()
        wd.deleteLater()


@pytest.fixture
def icon_style():
    yield icons.set_icon_style
    icons.set_icon_style("outline")


def _ev(kind, wd, local, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, QPointF(local), QPointF(wd.pos()) + QPointF(local), button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def _centre(wd):
    x, y, w, h = wd._rects[0]
    return QPoint(int(x + w / 2), int(y + h / 2))


def _drag(wd, delta):
    local = _centre(wd)
    start = QPointF(wd.pos()) + QPointF(local)
    wd.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, wd, local))
    g = start + QPointF(delta)
    wd.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, g - QPointF(wd.pos()), g, Qt.MouseButton.NoButton,
                                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    wd.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, g - QPointF(wd.pos()), g,
                                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                                     Qt.KeyboardModifier.NoModifier))


def _right(wd):
    return wd.x() + wd.width()


# ---- clicks
def test_right_click_requests_the_panel_without_opening_a_menu(widget):
    got, clicked = [], []
    widget.context_menu_requested.connect(got.append)
    widget.clicked.connect(lambda: clicked.append(1))
    widget.set_snapshot(make_snapshot())
    local = _centre(widget)
    R = Qt.MouseButton.RightButton
    widget.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, widget, local, R, R))
    widget.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease, widget, local, R, Qt.MouseButton.NoButton))
    assert len(got) == 1 and isinstance(got[0], QPoint) and clicked == []
    assert not hasattr(widget, "_open_menu")
    widget.set_menu(object())           # legacy no-op
    widget.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, widget, local, R, R))
    assert len(got) == 2


def test_press_and_release_on_different_pills_is_not_a_click(widget):
    got = []
    widget.clicked.connect(lambda: got.append(1))
    widget.set_snapshot(make_snapshot())
    a, b = widget._rects[0], widget._rects[1]
    L = Qt.MouseButton.LeftButton
    widget.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, widget, QPoint(int(a[0]) + 2, int(a[1]) + 2)))
    widget.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease, widget, QPoint(int(b[0]) + 2, int(b[1]) + 2), L,
                                 Qt.MouseButton.NoButton))
    assert got == []


# ---- interface scale
def test_scaled_pill_h_pure():
    assert scaled_pill_h(28, 100) == 28 and scaled_pill_h(28, 130) == 36 and scaled_pill_h(28, 90) == 25
    assert scaled_pill_h(30, 130, 40) == 36                 # 40 px bar - 2 * margin
    assert scaled_pill_h(30, 130, 20) == 30                 # never below what the placement asked for
    assert scaled_pill_h(30, 90, 40) == 27                  # shrinking is never limited
    assert [normalize_ui_scale(v) for v in (50, 90, 115, 400, "x", True, None)] == [90, 90, 115, 130, 100, 100, 100]


def test_ui_scale_scales_pill_height_font_icon_and_width(placed):
    wd = placed()
    base = (wd.pill_height(), wd._font().pixelSize(), wd._icon_size(), wd.width())
    assert base[0] == 28 and wd.ui_scale() == 100
    wd.set_ui_scale(130)
    big = (wd.pill_height(), wd._font().pixelSize(), wd._icon_size(), wd.width())
    assert big[0] == 36 and all(b > a for a, b in zip(base, big))
    assert wd.height() == 36 + 2 * 2 and all(r[3] == 36 for r in wd._rects)
    wd.set_ui_scale(90)
    small = (wd.pill_height(), wd._icon_size(), wd.width())
    assert small[0] == 25 and small[1] <= base[2] and small[2] < base[3]
    wd.set_ui_scale("bad")
    assert wd.ui_scale() == 100 and wd.pill_height() == 28
    wd.grab()           # painting at every scale must not fail


def test_ui_scale_before_start_applies_when_placed(widget):
    widget.set_ui_scale(115)
    assert widget.pill_height() == round(28 * 1.15)
    widget.set_snapshot(make_snapshot())
    assert widget.ui_scale() == 115


def test_ui_scale_on_taskbar_never_outgrows_the_bar(placed):
    wd = placed(bar=dict(rect=(0, 1040, 1920, 1080)))       # 40 px bar -> base 30
    assert wd.pill_height() == 30
    wd.set_ui_scale(130)
    assert wd.pill_height() == 36 and wd.height() <= 40


# ---- icon style "none" and custom icons
def test_style_none_removes_the_icon_slot(placed, icon_style):
    wd = placed()
    with_icon = wd._rects[0][2]
    icon_style("none")
    wd.refresh_style()
    assert wd._rects[0][2] == with_icon - wd._icon_size() - wd._gap(False)
    wd.grab()


def test_custom_icon_resolver_gets_kind_hw_and_key(placed, monkeypatch):
    wd = placed()
    calls, drawn = [], []

    def resolver(kind, hw_id, key):
        calls.append((kind, hw_id, key))
        return "0123456789abcdef.png"

    monkeypatch.setattr(custom_icons, "usable", lambda name: name == "0123456789abcdef.png")
    monkeypatch.setattr(custom_icons, "draw_custom_icon", lambda p, r, f, op=1.0: drawn.append(f) or True)
    wd.set_icon_resolver(resolver)
    wd.grab()
    sensor = next(s for s in make_snapshot().sensors if s.key == wd.pill_keys()[0])
    assert (sensor.hw_kind, sensor.hw_id, sensor.key) in calls
    assert wd.pills()[0].hw_id == sensor.hw_id
    assert drawn and set(drawn) == {"0123456789abcdef.png"}


def test_custom_icon_is_drawn_even_with_style_none(placed, monkeypatch, icon_style):
    wd = placed()
    icon_style("none")
    wd.refresh_style()
    plain = wd._rects[0][2]
    monkeypatch.setattr(custom_icons, "usable", lambda name: True)
    monkeypatch.setattr(custom_icons, "draw_custom_icon", lambda *a, **k: True)
    kind0 = wd.pills()[0].hw_kind
    wd.set_icon_resolver(lambda k, h, key: "0123456789abcdef.png" if k == kind0 else None)
    assert wd._rects[0][2] > plain          # this pill got its icon slot back
    wd.grab()


def test_broken_or_unusable_custom_icon_falls_back_to_the_glyph(placed, monkeypatch):
    wd = placed()
    base = wd._rects[0][2]

    def boom(*_a):
        raise RuntimeError("boom")

    wd.set_icon_resolver(boom)
    assert wd._rects[0][2] == base
    monkeypatch.setattr(custom_icons, "usable", lambda name: False)
    wd.set_icon_resolver(lambda *a: "0123456789abcdef.png")
    assert wd._rects[0][2] == base
    wd.set_icon_resolver(None)
    wd.grab()


def test_empty_pill_never_uses_custom_icons(placed, monkeypatch):
    asked = []
    monkeypatch.setattr(custom_icons, "usable", lambda name: True)
    wd = placed(pinned=[])
    wd.set_icon_resolver(lambda k, h, key: asked.append(key) or "0123456789abcdef.png")
    assert wd.pill_keys() == ["__empty__"] and asked == []


# ---- floating: drag, snap, anchors, monitors
def test_floating_default_bottom_right_of_primary(placed):
    wd = placed(PlacementSettings(mode="floating"))
    assert wd.isVisible() and wd._floating and wd.current_screen_key() == "aaa"
    assert (_right(wd), wd.y() + wd.height()) == (1920 - 12, 1040 - 12)
    assert wd.windowFlags() & Qt.WindowType.WindowStaysOnTopHint and wd.windowFlags() & Qt.WindowType.Tool
    assert wd.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert wd.cursor().shape() == Qt.CursorShape.SizeAllCursor


def test_drag_snaps_to_nearest_anchor_and_reports_it(placed):
    wd = placed(PlacementSettings(mode="floating"))
    moved, clicked = [], []
    wd.floating_moved.connect(lambda k, e, fs: moved.append((k, e, fs)))
    wd.clicked.connect(lambda: clicked.append(1))
    _drag(wd, QPoint(-3000, -3000))
    assert clicked == [] and len(moved) == 1
    key, entry, fs = moved[0]
    assert key == "aaa" and fs is False and entry == {"anchor": "top-left", "dx": 0, "dy": 0}
    assert (wd.x(), wd.y()) == (0, 0)
    assert wd.placement_settings().floating_positions["aaa"] == entry       # applied without the controller


def test_drag_across_monitors_changes_the_monitor_choice(placed):
    wd = placed(PlacementSettings(mode="floating"))
    moved, mon = [], []
    wd.floating_moved.connect(lambda k, e, fs: moved.append((k, e)))
    wd.monitor_changed.connect(mon.append)
    _drag(wd, QPoint(1500, 0))
    assert moved and moved[0][0] == "bbb" and mon == ["bbb"]
    assert wd.current_screen_key() == "bbb" and wd.x() >= 1920
    assert wd.placement_settings().monitor == "bbb"


def test_drag_on_primary_keeps_primary_choice(placed):
    wd = placed(PlacementSettings(mode="floating", floating_positions={"primary": {"anchor": "top-left"}}))
    mon = []
    wd.monitor_changed.connect(mon.append)
    _drag(wd, QPoint(-50, -50))
    assert mon == [] and "primary" not in wd.placement_settings().floating_positions


def test_click_without_moving_is_a_click_not_a_drag(placed):
    wd = placed(PlacementSettings(mode="floating"))
    moved, clicked = [], []
    wd.floating_moved.connect(lambda *a: moved.append(a))
    wd.clicked.connect(lambda: clicked.append(1))
    pos = wd.pos()
    _drag(wd, QPoint(1, 1))
    assert clicked == [1] and moved == [] and wd.pos() == pos


def test_set_floating_anchor_parks_on_the_corner(placed):
    wd = placed(PlacementSettings(mode="floating"))
    moved = []
    wd.floating_moved.connect(lambda k, e, fs: moved.append((k, e, fs)))
    wd.set_floating_anchor("top-right")
    assert moved == [("aaa", {"anchor": "top-right", "dx": 0, "dy": 0}, False)]
    assert (_right(wd), wd.y()) == (1920, 0)
    wd.set_floating_anchor("middle-left")
    assert wd.x() == 0 and abs(wd.y() + wd.height() / 2 - 520) <= 1
    wd.set_floating_anchor("nowhere")
    assert len(moved) == 2


def test_saved_position_and_chosen_monitor_are_used(placed):
    wd = placed(PlacementSettings(mode="floating", monitor="bbb",
                                  floating_positions={"bbb": {"anchor": "top-left", "dx": 10, "dy": 20}}))
    assert wd.current_screen_key() == "bbb" and (wd.x(), wd.y()) == (1930, 20)


def test_missing_monitor_falls_back_to_primary_without_touching_settings(placed):
    wd = placed(PlacementSettings(mode="floating", monitor="gone"))
    assert wd.current_screen_key() == "aaa" and wd.placement_settings().monitor == "gone"


def test_settings_are_copied_not_shared(placed):
    s = PlacementSettings(mode="floating", floating_positions={"aaa": {"anchor": "top-left", "dx": 0, "dy": 0}})
    wd = placed(s)
    s.floating_positions["zzz"] = {}
    assert "zzz" not in wd.placement_settings().floating_positions
    wd.placement_settings().floating_positions.clear()
    assert wd.placement_settings().floating_positions


def test_no_taskbar_on_a_monitor_floats_there_and_is_reported(placed):
    wd = placed(PlacementSettings(mode="taskbar", monitor="bbb"), bar=dict())     # only the primary has a bar
    assert wd.current_screen_key() == "bbb" and wd._floating and not wd.monitor_has_taskbar()
    labels = dict(wd.list_screens())
    assert labels["aaa"] == "Principal" and labels["bbb"] == "Monitor 2 (sin barra: flotante)"


# ---- taskbar anchors and neighbours
def test_docks_right_of_the_bar_centred_on_the_tray_row(placed):
    wd = placed(bar=dict())
    assert not wd._floating and _right(wd) == 1700 - 4 and wd.y() + wd.height() // 2 == 1060


def test_docks_left_of_a_neighbour_widget(placed, fake):
    fake.neighbours = [(1400, 1040, 1696, 1080)]
    wd = placed(bar=dict())
    assert _right(wd) == 1400 - 4 and wd.width() <= 436


def test_left_anchor_apart_from_neighbours(placed, fake):
    wd = placed(PlacementSettings(anchor="left"), bar=dict())
    assert wd.x() == 4
    fake.neighbours = [(4, 1040, 150, 1080)]
    wd._nb_cache = None             # the neighbour list is cached for a second
    wd.update_position()
    assert wd.x() == 150 + 4


def test_left_anchor_without_centred_icons_falls_back_and_warns_once(qapp, fake, monkeypatch):
    monkeypatch.setattr(taskbar, "taskbar_icons_centered", lambda: False)
    monkeypatch.setattr(taskbar, "get_taskbar_infos", lambda: [taskbar.TaskbarInfo(**BAR)])
    wd = TaskbarWidget(list(DEFAULTS["pinned"]))
    real = QGuiApplication.primaryScreen()
    wd._screens = lambda: [(P, real)]
    got = []
    wd.placement_warning.connect(got.append)
    wd.set_placement_settings(PlacementSettings(anchor="left"))
    wd.set_snapshot(make_snapshot())
    wd.start()
    wd.update_position()
    wd.update_position()
    assert got == ["left_needs_centered_icons"] and _right(wd) == 1700 - 4
    assert warning_text("left_needs_centered_icons") and warning_text("nope") == ""
    wd.stop()


# ---- fullscreen
def test_hidden_over_fullscreen_by_default(placed, fake):
    wd = placed(bar=dict())
    assert wd.isVisible()
    fake.fullscreen = True
    wd.update_position()
    assert not wd.isVisible()
    fake.fullscreen = False
    wd.update_position()
    assert wd.isVisible() and not wd.is_passive()


def test_visible_in_fullscreen_is_click_through_until_shift(placed, fake):
    wd = placed(PlacementSettings(show_in_fullscreen=True), bar=dict())
    clicked = []
    wd.clicked.connect(lambda: clicked.append(1))
    fake.fullscreen = True
    wd.update_position()
    assert wd.isVisible() and wd.is_passive() and fake.click_through[-1] is True
    local = _centre(wd)
    L = Qt.MouseButton.LeftButton
    wd.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, wd, local))
    wd.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease, wd, local, L, Qt.MouseButton.NoButton))
    assert clicked == []                                         # passive: ignores the mouse
    fake.shift = True
    wd._shift_tick()
    assert fake.click_through[-1] is False
    wd.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, wd, local))
    wd.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease, wd, local, L, Qt.MouseButton.NoButton))
    assert clicked == [1]
    fake.shift = False
    wd._shift_tick()
    assert fake.click_through[-1] is True
    fake.fullscreen = False
    wd.update_position()
    assert not wd.is_passive() and fake.click_through[-1] is False


def test_fullscreen_position_uses_the_anchor_or_the_saved_spot(placed, fake):
    wd = placed(PlacementSettings(show_in_fullscreen=True, fullscreen_placement="top-left"), bar=dict())
    fake.fullscreen = True
    wd.update_position()
    assert wd._floating and (wd.x(), wd.y()) == (0, 0)
    wd.set_placement_settings(PlacementSettings(
        show_in_fullscreen=True, fullscreen_placement="floating",
        fullscreen_positions={"aaa": {"anchor": "top-right", "dx": -10, "dy": 5}}))
    assert (_right(wd), wd.y()) == (1920 - 10, 5)
    fake.fullscreen = False
    wd.update_position()
    assert not wd._floating and _right(wd) == 1700 - 4          # back on the taskbar afterwards


def test_dropping_over_fullscreen_stores_the_fullscreen_spot_only(placed, fake):
    wd = placed(PlacementSettings(show_in_fullscreen=True, fullscreen_placement="top-left"), bar=dict())
    moved, fsp = [], []
    wd.floating_moved.connect(lambda k, e, fs: moved.append((k, e, fs)))
    wd.fullscreen_placement_changed.connect(fsp.append)
    fake.fullscreen = True
    wd.update_position()
    fake.shift = True
    wd._shift_tick()                                              # interactive again
    _drag(wd, QPoint(3000, 3000))
    assert moved and moved[0][2] is True and moved[0][1]["anchor"] == "bottom-right"
    assert fsp == ["floating"]
    s = wd.placement_settings()
    assert s.fullscreen_positions["aaa"]["anchor"] == "bottom-right" and s.floating_positions == {}


def test_stop_hides_and_clears_passive(placed, fake):
    wd = placed(PlacementSettings(show_in_fullscreen=True), bar=dict())
    fake.fullscreen = True
    wd.update_position()
    assert wd.is_passive()
    wd.stop()
    assert not wd.isVisible() and not wd.is_passive()


def test_signals_exist(widget):
    for name in ("clicked", "context_menu_requested", "floating_moved", "monitor_changed",
                 "fullscreen_placement_changed", "placement_warning"):
        assert hasattr(widget, name)
