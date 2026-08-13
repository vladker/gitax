"""Tests for channel management CLI functions."""

import pytest


@pytest.fixture(autouse=True)
def clear_config_cache():
    from config import init_config
    init_config("nonexistent.yaml")
    yield
    init_config("nonexistent.yaml")


@pytest.fixture
def isolated_env(monkeypatch):
    for key in ("CHANNEL_max", "CHANNEL_pypi", "CHANNEL_media", "CHANNEL_backup"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("config.loader.load_dotenv", lambda **kwargs: None)


class TestChannelManager:
    def test_add_channel_to_registry(self, tmp_path, monkeypatch, isolated_env):
        """Adding a channel creates a new entry."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {}
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config.model import ChannelEntry
        config = get_config()
        config.channel_registry.add_channel(
            "github",
            "https://web.max.ru/new-channel",
            "New GitHub Channel"
        )

        assert len(config.channel_registry.github) == 1
        assert config.channel_registry.github[0].url == "https://web.max.ru/new-channel"
        assert config.channel_registry.github[0].label == "New GitHub Channel"

    def test_list_channels_shows_all(self, tmp_path, monkeypatch, isolated_env):
        """Listing channels shows all entries with status."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                    {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": False},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        all_channels = get_config().channel_registry.github
        assert len(all_channels) == 2
        enabled = get_channels_for_function("github")
        assert len(enabled) == 1

    def test_toggle_channel(self, tmp_path, monkeypatch, isolated_env):
        """Toggling a channel flips enabled state."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        config = get_config()
        config.channel_registry.toggle_channel("github", 0)
        assert config.channel_registry.github[0].enabled is False

    def test_delete_channel(self, tmp_path, monkeypatch, isolated_env):
        """Deleting a channel removes it from the list."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                    {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        config = get_config()
        config.channel_registry.remove_channel("github", 0)
        assert len(config.channel_registry.github) == 1
        assert config.channel_registry.github[0].url == "https://web.max.ru/ch2"

    def test_save_registry_to_yaml(self, tmp_path, monkeypatch, isolated_env):
        """Registry changes persist to config.yaml."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {}
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        config = get_config()
        config.channel_registry.add_channel(
            "github",
            "https://web.max.ru/test",
            "Test Channel"
        )

        # Write back
        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(config.model_dump(), f, allow_unicode=True)

        # Re-read
        with open(cfg_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert "channel_registry" in data
        assert len(data["channel_registry"]["github"]) == 1
        assert data["channel_registry"]["github"][0]["url"] == "https://web.max.ru/test"
