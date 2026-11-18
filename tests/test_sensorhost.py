import subprocess
import sys
import time

from hardware_monitor_widget import sensorhost as sh

HELLO_ADMIN = {"type": "hello", "version": "1", "admin": True, "pawnio": True, "pawnio_version": "2.2.0.0"}

MSG = {
    "type": "snapshot", "t": 1700000000000,
    "hardware": [
        {"id": "/amdcpu/0", "name": "AMD Ryzen 5 3600", "type": "Cpu", "parent": None},
        {"id": "/gpu-amd/0", "name": "AMD Radeon RX 5700 XT", "type": "GpuAmd", "parent": None},
        {"id": "/motherboard", "name": "ASUS Board", "type": "Motherboard", "parent": None},
        {"id": "/lpc/nct6798d", "name": "Nuvoton NCT6798D", "type": "SuperIO", "parent": "/motherboard"},
        {"id": "/ram", "name": "Total Memory", "type": "Memory", "parent": None},
    ],
    "sensors": [
        {"id": "/amdcpu/0/temperature/2", "hw": "/amdcpu/0", "name": "Core (Tctl/Tdie)", "type": "Temperature",
         "index": 2, "value": 54.5, "min": 40.0, "max": 70.0},
        {"id": "/gpu-amd/0/temperature/0", "hw": "/gpu-amd/0", "name": "GPU Core", "type": "Temperature",
         "index": 0, "value": 50.0, "min": None, "max": None},
        {"id": "/lpc/nct6798d/fan/1", "hw": "/lpc/nct6798d", "name": "Fan #1", "type": "Fan",
         "index": 1, "value": None, "min": None, "max": None},
        {"id": "/ram/load/0", "hw": "/ram", "name": "Memory", "type": "Load", "index": 0, "value": 40.0},
        {"id": "/amdcpu/0/current/0", "hw": "/amdcpu/0", "name": "X", "type": "Current", "index": 0, "value": 1},
    ],
}


def test_parse_line():
    assert sh.parse_line('{"type":"hello","admin":true}') == {"type": "hello", "admin": True}
    assert sh.parse_line("") is None
    assert sh.parse_line("not json") is None
    assert sh.parse_line("[1,2]") is None
    assert sh.parse_line('{"no_type":1}') is None


def test_snapshot_mapping():
    snap = sh.snapshot_from_message(MSG, HELLO_ADMIN)
    by = snap.by_key()
    cpu = by["hw//amdcpu/0/temperature/2"]
    assert (cpu.source, cpu.hw_kind, cpu.kind, cpu.hw_id, cpu.limit) == (
        "native", "cpu", "temperature", "hw//amdcpu/0", 95.0)
    assert cpu.hardware == "AMD Ryzen 5 3600" and cpu.min == 40.0 and cpu.max == 70.0
    gpu = by["hw//gpu-amd/0/temperature/0"]
    assert gpu.hw_kind == "gpu" and gpu.limit == 110.0 and gpu.min is None
    fan = by["hw//lpc/nct6798d/fan/1"]
    assert fan.hw_kind == "mainboard" and fan.hardware == "ASUS Board"
    assert fan.value is None and fan.limit is None
    ram = by["hw//ram/load/0"]
    assert ram.hw_kind == "ram" and ram.limit is None
    assert by["hw//amdcpu/0/current/0"].kind == "current"
    assert snap.taken_at == 1700000000.0


def test_status_variants():
    assert sh.snapshot_from_message(MSG, HELLO_ADMIN).statuses["native"].reason == "ok"
    st = sh.snapshot_from_message(MSG, {**HELLO_ADMIN, "admin": False}).statuses["native"]
    assert st.reason == "needs_admin" and st.available
    st = sh.snapshot_from_message(MSG, {**HELLO_ADMIN, "pawnio": False}).statuses["native"]
    assert st.reason == "driver_missing" and "PawnIO" in st.detail
    empty = sh.snapshot_from_message({"type": "snapshot", "t": 1}, HELLO_ADMIN)
    assert empty.sensors == [] and empty.statuses["native"].reason == "error"


