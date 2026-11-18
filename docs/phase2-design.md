# Fase 2 - Hardware Monitor Widget: diseño

Solo lectura: no escribí nada en disco. Leí `docs/*`, el paquete `hardware_monitor_widget/`, `sensors-admin.jsonl` y los archivos de la referencia (`settings_*`, `tray`, `device_panel`, `colors`, `custom_*`, `placement`, `widget`, `updater`, `ed25519`, `about`, `tools/signing`).

Hallazgos que condicionan el diseño:
- `Sensor.hw_id` es `"hw/" + id LHM` (por ejemplo `hw//ram`, `hw//vram`, `hw//memory/dimm/2`, `hw//lpc/nct6798d/0`) y `Sensor.key` es `"hw/" + id` del sensor, basado en índice. El snapshot real **no** distingue RAM física de virtual en `hw_kind` (ambas son `ram`). La curación debe derivar una clase de hardware a partir del prefijo de `hw_id`.
- Hay nombres duplicados dentro del mismo hardware: `Fan #2` es `Control` y `Fan`, `GPU Fan` es `Fan` y `Control`, `GPU Core` es temperatura, reloj, voltaje y carga, y `D3D Copy` aparece dos veces. Las reglas deben casar por (clase de hardware, kind, nombre), nunca solo por nombre.
- `pins.MAX_PINS = 12` y el recorte `[:12]` de `config.py` son insuficientes. El resumen elegido por el usuario da 28 sensores en su equipo.
- El proceso corre **elevado** (`requireAdministrator`) y lee/escribe datos en carpetas escribibles por el usuario (`%APPDATA%`, `%LOCALAPPDATA%`, `%TEMP%`). Esto agrava el updater (instalador ejecutado como admin) y los iconos personalizados (decodificación de imágenes con privilegios). Ver ADR-5 y ADR-6.
- `installer.iss` solo relanza la app con `postinstall skipifsilent`, así que una actualización silenciosa no la relanza. Falta el equivalente a `/LAUNCHAPP=1` de la referencia.
- `win_window.py` y `taskbar.py` ya traen `set_click_through`, `shift_down`, `monitor_devices/monitor_key`, `is_fullscreen_on_monitor` y `docked_edge`. La parte de placement de la referencia se puede portar sin tocar la capa Win32.

---

# A. Especificación

## A.1 Historias
- H1. Veo un resumen curado (temperaturas y cargas, ventiladores, relojes y potencia, memoria y discos) en vez de más de 300 sensores.
- H2. Una ventana de detalle redimensionable (árbol y buscador) me da todo lo demás y me deja fijar o añadir al resumen desde ahí.
- H3. Fijo o quito en bloque en la barra los sensores de un hardware desde su cabecera.
- H4. El clic derecho (píldoras o bandeja) abre el panel en Ajustes con páginas navegables, como Taskbar Battery Widget.
- H5. Personalizo paleta, iconos, escala, tamaño, tema, idioma y posición.
- H6. Pongo iconos propios por tipo de hardware, hardware o sensor.
- H7. Gestiono logs, inicio con Windows, Acerca de y actualizaciones firmadas.

## A.2 Criterios de aceptación (Given/When/Then, verificables)

**Curación (módulo puro `curation`)**
- **C1.** *Given* `tests/data/sensors-admin.jsonl` convertido con `sensorhost.snapshot_from_message` y `Prefs` por defecto, *When* `summary_sections()`, *Then* devuelve exactamente 28 sensores en este orden de hardware (cpu, gpu, ram, vram, mainboard, nvme, hdd, ssd):
  - CPU (4): `Core (Tctl/Tdie)` temperatura, `CPU Total` carga, `Cores (Average)` reloj, `Package` potencia.
  - GPU (7): `GPU Core` temperatura, `GPU Hot Spot`, `GPU Core` carga, `GPU Core` reloj, `GPU Package`, `GPU Fan` RPM, `GPU Memory Used`.
  - Memoria `/ram` (3) y Memoria virtual `/vram` (3): `Memory` carga, `Memory Used`, `Memory Available`.
  - Placa `/lpc/nct6798d/0` (2): `Fan #2` y `Fan #7` (RPM).
  - Cada disco (3, total 9): temperatura (`Composite Temperature` en nvme, `Temperature` en hdd/ssd), `Used Space`, `Total Activity`.
- **C2.** *Given* el mismo snapshot, *Then* ningún sensor de estos tipos está en el resumen: núcleos individuales (`CPU Core #n`, `Core #n*`), Factor, SMU, VID, SVI2, `Bus Speed`/`Fabric`/`Uncore`/`Memory` clock, TDC/EDC, `D3D *`, timings DIMM, `Power On *`, `Warning/Critical Temperature`, voltajes SuperIO, ningún `Control %`. El hardware sin sensores de resumen (`/memory/dimm/*`) no aparece como sección.
- **C3.** *Given* un ventilador con `value==0` y `max in (0,None)`, *Then* no está en el resumen. Si `value>0` o `max>0`, aparece sin reiniciar la app. Si tiene override `True`, aparece siempre. Sigue visible en el detalle.
- **C4.** *Given* snapshots sintéticos Intel (`CPU Package` temperatura y potencia, `Core Max`, `Cores (Average)`, `CPU Total`) y NVIDIA (`GPU Core`, `GPU Hot Spot`, `GPU Power`, `GPU Memory Used`, `GPU Fan`), *Then* esos sensores se clasifican como resumen.
- **C5.** *Given* cualquier sensor (incluido kind desconocido), `classify()` no lanza excepción y devuelve siempre una regla (la última es comodín). Los ids de regla son únicos. El resultado es determinista (misma entrada, misma salida).
- **C6.** *Given* una categoría desactivada en `Prefs.categories`, *Then* sus sensores salen del resumen. Un override `True` los devuelve y un override `False` quita sensores que sí estaban. El override gana sobre la categoría y la regla.
- **C7.** Las secciones del resumen se ordenan por `HW_KINDS` y, dentro de gpu/ram, por orden estable. "Memoria" (`/ram`) va antes que "Memoria virtual" (`/vram`). Cada GPU y cada disco es su propia sección.

**Agrupación y detalle**
- **G1.** *Given* el snapshot real, *Then* `detail_tree()` da a la CPU un grupo `cores` titulado "Núcleos (6)" (cuenta núcleos distintos por reloj/factor/SMU/VID, no los 12 hilos de carga), con las cargas por hilo dentro.
- **G2.** Existe un grupo virtual "Módulos de memoria (2)" solo en el árbol de detalle, con los dos DIMM y sus timings. Ningún DIMM aparece en el resumen.
- **G3.** `D3D *` va en el grupo `d3d` de la GPU. Voltajes SuperIO en `electrical`. `Control %` en `controls`. Umbrales de disco en `thresholds`.
- **D1.** La ventana de detalle es un `Qt.Window` redimensionable (mínimo 560x360), con árbol hardware > grupo > sensor y columnas Nombre, Valor, Mín, Máx, Fijado y Resumen. Geometría y nodos expandidos/colapsados se guardan en config.
- **D2.** *Given* texto en el buscador, *Then* se filtra sin distinguir mayúsculas por nombre de hardware, grupo, sensor y tipo. Las coincidencias se expanden. Sin resultados aparece "Sin resultados". Vaciar el texto restaura el estado de expansión anterior.
- **D3.** La casilla "Fijado" fija o quita un sensor. La casilla "Resumen" crea o elimina el override. Una casilla en un nodo hardware o grupo (tri-estado) fija/quita en bloque sus sensores.
- **D4.** Cada tick actualiza valores en sitio, sin reconstruir el árbol y sin perder scroll, selección ni filtro. Solo se actualiza si la ventana está visible. El árbol se reconstruye únicamente si cambia el conjunto de claves.
- **D5.** Navegación completa por teclado (flechas, Espacio sobre las casillas, `Ctrl+F` enfoca el buscador). Todo elemento tiene nombre accesible.

**Fijar en bloque**
- **B1.** La cabecera de cada hardware del resumen tiene un control con estado ninguno/algunos/todos. *When* se pulsa y no están todos fijados, *Then* se añaden los que faltan, en orden de resumen y sin duplicados. *When* están todos, *Then* se quitan todos, incluidos los cubiertos por pins `auto:*` (se quita la entrada `auto`, igual que `toggle_pin`).
- **B2.** `MAX_PINS = 32`. Si el bloque supera el tope, se añaden hasta el tope y el control muestra un aviso accesible "N no añadidos". El orden de píldoras se conserva y se persiste.

