"""Bridge to the native SensorHost helper (LibreHardwareMonitorLib): process management + pure parsing."""
from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .models import Sensor, Snapshot, SourceStatus

log = logging.getLogger(__name__)

SOURCE = "native"
HOST_EXE = "SensorHost.exe"
BACKOFF_STEPS = (1.0, 2.0, 5.0, 30.0)   # seconds; last value is the cap
STABLE_RUN_SECONDS = 10.0               # a run at least this long resets the backoff
MAX_LINE_CHARS = 1_048_576              # protocol lines longer than this are discarded
MAX_SENSORS = 4000
MAX_HARDWARE = 256
MAX_NAME_CHARS = 200
# environment variable prefixes that could inject code into the .NET runtime of the elevated host
_BLOCKED_ENV_PREFIXES = ("DOTNET_", "COMPLUS_", "CORECLR_", "COREHOST_")

# LibreHardwareMonitor HardwareType -> models.HW_KINDS
_HW_KIND = {
    "cpu": "cpu",
    "gpunvidia": "gpu",
    "gpuamd": "gpu",
    "gpuintel": "gpu",
    "memory": "ram",
    "motherboard": "mainboard",
    "superio": "mainboard",
    "embeddedcontroller": "mainboard",
    "cooler": "mainboard",
    "storage": "storage",
    "network": "network",
}

# Hardware groups the host understands in the ``hardware`` command (models.HW_KINDS minus "other").
HOST_GROUPS = ("cpu", "gpu", "ram", "mainboard", "storage", "network")


def groups_arg(groups) -> str:
    """Wire form of a group set for ``hardware <list|all>``: None / unknown content -> "all"."""
    if groups is None:
        return "all"
    names = sorted({g for g in groups if g in HOST_GROUPS})
    return ",".join(names) if names else "cpu"


# Default scale of the pill bar for temperatures.
_TEMP_LIMITS = {"cpu": 95.0, "gpu": 110.0}


def host_path() -> Path:
    """Location of SensorHost.exe (next to the package, or inside the PyInstaller onedir bundle).

    Frozen: always inside the install folder (``<exe dir>\\_internal`` in onedir mode), never a temp dir.
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        meipass = getattr(sys, "_MEIPASS", None)
        base = Path(meipass) if meipass else exe_dir / "_internal"
        return base / "hardware_monitor_widget" / "native" / "sensorhost" / HOST_EXE
    return Path(__file__).resolve().parent / "native" / "sensorhost" / HOST_EXE


def parse_line(line: str) -> dict | None:
    """Parse one protocol line; None for blank/invalid lines or non-object JSON."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except Exception:   # ValueError, RecursionError (deeply nested JSON), ...
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        return None
    return obj


