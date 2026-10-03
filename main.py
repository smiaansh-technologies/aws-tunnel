"""
Entry point.

Run with `python main.py` (or `python main.py --debug` for console
logging in addition to the log file).

This module is wiring only: it creates the window, builds the menus and
connects the pieces. Dialogs live in ``gui/dialogs.py`` and the release
check in ``gui/update_service.py``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tkinter as tk

from config import Settings
from gui.dialogs import show_about, show_preferences
from gui.help_docs import open_logs_folder, open_user_guide
from gui.icons import apply_window_icon
from gui.main_window import MainWindow
from gui.tray import TrayIcon
from gui.tunnel_profiles_dialog import TunnelProfilesDialog
from gui.update_service import UpdateService
from logging_setup import setup_logging

log = logging.getLogger(__name__)

WINDOW_TITLE = "AWS Tunnel"
CHECK_FOR_UPDATES_LABEL = "Check for Updates..."


def minimize_to_tray(root: tk.Tk) -> None:
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


def _build_menu_bar(
    root: tk.Tk,
    window: "MainWindow",
    updates: UpdateService,
    on_quit,
) -> tk.Menu:
    menu_bar = tk.Menu(root)
    file_menu = tk.Menu(menu_bar, tearoff=False)
    file_menu.add_command(
        label="Preferences...",
        command=lambda: show_preferences(root, window, on_saved=updates.schedule_next),
    )
    file_menu.add_command(
        label="Tunnel Profiles...", command=lambda: _open_tunnel_profiles(root, window)
    )
    file_menu.add_separator()
    file_menu.add_command(label="Quit", command=on_quit)
    menu_bar.add_cascade(label="File", menu=file_menu)

    help_menu = tk.Menu(menu_bar, tearoff=False)
    help_menu.add_command(label="User Guide", command=open_user_guide)
    help_menu.add_command(label="Open Logs Folder", command=open_logs_folder)
    help_menu.add_command(
        label=CHECK_FOR_UPDATES_LABEL, command=lambda: updates.start_check(manual=True)
    )
    help_menu.add_separator()
    help_menu.add_command(label=f"About {WINDOW_TITLE}", command=lambda: show_about(root))
    menu_bar.add_cascade(label="Help", menu=help_menu)
    return menu_bar


def _open_tunnel_profiles(root: tk.Tk, window: "MainWindow") -> None:
    TunnelProfilesDialog(
        root,
        window.settings,
        window.refresh_profiles,
        on_open_tunnel=window._open_saved_tunnel_profile,
        bastions=list(window._bastions_by_id.values()),
    )


def _run_setup_wizard_if_needed(root: tk.Tk, on_ready) -> None:
    """Show the first-run wizard when no SSO profiles exist yet.

    The main window is built and shown once the wizard finishes, or skipped
    entirely when profiles already exist.
    """
    from aws.profiles import list_profiles
    from gui.profile_dialog import ProfileDialog

    if list_profiles():
        on_ready()
        return

    root.withdraw()  # hide the (empty) main window while the wizard runs
    wizard = ProfileDialog(root, on_created=on_ready)

    def close_wizard() -> None:
        # ProfileDialog owns a short-lived AWS config entry while the
        # wizard is active; always remove it before revealing the app.
        wizard._cancel_and_cleanup()

    def on_destroyed(event: tk.Event) -> None:
        # Only react to the dialog itself being destroyed, not its child
        # widgets (which are destroyed when switching wizard steps).
        if event.widget is wizard:
            on_ready()

    wizard.protocol("WM_DELETE_WINDOW", close_wizard)
    wizard.bind("<Destroy>", on_destroyed, add="+")


def main() -> None:
    parser = argparse.ArgumentParser(description=WINDOW_TITLE)
    parser.add_argument("--debug", action="store_true", help="also log to console")
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    log.info("Starting %s", WINDOW_TITLE)

    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.geometry("760x700")
    root.minsize(700, 620)
    apply_window_icon(root)

    main_window: MainWindow | None = None

    def current_settings() -> Settings:
        # The update timer can fire before the main window exists (during
        # the first-run wizard), so fall back to the persisted settings.
        return main_window.settings if main_window is not None else Settings.load()

    updates = UpdateService(root, current_settings)

    def quit_app() -> None:
        log.info("Shutting down")
        if main_window is not None:
            main_window.shutdown()
        tray.stop()
        root.destroy()
        sys.exit(0)

    def build_and_show_main_window() -> None:
        nonlocal main_window
        if main_window is not None:
            return
        main_window = MainWindow(root)
        main_window.restore_window_position()
        root.configure(menu=_build_menu_bar(root, main_window, updates, quit_app))
        root.deiconify()

    _run_setup_wizard_if_needed(root, build_and_show_main_window)

    def hide_to_tray() -> None:
        if not main_window.settings.minimize_to_tray:
            quit_app()
            return
        _fade_out(root)

    def on_window_unmap(event: tk.Event) -> None:
        """Send the minimize button to the tray too, not just the close button."""
        if event.widget is not root:
            return
        if main_window is None or not main_window.settings.minimize_to_tray:
            return
        minimize_to_tray(root)

    tray = TrayIcon(root, on_quit=quit_app)
    tray.start()

    root.protocol("WM_DELETE_WINDOW", hide_to_tray)
    root.bind("<Unmap>", on_window_unmap, add="+")

    # First update-check tick runs right away; it only performs a network
    # request when the configured interval has elapsed (or on first run).
    updates.tick()

    root.mainloop()


def _fade_out(root: tk.Tk) -> None:
    """Fade the window out, then hide it to the tray."""

    def fade(alpha: float) -> None:
        if alpha <= 0.1:
            root.withdraw()
            root.attributes("-alpha", 1.0)
            return
        root.attributes("-alpha", alpha)
        root.after(20, lambda: fade(alpha - 0.1))

    fade(1.0)


if __name__ == "__main__":
    main()
