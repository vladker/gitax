# config/model.py
"""Pydantic models for all config sections."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ArchiverConfig(BaseModel):
    """Settings: config.yaml → archiver section."""
    limit: int = 1000
    split_mode: Literal["auto", "on", "off", "prompt"] = "auto"
    split_threshold_mb: int = Field(default=49, ge=1)
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


class SetupConfig(BaseModel):
    """Settings: config.yaml → setup section."""
    skipped_channels: list[str] = Field(default_factory=list)


class GitHubConfig(BaseModel):
    """Settings: config.yaml → github section.
    Token is typically empty in YAML and comes from GITHUB_TOKEN env var."""
    token: str = ""


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
    setup: SetupConfig = SetupConfig()
    github: GitHubConfig = GitHubConfig()

    model_config = {"extra": "ignore"}  # Silently ignore unknown YAML keys