**Visualización (configuración)**
- **V1.** Hay un checkbox por hardware detectado, guardado por `hw_id` en `hidden_hardware`. Desmarcarlo oculta la sección del panel al instante y persiste tras reiniciar. No oculta sus píldoras ya fijadas.
- **V2.** Hay 5 categorías conmutables (`temps_loads`, `fans`, `clocks_power`, `memory_disks`, `other`), todas activas por defecto. Si el resumen queda vacío, el panel muestra un estado vacío con acceso a Visualización y al detalle.
- **V3.** "Restablecer resumen" borra `summary_overrides`. Está deshabilitado si no hay overrides.

**Ajustes (navegación y páginas)**
- **S1.** *When* clic derecho sobre las píldoras o el icono de bandeja, *Then* se abre el panel directamente en la raíz de Ajustes. El clic izquierdo abre el resumen. `Esc`, `Backspace` o `Alt+Izq.` retroceden un nivel. Desde la raíz, Atrás vuelve al resumen y un nuevo `Esc` cierra el panel.
- **S2.** Orden de la raíz de Ajustes: Visualización, Personalización, Posición, Iconos personalizados, Registros, (separador), Iniciar con Windows (check), Buscar actualizaciones, Acerca de. Los desplegables (Choice) se abren en el mismo sitio con radios, los checks conmutan en sitio y el panel se reajusta de alto.
- **S3.** Personalización:
  - Tema auto/claro/oscuro, paleta, estilo de icono, tamaño del panel (compact/normal/large), escala (90/100/115/130) e idioma (auto/es/en) se aplican en vivo y persisten.
  - Paleta `custom` abre el diálogo de 3 colores. Todo color resultante tiene contraste >= 4.5:1 contra los fondos de píldora (`ensure_contrast`).
  - A escala 90 % los objetivos táctiles siguen siendo >= 32 px.
- **S4.** Posición: derecha/izquierda/flotante, monitor, ancla (8), visible en pantalla completa y dónde, y restablecer.
  - En flotante se arrastra con umbral de 4 px y ajusta al ancla más cercana.
  - La posición se guarda por clave de monitor. Un monitor desconectado cae al primario.
  - "Izquierda" sin iconos centrados cae a la derecha y avisa una vez por sesión.
- **S5.** Iconos personalizados:
  - Acepta png/jpg/jpeg/bmp/ico/gif/webp/svg, <= 2 MiB, <= 2048 px y SVG <= 512 KiB sanitizado. Los límites y el saneado son los de la referencia.
  - Un archivo inválido muestra un error traducido y no rompe la app. Los GIF/WebP animados se validan con los límites de la referencia y se guarda solo el primer fotograma.
  - Precedencia: sensor > hardware > tipo de hardware. Un archivo ausente o corrupto cae al glifo vectorial.
  - Los archivos se guardan en una carpeta admin-only (ADR-6).
- **S6.** Registros: abrir `app.log` (se crea si falta), abrir la carpeta de logs, y "Nivel debug" que cambia el nivel raíz del logger al instante y persiste.
- **S7.** "Iniciar con Windows" refleja el estado real (`startup.is_enabled()`), conserva el comportamiento actual (tarea en modo instalado, Run en portable/dev) y muestra una pista del modo.
- **S8.** Acerca de muestra versión, modo de instalación, admin/PawnIO, versión de LibreHardwareMonitorLib, Python/Qt/Windows build, licencias (abre `THIRD_PARTY_NOTICES.md` local y `LICENSE`), "Copiar info técnica" y "Abrir carpeta de logs". La info copiada no contiene nombre de usuario ni rutas de perfil (se sustituyen por `%USERPROFILE%`).

**Actualizaciones**
- **U1.** Sin `REPO` ni claves configuradas, "Buscar actualizaciones" está deshabilitado con un motivo, sin tráfico de red.
- **U2.** La comprobación manual hace una petición HTTPS a `api.github.com/repos/<REPO>/releases/latest`, con timeout, tope de 2 MiB y `User-Agent` genérico con la versión. Estados visibles: comprobando, al día, disponible (con notas), error con motivo traducido.
- **U3.** La comprobación automática está **apagada por defecto**. Si se activa, hace como máximo una petición cada 24 h (`update_last_check`), con la primera a los 60 s del arranque. Solo notifica, nunca instala sola.
- **U4.** Instalar requiere confirmación explícita y solo se ofrece cuando se cumple todo esto:
  - `startup.install_mode()=="installed"`.
  - La release trae instalador, `SHA256SUMS.txt` y `.sig`.
  - Hay al menos una clave pública embebida.
  - Se puede crear la carpeta de descarga admin-only.
  - En otro caso se ofrece "Abrir página de la release".
- **U5.** Orden de verificación: (1) firma Ed25519 de `SHA256SUMS.txt`; (2) hash del instalador contra esa lista firmada; (3) re-hash justo antes de lanzar. Cualquier fallo borra la descarga y muestra el motivo. Tras un lanzamiento correcto, el instalador cierra la app, instala y la relanza elevada.

**Transversales**
- **X1.** Elevación, `--no-elevate`, tarea programada, comprobación DACL y límites de parseo existentes quedan intactos. Los tests actuales (`test_elevation`, `test_startup`, `test_sensorhost`, `test_coexistence`) siguen pasando.
- **X2.** Config:
  - Un `config.json` de fase 1 migra a `config_version: 2` sin perder `pinned`, `theme`, `fahrenheit`, `collapsed`, etc.
  - Cada clave se valida de forma independiente (un valor inválido cae a su default, no a todo).
  - Tamaño máximo de archivo 256 KiB, mapas con tope, y sigue sin escribir si el directorio es junction/symlink.
- **X3.** Convivencia: con Taskbar Battery Widget presente, las píldoras se acoplan a su izquierda (`docked_edge`). Con ancla izquierda se apartan a la derecha de los vecinos.
- **X4.** Sin red salvo comprobación manual u opt-in. Sin telemetría.
- **X5.** i18n: todas las claves existen en `es` y `en` (test de paridad). Solo es/en.
- **X6.** Rendimiento:
  - `classify` se cachea por (hw_class, kind, name).
  - El panel solo se reconstruye cuando cambia la firma estructural.
  - Con 4000 sensores sintéticos, `summary_sections` + `detail_tree` tardan < 100 ms (test con margen).

## A.3 Casos borde y errores esperados
- Sin admin o sin PawnIO, las temperaturas de CPU son `None`: se muestran como "—" y se mantiene la tarjeta de estado.
- Los sensores con valor `None` (`Read Rate`) se muestran como "—" y no rompen formatos.
- Hardware que aparece o desaparece (disco USB): lo nuevo es visible por defecto. Lo desaparecido conserva su entrada en `hidden_hardware`.
- Todo el hardware oculto, o todas las categorías apagadas: estado vacío explicativo.
- Overrides de sensores inexistentes: se ignoran. Con el tope alcanzado (1000), `set` lanza `ValueError` y la UI avisa. "Restablecer resumen" limpia.
- Dos GPU (iGPU y dGPU): secciones separadas. Los pins `auto:gpu_*` siguen resolviendo el primer `GPU Core` (comportamiento actual).
- Updater: sin red, 403/rate limit, JSON inválido, sin firma, hash distinto, disco lleno, cancelación, instalador bloqueado por Smart App Control (`is_policy_block`), app sin elevar (no puede crear la carpeta admin-only): todo cae a mensaje traducido y enlace a la página de la release.

## A.4 No funcionales
- **Seguridad:** ver ADR-5 y ADR-6. Los validadores de config acotan tipos y longitudes. El proceso elevado nunca interpreta datos de config como rutas ni comandos. Los pins y overrides se tratan como claves opacas.
- **Accesibilidad:** nombre y descripción accesibles en cada fila, anillo de foco solo con teclado, contraste >= 4.5:1, objetivos >= 32 px, estados no solo por color (mantiene "!" y contorno de críticos).
- **Observabilidad:** log INFO de migración de config, comprobaciones de update (resultado, sin cabeceras ni tokens) y cambios de placement. DEBUG para estadísticas de curación. Nada de nombres de usuario ni rutas de perfil en logs nuevos.

## A.5 Fuera de alcance
Instalación automática sin confirmación; iconos animados (primer fotograma solamente); histórico/gráficas/alertas; control de ventiladores; reglas de curación editables por el usuario (solo overrides por sensor); tutorial y "novedades" de la referencia; firma Authenticode; más idiomas que es/en; arrastrar para reordenar píldoras; perfiles o exportar/importar config.

---

# B. Arquitectura

## B.1 Mapa de módulos (nuevos = N, modificados = M)

