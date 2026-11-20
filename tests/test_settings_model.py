"""Settings model: declarative tree (no widgets), dynamic pages, callbacks and i18n parity."""
import pytest

from hardware_monitor_widget import i18n, release_info, strings_settings
from hardware_monitor_widget.colors import PALETTES
from hardware_monitor_widget.config import FLOATING_ANCHORS, FULLSCREEN_PLACEMENTS, ICON_STYLES
from hardware_monitor_widget.curation import SUMMARY_CATEGORIES
from hardware_monitor_widget.i18n import set_language
from hardware_monitor_widget.settings_model import (Action, Choice, Heading, Info, Page, Separator,
                                                    SettingsCallbacks, SettingsModel, Toggle,
                                                    build_settings_model)


class State:
    """Stand-in for the controller: state + the callbacks that mutate it, recording every call."""

    def __init__(self):
        self.calls = []
        self.d = {"theme": "auto", "palette": "classic", "icon_style": "outline", "panel_size": "normal",
                  "ui_scale": 100, "language": "auto", "placement": "right", "anchor": "bottom-right",
                  "monitor": "primary", "fs": "same", "log_debug": False, "startup": False,
                  "update_auto": False, "bools": {"fahrenheit": False, "show_in_fullscreen": False, "group_pills": True},
                  "hidden": set(), "cats": {c: True for c in SUMMARY_CATEGORIES}, "overrides": False,
                  "update_state": ("idle", ""), "can_install": False, "tech": "Version: 1.0\nMode: portable"}
        self.hardware = [("hw/cpu/0", "AMD Ryzen", "cpu"), ("hw/gpu/0", "NVIDIA RTX", "gpu")]

    def cb(self) -> SettingsCallbacks:
        d, calls = self.d, self.calls

        def rec(name, key=None):
            def fn(*a):
                calls.append((name, *a))
                if key is not None:
                    d[key] = a[0]
            return fn

        def set_bool(k, v):
            calls.append(("set_bool", k, v))
            d["bools"][k] = v

        def set_hw(h, v):
            calls.append(("set_hardware_visible", h, v))
            d["hidden"].discard(h) if v else d["hidden"].add(h)

        def set_cat(c, v):
            calls.append(("set_category", c, v))
            d["cats"][c] = v

        return SettingsCallbacks(
            set_bool=set_bool, get_bool=lambda k: d["bools"][k],
            list_hardware=lambda: list(self.hardware), hardware_visible=lambda h: h not in d["hidden"],
            set_hardware_visible=set_hw, category_enabled=lambda c: d["cats"][c], set_category=set_cat,
            open_detail=rec("open_detail"), reset_summary=rec("reset_summary"),
            has_overrides=lambda: d["overrides"],
            get_theme=lambda: d["theme"], set_theme=rec("set_theme", "theme"),
            get_palette=lambda: d["palette"], set_palette=rec("set_palette", "palette"),
            open_custom_colors=rec("open_custom_colors"),
            get_icon_style=lambda: d["icon_style"], set_icon_style=rec("set_icon_style", "icon_style"),
            get_panel_size=lambda: d["panel_size"], set_panel_size=rec("set_panel_size", "panel_size"),
            get_ui_scale=lambda: d["ui_scale"], set_ui_scale=rec("set_ui_scale", "ui_scale"),
            get_language=lambda: d["language"], set_language=rec("set_language", "language"),
            get_placement=lambda: d["placement"], set_placement=rec("set_placement", "placement"),
            get_floating_anchor=lambda: d["anchor"], set_floating_anchor=rec("set_floating_anchor", "anchor"),
            get_monitor=lambda: d["monitor"], set_monitor=rec("set_monitor", "monitor"),
            get_fullscreen_placement=lambda: d["fs"],
            set_fullscreen_placement=rec("set_fullscreen_placement", "fs"),
            reset_position=rec("reset_position"), list_screens=lambda: [("aa11", "Monitor 2")],
            open_custom_icons=rec("open_custom_icons"), clear_custom_icons=rec("clear_custom_icons"),
            open_log=rec("open_log"), open_logs_folder=rec("open_logs_folder"),
            get_log_debug=lambda: d["log_debug"], set_log_debug=rec("set_log_debug", "log_debug"),
            startup_enabled=lambda: d["startup"], set_startup=rec("set_startup", "startup"),
            update_state=lambda: d["update_state"], check_updates=rec("check_updates"),
            can_install_update=lambda: d["can_install"], install_update=rec("install_update"),
            open_release_page=rec("open_release_page"),
            get_update_auto=lambda: d["update_auto"], set_update_auto=rec("set_update_auto", "update_auto"),
            tech_info=lambda: d["tech"], copy_tech_info=rec("copy_tech_info"), open_notices=rec("open_notices"))


