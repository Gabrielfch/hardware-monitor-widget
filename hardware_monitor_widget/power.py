"""Resource saving (pure, no Qt): which hardware groups the sensor host needs and how often to poll.

The host runs every group by default; while no panel / detail window is open only what the visible pills,
the tray icon, the alerts and the history use has to be read, and when nothing is watching at all the
polling can slow down.
"""
from __future__ import annotations

from .models import Snapshot
from .pins import resolve_pins

IDLE_INTERVAL_MS = 5000        # polling when the bar is hidden and no alert / history is active
ALWAYS_GROUPS = frozenset({"cpu"})      # the tray icon shows the CPU temperature


def needed_groups(full: Snapshot | None, pinned: list[str], ui_open: bool, history: bool = False,
                  alerts: bool = False, prefs=None) -> frozenset[str] | None:
    """Hardware groups (models.HW_KINDS names) to enable; None = every group.

    `full` is the last snapshot taken with every group enabled: it tells which groups the pinned sensors
    (and the summary sensors that history / alerts use) belong to.
    """
    if ui_open or full is None or not full.sensors:
        return None
    sensors = list(resolve_pins(list(pinned), full))
    if history or alerts:
        from .alerts import alert_sensors
        from .curation import Prefs
        from .history import history_sensors
        prefs = prefs or Prefs()
        # alerts only read temperatures; history records every summary / pinned sensor
        sensors += (history_sensors if history else alert_sensors)(full, prefs, list(pinned))
    kinds = {s.hw_kind for s in sensors} | ALWAYS_GROUPS
    if "other" in kinds:
        return None             # the host cannot select that kind: keep everything
    return frozenset(kinds)


def effective_interval(configured_ms: int, ui_open: bool, bar_visible: bool, tracking: bool) -> int:
    """Polling interval: the configured one while someone looks at the data (panel / detail open, pills
    visible) or alerts / history are on; otherwise at least IDLE_INTERVAL_MS."""
    if ui_open or bar_visible or tracking:
        return configured_ms
    return max(configured_ms, IDLE_INTERVAL_MS)