```
config.py (M)            esquema v2: validadores por clave, migración, topes
models.py (M)            UNITS["timing"]="ns", format_value ampliado
i18n.py (M)              LANGUAGES, apply_language_setting, carga de strings_*.py
curation_rules.py (N)    tabla declarativa (datos puros)
curation.py (N)          motor: classify, Prefs, summary_sections, detail_tree (puro, sin Qt)
pins.py (M)              MAX_PINS=32, bulk_state, toggle_bulk
colors.py (M)            paletas, custom_palette, ensure_contrast, set_palette
icons.py (M)             glyph_icon, estilos de icono, set_icon_style
appearance.py (N)        apply_color_scheme, apply_appearance(cfg)
custom_colors_dialog.py (N)
dialogs.py (N)           app_dialog_guard (copiar)
custom_icons.py (N)      importar/validar/resolver (sin animación)
custom_icons_dialog.py (N)
placement.py (N)         matemática pura de posición (copiar + 3 ajustes)
widget.py (M)            placement, arrastre, pantalla completa, escala, iconos propios
panel.py (M)             resumen curado, cabecera con fijar en bloque, métricas, alojar Ajustes
detail_window.py (N)     ventana de detalle
settings_model.py (N)    árbol declarativo de Ajustes (puro)
settings_view.py (N)     vista de páginas dentro del panel
settings_actions.py (N)  implementa los callbacks del modelo (equivale a la parte de ajustes de tray.py)
about.py (N)             tech_info, UpdateController, AutoUpdateChecker (sin QDialog)
updater.py, ed25519.py, release_info.py (N)
tray.py (M)              solo icono, tooltip y activación (sin QMenu)
app.py (M)               cableado, nivel de log, controlador
strings_*.py (N)         una tabla es/en por tarea (evita conflictos en i18n.py)
tools/signing/gen_keys.py, sign_release.py (N)
```

Dependencias permitidas: `curation`, `curation_rules`, `pins`, `models`, `config` (validación), `settings_model`, `placement`, `updater` (la parte de red) y `ed25519` **no importan Qt** (salvo `updater` si `start_check` queda en `about.py`). `settings_view`, `panel`, `detail_window`, `widget` no importan `config.Config`: reciben datos y emiten señales. Solo `app.py` y `settings_actions.py` tocan `Config`.

## B.2 Tabla de curación (`curation_rules.py`)

```python
@dataclass(frozen=True)
class Rule:
    id: str                     # único, para tests y diagnóstico
    hw: tuple[str, ...] | str   # clases: cpu gpu ram vram dimm board storage network other, o "*"
    kind: tuple[str, ...] | str # kinds de sensor (minúsculas) o "*"
    name: str                   # regex, re.fullmatch IGNORECASE; "" = cualquiera
    category: str               # temps loads fans clocks power memory storage other | "auto" (por kind)
    level: str                  # "summary" | "detail"
    group: str = ""             # id de grupo de detalle ("" = suelto bajo el hardware)
    idle_hide: bool = False     # resumen solo si el sensor no está inactivo (value 0 y max 0/None)
    neutral_band: bool = False  # no pintar banda roja ni "!" (p. ej. actividad de disco)

def hw_class(hw_id: str, hw_kind: str) -> str
    # "hw//ram"->ram, "hw//vram"->vram, "hw//memory/dimm/N"->dimm; resto por hw_kind
    # (cpu->cpu, gpu->gpu, mainboard->board, storage->storage, network->network, otro->other)

KIND_CATEGORY = {"temperature":"temps", "load":"loads", "level":"storage"?? no -> "other",
                 "clock":"clocks", "factor":"clocks"(en cores)/"other", "power":"power", "voltage":"power",
                 "current":"power", "fan":"fans", "control":"fans", "data":"memory", "smalldata":"memory",
                 "timing":"memory", ...}      # para category="auto"; solo informativo en nivel detail
CATEGORY_TO_SUMMARY = {"temps":"temps_loads", "loads":"temps_loads", "fans":"fans", "clocks":"clocks_power",
                       "power":"clocks_power", "memory":"memory_disks", "storage":"memory_disks", "other":"other"}
```

Primera regla que casa gana. El orden de la lista fija también el orden de presentación dentro de cada categoría. La regla final `any.fallback` casa todo.

| id | hw | kind | name (regex) | cat | nivel | grupo | flags |
|---|---|---|---|---|---|---|---|
| cpu.temp.main | cpu | temperature | `Core \(Tctl/Tdie\)\|(CPU )?Package\|Core Max` | temps | summary | | |
| cpu.load.total | cpu | load | `CPU Total` | loads | summary | | |
| cpu.clock.avg | cpu | clock | `Cores \(Average\)` | clocks | summary | | |
| cpu.power.pkg | cpu | power | `(CPU )?Package` | power | summary | | |
| cpu.core | cpu | * | `(CPU )?([PE]-)?Core #\d+.*` | auto | detail | cores | |
| cpu.ccd | cpu | temperature | `CCDs?.*` | temps | detail | | |
| cpu.bus | cpu | clock | `Bus Speed\|Fabric\|Uncore\|Memory` | clocks | detail | buses | |
| gpu.temp.main | gpu | temperature | `GPU Core\|(GPU )?Hot Spot` | temps | summary | | |
| gpu.load.core | gpu | load | `GPU Core` | loads | summary | | |
| gpu.clock.core | gpu | clock | `GPU Core` | clocks | summary | | |
| gpu.power.pkg | gpu | power | `GPU (Package\|Power)` | power | summary | | |
| gpu.mem.used | gpu | smalldata, data | `GPU Memory Used` | memory | summary | | |
| gpu.d3d | gpu | load, smalldata | `D3D .*` | auto | detail | d3d | |
| ram.main | ram, vram | load | `Memory` | memory | summary | | |
| ram.used | ram, vram | data | `Memory (Used\|Available)` | memory | summary | | |
| dimm.timing | dimm | timing | `.*` | memory | detail | timings | |
| sto.temp.thr | storage | temperature | `(Warning\|Critical)( Composite)? Temperature.*` | temps | detail | thresholds | |
| sto.temp.main | storage | temperature | `(Composite )?Temperature` | temps | summary | | |
| sto.used | storage | load | `Used Space` | storage | summary | | |
| sto.activity | storage | load | `Total Activity` | storage | summary | | neutral_band |
| sto.activity.rw | storage | load, throughput | `(Read\|Write) (Activity\|Rate)` | storage | detail | activity | |
| sto.capacity | storage | data | `(Free\|Total) Space` | storage | detail | capacity | |
| sto.health | storage | level, data, factor | `Life\|Available Spare.*\|Percentage Used\|Data (Read\|Written)\|Power On (Count\|Hours)` | storage | detail | health | |
| net.speed | network | throughput | `(Upload\|Download) Speed` | other | summary | | |
| any.fan | * | fan | `.*` | fans | summary | | idle_hide |
| any.control | * | control | `.*` | fans | detail | controls | |
| any.electrical | * | voltage, current | `.*` | power | detail | electrical | |
| any.temp | * | temperature | `.*` | temps | detail | | |
| any.fallback | * | * | `.*` | auto | detail | | |

Ejemplos con nombres reales:
- `/amdcpu/0` `Temperature "Core (Tctl/Tdie)"` -> `cpu.temp.main` (resumen). `"CCD1 (Tdie)"` -> `cpu.ccd`. `"SoC"` -> `any.temp` (detalle).
- `/amdcpu/0` `Voltage "Core #3 VID"` y `Power "Core #3 (SMU)"` -> `cpu.core` (grupo `cores`). `Voltage "Core (SVI2 TFN)"` y `Current "TDC"` -> `any.electrical`.
- `/gpu-amd/0` `Load "D3D Copy"` (los dos duplicados) -> `gpu.d3d`. `Control "GPU Fan"` -> `any.control`. `Fan "GPU Fan"=1023` -> `any.fan` (resumen).
- `/nvme/2` `"Composite Temperature"` -> `sto.temp.main`. `"Warning Temperature"` -> `sto.temp.thr`.
- `/lpc/nct6798d/0` `Fan "Fan #1"` (0 RPM, max 0) -> `any.fan` pero inactivo, así que no sale en el resumen. `Control "Fan #1"` -> `any.control`.
- Intel: `"CPU Package"` (temperatura y potencia), `"Core Max"`. NVIDIA: `"GPU Hot Spot"`, `"GPU Power"`. Ambos casan con reglas genéricas.

Supuesto: los nombres Intel y NVIDIA están escritos de memoria según LHM, y solo el equipo AMD está verificado. Los tests Intel/NVIDIA usan fixtures sintéticos (ver D, riesgo 5).

Contrato de `curation.py`:

