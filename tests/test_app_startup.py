import sys

import pytest
from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication

from hardware_monitor_widget import app as app_mod
from hardware_monitor_widget import elevation, startup


@pytest.fixture
def shell_calls(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    calls = []

    def fake(*a):
        calls.append(a)
        return 42
    monkeypatch.setattr(elevation, "_shell_execute", fake)
    monkeypatch.setattr(app_mod, "setup_logging", lambda: None)
    monkeypatch.setattr(app_mod, "install_excepthooks", lambda: None)
    return calls


def _hold_lock(tmp_path):
    d = tmp_path / app_mod.APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(d / "app.lock"))
    lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    return lock


def test_second_instance_exits_without_uac(shell_calls, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["hardware_monitor_widget"])
    monkeypatch.setattr(elevation, "is_admin", lambda: False)
    held = _hold_lock(tmp_path)
    try:
        assert app_mod.main() == 0
    finally:
        held.unlock()
    assert shell_calls == []        # ShellExecute was never called


def test_not_admin_relaunches_and_releases_lock(shell_calls, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["hardware_monitor_widget", "--junk"])
    monkeypatch.setattr(elevation, "is_admin", lambda: False)
    assert app_mod.main() == 0
    assert len(shell_calls) == 1
    assert shell_calls[0][2].endswith(" --elevated") and "--junk" not in shell_calls[0][2]
    probe = QLockFile(str(tmp_path / app_mod.APP_DIR_NAME / "app.lock"))
    assert probe.tryLock(0)     # the lock was released for the elevated copy
    probe.unlock()


def test_elevated_but_not_admin_does_not_loop(shell_calls, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["hardware_monitor_widget", "--elevated"])
    monkeypatch.setattr(elevation, "is_admin", lambda: False)
    started = []

    class FakeController:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started.append(True)

    monkeypatch.setattr(app_mod, "Controller", FakeController)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    from hardware_monitor_widget import monitor as mon
    stopped = []
    monkeypatch.setattr(mon.HardwareMonitor, "stop", lambda self: stopped.append(True))
    assert app_mod.main() == 0
    assert shell_calls == [] and started == [True] and stopped == [True]


def test_startup_command_form():
    cmd = startup.build_command()
    assert " -I -c " in cmd and "runpy.run_module" in cmd
