"""Tests for channel selector integration with archiver runners."""

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


class TestArchiverChannelIntegration:
    def test_ensure_channel_ready_uses_selector(self, tmp_path, monkeypatch, isolated_env):
        """Channel registry is used for channel resolution."""
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

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        assert len(channels) == 1
        assert channels[0].url == "https://web.max.ru/ch1"

    def test_multi_channel_returns_list(self, tmp_path, monkeypatch, isolated_env):
        """When multiple channels exist, selector can return list for parallel."""
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

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        assert len(channels) == 2
