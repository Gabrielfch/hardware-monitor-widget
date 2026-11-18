"""User-supplied icon images (per hardware kind, per hardware, per sensor).

Security model (ADR-6): the app runs elevated, so images are never decoded from a folder a non-elevated
process could write to. Imported images are validated, **re-encoded to PNG** (only the first frame of an
animation is kept) and stored under a content-hash name in ``%ProgramData%\\HardwareMonitorWidget\\icons``,
a folder created atomically with a protected DACL (SYSTEM + Administrators full control, Users read-only).
Files are read only while ``secure_fs.admin_only`` holds for that folder and its parent (trusted owner, no
reparse point, no write access for non-administrators) and the file itself is a regular ``.png`` owned by
SYSTEM/Administrators; otherwise custom icons are ignored and import is disabled. Config only stores opaque file names, never paths. Every drawing helper
fails soft: a missing or broken image makes the caller fall back to the vector glyph.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPixmap

from . import secure_fs, startup

log = logging.getLogger(__name__)

ALLOWED_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".ico", ".gif", ".webp", ".svg")
MAX_BYTES = 2 * 1024 * 1024
MAX_SVG_BYTES = 512 * 1024
MAX_SVG_DIM = 4096          # logical size / viewBox limit of an imported SVG
SVG_OUT_PX = 256            # an SVG is rendered once, at import, to a PNG of this size
MAX_DIM = 2048
MAX_FILES = 200             # total icons referenced by the config
APP_DIR_NAME = "HardwareMonitorWidget"
# Stored (and referenced) names: "<sha16>.<ext>". Imports always produce ".png"; the other extensions are
# accepted in the config for compatibility, but only ".png" files are ever decoded when reading the store.
_SAFE_NAME = re.compile(r"[0-9a-f]{16}\.(?:png|jpg|jpeg|bmp|ico|gif)")
_ALLOWED_FORMATS = {"png", "jpg", "jpeg", "bmp", "ico", "gif"}
_TMP_MAX_AGE = 3600
_CACHE_MAX = 128
_TRUST_TTL = 5.0

# Animated GIF / WebP: validated with the limits below, but only the first frame is kept.
MAX_ANIM_READ = 200         # more source frames than this: rejected
MAX_ANIM_PIXELS = 60_000_000  # width * height * frames: bounds the decoding work
_ANIM_FORMATS = {"gif", "webp"}

# Error codes (translate with the strings_icons keys of the same name).
ERR_EXTENSION = "icon_err_extension"
ERR_TOO_BIG = "icon_err_too_big"
ERR_DIMENSIONS = "icon_err_dimensions"
ERR_UNREADABLE = "icon_err_unreadable"
ERR_IO = "icon_err_io"
ERR_ANIM_LONG = "icon_err_anim_long"
ERR_NOT_ELEVATED = "icon_err_not_elevated"
ERR_UNTRUSTED_DIR = "icon_err_untrusted_dir"


class IconImportError(Exception):
    """``code`` is one of the ERR_* constants (also the exception message)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# --- storage location and trust ---------------------------------------------------------------------------
def _program_data() -> Path:
    try:
        v = startup._known_folder("{62AB5D82-FDC1-4DC3-A9DD-070D1D495D97}")     # FOLDERID_ProgramData
    except Exception:
        v = None
    return Path(v or (os.environ.get("SystemDrive", "C:") + "\\ProgramData"))


def icons_dir() -> Path:
    return _program_data() / APP_DIR_NAME / "icons"


def _is_elevated() -> bool:
    from . import elevation
    return elevation.is_admin()


_trust: tuple | None = None     # (dir string, monotonic stamp, value)


def dir_trusted() -> bool:
    """The icons folder exists and so does its parent, both are plain directories (no link/junction) owned by
    SYSTEM/Administrators that no non-administrator can modify. Cached for a few seconds (clear_cache() resets it)."""
    global _trust
    d = icons_dir()
    key = str(d)
    now = time.monotonic()
    if _trust is not None and _trust[0] == key and now - _trust[1] < _TRUST_TTL:
        return _trust[2]
    ok = False
    try:
        ok = secure_fs.admin_only(d) and secure_fs.admin_only(d.parent)
    except Exception:
        ok = False
    _trust = (key, now, ok)
    return ok


