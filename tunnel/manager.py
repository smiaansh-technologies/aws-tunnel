"""
Single source of truth for "what tunnels are currently open."

The GUI never talks to ssm_tunnel/ssh_tunnel directly — it goes through
TunnelManager, so there's exactly one place that tracks state and one
place that needs a lock. This is what makes "show status" and
"disconnect" reliable regardless of which underlying method was used.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from tunnel import ssh_tunnel, ssm_tunnel

log = logging.getLogger(__name__)

TunnelMethod = Literal["ssm", "ssh", "ssm-session"]


@dataclass
class ActiveTunnel:
    id: str
    method: TunnelMethod
    bastion_label: str
    target_host: str
    target_port: int
    local_port: int
    started_at: datetime
    handle: object  # subprocess.Popen for ssm, SshTunnelHandle for ssh
    exit_code: int | None = None
    exit_details: str = ""
    # Only set for ssm-session method (interactive shell, no port fwd)
    instance_id: str | None = None
    region: str | None = None


class TunnelManager:
    """Thread-safe registry of currently-open tunnels."""

    def __init__(self) -> None:
        self._tunnels: dict[str, ActiveTunnel] = {}
        self._recently_dead: list[ActiveTunnel] = []
        self._lock = threading.Lock()

    # -- SSM ---------------------------------------------------------

    def start_ssm_tunnel(
        self,
        profile_name: str,
        instance_id: str,
        bastion_label: str,
        target_host: str,
        target_port: int,
        local_port: int,
        region: str | None = None,
    ) -> str:
        process = ssm_tunnel.start(
            profile_name, instance_id, target_host, target_port, local_port, region
        )
        return self._register("ssm", bastion_label, target_host, target_port, local_port, process)

    # -- SSH fallback --------------------------------------------------

    def start_ssh_tunnel(
        self,
        bastion_host: str,
        bastion_port: int,
        username: str,
        private_key_path: str,
        bastion_label: str,
        target_host: str,
        target_port: int,
        local_port: int,
    ) -> str:
        handle = ssh_tunnel.start(
            bastion_host, bastion_port, username, private_key_path,
            target_host, target_port, local_port,
        )
        return self._register("ssh", bastion_label, target_host, target_port, local_port, handle)

    # -- SSM interactive session --------------------------------------

    def start_ssm_session(
        self,
        profile_name: str,
        instance_id: str,
        bastion_label: str,
        region: str | None = None,
    ) -> str:
        process = ssm_tunnel.start_session(profile_name, instance_id, region)
        tunnel_id = str(uuid.uuid4())
        with self._lock:
            self._tunnels[tunnel_id] = ActiveTunnel(
                id=tunnel_id,
                method="ssm-session",
                bastion_label=bastion_label,
                target_host=instance_id,
                target_port=0,
                local_port=0,
                started_at=datetime.now(),
                handle=process,
                instance_id=instance_id,
                region=region,
            )
        log.info("Registered SSM session %s on %s", tunnel_id, instance_id)
        return tunnel_id

    # -- Shared --------------------------------------------------------

    def _register(self, method, bastion_label, target_host, target_port, local_port, handle) -> str:
        tunnel_id = str(uuid.uuid4())
        with self._lock:
            self._tunnels[tunnel_id] = ActiveTunnel(
                id=tunnel_id,
                method=method,
                bastion_label=bastion_label,
                target_host=target_host,
                target_port=target_port,
                local_port=local_port,
                started_at=datetime.now(),
                handle=handle,
            )
        log.info("Registered %s tunnel %s (local port %s)", method, tunnel_id, local_port)
        return tunnel_id

    def stop_tunnel(self, tunnel_id: str) -> None:
        with self._lock:
            tunnel = self._tunnels.get(tunnel_id)
        if tunnel is None:
            log.warning("stop_tunnel called for unknown id %s", tunnel_id)
            return

        try:
            if tunnel.method == "ssm":
                ssm_tunnel.stop(tunnel.handle)
            elif tunnel.method == "ssm-session":
                ssm_tunnel.stop_session(tunnel.handle)
            else:
                ssh_tunnel.stop(tunnel.handle)
        except Exception:
            # Do not hide a tunnel that may still be running if its shutdown
            # failed. The user can retry disconnecting it.
            log.exception("Failed to stop tunnel %s", tunnel_id)
            raise
        with self._lock:
            self._tunnels.pop(tunnel_id, None)
        log.info("Tunnel %s stopped", tunnel_id)

    def stop_all(self) -> None:
        """Stop every open tunnel — used on app shutdown."""
        for tunnel_id in list(self._tunnels):
            self.stop_tunnel(tunnel_id)

    def list_tunnels(self) -> list[ActiveTunnel]:
        with self._lock:
            # Drop any whose underlying process died on its own (e.g. token
            # expired mid-session) so the status table stays accurate.
            dead = [
                tid for tid, t in self._tunnels.items()
                if t.method in ("ssm", "ssm-session") and t.handle.poll() is not None
            ]
            for tid in dead:
                tunnel = self._tunnels[tid]
                output = tunnel.handle.communicate()
                stdout, stderr = output if isinstance(output, tuple) and len(output) == 2 else ("", "")
                details = (stderr or stdout or "").strip()
                log.warning(
                    "Tunnel %s exited unexpectedly with code %s; removing from registry%s",
                    tid,
                    tunnel.handle.returncode,
                    f": {details}" if details else "",
                )
                tunnel.exit_code = tunnel.handle.returncode
                tunnel.exit_details = details
                del self._tunnels[tid]
                self._recently_dead.append(tunnel)
            return list(self._tunnels.values())

    def take_recently_dead(self) -> list[ActiveTunnel]:
        """Return tunnels that exited unexpectedly since the last check."""
        with self._lock:
            dead = self._recently_dead
            self._recently_dead = []
            return dead
