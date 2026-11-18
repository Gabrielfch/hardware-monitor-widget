import pytest

from hardware_monitor_widget.models import (UNITS, Snapshot, SourceStatus, format_value, is_stale,
                                            source_reason)


def test_default_separator_is_point():
    assert format_value("power", 65.25) == "65.2 W"
    assert format_value("voltage", 1.25) == "1.250 V"


def test_spanish_decimal_comma():
    assert format_value("power", 65.2, decimal_sep=",") == "65,2 W"
    assert format_value("voltage", 1.25, decimal_sep=",") == "1,250 V"
    assert format_value("throughput", 3 * 1024 * 1024, decimal_sep=",") == "3,0 MB/s"
    assert format_value("temperature", 62.0, decimal_sep=",") == "62°C"
    assert format_value("temperature", None, decimal_sep=",") == "—"


@pytest.mark.parametrize("kind,value,expected", [
    ("current", 1.5, "1.50 A"), ("energy", 4200.0, "4200 mWh"), ("frequency", 50.0, "50 Hz"),
    ("timespan", 90.0, "90 s"), ("noise", 31.4, "31 dBA"), ("humidity", 45.0, "45%"),
    ("conductivity", 12.34, "12.3 µS/cm"),
])
def test_new_units(kind, value, expected):
    assert format_value(kind, value) == expected
    assert kind in UNITS and UNITS[kind]


def test_fahrenheit():
    assert format_value("temperature", 60.0, True) == "140°F"


def test_stale_helpers():
    ok = Snapshot(statuses={"native": SourceStatus("native", True, "ok")})
    stale = Snapshot(statuses={"native": SourceStatus("native", True, "stale", "x")})
    assert not is_stale(ok) and is_stale(stale) and source_reason(stale) == "stale"
    assert source_reason(Snapshot()) == ""


def test_timing_and_big_units():
    assert UNITS["timing"] == "ns"
    assert format_value("timing", 13.75) == "13.75 ns"
    assert format_value("data", 2048.0) == "2.00 TB"
    assert format_value("data", 500.1) == "500.1 GB"
    assert format_value("smalldata", 2048.0) == "2.0 GB"
    assert format_value("smalldata", 807.0) == "807 MB"
    assert format_value("factor", 45117.0) == "45117"
    assert format_value("factor", 41.25) == "41.25"
    assert format_value("timing", None) == "—"
