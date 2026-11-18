import hashlib
import os
import time

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QFileDialog, QMessageBox

from hardware_monitor_widget import custom_icons as ci
from hardware_monitor_widget import secure_fs, startup
from hardware_monitor_widget.custom_icons_dialog import CustomIconsDialog


@pytest.fixture(autouse=True)
def _icons(tmp_path, monkeypatch, qapp):
    """Never touch %ProgramData%: the folder is injected and the elevation / DACL checks are stubbed."""
    monkeypatch.setattr(ci, "icons_dir", lambda: tmp_path / "icons")
    monkeypatch.setattr(ci, "_is_elevated", lambda: True)
    monkeypatch.setattr(ci, "_create_secure_dir", lambda d: d.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(startup, "is_path_admin_only", lambda path: True)
    monkeypatch.setattr(secure_fs, "owner_trusted", lambda path: True)
    ci.clear_cache()
    yield
    ci.clear_cache()


def make_png(path, w=32, h=16, color=QColor("red")):
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(color)
    assert img.save(str(path))
    return path


def test_import_valid_and_dedup(tmp_path):
    a = make_png(tmp_path / "a.png")
    b = make_png(tmp_path / "b.png")
    n1 = ci.import_image(a)
    n2 = ci.import_image(b)
    assert n1 == n2 and ci.safe_name(n1) and n1.endswith(".png")
    assert (ci.icons_dir() / n1).is_file()
    assert len(list(ci.icons_dir().iterdir())) == 1


def test_reject_extension(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_EXTENSION


def test_reject_size(tmp_path):
    f = tmp_path / "big.png"
    f.write_bytes(b"\0" * (ci.MAX_BYTES + 1))
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_TOO_BIG


def test_reject_dimensions(tmp_path):
    f = make_png(tmp_path / "wide.png", 2049, 4)
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_DIMENSIONS


def test_reject_corrupt(tmp_path):
    f = tmp_path / "bad.png"
    f.write_bytes(b"not an image at all")
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_UNREADABLE


def test_reject_missing(tmp_path):
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(tmp_path / "nope.png")
    assert e.value.code == ci.ERR_IO


def test_pixmap_for_ok_square_and_missing(tmp_path):
    name = ci.import_image(make_png(tmp_path / "a.png", 40, 20))
    px = ci.pixmap_for(name, 16, 2.0)
    assert px is not None and px.width() == 32 and px.height() == 32
    (ci.icons_dir() / name).unlink()
    assert ci.pixmap_for("0123456789abcdef.png", 16, 1.0) is None
    assert ci.pixmap_for("../evil.png", 16, 1.0) is None


def test_failure_cache_and_mtime_invalidation():
    d = ci.icons_dir()
    d.mkdir(parents=True)
    name = "0123456789abcdef.png"
    f = d / name
    f.write_bytes(b"garbage")
    assert ci.pixmap_for(name, 16, 1.0) is None
    # fix the file without changing mtime -> still cached failure
    st = f.stat()
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(QColor("blue"))
    img.save(str(f))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert ci.pixmap_for(name, 16, 1.0) is None
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert ci.pixmap_for(name, 16, 1.0) is not None


def test_cache_bounded(tmp_path):
    name = ci.import_image(make_png(tmp_path / "a.png"))
    for s in range(1, ci._CACHE_MAX + 20):
        ci.pixmap_for(name, s, 1.0)
    assert len(ci._cache) <= ci._CACHE_MAX


def test_draw_custom_icon(tmp_path):
    name = ci.import_image(make_png(tmp_path / "a.png", 20, 20))
    pm = QPixmap(24, 24)
    pm.fill(QColor("white"))
    p = QPainter(pm)
    assert ci.draw_custom_icon(p, QRectF(0, 0, 24, 24), name, 0.5) is True
    assert not p.opacity() < 1.0  # state restored
    assert ci.draw_custom_icon(p, QRectF(0, 0, 24, 24), "0123456789abcdef.png") is False
    p.end()
    assert pm.toImage().pixelColor(12, 12) != QColor("white")


def test_cleanup_unused(tmp_path):
    n1 = ci.import_image(make_png(tmp_path / "a.png", color=QColor("red")))
    n2 = ci.import_image(make_png(tmp_path / "b.png", color=QColor("green")))
    assert n1 != n2
    ci.cleanup_unused({n1})
    assert [f.name for f in ci.icons_dir().iterdir()] == [n1]
    ci.cleanup_unused(set())
    assert list(ci.icons_dir().iterdir()) == []


_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>'


def test_reject_svg_disguised_as_png(tmp_path):
    f = tmp_path / "evil.png"
    f.write_bytes(_SVG)
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code in (ci.ERR_EXTENSION, ci.ERR_UNREADABLE)
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


def test_reject_tiff_disguised_as_png(tmp_path):
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(QColor("red"))
    f = tmp_path / "t.tiff"
    if not img.save(str(f), "TIFF"):
        pytest.skip("no TIFF writer")
    g = tmp_path / "t.png"
    g.write_bytes(f.read_bytes())
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(g)
    assert e.value.code in (ci.ERR_EXTENSION, ci.ERR_UNREADABLE)


def test_pixmap_for_rejects_svg_content():
    d = ci.icons_dir()
    d.mkdir(parents=True)
    name = "0123456789abcdef.png"
    (d / name).write_bytes(_SVG)
    assert ci.pixmap_for(name, 16, 1.0) is None
    assert ci.usable(name) is False


def test_safe_name_rejects_trailing_newline():
    assert ci.safe_name("0123456789abcdef.png")
    assert not ci.safe_name("0123456789abcdef.png\n")


def test_cleanup_leaves_foreign_files(tmp_path):
    n1 = ci.import_image(make_png(tmp_path / "a.png"))
    d = ci.icons_dir()
    (d / "notes.txt").write_text("mine")
    (d / "photo.png").write_bytes(b"x")
    (d / "sub").mkdir()
    fresh = d / "aaaaaaaaaaaaaaaa.png.tmp"
    fresh.write_bytes(b"x")
    old = d / "bbbbbbbbbbbbbbbb.png.tmp"
    old.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))
    ci.cleanup_unused(set())
    names = {f.name for f in d.iterdir()}
    assert names == {"notes.txt", "photo.png", "sub", fresh.name}
    assert n1 not in names


