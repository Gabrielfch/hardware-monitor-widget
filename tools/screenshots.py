"""Render offscreen screenshots with synthetic data into docs/images (panel-dark/light, pills)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hardware_monitor_widget import i18n  # noqa: E402
from hardware_monitor_widget.colors import TASKBAR_BASE  # noqa: E402
from hardware_monitor_widget.config import DEFAULTS  # noqa: E402
from hardware_monitor_widget.models import Snapshot, SourceStatus  # noqa: E402
from hardware_monitor_widget.detail_window import DetailWindow  # noqa: E402
from hardware_monitor_widget.panel import HardwarePanel  # noqa: E402
from hardware_monitor_widget.widget import TaskbarWidget  # noqa: E402
from tools.synthetic import load_real_snapshot, make_degraded_snapshot, make_snapshot  # noqa: E402

OUT = ROOT / "docs" / "images"


def shot_panel(dark: bool, path: Path) -> None:
    p = HardwarePanel(dark=dark)
    snap = make_snapshot(cpu_temp=62.0)
    p.set_pinned(list(DEFAULTS["pinned"]))
    p.set_snapshot(snap)
    p.resize(380, 760)
    p.setFixedSize(380, 760)
    p.show()
    p._focus_panel()        # as when opened with the mouse: no focus ring
    QApplication.processEvents()
    p.grab().save(str(path))
    p.close()


def shot_panel_real(path: Path) -> None:
    """Curated summary of the real machine fixture (31 sensors in 8 hardware sections), dark theme."""
    p = HardwarePanel(dark=True)
    p.set_pinned(list(DEFAULTS["pinned"]))
    p.set_snapshot(load_real_snapshot())
    next(iter(p.rows.values())).set_hover(True)      # the first row shows its min / max second line
    content = p._scroll.widget()
    p.setFixedSize(400, content.sizeHint().height() + 110)
    p.show()
    p._focus_panel()
    QApplication.processEvents()
    p.grab().save(str(path))
    p.close()


def shot_detail_window(path: Path, dark: bool = True) -> None:
    """Detail window with the real fixture: cores and Direct3D groups open, a few sensors pinned."""
    snap = load_real_snapshot()
    w = DetailWindow(dark=dark)
    w.set_snapshot(snap)
    w.set_pinned(list(DEFAULTS["pinned"]))
    w.set_toggled(["grp:hw//amdcpu/0:cores", "hw:hw//lpc/nct6798d/0"])
    w.resize(860, 680)
    w.show()
    w.tree.setCurrentItem(w._nodes["grp:hw//amdcpu/0:cores"])
    QApplication.processEvents()
    w.grab().save(str(path))
    w.close()


def shot_error_panel(path: Path) -> None:
    p = HardwarePanel(dark=True)
    p.set_snapshot(Snapshot(statuses={"native": SourceStatus("native", False, "needs_admin")}))
    p.setFixedSize(380, 300)
    p.show()
    p._focus_panel()        # as when opened with the mouse: no focus ring
    QApplication.processEvents()
    p.grab().save(str(path))
    p.close()


def shot_degraded_panel(path: Path) -> None:
    p = HardwarePanel(dark=True)
    p.set_pinned(list(DEFAULTS["pinned"]))
    p.set_snapshot(make_degraded_snapshot())
    p.setFixedSize(380, 560)
    p.show()
    p._focus_panel()        # as when opened with the mouse: no focus ring
    QApplication.processEvents()
    p.grab().save(str(path))
    p.close()


def render_at(widget, dpr: float) -> QPixmap:
    """Render `widget` into a pixmap of device pixel ratio `dpr` (what a 100/125/150 % screen draws)."""
    pm = QPixmap(round(widget.width() * dpr), round(widget.height() * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    widget.render(pm)
    return pm


def stack_rows(rows: list[tuple[str, QPixmap]], path: Path, dark: bool = True, scale: int = 1) -> None:
    """Labelled pixmaps stacked on a taskbar-coloured background (optionally enlarged for inspection)."""
    pad, label_h = 10, 22
    rows = [(t, pm.scaled(pm.width() * scale, pm.height() * scale, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.FastTransformation) if scale > 1 else pm) for t, pm in rows]
    width = max(pm.width() for _t, pm in rows) + 2 * pad
    height = sum(pm.height() + label_h + pad for _t, pm in rows) + pad
    out = QPixmap(width, height)
    out.fill(QColor(*TASKBAR_BASE[dark]))
    qp = QPainter(out)
    y = pad
    for title, pm in rows:
        qp.setPen(QColor(150, 150, 150))
        f = QFont("Segoe UI")
        f.setPixelSize(13)
        qp.setFont(f)
        qp.drawText(pad, y + 14, title)
        y += label_h
        qp.drawPixmap(pad, y, pm)
        y += pm.height() + pad
    qp.end()
    out.save(str(path))


OVERFLOW_KEYS = ["hw//amdcpu/0/temperature/2", "hw//amdcpu/0/load/0", "hw//amdcpu/0/power/0",
                 "hw//gpu-amd/0/temperature/0", "hw//gpu-amd/0/load/0", "hw//gpu-amd/0/fan/0",
                 "hw//ram/0/load/0", "hw//lpc/nct6798d/temperature/0", "hw//nvme/0/temperature/0",
                 "hw//lpc/nct6798d/fan/1"]


def shot_overflow(path: Path) -> None:
    """Grouped pills (10 pinned values over 5 components) with no limit, compact and compact + "+K"."""
    rows = []
    for title, limit in (("no limit", None), ("limit 380 px: compact", 380), ("limit 300 px: compact + \"+K\"", 300)):
        w = TaskbarWidget(OVERFLOW_KEYS)
        w.set_grouped(True)
        w.set_light_theme(False)
        w.set_snapshot(make_snapshot(cpu_temp=88.0))
        w.set_extent_limit(limit)
        w.show()
        QApplication.processEvents()
        rows.append((title, w.grab()))
        w.close()
    stack_rows(rows, path)


def shot_icons_dpr(path: Path) -> None:
    """Pills (default pins) at 100 / 125 / 150 % scaling, enlarged 2x to check the 16 px glyphs."""
    rows = []
    for dpr in (1.0, 1.25, 1.5):
        w = TaskbarWidget(list(DEFAULTS["pinned"]))
        w.set_light_theme(False)
        w.set_snapshot(make_snapshot(cpu_temp=62.0))
        rows.append((f"{int(dpr * 100)} %", render_at(w, dpr)))
        w.close()
    stack_rows(rows, path, scale=2)


def shot_pills(path: Path, grouped: bool = True) -> None:
    """Default pins plus a second GPU temperature on a dark and a light taskbar (one pill per hardware
    when `grouped`, one per sensor otherwise)."""
    rows = []
    for dark, temp in ((True, 62.0), (False, 88.0)):
        w = TaskbarWidget(list(DEFAULTS["pinned"]) + ["hw//gpu-amd/0/temperature/1"])
        w.set_grouped(grouped)
        w.set_light_theme(not dark)
        w.set_snapshot(make_snapshot(cpu_temp=temp))
        w.show()
        QApplication.processEvents()
        rows.append((dark, w.grab()))
        w.close()
    pad, label_h = 16, 28
    width = max(pm.width() for _d, pm in rows) + 2 * pad + 20
    height = sum(pm.height() + 3 * pad + label_h for _d, pm in rows) + pad
    out = QPixmap(width, height)
    out.fill(QColor(190, 196, 210))
    qp = QPainter(out)
    y = pad
    for dark, pm in rows:
        qp.setPen(QColor(30, 30, 30))
        f = QFont("Segoe UI")
        f.setPixelSize(16)
        f.setBold(True)
        qp.setFont(f)
        qp.drawText(pad, y + 18, "Dark taskbar" if dark else "Light taskbar")
        y += label_h
        qp.setBrush(QColor(*TASKBAR_BASE[dark]))
        qp.setPen(Qt.PenStyle.NoPen)
        qp.drawRoundedRect(pad, y, width - 2 * pad, pm.height() + 2 * pad, 8, 8)
        qp.drawPixmap(pad + 10, y + pad, pm)
        y += pm.height() + 2 * pad + pad
    qp.end()
    out.save(str(path))


def shot_styles(path: Path, dark: bool = True) -> None:
    """The four pill styles (classic, soft, transparent, compact), the last two with abbreviations too, with
    a critical CPU temperature so the red value with "!" shows."""
    rows = []
    for title, style, labels in (("A  classic", "classic", False), ("B  soft", "soft", False),
                                 ("C  transparent (default)", "transparent", False),
                                 ("C  transparent + abbreviations", "transparent", True),
                                 ("D  compact", "compact", False), ("D  compact + abbreviations", "compact", True)):
        w = TaskbarWidget(list(DEFAULTS["pinned"]) + ["hw//gpu-amd/0/temperature/1"])
        w.set_pill_style(style)
        w.set_pill_labels(labels)
        w.set_light_theme(not dark)
        w.set_snapshot(make_snapshot(cpu_temp=92.0))
        w.show()
        QApplication.processEvents()
        rows.append((title, w.grab()))
        w.close()
    stack_rows(rows, path, dark=dark, scale=1)


def shot_stacked(path: Path) -> None:
    """Floating bar with one component per row (values in aligned columns), icons and abbreviations."""
    rows = []
    for title, labels in (("stacked, icons", False), ("stacked, abbreviations", True)):
        w = TaskbarWidget(list(DEFAULTS["pinned"]) + ["hw//gpu-amd/0/temperature/1", "hw//nvme/0/temperature/0"])
        w.set_pill_labels(labels)
        w.set_light_theme(False)
        w.set_stacked(True)
        w._set_floating(True)       # as placed in floating mode (the offscreen run has no placement)
        w.set_snapshot(make_snapshot(cpu_temp=62.0))
        w.show()
        QApplication.processEvents()
        rows.append((title, w.grab()))
        w.close()
    pad = 16
    width = sum(pm.width() for _t, pm in rows) + pad * (len(rows) + 1)
    height = max(pm.height() for _t, pm in rows) + 2 * pad + 24
    out = QPixmap(width, height)
    out.fill(QColor(120, 130, 150))
    qp = QPainter(out)
    x = pad
    for title, pm in rows:
        qp.setPen(QColor(255, 255, 255))
        f = QFont("Segoe UI")
        f.setPixelSize(13)
        qp.setFont(f)
        qp.drawText(x, pad + 12, title)
        qp.drawPixmap(x, pad + 24, pm)
        x += pm.width() + pad
    qp.end()
    out.save(str(path))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    i18n.set_language("es")
    # the offscreen platform ships no fonts: load the Windows ones so text renders
    from PySide6.QtGui import QFontDatabase
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf"):
        f = Path(r"C:\Windows\Fonts") / name
        if f.exists():
            QFontDatabase.addApplicationFont(str(f))
    app.setFont(QFont("Segoe UI", 10))
    # never touch the real taskbar: the pills float on the primary screen of the offscreen platform
    from hardware_monitor_widget import taskbar
    taskbar.get_taskbar_info = lambda: None
    OUT.mkdir(parents=True, exist_ok=True)
    shot_panel(True, OUT / "panel-dark.png")
    shot_panel(False, OUT / "panel-light.png")
    shot_panel_real(OUT / "panel-summary-real.png")
    shot_detail_window(OUT / "detail-window.png")
    shot_detail_window(OUT / "detail-window-light.png", dark=False)
    shot_error_panel(OUT / "panel-error.png")
    shot_degraded_panel(OUT / "panel-degraded.png")
    shot_pills(OUT / "pills.png")
    shot_pills(OUT / "pills-ungrouped.png", grouped=False)
    shot_overflow(OUT / "pills-overflow.png")
    shot_styles(OUT / "pills-styles.png")
    shot_styles(OUT / "pills-styles-light.png", dark=False)
    shot_stacked(OUT / "pills-stacked.png")
    shot_icons_dpr(OUT / "icons-dpr.png")
    print("saved:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
