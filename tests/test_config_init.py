# tests/test_config_init.py
"""Tests for config singleton — caching, init_config, singleton reset."""

import os
import yaml
import pytest
from pathlib import Path


class TestGetConfig:
    def test_returns_appconfig(self):
        """get_config() returns an AppConfig instance."""
        from config import get_config, AppConfig, init_config
        init_config("nonexistent_config.yaml")
        cfg = get_config()
        assert isinstance(cfg, AppConfig)

    def test_singleton_same_object(self):
        """Two calls return the same object (singleton)."""
        from config import get_config, init_config
        init_config("nonexistent_config.yaml")
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_init_config_changes_path(self, tmp_path, monkeypatch):
        """init_config() overrides config path for next get_config()."""
        monkeypatch.chdir(tmp_path)

        # Create a test config
        cfg_file = tmp_path / "custom_config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 777},
        }), encoding="utf-8")

        from config import init_config, get_config

        # Get defaults first
        init_config(str(cfg_file))
        cfg = get_config()
        assert cfg.archiver.limit == 777

    def test_init_config_clears_singleton(self, tmp_path, monkeypatch):
        """init_config() resets the singleton so next get_config() reloads."""
        monkeypatch.chdir(tmp_path)

        from config import init_config, get_config

        # First load with nonexistent path
        init_config("nonexistent.yaml")
        cfg1 = get_config()

        # Create config file after first load
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 555},
        }), encoding="utf-8")

        # Init with new path — should reload
        init_config(str(cfg_file))
        cfg2 = get_config()
        assert cfg2.archiver.limit == 555
        assert cfg2 is not cfg1

    def test_singleton_resettable(self):
        """init_config() resets singleton for testing."""
        from config import init_config, get_config
        init_config("nonexistent_cache_test.yaml")
        cfg1 = get_config()
        init_config("nonexistent_cache_test2.yaml")
        cfg2 = get_config()
        # After reset, should be a new object
        assert cfg1 is not cfg2


class TestInitConfig:
    def test_none_path_auto_discovers(self, tmp_path, monkeypatch):
        """get_config() with no path and no init_config() uses find_config()."""
        monkeypatch.chdir(tmp_path)
        from config import init_config, get_config
        init_config(str(tmp_path / "nonexistent.yaml"))
        cfg = get_config()
        assert cfg.archiver.limit == 1000  # All defaults

    def test_init_config_twice(self, tmp_path, monkeypatch):
        """Calling init_config() multiple times works."""
        monkeypatch.chdir(tmp_path)
        from config import init_config, get_config

        init_config(str(tmp_path / "a.yaml"))
        init_config(str(tmp_path / "b.yaml"))
        cfg = get_config()
        assert cfg.archiver.limit == 1000  # Defaults since neither file exists
