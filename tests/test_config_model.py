# tests/test_config_model.py
"""Tests for config models — validation, defaults, type enforcement."""

import pytest
from typing import Literal
from pydantic import ValidationError


class TestArchiverConfig:
    def test_defaults(self):
        from config.model import ArchiverConfig
        cfg = ArchiverConfig()
        assert cfg.limit == 1000
        assert cfg.split_mode == "auto"
        assert cfg.split_threshold_mb == 49
        assert cfg.use_local_browser is False
        assert cfg.output_dir == "./temp"

    def test_valid_split_modes(self):
        from config.model import ArchiverConfig
        for mode in ("auto", "on", "off", "prompt"):
            cfg = ArchiverConfig(split_mode=mode)
            assert cfg.split_mode == mode

    def test_invalid_split_mode_raises(self):
        from config.model import ArchiverConfig
        with pytest.raises(ValidationError):
            ArchiverConfig(split_mode="invalid")

    def test_negative_threshold_raises(self):
        from config.model import ArchiverConfig
        with pytest.raises(ValidationError):
            ArchiverConfig(split_threshold_mb=0)


class TestBrowserConfig:
    def test_defaults(self):
        from config.model import BrowserConfig
        cfg = BrowserConfig()
        assert cfg.cdp_port == 9222
        assert cfg.profile_name == "Default"
        assert cfg.user_data_dir == ""


class TestChannelsConfig:
    def test_defaults(self):
        from config.model import ChannelsConfig
        cfg = ChannelsConfig()
        assert cfg.max == ""
        assert cfg.pypi == ""
        assert cfg.media == ""
        assert cfg.backup == ""

    def test_custom_values(self):
        from config.model import ChannelsConfig
        cfg = ChannelsConfig(max="https://max.example.com/1", pypi="https://max.example.com/2")
        assert cfg.max == "https://max.example.com/1"
        assert cfg.pypi == "https://max.example.com/2"
        assert cfg.media == ""


class TestBackuperConfig:
    def test_defaults(self):
        from config.model import BackuperConfig
        cfg = BackuperConfig()
        assert cfg.default_volume_size == "49M"
        assert cfg.compression_level == "5"
        assert cfg.seven_zip_exe == "C:\\Program Files\\7-Zip\\7z.exe"
        assert cfg.page_size == 10
        assert cfg.retries == 3


class TestMediaArchiverConfig:
    def test_defaults(self):
        from config.model import MediaArchiverConfig
        cfg = MediaArchiverConfig()
        assert cfg.watch_dir == ""
        assert ".jpg" in cfg.extensions.images
        assert ".mp4" in cfg.extensions.videos

    def test_custom_extensions(self):
        from config.model import MediaArchiverConfig, MediaExtensionsConfig
        cfg = MediaArchiverConfig(
            extensions=MediaExtensionsConfig(images=[".png"], videos=[".webm"])
        )
        assert cfg.extensions.images == [".png"]
        assert cfg.extensions.videos == [".webm"]


class TestPyPILibsArchiverConfig:
    def test_defaults(self):
        from config.model import PyPILibsArchiverConfig
        cfg = PyPILibsArchiverConfig()
        assert cfg.limit == 20
        assert cfg.split_mode == "auto"
        assert cfg.output_dir == "./temp_pypi_libs"


class TestSetupConfig:
    def test_defaults(self):
        from config.model import SetupConfig
        cfg = SetupConfig()
        assert cfg.skipped_channels == []


class TestGitHubConfig:
    def test_defaults(self):
        from config.model import GitHubConfig
        cfg = GitHubConfig()
        assert cfg.token == ""


class TestAppConfig:
    def test_defaults(self):
        from config.model import AppConfig
        cfg = AppConfig()
        assert cfg.archiver.limit == 1000
        assert cfg.browser.cdp_port == 9222
        assert cfg.channels.max == ""
        assert cfg.backuper.default_volume_size == "49M"

    def test_from_dict(self):
        from config.model import AppConfig
        data = {
            "archiver": {"limit": 50, "split_mode": "off"},
            "channels": {"max": "https://example.com"},
        }
        cfg = AppConfig(**data)
        assert cfg.archiver.limit == 50
        assert cfg.archiver.split_mode == "off"
        assert cfg.channels.max == "https://example.com"
        # Unset fields get defaults
        assert cfg.browser.cdp_port == 9222

    def test_extra_keys_ignored(self):
        from config.model import AppConfig
        cfg = AppConfig(**{"unknown_key": "value"})  # Should not raise
        assert cfg.archiver.limit == 1000

    def test_invalid_nested_value_raises(self):
        from config.model import AppConfig
        with pytest.raises(ValidationError):
            AppConfig(archiver={"split_mode": "bogus"})

    def test_to_dict_roundtrip(self):
        from config.model import AppConfig, ArchiverConfig
        cfg = AppConfig(archiver=ArchiverConfig(limit=42))
        d = cfg.model_dump()
        assert d["archiver"]["limit"] == 42
        cfg2 = AppConfig(**d)
        assert cfg2.archiver.limit == 42
