# Empaquetado

## Compilar
Requisitos: Python del venv (`.venv`) con PySide6, PyInstaller y pytest; .NET SDK; Inno Setup 6
(`ISCC.exe`, o variable de entorno `ISCC`).

    powershell -ExecutionPolicy Bypass -File packaging\build.ps1            # build completo
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -SkipTests # sin ejecutar pytest

Pasos: versión desde `hardware_monitor_widget.__version__` -> `version_info.txt` -> tests -> `app.ico`
(`packaging\make_icon.py`) -> publicación **self-contained** de SensorHost (`native\build.ps1 -SelfContained`,
no exige .NET en el equipo destino) -> PyInstaller **onedir** -> Inno Setup -> ZIP portable -> `SHA256SUMS.txt`.

## Qué produce (`dist\`)
- `HardwareMonitorWidget-Setup-<ver>.exe`: instalador (Inno Setup, español/inglés).
- `HardwareMonitorWidget-portable-<ver>.zip`: carpeta `HardwareMonitorWidget\` (onedir) + `LEEME-portable.txt`.
- `SHA256SUMS.txt`.

Intermedios en `packaging\build\` (ignorado por git). El SensorHost va en
`_internal\hardware_monitor_widget\native\sensorhost\` (`host_path()` lo busca ahí cuando `sys.frozen`).
Nunca se usa `--onefile` (extraería código a `%TEMP%`, escribible por el usuario, y se ejecutaría elevado).

## Instalar / desinstalar
- Instalar: ejecutar el Setup (pide UAC). Instala en `%ProgramFiles%\HardwareMonitorWidget`, crea acceso en el
  menú inicio (escritorio opcional) y la opción "Iniciar con Windows" (crea la tarea programada llamando a
  `HardwareMonitorWidget.exe --enable-startup`). Si PawnIO no está instalado muestra un aviso informativo con
  https://pawnio.eu/ (no se descarga ni se instala).
- Desinstalar: "Aplicaciones instaladas" o el desinstalador. Cierra la app, borra la tarea programada
  `HardwareMonitorWidget` y la carpeta. La configuración (`%APPDATA%`) no se toca.
- Banderas de la app (sin interfaz, salen al terminar): `--enable-startup`, `--disable-startup`.

## Instalado vs portable
| | Instalado | Portable |
| --- | --- | --- |
| Ubicación | `%ProgramFiles%` (solo escribible por administradores) | donde se extraiga |
| Administrador | siempre (manifiesto `requireAdministrator`) | siempre (mismo manifiesto) |
| Iniciar con Windows | tarea programada (logon, nivel más alto, solo el usuario actual): **sin UAC** en cada inicio | clave HKCU Run: **UAC en cada inicio** |
| Detección (`startup.install_mode()`) | ruta real del exe bajo Program Files (`SHGetKnownFolderPath`, no variables de entorno) y DACL sin escritura para no administradores | exe congelado fuera de ahí |

En modo `dev` (sin congelar) se usa la clave Run. La tarea con privilegios altos solo se crea en modo
instalado: jamás en portable/dev (carpeta potencialmente escribible = escalada silenciosa).

## Licencias en el paquete
`LICENSE` y `THIRD_PARTY_NOTICES.md` se instalan junto al exe (`installer.iss`) y también van en `_internal`
(PyInstaller). "Acerca de > Licencias" (`about.license_text()`) los busca junto al exe y, si no están (copia
portable), en `_internal`.

## Notas
- El instalador no permite elegir carpeta (`DisableDirPage=yes`, `UsePreviousAppDir=no`): siempre
  `%ProgramFiles%\HardwareMonitorWidget`. Si la carpeta/exe fueran escribibles por no administradores, la app
  no crea la tarea y usa la clave Run (aviso en el log).
- Instalar/actualizar/desinstalar cierra `HardwareMonitorWidget.exe` y `SensorHost.exe`.
- Firma de código: los binarios no están firmados (SmartScreen puede avisar).

## Release firmada (actualizaciones desde la app)
La app puede comprobar e instalar actualizaciones (Ajustes > Buscar actualizaciones). Está **deshabilitada y sin
tráfico de red** mientras `PUBLIC_KEYS` en `hardware_monitor_widget\release_info.py` esté vacío.

### Assets de cada release (`Gabrielfch/hardware-monitor-widget`, tag `vX.Y.Z`)
| Asset | Quién lo crea |
| --- | --- |
| `HardwareMonitorWidget-Setup-X.Y.Z.exe` | `packaging\build.ps1` |
| `HardwareMonitorWidget-portable-X.Y.Z.zip` | `packaging\build.ps1` |
| `SHA256SUMS.txt` | `packaging\build.ps1` (formato `sha256sum`, LF, sin BOM) |
| `SHA256SUMS.txt.sig` | `tools\signing\sign_release.py` (base64 de la firma Ed25519 de 64 bytes sobre los bytes exactos de `SHA256SUMS.txt`, una línea) |

La app solo instala si la release trae Setup, `SHA256SUMS.txt` y `.sig`, hay al menos una clave pública embebida y
corre en modo instalado. En cualquier otro caso ofrece abrir la página de la release.

### Procedimiento de claves (una vez)
1. Ejecuta `.venv\Scripts\python.exe tools\signing\gen_keys.py` y después otra vez con otra ruta
   (`... gen_keys.py D:\copia-offline\hmw_backup.key`). Cada ejecución guarda la clave privada **fuera del repositorio**
   (por defecto `%USERPROFILE%\.hmw-signing\hmw_release_ed25519.key`, restringida con `icacls`) y solo imprime la clave
   pública. Guarda la de respaldo en un USB sin conexión o en un gestor de contraseñas.
2. Copia las dos claves públicas (64 hex) en `PUBLIC_KEYS` de `release_info.py` y reconstruye. Los tests
   (`test_public_keys.py`) validan el formato.
3. Nunca subas ni compartas las privadas. Para rotar: publica una versión firmada con la clave vieja que embeba la nueva,
   y quita la vieja en una versión posterior. Si se filtra una privada, rota de inmediato con la de respaldo.

### Publicar una release
1. Sube `__version__` y ejecuta `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`.
2. Crea el borrador con los tres assets de `dist\`:
   `gh release create vX.Y.Z --repo Gabrielfch/hardware-monitor-widget --draft --title vX.Y.Z --notes-file NOTAS.md dist\HardwareMonitorWidget-Setup-X.Y.Z.exe dist\HardwareMonitorWidget-portable-X.Y.Z.zip dist\SHA256SUMS.txt`
3. Firma: `.venv\Scripts\python.exe tools\signing\sign_release.py vX.Y.Z` (descarga el borrador, recalcula los hashes, firma
   `SHA256SUMS.txt`, sube `SHA256SUMS.txt.sig` y vuelve a verificar exactamente como lo hace la app). Se niega a subir nada
   si la clave pública no está en `PUBLIC_KEYS`. Con `--attest` comprueba además attestations de GitHub (opcional: los
   binarios se compilan en local). Con `--publish` publica el borrador al terminar.
4. Verifica a mano si quieres: `.venv\Scripts\python.exe -m hardware_monitor_widget.ed25519 verify <clave-hex> SHA256SUMS.txt SHA256SUMS.txt.sig`.

### Cómo se actualiza una copia instalada
Descarga a `%ProgramData%\HardwareMonitorWidget\updates\HMW-update-<aleatorio>` (DACL protegida: SYSTEM y
Administradores control total, Usuarios solo lectura; se comprueba con `startup.is_path_admin_only` antes de descargar
y antes de lanzar), verifica la firma de `SHA256SUMS.txt`, el hash del instalador y lo vuelve a calcular justo antes de
lanzar `Setup.exe /SILENT /CLOSEAPPLICATIONS /LAUNCHAPP=1`. El instalador cierra la app, instala y la relanza
(`installer.iss`: entrada `[Run]` con `Check: ShouldRelaunchApp`, solo en modo silencioso con `/LAUNCHAPP=1`). Nunca hay
instalación automática: la comprobación diaria opcional solo avisa. El instalador no está firmado con Authenticode:
Smart App Control puede bloquearlo (la app lo detecta y ofrece la página de la release).