# ---------------------------------------------------------------- SVG import (rendered once to PNG)
_NS = 'xmlns="http://www.w3.org/2000/svg"'
_GOOD = f'<svg {_NS} viewBox="0 0 100 50" width="100" height="50"><rect x="0" y="0" width="100" height="50" fill="red"/></svg>'


def _svg(tmp_path, text, name="i.svg"):
    f = tmp_path / name
    f.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return f


def test_svg_imports_as_256_png_with_transparency(tmp_path):
    n = ci.import_image(_svg(tmp_path, _GOOD))
    assert ci.safe_name(n) and n.endswith(".png")
    assert [f.name for f in ci.icons_dir().iterdir()] == [n]          # only the PNG is stored
    img = QImage(str(ci.icons_dir() / n))
    assert (img.width(), img.height()) == (256, 256) and img.hasAlphaChannel()
    assert img.pixelColor(0, 0).alpha() == 0                            # 2:1 shape: transparent top margin
    assert img.pixelColor(128, 128).alpha() == 255
    assert ci.usable(n) and ci.pixmap_for(n, 16, 1.0) is not None
    import hashlib
    assert n == hashlib.sha256((ci.icons_dir() / n).read_bytes()).hexdigest()[:16] + ".png"


@pytest.mark.parametrize("text", [
    f'<!DOCTYPE svg [<!ENTITY a "x">]><svg {_NS} width="8" height="8"/>',
    f'<!DOCTYPE svg><svg {_NS} width="8" height="8"/>',
    f'<svg {_NS} width="8" height="8"><!ENTITY a "x"></svg>',
    f'<svg {_NS} width="8" height="8"><script>alert(1)</script></svg>',
    f'<svg {_NS} width="8" height="8"><foreignObject width="8" height="8"/></svg>',
    f'<svg {_NS} width="8" height="8" onload="x()"><rect width="8" height="8"/></svg>',
    f'<svg {_NS} width="8" height="8"><rect onclick = "x()" width="8" height="8"/></svg>',
    f'<svg {_NS} xmlns:xlink="http://www.w3.org/1999/xlink" width="8" height="8"><image xlink:href="http://e.com/a.png"/></svg>',
    f'<svg {_NS} width="8" height="8"><image href="file:///C:/x.png"/></svg>',
    f'<svg {_NS} width="8" height="8"><image href="C:\\x.png"/></svg>',
    f'<svg {_NS} width="8" height="8"><image href=x.png/></svg>',
    f'<svg {_NS} width="8" height="8"><image href="data:image/svg+xml;base64,AAAA"/></svg>',
    f'<svg {_NS} width="8" height="8"><rect style="fill:url(http://e.com/x)" width="8" height="8"/></svg>',
    f'<svg {_NS} width="8" height="8"><style>@import "x.css";</style></svg>',
    "<svg",                                                             # not well formed
    "not xml at all",
    f'<html {_NS}><body/></html>',
    f'<svg {_NS} width="9999" height="9999"><rect width="9" height="9"/></svg>',   # absurd size
    f'<svg {_NS} width="0" height="0"/>',
])
def test_svg_rejected(tmp_path, text):
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(_svg(tmp_path, text))
    assert e.value.code in (ci.ERR_UNREADABLE,)
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


