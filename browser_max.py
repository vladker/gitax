# -*- coding: utf-8 -*-
"""
MAX messenger automation using Playwright
"""

import os
import re
import time
import sys
import csv
import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PlaywrightTimeout
from logging_config import LogMixin, setup_logging


# 7z volume size (49MB to leave buffer for CDN limits)
SEVEN_ZIP_VOLUME_SIZE = "49M"

# Module-level logger for standalone functions
_logger = logging.getLogger("gitax")

# 7-Zip executable path (Windows default)
SEVEN_ZIP_EXE = "C:\\Program Files\\7-Zip\\7z.exe"


@dataclass
class ContentSnapshot:
    """Content snapshot with hash for change detection."""
    hash: str
    file_count: int


def split_file_with_7z(filepath: str, volume_size: str = SEVEN_ZIP_VOLUME_SIZE) -> list[str]:
    """
    Split a file into volumes using 7z.

    Args:
        filepath: Path to file to split
        volume_size: Volume size (e.g., "49M", "100M"). Default: 49M

    Returns:
        List of volume file paths (e.g., ['file.7z.001', 'file.7z.002', ...])
        Returns empty list if split failed or file is small enough.
    """
    if not os.path.exists(filepath):
        return []

    file_size = os.path.getsize(filepath)
    volume_bytes = _parse_size(volume_size)

    # No split needed if file is smaller than threshold
    if file_size <= volume_bytes:
        return []

    filename = os.path.basename(filepath)
    output_base = filepath + ".7z"

    # Remove any existing volumes with same base
    _cleanup_existing_volumes(output_base)

    cmd = [
        SEVEN_ZIP_EXE,
        "a",
        "-v" + volume_size,  # Volume size (e.g., -v49m)
        "-mx=0",             # No compression (faster, raw split)
        output_base,
        filepath
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour max for large files
        )

        if result.returncode != 0:
            _logger.warning(f"7z split failed: {result.stderr}")
            _cleanup_existing_volumes(output_base)
            return []

        # Find created volumes
        volumes = _find_volumes(output_base)

        if volumes:
            _logger.info(f"Split into {len(volumes)} volumes: {volumes[0]}...")
            return volumes
        else:
            _logger.warning("7z succeeded but no volumes found")
            return []

    except subprocess.TimeoutExpired:
        _logger.error("7z split timeout")
        _cleanup_existing_volumes(output_base)
        return []
    except FileNotFoundError:
        _logger.error(f"7z not found at {SEVEN_ZIP_EXE}")
        return []
    except Exception as e:
        _logger.error(f"7z split error: {e}")
        _cleanup_existing_volumes(output_base)
        return []


def _parse_size(size_str: str) -> int:
    """Parse size string like '49M' or '1G' to bytes"""
    size_str = size_str.upper().strip()
    multipliers = {
        'K': 1024,
        'M': 1024 * 1024,
        'G': 1024 * 1024 * 1024
    }

    for suffix, mult in multipliers.items():
        if size_str.endswith(suffix):
            try:
                return int(float(size_str[:-1]) * mult)
            except ValueError:
                pass

    # Plain number
    try:
        return int(size_str)
    except ValueError:
        return 0


def _cleanup_existing_volumes(base_path: str):
    """Remove any existing volume files matching base pattern"""
    import glob
    pattern = base_path + ".*"
    for f in glob.glob(pattern):
        try:
            os.remove(f)
        except Exception:
            pass


def _find_volumes(base_path: str) -> list[str]:
    """Find all volume files matching base.7z.xxx pattern, sorted"""
    import glob
    pattern = base_path + ".*"
    volumes = sorted(glob.glob(pattern))
    return volumes


def cleanup_volumes(volume_paths: list[str]):
    """Remove volume files after successful upload"""
    for vp in volume_paths:
        try:
            if os.path.exists(vp):
                os.remove(vp)
                _logger.debug(f"Removed: {vp}")
        except Exception as e:
            _logger.warning(f"Failed to remove {vp}: {e}")


def group_volumes(filenames: list[str]) -> list[dict]:
    """
    Group 7z volume files by base archive name.

    Groups:
    - "documents.7z.001", "documents.7z.002" -> base "documents.7z"
    - "photos.7z" -> base "photos.7z"

    Args:
        filenames: List of 7z-related filenames

    Returns:
        List of dicts: [{"base_name": "docs.7z", "volume_count": 3,
                         "volumes": ["docs.7z.001", ...]}, ...]
    """
    import re
    groups: dict[str, list[str]] = {}
    for fn in filenames:
        m = re.match(r'^(.+\.7z)(\.\d+)?$', fn)
        if m:
            base = m.group(1)
            groups.setdefault(base, []).append(fn)
    result = []
    for base, volumes in groups.items():
        result.append({
            "base_name": base,
            "volume_count": len(volumes),
            "volumes": sorted(volumes),
        })
    return result


def archive_directory_to_volumes(
    source_dir: str,
    output_base: str,
    volume_size: str = SEVEN_ZIP_VOLUME_SIZE,
    compression_level: int = 5,
    password: str | None = None,
    clean_existing: bool = True
) -> list[str]:
    """
    Archive an entire directory into 7z volumes with compression and optional password.

    Unlike split_file_with_7z() which does raw split (-mx=0), this creates
    proper compressed 7z archives with encryption.

    Args:
        source_dir: Path to directory to archive
        output_base: Base path for output (e.g., "./temp/name.7z")
        volume_size: Volume size string (e.g., "49M"). None for single archive.
        compression_level: 7z compression level 0-9 (default 5)
        password: Optional encryption password
        clean_existing: Remove existing volumes before archiving

    Returns:
        List of volume file paths, or empty list on failure.
    """
    if not os.path.isdir(source_dir):
        _logger.error(f"Source directory not found: {source_dir}")
        return []
    if not os.path.exists(SEVEN_ZIP_EXE):
        _logger.error(f"7z not found at {SEVEN_ZIP_EXE}")
        return []
    if clean_existing:
        _cleanup_existing_volumes(output_base)
    cmd = [SEVEN_ZIP_EXE, "a", f"-mx={compression_level}", output_base, source_dir + os.sep]
    if volume_size:
        cmd.insert(2, "-v" + volume_size)
    if password:
        cmd.insert(2, f"-p{password}")
        cmd.insert(2, "-mhe=on")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if result.returncode != 0:
            _logger.warning(f"7z archive failed: {result.stderr}")
            if clean_existing:
                _cleanup_existing_volumes(output_base)
            return []
        volumes = _find_volumes(output_base)
        if not volumes and not volume_size:
            single = output_base if output_base.endswith('.7z') else output_base + '.7z'
            if os.path.exists(single):
                volumes = [single]
        if volumes:
            total_size = sum(os.path.getsize(v) for v in volumes if os.path.exists(v))
            _logger.info(
                f"Archived {source_dir} -> {len(volumes)} volume(s), "
                f"total {total_size / 1024 / 1024:.1f} MB"
            )
            return volumes
        else:
            _logger.warning("7z succeeded but no output files found")
            return []
    except subprocess.TimeoutExpired:
        _logger.error("7z archive timeout")
        _cleanup_existing_volumes(output_base)
        return []
    except FileNotFoundError:
        _logger.error(f"7z not found at {SEVEN_ZIP_EXE}")
        return []
    except Exception as e:
        _logger.error(f"7z archive error: {e}")
        _cleanup_existing_volumes(output_base)
        return []


class BrowserMAXError(Exception):
    """Base exception for BrowserMAX errors"""
    pass


class ConnectionError(BrowserMAXError):
    """Failed to connect to Chrome"""
    pass


class UploadError(BrowserMAXError):
    """Failed to upload file"""
    pass


class UploadInProgressError(BrowserMAXError):
    """Raised when navigation is attempted during active upload"""
    pass


class ElementNotFoundError(BrowserMAXError):
    """Required element not found"""
    pass


