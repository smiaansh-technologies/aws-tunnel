"""
Shared helpers for spawning child processes from the GUI without
leaving stray console windows behind.

The app ships as a windowed executable (PyInstaller console=False).
Any console-subsystem child it launches (aws CLI, taskkill, ...) is
allocated a brand-new console window on Windows unless CREATE_NO_WINDOW
is passed — that console parks itself in the taskbar and looks like a
bug. Every background spawn should go through no_window_kwargs().
"""

from __future__ import annotations

import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"


def no_window_kwargs(extra_flags: int = 0) -> dict:
    """Extra subprocess kwargs that suppress the child's console window.

    Returns an empty dict on non-Windows platforms, where the flag does
    not exist and consoles are never allocated for GUI parents anyway.
    """
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW | extra_flags}
    return {}
