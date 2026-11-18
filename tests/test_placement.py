"""Pure placement maths (no Qt): anchors, snapping, docking next to neighbours, fullscreen, fallbacks."""
import pytest

from hardware_monitor_widget import placement as pl
from hardware_monitor_widget import taskbar
from hardware_monitor_widget.config import FLOATING_ANCHORS
from hardware_monitor_widget.placement import (DEFAULT_FLOATING, Env, Placement, PlacementSettings, ScreenInfo,
                                               choose_placement, compute_placement, docked_edge_left,
                                               lookup_floating, max_pill_extent, pill_height_for, resolve_screen,
                                               snap_anchor, taskbar_for_screen)

D1, D2 = "\\\\.\\DISPLAY1", "\\\\.\\DISPLAY2"
P = ScreenInfo("aaa", D1, (0, 0, 1920, 1080), (0, 0, 1920, 1040), 1.0, True)
B = ScreenInfo("bbb", D2, (1920, 0, 1280, 1024), (1920, 0, 1280, 984), 1.0, False)


def bar(**kw):
    base = dict(rect=(0, 1040, 1920, 1080), tray_rect=(1700, 1040, 1920, 1080), edge="bottom", autohide=False,
                monitor_rect=(0, 0, 1920, 1080), device_name=D1)
    base.update(kw)
    return taskbar.TaskbarInfo(**base)


def env(info=None, **kw):
    base = dict(info=bar() if info is None else info, screen_name=D1, size=(300, 32), available=(0, 0, 1920, 1040))
    base.update(kw)
    return Env(**base)


# --------------------------------------------------------------------------- small pure helpers
def test_pill_height_keeps_project_range():
    assert pill_height_for(40) == 30
    assert pill_height_for(20) == pl.MIN_PILL_H == 24
    assert pill_height_for(100) == pl.MAX_PILL_H == 36


def test_max_pill_extent_limits():
    # 1920 px bar, tray at 1700: 40 % cap = 768; centred icons stop at the middle of the bar
    assert max_pill_extent(0, 1920, 1700, False) == 768
    assert max_pill_extent(0, 1920, 1700, True) == 736 and max_pill_extent(0, 1920, 1100, True) == 136
    assert max_pill_extent(0, 1920, 300, False) == 296
    assert max_pill_extent(0, 1920, 0, False) == 0


def test_widget_reexports_the_moved_helpers():
    from hardware_monitor_widget import widget
    assert widget.max_pill_extent is max_pill_extent and widget.pill_height_for is pill_height_for


def test_docked_edge_left_chain():
    assert docked_edge_left(4, []) == 4
    assert docked_edge_left(4, [(4, 0, 120, 40)]) == 120
    assert docked_edge_left(4, [(4, 0, 120, 40), (130, 0, 200, 40)]) == 200      # contiguous chain
    assert docked_edge_left(4, [(900, 0, 1000, 40)]) == 4                        # far away: not a neighbour
    assert docked_edge_left(60, [(0, 0, 50, 40)]) == 60                          # entirely left of the start


def test_resolve_screen_and_missing_monitor():
    assert resolve_screen("primary", [B, P]) is P
    assert resolve_screen("bbb", [P, B]) is B
    assert resolve_screen("gone", [P, B]) is P
    assert resolve_screen("primary", []) is None


def test_taskbar_for_screen_matches_device_name_not_size():
    a, b = bar(device_name=D1), bar(device_name=D2, kind="secondary")
    assert taskbar_for_screen(P, [a, b]) is a and taskbar_for_screen(B, [a, b]) is b
    assert taskbar_for_screen(B, [a]) is None
    legacy = bar(device_name="")
    assert taskbar_for_screen(P, [legacy], {D1}) is legacy and taskbar_for_screen(B, [legacy], {D1, D2}) is None


def test_lookup_floating_validates_entries():
    assert lookup_floating({}, "aaa") == DEFAULT_FLOATING
    assert lookup_floating({"aaa": {"anchor": "top-left", "dx": 3, "dy": -4}}, "aaa") == ("top-left", 3, -4)
    assert lookup_floating({"primary": {"anchor": "top-left", "dx": 1, "dy": 2}}, "aaa", "primary") == ("top-left", 1, 2)
    assert lookup_floating({"primary": {"anchor": "top-left"}}, "aaa", "bbb") == DEFAULT_FLOATING
    assert lookup_floating({"aaa": {"anchor": "nowhere"}}, "aaa") == DEFAULT_FLOATING
    assert lookup_floating({"aaa": {"anchor": "top-left", "dx": "x"}}, "aaa") == DEFAULT_FLOATING
    assert lookup_floating("junk", "aaa") == DEFAULT_FLOATING


