import importlib
import json

import config


def _reload_config_with_temp_settings(monkeypatch, tmp_path):
    fake_settings = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", fake_settings)
    return importlib.reload(config)


def test_tunnel_profiles_round_trip(tmp_path, monkeypatch):
    settings_module = _reload_config_with_temp_settings(monkeypatch, tmp_path)
    settings = settings_module.Settings(
        tunnel_profiles=[
            settings_module.TunnelProfile(
                profile_id="profile-1",
                name="Development DB",
                aws_profile="dev",
                bastion_instance_id="i-123",
                bastion_name="Dev Bastion",
                target_host="db.internal",
                target_port=5432,
                local_port=15432,
            )
        ]
    )
    settings.save()

    loaded = settings_module.Settings.load()

    assert loaded.tunnel_profiles[0].name == "Development DB"
    assert loaded.tunnel_profiles[0].target_port == 5432


def test_legacy_bastion_profile_is_migrated(tmp_path, monkeypatch):
    settings_module = _reload_config_with_temp_settings(monkeypatch, tmp_path)
    settings_module.SETTINGS_FILE.write_text(
        json.dumps(
            {
                "bastion_profiles": [
                    {
                        "instance_id": "i-123",
                        "target_host": "db.internal",
                        "target_port": 5432,
                        "local_port": 15432,
                    }
                ]
            }
        )
    )

    loaded = settings_module.Settings.load()

    assert len(loaded.tunnel_profiles) == 1
    assert loaded.tunnel_profiles[0].bastion_instance_id == "i-123"
    assert loaded.tunnel_profiles[0].target_host == "db.internal"
    persisted = json.loads(settings_module.SETTINGS_FILE.read_text())
    assert "bastion_profiles" not in persisted
    assert persisted["tunnel_profiles"][0]["profile_id"] == loaded.tunnel_profiles[0].profile_id


def test_invalid_or_unknown_settings_fall_back_safely(tmp_path, monkeypatch):
    settings_module = _reload_config_with_temp_settings(monkeypatch, tmp_path)
    settings_module.SETTINGS_FILE.write_text("[]")
    assert settings_module.Settings.load() == settings_module.Settings()
    assert settings_module.SETTINGS_FILE.with_suffix(".json.bak").exists()

    settings_module.SETTINGS_FILE.write_text(json.dumps({"unknown": "ignored"}))
    assert settings_module.Settings.load().bastion_tag_key == "Role"


def test_invalid_json_is_backed_up_and_dirs_are_created(tmp_path, monkeypatch):
    settings_module = _reload_config_with_temp_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(settings_module, "APP_DATA_DIR", tmp_path / "app")
    monkeypatch.setattr(settings_module, "LOG_DIR", tmp_path / "app" / "logs")
    settings_module.SETTINGS_FILE.write_text("{")
    assert settings_module.Settings.load() == settings_module.Settings()
    settings_module.ensure_app_dirs()
    assert settings_module.LOG_DIR.is_dir()
