"""
Periodic GitHub release checks.

The network call runs on a worker thread; every Tk interaction is marshalled
back to the main thread with ``root.after(0, ...)``, per Tkinter's
single-thread rule.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from typing import Callable

import update_checker
from config import APP_VERSION, GITHUB_REPO, Settings
from gui.dialogs import (
    show_already_latest,
    show_update_available,
    show_update_failed,
    show_update_not_configured,
)

log = logging.getLogger(__name__)


class UpdateService:
    """Owns the update timer, the background lookup and its result dialogs."""

    def __init__(self, root: tk.Misc, settings_provider: Callable[[], Settings]) -> None:
        self._root = root
        self._settings = settings_provider
        self._job = ""  # pending root.after id, "" = none

    def start_check(self, manual: bool) -> None:
        """Kick off a release lookup on a worker thread."""
        if not GITHUB_REPO:
            if manual:
                show_update_not_configured(self._root)
            else:
                log.debug("Auto update check skipped: GITHUB_REPO is not configured")
            return

        log.info("Checking for updates against %s (manual=%s)", GITHUB_REPO, manual)

        def worker() -> None:
            try:
                tag = update_checker.latest_release(GITHUB_REPO)
            except Exception as exc:  # network/parse errors are non-fatal
                self._root.after(0, lambda: self._handle_result(None, exc, manual))
            else:
                self._root.after(0, lambda: self._handle_result(tag, None, manual))

        threading.Thread(target=worker, name="update-check", daemon=True).start()

    def tick(self) -> None:
        """Startup/periodic entry point: check when due, then re-arm."""
        self._job = ""
        if update_checker.is_check_due(self._settings()):
            self.start_check(manual=False)
        else:
            self.schedule_next()

    def schedule_next(self) -> None:
        """(Re)arm the periodic auto-check timer from current preferences."""
        if self._job:
            self._root.after_cancel(self._job)
            self._job = ""
        settings = self._settings()
        interval = update_checker.interval_seconds(settings.update_check_interval)
        if interval == 0:
            return  # auto checks disabled via preferences
        remaining = update_checker.seconds_until_due(settings)
        self._job = self._root.after(max(remaining, 1) * 1000, self.tick)

    def _handle_result(
        self, tag: str | None, error: Exception | None, manual: bool
    ) -> None:
        settings = self._settings()
        if error is None:
            update_checker.record_check(settings)
        else:
            log.warning("Update check failed: %s", error)

        if tag and update_checker.is_newer(tag, APP_VERSION):
            show_update_available(self._root, tag)
        elif manual:
            if error is not None:
                show_update_failed(self._root, error)
            else:
                show_already_latest(self._root)
        self.schedule_next()


__all__ = ["UpdateService"]
