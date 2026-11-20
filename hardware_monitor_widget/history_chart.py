"""Line chart of one sensor's history (plain QPainter, no dependencies). Geometry comes from history.plot_points."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import i18n
from .colors import text_color
from .history import plot_points
from .models import format_value

LEFT, RIGHT, TOP, BOTTOM = 56, 8, 6, 18


def value_range(points) -> tuple[float, float] | None:
    """(low, high) of the values, widened when they are all equal; None when there are no points."""
    if not points:
        return None
    lo, hi = min(v for _t, v in points), max(v for _t, v in points)
    if hi - lo < 1e-9:
        lo, hi = lo - 1.0, hi + 1.0
    return lo, hi


class HistoryChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("historyChart")
        self.setMinimumHeight(110)
        self._points: list[tuple[float, float]] = []
        self._kind = "temperature"
        self._fahrenheit = False
        self._dark = True
        self._t0, self._t1 = 0.0, 1.0
        self._span = 3600

    def set_data(self, points, kind: str, fahrenheit: bool, dark: bool, seconds: int, now: float) -> None:
        self._points = list(points)
        self._kind, self._fahrenheit, self._dark = kind, bool(fahrenheit), bool(dark)
        self._span = seconds
        self._t0, self._t1 = now - seconds, now
        self.update()

    def runs(self, width: int, height: int):
        """Pixel runs for the current data in a width x height box (see history.plot_points)."""
        rng = value_range(self._points)
        lo, hi = rng if rng else (None, None)
        return plot_points(self._points, self._t0, self._t1, width, height, lo, hi)

    def _time_label(self, ts: float) -> str:
        fmt = "%d/%m" if self._span > 86400 else "%H:%M"
        return datetime.fromtimestamp(ts).strftime(fmt)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = text_color(self._dark)
        muted = QColor(170, 170, 170) if self._dark else QColor(90, 90, 90)
        grid = QColor(255, 255, 255, 40) if self._dark else QColor(0, 0, 0, 35)
        plot = QRectF(LEFT, TOP, max(1, self.width() - LEFT - RIGHT), max(1, self.height() - TOP - BOTTOM))
        p.setPen(QPen(grid, 1))
        p.drawRect(plot)
        fm = QFontMetrics(p.font())
        rng = value_range(self._points)
        if rng is None:
            p.setPen(muted)
            p.drawText(plot, int(Qt.AlignmentFlag.AlignCenter), i18n.tr("hist_none"))
            p.end()
            return
        lo, hi = rng
        sep = i18n.decimal_sep()
        p.setPen(muted)
        p.drawText(QRectF(0, plot.top() - 2, LEFT - 4, fm.height()),
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
                   format_value(self._kind, hi, self._fahrenheit, sep))
        p.drawText(QRectF(0, plot.bottom() - fm.height() + 2, LEFT - 4, fm.height()),
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom),
                   format_value(self._kind, lo, self._fahrenheit, sep))
        y = plot.bottom() + 2
        p.drawText(QRectF(plot.left(), y, plot.width() / 2, BOTTOM - 2),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), self._time_label(self._t0))
        p.drawText(QRectF(plot.center().x(), y, plot.width() / 2, BOTTOM - 2),
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop), i18n.tr("hist_now"))
        p.setPen(QPen(text, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.translate(plot.left(), plot.top())
        for run in self.runs(int(plot.width()), int(plot.height())):
            if len(run) == 1:
                p.drawPoint(QPointF(*run[0]))
                continue
            path = QPainterPath(QPointF(*run[0]))
            for x, yy in run[1:]:
                path.lineTo(x, yy)
            p.drawPath(path)
        p.end()