def hw_kind_for(hw_type: str) -> str:
    return _HW_KIND.get(str(hw_type).lower(), "other")


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except (OverflowError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _text(v, default: str = "") -> str:
    s = default if v is None else str(v)
    return s[:MAX_NAME_CHARS]


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


def clean_env(env=None) -> dict[str, str]:
    """Copy of the environment without variables that influence the .NET runtime."""
    src = os.environ if env is None else env
    return {k: v for k, v in src.items() if not k.upper().startswith(_BLOCKED_ENV_PREFIXES)}


def snapshot_from_message(msg: dict, hello: dict | None) -> Snapshot:
    """Convert a ``snapshot`` message (plus the earlier ``hello``) into a models.Snapshot."""
    hardware = {}
    for h in _as_list(msg.get("hardware"))[:MAX_HARDWARE]:
        if isinstance(h, dict) and isinstance(h.get("id"), str):
            hardware[h["id"]] = h

    def resolve(hw_id: str) -> tuple[str, str, str]:
        """-> (display name, hw_kind, hw_id used for grouping)."""
        h = hardware.get(hw_id)
        if h is None:
            return _text(hw_id), "other", "hw/" + hw_id
        name = _text(h.get("name") or hw_id)
        kind = hw_kind_for(h.get("type", ""))
        parent = hardware.get(h.get("parent")) if isinstance(h.get("parent"), str) else None
        if str(h.get("type", "")).lower() == "superio":
            kind = "mainboard"
            if parent is not None:
                # the Super I/O chip (e.g. "Nuvoton NCT6798D") is a detail: show the board's name
                name = _text(parent.get("name", "")) or name
        return name, kind, "hw/" + hw_id

    sensors: list[Sensor] = []
    for s in _as_list(msg.get("sensors"))[:MAX_SENSORS]:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            continue
        name, hw_kind, hw_id = resolve(str(s.get("hw", "")))
        kind = str(s.get("type", "")).lower()
        limit = _TEMP_LIMITS.get(hw_kind) if kind == "temperature" else None
        value, vmin, vmax = _num(s.get("value")), _num(s.get("min")), _num(s.get("max"))
        if kind == "temperature" and hw_kind == "cpu" and value is not None and value <= 0.0:
            # without admin/PawnIO LHM reports 0 °C for CPU sensors it cannot read: show "—"
            value = vmin = vmax = None
        sensors.append(Sensor(
            key="hw/" + s["id"],
            source=SOURCE,
            hardware=name,
            hw_id=hw_id,
            hw_kind=hw_kind,
            kind=kind,
            name=_text(s.get("name", "")),
            value=value,
            min=vmin,
            max=vmax,
            limit=limit,
        ))

    t = _num(msg.get("t"))
    return Snapshot(
        sensors=sensors,
        statuses={SOURCE: status_for(hello, bool(sensors))},
        taken_at=t / 1000.0 if t else time.time(),
        groups=_text(msg.get("groups"), "all") or "all",
    )


def status_for(hello: dict | None, has_sensors: bool) -> SourceStatus:
    # "needs_admin" / "driver_missing" keep available=True when there are sensors; the UI shows a card
    # for any reason != "ok".
    if hello is not None and not hello.get("admin"):
        return SourceStatus(SOURCE, has_sensors, "needs_admin",
                            "Sin permisos de administrador: faltan temperaturas de CPU y placa base", "lhm")
    if hello is not None and not hello.get("pawnio"):
        return SourceStatus(SOURCE, has_sensors, "driver_missing",
                            "PawnIO no está instalado: faltan temperaturas de CPU", "lhm")
    if has_sensors:
        return SourceStatus(SOURCE, True, "ok", "", "lhm")
    return SourceStatus(SOURCE, False, "error", "Sin sensores", "lhm")


class SensorHostProcess:
    """Runs SensorHost.exe, reads its JSON lines on a thread and restarts it with backoff if it dies.

    ``on_message(msg)`` is called from the reader thread for every parsed message. When the process
    exits unexpectedly a synthetic ``{"type": "error", "message": ...}`` is delivered first.
    """

    def __init__(self, on_message: Callable[[dict], None], interval_ms: int = 1000,
                 path: Path | None = None):
        self._on_message = on_message
        self._interval_ms = int(interval_ms)
        self._path = Path(path) if path else host_path()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()      # interrupts the backoff wait (refresh / stop / restart)
        self._lock = threading.Lock()       # guards _proc and the _stop check around spawning
        self._write_lock = threading.Lock()
        self._reset_backoff = False
        self._restarting = False
        self._groups = "all"                # last requested hardware groups (reapplied on respawn)

    @property
    def path(self) -> Path:
        return self._path

    def is_running(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="SensorHostReader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the host and the reader; bounded to roughly 3 seconds."""
        with self._lock:
            self._stop.set()
            proc = self._proc
        self._wake.set()
        if proc is not None and proc.poll() is None:
            self._send("quit", proc)
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self._kill(proc, 1.0)
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=0.5)
        self._thread = None

    def restart(self) -> None:
        """Kill the current host (if any); the reader respawns it immediately."""
        with self._lock:
            proc = self._proc
            self._restarting = True
        self._reset_backoff = True
        self._wake.set()
        if proc is not None and proc.poll() is None:
            self._kill(proc, 1.0)

    def set_interval(self, ms: int) -> None:
        self._interval_ms = int(ms)
        self._send(f"interval {self._interval_ms}")

    def set_groups(self, groups) -> None:
        """Enable only these hardware groups in the host (None = all, the host default)."""
        self._groups = groups_arg(groups)
        self._send(f"hardware {self._groups}")

    def refresh(self) -> None:
        if not self._send("refresh"):
            # host dead or waiting in backoff: retry now
            self._reset_backoff = True
            self._wake.set()

    # -- internals -----------------------------------------------------------------------------
    @staticmethod
    def _kill(proc: subprocess.Popen, timeout: float) -> None:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("sensor host did not exit after kill")

    def _send(self, command: str, proc: subprocess.Popen | None = None) -> bool:
        proc = proc or self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return False
        try:
            with self._write_lock:
                proc.stdin.write(command + "\n")
                proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _spawn(self) -> subprocess.Popen:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        args = [str(self._path), "--interval", str(self._interval_ms)]
        if self._groups != "all":
            args += ["--hardware", self._groups]
        return subprocess.Popen(
            args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=str(self._path.parent), creationflags=flags, env=clean_env(),
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )

    def _emit(self, msg: dict) -> None:
        try:
            self._on_message(msg)
        except Exception:  # never let a consumer bug kill the reader
            log.exception("on_message callback failed")

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            log.exception("sensor host reader crashed")
            self._emit({"type": "error", "message": "sensor host reader crashed"})

    def _read_messages(self, proc: subprocess.Popen) -> str | None:
        """Pump protocol lines until EOF. Returns the last ``error`` message sent by the host."""
        assert proc.stdout is not None
        last_error: str | None = None
        try:
            while True:
                line = proc.stdout.readline(MAX_LINE_CHARS)
                if not line:
                    break
                if len(line) >= MAX_LINE_CHARS and not line.endswith("\n"):
                    # oversized line: drop it up to the next newline
                    while True:
                        rest = proc.stdout.readline(MAX_LINE_CHARS)
                        if not rest or rest.endswith("\n"):
                            break
                    continue
                msg = parse_line(line)
                if msg is None:
                    continue
                if msg["type"] == "error":
                    last_error = _text(msg.get("message", ""))
                self._emit(msg)
        except (OSError, ValueError):
            pass
        return last_error

    def _loop(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            started = time.monotonic()
            proc: subprocess.Popen | None = None
            spawn_error = ""
            with self._lock:
                if self._stop.is_set():
                    break
                self._restarting = False
                try:
                    proc = self._spawn()
                except OSError as exc:
                    self._proc = None
                    spawn_error = f"cannot start host: {exc.__class__.__name__}"
                else:
                    self._proc = proc
            if proc is None:
                self._emit({"type": "error", "message": spawn_error})
            else:
                if self._stop.is_set():      # defensive: stop() raced with the spawn
                    self._kill(proc, 2.0)
                    break
                last_error = self._read_messages(proc)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._kill(proc, 2.0)
                for stream in (proc.stdin, proc.stdout):
                    try:
                        if stream is not None:
                            stream.close()
                    except (OSError, ValueError):
                        pass
                if self._stop.is_set():
                    break
                if self._restarting:
                    # deliberate restart (watchdog): respawn at once, no error message
                    self._restarting = False
                    self._reset_backoff = False
                    attempt = 0
                    continue
                detail = f"host exited (code {proc.returncode})"
                if last_error:
                    detail += f": {last_error}"
                self._emit({"type": "error", "message": detail})
            if time.monotonic() - started >= STABLE_RUN_SECONDS:
                attempt = 0
            delay = BACKOFF_STEPS[min(attempt, len(BACKOFF_STEPS) - 1)]
            attempt += 1
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                break
            if self._reset_backoff:
                self._reset_backoff = False
                attempt = 0