@pytest.fixture
def st():
    set_language("en")
    return State()


@pytest.fixture
def m(st):
    return build_settings_model(st.cb())


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(release_info, "is_configured", lambda: True)


def ids(page):
    return [n.id for n in page.items()]


def test_root_order_matches_the_design(m):
    assert ids(m.root) == ["display", "personalization", "position", "alerts", "custom_icons", "logs", "root_sep",
                           "start_with_windows", "updates", "about"]
    assert isinstance(m.find("root_sep"), Separator)
    assert isinstance(m.find("start_with_windows"), Toggle)
    assert [p.icon for p in m.root.items() if isinstance(p, Page)] == [
        "display", "personalization", "position", "info", "image", "logs", "update", "info"]


def test_pages_content(m):
    assert ids(m.find("personalization")) == ["theme", "palette", "custom_colors", "icon_style", "panel_size",
                                              "ui_scale", "group_pills", "pill_style", "pill_labels", "pill_bars",
                                              "stack_pills", "language"]
    assert ids(m.find("position")) == ["placement", "float_anchor", "monitor", "show_in_fullscreen",
                                       "fullscreen_placement", "position_sep", "reset_position"]
    assert ids(m.find("custom_icons")) == ["manage_icons", "clear_icons"]
    assert ids(m.find("logs")) == ["open_log", "open_logs_folder", "log_debug", "history_enabled", "clear_history"]


def test_all_ids_unique_and_kinds(m):
    nodes = list(m.walk())
    all_ids = [n.id for n in nodes]
    assert len(all_ids) == len(set(all_ids))
    assert any(isinstance(n, Info) for n in nodes) and any(isinstance(n, Heading) for n in nodes)
    assert all(isinstance(n, (Page, Toggle, Choice, Action, Info, Heading, Separator)) for n in nodes)


def test_choice_values_match_constants(m):
    assert [o.value for o in m.find("palette").options()] == list(PALETTES)
    assert [o.value for o in m.find("icon_style").options()] == list(ICON_STYLES)
    assert [o.value for o in m.find("float_anchor").options()] == list(FLOATING_ANCHORS)
    assert [o.value for o in m.find("fullscreen_placement").options()] == list(FULLSCREEN_PLACEMENTS)
    assert [o.value for o in m.find("placement").options()] == ["right", "left", "floating"]
    assert [o.value for o in m.find("theme").options()] == ["auto", "light", "dark"]
    assert [o.value for o in m.find("panel_size").options()] == ["compact", "normal", "large"]
    assert [o.value for o in m.find("ui_scale").options()] == [90, 100, 115, 130]
    assert [o.value for o in m.find("language").options()] == ["auto", "es", "en"]
    assert [o.label for o in m.find("language").options()] == ["Automatic (system)", "Español", "English"]
    assert m.find("palette").options()[-1].value == "custom"


def test_reuses_existing_keys_not_duplicates():
    from hardware_monitor_widget import strings_appearance
    own = set(strings_settings.STRINGS["en"])
    assert not own & set(strings_appearance.STRINGS["en"])
    assert not own & {k for k in own if k.startswith(("palette_", "icon_style_", "upd_", "theme_"))}


# ------------------------------------------------------------------ display (V1, V3)
def test_display_lists_detected_hardware_dynamically(st, m):
    page = m.find("display")
    toggles = [n for n in page.items() if isinstance(n, Toggle) and n.id.startswith("hw:")]
    assert [(t.id, t.label()) for t in toggles] == [("hw:hw/cpu/0", "AMD Ryzen"), ("hw:hw/gpu/0", "NVIDIA RTX")]
    st.hardware.append(("hw/nvme/0", "Samsung SSD", "storage"))           # a disk is plugged in
    assert "hw:hw/nvme/0" in ids(page)
    st.hardware.clear()
    assert "no_hardware" in ids(page) and not any(i.startswith("hw:") for i in ids(page))


