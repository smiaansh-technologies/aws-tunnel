import tkinter as tk
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from gui import clipboard, tray
from gui.main_window import MainWindow
from main import main, minimize_to_tray
from tunnel.manager import ActiveTunnel


@pytest.fixture
def root():
    window = tk.Tk()
    window.withdraw()
    yield window
    window.destroy()


def test_clipboard_replace_uses_available_text(root, monkeypatch):
    entry = tk.Entry(root)
    entry.insert(0, "old")
    monkeypatch.setattr(clipboard, "read_text", lambda _widget: "new")
    assert clipboard.replace_from_clipboard(entry, MagicMock()) == "break"
    assert entry.get() == "new"


def test_clipboard_replace_leaves_entry_when_empty(root, monkeypatch):
    entry = tk.Entry(root)
    entry.insert(0, "old")
    monkeypatch.setattr(clipboard, "read_text", lambda _widget: "")
    clipboard.replace_from_clipboard(entry, MagicMock())
    assert entry.get() == "old"


def test_clipboard_falls_back_to_tk_when_windows_clipboard_is_unavailable(monkeypatch):
    monkeypatch.setattr(clipboard.os, "name", "posix")
    widget = MagicMock()
    widget.clipboard_get.return_value = " copied "
    assert clipboard.read_text(widget) == "copied"


def test_clipboard_returns_empty_when_tk_clipboard_raises(root, monkeypatch):
    monkeypatch.setattr(clipboard.os, "name", "posix")
    widget = MagicMock()
    widget.clipboard_get.side_effect = tk.TclError()
    assert clipboard.read_text(widget) == ""


def test_tray_icon_builds_and_routes_actions(root):
    on_quit = MagicMock()
    with patch("gui.tray.pystray.Icon") as icon_type:
        instance = tray.TrayIcon(root, on_quit)
    icon_type.assert_called_once()
    instance._icon = MagicMock()
    instance.start()
    instance.stop()
    instance._show()
    instance._quit()
    root.update()
    assert root.state() == "normal"
    on_quit.assert_called_once()
    instance._icon.stop.assert_called_once()


def test_build_icon_image_has_expected_size():
    assert tray._build_icon_image().size == (tray.ICON_SIZE, tray.ICON_SIZE)


@pytest.fixture
def main_window(root, monkeypatch):
    """A MainWindow with its AWS calls and background timers stubbed out.

    The real constructor refreshes AWS profiles and schedules recurring
    jobs, neither of which belongs in a unit test.
    """
    monkeypatch.setattr(MainWindow, "refresh_profiles", lambda self: None)
    for name in ("_schedule_profile_refresh", "_schedule_tunnel_refresh", "_schedule_uptime_refresh"):
        monkeypatch.setattr(MainWindow, name, lambda self: None)
    window = MainWindow(root)
    yield window
    window.destroy()


def test_active_tunnels_columns_put_profile_first_and_bastion_before_uptime(main_window):
    """Active Tunnels shows the Tunnel Profile name first and Bastion Host before Uptime."""
    labels = [main_window.tunnel_tree.heading(col, "text") for col in main_window.tunnel_tree["columns"]]
    assert labels == [
        "Tunnel Profile", "Target", "Local Port", "Method", "Bastion Host", "Uptime",
    ]


def test_active_tunnels_row_shows_profile_name_and_em_dash_for_adhoc(main_window):
    """A saved profile shows its name; an ad-hoc tunnel shows an em dash."""
    running = MagicMock()
    running.poll.return_value = None
    now = datetime.now()

    main_window._active_tunnels_by_id = {
        "saved": ActiveTunnel(
            id="saved", method="ssm", bastion_label="dev-bastion",
            target_host="db.internal", target_port=5432, local_port=15432,
            started_at=now, handle=running, profile_label="dev-postgres",
        ),
        "adhoc": ActiveTunnel(
            id="adhoc", method="ssh", bastion_label="other-bastion",
            target_host="api.internal", target_port=443, local_port=8443,
            started_at=now, handle=running, profile_label=None,
        ),
    }
    main_window.tunnel_manager.list_tunnels = lambda: list(main_window._active_tunnels_by_id.values())
    main_window._refresh_tunnels()

    saved_values = list(main_window.tunnel_tree.item("saved", "values"))
    assert saved_values[0] == "dev-postgres"
    assert saved_values[4] == "dev-bastion"
    assert saved_values[5].startswith("0:")

    adhoc_values = list(main_window.tunnel_tree.item("adhoc", "values"))
    assert adhoc_values[0] == "—"
    assert adhoc_values[4] == "other-bastion"


def test_uptime_refresh_updates_last_column_only(main_window):
    """The per-second tick writes to the Uptime column, not the profile name."""
    running = MagicMock()
    running.poll.return_value = None

    main_window._active_tunnels_by_id = {
        "t1": ActiveTunnel(
            id="t1", method="ssm", bastion_label="dev-bastion",
            target_host="db.internal", target_port=5432, local_port=15432,
            started_at=datetime.now() - timedelta(minutes=5), handle=running,
            profile_label="dev-postgres",
        ),
    }
    main_window.tunnel_manager.list_tunnels = lambda: list(main_window._active_tunnels_by_id.values())
    main_window._refresh_tunnels()

    before = list(main_window.tunnel_tree.item("t1", "values"))
    main_window._refresh_tunnel_uptimes()
    after = list(main_window.tunnel_tree.item("t1", "values"))

    assert after[0] == before[0] == "dev-postgres"
    assert after[-1] == before[-1]


def test_profiles_section_is_labelled_aws_profiles(main_window):
    assert main_window.profile_tree.winfo_parent() is not None
    frame = main_window.profile_tree.master
    assert frame.cget("text") == "AWS Profiles"


class _FakeRoot:
    """Minimal stand-in so the iconic branch can be tested without a WM.

    Whether Tk honours iconify() depends on a window manager being present,
    which headless CI does not have, so the state is reported directly.
    """

    def __init__(self, state: str) -> None:
        self._state = state
        self.withdrawn = False

    def state(self) -> str:
        return self._state

    def withdraw(self) -> None:
        self.withdrawn = True
        self._state = "withdrawn"

    def attributes(self, *args) -> None:
        pass

    def after_idle(self, func, *args) -> None:
        func(*args)


def test_minimize_button_hides_window_to_tray():
    """An iconic window (minimize clicked) must be withdrawn, not left in the taskbar."""
    fake = _FakeRoot("iconic")
    minimize_to_tray(fake)
    assert fake.withdrawn is True
    assert fake.state() == "withdrawn"


def test_withdraw_is_skipped_when_window_is_not_iconic(root):
    """A normal unmap (e.g. restoring from tray) must not re-withdraw the window."""
    window = tk.Toplevel(root)
    root.update()
    assert window.state() == "normal"

    minimize_to_tray(window)
    root.update()

    assert window.state() == "normal"
    window.destroy()


def test_unmap_binding_is_registered_for_minimize_to_tray(main_window):
    """main() binds <Unmap> so the minimize button routes through the tray handler."""
    import inspect

    source = inspect.getsource(main)
    assert 'root.bind("<Unmap>"' in source