def _wrap(body, attrs='width="16" height="16"'):
    return f'<svg {_NS} {attrs}>{body}</svg>'


_BAD_SVGS = [
    # declared encodings other than UTF-8 (the UTF-7 bypass: Qt would decode what Python never validated)
    '<?xml version="1.0" encoding="UTF-7"?>' + _wrap(
        '<desc>+ADw-image xlink:href=+ACI-C:/Windows/Web/x.png+ACI-/+AD4-</desc><rect width="8" height="8"/>'),
    '<?xml version="1.0" encoding="ISO-8859-1"?>' + _wrap('<rect width="8" height="8"/>'),
    "<?xml version='1.0' encoding='utf-16'?>" + _wrap('<rect width="8" height="8"/>'),
    '<?xml version="1.0"?><?xml-stylesheet href="x.css"?>' + _wrap('<rect width="8" height="8"/>'),
    # elements outside the allow-list
    _wrap('<defs><rect id="r" width="8" height="8"/></defs><use href="#r"/>'),
    _wrap('<image width="4" height="4"/>'),
    _wrap('<pattern id="p" width="1" height="1"><rect width="1" height="1"/></pattern><rect fill="url(#p)" width="8" height="8"/>'),
    _wrap('<filter id="f"><feGaussianBlur stdDeviation="9999"/></filter><rect filter="url(#f)" width="8" height="8"/>'),
    _wrap('<style>rect{fill:red}</style><rect width="8" height="8"/>'),
    _wrap('<text x="1" y="8">hi</text>'),
    _wrap('<a><rect width="8" height="8"/></a>'),
    _wrap('<mask id="m"><rect width="8" height="8"/></mask>'),
    _wrap('<symbol id="s"><rect width="8" height="8"/></symbol>'),
    _wrap('<switch><rect width="8" height="8"/></switch>'),
    _wrap('<rect width="8" height="8"><animate attributeName="x" to="3"/></rect>'),
    '<svg width="8" height="8"><rect width="8" height="8"/></svg>',                  # no SVG namespace
    _wrap('<rect xmlns="http://example.com/x" width="8" height="8"/>'),            # foreign namespace element
    # attributes outside the allow-list
    _wrap('<rect style="fill:red" width="8" height="8"/>'),
    _wrap('<rect onclick="x()" width="8" height="8"/>'),
    _wrap('<rect onmouseover="x()" width="8" height="8"/>'),
    _wrap('<rect href="#a" width="8" height="8"/>'),
    _wrap('<rect stroke="red" stroke-dasharray="0.001" width="8" height="8"/>'),
    _wrap('<rect filter="url(#f)" width="8" height="8"/>'),
    _wrap('<rect xml:space="preserve" width="8" height="8"/>'),
    f'<svg {_NS} xmlns:xlink="http://www.w3.org/1999/xlink" width="8" height="8"><rect xlink:href="C:/a.png" width="8" height="8"/></svg>',
    # url() values
    _wrap('<rect fill="url(#nope)" width="8" height="8"/>'),
    _wrap('<rect fill="url(http://e.com/x)" width="8" height="8"/>'),
    _wrap('<rect fill="url(C:/a.png)" width="8" height="8"/>'),
    _wrap('<linearGradient id="a"/><rect fill="url(#a) url(file:///x)" width="8" height="8"/>'),
    _wrap('<linearGradient id="a"/><rect opacity="url(#a)" width="8" height="8"/>'),
    _wrap('<linearGradient id="g"/><rect fill="url(#g)" clip-path="url(#g)" width="8" height="8"/>'),   # wrong kind
    _wrap('<clipPath id="c"><rect clip-path="url(#c)" width="8" height="8"/></clipPath>'),            # reference cycle
    _wrap('<clipPath id="c" clip-path="url(#c)"/>'),
    _wrap('<clipPath id="c"><clipPath id="d"/></clipPath>'),
    _wrap('<rect id="a" width="1" height="1"/><rect id="a" width="1" height="1"/>'),                  # duplicate id
    # complexity and absurd numbers
    _wrap("<g>" * 40 + '<rect width="8" height="8"/>' + "</g>" * 40),
    _wrap('<rect width="1" height="1"/>' * 2500),
    _wrap('<rect width="1e300" height="8"/>'),
    _wrap('<rect width="8" height="8" transform="scale(1e300)"/>'),
    _wrap('<path d="M0 0 L1e300 5"/>'),
    _wrap('<polygon points="0,0 1e7,5 3,4"/>'),
    _wrap('<rect width="8" height="8" opacity="nan"/>'),
    _wrap('<rect width="inf" height="8"/>'),
    _wrap('<path d="' + "M0 0L1 1" * 30000 + '"/>'),                                                   # > 200 KB of path data
]


