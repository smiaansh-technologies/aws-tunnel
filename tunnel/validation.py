"""Validation for values passed to local tunnel processes.

These checks are deliberately performed at the tunnel boundary as well as in
the GUI.  Saved settings can be edited outside the application, so GUI-only
validation is not a security boundary.
"""

from __future__ import annotations

import ipaddress
import re


class TunnelValidationError(ValueError):
    """Raised when a tunnel value is not safe or valid for AWS/SSH."""


_HOSTNAME_RE = re.compile(r"(?=.{1,253}\Z)[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,251}[A-Za-z0-9_])?\Z")
_INSTANCE_ID_RE = re.compile(r"(?:i-[0-9a-fA-F]{8,17}|mi-[A-Za-z0-9-]{1,128})\Z")
_REGION_RE = re.compile(r"[a-z]{2}(?:-gov)?-[a-z]+-\d+\Z")


def validate_host(value: str, *, field: str = "host") -> str:
    """Return a DNS name or IP address that is safe to pass as one value."""
    if not isinstance(value, str):
        raise TunnelValidationError(f"{field} must be text")
    value = value.strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise TunnelValidationError(f"{field} is invalid")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if not _HOSTNAME_RE.fullmatch(value):
            raise TunnelValidationError(f"{field} must be a DNS name or IP address")
    return value


def validate_port(value: int, *, field: str = "port") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise TunnelValidationError(f"{field} must be between 1 and 65535")
    return value


def validate_instance_id(value: str) -> str:
    if not isinstance(value, str) or not _INSTANCE_ID_RE.fullmatch(value):
        raise TunnelValidationError("instance ID is invalid")
    return value


def validate_region(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _REGION_RE.fullmatch(value):
        raise TunnelValidationError("AWS region is invalid")
    return value


def validate_cli_profile(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        raise TunnelValidationError("AWS profile name is invalid")
    return value
