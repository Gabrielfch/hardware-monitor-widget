from hardware_monitor_widget.config import DEFAULTS
from hardware_monitor_widget.models import Sensor, Snapshot
from hardware_monitor_widget.pins import MAX_PINS, is_pinned, resolve_pins, toggle_pin
from tools.synthetic import make_snapshot


def sensor(key, hw_kind, kind, name, value=10.0):
    return Sensor(key, "native", "hw", "hw//x", hw_kind, kind, name, value)


def test_defaults_resolve_all_five():
    snap = make_snapshot()
    got = resolve_pins(DEFAULTS["pinned"], snap)
    assert [(s.hw_kind, s.kind, s.name) for s in got] == [
        ("cpu", "temperature", "Core (Tctl/Tdie)"), ("cpu", "load", "CPU Total"),
        ("gpu", "temperature", "GPU Core"), ("gpu", "load", "GPU Core"), ("ram", "load", "Memory")]


def test_cpu_temp_prefers_package_then_first():
    a, b = sensor("k/a", "cpu", "temperature", "Core #1"), sensor("k/b", "cpu", "temperature", "CPU Package")
    assert resolve_pins(["auto:cpu_temp"], Snapshot([a, b]))[0].key == "k/b"
    assert resolve_pins(["auto:cpu_temp"], Snapshot([a]))[0].key == "k/a"


def test_gpu_prefers_gpu_core():
    a, b = sensor("g/a", "gpu", "temperature", "GPU Hot Spot"), sensor("g/b", "gpu", "temperature", "GPU Core")
    assert resolve_pins(["auto:gpu_temp"], Snapshot([a, b]))[0].key == "g/b"
    assert resolve_pins(["auto:gpu_temp"], Snapshot([a]))[0].key == "g/a"


def test_any_source_is_accepted():
    s = Sensor("x/t", "native", "CPU", "x", "cpu", "temperature", "Package", 50.0)
    assert resolve_pins(["auto:cpu_temp"], Snapshot([s])) == [s]


def test_missing_keys_are_omitted_and_duplicates_dropped():
    snap = make_snapshot()
    key = resolve_pins(["auto:cpu_temp"], snap)[0].key
    got = resolve_pins(["nope", "auto:nothing", "auto:cpu_temp", key], snap)
    assert [s.key for s in got] == [key]
    assert resolve_pins(DEFAULTS["pinned"], Snapshot()) == []


def test_explicit_key_and_toggle():
    snap = make_snapshot()
    fan = next(s for s in snap.sensors if s.kind == "fan")
    pins = toggle_pin(list(DEFAULTS["pinned"]), fan, snap)
    assert pins[-1] == fan.key and is_pinned(pins, fan, snap)
    assert toggle_pin(pins, fan, snap) == DEFAULTS["pinned"]


def test_toggle_removes_covering_auto():
    snap = make_snapshot()
    cpu = resolve_pins(["auto:cpu_temp"], snap)[0]
    pins = list(DEFAULTS["pinned"])
    assert is_pinned(pins, cpu, snap)
    new = toggle_pin(pins, cpu, snap)
    assert "auto:cpu_temp" not in new and not is_pinned(new, cpu, snap)
    assert toggle_pin(new, cpu, snap)[-1] == cpu.key


def test_pin_limit():
    snap = make_snapshot()
    extra = next(s for s in snap.sensors if s.kind == "fan")
    full = [f"k/{i}" for i in range(MAX_PINS)]
    assert MAX_PINS == 32 and toggle_pin(full, extra, snap) == full
    assert len(toggle_pin(full[:-1], extra, snap)) == MAX_PINS
    # unpinning is still allowed when full
    assert toggle_pin([*full[:-1], extra.key], extra, snap) == full[:-1]


def test_bulk_state_and_toggle():
    from hardware_monitor_widget.pins import bulk_state, toggle_bulk
    snap = make_snapshot()
    sensors = [s for s in snap.sensors if s.hw_kind == "gpu"]
    assert bulk_state([], sensors, snap) == "none" and bulk_state([], [], snap) == "none"
    pins, skipped = toggle_bulk([], sensors, snap)
    assert pins == [s.key for s in sensors] and skipped == 0
    assert bulk_state(pins, sensors, snap) == "all"
    assert bulk_state(pins[:1], sensors, snap) == "some"
    again, _ = toggle_bulk(pins[:1], [*sensors, sensors[0]], snap)
    assert again == pins                      # missing ones appended in order, no duplicates
    assert toggle_bulk(pins, sensors, snap) == ([], 0)


def test_bulk_unpin_removes_covering_auto():
    from hardware_monitor_widget.pins import toggle_bulk
    snap = make_snapshot()
    cpu = resolve_pins(["auto:cpu_temp"], snap)
    new, _ = toggle_bulk(list(DEFAULTS["pinned"]), cpu, snap)
    assert "auto:cpu_temp" not in new


def test_bulk_limit():
    from hardware_monitor_widget.pins import toggle_bulk
    snap = make_snapshot()
    sensors = snap.sensors[:6]
    base = [f"k/{i}" for i in range(MAX_PINS - 2)]
    pins, skipped = toggle_bulk(base, sensors, snap)
    assert len(pins) == MAX_PINS and skipped == 4 and pins[:len(base)] == base


def test_max_pins_matches_config_limit():
    from hardware_monitor_widget import config
    assert MAX_PINS == config.MAX_PINNED


def test_toggle_pin_result_reports_rejection_at_the_limit():
    from hardware_monitor_widget.pins import toggle_pin_result
    snap = Snapshot([sensor("k/new", "cpu", "temperature", "Core #1")])
    full = [f"x{i}" for i in range(MAX_PINS)]
    assert toggle_pin_result(full, snap.sensors[0], snap) == (full, True)
    assert toggle_pin(full, snap.sensors[0], snap) == full
    pins, rejected = toggle_pin_result(full[:-1], snap.sensors[0], snap)
    assert not rejected and pins[-1] == "k/new"
    assert toggle_pin_result(pins, snap.sensors[0], snap) == (full[:-1], False)      # unpinning is never rejected
