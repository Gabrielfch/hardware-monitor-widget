import dataclasses

import pytest
from PySide6.QtCore import QObject, Signal

from hardware_monitor_widget import app as app_mod
from hardware_monitor_widget import monitor as mon
from hardware_monitor_widget import power, sensorhost as sh
from hardware_monitor_widget.config import Config
from hardware_monitor_widget.models import Sensor, Snapshot
from hardware_monitor_widget.monitor import HardwareMonitor
from hardware_monitor_widget.widget import TaskbarWidget
from tools.synthetic import make_snapshot


def full():
    return make_snapshot()


# ---- group selection ---------------------------------------------------------------------------
def test_ui_open_or_unknown_catalog_means_everything():
    assert power.needed_groups(full(), ["auto:cpu_temp"], ui_open=True) is None
    assert power.needed_groups(None, ["auto:cpu_temp"], ui_open=False) is None
    assert power.needed_groups(Snapshot(), [], ui_open=False) is None


def test_only_pinned_kinds_plus_cpu_for_the_tray():
    assert power.needed_groups(full(), ["auto:gpu_temp"], False) == {"cpu", "gpu"}
    assert power.needed_groups(full(), ["auto:ram_load"], False) == {"cpu", "ram"}
    assert power.needed_groups(full(), [], False) == {"cpu"}


def test_pinned_concrete_key_and_missing_key():
    snap = full()
    disk = next(s for s in snap.sensors if s.hw_kind == "storage")
    assert power.needed_groups(snap, [disk.key, "hw/gone"], False) == {"cpu", "storage"}


def test_alerts_and_history_widen_the_selection():
    snap = full()
    alerts = power.needed_groups(snap, [], False, alerts=True)
    hist = power.needed_groups(snap, [], False, history=True)
    assert {"cpu"} <= alerts <= hist


def test_unselectable_kind_keeps_everything():
    s = Sensor("k/p", "native", "PSU", "hw//psu", "other", "power", "Out", 10.0)
    cpu = Sensor("k/c", "native", "CPU", "hw//c", "cpu", "load", "CPU Total", 1.0)
    assert power.needed_groups(Snapshot([cpu, s]), ["k/p"], False) is None


# ---- adaptive interval -------------------------------------------------------------------------
@pytest.mark.parametrize("ui,bar,track,want", [
    (True, False, False, 1000), (False, True, False, 1000), (False, False, True, 1000),
    (False, False, False, 5000)])
def test_effective_interval(ui, bar, track, want):
    assert power.effective_interval(1000, ui, bar, track) == want


def test_idle_never_speeds_up_a_slower_setting():
    assert power.effective_interval(8000, False, False, False) == 8000


# ---- protocol ----------------------------------------------------------------------------------
def test_groups_arg_wire_format():
    assert sh.groups_arg(None) == "all"
    assert sh.groups_arg({"gpu", "cpu", "bogus"}) == "cpu,gpu"
    assert sh.groups_arg(set()) == "cpu"


def test_snapshot_message_groups_default_and_echo():
    base = {"type": "snapshot", "t": 1000, "hardware": [], "sensors": []}
    assert sh.snapshot_from_message(base, None).groups == "all"
    assert sh.snapshot_from_message({**base, "groups": "cpu,gpu"}, None).groups == "cpu,gpu"


def test_host_process_sends_groups_and_spawns_with_them(monkeypatch, tmp_path):
    p = sh.SensorHostProcess(lambda m: None, 1000, path=tmp_path / "SensorHost.exe")
    sent = []
    monkeypatch.setattr(p, "_send", lambda c, proc=None: sent.append(c) or True)
    p.set_groups({"cpu", "ram"})
    assert sent == ["hardware cpu,ram"]
    captured = {}
    monkeypatch.setattr(sh.subprocess, "Popen", lambda args, **kw: captured.setdefault("args", args))
    p._spawn()
    assert captured["args"][-2:] == ["--hardware", "cpu,ram"]
    captured.clear()
    p.set_groups(None)
    p._spawn()
    assert "--hardware" not in captured["args"]


class FakeHost:
    def __init__(self, cb, interval):
        self.calls = []

    def start(self): self.calls.append("start")
    def stop(self): pass
    def refresh(self): pass
    def set_interval(self, ms): self.calls.append(("interval", ms))
    def set_groups(self, g): self.calls.append(("groups", g))


