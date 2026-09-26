"""
Read/write AWS SSO profiles directly in ~/.aws/config.

We use `configparser` instead of shelling out to the interactive
`aws configure sso` wizard: it's just as correct, and it's scriptable,
so the GUI form can create/delete profiles reliably in one step.
"""

from __future__ import annotations

import configparser
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config import AWS_CONFIG_FILE, AWS_SSO_CACHE_DIR

log = logging.getLogger(__name__)

_DEFAULT_SECTION = "default"
_IGNORED_CACHE_FILE_PREFIXES = ("aws-toolkit-vscode-client-id-",)


@dataclass
class AWSProfile:
    """One SSO profile as it appears in ~/.aws/config."""

    name: str
    sso_start_url: str
    sso_region: str
    sso_account_id: str
    sso_role_name: str
    region: str = "us-east-1"
    output: str = "json"


def _section_name(profile_name: str) -> str:
    return _DEFAULT_SECTION if profile_name == _DEFAULT_SECTION else f"profile {profile_name}"


def _load_config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    if AWS_CONFIG_FILE.exists():
        parser.read(AWS_CONFIG_FILE)
    return parser


def _save_config(parser: configparser.ConfigParser) -> None:
    AWS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with AWS_CONFIG_FILE.open("w") as fh:
        parser.write(fh)


def list_profiles() -> list[AWSProfile]:
    """Return every SSO profile found in ~/.aws/config."""
    parser = _load_config()
    profiles: list[AWSProfile] = []

    for section in parser.sections():
        if section != _DEFAULT_SECTION and not section.startswith("profile "):
            continue

        session_name = parser.get(section, "sso_session", fallback="")
        session_section = f"sso-session {session_name}" if session_name else ""
        if parser.has_option(section, "sso_start_url"):
            sso_start_url = parser.get(section, "sso_start_url")
            sso_region = parser.get(section, "sso_region", fallback="")
        elif session_section and parser.has_section(session_section):
            sso_start_url = parser.get(session_section, "sso_start_url", fallback="")
            sso_region = parser.get(session_section, "sso_region", fallback="")
        else:
            continue  # not an SSO profile (e.g. static-key profile) — skip

        if not sso_start_url or not sso_region:
            continue

        name = _DEFAULT_SECTION if section == _DEFAULT_SECTION else section[len("profile "):]
        profiles.append(
            AWSProfile(
                name=name,
                sso_start_url=sso_start_url,
                sso_region=sso_region,
                sso_account_id=parser.get(section, "sso_account_id", fallback=""),
                sso_role_name=parser.get(section, "sso_role_name", fallback=""),
                region=parser.get(section, "region", fallback="us-east-1"),
                output=parser.get(section, "output", fallback="json"),
            )
        )

    log.debug("Found %d SSO profile(s)", len(profiles))
    return profiles


def create_profile(profile: AWSProfile) -> None:
    """Add or overwrite a profile section in ~/.aws/config."""
    parser = _load_config()
    section = _section_name(profile.name)

    if not parser.has_section(section) and section != _DEFAULT_SECTION:
        parser.add_section(section)

    parser.set(section, "sso_start_url", profile.sso_start_url)
    parser.set(section, "sso_region", profile.sso_region)
    parser.set(section, "sso_account_id", profile.sso_account_id)
    parser.set(section, "sso_role_name", profile.sso_role_name)
    parser.set(section, "region", profile.region)
    parser.set(section, "output", profile.output)

    _save_config(parser)
    log.info("Created/updated profile '%s'", profile.name)


def delete_profile(profile_name: str) -> bool:
    """Remove a profile section. Returns True if something was actually removed."""
    parser = _load_config()
    section = _section_name(profile_name)

    if section == _DEFAULT_SECTION:
        # Never silently wipe the [default] section's credentials via this path.
        log.warning("Refusing to delete the 'default' profile section")
        return False

    removed = parser.remove_section(section)
    if removed:
        _save_config(parser)
        log.info("Deleted profile '%s'", profile_name)
    else:
        log.warning("Profile '%s' not found; nothing deleted", profile_name)
    return removed


def get_token_expiry(sso_start_url: str) -> Optional[datetime]:
    """
    Look up the cached SSO token for this start URL and return its expiry
    time, or None if there's no valid cached token.
    """
    if not AWS_SSO_CACHE_DIR.exists():
        return None

    expiries: list[datetime] = []
    for cache_file in AWS_SSO_CACHE_DIR.glob("*.json"):
        if cache_file.name.startswith(_IGNORED_CACHE_FILE_PREFIXES):
            continue
        try:
            data = json.loads(cache_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if data.get("startUrl") != sso_start_url:
            continue

        expires_at_raw = data.get("expiresAt")
        if not expires_at_raw:
            continue

        try:
            expiries.append(datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00")))
        except ValueError:
            continue

    return max(expiries, default=None)


def is_logged_in(profile: AWSProfile) -> bool:
    """True if this profile has valid, usable SSO credentials."""
    expiry = get_token_expiry(profile.sso_start_url)
    if expiry is None or expiry <= datetime.now(timezone.utc):
        return False
    # Verify boto3 can actually resolve credentials for this profile
    try:
        import boto3
        session = boto3.Session(profile_name=profile.name)
        creds = session.get_credentials()
        if creds is None:
            return False
        # Force credential resolution to verify the SSO token is usable
        frozen = creds.get_frozen_credentials()
        return bool(frozen.access_key and frozen.secret_key)
    except Exception:
        return False
