import os
import sys

from hardware_monitor_widget import elevation as el


def test_is_admin_bool():
    assert isinstance(el.is_admin(), bool)


def test_relaunch_builds_runas(monkeypatch):
    calls = []
    monkeypatch.setattr(el, "_shell_execute", lambda *a: calls.append(a) or 42)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "argv", ["x", "--evil", "--no-elevate", "file.txt"])
    assert el.relaunch_as_admin() is True
    verb, file, params, cwd = calls[0]
    assert verb == "runas" and file.lower().endswith(("python.exe", "pythonw.exe"))
    assert params.startswith("-I -c ")
    assert params.endswith(" --elevated")            # only --elevated, user argv never forwarded
    assert "--evil" not in params and "--no-elevate" not in params and "file.txt" not in params
    assert "runpy.run_module('hardware_monitor_widget'" in params
    assert os.path.isdir(os.path.join(cwd, "hardware_monitor_widget"))


def test_relaunch_frozen_passes_only_elevated(monkeypatch):
    calls = []
    monkeypatch.setattr(el, "_shell_execute", lambda *a: calls.append(a) or 33)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["app.exe", "--foo"])
    assert el.relaunch_as_admin() is True
    assert calls[0][1] == sys.executable and calls[0][2] == "--elevated"


def test_launch_code_is_valid_python():
    args = el.python_launch_args(["--elevated"])
    assert args[1:3] == ["-I", "-c"] and args[-1] == "--elevated"
    compile(args[3], "<launch>", "exec")


def test_relaunch_cancelled(monkeypatch):
    monkeypatch.setattr(el, "_shell_execute", lambda *a: 5)   # SE_ERR_ACCESSDENIED (UAC cancelled)
    assert el.relaunch_as_admin() is False


def test_ensure_admin(monkeypatch):
    def boom(*a):
        raise AssertionError("no UAC")
    monkeypatch.setattr(el, "_shell_execute", boom)
    monkeypatch.setattr(el, "is_admin", lambda: True)
    assert el.ensure_admin_or_relaunch([]) is False
    monkeypatch.setattr(el, "is_admin", lambda: False)
    monkeypatch.setattr(el, "relaunch_as_admin", lambda: True)
    assert el.ensure_admin_or_relaunch([]) is True
    monkeypatch.setattr(el, "relaunch_as_admin", lambda: False)
    assert el.ensure_admin_or_relaunch([]) is False


def test_loop_guard_no_relaunch_when_already_elevated_flag(monkeypatch):
    def boom(*a):
        raise AssertionError("no UAC loop")
    monkeypatch.setattr(el, "_shell_execute", boom)
    monkeypatch.setattr(el, "is_admin", lambda: False)
    assert el.ensure_admin_or_relaunch(["app", "--elevated"]) is False