```python
SUMMARY_CATEGORIES = ("temps_loads", "fans", "clocks_power", "memory_disks", "other")

@dataclass(frozen=True)
class Classification:
    rule_id: str; order: int; category: str; level: str; group: str
    idle_hide: bool; neutral_band: bool
    @property
    def summary_category(self) -> str: ...

def classify(s: Sensor) -> Classification            # lru_cache por (hw_class, kind, name)
def is_idle(s: Sensor) -> bool                       # value in (None, 0) y max in (None, 0)

@dataclass(frozen=True)
class Prefs:
    hidden_hardware: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset(SUMMARY_CATEGORIES)
    overrides: Mapping[str, bool] = field(default_factory=dict)

def in_summary(s: Sensor, prefs: Prefs) -> bool
    # override si existe; si no: level=="summary" y not (idle_hide and is_idle) y summary_category in categories

@dataclass
class SummarySection:
    hw_id: str; hw_kind: str; hw_class: str; hardware: str; sensors: list[Sensor]

def summary_sections(snapshot: Snapshot, prefs: Prefs) -> list[SummarySection]
    # omite hidden_hardware y hardware sin sensores; orden HW_KINDS, ram antes que vram;
    # sensores por (rank de categoría de resumen, Classification.order, key)

@dataclass
class DetailGroup:
    id: str; sensors: list[Sensor]
    count: int                                        # grupo "cores": núcleos distintos (core_count)

@dataclass
class DetailHardware:
    hw_id: str; hw_kind: str; hw_class: str; hardware: str
    loose: list[Sensor]; groups: list[DetailGroup]

@dataclass
class DetailTree:
    hardware: list[DetailHardware]                    # orden HW_KINDS, sin dimm
    modules: list[DetailHardware]                     # hw_class=="dimm" (nodo "Módulos de memoria (N)")

def detail_tree(snapshot: Snapshot) -> DetailTree     # ignora hidden_hardware y categorías: lista TODO
def core_count(sensors: Iterable[Sensor]) -> int
def section_title_key(hw_class: str) -> str | None    # "hw_ram"->"Memoria", "hw_vram"->"Memoria virtual"
def toggle_override(overrides: Mapping[str, bool], s: Sensor, want: bool, prefs: Prefs) -> dict
    # si want coincide con el valor por defecto de la regla, elimina la entrada
```

`GROUP_ORDER = ("cores","buses","electrical","d3d","controls","thresholds","capacity","activity","health","timings")`. Cada id de grupo tiene una clave i18n `grp_<id>` ("Núcleos ({n})", "Controles de ventilador", ...).

## B.3 `models.py`
- `UNITS["timing"] = "ns"`. `format_value("timing", 13.75)` -> `"13.75 ns"`.
- `factor`: `.2f` bajo 100 y `.0f` desde 100 (`Power On Hours` 45117 -> `"45117"`).
- `data`: >= 1024 GB -> `"x.xx TB"`. `smalldata`: >= 1024 MB -> `"x.x GB"`.
- `None` sigue siendo `"—"`. `kind_<x>` en i18n para `timing`.

## B.4 Modelo de configuración (`config.py` -> `config_version: 2`)

Claves nuevas y defaults:

| Clave | Default | Validación / tope |
|---|---|---|
| `config_version` | 2 | int |
| `hidden_hardware` | `[]` | lista de str `^hw/.{1,200}$`, <= 200 |
| `summary_categories` | las 5 | subconjunto de `SUMMARY_CATEGORIES` |
| `summary_overrides` | `{}` | `{clave sensor str <= 200: bool}`, <= 1000 |
| `detail_toggled` | `[]` | ids que difieren del estado por defecto (hardware expandido, grupos colapsados), <= 300 |
| `detail_geometry` | `{}` | `{x,y,w,h}` enteros acotados (w,h en 200..8000) |
| `color_palette` | `"classic"` | classic, colorblind, pastel, high_contrast, mono, custom |
| `custom_colors` | `{"ok":"#6EDC78","warn":"#F5C842","crit":"#FF9999"}` | `#RRGGBB` |
| `icon_style` | `"outline"` | outline, filled, simple, none |
| `ui_scale` | 100 | 90, 100, 115, 130 |
| `panel_size` | `"normal"` | compact, normal, large |
| `language` | `"auto"` | auto, es, en |
| `placement_mode` | `"taskbar"` | taskbar, floating |
| `taskbar_anchor` | `"right"` | right, left |
| `placement_monitor` | `"primary"` | `primary` o `[0-9a-f#]{1,40}` |
| `floating_positions` | `{}` | dict <= 16, `{anchor,dx,dy}`, offsets <= 10000 |
| `show_in_fullscreen` | False | bool |
| `fullscreen_placement` | `"same"` | same, floating o una de las 8 anclas |
| `fullscreen_floating_positions` | `{}` | igual que `floating_positions` |
| `custom_icons_kind` | `{}` | `{hw_kind: archivo}` |
| `custom_icons_hw` | `{}` | `{hw_id: archivo}` |
| `custom_icons_sensor` | `{}` | `{clave sensor: archivo}`; archivo = `^[0-9a-f]{16}\.(png\|jpg\|jpeg\|bmp\|ico\|gif)$`; total <= 200 |
| `log_debug` | False | bool |
| `update_check_auto` | False | bool |
| `update_last_check` | 0 | int en 0..4102444800 |

Claves existentes que no cambian: `interval_ms`, `pinned` (tope 12 -> 32), `theme` (cumple el papel de `app_theme` de la referencia, se mantiene el nombre), `fahrenheit`, `start_with_windows` (espejo, la verdad es `startup.is_enabled()`), `collapsed`.

Migración: `_migrate(raw) -> raw`. Si `config_version` falta (fase 1), se acepta tal cual y se añaden los defaults. Una versión mayor a 2 (downgrade) se carga con las claves conocidas y se avisa en el log. Cada clave se valida por separado con `_valid(key, value)` (adaptar de la referencia). Si no es válida, cae a su default y se registra un WARNING, sin perder el resto del archivo.

`Config.set` valida y lanza `ValueError` ante datos inválidos. Se mantiene el límite de 256 KiB, `is_reparse_point` y el guardado atómico.

## B.5 Navegación del panel

```
HardwarePanel (QFrame frameless, Tool + topmost)
 ├─ QStackedWidget
 │    ├─ 0  Resumen (secciones por hardware)  <- clic izquierdo
 │    └─ 1  SettingsView (pila de páginas)    <- clic derecho / engranaje
 └─ Barra inferior: actualizar | °C/°F | detalle | ajustes | salir      (barra superior: título + tema)

SettingsView raíz  (Atrás en la raíz -> vuelve al resumen)
 ├─ Visualización            (Page)  hardware (checks dinámicos), categorías (checks), °F, abrir detalle, restablecer resumen
 ├─ Personalización          (Page)  tema, paleta, estilo icono, tamaño panel, escala, idioma
 ├─ Posición                 (Page)  placement, ancla flotante, monitor, pantalla completa, dónde, restablecer
 ├─ Iconos personalizados    (Page)  Administrar… (diálogo), Quitar todos
 ├─ Registros                (Page)  abrir log, abrir carpeta, nivel debug
 ├─ ─────────
 ├─ Iniciar con Windows      (Toggle en la raíz)
 ├─ Buscar actualizaciones   (Page)  estado, comprobar, instalar, notas/página, auto 1/día
 └─ Acerca de                (Page)  versión, modo, admin/PawnIO, LHM, licencias, copiar info, abrir logs
Ventana de detalle: top-level aparte (Qt.Window), se abre desde la barra inferior, desde la cabecera de cada hardware
(con filtro) y desde Visualización. Abrirla oculta el panel.
```

`settings_model.py` (adaptar de la referencia):
- Copia `Node, Separator, Option, Page, Toggle, Choice, Action, SettingsModel (walk/find/parent_of/path_to)`.
- Añade `Info` (fila de solo lectura, clic = copiar) y `Heading`.
- `Page.children` puede ser `list` o `Callable[[], list]`, con método `Page.items()`. Necesario para la lista dinámica de hardware.
- `Toggle` y `Info` admiten `label_text` ya traducido, para nombres de hardware que no son claves i18n.
- `SettingsCallbacks` (todos con valores por defecto no-op, como en la referencia):
  - Visualización: `set_bool`, `list_hardware() -> [(hw_id, nombre, hw_class)]`, `set_hardware_visible(hw_id, bool)`, `hardware_visible(hw_id)`, `set_category(cat, bool)`, `category_enabled(cat)`, `open_detail()`, `reset_summary()`, `has_overrides()`.
  - Personalización y posición (nombres como en la referencia): `set_theme`, `set_palette`, `set_icon_style`, `set_panel_size`, `set_ui_scale`, `set_language`, `set_placement`, `set_floating_anchor`, `set_monitor`, `set_fullscreen_placement`, `reset_position`, `list_screens`, `current_key`, `icons_centered`, `monitor_has_taskbar`.
  - Iconos personalizados, registros, inicio y acerca de/updates: `open_custom_icons`, `clear_custom_icons`, `open_log`, `open_logs_folder`, `set_log_debug`, `startup_enabled`, `set_startup`, `tech_info`, `copy_tech_info`, `open_notices`, `update_state() -> (estado, texto)`, `check_updates`, `install_update`, `can_install_update`, `open_release_page`, `set_update_auto`.

