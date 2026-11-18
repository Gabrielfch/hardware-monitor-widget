import sys
from pathlib import Path

from hardware_monitor_widget import app as app_mod
from hardware_monitor_widget import sensorhost, startup

PKG = Path(__file__).resolve().parent.parent / "packaging"


def test_host_path_dev(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    p = sensorhost.host_path()
    assert p.name == "SensorHost.exe" and "native" in p.parts


def test_host_path_frozen_uses_bundle_not_temp(monkeypatch, tmp_path):
    exe_dir = tmp_path / "app"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "HardwareMonitorWidget.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(exe_dir / "_internal"), raising=False)
    p = sensorhost.host_path()
    assert p == exe_dir / "_internal" / "hardware_monitor_widget" / "native" / "sensorhost" / "SensorHost.exe"


def test_host_path_frozen_without_meipass_falls_back_to_exe_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "HardwareMonitorWidget.exe"))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert sensorhost.host_path().is_relative_to(tmp_path.resolve())


def test_startup_flags(monkeypatch):
    calls = []
    monkeypatch.setattr(startup, "set_enabled", lambda v: calls.append(v))
    assert app_mod._startup_command(["x", "--enable-startup"]) == 0
    assert app_mod._startup_command(["x", "--disable-startup"]) == 0
    assert app_mod._startup_command(["x", "--elevated"]) is None
    assert calls == [True, False]


def test_startup_flag_failure_exit_code(monkeypatch):
    def fail(v):
        raise OSError("nope")
    monkeypatch.setattr(startup, "set_enabled", fail)
    assert app_mod._startup_command(["x", "--enable-startup"]) == 1


def test_spec_is_onedir_and_admin():
    spec = (PKG / "hardware_monitor_widget.spec").read_text(encoding="utf-8")
    assert "uac_admin=True" in spec and "COLLECT(" in spec and "exclude_binaries=True" in spec
    assert "runtime_tmpdir" not in spec and "a.binaries, a.datas, []" not in spec   # no onefile EXE


def test_installer_script_requirements():
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    for needle in ("PrivilegesRequired=admin", r"DefaultDirName={autopf}\HardwareMonitorWidget",
                   "ArchitecturesAllowed=x64compatible", "ArchitecturesInstallIn64BitMode=x64compatible",
                   "--enable-startup", "RunOnceId", "taskkill.exe", "/Delete /TN", "CloseApplications=yes",
                   "THIRD_PARTY_NOTICES.md", "https://pawnio.eu/", "Uninstall\\PawnIO",
                   "DisableDirPage=yes", "DisableProgramGroupPage=yes", "UsePreviousAppDir=no",
                   "/F /IM SensorHost.exe", 'Name: "spanish"', 'Name: "english"'):
        assert needle in iss, needle


def test_installer_creates_protected_programdata_folders():
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    dirs = iss.split("[Dirs]")[1].split("[Files]")[0]
    for sub in ("", r"\icons", r"\updates"):
        line = [ln for ln in dirs.splitlines() if ln.startswith('Name: "{commonappdata}\\HardwareMonitorWidget' + sub + '"')]
        assert len(line) == 1, sub
        assert "admins-full" in line[0] and "system-full" in line[0] and "users-readexec" in line[0]
        assert "users-modify" not in line[0] and "users-full" not in line[0]
    runs = iss.split("[Run]")[1].split("[UninstallRun]")[0]
    icacls = [ln for ln in runs.splitlines() if "icacls.exe" in ln]
    assert len(icacls) == 3 and all("/inheritance:r" in ln for ln in icacls)       # no inherited Users write


def test_app_entry_hardens_dll_search_before_imports():
    src = (PKG / "app_entry.py").read_text(encoding="utf-8")
    compile(src, "app_entry.py", "exec")
    assert src.index("SetDefaultDllDirectories(0x00001000)") < src.index("from hardware_monitor_widget")
    assert src.index("os.chdir(") < src.index("from hardware_monitor_widget")


def test_build_script_produces_artifacts():
    ps = (PKG / "build.ps1").read_text(encoding="utf-8")
    for needle in ("SkipTests", "-SelfContained", "SHA256SUMS.txt", "HardwareMonitorWidget-portable-", "/DAppVersion"):
        assert needle in ps, needle


def test_installer_relaunches_after_silent_update():
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert "function ShouldRelaunchApp: Boolean;" in iss
    assert "{param:LAUNCHAPP|0}" in iss and "WizardSilent" in iss
    runs = iss.split("[Run]")[1].split("[UninstallRun]")[0]
    relaunch = [ln for ln in runs.splitlines() if "Check: ShouldRelaunchApp" in ln]
    assert len(relaunch) == 1
    assert "nowait" in relaunch[0] and "shellexec" in relaunch[0] and "postinstall" not in relaunch[0]
    # the interactive "launch" entry stays skipped on silent installs, so the app never starts twice
    interactive = [ln for ln in runs.splitlines() if "postinstall" in ln]
    assert len(interactive) == 1 and "skipifsilent" in interactive[0]
    # existing behaviour intact
    assert "--enable-startup" in runs and "DisableDirPage=yes" in iss and "taskkill.exe" in iss


def test_updater_installer_args_match_installer_parameter():
    from hardware_monitor_widget import updater
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert "/LAUNCHAPP=1" in updater.installer_args()
    assert "LAUNCHAPP" in iss and "/SILENT" in updater.installer_args()


def test_release_docs_describe_signed_assets():
    doc = (PKG.parent / "docs" / "PACKAGING.md").read_text(encoding="utf-8")
    for needle in ("SHA256SUMS.txt.sig", "gen_keys.py", "sign_release.py", "release_info.py", "PUBLIC_KEYS"):
        assert needle in doc, needle
