"""
Help menu support: locate the bundled user documentation and open it.

Works in two modes:
- source run      -> docs live next to the project root
- frozen (PyInstaller) -> docs are bundled and unpacked to sys._MEIPASS

The guide opens in the user's default browser: full HTML/CSS rendering
with zero extra runtime dependencies.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from config import APP_VERSION, LOG_DIR, app_root

log = logging.getLogger(__name__)


def user_guide_path() -> Path:
    return app_root() / "docs" / "user_guide.html"


def open_user_guide() -> bool:
    """Open the HTML user guide in the default browser.

    Returns True when the guide exists and was opened.
    """
    path = user_guide_path()
    if not path.exists():
        log.error("User guide not found at %s", path)
        return False
    webbrowser.open(path.as_uri())
    log.info("Opened user guide: %s", path)
    return True


def open_logs_folder() -> None:
    """Reveal the app's log directory in the OS file manager."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("Could not create log directory %s", LOG_DIR)
    _reveal_in_file_manager(LOG_DIR)


def _reveal_in_file_manager(path: Path) -> None:
    if sys.platform == "win32":
        # Explorer launch, not a shell: the path is app-owned (LOG_DIR), never
        # user input, so there is nothing to inject here.
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


__all__ = ["APP_VERSION", "app_root", "open_logs_folder", "open_user_guide", "user_guide_path"]
