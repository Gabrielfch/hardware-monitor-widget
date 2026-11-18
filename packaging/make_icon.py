"""Generate packaging/app.ico (16-256 px): a chip with a thermometer, in the style of the app's own icons."""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen  # noqa: E402

from hardware_monitor_widget.icons import draw_kind_icon  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 256)
BG = QColor("#1b2430")
CHIP = QColor("#e8eef5")
HOT = QColor("#ff7a2f")


def render(size: int) -> bytes:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = float(size)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BG)
    p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
    # chip, drawn with the app's own glyph (slightly inset)
    draw_kind_icon(p, "cpu", QRectF(s * 0.06, s * 0.06, s * 0.74, s * 0.74), CHIP, 2.0 if size >= 32 else 2.4)
    # thermometer in the lower right: filled bulb + stem, on a background disc so it reads over the chip
    cx, cy = s * 0.73, s * 0.66
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BG)
    p.drawEllipse(QPointF(cx, cy), s * 0.24, s * 0.24)
    stem_w = max(1.5, s * 0.10)
    pen = QPen(HOT, stem_w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(cx, cy - s * 0.13), QPointF(cx, cy + s * 0.04))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(HOT)
    p.drawEllipse(QPointF(cx, cy + s * 0.08), s * 0.08, s * 0.08)
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    pngs = [render(s) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries = b""
    for s, data in zip(SIZES, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    (ROOT / "packaging" / "app.ico").write_bytes(out + entries + b"".join(pngs))


if __name__ == "__main__":
    main()