@pytest.mark.parametrize("anchor", FLOATING_ANCHORS)
def test_snap_anchor_roundtrip(anchor):
    av = (0, 0, 1000, 800)
    w, h = 200, 32
    x, y = pl._anchor_xy(anchor, av, w, h)
    assert snap_anchor((x + 5, y - 3 if "top" not in anchor else y + 3, w, h), av)[0] == anchor
    a, dx, dy = snap_anchor((x, y, w, h), av)
    assert (a, dx, dy) == (anchor, 0, 0)


def test_placement_settings_defaults():
    s = PlacementSettings()
    assert (s.mode, s.anchor, s.monitor) == ("taskbar", "right", "primary")
    assert s.floating_positions == {} and s.fullscreen_positions == {} and not s.show_in_fullscreen
    assert PlacementSettings().floating_positions is not s.floating_positions       # no shared mutable default


# --------------------------------------------------------------------------- floating
def test_floating_default_is_bottom_right_with_margin():
    res = compute_placement(env(), Placement("floating"))
    assert res.visible and res.rect == (1920 - 300 - 12, 1040 - 32 - 12, 300, 32)
    assert res.max_extent == int(1920 * 0.9) and res.pill_h == pl.FLOATING_PILL_H


def test_floating_offset_is_clamped_to_available_area():
    res = compute_placement(env(), Placement("floating", float_anchor="top-left", dx=-50, dy=-50))
    assert res.rect[:2] == (0, 0)
    res = compute_placement(env(), Placement("floating", float_anchor="top-left", dx=99999, dy=99999))
    assert res.rect[:2] == (1920 - 300, 1040 - 32)


def test_floating_unknown_anchor_uses_default_and_no_screen_hides():
    res = compute_placement(env(), Placement("floating", float_anchor="bogus", dx=0, dy=0))
    assert res.rect[:2] == (1920 - 300, 1040 - 32)
    res = compute_placement(env(available=None), Placement("floating"))
    assert not res.visible and res.reason == "no_screen"


# --------------------------------------------------------------------------- taskbar, right anchor
def test_right_anchor_sits_left_of_tray_centred_on_its_row():
    res = compute_placement(env(), Placement())
    assert res.visible and not res.vertical and res.reason == "ok"
    assert res.rect == (1700 - 4 - 300, 1060 - 16, 300, 32)
    assert res.max_extent == 736 and res.pill_h == 30


def test_dock_edge_moves_pills_left_of_neighbours_and_limits_extent():
    res = compute_placement(env(dock_edge=1400), Placement())
    assert res.rect[0] + res.rect[2] == 1400 - 4
    assert res.max_extent == max_pill_extent(0, 1920, 1400, True) == 436
    # a dock edge right of the tray is ignored (never pushes the pills into the tray)
    res = compute_placement(env(dock_edge=1850), Placement())
    assert res.rect[0] + res.rect[2] == 1700 - 4


def test_extent_never_exceeds_bar_share_without_centering():
    res = compute_placement(env(info=bar(rect=(0, 1040, 1000, 1080), tray_rect=(800, 1040, 1000, 1080),
                                         monitor_rect=(0, 0, 1000, 1080)), icons_centered=False), Placement())
    assert res.max_extent == 400


def test_vertical_bar_uses_column_extent():
    info = bar(rect=(0, 0, 60, 1080), tray_rect=(0, 900, 60, 1080), edge="left")
    res = compute_placement(env(info=info, size=(80, 200)), Placement())
    assert res.vertical and res.max_extent == max_pill_extent(0, 1080, 900, False)
    assert res.rect[1] + res.rect[3] == 900 - 4


def test_dpr_scales_rects_and_limit():
    info = bar(rect=(0, 1560, 2880, 1620), tray_rect=(2500, 1560, 2880, 1620), monitor_rect=(0, 0, 2880, 1620))
    res = compute_placement(Env(info=info, screen_name=D1, dpr=1.5, size=(300, 32), available=(0, 0, 1920, 1080)),
                            Placement())
    assert res.rect[2:] == (300, 32) and res.rect[0] + res.rect[2] == round((2500 - 6) / 1.5)
    assert res.pill_h == pill_height_for(60 / 1.5)


