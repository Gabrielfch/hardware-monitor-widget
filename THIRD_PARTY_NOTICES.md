# Third-party notices

## LibreHardwareMonitorLib 0.9.6
- License: MPL-2.0 (Mozilla Public License 2.0)
- Source: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
- Used unmodified by `native/SensorHost` and shipped as `LibreHardwareMonitorLib.dll` next to `SensorHost.exe`.
  Under MPL-2.0 the source of that library is available at the link above.

## Dependencies restored from NuGet (exact versions, from `SensorHost.deps.json`)
Licenses below are the ones we expect; entries marked "pending verification" have NOT been confirmed
against the package's own license file and must be checked before any public distribution.

| Package | Version | License | Status |
| --- | --- | --- | --- |
| HidSharp | 2.6.4 | Apache-2.0 | pending verification |
| System.Management | 10.0.2 | MIT (.NET Foundation) | pending verification |
| System.IO.Ports | 10.0.3 | MIT (.NET Foundation) | pending verification |
| System.CodeDom | 10.0.2 | MIT (.NET Foundation) | pending verification |
| System.Threading.AccessControl | 10.0.3 | MIT (.NET Foundation) | pending verification |
| DiskInfoToolkit | 1.1.2 | MIT | pending verification |
| RAMSPDToolkit-NDD | 1.4.2 | MIT | pending verification |
| BlackSharp.Core | 1.0.7 | MIT | pending verification |
| Mono.Posix.NETStandard | 1.0.0 | MIT | pending verification |

Note: Apache-2.0 (HidSharp) requires that the LICENSE and NOTICE files of the package are kept and
shipped with any redistribution of `HidSharp.dll`. Add them to the distribution folder when packaging.

The native helpers `MonoPosixHelper.dll` / `libMonoPosixHelper.dll` are excluded from the publish output
(Linux/macOS only). Exact restored versions are pinned in `native/SensorHost/packages.lock.json`.

## PawnIO (system driver, not redistributed)
LibreHardwareMonitorLib uses the PawnIO ring-0 driver to read CPU model-specific registers. PawnIO is
installed separately by the user (https://pawnio.eu), is licensed GPL-2.0 with an exception for IOCTL
use (pending verification), and is NOT bundled or redistributed with this application.

## Código adaptado de Taskbar Battery Widget (MIT)
Partes de esta aplicación (geometría de la barra de tareas y z-order, paleta de colores, panel de tarjetas,
iconos y colores personalizados, ajustes, actualizador y herramientas de firma) están adaptadas de
**Taskbar Battery Widget**, del mismo autor (Gabrielfch), publicado bajo licencia MIT. El texto de la licencia
de este proyecto está en `LICENSE`.

## Ed25519 (RFC 8032)
`hardware_monitor_widget/ed25519.py` es una implementación en Python puro de Ed25519 basada en el código de
referencia de la sección 6 del RFC 8032 (https://www.rfc-editor.org/rfc/rfc8032), usada para verificar la firma
de las actualizaciones y por la herramienta de firma de releases. Los vectores de prueba proceden del mismo RFC.
No añade dependencias externas.

## Dependencias de Python
La aplicación usa PySide6 (Qt for Python, LGPL-3.0 / GPL / comercial; se usa como biblioteca dinámica sin
modificar) y pywin32 (PSF-2.0). PyInstaller (GPL-2.0 con excepción de empaquetado) solo se usa para construir
los binarios. Las versiones exactas están en `requirements.txt`.
