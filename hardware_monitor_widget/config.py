"""Settings persisted as JSON in %APPDATA%\\HardwareMonitorWidget\\config.json (schema v2, validated per key)."""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from .curation import CATEGORY_ORDER, CATEGORY_PREFIX, MAX_HW_ORDER, SUMMARY_CATEGORIES
from .models import HW_KINDS

log = logging.getLogger(__name__)

APP_DIR_NAME = "HardwareMonitorWidget"
MAX_CONFIG_BYTES = 256 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def is_reparse_point(path: Path | str) -> bool:
    """True if ``path`` is a symlink or a junction (never write through those: planting risk)."""
    p = str(path)
    try:
        if os.path.islink(p):
            return True
        st = os.stat(p, follow_symlinks=False)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


# Default pinned "virtual" keys resolved by the UI (see widget.resolve_pins):
#   "auto:cpu_temp"  -> CPU temperature named Package / Tctl / Tdie, else the first CPU temperature
#   "auto:cpu_load"  -> CPU load "CPU Total"
#   "auto:gpu_temp", "auto:gpu_load", "auto:ram_load" -> first matching GPU / RAM sensor
DEFAULT_PINS = ["auto:cpu_temp", "auto:cpu_load", "auto:gpu_temp", "auto:gpu_load", "auto:ram_load"]

CONFIG_VERSION = 2
MAX_PINNED = 32
THEMES = ("auto", "light", "dark")
COLOR_PALETTES = ("classic", "colorblind", "pastel", "high_contrast", "mono", "custom")
CUSTOM_COLOR_KEYS = ("ok", "warn", "crit")
DEFAULT_CUSTOM_COLORS = {"ok": "#6EDC78", "warn": "#F5C842", "crit": "#FF9999"}
ICON_STYLES = ("outline", "filled", "simple", "none")
PILL_STYLES = ("classic", "soft", "transparent", "compact")
UI_SCALES = (90, 100, 115, 130)
PANEL_SIZES = ("compact", "normal", "large")
LANGUAGE_CHOICES = ("auto", "es", "en")
PLACEMENT_MODES = ("taskbar", "floating")
TASKBAR_ANCHORS = ("right", "left")
FLOATING_ANCHORS = ("top-left", "top-center", "top-right", "middle-left", "middle-right",
                    "bottom-left", "bottom-center", "bottom-right")
