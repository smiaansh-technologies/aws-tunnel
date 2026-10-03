"""
Entry point.

Run with `python main.py` (or `python main.py --debug` for console
logging in addition to the log file).
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from config import GITHUB_REPO, Settings
from gui.help_docs import APP_VERSION, open_logs_folder, open_user_guide
from gui.main_window import MainWindow
from gui.tray import TrayIcon
from gui.tunnel_profiles_dialog import TunnelProfilesDialog
from logging_setup import setup_logging
import update_checker

log = logging.getLogger(__name__)


def minimize_to_tray(root: tk.Tk, event: tk.Event | None = None) -> None:
    """Withdraw the window when the minimize button iconifies it.

    Tk's minimize button only iconifies the window, which leaves it sitting
    in the taskbar. To match the close button (which already hides to the
    tray) we withdraw instead, so both buttons park the app in the tray.
    The actual withdraw is deferred with ``after_idle`` because doing it
    while ``<Unmap>`` is being dispatched can leave the window stuck in the
    iconic state on Windows.
    """
    root.after_idle(_withdraw_if_iconic, root)


def _withdraw_if_iconic(root: tk.Tk) -> None:
    if root.state() == "iconic":
        root.withdraw()
        root.attributes("-alpha", 1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="AWS Tunnel")
    parser.add_argument("--debug", action="store_true", help="also log to console")
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    log.info("Starting AWS Tunnel")

    root = tk.Tk()
    root.title("AWS Tunnel")
    root.geometry("760x700")
    root.minsize(700, 620)

    def quit_app() -> None:
        log.info("Shutting down")
        if main_window is not None:
            main_window.shutdown()
        tray.stop()
        root.destroy()
        sys.exit(0)

    def open_preferences() -> None:
        if main_window is None:
            return
        dialog = tk.Toplevel(root)
        dialog.title("Preferences")
        dialog.resizable(False, False)
        dialog.transient(root)
        dialog.grab_set()

        ttk.Label(dialog, text="Refresh profile status every(minutes):").grid(
            row=0, column=0, padx=12, pady=12
        )
        refresh_value = tk.StringVar(value=str(main_window.settings.profile_refresh_minutes))
        refresh_menu = ttk.Combobox(
            dialog, textvariable=refresh_value, values=("1", "5", "10"), state="readonly", width=8
        )
        refresh_menu.grid(row=0, column=1, padx=(0, 12), pady=12)
        minimize_to_tray = tk.BooleanVar(value=main_window.settings.minimize_to_tray)
        ttk.Checkbutton(
            dialog,
            text="Minimize to system tray instead of taskbar",
            variable=minimize_to_tray,
        ).grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="w")

        update_choices = {
            "Off": "off",
            "Daily": "daily",
            "Weekly": "weekly",
            "Biweekly": "biweekly",
            "Monthly": "monthly",
        }
        display_for = {interval: label for label, interval in update_choices.items()}
        ttk.Label(dialog, text="Check for updates:").grid(
            row=2, column=0, padx=12, pady=(0, 12)
        )
        update_value = tk.StringVar(
            value=display_for.get(main_window.settings.update_check_interval, "Weekly")
        )
        ttk.Combobox(
            dialog, textvariable=update_value, values=tuple(update_choices), state="readonly", width=10
        ).grid(row=2, column=1, padx=(0, 12), pady=(0, 12))

        def save_preferences() -> None:
            main_window.settings.profile_refresh_minutes = int(refresh_value.get())
            main_window.settings.minimize_to_tray = minimize_to_tray.get()
            main_window.settings.update_check_interval = update_choices.get(
                update_value.get(), "weekly"
            )
            main_window.settings.save()
            dialog.destroy()
            main_window.refresh_profiles()
            main_window._schedule_profile_refresh()
            _schedule_next_auto_check()

        ttk.Button(dialog, text="Save", command=save_preferences).grid(
            row=3, column=0, columnspan=2, pady=(0, 12)
        )

        root.update_idletasks()
        dialog.update_idletasks()
        center_x = root.winfo_x() + (root.winfo_width() - dialog.winfo_width()) // 2
        center_y = root.winfo_y() + (root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(center_x, 0)}+{max(center_y, 0)}")

    def open_tunnel_profiles() -> None:
        if main_window is None:
            return
        TunnelProfilesDialog(
            root,
            main_window.settings,
            main_window.refresh_profiles,
            on_open_tunnel=main_window._open_saved_tunnel_profile,
            bastions=list(main_window._bastions_by_id.values()),
        )

    def show_about() -> None:
        dialog = tk.Toplevel(root)
        dialog.title("About")
        dialog.resizable(False, False)
        dialog.transient(root)
        dialog.grab_set()

        container = ttk.Frame(dialog, padding=20)
        container.pack(fill="both", expand=True)
        ttk.Label(
            container, text="AWS Tunnel", font=("", 13, "bold")
        ).pack(anchor="w")
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

        root.update_idletasks()
        dialog.update_idletasks()
        center_x = root.winfo_x() + (root.winfo_width() - dialog.winfo_width()) // 2
        center_y = root.winfo_y() + (root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(center_x, 0)}+{max(center_y, 0)}")

    # -- Update checking -------------------------------------------------
    # The release lookup runs on a background thread; every Tk interaction
    # (dialogs, timers, settings writes) happens on the main thread only.
    update_check_job: list[str] = [""]  # pending root.after id, "" = none

    def current_settings() -> Settings:
        return main_window.settings if main_window is not None else Settings.load()

    def _show_update_dialog(latest_tag: str) -> None:
        dialog = tk.Toplevel(root)
        dialog.title("Update Available")
        dialog.resizable(False, False)
        dialog.transient(root)

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

        root.update_idletasks()
        dialog.update_idletasks()
        center_x = root.winfo_x() + (root.winfo_width() - dialog.winfo_width()) // 2
        center_y = root.winfo_y() + (root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(center_x, 0)}+{max(center_y, 0)}")

    def _start_check(manual: bool) -> None:
        """Kick off a release lookup on a worker thread."""
        if not GITHUB_REPO:
            if manual:
                messagebox.showinfo(
                    "Check for Updates",
                    "Update checking is not configured yet.\n\n"
                    "Set GITHUB_REPO in config.py to the project's\n"
                    "GitHub '<owner>/<repo>' to enable it.",
                    parent=root,
                )
            else:
                log.debug("Auto update check skipped: GITHUB_REPO is not configured")
            return
        log.info("Checking for updates against %s (manual=%s)", GITHUB_REPO, manual)

        def worker() -> None:
            try:
                tag = update_checker.latest_release(GITHUB_REPO)
            except Exception as exc:  # network/parse errors are non-fatal
                root.after(0, lambda: _handle_check_result(None, exc, manual))
            else:
                root.after(0, lambda: _handle_check_result(tag, None, manual))

        threading.Thread(target=worker, name="update-check", daemon=True).start()

    def _handle_check_result(tag: str | None, error: Exception | None, manual: bool) -> None:
        settings = current_settings()
        if error is None:
            update_checker.record_check(settings)
        else:
            log.warning("Update check failed: %s", error)

        if tag and update_checker.is_newer(tag, APP_VERSION):
            _show_update_dialog(tag)
        elif manual:
            if error is not None:
                messagebox.showwarning(
                    "Check for Updates",
                    f"Could not check for updates:\n{error}",
                    parent=root,
                )
            else:
                messagebox.showinfo(
                    "Check for Updates",
                    f"You are running the latest version ({APP_VERSION}).",
                    parent=root,
                )
        _schedule_next_auto_check()

    def _schedule_next_auto_check() -> None:
        """(Re)arm the periodic auto-check timer from current preferences."""
        if update_check_job[0]:
            root.after_cancel(update_check_job[0])
            update_check_job[0] = ""
        settings = current_settings()
        interval = update_checker.interval_seconds(settings.update_check_interval)
        if interval == 0:
            return  # auto checks disabled via preferences
        remaining = update_checker.seconds_until_due(settings)
        update_check_job[0] = root.after(max(remaining, 1) * 1000, auto_update_tick)

    def auto_update_tick() -> None:
        """Startup/periodic entry point: check when due, then re-arm."""
        update_check_job[0] = ""
        if update_checker.is_check_due(current_settings()):
            _start_check(manual=False)
        else:
            _schedule_next_auto_check()

    menu_bar = tk.Menu(root)
    file_menu = tk.Menu(menu_bar, tearoff=False)
    file_menu.add_command(label="Preferences...", command=open_preferences)
    file_menu.add_command(label="Tunnel Profiles...", command=open_tunnel_profiles)
    file_menu.add_separator()
    file_menu.add_command(label="Quit", command=quit_app)
    menu_bar.add_cascade(label="File", menu=file_menu)

    help_menu = tk.Menu(menu_bar, tearoff=False)
    help_menu.add_command(label="User Guide", command=open_user_guide)
    help_menu.add_command(label="Open Logs Folder", command=open_logs_folder)
    help_menu.add_command(
        label="Check for Updates...", command=lambda: _start_check(manual=True)
    )
    help_menu.add_separator()
    help_menu.add_command(
        label="About AWS Tunnel", command=show_about
    )
    menu_bar.add_cascade(label="Help", menu=help_menu)
    root.configure(menu=menu_bar)

    # If no SSO profiles exist yet, show only the setup wizard first.
    # The main window is built and shown once the wizard finishes (or is
    # skipped when profiles already exist).
    from aws.profiles import list_profiles
    from gui.profile_dialog import ProfileDialog

    main_window: MainWindow | None = None

    def build_and_show_main_window() -> None:
        nonlocal main_window
        if main_window is not None:
            return
        main_window = MainWindow(root)
        main_window.restore_window_position()
        root.deiconify()

    if not list_profiles():
        root.withdraw()  # hide the (empty) main window while the wizard runs

        def on_wizard_done() -> None:
            build_and_show_main_window()

        def on_wizard_closed() -> None:
            # Wizard cancelled without creating a profile — still show the
            # (empty) main window so the user can create one from there.
            build_and_show_main_window()

        wizard = ProfileDialog(root, on_created=on_wizard_done)
        def close_wizard() -> None:
            # ProfileDialog owns a short-lived AWS config entry while the
            # wizard is active; always remove it before revealing the app.
            wizard._cancel_and_cleanup()

        wizard.protocol("WM_DELETE_WINDOW", close_wizard)

        def _on_wizard_destroyed(event: tk.Event) -> None:
            # Only react to the dialog itself being destroyed, not its
            # child widgets (which are destroyed when switching steps).
            if event.widget is wizard:
                on_wizard_closed()

        wizard.bind("<Destroy>", _on_wizard_destroyed, add="+")
    else:
        build_and_show_main_window()

    def hide_to_tray() -> None:
        if not main_window.settings.minimize_to_tray:
            quit_app()
            return

        def fade(alpha: float) -> None:
            if alpha <= 0.1:
                root.withdraw()
                root.attributes("-alpha", 1.0)
                return
            root.attributes("-alpha", alpha)
            root.after(20, lambda: fade(alpha - 0.1))

        fade(1.0)

    def on_window_unmap(event: tk.Event) -> None:
        """Send the minimize button to the tray too, not just the close button."""
        if event.widget is not root:
            return
        if main_window is None or not main_window.settings.minimize_to_tray:
            return
        minimize_to_tray(root, event)

    tray = TrayIcon(root, on_quit=quit_app)
    tray.start()

    root.protocol("WM_DELETE_WINDOW", hide_to_tray)
    root.bind("<Unmap>", on_window_unmap, add="+")

    # First update-check tick runs right away; it only performs a network
    # request when the configured interval has elapsed (or on first run).
    auto_update_tick()

    root.mainloop()


if __name__ == "__main__":
    main()
