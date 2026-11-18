import dataclasses
import json
import time
from pathlib import Path

import pytest

from hardware_monitor_widget import curation as cur
from hardware_monitor_widget.curation_rules import GROUP_ORDER, RULES
from hardware_monitor_widget.models import HW_KINDS, Sensor, Snapshot
from hardware_monitor_widget.sensorhost import snapshot_from_message

DATA = Path(__file__).parent / "data" / "sensors-admin.jsonl"


@pytest.fixture(scope="module")
def real():
    msgs = [json.loads(line) for line in DATA.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    hello = next(m for m in msgs if m.get("type") == "hello")
    snap = next(m for m in msgs if m.get("type") == "snapshot")
    return snapshot_from_message(snap, hello)


def S(hw_id, hw_kind, kind, name, value=1.0, mx=None, key=None, hardware="HW"):
    return Sensor(key or f"{hw_id}/{kind}/{name}", "native", hardware, hw_id, hw_kind, kind, name, value, None, mx)


def names(sec):
    return [(s.kind, s.name) for s in sec.sensors]


def test_hw_class():
    assert cur.hw_class("hw//ram", "ram") == "ram"
    assert cur.hw_class("hw//vram", "ram") == "vram"
    assert cur.hw_class("hw//memory/dimm/2", "ram") == "dimm"
    assert cur.hw_class("hw//lpc/nct6798d/0", "mainboard") == "board"
    assert cur.hw_class("hw//x", "weird") == "other"


def test_c1_real_summary(real):
    secs = cur.summary_sections(real, cur.Prefs())
    assert [s.hw_class for s in secs] == ["cpu", "gpu", "ram", "vram", "board", "storage", "storage", "storage"]
    assert [s.hw_id for s in secs][-3:] == ["hw//nvme/2", "hw//hdd/1", "hw//ssd/0"]
    by = {s.hw_class if s.hw_class != "storage" else s.hw_id: s for s in secs}
    assert sorted(names(by["cpu"])) == sorted([("temperature", "Core (Tctl/Tdie)"), ("load", "CPU Total"),
                                               ("clock", "Cores (Average)"), ("power", "Package")])
    assert sorted(names(by["gpu"])) == sorted([
        ("temperature", "GPU Core"), ("temperature", "GPU Hot Spot"), ("load", "GPU Core"),
        ("clock", "GPU Core"), ("power", "GPU Package"), ("fan", "GPU Fan"), ("smalldata", "GPU Memory Used")])
    mem = sorted([("load", "Memory"), ("data", "Memory Used"), ("data", "Memory Available")])
    assert sorted(names(by["ram"])) == mem and sorted(names(by["vram"])) == mem
    assert sorted(names(by["board"])) == [("fan", "Fan #2"), ("fan", "Fan #7")]
    for sec in secs[-3:]:
        temp = "Composite Temperature" if "nvme" in sec.hw_id else "Temperature"
        assert sorted(names(sec)) == sorted([("temperature", temp), ("load", "Used Space"),
                                             ("load", "Read Activity"), ("load", "Write Activity")])
    assert sum(len(s.sensors) for s in secs) == 4 + 7 + 3 + 3 + 2 + 12
    assert not any(s.hw_class == "dimm" for s in secs)


def test_total_activity_is_detail_and_neutral(real):
    total = next(s for s in real.sensors if s.name == "Total Activity")
    c = cur.classify(total)
    assert c.level == "detail" and c.neutral_band and c.group == "activity"
    assert cur.classify(next(s for s in real.sensors if s.name == "Read Activity")).level == "summary"


def test_c2_excluded_from_summary(real):
    for sec in cur.summary_sections(real, cur.Prefs()):
        for s in sec.sensors:
            n = s.name
            assert not n.startswith(("CPU Core #", "D3D ", "Warning", "Critical", "Power On", "tCK", "Total Activity"))
            assert s.kind not in ("voltage", "control", "timing", "factor", "current")
            assert n not in ("Bus Speed", "Fabric", "Uncore") and not (s.kind == "clock" and n == "Memory")


def test_c3_idle_fans(real):
    fan1 = next(s for s in real.sensors if s.kind == "fan" and s.name == "Fan #1")
    assert cur.is_idle(fan1) and not cur.in_summary(fan1, cur.Prefs())
    live = dataclasses.replace(fan1, value=900.0, max=900.0)
    assert cur.in_summary(live, cur.Prefs())
    only_max = dataclasses.replace(fan1, value=0.0, max=1200.0)
    assert cur.in_summary(only_max, cur.Prefs())
    assert cur.in_summary(fan1, cur.Prefs(overrides={fan1.key: True}))
    tree = cur.detail_tree(real)
    board = next(h for h in tree.hardware if h.hw_class == "board")
    assert any(s.key == fan1.key for s in board.loose)


def test_c4_intel_nvidia():
    intel = Snapshot([S("hw//intelcpu/0", "cpu", "temperature", "CPU Package"),
                      S("hw//intelcpu/0", "cpu", "power", "CPU Package"),
                      S("hw//intelcpu/0", "cpu", "temperature", "Core Max"),
                      S("hw//intelcpu/0", "cpu", "clock", "Cores (Average)"),
                      S("hw//intelcpu/0", "cpu", "load", "CPU Total"),
                      S("hw//intelcpu/0", "cpu", "temperature", "Core #1"),
                      S("hw//intelcpu/0", "cpu", "load", "CPU Core #1")])
    sec = cur.summary_sections(intel, cur.Prefs())[0]
    assert len(sec.sensors) == 5 and ("temperature", "Core #1") not in names(sec)
    nv = Snapshot([S("hw//gpu-nvidia/0", "gpu", "temperature", "GPU Core"),
                   S("hw//gpu-nvidia/0", "gpu", "temperature", "GPU Hot Spot"),
                   S("hw//gpu-nvidia/0", "gpu", "power", "GPU Power"),
                   S("hw//gpu-nvidia/0", "gpu", "smalldata", "GPU Memory Used"),
                   S("hw//gpu-nvidia/0", "gpu", "fan", "GPU Fan", 40.0),
                   S("hw//gpu-nvidia/0", "gpu", "smalldata", "GPU Memory Total")])
    sec = cur.summary_sections(nv, cur.Prefs())[0]
    assert len(sec.sensors) == 5 and ("smalldata", "GPU Memory Total") not in names(sec)


def test_c5_classify_total_and_deterministic(real):
    ids = [r.id for r in RULES]
    assert len(set(ids)) == len(ids) and ids[-1] == "any.fallback"
    odd = [S("", "", "", ""), S("hw//?", "zzz", "unknown_kind", "ñ\n(["), S("hw//ram", "ram", "Load", "Memory")]
    for s in [*odd, *real.sensors]:
        a, b = cur.classify(s), cur.classify(s)
        assert a == b and a.rule_id in ids
    assert cur.classify(odd[1]).rule_id == "any.fallback"
    assert cur.classify(odd[2]).rule_id == "ram.main"   # kind compared in lower case


def test_c6_categories_and_overrides(real):
    prefs = cur.Prefs(categories=frozenset(c for c in cur.SUMMARY_CATEGORIES if c != "fans"))
    secs = cur.summary_sections(real, prefs)
    assert not any(s.kind == "fan" for sec in secs for s in sec.sensors)
    gpu_fan = next(s for s in real.sensors if s.kind == "fan" and s.name == "GPU Fan")
    prefs = dataclasses.replace(prefs, overrides={gpu_fan.key: True})
    assert any(s.key == gpu_fan.key for sec in cur.summary_sections(real, prefs) for s in sec.sensors)
    cpu_total = next(s for s in real.sensors if s.name == "CPU Total")
    prefs = cur.Prefs(overrides={cpu_total.key: False})
    assert not any(s.key == cpu_total.key for sec in cur.summary_sections(real, prefs) for s in sec.sensors)
    dimm = next(s for s in real.sensors if s.hw_id.startswith("hw//memory/dimm"))
    secs = cur.summary_sections(real, cur.Prefs(overrides={dimm.key: True}))
    assert any(sec.hw_class == "dimm" for sec in secs)
    hidden = cur.summary_sections(real, cur.Prefs(hidden_hardware=frozenset({"hw//ram"})))
    assert "hw//ram" not in [s.hw_id for s in hidden]


def test_toggle_override(real):
    prefs = cur.Prefs()
    cpu_total = next(s for s in real.sensors if s.name == "CPU Total")
    core = next(s for s in real.sensors if s.name == "Core #1" and s.kind == "clock")
    assert cur.toggle_override({}, cpu_total, True, prefs) == {}
    assert cur.toggle_override({}, cpu_total, False, prefs) == {cpu_total.key: False}
    assert cur.toggle_override({cpu_total.key: False}, cpu_total, True, prefs) == {}
    assert cur.toggle_override({}, core, True, prefs) == {core.key: True}


def test_c7_order_and_titles(real):
    secs = cur.summary_sections(real, cur.Prefs())
    ranks = [HW_KINDS.index(s.hw_kind) for s in secs]
    assert ranks == sorted(ranks)
    assert cur.section_title_key("ram") == "hw_ram" and cur.section_title_key("vram") == "hw_vram"
    assert cur.section_title_key("gpu") is None
    cats = [cur.SUMMARY_CATEGORIES.index(cur.classify(s).summary_category) for s in secs[0].sensors]
    assert cats == sorted(cats)
    two = Snapshot([S("hw//gpu-amd/1", "gpu", "temperature", "GPU Core", key="b"),
                    S("hw//gpu-amd/0", "gpu", "temperature", "GPU Core", key="a")])
    assert [s.hw_id for s in cur.summary_sections(two, cur.Prefs())] == ["hw//gpu-amd/1", "hw//gpu-amd/0"]


def test_g1_g3_detail_tree(real):
    tree = cur.detail_tree(real)
    assert [h.hw_class for h in tree.hardware][:5] == ["cpu", "gpu", "ram", "vram", "board"]
    cpu = tree.hardware[0]
    cores = next(g for g in cpu.groups if g.id == "cores")
    assert cores.count == 6
    assert any(s.kind == "load" and s.name == "CPU Core #12" for s in cores.sensors)
    order = [GROUP_ORDER.index(g.id) for g in cpu.groups]
    assert order == sorted(order) and len(set(order)) == len(order)
    gpu = tree.hardware[1]
    d3d = next(g for g in gpu.groups if g.id == "d3d")
    assert all(s.name.startswith("D3D ") for s in d3d.sensors) and sum(s.name == "D3D Copy" for s in d3d.sensors) == 2
    board = next(h for h in tree.hardware if h.hw_class == "board")
    assert {g.id for g in board.groups} >= {"controls", "electrical"}
    ctl = next(g for g in board.groups if g.id == "controls")
    assert all(s.kind == "control" for s in ctl.sensors) and len(ctl.sensors) == 7
    nvme = next(h for h in tree.hardware if "nvme" in h.hw_id)
    assert "thresholds" in {g.id for g in nvme.groups}
    assert len(tree.modules) == 2 and all(h.hw_class == "dimm" for h in tree.modules)
    assert "timings" in {g.id for g in tree.modules[0].groups}
    total = sum(len(h.loose) + sum(len(g.sensors) for g in h.groups) for h in [*tree.hardware, *tree.modules])
    assert total == len(real.sensors)


def test_x6_performance():
    sensors = []
    for i in range(4000):
        hw = i % 40
        sensors.append(S(f"hw//hdd/{hw}", "storage", ["temperature", "load", "data", "fan"][i % 4],
                         f"Sensor {i % 25}", float(i), key=f"k{i}", hardware=f"Disk {hw}"))
    snap = Snapshot(sensors)
    t0 = time.perf_counter()
    cur.summary_sections(snap, cur.Prefs())
    cur.detail_tree(snap)
    assert time.perf_counter() - t0 < 0.5   # design budget 100 ms; margin for slow machines