@pytest.mark.parametrize("text", _BAD_SVGS)
def test_svg_sanitiser_rejects(tmp_path, text):
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(_svg(tmp_path, text))
    assert e.value.code == ci.ERR_UNREADABLE
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


_GOOD_SVGS = [
    _wrap('<path d="M2 2 L14 2 L14 14 Z" fill="#1e90ff" stroke="#000" stroke-width="1.5" stroke-linejoin="round"/>'),
    _wrap('<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1" gradientUnits="objectBoundingBox">'
          '<stop offset="0" stop-color="#ff0000"/><stop offset="1" stop-color="rgb(0,0,255)" stop-opacity="0.5"/>'
          '</linearGradient></defs><rect width="16" height="16" rx="3" fill="url(#g)"/>'),
    _wrap('<defs><radialGradient id="r" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="white"/>'
          '<stop offset="1" stop-color="green"/></radialGradient></defs><circle cx="8" cy="8" r="7" fill="url(#r)"/>'),
    _wrap('<defs><clipPath id="c"><circle cx="8" cy="8" r="6"/></clipPath></defs>'
          '<g clip-path="url(#c)" transform="translate(1 1) rotate(10 8 8)"><rect width="16" height="16" fill="red"/>'
          '<ellipse cx="8" cy="8" rx="5" ry="3" fill="none" stroke="blue"/><line x1="0" y1="0" x2="16" y2="16" stroke="black"/>'
          '<polyline points="1,1 5,9 9,1" fill="none" stroke="black"/><polygon points="2,2 8,2 5,8" opacity=".5"/></g>'),
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?><!-- c -->' + _wrap(
        '<title>t</title><desc>d</desc><rect width="16" height="16" fill="red"/>',
        'width="16" height="16" viewBox="0 0 16 16" version="1.1"'),
]


@pytest.mark.parametrize("text", _GOOD_SVGS)
def test_svg_sanitiser_accepts_typical_icons(tmp_path, text):
    n = ci.import_image(_svg(tmp_path, text))
    img = QImage(str(ci.icons_dir() / n))
    assert (img.width(), img.height()) == (256, 256)
    assert any(img.pixelColor(x, y).alpha() for x in range(0, 256, 16) for y in range(0, 256, 16))   # not empty


def test_svg_sanitiser_output_is_rebuilt():
    text = ('\ufeff<?xml version="1.0" encoding="utf-8"?><svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" inkscape:version="1" width="8" height="8">'
            '<!-- note --><desc>+ADw-script+AD4-</desc><rect id="a" width="8" height="8" inkscape:label="x"/></svg>')
    out = ci._svg_sanitize(ci._svg_text(text.encode("utf-8")))
    assert out == b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><desc /><rect id="a" width="8" height="8" /></svg>'