def test_hw_kind_for():
    for t, k in [("GpuNvidia", "gpu"), ("GpuIntel", "gpu"), ("Storage", "storage"), ("Network", "network"),
                 ("Cooler", "mainboard"), ("EmbeddedController", "mainboard"), ("Psu", "other")]:
        assert sh.hw_kind_for(t) == k


def test_host_path():
    p = sh.host_path()
    assert p.name == "SensorHost.exe" and p.parent.name == "sensorhost"


def test_process_restarts_and_stops(monkeypatch):
    monkeypatch.setattr(sh, "BACKOFF_STEPS", (0.05,))
    msgs = []
    proc = sh.SensorHostProcess(msgs.append, 1000, path=sys.executable)
    code = "print('{\"type\":\"hello\"}', flush=True)"

    def spawn():
        return subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True, encoding="utf-8")

    monkeypatch.setattr(proc, "_spawn", spawn)
    proc.start()
    deadline = time.time() + 5
    while time.time() < deadline and sum(m["type"] == "hello" for m in msgs) < 2:
        time.sleep(0.05)
    proc.stop()
    assert sum(m["type"] == "hello" for m in msgs) >= 2      # restarted after exit
    assert any(m["type"] == "error" for m in msgs)           # exit reported


# ---- lifecycle / robustness ----------------------------------------------------------------------
HELLO_THEN_WAIT = "import sys; print('{\"type\":\"hello\"}', flush=True); sys.stdin.readline()"
IGNORE_QUIT = "import time; print('{\"type\":\"hello\"}', flush=True); time.sleep(60)"


