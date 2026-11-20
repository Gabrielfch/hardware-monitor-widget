"""Declarative Settings tree (no widgets): pages, toggles, exclusive choices, actions and read-only rows.

Adapted from Taskbar Battery Widget (MIT). The model does not know `Config` (ADR-7): every value is read and
written through `SettingsCallbacks`, which the controller implements. The settings view (settings_view.py) only
renders the tree, so every rule lives here. Everything is read lazily, so the view only has to call the
getters again after an action.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from . import release_info
from .colors import PALETTES
from .config import ALERT_COOLDOWN_OPTIONS, ALERT_SECONDS_OPTIONS, FLOATING_ANCHORS, FULLSCREEN_PLACEMENTS, ICON_STYLES, LANGUAGE_CHOICES, PANEL_SIZES, PILL_STYLES, THEMES, UI_SCALES
from .curation import SUMMARY_CATEGORIES
from .i18n import LANGUAGES as I18N_LANGUAGES, tr

log = logging.getLogger(__name__)

DEFAULT_FLOATING_ANCHOR = "bottom-right"
PLACEMENT_CHOICES = ("right", "left", "floating")
UPDATE_BUSY_STATES = ("checking", "downloading")
UPDATE_OFFER_STATES = ("available", "ready")


@dataclass
class SettingsCallbacks:
    """What the model needs from the application. Every member has a harmless default (no-op / neutral value)."""
    # generic booleans: "fahrenheit", "show_in_fullscreen"
    set_bool: Callable[[str, bool], None] = lambda key, value: None
    get_bool: Callable[[str], bool] = lambda key: False
    # display
    list_hardware: Callable[[], list] = lambda: []                  # [(hw_id, name, hw_class)]
    hardware_visible: Callable[[str], bool] = lambda hw_id: True
    set_hardware_visible: Callable[[str, bool], None] = lambda hw_id, visible: None
    category_enabled: Callable[[str], bool] = lambda cat: True      # one of curation.SUMMARY_CATEGORIES
    set_category: Callable[[str, bool], None] = lambda cat, on: None
    open_detail: Callable[[], None] = lambda: None
    reset_summary: Callable[[], None] = lambda: None
    has_overrides: Callable[[], bool] = lambda: False
    reset_order: Callable[[], None] = lambda: None                  # asks for confirmation, then clears hw_order
    has_order: Callable[[], bool] = lambda: False                   # a custom component order is saved
    # alerts
    get_alerts: Callable[[], bool] = lambda: True
    set_alerts: Callable[[bool], None] = lambda on: None
    get_alert_seconds: Callable[[], int] = lambda: 10
    set_alert_seconds: Callable[[int], None] = lambda n: None
    get_alert_cooldown: Callable[[], int] = lambda: 5
    set_alert_cooldown: Callable[[int], None] = lambda n: None
    # personalization
    get_theme: Callable[[], str] = lambda: "auto"                   # one of config.THEMES
    set_theme: Callable[[str], None] = lambda theme: None
    get_palette: Callable[[], str] = lambda: "classic"              # one of config.COLOR_PALETTES
    set_palette: Callable[[str], None] = lambda palette: None       # "custom" opens the colours dialog
    open_custom_colors: Callable[[], None] = lambda: None
    get_icon_style: Callable[[], str] = lambda: "outline"           # one of config.ICON_STYLES
    set_icon_style: Callable[[str], None] = lambda style: None
    get_panel_size: Callable[[], str] = lambda: "normal"            # one of config.PANEL_SIZES
    set_panel_size: Callable[[str], None] = lambda size: None
    get_pill_style: Callable[[], str] = lambda: "transparent"       # one of config.PILL_STYLES
    set_pill_style: Callable[[str], None] = lambda style: None
    get_ui_scale: Callable[[], int] = lambda: 100                   # one of config.UI_SCALES
    set_ui_scale: Callable[[int], None] = lambda pct: None
    get_language: Callable[[], str] = lambda: "auto"                # one of config.LANGUAGE_CHOICES
    set_language: Callable[[str], None] = lambda lang: None
    # position
    get_placement: Callable[[], str] = lambda: "right"              # "right" | "left" | "floating"
    set_placement: Callable[[str], None] = lambda choice: None
    get_floating_anchor: Callable[[], str] = lambda: DEFAULT_FLOATING_ANCHOR
    set_floating_anchor: Callable[[str], None] = lambda anchor: None
    get_monitor: Callable[[], str] = lambda: "primary"              # "primary" or a monitor key
    set_monitor: Callable[[str], None] = lambda key: None
    get_fullscreen_placement: Callable[[], str] = lambda: "same"    # one of config.FULLSCREEN_PLACEMENTS
    set_fullscreen_placement: Callable[[str], None] = lambda choice: None
    reset_position: Callable[[], None] = lambda: None
    list_screens: Callable[[], list] = lambda: []                   # [(key, label)]
    current_key: Callable[[], object] = lambda: None                # monitor key the widget is on
    icons_centered: Callable[[], object] = lambda: True             # True / False / None (unknown)
    monitor_has_taskbar: Callable[[], bool] = lambda: True          # does the widget's monitor have a taskbar
    # custom icons
    open_custom_icons: Callable[[], None] = lambda: None
    clear_custom_icons: Callable[[], None] = lambda: None
    # logs
    open_log: Callable[[], None] = lambda: None
    open_logs_folder: Callable[[], None] = lambda: None
    get_log_debug: Callable[[], bool] = lambda: False
    set_log_debug: Callable[[bool], None] = lambda on: None
    get_history_enabled: Callable[[], bool] = lambda: True
    set_history_enabled: Callable[[bool], None] = lambda on: None
    clear_history: Callable[[], None] = lambda: None
    # startup
    startup_enabled: Callable[[], bool] = lambda: False             # real state of the Windows startup entry
    set_startup: Callable[[bool], None] = lambda on: None
    # updates
    update_state: Callable[[], tuple] = lambda: ("idle", "")        # (idle|checking|available|up_to_date|downloading|ready|error, text)
    check_updates: Callable[[], None] = lambda: None
    can_install_update: Callable[[], bool] = lambda: False
    install_update: Callable[[], None] = lambda: None
    open_release_page: Callable[[], None] = lambda: None
    get_update_auto: Callable[[], bool] = lambda: False
    set_update_auto: Callable[[bool], None] = lambda on: None
    # about
    tech_info: Callable[[], str] = lambda: ""                       # "label: value" lines, no personal data
    copy_tech_info: Callable[[], None] = lambda: None
    open_notices: Callable[[], None] = lambda: None


# ------------------------------------------------------------------ nodes
@dataclass(eq=False, kw_only=True)
class Node:
    id: str = ""


@dataclass(eq=False, kw_only=True)
class Separator(Node):
    pass


@dataclass
class Option:
    """One entry of a Choice. `label` is already translated; `tip` is an optional short hint."""
    value: object
    label: str
    enabled: bool = True
    tip: str = ""


@dataclass(eq=False, kw_only=True)
class Page(Node):
    title_key: str
    # a list, or a callable returning one (dynamic content such as the detected hardware)
    children: list | Callable[[], list] = field(default_factory=list)
    icon: str = ""                  # glyph name (icons.glyph_icon) shown on the row that opens the page

    def title(self) -> str:
        return tr(self.title_key)

    def items(self) -> list:
        """The nodes of the page right now. A broken dynamic source gives an empty page, never an exception."""
        ch = self.children
        if callable(ch):
            try:
                return list(ch())
            except Exception:  # noqa: BLE001 - a broken source must not break the settings
                log.exception("settings page %s: could not build its items", self.id)
                return []
        return list(ch)


@dataclass(eq=False, kw_only=True)
class Toggle(Node):
    label_key: str = ""
    get: Callable[[], bool]
    set: Callable[[bool], None]
    tip_key: str = ""               # optional short hint (tooltip / accessible description)
    label_text: str = ""            # already translated label (e.g. a hardware name); wins over label_key
    enabled: Callable[[], bool] = lambda: True

    def label(self) -> str:
        return self.label_text or tr(self.label_key)

    def tip(self) -> str:
        return tr(self.tip_key) if self.tip_key else ""


@dataclass(eq=False, kw_only=True)
class Choice(Node):
    label_key: str
    options: Callable[[], list]     # () -> [Option]; re-evaluated on every refresh
    get: Callable[[], object]
    set: Callable[[object], None]

    def label(self) -> str:
        return tr(self.label_key)

    def current_label(self) -> str:
        cur = self.get()
        for o in self.options():
            if o.value == cur:
                return o.label
        return ""


@dataclass(eq=False, kw_only=True)
class Action(Node):
    label_key: str
    run: Callable[[], None]
    enabled: Callable[[], bool] = lambda: True

    def label(self) -> str:
        return tr(self.label_key)


@dataclass(eq=False, kw_only=True)
class Info(Node):
    """Read-only row: a label and an optional value. Clicking copies it (or runs `on_click`)."""
    label_key: str = ""
    label_text: str = ""            # already translated label; wins over label_key
    value: Callable[[], str] = lambda: ""
    on_click: Callable[[], None] | None = None
    tip_key: str = ""

    def label(self) -> str:
        return self.label_text or (tr(self.label_key) if self.label_key else "")

    def text(self) -> str:
        try:
            return str(self.value() or "")
        except Exception:  # noqa: BLE001
            return ""

    def tip(self) -> str:
        return tr(self.tip_key) if self.tip_key else ""

    def copy_text(self) -> str:
        label, text = self.label(), self.text()
        return f"{label}: {text}" if label and text else (text or label)


@dataclass(eq=False, kw_only=True)
class Heading(Node):
    """Non-interactive caption that groups the rows below it."""
    label_key: str = ""
    label_text: str = ""

    def label(self) -> str:
        return self.label_text or (tr(self.label_key) if self.label_key else "")


@dataclass(eq=False)
class SettingsModel:
    root: Page
    callbacks: SettingsCallbacks

    def walk(self, page: Page | None = None):
        """Yield every node below `page` (default: the root), depth first, pages before their children."""
        for node in (page or self.root).items():
            yield node
            if isinstance(node, Page):
                yield from self.walk(node)

    def find(self, node_id: str):
        for node in self.walk():
            if node.id == node_id:
                return node
        return self.root if node_id == self.root.id else None

    def parent_of(self, node_id: str) -> Page | None:
        stack = [self.root]
        while stack:
            page = stack.pop()
            for child in page.items():
                if child.id == node_id:
                    return page
                if isinstance(child, Page):
                    stack.append(child)
        return None

    def path_to(self, page_id: str) -> list[Page]:
        """Pages from the root down to `page_id` (inclusive); [root] when unknown."""
        chain = [self.find(page_id)]
        if not isinstance(chain[0], Page):
            return [self.root]
        while chain[0] is not self.root:
            parent = self.parent_of(chain[0].id)
            if parent is None:
                return [self.root]
            chain.insert(0, parent)
        return chain


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - a broken callback must not break the page
        return default


# ------------------------------------------------------------------ builder
def build_settings_model(cb: SettingsCallbacks | None = None) -> SettingsModel:
    """The whole Settings tree for the given callbacks (defaults: inert)."""
    cb = cb or SettingsCallbacks()

    def bool_toggle(key: str, label_key: str, tip_key: str = "") -> Toggle:
        return Toggle(id=key, label_key=label_key, tip_key=tip_key,
                      get=lambda: bool(_safe(lambda: cb.get_bool(key), False)),
                      set=lambda v: cb.set_bool(key, bool(v)))

    def static_options(values, label_of) -> Callable[[], list]:
        return lambda: [Option(v, label_of(v)) for v in values]

    # ---- display
    def display_items() -> list:
        items: list = [Heading(id="hardware_heading", label_key="set_heading_hardware")]
        hardware = _safe(lambda: list(cb.list_hardware()), [])
        seen: set[str] = set()
        for entry in hardware:
            if not isinstance(entry, (tuple, list)) or len(entry) < 2:
                continue
            hw_id, name = str(entry[0]), str(entry[1])
            if hw_id in seen:
                continue
            seen.add(hw_id)
            items.append(Toggle(
                id=f"hw:{hw_id}", label_text=name or hw_id,
                get=lambda h=hw_id: bool(_safe(lambda: cb.hardware_visible(h), True)),
                set=lambda v, h=hw_id: cb.set_hardware_visible(h, bool(v))))
        if not seen:
            items.append(Info(id="no_hardware", label_key="set_no_hardware"))
        items.append(Separator(id="display_sep1"))
        items.append(Heading(id="categories_heading", label_key="set_heading_categories"))
        for cat in SUMMARY_CATEGORIES:
            items.append(Toggle(
                id=f"category:{cat}", label_key=f"cat_{cat}",
                get=lambda c=cat: bool(_safe(lambda: cb.category_enabled(c), True)),
                set=lambda v, c=cat: cb.set_category(c, bool(v))))
        items.append(Separator(id="display_sep2"))
        items.append(bool_toggle("fahrenheit", "set_fahrenheit"))
        items.append(Action(id="open_detail", label_key="set_open_detail", run=lambda: cb.open_detail()))
        items.append(Action(id="reset_summary", label_key="set_reset_summary", run=lambda: cb.reset_summary(),
                            enabled=lambda: bool(_safe(cb.has_overrides, False))))
        items.append(Action(id="reset_order", label_key="set_reset_order", run=lambda: cb.reset_order(),
                            enabled=lambda: bool(_safe(cb.has_order, False))))
        return items

    display = Page(id="display", title_key="set_menu_display", icon="display", children=display_items)

    # ---- alerts
    def alert_choice(node_id, label_key, values, opt_key, get, set_):
        return Choice(id=node_id, label_key=label_key,
                      options=lambda: [Option(v, tr(opt_key, n=v)) for v in values],
                      get=lambda: _safe(get, values[0]), set=lambda v: set_(int(v)))

    alerts = Page(id="alerts", title_key="set_menu_alerts", icon="info", children=[
        Toggle(id="alerts_enabled", label_key="set_alerts_enabled", tip_key="set_alerts_enabled_tip",
               get=lambda: bool(_safe(cb.get_alerts, True)), set=lambda v: cb.set_alerts(bool(v))),
        alert_choice("alert_seconds", "set_alert_seconds", ALERT_SECONDS_OPTIONS, "set_alert_seconds_opt",
                     cb.get_alert_seconds, cb.set_alert_seconds),
        alert_choice("alert_cooldown", "set_alert_cooldown", ALERT_COOLDOWN_OPTIONS, "set_alert_cooldown_opt",
                     cb.get_alert_cooldown, cb.set_alert_cooldown),
    ])

    # ---- personalization
    def language_options() -> list:
        # Language names are shown in their own language, whatever the current UI language is.
        return [Option(v, tr("set_lang_auto") if v == "auto" else I18N_LANGUAGES.get(v, v)) for v in LANGUAGE_CHOICES]

    def custom_colors_enabled() -> bool:
        return _safe(cb.get_palette, "classic") == "custom"

    personalization = Page(id="personalization", title_key="set_menu_personalization", icon="personalization",
                           children=[
        Choice(id="theme", label_key="menu_theme",
               options=static_options(THEMES, lambda n: tr("theme_" + n)),
               get=lambda: _safe(cb.get_theme, "auto"), set=lambda v: cb.set_theme(str(v))),
        Choice(id="palette", label_key="set_palette",
               options=static_options(PALETTES, lambda n: tr("palette_" + n)),
               get=lambda: _safe(cb.get_palette, "classic"), set=lambda v: cb.set_palette(str(v))),
        Action(id="custom_colors", label_key="set_custom_colors", run=lambda: cb.open_custom_colors(),
               enabled=custom_colors_enabled),
        Choice(id="icon_style", label_key="set_icon_style",
               options=static_options(ICON_STYLES, lambda n: tr("icon_style_" + n)),
               get=lambda: _safe(cb.get_icon_style, "outline"), set=lambda v: cb.set_icon_style(str(v))),
        Choice(id="panel_size", label_key="set_panel_size",
               options=static_options(PANEL_SIZES, lambda n: tr("set_panel_size_" + n)),
               get=lambda: _safe(cb.get_panel_size, "normal"), set=lambda v: cb.set_panel_size(str(v))),
        Choice(id="ui_scale", label_key="set_ui_scale",
               options=static_options(UI_SCALES, lambda n: f"{n} %"),
               get=lambda: _safe(cb.get_ui_scale, 100), set=lambda v: cb.set_ui_scale(int(v))),
        bool_toggle("group_pills", "set_group_pills", "set_group_pills_tip"),
        Choice(id="pill_style", label_key="set_pill_style",
               options=static_options(PILL_STYLES, lambda n: tr("set_pill_style_" + n)),
               get=lambda: _safe(cb.get_pill_style, "transparent"), set=lambda v: cb.set_pill_style(str(v))),
        bool_toggle("pill_labels", "set_pill_labels", "set_pill_labels_tip"),
        bool_toggle("pill_bars", "set_pill_bars", "set_pill_bars_tip"),
        Toggle(id="stack_pills", label_key="set_stack_pills", tip_key="set_stack_pills_tip",
               get=lambda: bool(_safe(lambda: cb.get_bool("stack_pills"), False)),
               set=lambda v: cb.set_bool("stack_pills", bool(v)),
               enabled=lambda: _safe(cb.get_placement, "right") == "floating"),
        Choice(id="language", label_key="set_language", options=language_options,
               get=lambda: _safe(cb.get_language, "auto"), set=lambda v: cb.set_language(str(v))),
    ])

    # ---- position
    def placement_options() -> list:
        centered = _safe(cb.icons_centered, True) is True
        return [Option("right", tr("set_pos_right")),
                Option("left", tr("set_pos_left") if centered else tr("set_pos_left_fallback"),
                       tip=tr("set_pos_left_tip")),
                Option("floating", tr("set_pos_floating"), tip=tr("set_pos_floating_tip"))]

    def monitor_options() -> list:
        try:
            screens = [(str(k), str(lbl)) for k, lbl in cb.list_screens()]
        except Exception:  # noqa: BLE001 - a broken lister must not break the page
            screens = []
        cur = str(_safe(cb.get_monitor, "primary"))
        entries = [("primary", tr("set_monitor_primary"))] + [s for s in screens if s[0] != "primary"]
        opts = [Option(k, lbl) for k, lbl in entries]
        if cur != "primary" and cur not in {o.value for o in opts}:
            opts.append(Option(cur, tr("set_monitor_disconnected"), enabled=False))
        return opts

    def reset_enabled() -> bool:
        return (_safe(cb.get_placement, "right") == "floating" or not _safe(cb.monitor_has_taskbar, True)
                or _safe(cb.get_fullscreen_placement, "same") == "floating")

    def fs_options() -> list:
        # Needs "Visible in fullscreen": without it every option is disabled and says why.
        on = bool(_safe(lambda: cb.get_bool("show_in_fullscreen"), False))
        tip = "" if on else tr("set_fs_needs_visible")
        values = [("same", tr("set_fs_same")), ("floating", tr("set_pos_floating"))]
        values += [(a, tr(f"set_anchor_{a}")) for a in FLOATING_ANCHORS]
        return [Option(v, lbl, enabled=on, tip=tip) for v, lbl in values if v in FULLSCREEN_PLACEMENTS]

    def placement_get() -> str:
        cur = _safe(cb.get_placement, "right")
        return cur if cur in PLACEMENT_CHOICES else "right"

    def anchor_get() -> str:
        cur = _safe(cb.get_floating_anchor, DEFAULT_FLOATING_ANCHOR)
        return cur if cur in FLOATING_ANCHORS else DEFAULT_FLOATING_ANCHOR

    position = Page(id="position", title_key="set_menu_position", icon="position", children=[
        Choice(id="placement", label_key="set_placement", options=placement_options,
               get=placement_get, set=lambda v: cb.set_placement(str(v))),
        Choice(id="float_anchor", label_key="set_float_anchor",
               options=static_options(FLOATING_ANCHORS, lambda a: tr(f"set_anchor_{a}")),
               get=anchor_get, set=lambda v: cb.set_floating_anchor(str(v))),
        Choice(id="monitor", label_key="set_monitor", options=monitor_options,
               get=lambda: str(_safe(cb.get_monitor, "primary")), set=lambda v: cb.set_monitor(str(v))),
        bool_toggle("show_in_fullscreen", "set_show_fullscreen"),
        Choice(id="fullscreen_placement", label_key="set_fs_position", options=fs_options,
               get=lambda: _safe(cb.get_fullscreen_placement, "same"),
               set=lambda v: cb.set_fullscreen_placement(str(v))),
        Separator(id="position_sep"),
        Action(id="reset_position", label_key="set_reset_position", run=lambda: cb.reset_position(),
               enabled=reset_enabled),
    ])

    # ---- custom icons
    icons = Page(id="custom_icons", title_key="custom_icons_title", icon="image", children=[
        Action(id="manage_icons", label_key="set_manage_icons", run=lambda: cb.open_custom_icons()),
        Action(id="clear_icons", label_key="custom_icons_clear", run=lambda: cb.clear_custom_icons()),
    ])

    # ---- logs
    logs = Page(id="logs", title_key="set_menu_logs", icon="logs", children=[
        Action(id="open_log", label_key="set_open_log", run=lambda: cb.open_log()),
        Action(id="open_logs_folder", label_key="set_open_logs_folder", run=lambda: cb.open_logs_folder()),
        Toggle(id="log_debug", label_key="set_log_debug", tip_key="set_log_debug_tip",
               get=lambda: bool(_safe(cb.get_log_debug, False)), set=lambda v: cb.set_log_debug(bool(v))),
        Toggle(id="history_enabled", label_key="set_history", tip_key="set_history_tip",
               get=lambda: bool(_safe(cb.get_history_enabled, True)),
               set=lambda v: cb.set_history_enabled(bool(v))),
        Action(id="clear_history", label_key="set_clear_history", run=lambda: cb.clear_history()),
    ])

    # ---- updates
    def update_items() -> list:
        configured = bool(_safe(release_info.is_configured, False))
        state, text = "idle", ""
        got = _safe(cb.update_state, ("idle", ""))
        if isinstance(got, (tuple, list)) and len(got) == 2:
            state, text = str(got[0]), str(got[1] or "")
        status = text or tr("set_upd_idle") if configured else tr("upd_disabled")
        items: list = [Info(id="update_status", label_key="set_upd_status", value=lambda: status)]
        if not configured:
            items.append(Info(id="update_hint", label_text=tr("set_upd_hint_disabled")))
        items.append(Action(id="check_updates", label_key="upd_check", run=lambda: cb.check_updates(),
                            enabled=lambda: configured and state not in UPDATE_BUSY_STATES))
        if configured and state in UPDATE_OFFER_STATES and bool(_safe(cb.can_install_update, False)):
            items.append(Action(id="install_update", label_key="upd_install", run=lambda: cb.install_update()))
        if configured and state in UPDATE_OFFER_STATES + ("error",):
            items.append(Action(id="open_release_page", label_key="upd_open_page",
                                run=lambda: cb.open_release_page()))
        items.append(Separator(id="update_sep"))
        items.append(Toggle(id="update_check_auto", label_key="upd_auto", enabled=lambda: configured,
                            get=lambda: bool(_safe(cb.get_update_auto, False)) and configured,
                            set=lambda v: cb.set_update_auto(bool(v))))
        return items

    updates = Page(id="updates", title_key="upd_check", icon="update", children=update_items)

    # ---- about
    def about_items() -> list:
        raw = _safe(cb.tech_info, "")
        lines = [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()]
        items: list = []
        for i, line in enumerate(lines):
            label, sep, value = line.partition(":")
            if sep and value.strip():
                items.append(Info(id=f"about_{i}", label_text=label.strip(), value=lambda v=value.strip(): v,
                                  tip_key="set_info_tip"))
            else:
                items.append(Info(id=f"about_{i}", label_text=line, tip_key="set_info_tip"))
        if not items:
            items.append(Info(id="about_none", label_key="set_about_no_info"))
        items.append(Separator(id="about_sep"))
        items.append(Action(id="copy_tech_info", label_key="set_about_copy", run=lambda: cb.copy_tech_info()))
        items.append(Action(id="open_notices", label_key="set_about_licenses", run=lambda: cb.open_notices()))
        items.append(Action(id="open_logs_folder_about", label_key="set_open_logs_folder",
                            run=lambda: cb.open_logs_folder()))
        return items

    about = Page(id="about", title_key="set_menu_about", icon="info", children=about_items)

    def startup_state() -> bool:
        return bool(_safe(cb.startup_enabled, False))

    root = Page(id="root", title_key="set_title", children=[
        display,
        personalization,
        position,
        alerts,
        icons,
        logs,
        Separator(id="root_sep"),
        Toggle(id="start_with_windows", label_key="start_with_windows", get=startup_state,
               set=lambda v: cb.set_startup(bool(v))),
        updates,
        about,
    ])
    return SettingsModel(root, cb)

