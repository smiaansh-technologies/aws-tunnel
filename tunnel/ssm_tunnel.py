"""
Tunnels built on AWS SSM Session Manager port forwarding and
interactive shell sessions.

Port-forwarding is the recommended method: it needs no SSH key pair
and no open inbound port on the bastion — only the SSM Agent and an
IAM role/SSO permissions that allow ssm:StartSession.  It reuses the
exact same SSO credentials the rest of the app already has.

Interactive sessions give a shell directly on the remote instance
via the default AmazonSSM-SessionManager document.

Requires the `session-manager-plugin` to be installed alongside the
AWS CLI (AWS's own requirement for `aws ssm start-session`).
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess

from process_utils import no_window_kwargs
from tunnel.validation import (
    TunnelValidationError,
    validate_cli_profile,
    validate_host,
    validate_instance_id,
    validate_port,
    validate_region,
)

log = logging.getLogger(__name__)

_STOP_TIMEOUT_SECONDS = 5


def _process_start_kwargs(*, new_console: bool = False) -> dict:
    """Create an isolated process tree that can later be stopped as a unit.

    Background launches get CREATE_NO_WINDOW so the windowed exe does not
    pop up a stray console for the AWS CLI; interactive sessions opt into
    CREATE_NEW_CONSOLE instead so the user has a shell to type in.
    """
    if platform.system() == "Windows":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if new_console:
            flags |= subprocess.CREATE_NEW_CONSOLE
        else:
            flags |= subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags}
    return {"start_new_session": True}


def _stop_process_tree(process: subprocess.Popen) -> None:
    """Stop the AWS CLI and its session-manager-plugin child process."""
    if process.poll() is not None:
        return

    if platform.system() == "Windows":
        # The AWS CLI starts session-manager-plugin as a child. Terminating
        # only the CLI can leave that child listening on the local port.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_STOP_TIMEOUT_SECONDS,
            **no_window_kwargs(),
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return

    try:
        process.wait(timeout=_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        if platform.system() == "Windows":
            process.kill()
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=_STOP_TIMEOUT_SECONDS)


class SsmTunnelError(RuntimeError):
    """Raised when the `aws ssm start-session` process fails to start."""


def start(
    profile_name: str,
    instance_id: str,
    target_host: str,
    target_port: int,
    local_port: int,
    region: str | None = None,
) -> subprocess.Popen:
    """Launch an AWS SSM remote-host port-forwarding session."""
    try:
        profile_name = validate_cli_profile(profile_name)
        instance_id = validate_instance_id(instance_id)
        target_host = validate_host(target_host, field="target host")
        target_port = validate_port(target_port, field="target port")
        local_port = validate_port(local_port, field="local port")
        region = validate_region(region)
    except TunnelValidationError as exc:
        raise SsmTunnelError(str(exc)) from exc
    command = [
        "aws", "ssm", "start-session",
        "--profile", profile_name,
        "--target", instance_id,
    ]
    if region:
        command.extend(["--region", region])
    command.extend([
        "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
        "--parameters", (
            f"host=[\"{target_host}\"],"
            f"portNumber=[\"{target_port}\"],"
            f"localPortNumber=[\"{local_port}\"]"
        ),
    ])

    log.debug("SSM command arguments: %r", command)
    log.info(
        "Starting SSM tunnel: %s:%s via %s -> localhost:%s",
        target_host, target_port, instance_id, local_port,
    )

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_process_start_kwargs(),
        )
    except FileNotFoundError as exc:
        raise SsmTunnelError(
            "AWS CLI (and session-manager-plugin) not found on PATH"
        ) from exc

    return process


def stop(process: subprocess.Popen) -> None:
    """Terminate a running SSM tunnel process."""
    _stop_process_tree(process)
    log.info("SSM tunnel process (pid=%s) stopped", process.pid)


# -- Interactive SSM Session -------------------------------------------


class SsmSessionError(RuntimeError):
    """Raised when the `aws ssm start-session` shell fails to start."""


def start_session(
    profile_name: str,
    instance_id: str,
    region: str | None = None,
) -> subprocess.Popen:
    """Launch an interactive SSM shell session in a new console window.

    Uses the default AmazonSSM-SessionManager document, which gives the
    user a full shell on the remote instance. The remote account and any
    privilege elevation remain an explicit user choice.
    """
    try:
        profile_name = validate_cli_profile(profile_name)
        instance_id = validate_instance_id(instance_id)
        region = validate_region(region)
    except TunnelValidationError as exc:
        raise SsmSessionError(str(exc)) from exc

    command = [
        "aws", "ssm", "start-session",
        "--profile", profile_name,
        "--target", instance_id,
    ]
    if region:
        command.extend(["--region", region])

    log.debug("SSM session command: %r", command)
    log.info("Starting SSM session on %s (profile=%s)", instance_id, profile_name)

    if platform.system() == "Windows":
        return _start_session_windows(command)
    else:
        return _start_session_posix(command)


def _start_session_windows(command: list[str]) -> subprocess.Popen:
    """Launch the AWS CLI in a new console without an intermediate shell.

    Passing a list directly prevents configuration values from being parsed as
    cmd.exe or PowerShell syntax.  It also avoids injecting keystrokes into
    whichever application happens to have focus.
    """
    try:
        return subprocess.Popen(command, **_process_start_kwargs(new_console=True))
    except FileNotFoundError as exc:
        raise SsmSessionError("AWS CLI (and session-manager-plugin) not found on PATH") from exc


def _start_session_posix(command: list[str]) -> subprocess.Popen:
    """Linux/macOS: launch directly.  The init command is typed by the caller."""
    try:
        process = subprocess.Popen(command, **_process_start_kwargs())
    except FileNotFoundError as exc:
        raise SsmSessionError(
            "AWS CLI (and session-manager-plugin) not found on PATH"
        ) from exc
    return process


def stop_session(process: subprocess.Popen) -> None:
    """Terminate a running SSM interactive session."""
    _stop_process_tree(process)
    log.info("SSM session process (pid=%s) stopped", process.pid)