def test_svg_render_timeout_is_an_import_error(tmp_path, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(ci, "_svg_render", lambda clean: gate.wait(5) or b"")
    monkeypatch.setattr(ci, "SVG_RENDER_TIMEOUT", 0.1)
    try:
        with pytest.raises(ci.IconImportError) as e:
            ci.import_image(_svg(tmp_path, _GOOD))
        assert e.value.code == ci.ERR_UNREADABLE
    finally:
        gate.set()




# ---------------------------------------------------------------- resolution (sensor > hardware > kind)
def test_resolve_priority_and_unsafe():
    a, b, c = "0123456789abcdef.png", "fedcba9876543210.jpg", "aaaaaaaaaaaaaaaa.png"
    assert ci.resolve("cpu", "hw1", "s1", {"cpu": a}, {"hw1": b}, {"s1": c}) == c
    assert ci.resolve("cpu", "hw1", "s1", {"cpu": a}, {"hw1": b}, {}) == b
    assert ci.resolve("cpu", "hw1", "s1", {"cpu": a}, {}, {}) == a
    assert ci.resolve("cpu", "hw1", "s1", {}, {}, {}) is None
    assert ci.resolve("cpu", "hw1", "s1", {"cpu": a}, {}, {"s1": "..\\x.png"}) == a      # unsafe skipped
    assert ci.resolve("cpu", "hw1", "s1", {"cpu": "C:\\x\\0123456789abcdef.png"}, {}, {"s1": 5}) is None
    assert ci.resolve("cpu", "hw1", "s1", {"cpu": a}, None, [1]) == a


def test_set_maps_filters_and_versions():
    v0 = ci.maps_version()
    ci.set_maps({"cpu": "0123456789abcdef.png", "gpu": "../x.png"}, {"h": "fedcba9876543210.png"}, "bad")
    assert ci.maps_version() == v0 + 1
    assert ci.file_for("cpu") == "0123456789abcdef.png" and ci.file_for("gpu") is None
    assert ci.file_for("gpu", "h") == "fedcba9876543210.png"
    ci.set_maps({"cpu": "0123456789abcdef.png"}, {"h": "fedcba9876543210.png"}, {})
    assert ci.maps_version() == v0 + 1            # unchanged -> no bump
    ci.set_maps({}, {}, {})


# ---------------------------------------------------------------- ADR-6: elevation and admin-only folder
def test_import_blocked_when_not_elevated(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "_is_elevated", lambda: False)
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(make_png(tmp_path / "a.png"))
    assert e.value.code == ci.ERR_NOT_ELEVATED
    assert not ci.can_import()
    assert not (tmp_path / "icons").exists()


def test_import_blocked_when_folder_not_admin_only(tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "is_path_admin_only", lambda path: False)
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(make_png(tmp_path / "a.png"))
    assert e.value.code == ci.ERR_UNTRUSTED_DIR
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


def test_import_blocked_when_secure_dir_creation_fails(tmp_path, monkeypatch):
    def boom(d):
        raise PermissionError("denied")
    monkeypatch.setattr(ci, "_create_secure_dir", boom)
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(make_png(tmp_path / "a.png"))
    assert e.value.code == ci.ERR_UNTRUSTED_DIR


def test_files_ignored_when_folder_becomes_untrusted(tmp_path, monkeypatch):
    name = ci.import_image(make_png(tmp_path / "a.png", 20, 20))
    assert ci.pixmap_for(name, 16, 1.0) is not None and ci.usable(name)
    monkeypatch.setattr(startup, "is_path_admin_only", lambda path: False)
    ci.clear_cache()
    assert ci.pixmap_for(name, 16, 1.0) is None
    assert ci.usable(name) is False
    pm = QPixmap(24, 24)
    p = QPainter(pm)
    assert ci.draw_custom_icon(p, QRectF(0, 0, 24, 24), name) is False
    p.end()
    ci.cleanup_unused(set())                      # must not delete anything from an untrusted folder
    assert (ci.icons_dir() / name).exists()


def test_parent_folder_must_be_admin_only_too(tmp_path, monkeypatch):
    name = ci.import_image(make_png(tmp_path / "a.png", 20, 20))
    seen = []
    monkeypatch.setattr(startup, "is_path_admin_only", lambda path: seen.append(path) or path.endswith("icons"))
    ci.clear_cache()
    assert ci.pixmap_for(name, 16, 1.0) is None
    assert str(tmp_path) in seen


def test_reparse_point_folder_is_not_trusted(tmp_path, monkeypatch):
    ci.import_image(make_png(tmp_path / "a.png"))
    monkeypatch.setattr(secure_fs, "is_reparse_point", lambda p: p.name == "icons")
    ci.clear_cache()
    assert ci.dir_trusted() is False


def test_trust_result_is_cached_briefly(tmp_path, monkeypatch):
    ci.import_image(make_png(tmp_path / "a.png"))
    calls = []
    monkeypatch.setattr(startup, "is_path_admin_only", lambda path: calls.append(path) or True)
    ci.clear_cache()
    for _ in range(5):
        assert ci.dir_trusted()
    assert len(calls) == 2                        # folder + parent, once


def test_real_icons_dir_is_under_program_data(monkeypatch):
    monkeypatch.undo()
    d = ci.icons_dir()
    assert d.parts[-2:] == ("HardwareMonitorWidget", "icons")
    assert "programdata" in str(d).lower()


# ---------------------------------------------------------------- re-encoding and hostile rasters
def test_stored_file_is_reencoded_png_without_trailing_data(tmp_path):
    f = make_png(tmp_path / "a.png", 16, 16)
    f.write_bytes(f.read_bytes() + b"<script>TRAILING-PAYLOAD</script>")
    name = ci.import_image(f)
    stored = (ci.icons_dir() / name).read_bytes()
    assert b"TRAILING-PAYLOAD" not in stored and stored.startswith(b"\x89PNG")
    assert name == hashlib.sha256(stored).hexdigest()[:16] + ".png"


def test_jpeg_is_stored_as_png(tmp_path):
    img = QImage(16, 16, QImage.Format.Format_RGB32)
    img.fill(QColor("green"))
    f = tmp_path / "a.jpg"
    assert img.save(str(f), "JPG")
    name = ci.import_image(f)
    assert name.endswith(".png") and (ci.icons_dir() / name).read_bytes().startswith(b"\x89PNG")


def test_png_content_with_jpg_name_is_accepted_as_png(tmp_path):
    f = make_png(tmp_path / "real.png")
    g = tmp_path / "liar.jpg"
    g.write_bytes(f.read_bytes())
    assert ci.import_image(g).endswith(".png")


def _gif(frames: int) -> bytes:
    """A tiny valid 1x1 GIF89a with `frames` frames."""
    head = b"GIF89a\x01\x00\x01\x00\x80\x00\x00" + b"\0\0\0\xff\xff\xff"
    frame = b"\x21\xf9\x04\x00\x05\x00\x00\x00" + b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00" + b"\x02\x02\x44\x01\x00"
    return head + frame * frames + b"\x3b"


def test_animated_gif_keeps_only_first_frame(tmp_path):
    f = tmp_path / "anim.gif"
    f.write_bytes(_gif(5))
    name = ci.import_image(f)
    assert name.endswith(".png")
    assert [p.name for p in ci.icons_dir().iterdir()] == [name]            # no strip, no sidecar
    img = QImage(str(ci.icons_dir() / name))
    assert (img.width(), img.height()) == (1, 1)


def test_gif_with_too_many_frames_is_rejected(tmp_path):
    f = tmp_path / "long.gif"
    f.write_bytes(_gif(ci.MAX_ANIM_READ + 5))
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_ANIM_LONG
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


def test_gif_too_heavy_to_decode_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "MAX_ANIM_PIXELS", 3)
    f = tmp_path / "heavy.gif"
    f.write_bytes(_gif(5))
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_DIMENSIONS


