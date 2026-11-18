# Hardware Monitor Widget — spec v1

Windows 11 taskbar widget that shows hardware sensors (temperatures, loads, clocks, power, fans...)
read by its own bundled sensor engine, with the look of *Taskbar Battery Widget* (pills next to the
notification area + a dark/light card panel). Package `hardware_monitor_widget`, Python 3.14 venv at
`.venv`, PySide6 6.12, pywin32.

## Data source
The app reads hardware itself: a bundled sensor host (`native/SensorHost`, .NET 8,
LibreHardwareMonitorLib 0.9.6, MPL-2.0) is published to `hardware_monitor_widget/native/sensorhost/`
by `native/build.ps1` and launched as a child process; it writes one JSON line per tick to stdout
(`hello`, `snapshot`, `error`) and reads `interval <ms>`, `refresh`, `quit` on stdin (EOF ⇒ exit).
CPU temperatures need admin + the PawnIO driver (installed separately, never WinRing0); the app
relaunches itself elevated via UAC at start (`--no-elevate` skips). Sensor keys are
`hw/<LHM Identifier>`, single source `native`. No other monitoring application is required.

## Acceptance criteria
1. The panel lists every detected hardware (CPU, GPU, RAM, mainboard, storage) with its sensors
   grouped by hardware, value + min/max, correct unit per sensor type; values update every
   `interval_ms` (default 1000).
2. When the sensor host cannot read everything (no admin, PawnIO missing, host stalled or failed),
   a status card explains why and offers *Restart as administrator* or *Retry*; the app never
   crashes and restarts the host with backoff.
3. Taskbar pills (style of Taskbar Battery Widget: icon + value + thin bar, rounded, band colour,
   red outline when critical) for the *pinned* sensors. Default pins: CPU temperature (Tctl/Tdie or
   Package), CPU load, GPU temperature, GPU load, RAM load. The eye button on each panel row
   pins/unpins it. Hidden over fullscreen apps.
4. Colour bands: temperature < 60 green, < 75 amber, < 85 orange, ≥ 85 red (°C); load/percent
   < 60 green, < 85 amber, < 95 orange, ≥ 95 red; other kinds neutral.
5. Panel: left/right click on tray icon or the pills opens it; sections per hardware with a
   coloured header; light/dark theme follows Windows by default.
6. Settings persisted in `%APPDATA%\HardwareMonitorWidget\config.json`; corrupt/missing ⇒ defaults.
7. UI in Spanish (system locale `es*`) or English.
8. `python -m pytest` passes; `python -m hardware_monitor_widget` runs; `python -m
   hardware_monitor_widget.probe` prints the current snapshot as text (diagnostics).

## Non-goals (v1)
Logging/history charts, alerts/notifications, writing fan control.

## Package layout (`hardware_monitor_widget/`)
```
models.py      Sensor, SourceStatus, Snapshot dataclasses; band helpers
config.py      Config(path=None).get/set, DEFAULTS
sensorhost.py  host_path(), parse_line(), snapshot_from_message(), SensorHostProcess
monitor.py     HardwareMonitor(QObject): snapshot_ready(object), start/stop/refresh/set_interval, watchdog
elevation.py   is_admin(), relaunch_as_admin(), ensure_admin_or_relaunch()
probe.py       CLI: prints a snapshot
-- UI --
taskbar.py, win_window.py   copied from Taskbar Battery Widget (taskbar geometry, fullscreen, z-order)
colors.py, icons.py, widget.py, panel.py, tray.py, app.py, i18n.py, startup.py
```

---
---

# Fase 2 (v2): resumen

Diseño completo en `docs/phase2-design.md`. Lo que añade la fase 2 sobre la v1:

- **Ajustes dentro del panel**: páginas navegables (apariencia, posición, sensores, iconos, colores, inicio con
  Windows, actualizaciones, acerca de). El clic izquierdo en bandeja/píldoras abre el resumen; el clic derecho
  y el engranaje abren Ajustes. La bandeja no tiene menú contextual.
- **Resumen curado** (`curation`, `curation_rules`): los sensores se agrupan por hardware con reglas por nombre
  de LHM; lo desconocido va al detalle. `DetailWindow` muestra todos los sensores. Pins y overrides son claves
  opacas. En discos, el resumen muestra Read/Write Activity; Total Activity solo en detalle con banda neutra.
- **Apariencia**: tema, escala de interfaz (90/100/115/130 %), idioma (auto/es/en), paletas y colores
  personalizados, iconos personalizados (solo primer fotograma), posición y ancla en la barra.
- **Configuración v2**: `config_version: 2`, migración desde la fase 1 sin perder claves, validación por clave.
- **Actualizaciones**: comprobación manual y automática (una vez al día, solo notifica), descarga verificada con
  firma Ed25519 + SHA-256 e instalación solo en modo instalado. Repositorio `Gabrielfch/hardware-monitor-widget`.
  Deshabilitado mientras no haya claves públicas embebidas.
- **Acerca de** (`about.py`): versión, modo (instalado/portable/dev), build de Windows, Python, PySide6/Qt,
  LibreHardwareMonitorLib, admin y PawnIO, escala e idioma; "Copiar info técnica" sin datos personales;
  licencias (`LICENSE` + `THIRD_PARTY_NOTICES.md`) en un diálogo de solo lectura.
- **Licencia**: MIT (`LICENSE`). Atribuciones en `THIRD_PARTY_NOTICES.md`.
- Los textos de cada función viven en módulos `strings_*.py` registrados en `i18n.EXTENSION_MODULES`.