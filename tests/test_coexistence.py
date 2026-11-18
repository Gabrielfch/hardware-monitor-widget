"""Docking next to other taskbar widgets and unreadable CPU temperatures."""
from hardware_monitor_widget import taskbar
from hardware_monitor_widget.sensorhost import snapshot_from_message


def test_docked_edge_no_neighbours():
    assert taskbar.docked_edge(1550, []) == 1550


def test_docked_edge_moves_left_of_battery_widget():
    # real layout: Taskbar Battery Widget at (1390..1534), tray starts at 1550
    assert taskbar.docked_edge(1550, [(1390, 1038, 1534, 1074)]) == 1390


def test_docked_edge_chain_and_far_window_ignored():
    rects = [(1390, 1038, 1534, 1074), (1200, 1038, 1380, 1074), (100, 1038, 300, 1074)]
    assert taskbar.docked_edge(1550, rects) == 1200


def test_cpu_temperature_zero_becomes_unknown():
    hello = {"admin": False, "pawnio": True}
    msg = {"type": "snapshot", "t": 1, "hardware": [{"id": "/amdcpu/0", "name": "Ryzen", "type": "Cpu"},
                                                     {"id": "/gpu-amd/0", "name": "RX", "type": "GpuAmd"}],
           "sensors": [{"id": "/amdcpu/0/temperature/2", "hw": "/amdcpu/0", "name": "Core (Tctl/Tdie)",
                        "type": "Temperature", "value": 0.0, "min": 0.0, "max": 0.0},
                       {"id": "/gpu-amd/0/temperature/0", "hw": "/gpu-amd/0", "name": "GPU Core",
                        "type": "Temperature", "value": 52.0, "min": 40.0, "max": 60.0}]}
    snap = snapshot_from_message(msg, hello)
    by = snap.by_key()
    assert by["hw//amdcpu/0/temperature/2"].value is None
    assert by["hw//gpu-amd/0/temperature/0"].value == 52.0
