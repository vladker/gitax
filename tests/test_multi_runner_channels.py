"""Tests for channel selector in all archiver runners."""

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


class TestMultiRunnerChannels:
    def test_pypi_uses_pypi_channels(self, tmp_path, monkeypatch, isolated_env):
        """PyPI runner should use pypi channels from registry."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "pypi": [
                    {"url": "https://web.max.ru/pypi-ch", "label": "PyPI Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("pypi")
        assert len(channels) == 1
        assert channels[0].url == "https://web.max.ru/pypi-ch"

    def test_media_uses_media_channels(self, tmp_path, monkeypatch, isolated_env):
        """Media runner should use media channels from registry."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "media": [
                    {"url": "https://web.max.ru/media-ch", "label": "Media Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("media")
        assert len(channels) == 1

    def test_backup_uses_backup_channels(self, tmp_path, monkeypatch, isolated_env):
        """Backuper should use backup channels from registry."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "backup": [
                    {"url": "https://web.max.ru/backup-ch", "label": "Backup Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("backup")
        assert len(channels) == 1