def test_hardware_toggle_uses_hw_id_and_reads_state(st, m):
    t = m.find("hw:hw/gpu/0")
    assert t.get() is True
    t.set(False)
    assert st.calls[-1] == ("set_hardware_visible", "hw/gpu/0", False) and t.get() is False
    assert m.find("hw:hw/cpu/0").get() is True
    t.set(True)
    assert st.calls[-1] == ("set_hardware_visible", "hw/gpu/0", True)


def test_hardware_with_duplicate_or_odd_entries_is_tolerated(st, m):
    st.hardware[:] = [("hw/a", "A", "cpu"), ("hw/a", "A again", "cpu"), "bad", ("hw/b",)]
    got = [n.id for n in m.find("display").items() if n.id.startswith("hw:")]
    assert got == ["hw:hw/a"]


def test_categories_toggles(st, m):
    for cat in SUMMARY_CATEGORIES:
        assert m.find(f"category:{cat}").get() is True
    m.find("category:fans").set(False)
    assert st.calls[-1] == ("set_category", "fans", False) and m.find("category:fans").get() is False
    assert m.find("category:fans").label() == "Fans"


def test_reset_summary_disabled_without_overrides(st, m):
    a = m.find("reset_summary")
    assert not a.enabled()
    st.d["overrides"] = True
    assert a.enabled()
    a.run()
    assert st.calls[-1] == ("reset_summary",)
    m.find("open_detail").run()
    assert st.calls[-1] == ("open_detail",)


def test_fahrenheit_routes_through_set_bool(st, m):
    t = m.find("fahrenheit")
    assert t.get() is False
    t.set(True)
    assert st.calls[-1] == ("set_bool", "fahrenheit", True) and t.get() is True


# ------------------------------------------------------------------ personalization / position
def test_choices_and_actions_route_to_callbacks(st, m):
    m.find("theme").set("dark")
    m.find("palette").set("pastel")
    m.find("icon_style").set("simple")
    m.find("panel_size").set("large")
    m.find("ui_scale").set("115")
    m.find("language").set("es")
    m.find("placement").set("left")
    m.find("float_anchor").set("top-left")
    m.find("monitor").set("aa11")
    m.find("fullscreen_placement").set("floating")
    m.find("start_with_windows").set(True)
    for aid in ("manage_icons", "clear_icons", "open_log", "open_logs_folder", "reset_position"):
        m.find(aid).run()
    assert st.calls == [
        ("set_theme", "dark"), ("set_palette", "pastel"), ("set_icon_style", "simple"),
        ("set_panel_size", "large"), ("set_ui_scale", 115), ("set_language", "es"),
        ("set_placement", "left"), ("set_floating_anchor", "top-left"), ("set_monitor", "aa11"),
        ("set_fullscreen_placement", "floating"), ("set_startup", True),
        ("open_custom_icons",), ("clear_custom_icons",), ("open_log",), ("open_logs_folder",),
        ("reset_position",)]
    assert st.d["ui_scale"] == 115 and isinstance(st.d["ui_scale"], int)


def test_getters_follow_the_state(st, m):
    st.d.update(theme="light", palette="mono", icon_style="none", panel_size="compact", ui_scale=130,
                language="en", placement="floating", anchor="top-center", monitor="aa11")
    assert [m.find(i).get() for i in ("theme", "palette", "icon_style", "panel_size", "ui_scale", "language",
                                      "placement", "float_anchor", "monitor")] == [
        "light", "mono", "none", "compact", 130, "en", "floating", "top-center", "aa11"]
    assert m.find("monitor").current_label() == "Monitor 2"
    assert m.find("palette").current_label() == "Monochrome"


def test_invalid_state_falls_back(st, m):
    st.d.update(placement="weird", anchor="nowhere")
    assert m.find("placement").get() == "right" and m.find("float_anchor").get() == "bottom-right"


def test_custom_colors_action_only_for_custom_palette(st, m):
    a = m.find("custom_colors")
    assert not a.enabled()
    st.d["palette"] = "custom"
    assert a.enabled()
    a.run()
    assert st.calls[-1] == ("open_custom_colors",)