## B.6 Contratos de componentes que cambian

- **`pins.py`**
  - `MAX_PINS = 32`.
  - `bulk_state(pinned, sensors, snapshot) -> Literal["none","some","all"]`.
  - `toggle_bulk(pinned, sensors, snapshot, max_pins=MAX_PINS) -> tuple[list[str], int]`. Devuelve los pins nuevos y el número de sensores omitidos por el tope. Reutiliza `is_pinned` y `_covering_autos`.
- **`HardwarePanel`**
  - Nuevos métodos: `set_prefs(Prefs)`, `set_metrics(size, scale_pct)`, `set_settings_model(model)`, `show_settings(page_id="root", direct=False)`, `show_devices()`, `retranslate()`.
  - Nuevas señales: `bulk_pin_toggled(object)` (list[Sensor]), `detail_requested(str)` (hw_id o `""`), `settings_requested`.
  - Adaptado de `device_panel.py`: la pila, `_apply_scale`/`scaled_font` y la integración con `dialogs.hub` (`_modal`, `_on_dialog_opened/closed`, `_after_dialog`, `_on_focus_window_changed`). Esto es necesario: sin ello el panel se oculta al abrir un diálogo o `QFileDialog` y se lleva el diálogo.
  - `panel_dimensions` ahora toma `size`: compact 0.45 / normal 0.62 (valor actual) / large 0.80 de la altura disponible.
  - Sección: cabecera con chevron, icono, título, control de fijar en bloque (tri-estado) y botón "ver detalle".
- **`DetailWindow`**
  - Señales: `pin_toggled(object)`, `bulk_pin_toggled(object)`, `summary_override_changed(str, bool)`, `toggled_changed(object)`, `geometry_changed(object)`.
  - Métodos: `set_snapshot`, `set_pinned`, `set_prefs`, `set_fahrenheit`, `set_dark`, `retranslate`, `show_for(hw_id | None)`.
  - Implementación: `QTreeWidget` con casillas nativas (sin widgets por fila), una sola fuente de verdad `key -> QTreeWidgetItem`.
- **`TaskbarWidget`** (desacoplado de `Config`, ADR-7)
  - `PlacementSettings` (dataclass) con modo, ancla, monitor, posiciones flotantes, pantalla completa y posición en pantalla completa.
  - Métodos: `set_placement_settings(PlacementSettings)`, `set_ui_scale(pct)`, `set_icon_resolver(fn)` (custom icon por `hw_kind, hw_id, key`), `set_floating_anchor(anchor)`, `current_screen_key()`.
  - Señales: `floating_moved(str, dict, bool)` (clave de monitor, `{anchor,dx,dy}`, es_pantalla_completa), `placement_warning(str)`.
  - `Pill` gana `hw_id`. `context_menu_requested` se mantiene. Se elimina `popup_menu_foreground`/`_open_menu` con `QMenu`.
- **`placement.py`** (copiar casi literal). Tres ajustes:
  1. `Env.dock_edge: int | None`: borde izquierdo (px físicos) hasta donde pueden llegar las píldoras cuando hay vecinos (lo calcula `taskbar.docked_edge`). `_compute` sustituye `info.tray_rect` por ese borde antes de anclar. Se añade además `docked_edge_left` (simétrico, para el ancla izquierda).
  2. `limit` en la rama derecha/izquierda usa `max_pill_extent` (movido desde `widget.py` a `placement.py` y reexportado desde `widget`, para no romper tests).
  3. `pill_height_for` se mantiene el actual del proyecto (clamp 24..36), no el de la referencia.
- **`custom_icons.py`**
  - `icons_dir()` apunta a `%ProgramData%\HardwareMonitorWidget\icons` con DACL protegida (SYSTEM y Administradores control total, Users solo lectura), creada por el proceso elevado.
  - Solo se **leen** archivos si `startup.is_path_admin_only(icons_dir())`. En otro caso, importar está deshabilitado y los iconos propios se ignoran (el glifo vectorial sigue funcionando).
  - API: `import_image(src) -> str`, `import_image_busy(src) -> str`, `resolve(hw_kind, hw_id, sensor_key, by_kind, by_hw, by_sensor) -> str | None`, `set_maps(by_kind, by_hw, by_sensor)`, `pixmap_for(file, size_px, dpr)`, `draw_custom_icon(painter, rect, file, opacity=1.0)`, `cleanup_unused(used)`.
  - Se copian sin cambios los validadores SVG (`_svg_sanitize`, `_svg_check_value`, límites de elementos, profundidad y bulk), `_decode` con lista blanca de formatos, límites de bytes y píxeles y el timeout de render. Se elimina todo el soporte de animación (`_Anim`, sidecar `.anim.json`, reloj de animación).
- **`updater.py`** (adaptar)
  - `release_info.py`: `REPO = ""`, `PUBLIC_KEYS = ()` (se rellenan al crear el repositorio y las claves). `is_configured() -> bool`. Con `REPO` vacío no hay red.
  - `APP_DIR_NAME = "HardwareMonitorWidget"`, `UPDATE_DIR_PREFIX = "HMW-update-"`, `installer_asset_name(v) = f"HardwareMonitorWidget-Setup-{v}.exe"`.
  - `install_mode()` delega en `startup.install_mode()` (la referencia usa `%LOCALAPPDATA%\Programs`, que aquí no aplica).
  - `updates_dir()`: `%ProgramData%\HardwareMonitorWidget\updates`, creado con DACL protegida y comprobado con `startup.is_path_admin_only` antes de descargar y justo antes de lanzar. Si falla, `UpdateError("upd_err_dir")` y se ofrece la página de la release (ADR-5).
  - `installer_args()` conserva `["/SILENT", "/CLOSEAPPLICATIONS", "/LAUNCHAPP=1"]`.
  - Se copian literal: `parse_version`, `is_newer`, `Release`, `sanitize_body`, `_SafeRedirect`, `_final_url_ok`, `_download` (con topes de tamaño y deadline), `verify_sums_signature`, `expected_hash`, `reverify_installer`, `launch_installer`, `cleanup_old_updates`, `is_policy_block`.
- **`about.py`**: `tech_info(hello, cfg) -> str` (sin datos personales); `UpdateController(QObject)` con señal `state_changed`, estados idle/checking/available/up_to_date/downloading/ready/error y métodos `check_now()`, `install()`, `cancel()`; `AutoUpdateChecker` adaptado (primera comprobación a los 60 s, tick cada 6 h, `should_auto_check` 24 h). Reutiliza `_Bridge` y `_spawn` de la referencia. Sin `QDialog`: las páginas viven en Ajustes.
- **`i18n.py`**: `LANGUAGES = {"es": "Español", "en": "English"}`, `apply_language_setting(pref)`, y carga de extensiones con imports estáticos `from . import strings_x` dentro de `try/except ImportError` (así PyInstaller los detecta y cada tarea puede añadir su archivo sin tocar `i18n.py`).
- **`tray.py`**: solo icono y tooltip. Sin `QMenu`. La activación `Trigger` abre el resumen y `Context` abre Ajustes (en Windows el clic derecho llega como `Context` cuando no hay `contextMenu`, como en la referencia).
- **`app.py`**: orden `setup_logging()` -> `Config()` -> `apply_log_level(cfg)`. El `Controller` construye `Prefs` y `PlacementSettings` desde `cfg`, enruta las señales a `cfg.set` y empuja cambios a panel, detalle y widget.

## B.7 Reutilización de la referencia (archivo -> archivo)

