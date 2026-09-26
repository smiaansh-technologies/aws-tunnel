"""Manage saved named tunnel profiles."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable
from uuid import uuid4

from aws.bastions import Bastion
from aws.profiles import list_profiles
from config import Settings, TunnelProfile
from gui.clipboard import replace_from_clipboard
from tunnel.validation import TunnelValidationError, validate_host, validate_port


class TunnelProfilesDialog(tk.Toplevel):
    """Show and delete reusable tunnel profiles."""

    def __init__(
        self,
        parent: tk.Widget,
        settings: Settings,
        on_changed: Callable[[], None],
        on_open_tunnel: Callable[[TunnelProfile], None] | None = None,
        bastions: list[Bastion] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Tunnel Profiles")
        self.geometry("780x360")
        self.minsize(620, 300)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self._settings = settings
        self._on_changed = on_changed
        self._on_open_tunnel = on_open_tunnel
        self._bastions = bastions or []

        self._tree = ttk.Treeview(
            self,
            columns=("bastion", "target", "remote", "local", "aws_profile"),
            show="tree headings",
            height=8,
        )
        self._tree.heading("#0", text="Name")
        for column, label in (
            ("bastion", "Bastion"),
            ("target", "Target host"),
            ("remote", "Target port"),
            ("local", "Local port"),
            ("aws_profile", "AWS profile"),
        ):
            self._tree.heading(column, text=label)
        self._tree.column("#0", width=180, minwidth=130, stretch=False)
        self._tree.column("bastion", width=150, minwidth=120, stretch=False)
        self._tree.column("target", width=220, minwidth=160, stretch=False)
        self._tree.column("remote", width=90, minwidth=70, stretch=False)
        self._tree.column("local", width=80, minwidth=70, stretch=False)
        self._tree.column("aws_profile", width=120, minwidth=90, stretch=False)
        scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self._tree.xview)
        self._tree.configure(xscrollcommand=scrollbar.set)
        self._tree.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="nsew")
        scrollbar.grid(row=1, column=0, padx=10, sticky="ew")

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, padx=10, pady=10, sticky="e")
        ttk.Button(buttons, text="New", command=self._new_profile).pack(side="left", padx=4)
        self._edit_button = ttk.Button(buttons, text="Edit", command=self._edit_selected)
        self._edit_button.pack(side="left", padx=4)
        self._open_button = ttk.Button(buttons, text="Open Tunnel", command=self._open_selected)
        self._open_button.pack(side="left", padx=4)
        self._delete_button = ttk.Button(buttons, text="Delete", command=self._delete_selected)
        self._delete_button.pack(side="right", padx=4)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._populate()
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

    def _populate(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for profile in self._settings.tunnel_profiles:
            self._tree.insert(
                "",
                "end",
                iid=profile.profile_id,
                text=profile.name,
                values=(
                    profile.bastion_name or profile.bastion_instance_id,
                    profile.target_host,
                    profile.target_port,
                    profile.local_port,
                    profile.aws_profile,
                ),
            )
        self._edit_button.configure(
            state="normal" if self._settings.tunnel_profiles else "disabled"
        )
        state = "normal" if self._settings.tunnel_profiles else "disabled"
        self._open_button.configure(state=state)
        self._delete_button.configure(state=state)

    def _delete_selected(self) -> None:
        selection = self._tree.selection()
        if not selection:
            messagebox.showinfo("No selection", "Select a tunnel profile first.", parent=self)
            return
        profile_id = selection[0]
        profile = next(
            (item for item in self._settings.tunnel_profiles if item.profile_id == profile_id),
            None,
        )
        if profile is None:
            return
        if not messagebox.askyesno(
            "Delete tunnel profile",
            f"Delete tunnel profile '{profile.name}'?",
            parent=self,
        ):
            return
        self._settings.tunnel_profiles = [
            item for item in self._settings.tunnel_profiles if item.profile_id != profile_id
        ]
        self._settings.save()
        self._populate()
        self._on_changed()

    def _selected_profile(self) -> TunnelProfile | None:
        selection = self._tree.selection()
        if not selection:
            return None
        return next(
            (item for item in self._settings.tunnel_profiles if item.profile_id == selection[0]),
            None,
        )

    def _new_profile(self) -> None:
        TunnelProfileEditor(self, self._settings, None, self._save_profile, self._bastions)

    def _edit_selected(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo("No selection", "Select a tunnel profile first.", parent=self)
            return
        TunnelProfileEditor(self, self._settings, profile, self._save_profile, self._bastions)

    def _save_profile(self, profile: TunnelProfile) -> None:
        self._settings.tunnel_profiles = [
            item for item in self._settings.tunnel_profiles if item.profile_id != profile.profile_id
        ]
        self._settings.tunnel_profiles.append(profile)
        self._settings.save()
        self._populate()
        self._tree.selection_set(profile.profile_id)
        self._tree.focus(profile.profile_id)
        self._on_changed()

    def _open_selected(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo("No selection", "Select a tunnel profile first.", parent=self)
            return
        if self._on_open_tunnel is not None:
            self.destroy()
            self._on_open_tunnel(profile)


    class TunnelProfileSelectDialog(tk.Toplevel):
        """Select a saved tunnel profile to connect."""

        def __init__(
            self,
            parent: tk.Widget,
            settings: Settings,
            on_connect: Callable[[TunnelProfile], None],
            on_manage: Callable[[], None],
        ) -> None:
            super().__init__(parent)
            self.title("Connect Tunnel Profile")
            self.resizable(False, False)
            self.transient(parent)
            self.grab_set()
            self._settings = settings
            self._on_connect = on_connect
            self._on_manage = on_manage

            ttk.Label(self, text="Select a tunnel profile:").grid(
                row=0, column=0, padx=12, pady=(12, 4), sticky="w"
            )
            self._profile_list = tk.Listbox(self, height=8, width=48, exportselection=False)
            self._profile_list.grid(row=1, column=0, padx=12, pady=4)
            self._profile_list.bind("<Double-1>", lambda _event: self._connect())
            for profile in settings.tunnel_profiles:
                self._profile_list.insert(tk.END, f"{profile.name} - {profile.target_host}:{profile.target_port}")
            if settings.tunnel_profiles:
                self._profile_list.selection_set(0)

            buttons = ttk.Frame(self)
            buttons.grid(row=2, column=0, padx=12, pady=(4, 12), sticky="e")
            ttk.Button(buttons, text="Tunnel Profiles", command=self._manage).pack(side="left", padx=4)
            ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=4)
            ttk.Button(buttons, text="Connect", command=self._connect).pack(side="right", padx=4)
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

        def _connect(self) -> None:
            selection = self._profile_list.curselection()
            if not selection:
                messagebox.showinfo("No selection", "Select a tunnel profile first.", parent=self)
                return
            self.destroy()
            self._on_connect(self._settings.tunnel_profiles[selection[0]])

        def _manage(self) -> None:
            self.destroy()
            self._on_manage()


TunnelProfileSelectDialog = TunnelProfilesDialog.TunnelProfileSelectDialog


class TunnelProfileEditor(tk.Toplevel):
    """Create or edit a named tunnel profile."""

    _FIELDS = (
        ("name", "Name"),
        ("aws_profile", "AWS profile"),
        ("bastion_instance_id", "Bastion instance ID"),
        ("target_host", "Target host"),
        ("target_port", "Target port"),
        ("local_port", "Local port"),
        ("description", "Description"),
    )

    def __init__(
        self,
        parent: tk.Widget,
        settings: Settings,
        profile: TunnelProfile | None,
        on_saved: Callable[[TunnelProfile], None],
        bastions: list[Bastion],
    ) -> None:
        super().__init__(parent)
        self.title("Edit Tunnel Profile" if profile else "New Tunnel Profile")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._profile = profile
        self._on_saved = on_saved
        self._bastions = bastions
        self._entries: dict[str, tk.Widget] = {}

        container = ttk.Frame(self, padding=12)
        container.grid(row=0, column=0)
        for row, (key, label) in enumerate(self._FIELDS):
            ttk.Label(container, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=3)
            if key == "aws_profile":
                profile_names = [profile.name for profile in list_profiles()]
                if profile is not None and profile.aws_profile not in profile_names:
                    profile_names.append(profile.aws_profile)
                entry = ttk.Combobox(
                    container,
                    values=profile_names,
                    state="readonly",
                    width=33,
                )
            elif key == "bastion_instance_id":
                bastion_values = [
                    f"{bastion.name} ({bastion.instance_id})" for bastion in self._bastions
                ]
                if profile is not None and profile.bastion_instance_id not in {
                    bastion.instance_id for bastion in self._bastions
                }:
                    bastion_values.append(
                        f"{profile.bastion_name or profile.bastion_instance_id} "
                        f"({profile.bastion_instance_id})"
                    )
                entry = ttk.Combobox(
                    container,
                    values=bastion_values,
                    state="readonly",
                    width=33,
                )
            else:
                entry = ttk.Entry(container, width=36)
            entry.grid(row=row, column=1, padx=(8, 0), pady=3)
            self._entries[key] = entry
            if key == "target_host":
                entry.bind(
                    "<FocusIn>",
                    lambda _event, target_entry=entry: self._select_target_host(target_entry),
                )
                entry.bind(
                    "<Control-v>",
                    lambda event, target_entry=entry: replace_from_clipboard(target_entry, event),
                )
                entry.bind(
                    "<Control-V>",
                    lambda event, target_entry=entry: replace_from_clipboard(target_entry, event),
                )
            if profile is not None:
                value = str(getattr(profile, key))
                if key == "aws_profile":
                    entry.set(value)
                elif key == "bastion_instance_id":
                    bastion_label = next(
                        (
                            f"{item.name} ({item.instance_id})"
                            for item in self._bastions
                            if item.instance_id == value
                        ),
                        f"{profile.bastion_name or value} ({value})",
                    )
                    entry.set(bastion_label)
                else:
                    entry.insert(0, value)

        buttons = ttk.Frame(container)
        buttons.grid(row=len(self._FIELDS), column=0, columnspan=2, pady=(10, 0), sticky="e")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right", padx=4)
        self.after_idle(lambda: self._center_over_parent(parent))

    def _select_target_host(self, target_entry: tk.Widget) -> None:
        self.after_idle(lambda: target_entry.select_range(0, tk.END))

    def _center_over_parent(self, parent: tk.Widget) -> None:
        parent_window = parent.winfo_toplevel()
        parent_window.update_idletasks()
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = parent_window.winfo_rootx() + (parent_window.winfo_width() - width) // 2
        y = parent_window.winfo_rooty() + (parent_window.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    def _save(self) -> None:
        values = {key: self._entries[key].get().strip() for key, _ in self._FIELDS}
        if any(not values[key] for key in ("name", "aws_profile", "bastion_instance_id", "target_host")):
            messagebox.showerror("Missing fields", "Name, AWS profile, bastion ID, and target host are required.", parent=self)
            return
        if not values["target_port"].isdigit() or not values["local_port"].isdigit():
            messagebox.showerror("Invalid ports", "Target and local ports must be numeric.", parent=self)
            return
        try:
            target_host = validate_host(values["target_host"], field="target host")
            target_port = validate_port(int(values["target_port"]), field="target port")
            local_port = validate_port(int(values["local_port"]), field="local port")
        except TunnelValidationError as exc:
            messagebox.showerror("Invalid tunnel", str(exc), parent=self)
            return
        selected_bastion_id = values["bastion_instance_id"]
        selected_bastion_name = ""
        for bastion in self._bastions:
            option = f"{bastion.name} ({bastion.instance_id})"
            if values["bastion_instance_id"] == option:
                selected_bastion_id = bastion.instance_id
                selected_bastion_name = bastion.name
                break
        if not selected_bastion_name and self._profile is not None:
            selected_bastion_name = self._profile.bastion_name

        profile = TunnelProfile(
            profile_id=self._profile.profile_id if self._profile else uuid4().hex,
            name=values["name"],
            aws_profile=values["aws_profile"],
            bastion_instance_id=selected_bastion_id,
            bastion_name=selected_bastion_name,
            target_host=target_host,
            target_port=target_port,
            local_port=local_port,
            description=values["description"],
        )
        self._on_saved(profile)
        self.destroy()
