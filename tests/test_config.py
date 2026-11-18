import json


def test_loaded_ok_flag(tmp_path):
    from hardware_monitor_widget.config import Config
    assert Config(tmp_path / "missing.json").loaded_ok is True        # first run: nothing to protect
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert Config(bad).loaded_ok is False
    lst = tmp_path / "list.json"
    lst.write_text("[1, 2]", encoding="utf-8")
    assert Config(lst).loaded_ok is False
import os
import subprocess

import pytest

from hardware_monitor_widget import config as cfgmod
from hardware_monitor_widget.config import Config, is_reparse_point


def test_roundtrip(tmp_path):
    p = tmp_path / "d" / "config.json"
    c = Config(p)
    c.set("fahrenheit", True)
    assert json.loads(p.read_text(encoding="utf-8"))["fahrenheit"] is True
    assert Config(p).get("fahrenheit") is True


def test_oversized_config_ignored(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"fahrenheit": True, "pad": "x" * (cfgmod.MAX_CONFIG_BYTES + 10)}), encoding="utf-8")
    assert Config(p).get("fahrenheit") is False


def test_junction_directory_not_written(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot create junction")
    try:
        assert is_reparse_point(link) and not is_reparse_point(target)
        c = Config(link / "config.json")
        with pytest.raises(cfgmod.ConfigSaveError):
            c.set("fahrenheit", True)
        assert os.listdir(target) == []
    finally:
        os.rmdir(link)   # removes only the junction


def _write(p, data):
    p.write_text(json.dumps(data), encoding="utf-8")


def test_phase1_config_migrates_without_losing_keys(tmp_path):
    p = tmp_path / "config.json"
    _write(p, {"pinned": ["k/a"], "theme": "dark", "fahrenheit": True, "collapsed": ["hw//x"], "interval_ms": 2000})
    c = Config(p)
    assert c.get("pinned") == ["k/a"] and c.get("theme") == "dark" and c.get("fahrenheit") is True
    assert c.get("collapsed") == ["hw//x"] and c.get("interval_ms") == 2000
    assert c.get("config_version") == 2 and c.get("ui_scale") == 100 and c.get("hidden_hardware") == []
    c.save()
    assert json.loads(p.read_text(encoding="utf-8"))["config_version"] == 2


def test_each_key_validated_independently(tmp_path):
    p = tmp_path / "config.json"
    _write(p, {"theme": "purple", "fahrenheit": True, "ui_scale": 77, "language": "es", "interval_ms": 5,
               "color_palette": "nope", "custom_colors": {"ok": "#abcdef", "warn": "red"},
               "hidden_hardware": ["hw//a", "bad", 3], "summary_categories": ["fans", "zzz"],
               "summary_overrides": {"k": True, "j": "x"}, "detail_geometry": {"x": 0, "y": 0, "w": 10, "h": 10},
               "pinned": ["a"] * 100, "update_last_check": -1})
    c = Config(p)
    assert c.get("theme") == "auto" and c.get("fahrenheit") is True and c.get("ui_scale") == 100
    assert c.get("language") == "es" and c.get("interval_ms") == 500 and c.get("color_palette") == "classic"
    assert c.get("custom_colors") == {"ok": "#ABCDEF", "warn": "#F5C842", "crit": "#FF9999"}
    assert c.get("hidden_hardware") == ["hw//a"] and c.get("summary_categories") == ["fans"]
    assert c.get("summary_overrides") == {"k": True} and c.get("detail_geometry") == {}
    assert len(c.get("pinned")) == 32 and c.get("update_last_check") == 0


def test_newer_version_loads_known_keys(tmp_path):
    p = tmp_path / "config.json"
    _write(p, {"config_version": 9, "fahrenheit": True, "future_key": 1})
    c = Config(p)
    assert c.get("fahrenheit") is True
    assert c.save() is False                          # read-only: the newer file is kept as is
    assert json.loads(p.read_text(encoding="utf-8"))["config_version"] == 9


@pytest.mark.parametrize("key,value", [
    ("theme", "x"), ("ui_scale", 101), ("fahrenheit", 1), ("nope", 1), ("pinned", ["a"] * 33),
    ("pinned", "a"), ("summary_overrides", {f"k{i}": True for i in range(1001)}),
    ("hidden_hardware", ["nohw"]), ("custom_colors", {"ok": "#12"}), ("detail_geometry", {"x": 1}),
    ("floating_positions", {"primary": {"anchor": "nowhere", "dx": 0, "dy": 0}}),
    ("custom_icons_sensor", {"k": "../evil.png"}), ("update_last_check", 4102444801),
    ("placement_monitor", "../x"), ("fullscreen_placement", "middle"),
])
def test_set_rejects_invalid(tmp_path, key, value):
    c = Config(tmp_path / "config.json")
    before = c.get(key)
    with pytest.raises(ValueError):
        c.set(key, value)
    assert c.get(key) == before


def test_set_accepts_valid_and_roundtrips(tmp_path):
    p = tmp_path / "config.json"
    c = Config(p)
    c.set("pinned", [f"k/{i}" for i in range(32)])
    c.set("floating_positions", {"primary": {"anchor": "top-left", "dx": 99999, "dy": -5}})
    c.set("custom_icons_sensor", {"hw/x": "0123456789abcdef.png"})
    c.set("detail_geometry", {"x": 10, "y": 20, "w": 800, "h": 600})
    c.set("summary_overrides", {"hw/s": False})
    c2 = Config(p)
    assert len(c2.get("pinned")) == 32
    assert c2.get("floating_positions") == {"primary": {"anchor": "top-left", "dx": 10000, "dy": -5}}
    assert c2.get("custom_icons_sensor") == {"hw/x": "0123456789abcdef.png"}
    assert c2.get("detail_geometry") == {"x": 10, "y": 20, "w": 800, "h": 600}


def test_get_returns_copy(tmp_path):
    c = Config(tmp_path / "config.json")
    c.get("pinned").append("zzz")
    assert "zzz" not in c.get("pinned")


# ---------------------------------------------------------------- hardened save / read-only newer versions
def test_save_returns_bool_and_leaves_no_temp_files(tmp_path):
    p = tmp_path / "d" / "config.json"
    c = Config(p)
    assert c.save() is True
    assert [f.name for f in p.parent.iterdir()] == ["config.json"]


def test_save_does_not_follow_a_planted_tmp_file(tmp_path, monkeypatch):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    p = tmp_path / "config.json"
    try:
        os.symlink(victim, tmp_path / "config.tmp")
    except (OSError, NotImplementedError):
        (tmp_path / "config.tmp").write_text("leftover")     # no symlink privilege: a plain file stands in
    c = Config(p)
    c.set("fahrenheit", True)
    assert victim.read_text() == "keep"
    assert json.loads(p.read_text(encoding="utf-8"))["fahrenheit"] is True


def test_save_uses_exclusive_unique_temp_name(tmp_path, monkeypatch):
    seen = []
    real = cfgmod.tempfile.mkstemp

    def spy(*a, **k):
        seen.append(k)
        return real(*a, **k)
    monkeypatch.setattr(cfgmod.tempfile, "mkstemp", spy)
    assert Config(tmp_path / "config.json").save() is True
    assert seen and seen[0]["prefix"] == "config-" and seen[0]["suffix"] == ".tmp"


def test_save_refuses_reparse_destination_or_directory(tmp_path, monkeypatch):
    p = tmp_path / "d" / "config.json"
    c = Config(p)
    assert c.save() is True
    monkeypatch.setattr(cfgmod, "is_reparse_point", lambda path: str(path) == str(p))
    assert c.save() is False
    monkeypatch.setattr(cfgmod, "is_reparse_point", lambda path: str(path) == str(p.parent))
    assert c.save() is False


def test_save_failure_cleans_temp_and_returns_false(tmp_path, monkeypatch):
    c = Config(tmp_path / "config.json")

    def boom(src, dst):
        raise PermissionError("denied")
    monkeypatch.setattr(cfgmod.os, "replace", boom)
    assert c.save() is False
    assert list(tmp_path.glob("config-*.tmp")) == []


def test_set_raises_config_save_error_and_keeps_old_value(tmp_path, monkeypatch):
    c = Config(tmp_path / "config.json")
    monkeypatch.setattr(cfgmod.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(cfgmod.ConfigSaveError) as e:
        c.set("fahrenheit", True)
    assert isinstance(e.value, ValueError)
    assert c.get("fahrenheit") is False


def test_newer_config_version_is_read_only_and_never_overwritten(tmp_path):
    p = tmp_path / "config.json"
    original = {"config_version": 9, "fahrenheit": True, "future_key": {"a": 1}}
    _write(p, original)
    before = p.read_bytes()
    c = Config(p)
    assert c.get("fahrenheit") is True and c.read_only is True
    assert c.save() is False
    with pytest.raises(cfgmod.ConfigSaveError):
        c.set("theme", "dark")
    assert p.read_bytes() == before


@pytest.mark.parametrize("exc", [RecursionError, MemoryError])
def test_load_survives_recursion_and_memory_errors(tmp_path, monkeypatch, exc):
    p = tmp_path / "config.json"
    _write(p, {"fahrenheit": True})

    def boom(*a, **k):
        raise exc()
    monkeypatch.setattr(cfgmod.json, "loads", boom)
    assert Config(p).get("fahrenheit") is False


def test_deeply_nested_json_does_not_crash(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("[" * 50000 + "]" * 50000, encoding="utf-8")
    assert Config(p).get("fahrenheit") is False
