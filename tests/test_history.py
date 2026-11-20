import os
import subprocess
from datetime import datetime, timedelta

import pytest

from hardware_monitor_widget import history as hist
from hardware_monitor_widget.history import History, plot_points, stats
from hardware_monitor_widget.history_chart import HistoryChart, value_range

T0 = datetime(2026, 5, 10, 12, 0, 0).timestamp()


def make(tmp_path, **kw):
    return History(tmp_path / "local" / "history", clock=lambda: T0, **kw)


def test_minute_mean_and_roundtrip(tmp_path):
    h = make(tmp_path)
    h.record({"a": 10, "b": 1}, T0 + 1)
    h.record({"a": 20}, T0 + 30)
    assert h.series("a", 3600, T0 + 40) == [(T0, 15.0)]          # minute in progress included
    h.record({"a": 30}, T0 + 61)                                  # new minute: previous one is stored
    assert h.revision == 1
    assert h.series("a", 3600, T0 + 62) == [(T0, 15.0), (T0 + 60, 30.0)]
    h.flush()
    assert [f.name for f in h.dir.glob("*.csv")] == ["2026-05-10.csv"]
    h2 = History(h.dir, clock=lambda: T0 + 120)                  # reload from disk (memory + 24 h read)
    assert h2.series("a", 3600, T0 + 120) == [(T0, 15.0), (T0 + 60, 30.0)]
    assert h2.series("a", 86400, T0 + 120) == [(T0, 15.0), (T0 + 60, 30.0)]
    assert h2.series("b", 86400, T0 + 120) == [(T0, 1.0)]
    h2.record({"a": 5}, T0 + 130)
    h2.flush()                                                   # keys keep their index across restarts
    assert h2.series("a", 86400, T0 + 140) == [(T0, 15.0), (T0 + 60, 30.0), (T0 + 120, 5.0)]


def test_ignores_bad_values_and_corrupt_lines(tmp_path):
    h = make(tmp_path)
    h.record({"a": float("nan"), "b": float("inf"), "c": True, "d": 4}, T0)
    h.flush()
    p = next(h.dir.glob("*.csv"))
    with open(p, "a", encoding="utf-8") as f:
        f.write("garbage\n1,2\nx,y,z\n")
    assert h.series("d", 86400, T0 + 60) == [(T0, 4.0)]
    assert h.series("a", 86400, T0 + 60) == []


def test_disabled_records_nothing(tmp_path):
    h = make(tmp_path, enabled=False)
    h.record({"a": 1}, T0)
    h.record({"a": 1}, T0 + 70)
    assert not h.dir.exists() and h.series("a", 3600, T0 + 80) == []


def test_rotation_by_age_and_cap(tmp_path, monkeypatch):
    h = make(tmp_path)
    h.dir.mkdir(parents=True)
    today = datetime.fromtimestamp(T0).date()
    for n in (0, 5, 29, 30, 31, 90):
        (h.dir / f"{(today - timedelta(days=n)).isoformat()}.csv").write_text("x\n")
    (h.dir / "notes.txt").write_text("keep")
    h.prune()
    names = sorted(p.name for p in h.dir.iterdir())
    assert "notes.txt" in names
    assert (today - timedelta(days=31)).isoformat() + ".csv" not in names
    assert (today - timedelta(days=90)).isoformat() + ".csv" not in names
    assert (today - timedelta(days=30)).isoformat() + ".csv" in names
    monkeypatch.setattr(hist, "MAX_TOTAL_BYTES", 25)
    for p in h.dir.glob("*.csv"):
        p.write_text("0123456789\n")                             # 11 bytes each
    h.prune()
    left = sorted(p.name for p in h.dir.glob("*.csv"))
    assert len(left) == 2 and left[-1] == today.isoformat() + ".csv"      # the newest survive


def test_clear(tmp_path):
    h = make(tmp_path)
    h.record({"a": 1}, T0)
    h.flush()
    assert list(h.dir.glob("*.csv"))
    h.clear()
    assert not list(h.dir.glob("*.csv")) and h.series("a", 3600, T0 + 60) == []


