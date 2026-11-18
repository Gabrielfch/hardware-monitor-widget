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


def summary_sections(snapshot: Snapshot, prefs: Prefs) -> list[SummarySection]:
    rank = {c: i for i, c in enumerate(SUMMARY_CATEGORIES)}
    found: dict[str, tuple[int, SummarySection, list[tuple]]] = {}
    for i, s in enumerate(snapshot.sensors):
        if s.hw_id in prefs.hidden_hardware or not in_summary(s, prefs):
            continue
        entry = found.get(s.hw_id)
        if entry is None:
            sec = SummarySection(s.hw_id, s.hw_kind, hw_class(s.hw_id, s.hw_kind), s.hardware, [])
            entry = found[s.hw_id] = (i, sec, [])
        c = classify(s)
        entry[2].append(((rank.get(c.summary_category, len(rank)), c.order, s.key), s))
    out = []
    for first, sec, items in sorted(found.values(),
                                    key=lambda e: _hw_sort_key(e[1].hw_kind, e[1].hw_class, e[0])):
        items.sort(key=lambda t: t[0])
        sec.sensors = [s for _k, s in items]
        out.append(sec)
    return out


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
