"""Tests for ChannelEntry and ChannelRegistry models."""

import pytest
from pydantic import ValidationError


class TestChannelEntry:
    def test_defaults(self):
        from config.model import ChannelEntry
        entry = ChannelEntry(url="https://web.max.ru/test")
        assert entry.url == "https://web.max.ru/test"
        assert entry.label == ""
        assert entry.enabled is True

    def test_custom_label(self):
        from config.model import ChannelEntry
        entry = ChannelEntry(url="https://web.max.ru/test", label="My Channel")
        assert entry.label == "My Channel"

    def test_disabled(self):
        from config.model import ChannelEntry
        entry = ChannelEntry(url="https://web.max.ru/test", enabled=False)
        assert entry.enabled is False

    def test_url_required(self):
        from config.model import ChannelEntry
        with pytest.raises(ValidationError):
            ChannelEntry(url="")

    def test_url_must_be_http(self):
        from config.model import ChannelEntry
        with pytest.raises(ValidationError):
            ChannelEntry(url="not-a-url")


class TestChannelRegistry:
    def test_defaults_empty(self):
        from config.model import ChannelRegistry
        reg = ChannelRegistry()
        assert reg.github == []
        assert reg.pypi == []
        assert reg.media == []
        assert reg.backup == []

    def test_add_entry(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1", label="GitHub Main"))
        assert len(reg.github) == 1
        assert reg.github[0].label == "GitHub Main"

    def test_get_enabled(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1", enabled=True))
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch2", enabled=False))
        enabled = reg.get_enabled("github")
        assert len(enabled) == 1
        assert enabled[0].url == "https://web.max.ru/ch1"

    def test_get_enabled_empty(self):
        from config.model import ChannelRegistry
        reg = ChannelRegistry()
        assert reg.get_enabled("github") == []

    def test_get_enabled_invalid_function(self):
        from config.model import ChannelRegistry
        reg = ChannelRegistry()
        with pytest.raises(ValueError):
            reg.get_enabled("invalid")

    def test_toggle_channel(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1", enabled=True))
        reg.toggle_channel("github", 0)
        assert reg.github[0].enabled is False
        reg.toggle_channel("github", 0)
        assert reg.github[0].enabled is True

    def test_remove_channel(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1"))
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch2"))
        reg.remove_channel("github", 0)
        assert len(reg.github) == 1
        assert reg.github[0].url == "https://web.max.ru/ch2"

    def test_has_channels(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        assert not reg.has_channels("github")
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1"))
        assert reg.has_channels("github")

    def test_from_dict_roundtrip(self):
        from config.model import ChannelRegistry
        data = {
            "github": [
                {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": False},
            ],
            "pypi": [
                {"url": "https://web.max.ru/pypi1", "label": "PyPI Main"},
            ],
        }
        reg = ChannelRegistry(**data)
        assert len(reg.github) == 2
        assert reg.github[0].label == "Main"
        assert reg.github[1].enabled is False
        assert len(reg.pypi) == 1
        assert reg.media == []
        assert reg.backup == []