class Spawner:
    """Replaces SensorHostProcess._spawn with a python child and tracks every process it creates."""

    def __init__(self, code, delay=0.0):
        self.code, self.delay, self.procs = code, delay, []

    def __call__(self):
        time.sleep(self.delay)
        p = subprocess.Popen([sys.executable, "-c", self.code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self.procs.append(p)
        return p

    def alive(self):
        return [p for p in self.procs if p.poll() is None]

    def cleanup(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def make_host(monkeypatch, code, delay=0.0, backoff=(0.05,)):
    monkeypatch.setattr(sh, "BACKOFF_STEPS", backoff)
    msgs = []
    host = sh.SensorHostProcess(msgs.append, 1000, path=sys.executable)
    sp = Spawner(code, delay)
    monkeypatch.setattr(host, "_spawn", sp)
    return host, sp, msgs


def test_stop_during_spawn_leaves_no_children(monkeypatch):
    host, sp, msgs = make_host(monkeypatch, HELLO_THEN_WAIT, delay=0.5)
    try:
        host.start()
        time.sleep(0.1)          # reader is inside _spawn()
        t0 = time.time()
        host.stop()
        assert time.time() - t0 < 4.0
        assert sp.procs and wait_for(lambda: not sp.alive(), 3.0)
    finally:
        sp.cleanup()


def test_stop_during_backoff_leaves_no_children(monkeypatch):
    host, sp, msgs = make_host(monkeypatch, "pass", backoff=(30.0,))
    try:
        host.start()
        assert wait_for(lambda: any(m["type"] == "error" for m in msgs))
        t0 = time.time()
        host.stop()
        assert time.time() - t0 < 2.0      # does not wait for the 30 s backoff
        assert not sp.alive()
        assert host._thread is None
    finally:
        sp.cleanup()


def test_host_ignoring_quit_is_killed(monkeypatch):
    host, sp, msgs = make_host(monkeypatch, IGNORE_QUIT)
    try:
        host.start()
        assert wait_for(lambda: any(m["type"] == "hello" for m in msgs))
        t0 = time.time()
        host.stop()
        assert time.time() - t0 < 4.0
        assert not sp.alive()
    finally:
        sp.cleanup()


def test_oversized_and_nested_lines_do_not_kill_reader(monkeypatch):
    code = (
        "import sys\n"
        "sys.stdout.write('x' * 2_500_000 + '\\n')\n"
        "sys.stdout.write('{\"a\":' + '[' * 200000 + ']' * 200000 + '}\\n')\n"
        "sys.stdout.write('{\"type\":\"hello\"}\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stdin.readline()\n"
    )
    host, sp, msgs = make_host(monkeypatch, code)
    try:
        host.start()
        assert wait_for(lambda: any(m["type"] == "hello" for m in msgs), 10.0)
        assert all(m["type"] != "error" for m in msgs)
    finally:
        host.stop()
        sp.cleanup()


def test_refresh_wakes_backoff(monkeypatch):
    host, sp, msgs = make_host(monkeypatch, "print('{\"type\":\"hello\"}', flush=True)", backoff=(30.0,))
    try:
        host.start()
        assert wait_for(lambda: any(m["type"] == "error" for m in msgs))
        n = len(sp.procs)
        host.refresh()
        assert wait_for(lambda: len(sp.procs) > n, 3.0)
    finally:
        host.stop()
        sp.cleanup()


def test_exit_message_includes_last_host_error(monkeypatch):
    code = "print('{\"type\":\"error\",\"message\":\"fatal: Boom\"}', flush=True)"
    host, sp, msgs = make_host(monkeypatch, code, backoff=(30.0,))
    try:
        host.start()
        assert wait_for(lambda: any("host exited" in m.get("message", "") for m in msgs))
        exited = [m for m in msgs if "host exited" in m.get("message", "")][0]
        assert "fatal: Boom" in exited["message"]
    finally:
        host.stop()
        sp.cleanup()


def test_restart_respawns_without_error(monkeypatch):
    host, sp, msgs = make_host(monkeypatch, HELLO_THEN_WAIT, backoff=(30.0,))
    try:
        host.start()
        assert wait_for(lambda: len(sp.procs) == 1 and any(m["type"] == "hello" for m in msgs))
        host.restart()
        assert wait_for(lambda: len(sp.procs) == 2, 5.0)
        assert all(m["type"] != "error" for m in msgs)
    finally:
        host.stop()
        sp.cleanup()


def test_reader_crash_emits_error(monkeypatch):
    host, sp, msgs = make_host(monkeypatch, "pass")

    def crash():
        raise RuntimeError("x")

    monkeypatch.setattr(host, "_loop", crash)
    host.start()
    assert wait_for(lambda: any(m["type"] == "error" for m in msgs))
    host.stop()


def test_clean_env_drops_dotnet_vars():
    src = {"PATH": "p", "DOTNET_STARTUP_HOOKS": "x", "dotnet_root": "y", "COMPlus_Foo": "1",
           "CORECLR_ENABLE_PROFILING": "1", "COREHOST_TRACE": "1", "OTHER": "o"}
    assert sh.clean_env(src) == {"PATH": "p", "OTHER": "o"}


def test_snapshot_limits_and_sanitizing():
    nan, inf = float("nan"), float("inf")
    msg = {"type": "snapshot", "t": 1,
           "hardware": [{"id": f"/h/{i}", "name": "N" * 500, "type": "Cpu"} for i in range(300)],
           "sensors": [{"id": f"/h/0/s/{i}", "hw": "/h/0", "name": "S" * 500, "type": "Load",
                        "value": nan if i == 0 else (inf if i == 1 else 1.0), "min": -inf}
                       for i in range(5000)]}
    snap = sh.snapshot_from_message(msg, HELLO_ADMIN)
    assert len(snap.sensors) == sh.MAX_SENSORS
    assert snap.sensors[0].value is None and snap.sensors[1].value is None and snap.sensors[2].value == 1.0
    assert snap.sensors[0].min is None
    assert len(snap.sensors[0].name) == 200 and len(snap.sensors[0].hardware) == 200
    # hardware beyond the cap is unknown -> grouped as "other"
    msg["sensors"] = [{"id": "/x", "hw": "/h/299", "name": "a", "type": "Load", "value": 1}]
    assert sh.snapshot_from_message(msg, HELLO_ADMIN).sensors[0].hw_kind == "other"


def test_parse_line_deep_nesting():
    assert sh.parse_line('{"type":"a","x":' + "[" * 100000 + "]" * 100000 + "}") is None


def test_status_details_spanish():
    st = sh.status_for({"admin": False, "pawnio": True}, True)
    assert st.reason == "needs_admin" and st.available
    assert st.detail == "Sin permisos de administrador: faltan temperaturas de CPU y placa base"
    st = sh.status_for({"admin": True, "pawnio": False}, True)
    assert st.detail == "PawnIO no está instalado: faltan temperaturas de CPU"