| Referencia | Aquí | Acción |
|---|---|---|
| `settings_model.py` | `settings_model.py` | **Adaptar**: copiar nodos y `SettingsModel`; añadir `Info`, `Heading`, `Page.items()`, `label_text`; reescribir `build_settings_model` |
| `settings_view.py` | `settings_view.py` | **Copiar casi literal** (cambiar imports de `glyph_icon`/colores y claves i18n) |
| `tray.py` | `settings_actions.py` + `tray.py` | **Adaptar** solo los callbacks de ajustes (`_set_palette`, `_set_icon_style`, `_set_panel_size`, `_set_ui_scale`, `_set_language`, `open_custom_icons`, `_set_placement`, `_set_floating_anchor`, `_set_fullscreen_placement`, `_set_monitor`, `_reset_position`, `open_path`, `open_logs_folder`). No copiar el resto (dispositivos, alias, detail log, tutorial, whats new) |
| `device_panel.py` | `panel.py` | **Adaptar** pila, escala/métricas, `show_settings/show_devices`, guarda de diálogos. No copiar filas, alias, olvidar, logging ni animación |
| `colors.py` | `colors.py` | **Adaptar**: paletas, `custom_palette`, `ensure_contrast`, `set_palette`, `get_palette`. Descartar `battery_color`, `level_band`, `RowStyle` |
| `custom_colors_dialog.py` | idem | **Adaptar** (claves `ok/warn/crit`, muestras con temperatura) |
| `custom_icons.py` | idem | **Adaptar** (ver B.6) |
| `custom_icons_dialog.py` | idem | **Adaptar** (3 pestañas: tipo de hardware, hardware, sensor) |
| `dialogs.py` | idem | **Copiar** |
| `placement.py` | idem | **Copiar casi literal** + 3 ajustes (B.6) |
| `widget.py` | `widget.py` | **Adaptar** secciones: `screen_keys`, `screen_infos`, `_build_placement`, `_build_env`, `_apply_screen`, `update_position`, arrastre y snap, pantalla completa pasiva, `set_floating_anchor`, `scaled_pill_h`. El resto del widget actual (píldoras, overflow, `docked_edge`) se conserva |
| `config.py` | `config.py` | **Adaptar**: validadores `_valid`, `_clean_floating`, `clean_custom_icons`, constantes de dominio |
| `i18n.py` | `i18n.py` | **Adaptar** (`LANGUAGES`, `apply_language_setting`) |
| `updater.py` | `updater.py` | **Adaptar** (ver B.6) |
| `ed25519.py` | idem | **Copiar literal** |
| `about.py` | `about.py` | **Adaptar** (sin QDialog; `tech_info`, `UpdateController`, `AutoUpdateChecker`) |
| `tools/signing/*` | idem | **Adaptar**: nombres del paquete y de activos, directorio de clave `%USERPROFILE%\.hmw-signing\hmw_release_ed25519.key`, `gh attestation verify` pasa a opt-in `--attest` (los binarios se compilan en local, no en CI) |
| `whats_new`, `tutorial`, `popup`, `alerts`, `aliases`, `animation`, `stats_window`, `visibility` | | **No copiar** |
| tests de la referencia: `test_settings_model/view`, `test_palettes`, `test_placement*`, `test_custom_icons`, `test_updater`, `test_ed25519`, `test_public_keys`, `test_signing_tools`, `test_config`, `test_dialog_guard` | `tests/` | **Adaptar** |

## B.8 Modos de fallo y recuperación

| Fallo | Comportamiento |
|---|---|
| Config corrupta, demasiado grande o con clave inválida | Defaults por clave, WARNING, nunca excepción |
| Regla de curación sin match (sensor kind extraño) | Cae en `any.fallback` (detalle) |
| Archivo de icono ausente, corrupto o carpeta no admin-only | Glifo vectorial, sin excepción |
| Monitor guardado desconectado | Cae al primario, `Choice` lo marca deshabilitado |
| Callback de ajuste lanza excepción | `settings_view` la captura, la registra y refresca (patrón de la referencia) |
| Updater: red, firma, hash, carpeta no segura, SAC | `UpdateError(clave i18n)`, borra la descarga, ofrece la página de la release |
| Diálogo abierto mientras el panel pierde foco | `app_dialog_guard` evita que el panel se oculte |

## B.9 ADRs

**ADR-1. Curación como tabla de datos en código, no editable por el usuario.**
Contexto: hay más de 300 sensores y nombres que varían según proveedor. Decisión: reglas ordenadas (primera coincidencia) con regex por (clase de hardware, kind, nombre), más overrides por sensor en config. Consecuencias: testable de forma pura, estable entre versiones, y el usuario siempre puede subir o bajar un sensor concreto. Alternativas descartadas: reglas editables en config (superficie de validación grande y soporte difícil), heurística por frecuencia de cambio (impredecible), solo lista blanca por nombres exactos (no cubre Intel/NVIDIA).

**ADR-2. Ajustes dentro del panel con árbol declarativo copiado de la referencia; el clic derecho abre Ajustes; se elimina el `QMenu`.**
Contexto: el usuario pide el mismo menú navegable que Taskbar Battery Widget. Decisión: `settings_model` (puro) y `settings_view` (copia casi literal) alojados en un `QStackedWidget` del panel. Consecuencias: un solo lugar para todo, navegación por teclado ya resuelta y testeada en la referencia. Alternativas descartadas: `QMenu` con submenús (no admite checks in-place, ni listas largas, ni accesibilidad uniforme), diálogo de ajustes separado (no es lo pedido).

**ADR-3. Config v2 con validación por clave y migración.**
Contexto: hoy `Config._load` solo comprueba tipos y un fallo no puede rescatar el resto. Decisión: validadores por clave (adaptados de la referencia), `config_version`, topes de tamaño y `Config.set` que valida. Consecuencias: robustez frente a un proceso elevado que lee un archivo escribible por el usuario. Alternativa descartada: JSON Schema externo (dependencia nueva sin necesidad).

**ADR-4. Textos en módulos `strings_*.py` cargados por `i18n`.**
Decisión: cada tarea aporta su propia tabla es/en, y `i18n.py` las fusiona con imports estáticos. Consecuencias: tareas en paralelo sin conflictos y PyInstaller las detecta. Test de paridad es/en. Alternativa descartada: ampliar `_STRINGS` en `i18n.py` desde cada tarea (conflictos de merge y de ejecución en paralelo).

**ADR-5. Updater con instalador elevado: solo modo instalado, carpeta de descarga admin-only, firma Ed25519 primero.**
Contexto: la app es `requireAdministrator`, así que `Popen(Setup)` hereda el token alto y no hay UAC. Un archivo sustituido equivale a ejecución arbitraria como administrador. La referencia descarga a `%TEMP%` (escribible por el usuario) y confía en el re-hash. Decisión:
1. Solo modo `installed` (Program Files admin-only).
2. Descarga en `%ProgramData%\HardwareMonitorWidget\updates\<aleatorio>` con DACL protegida, comprobada con `is_path_admin_only` antes de descargar y antes de lanzar. Si falla, no se instala.
3. Firma Ed25519 de `SHA256SUMS.txt` verificada antes de confiar en ningún hash. Re-hash inmediatamente antes de lanzar.
4. Nunca instalación automática (la comprobación automática solo notifica).
5. Hosts HTTPS permitidos y topes de tamaño y tiempo de la referencia.
Consecuencias: más código de DACL (se reutiliza `startup`), y el updater no funciona sin elevación (cae a la página de la release). Alternativas descartadas: descargar a `%TEMP%` como la referencia (TOCTOU explotable por un proceso no elevado del mismo usuario), mantener el handle abierto con `FILE_SHARE_READ` (opcional como refuerzo futuro), actualización silenciosa automática (riesgo desproporcionado con binarios sin firma Authenticode).

**ADR-6. Iconos personalizados: carpeta admin-only, solo primer fotograma.**
Contexto: el proceso elevado decodificaría imágenes dejadas en una carpeta escribible por el usuario, con los decodificadores de Qt como superficie de ataque. Decisión: guardar los iconos re-codificados (PNG) en `%ProgramData%\HardwareMonitorWidget\icons` con DACL protegida, y cargar solo si `is_path_admin_only`. Las animaciones se validan pero se reducen al primer fotograma (no se porta `animation.py`). Consecuencias: coste menor y menos superficie. La app sin elevar no puede importar iconos (muestra un aviso). Alternativa descartada: `%APPDATA%` como la referencia (riesgo de decodificar con privilegios archivos manipulables por un proceso no elevado).

**ADR-7. `TaskbarWidget` y `HardwarePanel` no conocen `Config`.**
Decisión: reciben dataclasses y callables (`PlacementSettings`, `Prefs`, `icon_resolver`) y emiten señales; solo `Controller` y `SettingsActions` persisten. Consecuencias: tests de UI sin disco, y tareas paralelas sin acoplamiento. Alternativa descartada: pasar `cfg` como en la referencia (acopla widgets y config, dificulta tests).