def test_huge_png_dimensions_rejected_before_decoding(tmp_path):
    # Valid PNG header announcing 60000 x 60000: must be refused from the header, never allocated.
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 60000, 60000, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\0")) + chunk(b"IEND", b""))
    f = tmp_path / "huge.png"
    f.write_bytes(png)
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code in (ci.ERR_DIMENSIONS, ci.ERR_UNREADABLE)
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


def test_import_uses_unique_temp_files(tmp_path):
    f = make_png(tmp_path / "a.png", 4, 4, QColor("blue"))
    first = ci.import_image(f)
    d = ci.icons_dir()
    (d / first).unlink()
    (d / (first + ".tmp")).write_bytes(b"stale")          # a fixed temp name must not matter
    assert ci.import_image(f) == first
    assert sorted(p.name for p in d.iterdir()) == [first, first + ".tmp"]


def test_svg_too_big_and_svgz(tmp_path):
    big = _GOOD.replace("</svg>", "<!--" + "a" * (ci.MAX_SVG_BYTES + 10) + "--></svg>")
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(_svg(tmp_path, big))
    assert e.value.code == ci.ERR_TOO_BIG
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(_svg(tmp_path, _GOOD, "i.svgz"))
    assert e.value.code == ci.ERR_EXTENSION


def test_svg_binary_garbage_and_bad_encoding(tmp_path):
    for blob in (b"\xff\xfe\x00\x01garbage", b"\x00" * 100, b""):
        with pytest.raises(ci.IconImportError):
            ci.import_image(_svg(tmp_path, blob))