def _junction(link, target):
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot create junction")


def test_junction_directory_is_never_written_or_read(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "2026-05-10.csv").write_text("@0,a\n%d,0,99.00\n" % int(T0))
    base = tmp_path / "local"
    base.mkdir()
    link = base / "history"
    _junction(link, target)
    try:
        h = History(link, clock=lambda: T0)
        h.record({"a": 1}, T0)
        h.record({"a": 1}, T0 + 70)
        assert h.series("a", 86400, T0 + 80) == [(T0 + 60, 1.0)]   # only the minute in progress
        assert sorted(p.name for p in target.iterdir()) == ["2026-05-10.csv"]
        assert (target / "2026-05-10.csv").read_text().count("\n") == 2
        h.clear()
        assert (target / "2026-05-10.csv").exists()
    finally:
        os.rmdir(link)


def test_junction_day_file_is_skipped(tmp_path):
    h = make(tmp_path)
    h.dir.mkdir(parents=True)
    target = tmp_path / "t"
    target.mkdir()
    link = h.dir / "2026-05-10.csv"
    _junction(link, target)
    try:
        h.record({"a": 1}, T0)
        h.flush()
        assert list(target.iterdir()) == []
    finally:
        os.rmdir(link)


def test_stats_and_plot_points():
    assert stats([]) is None
    assert stats([(0, 1.0), (1, 3.0), (2, 8.0)]) == (1.0, 8.0, 4.0)
    assert plot_points([], 0, 100, 200, 100) == []
    assert plot_points([(50, 1)], 0, 0, 200, 100) == []
    runs = plot_points([(0, 0.0), (60, 10.0), (120, 5.0)], 0, 120, 120, 100)
    assert runs == [[(0.0, 100.0), (60.0, 0.0), (120.0, 50.0)]]
    gap = plot_points([(0, 1.0), (60, 2.0), (1000, 3.0)], 0, 1200, 120, 10)
    assert [len(r) for r in gap] == [2, 1]                       # long hole breaks the line
    flat = plot_points([(0, 5.0), (60, 5.0)], 0, 60, 60, 10)
    assert all(0 <= y <= 10 for r in flat for _x, y in r)
    assert [x for r in plot_points([(-5, 1.0), (10, 1.0)], 0, 20, 20, 10) for x, _ in r] == [10.0]


def test_chart_widget(qapp):
    c = HistoryChart()
    c.resize(400, 160)
    c.set_data([], "temperature", False, True, 3600, T0)
    assert c.runs(300, 100) == [] and value_range([]) is None
    c.grab()                                                     # paints the empty state without errors
    c.set_data([(T0 - 120, 40.0), (T0 - 60, 50.0), (T0, 60.0)], "temperature", True, False, 3600, T0)
    runs = c.runs(300, 100)
    assert len(runs) == 1 and len(runs[0]) == 3 and runs[0][-1][0] == 300
    c.grab()


def test_detail_panel(qapp, tmp_path):
    from hardware_monitor_widget.detail_window import DetailWindow
    from tools.synthetic import make_snapshot
    snap = make_snapshot()
    s = next(x for x in snap.sensors if x.kind == "temperature")
    h = History(tmp_path / "local" / "history")
    h.record({s.key: 40.0})
    w = DetailWindow(dark=True)
    w.set_snapshot(snap)
    w.set_history(h)
    w.show()
    assert not w.hist_panel.isVisible()                          # nothing selected
    w.tree.setCurrentItem(w._items[s.key])
    assert w.hist_panel.isVisible()
    assert "40" in w.hist_chart.accessibleName()
    w.set_history_period("7d")
    assert w.hist_buttons["7d"].isChecked() and not w.hist_buttons["1h"].isChecked()
    w.set_fahrenheit(True)
    assert "104" in w.hist_stats.text()
    h.set_enabled(False)
    w.set_history(h)
    assert not w.hist_panel.isVisible()
    w.close()
