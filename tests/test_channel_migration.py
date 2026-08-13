"""Tests for channel registry auto-migration."""

import os
import pytest
import yaml as yaml_mod


class TestChannelMigration:
    def _isolate_env(self, monkeypatch):
        """Clear legacy channel env vars and disable load_dotenv."""
        for key in ("CHANNEL_max", "CHANNEL_pypi", "CHANNEL_media", "CHANNEL_backup"):
            monkeypatch.delenv(key, raising=False)
        # Prevent load_dotenv from loading .env and overriding test config
        monkeypatch.setattr("config.loader.load_dotenv", lambda: None)

    def test_migrate_old_channels_to_registry(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "https://web.max.ru/github",
                "pypi": "https://web.max.ru/pypi",
                "media": "",
                "backup": "",
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        # Legacy channels are PRESERVED for backward compatibility
        assert cfg.channels.max == "https://web.max.ru/github"
        assert cfg.channels.pypi == "https://web.max.ru/pypi"
        # Registry gets populated from legacy
        assert len(cfg.channel_registry.github) == 1
        assert cfg.channel_registry.github[0].url == "https://web.max.ru/github"
        assert cfg.channel_registry.github[0].label == "GitHub Main"
        assert len(cfg.channel_registry.pypi) == 1
        assert cfg.channel_registry.pypi[0].url == "https://web.max.ru/pypi"

    def test_no_migration_if_registry_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "https://web.max.ru/old",
            },
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/new", "label": "New Channel", "enabled": True}
                ]
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        # Registry keeps existing entry + adds legacy URL if different (idempotent by URL)
        assert len(cfg.channel_registry.github) == 2
        urls = [ch.url for ch in cfg.channel_registry.github]
        assert "https://web.max.ru/new" in urls
        assert "https://web.max.ru/old" in urls
        # Legacy channel preserved for backward compat
        assert cfg.channels.max == "https://web.max.ru/old"

    def test_migration_idempotent_same_url(self, tmp_path, monkeypatch):
        """If registry already has the same URL as legacy, no duplicate is added."""
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "https://web.max.ru/same",
            },
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/same", "label": "Existing", "enabled": True}
                ]
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        # Only 1 entry — no duplicate
        assert len(cfg.channel_registry.github) == 1
        assert cfg.channel_registry.github[0].url == "https://web.max.ru/same"

    def test_migration_respects_skipped_channels(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "https://web.max.ru/github",
                "pypi": "https://web.max.ru/pypi",
            },
            "setup": {
                "skipped_channels": ["pypi"]
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        assert len(cfg.channel_registry.github) == 1
        assert len(cfg.channel_registry.pypi) == 0

    def test_migration_preserves_enabled_state(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "https://web.max.ru/github",
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        assert cfg.channel_registry.github[0].enabled is True

    def test_empty_channels_no_migration(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "",
                "pypi": "",
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        assert len(cfg.channel_registry.github) == 0
        assert len(cfg.channel_registry.pypi) == 0
