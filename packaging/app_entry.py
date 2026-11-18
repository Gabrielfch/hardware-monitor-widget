"""PyInstaller entry point."""
import os
import sys

# Must run before any Qt / package import: restrict DLL search to system dirs + dirs added explicitly
# (LOAD_LIBRARY_SEARCH_DEFAULT_DIRS) and never resolve DLLs/relative paths from a user-controlled cwd.
if sys.platform == "win32" and getattr(sys, "frozen", False):
    try:
        import ctypes
        ctypes.WinDLL("kernel32", use_last_error=True).SetDefaultDllDirectories(0x00001000)
    except (OSError, AttributeError):
        pass
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))

from hardware_monitor_widget.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
