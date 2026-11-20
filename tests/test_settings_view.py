"""Settings view: navigation, toggles, in-place accordions, dynamic pages, keyboard, themes and scale."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from hardware_monitor_widget import release_info
from hardware_monitor_widget.i18n import set_language, tr_in
from hardware_monitor_widget.settings_model import build_settings_model
from hardware_monitor_widget.settings_view import MIN_TARGET, SettingsView

from test_settings_model import State


@pytest.fixture
def st():
    set_language("en")
    return State()


class Host(QWidget):
    """Stands in for the panel's QStackedWidget page."""

    def __init__(self, view):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addWidget(view)
        self.resize(360, 520)


@pytest.fixture
def make(qapp, st):
    created = []

    def factory(dark=True, scale=1.0):
        model = build_settings_model(st.cb())
        v = SettingsView(model, dark=dark, scale=scale)
        host = Host(v)
        host.show()
        host.activateWindow()
        QTest.qWaitForWindowActive(host)      # hasFocus() needs an active window
        created.append(host)
        return v
    yield factory
    for h in created:
        h.close()


@pytest.fixture
def view(make):
    return make()


def click(row):
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)


def key(widget, k, mod=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(widget, k, mod)


def test_root_rows_and_title(view):
    assert view.title_text() == "Settings"
    assert list(view.rows()) == ["display", "personalization", "position", "alerts", "custom_icons", "logs",
                                 "start_with_windows", "updates", "about"]
    assert all(r.kind == "page" for k, r in view.rows().items() if k != "start_with_windows")
    assert view.row("start_with_windows").kind == "toggle"
    assert all(r.height() >= MIN_TARGET for r in view.rows().values())
    assert view.back_button().width() >= MIN_TARGET and view.back_button().height() >= MIN_TARGET


def test_enter_page_and_back_with_arrow(view):
    pages = []
    view.page_changed.connect(pages.append)
    left = []
    view.leave_requested.connect(lambda: left.append(1))
    click(view.row("logs"))
    assert view.path_ids == ["root", "logs"] and view.title_text() == "Logs"
    assert list(view.rows()) == ["open_log", "open_logs_folder", "log_debug", "history_enabled", "clear_history"]
    view.back_button().click()
    assert view.path_ids == ["root"] and not left
    view.back_button().click()                               # from the root: leave to the summary
    assert left == [1] and pages == ["logs", "root"]


def test_direct_page_back_leaves(view):
    left = []
    view.leave_requested.connect(lambda: left.append(1))
    view.show_page("position", direct=True)
    assert view.path_ids == ["position"]
    view.back_button().click()
    assert left == [1]
    view.show_page("position")
    assert view.path_ids == ["root", "position"]
    view.reset()
    assert view.path_ids == ["root"] and view.open_choice is None


@pytest.mark.parametrize("k,mod", [(Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
                                   (Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier),
                                   (Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)])
def test_back_keys(view, k, mod):
    left = []
    view.leave_requested.connect(lambda: left.append(1))
    click(view.row("position"))
    key(view.row("placement"), k, mod)
    assert view.path_ids == ["root"]
    key(view.row("position"), k, mod)
    assert left == [1]


def test_toggle_changes_state_in_place(view, st):
    click(view.row("logs"))
    row = view.row("log_debug")
    assert row.isCheckable() and not row.isChecked()
    click(row)
    assert st.d["log_debug"] is True and ("set_log_debug", True) in st.calls
    assert view.row("log_debug").isChecked() and view.page_id == "logs"
    click(view.row("log_debug"))
    assert not view.row("log_debug").isChecked()


def test_toggle_reflects_real_state_when_the_change_fails(view, st):
    cb = st.cb()
    cb.set_startup = lambda v: st.calls.append(("set_startup", v))      # the real state stays off
    view.set_model(build_settings_model(cb))
    click(view.row("start_with_windows"))
    assert ("set_startup", True) in st.calls and not view.row("start_with_windows").isChecked()


def test_failing_callback_is_swallowed_and_view_refreshes(view, st):
    cb = st.cb()

    def boom(_v):
        raise RuntimeError("nope")
    cb.set_log_debug = boom
    cb.open_log = lambda: 1 / 0
    view.set_model(build_settings_model(cb))
    click(view.row("logs"))
    click(view.row("log_debug"))
    click(view.row("open_log"))
    assert view.page_id == "logs" and not view.row("log_debug").isChecked()


def test_choice_accordion_open_pick_and_stay_open(view, st):
    click(view.row("personalization"))
    row = view.row("palette")
    assert row.value_text() == "Classic" and view.open_choice is None
    assert row.accessibleName() == "Colors, Classic, collapsed"
    click(row)
    assert view.open_choice == "palette" and view.row("palette").accessibleName() == "Colors, Classic, expanded"
    assert [r for r in view.rows() if r.startswith("palette:")] == [
        "palette:classic", "palette:colorblind", "palette:pastel", "palette:high_contrast",
        "palette:mono", "palette:custom"]
    assert view.row("palette:classic").isChecked() and not view.row("palette:pastel").isChecked()
    click(view.row("palette:pastel"))
    assert st.d["palette"] == "pastel" and view.open_choice == "palette"
    assert view.row("palette:pastel").isChecked() and view.row("palette").value_text() == "Pastel"
    click(view.row("palette:mono"))
    assert st.d["palette"] == "mono"
    click(view.row("palette"))
    assert view.open_choice is None and "palette:mono" not in view.rows()


def test_disabled_option_cannot_be_picked(view, st):
    st.d["monitor"] = "zz"
    click(view.row("position"))
    click(view.row("monitor"))
    opt = view.row("monitor:zz")
    assert not opt.isEnabled()
    click(opt)
    assert not [c for c in st.calls if c[0] == "set_monitor"]
    assert view.row("monitor").value_text() == "Disconnected monitor (using the primary)"


def test_only_one_accordion_open_and_navigation_folds_it(view):
    click(view.row("personalization"))
    click(view.row("palette"))
    click(view.row("icon_style"))
    assert view.open_choice == "icon_style" and not any(r.startswith("palette:") for r in view.rows())
    view.back_button().click()
    click(view.row("position"))
    assert view.open_choice is None


def test_layout_changed_on_accordion(view):
    seen = []
    view.layout_changed.connect(lambda: seen.append(1))
    click(view.row("personalization"))
    n = len(seen)
    h = view.content_height()
    click(view.row("palette"))
    assert len(seen) > n and view.content_height() > h


def test_action_calls_callback(view, st):
    click(view.row("logs"))
    click(view.row("open_log"))
    click(view.row("open_logs_folder"))
    assert [c[0] for c in st.calls] == ["open_log", "open_logs_folder"] and view.page_id == "logs"


def test_disabled_action_does_not_run(view, st):
    click(view.row("display"))
    assert not view.row("reset_summary").isEnabled()
    click(view.row("reset_summary"))
    assert not st.calls
    st.d["overrides"] = True
    view.refresh()
    assert view.row("reset_summary").isEnabled()
    click(view.row("reset_summary"))
    assert st.calls[-1] == ("reset_summary",)


# ------------------------------------------------------------------ display page (V1, V3) with dynamic content
def test_display_page_dynamic_hardware_checks(view, st):
    click(view.row("display"))
    assert "hw:hw/cpu/0" in view.rows() and "hw:hw/gpu/0" in view.rows()
    assert view.row("hw:hw/cpu/0").text() == "AMD Ryzen" and view.row("hw:hw/cpu/0").isChecked()
    assert set(view.headings()) == {"hardware_heading", "categories_heading"}
    click(view.row("hw:hw/gpu/0"))
    assert st.calls[-1] == ("set_hardware_visible", "hw/gpu/0", False)
    assert not view.row("hw:hw/gpu/0").isChecked() and view.row("hw:hw/cpu/0").isChecked()
    st.hardware.append(("hw/nvme/0", "Samsung SSD", "storage"))
    view.refresh()
    assert "hw:hw/nvme/0" in view.rows()                    # new hardware shows up without rebuilding the model
    st.hardware.clear()
    view.refresh()
    assert "no_hardware" in view.rows() and view.row("no_hardware").kind == "info"


def test_display_categories_and_fahrenheit(view, st):
    click(view.row("display"))
    click(view.row("category:fans"))
    assert st.calls[-1] == ("set_category", "fans", False) and not view.row("category:fans").isChecked()
    click(view.row("fahrenheit"))
    assert st.d["bools"]["fahrenheit"] is True and view.row("fahrenheit").isChecked()
    click(view.row("open_detail"))
    assert st.calls[-1] == ("open_detail",)


def test_display_long_list_scrolls(make, st):
    st.hardware[:] = [(f"hw/x/{i}", f"Disk {i}", "storage") for i in range(40)]
    v = make()
    click(v.row("display"))
    assert len([r for r in v.rows() if r.startswith("hw:")]) == 40
    assert v.content_height() > 0


# ------------------------------------------------------------------ updates / about (Info, disabled states)
def test_updates_page_unconfigured_is_disabled_with_reason(view, st, monkeypatch):
    monkeypatch.setattr(release_info, "is_configured", lambda: False)
    click(view.row("updates"))
    assert not view.row("check_updates").isEnabled()
    assert not view.row("update_check_auto").isEnabled()
    assert view.row("update_status").kind == "info"
    assert "not configured" in view.row("update_status").accessibleName()
    assert "no repository" in view.row("update_hint").accessibleName()
    click(view.row("check_updates"))
    click(view.row("update_check_auto"))
    assert not st.calls


def test_updates_page_configured(view, st, monkeypatch):
    monkeypatch.setattr(release_info, "is_configured", lambda: True)
    click(view.row("updates"))
    assert view.row("check_updates").isEnabled() and view.row("update_check_auto").isEnabled()
    click(view.row("update_check_auto"))
    assert st.d["update_auto"] is True and view.row("update_check_auto").isChecked()
    st.d["update_state"] = ("available", "A new version 2.0 is available.")
    st.d["can_install"] = True
    view.refresh()
    assert "install_update" in view.rows() and "open_release_page" in view.rows()
    click(view.row("install_update"))
    assert st.calls[-1] == ("install_update",)
    st.d["update_state"] = ("checking", "Checking...")
    view.refresh()
    assert not view.row("check_updates").isEnabled()


def test_about_info_rows_copy_to_clipboard(view, st):
    click(view.row("about"))
    rows = [r for k, r in view.rows().items() if k.startswith("about_")]
    assert [r.kind for r in rows] == ["info", "info"]
    assert rows[0].value_text() == "1.0" and rows[0].text() == "Version"
    QApplication.clipboard().clear()
    click(rows[0])
    assert QApplication.clipboard().text() == "Version: 1.0"
    click(view.row("copy_tech_info"))
    assert st.calls[-1] == ("copy_tech_info",)


def test_info_with_custom_handler_does_not_copy(qapp):
    from hardware_monitor_widget.settings_model import Info, Page, SettingsCallbacks, SettingsModel
    hits = []
    root = Page(id="root", title_key="set_title", children=[
        Info(id="i", label_text="Row", value=lambda: "v", on_click=lambda: hits.append(1))])
    v = SettingsView(SettingsModel(root, SettingsCallbacks()))
    v.show()
    QApplication.clipboard().setText("untouched")
    click(v.row("i"))
    assert hits == [1] and QApplication.clipboard().text() == "untouched"
    v.close()


# ------------------------------------------------------------------ keyboard
def test_keyboard_navigation(view, st):
    first = view.row("display")
    first.setFocus()
    key(first, Qt.Key.Key_Down)
    assert view.row("personalization").hasFocus()
    key(view.row("personalization"), Qt.Key.Key_Up)
    assert first.hasFocus()
    key(first, Qt.Key.Key_Up)
    assert view.back_button().hasFocus()
    key(first, Qt.Key.Key_Right)                              # Right enters the page
    assert view.path_ids == ["root", "display"]
    assert view.focus_order()[1].hasFocus()                   # focus lands on the first row
    key(view.row("fahrenheit"), Qt.Key.Key_Return)            # Enter activates
    assert st.d["bools"]["fahrenheit"] is True
    key(view.row("fahrenheit"), Qt.Key.Key_Space)             # Space too
    assert st.d["bools"]["fahrenheit"] is False
    key(view.row("fahrenheit"), Qt.Key.Key_Left)              # Left goes back
    assert view.path_ids == ["root"] and view.row("display").hasFocus()


def test_keyboard_accordion(view, st):
    click(view.row("personalization"))
    key(view.row("palette"), Qt.Key.Key_Right)                # Right opens the list
    assert view.open_choice == "palette"
    key(view.row("palette"), Qt.Key.Key_Down)
    assert view.row("palette:classic").hasFocus()
    key(view.row("palette:classic"), Qt.Key.Key_Down)
    key(view.row("palette:colorblind"), Qt.Key.Key_Return)
    assert st.d["palette"] == "colorblind" and view.open_choice == "palette"
    assert view.row("palette:colorblind").hasFocus()
    key(view.row("palette:colorblind"), Qt.Key.Key_Left)      # Left on an option folds the list
    assert view.open_choice is None and view.row("palette").hasFocus()
    key(view.row("palette"), Qt.Key.Key_Right)
    key(view.row("palette"), Qt.Key.Key_Left)                 # Left on an open choice folds it, stays on the page
    assert view.open_choice is None and view.page_id == "personalization"


def test_tab_order_skips_disabled_rows(view, st):
    click(view.row("display"))
    order = view.focus_order()
    assert view.back_button() is order[0]
    assert view.row("reset_summary") not in order            # disabled (no overrides)
    assert view.row("open_detail") in order
    view.focus_last()
    assert view.focus_order()[-1].hasFocus()
    view.focus_back()
    assert view.back_button().hasFocus()
    view.focus_first()
    assert order[1].hasFocus()


def test_focus_survives_refresh(view, st):
    click(view.row("display"))
    view.row("fahrenheit").setFocus(Qt.FocusReason.TabFocusReason)
    view.refresh()
    assert view.row("fahrenheit").hasFocus()


# ------------------------------------------------------------------ accessibility, i18n, themes, scale
def test_accessible_names_and_back_label(view):
    assert view.back_button().accessibleName() == "Back" and view.back_button().toolTip() == "Back"
    assert view.row("display").accessibleName() == "Display"
    assert view.row("display").accessibleDescription() == "Opens the page"
    click(view.row("display"))
    assert view.row("fahrenheit").accessibleName() == "Show temperatures in °F"
    assert view.row("fahrenheit").isCheckable()
    assert view.row("category:fans").accessibleName() == "Fans"


def test_spanish_labels(view, st):
    set_language("es")
    view.refresh()
    assert view.title_text() == "Ajustes" and view.back_button().accessibleName() == "Volver"
    click(view.row("personalization"))
    assert view.row("palette").accessibleName() == "Colores, Clásica, contraído"
    click(view.row("palette"))
    assert view.row("palette").accessibleName() == "Colores, Clásica, desplegado"


def test_view_keys_exist_in_both_languages():
    for key_ in ("set_back", "set_collapsed", "set_expanded", "set_page_desc"):
        assert tr_in("en", key_) != key_ and tr_in("es", key_) != key_
    assert tr_in("en", "set_back") == "Back" and tr_in("es", "set_back") == "Volver"


@pytest.mark.parametrize("dark", [True, False])
def test_themes_grab_without_errors(make, dark):
    v = make(dark=dark)
    for page in ("display", "personalization", "updates", "about"):
        v.show_page(page, direct=True)
        assert not v.grab().isNull()
    v.show_page("personalization")
    click(v.row("palette"))
    v.row("palette:pastel").setFocus(Qt.FocusReason.TabFocusReason)
    assert isinstance(v.grab(), QPixmap) and not v.grab().isNull()
    v.set_dark(not dark)                                     # live theme switch keeps the page and the open list
    assert v.page_id == "personalization" and v.open_choice == "palette" and not v.grab().isNull()


@pytest.mark.parametrize("scale", [0.9, 1.0, 1.15, 1.3])
def test_touch_targets_at_every_scale(make, scale):
    v = make(scale=scale)
    assert v.scale == pytest.approx(scale)
    assert all(r.height() >= MIN_TARGET for r in v.rows().values())
    assert v.back_button().height() >= MIN_TARGET and v.back_button().width() >= MIN_TARGET
    v.show_page("personalization")
    click(v.row("language"))
    assert all(r.height() >= MIN_TARGET for r in v.rows().values())
    assert not v.grab().isNull()


def test_set_scale_rebuilds_rows(view):
    h = view.row("display").height()
    view.set_scale(1.3)
    assert view.row("display").height() > h
    view.set_scale(0.9)
    assert view.row("display").height() >= MIN_TARGET


def test_set_model_keeps_current_page(view, st):
    click(view.row("position"))
    view.set_model(build_settings_model(st.cb()))
    assert view.path_ids == ["root", "position"] and "placement" in view.rows()


def test_set_model_with_vanished_page_goes_to_root(view, st):
    click(view.row("display"))
    from hardware_monitor_widget.settings_model import Page, SettingsCallbacks, SettingsModel
    view.set_model(SettingsModel(Page(id="root", title_key="set_title", children=[]), SettingsCallbacks()))
    assert view.path_ids == ["root"]
