"""Diagnostics: ``python -m hardware_monitor_widget.probe`` runs the sensor host once and prints it."""
from __future__ import annotations

import subprocess
import sys

from . import sensorhost
from .models import format_value


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    path = sensorhost.host_path()
    if not path.exists():
        print("Motor de sensores no compilado (native/build.ps1)")
        return 1
    proc = subprocess.run([str(path), "--once"], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(path.parent), timeout=60,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    hello = None
    snap_msg = None
    for line in proc.stdout.splitlines():
        msg = sensorhost.parse_line(line)
        if not msg:
            continue
        if msg["type"] == "hello":
            hello = msg
        elif msg["type"] == "snapshot":
            snap_msg = msg
        elif msg["type"] == "error":
            print("error:", msg.get("message"))
    if snap_msg is None:
        print("No snapshot received (exit code %s)" % proc.returncode)
        return 1
    snap = sensorhost.snapshot_from_message(snap_msg, hello)
    st = snap.statuses[sensorhost.SOURCE]
    if hello:
        print(f"admin={hello.get('admin')} pawnio={hello.get('pawnio')} "
              f"({hello.get('pawnio_version')}) os={hello.get('os')}")
    print(f"status: {st.reason}" + (f" - {st.detail}" if st.detail else ""))
    groups: dict[str, list] = {}
    for s in snap.sensors:
        groups.setdefault(s.hw_id, []).append(s)
    for sensors in groups.values():
        first = sensors[0]
        print(f"\n[{first.hw_kind}] {first.hardware}")
        for s in sensors:
            print(f"  {s.kind:<12} {s.name:<28} {format_value(s.kind, s.value):>12}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
