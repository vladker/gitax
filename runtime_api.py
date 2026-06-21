#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Runtime API — version checking and installer download URLs for all runtimes.

Each archiver can check if its runtime has a newer version and download
installers for all OS targets (Windows, macOS, Linux).

Usage:
    from runtime_api import RuntimeFactory
    runtime = RuntimeFactory.get_runtime("pypi")  # → PythonRuntime
    latest = runtime.get_latest_version()         # → "3.13.2"
    urls = runtime.get_download_urls(latest)      # → [{os, url, filename, ...}]
"""

from __future__ import annotations

import re
import requests
from abc import ABC, abstractmethod
from enum import Enum

from logging_config import LogMixin


class OSTarget(str, Enum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"


class RuntimeAPI(LogMixin, ABC):
    """Base class for runtime version checking and download URL resolution."""

    name: str = ""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "GitHubArchiver/1.0 (RuntimeChecker)"
        })

    @abstractmethod
    def get_latest_version(self) -> str:
        """Fetch the latest stable version from official sources."""
        pass

    @abstractmethod
    def get_download_urls(self, version: str) -> list[dict[str, str]]:
        """
        Get download URLs for all OS targets.

        Returns list of dicts:
            {
                "os": OSTarget,
                "url": str,
                "filename": str,
                "size_hint": str,
            }
        """
        pass

    def _parse_version(self, version_str: str) -> tuple[int, ...]:
        """Parse version string into comparable tuple. Fallback to (0,) on error."""
        try:
            parts = re.findall(r'\d+', version_str)
            return tuple(int(p) for p in parts) if parts else (0,)
        except (ValueError, TypeError):
            return (0,)

    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare two version strings. Returns -1, 0, or 1."""
        t1 = self._parse_version(v1)
        t2 = self._parse_version(v2)
        if t1 < t2:
            return -1
        elif t1 > t2:
            return 1
        return 0


# ── Concrete Runtimes ──────────────────────────────────────────────


class PythonRuntime(RuntimeAPI):
    """Python runtime — version from GitHub releases, downloads from python.org."""

    name = "python"
    _github_releases = "https://api.github.com/repos/python/cpython/releases"

    def get_latest_version(self) -> str:
        """Get latest stable Python version from GitHub releases."""
        try:
            resp = self.session.get(self._github_releases, timeout=30)
            resp.raise_for_status()
            releases = resp.json()
            for rel in releases:
                if rel.get("prerelease") or rel.get("draft"):
                    continue
                tag = rel.get("tag_name", "")
                m = re.match(r'v(\d+\.\d+\.\d+)', tag)
                if m:
                    return m.group(1)
            self.logger.warning("No stable Python release found")
            return ""
        except Exception as e:
            self.logger.error(f"Failed to fetch Python version: {e}")
            return ""

    def get_download_urls(self, version: str) -> list[dict[str, str]]:
        """Get Python installer URLs for all OS targets."""
        return [
            {
                "os": OSTarget.WINDOWS,
                "url": f"https://www.python.org/ftp/python/{version}/python-{version}-amd64.exe",
                "filename": f"python-{version}-amd64.exe",
                "size_hint": "~25 MB",
            },
            {
                "os": OSTarget.MACOS,
                "url": f"https://www.python.org/ftp/python/{version}/python-{version}-macos11.pkg",
                "filename": f"python-{version}-macos11.pkg",
                "size_hint": "~28 MB",
            },
            {
                "os": OSTarget.LINUX,
                "url": f"https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz",
                "filename": f"Python-{version}.tar.xz",
                "size_hint": "~20 MB",
            },
        ]