def _create_secure_dir(d: Path) -> None:
    """Create the icons folder (and its parent) atomically with a protected DACL. Needs an elevated process.
    An already existing folder is left alone: dir_trusted() decides whether it can be used."""
    for path in (d.parent, d):
        if not os.path.lexists(path):
            try:
                secure_fs.make_private_dir(path)
            except FileExistsError:
                pass


def import_blocker() -> str | None:
    """Error code explaining why importing is disabled right now, or None when it is possible. Creates the
    protected folder when it does not exist yet."""
    global _trust
    if not _is_elevated():
        return ERR_NOT_ELEVATED
    try:
        d = icons_dir()
        if not os.path.lexists(d):
            _create_secure_dir(d)
    except Exception as exc:     # noqa: BLE001 - pywin32 missing, access denied...
        log.warning("could not create the icons folder: %s", exc.__class__.__name__)
        return ERR_UNTRUSTED_DIR
    _trust = None
    return None if dir_trusted() else ERR_UNTRUSTED_DIR


def can_import() -> bool:
    return import_blocker() is None


def safe_name(value) -> bool:
    """A file name the config may reference."""
    return isinstance(value, str) and _SAFE_NAME.fullmatch(value) is not None


def _open_reader(data: bytes):
    """(reader, buffer) over `data`; the buffer must outlive the reader."""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    reader.setAutoTransform(False)
    return reader, buf


def _format_of(reader) -> str:
    try:
        return bytes(reader.format()).decode("ascii", "ignore").lower()
    except Exception:
        return ""


def _decode(data: bytes, err: str, allowed=_ALLOWED_FORMATS):
    """Validate `data` (allowed raster format, size limits) and return the decoded (first frame) QImage."""
    reader, buf = _open_reader(data)
    if not reader.canRead() or _format_of(reader) not in allowed:
        raise IconImportError(err)
    size = reader.size()
    if size.isValid() and (size.width() > MAX_DIM or size.height() > MAX_DIM):
        raise IconImportError(ERR_DIMENSIONS)
    img = reader.read()
    if img.isNull():
        raise IconImportError(ERR_UNREADABLE)
    if img.width() > MAX_DIM or img.height() > MAX_DIM:
        raise IconImportError(ERR_DIMENSIONS)
    return img


def _check_animation_limits(data: bytes) -> None:
    """Reject an animated GIF / WebP that is too long or too heavy to decode (only frame 1 is ever read)."""
    reader, buf = _open_reader(data)
    if not reader.canRead() or _format_of(reader) not in _ANIM_FORMATS or not reader.supportsAnimation():
        return
    count = reader.imageCount()
    if count > MAX_ANIM_READ:
        raise IconImportError(ERR_ANIM_LONG)
    size = reader.size()
    if size.isValid() and size.width() * size.height() * max(count, 1) > MAX_ANIM_PIXELS:
        raise IconImportError(ERR_DIMENSIONS)


# --- SVG: parsed, rebuilt from an allow-list, and THAT rebuilt document is rendered ONCE to a PNG.
# The original bytes never reach Qt (Qt may honour a different encoding declaration than the one we
# validated, e.g. UTF-7), and the SVG itself is never stored nor parsed again.
_SVG_NS = "http://www.w3.org/2000/svg"
_SVG_ELEMENTS = frozenset({
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "defs",
    "linearGradient", "radialGradient", "stop", "clipPath", "title", "desc"})
_SVG_ATTRS = frozenset({
    "id", "d", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "fx", "fy", "width", "height",
    "points", "viewBox", "transform", "version", "preserveAspectRatio", "fill", "fill-opacity", "fill-rule",
    "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-opacity",
    "opacity", "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod",
    "clip-path", "clip-rule", "clipPathUnits"})
# Editor metadata namespaces (Inkscape): inert, silently dropped. Any other foreign name is rejected.
_SVG_IGNORED_NS = ("http://www.inkscape.org/namespaces/inkscape",
                   "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd")
