"""Qt facade over the native sensor host: emits models.Snapshot objects."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import sensorhost
from .models import Snapshot, SourceStatus

log = logging.getLogger(__name__)

MIN_INTERVAL_MS = 250
MAX_INTERVAL_MS = 10000
WATCHDOG_MIN_S = 5.0
STARTUP_GRACE_S = 120.0     # max wait for the first snapshot after the host starts
WATCHDOG_FACTOR = 5
WATCHDOG_TICK_MS = 1000
STALE_DETAIL = "El motor de sensores no responde; reiniciando"
MISSING_DETAIL = "Motor de sensores no compilado (native/build.ps1)"


def _clamp(ms: int) -> int:
    return max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, int(ms)))


class HardwareMonitor(QObject):
    """Starts the sensor host and republishes its messages as ``snapshot_ready(Snapshot)``.

    ``host_factory(on_message, interval_ms)`` must return an object with start/stop/set_interval/refresh;
    ``on_message`` may be called from any thread.
    """

    snapshot_ready = Signal(object)
    _message = Signal(object)   # thread hop: host reader thread -> owner thread

    def __init__(self, interval_ms: int = 1000, parent=None, host_factory=None):
        super().__init__(parent)
        self._interval_ms = _clamp(interval_ms)
        self._host_factory = host_factory
        self._host = None
        self._hello: dict | None = None
        self._last: Snapshot | None = None
        self._last_activity = time.monotonic()
        self._host_down = False   # an error was reported and no snapshot has arrived since
        self._got_snapshot = False  # current host run already delivered a snapshot
        self._message.connect(self._handle)
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_TICK_MS)
        self._watchdog.timeout.connect(self.check_watchdog)

    def start(self) -> None:
        if self._host is not None:
            return
        if self._host_factory is None and not sensorhost.host_path().exists():
            self._publish_error(MISSING_DETAIL)
            return
        factory = self._host_factory or (lambda cb, ms: sensorhost.SensorHostProcess(cb, ms))
        self._host = factory(self._message.emit, self._interval_ms)
        self._last_activity = time.monotonic()
        self._host.start()
        self._watchdog.start()

    @property
    def hello(self) -> dict | None:
        """Last ``hello`` message of the sensor host (admin / PawnIO state), or None."""
        return self._hello

    def stop(self) -> None:
        self._watchdog.stop()
        host, self._host = self._host, None
        if host is not None:
            host.stop()

    def refresh(self) -> None:
        if self._host is not None:
            self._host.refresh()

    def set_interval(self, ms: int) -> None:
        self._interval_ms = _clamp(ms)
        if self._host is not None:
            self._host.set_interval(self._interval_ms)

    def stale_after_s(self) -> float:
        if not self._got_snapshot:
            # Elevated LHM init (PawnIO modules, SMBus/SPD, storage) can take tens of seconds
            # before the first snapshot: don't kill it in a restart loop.
            return STARTUP_GRACE_S
        return max(WATCHDOG_MIN_S, WATCHDOG_FACTOR * self._interval_ms / 1000.0)

    def check_watchdog(self, now: float | None = None) -> bool:
        """Publish a "stale" snapshot and restart the host if no snapshot arrived for too long.

        Contract with the UI: ``reason in ("stale", "error")`` means the values are out of date.
        Returns True when the watchdog fired.
        """
        if self._host is None or self._host_down:
            return False   # no host, or it already died and is being restarted with backoff
        now = time.monotonic() if now is None else now
        if now - self._last_activity <= self.stale_after_s():
            return False
        log.warning("sensor host silent for %.0f s (first snapshot: %s); restarting",
                    now - self._last_activity, self._got_snapshot)
        self._last_activity = now
        self._publish("stale", STALE_DETAIL)
        self._got_snapshot = False      # the restarted host gets the startup grace again
        restart = getattr(self._host, "restart", None)
        if callable(restart):
            restart()
        return True

    # -- internals -----------------------------------------------------------------------------
    def _handle(self, msg) -> None:
        kind = msg.get("type") if isinstance(msg, dict) else None
        if kind == "hello":
            self._hello = msg
            self._got_snapshot = False
            self._host_down = False
            self._last_activity = time.monotonic()
            log.info("sensor host started (admin=%s, pawnio=%s)", msg.get("admin"), msg.get("pawnio"))
        elif kind == "snapshot":
            snap = sensorhost.snapshot_from_message(msg, self._hello)
            if not self._got_snapshot:
                log.info("first snapshot: %d sensors", len(snap.sensors))
            self._got_snapshot = True
            self._last = snap
            self._last_activity = time.monotonic()
            self._host_down = False
            self.snapshot_ready.emit(snap)
        elif kind == "error":
            text = str(msg.get("message", "error"))
            if msg.get("fatal") is False:
                log.warning("sensor host: %s", text)     # one component failed; data keeps flowing
                return
            log.warning("sensor host error: %s", text)
            self._host_down = True
            self._publish_error(text)

    def _publish_error(self, detail: str) -> None:
        self._publish("error", detail)

    def _publish(self, reason: str, detail: str) -> None:
        sensors = list(self._last.sensors) if self._last else []
        snap = Snapshot(
            sensors=sensors,
            statuses={sensorhost.SOURCE: SourceStatus(sensorhost.SOURCE, False, reason, detail, "lhm")},
            taken_at=time.time(),
        )
        self.snapshot_ready.emit(snap)
