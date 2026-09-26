import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

from gui import clipboard, tray


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
