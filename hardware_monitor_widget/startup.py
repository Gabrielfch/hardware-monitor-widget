"""Start-with-Windows.

* installed (exe under Program Files, from SHGetKnownFolderPath, and its DACL grants no write access to
  non-admins): a scheduled task ``HardwareMonitorWidget`` (logon trigger, highest privileges, current user only)
  so the elevated app starts without a UAC prompt on every logon. If the DACL check fails the task is not
  created and the HKCU Run key is used instead.
* portable / dev: the per-user Run key (HKCU). NEVER a high-privilege task: the folder may be user-writable.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Task Manager / Settings > Startup apps keep the user's on/off choice here (value name = Run value name).
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
APPROVED_ENABLED = bytes([0x02] + [0] * 11)
VALUE_NAME = "HardwareMonitorWidget"
TASK_NAME = "HardwareMonitorWidget"
SCHTASKS_TIMEOUT = 15

# FOLDERID_ProgramFiles, FOLDERID_ProgramFilesX86, FOLDERID_ProgramFilesX64
_PROGRAM_FILES_FOLDER_IDS = (
    "{905e63b6-c1bf-494e-b29c-65b732d3d21a}",
    "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}",
    "{6D809377-6AF0-444b-8957-A3773F02200E}",
)


# ---- mode detection
def _norm_path(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def _known_folder(folder_id: str) -> str | None:
    """SHGetKnownFolderPath (never the environment, which a user process can falsify)."""
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    guid = GUID.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    ptr = ctypes.c_void_p()
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ptr)) != 0 or not ptr.value:
            return None
        return ctypes.wstring_at(ptr.value)
    finally:
        if ptr.value:
            ole32.CoTaskMemFree(ptr)


def _program_files_dirs() -> list[str]:
    dirs: list[str] = []
    for fid in _PROGRAM_FILES_FOLDER_IDS:
        try:
            v = _known_folder(fid)
        except (AttributeError, OSError, ValueError):
            v = None
        if v:
            n = _norm_path(os.path.realpath(v))
            if n not in dirs:
                dirs.append(n)
    return dirs


def _is_under(path: str, base: str) -> bool:
    try:
        return path != base and os.path.commonpath([path, base]) == base
    except ValueError:      # different drives
        return False


def install_mode() -> str:
    """``"installed"`` (frozen exe under Program Files), ``"portable"`` (frozen elsewhere) or ``"dev"``."""
    if not getattr(sys, "frozen", False):
        return "dev"
    exe = _norm_path(os.path.realpath(sys.executable))     # resolves junctions / symlinks
    if any(_is_under(exe, base) for base in _program_files_dirs()):
        return "installed"
    return "portable"


# ---- DACL check
# Users, Authenticated Users, Everyone, INTERACTIVE
_UNTRUSTED_SIDS = frozenset({"S-1-5-32-545", "S-1-5-11", "S-1-1-0", "S-1-5-4"})
_WRITE_MASK = (0x00000002        # FILE_WRITE_DATA
               | 0x00000004      # FILE_APPEND_DATA
               | 0x00000040      # FILE_DELETE_CHILD
               | 0x00010000      # DELETE
               | 0x00040000      # WRITE_DAC
               | 0x00080000      # WRITE_OWNER
               | 0x40000000      # GENERIC_WRITE
               | 0x10000000)     # GENERIC_ALL
_INHERIT_ONLY_ACE = 0x08


def _read_dacl(path: str) -> list[tuple[int, int, int, str]]:
    """``[(ace_type, ace_flags, mask, sid_string)]`` of the path's DACL (pywin32)."""
    import win32security
    sd = win32security.GetNamedSecurityInfo(
        path, win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION)
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:                 # NULL DACL = everyone has full access
        return [(0, 0, 0x10000000, "S-1-1-0")]
    out = []
    for i in range(dacl.GetAceCount()):
        (ace_type, ace_flags), mask, sid = dacl.GetAce(i)[:3]
        out.append((ace_type, ace_flags, mask, win32security.ConvertSidToStringSid(sid)))
    return out


def _current_user_sid() -> str:
    import win32api
    import win32con
    import win32security
    tok = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(tok, win32security.TokenUser)[0])


def is_path_admin_only(path: str) -> bool:
    """True when the DACL of *path* grants no write/modify right to Users, Authenticated Users, Everyone,
    INTERACTIVE or the current user. Any error reading it counts as "not admin-only" (fail closed)."""
    try:
        untrusted = _UNTRUSTED_SIDS | {_current_user_sid()}
        is_dir = os.path.isdir(path)
        for ace_type, ace_flags, mask, sid in _read_dacl(path):
            if ace_type != 0:                       # only ACCESS_ALLOWED_ACE_TYPE grants rights
                continue
            if is_dir and ace_flags & _INHERIT_ONLY_ACE:
                continue                            # applies to children only, not to this object
            if sid in untrusted and mask & _WRITE_MASK:
                return False
        return True
    except Exception as exc:        # noqa: BLE001 - fail closed on any failure (pywin32 missing, access denied...)
        log.warning("could not verify permissions of %s: %s", path, exc.__class__.__name__)
        return False


