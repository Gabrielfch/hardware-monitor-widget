# PyInstaller spec (onedir ONLY, never --onefile: onefile unpacks into a user-writable temp folder that
# would then be executed elevated). Usage: pyinstaller packaging/hardware_monitor_widget.spec
# Environment: HMW_SENSORHOST_DIR = folder with the self-contained SensorHost publish
#              (default packaging/build/sensorhost, produced by packaging/build.ps1).
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH)
ROOT = SPEC_DIR.parent
SENSORHOST = Path(os.environ.get("HMW_SENSORHOST_DIR") or (SPEC_DIR / "build" / "sensorhost"))
if not (SENSORHOST / "SensorHost.exe").is_file():
    raise SystemExit(f"SensorHost.exe not found in {SENSORHOST}: run packaging/build.ps1")

hiddenimports = collect_submodules("hardware_monitor_widget")

EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2", "PySide6.QtQml", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSql",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSpatialAudio", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    "PySide6.QtXml", "PySide6.QtConcurrent", "PySide6.QtDBus", "PySide6.QtPrintSupport",
    "PySide6.QtUiTools", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets", "PySide6.QtHttpServer",
    "PySide6.QtLocation", "PySide6.QtNetworkAuth", "PySide6.QtShaderTools",
    "tkinter", "unittest", "pytest", "test",
]

# Binaries/data that a QtCore/QtGui/QtWidgets-only app never needs.
DROP = (
    "opengl32sw", "qt6quick", "qt6qml", "qt6pdf", "qt6network", "qt6opengl", "qt6svg",
    "qt6virtualkeyboard", "qt6webengine", "qt6multimedia", "qt6charts", "qt6datavis",
    "d3dcompiler", "translations", "qtquick", "qml", r"imageformats\qpdf", r"imageformats\qsvg",
    r"iconengines\qsvgicon", "ffmpeg", "avcodec", "avformat", "avutil", "swresample", "swscale",
)


def keep(entry):
    name = entry[0].lower().replace("/", "\\")
    return not any(d in name for d in DROP)


# Self-contained SensorHost (+ .NET runtime) -> <bundle>/hardware_monitor_widget/native/sensorhost/
sensorhost_files = []
for dirpath, _dirs, files in os.walk(SENSORHOST):
    rel = os.path.relpath(dirpath, SENSORHOST)
    dest = "hardware_monitor_widget/native/sensorhost" + ("" if rel == "." else "/" + rel.replace("\\", "/"))
    for f in files:
        sensorhost_files.append((str(Path(dirpath) / f), dest))

legal = [(str(ROOT / n), ".") for n in ("LICENSE", "THIRD_PARTY_NOTICES.md") if (ROOT / n).is_file()]

a = Analysis(
    [str(SPEC_DIR / "app_entry.py")],
    pathex=[str(ROOT)],
    datas=sensorhost_files + legal,
    hiddenimports=hiddenimports,
    excludes=EXCLUDES,
    noarchive=False,
)
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="HardwareMonitorWidget",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=True,         # manifest requireAdministrator: installed and portable copies both ask for UAC
    icon=str(SPEC_DIR / "app.ico"),
    version=str(SPEC_DIR / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="HardwareMonitorWidget")
