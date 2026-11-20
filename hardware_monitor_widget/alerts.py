"""Overheating alerts (pure, no Qt): a temperature sensor of the summary / pinned set that stays in the red band
for N seconds triggers one notification, then stays quiet for a cooldown (per sensor).

The controller feeds `AlertManager.check` on every snapshot and shows what it returns in the tray. Idea adapted from
Taskbar Battery Widget (MIT): per-item cooldown with state that is re-armed when the condition clears.
"""
from __future__ import annotations

from .curation import Prefs, summary_sections
from .i18n import decimal_sep, tr
from .models import Sensor, Snapshot, band_for, format_value
from .pins import resolve_pins


def alert_sensors(snapshot: Snapshot, prefs: Prefs, pinned: list[str]) -> list[Sensor]:
    """Temperature sensors shown in the summary or pinned (deduplicated, snapshot order of discovery)."""
    out: dict[str, Sensor] = {}
    for sec in summary_sections(snapshot, prefs):
        for s in sec.sensors:
            out.setdefault(s.key, s)
    for s in resolve_pins(pinned, snapshot):
        out.setdefault(s.key, s)
    return [s for s in out.values() if s.kind == "temperature"]


class AlertManager:
    """`config` only needs `.get(key)`: alerts_enabled, alert_seconds, alert_cooldown_min, fahrenheit."""

    def __init__(self, config):
        self._config = config
        self._red_since: dict[str, float] = {}
        self._last_notified: dict[str, float] = {}

    def reset(self) -> None:
        self._red_since.clear()
        self._last_notified.clear()

    def check(self, sensors: list[Sensor], now: float, stale: bool = False) -> list[tuple[str, str]]:
        """[(title, body)] to notify now. `stale`: the values are the last known ones, so nothing changes."""
        if not self._config.get("alerts_enabled"):
            self._red_since.clear()                 # re-enabling never replays an old red spell
            return []
        if stale:
            return []
        seconds = float(self._config.get("alert_seconds"))
        cooldown = float(self._config.get("alert_cooldown_min")) * 60
        fahrenheit = bool(self._config.get("fahrenheit"))
        present = {s.key for s in sensors}
        for k in [k for k in self._red_since if k not in present]:
            del self._red_since[k]
        out: list[tuple[str, str]] = []
        for s in sensors:
            if s.kind != "temperature" or band_for(s.kind, s.value, s.key) != "red":
                self._red_since.pop(s.key, None)
                continue
            since = self._red_since.setdefault(s.key, now)
            if now - since < seconds:
                continue
            last = self._last_notified.get(s.key)
            if last is not None and now - last < cooldown:
                continue
            self._last_notified[s.key] = now
            name = f"{s.hardware} · {s.name}" if s.hardware else s.name
            value = format_value(s.kind, s.value, fahrenheit, decimal_sep())
            out.append((tr("alert_title"), tr("alert_body", name=name, value=value)))
        return out