**ADR-8. `MAX_PINS` sube de 12 a 32.**
Contexto: el resumen elegido da 28 sensores en el equipo del usuario. Decisión: 32 como tope duro con aviso al llegar. El widget ya compacta y agrupa el exceso en "+K". Alternativa descartada: sin tope (riesgo de config y píldoras incontrolables).

---

# C. Plan de tareas

Todas van a `worker-cloud` (más de 2 archivos). Comandos en PowerShell, sin `&&`. Verificación global al final de cada ola: `.venv\Scripts\python.exe -m pytest -q`. Cada tarea añade su propio módulo `strings_*.py` (ADR-4), salvo T1 que modifica `i18n.py`.

**Ola 1 (en paralelo, sin archivos compartidos): T1, T2, T3, T4.**

### T1. Dominio puro: modelos, config, i18n base, pins y curación
- Archivos permitidos: `hardware_monitor_widget/models.py`, `config.py`, `i18n.py`, `pins.py`, `curation_rules.py`, `curation.py`; `tests/test_models.py`, `test_config.py`, `test_pins.py`, `test_i18n.py` (nuevo), `test_curation.py` (nuevo); `tests/data/sensors-admin.jsonl` (copia del archivo de la raíz, sin modificar el original).
- Hace: B.2, B.3, B.4, `pins` (B.6), `i18n` (LANGUAGES, `apply_language_setting`, cargador de `strings_*`). Incluye fixtures sintéticos Intel y NVIDIA en `test_curation.py`.
- Criterios: C1 a C7, G1 a G3 (parte pura), B1 y B2 (parte pura), X2, X5, X6.
- Verificación: `.venv\Scripts\python.exe -m pytest -q tests\test_models.py tests\test_config.py tests\test_pins.py tests\test_i18n.py tests\test_curation.py`
- Si el worker falla dos veces, dividir en T1a (`models`, `config`, `i18n`) y T1b (`pins`, `curation*`).

### T2. Apariencia: paletas, estilos de icono, esquema de color
- Archivos: `colors.py`, `icons.py`, `appearance.py` (nuevo), `custom_colors_dialog.py` (nuevo), `strings_appearance.py` (nuevo); `tests/test_palettes.py` (nuevo), `tests/test_icon_styles.py` (nuevo).
- Hace: `set_palette` y paletas (classic, colorblind, pastel, high_contrast, mono, custom), `ensure_contrast`, `glyph_icon(name, size, color)` (back, chevron_right, chevron_up, chevron_down y glifos de página: display, personalization, position, image, logs, info, update), `set_icon_style` (outline, filled, simple, none), `apply_color_scheme`, `apply_appearance(cfg)`.
- Criterios: S3 (paletas, contraste >= 4.5:1 contra todos los fondos de píldora en dark y light, también para `custom`).
- Verificación: `.venv\Scripts\python.exe -m pytest -q tests\test_palettes.py tests\test_icon_styles.py`
- Sub-tarea opcional `worker-local`: `custom_colors_dialog.py` + su test (96 líneas, adaptación directa).

### T3. Iconos personalizados
- Archivos: `dialogs.py`, `custom_icons.py`, `custom_icons_dialog.py`, `strings_icons.py` (nuevos); `tests/test_custom_icons.py`, `tests/test_dialog_guard.py` (nuevos, adaptados de la referencia).
- Hace: B.6 (`custom_icons`), `app_dialog_guard`, diálogo de 3 pestañas.
- Criterios: S5, ADR-6. Tests con directorio inyectable y monkeypatch de `is_path_admin_only`, más casos hostiles de la referencia (SVG con entidades, `url(...)` externo, GIF con demasiados fotogramas, PNG enorme).
- Verificación: `.venv\Scripts\python.exe -m pytest -q tests\test_custom_icons.py tests\test_dialog_guard.py`
- Sub-tarea opcional `worker-local`: `dialogs.py` + `test_dialog_guard.py` (copia literal).

### T4. Actualizaciones y firma
- Archivos: `updater.py`, `ed25519.py`, `release_info.py` (nuevos), `strings_update.py` (nuevo), `tools/signing/gen_keys.py`, `tools/signing/sign_release.py` (nuevos), `packaging/installer.iss` (añadir ejecución silenciosa con `/LAUNCHAPP=1`: entrada `[Run]` con `Check` sobre el parámetro, flags `nowait shellexec`); `tests/test_updater.py`, `test_ed25519.py` (+ `tests/rfc8032_vectors.json`), `test_public_keys.py`, `test_signing_tools.py`.
- Hace: B.6 (`updater`), ADR-5, adaptación de herramientas de firma, documentación en `docs/PACKAGING.md` del procedimiento de release firmada (assets: Setup, zip, `SHA256SUMS.txt`, `.sig`).
- Criterios: U1, U2, U4, U5. Tests sin red (opener falso). `test_public_keys` acepta tupla vacía mientras `REPO` esté vacío y valida el formato (64 hex) cuando haya claves.
- Verificación: `.venv\Scripts\python.exe -m pytest -q tests\test_updater.py tests\test_ed25519.py tests\test_public_keys.py tests\test_signing_tools.py tests\test_packaging.py`
- Sub-tarea opcional `worker-local`: `ed25519.py` + `test_ed25519.py` (copia literal).

**Ola 2 (en paralelo tras la ola 1): T5, T6, T7.**

### T5. Posición y píldoras
- Depende de T1, T2, T3.
- Archivos: `placement.py` (nuevo), `widget.py`, `strings_position.py` (nuevo); `tests/test_placement.py` (nuevo), `tests/test_widget_ui.py` (adaptar), `tests/test_coexistence.py` (solo si hace falta, sin debilitar aserciones).
- Hace: B.6 (`placement`, `TaskbarWidget`), `max_pill_extent` movida a `placement.py` y reexportada, clic derecho emite `context_menu_requested` sin abrir menú, escala de interfaz y `pill_height` escalado, resolución de iconos propios.
- Criterios: S4, X3, parte de S3 (escala en píldoras).
- Verificación: `.venv\Scripts\python.exe -m pytest -q tests\test_placement.py tests\test_widget_ui.py tests\test_coexistence.py`

### T6. Panel de resumen y ventana de detalle
- Depende de T1, T2, T3.
- Archivos: `panel.py`, `detail_window.py` (nuevo), `strings_panel.py` (nuevo); `tests/test_panel_ui.py` (adaptar), `tests/test_detail_window.py` (nuevo).
- Hace: resumen curado con `summary_sections`, cabecera con control de fijar en bloque y botón de detalle, estado vacío, escala y tamaño de panel (`set_metrics`), iconos propios en cabeceras y filas, barra inferior nueva (sin botón de inicio con Windows), `DetailWindow` completa. **No** aloja `SettingsView` (eso es T8).
- Criterios: C7, G1 a G3 (UI), D1 a D5, B1 y B2 (UI), V2 (estado vacío), X6.
- Verificación: `.venv\Scripts\python.exe -m pytest -q tests\test_panel_ui.py tests\test_detail_window.py`

### T7. Núcleo de Ajustes (modelo y vista)
- Depende de T1, T2.
- Archivos: `settings_model.py`, `settings_view.py`, `strings_settings.py` (nuevos); `tests/test_settings_model.py`, `tests/test_settings_view.py` (nuevos, adaptados de la referencia).
- Hace: B.5 (todas las páginas, con callbacks no-op por defecto), vista con navegación por teclado, `Info`, `Heading` y `Page.items()` dinámico, acordeones in-place.
- Criterios: S2, S3 (estructura), S4 (estructura), V1, V3, S8 (estructura), navegación y teclado.
- Verificación: `.venv\Scripts\python.exe -m pytest -q tests\test_settings_model.py tests\test_settings_view.py`

**Ola 3 (secuencial tras la ola 2): T8.**

### T8. Integración, cableado y documentación
- Depende de T4, T5, T6, T7.
- Archivos: `settings_actions.py` (nuevo), `about.py` (nuevo), `tray.py`, `app.py`, `panel.py` (solo alojar `SettingsView`: pila, `show_settings`/`show_devices`, guarda de diálogos, gear en la barra), `tools/screenshots.py`, `strings_integration.py` (nuevo); `tests/test_settings_actions.py` (nuevo), `tests/test_app_startup.py` y `tests/test_packaging.py` (adaptar), `docs/spec.md` (sección v2), `docs/SECURITY_NOTES.md`, `docs/PACKAGING.md`, `THIRD_PARTY_NOTICES.md` (atribución al código adaptado).
- Hace: clic derecho y bandeja -> Ajustes, `Controller` con `Prefs`/`PlacementSettings`, nivel de log DEBUG en vivo, `tech_info`, `UpdateController`, `AutoUpdateChecker`, `startup` en Ajustes, abrir logs, y la persistencia de todas las claves.
- Criterios: S1, S6, S7, S8, U2 a U5 (flujo), V1 a V3, X1, X4.
- Verificación: `.venv\Scripts\python.exe -m pytest -q` (suite completa) y `.venv\Scripts\python.exe tools\screenshots.py` si aplica.
- Si resulta demasiado grande, dividir en T8a (`settings_actions`, `tray`, `app`, `panel`) y T8b (`about`, documentación, screenshots).