class RustRuntime(RuntimeAPI):
    """Rust runtime — version from GitHub releases, downloads from rustup."""

    name = "rust"
    _github_releases = "https://api.github.com/repos/rust-lang/rust/releases"

    def get_latest_version(self) -> str:
        """Get latest stable Rust version from GitHub releases."""
        try:
            resp = self.session.get(self._github_releases, timeout=30)
            resp.raise_for_status()
            releases = resp.json()
            for rel in releases:
                if rel.get("prerelease") or rel.get("draft"):
                    continue
                tag = rel.get("tag_name", "")
                m = re.match(r'(\d+\.\d+\.\d+)', tag)
                if m:
                    return m.group(1)
            self.logger.warning("No stable Rust release found")
            return ""
        except Exception as e:
            self.logger.error(f"Failed to fetch Rust version: {e}")
            return ""

    def get_download_urls(self, version: str) -> list[dict[str, str]]:
        """Get full offline Rust distribution URLs for all OS targets.

        Downloads the complete toolchain (rustc, cargo, stdlib) from
        static.rust-lang.org — no internet required during installation.
        Each archive is 300–500 MB.
        """
        base = f"https://static.rust-lang.org/dist/rust-{version}"
        return [
            {
                "os": OSTarget.WINDOWS,
                "url": f"{base}-x86_64-pc-windows-msvc.tar.gz",
                "filename": f"rust-{version}-x86_64-pc-windows-msvc.tar.gz",
                "size_hint": "~400 MB",
            },
            {
                "os": OSTarget.WINDOWS,
                "url": f"{base}-x86_64-pc-windows-gnu.tar.gz",
                "filename": f"rust-{version}-x86_64-pc-windows-gnu.tar.gz",
                "size_hint": "~400 MB",
            },
            {
                "os": OSTarget.MACOS,
                "url": f"{base}-x86_64-apple-darwin.tar.gz",
                "filename": f"rust-{version}-x86_64-apple-darwin.tar.gz",
                "size_hint": "~350 MB",
            },
            {
                "os": OSTarget.MACOS,
                "url": f"{base}-aarch64-apple-darwin.tar.gz",
                "filename": f"rust-{version}-aarch64-apple-darwin.tar.gz",
                "size_hint": "~350 MB",
            },
            {
                "os": OSTarget.LINUX,
                "url": f"{base}-x86_64-unknown-linux-gnu.tar.gz",
                "filename": f"rust-{version}-x86_64-unknown-linux-gnu.tar.gz",
                "size_hint": "~300 MB",
            },
            {
                "os": OSTarget.LINUX,
                "url": f"{base}-aarch64-unknown-linux-gnu.tar.gz",
                "filename": f"rust-{version}-aarch64-unknown-linux-gnu.tar.gz",
                "size_hint": "~300 MB",
            },
        ]