_SVG_URL_ATTRS = {"fill": ("linearGradient", "radialGradient"), "stroke": ("linearGradient", "radialGradient"),
                  "clip-path": ("clipPath",)}
_SVG_BULK_ATTRS = ("d", "points")
MAX_SVG_ELEMENTS = 2000
MAX_SVG_DEPTH = 32
MAX_SVG_BULK = 200 * 1024       # total length of all d / points values
MAX_SVG_NUMBER = 1e6
SVG_RENDER_TIMEOUT = 3.0        # seconds
_SVG_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]{0,63}")
_SVG_VALUE_CHARS = re.compile(r"[A-Za-z0-9\s.,%#()+\-_'\"]*")
_SVG_URL_REF = re.compile(r"url\(\s*(['\"]?)#([A-Za-z_][A-Za-z0-9_.\-]{0,63})\1\s*\)", re.I)
_SVG_URL_ANY = re.compile(r"url", re.I)
_SVG_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SVG_NONFINITE = re.compile(r"\b(?:nan|inf|infinity)\b", re.I)
_SVG_HEX = re.compile(r"#[0-9A-Fa-f]+")
_SVG_XMLDECL = re.compile(r"<\?xml\b[^>]*\?>")
_SVG_ENCODING = re.compile(r"""encoding\s*=\s*(["'])(.*?)\1""", re.I | re.S)
_SVG_FORBIDDEN = re.compile(r"<!doctype|<!entity", re.I)


def _bad():
    return IconImportError(ERR_UNREADABLE)


