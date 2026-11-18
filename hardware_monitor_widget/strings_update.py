"""Texts of the update feature (kept apart from i18n.py so it can evolve independently)."""
from __future__ import annotations

STRINGS = {
    "en": {
        "upd_check": "Check for updates",
        "upd_auto": "Check for updates automatically (once a day, notify only)",
        "upd_checking": "Checking for updates...",
        "upd_uptodate": "You have the latest version.",
        "upd_new": "A new version {version} is available.",
        "upd_notes": "View release notes",
        "upd_open_page": "Open the release page",
        "upd_install": "Download and install",
        "upd_disabled": "Updates are not configured in this build.",
        "upd_portable_hint": "This copy is not the installed version, so it cannot update itself. "
                             "Download the new version from the release page.",
        "upd_confirm_title": "Install update",
        "upd_confirm": "The app will close and version {version} will be installed. Continue?",
        "upd_downloading": "Downloading and verifying version {version}...",
        "upd_launching": "Starting the installer...",
        "upd_cancel": "Cancel",
        "upd_notify_title": "Update available",
        "upd_notify_body": "Version {version} is available. Open Settings > Check for updates to install it.",
        "upd_err_disabled": "Updates are not configured in this build (no signing keys). "
                            "Download new versions from the release page.",
        "upd_err_network": "Could not reach GitHub. Check your connection and try again.",
        "upd_err_url": "The download address is not valid. The update was not installed.",
        "upd_err_size": "The download is larger than expected. The update was not installed.",
        "upd_err_nosum": "The release has no checksum for the installer. The update was not installed.",
        "upd_err_hash": "The downloaded file failed the integrity check and was deleted. "
                        "The update was not installed.",
        "upd_err_cancelled": "Download cancelled.",
        "upd_err_launch": "Could not start the installer.",
        "upd_err_blocked": "Windows blocked the installer (Smart App Control) because it has no Windows "
                           "digital signature. Automatic updates are not available on this PC; "
                           "see the release page.",
        "upd_err_unverified": "The authenticity of this update cannot be verified, so it was not installed. "
                              "Download it manually from the release page.",
        "upd_err_mode": "Only the installed copy can update itself. "
                        "Download the new version from the release page.",
        "upd_err_timeout": "The download took too long and was stopped. Try again later.",
        "upd_err_dir": "A protected download folder (administrators only) could not be prepared, so the "
                       "update was not installed. Download it from the release page.",
    },
    "es": {
        "upd_check": "Buscar actualizaciones",
        "upd_auto": "Buscar actualizaciones automáticamente (una vez al día, solo avisa)",
        "upd_checking": "Buscando actualizaciones...",
        "upd_uptodate": "Tienes la última versión.",
        "upd_new": "Hay una versión nueva {version}.",
        "upd_notes": "Ver novedades",
        "upd_open_page": "Abrir la página de la release",
        "upd_install": "Descargar e instalar",
        "upd_disabled": "Las actualizaciones no están configuradas en esta compilación.",
        "upd_portable_hint": "Esta copia no es la versión instalada, así que no puede actualizarse sola. "
                             "Descarga la versión nueva desde la página de la release.",
        "upd_confirm_title": "Instalar actualización",
        "upd_confirm": "Se cerrará la app y se instalará la versión {version}. ¿Continuar?",
        "upd_downloading": "Descargando y verificando la versión {version}...",
        "upd_launching": "Iniciando el instalador...",
        "upd_cancel": "Cancelar",
        "upd_notify_title": "Actualización disponible",
        "upd_notify_body": "Hay una versión nueva {version}. Abre Ajustes > Buscar actualizaciones para "
                           "instalarla.",
        "upd_err_disabled": "Las actualizaciones no están configuradas en esta compilación (sin claves de "
                            "firma). Descarga las versiones nuevas desde la página de la release.",
        "upd_err_network": "No se pudo conectar con GitHub. Revisa tu conexión e inténtalo de nuevo.",
        "upd_err_url": "La dirección de descarga no es válida. No se instaló la actualización.",
        "upd_err_size": "La descarga es mayor de lo esperado. No se instaló la actualización.",
        "upd_err_nosum": "La release no incluye la suma de verificación del instalador. "
                         "No se instaló la actualización.",
        "upd_err_hash": "El archivo descargado no pasó la comprobación de integridad y se ha borrado. "
                        "No se instaló la actualización.",
        "upd_err_cancelled": "Descarga cancelada.",
        "upd_err_launch": "No se pudo iniciar el instalador.",
        "upd_err_blocked": "Windows bloqueó el instalador (Control inteligente de aplicaciones) porque no "
                           "tiene firma digital de Windows. Las actualizaciones automáticas no están "
                           "disponibles en este equipo; consulta la página de la release.",
        "upd_err_unverified": "No se puede verificar la autenticidad de esta actualización, así que no se "
                              "instaló. Descárgala manualmente desde la página de la release.",
        "upd_err_mode": "Solo la copia instalada puede actualizarse sola. "
                        "Descarga la versión nueva desde la página de la release.",
        "upd_err_timeout": "La descarga tardó demasiado y se detuvo. Inténtalo más tarde.",
        "upd_err_dir": "No se pudo preparar una carpeta de descarga protegida (solo administradores), así "
                       "que no se instaló la actualización. Descárgala desde la página de la release.",
    },
}