class BrowserMAX(LogMixin):
    """MAX messenger automation using Playwright"""

    # Class-level reference to the active playwright instance.
    # sync_playwright() shares a single asyncio event loop per process —
    # if one BrowserMAX starts it, another must reuse or stop it first.
    _active_playwright = None

    # Media type extension sets — used to adapt upload timeouts & confirmation
    VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    ARCHIVE_EXTENSIONS = {'.zip', '.tar', '.gz', '.7z', '.rar', '.tar.gz', '.whl'}

    @staticmethod
    def _classify_media(filepath: str) -> str:
        """
        Classify a file by its extension into a media type.

        Used to adapt upload timeouts and confirmation logic.
        Returns one of: 'video', 'image', 'archive', 'other'.

        Args:
            filepath: Path to the file (can be relative or absolute)

        Returns:
            'video' | 'image' | 'archive' | 'other'
        """
        basename = os.path.basename(filepath).lower()
        # Check compound extensions first (.tar.gz, .whl)
        if basename.endswith('.tar.gz') or basename.endswith('.whl'):
            return 'archive'
        ext = os.path.splitext(filepath)[1].lower()
        if ext in BrowserMAX.VIDEO_EXTENSIONS:
            return 'video'
        if ext in BrowserMAX.IMAGE_EXTENSIONS:
            return 'image'
        if ext in BrowserMAX.ARCHIVE_EXTENSIONS:
            return 'archive'
        return 'other'

    def __init__(self, channel_url: str, use_local_browser: bool = False):
        """
        Initialize MAX browser automation.

        Args:
            channel_url: MAX channel URL
            use_local_browser: If True, launch new Chrome. If False, connect to existing browser via CDP.
                              Default is False (connect to existing) for seamless UX.
        """
        self.channel_url = channel_url
        self.use_local_browser = use_local_browser
        self.playwright = None
        self.browser: Optional[Browser] = None
        self._context = None  # BrowserContext (used for persistent context mode)
        self.page: Optional[Page] = None
        self._connected = False
        self._expected_extensions = ['.zip']

        # Upload state management — blocks destructive navigation during upload
        self._upload_in_progress = False
        self._upload_file_size = 0
        self._upload_file_name = ""
        self._is_video = False

    def _lock_upload_state(self, filepath: str) -> None:
        """
        Lock upload guards — marks an upload as in progress.

        Sets flags that block destructive navigation/reload during upload.
        Must be called before file chooser opens.
        Must be paired with _unlock_upload_state() in a finally block.

        Args:
            filepath: Absolute path to the file being uploaded
        """
        self._upload_in_progress = True
        self._upload_file_size = os.path.getsize(filepath)
        self._upload_file_name = os.path.basename(filepath)
        self._is_video = self._classify_media(filepath) == "video"
        self.logger.info(
            f"Upload locked: {self._upload_file_name} "
            f"({'video' if self._is_video else 'file'}, "
            f"{self._upload_file_size / 1024 / 1024:.1f} MB)"
        )

    def _unlock_upload_state(self) -> None:
        """Unlock upload guards — marks upload as complete."""
        self._upload_in_progress = False
        self._upload_file_size = 0
        self._upload_file_name = ""
        self._is_video = False
        self.logger.debug("Upload unlocked")

    def _can_navigate(self) -> bool:
        """
        Check if navigation is safe (no upload in progress).

        Returns:
            True if navigation is allowed, False if upload in progress
        """
        return not self._upload_in_progress

    @classmethod
    def _stop_existing_playwright(cls) -> None:
        """
        Stop any existing playwright event loop shared across BrowserMAX instances.
        Must be called before sync_playwright().start() to avoid
        "Sync API inside asyncio loop" errors.
        """
        if cls._active_playwright is not None:
            try:
                cls._active_playwright.stop()
            except Exception:
                pass
            cls._active_playwright = None

    @staticmethod
    def _launch_chrome_cdp():
        """Launch Chrome with remote debugging port 9222"""
        import socket

        # Check if port 9222 is already in use
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", 9222)) == 0:
                _logger.debug("Port 9222 already in use, skipping launch")
                return

        _logger.info("Port 9222 not available, launching Chrome with remote debugging...")

        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome SxS\Application\chrome.exe",
        ]

        chrome_exe = None
        for path in chrome_paths:
            expanded = os.path.expandvars(path)
            if os.path.exists(expanded):
                chrome_exe = expanded
                break

        if not chrome_exe:
            _logger.error("Chrome executable not found")
            return

        user_data_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome", "User Data")

        cmd = [
            chrome_exe,
            "--remote-debugging-port=9222",
            f"--user-data-dir={user_data_dir}",
        ]

        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _logger.info(f"Launched Chrome: {chrome_exe}")
            time.sleep(3)
        except Exception as e:
            _logger.error(f"Failed to launch Chrome: {e}")

    def _get_user_data_dir(self) -> str:
        """
        Get Chrome user data directory path.

        Reads browser.user_data_dir from config if set, otherwise falls back to
        the default Chrome profile directory.

        Returns:
            Full path to Chrome user data directory with profile.
        """
        import yaml

        # Try to read from config.yaml
        user_data_dir = ""
        profile_name = "Default"

        try:
            config_path = "config.yaml"
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
                browser_config = config.get('browser', {})
                user_data_dir = browser_config.get('user_data_dir', '')
                profile_name = browser_config.get('profile_name', 'Default')
        except Exception:
            pass

        # Fallback to default Chrome directory
        if not user_data_dir:
            user_data_dir = os.path.join(
                os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome", "User Data"
            )

        return os.path.join(user_data_dir, profile_name)

    def _disconnect_cdp(self) -> None:
        """
        Disconnect from CDP browser gracefully.
        Closes page and browser connection without killing the Chrome process.
        """
        try:
            if self.page:
                try:
                    self.page.close()
                except Exception:
                    pass
                self.page = None
            if self.browser:
                try:
                    self.browser.close()
                except Exception:
                    pass
                self.browser = None
            self._connected = False
            self.logger.info("Disconnected from CDP")
        except Exception as e:
            self.logger.warning(f"Error during CDP disconnect: {e}")
            self.page = None
            self.browser = None
            self._connected = False

    def _launch_with_profile(self) -> bool:
        """
        Launch a local Chromium browser using the same user profile.
        Used for large file uploads that exceed CDP's 50MB transfer limit.

        Stops the existing playwright instance first to avoid
        "Sync API inside asyncio loop" errors on subsequent connect() calls.

        Returns:
            True if launch succeeded, False otherwise.
        """
        try:
            user_data_dir = self._get_user_data_dir()
            self.logger.info(f"Launching local Chrome with profile: {user_data_dir}")

            # Stop existing playwright (instance-level and class-level) to clear
            # the asyncio event loop. Without this, sync_playwright().start() fails.
            self._stop_existing_playwright()
            if self.playwright:
                try:
                    self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None

            # Start fresh playwright for local browser
            self.playwright = sync_playwright().start()
            self.__class__._active_playwright = self.playwright

            self.browser = self.playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=[
                    '--disable-blink-features=Automation'
                ],
                viewport={'width': 1200, 'height': 900}
            )
            self.page = self.browser.new_page()

            # Install API interceptor before navigation
            self._install_api_interceptor()

            self._connected = True
            self.logger.info("Local Chrome launched successfully")
            return True
        except Exception as e:
            self.logger.error(f"Failed to launch local Chrome: {e}")
            return False

    def _close_local_browser(self) -> None:
        """
        Close the locally-launched Chromium browser (kills the process).
        Stops playwright to clear the asyncio event loop so connect() works later.
        Includes delay to allow lock file to release.
        """
        try:
            if self.page:
                try:
                    self.page.close()
                except Exception:
                    pass
                self.page = None
            # For persistent context mode, close the context (which also closes the browser)
            if self._context:
                try:
                    self._context.close()
                except Exception:
                    pass
                self._context = None
            if self.browser:
                try:
                    self.browser.close()
                except Exception:
                    pass
                self.browser = None
            self._connected = False
            self.logger.info("Local browser closed")
        except Exception as e:
            self.logger.warning(f"Error closing local browser: {e}")
            self.page = None
            self._context = None
            self.browser = None
            self._connected = False
        finally:
            # Stop playwright to clear its asyncio event loop.
            # Without this, the next sync_playwright().start() in connect() fails with:
            # "It looks like you are using Playwright Sync API inside the asyncio loop."
            if self.playwright:
                try:
                    self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            # Clear class-level reference so other BrowserMAX instances can start fresh
            self.__class__._active_playwright = None
            # Allow lock file to release
            time.sleep(2)

    def _upload_large_file(self, filepath: str, filename: str, file_size_bytes: int,
                           retries: int = 3, retry_delay: int = 10,
                           baseline_count: int = 0,
                           expected_extensions: list[str] | None = None) -> bool:
        """
        Upload a large file (>50MB).

        When use_local_browser is True (persistent context mode):
        - Uploads directly using the existing browser session (no switching needed).

        When use_local_browser is False (CDP mode):
        - Disconnects from CDP, launches local Chrome, uploads, reconnects.

        Args:
            filepath: Absolute path to file
            filename: Display name for the file
            file_size_bytes: File size in bytes
            retries: Number of retries per upload attempt
            retry_delay: Delay between retries (seconds)
            baseline_count: Message count baseline for upload confirmation
            expected_extensions: List of file extensions to match for confirmation.
                                If None, auto-detected from filename.

        Returns:
            True if upload succeeded, False otherwise.
        """
        file_size_mb = file_size_bytes / 1024 / 1024
        self.logger.info(f"Large file detected ({file_size_mb:.1f} MB)")

        # Set expected extensions so _scan_messages_for_file can find the file
        if expected_extensions is not None:
            self._expected_extensions = expected_extensions
        else:
            import os as _os
            ext = _os.path.splitext(filename)[1].lower()
            self._expected_extensions = [ext]

        # If already in persistent context mode, upload directly — no browser switch needed
        if self.use_local_browser:
            self.logger.info(f"Uploading large file directly (persistent context mode)")
            self.navigate()
            self.ensure_page_ready()

            self._pre_upload_msg_count = self.page.evaluate(
                "() => document.querySelectorAll('[class*=\"message\"]').length"
            ) or 0
            self.logger.info(f"Pre-upload message count (large file): {self._pre_upload_msg_count}")

            success = self._upload_single_file(
                filepath, filename, file_size_bytes,
                retries=retries, retry_delay=retry_delay,
                baseline_count=self._pre_upload_msg_count
            )

            return success

        # CDP mode: switch to local browser for large files
        self.logger.info("Switching to local browser for large file upload")
        try:
            # Step 1: Disconnect from CDP
            self._disconnect_cdp()
            time.sleep(1)  # Let connection settle

            # Step 2: Launch local Chrome with same profile
            if not self._launch_with_profile():
                self.logger.error("Failed to launch local browser for large file upload")
                # Attempt recovery
                try:
                    self.connect()
                except Exception:
                    pass
                return False

            # Step 3: Navigate to MAX channel
            self.navigate()
            self.ensure_page_ready()

            # Capture message baseline for upload confirmation
            self._pre_upload_msg_count = self.page.evaluate(
                "() => document.querySelectorAll('[class*=\"message\"]').length"
            ) or 0
            self.logger.info(f"Pre-upload message count (large file): {self._pre_upload_msg_count}")

            # Step 4: Upload the file
            success = self._upload_single_file(
                filepath, filename, file_size_bytes,
                retries=retries, retry_delay=retry_delay,
                baseline_count=self._pre_upload_msg_count
            )

            # Step 5: Close local Chrome
            self._close_local_browser()
            time.sleep(2)  # Let lock file release

            # Step 6: Reconnect to CDP.
            # NOTE: no navigate() here — the CDP page retains its URL after reconnect.
            # Navigating reloads the page, which is visually disruptive and can
            # interfere with subsequent uploads in the caller's loop.
            try:
                self.connect()
            except Exception as e:
                self.logger.warning(f"CDP reconnect after large upload: {e}")

            return success

        except Exception as e:
            self.logger.error(f"Large file upload failed: {e}")
            # Recovery: close local browser and reconnect CDP
            try:
                self._close_local_browser()
            except Exception:
                pass
            try:
                self.connect()
            except Exception:
                pass
            return False

    def connect(self) -> bool:
        """Connect to Chrome - local browser or via CDP"""
        self.logger.info(f"Connecting to Chrome (local={self.use_local_browser})...")

        try:
            # Stop any existing playwright event loop before starting a new one.
            # This prevents "Sync API inside asyncio loop" errors when
            # multiple BrowserMAX instances share the same process.
            self._stop_existing_playwright()
            self.playwright = sync_playwright().start()
            self.__class__._active_playwright = self.playwright

            if self.use_local_browser:
                # Launch local Chromium with user's profile (preserves cookies/session)
                user_data_dir = self._get_user_data_dir()
                self.logger.info(f"Launching local Chromium with profile: {user_data_dir}")
                self._context = self.playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=False,
                    args=['--disable-blink-features=Automation'],
                    viewport={'width': 1200, 'height': 900}
                )
                self.page = self._context.new_page()
                # For persistent context, self.browser is None — context IS the top-level object
                self.browser = None
            else:
                # Use CDP connection to existing Chrome (must be open at channel_url)
                # This preserves existing browser state and cookies
                self.logger.info("Connecting via CDP (port 9222) to existing browser...")
                connected = False
                for attempt in range(1, 4):
                    try:
                        self.browser = self.playwright.chromium.connect_over_cdp(
                            "http://127.0.0.1:9222",
                            timeout=30000
                        )
                        connected = True
                        break
                    except Exception as e:
                        self.logger.warning(f"CDP attempt {attempt}/3 failed: {e}")
                        if attempt < 3:
                            wait_time = attempt * 3  # 3s, 6s, 9s backoff
                            self.logger.info(f"Retrying in {wait_time}s...")
                            time.sleep(wait_time)

                if not connected:
                    self.logger.warning("All CDP attempts failed")
                    self.logger.warning("Chrome SxS/Canary may be incompatible with Playwright CDP.")
                    self.logger.warning("Set archiver.use_local_browser=true in config.yaml to bypass CDP.")

                if not connected:
                    self.logger.error("Failed to connect to MAX via CDP after all retries")
                    self.playwright.stop()
                    self.playwright = None
                    self.__class__._active_playwright = None
                    return False

                context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
                self.page = context.pages[0] if context.pages else context.new_page()

            # Install API response interceptor before any navigation
            self._install_api_interceptor()

            self._connected = True
            self.logger.info("Connected successfully")
            return True
        except Exception as e:
            self.logger.error(f"Connection failed: {e}", exc_info=True)
            # Clean up playwright on any exception to prevent event loop poisoning
            if self.playwright:
                try:
                    self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
                self.__class__._active_playwright = None
            return False

    def keep_alive_connect(self) -> bool:
        """Connect to Chrome and stay connected. Use this for multiple operations."""
        # Persistent context mode: self.browser is None, self._context holds the context
        has_browser = self.browser is not None or self._context is not None
        if has_browser and self.page:
            self.logger.debug("Already connected (reusing connection)")
            return True
        return self.connect()

    def ensure_page_ready(self):
        """Ensure page is loaded and ready for interaction"""
        if not self.page:
            raise ConnectionError("Not connected. Call keep_alive_connect() first.")

        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            self.logger.warning("Page load timeout, continuing anyway")
        except Exception as e:
            self.logger.warning(f"Unexpected error waiting for page: {e}")

    def navigate(self):
        """Navigate to MAX channel"""
        # GUARD: block navigation during active upload
        if self._upload_in_progress:
            self.logger.warning(
                f"BLOCKED navigate() during upload: {self._upload_file_name}"
            )
            raise UploadInProgressError(
                f"Cannot navigate during upload: {self._upload_file_name}"
            )

        self.logger.info(f"Opening channel: {self.channel_url}")

        # Reconnect if needed
        if not self._ensure_alive():
            raise ConnectionError("Failed to connect to Chrome")

        try:
            self.page.goto(self.channel_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
        except Exception as e:
            self.logger.warning(f"Navigation error: {e}, reconnecting...")
            self._connected = False
            self.page = None
            if not self._ensure_alive():
                raise ConnectionError("Failed to reconnect to Chrome")
            self.page.goto(self.channel_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

    def _try_navigate(self) -> bool:
        """
        Safely navigate to MAX channel. Reconnects if needed.
        Returns True if navigation succeeded.
        """
        # GUARD: block navigation during active upload
        if self._upload_in_progress:
            self.logger.warning(
                f"BLOCKED _try_navigate() during upload: {self._upload_file_name}"
            )
            return False

        if not self._ensure_alive():
            return False
        try:
            self.page.goto(self.channel_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
            return True
        except Exception as e:
            self.logger.warning(f"Navigation failed: {e}")
            return False

    def wait_page_ready(self, timeout: int = 30):
        """Wait for page to be ready"""
        if not self._ensure_alive():
            return

        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout * 1000)
        except PlaywrightTimeout:
            self.logger.warning(f"Page ready timeout after {timeout}s")
        except Exception as e:
            self.logger.warning(f"Unexpected error: {e}")

    def _check_connection(self):
        """Verify page is available and alive"""
        if not self.page:
            raise ConnectionError("Not connected. Call connect() first.")
        if self.page.is_closed():
            self.logger.warning("Page was closed, reconnecting...")
            self._connected = False
            self.page = None
            raise ConnectionError("Page was closed, need to reconnect")

    def _ensure_alive(self) -> bool:
        """
        Ensure connection is alive. Reconnect if needed.

        Returns:
            True if connected, False otherwise
        """
        # GUARD: during active upload, only check current state — do NOT reconnect
        if self._upload_in_progress:
            if self.page and not self.page.is_closed():
                return True
            self.logger.warning(
                f"Page lost during upload of {self._upload_file_name} — "
                f"cannot reconnect without destroying upload"
            )
            return False

        if not self.page:
            return self.connect()

        try:
            if self.page.is_closed():
                self.logger.warning("Page is closed, reconnecting...")
                self._connected = False
                self.page = None
                return self.connect()
            return True
        except Exception:
            return self.connect()

    def _safe_evaluate(self, script: str, default=None):
        """
        Safely evaluate JavaScript, reconnecting if page was closed.

        Args:
            script: JavaScript to evaluate
            default: Default value if evaluation fails

        Returns:
            Result of evaluation or default
        """
        if not self._ensure_alive():
            return default

        try:
            return self.page.evaluate(script)
        except Exception as e:
            self.logger.debug(f"Safe evaluate error: {e}")
            return default

    def _install_api_interceptor(self):
        """
        Install script that intercepts JSON API responses (fetch + XHR)
        and stores them in window.__gitax_api_responses.
        Called once before page navigation.
        """
        if not self.page:
            return
        try:
            self.page.add_init_script("""
                () => {
                    // Avoid double-install
                    if (window.__gitax_api_interceptor_installed) return;
                    window.__gitax_api_interceptor_installed = true;
                    window.__gitax_api_responses = [];

                    // Intercept fetch JSON responses
                    const origFetch = window.fetch.bind(window);
                    window.fetch = function(resource, init) {
                        return origFetch(resource, init).then(response => {
                            const ct = response.headers.get('content-type') || '';
                            if (ct.includes('json')) {
                                response.clone().json().then(body => {
                                    const url = typeof resource === 'string'
                                        ? resource
                                        : (resource && resource.url) || '';
                                    window.__gitax_api_responses.push({
                                        url: url,
                                        body: body,
                                        time: Date.now()
                                    });
                                }).catch(() => {});
                            }
                            return response;
                        });
                    };

                    // Intercept XMLHttpRequest JSON responses
                    const origOpen = XMLHttpRequest.prototype.open;
                    const origSend = XMLHttpRequest.prototype.send;
                    XMLHttpRequest.prototype.open = function(method, url) {
                        this._gitax_url = typeof url === 'string' ? url : (url ? '' + url : '');
                        return origOpen.apply(this, arguments);
                    };
                    XMLHttpRequest.prototype.send = function() {
                        if (this._gitax_url) {
                            this.addEventListener('load', function() {
                                if (this.readyState === 4) {
                                    const ct = this.getResponseHeader('content-type') || '';
                                    if (ct.includes('json')) {
                                        try {
                                            const body = JSON.parse(this.responseText);
                                            window.__gitax_api_responses.push({
                                                url: this._gitax_url,
                                                body: body,
                                                time: Date.now()
                                            });
                                        } catch(e) {}
                                    }
                                }
                            });
                        }
                        return origSend.apply(this, arguments);
                    };
                }
            """)
        except Exception as e:
            self.logger.debug(f"Failed to install API interceptor: {e}")

    def _extract_messages_from_body(self, body, depth: int = 0) -> list[dict]:
        """
        Recursively extract message-like objects from an API response body.
        Tries common field names and structures used by chat APIs.
        """
        if depth > 5:
            return []

        results = []

        if isinstance(body, dict):
            for key in body:
                val = body[key]

                # Try arrays of messages
                if isinstance(val, list) and key.lower() in (
                    'messages', 'items', 'data', 'results', 'list',
                    'entries', 'feed', 'response', 'records', 'rows',
                    'collection', 'elements', 'nodes', 'edges',
                ):
                    for item in val:
                        if isinstance(item, dict):
                            text = (
                                item.get('text') or item.get('content')
                                or item.get('body') or item.get('message')
                                or item.get('description') or item.get('caption')
                            )
                            if text and isinstance(text, str) and len(text) > 10:
                                results.append({
                                    'text': text,
                                    'html': item.get('html') or item.get('htmlContent')
                                            or item.get('rendered') or '',
                                })
                            else:
                                results.extend(self._extract_messages_from_body(item, depth + 1))
                else:
                    results.extend(self._extract_messages_from_body(val, depth + 1))

        elif isinstance(body, list):
            for item in body:
                if isinstance(item, dict):
                    text = (
                        item.get('text') or item.get('content')
                        or item.get('body') or item.get('message')
                        or item.get('description') or item.get('caption')
                    )
                    if text and isinstance(text, str) and len(text) > 10:
                        results.append({
                            'text': text,
                            'html': item.get('html') or item.get('htmlContent')
                                    or item.get('rendered') or '',
                        })

        return results

    def _parse_messages_from_api(self) -> list[dict]:
        """
        Extract messages from captured API responses.
        Returns list of dicts in the same format as collect_all_messages()
        or empty list if API data is unavailable/unparseable.
        """
        try:
            raw = self.page.evaluate("() => window.__gitax_api_responses || []")
        except Exception:
            return []

        if not raw:
            return []

        # Log captured URLs for debugging
        seen_urls = set()
        for entry in raw:
            url = entry.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                self.logger.info(f"[API] Captured response: {url[:200]}")

        messages = []
        seen_sigs: set[str] = set()

        for entry in raw:
            body = entry.get('body', {})
            extracted = self._extract_messages_from_body(body)
            for msg in extracted:
                text = msg.get('text', '')
                sig = text[:120]
                if sig and sig not in seen_sigs:
                    seen_sigs.add(sig)
                    messages.append({
                        "idx": len(messages),
                        "text": text,
                        "html": msg.get('html', ''),
                        "classes": '',
                    })

        if messages:
            print(f"  [API] Извлечено {len(messages)} сообщений из {len(raw)} сетевых ответов")
            self.logger.info(f"Extracted {len(messages)} messages from {len(raw)} API responses")
        else:
            print(f"  [API] API-ответы не содержат сообщений ({len(raw)} ответов перехвачено)")
            self.logger.info(f"No messages found in {len(raw)} API responses")
            # Log first response keys for debugging
            if raw:
                first = raw[0].get('body', {})
                if isinstance(first, dict):
                    self.logger.info(f"First response keys: {list(first.keys())[:10]}")
                elif isinstance(first, list):
                    self.logger.info(f"First response is array of {len(first)} items")

        return messages

    def _collect_via_page_state(self) -> list[dict]:
        """
        Try to extract message data from the page's internal JavaScript state.
        Checks React fibers, Vue stores, and common global variables.
        All heavy lifting happens inside page.evaluate (JS context) where
        DOM objects are directly accessible. Returns only serializable data.

        Returns list of dicts (same format as collect_all_messages) or empty list.
        """
        try:
            raw = self.page.evaluate("""
                () => {
                    const results = [];

                    // Helper to extract messages from a state tree
                    function extractTexts(obj, depth) {
                        if (!obj || depth > 8) return [];
                        let texts = [];
                        if (Array.isArray(obj)) {
                            for (const item of obj) {
                                if (item && typeof item === 'object') {
                                    const t = item.text || item.content || item.body || item.message || '';
                                    if (typeof t === 'string' && t.length > 20) texts.push(t);
                                    texts = texts.concat(extractTexts(item, depth + 1));
                                }
                            }
                        } else if (obj && typeof obj === 'object') {
                            for (const key of Object.keys(obj)) {
                                const kl = key.toLowerCase();
                                if (['messages','items','feed','lenta','chat','history',
                                     'entries','results','data','records','rows',
                                     'collection','nodes','edges'].includes(kl)) {
                                    texts = texts.concat(extractTexts(obj[key], depth + 1));
                                }
                            }
                        }
                        return texts;
                    }

                    // 1) Common SSR / SPA initial state globals
                    const globals = [
                        '__INITIAL_STATE__', '__INITIAL_DATA__', '__DATA__',
                        '__STORE__', '__STATE__', '__APP_STATE__', '__DATA_STATE__',
                        '__INITIAL_PROPS__', '__NEXT_DATA__', '__NUXT__',
                        '___INITIAL_STATE___', 'window.__INITIAL_STORE__'
                    ];
                    for (const g of globals) {
                        try {
                            if (window[g] !== undefined) {
                                const texts = extractTexts(window[g], 0);
                                if (texts.length) {
                                    results.push({ source: g, data: texts });
                                }
                            }
                        } catch(e) {}
                    }

                    // 2) React fiber: walk the root fiber for cached message state
                    const roots = document.querySelectorAll('#root, #__next, #app, #__nuxt');
                    for (const root of roots) {
                        try {
                            const key = Object.keys(root).find(k => k.startsWith('__reactFiber$'));
                            if (!key || !root[key]) continue;

                            function walkFiber(fiber, depth) {
                                if (!fiber || depth > 30) return [];
                                let texts = [];
                                try {
                                    let state = fiber.memoizedState;
                                    while (state) {
                                        if (state.queue && state.queue.lastRenderedState) {
                                            const st = state.queue.lastRenderedState;
                                            if (st && typeof st === 'object') {
                                                texts = texts.concat(extractTexts(st, 0));
                                            }
                                        }
                                        state = state.next;
                                    }
                                    texts = texts.concat(walkFiber(fiber.child, depth + 1));
                                    texts = texts.concat(walkFiber(fiber.sibling, depth + 1));
                                } catch(e) {}
                                return texts;
                            }

                            const fiberTexts = walkFiber(root[key], 0);
                            if (fiberTexts.length) {
                                results.push({ source: 'reactFiber', data: fiberTexts });
                            }
                        } catch(e) {}
                    }

                    // 3) Vue app
                    try {
                        const app = document.getElementById('__nuxt') || document.getElementById('app');
                        if (app && app.__vue_app__) {
                            const state = app.__vue_app__.config.globalProperties;
                            const texts = extractTexts(state, 0);
                            if (texts.length) results.push({ source: 'vueApp', data: texts });
                        }
                    } catch(e) {}

                    // 4) Redux store (if exposed via window)
                    try {
                        if (window.__store__ && window.__store__.getState) {
                            const texts = extractTexts(window.__store__.getState(), 0);
                            if (texts.length) results.push({ source: 'redux', data: texts });
                        }
                    } catch(e) {}

                    return results;
                }
            """) or []
        except Exception as e:
            self.logger.debug(f"Page state extraction error: {e}")
            return []

        if not raw:
            return []

        messages = []
        seen_sigs: set[str] = set()

        for entry in raw:
            data = entry.get('data', [])
            source = entry.get('source', '')
            if isinstance(data, list):
                for text in data:
                    if isinstance(text, str) and text.strip():
                        sig = text[:120]
                        if sig not in seen_sigs:
                            seen_sigs.add(sig)
                            messages.append({
                                "idx": len(messages),
                                "text": text,
                                "html": '',
                                "classes": '',
                            })

        if messages:
            print(f"  [STATE] Извлечено {len(messages)} сообщений из состояния страницы ({len(raw)} источников)")
            self.logger.info(f"Extracted {len(messages)} messages from page state ({len(raw)} sources)")
        else:
            self.logger.info(f"No messages found in page state ({len(raw)} sources checked)")

        return messages

    def _scroll_to_bottom(self):
        """Scroll the feed to the bottom to trigger content reload."""
        self.page.evaluate("""
            () => {
                const c = window.__gitax_scroll;
                if (c) {
                    c.scrollTop = c.scrollHeight;
                    return true;
                }
                const containers = document.querySelectorAll(
                    '[class*="messages"],[class*="lenta"],[class*="feed"],' +
                    '[class*="chat"],[class*="dialog"],[class*="scroll"]'
                );
                for (const c2 of containers) {
                    if (c2.scrollHeight > c2.clientHeight + 50) {
                        c2.scrollTop = c2.scrollHeight;
                        return true;
                    }
                }
                window.scrollTo(0, document.body.scrollHeight);
                return true;
            }
        """)
        print("  [SCROLL] Скролл вниз для перезагрузки контента...")
        time.sleep(2)

    def get_message_count(self) -> int:
        """Get current message count in chat"""
        self._check_connection()
        try:
            count = self.page.evaluate(
                "() => document.querySelectorAll('[class*=\"message\"]').length"
            )
            return count or 0
        except Exception as e:
            self.logger.debug(f"Failed to get message count: {e}")
            return 0

    def _click_upload_button(self):
        """Find and click upload/file button - handles dropdown menu"""
        self._ensure_alive()
        self._check_connection()
        self.logger.info("Looking for upload button...")

        for selector in ['[aria-label="Upload file"]', 'button:has-text("File")']:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=3000):
                    btn.scroll_into_view_if_needed()
                    btn.click(timeout=5000)
                    self.logger.debug(f"Button clicked: {selector}")
                    time.sleep(0.5)

                    menu_btn = self.page.locator('button:has-text("File")').first
                    if menu_btn.is_visible(timeout=3000):
                        menu_btn.click()
                        self.logger.debug("File menu item clicked")
                        time.sleep(0.3)
                        return True
            except PlaywrightTimeout:
                self.logger.debug(f"Selector not found: {selector}")
                continue
            except Exception as e:
                self.logger.warning(f"Error with selector {selector}: {e}")
                continue

        try:
            upload_btn = self.page.locator('[aria-label="Upload file"]').first
            upload_btn.click()
            time.sleep(0.5)

            self.logger.info("Looking for File menu item...")
            file_btn = self.page.get_by_role("menuitem", name="File", exact=True).first
            file_btn.click(timeout=5000)
            self.logger.info("Upload > File clicked")
            return True
        except Exception as e:
            self.logger.warning(f"Menu click failed: {e}")

        return False

    def _validate_file(self, filepath: str) -> tuple[bool, str]:
        """Validate file before upload"""
        if not os.path.exists(filepath):
            return False, f"File not found: {filepath}"

        file_size = os.path.getsize(filepath)
        if file_size == 0:
            return False, "File is empty"

        if file_size > 2 * 1024 * 1024 * 1024:
            return False, f"File too large: {file_size / 1024 / 1024 / 1024:.1f} GB"

        filename = os.path.basename(filepath)
        self.logger.debug(f"File validated: {filename} ({file_size / 1024 / 1024:.1f} MB)")
        return True, ""

    def _upload_file(self, filepath: str) -> bool:
        """Upload file using input[type=file]"""
        valid, err = self._validate_file(filepath)
        if not valid:
            self.logger.error(f"File validation failed: {err}")
            return False

        abs_path = os.path.abspath(filepath)
        filename = os.path.basename(filepath)
        file_size_mb = os.path.getsize(abs_path) / 1024 / 1024

        self.logger.info(f"Selecting file: {filename} ({file_size_mb:.1f} MB)")

        for selector in ['input[type="file"]', 'input[type=file]']:
            try:
                file_input = self.page.locator(selector).first
                file_input.set_input_files(abs_path, timeout=60000)
                self.logger.info("File selected")
                return True
            except PlaywrightTimeout:
                self.logger.debug(f"Selector timeout: {selector}")
                continue
            except Exception as e:
                self.logger.warning(f"Error with selector {selector}: {e}")
                continue

        self.logger.error("Could not select file - no input found")
        return False

    def _upload_file_drag_drop(self, filepath: str) -> bool:
        """Upload file using file chooser"""
        self._check_connection()
        valid, err = self._validate_file(filepath)
        if not valid:
            self.logger.error(f"File validation failed: {err}")
            return False

        abs_path = os.path.abspath(filepath)
        filename = os.path.basename(filepath)
        file_size_mb = os.path.getsize(abs_path) / 1024 / 1024
        self.logger.info(f"Selecting file: {filename} ({file_size_mb:.1f} MB)")

        try:
            with self.page.expect_file_chooser(timeout=60000) as fc_info:
                self.logger.info("Waiting for file dialog...")

            file_chooser = fc_info.value
            file_chooser.set_files(abs_path)
            self.logger.info("File selected via dialog")
            return True
        except PlaywrightTimeout:
            self.logger.error("File chooser timeout")
            return False
        except Exception as e:
            self.logger.error(f"File chooser failed: {e}")
            return False

    def _install_upload_observer(self, expected_filename: str | None = None, expected_size: int | None = None) -> Optional[str]:
        """
        Install MutationObserver to track file upload.
        Returns unique observer ID for tracking.

        Args:
            expected_filename: Filename to match (e.g., "repo-master.zip")
            expected_size: File size in bytes to match for verification
        """
        self._check_connection()
        upload_id = f"gitax_{int(time.time() * 1000)}"
        window_key = "gitax_upload_done"

        # Build size pattern for matching (e.g., "388.2 MB" or "388 MB")
        size_pattern = ""
        if expected_size:
            if expected_size >= 1024 * 1024 * 1024:
                size_str = f"{expected_size / 1024 / 1024 / 1024:.1f} GB"
            elif expected_size >= 1024 * 1024:
                size_str = f"{expected_size / 1024 / 1024:.2f} MB"
            else:
                size_str = f"{expected_size / 1024:.1f} KB"
            size_pattern = size_str.replace(".", "\\.").replace(" ", "\\s*")

        # Build extension regex pattern for JS (e.g., "\\.zip" or "\\.tar\\.gz|\\.whl")
        ext_pattern = '|'.join(re.escape(ext) for ext in self._expected_extensions)

        script = f"""
            () => {{
                const id = '{upload_id}';
                const target = '{window_key}';
                const searchName = '{expected_filename or ''}';
                const sizePattern = '{size_pattern}';
                const expectedSize = {expected_size or 0};
                const extRegex = new RegExp('{ext_pattern}', 'i');

                window[target] = null;
                window[target + '_file'] = null;
                window[target + '_selector'] = null;

                const observer = new MutationObserver((mutations) => {{
                    for (const m of mutations) {{
                        if (m.type === 'childList') {{
                            for (const node of m.addedNodes) {{
                                if (node.nodeType !== 1) continue;

                                const text = node.textContent || '';
                                const className = node.className || '';

                                // Check if this looks like a file message
                                const hasFileClass = /file|attach|upload|preview|item/i.test(className);
                                const hasZip = extRegex.test(text);
                                const hasDownload = /скачать|download/i.test(text);

                                if (!hasFileClass && !hasZip && !hasDownload) continue;

                                // If we have search name, check for filename match
                                if (searchName) {{
                                    // Normalize for comparison (remove -master, -main)
                                    const normalizedSearch = searchName.toLowerCase()
                                        .replace('-master', '')
                                        .replace('-main', '');

                                    // Look for repo name pattern in text
                                    const textLower = text.toLowerCase();
                                    const hasRepoName = textLower.includes(normalizedSearch);

                                    // If no repo name match, skip unless this is a "Скачать" button
                                    if (!hasRepoName && !hasDownload) continue;
                                }}

                                // If we have size pattern, verify size matches
                                if (sizePattern) {{
                                    const sizeRegex = new RegExp(sizePattern, 'i');
                                    if (!sizeRegex.test(text)) continue;
                                }}

                                // Match found!
                                if (!window[target]) {{
                                    window[target] = Date.now();
                                    window[target + '_file'] = text.slice(0, 100);
                                    window[target + '_selector'] = id;
                                }}
                            }}
                        }}

                        if (m.type === 'attributes' && m.attributeName === 'src') {{
                            const src = m.target.src || '';
                            if (src.includes('file') || src.includes('upload') || src.includes('attach')) {{
                                if (!window[target]) {{
                                    window[target] = Date.now();
                                    window[target + '_file'] = src.split('/').pop() || 'unknown';
                                    window[target + '_selector'] = id;
                                }}
                            }}
                        }}
                    }}
                }});

                try {{
                    observer.observe(document.body, {{ childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'class'] }});
                    return id;
                }} catch (e) {{
                    return null;
                }}
            }}
        """

        try:
            result = self.page.evaluate(script)
            if result:
                self.logger.debug(f"Upload observer installed: {result}")
                return result
            return None
        except Exception as e:
            self.logger.warning(f"Failed to install observer: {e}")
            return None

    def _check_upload_done(self) -> tuple[bool, Optional[str]]:
        """
        Check if upload was completed via observer.

        Returns:
            (done: bool, filename: Optional[str])
        """
        script = """
            () => {
                const target = 'gitax_upload_done';
                if (window[target]) {
                    return {
                        done: true,
                        time: window[target],
                        file: window[target + '_file'] || 'unknown',
                        id: window[target + '_selector'] || null
                    };
                }
                return { done: false, time: null, file: null, id: null };
            }
        """

        try:
            result = self.page.evaluate(script)
            if result and result.get('done'):
                return True, result.get('file')
            return False, None
        except Exception as e:
            self.logger.debug(f"Check upload status error: {e}")
            return False, None

    def _capture_pre_upload_state(self) -> Optional[dict]:
        """
        Capture DOM state before upload to detect changes.
        """
        self._check_connection()
        script = """
            () => {
                const msgs = document.querySelectorAll('[class*="message"]');
                const lastMsg = msgs.length > 0 ? msgs[msgs.length - 1].innerHTML.slice(0, 500) : '';

                const attachments = document.querySelectorAll('[class*="attach"], [class*="file"], [class*="preview"]');
                const attachCount = attachments.length;

                const composer = document.querySelector('[class*="composer"], [class*="input"], [role="textbox"]');
                const composerHtml = composer ? composer.innerHTML.slice(0, 200) : '';

                return {
                    msgCount: msgs.length,
                    lastMsg: lastMsg,
                    attachCount: attachCount,
                    composerHtml: composerHtml
                };
            }
        """

        try:
            return self.page.evaluate(script)
        except Exception as e:
            self.logger.warning(f"Failed to capture state: {e}")
            return None

    def _detect_state_change(self, before: Optional[dict]) -> tuple[bool, str]:
        """
        Detect if DOM state changed (file uploaded).
        """
        if not before:
            return False, "no_baseline"

        script = """
            () => {
                const msgs = document.querySelectorAll('[class*="message"]');
                const newMsgs = document.querySelectorAll('[class*="message"]');
                const lastMsgNew = newMsgs.length > 0 ? (newMsgs[newMsgs.length - 1].textContent || '') : '';

                return {
                    msgCount: msgs.length,
                    lastMsgNew: lastMsgNew.slice(0, 100)
                };
            }
        """

        try:
            current = self.page.evaluate(script)

            changed = False
            reason = "none"

            if current.get('attachCount', 0) > before.get('attachCount', 0):
                changed = True
                reason = "new_attachment"

            composer_current = current.get('composerHtml', '') or ''
            composer_before = before.get('composerHtml', '') or ''
            if composer_current != composer_before:
                if 'file' in composer_current.lower() or 'preview' in composer_current.lower():
                    changed = True
                    reason = "composer_changed_with_file"

            if current.get('msgCount', 0) > before.get('msgCount', 0):
                changed = True
                reason = "new_message"

            if changed:
                return True, reason

            return False, reason
        except Exception as e:
            self.logger.debug(f"State change detection error: {e}")
            return False, "error"

    def _wait_upload_complete(self, timeout: int = 3600, poll_interval: float = 1.0,
                               expected_filename: str | None = None,
                               expected_size: int | None = None,
                               baseline_count: int | None = None) -> bool:
        """
        Wait for file upload to complete - uses PROGRESS-BASED detection.
        No fixed timeout - waits until upload is confirmed or user cancels.

        Args:
            timeout: Maximum seconds (default 1h, for huge files)
            poll_interval: How often to check (seconds)
            expected_filename: Filename to match for upload confirmation
            expected_size: File size in bytes to match
            baseline_count: Message count baseline — only check messages at or after
                            this index (to avoid false matches on old uploads).
        """
        self._check_connection()
        self.logger.info(f"Monitoring upload progress...")

        # Video: extend timeout to account for transcoding time
        if self._is_video and expected_size:
            video_mb = expected_size / (1024 * 1024)
            min_video_timeout = max(120, int(video_mb * 10))
            min_video_timeout = min(min_video_timeout, 1800)  # cap at 30 min
            if timeout < min_video_timeout:
                self.logger.info(
                    f"Extending timeout for video: {timeout}s -> {min_video_timeout}s"
                )
                timeout = min_video_timeout

        observer_id = self._install_upload_observer(expected_filename, expected_size)
        pre_state = self._capture_pre_upload_state()
        start = time.time()
        last_activity_time = start
        last_progress_log = start
        consecutive_no_activity = 0

        # Smaller files upload faster — reduce no-activity wait accordingly
        # For video files: NO no-activity exit (video transcoding can pause DOM updates)
        if self._is_video:
            no_activity_threshold = float('inf')  # never exit on no-activity for video
        elif expected_size and expected_size < 10 * 1024 * 1024:
            no_activity_threshold = 5   # 5 seconds for files < 10MB
        elif expected_size and expected_size < 50 * 1024 * 1024:
            no_activity_threshold = 10  # 10 seconds for files < 50MB
        else:
            no_activity_threshold = 45  # Increased from 30s to 45s for files > 50MB

        print(f"  [OK] Waiting for upload to complete... (Ctrl+C to cancel)")

        while time.time() - start < timeout:
            elapsed = int(time.time() - start)

            # Check for upload progress (progress bar, percentage, activity)
            progress_info = self._check_upload_progress()
            if progress_info:
                pct = progress_info.get('percent', 0)
                speed = progress_info.get('speed', '')
                remaining = progress_info.get('remaining', '')

                if pct > 0 and pct < 100:
                    last_activity_time = time.time()
                    consecutive_no_activity = 0

                    # Log progress every 10 seconds
                    if time.time() - last_progress_log >= 10:
                        print(f"\r  [UPLOAD] {pct}% {speed} {remaining}", end="", flush=True)
                        last_progress_log = time.time()

                elif pct >= 100:
                    print(f"\n  [OK] Upload reached 100%")
                    last_activity_time = time.time()

            # Check if file appeared in message feed via MutationObserver (NEW nodes only)
            done, filename = self._check_upload_done()
            if done:
                self.logger.info(f"Upload complete: {filename} ({elapsed}s)")
                return True

            # Check DOM for attached file in composer
            if self._check_dom_upload_ready():
                # Composer preview appears INSTANTLY, but actual upload to server
                # may still be in progress for files > ~5MB.
                # Require a minimum elapsed time proportional to file size.
                if expected_size:
                    # ~1s per 8MB, min 5s floor so even small files get a brief wait
                    min_composer_time = max(5, int(expected_size / (1024 * 1024) / 8))
                    if elapsed < min_composer_time:
                        remaining = min_composer_time - elapsed
                        print(f"\r  [UPLOAD] File preview ready, waiting for upload ({remaining}s remaining)...", end="", flush=True)
                        self.logger.debug(
                            f"Composer preview at {elapsed}s, waiting {min_composer_time}s "
                            f"before confirming upload (size={expected_size / (1024*1024):.1f}MB)"
                        )
                        time.sleep(0.5)
                        continue  # Don't confirm yet — keep monitoring
                print(f"\n  [OK] File attached in composer ({elapsed}s)")
                return True

            # Check for state changes — only count file-relevant changes as activity
            changed, reason = self._detect_state_change(pre_state)
            if changed:
                self.logger.info(f"DOM change detected: {reason} ({elapsed}s)")
                # Don't reset no-activity timer on random new messages from other users
                if reason in ('new_attachment', 'composer_changed_with_file'):
                    last_activity_time = time.time()
                    consecutive_no_activity = 0

            # Track activity - if no progress for a while, consider it done
            time_since_activity = time.time() - last_activity_time

            # Skip no-activity heuristic entirely for video files
            # Video transcoding can pause DOM updates for extended periods
            if self._is_video:
                if consecutive_no_activity >= 2:
                    # For video, only check DOM/observer, never assume done
                    if self._check_dom_upload_ready() or done:
                        print(f"\n  [OK] Video upload detected in DOM ({int(time_since_activity)}s)")
                        return True
                    consecutive_no_activity = 0  # Reset and keep waiting
            elif time_since_activity > no_activity_threshold:
                consecutive_no_activity += 1

                # After threshold of no activity, assume upload is done
                if consecutive_no_activity >= 2:
                    # Final verification — only trust composer or observer (new DOM nodes),
                    # NOT lenta scan (can match old messages from previous runs)
                    if self._check_dom_upload_ready() or done:
                        print(f"\n  [OK] Upload finished (no activity for {int(time_since_activity)}s)")
                        return True

                    # Still nothing after extended wait
                    if consecutive_no_activity >= 4:  # ~4x threshold
                        print(f"\n  [WARN] No upload activity for {int(time_since_activity)}s")
                        # Try one more thorough check
                        if self._check_dom_upload_ready() or self._check_upload_done():
                            print(f"\n  [OK] Upload confirmed after extended wait")
                            return True
                        print(f"  [INFO] Continuing to monitor...")

            # Show elapsed time every 30s if no progress
            if time.time() - last_progress_log >= 30 and elapsed > 30:
                print(f"\r  [MONITOR] Elapsed: {elapsed}s | No progress for: {int(time_since_activity)}s", end="", flush=True)
                last_progress_log = time.time()

            time.sleep(poll_interval)

        # Timeout - do final checks
        print(f"\n  [WARN] Upload monitoring timeout ({timeout}s) - final check...")
        time.sleep(2)

        done, filename = self._check_upload_done()
        if done:
            self.logger.info(f"Upload complete at timeout: {filename}")
            return True

        if self._check_dom_upload_ready():
            self.logger.info("Upload confirmed in composer at timeout")
            return True

        self.logger.error("Upload not confirmed - file may not have been uploaded")
        return False

    def _check_upload_progress(self) -> Optional[dict]:
        """
        Check for upload progress indicators in DOM.

        Returns:
            dict with keys: percent (int), speed (str), remaining (str) or None
        """
        script = """
            () => {
                // Look for various upload progress indicators
                const selectors = [
                    // Progress bar elements
                    '[class*="progress"] [class*="bar"]',
                    '[class*="upload"] [class*="progress"]',
                    '[role="progressbar"]',
                    '[class*="percent"]',
                    // Loading/spinner while uploading
                    '[class*="uploading"]',
                    '[class*="loading"]',
                    '[class*="transferring"]',
                    // Text indicators
                    '*[class*="upload"]',
                    '*[class*="progress"]'
                ];

                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        const text = el.textContent || '';
                        const style = el.getAttribute('style') || '';
                        const width = style.includes('width:') ? style.match(/width:\\s*([\\d.]+)%/) : null;
                        const aria = el.getAttribute('aria-valuenow') || '';

                        // Check for percentage patterns
                        const percentMatch = text.match(/(\\d+)%/) || aria.match(/(\\d+)/);
                        if (percentMatch) {
                            return {
                                percent: parseInt(percentMatch[1]),
                                text: text.slice(0, 50),
                                type: 'progress'
                            };
                        }

                        // Check for width style
                        if (width) {
                            return {
                                percent: parseFloat(width[1]),
                                text: text.slice(0, 50),
                                type: 'width'
                            };
                        }
                    }
                }

                // Check for network activity indicator (spinner, loading)
                const bodyClass = document.body.className || '';
                if (bodyClass.includes('uploading') || bodyClass.includes('loading')) {
                    return { percent: -1, text: 'uploading', type: 'class' };
                }

                // Check for any visible spinner
                const spinners = document.querySelectorAll('[class*="spinner"], [class*="loader"], [class*="activity"]');
                for (const sp of spinners) {
                    if (sp.offsetHeight > 0 && sp.offsetParent !== null) {
                        return { percent: -1, text: 'activity', type: 'spinner' };
                    }
                }

                return null;
            }
        """

        try:
            result = self.page.evaluate(script)
            return result if result else None
        except Exception as e:
            self.logger.debug(f"Progress check error: {e}")
            return None

    def _check_media_preview(self) -> bool:
        """
        Quick check: are there visible <img> or <video> elements with data:/blob: sources?
        These indicate a media file preview is ready (photo/video attached in composer).
        Searches the ENTIRE page, not just composer — more reliable for MAX.
        """
        try:
            result = self.page.evaluate("""
                () => {
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        if (img.offsetHeight > 50 && img.offsetWidth > 50) {
                            const src = img.src || '';
                            if (src.startsWith('data:') || src.startsWith('blob:')) {
                                return true;
                            }
                        }
                    }
                    const vids = document.querySelectorAll('video');
                    for (const vid of vids) {
                        if (vid.offsetHeight > 50 && vid.offsetWidth > 50) {
                            const src = vid.src || '';
                            // Only blob/data URLs indicate a NEW upload in composer
                            // Existing video messages have http/https src
                            if (src.startsWith('data:') || src.startsWith('blob:')) {
                                return true;
                            }
                        }
                    }
                    return false;
                }
            """)
            return result is True
        except Exception:
            return False

    def _check_dom_upload_ready(self) -> bool:
        """
        Final DOM check for attached file in composer.
        NOTE: Does NOT check input[type=file] - that only means file is selected, not uploaded.
        """
        # Fast path: check for media preview anywhere on page
        if self._check_media_preview():
            return True

        ext_json = str(self._expected_extensions)
        script = f"""
            () => {{
                // Only check for visible file indicators in composer
                const composer = document.querySelector('[class*="composer"], [role="textbox"], [contenteditable]');
                if (!composer) return false;

                // Check for visible <img> or <video> previews (media files - photos/videos)
                const mediaPreviews = composer.querySelectorAll('img, video');
                for (const media of mediaPreviews) {{
                    if (media.offsetHeight > 0 && media.offsetWidth > 0) {{
                        return true;
                    }}
                }}

                // Check for file preview/attachment elements within composer
                const fileIndicators = composer.querySelectorAll(
                    '[class*="preview"], [class*="file-item"], [class*="attach"], [class*="upload"], [data-file]'
                );
                const expectedExts = {ext_json};

                for (const el of fileIndicators) {{
                    if (el.offsetHeight > 0) {{
                        const text = el.textContent || '';
                        if (expectedExts.some(ext => text.includes(ext)) || text.includes('MB') || text.includes('KB')) {{
                            return true;
                        }}
                    }}
                }}

                // Alternative: check if composer has changed to include file class
                const composerClasses = composer.className || '';
                if (composerClasses.includes('with-file') || composerClasses.includes('has-file') || composerClasses.includes('file-attached')) {{
                    return true;
                }}

                return false;
            }}
        """

        try:
            result = self.page.evaluate(script)
            return result is True
        except Exception as e:
            self.logger.debug(f"DOM composer check error: {e}")
            return False

    def _take_content_snapshot(self, depth: int = 15, window: int = 100) -> Optional["ContentSnapshot"]:
        """
        Capture content snapshot: (hash, file_count).
        Used to detect new messages in virtual-scrolling feeds.

        Args:
            depth: Number of messages from the bottom to include
            window: Number of characters per message for hashing

        Returns:
            (hash_string, file_element_count) or None
        """
        try:
            result = self.page.evaluate(f"""
                () => {{
                    const msgs = document.querySelectorAll('[class*="message"]');
                    const depth = {depth};
                    const window = {window};
                    const start = Math.max(0, msgs.length - depth);
                    const texts = [];
                    for (let i = start; i < msgs.length; i++) {{
                        const text = (msgs[i].textContent || '').trim();
                        texts.push(text.slice(0, window));
                    }}
                    const fileCount = document.querySelectorAll(
                        '[class*="file"],[class*="attach"],[class*="preview"]'
                    ).length;
                    return {{ texts, fileCount }};
                }}
            """)

            if not result or not result.get('texts'):
                return None

            combined = "\n".join(result['texts'])
            import hashlib
            return ContentSnapshot(
                hash=hashlib.sha256(combined.encode("utf-8")).hexdigest(),
                file_count=result.get('fileCount', 0)
            )
        except Exception as e:
            self.logger.debug(f"Snapshot error: {e}")
            return None

    def _confirm_file_sent(self, pre_snapshot: "ContentSnapshot", file_size_bytes: int) -> bool:
        """
        Fast confirmation: check if feed content changed after sending.
        NOTE: Only reliable for files < 50MB. Larger files use _confirm_file_in_feed().

        Adaptive wait based on file size — photos render faster than videos.

        Args:
            pre_snapshot: Content snapshot taken BEFORE pressing Enter
            file_size_bytes: Size of the file in bytes

        Returns:
            True if content changed (file likely sent), False if no change detected
        """
        # Guard: only valid for files < 50MB
        if file_size_bytes >= 50 * 1024 * 1024:
            self.logger.debug(
                f"Skipping hash check for file >= 50MB ({file_size_bytes / 1024 / 1024:.1f} MB)"
            )
            return False

        if not pre_snapshot:
            return False

        # Adaptive wait: photos render faster than videos
        size_mb = file_size_bytes / (1024 * 1024)
        if size_mb < 5:
            initial_wait = 0.5
        elif size_mb < 50:
            initial_wait = 1.0
        else:
            initial_wait = 2.0

        time.sleep(initial_wait)

        # First check
        post = self._take_content_snapshot()
        if post and post.hash != pre_snapshot.hash:
            return True

        # Retry with increasing delay
        for _ in range(2):
            time.sleep(1.0)
            post = self._take_content_snapshot()
            if post and post.hash != pre_snapshot.hash:
                return True

        return False  # Caller falls back to _wait_for_file_message

    def _verify_composer_cleared(self, timeout: int = 30, poll_interval: float = 1.0) -> bool:
        """
        Verify the composer area no longer shows upload-in-progress indicators.

        Polls the DOM for progress bars, spinners, loading states, file previews,
        and attachment elements. Returns True when composer is clear, False on timeout.

        Args:
            timeout: Maximum seconds to wait (default 30)
            poll_interval: Seconds between polls (default 1)

        Returns:
            True if composer is clear, False if timeout reached
        """
        start = time.time()
        self.logger.debug("Verifying composer is clear...")

        while time.time() - start < timeout:
            try:
                is_clear = self.page.evaluate(r"""
                    () => {
                        const composer = document.querySelector(
                            '[class*="composer"], [class*="input"], [role="textbox"], [contenteditable]'
                        );
                        if (!composer) return false; // No composer = not clear (page may have reloaded)

                        // Check for progress indicators
                        const hasProgress = !!composer.querySelector(
                            '[class*="progress"], [role="progressbar"]'
                        );
                        // Check for spinner/loading indicators
                        const hasSpinner = !!composer.querySelector(
                            '[class*="spinner"], [class*="loading"], [class*="loader"]'
                        );
                        // Check for file preview/attachment elements
                        const hasPreview = !!composer.querySelector(
                            '[class*="preview"], [class*="attach"], [class*="upload-preview"], [data-file]'
                        );
                        // Check for upload percentage text
                        const text = composer.textContent || '';
                        const hasPercent = /\d+%/.test(text);
                        const hasUploading = /loading|uploading|sending/i.test(text);

                        if (hasProgress || hasSpinner || hasPreview || hasPercent || hasUploading) {
                            return false;
                        }
                        return true;
                    }
                """)

                if is_clear:
                    self.logger.debug("Composer is clear")
                    return True

                time.sleep(poll_interval)

            except Exception as e:
                self.logger.debug(f"Composer check error: {e}")
                time.sleep(poll_interval)

        self.logger.warning(f"Composer still busy after {timeout}s")
        return False

    def _confirm_file_in_feed(self, filename: str, file_size_bytes: int,
                               baseline_count: int = 0) -> bool:
        """
        For files >= 50 MB, verify the specific filename appears in the message feed.

        Unlike the delta check (_confirm_file_sent), this verifies the SPECIFIC file
        was posted — not just that "something changed" in the DOM.

        Adaptive wait before first check based on file size:
        - < 50 MB: 5 seconds
        - 50-200 MB: 15 seconds
        - 200-500 MB: 30 seconds
        - >= 500 MB: 60 seconds

        Up to 3 retries with 3-second delays between checks.

        Args:
            filename: Expected filename to find in feed
            file_size_bytes: Size of file for adaptive timing
            baseline_count: Message count baseline (only check new messages)

        Returns:
            True if filename found in feed, False otherwise
        """
        size_mb = file_size_bytes / (1024 * 1024)

        # Adaptive initial wait based on file size
        # Larger files take longer to appear in feed after sending
        if size_mb < 50:
            initial_wait = 5
        elif size_mb < 200:
            initial_wait = 15    # was 5s
        elif size_mb < 500:
            initial_wait = 30    # was 10s
        else:
            initial_wait = 60    # was 15s

        self.logger.debug(f"Confirming file in feed: {filename} ({size_mb:.1f} MB, wait {initial_wait}s)")
        time.sleep(initial_wait)

        # Normalize filename for comparison
        search_name = os.path.basename(filename).lower()
        search_name = search_name.replace('-master', '').replace('-main', '')

        max_retries = 3

        for attempt in range(max_retries):
            try:
                # CRITICAL: Scroll to bottom where new messages appear.
                # Virtual scrolling means baseline_count (captured at top) is USELESS
                # for comparison — the DOM shows completely different messages at bottom.
                # So we ignore baseline_count entirely and just scan all visible messages
                # for a STRICT match: both a file indicator AND the exact filename.
                self._scroll_to_bottom()
                self._force_rerender()
                self.page.wait_for_timeout(1500)  # Wait for DOM to update

                # Query ALL messages for STRICT filename match.
                # We require BOTH a file attachment indicator AND the exact filename
                # in a file-specific context (not just anywhere in message text).
                escaped_name = search_name.replace("\\", "\\\\").replace("'", "\\'")
                found = self.page.evaluate(
                    f"""
                    () => {{
                        const searchName = '{escaped_name}';
                        const msgs = document.querySelectorAll('[class*="message"]');

                        for (let i = msgs.length - 1; i >= 0; i--) {{
                            const msg = msgs[i];
                            const text = msg.textContent || '';
                            const html = msg.innerHTML || '';

                            // Only consider messages with file indicators
                            const hasFileClass = !!msg.querySelector(
                                '[class*="file"], [class*="attach"]'
                            );
                            const hasDownloadLink = !!msg.querySelector(
                                'a[download]'
                            );
                            // Video/audio elements with filenames in attributes
                            const videos = msg.querySelectorAll('video, audio');
                            const hasMediaWithFilename = Array.from(videos).some(v =>
                                (v.poster || v.src || v.getAttribute('data-filename') || '')
                                    .toLowerCase().includes(searchName)
                            );
                            // File name elements (previews, file cards)
                            const nameElements = msg.querySelectorAll(
                                '[class*="file-name"], [class*="name"], [class*="title"]'
                            );
                            const hasNameWithFilename = Array.from(nameElements).some(el =>
                                (el.textContent || '').toLowerCase().includes(searchName)
                            );

                            if (!hasFileClass && !hasDownloadLink && !hasMediaWithFilename && !hasNameWithFilename) {{
                                continue;
                            }}

                            // STRICT check: filename must appear in message
                            const textLower = text.toLowerCase();
                            if (textLower.includes(searchName)) {{
                                return {{ found: true, text: text.slice(0, 100) }};
                            }}
                            const htmlLower = html.toLowerCase();
                            if (htmlLower.includes(searchName)) {{
                                return {{ found: true, text: text.slice(0, 100) }};
                            }}
                        }}

                        return {{ found: false }};
                    }}
                """
                ) or {}

                if found.get('found'):
                    self.logger.info(
                        f"File confirmed in feed: {found.get('text', 'unknown')} (attempt {attempt + 1}/{max_retries})"
                    )
                    return True

                if attempt < max_retries - 1:
                    self.logger.debug(f"Filename not in feed yet, retry {attempt + 1}/{max_retries}")
                    time.sleep(3)

            except Exception as e:
                self.logger.warning(f"Feed check error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(3)

        self.logger.warning(f"Filename '{search_name}' not found in feed after {max_retries} attempts")
        return False

    def _match_filename_in_message(self, msg_text: str, msg_html: str,
                                    search_name: Optional[str] = None) -> tuple[bool, str]:
        """
        Check if a message contains the expected file. Three-tier matching:
        1. Regex extraction + normalized comparison
        2. Direct substring search
        3. Generic file indicator (fallback)

        Args:
            msg_text: Message textContent (lowercased)
            msg_html: Message innerHTML (lowercased)
            search_name: Normalized filename to search for (without -master/-main)

        Returns:
            (matched: bool, match_detail: str)
        """
        if not search_name:
            # No specific filename — accept any file message
            has_archive = bool(re.search(r'\.(zip|tar|gz|rar|7z)', msg_text))
            has_media = bool(re.search(r'\.(jpg|jpeg|png|gif|webp|mp4|mov|avi|mkv|webm)', msg_text))
            has_download = 'download' in msg_text or 'скачать' in msg_text
            matched = has_archive or has_media or has_download
            return (matched, "generic_file" if matched else "none")

        # Tier 1: Regex extraction + normalized comparison
        # Include media extensions so .mp4/.jpg files are matched too
        all_exts = list(self._expected_extensions) + [
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.mp4', '.mov', '.avi', '.mkv', '.webm'
        ]
        ext_pattern = '|'.join(re.escape(ext) for ext in all_exts)
        match = re.search(r'([a-z0-9\-_.]+(?:' + ext_pattern + r')(?:\.7z\.\d+)?)', msg_text)
        if match:
            msg_filename = match.group(1).replace('-master', '').replace('-main', '')
            if search_name in msg_filename or msg_filename in search_name:
                return (True, f"regex:{match.group(1)}")

        # Tier 1 on HTML too
        match = re.search(r'([a-z0-9\-_.]+(?:' + ext_pattern + r')(?:\.7z\.\d+)?)', msg_html)
        if match:
            msg_filename = match.group(1).replace('-master', '').replace('-main', '')
            if search_name in msg_filename or msg_filename in search_name:
                return (True, f"regex_html:{match.group(1)}")

        # Tier 2: Direct substring search
        if search_name in msg_text:
            return (True, f"substring:{search_name}")

        # Tier 3: Generic file indicator (archive OR media + download)
        has_archive = bool(re.search(r'\.(zip|7z)', msg_text))
        has_media = bool(re.search(r'\.(jpg|jpeg|png|gif|webp|mp4|mov|avi|mkv|webm)', msg_text))
        has_download = 'download' in msg_text or 'скачать' in msg_text
        if (has_archive or has_media) and has_download:
            self.logger.warning(f"Tertiary match — no exact filename, but file+download found near '{search_name}'")
            return (True, "tertiary_fallback")

        return (False, "no_match")

    def _scan_messages_for_file(self, start_idx: int, end_idx: int,
                                 search_name: Optional[str] = None) -> tuple[bool, int, str]:
        """
        Scan messages in range [start_idx, end_idx) for a file upload.

        Args:
            start_idx: First message index to check (inclusive)
            end_idx: Last message index to check (exclusive)
            search_name: Normalized filename to match (None = accept any file)

        Returns:
            (found: bool, msg_index: int, detail: str)
        """
        js_ext_pattern = '|'.join(re.escape(ext) for ext in self._expected_extensions)
        # Also check for common media extensions (photos/videos) so messages aren't skipped
        js_media_pattern = r'\.(jpg|jpeg|png|gif|webp|bmp|tiff|mp4|mov|avi|mkv|webm)'

        for idx in range(start_idx, end_idx):
            try:
                msg_result = self.page.evaluate(f"""
                    () => {{
                        const msgs = document.querySelectorAll('[class*="message"]');
                        const msg = msgs[{idx}];
                        if (!msg) return null;
                        const text = msg.textContent || '';
                        const html = msg.innerHTML || '';
                        const classes = msg.className || '';
                        const hasFileClass = /file|attach|download|archive|preview/i.test(classes);
                        const extRegex = new RegExp('{js_ext_pattern}', 'i');
                        const hasExpectedExt = extRegex.test(text) || extRegex.test(html);
                        const mediaRegex = new RegExp('{js_media_pattern}', 'i');
                        const hasMedia = mediaRegex.test(text) || mediaRegex.test(html);
                        const hasMediaTag = !!msg.querySelector('img, video, audio');
                        const hasDownload = msg.querySelector('[download]') !== null ||
                                            msg.querySelector('a[href*="download"]') !== null;
                        return {{
                            text: text.slice(0, 200),
                            html: html.slice(0, 300),
                            hasFileClass,
                            hasExpectedExt,
                            hasMedia,
                            hasMediaTag,
                            hasDownload,
                            classes: classes.slice(0, 80)
                        }};
                    }}
                """) or {}

                if not (msg_result.get('hasFileClass') or msg_result.get('hasExpectedExt') or
                        msg_result.get('hasMedia') or msg_result.get('hasMediaTag') or
                        msg_result.get('hasDownload')):
                    continue

                msg_text = (msg_result.get('text') or '').lower()
                msg_html = (msg_result.get('html') or '').lower()

                matched, detail = self._match_filename_in_message(msg_text, msg_html, search_name)
                if matched:
                    return (True, idx + 1, detail)

            except Exception as e:
                self.logger.debug(f"Scan msg #{idx + 1} error: {e}")
                continue

        return (False, 0, "not_found")

    @staticmethod
    def _compute_monitor_timeouts(file_size_bytes: int | None) -> tuple[int, int]:
        """
        Compute adaptive fallback timeouts based on file size.

        Smaller files render faster, so shorter timeouts avoid unnecessary delays.
        Very large files get proportionally longer timeouts to match actual upload
        times (e.g., 900 MB at 5 MB/s takes ~3 minutes).

        Timeout tiers:
        | Size       | Re-render | Reload |
        |------------|-----------|--------|
        | < 5 MB     | 3s        | 6s     |
        | 5-50 MB    | 15s       | 20s    |
        | 50-200 MB  | 25s       | 35s    |
        | 200-500 MB | 50s       | 60s    |
        | >= 500 MB  | 90s       | 120s   |

        Args:
            file_size_bytes: File size in bytes. If None, returns defaults.

        Returns:
            (rerender_timeout: int, reload_timeout: int) in seconds.
        """
        if file_size_bytes is None:
            return (30, 45)  # defaults for backwards compatibility

        size_mb = file_size_bytes / (1024 * 1024)

        if size_mb < 5:
            return (3, 6)
        elif size_mb < 50:
            return (15, 20)
        elif size_mb < 200:
            return (25, 35)
        elif size_mb < 500:
            return (50, 60)   # NEW: 200-500 MB tier
        else:
            return (90, 120)  # NEW: >= 500 MB tier (was 30, 45)

    def _check_upload_in_lenta(self, expected_filename: Optional[str] = None,
                                min_msg_index: int = 0) -> tuple[bool, Optional[str]]:
        """
        Check if file appears in message feed (not in composer).
        This is the definitive check that file was actually uploaded.

        Args:
            expected_filename: Optional filename to match.
                               If provided, only messages containing this filename count.
            min_msg_index: Only check messages at this index or higher
                           (to exclude old messages from previous repos).

        Returns:
            (found: bool, filename: Optional[str])
        """
        search_name = ""
        if expected_filename:
            import os as _os
            search_name = _os.path.basename(expected_filename).lower()

        escaped = search_name.replace("\\", "\\\\").replace("'", "\\'")
        script = f"""
            () => {{
                const searchName = '{escaped}';
                const minIdx = {min_msg_index};
                const msgs = document.querySelectorAll('[class*="message"]');

                for (let i = minIdx; i < msgs.length; i++) {{
                    const msg = msgs[i];
                    const text = msg.textContent || '';
                    const html = msg.innerHTML || '';
                    const hasFile = msg.querySelector('[class*="file"], [class*="attach"]') !== null;
                    const hasDownload = msg.querySelector('a[download], [download]') !== null;

                    const hasArchive = /\\.(zip|tar|gz|rar|7z|zip\\.\\w+)/i.test(text) ||
                                     /\\.(zip|tar|gz|rar|7z|zip\\.\\w+)/i.test(html);
                    const hasFileIndicator = hasFile || hasDownload || hasArchive;

                    if (!hasFileIndicator) continue;

                    if (searchName) {{
                        const textLower = text.toLowerCase();
                        if (!textLower.includes(searchName)) continue;
                    }}

                    return {{ found: true, text: text.slice(0, 100) }};
                }}

                return {{ found: false }};
            }}
        """

        try:
            result = self.page.evaluate(script)
            if result and result.get('found'):
                filename = result.get('text', 'unknown')
                self.logger.info(f"File found in lenta: {filename}")
                return (True, filename)
            elif result:
                self.logger.debug(f"Lenta check: no file")
            return (False, None)
        except Exception as e:
            self.logger.debug(f"Lenta check error: {e}")
            return (False, None)

    def _wait_for_file_message(self, timeout: int = 300,
                                expected_msg_index: Optional[int] = None,
                                expected_filename: Optional[str] = None,
                                baseline_count: Optional[int] = None,
                                fast_mode: bool = False,
                                file_size_bytes: int | None = None) -> tuple[bool, str, int]:
        """
        Monitor chat for file message using content-based snapshots.

        Since MAX uses virtual scrolling (DOM count stays constant),
        we detect new messages by comparing text content hashes of
        the last N messages at regular intervals.

        Args:
            timeout: Max time to wait (default 5 min as safety fallback)
            expected_msg_index: Expected message index (0 = any new)
            expected_filename: Filename to match (if provided, only confirms if filename matches)
            baseline_count: Message count BEFORE this upload started (to ignore old messages)
            fast_mode: If True, use quick polls instead of snapshot monitoring (for small media files)
            file_size_bytes: File size in bytes for adaptive timeouts (optional, defaults to 30s/45s)

        Returns:
            (found: bool, reason: str, found_msg_index: int)
            reason = "found" | "timeout" | "disconnected" | "init_failed"
        """
        start = time.time()
        snapshot_interval = 2  # seconds between snapshots
        snapshot_depth = 30    # last N messages to snapshot (increased for better coverage)

        # FIX 3: Adaptive timeouts based on file size
        rerender_timeout, reload_timeout = self._compute_monitor_timeouts(file_size_bytes)

        print(f"  [MONITOR] Starting content-based monitoring...")

        # Normalize search name
        search_name = None
        if expected_filename:
            import os as os_module
            basename = os_module.path.basename(expected_filename).lower()
            search_name = basename.replace('-master', '').replace('-main', '')
            print(f"  [SCAN] Looking for: {search_name}")

        try:
            if not self._ensure_alive():
                return (False, "not_connected", 0)

            base_count = self.page.evaluate(
                "() => document.querySelectorAll('[class*=\"message\"]').length"
            ) or 0

            init_result = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    const last = msgs[msgs.length - 1];
                    return last ? last.textContent?.slice(0, 200) || '' : '';
                }
            """) or ""
            print(f"  [MONITOR] Initial: {base_count} msgs, last: {init_result[:50]}...")

            # Use provided baseline or capture current count
            if baseline_count is None:
                baseline_count = base_count

            # Detect virtual scrolling — stable count means count-based checks are useless
            is_virtual_scroll = base_count <= 150
            if is_virtual_scroll:
                print(f"  [MONITOR] Virtual scroll detected ({base_count} msgs), skipping count-based phases")

            # ── INITIAL SCAN ──
            if not is_virtual_scroll:
                print(f"  [SCAN] Scanning from msg #{baseline_count + 1}...")
                if baseline_count < base_count:
                    found, msg_idx, detail = self._scan_messages_for_file(
                        baseline_count, base_count, search_name
                    )
                    if found:
                        print(f"  [OK] FILE FOUND in initial scan! Message #{msg_idx} ({detail})")
                        return (True, "found", msg_idx)
                else:
                    print(f"  [SCAN] No new messages yet (baseline={baseline_count}, current={base_count}), retrying...")
                    for retry in range(2):
                        time.sleep(2)
                        try:
                            new_count = self.page.evaluate(
                                "() => document.querySelectorAll('[class*=\"message\"]').length"
                            ) or 0
                            scan_start = max(baseline_count, new_count - 15)
                            if scan_start < new_count:
                                found, msg_idx, detail = self._scan_messages_for_file(
                                    scan_start, new_count, search_name
                                )
                                if found:
                                    print(f"  [OK] FILE FOUND in retry scan #{retry + 1}! Message #{msg_idx} ({detail})")
                                    return (True, "found", msg_idx)
                        except Exception as retry_err:
                            print(f"  [WARN] Retry scan #{retry + 1} failed: {retry_err}")
            else:
                print(f"  [SCAN] Skipping count-based scan (virtual scroll, {base_count} msgs)")

        except Exception as e:
            print(f"  [ERROR] Failed to initialize: {e}")
            return (False, "init_failed", 0)

        # ── FAST MODE: quick polls for small media files ──
        # Skip under virtual scroll — count-based polls are useless
        if fast_mode and not is_virtual_scroll:
            for attempt in range(5):
                time.sleep(1)
                try:
                    current_total = self.page.evaluate(
                        "() => document.querySelectorAll('[class*=\"message\"]').length"
                    ) or 0
                    scan_start = baseline_count if baseline_count else 0
                    if scan_start < current_total:
                        found, msg_idx, detail = self._scan_messages_for_file(
                            scan_start, current_total, search_name
                        )
                        if found:
                            elapsed = int(time.time() - start)
                            print(f"  [OK] FILE FOUND (fast)! Message #{msg_idx} in {elapsed}s ({detail})")
                            return (True, "found", msg_idx)
                except Exception:
                    pass
            # FIX 4: Full scan fallback — search ALL messages by filename
            # After virtual scroll reload, baseline may be stale but file exists in DOM
            try:
                total = self.page.evaluate(
                    "() => document.querySelectorAll('[class*=\"message\"]').length"
                ) or 0
                # Limit scan range for performance: if > 500 messages, scan last 50 only
                if total > 500:
                    full_start = total - 50
                else:
                    full_start = 0
                found, msg_idx, detail = self._scan_messages_for_file(
                    full_start, total, search_name
                )
                if found:
                    elapsed = int(time.time() - start)
                    print(f"  [OK] FILE FOUND (full scan)! Message #{msg_idx} in {elapsed}s ({detail})")
                    return (True, "found", msg_idx)
            except Exception as full_scan_err:
                print(f"  [WARN] Full scan fallback failed: {full_scan_err}")
            # Fall through to normal monitoring

        # ── CONTENT-BASED MONITORING LOOP ──
        prev_snapshot = self._take_content_snapshot(depth=snapshot_depth)
        if prev_snapshot:
            print(f"  [SNAPSHOT] Baseline hash: {prev_snapshot.hash[:16]}... files: {prev_snapshot.file_count}")
        else:
            print(f"  [SNAPSHOT] Baseline: none")

        last_rerender_time = 0
        # last_reload_time removed -- reload is eliminated from the pipeline

        while True:
            elapsed = int(time.time() - start)
            timeout_reached = elapsed >= timeout

            try:
                # Ensure connection alive
                if not self._ensure_alive():
                    print(f"  [WARN] Connection lost after {elapsed}s")
                    return (False, "disconnected", 0)

                # Take new content snapshot
                curr_snapshot = self._take_content_snapshot(depth=snapshot_depth)

                if curr_snapshot and prev_snapshot and curr_snapshot != prev_snapshot:
                    print(f"  [UPDATE] Snapshot changed at {elapsed}s")

                    # Scan for file message
                    current_total = self.page.evaluate(
                        "() => document.querySelectorAll('[class*=\"message\"]').length"
                    ) or 0
                    scan_start = max(baseline_count, current_total - snapshot_depth)

                    found, msg_idx, detail = self._scan_messages_for_file(
                        scan_start, current_total, search_name
                    )
                    if found:
                        print(f"  [OK] FILE FOUND! Message #{msg_idx} ({detail})")
                        return (True, "found", msg_idx)

                    prev_snapshot = curr_snapshot

                # Check for timeout
                if timeout_reached:
                    final_count = self.page.evaluate(
                        "() => document.querySelectorAll('[class*=\"message\"]').length"
                    ) or 0
                    print(f"  [WARN] Timeout after {elapsed}s. Checking last 20 msgs...")

                    # Fallback: scan last 20 messages
                    fallback_start = max(0, final_count - 20)
                    found, msg_idx, detail = self._scan_messages_for_file(
                        fallback_start, final_count, search_name
                    )
                    if found:
                        print(f"  [OK] File found at timeout! Msg #{msg_idx} ({detail})")
                        return (True, "found", msg_idx)

                    print(f"  [WARN] No file found. Messages: {base_count} -> {final_count}")
                    return (False, "timeout", 0)

                # Periodic status every 30s
                if elapsed > 0 and elapsed % 30 == 0:
                    print(f"  [MONITOR] {elapsed}s | waiting...")

                # Fallback: force re-render after 30s of no changes
                if elapsed >= rerender_timeout and elapsed < rerender_timeout + 5 and prev_snapshot and (elapsed - last_rerender_time) > (rerender_timeout - 5):
                    print(f"  [FALLBACK] No changes for {rerender_timeout}s, forcing re-render...")
                    self._force_rerender()
                    last_rerender_time = elapsed
                    new_snapshot = self._take_content_snapshot(depth=snapshot_depth)
                    if new_snapshot and new_snapshot != prev_snapshot:
                        print(f"  [UPDATE] Re-render detected change")
                        current_total = self.page.evaluate(
                            "() => document.querySelectorAll('[class*=\"message\"]').length"
                        ) or 0
                        scan_start = max(baseline_count, current_total - snapshot_depth)
                        found, msg_idx, detail = self._scan_messages_for_file(
                            scan_start, current_total, search_name
                        )
                        if found:
                            print(f"  [OK] FILE FOUND after re-render! Message #{msg_idx} ({detail})")
                            return (True, "found", msg_idx)
                        prev_snapshot = new_snapshot

                # Extended fallback: after 2x rerender_timeout (3x for video), give up
                # WITHOUT reloading — the caller will retry via _upload_single_file loop
                extended_timeout = rerender_timeout * 3 if self._is_video else rerender_timeout * 2
                if elapsed >= extended_timeout:
                    print(f"  [WARN] Extended timeout ({extended_timeout}s) reached — "
                          f"file not found, returning without reload")
                    final_count = self.page.evaluate(
                        "() => document.querySelectorAll('[class*=\"message\"]').length"
                    ) or 0
                    fallback_start = max(0, final_count - 20)
                    found, msg_idx, detail = self._scan_messages_for_file(
                        fallback_start, final_count, search_name
                    )
                    if found:
                        print(f"  [OK] File found at extended timeout! Msg #{msg_idx} ({detail})")
                        return (True, "found", msg_idx)
                    return (False, "not_found", -1)

                time.sleep(snapshot_interval)

            except Exception as e:
                print(f"  [ERROR] Monitor error: {e}")
                time.sleep(snapshot_interval)

    def _watch_message_feed(self, timeout: int = 10800, progress: bool = True) -> tuple[bool, str]:
        """
        Monitor MAX feed for file confirmation.

        Args:
            timeout: Maximum seconds to wait (default: 10800 = 3 hours)
            progress: Show progress bar

        Returns:
            (confirmed: bool, reason: str)
            reason = "confirmed" | "timeout" | "cancelled"
        """
        self._check_connection()
        hours = timeout // 3600
        mins = (timeout % 3600) // 60
        secs = timeout % 60
        time_str = f"{hours}:{mins:02d}:{secs:02d}" if hours > 0 else f"{mins}:{secs:02d}"

        self.logger.info(f"Watching feed for file confirmation (timeout: {time_str})")

        start = time.time()
        last_progress_time = start
        cancel_requested = False

        initial_count = 0
        initial_last_msg = ""
        try:
            initial_count = self.page.evaluate("""
                () => document.querySelectorAll('[class*="message"]').length
            """) or 0
            initial_last_msg = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    return msgs[msgs.length - 1]?.textContent?.slice(0, 200) || '';
                }
            """) or ""
        except Exception as e:
            self.logger.warning(f"Failed to get initial state: {e}")

        self.logger.debug(f"Initial: count={initial_count}, last_msg={initial_last_msg[:50]}")

        while time.time() - start < timeout:
            remaining = int(timeout - (time.time() - start))

            if progress and time.time() - last_progress_time >= 60:
                last_progress_time = time.time()

                r_hours = remaining // 3600
                r_mins = (remaining % 3600) // 60
                r_secs = remaining % 60
                remaining_str = f"{r_hours}:{r_mins:02d}:{r_secs:02d}" if r_hours > 0 else f"{r_mins}:{r_secs:02d}"

                elapsed = int(time.time() - start)
                e_hours = elapsed // 3600
                e_mins = (elapsed % 3600) // 60
                e_secs = elapsed % 60
                elapsed_str = f"{e_hours}:{e_mins:02d}:{e_secs:02d}" if e_hours > 0 else f"{e_mins}:{e_secs:02d}"

                progress_pct = min(100, int((elapsed / timeout) * 100))
                filled = progress_pct // 2
                empty = 50 - filled
                bar = "█" * filled + "░" * empty

                print(f"\r  [{bar}] {elapsed_str}/{remaining_str} remaining | Q to cancel", end="", flush=True)

            try:
                import select
                import tty

                if sys.platform == 'win32':
                    import msvcrt
                    if msvcrt.kbhit():
                        key = msvcrt.getch()
                        if key in [b'q', b'Q', b'\x03']:
                            cancel_requested = True
                else:
                    if select.select([sys.stdin], [], [], 0)[0]:
                        key = sys.stdin.read(1)
                        if key.lower() == 'q':
                            cancel_requested = True

            except Exception as e:
                self.logger.debug(f"Keyboard check error: {e}")

            if cancel_requested:
                self.logger.info("Cancelled by user")
                return (False, "cancelled")

            time.sleep(2)

            try:
                current_count = self.page.evaluate("""
                    () => document.querySelectorAll('[class*="message"]').length
                """) or 0

                if current_count > initial_count:
                    last_msg = self.page.evaluate("""
                        () => {
                            const msgs = document.querySelectorAll('[class*="message"]');
                            const last = msgs[msgs.length - 1];
                            if (!last) return null;

                            const text = last.textContent || '';
                            const html = last.innerHTML || '';
                            
                            // Check for various file indicators
                            const hasFileClass = last.querySelector(
                                '[class*="file"], [class*="attach"], [class*="download"], [class*="archive"]'
                            ) !== null;
                            
                            // Check for SVG/file icons
                            const hasSvgIcon = last.querySelector('svg') !== null;
                            
                            // Check for download button/link
                            const hasDownloadLink = last.querySelector('a[href*="download"], [download]') !== null;
                            
                            // Check for file size patterns (common in file messages)
                            const hasFileSize = /\\d+\\.?\\d*\\s*(MB|GB|KB|mb|gb|kb)/i.test(text);
                            
                            // Check for archive extensions
                            const hasArchive = /\\.(zip|tar|gz|rar|7z|tgz|zip\\.\\w+)/i.test(text) ||
                                              /\\.(zip|tar|gz|rar|7z|tgz|zip\\.\\w+)/i.test(html);
                            
                            // Check for specific file message patterns
                            const isFileMessage = (
                                hasFileClass ||
                                hasDownloadLink ||
                                hasFileSize ||
                                hasArchive
                            );

                            return {
                                text: text.slice(0, 200),
                                html: html.slice(0, 500),
                                hasFile: isFileMessage,
                                hasFileClass: hasFileClass,
                                hasArchive: hasArchive,
                                hasFileSize: hasFileSize
                            };
                        }
                    """) or {}

                    if last_msg:
                        has_file = last_msg.get('hasFile', False)
                        self.logger.debug(f"New message detected: hasFile={has_file}, text={last_msg.get('text', '')[:50]}")

                        if has_file:
                            elapsed = int(time.time() - start)
                            self.logger.info(f"File confirmed in feed! ({elapsed}s)")
                            self.logger.debug(f"Message details: {last_msg.get('text', '')[:100]}")
                            return (True, "confirmed")

            except Exception as e:
                self.logger.debug(f"Feed check error: {e}")

        self.logger.warning(f"File confirmation timeout ({timeout}s)")
        return (False, "timeout")

    def _find_message_input(self):
        """Find message input field"""
        self._check_connection()
        self.logger.debug("Looking for message input...")

        selectors = [
            '[contenteditable="true"]',
            '[contenteditable]',
            'div[role="textbox"]',
        ]

        for selector in selectors:
            try:
                inp = self.page.locator(selector).first
                if inp.is_visible(timeout=3000):
                    self.logger.debug(f"Input found: {selector}")
                    return inp
            except Exception as e:
                self.logger.debug(f"Selector failed: {selector} ({e})")
                continue

        self.logger.warning("Message input not found")
        return None

    def _click_composer_area(self):
        """Click on message composer area to ensure focus"""
        self._check_connection()
        self.logger.debug("Clicking composer area...")

        selectors = [
            '[contenteditable="true"]',
            '[contenteditable]',
            'div[role="textbox"]',
            '[class*="composer"]',
            '[class*="input"]',
        ]

        for selector in selectors:
            try:
                elem = self.page.locator(selector).first
                if elem.is_visible(timeout=2000):
                    elem.click()
                    self.page.wait_for_timeout(200)
                    self.logger.debug(f"Clicked on: {selector}")
                    return True
            except Exception as e:
                self.logger.debug(f"Selector failed: {selector} ({e})")
                continue

        return False

    def _type_message(self, text: str, input_elem):
        """Type message into input field using clipboard paste"""
        self.logger.debug("Typing message...")

        try:
            import pyperclip

            self._click_composer_area()
            input_elem.scroll_into_view_if_needed()
            input_elem.click(click_count=3)
            self.page.wait_for_timeout(100)

            pyperclip.copy(text)
            self.page.wait_for_timeout(50)

            self.page.keyboard.press("Control+A")
            self.page.wait_for_timeout(50)
            self.page.keyboard.press("Control+V")

            self.logger.debug("Message typed via clipboard")
            return True
        except ImportError:
            self.logger.error("pyperclip not installed")
            return False
        except Exception as e:
            self.logger.error(f"Type failed: {e}")
            return False

    def _send_message(self):
        """Send message (press Enter)"""
        self.logger.debug("Sending message...")

        try:
            self._click_composer_area()
            self.page.wait_for_timeout(100)

            self.page.keyboard.press("Enter")
            self.logger.debug("Message sent (Enter)")
            return True
        except Exception as e:
            self.logger.error(f"Send failed: {e}")
            return False

    def _force_rerender(self) -> bool:
        """
        Force DOM re-render by scrolling up and down.
        Used as fallback when virtual scrolling doesn't update.

        Returns:
            True if re-render was attempted, False on error
        """
        try:
            self.logger.debug("Forcing re-render via scroll...")
            # Scroll down
            for _ in range(3):
                self.page.keyboard.press("PageDown")
                self.page.wait_for_timeout(200)
            # Scroll to bottom
            self.page.keyboard.press("End")
            self.page.wait_for_timeout(500)
            # Scroll back to top
            self.page.keyboard.press("Home")
            self.page.wait_for_timeout(500)
            # Scroll to bottom again
            self.page.keyboard.press("End")
            self.page.wait_for_timeout(500)
            self.logger.debug("Re-render complete")
            return True
        except Exception as e:
            self.logger.debug(f"Re-render failed: {e}")
            return False

    def send_message_with_files(self, text: str, filepaths: list[str],
                                retries: int = 3, retry_delay: int = 10,
                                split_threshold_mb: float = 49.0,
                                expected_extensions: list[str] | None = None) -> tuple[bool, bool]:
        """
        Send text message with one or more files (supports split archives).

        Files larger than split_threshold_mb will be split using 7z into volumes.
        Each volume is sent as a separate message.

        Args:
            text: Message text
            filepaths: List of file paths to upload
            retries: Number of retries per file
            retry_delay: Delay between retries (seconds)
            split_threshold_mb: Threshold in MB to trigger splitting (default: 49)
            expected_extensions: List of file extensions to match for upload confirmation.
                                Default: ['.zip']

        Returns:
            Tuple of (all_success: bool, all_files_deletable: bool)
        """
        if expected_extensions is not None:
            self._expected_extensions = expected_extensions
        all_files = []
        volumes_to_cleanup = []

        # Process each file - split if needed
        for fp in filepaths:
            if not os.path.exists(fp):
                self.logger.error(f"File not found: {fp}")
                continue

            file_size_mb = os.path.getsize(fp) / 1024 / 1024

            if file_size_mb > split_threshold_mb:
                self.logger.info(f"File {os.path.basename(fp)} ({file_size_mb:.1f} MB) > {split_threshold_mb} MB - splitting...")

                # Split into volumes
                volumes = split_file_with_7z(fp, SEVEN_ZIP_VOLUME_SIZE)

                if volumes:
                    self.logger.info(f"Split into {len(volumes)} volumes")
                    all_files.extend(volumes)
                    volumes_to_cleanup.extend(volumes)
                else:
                    # Split failed, try sending original
                    self.logger.warning("Split failed, trying original file")
                    all_files.append(fp)
            else:
                all_files.append(fp)

        if not all_files:
            self.logger.error("No files to upload")
            return (False, True)

        self.logger.info(f"Uploading {len(all_files)} file(s)")

        # Ensure connected (reuse existing connection)
        if not self.page:
            self.logger.info("Connecting to MAX...")
            if not self.connect():
                raise ConnectionError("Failed to connect to Chrome")
            self.navigate()

        self.ensure_page_ready()

        # CRITICAL: Capture message count BEFORE starting uploads.
        # Only messages >= this count belong to this batch of files.
        self._pre_upload_msg_count = self.page.evaluate(
            "() => document.querySelectorAll('[class*=\"message\"]').length"
        ) or 0
        self.logger.info(f"Pre-upload message count: {self._pre_upload_msg_count}")

        # Send message text first
        self.logger.debug("Typing message...")
        input_elem = self._find_message_input()
        if input_elem:
            self._type_message(text, input_elem)

        self.logger.debug("Sending about message...")
        self._send_message()
        time.sleep(1)

        # CRITICAL: Update baseline AFTER text message is sent.
        # Messages from 0 to (old baseline-1) are from previous repos.
        # Messages from (old baseline) to (new baseline-1) are from THIS upload's text.
        # File messages will appear after (new baseline), so we need the updated count.
        self._pre_upload_msg_count = self.page.evaluate(
            "() => document.querySelectorAll('[class*=\"message\"]').length"
        ) or 0
        self.logger.info(f"Post-text message count: {self._pre_upload_msg_count}")

        # Upload each file - delete immediately after confirmation
        all_success = True
        all_deletable = True

        for i, fp in enumerate(all_files, 1):
            filename = os.path.basename(fp)
            file_size_bytes = os.path.getsize(fp)
            file_size_mb = file_size_bytes / 1024 / 1024

            self.logger.info(f"Uploading file {i}/{len(all_files)}: {filename} ({file_size_mb:.1f} MB)")

            success = self._upload_single_file(
                fp, filename, file_size_bytes,
                retries=retries, retry_delay=retry_delay,
                baseline_count=self._pre_upload_msg_count
            )

            if not success:
                all_success = False
                self.logger.error(f"Failed to upload: {filename}")
                # Keep failed file for potential retry
            else:
                self.logger.info(f"✓ Uploaded: {filename}")
                # Update baseline so next volume only looks at messages AFTER this one
                self._pre_upload_msg_count = self.page.evaluate(
                    "() => document.querySelectorAll('[class*=\"message\"]').length"
                ) or 0
                self.logger.debug(f"Updated baseline: {self._pre_upload_msg_count}")
                # Delete volume IMMEDIATELY after confirmation
                # This ensures partial uploads are not lost on interrupt
                if fp in volumes_to_cleanup:
                    try:
                        if os.path.exists(fp):
                            os.remove(fp)
                            self.logger.debug(f"Deleted: {filename}")
                    except Exception as e:
                        self.logger.warning(f"Failed to delete {filename}: {e}")

            # Small delay between files
            if i < len(all_files):
                time.sleep(1)

        # Cleanup any remaining volumes (should be none with immediate delete)
        if volumes_to_cleanup:
            remaining = [v for v in volumes_to_cleanup if os.path.exists(v)]
            if remaining:
                self.logger.info(f"Cleaning up {len(remaining)} remaining volumes...")
                cleanup_volumes(remaining)

        return (all_success, all_deletable)

    def _upload_single_file(self, filepath: str, filename: str, file_size_bytes: int,
                            retries: int = 3, retry_delay: int = 10,
                            baseline_count: int = 0) -> bool:
        """
        Upload a single file and wait for confirmation.

        Args:
            filepath: Absolute path to file
            filename: Display name for the file
            file_size_bytes: File size in bytes
            retries: Number of retries
            retry_delay: Delay between retries
            baseline_count: Message count baseline — passed to _wait_upload_complete
                            to avoid false matches on old messages

        Returns:
            True if upload successful
        """
        file_size_mb = file_size_bytes / 1024 / 1024

        for attempt in range(1, retries + 1):
            try:
                self.logger.debug(f"Attempt {attempt}/{retries}")

                # === UPLOAD STATE LOCK ===
                # Set flags before any browser interaction to block destructive navigation
                self._lock_upload_state(filepath)

                # Check connection is still alive.
                # NOTE: Navigation is the caller's responsibility.
                # _upload_large_file() handles its own navigation,
                # send_message_with_files() navigates before calling us.
                # We only verify the page is alive, we don't navigate here.
                if not self._ensure_alive():
                    self.logger.error("Cannot reconnect to Chrome")
                    if attempt < retries:
                        time.sleep(retry_delay)
                        continue
                    else:
                        return False

                # Capture content snapshot before upload for delta confirmation
                pre_snapshot = self._take_content_snapshot()

                upload_timeout = max(60000, int(file_size_mb * 5000))
                self.logger.debug(f"Upload timeout: {upload_timeout//1000}s")

                uploaded = False
                try:
                    with self.page.expect_file_chooser(timeout=upload_timeout) as fc_info:
                        self._click_upload_button()

                    fc_info.value.set_files(filepath, timeout=upload_timeout)
                    self.logger.info(f"File selected: {filename}")
                    uploaded = True
                except PlaywrightTimeout:
                    self.logger.warning("File chooser timeout, trying input method...")
                    try:
                        file_input = self.page.locator('input[type="file"]').first
                        file_input.set_input_files(filepath, timeout=upload_timeout)
                        self.logger.info("File uploaded via input")
                        uploaded = True
                    except Exception as e2:
                        self.logger.error(f"Input method also failed: {e2}")
                except Exception as e:
                    self.logger.warning(f"File chooser failed: {e}")
                    try:
                        file_input = self.page.locator('input[type="file"]').first
                        file_input.set_input_files(filepath, timeout=upload_timeout)
                        self.logger.info("File uploaded via input")
                        uploaded = True
                    except Exception as e2:
                        self.logger.error(f"Input method also failed: {e2}")

                if not uploaded:
                    self.logger.error("Failed to upload file - both methods failed")
                    if attempt < retries:
                        time.sleep(retry_delay)
                    continue

                self.logger.debug("Waiting for upload...")
                if not self._wait_upload_complete(expected_filename=filename, expected_size=file_size_bytes,
                                                     baseline_count=baseline_count):
                    self.logger.error("Upload did not complete in time")
                    if attempt < retries:
                        time.sleep(retry_delay)
                    continue

                self.logger.debug("Sending file message...")
                self._send_message()

                # NEW: Verify composer cleared before confirming (prevents false positives)
                if not self._verify_composer_cleared():
                    self.logger.warning("Composer still busy — treating as unconfirmed")
                else:
                    self.logger.debug("Composer cleared, proceeding to confirmation")

                # NEW: For large files (>= 50 MB), use filename-based confirmation
                # instead of the delta check which triggers false positives
                LARGE_CONFIRM_THRESHOLD = 50 * 1024 * 1024  # 50 MB
                confirmed = False

                if file_size_bytes >= LARGE_CONFIRM_THRESHOLD:
                    # Large file path: check filename appears in feed
                    confirmed = self._confirm_file_in_feed(
                        filename, file_size_bytes,
                        baseline_count=baseline_count
                    )
                    if confirmed:
                        confirm_elapsed = 0  # elapsed tracked inside _confirm_file_in_feed
                        self.logger.info(
                            f"File confirmed (feed check, {filename})"
                        )
                        print(
                            f"  [OK] File confirmed (feed check, {filename})"
                        )
                else:
                    # Small file path: existing fast delta check
                    confirm_start = time.time()
                    confirmed = self._confirm_file_sent(pre_snapshot, file_size_bytes)
                    confirm_elapsed = time.time() - confirm_start

                    if confirmed:
                        self.logger.info(
                            f"File confirmed (delta check, {confirm_elapsed:.1f}s)"
                        )
                        print(
                            f"  [OK] File confirmed (delta check, {confirm_elapsed:.1f}s)"
                        )

                if confirmed:
                    return True

                # Fallback: full confirmation flow for edge cases
                self.logger.debug(
                    f"Fast confirmation failed, falling back to full confirmation"
                )
                # Adaptive timeout: scale with file size
                # ~2 MB/s baseline upload speed, plus buffer
                size_mb = file_size_bytes / (1024 * 1024)
                adaptive_timeout = max(120, int(size_mb * 60 / 2))  # 2 MB/s baseline
                adaptive_timeout = min(adaptive_timeout, 900)  # cap at 15 min
                found, reason, msg_idx = self._wait_for_file_message(
                    timeout=adaptive_timeout,
                    expected_filename=filename,
                    baseline_count=self._pre_upload_msg_count,
                    fast_mode=file_size_bytes < 5 * 1024 * 1024,  # fast mode for files < 5MB
                    file_size_bytes=file_size_bytes
                )
                self.logger.info(f"Result: {reason}, msg #{msg_idx}")

                if found:
                    return True
                else:
                    self.logger.error(f"File not found in chat: {reason}")
                    if attempt < retries:
                        time.sleep(retry_delay)
                    continue

            except UploadInProgressError:
                # Caught from guards in navigate() — retry with delay
                self.logger.warning(
                    f"UploadInProgressError in attempt {attempt} — retrying"
                )
                if attempt < retries:
                    time.sleep(retry_delay)
                continue
            except Exception as e:
                self.logger.error(f"Upload error: {e}", exc_info=True)
                if attempt < retries:
                    time.sleep(retry_delay)
                else:
                    return False
            finally:
                # === UPLOAD STATE UNLOCK ===
                # Always unlock to reset guards, regardless of outcome
                self._unlock_upload_state()

        return False

    def send_message_with_file(self, text: str, filepath: str,
                               retries: int = 3, retry_delay: int = 10,
                               keep_alive: bool = False,
                               expected_extensions: list[str] | None = None) -> tuple[bool, bool]:
        """
        Send text message first, then file as second message.

        Args:
            keep_alive: If True, don't close connection after sending
            expected_extensions: List of file extensions to match for upload confirmation.
                                Default: ['.zip']

        Returns:
            Tuple of (success: bool, file_deletable: bool)
            file_deletable indicates if file can be safely deleted after upload
        """
        # Use the new multi-file method for backward compatibility
        success, deletable = self.send_message_with_files(
            text=text,
            filepaths=[filepath],
            retries=retries,
            retry_delay=retry_delay,
            expected_extensions=expected_extensions
        )
        return (success, deletable)

    # ──────────────────────────────────────────────
    # Audit & Restore — scroll, collect, verify
    # ──────────────────────────────────────────────

    def _find_scroll_container(self) -> str | None:
        """
        Find the CSS selector of the scrollable container holding messages.
        Returns a CSS selector string or None.
        """
        result = self.page.evaluate("""
            () => {
                const msgs = document.querySelectorAll('[class*="message"]');
                if (msgs.length === 0) return null;

                const first = msgs[0];
                let el = first.parentElement;
                let depth = 0;
                while (el && depth < 20) {
                    const style = window.getComputedStyle(el);
                    const ov = style.overflowY + ' ' + style.overflow;
                    const scrollable = el.scrollHeight > el.clientHeight + 20;
                    const hasScrollStyle = ov.includes('auto') || ov.includes('scroll');
                    if (scrollable && hasScrollStyle) {
                        return el.tagName + '#' + (el.id || '') + '.' + (el.className || '').replace(/\\s+/g, '.');
                    }
                    el = el.parentElement;
                    depth++;
                }
                return null;
            }
        """)
        return result

    def scroll_to_top(self) -> int:
        """
        Scroll chat to top by repeatedly scrolling up.
        Uses text-based deduplication — works with virtual lists
        (where DOM elements are recycled, so count stays constant).

        Collects ALL unique message texts seen during the scroll.
        When the visible top is reached, performs overscroll cycles
        to force MAX to load older messages, then continues scrolling.

        Returns:
            Total number of unique messages found.
        """
        self._check_connection()
        print(f"  [SCROLL] Загрузка всех сообщений, скролл вверх...")

        # Collect ALL unique message signatures (text snippet for dedup)
        all_signatures: set[str] = set()
        overscroll_stale = 0
        MAX_OVERSCROLL_STALE = 3

        # Try to focus the scroll container first
        self.page.evaluate("""
            () => {
                const containers = document.querySelectorAll(
                    '[class*="messages"],[class*="lenta"],[class*="feed"],' +
                    '[class*="chat"],[class*="dialog"],[class*="scroll"]'
                );
                let best = null;
                let bestH = 0;
                for (const c of containers) {
                    if (c.scrollHeight > c.clientHeight + 50 && c.scrollHeight > bestH) {
                        best = c;
                        bestH = c.scrollHeight;
                    }
                }
                if (best) {
                    window.__gitax_scroll = best;
                    best.setAttribute('tabindex', '-1');
                    best.focus();
                } else {
                    window.__gitax_scroll = null;
                }
            }
        """)
        self.page.wait_for_timeout(300)

        step = 0
        while overscroll_stale < MAX_OVERSCROLL_STALE:
            no_new = 0

            while True:
                try:
                    # Get all currently-rendered messages' text signatures
                    current = self.page.evaluate("""
                        () => {
                            const msgs = document.querySelectorAll('[class*="message"]');
                            const sigs = [];
                            for (const m of msgs) {
                                const t = (m.textContent || '').trim();
                                sigs.push(t.slice(0, 120));
                            }
                            return sigs;
                        }
                    """) or []

                    # Find new unique signatures
                    new_sigs = list(set(s for s in current if s and s not in all_signatures))
                    if new_sigs:
                        all_signatures.update(new_sigs)
                        no_new = 0
                        if step % 15 == 0:
                            print(f"  [SCROLL] Шаг {step}: +{len(new_sigs)} новых, всего {len(all_signatures)}")
                    else:
                        no_new += 1
                        if no_new >= 15:
                            print(f"  [SCROLL] Видимый верх на шаге {step} — {len(all_signatures)} уникальных")
                            break

                    # Scroll up — try multiple methods
                    scrolled = self.page.evaluate("""
                        () => {
                            const c = window.__gitax_scroll;
                            if (c) {
                                const st = Math.max(100, c.clientHeight * 0.7);
                                const before = c.scrollTop;
                                c.scrollBy(0, -st);
                                return c.scrollTop !== before;
                            }
                            const containers = document.querySelectorAll(
                                '[class*="messages"],[class*="lenta"],[class*="feed"],' +
                                '[class*="chat"],[class*="dialog"],[class*="scroll"]'
                            );
                            for (const c2 of containers) {
                                if (c2.scrollHeight > c2.clientHeight + 50) {
                                    c2.scrollBy(0, -c2.clientHeight * 0.7);
                                    return true;
                                }
                            }
                            window.scrollBy(0, -window.innerHeight * 0.7);
                            return true;
                        }
                    """)

                    if not scrolled:
                        self.logger.debug(f"Scroll method returned false at step {step}")
                        no_new += 1
                        if no_new >= 15:
                            break

                    step += 1
                    self.page.wait_for_timeout(500)

                except Exception as e:
                    self.logger.debug(f"Scroll error: {e}")
                    no_new += 1
                    if no_new >= 15:
                        break

            # ── Overscroll cycle: force MAX to load older history ──
            print(f"  [OVERSCROLL] Попытка #{overscroll_stale + 1}/{MAX_OVERSCROLL_STALE}...")

            sigs_before = len(all_signatures)

            self.page.evaluate("""
                () => {
                    const c = window.__gitax_scroll;
                    function overscroll() {
                        if (c) {
                            c.scrollTop = 0;
                            c.scrollBy(0, -100);
                            c.dispatchEvent(new WheelEvent('wheel', {
                                deltaY: -500, deltaMode: 0,
                                bubbles: true, cancelable: true
                            }));
                        } else {
                            window.scrollBy(0, -200);
                        }
                    }
                    // Fire multiple overscroll events with delays
                    overscroll();
                    setTimeout(overscroll, 300);
                    setTimeout(overscroll, 700);
                }
            """)

            time.sleep(3)

            # Collect signatures after overscroll
            current_after = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    const sigs = [];
                    for (const m of msgs) {
                        const t = (m.textContent || '').trim();
                        sigs.push(t.slice(0, 120));
                    }
                    return sigs;
                }
            """) or []

            new_after = set(s for s in current_after if s and s not in all_signatures)
            if new_after:
                all_signatures.update(new_after)
                overscroll_stale = 0
                print(f"  [OVERSCROLL] +{len(new_after)} новых сообщений — продолжаем скролл")
                continue  # Back to main scroll loop

            overscroll_stale += 1
            print(f"  [OVERSCROLL] Новых нет ({overscroll_stale}/{MAX_OVERSCROLL_STALE})")
            time.sleep(2)

        total = len(all_signatures)
        print(f"  [SCROLL] Итого: {total} уникальных сообщений")
        self.logger.info(f"Total unique messages collected: {total}")

        if total < 100:
            # Try keyboard PageUp as fallback
            self.logger.info("Trying keyboard PageUp fallback...")
            print(f"  [SCROLL] Клавиатурный скролл (PageUp)...")
            no_new_kb = 0

            for kb in range(200):
                try:
                    self.page.keyboard.press("PageUp")
                    self.page.wait_for_timeout(300)

                    current_kb = self.page.evaluate("""
                        () => {
                            const msgs = document.querySelectorAll('[class*="message"]');
                            const sigs = [];
                            for (const m of msgs) {
                                sigs.push((m.textContent || '').trim().slice(0, 120));
                            }
                            return sigs;
                        }
                    """) or []

                    new_kb = list(set(s for s in current_kb if s and s not in all_signatures))
                    if new_kb:
                        all_signatures.update(new_kb)
                        no_new_kb = 0
                        if kb % 20 == 0:
                            print(f"  [SCROLL] PageUp {kb}: +{len(new_kb)}, всего {len(all_signatures)}")
                    else:
                        no_new_kb += 1
                        if no_new_kb >= 8:
                            break
                except Exception:
                    no_new_kb += 1
                    if no_new_kb >= 8:
                        break

            total = len(all_signatures)
            print(f"  [SCROLL] После PageUp: {total} сообщений")

        return total

    def _collect_pass_sigs(self) -> list[str]:
        """Get current message text signatures from DOM."""
        return self.page.evaluate("""
            () => {
                const msgs = document.querySelectorAll('[class*="message"]');
                const sigs = [];
                for (const m of msgs) {
                    const t = (m.textContent || '').trim();
                    sigs.push(t.slice(0, 120));
                }
                return sigs;
            }
        """) or []

    def _collect_new_message_data(self, new_sigs: list[str]) -> list[dict]:
        """Collect full message data for new signatures from DOM."""
        result = self.page.evaluate("""
            (newSigs) => {
                const newSet = new Set(newSigs);
                const seen = new Set();
                const msgs = document.querySelectorAll('[class*="message"]');
                const result = [];
                for (const m of msgs) {
                    const p = m.parentElement;
                    if (p && p.matches && p.matches('[class*="message"]')) continue;
                    const t = (m.textContent || '').trim();
                    const sig = t.slice(0, 120);
                    if (newSet.has(sig) && !seen.has(sig)) {
                        seen.add(sig);
                        result.push({
                            text: t,
                            html: m.innerHTML || '',
                            classes: m.className || ''
                        });
                    }
                }
                return result;
            }
        """, list(new_sigs))
        return result or []

    def _do_overscroll(self) -> None:
        """Trigger overscroll events to force MAX to load older messages."""
        self.page.evaluate("""
            () => {
                const c = window.__gitax_scroll;
                function fire() {
                    if (c) {
                        c.scrollTop = 0;
                        c.scrollBy(0, -100);
                        c.dispatchEvent(new WheelEvent('wheel', {
                            deltaY: -500, deltaMode: 0,
                            bubbles: true, cancelable: true
                        }));
                    } else {
                        window.scrollBy(0, -200);
                    }
                }
                fire();
                setTimeout(fire, 300);
                setTimeout(fire, 700);
            }
        """)

    def collect_all_messages(self, passes: int = 1, max_stale: int = 0,
                              overscroll_cycles: int = 3,
                              max_reloads: int = 0) -> list[dict]:
        """
        Load ALL messages by scrolling to top while collecting message data
        at each scroll position. Supports multiple passes — after scrolling to
        top, scrolls back to bottom and repeats to load content that MAX may
        have cached differently on subsequent passes.

        When the visible top is reached, performs overscroll cycles to force
        MAX to load older messages. Optionally reloads the page for more coverage.

        Supports two pass modes:
          - Exact passes:  passes=N, max_stale=0 — runs exactly N passes
          - Auto converge:  passes=0, max_stale=N — runs until N consecutive
            passes yield zero new signatures

        Args:
            passes: Number of scroll passes (default 1). Set to 0 for auto.
            max_stale: Stop after N passes with no new signatures (0 = disabled).
            overscroll_cycles: Max overscroll attempts before declaring end (0 = skip).
            max_reloads: Max page reload fallback attempts (0 = skip).

        Returns:
            List of dicts in detection order.
            Each dict: { idx, text, html, classes }
        """
        self._check_connection()

        all_signatures: set[str] = set()
        all_messages: list[dict] = []
        stale_count = 0
        total_passes = 0
        os_stale_count = 0
        pass_limit = passes if passes > 0 else 999
        reload_count = 0

        while True:
            # ── Determine if this is a new pass or a reload pass ──
            is_reload = reload_count > 0 and total_passes == 0
            if is_reload:
                self.navigate()
                self.wait_page_ready()
                self._scroll_to_bottom()
                self.page.wait_for_timeout(3000)
                total_passes += 1
            elif total_passes > 0:
                self._scroll_to_bottom()
                self.page.wait_for_timeout(2000)
                total_passes += 1
            else:
                total_passes += 1

            auto_mode = passes == 0 and max_stale > 0
            pass_label = f"проход {total_passes}/{pass_limit}" if passes > 0 else f"проход {total_passes}"
            if is_reload:
                pass_label = f"reload #{reload_count}"

            print(f"  [SCROLL] {pass_label}, скролл вверх...")

            sigs_before = len(all_signatures)
            found_any = False
            no_new = 0
            overscrolled_this_pass = False

            # Focus scroll container
            self.page.evaluate("""
                () => {
                    const containers = document.querySelectorAll(
                        '[class*="messages"],[class*="lenta"],[class*="feed"],' +
                        '[class*="chat"],[class*="dialog"],[class*="scroll"]'
                    );
                    let best = null;
                    let bestH = 0;
                    for (const c of containers) {
                        if (c.scrollHeight > c.clientHeight + 50 && c.scrollHeight > bestH) {
                            best = c;
                            bestH = c.scrollHeight;
                        }
                    }
                    if (best) {
                        window.__gitax_scroll = best;
                        best.setAttribute('tabindex', '-1');
                        best.focus();
                    } else {
                        window.__gitax_scroll = null;
                    }
                }
            """)
            self.page.wait_for_timeout(300)

            step_in_pass = -1
            scroll_stuck_count = 0
            while True:
                step_in_pass += 1
                try:
                    current_sigs = self._collect_pass_sigs()

                    new_sig_set: set[str] = set()
                    for s in current_sigs:
                        if s and s not in all_signatures:
                            all_signatures.add(s)
                            new_sig_set.add(s)

                    if new_sig_set:
                        no_new = 0
                        found_any = True

                        new_data = self._collect_new_message_data(list(new_sig_set))
                        for d in new_data:
                            all_messages.append({
                                "idx": len(all_messages),
                                "text": d.get("text", ""),
                                "html": d.get("html", ""),
                                "classes": d.get("classes", ""),
                            })

                        if step_in_pass % 15 == 0:
                            print(f"  [SCROLL] Шаг {step_in_pass}: +{len(new_sig_set)} новых, всего {len(all_signatures)}")
                    else:
                        no_new += 1
                        if no_new >= 15:
                            if step_in_pass > 0:
                                print(f"  [SCROLL] Достигнут верх (шаг {step_in_pass}) — {len(all_signatures)} уникальных сообщений")
                            break

                    # Scroll up
                    scrolled = self.page.evaluate("""
                        () => {
                            const c = window.__gitax_scroll;
                            if (c) {
                                const st = Math.max(100, c.clientHeight * 0.7);
                                const before = c.scrollTop;
                                c.scrollBy(0, -st);
                                return c.scrollTop !== before;
                            }
                            const containers = document.querySelectorAll(
                                '[class*="messages"],[class*="lenta"],[class*="feed"],' +
                                '[class*="chat"],[class*="dialog"],[class*="scroll"]'
                            );
                            for (const c2 of containers) {
                                if (c2.scrollHeight > c2.clientHeight + 50) {
                                    c2.scrollBy(0, -c2.clientHeight * 0.7);
                                    return true;
                                }
                            }
                            window.scrollBy(0, -window.innerHeight * 0.7);
                            return true;
                        }
                    """)

                    if not scrolled:
                        # Only count as "stuck" when scroll didn't move AND no new
                        # messages were found. MAX lazy-loads content — scroll position
                        # may stay the same while new messages appear above the viewport.
                        if not new_sig_set:
                            scroll_stuck_count += 1
                            if scroll_stuck_count >= 2:
                                print(f"  [SCROLL] Скролл застрял на шаге {step_in_pass}, лента закончена")
                                break
                        no_new += 1
                        if no_new >= 15:
                            break
                    else:
                        scroll_stuck_count = 0

                    self.page.wait_for_timeout(800)

                except Exception as e:
                    self.logger.debug(f"Scroll error in collect_all_messages: {e}")
                    no_new += 1
                    if no_new >= 15:
                        break

            sigs_after = len(all_signatures)
            new_in_pass = sigs_after - sigs_before

            print(f"  [SCROLL] {pass_label} завершён: +{new_in_pass} новых сигнатур, всего {sigs_after}")

            # ── Overscroll cycles (if enabled) ──
            if overscroll_cycles > 0 and found_any:
                ov_stale = 0
                while ov_stale < overscroll_cycles:
                    print(f"  [OVERSCROLL] Попытка #{ov_stale + 1}/{overscroll_cycles}...")
                    self._do_overscroll()
                    time.sleep(3)

                    os_sigs = self._collect_pass_sigs()
                    new_os = set(s for s in os_sigs if s and s not in all_signatures)
                    if new_os:
                        all_signatures.update(new_os)
                        os_data = self._collect_new_message_data(list(new_os))
                        for d in os_data:
                            all_messages.append({
                                "idx": len(all_messages),
                                "text": d.get("text", ""),
                                "html": d.get("html", ""),
                                "classes": d.get("classes", ""),
                            })
                        print(f"  [OVERSCROLL] +{len(new_os)} новых — ещё один проход")
                        ov_stale = 0
                        # Start a new pass (continue outer while True)
                        overscrolled_this_pass = True
                        break
                    else:
                        ov_stale += 1
                        print(f"  [OVERSCROLL] Новых нет ({ov_stale}/{overscroll_cycles})")

                if overscrolled_this_pass:
                    # Reset stale counters and start a fresh pass
                    if auto_mode:
                        stale_count = 0
                    continue

            # ── Convergence check ──
            if auto_mode:
                if new_in_pass == 0 and not overscrolled_this_pass:
                    stale_count += 1
                    print(f"  [SCROLL] Без прогресса ({stale_count}/{max_stale})")
                    if stale_count >= max_stale:
                        print(f"  [SCROLL] {max_stale} проходов без прогресса — остановка")
                        break
                else:
                    stale_count = 0

                if total_passes >= pass_limit:
                    print(f"  [SCROLL] Достигнут лимит проходов ({pass_limit}) — остановка")
                    break
            else:
                if total_passes >= passes:
                    # ── Page reload fallback ──
                    if max_reloads > 0 and reload_count < max_reloads:
                        reload_count += 1
                        print(f"\n  [SCROLL RELOAD] Перезагрузка страницы ({reload_count}/{max_reloads})...")
                        total_passes = 0  # Reset pass counter for reload cycle
                        break  # Out of inner loop, outer while will handle reload
                    break

        total = len(all_signatures)
        print(f"  [SCROLL] Итого: {total} уникальных сообщений за {total_passes + reload_count} проходов")
        self.logger.info(f"Total unique messages: {len(all_messages)} ({total_passes + reload_count} passes)")

        return all_messages

    def _extract_file_urls(self) -> dict[str, str]:
        """
        Extract filename → download_url map from current DOM.

        Uses a single page.evaluate() JS call that scans all message-like
        elements, applying the same CSS selectors as scan_channel_for_files().

        On failure or empty result, logs debug info (message count, strategy
        match stats, first message outerHTML sample) to help diagnose MAX's
        DOM structure for file messages.

        Returns:
            Dict mapping filename (str) → download URL (str).
            Empty dict if no files found or on error.
        """
        self._check_connection()
        try:
            result = self.page.evaluate(r"""
                () => {
                    const result = {};
                    const seen = new Set();

                    // Debug counters
                    const debug = {
                        totalMessages: 0,
                        byStrategy: { a_download: 0, a_href_download: 0, video: 0, img: 0, genericFile: 0 },
                        withFilenameOnly: 0,  // strategy 5 hit but no URL
                        skippedNoFilename: 0,
                        skippedNoUrl: 0,
                        firstMsgClasses: '',
                        firstMsgTag: '',
                        firstMsgSample: '',
                        archiveMsgSamples: [],  // outerHTML of messages containing .7z
                    };

                    // Find all message-like elements in the feed
                    const messages = document.querySelectorAll(
                        '[class*="message"],[class*="msg"],' +
                        '[class*="lenta-item"],[class*="feed-item"]'
                    );
                    debug.totalMessages = messages.length;

                    if (messages.length > 0) {
                        const first = messages[0];
                        debug.firstMsgClasses = first.className || '';
                        debug.firstMsgTag = first.tagName || '';
                        debug.firstMsgSample = (first.outerHTML || '').slice(0, 1500);
                    }

                    messages.forEach((msg) => {
                        let filename = '';
                        let downloadUrl = '';
                        const msgText = msg.textContent || '';

                        // 1. Direct download links: a[download]
                        const downloadLinks = msg.querySelectorAll('a[download]');
                        for (const a of downloadLinks) {
                            const href = a.getAttribute('href') || '';
                            const name = a.getAttribute('download') || '';
                            if (name && href) {
                                filename = name;
                                downloadUrl = href;
                                debug.byStrategy.a_download++;
                                break;
                            }
                        }

                        // 2. Alternative download links (a[href*="download"])
                        if (!filename) {
                            const altLinks = msg.querySelectorAll(
                                'a[href*="download"],a[href*="attachment"]'
                            );
                            for (const a of altLinks) {
                                const href = a.getAttribute('href') || '';
                                if (href) {
                                    downloadUrl = href;
                                    filename = a.textContent?.trim()
                                        || href.split('/').pop() || '';
                                    debug.byStrategy.a_href_download++;
                                    break;
                                }
                            }
                        }

                        // 3. Video elements with src attribute
                        if (!filename) {
                            const videos = msg.querySelectorAll('video[src]');
                            if (videos.length > 0) {
                                const src = videos[0].getAttribute('src') || '';
                                downloadUrl = src;
                                filename = videos[0].getAttribute('title')
                                    || src.split('/').pop() || 'video.mp4';
                                debug.byStrategy.video++;
                            }
                        }

                        // 4. Image elements (non-emoji, non-avatar)
                        if (!filename) {
                            const imgs = msg.querySelectorAll('img[src]');
                            for (const img of imgs) {
                                const src = img.getAttribute('src') || '';
                                if (src && !src.includes('emoji')
                                    && !src.includes('avatar')) {
                                    downloadUrl = src;
                                    filename = img.getAttribute('alt')
                                        || src.split('/').pop() || 'image.jpg';
                                    debug.byStrategy.img++;
                                    break;
                                }
                            }
                        }

                        // 5. Generic file/attachment indicator classes (NAME ONLY, no URL)
                        if (!filename) {
                            const fileEls = msg.querySelectorAll(
                                '[class*="file"],[class*="attach"]'
                            );
                            for (const el of fileEls) {
                                const title = el.getAttribute('title')
                                    || el.getAttribute('alt') || '';
                                if (title) {
                                    filename = title;
                                    debug.byStrategy.genericFile++;
                                    break;
                                }
                            }
                        }

                        // Collect .7z message samples for debug
                        if (msgText.includes('.7z')) {
                            debug.archiveMsgSamples.push({
                                text: msgText.slice(0, 300),
                                html: (msg.innerHTML || '').slice(0, 2000),
                                outerHTML: (msg.outerHTML || '').slice(0, 2000),
                                className: msg.className || '',
                                tagName: msg.tagName || '',
                                links: Array.from(msg.querySelectorAll('a')).map(a => ({
                                    href: a.getAttribute('href') || '',
                                    download: a.getAttribute('download') || '',
                                    text: (a.textContent || '').trim().slice(0, 100),
                                    rel: a.getAttribute('rel') || '',
                                    target: a.getAttribute('target') || '',
                                    onclick: a.getAttribute('onclick') ? true : false,
                                })),
                                buttons: Array.from(msg.querySelectorAll('button')).map(b => ({
                                    text: (b.textContent || '').trim().slice(0, 100),
                                    onclick: b.getAttribute('onclick') ? true : false,
                                })),
                                dataAttrs: (() => {
                                    const attrs = {};
                                    // NOTE: [data-*] is NOT a valid CSS selector.
                                    // Use JS loop over all elements + attribute check.
                                    const all = msg.querySelectorAll('*');
                                    all.forEach(el => {
                                        Array.from(el.attributes).forEach(a => {
                                            if (a.name.startsWith('data-') && !(a.name in attrs)) {
                                                attrs[a.name] = (a.value || '').slice(0, 200);
                                            }
                                        });
                                    });
                                    return attrs;
                                })(),
                                allAttrs: (() => {
                                    const attrs = {};
                                    Array.from(msg.attributes).forEach(a => {
                                        attrs[a.name] = (a.value || '').slice(0, 200);
                                    });
                                    return attrs;
                                })(),
                            });
                            // Keep max 3 samples to avoid huge payload
                            if (debug.archiveMsgSamples.length >= 3) return;
                        }

                        // Skip messages without filenames
                        if (!filename) {
                            debug.skippedNoFilename++;
                            return;
                        }
                        if (!downloadUrl) {
                            debug.skippedNoUrl++;
                            debug.withFilenameOnly++;
                            return;
                        }

                        // Deduplicate by filename (first occurrence wins)
                        if (seen.has(filename)) return;
                        seen.add(filename);

                        result[filename] = downloadUrl;
                    });

                    // Store debug info for Python-side retrieval
                    window.__gitax_url_extract_debug = debug;

                    return { urlMap: result, debug: debug };
                }
            """)
            url_map = result.get("urlMap", {}) if isinstance(result, dict) else {}
            debug = result.get("debug", {}) if isinstance(result, dict) else {}

            if not isinstance(url_map, dict):
                url_map = {}

            # Log debug info if empty or suspicious
            total_msgs = debug.get("totalMessages", 0)
            skipped_url = debug.get("skippedNoUrl", 0)
            skipped_fn = debug.get("skippedNoFilename", 0)
            strat = debug.get("byStrategy", {})

            if total_msgs == 0:
                self.logger.warning(
                    "_extract_file_urls: 0 message-like elements found in DOM. "
                    "MAX may use different CSS class names."
                )
            elif not url_map and total_msgs > 0:
                self.logger.warning(
                    f"_extract_file_urls: no files found in {total_msgs} messages. "
                    f"Skipped (no filename): {skipped_fn}, (no URL): {skipped_url}. "
                    f"Strategy hits: a[download]={strat.get('a_download',0)}, "
                    f"a[href*=download]={strat.get('a_href_download',0)}, "
                    f"video={strat.get('video',0)}, img={strat.get('img',0)}, "
                    f"genericFile={strat.get('genericFile',0)}. "
                    f"First msg class: {debug.get('firstMsgClasses','')[:100]}"
                )
                # Print to stderr so user sees it in console
                print(
                    f"  [DEBUG] _extract_file_urls: 0 URL в {total_msgs} сообщениях. "
                    f"Стратегии: a[download]={strat.get('a_download',0)}, "
                    f"a[href]={strat.get('a_href_download',0)}, "
                    f"video={strat.get('video',0)}, img={strat.get('img',0)}, "
                    f"genericFile={strat.get('genericFile',0)}",
                    file=sys.stderr,
                )
            elif url_map:
                self.logger.info(
                    f"_extract_file_urls: {len(url_map)} file(s) from {total_msgs} messages. "
                    f"Strategy hits: a[download]={strat.get('a_download',0)}, "
                    f"a[href*=download]={strat.get('a_href_download',0)}"
                )

            # Dump .7z message samples to log for debugging
            samples = debug.get("archiveMsgSamples", [])
            if samples:
                self.logger.info(f"_extract_file_urls: {len(samples)} .7z message(s) in DOM")
                for i, s in enumerate(samples):
                    self.logger.info(
                        f"  .7z msg #{i}: class='{s.get('className','')[:120]}' "
                        f"tag={s.get('tagName','')} "
                        f"text='{s.get('text','')[:200]}'"
                    )
                    self.logger.info(
                        f"  .7z msg #{i} outerHTML (first 1000): {s.get('outerHTML','')[:1000]}"
                    )
                    links = s.get("links", [])
                    if links:
                        self.logger.info(f"  .7z msg #{i}: {len(links)} link(s):")
                        for li in links:
                            self.logger.info(
                                f"    href='{li.get('href','')[:200]}' "
                                f"download='{li.get('download','')}' "
                                f"text='{li.get('text','')[:80]}' "
                                f"onclick={li.get('onclick',False)}"
                            )
                    buttons = s.get("buttons", [])
                    if buttons:
                        self.logger.info(f"  .7z msg #{i}: {len(buttons)} button(s)")
                    data_attrs = s.get("dataAttrs", {})
                    if data_attrs:
                        self.logger.info(f"  .7z msg #{i}: data attrs: {dict(list(data_attrs.items())[:10])}")
                    all_attrs = s.get("allAttrs", {})
                    if all_attrs:
                        self.logger.info(f"  .7z msg #{i}: all attrs: {dict(list(all_attrs.items())[:15])}")

            return url_map
        except Exception:
            self.logger.warning("_extract_file_urls: evaluate failed", exc_info=True)
            return {}

    def _debug_dump_file_messages(self, target_filename: str | None = None) -> dict:
        """
        Debug helper: dump DOM structure of file messages for diagnosis.

        Runs a JS evaluate that scans all message-like elements and returns
        their outerHTML, attributes, links, and button info. If target_filename
        is provided, filters to messages containing that filename.

        Also checks window.__gitax_api_responses for file download URLs.

        Returns:
            Dict with keys:
            - total_messages: int
            - matching_messages: list of dicts with message DOM details
            - api_urls: list of API response URLs found in interceptor
        """
        self._check_connection()
        result: dict = {"total_messages": 0, "matching_messages": [], "api_urls": []}

        try:
            dump = self.page.evaluate("""
                (targetFn) => {
                    const msgs = document.querySelectorAll(
                        '[class*="message"],[class*="msg"],' +
                        '[class*="lenta-item"],[class*="feed-item"]'
                    );
                    const matching = [];
                    let total = msgs.length;

                    msgs.forEach((msg, idx) => {
                        const text = msg.textContent || '';
                        if (targetFn && !text.includes(targetFn)) return;

                        const info = {
                            index: idx,
                            tagName: msg.tagName,
                            className: (msg.className || '').slice(0, 500),
                            textPreview: text.slice(0, 300),
                            outerHTML: (msg.outerHTML || '').slice(0, 3000),
                            innerHTML: (msg.innerHTML || '').slice(0, 1500),
                            attributes: {},
                            links: [],
                            buttons: [],
                            inputs: [],
                            imgs: [],
                            videos: [],
                            audios: [],
                            iframes: [],
                            dataAttrs: {},
                        };

                        // All attributes
                        Array.from(msg.attributes).forEach(a => {
                            info.attributes[a.name] = (a.value || '').slice(0, 300);
                        });

                        // Links
                        msg.querySelectorAll('a').forEach(a => {
                            info.links.push({
                                href: a.getAttribute('href') || '',
                                download: a.getAttribute('download') || '',
                                text: (a.textContent || '').trim().slice(0, 100),
                                rel: a.getAttribute('rel') || '',
                                target: a.getAttribute('target') || '',
                                type: a.getAttribute('type') || '',
                                onclick: !!a.getAttribute('onclick'),
                                className: (a.className || '').slice(0, 100),
                                role: a.getAttribute('role') || '',
                                ariaLabel: a.getAttribute('aria-label') || '',
                            });
                        });

                        // Buttons
                        msg.querySelectorAll('button').forEach(b => {
                            info.buttons.push({
                                text: (b.textContent || '').trim().slice(0, 100),
                                onclick: !!b.getAttribute('onclick'),
                                className: (b.className || '').slice(0, 100),
                                type: b.getAttribute('type') || '',
                                ariaLabel: b.getAttribute('aria-label') || '',
                                value: b.getAttribute('value') || '',
                                formAction: b.getAttribute('formaction') || '',
                            });
                        });

                        // Inputs
                        msg.querySelectorAll('input').forEach(inp => {
                            info.inputs.push({
                                type: inp.getAttribute('type') || '',
                                name: inp.getAttribute('name') || '',
                                value: inp.getAttribute('value') || '',
                                src: inp.getAttribute('src') || '',
                                accept: inp.getAttribute('accept') || '',
                            });
                        });

                        // Images (non-emoji)
                        msg.querySelectorAll('img').forEach(img => {
                            const src = img.getAttribute('src') || '';
                            if (!src.includes('emoji') && !src.includes('avatar')) {
                                info.imgs.push({
                                    src: src.slice(0, 300),
                                    alt: img.getAttribute('alt') || '',
                                    title: img.getAttribute('title') || '',
                                });
                            }
                        });

                        // Video
                        msg.querySelectorAll('video').forEach(v => {
                            info.videos.push({
                                src: v.getAttribute('src') || '',
                                title: v.getAttribute('title') || '',
                                poster: v.getAttribute('poster') || '',
                                className: (v.className || '').slice(0, 100),
                            });
                        });

                        // Audio
                        msg.querySelectorAll('audio').forEach(a => {
                            info.audios.push({
                                src: a.getAttribute('src') || '',
                                title: a.getAttribute('title') || '',
                            });
                        });

                        // iframe
                        msg.querySelectorAll('iframe').forEach(f => {
                            info.iframes.push({
                                src: f.getAttribute('src') || '',
                                title: f.getAttribute('title') || '',
                            });
                        });

                        // data-* attributes from all children
                        // NOTE: [data-*] is NOT a valid CSS selector.
                        // Use JS loop over all elements + attribute check.
                        const dataAttrMap = {};
                        const allElements = msg.querySelectorAll('*');
                        allElements.forEach(el => {
                            Array.from(el.attributes).forEach(a => {
                                if (a.name.startsWith('data-') && !(a.name in dataAttrMap)) {
                                    dataAttrMap[a.name] = (a.value || '').slice(0, 200);
                                }
                            });
                        });
                        info.dataAttrs = dataAttrMap;

                        matching.push(info);
                    });

                    return { totalMessages: total, matching: matching };
                }
            """, target_filename)

            if isinstance(dump, dict):
                result["total_messages"] = dump.get("totalMessages", 0)
                result["matching_messages"] = dump.get("matching", [])
        except Exception as e:
            self.logger.warning(f"_debug_dump_file_messages: evaluate failed: {e}")

        # Also check API responses for file URLs
        try:
            api_responses = self.page.evaluate("() => window.__gitax_api_responses || []")
            if api_responses and isinstance(api_responses, list):
                file_urls = set()
                for entry in api_responses:
                    url = entry.get("url", "")
                    if url and ("file" in url.lower() or "download" in url.lower()
                                or "upload" in url.lower() or "attach" in url.lower()):
                        file_urls.add(url[:300])
                result["api_urls"] = sorted(file_urls)
                if file_urls:
                    self.logger.info(
                        f"_debug_dump_file_messages: found {len(file_urls)} file-related API URLs"
                    )
                    for u in sorted(file_urls):
                        self.logger.info(f"  API URL: {u}")
        except Exception as e:
            self.logger.debug(f"_debug_dump_file_messages: api check failed: {e}")

        return result

    def scan_channel_for_archives(self) -> list[dict]:
        """
        Scan the MAX channel for 7z archive files and group them by archive.

        Collects all messages, extracts filenames of .7z files, groups volumes
        by base archive name, and returns structured archive info.

        Returns:
            List of dicts:
            [
                {
                    "base_name": "documents.7z",
                    "archive_name": "documents",
                    "volume_count": 3,
                    "volumes": ["documents.7z.001", "documents.7z.002", "documents.7z.003"],
                    "message_indices": [5, 12, 18],
                    "volume_urls": {                          # ← NEW
                        "documents.7z.001": "https://...",   # ← NEW
                        "documents.7z.002": "https://...",   # ← NEW
                    },                                         # ← NEW
                },
                ...
            ]
        """
        self._check_connection()
        messages = self.collect_all_messages()
        if not messages:
            self.logger.warning("No messages found in channel")
            return []
        import re
        archive_files: list[tuple[str, int]] = []
        for idx, msg in enumerate(messages):
            text = msg.get("text", "") or ""
            html = msg.get("html", "") or ""
            content = text + " " + html
            matches = re.findall(r'\b([a-zA-Z0-9_\-\.]+\.(?:7z(?:\.\d{3})?))\b', content)
            for match in matches:
                if match.endswith('.7z') or re.search(r'\.7z\.\d{3}$', match):
                    archive_files.append((match, idx))
        if not archive_files:
            self.logger.info("No 7z archive files found in channel")
            return []
        seen: dict[str, int] = {}
        for fn, idx in archive_files:
            if fn not in seen:
                seen[fn] = idx
        filenames = list(seen.keys())
        self.logger.info(f"Found {len(filenames)} archive file(s) in channel")
        groups = group_volumes(filenames)
        result = []
        for group in groups:
            archive_name = group["base_name"].replace(".7z", "")
            msg_indices = [seen[vol] for vol in group["volumes"] if vol in seen]
            result.append({
                "base_name": group["base_name"],
                "archive_name": archive_name,
                "volume_count": group["volume_count"],
                "volumes": group["volumes"],
                "message_indices": msg_indices,
            })

        # Extract download URLs from DOM while it's still populated
        url_map = self._extract_file_urls()
        missing_urls = []
        for arch in result:
            arch["volume_urls"] = {}
            for vol in arch["volumes"]:
                if vol in url_map:
                    arch["volume_urls"][vol] = url_map[vol]
                else:
                    missing_urls.append(vol)

        if missing_urls:
            self.logger.warning(
                f"scan_channel_for_archives: {len(missing_urls)} volume(s) without URL: "
                f"{missing_urls[:5]}{'...' if len(missing_urls) > 5 else ''}"
            )
            print(
                f"  [DEBUG] {len(missing_urls)} том(ов) без download URL. "
                f"Будет использован fallback DOM-запрос.",
                file=sys.stderr,
            )

        self.logger.info(f"Grouped into {len(result)} archive(s)")
        return result

    def parse_message(self, msg: dict) -> dict:
        """
        Classify a single message and extract structured data.

        Returns dict with at least:
            type: "repo_text" | "file" | "other"
            full_name: str | None
            display_name: str | None
            filename: str | None    (file messages)
            volume: str | None      (split volumes, e.g. "001")
        """
        text = msg.get("text", "")
        html = msg.get("html", "")
        idx = msg.get("idx", -1)

        result = {
            "idx": idx,
            "type": "other",
            "full_name": None,
            "display_name": None,
            "filename": None,
            "volume": None,
        }

        # ── Repo text message (contains 📦 and GitHub URL) ──
        repo_url_match = re.search(
            r'github\.com[\/:]([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)',
            text
        )
        if repo_url_match and ("📦" in text or "⭐" in text or "🍴" in text):
            full_name = repo_url_match.group(1).lower()
            # Extract display name from 📦 <name>
            display_match = re.search(r'📦\s*(\S+)', text)
            display_name = display_match.group(1) if display_match else full_name.split("/")[-1]

            version_match = re.search(r'🔖\s*Версия:\s*(\S+)', text)
            version = version_match.group(1) if version_match else ""

            result.update({
                "type": "repo_text",
                "full_name": full_name,
                "display_name": display_name,
                "version": version,
            })
            return result

        # ── File message ──
        file_match = re.search(r'([A-Za-z0-9._-]+\.zip(?:\.7z\.(\d+))?)', text, re.IGNORECASE)
        if file_match:
            full_filename = file_match.group(1)
            volume = file_match.group(2)

            result.update({
                "type": "file",
                "filename": full_filename,
                "volume": volume,
            })
            return result

        return result

    def _resolve_file_owner(self, filename: str, known_repos: dict[str, str]) -> str | None:
        """
        Match a filename (owner-repo-branch.zip) to a known repo from text messages.
        known_repos: {full_name: display_name}
        Matches longest prefix first to avoid false matches (e.g. a/b-c vs a/b).
        """
        name_lower = filename.lower()
        # Sort by prefix length descending — most specific first
        sorted_repos = sorted(known_repos.items(), key=lambda x: len(x[0]), reverse=True)
        for full_name in sorted_repos:
            owner, repo = full_name[0].split("/", 1)
            prefix = f"{owner}-{repo}-"
            if name_lower.startswith(prefix.lower()):
                return full_name[0]
        return None

    def group_messages_by_repo(self, messages: list[dict]) -> dict:
        """
        Group parsed messages by repository.

        Returns:
            {
                "complete": [ ... ],
                "incomplete": [ ... ]
            }
        Each group dict:
            full_name, display_name, text_idx, file_idxs, volumes, version, issue
        """
        # Pass 1: collect all repo_text entries and build full_name lookup
        repo_texts: dict[str, dict] = {}
        file_msgs: list[dict] = []

        for msg in messages:
            parsed = self.parse_message(msg)
            if parsed["type"] == "repo_text":
                fn = parsed["full_name"]
                if fn not in repo_texts:
                    repo_texts[fn] = parsed
            elif parsed["type"] == "file":
                file_msgs.append(parsed)

        # Build known_repos lookup for filename matching
        known_repos = {fn: rt.get("display_name", fn.split("/")[-1]) for fn, rt in repo_texts.items()}

        # Pass 2: assign files to repos
        repo_files: dict[str, list[dict]] = {fn: [] for fn in repo_texts}
        orphaned_files: list[dict] = []

        for fm in file_msgs:
            fn = None
            if fm["filename"]:
                fn = self._resolve_file_owner(fm["filename"], known_repos)
            if fn:
                repo_files.setdefault(fn, []).append(fm)
            else:
                orphaned_files.append(fm)

        # Pass 3: build result groups
        complete = []
        incomplete = []

        for fn, rt in repo_texts.items():
            files = repo_files.get(fn, [])
            file_idxs = [f["idx"] for f in files]
            volumes = [f["volume"] for f in files if f.get("volume")]

            if not files:
                incomplete.append({
                    "full_name": fn,
                    "display_name": rt.get("display_name", fn.split("/")[-1]),
                    "text_idx": rt["idx"],
                    "file_idxs": [],
                    "volumes": [],
                    "version": rt.get("version", ""),
                    "issue": "missing_file",
                })
            elif volumes:
                # Check for missing volumes
                vol_nums = sorted(set(v for v in volumes if v))
                if vol_nums:
                    expected = set(f"{i:03d}" for i in range(1, int(vol_nums[-1]) + 1))
                    missing = expected - set(vol_nums)
                    if missing:
                        incomplete.append({
                            "full_name": fn,
                            "display_name": rt.get("display_name", fn.split("/")[-1]),
                            "text_idx": rt["idx"],
                            "file_idxs": file_idxs,
                            "volumes": vol_nums,
                            "missing_volumes": sorted(missing),
                            "version": rt.get("version", ""),
                            "issue": "missing_volumes",
                        })
                        continue
                complete.append({
                    "full_name": fn,
                    "display_name": rt.get("display_name", fn.split("/")[-1]),
                    "text_idx": rt["idx"],
                    "file_idxs": file_idxs,
                    "volumes": volumes,
                    "version": rt.get("version", ""),
                    "issue": None,
                })
            else:
                complete.append({
                    "full_name": fn,
                    "display_name": rt.get("display_name", fn.split("/")[-1]),
                    "text_idx": rt["idx"],
                    "file_idxs": file_idxs,
                    "volumes": [],
                    "version": rt.get("version", ""),
                    "issue": None,
                })

        # Orphaned files (no text message found) — group by filename to avoid N duplicate entries
        orphan_groups: dict[str, dict] = {}
        for of in orphaned_files:
            fn_key = of.get("filename", "unknown")
            if fn_key not in orphan_groups:
                orphan_groups[fn_key] = {
                    "full_name": fn_key,
                    "display_name": fn_key,
                    "text_idx": None,
                    "file_idxs": [],
                    "volumes": set(),
                    "filename": fn_key,
                    "issue": "missing_text",
                }
            orphan_groups[fn_key]["file_idxs"].append(of["idx"])
            if of.get("volume"):
                orphan_groups[fn_key]["volumes"].add(of["volume"])

        for entry in orphan_groups.values():
            entry["volumes"] = sorted(entry["volumes"])
            incomplete.append(entry)

        return {"complete": complete, "incomplete": incomplete}

    def _quick_extract_repos(self, messages: list[dict]) -> set[str]:
        """
        Fast extraction of repo full_names from a list of message dicts.
        Uses the same regex as parse_message but without full classification.
        Returns a set of "owner/repo" strings.
        """
        found: set[str] = set()
        for msg in messages:
            text = msg.get("text", "")
            match = re.search(
                r'github\.com[\/:]([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)',
                text
            )
            if match:
                fn = match.group(1).lower()
                if "/" in fn:
                    found.add(fn)
        return found

    def audit_channel_completeness(self, known_repos: set[str] | None = None) -> dict:
        """
        Full audit of the MAX channel:
          1. API response interception (fast, covers API-served history)
          2. Internal page state (React/Vue stores)
          3. Iterative DOM scroll until convergence (stale detection)
          4. Classify every message
          5. Group by repo
          6. Show progress against known_repos if provided

        The 3-source approach ensures ANY length feed is fully audited:
          - API: unlimited, catches all messages ever loaded by the page
          - Page state: catches what MAX holds in memory/React store
          - DOM scroll: catches everything MAX renders, iterates until stale

        Args:
            known_repos: Set of "owner/repo" full_names from journal.
                         If provided, shows coverage progress per pass.

        Returns:
            { "complete": [...], "incomplete": [...] }
        """
        print("\n  [SCAN] Проверка целостности публикаций (3 источника)...")

        all_by_sig: dict[str, dict] = {}
        total_known = len(known_repos) if known_repos else 0

        def _add(m: dict):
            sig = (m.get("text", "") or "")[:120]
            if sig and sig not in all_by_sig:
                all_by_sig[sig] = m

        def _cover():
            if total_known == 0:
                return 0, 0
            found = self._quick_extract_repos(list(all_by_sig.values()))
            covered = len(found & known_repos) if known_repos else 0
            return covered, len(found)

        # ── Источник A: API-перехват ──
        print("  [SCAN] Источник A: перехват сетевых ответов...")
        self.collect_all_messages(passes=1, overscroll_cycles=3)  # triggers API calls via one scroll pass
        api_msgs = self._parse_messages_from_api()
        for m in api_msgs:
            _add(m)
        covered, found = _cover()
        if total_known:
            print(f"  [AUDIT] API: найдено {covered}/{total_known} репозиториев")
        else:
            print(f"  [AUDIT] API: {len(api_msgs)} сообщений")

        # ── Источник B: Внутреннее состояние страницы ──
        print("  [SCAN] Источник B: состояние страницы...")
        state_msgs = self._collect_via_page_state()
        for m in state_msgs:
            _add(m)
        covered_b, found_b = _cover()
        if total_known:
            print(f"  [AUDIT] State: +{covered_b - covered} репозиториев (всего {covered_b}/{total_known})")
        else:
            print(f"  [AUDIT] State: {len(state_msgs)} сообщений")

        # ── Источник C: DOM-скролл до схождения ──
        if total_known and covered_b >= total_known:
            print(f"  [AUDIT] ✓ Все {total_known} репозиториев найдены, скролл не требуется")
        else:
            print("  [SCAN] Источник C: итеративный скролл DOM до схождения...")
            print("  [SCAN] Пожалуйста, не трогайте браузер")

            scroll_msgs = self.collect_all_messages(passes=0, max_stale=3, overscroll_cycles=3, max_reloads=3)
            prev_covered = covered_b
            for m in scroll_msgs:
                _add(m)
            covered_c, found_c = _cover()
            new_from_scroll = covered_c - prev_covered
            if total_known:
                print(f"  [AUDIT] DOM скролл: +{new_from_scroll} репозиториев (всего {covered_c}/{total_known})")
            else:
                print(f"  [AUDIT] DOM скролл: {len(scroll_msgs)} сообщений")

        # ── Результат ──
        messages = list(all_by_sig.values())
        print(f"  [SCAN] Всего собрано: {len(messages)} сообщений")

        if total_known > 0:
            found_final = self._quick_extract_repos(messages)
            truly_missing = known_repos - found_final
            print(f"  [AUDIT] Найдено репозиториев: {len(found_final)}/{total_known}")
            if truly_missing:
                print(f"  [AUDIT] Не найдено в канале: {len(truly_missing)} — будут помечены для восстановления")
                # Show top 5 missing
                for fn in sorted(truly_missing)[:5]:
                    print(f"           {fn}")
                if len(truly_missing) > 5:
                    print(f"           ... и ещё {len(truly_missing) - 5}")

        print(f"  [SCAN] Классифицирую...")
        grouped = self.group_messages_by_repo(messages)

        # Attach truly_missing to the result (for caller to use in restoration)
        if total_known > 0:
            grouped["truly_missing"] = truly_missing if 'truly_missing' in locals() else set()
            grouped["found_count"] = len(found_final) if 'found_final' in locals() else 0
            grouped["total_known"] = total_known

        print(f"  [AUDIT] Complete: {len(grouped['complete'])}, "
              f"Incomplete: {len(grouped['incomplete'])}")

        grouped["channel_messages"] = messages

        return grouped

    def verify_repo_publication(self, full_name: str) -> bool:
        """
        Quick check after upload: verify the repo has both text + file in the feed.
        Only scans recent messages (last 20) — does NOT scroll to top.
        """
        self._check_connection()

        script = f"""
            () => {{
                const msgs = document.querySelectorAll('[class*="message"]');
                const lastN = Math.min(msgs.length, 20);
                const startIdx = msgs.length - lastN;

                let foundText = false;
                let foundFile = false;

                const searchLower = '{full_name.lower()}'.replace('/', '-');

                for (let i = startIdx; i < msgs.length; i++) {{
                    const text = msgs[i].textContent || '';

                    if (text.includes('📦') && text.toLowerCase().includes('{full_name.lower()}')) {{
                        foundText = true;
                        continue;
                    }}

                    if (text.toLowerCase().includes(searchLower) && /\\.zip/i.test(text)) {{
                        foundFile = true;
                        continue;
                    }}
                }}

                return foundText && foundFile;
            }}
        """

        try:
            return bool(self.page.evaluate(script))
        except Exception:
            return False

    def _click_delete_element(self, x: float, y: float) -> bool:
        """
        Click an element found near the message, then handle the confirmation dialog.
        After clicking delete, waits for confirmation popup and clicks it.
        """
        self.page.mouse.click(x, y)
        self.page.wait_for_timeout(500)
        return self._handle_delete_confirmation()

    def _find_delete_button_after_hover(self) -> dict | None:
        """
        After hovering over a message, scan the page for any clickable
        delete-related element (button, icon, text, etc.).
        """
        return self.page.evaluate("""
            () => {
                const all = document.querySelectorAll('*');
                const msgRect = window.__gitax_hovered_rect;
                const nearY = msgRect ? msgRect.y : 0;
                const nearH = msgRect ? msgRect.h : 200;

                for (const el of all) {
                    if (el.offsetHeight === 0 || el.offsetParent === null) continue;
                    if (el.offsetWidth > 400 && el.offsetHeight > 200) continue;

                    const text = (el.textContent || '').trim();
                    const cls = el.getAttribute('class') || '';
                    const aria = el.getAttribute('aria-label') || '';
                    const title = el.getAttribute('title') || '';
                    const tag = el.tagName.toLowerCase();

                    const allText = text + ' ' + cls + ' ' + aria + ' ' + title;

                    // Must be near the hovered message
                    if (msgRect) {
                        try {
                            const r = el.getBoundingClientRect();
                            if (Math.abs(r.y - nearY) > nearH * 4) continue;
                        } catch(e) { continue; }
                    }

                    // Check text (including "Delete for all")
                    if (/delete|удалить|remove|trash|✕|×|🗑|delete for all|удалить для всех|удалить сообщение/i.test(allText)) {
                        const r = el.getBoundingClientRect();
                        return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height, text: text.slice(0, 40) };
                    }

                    // Check for SVG trash icon
                    if (tag === 'svg' || tag === 'path' || tag === 'use') {
                        const parentText = (el.parentElement?.textContent || '').trim();
                        const parentCls = (el.parentElement?.getAttribute('class') || '');
                        if (/delete|trash|remove|удалить/i.test(parentCls) || /delete|trash|remove|удалить/i.test(parentText)) {
                            const r = el.getBoundingClientRect();
                            return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height, text: '(svg)', fromSvg: true };
                        }
                    }
                }
                return null;
            }
        """)

    def _handle_delete_confirmation(self) -> bool:
        """
        Handle the "Delete for all" / "Удалить для всех" confirmation dialog
        that appears after clicking the delete button.
        Waits, finds the confirm button, clicks it with real mouse.
        Returns True if confirmation was handled.
        """
        self.page.wait_for_timeout(800)

        for attempt in range(8):
            btn_rect = self.page.evaluate("""
                () => {
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        if (el.offsetHeight === 0 || el.offsetParent === null) continue;
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        const t = (el.textContent || '').trim().toLowerCase();
                        // Look for the confirm button via aria or exact text
                        if (aria === 'delete for all' || aria === 'удалить для всех') {
                            const r = el.getBoundingClientRect();
                            return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
                        }
                        // Fallback: button element with matching text
                        if (el.tagName === 'BUTTON' && (t === 'delete for all' || t === 'удалить для всех')) {
                            const r = el.getBoundingClientRect();
                            return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
                        }
                    }
                    return null;
                }
            """)

            if btn_rect:
                self.page.mouse.click(btn_rect['x'], btn_rect['y'])
                self.logger.debug("Delete confirmed")
                self.page.wait_for_timeout(500)
                return True

            self.page.wait_for_timeout(400)

        self.logger.warning("Delete confirmation dialog not found")
        return False

    def _locate_and_delete_by_text(self, search_text: str) -> bool:
        """
        Find a visible message element containing search_text,
        hover, open action menu (three dots), find Delete, click, confirm.

        MAX flow: Hover → click "Действия с сообщением" (3-dot button) →
        popup menu with "Delete" (danger style) → click → confirm dialog.

        Returns True if deleted.
        """
        # Step 1: Find the message in visible DOM
        rect = self.page.evaluate(f"""
            () => {{
                const msgs = document.querySelectorAll('[class*="message"]');
                const search = {repr(search_text.lower())};
                for (const msg of msgs) {{
                    const t = (msg.textContent || '').toLowerCase();
                    if (t.includes(search)) {{
                        msg.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                        const r = msg.getBoundingClientRect();
                        window.__gitax_hovered_rect = {{ x: r.x, y: r.y, w: r.width, h: r.height }};
                        return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
                    }}
                }}
                return null;
            }}
        """)
        if not rect:
            return False

        # Step 2: Hover to reveal action buttons
        self.page.mouse.move(rect['x'], rect['y'])
        self.page.wait_for_timeout(800)

        # Step 3: Click "Действия с сообщением" button (the 3-dot menu)
        actions_btn = self.page.evaluate("""
            () => {
                const all = document.querySelectorAll('*');
                const msgR = window.__gitax_hovered_rect;
                for (const el of all) {
                    if (el.offsetHeight === 0 || el.offsetParent === null) continue;
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    if (aria.includes('действия с сообщением') || aria.includes('message actions')) {
                        const r = el.getBoundingClientRect();
                        return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
                    }
                }
                return null;
            }
        """)
        if actions_btn:
            self.page.mouse.click(actions_btn['x'], actions_btn['y'])
            self.page.wait_for_timeout(600)

            # Step 4: Find "Delete" in the popup menu and CLICK it with real mouse
            delete_btn_rect = self.page.evaluate("""
                () => {
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        if (el.offsetHeight === 0 || el.offsetParent === null) continue;
                        const t = (el.textContent || '').trim().toLowerCase();
                        const role = el.getAttribute('role') || '';
                        if (t === 'delete' && role === 'menuitem') {
                            const r = el.getBoundingClientRect();
                            return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
                        }
                    }
                    return null;
                }
            """)
            if delete_btn_rect:
                self.page.mouse.click(delete_btn_rect['x'], delete_btn_rect['y'])
                self.page.wait_for_timeout(800)
                return self._handle_delete_confirmation()

        # Step 5: Try right-click context menu (may work for file messages)
        self.page.mouse.click(rect['x'], rect['y'], button='right')
        self.page.wait_for_timeout(700)

        ctx_delete = self.page.evaluate("""
            () => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (el.offsetHeight === 0 || el.offsetParent === null) continue;
                    const t = (el.textContent || '').trim().toLowerCase();
                    const role = el.getAttribute('role') || '';
                    if (t === 'delete' && role === 'menuitem') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        if ctx_delete:
            self.page.wait_for_timeout(300)
            return self._handle_delete_confirmation()

        return False

    def delete_messages_by_content(self, search_terms: list[str], label: str = "") -> int:
        """
        Scroll down through the feed, find and DELETE ALL messages
        matching any of the search_terms (case-insensitive).

        Each term can match MULTIPLE messages (e.g. all 20 file volumes).
        Keeps deleting until the entire feed is exhausted.

        Returns count of deleted messages.
        """
        self._check_connection()
        label_str = f" ({label})" if label else ""
        print(f"  [DELETE{label_str}] Поиск и удаление сообщений...")

        deleted = 0
        failed_sigs: set[str] = set()
        no_new = 0
        no_delete_in_row = 0
        scroll_sigs: set[str] = set()

        for step in range(500):
            if no_delete_in_row >= 10:
                break

            # Find all messages matching ANY search term in the visible DOM
            matches = self.page.evaluate(f"""
                () => {{
                    const terms = {[s.lower() for s in search_terms]};
                    const msgs = document.querySelectorAll('[class*="message"]');
                    const result = [];
                    for (const msg of msgs) {{
                        const t = (msg.textContent || '').trim();
                        if (!t) continue;
                        const lower = t.toLowerCase();
                        for (const term of terms) {{
                            if (lower.includes(term)) {{
                                result.push({{ sig: t.slice(0, 80), idx: result.length }});
                                break;
                            }}
                        }}
                    }}
                    return result;
                }}
            """) or []

            if matches:
                no_new = 0
                found_and_deleted = False

                for m in matches:
                    sig = m['sig']
                    if sig in failed_sigs:
                        continue

                    print(f"    [DELETE{label_str}] Найдено: \"{sig[:50]}...\"")
                    if self._locate_and_delete_by_text(sig[:50]):
                        deleted += 1
                        found_and_deleted = True
                        no_delete_in_row = 0
                        print(f"    ✓ Удалено ({deleted})")
                        self.page.wait_for_timeout(800)
                        break  # DOM changed, re-evaluate in next step
                    else:
                        failed_sigs.add(sig)
                        print(f"    ⚠ Не удалось удалить")

                if not found_and_deleted:
                    no_delete_in_row += 1

                # Track scroll sigs for end-of-feed detection
                for m in matches:
                    scroll_sigs.add(m['sig'])
            else:
                no_new += 1
                if no_new >= 10:
                    break

            # Scroll down one viewport
            self.page.evaluate("""
                () => {
                    const c = window.__gitax_scroll;
                    if (c) {
                        const step = Math.max(100, c.clientHeight * 0.7);
                        c.scrollBy(0, step);
                    } else {
                        window.scrollBy(0, window.innerHeight * 0.7);
                    }
                }
            """)
            self.page.wait_for_timeout(400)

        print(f"  [DELETE{label_str}] Итого удалено: {deleted}")
        return deleted

    def delete_messages_by_texts(self, target_texts: set[str], label: str = "") -> int:
        """
        Multi-pass deletion: scroll to top, traverse down deleting matching messages.
        After reaching the bottom, repeats from the top if any targets remain,
        up to MAX_PASSES times. Targets that fail deletion are kept for retry.

        Uses exact text matching (not substring) — eliminates false positives
        from search terms matching unrelated messages.

        Args:
            target_texts: Set of exact message full texts to match and delete.
            label: Optional display label for logging.

        Returns:
            Set of message texts that were successfully deleted.
        """
        self._check_connection()
        label_str = f" ({label})" if label else ""
        total_targets = len(target_texts)

        if not target_texts:
            return 0

        remaining = set(target_texts)
        deleted = 0
        MAX_PASSES = 3
        MAX_STALE_SCROLLS = 10

        for current_pass in range(1, MAX_PASSES + 1):
            if not remaining:
                break

            if current_pass == 1:
                print(f"  [DELETE{label_str}] Удаление {total_targets} сообщений, проход {current_pass}/{MAX_PASSES}...")
                self.navigate()
                self.wait_page_ready()
            else:
                print(f"  [DELETE{label_str}] Повторный проход {current_pass}/{MAX_PASSES}, осталось: {len(remaining)}...")

            self.scroll_to_top()
            self.page.wait_for_timeout(500)

            no_match_in_row = 0

            while True:
                if not remaining:
                    print(f"  [DELETE{label_str}] ✓ Все цели удалены")
                    break

                visible = self.page.evaluate("""
                    () => {
                        const msgs = document.querySelectorAll('[class*="message"]');
                        const result = [];
                        for (const m of msgs) {
                            const t = (m.textContent || '').trim();
                            if (t) {
                                result.push(t);
                            }
                        }
                        return result;
                    }
                """) or []

                match_found = False
                for v in visible:
                    if v in remaining:
                        match_found = True
                        no_match_in_row = 0
                        snippet = v[:60]
                        print(f"  [DELETE{label_str}] Найдено совпадение: \"{snippet}...\"")
                        if self._locate_and_delete_by_text(v[:50]):
                            deleted += 1
                            remaining.discard(v)
                            print(f"  ✓ Удалено ({deleted}/{total_targets})")
                            self.page.wait_for_timeout(800)
                        else:
                            print(f"  ⚠ Не удалось удалить (будет повторено)")
                        break

                if not match_found:
                    no_match_in_row += 1
                    if no_match_in_row >= MAX_STALE_SCROLLS:
                        print(f"  [DELETE{label_str}] Проход {current_pass} завершён, осталось целей: {len(remaining)}")
                        break

                self.page.evaluate("""
                    () => {
                        const c = window.__gitax_scroll;
                        if (c) {
                            const st = Math.max(100, c.clientHeight * 0.7);
                            c.scrollBy(0, st);
                        } else {
                            window.scrollBy(0, window.innerHeight * 0.7);
                        }
                    }
                """)
                self.page.wait_for_timeout(400)

        if remaining:
            print(f"  [DELETE{label_str}] Не удалось удалить {len(remaining)} сообщений после {MAX_PASSES} проходов:")
            for r in sorted(remaining):
                print(f"       \"{r[:60]}...\"")

        print(f"  [DELETE{label_str}] Итого удалено: {deleted}/{total_targets}")
        return set(target_texts) - remaining

    # ──────────────────────────────────────────────
    # Delete ALL messages
    # ──────────────────────────────────────────────

    def _ensure_messages_loaded(self, timeout: float = 25) -> bool:
        """Wait until at least one message with text appears in the DOM."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            ok = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    for (const m of msgs)
                        if ((m.textContent || '').trim().length > 5) return true;
                    return false;
                }
            """)
            if ok:
                return True
            self.page.wait_for_timeout(500)
        return False

    def delete_all_messages(self) -> int:
        """
        Delete ALL messages in the channel feed.
        Stays at the bottom (newest messages), deletes the last visible
        message, repeats until the viewport runs out, then scrolls up
        to load the next batch.  Continues until no messages remain.
        """
        self._check_connection()

        # -- Wait for DOM to contain rendered messages --
        print("  [DELETE ALL] Загрузка сообщений...", end="", flush=True)
        loaded = self._ensure_messages_loaded(25)
        if not loaded:
            print("  timeout")
            return 0
        print(f" OK")

        # -- Scroll to bottom (newest messages) --
        self._scroll_to_bottom()
        self.page.wait_for_timeout(1000)

        total = self.page.evaluate(
            "() => document.querySelectorAll('[class*=\"message\"]').length"
        ) or 0
        print(f"  [DELETE ALL] Удаляю ~{total} сообщений (снизу вверх)...")

        deleted = 0
        failed_texts: set[str] = set()
        empty_scrolls = 0

        for _ in range(10000):
            last_text = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    for (let i = msgs.length - 1; i >= 0; i--) {
                        const t = (msgs[i].textContent || '').trim();
                        if (t) return t.slice(0, 80);
                    }
                    return null;
                }
            """)

            if not last_text:
                cur = self.page.evaluate(
                    "() => document.querySelectorAll('[class*=\"message\"]').length"
                ) or 0
                if cur == 0:
                    break
                empty_scrolls += 1
                if empty_scrolls > 8:
                    print(f"\n  [DELETE ALL] Больше сообщений не найдено")
                    break
                self.page.evaluate("""
                    () => {
                        const c = window.__gitax_scroll;
                        if (c) c.scrollBy(0, -c.clientHeight * 0.5);
                        else window.scrollBy(0, -window.innerHeight * 0.5);
                    }
                """)
                self.page.wait_for_timeout(600)
                continue

            empty_scrolls = 0

            if last_text in failed_texts:
                self.page.evaluate("""
                    () => {
                        const c = window.__gitax_scroll;
                        if (c) c.scrollBy(0, -c.clientHeight * 0.25);
                    }
                """)
                self.page.wait_for_timeout(300)
                continue

            if self._locate_and_delete_by_text(last_text[:50]):
                deleted += 1
                if deleted % 5 == 0:
                    print(f"\r  [DELETE ALL] Удалено: {deleted}", end="", flush=True)
                self.page.wait_for_timeout(400)
            else:
                failed_texts.add(last_text)
                print(f"\n  ⚠ Не удалось: \"{last_text[:40]}...\"")

        print()
        remaining = self.page.evaluate(
            "() => document.querySelectorAll('[class*=\"message\"]').length"
        ) or 0
        print(f"  [DELETE ALL] Готово. Удалено: {deleted}, осталось: {remaining}")
        return deleted

    def inspect_message_actions(self, msg_index: int):
        """
        Debug helper — prints all visible elements near a message.
        Call after hovering over a message to see what MAX shows.
        """
        self._check_connection()
        info = self.page.evaluate(f"""
            () => {{
                const msgs = document.querySelectorAll('[class*="message"]');
                const msg = msgs[{msg_index}];
                if (!msg) return 'message_not_found';

                msg.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                const rect = msg.getBoundingClientRect();

                const visible = [];
                const all = document.querySelectorAll('*');
                for (const el of all) {{
                    if (el.offsetHeight === 0 || el.offsetParent === null) continue;
                    const r = el.getBoundingClientRect();
                    if (Math.abs(r.y - rect.y) < rect.h * 3) {{
                        const t = (el.textContent || '').trim().slice(0, 60);
                        const cls = (el.getAttribute('class') || '').slice(0, 60);
                        const tag = el.tagName;
                        visible.push({{ tag: tag, text: t || '(no text)', cls: cls, x: r.x, y: r.y, w: r.width, h: r.height }});
                    }}
                    if (visible.length > 40) break;
                }}
                return visible;
            }}
        """)

        if isinstance(info, str):
            print(f"  [INSPECT] {info}")
            return

        print(f"\n  [INSPECT] Elements near message #{msg_index}:")
        for el in info:
            print(f"    <{el['tag']}> text=\"{el['text']}\"")
            print(f"      class={el['cls']}")
            print(f"      pos=({el['x']:.0f},{el['y']:.0f}) size={el['w']}x{el['h']}")

    def _confirm_delete(self):
        """Legacy — delegates to _handle_delete_confirmation."""
        self._handle_delete_confirmation()

    # ─────────────────────────────────────────────
    # Export messages to file
    # ─────────────────────────────────────────────

    def _collect_enrich_new(self, known_sigs: set[str]) -> list[dict]:
        """
        Single-phase enrichment: ONE page.evaluate that skips known messages
        and returns full data only for new ones.

        Replaces the two-phase approach (_collect_pass_sigs + _collect_full_for_sigs)
        which made 2 DOM traversals per scroll step. This makes only 1.

        Args:
            known_sigs: Set of text signatures (text[:120]) already collected.

        Returns:
            List of full message dicts for messages NOT in known_sigs.
        """
        self._check_connection()

        try:
            result = self.page.evaluate("""
                (knownSigs) => {
                    const knownSet = new Set(knownSigs);
                    const msgs = document.querySelectorAll('[class*="message"]');
                    const results = [];
                    const seen = new Set();

                    for (const m of msgs) {
                        // Skip nested message elements
                        const p = m.parentElement;
                        if (p && p.matches && p.matches('[class*="message"]')) continue;

                        const text = (m.textContent || '').trim();
                        const sig = text.slice(0, 120);
                        if (!sig || knownSet.has(sig) || seen.has(sig)) continue;
                        seen.add(sig);

                        // Extract sender name
                        let sender = '';
                        const senderSelectors = [
                            '[class*="sender"]', '[class*="author"]', '[class*="name"]',
                            '[class*="from"]', '[class*="user"]'
                        ];
                        for (const sel of senderSelectors) {
                            const el = m.querySelector(sel);
                            if (el && el.textContent && el.textContent.trim()) {
                                sender = el.textContent.trim();
                                break;
                            }
                        }

                        // Extract timestamp
                        let timestamp = '';
                        const timeSelectors = [
                            '[class*="time"]', '[class*="date"]', '[class*="when"]',
                            '[class*="stamp"]', 'time', '[datetime]'
                        ];
                        for (const sel of timeSelectors) {
                            const el = m.querySelector(sel);
                            if (el) {
                                timestamp = el.getAttribute('datetime') || el.textContent.trim();
                                if (timestamp) break;
                            }
                        }

                        // Determine direction (out/in)
                        const classes = (m.className || '').toString();
                        let direction = 'unknown';
                        if (/message--?out|outgoing|sent|mine/i.test(classes)) {
                            direction = 'out';
                        } else if (/message--?in|incoming|received|theirs/i.test(classes)) {
                            direction = 'in';
                        }

                        // Extract attachments
                        const attachments = [];
                        const attachSelectors = [
                            '[class*="file"]', '[class*="attach"]', '[class*="preview"]',
                            '[class*="document"]', '[class*="media"]'
                        ];
                        for (const sel of attachSelectors) {
                            const els = m.querySelectorAll(sel);
                            for (const el of els) {
                                const name = el.getAttribute('title') ||
                                             el.getAttribute('alt') ||
                                             (el.textContent || '').trim().slice(0, 200);
                                const size = el.querySelector('[class*="size"]');
                                if (name) {
                                    attachments.push({
                                        name: name,
                                        size: size ? size.textContent.trim() : ''
                                    });
                                }
                            }
                        }

                        // Extract reactions
                        const reactions = [];
                        const reactSelectors = [
                            '[class*="reaction"]', '[class*="emoji"]', '[class*="like"]'
                        ];
                        for (const sel of reactSelectors) {
                            const els = m.querySelectorAll(sel);
                            for (const el of els) {
                                const reactText = (el.textContent || '').trim();
                                if (reactText) {
                                    reactions.push(reactText);
                                }
                            }
                        }

                        // Check if reply/forward
                        const isReply = /reply|forward|переслан|ответ/i.test(classes) ||
                                       !!m.querySelector('[class*="reply"], [class*="forward"]');

                        results.push({
                            text: text,
                            html: m.innerHTML || '',
                            classes: classes.slice(0, 500),
                            sender: sender,
                            timestamp: timestamp,
                            direction: direction,
                            attachments: attachments,
                            reactions: reactions,
                            is_reply: isReply
                        });
                    }

                    return results;
                }
            """, list(known_sigs))
            return result or []
        except Exception as e:
            self.logger.debug(f"Enrich new error: {e}")
            return []

    def _collect_full_for_sigs(self, target_sigs: list[str]) -> list[dict]:
        """
        Extract full message data ONLY for messages matching given signatures.

        Much faster than _collect_full_batch because it skips messages we
        already know about and only enriches new ones.

        Args:
            target_sigs: List of text signatures (first 120 chars) to look for.

        Returns:
            List of dicts with keys: text, html, classes, sender, timestamp,
            direction, attachments, reactions, is_reply
        """
        self._check_connection()

        try:
            result = self.page.evaluate("""
                (targetSigs) => {
                    const targetSet = new Set(targetSigs);
                    const msgs = document.querySelectorAll('[class*="message"]');
                    const results = [];
                    const seen = new Set();

                    for (const m of msgs) {
                        // Skip nested message elements
                        const p = m.parentElement;
                        if (p && p.matches && p.matches('[class*="message"]')) continue;

                        const text = (m.textContent || '').trim();
                        const sig = text.slice(0, 120);
                        if (!sig || !targetSet.has(sig) || seen.has(sig)) continue;
                        seen.add(sig);

                        // Extract sender name
                        let sender = '';
                        const senderSelectors = [
                            '[class*="sender"]', '[class*="author"]', '[class*="name"]',
                            '[class*="from"]', '[class*="user"]'
                        ];
                        for (const sel of senderSelectors) {
                            const el = m.querySelector(sel);
                            if (el && el.textContent && el.textContent.trim()) {
                                sender = el.textContent.trim();
                                break;
                            }
                        }

                        // Extract timestamp
                        let timestamp = '';
                        const timeSelectors = [
                            '[class*="time"]', '[class*="date"]', '[class*="when"]',
                            '[class*="stamp"]', 'time', '[datetime]'
                        ];
                        for (const sel of timeSelectors) {
                            const el = m.querySelector(sel);
                            if (el) {
                                timestamp = el.getAttribute('datetime') || el.textContent.trim();
                                if (timestamp) break;
                            }
                        }

                        // Determine direction (out/in)
                        const classes = (m.className || '').toString();
                        let direction = 'unknown';
                        if (/message--?out|outgoing|sent|mine/i.test(classes)) {
                            direction = 'out';
                        } else if (/message--?in|incoming|received|theirs/i.test(classes)) {
                            direction = 'in';
                        }

                        // Extract attachments
                        const attachments = [];
                        const attachSelectors = [
                            '[class*="file"]', '[class*="attach"]', '[class*="preview"]',
                            '[class*="document"]', '[class*="media"]'
                        ];
                        for (const sel of attachSelectors) {
                            const els = m.querySelectorAll(sel);
                            for (const el of els) {
                                const name = el.getAttribute('title') ||
                                             el.getAttribute('alt') ||
                                             (el.textContent || '').trim().slice(0, 200);
                                const size = el.querySelector('[class*="size"]');
                                if (name) {
                                    attachments.push({
                                        name: name,
                                        size: size ? size.textContent.trim() : ''
                                    });
                                }
                            }
                        }

                        // Extract reactions
                        const reactions = [];
                        const reactSelectors = [
                            '[class*="reaction"]', '[class*="emoji"]', '[class*="like"]'
                        ];
                        for (const sel of reactSelectors) {
                            const els = m.querySelectorAll(sel);
                            for (const el of els) {
                                const reactText = (el.textContent || '').trim();
                                if (reactText) {
                                    reactions.push(reactText);
                                }
                            }
                        }

                        // Check if reply/forward
                        const isReply = /reply|forward|переслан|ответ/i.test(classes) ||
                                       !!m.querySelector('[class*="reply"], [class*="forward"]');

                        results.push({
                            text: text,
                            html: m.innerHTML || '',
                            classes: classes.slice(0, 500),
                            sender: sender,
                            timestamp: timestamp,
                            direction: direction,
                            attachments: attachments,
                            reactions: reactions,
                            is_reply: isReply
                        });
                    }

                    return results;
                }
            """, list(target_sigs))
            return result or []
        except Exception as e:
            self.logger.debug(f"Full sigs collection error: {e}")
            return []

    def _collect_full_batch(self) -> list[dict]:
        """
        Extract full message data from all visible [class*="message"] elements in DOM.

        NOTE: This is slow — it parses ALL visible messages. Prefer
        _scroll_and_collect_full which uses the two-phase sigs-then-enrich approach.

        Returns:
            List of dicts with keys: text, html, classes, sender, timestamp,
            direction, attachments, reactions, is_reply
        """
        self._check_connection()

        try:
            result = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    const results = [];
                    const seen = new Set();

                    for (const m of msgs) {
                        // Skip nested message elements
                        const p = m.parentElement;
                        if (p && p.matches && p.matches('[class*="message"]')) continue;

                        const text = (m.textContent || '').trim();
                        const sig = text.slice(0, 120);
                        if (!sig || seen.has(sig)) continue;
                        seen.add(sig);

                        // Extract sender name
                        let sender = '';
                        const senderSelectors = [
                            '[class*="sender"]', '[class*="author"]', '[class*="name"]',
                            '[class*="from"]', '[class*="user"]'
                        ];
                        for (const sel of senderSelectors) {
                            const el = m.querySelector(sel);
                            if (el && el.textContent && el.textContent.trim()) {
                                sender = el.textContent.trim();
                                break;
                            }
                        }

                        // Extract timestamp
                        let timestamp = '';
                        const timeSelectors = [
                            '[class*="time"]', '[class*="date"]', '[class*="when"]',
                            '[class*="stamp"]', 'time', '[datetime]'
                        ];
                        for (const sel of timeSelectors) {
                            const el = m.querySelector(sel);
                            if (el) {
                                timestamp = el.getAttribute('datetime') || el.textContent.trim();
                                if (timestamp) break;
                            }
                        }

                        // Determine direction (out/in)
                        const classes = (m.className || '').toString();
                        let direction = 'unknown';
                        if (/message--?out|outgoing|sent|mine/i.test(classes)) {
                            direction = 'out';
                        } else if (/message--?in|incoming|received|theirs/i.test(classes)) {
                            direction = 'in';
                        }

                        // Extract attachments
                        const attachments = [];
                        const attachSelectors = [
                            '[class*="file"]', '[class*="attach"]', '[class*="preview"]',
                            '[class*="document"]', '[class*="media"]'
                        ];
                        for (const sel of attachSelectors) {
                            const els = m.querySelectorAll(sel);
                            for (const el of els) {
                                const name = el.getAttribute('title') ||
                                             el.getAttribute('alt') ||
                                             (el.textContent || '').trim().slice(0, 200);
                                const size = el.querySelector('[class*="size"]');
                                if (name) {
                                    attachments.push({
                                        name: name,
                                        size: size ? size.textContent.trim() : ''
                                    });
                                }
                            }
                        }

                        // Extract reactions
                        const reactions = [];
                        const reactSelectors = [
                            '[class*="reaction"]', '[class*="emoji"]', '[class*="like"]'
                        ];
                        for (const sel of reactSelectors) {
                            const els = m.querySelectorAll(sel);
                            for (const el of els) {
                                const reactText = (el.textContent || '').trim();
                                if (reactText) {
                                    reactions.push(reactText);
                                }
                            }
                        }

                        // Check if reply/forward
                        const isReply = /reply|forward|переслан|ответ/i.test(classes) ||
                                       !!m.querySelector('[class*="reply"], [class*="forward"]');

                        results.push({
                            text: text,
                            html: m.innerHTML || '',
                            classes: classes.slice(0, 500),
                            sender: sender,
                            timestamp: timestamp,
                            direction: direction,
                            attachments: attachments,
                            reactions: reactions,
                            is_reply: isReply
                        });
                    }

                    return results;
                }
            """)
            return result or []
        except Exception as e:
            self.logger.debug(f"Full batch collection error: {e}")
            return []

    def _scroll_and_collect_full(self, passes: int = 3) -> list[dict]:
        """
        Scroll through the entire message feed and collect full message data.

        SINGLE-PHASE approach (fastest):
          Each scroll step calls _collect_enrich_new() which does ONE DOM
          traversal that skips already-known messages and returns full data
          for new ones. This halves the round-trips compared to the old
          two-phase approach (_collect_pass_sigs + _collect_full_for_sigs).

        Args:
            passes: Number of scroll passes to make

        Returns:
            List of message dicts with full data
        """
        self._check_connection()

        all_signatures: set[str] = set()
        all_messages: list[dict] = []
        pass_count = 0

        for pass_num in range(passes):
            pass_count += 1
            sigs_before = len(all_signatures)

            print(f"  [EXPORT] Проход {pass_count}/{passes}, скролл вверх...")

            # Focus scroll container (same as collect_all_messages)
            self.page.evaluate("""
                () => {
                    const containers = document.querySelectorAll(
                        '[class*="messages"],[class*="lenta"],[class*="feed"],' +
                        '[class*="chat"],[class*="dialog"],[class*="scroll"]'
                    );
                    let best = null;
                    let bestH = 0;
                    for (const c of containers) {
                        if (c.scrollHeight > c.clientHeight + 50 && c.scrollHeight > bestH) {
                            best = c;
                            bestH = c.scrollHeight;
                        }
                    }
                    if (best) {
                        window.__gitax_scroll = best;
                        best.setAttribute('tabindex', '-1');
                        best.focus();
                    } else {
                        window.__gitax_scroll = null;
                    }
                }
            """)
            self.page.wait_for_timeout(300)

            # Scroll up and collect — SINGLE-PHASE: one evaluate per step
            no_new = 0
            step = 0
            scroll_stuck_count = 0
            sigs_stagnant = 0  # Track when signatures stop growing
            while no_new < 15:
                step += 1
                try:
                    # Single call: skip known sigs, return full data for new ones
                    enriched = self._collect_enrich_new(all_signatures)
                    new_count = len(enriched)
                    sigs_before_step = len(all_signatures)

                    if new_count > 0:
                        # Update signatures from new messages
                        for msg in enriched:
                            sig = msg.get("text", "")[:120]
                            if sig:
                                all_signatures.add(sig)
                        all_messages.extend(enriched)

                    sigs_after_step = len(all_signatures)
                    sigs_grew = (sigs_after_step - sigs_before_step) > 0

                    if new_count == 0:
                        no_new += 1
                    elif not sigs_grew:
                        # Enriched messages returned but signatures didn't grow —
                        # likely signature mismatch (DOM changed between collections).
                        # Count as stagnant to prevent infinite loops.
                        sigs_stagnant += 1
                        if sigs_stagnant >= 5:
                            print(f"  [EXPORT] Подписи не растут на шаге {step}, лента закончена")
                            break
                        no_new += 1
                    else:
                        no_new = 0
                        sigs_stagnant = 0
                        if step % 10 == 0:
                            print(f"  [EXPORT] Шаг {step}: +{new_count}, всего {len(all_signatures)}")

                    # Scroll up
                    scrolled = self.page.evaluate("""
                        () => {
                            const c = window.__gitax_scroll;
                            if (c) {
                                const st = Math.max(100, c.clientHeight * 0.7);
                                const before = c.scrollTop;
                                c.scrollBy(0, -st);
                                return c.scrollTop !== before;
                            }
                            window.scrollBy(0, -window.innerHeight * 0.7);
                            return true;
                        }
                    """)

                    if not scrolled:
                        # Only count as "stuck" when scroll didn't move AND no new
                        # messages were found. MAX lazy-loads content — scroll position
                        # may stay the same while new messages appear above the viewport.
                        if new_count == 0:
                            scroll_stuck_count += 1
                            if scroll_stuck_count >= 2:
                                print(f"  [EXPORT] Скролл застрял на шаге {step}, лента закончена")
                                break
                            no_new += 1
                    else:
                        scroll_stuck_count = 0

                    self.page.wait_for_timeout(300)

                except Exception as e:
                    self.logger.debug(f"Scroll error in export: {e}")
                    no_new += 1

            sigs_after = len(all_signatures)
            new_in_pass = sigs_after - sigs_before
            print(f"  [EXPORT] Проход {pass_count}: +{new_in_pass} новых, всего {sigs_after}")

            if new_in_pass == 0:
                print(f"  [EXPORT] Новых сообщений нет, остановка")
                break

            # Scroll back to bottom for next pass
            if pass_num < passes - 1:
                self._scroll_to_bottom()

        total = len(all_signatures)
        print(f"  [EXPORT] Итого: {total} уникальных сообщений за {pass_count} проходов")
        self.logger.info(f"Export collected {total} messages in {pass_count} passes")

        return all_messages

    def _write_json(self, output_path: str, messages: list[dict], channel_url: str):
        """
        Write messages to a JSON file with metadata.

        Args:
            output_path: File path for output
            messages: List of message dicts
            channel_url: Channel URL for metadata
        """
        from datetime import datetime

        data = {
            "metadata": {
                "exported_at": datetime.now().isoformat(),
                "channel_url": channel_url,
                "total_messages": len(messages),
                "format_version": "1.0"
            },
            "messages": messages
        }

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.logger.info(f"JSON export written to {output_path} ({len(messages)} messages)")
        except Exception as e:
            self.logger.error(f"Failed to write JSON: {e}")
            raise

    def _write_csv(self, output_path: str, messages: list[dict]):
        """
        Write messages to a CSV file. HTML is excluded to keep file size reasonable.

        Args:
            output_path: File path for output
            messages: List of message dicts
        """
        headers = ["index", "sender", "timestamp", "direction", "text", "type", "attachments"]

        try:
            with open(output_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()

                for msg in messages:
                    # Determine message type from classes
                    msg_type = "text"
                    classes = msg.get("classes", "").lower()
                    if any(k in classes for k in ['file', 'attach', 'preview']):
                        msg_type = "file"
                    elif any(k in classes for k in ['link', 'url']):
                        msg_type = "link"

                    row = {
                        "index": msg.get("index", ""),
                        "sender": msg.get("sender", ""),
                        "timestamp": msg.get("timestamp", ""),
                        "direction": msg.get("direction", ""),
                        "text": msg.get("text", ""),
                        "type": msg_type,
                        "attachments": json.dumps(msg.get("attachments", []), ensure_ascii=False)
                    }
                    writer.writerow(row)

            self.logger.info(f"CSV export written to {output_path} ({len(messages)} messages)")
        except Exception as e:
            self.logger.error(f"Failed to write CSV: {e}")
            raise

    def export_messages_to_file(
        self,
        output_path: str = "messages_export.json",
        format: str = "json",
        scroll_passes: int = 3,
        include_html: bool = False,
        max_messages: int = 0
    ) -> int:
        """
        Export all messages from the MAX chat feed to a file.

        Scrolls through the entire message feed, collects full data from each
        message (sender, timestamp, direction, attachments, reactions), and
        writes to a structured file.

        Args:
            output_path: Output file path (default: messages_export.json)
            format: Output format - "json" or "csv" (default: json)
            scroll_passes: Number of scroll passes to collect messages (default: 3)
            include_html: Include HTML content in output (default: False, saves space)
            max_messages: Maximum messages to export (0 = no limit)

        Returns:
            Total number of messages exported

        Example:
            bm = BrowserMAX(channel_url)
            bm.connect()
            bm.navigate()
            count = bm.export_messages_to_file("my_export.json")
            print(f"Exported {count} messages")
        """
        self._check_connection()
        self.logger.info(f"Starting message export to {output_path} (format={format})")

        # Collect all messages
        messages = self._scroll_and_collect_full(passes=scroll_passes)

        if not messages:
            print("  [EXPORT] Сообщений не найдено")
            self.logger.warning("No messages collected for export")
            return 0

        # Apply max_messages limit
        if max_messages > 0 and len(messages) > max_messages:
            messages = messages[:max_messages]
            self.logger.info(f"Limited to {max_messages} messages")

        # Strip HTML if requested
        if not include_html:
            for msg in messages:
                msg["html"] = ""

        # Re-index messages sequentially
        for idx, msg in enumerate(messages):
            msg["index"] = idx

        # Write to file
        try:
            if format.lower() == "csv":
                self._write_csv(output_path, messages)
            else:
                self._write_json(output_path, messages, self.channel_url)
        except Exception as e:
            # Fallback to temp directory
            import tempfile
            temp_path = os.path.join(tempfile.gettempdir(), "messages_export.json")
            self.logger.warning(f"Original path failed, writing to {temp_path}")
            self._write_json(temp_path, messages, self.channel_url)
            output_path = temp_path

        print(f"  [EXPORT] Готово! {len(messages)} сообщений → {output_path}")
        return len(messages)

    def scan_channel_for_files(self) -> list[dict]:
        """
        Scan all messages in the channel and extract file metadata.

        Uses collect_all_messages() to scroll through and load all messages,
        then extracts file information from the DOM using CSS selectors.

        Returns:
            List of dicts with keys:
            - filename (str): name of the file
            - download_url (str): URL to download the file
            - file_size (int): size in bytes (0 if unknown)
            - message_idx (int): approximate index in the feed
            - has_direct_url (bool): True if URL is directly downloadable
            - media_type (str): "file", "video", or "image"
        """
        self._check_connection()

        # Scroll to load all messages into DOM (reuse existing infrastructure)
        # Uses same auto-converge pattern as export feature
        self.collect_all_messages(passes=2, max_stale=3, overscroll_cycles=3)

        # Extract file info from all loaded messages in a single evaluate call
        try:
            file_data = self.page.evaluate(r"""
                () => {
                    const results = [];
                    const seen = new Set();

                    // Find all message-like elements in the feed
                    const messages = document.querySelectorAll(
                        '[class*="message"],[class*="msg"],' +
                        '[class*="lenta-item"],[class*="feed-item"]'
                    );

                    messages.forEach((msg, idx) => {
                        let filename = '';
                        let downloadUrl = '';
                        let hasDirectUrl = false;
                        let fileSize = 0;
                        let mediaType = 'file';

                        // 1. Direct download links: a[download]
                        const downloadLinks = msg.querySelectorAll('a[download]');
                        for (const a of downloadLinks) {
                            const href = a.getAttribute('href') || '';
                            const name = a.getAttribute('download') || '';
                            if (name) {
                                filename = name;
                                downloadUrl = href;
                                hasDirectUrl = !!href;
                                break;
                            }
                        }

                        // 2. Alternative download links (a[href*="download"])
                        if (!filename) {
                            const altLinks = msg.querySelectorAll(
                                'a[href*="download"],a[href*="attachment"]'
                            );
                            for (const a of altLinks) {
                                const href = a.getAttribute('href') || '';
                                if (href) {
                                    downloadUrl = href;
                                    hasDirectUrl = true;
                                    filename = a.textContent?.trim()
                                        || href.split('/').pop() || '';
                                    break;
                                }
                            }
                        }

                        // 3. Video elements with src attribute
                        if (!filename) {
                            const videos = msg.querySelectorAll('video[src]');
                            if (videos.length > 0) {
                                const src = videos[0].getAttribute('src') || '';
                                downloadUrl = src;
                                hasDirectUrl = true;
                                mediaType = 'video';
                                filename = videos[0].getAttribute('title')
                                    || src.split('/').pop() || 'video.mp4';
                            }
                        }

                        // 4. Image elements (non-emoji, non-avatar)
                        if (!filename) {
                            const imgs = msg.querySelectorAll('img[src]');
                            for (const img of imgs) {
                                const src = img.getAttribute('src') || '';
                                if (src && !src.includes('emoji')
                                    && !src.includes('avatar')) {
                                    downloadUrl = src;
                                    hasDirectUrl = true;
                                    mediaType = 'image';
                                    filename = img.getAttribute('alt')
                                        || src.split('/').pop() || 'image.jpg';
                                    break;
                                }
                            }
                        }

                        // 5. Generic file/attachment indicator classes
                        if (!filename) {
                            const fileEls = msg.querySelectorAll(
                                '[class*="file"],[class*="attach"]'
                            );
                            for (const el of fileEls) {
                                const title = el.getAttribute('title')
                                    || el.getAttribute('alt') || '';
                                if (title) {
                                    filename = title;
                                    break;
                                }
                            }
                        }

                        // Skip messages without any file indicators
                        if (!filename) return;

                        // Deduplicate by filename
                        if (seen.has(filename)) return;
                        seen.add(filename);

                        // Extract file size from [class*="size"] elements
                        const sizeEls = msg.querySelectorAll('[class*="size"]');
                        for (const el of sizeEls) {
                            const text = el.textContent?.trim()
                                || el.getAttribute('title') || '';
                            if (text) {
                                const match = text.match(
                                    /([\d.]+)\s*(B|KB|MB|GB)/i
                                );
                                if (match) {
                                    const num = parseFloat(match[1]);
                                    const unit = match[2].toUpperCase();
                                    if (unit === 'GB') {
                                        fileSize = num * 1073741824;
                                    } else if (unit === 'MB') {
                                        fileSize = num * 1048576;
                                    } else if (unit === 'KB') {
                                        fileSize = num * 1024;
                                    } else {
                                        fileSize = num;
                                    }
                                }
                                break;
                            }
                        }

                        results.push({
                            filename: filename,
                            download_url: downloadUrl,
                            file_size: fileSize,
                            message_idx: idx,
                            has_direct_url: hasDirectUrl,
                            media_type: mediaType
                        });
                    });

                    return results;
                }
            """)
        except Exception:
            self.logger.warning("scan_channel_for_files: evaluate failed", exc_info=True)
            return []

        if file_data:
            # Deduplicate by filename (redundant with JS-side dedup,
            # but covers edge cases where evaluate returns duplicates)
            seen = set()
            deduped = []
            for item in file_data:
                fname = item.get("filename", "")
                if fname and fname not in seen:
                    seen.add(fname)
                    deduped.append(item)
            return deduped
        return []

    def close(self):
        """Close browser connection gracefully"""
        self.logger.debug("Closing connection...")
        try:
            # For persistent context mode, close context (which also closes browser)
            if self._context:
                self._context.close()
                self._context = None
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")
        finally:
            self._connected = False
            self.playwright = None
            self.__class__._active_playwright = None
            self.browser = None
            self.page = None
        self.logger.debug("Connection closed")


if __name__ == "__main__":
    print("Browser MAX module (Playwright)")
    print("Usage: from browser_max import BrowserMAX")