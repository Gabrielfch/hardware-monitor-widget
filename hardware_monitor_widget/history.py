"""Sensor history: one sample per minute (the mean of that minute) for the summary and pinned sensors.

No Qt. One CSV file per local day in ``history/`` (``YYYY-MM-DD.csv``). Lines are ``@idx,key`` (defines a sensor
key for this file) or ``epoch_minute_seconds,idx,value``. Files older than ``RETENTION_DAYS`` are deleted and the
folder is kept under ``MAX_TOTAL_BYTES`` (oldest first). Like the log folder, nothing is read or written through
a symlink / junction (the process runs elevated). The last hour is kept in memory for immediate charts.
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

from .secure_fs import is_reparse_point

log = logging.getLogger(__name__)

RETENTION_DAYS = 30
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MEMORY_MINUTES = 60
PERIODS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}
GAP_SECONDS = 150               # a longer hole between two samples breaks the line
_FILE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\.csv")


def _unsafe(path: Path) -> bool:
    """True when `path` exists and is a link / junction / reparse point (a missing path is fine)."""
    return os.path.lexists(path) and is_reparse_point(path)


def history_sensors(snapshot, prefs, pinned) -> list:
    """Sensors recorded: those in the summary plus the pinned ones (deduplicated)."""
    from .curation import summary_sections
    from .pins import resolve_pins
    out: dict = {}
    for sec in summary_sections(snapshot, prefs):
        for s in sec.sensors:
            out.setdefault(s.key, s)
    for s in resolve_pins(list(pinned), snapshot):
        out.setdefault(s.key, s)
    return list(out.values())


def stats(points: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    """(min, max, mean) of the values, or None when there are none."""
    if not points:
        return None
    vals = [v for _t, v in points]
    return min(vals), max(vals), sum(vals) / len(vals)


def plot_points(points: list[tuple[float, float]], t0: float, t1: float, width: int, height: int,
                vmin: float | None = None, vmax: float | None = None) -> list[list[tuple[float, float]]]:
    """Maps (time, value) samples to pixel coordinates inside a width x height box, as runs of points
    (a run ends where there is a gap longer than GAP_SECONDS). Empty list when there is nothing to draw."""
    pts = [(t, v) for t, v in points if t0 <= t <= t1]
    if not pts or width <= 0 or height <= 0 or t1 <= t0:
        return []
    lo = min(v for _t, v in pts) if vmin is None else vmin
    hi = max(v for _t, v in pts) if vmax is None else vmax
    if hi - lo < 1e-9:
        lo, hi = lo - 1.0, hi + 1.0
    runs: list[list[tuple[float, float]]] = []
    last_t = None
    for t, v in pts:
        x = (t - t0) / (t1 - t0) * width
        y = height - (v - lo) / (hi - lo) * height
        if last_t is None or t - last_t > GAP_SECONDS:
            runs.append([])
        runs[-1].append((x, y))
        last_t = t
    return runs


class History:
    def __init__(self, directory: Path, enabled: bool = True, clock=time.time):
        self.dir = Path(directory)
        self.enabled = bool(enabled)
        self._clock = clock
        self.revision = 0                                       # bumps each time a minute is stored
        self._minute: int | None = None                         # minute (epoch // 60) being accumulated
        self._acc: dict[str, list[float]] = {}                  # key -> [sum, count]
        self._mem: dict[str, deque] = {}                        # key -> deque[(ts, value)] last hour
        self._defs: dict[str, int] = {}                         # keys defined in the current day file
        self._def_day: str | None = None
        self._warned = False
        if self.enabled:
            self.prune()
            self._load_recent()

    # ------------------------------------------------------------------ recording
    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if on == self.enabled:
            return
        self.enabled = on
        self._acc.clear()
        self._minute = None
        if on:
            self.prune()

    def record(self, values: dict[str, float], now: float | None = None) -> None:
        """Adds one reading per key; when the minute rolls over the previous minute's means are stored."""
        if not self.enabled:
            return
        now = self._clock() if now is None else now
        minute = int(now // 60)
        if self._minute is not None and minute != self._minute:
            self._flush()
        self._minute = minute
        for key, v in values.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                continue
            a = self._acc.setdefault(key, [0.0, 0])
            a[0] += float(v)
            a[1] += 1

    def _flush(self) -> None:
        minute, acc = self._minute, self._acc
        self._acc = {}
        if minute is None or not acc:
            return
        ts = minute * 60
        samples = {k: a[0] / a[1] for k, a in acc.items() if a[1]}
        for k, v in samples.items():
            self._mem.setdefault(k, deque()).append((ts, v))
        cutoff = ts - MEMORY_MINUTES * 60
        for k in list(self._mem):
            q = self._mem[k]
            while q and q[0][0] <= cutoff:
                q.popleft()
            if not q:
                del self._mem[k]
        self.revision += 1
        self._write(ts, samples)

    def flush(self) -> None:
        """Stores the minute in progress (used on exit)."""
        if self.enabled:
            self._flush()
            self._minute = None

    def _safe_dir(self) -> bool:
        if _unsafe(self.dir.parent) or _unsafe(self.dir):
            if not self._warned:
                log.warning("history directory is a link/junction; history is not saved")
                self._warned = True
            return False
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return True

    def _day_path(self, ts: float) -> Path:
        return self.dir / (datetime.fromtimestamp(ts).strftime("%Y-%m-%d") + ".csv")

    def _write(self, ts: float, samples: dict[str, float]) -> None:
        if not self._safe_dir():
            return
        path = self._day_path(ts)
        if _unsafe(path):
            return
        try:
            if self._def_day != path.name:
                new_day = self._def_day is not None
                self._defs = self._read_defs(path)
                self._def_day = path.name
                if new_day:
                    self.prune()
            lines = []
            for k, v in samples.items():
                idx = self._defs.get(k)
                if idx is None:
                    idx = len(self._defs)
                    self._defs[k] = idx
                    lines.append("@%d,%s" % (idx, k.replace("\n", " ").replace("\r", " ")))
                lines.append(f"{int(ts)},{idx},{v:.2f}")
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            if not self._warned:
                log.warning("could not write the history", exc_info=True)
                self._warned = True

    @staticmethod
    def _parse(text: str):
        """Yields ('def', idx, key) / ('s', ts, idx, value) for each valid line."""
        for line in text.splitlines():
            if not line:
                continue
            try:
                if line[0] == "@":
                    idx, key = line[1:].split(",", 1)
                    yield ("def", int(idx), key)
                else:
                    ts, idx, v = line.split(",")
                    yield ("s", int(ts), int(idx), float(v))
            except ValueError:
                continue

    @staticmethod
    def _read_text(path: Path) -> str:
        if not os.path.lexists(path) or is_reparse_point(path):
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _read_defs(self, path: Path) -> dict[str, int]:
        return {item[2]: item[1] for item in self._parse(self._read_text(path)) if item[0] == "def"}

    def _read_file(self, path: Path, key: str, t0: float) -> list[tuple[float, float]]:
        names: dict[int, str] = {}
        out: list[tuple[float, float]] = []
        for item in self._parse(self._read_text(path)):
            if item[0] == "def":
                names[item[1]] = item[2]
            elif item[1] >= t0 and names.get(item[2]) == key:
                out.append((float(item[1]), item[3]))
        return out

    def _dir_ok(self) -> bool:
        return not _unsafe(self.dir.parent) and not _unsafe(self.dir) and self.dir.is_dir()

    def _load_recent(self) -> None:
        if not self._dir_ok():
            return
        now = self._clock()
        t0 = now - MEMORY_MINUTES * 60
        for day in (now - 86400, now):
            names: dict[int, str] = {}
            for item in self._parse(self._read_text(self._day_path(day))):
                if item[0] == "def":
                    names[item[1]] = item[2]
                elif item[1] > t0 and item[2] in names:
                    self._mem.setdefault(names[item[2]], deque()).append((float(item[1]), item[3]))

    # ------------------------------------------------------------------ reading
    def series(self, key: str, seconds: int, now: float | None = None) -> list[tuple[float, float]]:
        """(timestamp, value) of the last `seconds` seconds, oldest first, including the minute in progress."""
        now = self._clock() if now is None else now
        t0 = now - seconds
        if seconds <= MEMORY_MINUTES * 60:
            pts = [p for p in self._mem.get(key, ()) if p[0] >= t0]
        else:
            pts = []
            if self._dir_ok():
                day = datetime.fromtimestamp(t0).date()
                today = datetime.fromtimestamp(now).date()
                while day <= today:
                    pts.extend(self._read_file(self.dir / (day.isoformat() + ".csv"), key, t0))
                    day += timedelta(days=1)
        a = self._acc.get(key)
        if a and a[1] and self._minute is not None:
            pts.append((float(self._minute * 60), a[0] / a[1]))
        return pts

    # ------------------------------------------------------------------ maintenance
    def _files(self) -> list[tuple[date, Path, int]]:
        out = []
        try:
            entries = list(os.scandir(self.dir))
        except OSError:
            return out
        for e in entries:
            m = _FILE_RE.fullmatch(e.name)
            if not m:
                continue
            p = Path(e.path)
            try:
                if is_reparse_point(p) or not p.is_file():
                    continue
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                out.append((d, p, p.stat().st_size))
            except (OSError, ValueError):
                continue
        return sorted(out)

    def prune(self, today: date | None = None) -> None:
        """Deletes files older than RETENTION_DAYS, then the oldest ones while the total exceeds the cap."""
        if not self._dir_ok():
            return
        today = today or datetime.fromtimestamp(self._clock()).date()
        limit = today - timedelta(days=RETENTION_DAYS)
        kept = []
        for d, p, size in self._files():
            if d < limit:
                self._remove(p)
            else:
                kept.append((d, p, size))
        total = sum(s for _d, _p, s in kept)
        while total > MAX_TOTAL_BYTES and len(kept) > 1:
            _d, p, size = kept.pop(0)
            self._remove(p)
            total -= size

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            log.warning("could not delete a history file")

    def clear(self) -> None:
        """Deletes every stored sample (disk and memory)."""
        self._acc.clear()
        self._mem.clear()
        self._minute = None
        self._defs = {}
        self._def_day = None
        self.revision += 1
        if not self._dir_ok():
            return
        for _d, p, _s in self._files():
            self._remove(p)
