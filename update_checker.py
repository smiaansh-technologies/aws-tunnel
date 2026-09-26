"""
Release update checking against the project's GitHub Releases.

Pure stdlib, no GUI or AWS dependencies — designed to be called from a
background thread while the GUI marshals results onto the Tk main
thread. The repo is configured via config.GITHUB_REPO ("<owner>/<repo>");
when unset, update checking is disabled.

Version comparison is intentionally simple: numeric components only
("v1.2.3", "1.10.0"), which matches this project's own APP_VERSION
scheme. Pre-release suffixes are ignored for comparison.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import config

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 10

# Allowed auto-check intervals; anything else (notably "off") disables
# automatic checks.
INTERVALS: dict[str, timedelta] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "biweekly": timedelta(weeks=2),
    "monthly": timedelta(days=30),
}

_NUMBER_PREFIX = re.compile(r"\d+")


class UpdateCheckError(RuntimeError):
    """Raised when the release lookup fails."""


def releases_api_url(repo: str) -> str:
    return f"https://api.github.com/repos/{repo}/releases/latest"


def releases_page_url(repo: str) -> str:
    return f"https://github.com/{repo}/releases/latest"


def latest_release(repo: str) -> str:
    """Return the tag name of the repo's latest published release."""
    request = urllib.request.Request(
        releases_api_url(repo),
        headers={"User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        raise UpdateCheckError(f"Could not reach GitHub releases: {exc}") from exc

    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not tag:
        raise UpdateCheckError("Release response contained no tag_name")
    return str(tag)


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse 'v1.10.2' -> (1, 10, 2); non-numeric parts become 0."""
    components = []
    for part in version.strip().lstrip("vV").split("."):
        match = _NUMBER_PREFIX.match(part)
        components.append(int(match.group()) if match else 0)
    return tuple(components)


def is_newer(candidate: str, current: str) -> bool:
    """True when the candidate release tag is newer than the current version."""
    candidate_parts = _version_tuple(candidate)
    current_parts = _version_tuple(current)
    width = max(len(candidate_parts), len(current_parts), 1)
    candidate_parts += (0,) * (width - len(candidate_parts))
    current_parts += (0,) * (width - len(current_parts))
    return candidate_parts > current_parts


def interval_seconds(interval: str) -> int:
    """Seconds for a named interval; 0 when the interval is off/unknown."""
    delta = INTERVALS.get(interval)
    return int(delta.total_seconds()) if delta else 0


def seconds_until_due(settings) -> int:
    """Seconds until the next auto check is due (0 = due now)."""
    interval = interval_seconds(settings.update_check_interval)
    if interval == 0:
        return 0  # caller treats unset intervals as "never schedule"
    if not settings.last_update_check:
        return 0
    try:
        last = datetime.fromisoformat(settings.last_update_check)
    except ValueError:
        return 0
    elapsed = (datetime.now() - last).total_seconds()
    return max(0, interval - int(elapsed))


def is_check_due(settings) -> bool:
    """True when an auto check should run at this moment."""
    return interval_seconds(settings.update_check_interval) > 0 and seconds_until_due(settings) == 0


def record_check(settings) -> None:
    """Stamp the last successful check time and persist it."""
    settings.last_update_check = datetime.now().isoformat()
    settings.save()