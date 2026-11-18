# Security notes

## Install location
The app runs elevated (UAC) and starts `SensorHost.exe` as an elevated child. Any user who can write to the
application folder could replace the code that runs as administrator.

- **Installer** (`HardwareMonitorWidget-Setup-<ver>.exe`): `PrivilegesRequired=admin`, installs under
  `%ProgramFiles%\HardwareMonitorWidget` (writable only by administrators); the directory page is disabled. This is the supported way to run.
- The executable has a `requireAdministrator` manifest (PyInstaller `uac_admin=True`): installed and portable
  copies both ask for UAC on every launch; nothing relies on a user-writable relaunch path.
- **Portable ZIP**: extract it into a folder only administrators can write to (see `LEEME-portable.txt`).
- The development folder (`D:\Proyectos\monitor`) is writable by authenticated users: do not rely on the
  elevated mode there on a shared machine (privilege escalation risk).

## Start with Windows
- Installed mode (`startup.install_mode() == "installed"`): the real path of the exe (`os.path.realpath`, so
  junctions/symlinks are resolved) must be under Program Files as returned by `SHGetKnownFolderPath`
  (`FOLDERID_ProgramFiles`, `...X86`, `...X64`); the environment (`%ProgramFiles%`) is never trusted.
- Before creating the task, `is_path_admin_only()` reads the DACL (pywin32) of the exe and of its folder and
  refuses if Users, Authenticated Users, Everyone, INTERACTIVE or the current user have write/modify rights
  (FILE_WRITE_DATA, FILE_APPEND_DATA, DELETE, WRITE_DAC, WRITE_OWNER, GENERIC_WRITE, GENERIC_ALL). On refusal
  (or any error reading the DACL, fail closed) no task is created: the HKCU Run key is used and a warning is logged.
- Task `HardwareMonitorWidget`: logon trigger limited to the current user, `HighestAvailable`, interactive
  token, `AllowStartOnDemand=false`. The task XML is written with `tempfile.mkstemp` (random name, `O_EXCL`)
  inside the application folder (verified admin-only) and deleted in `finally` right after `schtasks /Create`.
- The task is created for the account that elevates (the user that approved UAC), which is the one that gets
  the no-prompt start (P2-6: documented behaviour, not a defect).
- Portable and dev: only the HKCU Run key (UAC at each start). A high-privilege task is NEVER created from a
  possibly user-writable folder.
- `schtasks.exe` is invoked by absolute path (`GetSystemDirectoryW`, not `%SystemRoot%`), with an argument
  list (no shell), `CREATE_NO_WINDOW`, 15 s timeout.

## Hardening status of the packaging review
- P1-1 (Program Files detection from the environment, XML race): resolved (known folders + realpath + DACL check).
- P1-2 (custom install directory): resolved. The installer has `DisableDirPage=yes`, `UsePreviousAppDir=no`;
  it always installs in `{autopf}\HardwareMonitorWidget`.
- P2-1 (schtasks by PATH/%SystemRoot%): resolved. P2-4 (`AllowStartOnDemand`): resolved (`false`).
- P2-2 (DLL search / cwd): `packaging/app_entry.py` calls `SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS)`
  and `chdir` to the exe folder before any other import.
- P2-3 (SensorHost left running during update/uninstall): resolved (`taskkill` for `SensorHost.exe` too).
- P2-5 (code signing): PENDING. Binaries are not signed.

## Packaging
- Do not build with PyInstaller `--onefile`: it unpacks into a user-writable temp folder (`_MEIPASS`) on every
  start, which would then be executed elevated. The build uses `--onedir`; `host_path()` resolves SensorHost
  inside the install folder (`_internal`), never `%TEMP%`.
- SensorHost is published self-contained (bundled .NET runtime); the elevated host does not load a runtime from
  user-controlled locations, and `DOTNET_*`/`COMPLUS_*`/`CORECLR_*`/`COREHOST_*` variables are removed from its
  environment.
- The installer does not download or install PawnIO; it only informs the user when it is missing.
- Binaries are not code-signed (P2-5, pending).

## Residual driver service
Some older hardware-monitoring tools (including old LibreHardwareMonitor versions) install a kernel driver service named
`WinRing0_1_2_0`. If it is present and you do not need it, remove it from an elevated prompt:

    sc delete WinRing0_1_2_0