def test_svg_entities_and_external_urls_never_reach_disk(tmp_path):
    hostile = [
        f'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///C:/Windows/win.ini">]><svg {_NS} width="8" height="8"/>',
        f'<svg {_NS} width="8" height="8"><rect fill="url(https://evil.example/x)" width="8" height="8"/></svg>',
        f'<svg {_NS} width="8" height="8"><script>alert(1)</script></svg>',
    ]
    for i, text in enumerate(hostile):
        with pytest.raises(ci.IconImportError):
            ci.import_image(_svg(tmp_path, text, f"h{i}.svg"))
    assert not ci.icons_dir().exists() or list(ci.icons_dir().iterdir()) == []


def test_image_filter_lists_svg():
    from hardware_monitor_widget.custom_icons_dialog import IMAGE_FILTER
    assert "*.svg" in IMAGE_FILTER


# ---------------------------------------------------------------- dialog
def _pick(monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))


def _rows(dlg, target):
    return [r for r in dlg._rows if r[0] is target]


def test_dialog_choose_remove_cancel(tmp_path, monkeypatch):
    _pick(monkeypatch, make_png(tmp_path / "a.png"))
    dlg = CustomIconsDialog({}, {}, {}, [("hw-key", "My CPU", "cpu")], [("sensor-key", "CPU temperature", "cpu")],
                            tr=lambda c: c)
    kind_rows = _rows(dlg, dlg._by_kind)
    hw_rows, sensor_rows = _rows(dlg, dlg._by_hw), _rows(dlg, dlg._by_sensor)
    assert len(kind_rows) == 7 and len(hw_rows) == 1 and len(sensor_rows) == 1
    dlg._choose(*kind_rows[0])
    by_kind, by_hw, by_sensor = dlg.result_maps()
    assert list(by_kind) == [kind_rows[0][1]] and by_hw == {} and by_sensor == {}
    name = by_kind[kind_rows[0][1]]
    assert ci.safe_name(name)
    dlg._choose(*hw_rows[0])
    dlg._choose(*sensor_rows[0])
    r = dlg.result_maps()
    assert r[1] == {"hw-key": name} and r[2] == {"sensor-key": name}
    dlg._remove(*kind_rows[0])
    assert dlg.result_maps()[0] == {}
    orig = {"cpu": name}
    dlg2 = CustomIconsDialog(orig, {}, {}, tr=lambda c: c)
    dlg2._remove(*[r for r in dlg2._rows if r[1] == "cpu"][0])
    dlg2.reject()
    assert orig == {"cpu": name}


def test_dialog_error_shows_message(tmp_path, monkeypatch):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"xx")
    _pick(monkeypatch, bad)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    dlg = CustomIconsDialog({}, {}, {}, tr=lambda c: "T:" + c)
    dlg._choose(*dlg._rows[0])
    assert shown == ["T:" + ci.ERR_UNREADABLE]
    assert dlg.result_maps() == ({}, {}, {})


def test_dialog_warns_and_skips_picker_when_not_elevated(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "_is_elevated", lambda: False)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: pytest.fail("picker must not open"))
    dlg = CustomIconsDialog({}, {}, {}, tr=lambda c: c)
    assert not dlg._notice.isHidden() and dlg._notice.text() == "custom_icons_need_admin"
    dlg._choose(*dlg._rows[0])
    assert shown == [ci.ERR_NOT_ELEVATED]


def test_dialog_ignores_unsafe_input():
    dlg = CustomIconsDialog({"cpu": "..\\x.png"}, {"k": "/abs.png"}, {"s": "C:\\a.png", 3: "0123456789abcdef.png"},
                            tr=lambda c: c)
    assert dlg.result_maps() == ({}, {}, {})


def test_dialog_enforces_total_limit(tmp_path, monkeypatch):
    _pick(monkeypatch, make_png(tmp_path / "a.png"))
    name = "0123456789abcdef.png"
    full = {f"s{i}": name for i in range(ci.MAX_FILES)}
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    dlg = CustomIconsDialog({}, {}, full, tr=lambda c: c)
    dlg._choose(*_rows(dlg, dlg._by_kind)[0])
    assert shown == ["custom_icons_limit"] and dlg.result_maps()[0] == {}


def test_dialog_uses_app_dialog_guard_for_picker(monkeypatch):
    from hardware_monitor_widget import dialogs
    seen = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: seen.append(dialogs.dialog_open()) or ("", ""))
    dlg = CustomIconsDialog({}, {}, {}, tr=lambda c: c)
    dlg._choose(*dlg._rows[0])
    assert seen == [True] and not dialogs.dialog_open()


