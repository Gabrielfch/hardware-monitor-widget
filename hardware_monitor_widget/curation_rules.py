"""Declarative curation table (pure data, no Qt): which sensors go to the summary and which to the detail.

First matching rule wins; list order also fixes the presentation order inside a category (ADR-1).
Rules match on (hardware class, sensor kind, sensor name), never on the name alone.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str                      # unique, for tests and diagnostics
    hw: tuple[str, ...] | str    # hardware classes (cpu gpu ram vram dimm board storage network other) or "*"
    kind: tuple[str, ...] | str  # sensor kinds (lower case) or "*"
    name: str                    # regex, re.fullmatch IGNORECASE; "" = any name
    category: str                # temps loads fans clocks power memory storage other | "auto" (by kind)
    level: str                   # "summary" | "detail"
    group: str = ""              # detail group id ("" = loose under the hardware)
    idle_hide: bool = False      # summary only while the sensor is not idle (value 0 and max 0/None)
    neutral_band: bool = False   # no red band nor "!" (e.g. disk activity)


# Kind -> category for rules with category == "auto".
KIND_CATEGORY = {
    "temperature": "temps", "load": "loads", "level": "other", "clock": "clocks", "factor": "other",
    "power": "power", "voltage": "power", "current": "power", "energy": "power",
    "fan": "fans", "control": "fans", "flow": "fans",
    "data": "memory", "smalldata": "memory", "timing": "memory", "throughput": "storage",
}
DEFAULT_CATEGORY = "other"

CATEGORY_TO_SUMMARY = {
    "temps": "temps_loads", "loads": "temps_loads", "fans": "fans", "clocks": "clocks_power",
    "power": "clocks_power", "memory": "memory_disks", "storage": "memory_disks", "other": "other",
}

GROUP_ORDER = ("cores", "buses", "electrical", "d3d", "controls", "thresholds", "capacity", "activity",
               "health", "timings")

_RAMS = ("ram", "vram")

RULES: tuple[Rule, ...] = (
    # CPU
    Rule("cpu.temp.main", "cpu", "temperature", r"Core \(Tctl/Tdie\)|(CPU )?Package|Core Max", "temps", "summary"),
    Rule("cpu.load.total", "cpu", "load", r"CPU Total", "loads", "summary"),
    Rule("cpu.clock.avg", "cpu", "clock", r"Cores \(Average\)", "clocks", "summary"),
    Rule("cpu.power.pkg", "cpu", "power", r"(CPU )?Package", "power", "summary"),
    Rule("cpu.core", "cpu", "*", r"(CPU )?([PE]-)?Core #\d+.*", "auto", "detail", "cores"),
    Rule("cpu.ccd", "cpu", "temperature", r"CCDs?.*", "temps", "detail"),
    Rule("cpu.bus", "cpu", "clock", r"Bus Speed|Fabric|Uncore|Memory", "clocks", "detail", "buses"),
    # GPU
    Rule("gpu.temp.main", "gpu", "temperature", r"GPU Core|(GPU )?Hot Spot", "temps", "summary"),
    Rule("gpu.load.core", "gpu", "load", r"GPU Core", "loads", "summary"),
    Rule("gpu.clock.core", "gpu", "clock", r"GPU Core", "clocks", "summary"),
    Rule("gpu.power.pkg", "gpu", "power", r"GPU (Package|Power)", "power", "summary"),
    Rule("gpu.mem.used", "gpu", ("smalldata", "data"), r"GPU Memory Used", "memory", "summary"),
    Rule("gpu.d3d", "gpu", "*", r"D3D .*", "auto", "detail", "d3d"),
    # System memory
    Rule("ram.main", _RAMS, "load", r"Memory", "memory", "summary"),
    Rule("ram.used", _RAMS, "data", r"Memory (Used|Available)", "memory", "summary"),
    Rule("dimm.timing", "dimm", "timing", "", "memory", "detail", "timings"),
    # Storage
    Rule("sto.temp.thr", "storage", "temperature", r"(Warning|Critical)( Composite)? Temperature.*",
         "temps", "detail", "thresholds"),
    Rule("sto.temp.main", "storage", "temperature", r"(Composite )?Temperature", "temps", "summary"),
    Rule("sto.used", "storage", "load", r"Used Space", "storage", "summary"),
    Rule("sto.activity.rw", "storage", "load", r"(Read|Write) Activity", "storage", "summary",
         neutral_band=True),
    Rule("sto.activity.total", "storage", "load", r"Total Activity", "storage", "detail", "activity",
         neutral_band=True),
    Rule("sto.rate", "storage", "throughput", r"(Read|Write) Rate", "storage", "detail", "activity"),
    Rule("sto.capacity", "storage", "data", r"(Free|Total) Space", "storage", "detail", "capacity"),
    Rule("sto.health", "storage", ("level", "data", "factor"),
         r"Life|Available Spare.*|Percentage Used|Data (Read|Written)|Power On (Count|Hours)",
         "storage", "detail", "health"),
    # Network
    Rule("net.speed", "network", "throughput", r"(Upload|Download) Speed", "other", "summary"),
    # Generic
    Rule("any.fan", "*", "fan", "", "fans", "summary", idle_hide=True),
    Rule("any.control", "*", "control", "", "fans", "detail", "controls"),
    Rule("any.electrical", "*", ("voltage", "current"), "", "power", "detail", "electrical"),
    Rule("any.temp", "*", "temperature", "", "temps", "detail"),
    Rule("any.fallback", "*", "*", "", "auto", "detail"),
)