def _svg_text(data: bytes) -> str:
    """UTF-8 text of the file; any other declared encoding, DOCTYPE/ENTITY or PI is rejected."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _bad() from None
    if chr(0) in text or _SVG_FORBIDDEN.search(text):
        raise _bad()
    m = _SVG_XMLDECL.match(text)
    if m:
        enc = _SVG_ENCODING.search(m.group(0))
        if enc and enc.group(2).strip().lower() != "utf-8":
            raise _bad()
        text = text[m.end():]
    if "<?" in text:                    # processing instruction (xml-stylesheet...) or malformed declaration
        raise _bad()
    return text


def _svg_check_value(name: str, value: str, refs: list) -> None:
    if len(value) > (MAX_SVG_BULK if name in _SVG_BULK_ATTRS else 2048):
        raise _bad()
    if name == "id":
        if _SVG_ID.fullmatch(value) is None:
            raise _bad()
        return
    if _SVG_VALUE_CHARS.fullmatch(value) is None or _SVG_NONFINITE.search(value):
        raise _bad()
    if name in _SVG_URL_ATTRS:
        found = _SVG_URL_REF.findall(value)
        if len(found) != len(_SVG_URL_ANY.findall(value)):
            raise _bad()                # any url() that is not a plain #fragment
        refs.extend((name, ident) for _q, ident in found)
        value = _SVG_URL_REF.sub(" ", value)
    elif _SVG_URL_ANY.search(value):
        raise _bad()
    for num in _SVG_NUM.findall(_SVG_HEX.sub(" ", value)):
        try:
            if not abs(float(num)) <= MAX_SVG_NUMBER:
                raise _bad()
        except (ValueError, OverflowError):
            raise _bad() from None


def _svg_sanitize(text: str) -> bytes:
    """Parse `text` and rebuild a minimal SVG (allow-listed elements/attributes only) as UTF-8 bytes."""
    try:
        root = ET.fromstring(text)      # DOCTYPE/ENTITY already excluded: no entity is ever expanded
    except (ET.ParseError, RecursionError, ValueError):
        raise _bad() from None
    if root.tag != f"{{{_SVG_NS}}}svg":
        raise _bad()
    new_root = ET.Element("svg", {"xmlns": _SVG_NS})   # plain names + one xmlns attribute
    ids: dict = {}
    refs: list = []
    bulk = 0
    count = 0
    stack = [(root, new_root, 1, False)]        # (source, rebuilt, depth, inside clipPath)
    while stack:
        src, dst, depth, in_clip = stack.pop()
        count += 1
        if count > MAX_SVG_ELEMENTS or depth > MAX_SVG_DEPTH:
            raise _bad()
        local = src.tag.rsplit("}", 1)[-1]
        if in_clip and local == "clipPath":
            raise _bad()                # a clip path inside a clip path: could build reference cycles
        for key, value in src.attrib.items():
            if key.startswith("{"):
                if key[1:].split("}", 1)[0] in _SVG_IGNORED_NS:
                    continue
                raise _bad()            # xlink:href, xml:space, unknown namespaces...
            if key not in _SVG_ATTRS or (key == "clip-path" and (in_clip or local == "clipPath")):
                raise _bad()            # style, on*, href, stroke-dasharray, filter, mask...
            _svg_check_value(key, value, refs)
            if key == "id":
                if value in ids:
                    raise _bad()
                ids[value] = local
            elif key in _SVG_BULK_ATTRS:
                bulk += len(value)
                if bulk > MAX_SVG_BULK:
                    raise _bad()
            dst.set(key, value)
        # children (text and tail are never copied)
        kids = []
        for child in src:
            if not isinstance(child.tag, str):
                continue                # comment / PI nodes are not built by the default parser
            if not child.tag.startswith("{"):
                raise _bad()
            ns, _, name = child.tag[1:].partition("}")
            if ns in _SVG_IGNORED_NS:
                continue
            if ns != _SVG_NS or name not in _SVG_ELEMENTS:
                raise _bad()
            kids.append((child, ET.SubElement(dst, name), depth + 1, in_clip or local == "clipPath"))
        stack.extend(reversed(kids))
    for attr, ident in refs:
        if ids.get(ident) not in _SVG_URL_ATTRS[attr]:
            raise _bad()                # missing id, or it points at the wrong kind of element
    return ET.tostring(new_root, encoding="utf-8", xml_declaration=False)


def _svg_render(clean: bytes) -> bytes:
    """Render the sanitised SVG to a 256x256 PNG (no GUI object is used, so it can run off the GUI thread)."""
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(QByteArray(clean))
    if not renderer.isValid():
        raise _bad()
    size = renderer.defaultSize()
    vb = renderer.viewBoxF()
    w, h = size.width(), size.height()
    if w <= 0 or h <= 0 or w > MAX_SVG_DIM or h > MAX_SVG_DIM or vb.width() > MAX_SVG_DIM or vb.height() > MAX_SVG_DIM:
        raise _bad()
    scale = SVG_OUT_PX / max(w, h)
    tw, th = max(1.0, w * scale), max(1.0, h * scale)
    img = QImage(SVG_OUT_PX, SVG_OUT_PX, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(p, QRectF((SVG_OUT_PX - tw) / 2, (SVG_OUT_PX - th) / 2, tw, th))
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        raise _bad()
    return bytes(buf.data())


def _svg_to_png(data: bytes) -> bytes:
    """Validate an SVG and return it rendered as a 256x256 transparent PNG (aspect ratio kept).

    The render runs in a daemon thread (QImage/QPainter/QSvgRenderer are safe off the GUI thread) and is
    abandoned after SVG_RENDER_TIMEOUT: the import then fails, while a stuck thread may keep running in the
    background until it finishes (Python cannot kill it). The complexity limits above already bound the cost.
    """
    if len(data) > MAX_SVG_BYTES:
        raise IconImportError(ERR_TOO_BIG)
    clean = _svg_sanitize(_svg_text(data))
    box: dict = {}

    def work():
        try:
            box["png"] = _svg_render(clean)
        except BaseException as exc:    # noqa: BLE001 - handed over to the caller
            box["err"] = exc

    t = threading.Thread(target=work, name="svg-render", daemon=True)
    t.start()
    t.join(SVG_RENDER_TIMEOUT)
    if t.is_alive() or "png" not in box:
        err = box.get("err")
        raise err if isinstance(err, IconImportError) else _bad()
    return box["png"]


def import_image_busy(src) -> str:
    """`import_image` with the wait cursor shown while the image is decoded."""
    from PySide6.QtWidgets import QApplication
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        return import_image(src)
    finally:
        QApplication.restoreOverrideCursor()


def import_image(src) -> str:
    """Validate `src`, re-encode it as PNG into the protected folder and return the stored file name."""
    src = Path(src)
    ext = src.suffix.lower()
    if ext not in ALLOWED_EXT:
        raise IconImportError(ERR_EXTENSION)
    blocked = import_blocker()
    if blocked:
        raise IconImportError(blocked)
    limit = MAX_SVG_BYTES if ext == ".svg" else MAX_BYTES
    try:
        if src.stat().st_size > limit:
            raise IconImportError(ERR_TOO_BIG)
        data = src.read_bytes()
    except IconImportError:
        raise
    except OSError:
        raise IconImportError(ERR_IO) from None
    if len(data) > limit:
        raise IconImportError(ERR_TOO_BIG)
    if ext == ".svg":
        img = _decode(_svg_to_png(data), ERR_UNREADABLE, {"png"})
    else:
        _check_animation_limits(data)
        img = _decode(data, ERR_UNREADABLE, _ALLOWED_FORMATS | {"webp"})    # first frame only
    png = _png_bytes(img)               # the stored bytes are our own encoding, never the user's
    name = hashlib.sha256(png).hexdigest()[:16] + ".png"
    try:
        _write_new(icons_dir(), name, png)
    except OSError:
        raise IconImportError(ERR_IO) from None
    return name


def _write_new(d: Path, name: str, data: bytes) -> None:
    """Publish `data` as d/name without ever overwriting: unique temp file (O_EXCL) in the same directory,
    flushed to disk, then renamed (fails if the target exists). An existing target must hold identical bytes."""
    dest = d / name
    fd, tmp_name = tempfile.mkstemp(dir=d, prefix=name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.rename(tmp_name, dest)           # unlike os.replace, never replaces an existing file
        except FileExistsError:
            if not _same_content(dest, data):
                raise OSError("an icon file with a different content already exists") from None
            os.unlink(tmp_name)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _same_content(path: Path, data: bytes) -> bool:
    try:
        if secure_fs.is_reparse_point(path) or not secure_fs.owner_trusted(path):
            return False
        with open(path, "rb") as fh:
            return fh.read(len(data) + 1) == data
    except OSError:
        return False


def _png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.convertToFormat(QImage.Format.Format_ARGB32).save(buf, "PNG"):
        raise IconImportError(ERR_UNREADABLE)
    return bytes(buf.data())


def resolve(hw_kind: str, hw_id: str, sensor_key: str, by_kind: dict, by_hw: dict, by_sensor: dict) -> str | None:
    """File name to draw: sensor > hardware > hardware kind; unsafe or non-string values are skipped."""
    for table, k in ((by_sensor, sensor_key), (by_hw, hw_id), (by_kind, hw_kind)):
        try:
            v = table.get(k) if isinstance(table, dict) else None
        except TypeError:
            v = None
        if safe_name(v):
            return v
    return None


# Active maps (pushed from the config), like the palette / icon style.
_by_kind: dict = {}
_by_hw: dict = {}
_by_sensor: dict = {}
_version = 0


def _clean(m) -> dict:
    return {k: v for k, v in m.items() if isinstance(k, str) and safe_name(v)} if isinstance(m, dict) else {}


def set_maps(by_kind, by_hw, by_sensor) -> None:
    global _by_kind, _by_hw, _by_sensor, _version
    nk, nh, ns = _clean(by_kind), _clean(by_hw), _clean(by_sensor)
    if (nk, nh, ns) != (_by_kind, _by_hw, _by_sensor):
        _by_kind, _by_hw, _by_sensor = nk, nh, ns
        _version += 1


def maps_version() -> int:
    """Bumped whenever the active maps change (layout / panel signatures include it)."""
    return _version


def file_for(hw_kind: str, hw_id: str = "", sensor_key: str = "") -> str | None:
    """Image file name to draw (sensor > hardware > kind), or None for the vector glyph."""
    return resolve(hw_kind, hw_id, sensor_key, _by_kind, _by_hw, _by_sensor)


# (filename, size_px, dpr, mtime) -> QPixmap ; failures: filename -> mtime_ns at failure
_cache: OrderedDict = OrderedDict()
_failed: dict = {}
_usable: dict = {}


def _mtime(path: Path) -> int | None:
    """mtime of a regular, non-link file (never followed); None for anything else."""
    try:
        if secure_fs.is_reparse_point(path):
            return None
        st = os.lstat(path)
        return st.st_mtime_ns if stat.S_ISREG(st.st_mode) else None
    except OSError:
        return None


def _read_icon(path: Path):
    """Decode a stored icon. Only ``.png`` files (the only thing the store produces) that are regular files
    owned by SYSTEM/Administrators are decoded; the read itself is capped at MAX_BYTES + 1."""
    if path.suffix.lower() != ".png":
        raise IconImportError(ERR_EXTENSION)
    if secure_fs.is_reparse_point(path) or not secure_fs.owner_trusted(path):
        raise IconImportError(ERR_UNTRUSTED_DIR)
    with open(path, "rb") as fh:
        data = fh.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise IconImportError(ERR_TOO_BIG)
    return _decode(data, ERR_UNREADABLE, {"png"})


def clear_cache() -> None:
    global _trust
    _trust = None
    _cache.clear()
    _failed.clear()
    _usable.clear()


def usable(filename: str) -> bool:
    """Cheap check that the folder is trusted and the file exists and decodes as an allowed image."""
    try:
        if not safe_name(filename) or not dir_trusted():
            return False
        path = icons_dir() / filename
        mt = _mtime(path)
        if mt is None:
            return False
        if _failed.get(filename) == mt:
            return False
        if _usable.get(filename) == mt:
            return True
        try:
            _read_icon(path)
        except (IconImportError, OSError):
            _failed[filename] = mt
            return False
        _usable[filename] = mt
        return True
    except Exception:
        return False


def pixmap_for(filename: str, size_px: int, dpr: float) -> QPixmap | None:
    try:
        if not safe_name(filename) or not dir_trusted():
            return None
        size_px = int(size_px)
        dpr = float(dpr) if dpr and dpr > 0 else 1.0
        if size_px <= 0:
            return None
        path = icons_dir() / filename
        mt = _mtime(path)
        if mt is None:
            _failed[filename] = None
            return None
        if filename in _failed and _failed[filename] == mt:
            return None
        key = (filename, size_px, round(dpr, 3), mt)
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
        _failed.pop(filename, None)
        try:
            img = _read_icon(path)
        except (IconImportError, OSError):
            _failed[filename] = mt
            return None
        px = max(1, int(round(size_px * dpr)))
        scaled = img.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(px, px)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
        p.end()
        out.setDevicePixelRatio(dpr)
        for k in [k for k in _cache if k[0] == filename and k[3] != mt]:
            del _cache[k]
        _cache[key] = out
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
        return out
    except Exception:  # never raises
        return None


def draw_custom_icon(painter: QPainter, rect, filename: str, opacity: float = 1.0) -> bool:
    """Draw the icon in `rect`; False when it cannot be drawn (the caller draws the vector glyph)."""
    try:
        r = QRectF(rect)
        side = min(r.width(), r.height())
        if side < 1:
            return False
        dpr = 1.0
        try:
            dpr = float(painter.device().devicePixelRatioF())
        except Exception:
            pass
        px = pixmap_for(filename, int(round(side)), dpr)
        if px is None:
            return False
        painter.save()
        try:
            painter.setOpacity(painter.opacity() * max(0.0, min(1.0, float(opacity))))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            target = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
            painter.drawPixmap(target, px, QRectF(0, 0, px.width(), px.height()))
        finally:
            painter.restore()
        return True
    except Exception:
        return False


def cleanup_unused(used: set[str]) -> None:
    """Delete our own image files not in `used`, plus stale ``*.tmp`` leftovers; nothing else."""
    try:
        d = icons_dir()
        if not d.is_dir() or not dir_trusted():
            return
        keep = {u for u in used if isinstance(u, str)}
        now = time.time()
        for f in d.iterdir():
            try:
                if not f.is_file():
                    continue
                if safe_name(f.name):
                    doomed = f.name not in keep
                elif f.suffix == ".tmp":
                    doomed = now - f.stat().st_mtime > _TMP_MAX_AGE
                else:
                    doomed = False
                if doomed:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass
