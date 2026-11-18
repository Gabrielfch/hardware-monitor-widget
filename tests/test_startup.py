import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from hardware_monitor_widget import startup


@pytest.fixture
def frozen(monkeypatch):
    def _set(exe):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", exe)
    return _set


@pytest.fixture
def pf(monkeypatch):
    monkeypatch.setattr(startup, "_known_folder", lambda fid: {
        startup._PROGRAM_FILES_FOLDER_IDS[0]: r"C:\Program Files",
        startup._PROGRAM_FILES_FOLDER_IDS[1]: r"C:\Program Files (x86)",
    }.get(fid))
    monkeypatch.setattr(os.path, "realpath", lambda p: os.path.normpath(p))


@pytest.fixture
def schtasks(monkeypatch):
    calls = []
    rc = {"value": 0}
    monkeypatch.setattr(startup, "_system32", lambda: r"C:\Windows\System32")

    def fake_run(args, **kw):
        calls.append((args, kw))
        return subprocess.CompletedProcess(args, rc["value"], b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls, rc


def test_mode_dev(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert startup.install_mode() == "dev"


@pytest.mark.parametrize("exe,mode", [
    (r"C:\Program Files\HardwareMonitorWidget\HardwareMonitorWidget.exe", "installed"),
    (r"c:\program files\HardwareMonitorWidget\..\HardwareMonitorWidget\x.exe", "installed"),
    (r"C:\Program Files (x86)\Foo\x.exe", "installed"),
    (r"C:\Program Files Evil\x.exe", "portable"),
    (r"C:\Users\me\Downloads\HardwareMonitorWidget\x.exe", "portable"),
    (r"D:\Program Files\x.exe", "portable"),
])
def test_mode_frozen(frozen, pf, exe, mode):
    frozen(exe)
    assert startup.install_mode() == mode


def test_mode_ignores_forged_environment(frozen, pf, monkeypatch):
    monkeypatch.setenv("ProgramFiles", r"C:\Users\me\evil")
    monkeypatch.setenv("ProgramW6432", r"C:\Users\me\evil")
    frozen(r"C:\Users\me\evil\HardwareMonitorWidget\x.exe")
    assert startup.install_mode() == "portable"


def test_mode_junction_resolving_outside_program_files_is_portable(frozen, monkeypatch):
    monkeypatch.setattr(startup, "_known_folder", lambda fid: r"C:\Program Files")
    real = os.path.realpath
    monkeypatch.setattr(os.path, "realpath",
                        lambda p: r"C:\Users\me\payload\x.exe" if "HardwareMonitorWidget" in p else real(p))
    frozen(r"C:\Program Files\HardwareMonitorWidget\x.exe")      # junction inside PF pointing outside
    assert startup.install_mode() == "portable"


def test_task_xml_is_valid_and_restricted():
    xml = startup.task_xml(r"C:\Program Files\HMW\HardwareMonitorWidget.exe", r"PC\jo&e")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(xml.split("?>", 1)[1])
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", ns).text == r"PC\jo&e"
    assert root.find("t:Principals/t:Principal/t:RunLevel", ns).text == "HighestAvailable"
    assert root.find("t:Principals/t:Principal/t:LogonType", ns).text == "InteractiveToken"
    s = root.find("t:Settings", ns)
    assert s.find("t:DisallowStartIfOnBatteries", ns).text == "false"
    assert s.find("t:StopIfGoingOnBatteries", ns).text == "false"
    assert s.find("t:ExecutionTimeLimit", ns).text == "PT0S"
    assert s.find("t:AllowStartOnDemand", ns).text == "false"
    assert "<AllowStartOnDemand>false</AllowStartOnDemand>" in xml
    assert root.find("t:Actions/t:Exec/t:Command", ns).text == r'"C:\Program Files\HMW\HardwareMonitorWidget.exe"'


def test_installed_enable_creates_task_and_removes_run(frozen, schtasks, monkeypatch, tmp_path):
    calls, _ = schtasks
    frozen(str(tmp_path / "HardwareMonitorWidget.exe"))
    monkeypatch.setattr(startup, "install_mode", lambda: "installed")
    monkeypatch.setattr(startup, "is_path_admin_only", lambda p: True)
    seen = {}
    monkeypatch.setattr(startup, "current_user", lambda: r"PC\me")
    real_write = startup._write_task_xml

    def spy(e):
        p = real_write(e)
        with open(p, "rb") as f:
            seen["xml"] = f.read()
        seen["path"] = p
        return p

    monkeypatch.setattr(startup, "_write_task_xml", spy)
    removed = []
    monkeypatch.setattr(startup, "_delete_run_value", lambda: removed.append(True))
    startup.set_enabled(True)
    args, kw = calls[0]
    assert args[:4] == [r"C:\Windows\System32\schtasks.exe", "/Create", "/TN", "HardwareMonitorWidget"]
    assert os.path.dirname(seen["path"]) == os.path.dirname(os.path.realpath(sys.executable))
    assert "/XML" in args and "/F" in args and isinstance(args, list)
    assert kw["check"] is False and kw["timeout"] == 15 and "shell" not in kw
    assert kw["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert seen["xml"].startswith(b"\xff\xfe") and r"PC\me".encode("utf-16-le") in seen["xml"]
    assert not os.path.exists(seen["path"])         # temp XML removed
    assert removed == [True]


def test_installed_create_failure_raises(frozen, schtasks, monkeypatch, tmp_path):
    _, rc = schtasks
    rc["value"] = 1
    frozen(str(tmp_path / "x.exe"))
    monkeypatch.setattr(startup, "install_mode", lambda: "installed")
    monkeypatch.setattr(startup, "is_path_admin_only", lambda p: True)
    with pytest.raises(OSError):
        startup.set_enabled(True)
    assert list(tmp_path.glob("startup-task-*.xml")) == []


def _dacl(*aces):
    return lambda path: list(aces)


@pytest.mark.parametrize("sid,mask", [
    ("S-1-5-32-545", 0x1301BF),     # Users: Modify
    ("S-1-5-11", 0x2),              # Authenticated Users: FILE_WRITE_DATA
    ("S-1-1-0", 0x10000000),        # Everyone: GENERIC_ALL
    ("S-1-5-4", 0x40000000),        # INTERACTIVE: GENERIC_WRITE
    ("S-1-5-21-1-2-3-1001", 0x40000),   # current user: WRITE_DAC
])
def test_is_path_admin_only_rejects_untrusted_write(monkeypatch, sid, mask):
    monkeypatch.setattr(startup, "_current_user_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(startup, "_read_dacl", _dacl((0, 0, 0x1F01FF, "S-1-5-18"), (0, 0, mask, sid)))
    assert startup.is_path_admin_only(r"C:\x") is False


def test_is_path_admin_only_accepts_read_execute_for_users(monkeypatch):
    monkeypatch.setattr(startup, "_current_user_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(startup, "_read_dacl", _dacl(
        (0, 0, 0x1F01FF, "S-1-5-18"), (0, 0, 0x1F01FF, "S-1-5-32-544"),
        (0, 0, 0x1200A9, "S-1-5-32-545"), (1, 0, 0x2, "S-1-1-0")))     # deny ACE ignored
    assert startup.is_path_admin_only(r"C:\x") is True


def test_is_path_admin_only_fails_closed(monkeypatch):
    def boom(path):
        raise OSError("denied")
    monkeypatch.setattr(startup, "_current_user_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(startup, "_read_dacl", boom)
    assert startup.is_path_admin_only(r"C:\x") is False


def test_writable_dacl_does_not_create_task_and_falls_back_to_run_key(frozen, schtasks, monkeypatch, tmp_path):
    calls, _ = schtasks
    frozen(str(tmp_path / "HardwareMonitorWidget.exe"))
    monkeypatch.setattr(startup, "install_mode", lambda: "installed")
    monkeypatch.setattr(startup, "_current_user_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(startup, "_read_dacl", _dacl((0, 0, 0x1301BF, "S-1-5-32-545")))     # Users:Modify
    sets = []
    monkeypatch.setattr(startup, "_set_run_value", lambda: sets.append(True))
    monkeypatch.setattr(startup, "_delete_run_value", lambda: sets.append(False))
    startup.set_enabled(True)
    assert calls == [] and sets == [True]
    assert list(tmp_path.glob("startup-task-*")) == []


def test_installed_disable_deletes_task_and_run(schtasks, monkeypatch):
    calls, _ = schtasks
    monkeypatch.setattr(startup, "install_mode", lambda: "installed")
    removed = []
    monkeypatch.setattr(startup, "_delete_run_value", lambda: removed.append(True))
    startup.set_enabled(False)
    assert calls[0][0] == [r"C:\Windows\System32\schtasks.exe", "/Delete", "/TN", "HardwareMonitorWidget", "/F"]
    assert removed == [True]


def test_installed_is_enabled_queries_task(schtasks, monkeypatch):
    calls, rc = schtasks
    monkeypatch.setattr(startup, "install_mode", lambda: "installed")
    assert startup.is_enabled() is True
    assert calls[0][0] == [r"C:\Windows\System32\schtasks.exe", "/Query", "/TN", "HardwareMonitorWidget"]
    rc["value"] = 1
    assert startup.is_enabled() is False


@pytest.mark.parametrize("mode", ["portable", "dev"])
def test_portable_and_dev_never_touch_tasks(schtasks, monkeypatch, mode):
    calls, _ = schtasks
    monkeypatch.setattr(startup, "install_mode", lambda: mode)
    sets = []
    monkeypatch.setattr(startup, "_set_run_value", lambda: sets.append(True))
    monkeypatch.setattr(startup, "_delete_run_value", lambda: sets.append(False))
    monkeypatch.setattr(startup, "run_key_enabled", lambda: True)
    startup.set_enabled(True)
    startup.set_enabled(False)
    assert startup.is_enabled() is True
    assert sets == [True, False]
    assert calls == []


def test_schtasks_timeout_means_disabled(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("schtasks", 15)
    monkeypatch.setattr(subprocess, "run", boom)
    assert startup.task_exists() is False
    startup.delete_task()           # must not raise


def test_portable_run_command_is_quoted_exe(frozen):
    frozen(r"C:\Users\me\My Apps\HardwareMonitorWidget.exe")
    assert startup.build_command() == '"C:\\Users\\me\\My Apps\\HardwareMonitorWidget.exe"'
