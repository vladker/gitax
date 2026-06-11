"""Tests for channel selector logic."""

import pytest


@pytest.fixture(autouse=True)
def clear_config_cache():
    from config import get_config
    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.fixture
def isolated_env(monkeypatch):
    for key in ("CHANNEL_MAX", "CHANNEL_PYPI", "CHANNEL_MEDIA", "CHANNEL_BACKUP"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("config.loader.load_dotenv", lambda **kwargs: None)


class TestChannelSelector:
    def test_single_channel_no_prompt(self, tmp_path, monkeypatch, isolated_env):
        """When only 1 enabled channel, no selector prompt needed."""
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

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        assert len(channels) == 1
        # Single channel = transparent pass-through
        selected = channels[0]
        assert selected.url == "https://web.max.ru/ch1"

    def test_multiple_channels_shows_options(self, tmp_path, monkeypatch, isolated_env):
        """When 2+ enabled channels, all options are available."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                    {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": True},
                    {"url": "https://web.max.ru/ch3", "label": "Disabled", "enabled": False},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        # Only enabled channels shown
        assert len(channels) == 2
        urls = [ch.url for ch in channels]
        assert "https://web.max.ru/ch1" in urls
        assert "https://web.max.ru/ch2" in urls
        assert "https://web.max.ru/ch3" not in urls

    def test_no_channels_returns_empty(self, tmp_path, monkeypatch, isolated_env):
        """When no channels configured, return empty."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {}
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        assert len(channels) == 0

    def test_disabled_channels_excluded(self, tmp_path, monkeypatch, isolated_env):
        """Disabled channels should not appear in selector."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "pypi": [
                    {"url": "https://web.max.ru/ch1", "label": "A", "enabled": False},
                    {"url": "https://web.max.ru/ch2", "label": "B", "enabled": False},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("pypi")
        assert len(channels) == 0
