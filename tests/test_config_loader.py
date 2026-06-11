# tests/test_config_loader.py
"""Tests for config loader — YAML loading, env overrides, missing files."""

import os
import yaml
import pytest
from pathlib import Path


class TestFindConfig:
    def test_find_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("archiver:\n  limit: 50\n", encoding="utf-8")

        from config.loader import find_config
        result = find_config([tmp_path])
        assert result == cfg_file

    def test_find_not_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from config.loader import find_config
        result = find_config([tmp_path])
        assert result is None


class TestLoadConfig:
    def test_no_file_returns_defaults(self, tmp_path, monkeypatch):
        """App starts without config.yaml using all defaults."""
        monkeypatch.chdir(tmp_path)
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.archiver.limit == 1000
        assert cfg.archiver.split_mode == "auto"
        assert cfg.browser.cdp_port == 9222
        assert cfg.channels.max == ""

    def test_load_from_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 50, "split_mode": "off"},
            "channels": {"max": "https://example.com/max"},
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)
        assert cfg.archiver.limit == 50
        assert cfg.archiver.split_mode == "off"
        # Legacy channel cleared after migration to registry
        assert cfg.channels.max == ""
        assert len(cfg.channel_registry.github) == 1
        assert cfg.channel_registry.github[0].url == "https://example.com/max"
        # Unset fields get defaults
        assert cfg.archiver.repo_delay == 30
        assert cfg.browser.cdp_port == 9222

    def test_env_override_generic(self, tmp_path, monkeypatch):
        """ARCHIVER_LIMIT env var overrides YAML value."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ARCHIVER_LIMIT", "999")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 50},
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)
        assert cfg.archiver.limit == 999

    def test_env_override_legacy_github_token(self, tmp_path, monkeypatch):
        """GITHUB_TOKEN env var maps to github.token."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.github.token == "ghp_test123"

    def test_env_override_legacy_channel(self, tmp_path, monkeypatch):
        """CHANNEL_MAX env var maps to channels.max and migrates to registry."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CHANNEL_MAX", "https://channel.from.env")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        # Legacy channel preserved for backward compat
        assert cfg.channels.max == "https://channel.from.env"
        # Also migrated to registry
        assert len(cfg.channel_registry.github) == 1
        assert cfg.channel_registry.github[0].url == "https://channel.from.env"

    def test_env_override_legacy_media_watch_dir(self, tmp_path, monkeypatch):
        """MEDIA_WATCH_DIR env var maps to media_archiver.watch_dir."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MEDIA_WATCH_DIR", "/path/to/watch")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.media_archiver.watch_dir == "/path/to/watch"

    def test_env_override_bool(self, tmp_path, monkeypatch):
        """ARCHIVER_USE_LOCAL_BROWSER=true coerces to bool."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ARCHIVER_USE_LOCAL_BROWSER", "true")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.archiver.use_local_browser is True

    def test_env_override_int(self, tmp_path, monkeypatch):
        """BROWSER_CDP_PORT=9223 coerces to int."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("BROWSER_CDP_PORT", "9223")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.browser.cdp_port == 9223

    def test_invalid_env_value_ignored(self, tmp_path, monkeypatch):
        """Invalid env value (e.g., non-int for int field) keeps YAML/default."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ARCHIVER_LIMIT", "not-a-number")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.archiver.limit == 1000  # Keeps default

    def test_malformed_yaml_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bad_file = tmp_path / "config.yaml"
        bad_file.write_text("{invalid yaml: [[[}", encoding="utf-8")

        from config.loader import load_config
        with pytest.raises(ValueError, match="Malformed"):
            load_config(bad_file)

    def test_yaml_overrides_defaults(self, tmp_path, monkeypatch):
        """YAML values take precedence over model defaults."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"repo_delay": 60, "retries": 5},
            "backuper": {"page_size": 25},
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)
        assert cfg.archiver.repo_delay == 60
        assert cfg.archiver.retries == 5
        assert cfg.backuper.page_size == 25

    def test_env_beats_yaml(self, tmp_path, monkeypatch):
        """Env var overrides YAML value (env has highest priority)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ARCHIVER_LIMIT", "42")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 100},
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)
        assert cfg.archiver.limit == 42  # env beats yaml

    def test_extra_yaml_keys_ignored(self, tmp_path, monkeypatch):
        """Unknown YAML keys are silently ignored (extra='ignore')."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 5},
            "some_random_key": "value",
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)  # Should not raise
        assert cfg.archiver.limit == 5
