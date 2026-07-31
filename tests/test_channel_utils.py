"""Tests for channel registry utility functions."""

import pytest


@pytest.fixture(autouse=True)
def clear_config_cache():
    """Clear the config singleton before each test."""
    from config import init_config
    init_config("nonexistent.yaml")
    yield
    init_config("nonexistent.yaml")


@pytest.fixture
def isolated_env(monkeypatch):
    """Remove CHANNEL_* env vars and prevent load_dotenv from reloading them."""
    for key in ("CHANNEL_MAX", "CHANNEL_PYPI", "CHANNEL_MEDIA", "CHANNEL_BACKUP"):
        monkeypatch.delenv(key, raising=False)
    # Prevent load_dotenv from reloading .env (which has fake CHANNEL_* values)
    # Patch at the module where it's used, not where it's defined
    monkeypatch.setattr("config.loader.load_dotenv", lambda **kwargs: None)


class TestGetChannelsForFunction:
    def test_returns_enabled_channels(self, tmp_path, monkeypatch, isolated_env):
        monkeypatch.chdir(tmp_path)
        from config import init_config, get_config
        from config_utils import get_channels_for_function

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                    {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": False},
                ]
            }
        }), encoding="utf-8")
        init_config(str(cfg_file))

        channels = get_channels_for_function("github")
        assert len(channels) == 1
        assert channels[0].url == "https://web.max.ru/ch1"

    def test_returns_empty_for_no_channels(self, tmp_path, monkeypatch, isolated_env):
        monkeypatch.chdir(tmp_path)
        from config_utils import get_channels_for_function
        channels = get_channels_for_function("backup")
        assert channels == []

    def test_returns_all_enabled(self, tmp_path, monkeypatch, isolated_env):
        monkeypatch.chdir(tmp_path)
        from config import init_config, get_config
        from config_utils import get_channels_for_function

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "pypi": [
                    {"url": "https://web.max.ru/p1", "label": "A", "enabled": True},
                    {"url": "https://web.max.ru/p2", "label": "B", "enabled": True},
                ]
            }
        }), encoding="utf-8")
        init_config(str(cfg_file))

        channels = get_channels_for_function("pypi")
        assert len(channels) == 2

    def test_invalid_function_raises(self, tmp_path, monkeypatch, isolated_env):
        monkeypatch.chdir(tmp_path)
        from config_utils import get_channels_for_function
        with pytest.raises(ValueError):
            get_channels_for_function("invalid")


class TestGetChannelUrlBackwardCompat:
    def test_returns_first_enabled_url(self, tmp_path, monkeypatch, isolated_env):
        monkeypatch.chdir(tmp_path)
        from config import init_config
        from config_utils import get_channel_url_for_channel_key

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")
        init_config(str(cfg_file))

        url = get_channel_url_for_channel_key("max")
        assert url == "https://web.max.ru/ch1"

    def test_falls_back_to_old_channels(self, tmp_path, monkeypatch, isolated_env):
        """When registry is empty, fall back to old channels.{key}."""
        monkeypatch.chdir(tmp_path)
        from config import init_config
        from config_utils import get_channel_url_for_channel_key

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({
            "channels": {
                "max": "https://web.max.ru/legacy",
            }
        }), encoding="utf-8")
        init_config(str(cfg_file))

        url = get_channel_url_for_channel_key("max")
        assert url == "https://web.max.ru/legacy"
