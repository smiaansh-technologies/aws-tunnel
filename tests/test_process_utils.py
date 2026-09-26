import subprocess
import sys

from process_utils import IS_WINDOWS, no_window_kwargs


def test_no_window_kwargs_suppress_console_on_windows():
    if not IS_WINDOWS:
        assert no_window_kwargs() == {}
        return
    kwargs = no_window_kwargs()
    assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_no_window_kwargs_combines_with_extra_flags():
    if not IS_WINDOWS:
        assert no_window_kwargs(0x1) == {}
        return
    kwargs = no_window_kwargs(subprocess.CREATE_NEW_PROCESS_GROUP)
    expected = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    assert kwargs["creationflags"] == expected


def test_non_windows_platforms_get_no_flags():
    with __import__("unittest").mock.patch("process_utils.IS_WINDOWS", False):
        assert no_window_kwargs() == {}
    assert sys.platform  # sanity: patching module flag does not touch sys.platform
