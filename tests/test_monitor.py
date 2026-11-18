from hardware_monitor_widget import monitor as mon
from hardware_monitor_widget import sensorhost as sh
from hardware_monitor_widget.monitor import HardwareMonitor

from test_sensorhost import HELLO_ADMIN, MSG


class FakeHost:
    def __init__(self, cb, interval):
        self.cb, self.interval = cb, interval
        self.calls = []

    def start(self): self.calls.append("start")
    def stop(self): self.calls.append("stop")
    def refresh(self): self.calls.append("refresh")
    def restart(self): self.calls.append("restart")
    def set_interval(self, ms): self.calls.append(("interval", ms))


def make():
    hosts = []

    def factory(cb, ms):
        h = FakeHost(cb, ms)
        hosts.append(h)
        return h
    m = HardwareMonitor(1000, host_factory=factory)
    got = []
    m.snapshot_ready.connect(got.append)
    return m, hosts, got


def test_snapshot_flow():
    m, hosts, got = make()
    m.start()
    h = hosts[0]
    h.cb(HELLO_ADMIN)
    h.cb(MSG)
    assert len(got) == 1
    assert got[0].statuses["native"].reason == "ok" and got[0].sensors


def test_needs_admin_status():
    m, hosts, got = make()
    m.start()
    hosts[0].cb({**HELLO_ADMIN, "admin": False})
    hosts[0].cb(MSG)
    assert got[0].statuses["native"].reason == "needs_admin"


def test_error_keeps_last_sensors():
    m, hosts, got = make()
    m.start()
    hosts[0].cb(HELLO_ADMIN)
    hosts[0].cb(MSG)
    hosts[0].cb({"type": "error", "message": "boom"})
    st = got[-1].statuses["native"]
    assert st.reason == "error" and st.detail == "boom" and got[-1].sensors


def test_controls_and_interval_clamp():
    m, hosts, got = make()
    m.start()
    m.start()  # idempotent
    m.refresh()
    m.set_interval(50)
    m.stop()
    assert len(hosts) == 1
    assert hosts[0].calls == ["start", "refresh", ("interval", 250), "stop"]


def test_missing_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(sh, "host_path", lambda: tmp_path / "nope.exe")
    m = HardwareMonitor(1000)
    got = []
    m.snapshot_ready.connect(got.append)
    m.start()
    st = got[0].statuses["native"]
    assert st.reason == "error" and st.detail == mon.MISSING_DETAIL


def test_watchdog_publishes_stale_and_restarts():
    import time
    m, hosts, got = make()
    m.start()
    hosts[0].cb(HELLO_ADMIN)
    hosts[0].cb(MSG)
    n = len(got)
    assert m.check_watchdog(time.monotonic() + 1.0) is False        # still fresh
    assert m.check_watchdog(time.monotonic() + 60.0) is True
    st = got[-1].statuses["native"]
    assert len(got) == n + 1 and st.reason == "stale" and not st.available and got[-1].sensors
    assert st.detail == mon.STALE_DETAIL
    assert "restart" in hosts[0].calls
    m.stop()


def test_watchdog_threshold_scales_with_interval():
    m, hosts, got = make()
    m.start()
    hosts[0].cb(HELLO_ADMIN)
    hosts[0].cb(MSG)
    assert m.stale_after_s() == 5.0
    m.set_interval(10000)
    assert m.stale_after_s() == 50.0


def test_watchdog_waits_for_slow_first_snapshot():
    """Regression: elevated LHM init takes > 5 s; the watchdog must not kill it before the
    first snapshot (it caused an endless restart loop in the installed app)."""
    import time
    m, hosts, got = make()
    m.start()
    hosts[0].cb(HELLO_ADMIN)
    assert m.stale_after_s() == mon.STARTUP_GRACE_S
    assert m.check_watchdog(time.monotonic() + 30.0) is False
    assert "restart" not in hosts[0].calls
    assert m.check_watchdog(time.monotonic() + mon.STARTUP_GRACE_S + 1) is True
    hosts[0].cb(HELLO_ADMIN)          # restarted host: grace applies again
    assert m.stale_after_s() == mon.STARTUP_GRACE_S
    m.stop()


def test_non_fatal_error_does_not_mark_stale():
    m, hosts, got = make()
    m.start()
    hosts[0].cb(HELLO_ADMIN)
    hosts[0].cb(MSG)
    n = len(got)
    hosts[0].cb({"type": "error", "message": "hardware update failed (/nvme/0): IOException", "fatal": False})
    assert len(got) == n                 # nothing published
    hosts[0].cb(MSG)
    assert got[-1].statuses["native"].reason == "ok"


def test_watchdog_skips_when_host_reported_dead():
    import time
    m, hosts, got = make()
    m.start()
    hosts[0].cb({"type": "error", "message": "host exited (code 1)"})
    assert m.check_watchdog(time.monotonic() + 600.0) is False
    assert got[-1].statuses["native"].reason == "error"
    m.stop()