def _monitor():
    hosts = []

    def factory(cb, ms):
        hosts.append(FakeHost(cb, ms))
        return hosts[-1]
    return HardwareMonitor(1000, host_factory=factory), hosts


def test_monitor_forwards_groups_once_and_gives_startup_grace(qapp):
    m, hosts = _monitor()
    m.set_groups({"cpu"})            # before start: applied when the host is created
    m.start()
    assert ("groups", frozenset({"cpu"})) in hosts[0].calls
    m._got_snapshot = True
    m.set_groups({"cpu"})            # unchanged: nothing sent
    assert hosts[0].calls.count(("groups", frozenset({"cpu"}))) == 1
    m.set_groups(None)
    assert hosts[0].calls[-1] == ("groups", None) and m.stale_after_s() == mon.STARTUP_GRACE_S
    m.stop()


def test_monitor_set_interval_is_idempotent(qapp):
    m, hosts = _monitor()
    m.start()
    m.set_interval(1000)
    m.set_interval(5000)
    m.set_interval(5000)
    assert [c for c in hosts[0].calls if isinstance(c, tuple) and c[0] == "interval"] == [("interval", 5000)]
    m.stop()


# ---- controller wiring -------------------------------------------------------------------------
class FakeMonitor(QObject):
    snapshot_ready = Signal(object)

    def __init__(self):
        super().__init__()
        self.groups, self.interval, self.refreshes = None, 1000, 0

    def set_groups(self, g):
        self.groups = None if g is None else frozenset(g)

    def set_interval(self, ms):
        self.interval = ms

    def refresh(self):
        self.refreshes += 1


@pytest.fixture
def ctl(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = Config(tmp_path / "config.json")
    fm = FakeMonitor()
    c = app_mod.Controller(qapp, cfg, fm)
    c.fm = fm
    yield c
    c.panel.hide()
    c.widget.close()


def test_controller_narrows_groups_and_slows_when_nothing_watches(ctl, monkeypatch):
    ctl.cfg.set("alerts_enabled", False)
    ctl.cfg.set("history_enabled", False)
    ctl.cfg.set("pinned", ["auto:cpu_load"])
    ctl.on_snapshot(full())
    monkeypatch.setattr(ctl.widget, "isVisible", lambda: False)
    ctl.sync_power()
    assert ctl.fm.groups == {"cpu"} and ctl.fm.interval == power.IDLE_INTERVAL_MS
    monkeypatch.setattr(ctl.widget, "isVisible", lambda: True)
    ctl.sync_power()
    assert ctl.fm.interval == 1000 and ctl.fm.groups == {"cpu"}


def test_controller_opening_ui_restores_everything_and_refreshes(ctl, monkeypatch):
    ctl.cfg.set("pinned", ["auto:cpu_load"])
    ctl.cfg.set("alerts_enabled", False)
    ctl.cfg.set("history_enabled", False)
    ctl.on_snapshot(full())
    ctl.sync_power()
    assert ctl.fm.groups == {"cpu"}
    monkeypatch.setattr(ctl.panel, "isVisible", lambda: True)
    ctl.sync_power()
    assert ctl.fm.groups is None and ctl.fm.refreshes == 1
    ctl.sync_power()
    assert ctl.fm.refreshes == 1


def test_controller_ignores_reduced_snapshots_as_catalog(ctl):
    ctl.on_snapshot(full())
    ctl.on_snapshot(dataclasses.replace(make_snapshot(), groups="cpu"))
    assert ctl._full.groups == "all"


# ---- widget: no redundant work -----------------------------------------------------------------
def test_widget_skips_rebuild_when_nothing_changed(qapp, monkeypatch):
    w = TaskbarWidget(["auto:cpu_load"])
    snap = full()
    w.set_snapshot(snap)
    calls = []
    orig = w._relayout
    monkeypatch.setattr(w, "_relayout", lambda: calls.append(1) or orig())
    w.set_snapshot(dataclasses.replace(snap, taken_at=99.0))
    assert calls == []
    changed = dataclasses.replace(
        snap, sensors=[dataclasses.replace(s, value=(s.value or 0) + 7) for s in snap.sensors])
    w.set_snapshot(changed)
    assert calls
    w.close()