class DotNetRuntime(RuntimeAPI):
    """.NET SDK runtime — version from Microsoft releases API."""

    name = "dotnet"
    _lts_releases_url = (
        "https://api.dotnet.microsoft.com/download/dotnet/"
        "release-metadata/lts-releases.json"
    )

    def get_latest_version(self) -> str:
        """Get latest LTS .NET version from Microsoft releases API."""
        try:
            resp = self.session.get(self._lts_releases_url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data:
                return data[0].get("channel-version", "")
            self.logger.warning("No .NET LTS release found")
            return ""
        except Exception as e:
            self.logger.error(f"Failed to fetch .NET version: {e}")
            return ""

    def get_download_urls(self, version: str) -> list[dict[str, str]]:
        """Get .NET SDK installer URLs for all OS targets."""
        major = version.split(".")[0] if version else "9"
        return [
            {
                "os": OSTarget.WINDOWS,
                "url": f"https://dotnet.microsoft.com/download/dotnet/{major}",
                "filename": f"dotnet-sdk-{version}-win-x64.exe",
                "size_hint": "~150 MB",
            },
            {
                "os": OSTarget.MACOS,
                "url": f"https://dotnet.microsoft.com/download/dotnet/{major}",
                "filename": f"dotnet-sdk-{version}-osx-x64.pkg",
                "size_hint": "~140 MB",
            },
            {
                "os": OSTarget.LINUX,
                "url": f"https://dotnet.microsoft.com/download/dotnet/{major}",
                "filename": f"dotnet-sdk-{version}-linux-x64.tar.gz",
                "size_hint": "~130 MB",
            },
        ]


class RubyRuntime(RuntimeAPI):
    """Ruby runtime — version from GitHub releases, downloads from ruby-lang."""

    name = "ruby"
    _github_releases = "https://api.github.com/repos/ruby/ruby/releases"

    def get_latest_version(self) -> str:
        """Get latest stable Ruby version from GitHub releases."""
        try:
            resp = self.session.get(self._github_releases, timeout=30)
            resp.raise_for_status()
            releases = resp.json()
            for rel in releases:
                if rel.get("prerelease") or rel.get("draft"):
                    continue
                tag = rel.get("tag_name", "")
                m = re.match(r'v(\d+\.\d+\.\d+)', tag)
                if m:
                    return m.group(1)
            self.logger.warning("No stable Ruby release found")
            return ""
        except Exception as e:
            self.logger.error(f"Failed to fetch Ruby version: {e}")
            return ""

    def get_download_urls(self, version: str) -> list[dict[str, str]]:
        """Get Ruby installer URLs for all OS targets."""
        major_minor = ".".join(version.split(".")[:2]) if version else "3.4"
        return [
            {
                "os": OSTarget.WINDOWS,
                "url": (
                    f"https://github.com/oneclick-ruby/rubyinstaller-directories/"
                    f"releases/download/{version}/"
                    f"rubyinstaller-{version}-x64.exe"
                ),
                "filename": f"rubyinstaller-{version}-x64.exe",
                "size_hint": "~80 MB",
            },
            {
                "os": OSTarget.MACOS,
                "url": (
                    f"https://cache.ruby-lang.org/pub/ruby/{major_minor}/"
                    f"ruby-{version}-universal-darwin.tar.gz"
                ),
                "filename": f"ruby-{version}-universal-darwin.tar.gz",
                "size_hint": "~30 MB",
            },
            {
                "os": OSTarget.LINUX,
                "url": (
                    f"https://cache.ruby-lang.org/pub/ruby/{major_minor}/"
                    f"ruby-{version}.tar.gz"
                ),
                "filename": f"ruby-{version}.tar.gz",
                "size_hint": "~15 MB",
            },
        ]


class GitRuntime(RuntimeAPI):
    """Git runtime — version from GitHub releases, downloads from git-scm."""

    name = "git"
    _github_releases = "https://api.github.com/repos/git/git/releases"

    def get_latest_version(self) -> str:
        """Get latest stable Git version from GitHub releases."""
        try:
            resp = self.session.get(self._github_releases, timeout=30)
            resp.raise_for_status()
            releases = resp.json()
            for rel in releases:
                if rel.get("prerelease") or rel.get("draft"):
                    continue
                tag = rel.get("tag_name", "")
                m = re.match(r'v(\d+\.\d+\.\d+)', tag)
                if m and ".windows" not in tag:
                    return m.group(1)
            self.logger.warning("No stable Git release found")
            return ""
        except Exception as e:
            self.logger.error(f"Failed to fetch Git version: {e}")
            return ""

    def get_download_urls(self, version: str) -> list[dict[str, str]]:
        """Get Git installer URLs for all OS targets."""
        return [
            {
                "os": OSTarget.WINDOWS,
                "url": (
                    f"https://github.com/git-for-windows/git/releases/"
                    f"download/v{version}.windows.1/"
                    f"Git-{version}-64-bit.exe"
                ),
                "filename": f"Git-{version}-64-bit.exe",
                "size_hint": "~50 MB",
            },
            {
                "os": OSTarget.MACOS,
                "url": (
                    f"https://github.com/git/git/releases/download/v{version}/"
                    f"git-{version}-intel-universal-macos.pkg"
                ),
                "filename": f"git-{version}-intel-universal-macos.pkg",
                "size_hint": "~40 MB",
            },
            {
                "os": OSTarget.LINUX,
                "url": (
                    f"https://github.com/git/git/archive/refs/tags/"
                    f"v{version}.tar.gz"
                ),
                "filename": f"git-{version}.tar.gz",
                "size_hint": "~10 MB",
            },
        ]


class NodeJSRuntime(RuntimeAPI):
    """Node.js runtime — version from GitHub releases, downloads from nodejs.org."""

    name = "nodejs"
    _github_releases = "https://api.github.com/repos/nodejs/node/releases/latest"

    def get_latest_version(self) -> str:
        """Get latest LTS Node.js version from GitHub releases."""
        try:
            resp = self.session.get(self._github_releases, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            tag = data.get("tag_name", "")
            if tag.startswith("v"):
                return tag[1:]
            return tag
        except Exception:
            try:
                resp = self.session.get(
                    "https://nodejs.org/dist/index.json", timeout=30
                )
                resp.raise_for_status()
                releases = resp.json()
                for rel in releases:
                    if rel.get("lts"):
                        return rel.get("version", "").lstrip("v")
            except Exception:
                pass
        return ""

    def get_download_urls(self, version: str) -> list[dict[str, str]]:
        """Get download URLs for Node.js installers."""
        base = f"https://nodejs.org/dist/v{version}"
        return [
            {
                "os": OSTarget.WINDOWS,
                "url": f"{base}/node-v{version}-x64.msi",
                "filename": f"node-v{version}-x64.msi",
                "size_hint": "~30 MB",
            },
            {
                "os": OSTarget.MACOS,
                "url": f"{base}/node-v{version}.pkg",
                "filename": f"node-v{version}.pkg",
                "size_hint": "~30 MB",
            },
            {
                "os": OSTarget.LINUX,
                "url": f"{base}/node-v{version}-linux-x64.tar.xz",
                "filename": f"node-v{version}-linux-x64.tar.xz",
                "size_hint": "~25 MB",
            },
        ]


# ── Factory ────────────────────────────────────────────────────────

CHANNEL_TO_RUNTIME: dict[str, type[RuntimeAPI]] = {
    "pypi": PythonRuntime,
    "cargo": RustRuntime,
    "nuget": DotNetRuntime,
    "rubygems": RubyRuntime,
    "github": GitRuntime,
    "max": GitRuntime,
}

RUNTIME_ICON: dict[str, str] = {
    "python": "🐍",
    "rust": "🦀",
    "dotnet": "⚡",
    "ruby": "💎",
    "git": "📦",
    "nodejs": "🟢",
}

RUNTIME_DISPLAY: dict[str, str] = {
    "python": "Python",
    "rust": "Rust",
    "dotnet": ".NET",
    "ruby": "Ruby",
    "git": "Git",
    "nodejs": "Node.js",
}

RUNTIME_DOWNLOAD_PAGE: dict[str, str] = {
    "python": "python.org/downloads",
    "rust": "www.rust-lang.org/tools/install",
    "dotnet": "dotnet.microsoft.com/download",
    "ruby": "ruby-lang.org/en/downloads",
    "git": "git-scm.com/downloads",
    "nodejs": "nodejs.org/en/download",
}


class RuntimeFactory:
    """Factory for obtaining the correct RuntimeAPI for a channel."""

    @staticmethod
    def get_runtime(channel_key: str) -> RuntimeAPI:
        """
        Get the appropriate runtime API for a channel key.

        Args:
            channel_key: Channel identifier
                         ("pypi", "cargo", "nuget", "rubygems", "github")

        Returns:
            RuntimeAPI instance

        Raises:
            ValueError: If channel_key is not recognized
        """
        runtime_cls = CHANNEL_TO_RUNTIME.get(channel_key)
        if runtime_cls is None:
            raise ValueError(
                f"Unknown channel key: {channel_key}. "
                f"Supported: {', '.join(sorted(CHANNEL_TO_RUNTIME.keys()))}"
            )
        return runtime_cls()

    @staticmethod
    def get_runtime_by_name(runtime_name: str) -> RuntimeAPI:
        """Get runtime by its name attribute."""
        name_map: dict[str, type[RuntimeAPI]] = {
            "python": PythonRuntime,
            "rust": RustRuntime,
            "dotnet": DotNetRuntime,
            "ruby": RubyRuntime,
            "git": GitRuntime,
            "nodejs": NodeJSRuntime,
        }
        runtime_cls = name_map.get(runtime_name)
        if runtime_cls is None:
            raise ValueError(
                f"Unknown runtime name: {runtime_name}. "
                f"Supported: {', '.join(sorted(name_map.keys()))}"
            )
        return runtime_cls()

    @staticmethod
    def get_icon(runtime_name: str) -> str:
        """Get emoji icon for a runtime."""
        return RUNTIME_ICON.get(runtime_name, "📦")

    @staticmethod
    def get_display_name(runtime_name: str) -> str:
        """Get display name for a runtime."""
        return RUNTIME_DISPLAY.get(runtime_name, runtime_name)

    @staticmethod
    def get_download_page(runtime_name: str) -> str:
        """Get download page URL for a runtime."""
        return RUNTIME_DOWNLOAD_PAGE.get(runtime_name, "")
