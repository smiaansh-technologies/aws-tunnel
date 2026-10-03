"""
System tray integration.

pystray runs its own event loop and blocks, so it's started on its own
daemon thread. It only ever calls back into Tkinter via `root.after(0, ...)`
to stay on the safe side of Tkinter's single-thread rule.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk

import pystray

from gui.icons import load_icon

log = logging.getLogger(__name__)

ICON_SIZE = 64


class TrayIcon:
    """Wraps a pystray.Icon bound to the app's main Tk window."""

    def __init__(self, root: tk.Tk, on_quit) -> None:
        self.root = root
        self._on_quit = on_quit
        self._icon = pystray.Icon(
            "aws_tunnel",
            icon=load_icon(ICON_SIZE),
            title="AWS Tunnel",
            menu=pystray.Menu(
                pystray.MenuItem("Show", self._show, default=True),
                pystray.MenuItem("Quit", self._quit),
            ),
        )

    def start(self) -> None:
        threading.Thread(target=self._icon.run, daemon=True).start()

    def stop(self) -> None:
        self._icon.stop()

    def _show(self, icon=None, item=None) -> None:
        self.root.after(0, self._deiconify)

    def _deiconify(self) -> None:
        self.root.deiconify()
        self.root.attributes("-alpha", 1.0)
        self.root.lift()
        self.root.focus_force()

    def _quit(self, icon=None, item=None) -> None:
        self.root.after(0, self._on_quit)
