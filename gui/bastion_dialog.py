"""Dialog to configure and start a tunnel through a chosen bastion."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable
from uuid import uuid4

from aws.bastions import Bastion
from config import TunnelProfile
from gui.clipboard import replace_from_clipboard
from tunnel.manager import TunnelManager
from tunnel.validation import TunnelValidationError, validate_host, validate_port

log = logging.getLogger(__name__)


class NewTunnelDialog(tk.Toplevel):
    """Collects target host/port and local port, then starts an SSM tunnel."""

    def __init__(
        self,
        parent: tk.Widget,
        profile_name: str,
        bastion: Bastion,
        tunnel_manager: TunnelManager,
        on_started: Callable[[], None],
        saved_profile: TunnelProfile | None = None,
        on_profile_saved: Callable[[TunnelProfile], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title(f"New Tunnel via {bastion.name}")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._profile_name = profile_name
        self._bastion = bastion
        self._tunnel_manager = tunnel_manager
        self._on_started = on_started
        self._saved_profile = saved_profile
        self._on_profile_saved = on_profile_saved

        self._build_form()
        self.after_idle(lambda: self._center_over_parent(parent))

    def _center_over_parent(self, parent: tk.Widget) -> None:
        parent_window = parent.winfo_toplevel()
        parent_window.update_idletasks()
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = parent_window.winfo_rootx() + (parent_window.winfo_width() - width) // 2
        y = parent_window.winfo_rooty() + (parent_window.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    def _build_form(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.grid(row=0, column=0)

        if not self._bastion.ssm_online:
            warning = ttk.Label(
                container,
                text="Warning: SSM Agent is not reporting online for this instance.",
                foreground="red",
            )
            warning.grid(row=0, column=0, columnspan=2, pady=(0, 8))

        ttk.Label(container, text="Target host (reachable from bastion):").grid(
            row=1, column=0, sticky="w", pady=4
        )
        self._target_host = ttk.Entry(container, width=32)
        self._target_host.grid(row=1, column=1, pady=4, padx=(8, 0))
        self._target_host.bind("<FocusIn>", self._select_target_host)
        self._target_host.bind("<Control-v>", lambda event: replace_from_clipboard(self._target_host, event))
        self._target_host.bind("<Control-V>", lambda event: replace_from_clipboard(self._target_host, event))

        ttk.Label(container, text="Target port:").grid(row=2, column=0, sticky="w", pady=4)
        self._target_port = ttk.Entry(container, width=10)
        self._target_port.grid(row=2, column=1, sticky="w", pady=4, padx=(8, 0))

        ttk.Label(container, text="Local port:").grid(row=3, column=0, sticky="w", pady=4)
        self._local_port = ttk.Entry(container, width=10)
        self._local_port.grid(row=3, column=1, sticky="w", pady=4, padx=(8, 0))

        ttk.Label(container, text="Tunnel profile name:").grid(row=4, column=0, sticky="w", pady=4)
        self._profile_name_entry = ttk.Entry(container, width=32)
        self._profile_name_entry.grid(row=4, column=1, pady=4, padx=(8, 0))

        if self._saved_profile is not None:
            self._profile_name_entry.insert(0, self._saved_profile.name)
            self._target_host.insert(0, self._saved_profile.target_host)
            self._target_port.insert(0, str(self._saved_profile.target_port))
            self._local_port.insert(0, str(self._saved_profile.local_port))

        button_frame = ttk.Frame(container)
        button_frame.grid(row=5, column=0, columnspan=2, pady=(12, 0), sticky="e")
        ttk.Button(button_frame, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(button_frame, text="Connect", command=self._submit).pack(side="right", padx=4)

    def _select_target_host(self, _event: tk.Event) -> None:
        self.after_idle(lambda: self._target_host.select_range(0, tk.END))

    def _submit(self) -> None:
        target_host = self._target_host.get().strip()
        profile_name = self._profile_name_entry.get().strip()
        target_port_raw = self._target_port.get().strip()
        local_port_raw = self._local_port.get().strip()

        if not profile_name or not target_host or not target_port_raw.isdigit() or not local_port_raw.isdigit():
            messagebox.showerror(
                "Invalid input",
                "Please provide a profile name, valid host, and numeric ports.",
            )
            return

        try:
            target_host = validate_host(target_host, field="target host")
            target_port = validate_port(int(target_port_raw), field="target port")
            local_port = validate_port(int(local_port_raw), field="local port")
        except TunnelValidationError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self)
            return

        try:
            profile = TunnelProfile(
                profile_id=self._saved_profile.profile_id if self._saved_profile else uuid4().hex,
                name=profile_name,
                aws_profile=self._profile_name,
                bastion_instance_id=self._bastion.instance_id,
                bastion_name=self._bastion.name,
                target_host=target_host,
                target_port=target_port,
                local_port=local_port,
            )
            if self._on_profile_saved is not None:
                self._on_profile_saved(profile)
            self._tunnel_manager.start_ssm_tunnel(
                profile_name=self._profile_name,
                instance_id=self._bastion.instance_id,
                bastion_label=self._bastion.name,
                target_host=target_host,
                target_port=profile.target_port,
                local_port=profile.local_port,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("Failed to start tunnel")
            messagebox.showerror("Error", f"Could not start tunnel: {exc}")
            return

        self._on_started()
        self.destroy()
