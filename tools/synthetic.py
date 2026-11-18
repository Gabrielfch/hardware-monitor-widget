"""Synthetic snapshots for tests and screenshots (no hardware access)."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from hardware_monitor_widget.models import Sensor, Snapshot, SourceStatus


def _s(key, hw, hw_id, hw_kind, kind, name, value, mn=None, mx=None, limit=None):
    return Sensor(key=key, source="native", hardware=hw, hw_id=hw_id, hw_kind=hw_kind, kind=kind,
                  name=name, value=value, min=mn, max=mx, limit=limit)


def make_sensors(cpu_temp: float = 62.0, gpu_temp: float = 71.0, ram: float = 48.0) -> list[Sensor]:
    cpu, cid = "AMD Ryzen 7 5800X", "hw//amdcpu/0"
    gpu, gid = "AMD Radeon RX 6800", "hw//gpu-amd/0"
    ram_n, rid = "Generic Memory", "hw//ram/0"
    mb, mid = "ASUS PRIME B550-PLUS", "hw//lpc/nct6798d"
    sto, sid = "Samsung SSD 980 PRO 1TB", "hw//nvme/0"
    return [
        _s(f"{cid}/load/0", cpu, cid, "cpu", "load", "CPU Total", 23.0, 2.0, 100.0),
        _s(f"{cid}/temperature/2", cpu, cid, "cpu", "temperature", "Core (Tctl/Tdie)", cpu_temp, 38.0, 81.0, 95.0),
        _s(f"{cid}/temperature/3", cpu, cid, "cpu", "temperature", "CCD1 (Tdie)", 55.0, 36.0, 74.0),
        _s(f"{cid}/clock/1", cpu, cid, "cpu", "clock", "Core #1", 4350.0, 2200.0, 4750.0),
        _s(f"{cid}/power/0", cpu, cid, "cpu", "power", "Package", 65.2, 18.0, 112.4),
        _s(f"{cid}/voltage/0", cpu, cid, "cpu", "voltage", "Core (SVI2 TFN)", 1.25, 0.9, 1.4),
        _s(f"{gid}/temperature/0", gpu, gid, "gpu", "temperature", "GPU Core", gpu_temp, 38.0, 80.0),
        _s(f"{gid}/temperature/1", gpu, gid, "gpu", "temperature", "GPU Hot Spot", 88.0, 40.0, 92.0),
        _s(f"{gid}/load/0", gpu, gid, "gpu", "load", "GPU Core", 38.0, 0.0, 99.0),
        _s(f"{gid}/fan/0", gpu, gid, "gpu", "fan", "GPU Fan", 1200.0, 0.0, 1800.0),
        _s(f"{rid}/load/0", ram_n, rid, "ram", "load", "Memory", ram, 30.0, 70.0),
        _s(f"{rid}/data/0", ram_n, rid, "ram", "data", "Memory Used", 15.4, 9.0, 22.0),
        _s(f"{mid}/fan/1", mb, mid, "mainboard", "fan", "Fan #1", 920.0, 600.0, 1100.0),
        _s(f"{mid}/temperature/0", mb, mid, "mainboard", "temperature", "System", 33.0, 30.0, 40.0),
        _s(f"{sid}/temperature/0", sto, sid, "storage", "temperature", "Temperature", 41.0, 35.0, 52.0),
    ]


def make_snapshot(**kw) -> Snapshot:
    return Snapshot(sensors=make_sensors(**kw),
                    statuses={"native": SourceStatus("native", True, "ok")}, taken_at=1.0)


def make_error_snapshot(reason: str = "needs_admin", detail: str = "") -> Snapshot:
    return Snapshot(sensors=[], statuses={"native": SourceStatus("native", False, reason, detail)})


def make_degraded_snapshot(reason: str = "needs_admin", detail: str = "") -> Snapshot:
    """Sensors without CPU temperatures (no admin / no driver): available, but degraded."""
    sensors = [dataclasses.replace(s, value=None, min=None, max=None)
               if s.hw_kind == "cpu" and s.kind == "temperature" else s for s in make_sensors()]
    return Snapshot(sensors=sensors, statuses={"native": SourceStatus("native", True, reason, detail)},
                    taken_at=1.0)


REAL_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "data" / "sensors-admin.jsonl"


def load_real_snapshot() -> Snapshot:
    """Snapshot of a real machine (AMD CPU + GPU, 3 disks, 2 DIMMs) captured by the sensor host as admin."""
    from hardware_monitor_widget.sensorhost import snapshot_from_message
    msgs = [json.loads(line) for line in REAL_FIXTURE.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    hello = next(m for m in msgs if m.get("type") == "hello")
    snap = next(m for m in msgs if m.get("type") == "snapshot")
    return snapshot_from_message(snap, hello)