class UnsafeLocationError(OSError):
    """The executable (or its folder) can be modified by non-administrators."""


# ---- scheduled task (installed mode)
def _system32() -> str:
    buf = ctypes.create_unicode_buffer(260)
    n = ctypes.windll.kernel32.GetSystemDirectoryW(buf, 260)
    if n <= 0 or n >= 260:
        raise OSError("GetSystemDirectoryW failed")
    return buf.value


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [os.path.join(_system32(), "schtasks.exe"), *args],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False, timeout=SCHTASKS_TIMEOUT, capture_output=True)


def current_user() -> str:
    """``DOMAIN\\user`` of the current user."""
    try:
        buf = ctypes.create_unicode_buffer(256)
        size = ctypes.c_ulong(256)
        if ctypes.windll.secur32.GetUserNameExW(2, buf, ctypes.byref(size)) and buf.value:   # NameSamCompatible
            return buf.value
    except (AttributeError, OSError):
        pass
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if domain and user else user


def task_xml(exe: str, user: str) -> str:
    exe_x, user_x = escape(exe), escape(user)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts Hardware Monitor Widget at logon (elevated, no UAC prompt).</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user_x}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user_x}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>false</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>"{exe_x}"</Command>
      <WorkingDirectory>{escape(os.path.dirname(exe))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _write_task_xml(exe: str) -> str:
    """Write the XML with a random name (mkstemp: O_CREAT|O_EXCL) inside the application folder, which
    create_task has verified is admin-only, so a user process cannot swap the file before schtasks reads it."""
    data = task_xml(exe, current_user()).encode("utf-16")      # BOM + UTF-16 LE
    fd, path = tempfile.mkstemp(prefix="startup-task-", suffix=".xml", dir=os.path.dirname(exe))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


def task_exists() -> bool:
    try:
        return _run_schtasks(["/Query", "/TN", TASK_NAME]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def create_task() -> None:
    exe = os.path.realpath(sys.executable)
    if not (is_path_admin_only(os.path.dirname(exe)) and is_path_admin_only(exe)):
        raise UnsafeLocationError("the application folder or executable is writable by non-administrators")
    xml = _write_task_xml(exe)
    try:
        r = _run_schtasks(["/Create", "/TN", TASK_NAME, "/XML", xml, "/F"])
    except subprocess.SubprocessError as exc:
        raise OSError(f"schtasks failed: {exc.__class__.__name__}") from exc
    finally:
        try:
            os.remove(xml)
        except OSError:
            pass
    if r.returncode != 0:
        raise OSError(f"schtasks /Create failed (exit {r.returncode})")


def delete_task() -> None:
    try:
        _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])      # non-zero when it does not exist: fine
    except (OSError, subprocess.SubprocessError):
        pass


# ---- Run key (portable / dev, and legacy cleanup)
def build_command() -> str:
    """`pythonw -I -c ...` running the package (or the frozen executable); independent of the cwd."""
    from . import elevation
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable])
    return subprocess.list2cmdline(elevation.python_launch_args([]))


def registered_command() -> str | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, VALUE_NAME)
        return value if isinstance(value, str) else None
    except OSError:
        return None


def _normalize(cmd: str) -> str:
    return cmd.strip().strip('"').strip().casefold()


def approved_disabled() -> bool:
    """True when the user turned the entry off in Task Manager / Settings (StartupApproved first byte odd)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as k:
            value, _ = winreg.QueryValueEx(k, VALUE_NAME)
        return isinstance(value, (bytes, bytearray)) and len(value) > 0 and bool(value[0] & 1)
    except OSError:
        return False


def run_key_enabled() -> bool:
    cur = registered_command()
    return cur is not None and _normalize(cur) == _normalize(build_command()) and not approved_disabled()


def _delete_run_value() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, VALUE_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def _set_run_value() -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, build_command())
    if approved_disabled():
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_BINARY, APPROVED_ENABLED)


def is_enabled() -> bool:
    if install_mode() == "installed":
        return task_exists() or run_key_enabled()      # Run key = fallback when the task was refused
    return run_key_enabled()


def set_enabled(enabled: bool) -> None:
    """Apply the setting for the current install mode. Raises OSError when it could not be applied."""
    if install_mode() == "installed":
        if enabled:
            try:
                create_task()
            except UnsafeLocationError as exc:
                log.warning("not creating the elevated startup task (%s); using the HKCU Run key", exc)
                _set_run_value()
                return
            _delete_run_value()         # never start twice (Run entry + task)
        else:
            delete_task()
            _delete_run_value()
        return
    # portable / dev: only the HKCU Run key; tasks (possibly from an installed copy) are never touched
    if enabled:
        _set_run_value()
    else:
        _delete_run_value()