FULLSCREEN_PLACEMENTS = ("same", "floating") + FLOATING_ANCHORS
MAX_FLOATING = 16
MAX_OFFSET = 10000
MAX_HIDDEN_HARDWARE = 200
MAX_OVERRIDES = 1000
MAX_DETAIL_TOGGLED = 300
MAX_ICONS = 200
MAX_COLLAPSED = 500
MAX_THRESHOLDS = 500
THRESHOLD_MIN, THRESHOLD_MAX = 0.0, 200.0       # bounds of a per-sensor band limit (degrees C or %)
ALERT_SECONDS_RANGE = (1, 3600)
ALERT_COOLDOWN_MIN_RANGE = (1, 1440)
ALERT_SECONDS_OPTIONS = (5, 10, 30, 60, 120)        # choices offered in Settings (any value in range is valid)
ALERT_COOLDOWN_OPTIONS = (1, 5, 15, 30, 60)
MAX_KEY_LEN = 200
HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
MONITOR_KEY_RE = re.compile(r"[0-9a-f#]{1,40}")
HW_ID_RE = re.compile(r"hw/.{1,200}", re.DOTALL)
ICON_FILE_RE = re.compile(r"[0-9a-f]{16}\.(png|jpg|jpeg|bmp|ico|gif)")

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "interval_ms": 1000,            # 500..10000
    "pinned": list(DEFAULT_PINS),   # sensor keys or auto:* keys, in pill order (max MAX_PINNED)
    "theme": "auto",                # auto | light | dark
    "fahrenheit": False,
    "start_with_windows": False,
    "collapsed": [],                # hw_id of sections collapsed in the panel
    "hidden_hardware": [],          # hw_id whose summary section is hidden
    "hw_order": [],                 # panel / pill order: "cat:<category>" and hw_id entries ([] = default order)
    "summary_categories": list(SUMMARY_CATEGORIES),
    "summary_overrides": {},        # sensor key -> bool (forced in / out of the summary)
    "detail_toggled": [],           # detail-tree node ids that differ from their default state
    "detail_geometry": {},          # {x, y, w, h} of the detail window
    "color_palette": "classic",
    "custom_colors": dict(DEFAULT_CUSTOM_COLORS),
    "icon_style": "outline",
    "ui_scale": 100,
    "group_pills": True,            # one pill per hardware (several values) instead of one per sensor
    "pill_style": "transparent",    # classic | soft | transparent | compact
    "pill_labels": False,           # CPU / GPU / RAM... text instead of the hardware icon
    "pill_bars": True,              # thin level bar under the values (never in "compact")
    "stack_pills": False,           # floating: one component per row, values in columns
    "panel_size": "normal",
    "language": "auto",
    "placement_mode": "taskbar",
    "taskbar_anchor": "right",
    "placement_monitor": "primary",
    "floating_positions": {},
    "show_in_fullscreen": False,
    "fullscreen_placement": "same",
    "fullscreen_floating_positions": {},
    "custom_icons_kind": {},
    "custom_icons_hw": {},
    "custom_icons_sensor": {},
    "log_debug": False,
    "update_check_auto": False,
    "update_last_check": 0,
    "thresholds": {},               # sensor key -> [amber, orange, red] overriding the default colour bands
    "alerts_enabled": True,         # tray notification when a summary / pinned temperature stays red
    "alert_seconds": 10,            # seconds the sensor must stay red before the notification
    "alert_cooldown_min": 5,        # minutes between two notifications of the same sensor
}


# --- per-key validators: (value, strict) -> clean value; ValueError when invalid ----------------------
# strict (Config.set): any bad element raises. Lenient (file load): bad list/dict elements are dropped.
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _enum(choices):
    def clean(v, strict):
        if isinstance(v, str) and v in choices:
            return v
        raise ValueError(f"not one of {choices}")
    return clean


def _bool(v, strict):
    if isinstance(v, bool):
        return v
    raise ValueError("not a bool")


def _str_list(v, strict, limit, item_ok=None, dedupe=False):
    if not isinstance(v, list):
        raise ValueError("not a list")
    out: list[str] = []
    for item in v:
        ok = isinstance(item, str) and 0 < len(item) <= MAX_KEY_LEN and (item_ok is None or item_ok(item))
        if not ok:
            if strict:
                raise ValueError(f"invalid item {item!r}")
            continue
        if dedupe and item in out:
            continue
        out.append(item)
    if len(out) > limit:
        if strict:
            raise ValueError("too many items")
        out = out[:limit]
    return out


def _interval(v, strict):
    if not _is_int(v):
        raise ValueError("not an int")
    return min(10000, max(500, v))


def _pinned(v, strict):
    return _str_list(v, strict, MAX_PINNED)


def _collapsed(v, strict):
    return _str_list(v, strict, MAX_COLLAPSED)


def _hidden(v, strict):
    return _str_list(v, strict, MAX_HIDDEN_HARDWARE, lambda s: HW_ID_RE.fullmatch(s) is not None, True)


def _hw_order_item(s: str) -> bool:
    if s.startswith(CATEGORY_PREFIX):
        return s[len(CATEGORY_PREFIX):] in CATEGORY_ORDER
    return HW_ID_RE.fullmatch(s) is not None


def _hw_order(v, strict):
    return _str_list(v, strict, MAX_HW_ORDER, _hw_order_item, True)


