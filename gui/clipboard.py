"""Clipboard helpers with a Windows-native text path."""

from __future__ import annotations

import ctypes
import os
import tkinter as tk


def read_text(widget: tk.Widget) -> str:
    """Read Unicode text from the OS clipboard, falling back to Tk."""
    if os.name == "nt":
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        if user32.OpenClipboard(None):
            try:
                handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
                if handle:
                    pointer = kernel32.GlobalLock(handle)
                    if pointer:
                        try:
                            return ctypes.wstring_at(pointer).strip()
                        finally:
                            kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
    try:
        return widget.clipboard_get().strip()
    except tk.TclError:
        return ""


def replace_from_clipboard(widget: tk.Entry, _event: tk.Event) -> str:
    """Replace an entry's contents with text from the OS clipboard."""
    value = read_text(widget)
    if value:
        widget.delete(0, tk.END)
        widget.insert(0, value)
    return "break"
