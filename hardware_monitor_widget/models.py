"""Shared data model (pure, no Qt): sensors read by the bundled native sensor host (LibreHardwareMonitorLib)."""
from __future__ import annotations

from dataclasses import dataclass, field

SOURCES = ("native",)

# Hardware kinds (drive the icon and section order in the panel).
HW_KINDS = ("cpu", "gpu", "ram", "mainboard", "storage", "network", "other")

# Sensor kinds -> display unit. Mirrors LibreHardwareMonitor SensorType names (lower case).
UNITS = {
    "temperature": "°C",
    "load": "%",
    "level": "%",
    "control": "%",
    "clock": "MHz",
    "power": "W",
    "voltage": "V",
    "fan": "RPM",
    "flow": "L/h",
    "data": "GB",
    "smalldata": "MB",
    "throughput": "B/s",
    "factor": "",
    "current": "A",
    "energy": "mWh",
    "frequency": "Hz",
    "timespan": "s",
    "noise": "dBA",
    "humidity": "%",
    "conductivity": "µS/cm",
    "timing": "ns",
}


@dataclass(frozen=True)
class Sensor:
    key: str                 # stable id, e.g. "hw//amdcpu/0/temperature/2"
    source: str              # "native"
    hardware: str            # hardware display name, e.g. "AMD Ryzen 5 3600"
    hw_id: str               # stable hardware id (groups rows in the panel), e.g. "hw//amdcpu/0"
    hw_kind: str             # one of HW_KINDS
    kind: str                # one of UNITS keys
    name: str                # sensor display name, e.g. "Core #1", "CPU Package"
    value: float | None
    min: float | None = None
    max: float | None = None
    limit: float | None = None   # scale for the pill bar (e.g. TjMax for temperatures); None -> default

    @property
    def unit(self) -> str:
        return UNITS.get(self.kind, "")


@dataclass(frozen=True)
class SourceStatus:
    source: str              # "native"
    available: bool
    reason: str = ""         # "ok" | "needs_admin" | "driver_missing" | "error" | "stale"
    detail: str = ""         # free text for logs/tooltips (no personal data)
    method: str = ""         # "lhm" (LibreHardwareMonitor sensor host)


@dataclass
class Snapshot:
    sensors: list[Sensor] = field(default_factory=list)
    statuses: dict[str, SourceStatus] = field(default_factory=dict)
    taken_at: float = 0.0

    def by_key(self) -> dict[str, Sensor]:
        return {s.key: s for s in self.sensors}


# --- colour bands (pure) -------------------------------------------------------------------------
BANDS = ("green", "amber", "orange", "red", "grey")

TEMP_BANDS = (60.0, 75.0, 85.0)      # °C thresholds: < green, < amber, < orange, else red
LOAD_BANDS = (60.0, 85.0, 95.0)      # %


def band_for(kind: str, value: float | None) -> str:
    """Colour band for a sensor value: green / amber / orange / red, or grey (no value / neutral kind)."""
    if value is None:
        return "grey"
    if kind == "temperature":
        limits = TEMP_BANDS
    elif kind in ("load", "level"):
        limits = LOAD_BANDS
    else:
        return "grey"
    if value < limits[0]:
        return "green"
    if value < limits[1]:
        return "amber"
    if value < limits[2]:
        return "orange"
    return "red"


STALE_REASONS = ("stale", "error")


def source_reason(snapshot: "Snapshot", source: str = "native") -> str:
    """Status reason of `source` ("" when the snapshot carries no status for it)."""
    st = snapshot.statuses.get(source)
    return st.reason if st is not None else ""


def is_stale(snapshot: "Snapshot") -> bool:
    """True when the sensor values shown are the last known ones (host stalled or failed)."""
    return any(st.reason in STALE_REASONS for st in snapshot.statuses.values())


def format_value(kind: str, value: float | None, fahrenheit: bool = False, decimal_sep: str = ".") -> str:
    """Compact display text, e.g. '54°C', '12%', '3600 MHz', '65.2 W', '1.250 V'.

    `decimal_sep` replaces the decimal point of the number ("," for Spanish)."""
    text = _format(kind, value, fahrenheit)
    return text.replace(".", decimal_sep) if decimal_sep != "." else text


def _format(kind: str, value: float | None, fahrenheit: bool) -> str:
    if value is None:
        return "—"
    if kind == "temperature":
        if fahrenheit:
            return f"{value * 9 / 5 + 32:.0f}°F"
        return f"{value:.0f}°C"
    if kind in ("load", "level", "control", "humidity"):
        return f"{value:.0f}%"
    if kind == "clock":
        return f"{value:.0f} MHz"
    if kind == "power":
        return f"{value:.1f} W"
    if kind == "voltage":
        return f"{value:.3f} V"
    if kind == "current":
        return f"{value:.2f} A"
    if kind == "energy":
        return f"{value:.0f} mWh"
    if kind == "frequency":
        return f"{value:.0f} Hz"
    if kind == "timespan":
        return f"{value:.0f} s"
    if kind == "noise":
        return f"{value:.0f} dBA"
    if kind == "conductivity":
        return f"{value:.1f} µS/cm"
    if kind == "fan":
        return f"{value:.0f} RPM"
    if kind == "data":
        if value >= 1024:
            return f"{value / 1024:.2f} TB"
        return f"{value:.1f} GB"
    if kind == "smalldata":
        if value >= 1024:
            return f"{value / 1024:.1f} GB"
        return f"{value:.0f} MB"
    if kind == "timing":
        return f"{value:.2f} ns"
    if kind == "factor":
        return f"{value:.2f}" if abs(value) < 100 else f"{value:.0f}"
    if kind == "throughput":
        if value >= 1024 * 1024:
            return f"{value / (1024 * 1024):.1f} MB/s"
        if value >= 1024:
            return f"{value / 1024:.0f} KB/s"
        return f"{value:.0f} B/s"
    if kind == "flow":
        return f"{value:.0f} L/h"
    return f"{value:.2f}"
