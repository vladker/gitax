"""Tests for config persistence with channel registry."""

import pytest
import yaml


@pytest.fixture(autouse=True)
def clear_config_cache():
    from config import init_config
    init_config("nonexistent.yaml")
    yield
    init_config("nonexistent.yaml")


@pytest.fixture
def isolated_env(monkeypatch):
    for key in ("CHANNEL_MAX", "CHANNEL_PYPI", "CHANNEL_MEDIA", "CHANNEL_BACKUP"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("config.loader.load_dotenv", lambda **kwargs: None)


class TestConfigPersistence:
    def test_save_and_reload_registry(self, tmp_path, monkeypatch, isolated_env):
        """Channel registry changes persist across config reloads."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")

        from config import init_config, get_config
        from config.model import ChannelEntry

        # First load — add channel
        init_config(str(cfg_file))
        config = get_config()
        config.channel_registry.add_channel(
            "github",
            "https://web.max.ru/new-ch",
            "New Channel"
        )

        # Save
        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(config.model_dump(), f, allow_unicode=True)

        # Reload
        init_config(str(cfg_file))
        config2 = get_config()

        assert len(config2.channel_registry.github) == 1
        assert config2.channel_registry.github[0].url == "https://web.max.ru/new-ch"
        assert config2.channel_registry.github[0].label == "New Channel"

    def test_registry_preserves_other_config(self, tmp_path, monkeypatch, isolated_env):
        """Saving registry doesn't lose other config sections."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 50},
            "browser": {"cdp_port": 9222},
        }), encoding="utf-8")

        from config import init_config, get_config

        init_config(str(cfg_file))
        config = get_config()
        config.channel_registry.add_channel(
            "github",
            "https://web.max.ru/ch",
            "Channel"
        )

        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(config.model_dump(), f, allow_unicode=True)

        init_config(str(cfg_file))
        config2 = get_config()

        # Other config preserved
        assert config2.archiver.limit == 50
        assert config2.browser.cdp_port == 9222
        # Registry added
        assert len(config2.channel_registry.github) == 1
