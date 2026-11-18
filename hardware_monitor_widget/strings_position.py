"""Texts of the position / pill features (kept apart from i18n.py so they can evolve independently)."""
from __future__ import annotations

STRINGS = {
    "en": {
        "pos_monitor_primary": "Primary",
        "pos_monitor_label": "Monitor {n}",
        "pos_monitor_no_taskbar": " (no taskbar: floating)",
        "pos_monitor_disconnected": "Disconnected monitor (using the primary)",
        "pos_warn_left_needs_centered_icons": "The left position needs centred Windows icons; the right one is used instead.",
        "pos_warn_no_taskbar_floating": "This monitor has no taskbar; the pills float on it.",
        "pos_floating_hint": "Drag the pills to move them, or pick a corner in the floating position.",
        "pos_fullscreen_hint": "Over a fullscreen app the pills let clicks through; hold Shift to use them.",
    },
    "es": {
        "pos_monitor_primary": "Principal",
        "pos_monitor_label": "Monitor {n}",
        "pos_monitor_no_taskbar": " (sin barra: flotante)",
        "pos_monitor_disconnected": "Monitor desconectado (se usa el principal)",
        "pos_warn_left_needs_centered_icons": "La posición izquierda necesita los iconos de Windows centrados; se usa la derecha.",
        "pos_warn_no_taskbar_floating": "Este monitor no tiene barra de tareas; las píldoras flotan en él.",
        "pos_floating_hint": "Arrastra las píldoras para moverlas, o elige una esquina en la posición flotante.",
        "pos_fullscreen_hint": "Sobre una app a pantalla completa las píldoras dejan pasar los clics; mantén Mayús para usarlas.",
    },
}
