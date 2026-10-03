"""
The main window. Three sections stacked vertically:

  1. Profiles   — list, refresh, create, delete, login
  2. Bastions   — discover (for the selected/logged-in profile), open tunnel
  3. Tunnels    — live status, disconnect

All AWS calls run on background threads so the UI never freezes; results
are marshalled back to the main thread with `root.after(0, ...)`, which
is the standard safe way to touch Tkinter widgets from another thread.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import messagebox, ttk

from aws.bastions import BastionDiscoveryError, discover_bastions
from aws.profiles import AWSProfile, delete_profile, is_logged_in, list_profiles
from aws.sso_login import SsoLoginError, login, logout
from config import Settings, TunnelProfile
from gui.bastion_dialog import NewTunnelDialog
from gui.profile_dialog import ProfileDialog
from gui.tunnel_profiles_dialog import TunnelProfilesDialog
from tunnel.manager import TunnelManager

log = logging.getLogger(__name__)

# Dialog title used whenever an action needs a row picked first.
NO_SELECTION = "No selection"

_GREEN = "#1a7f37"
_RED = "#c62828"


class MainWindow(ttk.Frame):
    def __init__(self, root: tk.Tk) -> None:
        super().__init__(root, padding=10)
        self.root = root
        self.settings = Settings.load()
        self.tunnel_manager = TunnelManager()

        self._profiles: list[AWSProfile] = []
        self._bastions_by_id: dict[str, object] = {}
        self._bastions_loaded_for: set[str] = set()
        self._bastion_discovery_in_progress: set[str] = set()
        self._tunnel_profiles_by_id: dict[str, TunnelProfile] = {}
        self._active_tunnels_by_id: dict[str, object] = {}
        self._reconnect_in_progress: set[str] = set()
        self._profile_refresh_job: str | None = None
        self._window_position_job: str | None = None
        self._profile_wizard_open = False

        self.pack(fill="both", expand=True)
        self._build_profiles_section()
        self._build_bastions_section()
        self._build_tunnels_section()

        self.refresh_profiles()
        self._schedule_profile_refresh()
        self._schedule_tunnel_refresh()
        self._schedule_uptime_refresh()

    def restore_window_position(self) -> None:
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = self.settings.window_x
        y = self.settings.window_y
        # Center on screen unless the user previously moved the window to a
        # position that is still (mostly) visible on the current display.
        if x is None or y is None or not (-50 <= x < screen_w - 100 and -50 <= y < screen_h - 100):
            x = max((screen_w - width) // 2, 0)
            y = max((screen_h - height) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.bind("<Configure>", self._on_window_configure)

    def _on_window_configure(self, event: tk.Event) -> None:
        if event.widget is self.root and self.root.state() == "normal":
            if self._window_position_job is not None:
                self.root.after_cancel(self._window_position_job)
            self._window_position_job = self.root.after(300, self._save_window_position)

    def _save_window_position(self) -> None:
        self._window_position_job = None
        self.settings.window_x = self.root.winfo_x()
        self.settings.window_y = self.root.winfo_y()
        self.settings.save()

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    @staticmethod
    def _autofit_tree_columns(tree: ttk.Treeview, columns: tuple[str, ...]) -> None:
        body_font = tkfont.nametofont("TkDefaultFont")
        heading_font = tkfont.nametofont("TkHeadingFont")
        for column in columns:
            heading = tree.heading(column).get("text", "")
            width = heading_font.measure(str(heading))
            for item_id in tree.get_children(""):
                value = tree.item(item_id, "text") if column == "#0" else tree.set(item_id, column)
                width = max(width, body_font.measure(str(value)))
            tree.column(
                column,
                width=max(80, min(width + 24, 420)),
                stretch=False,
                anchor="w",
            )
            tree.heading(column, anchor="w")

    def _build_profiles_section(self) -> None:
        frame = ttk.LabelFrame(self, text="AWS Profiles", padding=8)
        frame.pack(fill="x", pady=(0, 8))

        columns = ("account", "role", "status", "region")
        self.profile_tree = ttk.Treeview(frame, columns=columns, show="tree headings", height=5)
        self.profile_tree.heading("#0", text="Profile")
        self.profile_tree.heading("account", text="Account")
        self.profile_tree.heading("role", text="Role")
        self.profile_tree.heading("status", text="Status")
        self.profile_tree.heading("region", text="Region")
        self.profile_tree.column("#0", width=105, minwidth=90, stretch=False)
        self.profile_tree.column("account", width=115, minwidth=100, stretch=False)
        self.profile_tree.column("role", width=190, minwidth=150, stretch=False)
        self.profile_tree.column("status", width=105, minwidth=90, stretch=False)
        self.profile_tree.column("region", width=90, minwidth=80, stretch=False)
        self.profile_tree.tag_configure("connected", foreground=_GREEN)
        self.profile_tree.tag_configure("disconnected", foreground=_RED)
        profile_scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=self.profile_tree.xview)
        self.profile_tree.configure(xscrollcommand=profile_scrollbar.set)
        self.profile_tree.bind("<<TreeviewSelect>>", self._on_profile_tree_select)
        self.profile_tree.bind("<Double-1>", self._toggle_selected_profile)
        self.profile_tree.pack(fill="x", side="top")
        profile_scrollbar.pack(fill="x", side="top")

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x", pady=(6, 0))
        ttk.Button(button_row, text="Refresh", command=self.refresh_profiles).pack(side="left")
        ttk.Button(button_row, text="New Profile", command=self._open_new_profile_dialog).pack(
            side="left", padx=4
        )
        ttk.Button(button_row, text="Delete Profile", command=self._delete_selected_profile).pack(
            side="left", padx=4
        )
        self.profile_action_button = ttk.Button(
            button_row, text="Login", command=self._login_selected_profile
        )
        self.profile_action_button.pack(
            side="left", padx=4
        )

    def _on_profile_tree_select(self, _event: tk.Event | None = None) -> None:
        """Single dispatcher for profile selection — updates button and bastions."""
        self._update_profile_action_button()
        self._on_profile_select()

    def refresh_profiles(self) -> None:
        selected_name = self.profile_tree.selection()[0] if self.profile_tree.selection() else None
        self._profiles = list_profiles()
        self.profile_tree.delete(*self.profile_tree.get_children())
        for profile in self._profiles:
            logged_in = is_logged_in(profile)
            status = "Connected" if logged_in else "Disconnected"
            self.profile_tree.insert(
                "", "end", iid=profile.name, text=profile.name,
                values=(profile.sso_account_id, profile.sso_role_name, status, profile.region),
                tags=("connected" if logged_in else "disconnected",),
            )
        self._autofit_tree_columns(self.profile_tree, ("#0", "account", "role", "status", "region"))
        profile_names = {profile.name for profile in self._profiles}
        if selected_name in profile_names:
            self.profile_tree.selection_set(selected_name)
            self.profile_tree.focus(selected_name)
        elif self._profiles:
            first_profile = self._profiles[0].name
            self.profile_tree.selection_set(first_profile)
            self.profile_tree.focus(first_profile)
        self._update_profile_action_button()
        self._update_tunnel_controls()
        self._clear_bastions()
        self._auto_discover_selected_profile()
        if not self._profiles and not self._profile_wizard_open:
            self._profile_wizard_open = True
            self.root.after_idle(self._open_new_profile_dialog)
        elif self._profiles:
            self._profile_wizard_open = False

    def _update_tunnel_controls(self) -> None:
        connected = any(is_logged_in(profile) for profile in self._profiles)
        if connected:
            if not self._bastion_frame.winfo_ismapped():
                self._bastion_frame.pack(fill="x", pady=(0, 8))
            if not self._tunnel_frame.winfo_ismapped():
                self._tunnel_frame.pack(fill="x")
        else:
            self._bastion_frame.pack_forget()
            self._tunnel_frame.pack_forget()
            self._clear_bastions()

    def _schedule_profile_refresh(self) -> None:
        if self._profile_refresh_job is not None:
            self.root.after_cancel(self._profile_refresh_job)
        interval_ms = self.settings.profile_refresh_minutes * 60 * 1000
        self._profile_refresh_job = self.root.after(interval_ms, self._refresh_profile_statuses)

    def _refresh_profile_statuses(self) -> None:
        self._profile_refresh_job = None
        self.refresh_profiles()
        self._schedule_profile_refresh()

    def _update_profile_action_button(self, _event: tk.Event | None = None) -> None:
        profile = self._selected_profile()
        if profile is not None and is_logged_in(profile):
            self.profile_action_button.configure(text="Logout", command=self._logout_selected_profile)
        else:
            self.profile_action_button.configure(text="Login", command=self._login_selected_profile)

    def _on_profile_select(self, _event: tk.Event | None = None) -> None:
        """When user selects a different profile, clear and rediscover bastions."""
        self._bastions_loaded_for.clear()
        self._clear_bastions()
        self._auto_discover_selected_profile()

    def _selected_profile(self) -> AWSProfile | None:
        selection = self.profile_tree.selection()
        if not selection:
            return None
        name = selection[0]
        return next((p for p in self._profiles if p.name == name), None)

    def _open_new_profile_dialog(self) -> None:
        ProfileDialog(self.root, on_created=self.refresh_profiles)

    def _delete_selected_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo(NO_SELECTION, "Select a profile to delete first.")
            return
        if not messagebox.askyesno("Confirm delete", f"Delete profile '{profile.name}'?"):
            return
        delete_profile(profile.name)
        self.refresh_profiles()

    def _login_selected_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo(NO_SELECTION, "Select a profile to log in with first.")
            return

        self._run_profile_action(profile, login)

    def _logout_selected_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo(NO_SELECTION, "Select a profile to log out with first.")
            return
        if not messagebox.askyesno("Disconnect profile", f"Disconnect profile '{profile.name}'?"):
            return
        self._run_profile_action(profile, logout)

    def _toggle_selected_profile(self, _event: tk.Event) -> None:
        profile = self._selected_profile()
        if profile is None:
            return

        if is_logged_in(profile):
            self._logout_selected_profile()
        else:
            self._run_profile_action(profile, login)

    def _run_profile_action(self, profile: AWSProfile, action) -> None:
        action_name = "Login" if action is login else "Logout"

        def worker() -> None:
            try:
                action(profile.name)
                def update_after_action() -> None:
                    self.refresh_profiles()
                    if action is login:
                        self._discover_bastions_for(profile, automatic=True)
                    else:
                        self._bastions_loaded_for.discard(profile.name)
                        self._clear_bastions()

                self.root.after(0, update_after_action)
            except SsoLoginError as exc:
                log.exception("%s failed", action_name)
                error_message = str(exc)
                self.root.after(
                    0,
                    lambda: messagebox.showerror(f"{action_name} failed", error_message),
                )

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Bastions
    # ------------------------------------------------------------------

    def _build_bastions_section(self) -> None:
        self._bastion_frame = ttk.LabelFrame(self, text="Bastion Hosts", padding=8)

        columns = ("instance_id", "private_ip", "state", "ssm")
        self.bastion_tree = ttk.Treeview(self._bastion_frame, columns=columns, show="tree headings", height=5)
        self.bastion_tree.heading("#0", text="Name")
        self.bastion_tree.heading("instance_id", text="Instance ID")
        self.bastion_tree.heading("private_ip", text="Private IP")
        self.bastion_tree.heading("state", text="State")
        self.bastion_tree.heading("ssm", text="SSM Agent")
        self.bastion_tree.column("#0", width=150, minwidth=100, stretch=False)
        self.bastion_tree.column("instance_id", width=140, minwidth=120, stretch=False)
        self.bastion_tree.column("private_ip", width=110, minwidth=90, stretch=False)
        self.bastion_tree.column("state", width=90, minwidth=70, stretch=False)
        self.bastion_tree.column("ssm", width=100, minwidth=80, stretch=False)
        self.bastion_tree.tag_configure("available", foreground=_GREEN)
        self.bastion_tree.tag_configure("unavailable", foreground=_RED)
        bastion_scrollbar = ttk.Scrollbar(self._bastion_frame, orient="horizontal", command=self.bastion_tree.xview)
        self.bastion_tree.configure(xscrollcommand=bastion_scrollbar.set)
        self.bastion_tree.pack(fill="x", side="top")
        bastion_scrollbar.pack(fill="x", side="top")

        # Right-click context menu
        self._bastion_menu = tk.Menu(self.bastion_tree, tearoff=0)
        self._bastion_menu.add_command(label="Connect Tunnel", command=self._open_tunnel_from_bastion)
        self._bastion_menu.add_command(label="Start Terminal", command=self._start_terminal_from_bastion)
        self._bastion_menu.add_separator()
        self._bastion_menu.add_command(label="Refresh Bastions", command=self._discover_bastions)
        self.bastion_tree.bind("<Button-3>", self._show_bastion_menu)

    def _discover_bastions(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo(NO_SELECTION, "Select a logged-in profile first.")
            return

        self._discover_bastions_for(profile)

    def _show_bastion_menu(self, event: tk.Event) -> None:
        """Show context menu on right-click of a bastion row."""
        item_id = self.bastion_tree.identify_row(event.y)
        if item_id:
            self.bastion_tree.selection_set(item_id)
            self._bastion_menu.post(event.x_root, event.y_root)

    def _open_tunnel_from_bastion(self) -> None:
        """Open tunnel profile selector for the selected bastion."""
        self._open_tunnel_profile_selector()

    def _start_terminal_from_bastion(self) -> None:
        """Start an interactive SSM shell session on the selected bastion."""
        profile = self._selected_profile()
        selection = self.bastion_tree.selection()
        if profile is None or not selection:
            messagebox.showinfo(NO_SELECTION, "Select a profile and a bastion first.")
            return

        bastion = self._bastions_by_id.get(selection[0])
        if bastion is None:
            return

        if not bastion.ssm_online:
            messagebox.showwarning(
                "SSM Agent offline",
                f"The SSM Agent on '{bastion.name}' is not online.\n"
                "Terminal access requires the SSM Agent to be running.",
            )
            return

        def worker() -> None:
            try:
                if not is_logged_in(profile):
                    login(profile.name)
                tunnel_id = self.tunnel_manager.start_ssm_session(
                    profile_name=profile.name,
                    instance_id=bastion.instance_id,
                    bastion_label=bastion.name,
                    region=profile.region,
                )
                log.info("Started SSM session %s on %s", tunnel_id, bastion.name)
                self.root.after(0, self._refresh_tunnels)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                log.exception("Failed to start SSM session")
                error_message = str(exc)
                self.root.after(
                    0,
                    lambda: messagebox.showerror("Session failed", error_message),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _auto_discover_selected_profile(self) -> None:
        profile = self._selected_profile()
        if profile is not None:
            self._discover_bastions_for(profile, automatic=True)

    def _discover_bastions_for(self, profile: AWSProfile, automatic: bool = False) -> None:
        if profile.name in self._bastion_discovery_in_progress:
            return
        if automatic and profile.name in self._bastions_loaded_for:
            return
        self._bastion_discovery_in_progress.add(profile.name)

        def worker() -> None:
            try:
                bastions = discover_bastions(
                    profile.name, self.settings.bastion_tag_key, self.settings.bastion_tag_value
                )
                self.root.after(0, lambda: self._finish_bastion_discovery(profile, bastions))
            except BastionDiscoveryError as exc:
                self._bastion_discovery_in_progress.discard(profile.name)
                log.exception("Bastion discovery failed")
                error_message = str(exc)

                def report_failure() -> None:
                    if automatic:
                        # Auto-discovery (profile select / periodic refresh):
                        # don't spam error dialogs.  Mark as loaded so the
                        # periodic refresh doesn't retry and re-fail forever;
                        # the user can retry manually via the context menu.
                        self._bastions_loaded_for.add(profile.name)
                        log.warning(
                            "Automatic bastion discovery failed for '%s': %s",
                            profile.name, error_message,
                        )
                    else:
                        messagebox.showerror("Discovery failed", error_message)

                self.root.after(0, report_failure)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_bastion_discovery(self, profile: AWSProfile, bastions: list) -> None:
        self._bastion_discovery_in_progress.discard(profile.name)
        self._bastions_loaded_for.add(profile.name)
        if self._selected_profile() is not None and self._selected_profile().name != profile.name:
            return
        self._populate_bastions(bastions)

    def _clear_bastions(self) -> None:
        self.bastion_tree.delete(*self.bastion_tree.get_children())
        self._bastions_by_id = {}

    def _populate_bastions(self, bastions: list) -> None:
        self.bastion_tree.delete(*self.bastion_tree.get_children())
        self._bastions_by_id = {b.instance_id: b for b in bastions}
        for bastion in bastions:
            self.bastion_tree.insert(
                "", "end", iid=bastion.instance_id, text=bastion.name,
                values=(
                    bastion.instance_id,
                    bastion.private_ip,
                    bastion.state,
                    "Online" if bastion.ssm_online else "Offline",
                ),
                tags=("available" if bastion.state == "running" and bastion.ssm_online else "unavailable",),
            )
        self._autofit_tree_columns(
            self.bastion_tree, ("#0", "instance_id", "private_ip", "state", "ssm")
        )

    def _open_new_tunnel_dialog(self) -> None:
        profile = self._selected_profile()
        selection = self.bastion_tree.selection()
        if profile is None or not selection:
            messagebox.showinfo(NO_SELECTION, "Select a profile and a bastion first.")
            return

        bastion = self._bastions_by_id.get(selection[0])
        if bastion is None:
            return

        saved_profile = next(
            (
                p for p in reversed(self.settings.tunnel_profiles)
                if p.bastion_instance_id == bastion.instance_id
                and p.aws_profile in ("", profile.name)
            ),
            None,
        )
        if saved_profile is None:
            saved_profile = next(
                (p for p in reversed(self.settings.tunnel_profiles)
                 if p.bastion_instance_id == bastion.instance_id),
                None,
            )

        self._show_new_tunnel_dialog(profile, bastion, saved_profile)

    def _open_tunnel_profile_selector(self) -> None:
        if not self.settings.tunnel_profiles:
            messagebox.showinfo(
                "No tunnel profiles",
                "Create a tunnel profile from File > Tunnel Profiles first.",
            )
            return
        self._open_tunnel_profiles()

    def _show_new_tunnel_dialog(
        self,
        profile: AWSProfile,
        bastion,
        saved_profile: TunnelProfile | None,
    ) -> None:

        NewTunnelDialog(
            self.root, profile.name, bastion, self.tunnel_manager,
            on_started=self._refresh_tunnels,
            saved_profile=saved_profile,
            on_profile_saved=self._save_bastion_profile,
        )

    def _open_tunnel_profiles(self) -> None:
        TunnelProfilesDialog(
            self.root,
            self.settings,
            self.refresh_profiles,
            on_open_tunnel=self._open_saved_tunnel_profile,
            bastions=list(self._bastions_by_id.values()),
        )

    def _open_saved_tunnel_profile(self, saved_profile: TunnelProfile) -> None:
        profile = next((item for item in self._profiles if item.name == saved_profile.aws_profile), None)
        if profile is None:
            connected_profiles = [item for item in self._profiles if is_logged_in(item)]
            if len(connected_profiles) == 1:
                profile = connected_profiles[0]

        bastion = self._bastions_by_id.get(saved_profile.bastion_instance_id)
        if bastion is None and len(self._bastions_by_id) == 1:
            bastion = next(iter(self._bastions_by_id.values()))
        if profile is None or bastion is None:
            messagebox.showinfo(
                "Bastion unavailable",
                "Connect an AWS profile and refresh bastions before opening this tunnel.",
            )
            return
        self._connect_saved_tunnel_profile(profile, bastion, saved_profile)

    def _connect_saved_tunnel_profile(
        self,
        profile: AWSProfile,
        bastion,
        saved_profile: TunnelProfile,
    ) -> None:
        saved_profile.aws_profile = profile.name
        saved_profile.bastion_instance_id = bastion.instance_id
        saved_profile.bastion_name = bastion.name
        self._save_bastion_profile(saved_profile)

        def worker() -> None:
            try:
                if not is_logged_in(profile):
                    login(profile.name)
                tunnel_id = self.tunnel_manager.start_ssm_tunnel(
                    profile_name=profile.name,
                    instance_id=bastion.instance_id,
                    bastion_label=bastion.name,
                    target_host=saved_profile.target_host,
                    target_port=saved_profile.target_port,
                    local_port=saved_profile.local_port,
                    region=profile.region,
                    profile_label=saved_profile.name,
                )
                self._tunnel_profiles_by_id[tunnel_id] = saved_profile
                self.root.after(0, self._refresh_tunnels)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                log.exception("Failed to start saved tunnel profile")
                error_message = str(exc)
                self.root.after(
                    0,
                    lambda: messagebox.showerror("Connection failed", error_message),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _save_bastion_profile(self, profile: TunnelProfile) -> None:
        self.settings.tunnel_profiles = [
            saved for saved in self.settings.tunnel_profiles
            if saved.profile_id != profile.profile_id
        ]
        self.settings.tunnel_profiles.append(profile)
        self.settings.save()

    # ------------------------------------------------------------------
    # Tunnels
    # ------------------------------------------------------------------

    def _build_tunnels_section(self) -> None:
        self._tunnel_frame = ttk.LabelFrame(self, text="Active Tunnels", padding=8)

        columns = ("profile", "target", "local_port", "method", "bastion", "uptime")
        self.tunnel_tree = ttk.Treeview(self._tunnel_frame, columns=columns, show="headings", height=5)
        for col, label in zip(
            columns,
            ("Tunnel Profile", "Target", "Local Port", "Method", "Bastion Host", "Uptime"),
        ):
            self.tunnel_tree.heading(col, text=label)
        tunnel_scrollbar = ttk.Scrollbar(self._tunnel_frame, orient="horizontal", command=self.tunnel_tree.xview)
        self.tunnel_tree.configure(xscrollcommand=tunnel_scrollbar.set)
        self.tunnel_tree.bind("<<TreeviewSelect>>", self._update_disconnect_button)
        self.tunnel_tree.bind("<Double-1>", self._update_disconnect_button)
        self.tunnel_tree.pack(fill="x", side="top")
        tunnel_scrollbar.pack(fill="x", side="top")

        button_row = ttk.Frame(self._tunnel_frame)
        button_row.pack(fill="x", pady=(6, 0))
        self.disconnect_button = ttk.Button(
            button_row, text="Disconnect", command=self._disconnect_selected_tunnel
        )
        self.disconnect_button.pack(side="left")
        ttk.Label(button_row, text="Select an active tunnel to disconnect it.").pack(
            side="left"
        )
        self._update_disconnect_button()

    def _update_disconnect_button(self, _event: tk.Event | None = None) -> None:
        state = "normal" if self.tunnel_tree.selection() else "disabled"
        self.disconnect_button.configure(state=state)

    def _refresh_tunnels(self) -> None:
        selected_id = self.tunnel_tree.selection()[0] if self.tunnel_tree.selection() else None
        tunnels = self.tunnel_manager.list_tunnels()
        self._active_tunnels_by_id = {tunnel.id: tunnel for tunnel in tunnels}
        for dead_tunnel in self.tunnel_manager.take_recently_dead():
            saved_profile = self._tunnel_profiles_by_id.pop(dead_tunnel.id, None)
            if saved_profile is not None:
                self._auto_reconnect(saved_profile, dead_tunnel)
        self.tunnel_tree.delete(*self.tunnel_tree.get_children())
        for tunnel in tunnels:
            uptime = str(datetime.now() - tunnel.started_at).split(".")[0]
            if tunnel.method == "ssm-session":
                target = tunnel.instance_id or tunnel.target_host
                local_port = "N/A"
            else:
                target = f"{tunnel.target_host}:{tunnel.target_port}"
                local_port = tunnel.local_port
            self.tunnel_tree.insert(
                "", "end", iid=tunnel.id,
                values=(
                    tunnel.profile_label or "—",
                    target,
                    local_port,
                    tunnel.method.upper().replace("SSM-SESSION", "SSM SESSION"),
                    tunnel.bastion_label,
                    uptime,
                ),
            )
        self._autofit_tree_columns(
            self.tunnel_tree,
            ("profile", "target", "local_port", "method", "bastion", "uptime"),
        )
        if selected_id in {tunnel.id for tunnel in tunnels}:
            self.tunnel_tree.selection_set(selected_id)
            self.tunnel_tree.focus(selected_id)
        self._update_disconnect_button()

    def _schedule_uptime_refresh(self) -> None:
        self._refresh_tunnel_uptimes()
        self.root.after(1000, self._schedule_uptime_refresh)

    def _refresh_tunnel_uptimes(self) -> None:
        for tunnel_id, tunnel in self._active_tunnels_by_id.items():
            if tunnel_id not in self.tunnel_tree.get_children(""):
                continue
            values = list(self.tunnel_tree.item(tunnel_id, "values"))
            if len(values) >= 6:
                values[5] = str(datetime.now() - tunnel.started_at).split(".")[0]
                self.tunnel_tree.item(tunnel_id, values=values)

    def _auto_reconnect(self, saved_profile: TunnelProfile, dead_tunnel) -> None:
        if saved_profile.profile_id in self._reconnect_in_progress:
            return
        uptime = datetime.now() - dead_tunnel.started_at
        details = dead_tunnel.exit_details.lower()
        permanent_errors = (
            "accessdeniedexception",
            "not authorized",
            "no such host",
            "invalidinstanceid",
            "targetnotconnected",
            "targetnotfound",
            "parameter validation",
        )
        if any(error in details for error in permanent_errors) or uptime.total_seconds() < 15:
            log.warning(
                "Not reconnecting tunnel profile '%s': startup/session failure after %.1fs%s",
                saved_profile.name,
                uptime.total_seconds(),
                f" ({dead_tunnel.exit_details.strip()})" if dead_tunnel.exit_details else "",
            )
            return
        profile = next(
            (item for item in self._profiles if item.name == saved_profile.aws_profile),
            None,
        )
        bastion = self._bastions_by_id.get(saved_profile.bastion_instance_id)
        if profile is None or bastion is None or not is_logged_in(profile):
            return

        self._reconnect_in_progress.add(saved_profile.profile_id)

        def worker() -> None:
            try:
                tunnel_id = self.tunnel_manager.start_ssm_tunnel(
                    profile_name=profile.name,
                    instance_id=bastion.instance_id,
                    bastion_label=bastion.name,
                    target_host=saved_profile.target_host,
                    target_port=saved_profile.target_port,
                    local_port=saved_profile.local_port,
                    region=profile.region,
                    profile_label=saved_profile.name,
                )
                self._tunnel_profiles_by_id[tunnel_id] = saved_profile
                log.info("Automatically reconnected tunnel profile '%s'", saved_profile.name)
            except Exception:
                log.exception("Automatic reconnect failed for '%s'", saved_profile.name)
            finally:
                self._reconnect_in_progress.discard(saved_profile.profile_id)

        threading.Thread(target=worker, daemon=True).start()

    def _disconnect_selected_tunnel(self) -> None:
        selection = self.tunnel_tree.selection()
        if not selection:
            messagebox.showinfo(NO_SELECTION, "Select a tunnel to disconnect first.")
            return
        tunnel_id = selection[0]
        self.tunnel_manager.stop_tunnel(tunnel_id)
        self._tunnel_profiles_by_id.pop(tunnel_id, None)
        self._refresh_tunnels()

    def _schedule_tunnel_refresh(self) -> None:
        """Keep the uptime column and 'died on its own' cleanup live."""
        self._refresh_tunnels()
        self.root.after(5000, self._schedule_tunnel_refresh)

    def shutdown(self) -> None:
        """Called on app exit — make sure no tunnel is left running."""
        if self._window_position_job is not None:
            self.root.after_cancel(self._window_position_job)
        self._save_window_position()
        self.tunnel_manager.stop_all()
