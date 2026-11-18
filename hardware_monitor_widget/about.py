"""About: technical info (no personal data) and license viewer. The update logic lives elsewhere."""
from __future__ import annotations

import logging
import os
import platform
import re
import sys
from pathlib import Path

from . import __version__, startup
from .i18n import get_language, tr

log = logging.getLogger(__name__)

LHM_VERSION = "0.9.6"          # LibreHardwareMonitorLib bundled with the sensor host (see THIRD_PARTY_NOTICES.md)
_MAX_LICENSE_BYTES = 512 * 1024
_SEPARATOR = "\n\n" + "-" * 60 + "\n\n"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return project_root()


def _locate(name: str) -> Path:
    """File next to the exe; packaged builds also carry a copy under PyInstaller's data dir."""
    primary = _app_dir() / name
    if primary.is_file() or not getattr(sys, "frozen", False):
        return primary
    meipass = getattr(sys, "_MEIPASS", None)
    alt = Path(meipass) / name if meipass else None
    return alt if alt is not None and alt.is_file() else primary


def notices_path() -> Path:
    """THIRD_PARTY_NOTICES.md next to the exe (frozen) or in the repository root (dev)."""
    return _locate("THIRD_PARTY_NOTICES.md")


def license_path() -> Path:
    return _locate("LICENSE")


def _read_text(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(_MAX_LICENSE_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def license_text() -> str:
    """LICENSE followed by the third-party notices (empty parts are skipped)."""
    parts = [t.strip() for t in (_read_text(license_path()), _read_text(notices_path())) if t.strip()]
    return _SEPARATOR.join(parts)


def windows_build() -> str:
    try:
        return str(sys.getwindowsversion().build)        # type: ignore[attr-defined]
    except AttributeError:
        return platform.version() or "?"


def _yes_no(value) -> str:
    return tr("about_yes") if value else tr("about_no")


def _scale_text() -> str:
    try:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return tr("about_unknown")
        return f"{round(screen.devicePixelRatio() * 100)}% ({round(screen.logicalDotsPerInch())} DPI)"
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return tr("about_unknown")


def scrub(text: str) -> str:
    """Remove user/computer names and profile paths from a piece of text."""
    repl = []
    home = os.path.expanduser("~")
    if home and home != "~":
        repl.append((home, "%USERPROFILE%"))
    for var, token in (("USERPROFILE", "%USERPROFILE%"), ("APPDATA", "%APPDATA%"),
                       ("LOCALAPPDATA", "%LOCALAPPDATA%")):
        if os.environ.get(var):
            repl.append((os.environ[var], token))
    for old, new in sorted(repl, key=lambda r: -len(r[0])):
        text = re.sub(re.escape(old), lambda _m, n=new: n, text, flags=re.IGNORECASE)
    for secret in (os.environ.get("USERNAME"), os.environ.get("COMPUTERNAME")):
        if secret and len(secret) >= 3:
            text = re.sub(re.escape(secret), "<redacted>", text, flags=re.IGNORECASE)
    return text


def tech_info(hello: dict | None = None) -> str:
    """"Label: value" lines for support. ``hello`` is the sensor host's hello message, if known."""
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion
    hello = hello if isinstance(hello, dict) else None
    mode = {"installed": "about_mode_installed", "portable": "about_mode_portable"}.get(
        startup.install_mode(), "about_mode_dev")
    rows = [
        (tr("about_app"), __version__),
        (tr("about_mode"), tr(mode)),
        (tr("about_windows"), windows_build()),
        (tr("about_python"), platform.python_version()),
        (tr("about_qt"), f"{pyside_version} / {qVersion()}"),
        (tr("about_lhm"), str((hello or {}).get("lhm_version") or LHM_VERSION)),
    ]
    if hello is not None:
        if hello.get("dotnet"):
            rows.append((tr("about_dotnet"), str(hello["dotnet"])))
        rows.append((tr("about_admin"), _yes_no(hello.get("admin"))))
        if hello.get("pawnio"):
            rows.append((tr("about_pawnio"), str(hello.get("pawnio_version") or tr("about_yes"))))
        else:
            rows.append((tr("about_pawnio"), tr("about_not_installed")))
    else:
        try:
            from .elevation import is_admin
            rows.append((tr("about_admin"), _yes_no(is_admin())))
        except Exception:  # noqa: BLE001
            rows.append((tr("about_admin"), tr("about_unknown")))
    rows.append((tr("about_scale"), _scale_text()))
    rows.append((tr("about_language"), get_language()))
    return scrub("\n".join(f"{k}: {v}" for k, v in rows))


def show_licenses(parent=None) -> None:
    """Read-only dialog with LICENSE and the third-party notices."""
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout
    from .dialogs import app_dialog_guard
    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("about_licenses_title"))
    dlg.resize(640, 520)
    lay = QVBoxLayout(dlg)
    view = QPlainTextEdit(license_text() or tr("about_licenses_missing"))
    view.setReadOnly(True)
    view.setAccessibleName(tr("about_licenses_title"))
    lay.addWidget(view)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("about_licenses_close"))
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    with app_dialog_guard():
        dlg.exec()
