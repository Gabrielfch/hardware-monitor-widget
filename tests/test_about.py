import sys

from hardware_monitor_widget import __version__, about, i18n


def test_tech_info_basic(qapp):
    text = about.tech_info()
    assert __version__ in text
    assert "Python" in text and "PySide6" in text
    for line in text.splitlines():
        assert ": " in line


def test_tech_info_with_hello(qapp):
    hello = {"admin": True, "pawnio": True, "pawnio_version": "2.0.1", "dotnet": "8.0.1"}
    text = about.tech_info(hello)
    assert "2.0.1" in text and "8.0.1" in text and about.LHM_VERSION in text
    off = about.tech_info({"admin": False, "pawnio": False})
    assert i18n.tr("about_not_installed") in off


def test_tech_info_has_no_personal_data(qapp, monkeypatch):
    monkeypatch.setenv("USERNAME", "zzuserzz")
    monkeypatch.setenv("COMPUTERNAME", "ZZPCNAMEZZ")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\zzuserzz")
    text = about.tech_info({"admin": True, "dotnet": r"x C:\Users\zzuserzz\y ZZPCNAMEZZ"})
    assert "zzuserzz" not in text.lower()
    assert "zzpcnamezz" not in text.lower()
    assert "C:\\Users" not in text


def test_scrub_replaces_profile_path(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\zzuserzz")
    monkeypatch.setenv("APPDATA", r"C:\Users\zzuserzz\AppData\Roaming")
    out = about.scrub(r"C:\Users\zzuserzz\AppData\Roaming\x")
    assert out == r"%APPDATA%\x"


def test_notices_path_dev_and_frozen(monkeypatch, tmp_path):
    p = about.notices_path()
    assert p.name == "THIRD_PARTY_NOTICES.md" and p.is_file()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
    assert about.notices_path() == tmp_path / "THIRD_PARTY_NOTICES.md"


def test_license_text_contains_mit_and_notices():
    text = about.license_text()
    assert "MIT" in text and "LibreHardwareMonitorLib" in text


def test_show_licenses_uses_guard(qapp, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from hardware_monitor_widget import dialogs
    seen = []
    monkeypatch.setattr(QDialog, "exec", lambda self: seen.append(dialogs.dialog_open()) or 0)
    about.show_licenses()
    assert seen == [True]
    assert not dialogs.dialog_open()


def test_strings_registered_in_both_languages():
    for key in ("about_app", "about_licenses_title", "about_pawnio"):
        assert i18n.tr_in("es", key) != key and i18n.tr_in("en", key) != key