# --------------------------------------------------------------------------- taskbar, left anchor
def test_left_anchor_starts_at_the_left_end_and_caps_extent():
    res = compute_placement(env(), Placement(anchor="left"))
    assert res.reason == "ok" and res.rect[0] == 4 and res.rect[1] == 1060 - 16
    assert res.max_extent == int(1920 * 0.25 - 4)


def test_left_anchor_leaves_the_widgets_button_free():
    res = compute_placement(env(widgets_button=None), Placement(anchor="left"))
    assert res.rect[0] == 40 + 8
    res = compute_placement(env(widgets_button=True), Placement(anchor="left"))
    assert res.rect[0] == 48


def test_left_anchor_stays_right_of_neighbours_at_the_left_end():
    res = compute_placement(env(dock_edge_left=100), Placement(anchor="left"))
    assert res.rect[0] == 100 + 4
    assert res.max_extent == int(1920 * 0.25 - 104)
    res = compute_placement(env(dock_edge_left=1500), Placement(anchor="left"))      # never into the tray
    assert res.rect[0] + res.rect[2] <= 1700 - 4 and res.max_extent == pl.LEFT_MIN_LIMIT


@pytest.mark.parametrize("centered", [False, None])
def test_left_anchor_falls_back_to_right_without_centered_icons(centered):
    res = compute_placement(env(icons_centered=centered), Placement(anchor="left"))
    assert res.reason == pl.REASON_LEFT_NEEDS_CENTERED and res.visible
    assert res.rect[0] + res.rect[2] == 1700 - 4


def test_left_anchor_falls_back_on_vertical_bars():
    info = bar(rect=(0, 0, 60, 1080), tray_rect=(0, 900, 60, 1080), edge="left")
    res = compute_placement(env(info=info, size=(80, 200)), Placement(anchor="left"))
    assert res.reason == pl.REASON_LEFT_NEEDS_CENTERED and res.vertical


# --------------------------------------------------------------------------- hidden cases and fullscreen
def test_hidden_reasons():
    assert compute_placement(Env(info=None, size=(10, 10)), Placement()).reason == "no_taskbar"
    assert compute_placement(env(has_pills=False), Placement()).reason == "no_pills"
    assert compute_placement(env(has_pills=False), Placement("floating")).reason == "no_pills"
    tucked = bar(rect=(0, 1076, 1920, 1116), autohide=True)
    assert compute_placement(env(info=tucked), Placement()).reason == "tucked"


def test_no_taskbar_floats_on_the_monitor_when_asked():
    res = compute_placement(Env(info=None, floating_fallback=True, size=(300, 32), available=(0, 0, 1920, 1040)),
                            Placement("taskbar", float_anchor="top-right", dx=-10, dy=10))
    assert res.visible and res.reason == pl.REASON_NO_TASKBAR_FLOATING and res.rect[:2] == (1920 - 300 - 10, 10)


def test_fullscreen_hides_unless_kept_visible_and_then_is_passive():
    assert compute_placement(env(fullscreen=True), Placement()).reason == "fullscreen"
    assert compute_placement(env(fullscreen=True), Placement("floating")).reason == "fullscreen"
    res = compute_placement(env(fullscreen=True, show_in_fullscreen=True), Placement())
    assert res.visible and res.fullscreen_passive
    assert not compute_placement(env(show_in_fullscreen=True), Placement()).fullscreen_passive


def test_choose_placement_in_fullscreen():
    base = Placement()
    over = env(fullscreen=True, show_in_fullscreen=True)
    assert choose_placement(over, base, "same", {}, "aaa") is base
    assert choose_placement(env(), base, "top-left", {}, "aaa") is base                   # no fullscreen app
    assert choose_placement(env(fullscreen=True), base, "top-left", {}, "aaa") is base    # not kept visible
    p = choose_placement(over, base, "top-left", {}, "aaa")
    assert (p.mode, p.float_anchor, p.dx, p.dy) == ("floating", "top-left", 0, 0)
    p = choose_placement(over, base, "floating", {"aaa": {"anchor": "top-center", "dx": 5, "dy": 6}}, "aaa")
    assert (p.mode, p.float_anchor, p.dx, p.dy) == ("floating", "top-center", 5, 6)
    p = choose_placement(over, base, "floating", {}, "aaa")
    assert (p.float_anchor, p.dx, p.dy) == DEFAULT_FLOATING
    assert choose_placement(over, base, "bogus", {}, "aaa") is base
