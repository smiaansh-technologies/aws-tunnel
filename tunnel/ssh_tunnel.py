"""
Classic SSH local-port-forward tunnel, for bastions that don't run the
SSM Agent. This is the fallback path — prefer ssm_tunnel when possible.

Implementation follows the standard paramiko local-forwarding pattern:
a local TCP server accepts connections and, for each one, opens a
forwarded channel over a single SSH connection to the bastion.
"""

from __future__ import annotations

import logging
import select
import socket
import socketserver
import threading
from dataclasses import dataclass

import paramiko

from tunnel.validation import TunnelValidationError, validate_host, validate_port

log = logging.getLogger(__name__)


class SshTunnelError(RuntimeError):
    """Raised when the SSH connection to the bastion fails."""


@dataclass
class SshTunnelHandle:
    """Everything needed to later stop a running SSH tunnel."""

    client: paramiko.SSHClient
    server: socketserver.ThreadingTCPServer
    server_thread: threading.Thread


def start(
    bastion_host: str,
    bastion_port: int,
    username: str,
    private_key_path: str,
    target_host: str,
    target_port: int,
    local_port: int,
) -> SshTunnelHandle:
    """Open an SSH connection to the bastion and start local forwarding."""
    try:
        bastion_host = validate_host(bastion_host, field="bastion host")
        target_host = validate_host(target_host, field="target host")
        bastion_port = validate_port(bastion_port, field="bastion port")
        target_port = validate_port(target_port, field="target port")
        local_port = validate_port(local_port, field="local port")
    except TunnelValidationError as exc:
        raise SshTunnelError(str(exc)) from exc

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())

    try:
        client.connect(
            bastion_host,
            port=bastion_port,
            username=username,
            key_filename=private_key_path,
            timeout=15,
        )
    except (paramiko.SSHException, OSError) as exc:
        raise SshTunnelError(f"Could not connect to bastion {bastion_host}: {exc}") from exc

    try:
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise SshTunnelError("SSH connection closed before the tunnel could start")
        forwarder_cls = _make_forwarder_class(transport, target_host, target_port)
        server = _LocalOnlyTCPServer(("127.0.0.1", local_port), forwarder_cls)
        server.daemon_threads = True
    except Exception:
        client.close()
        raise

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    log.info(
        "SSH tunnel up: localhost:%s -> %s (via %s) -> %s:%s",
        local_port, bastion_host, username, target_host, target_port,
    )
    return SshTunnelHandle(client=client, server=server, server_thread=thread)


class _LocalOnlyTCPServer(socketserver.ThreadingTCPServer):
    """Loopback-only forwarder with a small pending-connection queue."""

    request_queue_size = 16


def stop(handle: SshTunnelHandle) -> None:
    """Shut down the local forwarding server and close the SSH connection."""
    handle.server.shutdown()
    handle.server.server_close()
    handle.client.close()
    log.info("SSH tunnel stopped")


def _make_forwarder_class(transport: paramiko.Transport, target_host: str, target_port: int):
    """Build a request handler bound to this tunnel's transport/target."""

    class ForwardHandler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            try:
                channel = transport.open_channel(
                    "direct-tcpip",
                    (target_host, target_port),
                    self.request.getpeername(),
                )
            except paramiko.SSHException as exc:
                log.error("Failed to open forwarded channel: %s", exc)
                return

            if channel is None:
                return

            try:
                _pump(self.request, channel)
            finally:
                channel.close()
                self.request.close()

    return ForwardHandler


def _pump(sock, channel) -> None:
    """Relay bytes both directions between the local socket and SSH channel."""
    sock.settimeout(30)
    while True:
        try:
            readable, _, _ = select.select([sock, channel], [], [], 1.0)
        except (OSError, ValueError):
            break
        if sock in readable:
            try:
                data = sock.recv(4096)
            except (OSError, socket.timeout):
                break
            if not data:
                break
            channel.sendall(data)
        if channel in readable:
            try:
                data = channel.recv(4096)
            except (OSError, socket.timeout):
                break
            if not data:
                break
            try:
                sock.sendall(data)
            except (OSError, socket.timeout):
                break