def test_strings_cover_every_error_code_in_both_languages():
    from hardware_monitor_widget import strings_icons as s
    codes = [getattr(ci, n) for n in dir(ci) if n.startswith("ERR_")]
    for lang in ("en", "es"):
        for c in codes + ["custom_icons_title", "custom_icons_need_admin", "custom_icons_limit",
                          "hw_cpu", "hw_other"]:
            assert c in s.STRINGS[lang], (lang, c)
    assert s.STRINGS["en"].keys() == s.STRINGS["es"].keys()


# ---------------------------------------------------------------- hardening: owner, reparse, extension, no overwrite
def test_folder_with_untrusted_owner_is_not_trusted(tmp_path, monkeypatch):
    ci.import_image(make_png(tmp_path / "a.png"))
    monkeypatch.setattr(secure_fs, "owner_trusted", lambda p: p.name != "icons")      # pre-created by a user
    ci.clear_cache()
    assert ci.dir_trusted() is False
    monkeypatch.setattr(secure_fs, "owner_trusted", lambda p: p.name == "icons")      # parent pre-created
    ci.clear_cache()
    assert ci.dir_trusted() is False
    assert ci.import_blocker() == ci.ERR_UNTRUSTED_DIR


def test_icon_file_that_is_a_reparse_point_is_ignored(tmp_path, monkeypatch):
    name = ci.import_image(make_png(tmp_path / "a.png", 20, 20))
    assert ci.pixmap_for(name, 16, 1.0) is not None
    ci.clear_cache()
    monkeypatch.setattr(secure_fs, "is_reparse_point", lambda p: p.name == name)
    assert ci.pixmap_for(name, 16, 1.0) is None and ci.usable(name) is False


def test_icon_file_with_untrusted_owner_is_not_decoded(tmp_path, monkeypatch):
    name = ci.import_image(make_png(tmp_path / "a.png", 20, 20))
    ci.clear_cache()
    monkeypatch.setattr(secure_fs, "owner_trusted", lambda p: p.name != name)
    assert ci.pixmap_for(name, 16, 1.0) is None and ci.usable(name) is False


def test_only_png_files_are_read(tmp_path):
    src = make_png(tmp_path / "a.png", 20, 20)
    d = ci.icons_dir()
    d.mkdir(parents=True)
    for ext in ("jpg", "bmp", "gif", "ico", "jpeg"):
        name = f"0123456789abcdef.{ext}"
        (d / name).write_bytes(src.read_bytes())                 # valid image data, but not a stored PNG
        assert ci.safe_name(name)                                # still accepted in the config...
        assert ci.pixmap_for(name, 16, 1.0) is None              # ...but never decoded
        assert ci.usable(name) is False


def test_read_is_capped_at_max_bytes(tmp_path):
    d = ci.icons_dir()
    d.mkdir(parents=True)
    name = "0123456789abcdef.png"
    (d / name).write_bytes(b"PNG" + bytes(ci.MAX_BYTES + 10))
    assert ci.usable(name) is False and ci.pixmap_for(name, 16, 1.0) is None


def test_import_never_overwrites_a_different_existing_file(tmp_path):
    f = make_png(tmp_path / "a.png", 8, 8, QColor("green"))
    name = ci.import_image(f)
    stored = ci.icons_dir() / name
    good = stored.read_bytes()
    assert ci.import_image(f) == name and stored.read_bytes() == good      # identical content: fine
    stored.write_bytes(b"planted")                                         # same name, other content
    with pytest.raises(ci.IconImportError) as e:
        ci.import_image(f)
    assert e.value.code == ci.ERR_IO and stored.read_bytes() == b"planted"
    assert [p.name for p in ci.icons_dir().iterdir()] == [name]            # no temp file left behind


def test_create_secure_dir_uses_atomic_creation(tmp_path, monkeypatch):
    monkeypatch.undo()
    made = []
    monkeypatch.setattr(secure_fs, "make_private_dir", lambda p: (made.append(p), p.mkdir())[0])
    d = tmp_path / "pd" / "icons"
    (tmp_path / "pd").mkdir()
    ci._create_secure_dir(d)
    assert made == [d] and d.is_dir()
    ci._create_secure_dir(d)                                               # existing: untouched
    assert made == [d]
