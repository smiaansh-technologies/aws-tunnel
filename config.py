"""
App-wide configuration: file locations and simple persisted settings.

Nothing AWS-specific lives here except *paths* to AWS's own files —
we never store credentials or tokens ourselves.
"""

from __future__ import annotations

import json
import logging
import shutil
from uuid import uuid4
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "aws-tunnel"
APP_VERSION = "1.2.0"

# Where published releases live, as "<owner>/<repo>" on GitHub. The
# Check-for-Updates feature queries this repo's Releases API. Leave as
# None until the repo is published; update checks are disabled (and the
# Help menu entry says so) when it is not set.
GITHUB_REPO: str | None = "smiaansh-technologies/aws-tunnel"  # e.g. "kamal/aws-tunnel"

# Where AWS itself keeps its config/cache — we only read/write the config
# file (to manage profiles) and read the SSO token cache (to show status).
AWS_DIR = Path.home() / ".aws"
AWS_CONFIG_FILE = AWS_DIR / "config"
AWS_CREDENTIALS_FILE = AWS_DIR / "credentials"
AWS_SSO_CACHE_DIR = AWS_DIR / "sso" / "cache"

# Where *this app* keeps its own data: logs and settings only.
APP_DATA_DIR = Path.home() / f".{APP_NAME}"
LOG_DIR = APP_DATA_DIR / "logs"
SETTINGS_FILE = APP_DATA_DIR / "settings.json"


def _migrate_old_app_data_dir() -> None:
    """Move settings/logs from the old ~/.aws-sso-connector/ to
    ~/.aws-tunnel/ on first run after the rename, so existing users
    don't lose their data."""
    old_dir = Path.home() / ".aws-tunnel"
    if not old_dir.exists():
        return
    if APP_DATA_DIR.exists():
        # Both exist — copy settings.json (and logs) into the new
        # dir without removing the old one.
        try:
            for item in old_dir.iterdir():
                dest = APP_DATA_DIR / item.name
                if not dest.exists():
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)
        except OSError:
            log.exception("Could not migrate old app data dir %s", old_dir)
    else:
        try:
            old_dir.rename(APP_DATA_DIR)
        except OSError:
            log.exception("Could not rename old app data dir %s -> %s", old_dir, APP_DATA_DIR)


_migrate_old_app_data_dir()


@dataclass
class TunnelPreset:
    """A saved 'bastion + target + local port' combination for one-click reopen."""

    name: str
    bastion_instance_id: str
    target_host: str
    target_port: int
    local_port: int


@dataclass
class BastionProfile:
    """Saved tunnel defaults keyed by the stable EC2 instance ID."""

    instance_id: str
    target_host: str
    target_port: int
    local_port: int


@dataclass
class TunnelProfile:
    """Named, reusable SSM port-forwarding connection."""

    profile_id: str
    name: str
    aws_profile: str
    bastion_instance_id: str
    bastion_name: str
    target_host: str
    target_port: int
    local_port: int
    method: str = "ssm"
    enabled: bool = True
    description: str = ""


@dataclass
class Settings:
    """Persisted, user-editable app settings."""

    bastion_tag_key: str = "Role"
    bastion_tag_value: str = "bastion"
    preferred_tunnel_method: str = "ssm"  # "ssm" or "ssh"
    profile_refresh_minutes: int = 5
    minimize_to_tray: bool = True
    # "off" | "daily" | "weekly" | "biweekly" | "monthly"
    update_check_interval: str = "weekly"
    # ISO timestamp of the last successful release check (auto or manual).
    last_update_check: str = ""
    window_x: int | None = None
    window_y: int | None = None
    presets: list[TunnelPreset] = field(default_factory=list)
    bastion_profiles: list[BastionProfile] = field(default_factory=list)
    tunnel_profiles: list[TunnelProfile] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Settings":
        if not SETTINGS_FILE.exists():
            return cls()
        try:
            raw = json.loads(SETTINGS_FILE.read_text())
        except json.JSONDecodeError:
            # Corrupt JSON — keep a backup for forensics, fall back to defaults.
            _backup_corrupt_settings()
            return cls()

        if not isinstance(raw, dict):
            _backup_corrupt_settings()
            return cls()

        presets = [
            TunnelPreset(**p) for p in raw.pop("presets", []) or []
            if isinstance(p, dict)
        ]
        bastion_profiles = [
            BastionProfile(**p) for p in raw.pop("bastion_profiles", []) or []
            if isinstance(p, dict)
        ]
        tunnel_profiles = [
            TunnelProfile(**p) for p in raw.pop("tunnel_profiles", []) or []
            if isinstance(p, dict)
        ]
        for saved in bastion_profiles:
            if not any(
                profile.bastion_instance_id == saved.instance_id
                for profile in tunnel_profiles
            ):
                tunnel_profiles.append(
                    TunnelProfile(
                        profile_id=uuid4().hex,
                        name=f"{saved.target_host}:{saved.target_port}",
                        aws_profile="",
                        bastion_instance_id=saved.instance_id,
                        bastion_name=saved.instance_id,
                        target_host=saved.target_host,
                        target_port=saved.target_port,
                        local_port=saved.local_port,
                    )
                )

        # Ignore keys the current schema doesn't know about (e.g. fields
        # removed/renamed by an app update) instead of failing the whole
        # load and resetting every setting to defaults.
        known_fields = {f.name for f in fields(cls)}
        unknown = set(raw) - known_fields
        if unknown:
            log.warning("Ignoring unknown settings keys: %s", sorted(unknown))
        raw = {k: v for k, v in raw.items() if k in known_fields}

        try:
            settings = cls(
                presets=presets,
                bastion_profiles=[],
                tunnel_profiles=tunnel_profiles,
                **raw,
            )
        except (TypeError, ValueError):
            log.exception("Settings file has invalid values; using defaults")
            _backup_corrupt_settings()
            return cls()
        if bastion_profiles:
            settings.save()
        return settings

    def save(self) -> None:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("bastion_profiles", None)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def _backup_corrupt_settings() -> None:
    """Keep the unreadable settings file around for forensics instead of
    silently overwriting it with defaults."""
    if not SETTINGS_FILE.exists():
        return
    backup = SETTINGS_FILE.with_suffix(".json.bak")
    try:
        shutil.copy2(SETTINGS_FILE, backup)
        log.warning("Settings file unreadable; backed up to %s", backup)
    except OSError:
        log.exception("Could not back up corrupt settings file")


def ensure_app_dirs() -> None:
    """Create the app's own data/log directories if they don't exist yet."""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
