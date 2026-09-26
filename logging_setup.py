"""
One place to configure logging for the whole app.

Every other module just does `logging.getLogger(__name__)` and logs
normally — nobody else touches handlers or formatters. This keeps log
format/location consistent and makes it trivial to change later.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from config import LOG_DIR, ensure_app_dirs

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_FILE = LOG_DIR / "app.log"

# Rotate at 5 MB, keep 5 old copies — enough history without unbounded growth.
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


def setup_logging(debug: bool = False) -> None:
    """Configure the root logger. Call this once, at app startup."""
    ensure_app_dirs()

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if debug:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    logging.getLogger(__name__).info("Logging initialized (debug=%s)", debug)
