"""Filesystem helpers for folders that an ELEVATED process trusts (updater downloads, custom icons).

A folder is trusted only when it was created by an administrator (owner SYSTEM / Administrators, because the
owner always has implicit WRITE_DAC), has a DACL no non-administrator can write to, and neither it nor any
component of its path below %ProgramData% is a symlink / junction / other reparse point.
``make_private_dir`` creates such a folder atomically (the protected DACL is part of the CreateDirectory call,
so there is no window in which it exists with default permissions).
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from . import startup

log = logging.getLogger(__name__)

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
TRUSTED_OWNER_SIDS = ("S-1-5-18", "S-1-5-32-544")       # SYSTEM, Administrators


def _sid(text: str):
    import win32security
    return win32security.ConvertStringSidToSid(text)


def _private_attributes():
    """SECURITY_ATTRIBUTES: protected DACL = SYSTEM and Administrators full control, Users read only."""
    import ntsecuritycon as con
    import pywintypes
    import win32security
    inherit = win32security.CONTAINER_INHERIT_ACE | win32security.OBJECT_INHERIT_ACE
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, inherit, con.FILE_ALL_ACCESS, _sid("S-1-5-18"))
    dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, inherit, con.FILE_ALL_ACCESS,
                               _sid("S-1-5-32-544"))
    dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, inherit,
                               con.FILE_GENERIC_READ | con.FILE_GENERIC_EXECUTE, _sid("S-1-5-32-545"))
    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    sd.SetSecurityDescriptorControl(win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
    sa = pywintypes.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = sd
    return sa


def make_private_dir(path: Path) -> None:
    """Create ``path`` (it must not exist) with the protected admin-only DACL, atomically. Raises OSError."""
    try:
        import win32file
        win32file.CreateDirectory(str(path), _private_attributes())
    except OSError:
        raise
    except Exception as exc:                  # pywintypes.error is not an OSError subclass
        raise OSError(f"cannot create a protected folder: {exc.__class__.__name__}") from exc


def owner_trusted(path: Path) -> bool:
    """Owner is SYSTEM or Administrators (an object pre-created by a normal user would not be)."""
    try:
        import win32security
        sd = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                                win32security.OWNER_SECURITY_INFORMATION)
        owner = win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
    except Exception:                          # noqa: BLE001 - fail closed
        return False
    return owner in TRUSTED_OWNER_SIDS


def is_reparse_point(path: Path) -> bool:
    """Symlink, junction or any reparse point (looked at with lstat, never followed). Errors count as True."""
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(st.st_mode)


def _program_data() -> Path | None:
    base = os.environ.get("ProgramData")
    return Path(os.path.abspath(base)) if base else None


def _norm_parts(p: Path) -> tuple:
    return tuple(part.lower() for part in p.parts)


def _components_to_check(p: Path) -> list[Path]:
    """The path itself and, when it lives under %ProgramData%, every component from %ProgramData% down."""
    base = _program_data()
    if base is not None:
        bp, pp = _norm_parts(base), _norm_parts(p)
        if len(pp) >= len(bp) and pp[:len(bp)] == bp:
            comps = [base]
            for part in p.parts[len(bp):]:
                comps.append(comps[-1] / part)
            return comps
    return [p]


def admin_only(path) -> bool:
    """Real folder: no reparse point in any component under %ProgramData%, owner SYSTEM/Administrators and a
    DACL no non-administrator can write to."""
    try:
        p = Path(os.path.abspath(path))
        if not p.is_dir():
            return False
        if any(is_reparse_point(c) for c in _components_to_check(p)):
            return False
    except OSError:
        return False
    return owner_trusted(p) and bool(startup.is_path_admin_only(str(p)))
