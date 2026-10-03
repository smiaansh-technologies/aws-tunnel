"""
The app's modal dialogs: Preferences, About, and Update Available.

These were previously nested closures inside ``main()``. They live here so
the entry point stays a thin wiring layer instead of a 300-line function.

Every callback here touches Tk, so all of it must be called from the main
thread (the update service marshals results back with ``root.after(0, ...)``).
"""

from __future__ import annotations

import logging
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Callable

import update_checker
from config import APP_VERSION, GITHUB_REPO
from gui.help_docs import open_user_guide

if TYPE_CHECKING:
    from gui.main_window import MainWindow

log = logging.getLogger(__name__)

# Shared between the "not configured" notice, the failure warning and the
# "already up to date" confirmation.
UPDATE_DIALOG_TITLE = "Check for Updates"

# Label -> stored interval value, in the order the dropdown shows them.
UPDATE_INTERVAL_CHOICES = {
    "Off": "off",
    "Daily": "daily",
    "Weekly": "weekly",
    "Biweekly": "biweekly",
    "Monthly": "monthly",
}
DEFAULT_UPDATE_INTERVAL = "weekly"
REFRESH_CHOICES = ("1", "5", "10")


def center_on_parent(root: tk.Misc, dialog: tk.Misc) -> None:
    """Centre ``dialog`` over ``root`` once both have been laid out."""
    root.update_idletasks()
    dialog.update_idletasks()
    x = root.winfo_x() + (root.winfo_width() - dialog.winfo_width()) // 2
    y = root.winfo_y() + (root.winfo_height() - dialog.winfo_height()) // 2
    dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def _modal(root: tk.Misc, title: str) -> tk.Toplevel:
    dialog = tk.Toplevel(root)
    dialog.title(title)
    dialog.resizable(False, False)
    dialog.transient(root)
    return dialog


def show_preferences(
    root: tk.Misc,
    window: "MainWindow",
    on_saved: Callable[[], None] | None = None,
) -> None:
    """Edit the persisted settings, then hand control back to ``window``.

    ``on_saved`` runs after the settings are written and the profile list is
    refreshed; the entry point uses it to re-arm the update timer, since the
    interval may have changed.
    """
    dialog = _modal(root, "Preferences")
    dialog.grab_set()
    settings = window.settings

    ttk.Label(dialog, text="Refresh profile status every(minutes):").grid(
        row=0, column=0, padx=12, pady=12
    )
    refresh_value = tk.StringVar(value=str(settings.profile_refresh_minutes))
    ttk.Combobox(
        dialog,
        textvariable=refresh_value,
        values=REFRESH_CHOICES,
        state="readonly",
        width=8,
    ).grid(row=0, column=1, padx=(0, 12), pady=12)

    minimize_to_tray = tk.BooleanVar(value=settings.minimize_to_tray)
    ttk.Checkbutton(
        dialog,
        text="Minimize to system tray instead of taskbar",
        variable=minimize_to_tray,
    ).grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="w")

    label_for = {interval: label for label, interval in UPDATE_INTERVAL_CHOICES.items()}
    ttk.Label(dialog, text="Check for updates:").grid(row=2, column=0, padx=12, pady=(0, 12))
    update_value = tk.StringVar(
        value=label_for.get(settings.update_check_interval, "Weekly")
    )
    ttk.Combobox(
        dialog,
        textvariable=update_value,
        values=tuple(UPDATE_INTERVAL_CHOICES),
        state="readonly",
        width=10,
    ).grid(row=2, column=1, padx=(0, 12), pady=(0, 12))

    def save_preferences() -> None:
        settings.profile_refresh_minutes = int(refresh_value.get())
        settings.minimize_to_tray = minimize_to_tray.get()
        settings.update_check_interval = UPDATE_INTERVAL_CHOICES.get(
            update_value.get(), DEFAULT_UPDATE_INTERVAL
        )
        settings.save()
        dialog.destroy()
        window.refresh_profiles()
        window._schedule_profile_refresh()
        if on_saved is not None:
            on_saved()

    ttk.Button(dialog, text="Save", command=save_preferences).grid(
        row=3, column=0, columnspan=2, pady=(0, 12)
    )

    center_on_parent(root, dialog)


def show_about(root: tk.Misc) -> None:
    dialog = _modal(root, "About")
    dialog.grab_set()

    container = ttk.Frame(dialog, padding=20)
    container.pack(fill="both", expand=True)
    ttk.Label(container, text="AWS Tunnel", font=("", 13, "bold")).pack(anchor="w")
    ttk.Label(
        container,
        text=f"Version {APP_VERSION}\n\n"
             "Manage SSO profiles, log in, discover bastion hosts,\n"
             "and open/monitor/close SSM tunnels — in one place.",
        justify="left",
    ).pack(anchor="w", pady=(6, 12))
    ttk.Button(container, text="User Guide", command=open_user_guide).pack(
        side="left", padx=(0, 8)
    )
    ttk.Button(container, text="Close", command=dialog.destroy).pack(side="left")

    center_on_parent(root, dialog)


def show_update_available(root: tk.Misc, latest_tag: str) -> None:
    dialog = _modal(root, "Update Available")

    container = ttk.Frame(dialog, padding=20)
    container.pack(fill="both", expand=True)
    ttk.Label(
        container,
        text=f"Version {latest_tag} is available.\nYou are running {APP_VERSION}.",
        justify="left",
    ).pack(anchor="w", pady=(0, 12))

    buttons = ttk.Frame(container)
    buttons.pack()

    def open_release_page() -> None:
        webbrowser.open(update_checker.releases_page_url(GITHUB_REPO))
        dialog.destroy()

    ttk.Button(buttons, text="Open Download Page", command=open_release_page).pack(
        side="left", padx=(0, 8)
    )
    ttk.Button(buttons, text="Later", command=dialog.destroy).pack(side="left")

    center_on_parent(root, dialog)


def show_update_not_configured(root: tk.Misc) -> None:
    messagebox.showinfo(
        UPDATE_DIALOG_TITLE,
        "Update checking is not configured yet.\n\n"
        "Set GITHUB_REPO in config.py to the project's\n"
        "GitHub '<owner>/<repo>' to enable it.",
        parent=root,
    )


def show_update_failed(root: tk.Misc, error: Exception) -> None:
    messagebox.showwarning(
        UPDATE_DIALOG_TITLE,
        f"Could not check for updates:\n{error}",
        parent=root,
    )


def show_already_latest(root: tk.Misc) -> None:
    messagebox.showinfo(
        UPDATE_DIALOG_TITLE,
        f"You are running the latest version ({APP_VERSION}).",
        parent=root,
    )


__all__ = [
    "UPDATE_DIALOG_TITLE",
    "center_on_parent",
    "show_about",
    "show_already_latest",
    "show_preferences",
    "show_update_available",
    "show_update_failed",
    "show_update_not_configured",
]
