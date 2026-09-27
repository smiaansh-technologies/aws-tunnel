import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Module that needs a real Tk root; skipped when the environment can't
# provide one (headless CI, broken/mismatched Tcl runtime on a runner).
_GUI_MODULE = "test_gui_helpers.py"


def _tk_display_available() -> bool:
    try:
        import tkinter as tk
    except Exception:
        return False
    try:
        root = tk.Tk()
    except Exception:
        return False
    try:
        root.destroy()
    except Exception:
        return False
    return True


def pytest_collection_modifyitems(config, items):
    if _tk_display_available():
        return
    skip = pytest.mark.skip(reason="no usable Tk display in this environment")
    for item in items:
        if item.fspath.basename == _GUI_MODULE:
            item.add_marker(skip)
