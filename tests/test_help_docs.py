"""Tests for gui/help_docs.py — bundled docs path resolution and openers."""

import re
from pathlib import Path
from unittest import mock

import config
import gui.help_docs as help_docs


def test_app_root_in_source_mode_points_at_repo_root():
    root = help_docs.app_root()
    assert (root / "main.py").exists()
    assert (root / "config.py").exists()


def test_user_guide_path_is_docs_user_guide_html():
    path = help_docs.user_guide_path()
    assert path.parent.name == "docs"
    assert path.name == "user_guide.html"


def test_bundled_guide_exists_in_repo():
    assert help_docs.user_guide_path().exists()


def test_open_user_guide_opens_browser_with_file_uri():
    with mock.patch.object(help_docs.webbrowser, "open") as open_mock:
        assert help_docs.open_user_guide() is True
    opened = open_mock.call_args[0][0]
    assert opened.startswith("file:")
    assert opened.endswith("user_guide.html")


def test_open_user_guide_returns_false_when_guide_missing():
    with mock.patch.object(
        help_docs, "user_guide_path", return_value=Path("Z:/missing/user_guide.html")
    ):
        with mock.patch.object(help_docs.webbrowser, "open") as open_mock:
            assert help_docs.open_user_guide() is False
    open_mock.assert_not_called()


def test_open_logs_folder_creates_dir_and_reveals(monkeypatch, tmp_path):
    monkeypatch.setattr(help_docs, "LOG_DIR", tmp_path / "logs")
    revealed = {}
    monkeypatch.setattr(
        help_docs, "_reveal_in_file_manager", lambda path: revealed.setdefault("path", path)
    )
    help_docs.open_logs_folder()
    assert revealed["path"] == tmp_path / "logs"
    assert (tmp_path / "logs").is_dir()


def test_guide_image_references_exist():
    """Every local image the guide references must exist on disk."""
    guide = help_docs.user_guide_path()
    refs = re.findall(r'src="([^"]+)"', guide.read_text(encoding="utf-8"))
    assert refs, "guide should reference screenshots"
    for ref in refs:
        if ref.startswith(("http:", "https:")):
            continue
        assert (guide.parent / ref).exists(), f"missing asset referenced by guide: {ref}"


def test_app_version_is_a_nonempty_string():
    assert isinstance(config.APP_VERSION, str)
    assert config.APP_VERSION.strip()
