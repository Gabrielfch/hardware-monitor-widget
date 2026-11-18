"""Tiny translation table (Spanish when the system locale starts with "es", otherwise English)."""
from __future__ import annotations

import locale
import sys

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "app_name": "Hardware Monitor",
        "panel_title": "Hardware",
        "no_data": "No data",
        "status_needs_admin": "Administrator rights are required for CPU temperatures",
        "status_error": "Sensors unavailable: {detail}",
        "status_error_plain": "Sensors unavailable",
        "status_stale": "Data not updated: {detail}",
        "status_stale_plain": "Data not updated",
        "stale_tip": "Data not updated",
        "loading": "Reading sensors…",
        "critical": "critical",
        "pill_no_data_admin": "No data: administrator rights are required. Click to open the panel",
        "pill_no_data": "No data. Click to open the panel",
        "pill_no_pins": "No sensor pinned. Open the panel and press the eye",
        "pill_loading": "Reading sensors…",
        "pill_more": "{n} more sensors. Click to open the panel",
        "kind_temperature": "temperature", "kind_load": "load", "kind_level": "level",
        "kind_control": "control", "kind_clock": "clock", "kind_power": "power",
        "kind_voltage": "voltage", "kind_current": "current", "kind_energy": "energy",
        "kind_frequency": "frequency", "kind_timespan": "time", "kind_noise": "noise",
        "kind_humidity": "humidity", "kind_conductivity": "conductivity", "kind_fan": "fan",
        "kind_flow": "flow", "kind_data": "data", "kind_smalldata": "data",
        "kind_throughput": "throughput", "kind_factor": "factor",
        "restart_admin": "Restart as administrator",
        "retry": "Retry",
        "min_max": "min {min} · max {max}",
        "min": "min",
        "max": "max",
        "pin_on": "Shown in the taskbar. Click to remove",
        "pin_off": "Click to show in the taskbar",
        "pin_name": "Show {name} in the taskbar",
        "collapse": "Collapse {name}",
        "expand": "Expand {name}",
        "refresh": "Refresh",
        "fahrenheit": "Show °F",
        "celsius": "Show °C",
        "start_with_windows": "Start with Windows",
        "quit": "Quit",
        "theme_to_light": "Switch to light theme",
        "theme_to_dark": "Switch to dark theme",
        "menu_open": "Open panel",
        "menu_refresh": "Refresh",
        "menu_theme": "Theme",
        "theme_auto": "Automatic",
        "theme_light": "Light",
        "theme_dark": "Dark",
        "menu_start": "Start with Windows",
        "menu_quit": "Quit",
        "hw_cpu": "CPU",
        "hw_gpu": "GPU",
        "hw_ram": "Memory",
        "hw_mainboard": "Motherboard",
        "hw_storage": "Storage",
        "hw_network": "Network",
        "hw_other": "Other",
        "tip_hardware": "Hardware",
        "tip_sensor": "Sensor",
        "tip_value": "Value",
        "menu_pill_unpin": "Remove from the taskbar",
        "menu_pill_open": "Open panel",
        "menu_pill_quit": "Quit",
        "empty": "No sensors available yet",
        "hw_vram": "Virtual memory",
        "hw_dimms": "Memory modules ({n})",
        "kind_timing": "timing",
        "grp_cores": "Cores ({n})", "grp_buses": "Buses and clocks", "grp_electrical": "Voltages and currents",
        "grp_d3d": "Direct3D", "grp_controls": "Fan controls", "grp_thresholds": "Thresholds",
        "grp_capacity": "Capacity", "grp_activity": "Activity", "grp_health": "Health",
        "grp_timings": "Timings",
        "cat_temps_loads": "Temperatures and loads", "cat_fans": "Fans",
        "cat_clocks_power": "Clocks and power", "cat_memory_disks": "Memory and disks",
        "cat_other": "Other",
    },
    "es": {
        "app_name": "Monitor de hardware",
        "panel_title": "Hardware",
        "no_data": "Sin datos",
        "status_needs_admin": "Se necesitan permisos de administrador para las temperaturas de CPU",
        "status_error": "Sensores no disponibles: {detail}",
        "status_error_plain": "Sensores no disponibles",
        "status_stale": "Datos no actualizados: {detail}",
        "status_stale_plain": "Datos no actualizados",
        "stale_tip": "Datos no actualizados",
        "loading": "Leyendo sensores…",
        "critical": "crítico",
        "pill_no_data_admin": "Sin datos: se necesitan permisos de administrador. Clic para abrir el panel",
        "pill_no_data": "Sin datos. Clic para abrir el panel",
        "pill_no_pins": "Ningún sensor fijado. Abre el panel y pulsa el ojo",
        "pill_loading": "Leyendo sensores…",
        "pill_more": "{n} sensores más. Clic para abrir el panel",
        "kind_temperature": "temperatura", "kind_load": "carga", "kind_level": "nivel",
        "kind_control": "control", "kind_clock": "reloj", "kind_power": "potencia",
        "kind_voltage": "tensión", "kind_current": "corriente", "kind_energy": "energía",
        "kind_frequency": "frecuencia", "kind_timespan": "tiempo", "kind_noise": "ruido",
        "kind_humidity": "humedad", "kind_conductivity": "conductividad", "kind_fan": "ventilador",
        "kind_flow": "caudal", "kind_data": "datos", "kind_smalldata": "datos",
        "kind_throughput": "velocidad", "kind_factor": "factor",
        "restart_admin": "Reiniciar como administrador",
        "retry": "Reintentar",
        "min_max": "mín {min} · máx {max}",
        "min": "mín",
        "max": "máx",
        "pin_on": "Se muestra en la barra de tareas. Clic para quitarlo",
        "pin_off": "Clic para mostrarlo en la barra de tareas",
        "pin_name": "Mostrar {name} en la barra de tareas",
        "collapse": "Contraer {name}",
        "expand": "Expandir {name}",
        "refresh": "Actualizar",
        "fahrenheit": "Mostrar °F",
        "celsius": "Mostrar °C",
        "start_with_windows": "Iniciar con Windows",
        "quit": "Salir",
        "theme_to_light": "Cambiar al tema claro",
        "theme_to_dark": "Cambiar al tema oscuro",
        "menu_open": "Abrir panel",
        "menu_refresh": "Actualizar",
        "menu_theme": "Tema",
        "theme_auto": "Automático",
        "theme_light": "Claro",
        "theme_dark": "Oscuro",
        "menu_start": "Iniciar con Windows",
        "menu_quit": "Salir",
        "hw_cpu": "CPU",
        "hw_gpu": "GPU",
        "hw_ram": "Memoria",
        "hw_mainboard": "Placa base",
        "hw_storage": "Almacenamiento",
        "hw_network": "Red",
        "hw_other": "Otros",
        "tip_hardware": "Hardware",
        "tip_sensor": "Sensor",
        "tip_value": "Valor",
        "menu_pill_unpin": "Quitar de la barra de tareas",
        "menu_pill_open": "Abrir panel",
        "menu_pill_quit": "Salir",
        "empty": "Aún no hay sensores disponibles",
        "hw_vram": "Memoria virtual",
        "hw_dimms": "Módulos de memoria ({n})",
        "kind_timing": "temporización",
        "grp_cores": "Núcleos ({n})", "grp_buses": "Buses y relojes", "grp_electrical": "Tensiones y corrientes",
        "grp_d3d": "Direct3D", "grp_controls": "Controles de ventilador", "grp_thresholds": "Umbrales",
        "grp_capacity": "Capacidad", "grp_activity": "Actividad", "grp_health": "Salud",
        "grp_timings": "Temporizaciones",
        "cat_temps_loads": "Temperaturas y cargas", "cat_fans": "Ventiladores",
        "cat_clocks_power": "Relojes y potencia", "cat_memory_disks": "Memoria y discos",
        "cat_other": "Otros",
    },
}

