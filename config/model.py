# config/model.py
"""Pydantic models for all config sections."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ArchiverConfig(BaseModel):
    """Settings: config.yaml → archiver section."""
    limit: int = 1000
    split_mode: Literal["auto", "on", "off", "prompt"] = "auto"
    split_threshold_mb: int = Field(default=49, ge=1)
    large_file_threshold_mb: int = Field(default=50, ge=1)
    use_local_browser: bool = False
    output_dir: str = "./temp"
    retries: int = 3
    retry_delay: int = 10
    repo_delay: int = 30


class BrowserConfig(BaseModel):
    """Settings: config.yaml → browser section."""
    cdp_port: int = 9222
    profile_name: str = "Default"
    user_data_dir: str = ""


class ChannelsConfig(BaseModel):
    """Settings: config.yaml → channels section."""
    max: str = ""
    pypi: str = ""
    media: str = ""
    backup: str = ""
    npm: str = ""
    cargo: str = ""
    nuget: str = ""
    rubygems: str = ""


class BackuperConfig(BaseModel):
    """Settings: config.yaml → backuper section."""
    compression_level: str = "5"
    default_volume_size: str = "49M"
    seven_zip_exe: str = "C:\\Program Files\\7-Zip\\7z.exe"
    download_dir: str = "./restored"
    output_dir: str = "./temp_backups"
    page_size: int = 10
    retries: int = 3
    retry_delay: int = 10
    # Upload-as-is mode settings
    upload_as_is_extensions: list[str] = Field(default_factory=list)  # Empty = all extensions
    upload_as_is_max_size_mb: int = 0  # 0 = no limit
    upload_as_is_recursive: bool = True


class ChannelDownloaderConfig(BaseModel):
    """Settings: config.yaml → channel_downloader section."""
    output_dir: str = "./downloads"
    retries: int = 3
    retry_delay: int = 5


class MediaExtensionsConfig(BaseModel):
    """Nested model for media_archiver.extensions."""
    images: list[str] = Field(default_factory=lambda: [
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"
    ])
    videos: list[str] = Field(default_factory=lambda: [
        ".mp4", ".mov", ".avi", ".mkv", ".webm"
    ])


class MediaArchiverConfig(BaseModel):
    """Settings: config.yaml → media_archiver section."""
    watch_dir: str = ""
    extensions: MediaExtensionsConfig = MediaExtensionsConfig()
    use_local_browser: bool = False
    retries: int = 3
    retry_delay: int = 10


class PyPILibsArchiverConfig(BaseModel):
    """Settings: config.yaml → pypi_libs_archiver section."""
    limit: int = 20
    output_dir: str = "./temp_pypi_libs"
    retries: int = 3
    retry_delay: int = 10
    split_mode: Literal["auto", "on", "off", "prompt"] = "auto"


class NpmArchiverConfig(BaseModel):
    """Settings: config.yaml → npm_archiver section."""
    limit: int = 20
    output_dir: str = "./temp_npm"
    retries: int = 3
    retry_delay: int = 10
    split_mode: Literal["auto", "on", "off", "prompt"] = "auto"


class CargoArchiverConfig(BaseModel):
    """Settings: config.yaml → cargo_archiver section."""
    limit: int = 500
    output_dir: str = "./temp_cargo"
    retries: int = 3
    retry_delay: int = 10
    split_mode: Literal["auto", "on", "off", "prompt"] = "off"


class NuGetArchiverConfig(BaseModel):
    """Settings: config.yaml → nuget_archiver section."""
    limit: int = 500
    output_dir: str = "./temp_nuget"
    retries: int = 3
    retry_delay: int = 10
    split_mode: Literal["auto", "on", "off", "prompt"] = "off"


class RubyGemsArchiverConfig(BaseModel):
    """Settings: config.yaml → rubygems_archiver section."""
    limit: int = 500
    output_dir: str = "./temp_rubygems"
    retries: int = 3
    retry_delay: int = 10
    split_mode: Literal["auto", "on", "off", "prompt"] = "off"


class SetupConfig(BaseModel):
    """Settings: config.yaml → setup section."""
    skipped_channels: list[str] = Field(default_factory=list)


class BatchConfig(BaseModel):
    """Settings: config.yaml → batch section.

    Controls how multiple archivers run in parallel via multiprocessing.
    """
    max_concurrent: int = Field(default=1, ge=1, le=8)
    timeout_seconds: int = Field(default=7200, ge=60)


class GitHubConfig(BaseModel):
    """Settings: config.yaml → github section.
    Token is typically empty in YAML and comes from GITHUB_TOKEN env var."""
    token: str = ""


VALID_CHANNEL_FUNCTIONS = ("github", "pypi", "media", "backup", "npm", "cargo", "nuget", "rubygems")


class ChannelEntry(BaseModel):
    """Single channel entry in the registry."""
    url: str = Field(min_length=1)
    label: str = ""
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("Channel URL must start with http:// or https://")
        return v


class ChannelRegistry(BaseModel):
    """Registry of channels per function. Replaces flat channels.{key} = url."""
    github: list[ChannelEntry] = Field(default_factory=list)
    pypi: list[ChannelEntry] = Field(default_factory=list)
    media: list[ChannelEntry] = Field(default_factory=list)
    backup: list[ChannelEntry] = Field(default_factory=list)
    npm: list[ChannelEntry] = Field(default_factory=list)
    cargo: list[ChannelEntry] = Field(default_factory=list)
    nuget: list[ChannelEntry] = Field(default_factory=list)
    rubygems: list[ChannelEntry] = Field(default_factory=list)

    def get_enabled(self, function: str) -> list[ChannelEntry]:
        """Return enabled channels for a function."""
        if function not in VALID_CHANNEL_FUNCTIONS:
            raise ValueError(f"Invalid function: {function}. Must be one of {VALID_CHANNEL_FUNCTIONS}")
        channels = getattr(self, function, [])
        return [ch for ch in channels if ch.enabled]

    def has_channels(self, function: str) -> bool:
        """Check if a function has any channels configured."""
        channels = getattr(self, function, [])
        return len(channels) > 0

    def toggle_channel(self, function: str, index: int) -> None:
        """Toggle enabled state of a channel."""
        channels = getattr(self, function, [])
        if 0 <= index < len(channels):
            channels[index].enabled = not channels[index].enabled

    def remove_channel(self, function: str, index: int) -> None:
        """Remove a channel by index."""
        channels = getattr(self, function, [])
        if 0 <= index < len(channels):
            del channels[index]

    def add_channel(self, function: str, url: str, label: str = "") -> None:
        """Add a new channel entry."""
        channels = getattr(self, function, [])
        channels.append(ChannelEntry(url=url, label=label))


class AppConfig(BaseModel):
    """Root config model — composes all section models.
    Every section is optional (defaults to its own defaults)."""
    archiver: ArchiverConfig = ArchiverConfig()
    browser: BrowserConfig = BrowserConfig()
    channels: ChannelsConfig = ChannelsConfig()
    backuper: BackuperConfig = BackuperConfig()
    channel_downloader: ChannelDownloaderConfig = ChannelDownloaderConfig()
    media_archiver: MediaArchiverConfig = MediaArchiverConfig()
    pypi_libs_archiver: PyPILibsArchiverConfig = PyPILibsArchiverConfig()
    npm_archiver: NpmArchiverConfig = NpmArchiverConfig()
    cargo_archiver: CargoArchiverConfig = CargoArchiverConfig()
    nuget_archiver: NuGetArchiverConfig = NuGetArchiverConfig()
    rubygems_archiver: RubyGemsArchiverConfig = RubyGemsArchiverConfig()
    setup: SetupConfig = SetupConfig()
    batch: BatchConfig = BatchConfig()
    github: GitHubConfig = GitHubConfig()
    channel_registry: ChannelRegistry = Field(default_factory=ChannelRegistry)

    model_config = {"extra": "ignore"}  # Silently ignore unknown YAML keys

    def clear_legacy_channels(self) -> None:
        """Clear legacy channels.* fields after migration to prevent re-migration."""
        self.channels.max = ""
        self.channels.pypi = ""
        self.channels.media = ""
        self.channels.backup = ""
        self.channels.npm = ""
        self.channels.cargo = ""
        self.channels.nuget = ""
        self.channels.rubygems = ""

    def save(self) -> None:
        """Persist config to YAML file.

        Uses _config_path_attr set by config/__init__.py during init_config().
        """
        import yaml
        config_path = getattr(self, "_config_path_attr", None)
        if config_path is None:
            config_path = "config.yaml"
        data = self.model_dump()
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
