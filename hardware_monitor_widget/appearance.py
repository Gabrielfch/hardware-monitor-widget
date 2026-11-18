"""Applies the appearance settings (palette, icon style, language, colour scheme) to the drawing modules."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from . import colors, i18n, icons

log = logging.getLogger(__name__)


def apply_color_scheme(theme) -> None:
    """Give the Qt application the chosen light / dark scheme, so windows that take their colours from the
    application palette (dialogs, detail window) follow it; "auto" hands it back to Windows."""
    try:
        scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(theme, Qt.ColorScheme.Unknown)
        hints = QGuiApplication.styleHints()
        if hints is not None:
            # always set it: colorScheme() reports the EFFECTIVE scheme, so comparing would skip pinning
            # "dark" while Windows happens to be dark
            hints.setColorScheme(scheme)
    except Exception:  # noqa: BLE001 - an older Qt without the setter: windows keep the Windows look
        log.debug("could not set the colour scheme", exc_info=True)


def apply_appearance(cfg) -> None:
    """Push palette, icon style, language and colour scheme from a config-like object (`.get(key)`)."""
    colors.set_palette(cfg.get("color_palette"), cfg.get("custom_colors"))
    icons.set_icon_style(cfg.get("icon_style"))
    setter = getattr(i18n, "apply_language_setting", None)
    if setter is not None:
        setter(cfg.get("language"))
    apply_color_scheme(cfg.get("theme"))
