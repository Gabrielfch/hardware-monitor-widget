"""Administrator detection and UAC relaunch (needed for CPU temperature via PawnIO)."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

PACKAGE = "hardware_monitor_widget"
ELEVATED_FLAG = "--elevated"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _interpreter() -> str:
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.basename(exe).lower() == "python.exe" and os.path.exists(pyw):
        return pyw
    return exe


def _shell_execute(verb, file, params, cwd) -> int:
    fn = ctypes.windll.shell32.ShellExecuteW
    fn.restype = wintypes.HINSTANCE
    fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                   wintypes.LPCWSTR, ctypes.c_int]
    result = fn(None, verb, file, params, cwd, 1)
    return int(result or 0)


def project_root() -> str:
    """Folder that contains the ``hardware_monitor_widget`` package."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def python_launch_args(extra: list[str]) -> list[str]:
    """Full command line (interpreter first) that runs the package without depending on the cwd.

    ``-I`` (isolated mode) ignores PYTHON* variables, the user site and the script directory.
    """
    code = (f"import sys,runpy; sys.path.insert(0, {project_root()!r}); "
            f"sys.argv[0]={PACKAGE!r}; "
            f"runpy.run_module({PACKAGE!r}, run_name='__main__', alter_sys=True)")
    return [_interpreter(), "-I", "-c", code, *extra]


def relaunch_as_admin() -> bool:
    """Start this app again elevated (UAC prompt). True if the launch was accepted (> 32).

    The elevated copy receives ONLY ``--elevated``: user arguments are never forwarded.
    """
    if getattr(sys, "frozen", False):
        file = sys.executable
        params = subprocess.list2cmdline([ELEVATED_FLAG])
        cwd = os.path.dirname(sys.executable)
    else:
        argv = python_launch_args([ELEVATED_FLAG])
        file = argv[0]
        params = subprocess.list2cmdline(argv[1:])
        cwd = project_root()
    try:
        return _shell_execute("runas", file, params, cwd) > 32
    except (AttributeError, OSError):
        return False


def ensure_admin_or_relaunch(argv: list[str] | None = None) -> bool:
    """True if an elevated copy was launched (caller should exit). False if already admin, if this
    already is the elevated relaunch (loop guard) or the user cancelled UAC (the app continues
    without admin and shows the needs_admin status)."""
    if is_admin():
        return False
    if ELEVATED_FLAG in (sys.argv if argv is None else argv):
        return False
    return relaunch_as_admin()
