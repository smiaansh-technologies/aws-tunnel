"""
Tests for aws/profiles.py. These point the module at a temp file instead
of the real ~/.aws/config, so they never touch a real AWS setup.
"""

import importlib
import json

import config


def _reload_profiles_with_temp_config(monkeypatch, tmp_path):
    fake_config = tmp_path / "config"
    monkeypatch.setattr(config, "AWS_CONFIG_FILE", fake_config)
    import aws.profiles as profiles
    importlib.reload(profiles)
    monkeypatch.setattr(profiles, "AWS_CONFIG_FILE", fake_config)
    return profiles


def test_create_and_list_profile(tmp_path, monkeypatch):
    profiles = _reload_profiles_with_temp_config(monkeypatch, tmp_path)

    new_profile = profiles.AWSProfile(
        name="dev",
        sso_start_url="https://example.awsapps.com/start",
        sso_region="us-east-1",
        sso_account_id="111122223333",
        sso_role_name="DevRole",
        region="us-east-1",
    )
    profiles.create_profile(new_profile)

    found = profiles.list_profiles()
    assert len(found) == 1
    assert found[0].name == "dev"
    assert found[0].sso_account_id == "111122223333"


def test_delete_profile(tmp_path, monkeypatch):
    profiles = _reload_profiles_with_temp_config(monkeypatch, tmp_path)

    profiles.create_profile(
        profiles.AWSProfile(
            name="staging",
            sso_start_url="https://example.awsapps.com/start",
            sso_region="us-east-1",
            sso_account_id="444455556666",
            sso_role_name="StagingRole",
        )
    )
    assert len(profiles.list_profiles()) == 1

    removed = profiles.delete_profile("staging")
    assert removed is True
    assert profiles.list_profiles() == []


def test_delete_default_profile_is_refused(tmp_path, monkeypatch):
    profiles = _reload_profiles_with_temp_config(monkeypatch, tmp_path)
    assert profiles.delete_profile("default") is False


def test_list_profile_resolves_shared_sso_session(tmp_path, monkeypatch):
    profiles = _reload_profiles_with_temp_config(monkeypatch, tmp_path)
    profiles.AWS_CONFIG_FILE.write_text(
        """
[profile dev]
sso_session = company
sso_account_id = 111122223333
sso_role_name = Developer
region = ap-south-1

[sso-session company]
sso_start_url = https://example.awsapps.com/start
sso_region = ap-south-1
"""
    )

    found = profiles.list_profiles()

    assert len(found) == 1
    assert found[0].name == "dev"
    assert found[0].sso_start_url == "https://example.awsapps.com/start"
    assert found[0].sso_region == "ap-south-1"


def test_get_token_expiry_uses_latest_matching_cache_entry(tmp_path, monkeypatch):
    profiles = _reload_profiles_with_temp_config(monkeypatch, tmp_path)
    cache_dir = tmp_path / "sso-cache"
    cache_dir.mkdir()
    monkeypatch.setattr(profiles, "AWS_SSO_CACHE_DIR", cache_dir)

    for index, expires_at in enumerate((
        "2026-09-25T03:10:51Z",
        "2026-09-25T04:38:41Z",
    )):
        (cache_dir / f"token-{index}.json").write_text(
            json.dumps({
                "startUrl": "https://example.awsapps.com/start",
                "expiresAt": expires_at,
            })
        )

    (cache_dir / "aws-toolkit-vscode-client-id-region-client.json").write_text(
        json.dumps({
            "startUrl": "https://example.awsapps.com/start",
            "expiresAt": "2026-12-24T02:49:56.000Z",
        })
    )

    assert profiles.get_token_expiry("https://example.awsapps.com/start").isoformat() == (
        "2026-09-25T04:38:41+00:00"
    )


def test_profile_listing_skips_non_sso_and_invalid_token_cache(tmp_path, monkeypatch):
    profiles = _reload_profiles_with_temp_config(monkeypatch, tmp_path)
    profiles.AWS_CONFIG_FILE.write_text("[profile static]\nregion = us-east-1\n")
    assert profiles.list_profiles() == []

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(profiles, "AWS_SSO_CACHE_DIR", cache_dir)
    (cache_dir / "bad.json").write_text("not json")
    (cache_dir / "wrong.json").write_text(json.dumps({"startUrl": "https://other", "expiresAt": "bad"}))
    assert profiles.get_token_expiry("https://example") is None


def test_is_logged_in_handles_expiry_and_credential_errors(monkeypatch):
    import aws.profiles as profiles

    profile = profiles.AWSProfile("dev", "https://example", "us-east-1", "1", "Role")
    monkeypatch.setattr(profiles, "get_token_expiry", lambda _url: None)
    assert profiles.is_logged_in(profile) is False

    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(profiles, "get_token_expiry", lambda _url: datetime.now(timezone.utc) + timedelta(minutes=5))
    import boto3
    monkeypatch.setattr(boto3, "Session", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("no creds")))
    assert profiles.is_logged_in(profile) is False
