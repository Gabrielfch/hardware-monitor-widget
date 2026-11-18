import pytest

from hardware_monitor_widget.curation import Prefs, summary_sections
from hardware_monitor_widget.models import Sensor
from hardware_monitor_widget.sensor_names import display_labels, display_name, uniquify
from tools.synthetic import load_real_snapshot


@pytest.fixture(scope="module")
def real():
    return load_real_snapshot()


def _s(hw_id, hw_kind, kind, name, key=None):
    return Sensor(key or f"{hw_id}/{kind}/{name}", "native", "HW", hw_id, hw_kind, kind, name, 1.0)


# (hw_id, hw_kind, kind, LHM name) -> expected Spanish name
SPANISH = [
    ("hw//amdcpu/0", "cpu", "temperature", "Core (Tctl/Tdie)", "CPU (Tctl/Tdie)"),
    ("hw//amdcpu/0", "cpu", "load", "CPU Total", "Carga total"),
    ("hw//amdcpu/0", "cpu", "clock", "Cores (Average)", "Frecuencia media"),
    ("hw//amdcpu/0", "cpu", "power", "Package", "Potencia del paquete"),
    ("hw//amdcpu/0", "cpu", "clock", "Core #3", "Núcleo #3"),
    ("hw//gpu-amd/0", "gpu", "temperature", "GPU Core", "Temperatura del núcleo"),
    ("hw//gpu-amd/0", "gpu", "temperature", "GPU Hot Spot", "Punto más caliente"),
    ("hw//gpu-amd/0", "gpu", "load", "GPU Core", "Carga del núcleo"),
    ("hw//gpu-amd/0", "gpu", "clock", "GPU Core", "Reloj del núcleo"),
    ("hw//gpu-amd/0", "gpu", "fan", "GPU Fan", "Ventilador"),
    ("hw//gpu-amd/0", "gpu", "power", "GPU Package", "Potencia"),
    ("hw//gpu-amd/0", "gpu", "smalldata", "GPU Memory Used", "VRAM usada"),
    ("hw//ram/0", "ram", "load", "Memory", "Uso"),
    ("hw//ram/0", "ram", "data", "Memory Used", "Usada"),
    ("hw//ram/0", "ram", "data", "Memory Available", "Disponible"),
    ("hw//vram", "ram", "load", "Memory", "Uso"),
    ("hw//lpc/nct6798d/0", "mainboard", "fan", "Fan #4", "Ventilador #4"),
    ("hw//lpc/nct6798d/0", "mainboard", "control", "Fan #4", "Ventilador #4"),
    ("hw//nvme/0", "storage", "temperature", "Composite Temperature", "Temperatura"),
    ("hw//nvme/0", "storage", "temperature", "Temperature", "Temperatura"),
    ("hw//nvme/0", "storage", "load", "Used Space", "Espacio usado"),
    ("hw//nvme/0", "storage", "load", "Read Activity", "Lectura"),
    ("hw//nvme/0", "storage", "load", "Write Activity", "Escritura"),
]


@pytest.mark.parametrize("hw_id,hw_kind,kind,name,want", SPANISH)
def test_spanish_names(hw_id, hw_kind, kind, name, want):
    assert display_name(_s(hw_id, hw_kind, kind, name), "es") == want


def test_unknown_names_fall_back_to_the_original():
    s = _s("hw//amdcpu/0", "cpu", "voltage", "Totally Unknown Sensor")
    assert display_name(s, "es") == display_name(s, "en") == "Totally Unknown Sensor"


def test_the_rule_needs_the_right_hardware_and_kind():
    assert display_name(_s("hw//nvme/0", "storage", "load", "CPU Total"), "es") == "CPU Total"
    assert display_name(_s("hw//amdcpu/0", "cpu", "fan", "Core (Tctl/Tdie)"), "es") == "Core (Tctl/Tdie)"


def test_english_names_are_readable():
    assert display_name(_s("hw//gpu-amd/0", "gpu", "temperature", "GPU Hot Spot"), "en") == "Hot spot"
    assert display_name(_s("hw//gpu-amd/0", "gpu", "smalldata", "GPU Memory Used"), "en") == "VRAM used"
    assert display_name(_s("hw//ram/0", "ram", "data", "Memory Available"), "en") == "Available"
    assert display_name(_s("hw//amdcpu/0", "cpu", "load", "CPU Total"), "en") == "Total load"


def test_every_summary_sensor_of_the_real_fixture_is_translated(real):
    changed = 0
    for sec in summary_sections(real, Prefs()):
        for s in sec.sensors:
            es, en = display_name(s, "es"), display_name(s, "en")
            assert es and en and not es.startswith("sn_") and not en.startswith("sn_")
            changed += es != s.name
    assert changed >= 28          # nearly the whole summary reads in Spanish (the exceptions keep their name)


def test_labels_add_the_type_only_when_two_names_collide():
    a = _s("hw//amdcpu/0", "cpu", "clock", "Core #1")
    b = _s("hw//amdcpu/0", "cpu", "power", "Core #1")
    c = _s("hw//amdcpu/0", "cpu", "load", "CPU Total")
    labels = display_labels([a, b, c], "es")
    assert labels[a.key] == "Núcleo #1 · reloj" and labels[b.key] == "Núcleo #1 · potencia"
    assert labels[c.key] == "Carga total"


def test_uniquify_numbers_only_the_duplicates():
    assert uniquify(["a", "b", "a", "c", "a"]) == ["a (1)", "b", "a (2)", "c", "a (3)"]
    assert uniquify([]) == []
