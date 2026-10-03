"""
The application icon, shown in the window title bar and the system tray.

The artwork is authored once as ``assets/logo.svg`` and rasterized to
``assets/logo.png`` by ``scripts/render_logo_png.py``, so both the tray
(pystray/PIL) and the window (Tk) render from the same source.
"""

from __future__ import annotations

import logging
import tkinter as tk

from PIL import Image, ImageDraw

from config import APP_ICON

log = logging.getLogger(__name__)


def _placeholder_icon(size: int) -> Image.Image:
    """Draw a stand-in tile for when the icon asset can't be read.

    pystray serialises whatever it is handed straight to Pillow
    (``image.save(...)``), so handing it ``None`` raises an AttributeError
    on the tray thread rather than at construction. Always giving it a real
    image keeps a broken/missing asset from taking the tray down with it.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=size // 6, fill=(255, 153, 0, 255)
    )
    # Centre the letter without relying on font metrics varying by platform.
    left, top, right, bottom = draw.textbbox((0, 0), "S")
    draw.text(
        ((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top),
        "S",
        fill=(255, 255, 255, 255),
    )
    return image


def load_icon(size: int) -> Image.Image:
    """Return the app icon as a square RGBA image.

    Falls back to a drawn placeholder if the asset is missing or corrupt, so
    callers never have to handle ``None``.
    """
    try:
        with Image.open(APP_ICON) as source:
            return source.convert("RGBA").resize((size, size), Image.LANCZOS)
    except OSError:
        log.error("App icon not readable at %s", APP_ICON)
        return _placeholder_icon(size)


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