This application uses PawnIO instead and does not need WinRing0.

## Other measures
- The elevated process is relaunched with only `--elevated` (user arguments are never forwarded).
- Config and log directories are not written to when they are junctions/symlinks.
- Config reads are limited to 256 KiB; host output lines to 1 MiB.

## Fase 2: actualizador, iconos y configuración v2
- **Actualizador** (`updater.py`): la aplicación corre elevada, así que el instalador descargado se ejecuta con
  privilegios. Defensas: solo en modo instalado (Program Files, escribible solo por administradores); descarga
  en `%ProgramData%\HardwareMonitorWidget\updates` con DACL protegida, comprobada con
  `startup.is_path_admin_only` antes de descargar y antes de lanzar; firma Ed25519 de `SHA256SUMS.txt` verificada
  **antes** que el hash; re-hash completo antes de lanzar; dos confirmaciones del usuario; redirecciones solo a
  hosts permitidos; topes de tamaño y de tiempo. Con `REPO = ""` o `PUBLIC_KEYS = ()` no hay tráfico de red.
  La clave privada de firma se guarda offline (hay una clave de respaldo) y nunca está en el repositorio.
- **Sin firma Authenticode**: Smart App Control puede bloquear el instalador; se detecta y se ofrece la página
  de la release.
- **Iconos personalizados**: se guardan re-codificados (PNG, solo primer fotograma) en
  `%ProgramData%\HardwareMonitorWidget\icons` (admin-only) y solo se cargan si la carpeta sigue siendo
  admin-only. Sin elevar no se pueden importar.
- **Configuración v2** (`%APPDATA%`, escribible por el usuario): validadores por clave, tope de 256 KiB, valores
  inválidos caen al default con un WARNING, y las claves de pins/overrides son opacas (nunca se interpretan como
  rutas ni comandos). Una versión anterior que guarde pierde las claves desconocidas (aceptado).
- **Datos leídos con privilegios**: config y logs viven en carpetas del usuario; no se escriben si son
  junctions/symlinks (`is_reparse_point`). El proceso elevado nunca ejecuta ni abre rutas procedentes de la config.
- **Acerca de**: la información técnica copiable no incluye nombre de usuario, nombre de equipo ni rutas de
  perfil (se sustituyen por `%USERPROFILE%`, `%APPDATA%`, `%LOCALAPPDATA%`; hay un test que lo comprueba).

## Fase 2: endurecimiento tras la revisión de seguridad (2026-11-18)
- `secure_fs.py` centraliza la creación atómica de carpetas con DACL protegida (`make_private_dir`), la comprobación de propietario (SYSTEM o Administradores), la detección de reparse points por componente y `admin_only(path)`. Lo usan el actualizador y los iconos personalizados.
- Iconos personalizados: la carpeta `%ProgramData%\HardwareMonitorWidget\icons` exige propietario de confianza (una carpeta pre-creada por un usuario estándar se rechaza y se desactiva la importación); solo se decodifican `.png`; cada archivo se comprueba (propietario, sin reparse) y se lee con tope real; los archivos nuevos nunca sobrescriben otros distintos.
- El instalador crea `%ProgramData%\HardwareMonitorWidget`, `\icons` y `\updates` con permisos solo de administradores (`icacls /inheritance:r`). No corrige el propietario de una carpeta pre-creada por otro usuario: la app la rechaza.
- Configuración: guardado con `mkstemp` + `os.replace` sin seguir enlaces; `set()` lanza `ConfigSaveError` si no se pudo guardar y restaura el valor; una configuración de una versión más nueva se carga en solo lectura; `loaded_ok=False` si el archivo existía y no se pudo leer (la limpieza de iconos no actúa en ese caso).
- Actualizador: limpieza de descargas antiguas solo con raíz admin-only y sin seguir reparse points; `discard_update` acotado a `updates\HMW-update-<hex>`; descarga con `"xb"`; redirecciones validadas fallando en cerrado; claves públicas de orden pequeño rechazadas.
- Firma de releases: `sign_release.py` compara los hashes del borrador con los artefactos locales de `dist\` y pide confirmación antes de firmar (`--yes` para omitirla).
