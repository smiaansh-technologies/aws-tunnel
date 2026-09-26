"""
Drives `aws sso login` / `aws sso logout` for a given profile.

We shell out to the AWS CLI (v2) rather than reimplementing the SSO OIDC
device-code flow ourselves: the CLI already opens the browser, polls for
the token, and writes it to ~/.aws/sso/cache in the format the rest of
this app (and every other AWS tool) expects. Fewer moving parts, less
code to get wrong.

Assumption: AWS CLI v2 is installed and on PATH. If that's not true in
your environment, this is the one module that would need to change to
call the SSO OIDC API via boto3 directly instead.
"""

from __future__ import annotations

import logging
import subprocess

from process_utils import no_window_kwargs

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 180  # generous — the user has to click through a browser


class SsoLoginError(RuntimeError):
    """Raised when `aws sso login`/`logout` exits non-zero."""


def login(profile_name: str) -> None:
    """
    Run `aws sso login --profile <name>`. Blocks until the browser flow
    completes or times out. Intended to be called from a background
    thread by the GUI, not the main/UI thread.
    """
    log.info("Starting SSO login for profile '%s'", profile_name)
    _run(["aws", "sso", "login", "--profile", profile_name])
    log.info("SSO login succeeded for profile '%s'", profile_name)


def logout(profile_name: str) -> None:
    """Run `aws sso logout --profile <name>`."""
    log.info("Logging out profile '%s'", profile_name)
    _run(["aws", "sso", "logout", "--profile", profile_name])
    log.info("Logged out profile '%s'", profile_name)


def _run(command: list[str]) -> None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            **no_window_kwargs(),
        )
    except FileNotFoundError as exc:
        raise SsoLoginError("AWS CLI not found on PATH — is it installed?") from exc
    except subprocess.TimeoutExpired as exc:
        raise SsoLoginError("Timed out waiting for the browser login to complete") from exc

    if result.stdout:
        log.debug("aws stdout: %s", result.stdout.strip())
    if result.returncode != 0:
        log.error("Command failed (%s): %s", " ".join(command), result.stderr.strip())
        raise SsoLoginError(result.stderr.strip() or "Unknown AWS CLI error")