def _categories(v, strict):
    out = _str_list(v, strict, len(SUMMARY_CATEGORIES), lambda s: s in SUMMARY_CATEGORIES, True)
    return [c for c in SUMMARY_CATEGORIES if c in out]


def _overrides(v, strict):
    if not isinstance(v, dict):
        raise ValueError("not a dict")
    out = {}
    for k, val in v.items():
        if not (isinstance(k, str) and 0 < len(k) <= MAX_KEY_LEN and isinstance(val, bool)):
            if strict:
                raise ValueError(f"invalid override {k!r}")
            continue
        out[k] = val
    if len(out) > MAX_OVERRIDES:
        if strict:
            raise ValueError("too many overrides")
        out = dict(list(out.items())[:MAX_OVERRIDES])
    return out


def _toggled(v, strict):
    return _str_list(v, strict, MAX_DETAIL_TOGGLED, dedupe=True)


def _geometry(v, strict):
    if not isinstance(v, dict):
        raise ValueError("not a dict")
    if not v:
        return {}
    ok = (set(v) == {"x", "y", "w", "h"} and all(_is_int(n) for n in v.values())
          and 200 <= v["w"] <= 8000 and 200 <= v["h"] <= 8000
          and -32000 <= v["x"] <= 32000 and -32000 <= v["y"] <= 32000)
    if not ok:
        if strict:
            raise ValueError("invalid geometry")
        return {}
    return {k: v[k] for k in ("x", "y", "w", "h")}


def _custom_colors(v, strict):
    if not isinstance(v, dict):
        raise ValueError("not a dict")
    out = dict(DEFAULT_CUSTOM_COLORS)
    for k in CUSTOM_COLOR_KEYS:
        if k not in v:
            continue
        c = v[k]
        if isinstance(c, str) and HEX_COLOR_RE.fullmatch(c):
            out[k] = c.upper()
        elif strict:
            raise ValueError(f"invalid colour for {k!r}")
    if strict and set(v) - set(CUSTOM_COLOR_KEYS):
        raise ValueError("unknown colour key")
    return out


def _ui_scale(v, strict):
    if _is_int(v) and v in UI_SCALES:
        return v
    raise ValueError("invalid scale")


def _monitor(v, strict):
    if isinstance(v, str) and (v == "primary" or MONITOR_KEY_RE.fullmatch(v)):
        return v
    raise ValueError("invalid monitor key")


def _floating(v, strict):
    if not isinstance(v, dict):
        raise ValueError("not a dict")
    out = {}
    for mon, pos in v.items():
        ok = (isinstance(mon, str) and (mon == "primary" or MONITOR_KEY_RE.fullmatch(mon) is not None)
              and isinstance(pos, dict) and pos.get("anchor") in FLOATING_ANCHORS
              and _is_int(pos.get("dx")) and _is_int(pos.get("dy")))
        if not ok:
            if strict:
                raise ValueError(f"invalid floating entry {mon!r}")
            continue
        if len(out) >= MAX_FLOATING:
            if strict:
                raise ValueError("too many floating entries")
            continue
        out[mon] = {"anchor": pos["anchor"], "dx": max(-MAX_OFFSET, min(MAX_OFFSET, pos["dx"])),
                    "dy": max(-MAX_OFFSET, min(MAX_OFFSET, pos["dy"]))}
    return out


def _icons(key_ok):
    def clean(v, strict):
        if not isinstance(v, dict):
            raise ValueError("not a dict")
        out = {}
        for k, f in v.items():
            ok = (isinstance(k, str) and 0 < len(k) <= MAX_KEY_LEN and key_ok(k)
                  and isinstance(f, str) and ICON_FILE_RE.fullmatch(f) is not None)
            if not ok:
                if strict:
                    raise ValueError(f"invalid icon entry {k!r}")
                continue
            if len(out) >= MAX_ICONS:
                if strict:
                    raise ValueError("too many icons")
                continue
            out[k] = f
        return out
    return clean


