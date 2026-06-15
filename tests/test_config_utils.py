# -*- coding: utf-8 -*-
"""
Tests for config_utils — set_env_value, is_setup_complete, ensure_channel_url
"""

import os
import pytest


class TestSetEnvValue:
    """Tests for set_env_value() — .env file manipulation"""

    def test_set_env_value_new_key(self, tmp_path, monkeypatch):
        """Add a new key to an existing .env file"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        env_file = tmp_path / ".env"
        env_file.write_text("# Existing config\nEXISTING_KEY=old_value\n", encoding="utf-8")

        set_env_value("NEW_KEY", "new_value")

        content = env_file.read_text(encoding="utf-8")
        assert "NEW_KEY=new_value" in content
        assert "EXISTING_KEY=old_value" in content
        assert os.environ.get("NEW_KEY") == "new_value"

    def test_set_env_value_update_key(self, tmp_path, monkeypatch):
        """Update an existing key in .env"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        env_file = tmp_path / ".env"
        env_file.write_text("MY_KEY=old_value\n", encoding="utf-8")

        set_env_value("MY_KEY", "new_value")

        content = env_file.read_text(encoding="utf-8")
        assert "MY_KEY=new_value" in content
        assert "MY_KEY=old_value" not in content
        assert os.environ.get("MY_KEY") == "new_value"

    def test_set_env_value_new_file(self, tmp_path, monkeypatch):
        """Create .env from scratch if it doesn't exist"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        assert not (tmp_path / ".env").exists()

        set_env_value("FRESH_KEY", "fresh_value")

        assert (tmp_path / ".env").exists()
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "FRESH_KEY=fresh_value" in content
        assert os.environ.get("FRESH_KEY") == "fresh_value"

    def test_set_env_value_preserve_comments(self, tmp_path, monkeypatch):
        """Preserve comments and blank lines in .env"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        original = (
            "# This is a header comment\n"
            "\n"
            "# GitHub token\n"
            "GITHUB_TOKEN=old_token\n"
            "\n"
            "# Channel URLs\n"
            "CHANNEL_max=https://old.url\n"
        )
        env_file = tmp_path / ".env"
        env_file.write_text(original, encoding="utf-8")

        set_env_value("GITHUB_TOKEN", "new_token")

        content = env_file.read_text(encoding="utf-8")
        assert "# This is a header comment" in content
        assert "# GitHub token" in content
        assert "# Channel URLs" in content
        assert "\n\n" in content
        assert "GITHUB_TOKEN=new_token" in content
        assert "GITHUB_TOKEN=old_token" not in content

    def test_set_env_value_same_value(self, tmp_path, monkeypatch):
        """Setting the same value does not corrupt the file"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\n", encoding="utf-8")

        set_env_value("KEY", "value")

        content = env_file.read_text(encoding="utf-8")
        assert content.strip() == "KEY=value"

    def test_set_env_value_with_spaces_around_equals(self, tmp_path, monkeypatch):
        """Handle KEY = value format with spaces around ="""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        env_file = tmp_path / ".env"
        env_file.write_text("KEY = old_value\n", encoding="utf-8")

        set_env_value("KEY", "new_value")

        content = env_file.read_text(encoding="utf-8")
        assert "KEY = new_value" in content


class TestIsSetupComplete:
    """Tests for is_setup_complete()"""

    def test_all_values_present_via_env(self, monkeypatch):
        """Returns True when all values are set via env vars"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("CHANNEL_max", "https://max.example.com/max")
        monkeypatch.setenv("CHANNEL_pypi", "https://max.example.com/pypi")
        monkeypatch.setenv("CHANNEL_media", "https://max.example.com/media")
        monkeypatch.setenv("CHANNEL_backup", "https://max.example.com/backup")
        monkeypatch.setenv("CHANNEL_npm", "https://max.example.com/npm")
        monkeypatch.setenv("CHANNEL_cargo", "https://max.example.com/cargo")
        monkeypatch.setenv("CHANNEL_nuget", "https://max.example.com/nuget")
        monkeypatch.setenv("CHANNEL_rubygems", "https://max.example.com/rubygems")

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is True

    def test_all_values_present_via_config(self, monkeypatch):
        """Returns True when channels are in config dict"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.delenv("CHANNEL_max", raising=False)
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        monkeypatch.delenv("CHANNEL_media", raising=False)
        monkeypatch.delenv("CHANNEL_backup", raising=False)
        monkeypatch.delenv("CHANNEL_npm", raising=False)
        monkeypatch.delenv("CHANNEL_cargo", raising=False)
        monkeypatch.delenv("CHANNEL_nuget", raising=False)
        monkeypatch.delenv("CHANNEL_rubygems", raising=False)

        config = {
            "channels": {
                "max": "https://max.example.com/max",
                "pypi": "https://max.example.com/pypi",
                "media": "https://max.example.com/media",
                "backup": "https://max.example.com/backup",
                "npm": "https://max.example.com/npm",
                "cargo": "https://max.example.com/cargo",
                "nuget": "https://max.example.com/nuget",
                "rubygems": "https://max.example.com/rubygems",
            }
        }

        from config_utils import is_setup_complete
        assert is_setup_complete(config) is True

    def test_missing_token(self, monkeypatch):
        """Returns False when GITHUB_TOKEN is missing"""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("CHANNEL_max", "url")
        monkeypatch.setenv("CHANNEL_pypi", "url")
        monkeypatch.setenv("CHANNEL_media", "url")
        monkeypatch.setenv("CHANNEL_backup", "url")

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is False

    def test_missing_one_channel(self, monkeypatch):
        """Returns False when one CHANNEL_* is missing"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("CHANNEL_max", "url")
        monkeypatch.setenv("CHANNEL_pypi", "url")
        monkeypatch.setenv("CHANNEL_media", "url")
        monkeypatch.delenv("CHANNEL_backup", raising=False)

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is False

    def test_missing_all_channels(self, monkeypatch):
        """Returns False when no channels are set"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.delenv("CHANNEL_max", raising=False)
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        monkeypatch.delenv("CHANNEL_media", raising=False)
        monkeypatch.delenv("CHANNEL_backup", raising=False)

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is False

    def test_empty_strings_not_valid(self, monkeypatch):
        """Empty string values don't count as configured"""
        monkeypatch.setenv("GITHUB_TOKEN", "")
        monkeypatch.setenv("CHANNEL_max", "url")
        monkeypatch.setenv("CHANNEL_pypi", "url")
        monkeypatch.setenv("CHANNEL_media", "url")
        monkeypatch.setenv("CHANNEL_backup", "url")

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is False

    def test_partial_config_via_both_sources(self, monkeypatch):
        """Mix of env and config dict sources"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("CHANNEL_max", "url_from_env")
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        monkeypatch.delenv("CHANNEL_media", raising=False)
        monkeypatch.delenv("CHANNEL_backup", raising=False)
        monkeypatch.delenv("CHANNEL_npm", raising=False)
        monkeypatch.delenv("CHANNEL_cargo", raising=False)
        monkeypatch.delenv("CHANNEL_nuget", raising=False)
        monkeypatch.delenv("CHANNEL_rubygems", raising=False)

        config = {
            "channels": {
                "pypi": "url_from_config",
                "media": "url_from_config",
                "backup": "url_from_config",
                "npm": "url_from_config",
                "cargo": "url_from_config",
                "nuget": "url_from_config",
                "rubygems": "url_from_config",
            }
        }

        from config_utils import is_setup_complete
        assert is_setup_complete(config) is True

    def test_complete_with_skipped_channels(self, monkeypatch):
        """Returns True when missing channels are explicitly skipped"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("CHANNEL_max", "url")
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        monkeypatch.delenv("CHANNEL_media", raising=False)
        monkeypatch.delenv("CHANNEL_backup", raising=False)
        monkeypatch.delenv("CHANNEL_npm", raising=False)
        monkeypatch.delenv("CHANNEL_cargo", raising=False)
        monkeypatch.delenv("CHANNEL_nuget", raising=False)
        monkeypatch.delenv("CHANNEL_rubygems", raising=False)

        config = {
            "setup": {
                "skipped_channels": ["pypi", "media", "backup", "npm", "cargo", "nuget", "rubygems"]
            }
        }

        from config_utils import is_setup_complete
        assert is_setup_complete(config) is True

    def test_complete_with_all_channels_skipped(self, monkeypatch):
        """Returns True when ALL channels are explicitly skipped"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.delenv("CHANNEL_max", raising=False)
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        monkeypatch.delenv("CHANNEL_media", raising=False)
        monkeypatch.delenv("CHANNEL_backup", raising=False)
        monkeypatch.delenv("CHANNEL_npm", raising=False)
        monkeypatch.delenv("CHANNEL_cargo", raising=False)
        monkeypatch.delenv("CHANNEL_nuget", raising=False)
        monkeypatch.delenv("CHANNEL_rubygems", raising=False)

        config = {
            "setup": {
                "skipped_channels": ["max", "pypi", "media", "backup", "npm", "cargo", "nuget", "rubygems"]
            }
        }

        from config_utils import is_setup_complete
        assert is_setup_complete(config) is True

    def test_incomplete_with_skipped_but_missing_token(self, monkeypatch):
        """Returns False when token is missing even if channels skipped"""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        config = {
            "setup": {
                "skipped_channels": ["max", "pypi", "media", "backup"]
            }
        }

        from config_utils import is_setup_complete
        assert is_setup_complete(config) is False

    def test_incomplete_with_partial_skip_and_missing(self, monkeypatch):
        """Returns False when non-skipped channel is missing URL"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("CHANNEL_max", "url")
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        monkeypatch.delenv("CHANNEL_media", raising=False)
        monkeypatch.delenv("CHANNEL_backup", raising=False)

        config = {
            "setup": {
                "skipped_channels": ["pypi"]  # media and backup not skipped but missing
            }
        }

        from config_utils import is_setup_complete
        assert is_setup_complete(config) is False