def test_disconnected_monitor_is_disabled(st, m):
    st.d["monitor"] = "zz"
    opts = m.find("monitor").options()
    assert [o.value for o in opts] == ["primary", "aa11", "zz"]
    assert opts[-1].enabled is False and opts[-1].label == "Disconnected monitor (using the primary)"
    assert m.find("monitor").current_label() == "Disconnected monitor (using the primary)"


def test_left_placement_falls_back_when_icons_not_centered(st):
    cb = st.cb()
    cb.icons_centered = lambda: False
    left = build_settings_model(cb).find("placement").options()[1]
    assert "using the right" in left.label and left.value == "left"


def test_fullscreen_options_need_visible_in_fullscreen(st, m):
    assert all(not o.enabled and o.tip for o in m.find("fullscreen_placement").options())
    m.find("show_in_fullscreen").set(True)
    opts = m.find("fullscreen_placement").options()
    assert all(o.enabled and not o.tip for o in opts)


def test_reset_position_enabled_rules(st):
    cb = st.cb()
    a = build_settings_model(cb).find("reset_position")
    assert not a.enabled()
    st.d["placement"] = "floating"
    assert a.enabled()
    st.d["placement"] = "right"
    st.d["fs"] = "floating"
    assert a.enabled()
    st.d["fs"] = "same"
    cb.monitor_has_taskbar = lambda: False
    assert a.enabled()


def test_broken_callbacks_never_raise(st):
    def boom(*_a):
        raise RuntimeError("x")
    cb = SettingsCallbacks(list_hardware=boom, get_theme=boom, get_palette=boom, list_screens=boom,
                           get_monitor=boom, update_state=boom, tech_info=boom, get_log_debug=boom,
                           startup_enabled=boom, has_overrides=boom, get_bool=boom, category_enabled=boom)
    m = build_settings_model(cb)
    for node in m.walk():                      # building every page and reading every getter must not raise
        if isinstance(node, (Toggle, Choice)):
            node.get()
        if isinstance(node, Choice):
            node.options()
        if isinstance(node, Action):
            node.enabled()
    assert m.find("theme").get() == "auto"


def test_defaults_are_inert():
    m = build_settings_model()
    for node in m.walk():
        if isinstance(node, Action):
            node.run()
        if isinstance(node, Toggle):
            node.set(True)
        if isinstance(node, Choice):
            node.set(node.options()[0].value)


# ------------------------------------------------------------------ logs / startup
def test_log_debug_and_startup_toggles(st, m):
    t = m.find("log_debug")
    assert t.get() is False
    t.set(True)
    assert st.calls[-1] == ("set_log_debug", True) and t.get() is True
    s = m.find("start_with_windows")
    assert s.get() is False
    st.d["startup"] = True                       # the real state wins
    assert s.get() is True


# ------------------------------------------------------------------ updates (disabled when not configured)
def test_updates_disabled_when_not_configured(st, m, monkeypatch):
    monkeypatch.setattr(release_info, "is_configured", lambda: False)
    page = m.find("updates")
    assert ids(page) == ["update_status", "update_hint", "check_updates", "update_sep", "update_check_auto"]
    assert page.items()[0].text() == i18n.tr("upd_disabled")
    assert not m.find("check_updates").enabled()
    auto = m.find("update_check_auto")
    assert not auto.enabled() and auto.get() is False
    st.d["update_auto"] = True
    assert auto.get() is False                  # cannot be on while updates are not configured


def test_updates_configured_flow(st, m, configured):
    assert m.find("check_updates").enabled()
    assert m.find("update_status").text() == "No check done yet"
    auto = m.find("update_check_auto")
    assert auto.enabled()
    auto.set(True)
    assert st.calls[-1] == ("set_update_auto", True) and auto.get() is True
    m.find("check_updates").run()
    assert st.calls[-1] == ("check_updates",)

    st.d["update_state"] = ("checking", "Checking for updates...")
    assert not m.find("check_updates").enabled()
    assert m.find("update_status").text() == "Checking for updates..."

    st.d["update_state"] = ("available", "A new version 2.0 is available.")
    assert "install_update" not in ids(m.find("updates")) and "open_release_page" in ids(m.find("updates"))
    st.d["can_install"] = True
    assert "install_update" in ids(m.find("updates"))
    m.find("install_update").run()
    m.find("open_release_page").run()
    assert [c[0] for c in st.calls[-2:]] == ["install_update", "open_release_page"]

    st.d["update_state"] = ("up_to_date", "You have the latest version.")
    assert not {"install_update", "open_release_page"} & set(ids(m.find("updates")))
    st.d["update_state"] = ("error", "Could not reach GitHub.")
    assert "open_release_page" in ids(m.find("updates")) and "install_update" not in ids(m.find("updates"))


