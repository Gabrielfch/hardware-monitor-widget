"""Per-sensor thresholds, overheating alerts, stacked height limit and the reset-order setting."""
import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from hardware_monitor_widget import i18n, strings_settings
from hardware_monitor_widget.alerts import AlertManager, alert_sensors
from hardware_monitor_widget.config import Config, DEFAULTS, clean_value
from hardware_monitor_widget.curation import Prefs
from hardware_monitor_widget.detail_window import DetailWindow
from hardware_monitor_widget.models import (Sensor, band_for, default_limits, limits_for,
                                            set_threshold_overrides)
from hardware_monitor_widget.settings_model import SettingsCallbacks, build_settings_model
from hardware_monitor_widget.thresholds_dialog import RESET_CODE, ThresholdsDialog
from hardware_monitor_widget.widget import TaskbarWidget
from tools.synthetic import make_snapshot

KEY = "hw//amdcpu/0/temperature/2"


# ------------------------------------------------------------------ models / config
def test_band_for_uses_the_override_only_for_its_sensor():
    assert band_for("temperature", 70.0, KEY) == "amber"
    set_threshold_overrides({KEY: [50, 60, 65]})
    assert band_for("temperature", 70.0, KEY) == "red"
    assert band_for("temperature", 70.0, "other") == "amber"
    assert band_for("temperature", 70.0) == "amber"
    assert limits_for("temperature", KEY) == (50.0, 60.0, 65.0)
    assert limits_for("fan", KEY) is None and default_limits("fan") is None
    set_threshold_overrides({})
    assert band_for("temperature", 70.0, KEY) == "amber"


def test_config_thresholds_validation():
    ok = clean_value("thresholds", {KEY: [50, 60.5, 70]})
    assert ok == {KEY: [50.0, 60.5, 70.0]}
    for bad in ([60, 50, 70], [1, 2], [1, 2, 3, 4], [1, 2, "x"], [1, 2, True], [-1, 2, 3], [1, 2, 500],
                [1, 2, float("nan")], [5, 5, 6]):
        with pytest.raises(ValueError):
            clean_value("thresholds", {KEY: bad})
        assert clean_value("thresholds", {KEY: bad}, strict=False) == {}
    with pytest.raises(ValueError):
        clean_value("thresholds", [1])
    assert DEFAULTS["thresholds"] == {} and DEFAULTS["alerts_enabled"] is True
    assert (DEFAULTS["alert_seconds"], DEFAULTS["alert_cooldown_min"]) == (10, 5)
    assert clean_value("alert_seconds", 0) == 1 and clean_value("alert_cooldown_min", 10 ** 6) == 1440
    with pytest.raises(ValueError):
        clean_value("alert_seconds", True)


def test_config_thresholds_roundtrip(tmp_path):
    cfg = Config(tmp_path / "c.json")
    cfg.set("thresholds", {KEY: [40, 50, 60]})
    assert Config(tmp_path / "c.json").get("thresholds") == {KEY: [40.0, 50.0, 60.0]}


# ------------------------------------------------------------------ alerts
class Cfg(dict):
    def get(self, k, d=None):
        return self[k] if k in self else DEFAULTS[k]


def hot(value=95.0, key=KEY):
    return Sensor(key=key, source="native", hardware="CPU", hw_id="hw//cpu", hw_kind="cpu", kind="temperature",
                  name="Core", value=value)


def test_alert_fires_after_n_seconds_in_red_then_waits_the_cooldown():
    m = AlertManager(Cfg())
    assert m.check([hot()], 0) == []
    assert m.check([hot()], 9.9) == []
    got = m.check([hot()], 10)
    assert len(got) == 1 and "95°C" in got[0][1] and "CPU · Core" in got[0][1]
    assert m.check([hot()], 20) == [] and m.check([hot()], 309) == []
    assert len(m.check([hot()], 310)) == 1                       # cooldown (5 min) elapsed, still red


def test_alert_requires_continuous_red_and_resets_when_it_cools():
    m = AlertManager(Cfg())
    m.check([hot()], 0)
    assert m.check([hot(50.0)], 8) == []                          # cooled: the spell restarts
    assert m.check([hot()], 9) == [] and m.check([hot()], 18) == []
    assert len(m.check([hot()], 19)) == 1


def test_alert_disabled_stale_and_missing_sensors():
    c = Cfg(alerts_enabled=False)
    m = AlertManager(c)
    assert m.check([hot()], 0) == [] and m.check([hot()], 50) == []
    c["alerts_enabled"] = True
    assert m.check([hot()], 60) == []                             # re-enabling does not replay the old spell
    assert m.check([hot()], 80, stale=True) == []
    assert m.check([], 90) == [] and m.check([hot()], 91) == []   # sensor vanished: spell cleared