class TestGetSkippedChannels:
    """Tests for get_skipped_channels()"""

    def test_no_skipped(self):
        """Returns empty list when no setup section"""
        from config_utils import get_skipped_channels
        assert get_skipped_channels({}) == []

    def test_empty_skipped_list(self):
        """Returns empty list when skipped_channels is empty"""
        from config_utils import get_skipped_channels
        assert get_skipped_channels({"setup": {"skipped_channels": []}}) == []

    def test_some_skipped(self):
        """Returns list of skipped channel names"""
        from config_utils import get_skipped_channels
        config = {"setup": {"skipped_channels": ["pypi", "media"]}}
        result = get_skipped_channels(config)
        assert "pypi" in result
        assert "media" in result
        assert "max" not in result
        assert "backup" not in result


class TestGetSplitMode:
    """Tests for get_split_mode()"""

    def test_returns_auto_by_default(self):
        from config_utils import get_split_mode
        assert get_split_mode({}, "archiver") == "auto"

    def test_reads_from_config(self):
        from config_utils import get_split_mode
        config = {"archiver": {"split_mode": "prompt"}}
        assert get_split_mode(config, "archiver") == "prompt"

    def test_fallback_to_default(self):
        from config_utils import get_split_mode
        assert get_split_mode({}, "pypi_libs_archiver", default="off") == "off"

    def test_invalid_value_falls_back_to_default(self):
        from config_utils import get_split_mode
        config = {"archiver": {"split_mode": "invalid"}}
        assert get_split_mode(config, "archiver") == "auto"

    def test_none_value_falls_back(self):
        from config_utils import get_split_mode
        config = {"archiver": {"split_mode": None}}
        assert get_split_mode(config, "archiver") == "auto"

    def test_case_insensitive(self):
        from config_utils import get_split_mode
        config = {"archiver": {"split_mode": "ON"}}
        assert get_split_mode(config, "archiver") == "on"
