"""Pinned sensors (pure, no Qt): resolve `auto:*` virtual pins and toggle pins from the panel."""
from __future__ import annotations

from .models import Sensor, Snapshot

AUTO_PREFIX = "auto:"
MAX_PINS = 32


def _of(snapshot: Snapshot, hw_kind: str, kind: str) -> list[Sensor]:
    return [s for s in snapshot.sensors if s.hw_kind == hw_kind and s.kind == kind]


def _name_has(s: Sensor, *needles: str) -> bool:
    n = s.name.lower()
    return any(x.lower() in n for x in needles)


def _resolve_cpu_temp(snapshot: Snapshot) -> Sensor | None:
    temps = _of(snapshot, "cpu", "temperature")
    for s in temps:
        if _name_has(s, "Package", "Tctl", "Tdie"):
            return s
    return temps[0] if temps else None


def _resolve_cpu_load(snapshot: Snapshot) -> Sensor | None:
    loads = _of(snapshot, "cpu", "load")
    for s in loads:
        if s.name.strip().lower() == "cpu total":
            return s
    return None


def _resolve_gpu(snapshot: Snapshot, kind: str) -> Sensor | None:
    items = _of(snapshot, "gpu", kind)
    for s in items:
        if _name_has(s, "GPU Core"):
            return s
    return items[0] if items else None


def _resolve_ram_load(snapshot: Snapshot) -> Sensor | None:
    items = _of(snapshot, "ram", "load")
    for s in items:
        if s.name.strip().lower() == "memory":
            return s
    return items[0] if items else None


def resolve_auto(name: str, snapshot: Snapshot) -> Sensor | None:
    """Sensor an `auto:*` pin stands for in this snapshot (None when nothing matches)."""
    if name == "auto:cpu_temp":
        return _resolve_cpu_temp(snapshot)
    if name == "auto:cpu_load":
        return _resolve_cpu_load(snapshot)
    if name == "auto:gpu_temp":
        return _resolve_gpu(snapshot, "temperature")
    if name == "auto:gpu_load":
        return _resolve_gpu(snapshot, "load")
    if name == "auto:ram_load":
        return _resolve_ram_load(snapshot)
    return None


def resolve_pins(pinned: list[str], snapshot: Snapshot) -> list[Sensor]:
    """Sensors to show as pills, in pin order. Unknown / missing keys are skipped, duplicates dropped."""
    by_key = snapshot.by_key()
    out: list[Sensor] = []
    seen: set[str] = set()
    for pin in pinned:
        s = resolve_auto(pin, snapshot) if pin.startswith(AUTO_PREFIX) else by_key.get(pin)
        if s is not None and s.key not in seen:
            seen.add(s.key)
            out.append(s)
    return out


def _covering_autos(pinned: list[str], sensor: Sensor, snapshot: Snapshot) -> list[str]:
    return [p for p in pinned if p.startswith(AUTO_PREFIX)
            and (r := resolve_auto(p, snapshot)) is not None and r.key == sensor.key]


def is_pinned(pinned: list[str], sensor: Sensor, snapshot: Snapshot) -> bool:
    return sensor.key in pinned or bool(_covering_autos(pinned, sensor, snapshot))


def toggle_pin_result(pinned: list[str], sensor: Sensor, snapshot: Snapshot) -> tuple[list[str], bool]:
    """Like `toggle_pin`, also telling whether the pin was rejected because MAX_PINS was reached."""
    autos = _covering_autos(pinned, sensor, snapshot)
    if sensor.key in pinned or autos:
        drop = set(autos) | {sensor.key}
        return [p for p in pinned if p not in drop], False
    if len(pinned) >= MAX_PINS:
        return list(pinned), True
    return [*pinned, sensor.key], False


def toggle_pin(pinned: list[str], sensor: Sensor, snapshot: Snapshot) -> list[str]:
    """New pin list with `sensor` pinned/unpinned. A sensor covered by an `auto:*` pin is unpinned by
    removing that auto entry (so the default pins can be turned off one by one). At most MAX_PINS pins."""
    return toggle_pin_result(pinned, sensor, snapshot)[0]


def bulk_state(pinned: list[str], sensors: list[Sensor], snapshot: Snapshot) -> str:
    """"none" / "some" / "all" of `sensors` currently pinned (empty list -> "none")."""
    if not sensors:
        return "none"
    n = sum(1 for s in sensors if is_pinned(pinned, s, snapshot))
    if n == 0:
        return "none"
    return "all" if n == len(sensors) else "some"


def toggle_bulk(pinned: list[str], sensors: list[Sensor], snapshot: Snapshot,
                max_pins: int = MAX_PINS) -> tuple[list[str], int]:
    """Pin the missing `sensors` (in the given order, no duplicates, up to `max_pins`), or unpin all of
    them when every one is already pinned (also removing covering `auto:*` entries).
    Returns (new pins, number of sensors not added because of the limit)."""
    if bulk_state(pinned, sensors, snapshot) == "all":
        drop: set[str] = set()
        for s in sensors:
            drop.add(s.key)
            drop.update(_covering_autos(pinned, s, snapshot))
        return [p for p in pinned if p not in drop], 0
    out = list(pinned)
    skipped = 0
    seen: set[str] = set()
    for s in sensors:
        if s.key in seen:
            continue
        seen.add(s.key)
        if is_pinned(out, s, snapshot):
            continue
        if len(out) >= max_pins:
            skipped += 1
        else:
            out.append(s.key)
    return out, skipped