def test_update_state_garbage_is_tolerated(st, m, configured):
    st.d["update_state"] = "nonsense"
    assert m.find("update_status").text() == "No check done yet"


# ------------------------------------------------------------------ about
def test_about_lists_tech_info_rows_and_actions(st, m):
    page = m.find("about")
    infos = [n for n in page.items() if isinstance(n, Info)]
    assert [(i.label(), i.text()) for i in infos] == [("Version", "1.0"), ("Mode", "portable")]
    assert infos[0].copy_text() == "Version: 1.0"
    assert {"copy_tech_info", "open_notices", "open_logs_folder_about"} <= set(ids(page))
    m.find("copy_tech_info").run()
    m.find("open_notices").run()
    assert [c[0] for c in st.calls] == ["copy_tech_info", "open_notices"]
    st.d["tech"] = ""
    assert "about_none" in ids(page)


def test_info_and_heading_labels():
    assert Info(id="a", label_text="X", value=lambda: "y").copy_text() == "X: y"
    assert Info(id="a", label_text="X").copy_text() == "X"
    assert Info(id="a", value=lambda: "only").copy_text() == "only"
    assert Info(id="a", value=lambda: 1 / 0).text() == ""
    assert Heading(id="h", label_text="Title").label() == "Title"
    t = Toggle(id="t", label_text="Already translated", label_key="start_with_windows", get=lambda: True,
               set=lambda v: None)
    assert t.label() == "Already translated"


def test_page_items_accepts_list_or_callable():
    a = Page(id="a", title_key="set_title", children=[Separator(id="s")])
    b = Page(id="b", title_key="set_title", children=lambda: [Separator(id="t")])
    c = Page(id="c", title_key="set_title", children=lambda: 1 / 0)
    assert ids(a) == ["s"] and ids(b) == ["t"] and ids(c) == []


# ------------------------------------------------------------------ navigation / i18n
def test_navigation_helpers(m):
    assert [p.id for p in m.path_to("position")] == ["root", "position"]
    assert [p.id for p in m.path_to("nope")] == ["root"]
    assert m.parent_of("monitor").id == "position" and m.parent_of("root") is None
    assert m.parent_of("hw:hw/cpu/0").id == "display"
    assert isinstance(m, SettingsModel) and m.find("root") is m.root


def test_titles_translate(m):
    assert m.root.title() == "Settings" and m.find("display").title() == "Display"
    assert m.find("updates").title() == "Check for updates" and m.find("about").title() == "About"
    set_language("es")
    assert m.root.title() == "Ajustes" and m.find("personalization").title() == "Personalización"
    assert m.find("custom_icons").title() == "Iconos personalizados"


def test_strings_parity_and_placeholders():
    en, es = strings_settings.STRINGS["en"], strings_settings.STRINGS["es"]
    assert set(en) == set(es)
    assert all(en[k].strip() and es[k].strip() for k in en)


def test_every_key_used_by_the_model_exists_in_both_languages(m, st, configured):
    st.d["update_state"] = ("available", "x")
    st.d["can_install"] = True
    set_language("en")
    keys = set()
    for node in m.walk():
        for attr in ("title_key", "label_key", "tip_key"):
            k = getattr(node, attr, "")
            if k:
                keys.add(k)
    for lang in ("en", "es"):
        for k in keys:
            assert i18n.tr_in(lang, k) != k, (lang, k)
    for k in ("set_back", "set_page_desc", "set_expanded", "set_collapsed"):
        assert i18n.tr_in("en", k) != k and i18n.tr_in("es", k) != k


def test_option_labels_exist_in_both_languages(m):
    for lang in ("en", "es"):
        set_language(lang)
        for cid in ("theme", "palette", "icon_style", "panel_size", "placement", "float_anchor", "monitor",
                    "fullscreen_placement", "language", "ui_scale"):
            for o in m.find(cid).options():
                assert o.label and not o.label.startswith(("set_", "palette_", "icon_style_", "theme_")), (lang, o)