def _threshold_triple(t):
    """[amber, orange, red] as floats, strictly increasing within bounds; None when invalid."""
    if not isinstance(t, (list, tuple)) or len(t) != 3:
        return None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in t):
        return None
    a, o, r = (float(x) for x in t)
    if not all(THRESHOLD_MIN <= x <= THRESHOLD_MAX for x in (a, o, r)):    # also rejects NaN / inf
        return None
    if not (a < o < r):
        return None
    return [a, o, r]


def _thresholds(v, strict):
    if not isinstance(v, dict):
        raise ValueError("not a dict")
    out = {}
    for k, t in v.items():
        triple = _threshold_triple(t) if isinstance(k, str) and 0 < len(k) <= MAX_KEY_LEN else None
        if triple is None:
            if strict:
                raise ValueError(f"invalid thresholds for {k!r}")
            continue
        if len(out) >= MAX_THRESHOLDS:
            if strict:
                raise ValueError("too many thresholds")
            continue
        out[k] = triple
    return out


def _int_range(lo, hi):
    def clean(v, strict):
        if not _is_int(v):
            raise ValueError("not an int")
        return min(hi, max(lo, v))
    return clean


def _update_last_check(v, strict):
    if _is_int(v) and 0 <= v <= 4102444800:
        return v
    raise ValueError("invalid timestamp")


def _version(v, strict):
    if _is_int(v) and 0 <= v <= 1000:
        return v
    raise ValueError("invalid version")


_CLEANERS = {
    "config_version": _version,
    "interval_ms": _interval,
    "pinned": _pinned,
    "theme": _enum(THEMES),
    "fahrenheit": _bool,
    "group_pills": _bool,
    "pill_style": _enum(PILL_STYLES),
    "pill_labels": _bool,
    "pill_bars": _bool,
    "stack_pills": _bool,
    "start_with_windows": _bool,
    "collapsed": _collapsed,
    "hidden_hardware": _hidden,
    "hw_order": _hw_order,
    "summary_categories": _categories,
    "summary_overrides": _overrides,
    "detail_toggled": _toggled,
    "detail_geometry": _geometry,
    "color_palette": _enum(COLOR_PALETTES),
    "custom_colors": _custom_colors,
    "icon_style": _enum(ICON_STYLES),
    "ui_scale": _ui_scale,
    "panel_size": _enum(PANEL_SIZES),
    "language": _enum(LANGUAGE_CHOICES),
    "placement_mode": _enum(PLACEMENT_MODES),
    "taskbar_anchor": _enum(TASKBAR_ANCHORS),
    "placement_monitor": _monitor,
    "floating_positions": _floating,
    "show_in_fullscreen": _bool,
    "fullscreen_placement": _enum(FULLSCREEN_PLACEMENTS),
    "fullscreen_floating_positions": _floating,
    "custom_icons_kind": _icons(lambda k: k in HW_KINDS),
    "custom_icons_hw": _icons(lambda k: HW_ID_RE.fullmatch(k) is not None),
    "custom_icons_sensor": _icons(lambda k: True),
    "log_debug": _bool,
    "update_check_auto": _bool,
    "update_last_check": _update_last_check,
    "thresholds": _thresholds,
    "alerts_enabled": _bool,
    "alert_seconds": _int_range(*ALERT_SECONDS_RANGE),
    "alert_cooldown_min": _int_range(*ALERT_COOLDOWN_MIN_RANGE),
}
assert set(_CLEANERS) == set(DEFAULTS)


def clean_value(key: str, value, strict: bool = True):
    """Validated copy of `value` for `key`; ValueError when invalid (lenient mode drops bad list items)."""
    if key not in _CLEANERS:
        raise ValueError(f"unknown config key {key!r}")
    return _CLEANERS[key](value, strict)


def _copy(v):
    return copy.deepcopy(v) if isinstance(v, (list, dict)) else v


