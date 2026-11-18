"""The dialog guard keeps the panel alive while a native (non-QWindow) dialog is open.

The panel-side hooks are wired in T8; here the guard itself and its `hub` signals are verified, which is
what the panel relies on.
"""
import pytest
from PySide6.QtWidgets import QDialog

from hardware_monitor_widget import dialogs


def test_guard_nesting_and_exception_safety():
    assert not dialogs.dialog_open()
    with pytest.raises(RuntimeError):
        with dialogs.app_dialog_guard():
            with dialogs.app_dialog_guard():
                assert dialogs.dialog_open()
            assert dialogs.dialog_open()
            raise RuntimeError
    assert not dialogs.dialog_open()


def test_hub_signals_only_for_outermost_dialog():
    events = []
    dialogs.hub.opened.connect(lambda: events.append("opened"))
    dialogs.hub.closed.connect(lambda: events.append("closed"))
    try:
        with dialogs.app_dialog_guard():
            with dialogs.app_dialog_guard():
                assert events == ["opened"]
            assert events == ["opened"]
        assert events == ["opened", "closed"]
    finally:
        dialogs.hub.opened.disconnect()
        dialogs.hub.closed.disconnect()


def test_closed_signal_emitted_even_when_dialog_raises():
    events = []
    dialogs.hub.closed.connect(lambda: events.append("closed"))
    try:
        with pytest.raises(ValueError):
            with dialogs.app_dialog_guard():
                raise ValueError
        assert events == ["closed"]
    finally:
        dialogs.hub.closed.disconnect()


def test_qt_modal_dialog_counts_without_explicit_guard(qapp):
    dlg = QDialog()
    dlg.setModal(True)
    dlg.show()
    try:
        assert dialogs.dialog_open()
    finally:
        dlg.close()
    assert not dialogs.dialog_open()


def test_dialog_open_is_false_when_idle(qapp):
    assert not dialogs.dialog_open()
