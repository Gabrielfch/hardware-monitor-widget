import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from hardware_monitor_widget import i18n  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    # Tear Qt down deterministically while the interpreter is still alive: widgets leaked by tests being
    # destroyed during interpreter finalization intermittently crashed the process (0xC0000409) after a
    # fully green run.
    import gc
    import threading
    from PySide6.QtCore import QCoreApplication, QEvent
    for t in threading.enumerate():
        if t.daemon and t.name in ("hmw-update", "svg-render"):
            t.join(2.0)
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()


@pytest.fixture(autouse=True)
def _no_real_taskbar(monkeypatch):
    """Never touch the real taskbar / z-order: no taskbar, never fullscreen, dark theme, Spanish UI."""
    from hardware_monitor_widget import taskbar, win_window
    monkeypatch.setattr(taskbar, "get_taskbar_info", lambda: None)
    monkeypatch.setattr(taskbar, "get_taskbar_infos", lambda: [])
    monkeypatch.setattr(taskbar, "is_fullscreen_foreground", lambda *_a: False)
    monkeypatch.setattr(taskbar, "taskbar_uses_light_theme", lambda: False)
    for name in ("_assert_topmost", "_drop_topmost", "_apply_ex_styles", "_foreground", "_post_null"):
        monkeypatch.setattr(win_window, name, lambda *_a, **_k: None)
    i18n.set_language("es")
    yield
    i18n.set_language(None)