**Revisión posterior:** `reviewer` y `codex-reviewer` sobre el diff completo. `security` es obligatorio (updater, iconos personalizados, config y DACL). `ui-reviewer` sobre panel, detalle y Ajustes.

---

# D. Riesgos y decisiones abiertas

## Decisiones del usuario (2026-11-18) — RESUELVEN las preguntas de abajo
1. Repo de actualizaciones: `Gabrielfch/hardware-monitor-widget`. Claves Ed25519: dos (principal + respaldo offline) como en la referencia; las genera el usuario con `tools/signing/gen_keys.py` (las privadas NUNCA en el repo). Mientras `PUBLIC_KEYS = ()` el updater queda deshabilitado sin red.
2. Licencia del proyecto: MIT (archivo `LICENSE` en la raíz).
3. Clic derecho (píldoras y bandeja): igual que Taskbar Battery Widget → abre el panel (como el clic izquierdo); el engranaje de la barra inferior lleva a Ajustes. El menú contextual nativo de la bandeja se elimina.
4. Iconos animados: solo primer fotograma (propuesta aceptada por defecto).
5. Discos: en el resumen Read Activity y Write Activity; Total Activity solo en detalle y con banda neutra.

## Preguntas exactas para el usuario (bloquean o cambian el diseño)
1. **Repositorio y claves del updater.** Cuál es el `owner/repo` del nuevo repo de GitHub, y quieres dos claves Ed25519 como en la referencia (principal y respaldo guardada offline)? Hasta saberlo, `REPO = ""` y `PUBLIC_KEYS = ()` dejan las actualizaciones deshabilitadas, sin tráfico de red. Para activarlas hay que ejecutar `tools/signing/gen_keys.py`, copiar las claves públicas a `release_info.py` y reconstruir.
2. **Licencia del proyecto.** `D:\Proyectos\monitor` no tiene `LICENSE` y la referencia es MIT. "Acerca de > Licencias" y la atribución del código adaptado necesitan una licencia propia. Cuál quieres?
3. **Clic derecho.** Interpreto "abre el mismo panel/menú con páginas de Ajustes" como que el clic derecho (píldoras y bandeja) abre directamente la raíz de Ajustes, y el clic izquierdo el resumen. En la referencia el clic derecho abre el panel de dispositivos y el engranaje lleva a Ajustes. Confirmas mi interpretación?
4. **Iconos animados.** Propongo solo primer fotograma de GIF/WebP (no se porta el reloj de animación). Aceptable en esta fase?
5. **Actividad de disco.** Los tres discos reportan `Total Activity ≈ 99.9998 %` con lectura y escritura a 0. Parece un artefacto de la primera muestra de LHM. Si es real, con la banda por defecto (`load >= 95` rojo) las píldoras de disco saldrían con "!" permanente. Propongo `neutral_band` para `Total Activity` (sin rojo ni "!"). Confirmas, o prefieres Read/Write Activity en el resumen?

## Riesgos
1. **Updater ejecuta un instalador elevado.** Al ser el proceso `requireAdministrator`, el instalador hereda privilegios sin UAC. La defensa está en ADR-5: modo instalado (Program Files admin-only), carpeta de descarga admin-only verificada, firma antes que hash, re-hash antes de lanzar y confirmación. Compromiso de la clave privada = ejecución como administrador en todos los equipos: guardarla offline, usar clave de respaldo para rotación y no firmar desde un entorno compartido. El instalador no está firmado con Authenticode: Smart App Control puede bloquearlo (se detecta con `is_policy_block` y se ofrece la página de la release). Un atacante que sirva una release antigua con firma válida solo logra reinstalar una versión anterior permitida por `is_newer`; el nombre de activo lleva la versión y la firma cubre la lista, por lo que no puede cambiar de versión sin invalidarla.
2. **Relanzar tras actualizar.** `installer.iss` hoy usa `postinstall skipifsilent`, que no relanza tras `/SILENT`. T4 añade la entrada con `/LAUNCHAPP=1`. Hay que probarlo a mano en una VM: el relanzado hereda el token elevado del instalador y `QLockFile` ya tolera la instancia cerrada por `taskkill`. `startup` mantiene su estado gracias a `UsePreviousTasks=no` y la detección de la tarea existente.
3. **Datos de usuario leídos con privilegios.** Config y logs viven en carpetas escribibles por el usuario y el proceso es elevado. Mitigaciones: validadores por clave, topes, `is_reparse_point`, claves opacas (nunca rutas). Los iconos se mueven a una carpeta admin-only (ADR-6). Aviso: la app sin elevar no puede importar iconos. Arrastrar y soltar archivos desde un Explorer no elevado a la app elevada no funciona (UIPI): solo diálogo de archivo.
4. **Panel y diálogos.** La emulación de "clic fuera" oculta el panel si un diálogo nativo roba el foco. Es obligatorio portar la guarda `dialogs.hub` y sus ganchos al panel (T3 + T8). Sin eso, el selector de iconos, el diálogo de colores y los `QMessageBox` cierran el panel.
5. **Reglas Intel y NVIDIA sin verificar en hardware real.** Solo conozco los nombres del equipo AMD. Los nombres Intel y NVIDIA de la tabla vienen de LHM de memoria. Mitigación: los tests usan fixtures sintéticos, la regla `any.fallback` envía lo desconocido al detalle (sin romper), y el usuario siempre puede subir sensores con overrides. Si hay acceso a otros equipos, correr `tools\dump_sensors.ps1` y añadir los resultados como fixtures.
6. **`auto:*` y varias GPU.** Los pins `auto:gpu_*` seguirán resolviendo el primer `GPU Core`. No se cambia en esta fase.
7. **Pins y rendimiento de la barra.** Hasta 32 píldoras compactan y colapsan a "+K" (comportamiento ya existente). En el equipo del usuario, fijar todo el resumen supera el espacio de la barra, es decisión suya.
8. **Ancla izquierda con Taskbar Battery Widget también a la izquierda.** El diseño añade `docked_edge_left`, pero el número real de botones de la barra no es conocible: queda como limitación documentada, igual que en la referencia (tope de un cuarto de la barra).
9. **Tamaño de la tarea T8.** Es la más grande y toca `panel.py` después de T6. Si el worker falla dos veces, dividir en T8a y T8b como se indica.
10. **Degradación de config.** Una versión nueva guardada y una anterior ejecutada pierde claves desconocidas al guardar. Aceptado y registrado en el log.
11. **Fuga de datos personales.** `tech_info` y los logs nuevos deben sustituir rutas con el nombre de usuario y no incluir nombre de equipo. Hay un test específico.
12. **Estilos de icono.** La referencia tiene `retro` y `battery`, que no tienen sentido para glifos de hardware; aquí se limitan a outline, filled, simple y none.

## Archivos relevantes (todos ya existen salvo los marcados como nuevos en el plan)
- `D:\Proyectos\monitor\docs\spec.md`, `docs\PACKAGING.md`, `docs\SECURITY_NOTES.md`
- `D:\Proyectos\monitor\hardware_monitor_widget\` (`config.py`, `models.py`, `panel.py`, `widget.py`, `tray.py`, `app.py`, `pins.py`, `colors.py`, `icons.py`, `i18n.py`, `startup.py`, `sensorhost.py`, `taskbar.py`, `win_window.py`)
- `D:\Proyectos\monitor\packaging\installer.iss`
- `D:\Proyectos\monitor\sensors-admin.jsonl` (fuente del fixture `tests\data\sensors-admin.jsonl`)
- Referencia en `D:\Proyectos\Taskbar Battery Widget\taskbar_battery_widget\`: `settings_model.py`, `settings_view.py`, `tray.py`, `device_panel.py`, `colors.py`, `custom_colors_dialog.py`, `custom_icons.py`, `custom_icons_dialog.py`, `dialogs.py`, `placement.py`, `widget.py`, `updater.py`, `ed25519.py`, `about.py`, `config.py`, `i18n.py`; y `D:\Proyectos\Taskbar Battery Widget\tools\signing\`.