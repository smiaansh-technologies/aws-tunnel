"""
The application icon, shown in the window title bar and the system tray.

The artwork is authored once as ``assets/logo.svg`` and rasterized to
``assets/logo.png`` by ``scripts/render_logo_png.py``, so both the tray
(pystray/PIL) and the window (Tk) render from the same source.
"""

from __future__ import annotations

import logging
import tkinter as tk

from PIL import Image

from config import APP_ICON

log = logging.getLogger(__name__)


def load_icon(size: int) -> Image.Image | None:
    """Return the app icon as a square RGBA image, or None if it's missing."""
    try:
        with Image.open(APP_ICON) as source:
            return source.convert("RGBA").resize((size, size), Image.LANCZOS)
    except OSError:
        log.error("App icon not readable at %s", APP_ICON)
        return None


def apply_window_icon(root: tk.Tk) -> bool:
    """Show the app icon in the window title bar.

    Returns True when the icon was applied.
    """
    try:
        photo = tk.PhotoImage(master=root, file=str(APP_ICON))
    except (tk.TclError, OSError):
        log.error("Could not load window icon from %s", APP_ICON)
        return False

    root.iconphoto(True, photo)
    # Tk keeps only a weak reference to the image, so hold it ourselves or
    # it gets garbage collected and the title bar falls back to a default.
    root.app_icon_photo = photo
    return True


__all__ = ["apply_window_icon", "load_icon"]