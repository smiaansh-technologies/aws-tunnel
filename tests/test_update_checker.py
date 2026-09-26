import io
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import update_checker
from config import Settings


# -- version comparison -------------------------------------------------


@pytest.mark.parametrize("candidate,current,expected", [
    ("v1.1.0", "1.0.0", True),
    ("1.10.0", "1.9.0", True),
    ("2.0", "1.9.9", True),
    ("1.0.0", "1.0.0", False),
    ("1.0.0", "1.0", False),      # 1.0.0 == 1.0 (zero padding)
    ("v1.0.0", "1.0", False),
    ("1.0", "1.0.1", False),
    ("0.9.9", "1.0.0", False),
])
def test_is_newer_handles_versions_of_different_length(candidate, current, expected):
    assert update_checker.is_newer(candidate, current) is expected


def test_is_newer_ignores_non_numeric_suffixes():
    assert update_checker.is_newer("v1.2.0-beta", "1.1.9") is True
    assert update_checker.is_newer("v1.2.0-beta", "1.2.0") is False


def test_is_newer_treats_garbage_as_zero():
    assert update_checker.is_newer("v1.2.x", "1.2.0") is False
    assert update_checker.is_newer("release", "0.0.1") is False


# -- GitHub release lookup ----------------------------------------------


def _fake_urlopen(payload: bytes):
    response = MagicMock()
    response.__enter__.return_value = io.BytesIO(payload)
    return response


def test_latest_release_returns_tag_name():
    payload = json.dumps({"tag_name": "v1.2.3", "name": "1.2.3"}).encode()
    with patch.object(update_checker.urllib.request, "urlopen", return_value=_fake_urlopen(payload)) as urlopen:
        assert update_checker.latest_release("owner/repo") == "v1.2.3"
    request = urlopen.call_args.args[0]
    assert request.full_url == update_checker.releases_api_url("owner/repo")
    assert "aws-tunnel" in request.headers["User-agent"]


def test_latest_release_raises_on_missing_tag():
    with patch.object(update_checker.urllib.request, "urlopen", return_value=_fake_urlopen(b"{}")):
        with pytest.raises(update_checker.UpdateCheckError, match="tag_name"):
            update_checker.latest_release("owner/repo")


def test_latest_release_wraps_network_errors():
    with patch.object(
        update_checker.urllib.request, "urlopen", side_effect=update_checker.urllib.error.URLError("down")
    ):
        with pytest.raises(update_checker.UpdateCheckError, match="Could not reach"):
            update_checker.latest_release("owner/repo")


# -- scheduling ---------------------------------------------------------


def test_interval_seconds_known_and_unknown():
    assert update_checker.interval_seconds("weekly") == 7 * 86400
    assert update_checker.interval_seconds("biweekly") == 14 * 86400
    assert update_checker.interval_seconds("daily") == 86400
    assert update_checker.interval_seconds("monthly") == 30 * 86400
    assert update_checker.interval_seconds("off") == 0


def test_check_due_on_first_run_and_recurring_after_interval():
    settings = Settings(update_check_interval="weekly", last_update_check="")
    assert update_checker.is_check_due(settings)

    recent = (datetime.now() - timedelta(days=1)).isoformat()
    settings.last_update_check = recent
    assert not update_checker.is_check_due(settings)

    stale = (datetime.now() - timedelta(days=8)).isoformat()
    settings.last_update_check = stale
    assert update_checker.is_check_due(settings)


def test_off_interval_never_due():
    settings = Settings(update_check_interval="off", last_update_check="")
    assert not update_checker.is_check_due(settings)


def test_corrupt_timestamp_is_treated_as_due():
    settings = Settings(update_check_interval="daily", last_update_check="not-a-date")
    assert update_checker.is_check_due(settings)


def test_record_check_stamps_and_saves(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("config.SETTINGS_FILE", settings_file)
    settings = Settings(update_check_interval="daily", last_update_check="")
    update_checker.record_check(settings)
    assert settings.last_update_check  # stamped
    saved = json.loads(settings_file.read_text())
    assert saved["last_update_check"] == settings.last_update_check