def _migrate(raw: dict) -> dict:
    """Bring a loaded file to the current schema. Phase 1 files (no config_version) are valid v2 subsets."""
    version = raw.get("config_version")
    if version is None:
        log.info("config migrated from version 1 to %d", CONFIG_VERSION)
    elif _is_int(version) and version > CONFIG_VERSION:
        log.warning("config version %s is newer than supported (%d): unknown keys are ignored and the "
                    "file is opened read-only (it will not be overwritten)", version, CONFIG_VERSION)
    return raw


def default_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_DIR_NAME / "config.json"


class ConfigSaveError(ValueError):
    """``Config.set`` could not persist the value (read-only newer config, link/junction, I/O error)."""


class Config:
    """JSON settings store.

    Contract: ``get`` never raises. ``set(key, value)`` raises ``ValueError`` for an unknown key or an invalid
    value and ``ConfigSaveError`` (a ``ValueError`` subclass) when the value is valid but could not be written;
    in both cases the in-memory value is left unchanged. ``save()`` returns True when the file was written and
    False (after logging a warning) otherwise. A file with a newer ``config_version`` is loaded read-only:
    ``save()`` always returns False so data written by a newer app version is never lost.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_path()
        self._data = {k: _copy(v) for k, v in DEFAULTS.items()}
        self.read_only = False
        # False when a config file existed but could not be read (defaults in use): callers must not
        # treat the defaults as the user's real choices (e.g. custom-icon cleanup would delete files).
        self.loaded_ok = True
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "rb") as fh:
                data = fh.read(MAX_CONFIG_BYTES + 1)
            if len(data) > MAX_CONFIG_BYTES:
                log.warning("config larger than %d bytes, using defaults", MAX_CONFIG_BYTES)
                self.loaded_ok = False
                return
            raw = json.loads(data.decode("utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError, RecursionError, MemoryError) as exc:
            log.warning("config unreadable, using defaults: %s", exc)
            self.loaded_ok = False
            return
        if not isinstance(raw, dict):
            self.loaded_ok = False
            return
        raw = _migrate(raw)
        version = raw.get("config_version")
        self.read_only = _is_int(version) and version > CONFIG_VERSION
        for k in DEFAULTS:
            if k == "config_version" or k not in raw:
                continue
            try:
                self._data[k] = clean_value(k, raw[k], strict=False)
            except (ValueError, RecursionError) as exc:
                log.warning("config key %r invalid (%s), using default", k, exc)

    def get(self, key: str, default=None):
        return _copy(self._data.get(key, DEFAULTS.get(key, default)))

    def set(self, key: str, value) -> None:
        """Validate, store and save. ValueError for unknown keys or invalid values, ConfigSaveError when the
        value could not be saved; either way nothing is changed in memory."""
        new = clean_value(key, value, strict=True)
        missing = key not in self._data
        old = self._data.get(key)
        self._data[key] = new
        if not self.save():
            if missing:
                self._data.pop(key, None)
            else:
                self._data[key] = old
            raise ConfigSaveError(f"could not save {key!r}")

    def save(self) -> bool:
        """Write the file atomically; True on success, False (and a warning) when it was not written."""
        if self.read_only:
            log.warning("config has a newer version than supported; not overwriting it")
            return False
        if is_reparse_point(self.path.parent) or is_reparse_point(self.path):
            log.warning("config directory or file is a link/junction; not saving")
            return False
        tmp_name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if is_reparse_point(self.path.parent):
                log.warning("config directory is a link/junction; not saving")
                return False
            self._data["config_version"] = CONFIG_VERSION
            text = json.dumps(self._data, indent=2, ensure_ascii=False)
            fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix="config-", suffix=".tmp")   # O_EXCL
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp_name, self.path)
            tmp_name = None
            return True
        except (OSError, ValueError) as exc:
            log.warning("config not saved: %s", exc)
            return False
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
