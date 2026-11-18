import os
import subprocess

import pytest

from hardware_monitor_widget import secure_fs, startup


def test_admin_only_requires_trusted_owner_and_dacl(tmp_path, monkeypatch):
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(startup, "is_path_admin_only", lambda p: True)
    monkeypatch.setattr(secure_fs, "owner_trusted", lambda p: True)
    assert secure_fs.admin_only(d) is True
    monkeypatch.setattr(secure_fs, "owner_trusted", lambda p: False)
    assert secure_fs.admin_only(d) is False                   # owner has implicit WRITE_DAC
    monkeypatch.setattr(secure_fs, "owner_trusted", lambda p: True)
    monkeypatch.setattr(startup, "is_path_admin_only", lambda p: False)
    assert secure_fs.admin_only(d) is False
    monkeypatch.setattr(startup, "is_path_admin_only", lambda p: True)
    assert secure_fs.admin_only(tmp_path / "missing") is False
    f = tmp_path / "f"
    f.write_text("x")
    assert secure_fs.admin_only(f) is False                   # a file is not a folder


def test_admin_only_rejects_reparse_in_any_component_under_programdata(tmp_path, monkeypatch):
    pd = tmp_path / "pd"
    leaf = pd / "App" / "updates"
    leaf.mkdir(parents=True)
    monkeypatch.setenv("ProgramData", str(pd))
    monkeypatch.setattr(startup, "is_path_admin_only", lambda p: True)
    monkeypatch.setattr(secure_fs, "owner_trusted", lambda p: True)
    assert secure_fs.admin_only(leaf) is True
    for bad in (pd, pd / "App", leaf):
        monkeypatch.setattr(secure_fs, "is_reparse_point", lambda p, bad=bad: p == bad)
        assert secure_fs.admin_only(leaf) is False, bad


def test_is_reparse_point_detects_junction(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot create junction")
    try:
        assert secure_fs.is_reparse_point(link) is True
        assert secure_fs.is_reparse_point(target) is False
        assert secure_fs.is_reparse_point(tmp_path / "missing") is True      # fail closed
    finally:
        os.rmdir(link)


@pytest.mark.skipif(os.name != "nt", reason="Windows DACLs")
def test_private_dir_has_protected_admin_only_dacl(tmp_path):
    d = tmp_path / "private"
    secure_fs.make_private_dir(d)
    assert d.is_dir()
    assert startup.is_path_admin_only(str(d))                 # no write for Users/Everyone/current user
    aces = {sid: mask for t, f, mask, sid in startup._read_dacl(str(d)) if t == 0}
    assert set(aces) == {"S-1-5-18", "S-1-5-32-544", "S-1-5-32-545"}      # protected: nothing inherited
    assert aces["S-1-5-32-545"] & startup._WRITE_MASK == 0
    with pytest.raises(OSError):
        secure_fs.make_private_dir(d)                         # exists already: atomic create fails


@pytest.mark.skipif(os.name != "nt", reason="Windows DACLs")
def test_owner_trusted_matches_sid(tmp_path):
    d = tmp_path / "o"
    d.mkdir()
    import win32security
    sd = win32security.GetNamedSecurityInfo(str(d), win32security.SE_FILE_OBJECT,
                                            win32security.OWNER_SECURITY_INFORMATION)
    owner = win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
    assert secure_fs.owner_trusted(d) is (owner in secure_fs.TRUSTED_OWNER_SIDS)
    assert secure_fs.owner_trusted(tmp_path / "missing") is False
