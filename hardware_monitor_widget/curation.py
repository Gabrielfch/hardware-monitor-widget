"""Sensor curation engine (pure, no Qt): decides what the summary shows and how the detail tree is grouped."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Mapping

from .curation_rules import (CATEGORY_TO_SUMMARY, DEFAULT_CATEGORY, GROUP_ORDER, KIND_CATEGORY, RULES,
                             Rule)
from .models import HW_KINDS, Sensor, Snapshot

SUMMARY_CATEGORIES = ("temps_loads", "fans", "clocks_power", "memory_disks", "other")
MAX_OVERRIDES = 1000

_CLASS_RANK = {"ram": 0, "vram": 1, "dimm": 2}   # sections of the same hw_kind: memory before virtual memory
_KIND_TO_CLASS = {"cpu": "cpu", "gpu": "gpu", "ram": "ram", "mainboard": "board", "storage": "storage",
                  "network": "network"}


def hw_class(hw_id: str, hw_kind: str) -> str:
    """Hardware class used by the rules: separates RAM / virtual memory / DIMMs (all `ram` in hw_kind)."""
    hid = hw_id or ""
    if hid == "hw//vram" or hid.startswith("hw//vram/"):
        return "vram"
    if hid.startswith("hw//memory/dimm") or "/dimm/" in hid:
        return "dimm"
    if hid == "hw//ram" or hid.startswith("hw//ram/"):
        return "ram"
    return _KIND_TO_CLASS.get(hw_kind, "other")


# --- rule compilation -------------------------------------------------------------------------------
def _as_tuple(v: tuple[str, ...] | str) -> tuple[str, ...]:
    return (v,) if isinstance(v, str) else tuple(v)


@dataclass(frozen=True)
class _Compiled:
    rule: Rule
    order: int
    hw: tuple[str, ...]
    kind: tuple[str, ...]
    name: re.Pattern | None


def _compile() -> tuple[_Compiled, ...]:
    ids = [r.id for r in RULES]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate curation rule ids")
    return tuple(_Compiled(r, i, _as_tuple(r.hw), _as_tuple(r.kind),
                           re.compile(r.name, re.IGNORECASE) if r.name else None)
                 for i, r in enumerate(RULES))


_COMPILED = _compile()


@dataclass(frozen=True)
class Classification:
    rule_id: str
    order: int
    category: str
    level: str
    group: str
    idle_hide: bool
    neutral_band: bool

    @property
    def summary_category(self) -> str:
        return CATEGORY_TO_SUMMARY.get(self.category, "other")


@lru_cache(maxsize=8192)
def _classify(cls: str, kind: str, name: str) -> Classification:
    for c in _COMPILED:
        if "*" not in c.hw and cls not in c.hw:
            continue
        if "*" not in c.kind and kind not in c.kind:
            continue
        if c.name is not None and c.name.fullmatch(name) is None:
            continue
        r = c.rule
        cat = KIND_CATEGORY.get(kind, DEFAULT_CATEGORY) if r.category == "auto" else r.category
        return Classification(r.id, c.order, cat, r.level, r.group, r.idle_hide, r.neutral_band)
    last = _COMPILED[-1]   # unreachable while the last rule is the wildcard; kept as a safety net
    return Classification(last.rule.id, last.order, DEFAULT_CATEGORY, "detail", "", False, False)


def classify(s: Sensor) -> Classification:
    """Never raises; always returns a rule (the last one matches everything)."""
    return _classify(hw_class(str(s.hw_id), str(s.hw_kind)), str(s.kind).lower(), str(s.name))


def is_idle(s: Sensor) -> bool:
    return s.value in (None, 0) and s.max in (None, 0)


@dataclass(frozen=True)
class Prefs:
    hidden_hardware: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset(SUMMARY_CATEGORIES)
    overrides: Mapping[str, bool] = field(default_factory=dict)


def default_in_summary(s: Sensor, categories: Iterable[str] = SUMMARY_CATEGORIES) -> bool:
    c = classify(s)
    return (c.level == "summary" and not (c.idle_hide and is_idle(s))
            and c.summary_category in categories)


def in_summary(s: Sensor, prefs: Prefs) -> bool:
    ov = prefs.overrides.get(s.key)
    if ov is not None:
        return bool(ov)
    return default_in_summary(s, prefs.categories)


def _hw_sort_key(hw_kind: str, cls: str, first_index: int) -> tuple[int, int, int]:
    kind_rank = HW_KINDS.index(hw_kind) if hw_kind in HW_KINDS else len(HW_KINDS)
    return kind_rank, _CLASS_RANK.get(cls, 0), first_index


@dataclass
class SummarySection:
    hw_id: str
    hw_kind: str
    hw_class: str
    hardware: str
    sensors: list[Sensor]


_SUMMARY_RANK = {c: i for i, c in enumerate(SUMMARY_CATEGORIES)}


def sensor_sort_key(s: Sensor) -> tuple:
    """Stable order of the values of one component (summary category, rule order, key): the same in the
    panel rows and in the pills, so pinning or unpinning a sensor never reorders the others."""
    c = classify(s)
    return _SUMMARY_RANK.get(c.summary_category, len(_SUMMARY_RANK)), c.order, s.key


def summary_sections(snapshot: Snapshot, prefs: Prefs) -> list[SummarySection]:
    found: dict[str, tuple[int, SummarySection, list[tuple]]] = {}
    for i, s in enumerate(snapshot.sensors):
        if s.hw_id in prefs.hidden_hardware or not in_summary(s, prefs):
            continue
        entry = found.get(s.hw_id)
        if entry is None:
            sec = SummarySection(s.hw_id, s.hw_kind, hw_class(s.hw_id, s.hw_kind), s.hardware, [])
            entry = found[s.hw_id] = (i, sec, [])
        entry[2].append((sensor_sort_key(s), s))
    out = []
    for first, sec, items in sorted(found.values(),
                                    key=lambda e: _hw_sort_key(e[1].hw_kind, e[1].hw_class, e[0])):
        items.sort(key=lambda t: t[0])
        sec.sensors = [s for _k, s in items]
        out.append(sec)
    return out


# --- panel categories and user order (hw_order) ------------------------------------------------------
CATEGORY_ORDER = ("cpu", "gpu", "ram", "storage", "mainboard", "network", "other")   # default panel order
CATEGORY_PREFIX = "cat:"
MAX_HW_ORDER = 200


def hw_category(hw_kind: str) -> str:
    """Panel category of a hardware kind (every kind is its own category; anything unknown is "other")."""
    return hw_kind if hw_kind in CATEGORY_ORDER else "other"


def category_id(cat: str) -> str:
    return CATEGORY_PREFIX + cat


def arrange_hardware(by_cat: Mapping[str, list[str]], hw_order: Iterable[str]) -> list[tuple[str, list[str]]]:
    """[(category, [hw_id, ...])] in display order. `hw_order` is the saved list of `cat:<category>` and hw_id
    entries: listed items keep that relative order, unknown entries are ignored and unlisted items (new
    categories / hardware) stay in default order after the listed ones."""
    order = list(hw_order)
    pos: dict[str, int] = {}
    for i, x in enumerate(order):
        pos.setdefault(x, i)
    end = len(order)
    cats = sorted((c for c in CATEGORY_ORDER if by_cat.get(c)), key=lambda c: pos.get(category_id(c), end))
    return [(c, sorted(by_cat[c], key=lambda h: pos.get(h, end))) for c in cats]


def order_hardware(snapshot: Snapshot, hw_order: Iterable[str] = ()) -> list[tuple[str, list[str]]]:
    """Every hardware of the snapshot grouped by category, in display order (see `arrange_hardware`).
    Default order inside a category: memory before virtual memory before modules, then first appearance."""
    first: dict[str, tuple[int, int, str]] = {}
    for i, s in enumerate(snapshot.sensors):
        if s.hw_id not in first:
            first[s.hw_id] = (_CLASS_RANK.get(hw_class(s.hw_id, s.hw_kind), 0), i, hw_category(s.hw_kind))
    by_cat: dict[str, list[str]] = {}
    for hw_id, (_r, _i, cat) in sorted(first.items(), key=lambda kv: kv[1][:2]):
        by_cat.setdefault(cat, []).append(hw_id)
    return arrange_hardware(by_cat, hw_order)


def order_sensors(sensors: list[Sensor], snapshot: Snapshot, hw_order: Iterable[str] = ()) -> list[Sensor]:
    """`sensors` in display order: by category and component (as the panel), then by `sensor_sort_key`."""
    rank = {h: i for i, h in enumerate(h for _c, ids in order_hardware(snapshot, hw_order) for h in ids)}
    end = len(rank)
    return sorted(sensors, key=lambda s: (rank.get(s.hw_id, end), sensor_sort_key(s)))


def flatten_order(layout: list[tuple[str, list[str]]], old: Iterable[str] = (),
                  limit: int = MAX_HW_ORDER) -> list[str]:
    """New `hw_order` from a displayed layout: each category id followed by its components, then the old
    entries that are not displayed now (kept so their position is remembered), cut to `limit`."""
    out: list[str] = []
    for cat, ids in layout:
        out.append(category_id(cat))
        out.extend(ids)
    seen = set(out)
    out.extend(x for x in old if x not in seen and not seen.add(x))
    return out[:limit]


def move_to(ids: list[str], item: str, index: int) -> list[str]:
    """`ids` with `item` moved so that it ends at position `index` (clamped)."""
    if item not in ids:
        return list(ids)
    rest = [x for x in ids if x != item]
    rest.insert(max(0, min(index, len(rest))), item)
    return rest


@dataclass
class DetailGroup:
    id: str
    sensors: list[Sensor]
    count: int


@dataclass
class DetailHardware:
    hw_id: str
    hw_kind: str
    hw_class: str
    hardware: str
    loose: list[Sensor] = field(default_factory=list)
    groups: list[DetailGroup] = field(default_factory=list)


@dataclass
class DetailTree:
    hardware: list[DetailHardware] = field(default_factory=list)
    modules: list[DetailHardware] = field(default_factory=list)


_CORE_RE = re.compile(r"(?:([PE])-)?Core #(\d+)", re.IGNORECASE)


def core_count(sensors: Iterable[Sensor]) -> int:
    """Distinct physical cores: from clock/factor/SMU/VID sensors; per-thread loads only if nothing else."""
    sensors = list(sensors)
    def ids(items):
        found = set()
        for s in items:
            m = _CORE_RE.search(s.name)
            if m:
                found.add(((m.group(1) or "").upper(), int(m.group(2))))
        return found
    cores = ids(s for s in sensors if s.kind != "load")
    return len(cores or ids(sensors))


def detail_tree(snapshot: Snapshot) -> DetailTree:
    """Every sensor, grouped; ignores hidden hardware and categories."""
    buckets: dict[str, tuple[int, DetailHardware, list[tuple[int, int, Sensor]]]] = {}
    for i, s in enumerate(snapshot.sensors):
        entry = buckets.get(s.hw_id)
        if entry is None:
            hw = DetailHardware(s.hw_id, s.hw_kind, hw_class(s.hw_id, s.hw_kind), s.hardware)
            entry = buckets[s.hw_id] = (i, hw, [])
        entry[2].append((classify(s).order, i, s))
    group_rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    ordered = sorted(buckets.values(), key=lambda e: _hw_sort_key(e[1].hw_kind, e[1].hw_class, e[0]))
    tree = DetailTree()
    for _first, hw, items in ordered:
        groups: dict[str, list[Sensor]] = {}
        for order, _i, s in sorted(items, key=lambda t: (t[0], t[1])):
            gid = classify(s).group
            if gid:
                groups.setdefault(gid, []).append(s)
            else:
                hw.loose.append(s)
        for gid in sorted(groups, key=lambda g: group_rank.get(g, len(group_rank))):
            items_g = groups[gid]
            hw.groups.append(DetailGroup(gid, items_g, core_count(items_g) if gid == "cores" else len(items_g)))
        (tree.modules if hw.hw_class == "dimm" else tree.hardware).append(hw)
    return tree


def section_title_key(cls: str) -> str | None:
    return {"ram": "hw_ram", "vram": "hw_vram"}.get(cls)


def toggle_override(overrides: Mapping[str, bool], s: Sensor, want: bool, prefs: Prefs) -> dict:
    """New overrides making `s` appear (want=True) or not in the summary; the entry is dropped when `want`
    equals the rule default. Category switches are considered for the default."""
    new = dict(overrides)
    if bool(want) == default_in_summary(s, prefs.categories):
        new.pop(s.key, None)
    else:
        new[s.key] = bool(want)
    return new