def test_alert_custom_times_threshold_override_and_fahrenheit():
    m = AlertManager(Cfg(alert_seconds=3, alert_cooldown_min=1, fahrenheit=True))
    set_threshold_overrides({KEY: [30, 40, 50]})
    m.check([hot(55.0)], 0)
    got = m.check([hot(55.0)], 3)
    assert len(got) == 1 and "131°F" in got[0][1]
    assert m.check([hot(55.0)], 63)
    set_threshold_overrides({})
    m2 = AlertManager(Cfg())
    m2.check([hot(70.0)], 0)
    assert m2.check([hot(70.0)], 100) == []                       # amber by default: no alert


def test_alert_sensors_are_summary_or_pinned_temperatures_only():
    snap = make_snapshot()
    out = alert_sensors(snap, Prefs(), ["auto:cpu_temp", "auto:cpu_load"])
    assert out and all(s.kind == "temperature" for s in out)
    assert len({s.key for s in out}) == len(out)
    assert any(s.key == KEY for s in out)
    extra = alert_sensors(snap, Prefs(), ["hw//gpu-amd/0/temperature/1"])
    assert any(s.key == "hw//gpu-amd/0/temperature/1" for s in extra)


def test_new_texts_have_es_en_parity():
    for key in ("alert_title", "alert_body", "thr_menu", "thr_title", "reset_order_confirm", "set_menu_alerts"):
        assert key in strings_settings.STRINGS["es"] and key in strings_settings.STRINGS["en"]
    assert set(strings_settings.STRINGS["es"]) == set(strings_settings.STRINGS["en"])
    i18n.set_language("en")
    try:
        assert "{name}" not in i18n.tr("alert_body", name="CPU", value="1")
    finally:
        i18n.set_language("es")


# ------------------------------------------------------------------ settings model
def test_alerts_page_and_reset_order_action():
    calls = []
    cb = SettingsCallbacks(set_alerts=lambda v: calls.append(("on", v)),
                           set_alert_seconds=lambda n: calls.append(("s", n)),
                           set_alert_cooldown=lambda n: calls.append(("c", n)),
                           reset_order=lambda: calls.append("reset"), has_order=lambda: True)
    m = build_settings_model(cb)
    page = m.find("alerts")
    assert [n.id for n in page.items()] == ["alerts_enabled", "alert_seconds", "alert_cooldown"]
    page.items()[0].set(False)
    page.items()[1].set(30)
    page.items()[2].set(15)
    assert [o.value for o in page.items()[1].options()] == [5, 10, 30, 60, 120]
    assert page.items()[1].current_label() == "10 s"
    act = m.find("reset_order")
    assert act.enabled()
    act.run()
    assert calls == [("on", False), ("s", 30), ("c", 15), "reset"]
    assert not build_settings_model(SettingsCallbacks()).find("reset_order").enabled()


# ------------------------------------------------------------------ detail window
@pytest.fixture
def win(qapp):
    w = DetailWindow(dark=True)
    w.set_snapshot(make_snapshot())
    w.show()
    yield w
    w.close()


def test_thresholds_dialog_validation_and_reset(qapp):
    d = ThresholdsDialog("CPU", (60, 75, 85), "°C")
    assert d.values() == [60.0, 75.0, 85.0] and d.is_valid()
    d.boxes[0].setValue(80)
    assert not d.is_valid() and d.error.text()
    assert not d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    d.boxes[0].setValue(55)
    assert d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    d.reset_btn.click()
    assert d.result() == RESET_CODE


def test_edit_thresholds_emits_values_or_reset(win, monkeypatch):
    got = []
    win.thresholds_changed.connect(lambda k, v: got.append((k, v)))

    def accept_with_red_90(self):
        self.boxes[2].setValue(90)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ThresholdsDialog, "exec", accept_with_red_90)
    win.edit_thresholds(KEY)
    assert got == [(KEY, [60.0, 75.0, 90.0])]
    monkeypatch.setattr(ThresholdsDialog, "exec", lambda self: RESET_CODE)
    win.edit_thresholds(KEY)
    assert got[-1] == (KEY, None)
    monkeypatch.setattr(ThresholdsDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    win.edit_thresholds(KEY)
    assert len(got) == 2
    win.edit_thresholds("hw//gpu-amd/0/fan/0")                    # no bands for fans: nothing opens
    assert len(got) == 2
    assert win.can_edit_thresholds(KEY) and not win.can_edit_thresholds("hw//gpu-amd/0/fan/0")


def test_detail_value_follows_override(win):
    assert win._cache[KEY][3] == "amber"
    set_threshold_overrides({KEY: [10, 20, 30]})
    win.retranslate()
    assert win._cache[KEY][3] == "red" and win._items[KEY].text(1).startswith("! ")


# ------------------------------------------------------------------ pills
def test_pill_uses_the_override(qapp):
    w = TaskbarWidget(["auto:cpu_temp"])
    w.set_grouped(False)
    w.set_snapshot(make_snapshot(cpu_temp=70.0))
    assert w.pill_bands() == ["amber"]
    set_threshold_overrides({KEY: [10, 20, 30]})
    w.set_snapshot(make_snapshot(cpu_temp=70.0))
    assert w.pill_bands() == ["red"]
    w.close()