LANGUAGES = {"es": "Español", "en": "English"}
SETTING_AUTO = "auto"

# Text tables of other features (ADR-4). Each module exposes STRINGS = {"es": {...}, "en": {...}}.
# Static imports so PyInstaller finds them; modules that do not exist yet are skipped.
EXTENSION_MODULES = ("strings_appearance", "strings_icons", "strings_update", "strings_position",
                     "strings_panel", "strings_settings", "strings_integration",
                     "strings_about")


def _merge_extensions() -> None:
    import importlib
    for name in EXTENSION_MODULES:
        try:
            mod = importlib.import_module(f"{__package__}.{name}")
        except ImportError:
            continue
        table = getattr(mod, "STRINGS", None)
        if not isinstance(table, dict):
            continue
        for lang, entries in table.items():
            if lang in _STRINGS and isinstance(entries, dict):
                _STRINGS[lang].update(entries)


def _static_imports() -> None:   # pragma: no cover - only makes PyInstaller collect the modules
    try:
        from . import strings_appearance  # noqa: F401
    except ImportError:
        pass
    try:
        from . import strings_icons  # noqa: F401
    except ImportError:
        pass
    try:
        from . import strings_update  # noqa: F401
    except ImportError:
        pass
    try:
        from . import strings_position  # noqa: F401
    except ImportError:
        pass
    try:
        from . import strings_panel  # noqa: F401
    except ImportError:
        pass
    try:
        from . import strings_settings  # noqa: F401
    except ImportError:
        pass
    try:
        from . import strings_integration  # noqa: F401
    except ImportError:
        pass


_merge_extensions()

_lang: str | None = None


def system_language() -> str:
    loc = ""
    try:
        if sys.platform == "win32":
            import ctypes
            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                loc = buf.value
        if not loc:
            loc = locale.getlocale()[0] or ""
    except Exception:  # noqa: BLE001
        loc = ""
    return "es" if loc.lower().startswith("es") else "en"


def get_language() -> str:
    global _lang
    if _lang is None:
        _lang = system_language()
    return _lang


def set_language(lang: str | None) -> None:
    """Force "es"/"en" (None re-detects from the system locale)."""
    global _lang
    _lang = lang if lang in _STRINGS else None


def tr_in(lang: str, key: str, **kw) -> str:
    table = _STRINGS.get(lang, _STRINGS["en"])
    text = table.get(key) or _STRINGS["en"].get(key, key)
    return text.format(**kw) if kw else text


def decimal_sep() -> str:
    """Decimal separator of the UI language: comma in Spanish, point otherwise."""
    return "," if get_language() == "es" else "."


def tr(key: str, **kw) -> str:
    return tr_in(get_language(), key, **kw)


def apply_language_setting(pref: str | None) -> None:
    """Apply the `language` config value: "es"/"en" force it, anything else ("auto") follows the system."""
    set_language(pref if pref in LANGUAGES else None)
