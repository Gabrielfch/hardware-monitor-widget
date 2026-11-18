"""Readable sensor names (pure, no Qt): maps (hardware class, kind, LibreHardwareMonitor name) to a translated name.

The table is data: each entry is (hardware classes | "*", kinds | "*", regex, i18n key). The text lives in
`strings_panel.py` (keys `sn_*`). A capture group 1, when present, is passed to the text as `{n}`.
Unknown sensors keep their original name (the fallback), so the table never has to be exhaustive.
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from typing import Iterable

from . import i18n
from .curation import hw_class
from .models import Sensor

_CPU, _GPU, _RAMS, _BOARD, _STO, _NET = ("cpu",), ("gpu",), ("ram", "vram"), ("board",), ("storage",), ("network",)

# (hardware classes, kinds, name regex (fullmatch, ignore case), i18n key)
_TABLE: tuple[tuple, ...] = (
    # CPU
    (_CPU, "temperature", r"Core \(Tctl/Tdie\)", "sn_cpu_tctl"),
    (_CPU, "temperature", r"CCD(\d+) \(Tdie\)", "sn_cpu_ccd"),
    (_CPU, "temperature", r"Core Max", "sn_cpu_core_max_temp"),
    (_CPU, "load", r"CPU Total", "sn_cpu_total"),
    (_CPU, "load", r"CPU Core Max", "sn_cpu_core_max_load"),
    (_CPU, "clock", r"Cores \(Average\)", "sn_cpu_clock_avg"),
    (_CPU, "clock", r"Cores \(Average Effective\)", "sn_cpu_clock_eff"),
    (_CPU, "power", r"(?:CPU )?Package", "sn_cpu_package"),
    # LHM "CPU Core #n" loads are per logical thread (12 on a 6-core SMT CPU): name them as threads
    (_CPU, "load", r"CPU Core #(\d+)", "sn_cpu_thread_load"),
    (_CPU, "*", r"(?:CPU )?Core #(\d+)", "sn_cpu_core_n"),
    (_CPU, "clock", r"Bus Speed", "sn_cpu_bus"),
    # GPU
    (_GPU, "temperature", r"GPU Core", "sn_gpu_core_temp"),
    (_GPU, "temperature", r"(?:GPU )?Hot Spot", "sn_gpu_hotspot"),
    (_GPU, "temperature", r"GPU Memory", "sn_gpu_mem_temp"),
    (_GPU, "load", r"GPU Core", "sn_gpu_core_load"),
    (_GPU, "load", r"GPU Memory", "sn_gpu_mem_load"),
    (_GPU, "clock", r"GPU Core", "sn_gpu_core_clock"),
    (_GPU, "clock", r"GPU Memory", "sn_gpu_mem_clock"),
    (_GPU, "clock", r"GPU SoC", "sn_gpu_soc_clock"),
    (_GPU, "voltage", r"GPU Core", "sn_gpu_core_volt"),
    (_GPU, ("fan", "control"), r"GPU Fan", "sn_fan"),
    (_GPU, "power", r"GPU (?:Package|Power)", "sn_gpu_power"),
    (_GPU, ("smalldata", "data"), r"GPU Memory Used", "sn_vram_used"),
    (_GPU, ("smalldata", "data"), r"GPU Memory Free", "sn_vram_free"),
    (_GPU, ("smalldata", "data"), r"GPU Memory Total", "sn_vram_total"),
    # System memory and virtual memory
    (_RAMS, "load", r"Memory", "sn_mem_usage"),
    (_RAMS, "data", r"Memory Used", "sn_mem_used"),
    (_RAMS, "data", r"Memory Available", "sn_mem_available"),
    ("dimm", "data", r"Capacity", "sn_capacity"),
    # Fans (any hardware)
    ("*", ("fan", "control"), r"Fan #(\d+)", "sn_fan_n"),
    # Storage
    (_STO, "temperature", r"(?:Composite )?Temperature", "sn_temperature"),
    (_STO, "temperature", r"Warning(?: Composite)? Temperature", "sn_temp_warning"),
    (_STO, "temperature", r"Critical(?: Composite)? Temperature", "sn_temp_critical"),
    (_STO, "load", r"Used Space", "sn_sto_used"),
    (_STO, "load", r"Read Activity", "sn_sto_read"),
    (_STO, "load", r"Write Activity", "sn_sto_write"),
    (_STO, "load", r"Total Activity", "sn_sto_total_activity"),
    (_STO, "data", r"Free Space", "sn_sto_free"),
    (_STO, "data", r"Total Space", "sn_sto_total"),
    (_STO, "data", r"Data Read", "sn_sto_data_read"),
    (_STO, "data", r"Data Written", "sn_sto_data_written"),
    (_STO, "throughput", r"Read Rate", "sn_sto_read_rate"),
    (_STO, "throughput", r"Write Rate", "sn_sto_write_rate"),
    (_STO, "level", r"Life", "sn_sto_life"),
    (_STO, "level", r"Available Spare", "sn_sto_spare"),
    (_STO, "level", r"Percentage Used", "sn_sto_pct_used"),
    (_STO, "factor", r"Power On Hours", "sn_sto_hours"),
    (_STO, "factor", r"Power On Count", "sn_sto_count"),
    # Network
    (_NET, "throughput", r"Upload Speed", "sn_net_up"),
    (_NET, "throughput", r"Download Speed", "sn_net_down"),
    # Motherboard
    (_BOARD, "voltage", r"CMOS Battery", "sn_cmos"),
)


def _as_tuple(v) -> tuple[str, ...]:
    return (v,) if isinstance(v, str) else tuple(v)


_COMPILED = tuple((_as_tuple(hw), _as_tuple(kind), re.compile(rx, re.IGNORECASE), key)
                  for hw, kind, rx, key in _TABLE)


@lru_cache(maxsize=8192)
def _display(cls: str, kind: str, name: str, lang: str) -> str:
    for hws, kinds, rx, key in _COMPILED:
        if ("*" in hws or cls in hws) and ("*" in kinds or kind in kinds):
            m = rx.fullmatch(name)
            if m:
                return i18n.tr_in(lang, key, n=m.group(1) if m.groups() else "")
    return name


def display_name(sensor: Sensor, lang: str | None = None) -> str:
    """Translated, readable name of `sensor` (its original name when the table has no entry)."""
    return _display(hw_class(sensor.hw_id, sensor.hw_kind), sensor.kind, sensor.name,
                    lang or i18n.get_language())


def kind_text(kind: str, lang: str | None = None) -> str:
    label = i18n.tr_in(lang or i18n.get_language(), "kind_" + kind)
    return kind if label == "kind_" + kind else label


def display_labels(sensors: Iterable[Sensor], lang: str | None = None) -> dict[str, str]:
    """key -> shown name for sensors that share a section: when two translated names are equal the type is
    appended ("Core #1 · clock")."""
    lang = lang or i18n.get_language()
    sensors = list(sensors)
    names = {s.key: display_name(s, lang) for s in sensors}
    count = Counter(names.values())
    return {s.key: (f"{names[s.key]} · {kind_text(s.kind, lang)}" if count[names[s.key]] > 1 else names[s.key])
            for s in sensors}


def uniquify(names: list[str]) -> list[str]:
    """Same names get an index suffix (" (1)", " (2)", ...) so every accessible name is unique."""
    count = Counter(names)
    seen: Counter = Counter()
    out = []
    for n in names:
        if count[n] > 1:
            seen[n] += 1
            out.append(f"{n} ({seen[n]})")
        else:
            out.append(n)
    return out
