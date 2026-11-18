"""Texts of the custom icons feature (kept apart from i18n.py so it can evolve independently)."""
from __future__ import annotations

STRINGS = {
    "en": {
        "custom_icons_title": "Custom icons",
        "custom_icons_tab_kind": "Hardware type",
        "custom_icons_tab_hw": "Hardware",
        "custom_icons_tab_sensor": "Sensor",
        "custom_icons_choose": "Choose...",
        "custom_icons_remove": "Remove",
        "custom_icons_default": "default",
        "custom_icons_custom": "custom",
        "custom_icons_need_admin": "Custom icons can only be imported when the app runs as administrator.",
        "custom_icons_untrusted_dir": "The icons folder is not protected (administrators only), so importing is disabled.",
        "custom_icons_clear": "Remove all custom icons",
        "hw_cpu": "CPU", "hw_gpu": "GPU", "hw_ram": "Memory", "hw_mainboard": "Motherboard",
        "hw_storage": "Storage", "hw_network": "Network", "hw_other": "Other",
        "custom_icons_limit": "Too many custom icons (at most 200).",
        "icon_err_extension": "Unsupported file type. Use png, jpg, bmp, ico, gif, webp or svg.",
        "icon_err_too_big": "The file is too big (images up to 2 MiB, SVG up to 512 KiB).",
        "icon_err_dimensions": "The image is too large (at most 2048 x 2048 px).",
        "icon_err_unreadable": "The file is not a valid image.",
        "icon_err_io": "The file could not be read or saved.",
        "icon_err_anim_long": "The animation is too long or has too many frames.",
        "icon_err_not_elevated": "Run the app as administrator to import icons.",
        "icon_err_untrusted_dir": "The icons folder is not protected; import is disabled.",
    },
    "es": {
        "custom_icons_title": "Iconos personalizados",
        "custom_icons_tab_kind": "Tipo de hardware",
        "custom_icons_tab_hw": "Hardware",
        "custom_icons_tab_sensor": "Sensor",
        "custom_icons_choose": "Elegir...",
        "custom_icons_remove": "Quitar",
        "custom_icons_default": "predeterminado",
        "custom_icons_custom": "personalizado",
        "custom_icons_need_admin": "Solo se pueden importar iconos cuando la aplicación se ejecuta como administrador.",
        "custom_icons_untrusted_dir": "La carpeta de iconos no está protegida (solo administradores), por lo que la importación está desactivada.",
        "custom_icons_clear": "Quitar todos los iconos personalizados",
        "hw_cpu": "CPU", "hw_gpu": "GPU", "hw_ram": "Memoria", "hw_mainboard": "Placa base",
        "hw_storage": "Almacenamiento", "hw_network": "Red", "hw_other": "Otro",
        "custom_icons_limit": "Demasiados iconos personalizados (máximo 200).",
        "icon_err_extension": "Tipo de archivo no admitido. Usa png, jpg, bmp, ico, gif, webp o svg.",
        "icon_err_too_big": "El archivo es demasiado grande (imágenes hasta 2 MiB, SVG hasta 512 KiB).",
        "icon_err_dimensions": "La imagen es demasiado grande (máximo 2048 x 2048 px).",
        "icon_err_unreadable": "El archivo no es una imagen válida.",
        "icon_err_io": "No se pudo leer o guardar el archivo.",
        "icon_err_anim_long": "La animación es demasiado larga o tiene demasiados fotogramas.",
        "icon_err_not_elevated": "Ejecuta la aplicación como administrador para importar iconos.",
        "icon_err_untrusted_dir": "La carpeta de iconos no está protegida; la importación está desactivada.",
    },
}


def tr(key: str, lang: str | None = None) -> str:
    """Text for `key` in `lang` (default: the application language); falls back to English, then the key."""
    if lang is None:
        try:
            from . import i18n
            lang = i18n.get_language()
        except Exception:
            lang = "en"
    return STRINGS.get(lang, STRINGS["en"]).get(key) or STRINGS["en"].get(key, key)
