"""Tests for ChannelRegistryUI."""

import pytest


class TestChannelRegistryUI:
    def _isolate_env(self, monkeypatch):
        """Clear legacy channel env vars and disable load_dotenv."""
        for key in ("CHANNEL_MAX", "CHANNEL_PYPI", "CHANNEL_MEDIA", "CHANNEL_BACKUP"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr("config.loader.load_dotenv", lambda: None)

    def test_add_channel(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        from config import init_config
        from channel_registry_ui import add_channel, show_channels

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")
        init_config(str(cfg_file))

        add_channel("github", "https://web.max.ru/ch1", "GitHub Main")

        from config import get_config
        config = get_config()
        assert len(config.channel_registry.github) == 1
        assert config.channel_registry.github[0].url == "https://web.max.ru/ch1"
        assert config.channel_registry.github[0].label == "GitHub Main"

    def test_remove_channel(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        from config import init_config
        from channel_registry_ui import add_channel, remove_channel

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")
        init_config(str(cfg_file))

        add_channel("github", "https://web.max.ru/ch1", "Main")
        add_channel("github", "https://web.max.ru/ch2", "Archive")

        remove_channel("github", 0)

        from config import get_config
        config = get_config()
        assert len(config.channel_registry.github) == 1
        assert config.channel_registry.github[0].url == "https://web.max.ru/ch2"

    def test_toggle_channel(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        from config import init_config
        from channel_registry_ui import add_channel, toggle_channel

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")
        init_config(str(cfg_file))

        add_channel("github", "https://web.max.ru/ch1", "Main")

        from config import get_config
        config = get_config()
        assert config.channel_registry.github[0].enabled is True

        toggle_channel("github", 0)

        config = get_config()
        assert config.channel_registry.github[0].enabled is False

    def test_select_channel_single(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        from config import init_config
        from channel_registry_ui import add_channel, select_channel

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")
        init_config(str(cfg_file))

        add_channel("github", "https://web.max.ru/ch1", "GitHub Main")

        result = select_channel("github")
        assert result is not None
        assert result.url == "https://web.max.ru/ch1"

    def test_select_channel_none_enabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        from config import init_config
        from channel_registry_ui import select_channel

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")
        init_config(str(cfg_file))

        result = select_channel("github")
        assert result is None

    def test_select_channel_multiple(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._isolate_env(monkeypatch)
        from config import init_config
        from channel_registry_ui import add_channel, select_channel

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")
        init_config(str(cfg_file))

        add_channel("github", "https://web.max.ru/ch1", "Main")
        add_channel("github", "https://web.max.ru/ch2", "Archive")

        import io
        import sys
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("1\n")
        try:
            result = select_channel("github")
        finally:
            sys.stdin = old_stdin

        assert result is not None
        assert result.url == "https://web.max.ru/ch2"
