"""Small dialog to edit the amber / orange / red thresholds of one sensor (with a reset to the defaults)."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QPushButton,
                               QVBoxLayout)

from .config import THRESHOLD_MAX, THRESHOLD_MIN
from .i18n import tr

RESET_CODE = 2          # `exec()` result when the user pressed "Reset"


class ThresholdsDialog(QDialog):
    """Accepted -> `values()` is the new [amber, orange, red]; result `RESET_CODE` -> back to the defaults."""

    def __init__(self, name: str, current, unit: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("thr_title"))
        self.setModal(True)
        root = QVBoxLayout(self)
        intro = QLabel(tr("thr_intro", name=name))
        intro.setWordWrap(True)
        root.addWidget(intro)
        form = QFormLayout()
        self.boxes: list[QDoubleSpinBox] = []
        for key, value in zip(("thr_amber", "thr_orange", "thr_red"), current):
            box = QDoubleSpinBox()
            box.setRange(THRESHOLD_MIN, THRESHOLD_MAX)
            box.setDecimals(1)
            box.setSingleStep(1.0)
            box.setValue(float(value))
            if unit:
                box.setSuffix(" " + unit)
            box.setAccessibleName(tr(key))
            box.valueChanged.connect(lambda _v: self._validate())
            form.addRow(tr(key), box)
            self.boxes.append(box)
        root.addLayout(form)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.reset_btn = QPushButton(tr("thr_reset"))
        self.buttons.addButton(self.reset_btn, QDialogButtonBox.ButtonRole.ResetRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.reset_btn.clicked.connect(lambda _c=False: self.done(RESET_CODE))
        root.addWidget(self.buttons)
        self._validate()

    def values(self) -> list[float]:
        return [round(b.value(), 1) for b in self.boxes]

    def is_valid(self) -> bool:
        a, o, r = self.values()
        return a < o < r

    def _validate(self) -> None:
        ok = self.is_valid()
        self.error.setText("" if ok else tr("thr_invalid"))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
