#!/usr/bin/env python3
"""
GitHub Archiver — Резервное копирование репозиториев в MAX Messenger

Главный скрипт с меню и основной логикой
"""

import glob
import json
import os
import re
import sys
import yaml
import time
import atexit
import signal
from datetime import datetime
from dotenv import load_dotenv
from logging_config import setup_logging, LogMixin, SessionCapture

from journal import Journal
from github_api import GitHubAPI
from browser_max import BrowserMAX
from repo_collector import RepoCollector
from scroll_registry import ScrollRegistry
from pypi_libs_journal import PyPILibsJournal
from config_utils import get_channel_url, is_setup_complete, ensure_channel_url, get_skipped_channels, get_split_mode, get_channels_for_function
from parallel_uploader import ParallelGroupUploader
from progressbar import LiveProgressBar
from utils import format_file_size
from channel_registry_ui import channel_registry_menu, select_channel


def prompt_numeric_choice(prompt_text: str, valid_options: list[str]) -> str:
    """
    Prompt the user for a numeric choice, looping until valid input is received.

    Args:
        prompt_text: The prompt to display
        valid_options: List of valid option strings (e.g., ["0", "1", "2", "3"])

    Returns:
        The user's valid choice as a string
    """
    while True:
        try:
            choice = input(f"  {prompt_text}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Ввод прерван.")
            return ""
        if choice in valid_options:
            return choice
        print(f"  Неверный выбор. Доступно: {', '.join(sorted(valid_options))}")


class GracefulShutdown:
    """Context manager for graceful shutdown"""
    def __init__(self, archiver, browsers=None):
        self.archiver = archiver
        self.interrupted = False
        self.browsers = browsers if browsers is not None else []
        # Add archiver's browser if not already in list
        if self.archiver.max_browser and self.archiver.max_browser not in self.browsers:
            self.browsers.append(self.archiver.max_browser)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def cleanup(self):
        """Clean up resources on shutdown.

        Marks currently processing repo as 'failed' in journal BEFORE
        deleting temp files so interrupted repos are recoverable on restart.
        """
        if self.interrupted:
            return

        self.interrupted = True

        # Mark interrupted repo as failed BEFORE cleanup
        self._mark_interrupted_repo_as_failed()

        # Clean up temp files before closing browsers
        self._cleanup_temp_files()

        for browser in self.browsers:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

    def _mark_interrupted_repo_as_failed(self):
        """Mark the currently processing repo as 'failed' for crash recovery.

        Checks journal for a repo being processed when shutdown occurred.
        If found, updates its status to 'failed' so retry will pick it up.
        """
        import logging
        logger = logging.getLogger("gitax")

        journal = getattr(self.archiver, 'journal', None)
        if not journal:
            return

        cp = journal.get_currently_processing()
        if not cp:
            return

        full_name = cp.get('full_name')
        filename = cp.get('filename', '')
        logger.info(f"Shutdown during processing: {full_name} ({filename})")

        # Check if repo already in journal
        existing = journal.get_repository(full_name)
        if existing and existing.get('status') == 'sent':
            # Already sent — don't downgrade status
            logger.info(f"  {full_name} already marked as 'sent', skipping")
            return

        # Mark as failed (or update existing failed/incomplete entry)
        journal.update_repository(full_name, {
            'status': 'failed',
            'interrupted': True,
            'interrupted_filename': filename,
        })

        # If not in journal yet, add it
        if not journal.get_repository(full_name):
            # Parse owner/repo from full_name for minimal journal entry
            parts = full_name.split('/', 1)
            if len(parts) == 2:
                journal.add_repository({
                    'full_name': full_name,
                    'display_name': parts[1],
                    'status': 'failed',
                    'interrupted': True,
                    'interrupted_filename': filename,
                    'version': 'unknown',
                })
                logger.info(f"  Added {full_name} to journal as 'failed'")

        # Clear the currently_processing marker
        journal.clear_currently_processing()

    def _cleanup_temp_files(self):
        """Remove any remaining files in the temp directory.

        Skips the file associated with a currently processing repo if it
        was just marked as failed — the file is needed for recovery.
        """
        import logging
        logger = logging.getLogger("gitax")

        output_dir = self.archiver.config.get('archiver', {}).get('output_dir', './temp')
        if not os.path.exists(output_dir):
            return

        # Find all temp files (7z volumes, ZIPs, locked files)
        patterns = [
            os.path.join(output_dir, "*.7z.*"),
            os.path.join(output_dir, "*.zip"),
            os.path.join(output_dir, "*.locked_*"),
        ]

        # Add PyPI temp files
        patterns.append(os.path.join("temp_pypi", "**", "*"))  # pypi libs downloads

        temp_files = []
        for pattern in patterns:
            temp_files.extend(glob.glob(pattern))

        if not temp_files:
            return

        temp_files = list(set(temp_files))
        logger.info(f"Cleaning up {len(temp_files)} temp file(s) on shutdown")

        deleted = 0
        for f in temp_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
                    deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete temp file {f}: {e}")

        if deleted:
            logger.info(f"Deleted {deleted} temp file(s)")

        # Clean up entire pypi_api temp directory
        temp_pypi = os.path.join(os.getcwd(), "temp_pypi")
        if os.path.exists(temp_pypi):
            try:
                import shutil
                shutil.rmtree(temp_pypi)
                deleted += 1
                logger.info("Cleaned up ./temp_pypi/ directory")
            except Exception as e:
                logger.warning(f"Failed to clean ./temp_pypi/: {e}")


class GitHubArchiver(LogMixin):
    """Главный класс программы"""

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        self.journal = Journal("journal.json")
        self.github = None
        self.max_browser = None

        # Orphaned repos recovered from filenames — set by _check_orphaned_files
        # when user chooses to retry. Processed first in load_new_repositories.
        self._orphaned_repos_to_retry = []

        # Проверить и создать временную папку
        output_dir = self.config.get('archiver', {}).get('output_dir', './temp')
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Check for orphaned files from interrupted sessions
        self._check_orphaned_files(output_dir)

    @staticmethod
    def _safe_remove_file(filepath: str, max_wait: int = 60, poll_interval: int = 2) -> bool:
        """
        Safely remove a file with retry logic for Windows file locking.

        Args:
            filepath: Path to file to remove
            max_wait: Maximum seconds to wait for file to become available
            poll_interval: Seconds between retry attempts

        Returns:
            True if file was removed, False otherwise
        """
        if not os.path.exists(filepath):
            return True

        elapsed = 0
        while elapsed < max_wait:
            try:
                os.remove(filepath)
                return True
            except PermissionError:
                elapsed += poll_interval
                time.sleep(poll_interval)
            except OSError as e:
                if e.errno == 13:  # Permission denied
                    elapsed += poll_interval
                    time.sleep(poll_interval)
                else:
                    return False
            except Exception:
                return False

        return False

    def _check_orphaned_files(self, output_dir: str):
        """
        Check for orphaned files from interrupted sessions.
        Includes 7z volumes, ZIP archives, and locked files.
        Recovers repos from filenames and offers to retry failed uploads.

        Args:
            output_dir: Directory to check for orphaned files
        """
        import logging
        import re
        logger = logging.getLogger("gitax")

        # Find all orphaned file types
        patterns = [
            os.path.join(output_dir, "*.7z.*"),      # 7z volumes
            os.path.join(output_dir, "*.zip"),        # ZIP archives
            os.path.join(output_dir, "*.locked_*"),   # Locked files
        ]

        orphaned = []
        for pattern in patterns:
            orphaned.extend(glob.glob(pattern))

        if not orphaned:
            return

        orphaned = sorted(set(orphaned))  # Remove duplicates

        # Categorize files for display
        volume_count = len([f for f in orphaned if '.7z.' in f])
        zip_count = len([f for f in orphaned if f.endswith('.zip')])
        locked_count = len([f for f in orphaned if '.locked_' in f])

        print(f"\n  Found {len(orphaned)} orphaned file(s) in {output_dir}:")
        if volume_count:
            print(f"    - {volume_count} × 7z volume(s)")
        if zip_count:
            print(f"    - {zip_count} × ZIP archive(s)")
        if locked_count:
            print(f"    - {locked_count} × locked file(s)")

        # Show first few files
        for f in orphaned[:5]:
            try:
                size_mb = os.path.getsize(f) / 1024 / 1024
                print(f"      {os.path.basename(f)} ({size_mb:.1f} MB)")
            except OSError as e:
                logger.warning(f"Could not stat orphaned file {f}: {e}")
                print(f"      {os.path.basename(f)}")
        if len(orphaned) > 5:
            print(f"      ... and {len(orphaned) - 5} more")

        # Parse repo names from orphaned filenames
        # Pattern: owner-repo-branch.zip or owner-repo-branch.7z.NNN
        recovered_repos = self._parse_repos_from_filenames(orphaned)

        if recovered_repos:
            print(f"\n  Recovered {len(recovered_repos)} repo(s) from filenames:")
            for repo in recovered_repos[:5]:
                print(f"    - {repo['full_name']}")
            if len(recovered_repos) > 5:
                print(f"      ... and {len(recovered_repos) - 5} more")

            # Add recovered repos to journal as 'failed' if not already there
            added = 0
            for repo in recovered_repos:
                existing = self.journal.get_repository(repo['full_name'])
                if not existing or existing.get('status') != 'sent':
                    self.journal.add_repository(repo)
                    added += 1
                    logger.info(f"Added recovered repo to journal: {repo['full_name']}")

            if added:
                print(f"\n  ✓ {added} repo(s) marked as 'failed' in journal for retry")

        print("\n  These files are from interrupted upload sessions.")
        print("  [1] Delete all orphaned files")
        print("  [2] Retry uploading recovered repos")
        print("  [3] Don't ask again this session")

        try:
            choice = prompt_numeric_choice("Choose [1/2/3]", ["1", "2", "3"])
            if choice == '1':
                deleted = 0
                for f in orphaned:
                    if self._safe_remove_file(f, max_wait=10):
                        deleted += 1
                        logger.info(f"Deleted orphaned: {f}")
                    else:
                        logger.warning(f"Failed to delete orphaned file: {f}")
                print(f"  ✓ Deleted {deleted}/{len(orphaned)} orphaned file(s)")
            elif choice == '2' and recovered_repos:
                print(f"\n  Starting retry for {len(recovered_repos)} recovered repo(s)...")
                # Return the recovered repos for the caller to process
                self._orphaned_repos_to_retry = recovered_repos
            elif choice == '3':
                print("  Will not ask again this session")
        except KeyboardInterrupt:
            logger.info("Orphaned file cleanup cancelled by user")
        except Exception as e:
            logger.warning(f"Orphaned file check error: {e}")

    def _parse_repos_from_filenames(self, filenames: list) -> list:
        """
        Parse repository names from orphaned filenames.

        Filename patterns:
        - owner-repo-branch.zip
        - owner-repo-branch.7z.001
        - owner-repo-branch.7z.002

        Args:
            filenames: List of orphaned file paths

        Returns:
            List of repo dicts ready for journal insertion
        """
        import re

        repos = []
        seen = set()

        for fpath in filenames:
            basename = os.path.basename(fpath)

            # Try to match owner-repo-branch pattern
            # Remove .zip or .7z.NNN extensions
            name_part = re.sub(r'\.(zip|7z\.\d+)$', '', basename)

            # Split by '-' but handle repos with hyphens in name
            # Common branch names: main, master, dev, etc.
            parts = name_part.rsplit('-', 1)
            if len(parts) == 2:
                owner_repo = parts[0]
                branch = parts[1]
                # owner_repo might still have hyphens, so split on first hyphen
                owner_repo_parts = owner_repo.split('-', 1)
                if len(owner_repo_parts) == 2:
                    owner, repo_name = owner_repo_parts
                    full_name = f"{owner}/{repo_name}"
                    if full_name not in seen:
                        seen.add(full_name)
                        repos.append({
                            'full_name': full_name,
                            'display_name': repo_name,
                            'status': 'failed',
                            'interrupted': True,
                            'interrupted_filename': basename,
                            'version': 'unknown',
                        })

        return repos

    def _process_orphaned_retries(self):
        """Process orphaned repos recovered from filenames.

        Fetches full info from GitHub, connects browser, and retries uploads.
        Called from load_new_repositories before the normal flow.
        """
        import logging
        logger = logging.getLogger("gitax")

        if not self._orphaned_repos_to_retry:
            return

        orphaned = self._orphaned_repos_to_retry
        print(f"\n  {'─' * 58}")
        print(f"  Повторная загрузка {len(orphaned)} прерванного репозитория(ев):")
        print(f"  {'─' * 58}")

        # Connect browser
        if not self.max_browser:
            self.max_browser = BrowserMAX(self.config)
        if not self.max_browser.is_connected:
            self.max_browser.connect()
            if not self.max_browser.is_connected:
                print("  ✗ Не удалось подключиться к браузеру")
                return

        # Navigate to channel
        channel_url = getattr(self, '_active_channel_url', None)
        if channel_url:
            self.max_browser.navigate_to_channel(channel_url)

        success_count = 0
        fail_count = 0

        for i, orphaned_repo in enumerate(orphaned, 1):
            full_name = orphaned_repo.get('full_name', '')
            display_name = orphaned_repo.get('display_name', full_name)
            logger.info(f"Orphaned retry {i}/{len(orphaned)}: {full_name}")
            print(f"\n  [{i}/{len(orphaned)}] {display_name}")

            # Fetch full repo info from GitHub
            try:
                parts = full_name.split('/', 1)
                if len(parts) != 2:
                    print(f"    ✗ Неверное имя репозитория")
                    fail_count += 1
                    continue
                owner, repo_name = parts
                repo_info = self.github.get_repo(owner, repo_name)
                if not repo_info:
                    print(f"    ✗ Не удалось получить информацию о репозитории")
                    fail_count += 1
                    continue
            except Exception as e:
                logger.warning(f"Failed to fetch {full_name}: {e}")
                print(f"    ✗ Ошибка API: {e}")
                fail_count += 1
                continue

            # Download and send
            try:
                if self._download_and_send_repo_info_connected(self.max_browser, repo_info):
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"Failed to process {full_name}: {e}")
                print(f"    ✗ Ошибка: {e}")
                fail_count += 1

        print(f"\n  {'─' * 58}")
        print(f"  ✓ Успешно: {success_count}, ✗ Ошибок: {fail_count}")
        print(f"  {'─' * 58}")

    def _ensure_channel_ready(self, channel_name: str, label: str,
                              config_section: str = None) -> bool:
        """
        Ensure a channel URL is configured. Tries channel registry first,
        falls back to legacy config (.env / config.yaml). Updates self.config
        so subsequent code finds the URL.

        Args:
            channel_name: Channel key name (e.g., "max", "pypi", "media", "backup")
            label: Human-readable label for prompts (e.g., "MAX канал")
            config_section: Optional section in self.config to update
                            (e.g., "max" for self.config['max']['channel_url'])

        Returns:
            True if URL is available (after optional prompt), False if user skips
        """
        # Map channel key → registry function name
        _CHANNEL_TO_FUNCTION = {
            "max": "github",
            "pypi": "pypi",
            "media": "media",
            "backup": "backup",
            "softportal": "softportal",
        }
        function = _CHANNEL_TO_FUNCTION.get(channel_name, channel_name)

        # Try channel registry first (allow inline add of new channels)
        selected = select_channel(function, allow_add=True)
        if selected:
            self._active_channel_url = selected.url
            self._active_channel_label = selected.label or selected.url
            print(f"  ✓ Канал: {self._active_channel_label}")
            # Set env var so child archiver instances find the URL on config reload
            env_var = f"CHANNEL_{channel_name.upper()}"
            os.environ[env_var] = selected.url
            self.config.setdefault("channels", {})[channel_name] = selected.url
        else:
            # Fall back to legacy config
            url = ensure_channel_url(self.config, channel_name, label)
            if not url:
                return False
            self._active_channel_url = url
            self._active_channel_label = channel_name

            # Update self.config so subsequent internal lookups find the URL
            env_var = f"CHANNEL_{channel_name.upper()}"
            env_val = os.environ.get(env_var, "").strip()
            if env_val:
                self.config.setdefault("channels", {})[channel_name] = env_val
                if config_section:
                    self.config.setdefault(config_section, {})["channel_url"] = env_val
        return True

    # Module-to-channel mapping for skip-aware UI
    _MODULE_CHANNELS = {
        "1": "max",         # GitHub → max
        "2": "pypi",        # PyPI → pypi
        "3": "backup",      # Backuper → backup
        "4": "media",       # Файлы → media
        "a": "thingiverse", # Thingiverse → thingiverse
    }

    def _is_module_enabled(self, menu_key: str) -> bool:
        """
        Check if a main menu module is enabled (not skipped in setup).

        Args:
            menu_key: Main menu key ("1".."5")

        Returns:
            True if module is enabled or always-available (service menu)
        """
        ch = self._MODULE_CHANNELS.get(menu_key)
        if not ch:
            return True  # Module 5 (service) always enabled
        return ch not in get_skipped_channels(self.config)



    def _init_github(self) -> GitHubAPI:
        """Инициализировать GitHub API"""
        if self.github is None:
            # SECURITY: Always read token from environment, never from config dict
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                # Fallback to config only if env is not set (loader maps env→config)
                token = self.config.get('github', {}).get('token', '')
            output_dir = self.config.get('archiver', {}).get('output_dir', './temp')
            self.github = GitHubAPI(token, output_dir)
        if not self.github.token:
            self.logger.warning(
                "⚠️ Работа без токена. Rate limit: 10 запросов/мин. "
                "Для увеличения лимита: https://github.com/settings/tokens"
            )
        return self.github

    def _init_max_browser(self) -> BrowserMAX:
        """Initialize MAX browser (reuses connection if alive)"""
        if self.max_browser is None:
            channel_url = getattr(self, '_active_channel_url', None) or self.config.get('channels', {}).get('max', '')
            # Default to CDP (existing browser) for seamless UX
            # Use local browser only if explicitly requested
            use_local = self.config.get('archiver', {}).get('use_local_browser', False)
            self.max_browser = BrowserMAX(channel_url, use_local_browser=use_local)
        return self.max_browser

    def _ensure_max_connected(self):
        """Ensure MAX browser is connected and ready"""
        browser = self._init_max_browser()
        if not browser.keep_alive_connect():
            raise Exception("Failed to connect to MAX")
        browser.navigate()
        browser.ensure_page_ready()
        return browser

    # ── Runtime helpers ──

    def _download_file(self, url: str, filename: str, output_dir: str) -> str | None:
        """Download a file from URL to output_dir. Returns file path or None on error."""
        import requests
        try:
            os.makedirs(output_dir, exist_ok=True)
            file_path = os.path.join(output_dir, filename)
            resp = requests.get(url, timeout=300, stream=True)
            resp.raise_for_status()
            with open(file_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return file_path
        except Exception as e:
            self.logger.error(f"Download error {filename}: {e}")
            return None

    def _send_file_to_channel(self, file_path: str, message: str, browser) -> bool:
        """Send a file to MAX channel. Returns True on success."""
        retries = self.config.get('archiver', {}).get('retries', 3)
        retry_delay = self.config.get('archiver', {}).get('retry_delay', 10)
        try:
            success, _ = browser.send_message_with_files(
                text=message,
                filepaths=[file_path],
                retries=retries,
                retry_delay=retry_delay,
                split_mode="auto",
                expected_extensions=['.exe', '.msi', '.pkg', '.sh', '.tar.gz', '.tar.xz']
            )
            return success
        except Exception as e:
            self.logger.error(f"Send error {file_path}: {e}")
            return False

    def _cleanup_file(self, file_path: str):
        """Remove a temporary file."""
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    def load_runtime(self):
        """Загрузить Git runtime installer (первичная загрузка)."""
        from datetime import datetime
        from runtime_api import RuntimeFactory, OSTarget

        runtime_cfg = self.config.get("runtime", {})
        if not runtime_cfg.get("enabled", True):
            self.logger.info("Runtime sync disabled in config")
            return

        runtime = RuntimeFactory.get_runtime("github")
        print(f"\n  {RuntimeFactory.get_icon('git')} Загрузка Git runtime...")

        latest = runtime.get_latest_version()
        if not latest:
            print("  ⚠ Не удалось получить версию Git. Пропуск.")
            return

        print(f"  📦 Git {latest} — загрузка инсталляторов для всех ОС...")

        urls = runtime.get_download_urls(latest)
        os_targets = runtime_cfg.get("os_targets", ["windows", "macos", "linux"])
        urls = [u for u in urls if u["os"] in os_targets]

        browser = self._ensure_max_connected()
        entries = []
        output_dir = runtime_cfg.get("output_dir", "./temp_runtime")

        for url_info in urls:
            filename = url_info["filename"]
            download_url = url_info["url"]
            os_name = url_info["os"]

            print(f"  ⬇ Скачиваю {filename} ({url_info.get('size_hint', '')})...")
            file_path = self._download_file(download_url, filename, output_dir)

            if file_path and os.path.exists(file_path):
                print(f"  📤 Отправляю {filename} в канал...")
                sent = self._send_file_to_channel(
                    file_path,
                    message=f"Git {latest} — {os_name} installer\n\n{RuntimeFactory.get_download_page('git')}",
                    browser=browser,
                )
                if sent:
                    entries.append({
                        "os": os_name,
                        "filename": filename,
                        "sent_at": datetime.now().isoformat(),
                    })
                    print(f"  ✓ {filename} отправлен")
                else:
                    print(f"  ✗ Ошибка отправки {filename}")
                self._cleanup_file(file_path)
            else:
                print(f"  ✗ Ошибка скачивания {filename}")

        if entries:
            self.journal.set_runtime_version(latest, entries)
            print(f"\n  ✓ Git runtime {latest} загружен в журнал")
        else:
            print("\n  ✗ Не удалось загрузить runtime")

    def sync_runtimes(self):
        """Check and sync Git runtime installer if a newer version is available."""
        from datetime import datetime
        from runtime_api import RuntimeFactory, OSTarget

        runtime_cfg = self.config.get("runtime", {})
        if not runtime_cfg.get("enabled", True):
            self.logger.info("Runtime sync disabled in config")
            return

        runtime = RuntimeFactory.get_runtime("github")
        print(f"\n  {RuntimeFactory.get_icon('git')} Проверяю Git runtime...")

        latest = runtime.get_latest_version()
        if not latest:
            print("  ⚠ Не удалось получить версию Git. Пропуск.")
            return

        if not self.journal.should_update_runtime(latest):
            saved = self.journal.get_runtime_version()
            print(f"  ✓ Git {saved} — актуален")
            return

        print(f"  🆕 Git {latest} доступен (текущий: {self.journal.get_runtime_version() or 'не установлен'})")
        print("  Загрузка инсталляторов для всех ОС...")

        urls = runtime.get_download_urls(latest)
        os_targets = runtime_cfg.get("os_targets", ["windows", "macos", "linux"])
        urls = [u for u in urls if u["os"] in os_targets]

        browser = self._ensure_max_connected()
        entries = []
        output_dir = runtime_cfg.get("output_dir", "./temp_runtime")

        for url_info in urls:
            filename = url_info["filename"]
            download_url = url_info["url"]
            os_name = url_info["os"]

            print(f"  ⬇ Скачиваю {filename} ({url_info.get('size_hint', '')})...")
            file_path = self._download_file(download_url, filename, output_dir)

            if file_path and os.path.exists(file_path):
                print(f"  📤 Отправляю {filename} в канал...")
                sent = self._send_file_to_channel(
                    file_path,
                    message=f"Git {latest} — {os_name} installer\n\n{RuntimeFactory.get_download_page('git')}",
                    browser=browser,
                )
                if sent:
                    entries.append({
                        "os": os_name,
                        "filename": filename,
                        "sent_at": datetime.now().isoformat(),
                    })
                    print(f"  ✓ {filename} отправлен")
                else:
                    print(f"  ✗ Ошибка отправки {filename}")
                self._cleanup_file(file_path)
            else:
                print(f"  ✗ Ошибка скачивания {filename}")

        if entries:
            self.journal.set_runtime_version(latest, entries)
            print(f"\n  ✓ Git runtime {latest} обновлён в журнале")
        else:
            print("\n  ✗ Не удалось обновить runtime")

    def _format_stars(self, count: int) -> str:
        """Форматировать количество звёзд"""
        if count >= 1000000:
            return f"{count / 1000000:.1f}M"
        elif count >= 1000:
            return f"{count / 1000:.1f}K"
        return str(count)

    def _format_description(self, desc: str, max_len: int = 100) -> str:
        """Форматировать описание"""
        if not desc:
            return "Без описания"
        if len(desc) > max_len:
            return desc[:max_len-3] + "..."
        return desc

    def _print_progress(self, current: int, total: int, updated: int, skipped: int, status: str = ""):
        """Print progress bar for sync/load operations"""
        if total == 0:
            return
        pct = int(current / total * 100)
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)

        print(f"\r  Синхронизация: {current}/{total} | {bar} {pct}% | ✓{updated} | –{skipped} {status}", end="", flush=True)
        if current >= total:
            print()

    def _build_message_text(self, repo_data: dict, zip_size: int | None = None) -> str:
        """Построить текст сообщения для MAX"""
        desc = self._format_description(repo_data.get('description', ''))

        text = f"""📦 {repo_data.get('display_name', '')}

📝 {desc}

🔖 Версия: {repo_data.get('version', 'unknown')} ({repo_data.get('version_type', 'unknown')})
⭐ Звёзды: {self._format_stars(repo_data.get('stars', 0))}
🍴 Форки: {self._format_stars(repo_data.get('forks', 0))}
🔗 GitHub: {repo_data.get('github_url', '')}"""

        if zip_size:
            text += f"\n📦 Размер: {format_file_size(zip_size)}"

        return text

    def _show_header(self):
        """Показать заголовок программы"""
        stats = self.journal.get_stats()

        print("\n" + "═" * 60)
        print("           GitHub Archiver")
        print("           Резервное копирование в MAX")
        print("═" * 60)
        print(f"  Журнал: {stats['total']} репозиториев "
              f"({stats['sent']} отправлено, {stats['failed']} ошибок)")
        print("─" * 60)

    def _show_main_menu(self):
        """Показать главное меню"""
        self._show_header()

        if not is_setup_complete(self.config):
            print("\n  ⚡ Требуется начальная настройка")
            print()
            print("  [0] ⚡ Начальная настройка")
        else:
            print()

        skipped = get_skipped_channels(self.config)

        def menu_item(num, name, channel):
            if channel in skipped:
                return f"  [{num}] {name}  (отключён)"
            return f"  [{num}] {name}"

        print(menu_item("1", "GitHub — репозитории", "max"))
        print(menu_item("2", "PyPI — Python библиотеки", "pypi"))
        print(menu_item("3", "Backuper — бэкап папок в канал", "backup"))
        print(menu_item("4", "Файлы — медиа, скачивание, экспорт", "media"))
        print(menu_item("6", "Cargo — Rust пакеты", "cargo"))
        print(menu_item("7", "NuGet — .NET пакеты", "nuget"))
        print(menu_item("8", "RubyGems — Ruby пакеты", "rubygems"))
        print(menu_item("9", "SoftPortal — программы", "softportal"))
        print(menu_item("a", "Thingiverse (3D модели)", "thingiverse"))
        print("  [5] Сервис — журналы, настройки")

        if not is_setup_complete(self.config):
            print("  [X] Выход")
        else:
            print("  [0] Выход")

        print()

    def _github_menu(self):
        """Подменю GitHub"""
        ignored_count = self.journal.get_ignored_count()
        ignored_str = f" ({ignored_count} в игноре)" if ignored_count else ""
        failed_count = len(self.journal.get_repositories_by_status('failed'))
        failed_str = f" ({failed_count} неудачных)" if failed_count else ""
        print("\n" + "═" * 60)
        print("  GitHub — репозитории")
        print("─" * 60)
        print()
        print("  [1] Синхронизировать репозитории (пошагово)")
        print("  [2] Синхронизировать репозитории (авто)")
        print("  [3] Синхронизировать репозитории (параллельно)")
        print("  [4] Загрузить новые репозитории (пошагово)")
        print("  [5] Загрузить новые репозитории (авто)")
        print("  [6] Загрузить новые репозитории (параллельно)")
        print(f"  [7] Повторить неудачные загрузки{failed_str}")
        print("  [8] Собрать базу репозиториев")
        print("  [9] Статус коллекции репозиториев")
        print("  [a] Загрузить Git runtime (первичная)")
        print("  [b] Синхронизировать Git runtime")
        print(f"  [c] Список игнорирования{ignored_str}")
        print("  [d] Аудит — очистка / восстановление публикаций")
        print("  [0] Назад")
        print()

    def _pypi_menu(self):
        """Подменю PyPI"""
        print("\n" + "═" * 60)
        print("  PyPI — Python библиотеки")
        print("─" * 60)
        print()
        print("  [1] Загрузить топ Python библиотек")
        print("  [2] Синхронизировать Python библиотеки")
        print("  [3] Загрузить Python runtime (первичная)")
        print("  [4] Синхронизировать Python runtime")
        print("  [0] Назад")
        print()

    def _cargo_menu(self):
        """Подменю Cargo (Rust)"""
        print("\n" + "═" * 60)
        print("  Cargo — Rust пакеты (crates.io)")
        print("─" * 60)
        print()
        print("  [1] Загрузить топ Rust пакеты")
        print("  [2] Синхронизировать Rust пакеты")
        print("  [3] Загрузить Rust runtime (первичная)")
        print("  [4] Синхронизировать Rust runtime")
        print("  [0] Назад")
        print()

    def _nuget_menu(self):
        """Подменю NuGet (.NET)"""
        print("\n" + "═" * 60)
        print("  NuGet — .NET пакеты (nuget.org)")
        print("─" * 60)
        print()
        print("  [1] Загрузить топ .NET пакеты")
        print("  [2] Синхронизировать .NET пакеты")
        print("  [3] Загрузить .NET runtime (первичная)")
        print("  [4] Синхронизировать .NET runtime")
        print("  [0] Назад")
        print()

    def _rubygems_menu(self):
        """Подменю RubyGems"""
        print("\n" + "═" * 60)
        print("  RubyGems — Ruby пакеты (rubygems.org)")
        print("─" * 60)
        print()
        print("  [1] Загрузить топ Ruby пакеты")
        print("  [2] Синхронизировать Ruby пакеты")
        print("  [3] Загрузить Ruby runtime (первичная)")
        print("  [4] Синхронизировать Ruby runtime")
        print("  [0] Назад")
        print()

    def _softportal_menu(self):
        """Подменю SoftPortal"""
        print("\n" + "═" * 60)
        print("  SoftPortal — программы (softportal.com)")
        print("─" * 60)
        print()
        print("  [1] Загрузить топ программы")
        print("  [2] Синхронизировать программы")
        print("  [3] Обновить список категорий")
        print("  [0] Назад")
        print()

    def _thingiverse_menu(self):
        """Подменю Thingiverse"""
        print("\n" + "═" * 60)
        print("  Thingiverse — 3D модели (thingiverse.com)")
        print("─" * 60)
        print()
        print("  [1] Популярные модели")
        print("  [2] По тегам")
        print("  [3] По категориям")
        print("  [4] По авторам")
        print("  [0] Назад")
        print()

    def _files_menu(self):
        """Подменю Файлы"""
        print("\n" + "═" * 60)
        print("  Файлы — медиа, скачивание, экспорт")
        print("─" * 60)
        print()
        print("  [1] Загрузить медиа из папки")
        print("  [2] Скачать все файлы из канала")
        print("  [3] Экспорт всех сообщений в файл")
        print("  [4] Удалить все сообщения в ленте")
        print("  [0] Назад")
        print()

    def _service_menu(self):
        """Подменю Сервис"""
        print("\n" + "═" * 60)
        print("  Сервис — журналы, настройки")
        print("─" * 60)
        print()
        print("  [1] Очистить журналы")
        if is_setup_complete(self.config):
            print("  [2] ⚙ Настройки")
        print("  [3] Каналы — управление каналами")
        print("  [4] Верификация журналов")
        print("  [5] Batch — параллельный запуск архиверов")
        print("  [0] Назад")
        print()

    def _get_user_choice(self, options: list, prompt: str = "Выберите действие") -> str:
        """Получить выбор пользователя"""
        while True:
            choice = input(f"  {prompt}: ").strip().lower()
            if choice in options:
                return choice
            print(f"  Неверный выбор. Доступно: {', '.join(options)}")

    def sync_repositories(self, mode: str = "step"):
        """Синхронизация репозиториев - проверка обновлений

        Args:
            mode: 'step' (пошаговая) или 'parallel' (параллельная)
        """
        print("\n" + "═" * 60)
        print("Синхронизация репозиториев")
        print("═" * 60)

        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if not self.journal.get_count():
            print("\n  ⚠ Журнал пуст. Нет репозиториев для проверки.")
            print("  Используйте пункт [2] для загрузки новых репозиториев.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        self._init_github()

        repos = self.journal.get_all_repositories()
        # Отфильтровать игнорируемые
        total_ignored = len([r for r in repos if self.journal.is_ignored(r.get('full_name', ''))])
        repos = [r for r in repos if not self.journal.is_ignored(r.get('full_name', ''))]
        total_repos = len(repos)
        print(f"\n  Загружено {total_repos} репозиториев из журнала"
              f"{'  (' + str(total_ignored) + ' в игнор-листе)' if total_ignored else ''}")
        print("  Проверяю актуальные версии на GitHub...\n")

        # Phase 1: Проверить ВСЕ репозитории без вопросов
        repo_updates = []  # (repo, has_new, latest_version)
        checked_count = 0
        has_update_count = 0
        retry_failed_count = 0

        for i, repo in enumerate(repos, 1):
            full_name = repo.get('full_name', '')
            display_name = repo.get('display_name', '')
            saved_version = repo.get('version', '')
            default_branch = repo.get('default_branch', 'main')
            owner, repo_name = full_name.split('/', 1)
            repo_status = repo.get('status', '')

            # Репозитории с неудачной загрузкой — помечаем на повтор
            if repo_status in ('failed', 'incomplete'):
                repo_updates.append((repo, True, saved_version))
                has_update_count += 1
                retry_failed_count += 1
                checked_count += 1
                pct = int(checked_count / total_repos * 100)
                print(f"\r  Проверка: {checked_count}/{total_repos} ({pct}%) | Новых версий: {has_update_count}", end="", flush=True)
                continue

            try:
                has_new, latest_version = self.github.check_new_version(
                    owner, repo_name, default_branch, saved_version
                )
            except Exception as e:
                print(f"  ✗ {full_name}: ошибка {e}")
                repo_updates.append((repo, False, saved_version))
                checked_count += 1
                continue

            repo_updates.append((repo, has_new, latest_version))
            checked_count += 1
            if has_new:
                has_update_count += 1

            # Компактный прогресс
            pct = int(checked_count / total_repos * 100)
            print(f"\r  Проверка: {checked_count}/{total_repos} ({pct}%) | Новых версий: {has_update_count}", end="", flush=True)

        print()  # newline
        if retry_failed_count:
            print(f"\n  ✓ Проверка завершена: {has_update_count} обновлений доступно ({retry_failed_count} неудачных на повтор)\n")
        else:
            print(f"\n  ✓ Проверка завершена: {has_update_count} обновлений доступно\n")

        if has_update_count == 0:
            print("  ✓ Все репозитории уже актуальны!")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Phase 2: Показать таблицу репозиториев с обновлениями
        print("  " + "─" * 74)
        print(f"  {'#':<4} {'Репозиторий':<35} {'Было':<20} {'Стало':<20}")
        print("  " + "─" * 74)

        idx = 0
        for repo, has_new, latest_version in repo_updates:
            if has_new:
                idx += 1
                name = repo.get('full_name', '')[:33]
                old_ver = repo.get('version', '')[:18]
                new_ver = latest_version[:18]
                print(f"  {idx:<4} {name:<35} {old_ver:<20} {new_ver:<20}")
        print("  " + "─" * 74)

        # Phase 3: Обновление по выбранному режиму
        use_parallel = (mode == "parallel")

        if use_parallel:
            github_channels = get_channels_for_function("github")
            if len(github_channels) <= 1:
                print("\n  ⚠ Для параллельной отправки нужно 2+ канала.")
                print("  Сейчас доступен только 1 канал. Добавить новый?")
                add_choice = input("  [Y/N]: ").strip().lower()
                if add_choice == 'y':
                    new_ch = select_channel("github", allow_add=True)
                    if new_ch:
                        # Reload channels list after adding
                        github_channels = get_channels_for_function("github")
                    else:
                        print("\n  Отменено. Использую обычный режим.")
                        use_parallel = False
                else:
                    print("\n  Использую обычный режим.")
                    use_parallel = False

        if use_parallel:
            channels = [
                {"url": ch.url, "label": ch.label or ch.url}
                for ch in github_channels
            ]
            print(f"\n  Параллельная отправка в {len(channels)} канал(а)...\n")

            repos_to_update = [(repo, latest_version)
                               for repo, has_new, latest_version in repo_updates if has_new]
            zip_paths = []
            file_to_repo = {}
            updated_count = 0
            error_count = 0
            skipped_count = len(repo_updates) - has_update_count
            failed_names = []

            for repo, latest_version in repos_to_update:
                full_name = repo.get('full_name', '')
                display_name = repo.get('display_name', '')
                default_branch = repo.get('default_branch', 'main')
                owner, repo_name = full_name.split('/', 1)

                print(f"  📦 {display_name} — ↓ Скачиваю ZIP...")
                zip_path = self.github.download_zip(owner, repo_name, default_branch)

                if not zip_path or not os.path.exists(zip_path):
                    print(f"    ✗ Не удалось скачать ZIP")
                    error_count += 1
                    failed_names.append(full_name)
                    self.journal.update_repository(full_name, {
                        'version': latest_version,
                        'status': 'failed'
                    })
                    continue

                zip_size = os.path.getsize(zip_path)
                zip_size_str = format_file_size(zip_size)
                print(f"    ✓ {zip_size_str}")
                zip_paths.append(zip_path)

                repo_update = dict(repo)
                repo_update['version'] = latest_version
                repo_update['zip_size'] = zip_size
                file_to_repo[zip_path] = (full_name, repo_update)

            if zip_paths:
                print(f"\n  → Отправляю {len(zip_paths)} файл(ов) в {len(channels)} канал(а)...")
                uploader = ParallelGroupUploader(
                    files=zip_paths,
                    channels=channels,
                    cleanup=True,
                    journal=self.journal,
                )
                summary = uploader.run()

                for filepath, (full_name, repo_update) in file_to_repo.items():
                    uploaded_any = any(
                        filepath in r.files
                        for r in summary.channel_results.values()
                    )
                    if uploaded_any:
                        self.journal.update_repository(full_name, {
                            'version': repo_update.get('version', ''),
                            'status': 'sent',
                            'archive_size': repo_update.get('zip_size', 0)
                        })
                        updated_count += 1
                    else:
                        self.journal.update_repository(full_name, {
                            'version': repo_update.get('version', ''),
                            'status': 'failed',
                            'archive_size': repo_update.get('zip_size', 0)
                        })
                        error_count += 1
                        failed_names.append(full_name)
                        if os.path.exists(filepath):
                            self._safe_remove_file(filepath)
            else:
                print("  ✗ Нет файлов для отправки")

            browser = None
        else:
            auto_sync = (mode == "auto")
            if auto_sync:
                print("\n  Начинаю автоматическое обновление...\n")
            else:
                print("\n  Начинаю обновление...\n")

            browser = None
            try:
                browser = self._ensure_max_connected()
            except Exception as e:
                print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
                input("\n  Нажмите Enter для возврата в меню...")
                return

            updated_count = 0
            error_count = 0
            skipped_count = 0
            failed_names = []
            total_to_update = has_update_count
            repo_delay = self.config.get('archiver', {}).get('repo_delay', 30)
            split_mode = get_split_mode(self.config, "archiver", default="auto")

            if auto_sync:
                # Compact progress without progress bar
                for i, (repo, has_new, latest_version) in enumerate(repo_updates, 1):
                    if not has_new:
                        skipped_count += 1
                        continue

                    full_name = repo.get('full_name', '')
                    display_name = repo.get('display_name', '')
                    default_branch = repo.get('default_branch', 'main')
                    owner, repo_name = full_name.split('/', 1)

                    repo_update = dict(repo)
                    repo_update['version'] = latest_version
                    repo_update['zip_size'] = None

                    print(f"  [{updated_count + 1}/{has_update_count}] {display_name} — ↓...", end="", flush=True)

                    zip_path = self.github.download_zip(owner, repo_name, default_branch)

                    if not zip_path or not os.path.exists(zip_path):
                        print(" ✗")
                        error_count += 1
                        failed_names.append(full_name)
                        continue

                    zip_size = os.path.getsize(zip_path)

                    success, _ = browser.send_message_with_file(
                        text=self._build_message_text(repo_update, zip_size),
                        filepath=zip_path,
                        retries=self.config.get('archiver', {}).get('retries', 3),
                        retry_delay=self.config.get('archiver', {}).get('retry_delay', 10),
                        split_mode=split_mode,
                    )

                    if success:
                        self.journal.update_repository(full_name, {
                            'version': latest_version,
                            'status': 'sent',
                            'archive_size': zip_size
                        })
                        updated_count += 1
                        print(f" ✓")
                    else:
                        self.journal.update_repository(full_name, {'status': 'failed', 'archive_size': zip_size})
                        error_count += 1
                        failed_names.append(full_name)
                        print(" ✗")

                    if os.path.exists(zip_path):
                        self._safe_remove_file(zip_path)

                    time.sleep(repo_delay)
            else:
                with LiveProgressBar(has_update_count, "Синхронизация репозиториев") as bar:
                    current = 0
                    for i, (repo, has_new, latest_version) in enumerate(repo_updates, 1):
                        if not has_new:
                            skipped_count += 1
                            continue

                        current += 1
                        full_name = repo.get('full_name', '')
                        display_name = repo.get('display_name', '')
                        saved_version = repo.get('version', '')
                        default_branch = repo.get('default_branch', 'main')
                        owner, repo_name = full_name.split('/', 1)
                        stars = repo.get('stars', 0)
                        forks = repo.get('forks', 0)
                        desc = repo.get('description', '') or 'Без описания'

                        bar.update(current, item_name=display_name)

                        repo_update = dict(repo)
                        repo_update['version'] = latest_version
                        repo_update['zip_size'] = None  # Will be set after download

                        print(f"\n  📦 {display_name}")
                        print(f"  📝 {self._format_description(desc, 50)}")
                        print("    ↓ Скачиваю ZIP...")

                        zip_path = self.github.download_zip(owner, repo_name, default_branch)

                        if not zip_path or not os.path.exists(zip_path):
                            print("    ✗ Не удалось скачать ZIP")
                            error_count += 1
                            failed_names.append(full_name)
                            continue

                        zip_size = os.path.getsize(zip_path)
                        zip_size_str = format_file_size(zip_size)
                        print(f"    ✓ {zip_size_str}")

                        text = self._build_message_text(repo_update, zip_size)

                        print(f"    → Отправляю в MAX...")
                        success, _ = browser.send_message_with_file(
                            text=text,
                            filepath=zip_path,
                            retries=self.config.get('archiver', {}).get('retries', 3),
                            retry_delay=self.config.get('archiver', {}).get('retry_delay', 10),
                            split_mode=split_mode,
                        )

                        if success:
                            self.journal.update_repository(full_name, {
                                'version': latest_version,
                                'status': 'sent',
                                'archive_size': zip_size
                            })
                            updated_count += 1
                        else:
                            self.journal.update_repository(full_name, {'status': 'failed', 'archive_size': zip_size})
                            error_count += 1
                            failed_names.append(full_name)

                        if os.path.exists(zip_path):
                            self._safe_remove_file(zip_path)

                        time.sleep(repo_delay)

        print()
        print("\n" + "═" * 60)
        print("Синхронизация завершена")
        print(f"  Обновлено: {updated_count}")
        print(f"  Пропущено: {skipped_count}")
        print(f"  Ошибок: {error_count}")
        print("═" * 60)

        if failed_names:
            self._prompt_ignore_failed(failed_names)

        if browser:
            browser.close()

        input("\n  Нажмите Enter для возврата в меню...")

    def load_new_repositories(self, mode: str = "step"):
        """Загрузка новых репозиториев

        Автоматический автосбор: если в базе меньше репо чем запрошено,
        система сама собирает недостающие тира через tiered-коллектор.

        Args:
            mode: 'step' (пошаговая), 'auto' (автозагрузка), 'parallel' (параллельная)
        """
        print("\n" + "═" * 60)
        print("Загрузка новых репозиториев")
        print("═" * 60)

        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        limit = self.config.get('archiver', {}).get('limit', 100)

        self._init_github()

        # ── Process orphaned repos recovered from filenames ──
        if self._orphaned_repos_to_retry:
            self._process_orphaned_retries()
            self._orphaned_repos_to_retry = []

        # ── Auto-collect if needed ────────────────────────────
        from repo_collector import RepoCollector as _RepoCollector

        collector = _RepoCollector(
            self.github,
            per_page=self.config.get('repo_collector', {}).get('per_page', 100),
        )

        db_count_before = collector.database.get_count()
        print(f"\n  Целевое количество: {limit} репозиториев")
        print(f"  Текущая база: {db_count_before} репозиториев")

        if db_count_before < limit:
            collector.collect_until_count(limit)
        elif db_count_before > 0:
            print(f"\n  ✓ Базы достаточно ({db_count_before} >= {limit})")

        # ── Get top N from database (sorted by stars) ─────────
        top_repos = collector.database.get_all_sorted(
            sort_by="stargazers_count", reverse=True
        )

        if not top_repos:
            print("\n  ✗ Не удалось получить репозитории")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Получить updated_at для фильтрации дублей (без доп. API запросов)
        repos_to_process = []
        skipped_already_sent = 0
        skipped_different_version = 0

        for repo_info in top_repos:
            full_name = repo_info.get('full_name', '')
            if not full_name:
                continue

            # Используем updated_at вместо версии - это быстро, без доп. запросов
            updated_at = repo_info.get('updated_at', '')

            # Проверить, есть ли репозиторий с такой датой обновления
            existing = self.journal.get_repository(full_name)
            if existing:
                # Репозиторий уже есть в журнале
                existing_status = existing.get('status', '')
                if existing_status in ('failed', 'incomplete'):
                    # Неудачная загрузка — повторим
                    repos_to_process.append(repo_info)
                else:
                    existing_updated = existing.get('updated_at', '')
                    if not existing_updated:
                        # Старая запись без updated_at — уже отправлен
                        skipped_already_sent += 1
                    elif existing_updated == updated_at:
                        # Та же дата — пропускаем
                        skipped_already_sent += 1
                    else:
                        # Дата изменилась — это обновление для sync_repositories
                        skipped_different_version += 1
                continue

            repos_to_process.append(repo_info)

        # Фильтр игнорируемых репозиториев
        ignored_in_new = [r for r in repos_to_process if self.journal.is_ignored(r.get('full_name', ''))]
        repos_to_process = [r for r in repos_to_process if not self.journal.is_ignored(r.get('full_name', ''))]

        print(f"  Уже отправлены: {skipped_already_sent}")
        print(f"  Другие версии в журнале: {skipped_different_version}")
        print(f"  В игнор-листе: {len(ignored_in_new)}")
        print(f"  Осталось для загрузки: {len(repos_to_process)}\n")

        if not repos_to_process:
            print("  ✓ Все репозитории уже загружены!")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Режим из параметра
        use_parallel = (mode == "parallel")
        if use_parallel:
            github_channels = get_channels_for_function("github")
            if len(github_channels) <= 1:
                print("\n  ⚠ Для параллельной отправки нужно 2+ канала.")
                print("  Сейчас доступен только 1 канал. Добавить новый?")
                add_choice = input("  [Y/N]: ").strip().lower()
                if add_choice == 'y':
                    new_ch = select_channel("github", allow_add=True)
                    if new_ch:
                        github_channels = get_channels_for_function("github")
                    else:
                        print("\n  Отменено. Использую пошаговый режим.")
                        use_parallel = False
                else:
                    print("\n  Использую пошаговый режим.")
                    use_parallel = False

        loaded_count = 0
        error_count = 0
        failed_names = []
        browser = None

        if use_parallel:
            channels = [
                {"url": ch.url, "label": ch.label or ch.url}
                for ch in github_channels
            ]
            print(f"\n  Параллельная отправка в {len(channels)} канал(а)...\n")

            zip_paths = []
            file_to_repo = []

            with LiveProgressBar(len(repos_to_process), "Скачивание ZIP") as bar:
                for i, repo_info in enumerate(repos_to_process, 1):
                    full_name = repo_info.get('full_name', '')
                    display_name = repo_info.get('name', '')
                    default_branch = repo_info.get('default_branch', 'main')
                    owner, repo_name = full_name.split('/', 1)

                    bar.update(i, item_name=display_name)
                    zip_path = self.github.download_zip(owner, repo_name, default_branch)

                    if not zip_path or not os.path.exists(zip_path):
                        error_count += 1
                        failed_names.append(full_name)
                        repo_data = self.github.build_repo_data(repo_info)
                        repo_data['status'] = 'failed'
                        self.journal.add_repository(repo_data)
                        continue

                    zip_size = os.path.getsize(zip_path)
                    zip_paths.append(zip_path)

                repo_data = self.github.build_repo_data(repo_info)
                repo_data['zip_size'] = zip_size
                file_to_repo.append((zip_path, full_name, repo_data))

            if zip_paths:
                print(f"\n  → Отправляю {len(zip_paths)} файл(ов) в {len(channels)} канал(а)...")
                uploader = ParallelGroupUploader(
                    files=zip_paths,
                    channels=channels,
                    cleanup=True,
                    journal=self.journal,
                )
                summary = uploader.run()

                for filepath, full_name, repo_data in file_to_repo:
                    uploaded_any = any(
                        filepath in r.files
                        for r in summary.channel_results.values()
                    )
                    if uploaded_any:
                        repo_data['status'] = 'sent'
                        self.journal.add_repository(repo_data)
                        loaded_count += 1
                    else:
                        repo_data['status'] = 'failed'
                        self.journal.add_repository(repo_data)
                        error_count += 1
                        failed_names.append(full_name)
                        if os.path.exists(filepath):
                            self._safe_remove_file(filepath)
            else:
                print("  ✗ Нет файлов для отправки")
        else:
            auto_load = (mode == "auto")
            repo_delay = self.config.get('archiver', {}).get('repo_delay', 30)

            try:
                browser = self._ensure_max_connected()
            except Exception as e:
                print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
                input("\n  Нажмите Enter для возврата в меню...")
                return

            with LiveProgressBar(len(repos_to_process), "Загрузка репозиториев") as bar:
                for i, repo_info in enumerate(repos_to_process, 1):
                    full_name = repo_info.get('full_name', '')
                    display_name = repo_info.get('name', '')
                    stars = repo_info.get('stargazers_count', 0)
                    desc = repo_info.get('description', '') or 'Без описания'

                    bar.update(i, item_name=display_name)
                    print(f"\n  {'═' * 56}")
                    print(f"  #{i}/{len(repos_to_process)} | {display_name}")
                    print(f"  {'─' * 56}")
                    print(f"  ⭐ {self._format_stars(stars)} звёзд | 🍴 {self._format_stars(repo_info.get('forks_count', 0))} форков")
                    print(f"  📝 {self._format_description(desc, 50)}")

                    if auto_load:
                        choice = 'y'
                    else:
                        print()
                        choice = input("  [Enter] Загрузить | [S] Пропустить | [A] Все | [Q] Выход: ").strip().lower()

                        if choice == 'a':
                            auto_load = True
                            choice = 'y'

                    if choice == 's':
                        print("  Пропускаю...")
                        continue
                    elif choice == 'q':
                        print("\n  Выход из загрузки...")
                        break
                    elif choice in ['', 'y', 'enter']:
                        success = self._download_and_send_repo_info_connected(browser, repo_info)

                        if success:
                            loaded_count += 1
                            print(f"\n  ✓ Загружено ({loaded_count}/{len(repos_to_process)})")
                        else:
                            error_count += 1
                            failed_names.append(full_name)
                            print(f"\n  ✗ Ошибка загрузки")
                    else:
                        success = self._download_and_send_repo_info_connected(browser, repo_info)
                        if success:
                            loaded_count += 1
                        else:
                            error_count += 1
                            failed_names.append(full_name)

                    time.sleep(0.5)

        print("\n" + "═" * 60)
        print("Загрузка завершена")
        print(f"  Обработано: {loaded_count + error_count}")
        print(f"  Успешно: {loaded_count}")
        print(f"  Ошибок: {error_count}")
        print("═" * 60)

        if failed_names:
            self._prompt_ignore_failed(failed_names)

        if browser:
            browser.close()

        input("\n  Нажмите Enter для возврата в меню...")

    def retry_failed_repositories(self):
        """Повторить загрузку неудачных репозиториев из журнала.

        Берёт все репозитории со статусом 'failed' или 'incomplete',
        перекачивает и пересылает их в MAX.
        """
        print("\n" + "═" * 60)
        print("Повтор неудачных загрузок")
        print("═" * 60)

        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Собрать неудачные репозитории
        failed_repos = self.journal.get_repositories_by_status('failed')
        incomplete_repos = self.journal.get_repositories_by_status('incomplete')
        retry_repos = failed_repos + incomplete_repos

        if not retry_repos:
            print("\n  ✓ Нет неудачных загрузок для повторения.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Отфильтровать игнорируемые
        ignored_retry = [r for r in retry_repos if self.journal.is_ignored(r.get('full_name', ''))]
        retry_repos = [r for r in retry_repos if not self.journal.is_ignored(r.get('full_name', ''))]

        print(f"\n  Найдено неудачных загрузок: {len(failed_repos) + len(incomplete_repos)}")
        if ignored_retry:
            print(f"  В игнор-листе (пропускаются): {len(ignored_retry)}")
        print(f"  К повторению: {len(retry_repos)}\n")

        for i, repo in enumerate(retry_repos[:10], 1):
            print(f"    {i}. {repo.get('full_name', 'unknown')} ({repo.get('status', '?')})")
        if len(retry_repos) > 10:
            print(f"    ... и ещё {len(retry_repos) - 10}")

        print()
        choice = input("  [Enter] Повторить все | [Q] Отмена: ").strip().lower()
        if choice == 'q':
            print("\n  Отменено.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        self._init_github()

        browser = None
        try:
            browser = self._ensure_max_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        repo_delay = self.config.get('archiver', {}).get('repo_delay', 30)
        split_mode = get_split_mode(self.config, "archiver", default="auto")
        retries = self.config.get('archiver', {}).get('retries', 3)
        retry_delay = self.config.get('archiver', {}).get('retry_delay', 10)

        recovered_count = 0
        still_failed_count = 0
        failed_names = []

        with LiveProgressBar(len(retry_repos), "Повтор неудачных загрузок") as bar:
            for i, repo_data in enumerate(retry_repos, 1):
                full_name = repo_data.get('full_name', '')
                display_name = repo_data.get('display_name', full_name)
                default_branch = repo_data.get('default_branch', 'main')

                bar.update(i, item_name=display_name)
                print(f"\n  {'═' * 56}")
                print(f"  #{i}/{len(retry_repos)} | {display_name}")
                print(f"  Предыдущий статус: {repo_data.get('status', '?')}")
                print(f"  {'─' * 56}")

                # Скачать ZIP
                owner, repo_name = full_name.split('/', 1)
                print("    ↓ Скачиваю ZIP...")
                zip_path = self.github.download_zip(owner, repo_name, default_branch)

                if not zip_path or not os.path.exists(zip_path):
                    print("    ✗ Не удалось скачать ZIP")
                    still_failed_count += 1
                    failed_names.append(full_name)
                    continue

                zip_size = os.path.getsize(zip_path)
                zip_size_str = format_file_size(zip_size)
                print(f"    ✓ {zip_size_str}")

                # Подготовить сообщение
                text = self._build_message_text(repo_data, zip_size)
                print(f"    → Отправляю в MAX...")

                success, _ = browser.send_message_with_file(
                    text=text,
                    filepath=zip_path,
                    retries=retries,
                    retry_delay=retry_delay,
                    split_mode=split_mode,
                )

                if success:
                    self.journal.update_repository(full_name, {
                        'status': 'sent',
                        'archive_size': zip_size
                    })
                    recovered_count += 1
                    print(f"    ✓ Отправлено (восстановлено)")
                else:
                    self.journal.update_repository(full_name, {
                        'status': 'failed',
                        'archive_size': zip_size
                    })
                    still_failed_count += 1
                    failed_names.append(full_name)
                    print(f"    ✗ Не удалось отправить")

                # Удалить временный файл
                if os.path.exists(zip_path):
                    self._safe_remove_file(zip_path)

                time.sleep(repo_delay)

        print()
        print("\n" + "═" * 60)
        print("Повтор загрузок завершён")
        print(f"  Восстановлено: {recovered_count}")
        print(f"  Всё ещё неудачных: {still_failed_count}")
        print("═" * 60)

        if failed_names:
            self._prompt_ignore_failed(failed_names)

        if browser:
            browser.close()

        input("\n  Нажмите Enter для возврата в меню...")

    def _download_and_send(self, repo_data: dict, new_version: str | None = None) -> bool:
        """
        Скачать репозиторий и отправить в MAX

        Args:
            repo_data: Данные репозитория из журнала
            new_version: Новая версия (если обновление)

        Returns:
            True при успехе
        """
        full_name = repo_data.get('full_name', '')
        owner, repo_name = full_name.split('/', 1)
        default_branch = repo_data.get('default_branch', 'main')

        # Обновить версию если передана
        if new_version:
            repo_data['version'] = new_version

        version = repo_data.get('version', '')

        # Second line of defence: check if this exact version already in journal
        if version and self.journal.is_version_in_journal(full_name, version):
            print(f"    ✓ Версия {version} уже загружена, пропускаю")
            return True

        # Скачать ZIP
        zip_path = self.github.download_zip(owner, repo_name, default_branch)  # type: ignore

        if not zip_path or not os.path.exists(zip_path):
            print("    ✗ Не удалось скачать ZIP")
            return False

        zip_size = os.path.getsize(zip_path)

        # Подготовить данные для сообщения
        text = self._build_message_text(repo_data, zip_size)

        # Отправить в MAX
        browser = self._init_max_browser()  # type: ignore

        split_mode = get_split_mode(self.config, "archiver", default="auto")
        success, _ = browser.send_message_with_file(
            text=text,
            filepath=zip_path,
            retries=self.config.get('archiver', {}).get('retries', 3),
            retry_delay=self.config.get('archiver', {}).get('retry_delay', 10),
            split_mode=split_mode,
        )

        # Удалить временный файл
        if self._safe_remove_file(zip_path):
            print("    ✓ Временный файл удалён")
        else:
            print(f"    ⚠ Не удалось удалить файл: {os.path.basename(zip_path)}")

        # Обновить журнал
        if success:
            self.journal.update_repository(full_name, {
                'version': new_version or repo_data.get('version'),
                'status': 'sent',
                'archive_size': zip_size
            })
        else:
            self.journal.update_repository(full_name, {
                'status': 'failed',
                'archive_size': zip_size
            })

        return success

    def _download_and_send_repo_info(self, repo_info: dict) -> bool:
        """
        Download and send new repository (from API data)

        Args:
            repo_info: Data from GitHub API

        Returns:
            True on success
        """
        browser = self._init_max_browser()
        return self._download_and_send_repo_info_connected(browser, repo_info)

    def _download_and_send_repo_info_connected(self, browser: BrowserMAX, repo_info: dict) -> bool:
        """
        Download and send repository with provided browser connection

        Args:
            browser: BrowserMAX instance (already connected)
            repo_info: Data from GitHub API

        Returns:
            True on success
        """
        full_name = repo_info.get('full_name', '')
        owner, repo_name = full_name.split('/', 1)
        default_branch = repo_info.get('default_branch', 'main')

        repo_data = self.github.build_repo_data(repo_info)
        version = repo_data.get('version', '')

        # Second line of defence: check if this exact version already in journal
        if version and self.journal.is_version_in_journal(full_name, version):
            print(f"    ✓ Версия {version} уже загружена, пропускаю")
            return True

        # Predict the filename that download_zip will create
        expected_filename = f"{owner}-{repo_name}-{default_branch}.zip"

        # Track in journal BEFORE download — survives Ctrl+C so we can recover
        self.journal.set_currently_processing(full_name, expected_filename)

        print("    ↓ Скачиваю ZIP...")
        zip_path = self.github.download_zip(owner, repo_name, default_branch)

        if not zip_path or not os.path.exists(zip_path):
            print("    ✗ Не удалось скачать ZIP")
            # Mark as failed in journal so retry will pick it up
            repo_data['status'] = 'failed'
            repo_data['version'] = repo_data.get('version', '') or 'unknown'
            self.journal.add_repository(repo_data)
            self.journal.clear_currently_processing()
            return False

        zip_size = os.path.getsize(zip_path)
        zip_size_str = format_file_size(zip_size)
        print(f"    ✓ {zip_size_str}")

        text = self._build_message_text(repo_data, zip_size)

        print(f"    → Отправляю в MAX...")

        # Send message with file - returns (success, file_deletable)
        # Note: send_message_with_file confirms file message appears in chat before returning
        split_mode = get_split_mode(self.config, "archiver", default="auto")
        success, _ = browser.send_message_with_file(
            text=text,
            filepath=zip_path,
            retries=self.config.get('archiver', {}).get('retries', 3),
            retry_delay=self.config.get('archiver', {}).get('retry_delay', 10),
            split_mode=split_mode,
        )

        # If upload failed, log and move on
        if not success:
            print(f"    ⚠ Upload failed")
            # Clean up failed file
            if self._safe_remove_file(zip_path):
                print("    ✓ Failed file cleaned up")
            else:
                print(f"    ⚠ Could not remove failed file: {os.path.basename(zip_path)}")
            repo_data['status'] = 'failed'
            repo_data['version'] = repo_data.get('version', '') or 'unknown'
            repo_data['archive_size'] = zip_size
            self.journal.add_repository(repo_data)
            self.journal.clear_currently_processing()
            return False

        # Clean up temp file after upload
        if self._safe_remove_file(zip_path, max_wait=120):
            print("    ✓ Temp file removed")
        else:
            print(f"    ⚠ Could not remove file: {os.path.basename(zip_path)} (will be cleaned at next startup)")

        repo_data['status'] = 'sent' if success else 'failed'
        repo_data['version'] = repo_data.get('version', '') or 'unknown'
        repo_data['archive_size'] = zip_size
        self.journal.add_repository(repo_data)
        self.journal.clear_currently_processing()

        return success

    def _prompt_ignore_failed(self, failed_names: list):
        """Предложить добавить ошибочные репозитории в игнор-лист"""
        if not failed_names:
            return

        print("\n  ⚠ Обнаружены ошибки при обработке:")
        for name in failed_names:
            print(f"    - {name}")

        print()
        print("  Добавить их в список игнорирования?")
        print("  [1] Добавить все")
        print("  [2] Выбрать по одному")
        print("  [3] Пропустить")
        print()

        try:
            choice = prompt_numeric_choice("Ваш выбор [1/2/3]", ["1", "2", "3"])
        except (EOFError, KeyboardInterrupt):
            return

        if choice == '1':
            added = self.journal.add_ignored_batch(failed_names)
            print(f"  ✓ Добавлено {added} репозиториев в игнор-лист")

        elif choice == '2':
            added_count = 0
            for name in failed_names:
                if self.journal.is_ignored(name):
                    print(f"  • {name} — уже в игнор-листе")
                    continue
                try:
                    sub = input(f"  {name} — Добавить в игнор? [Y/N/A]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    break
                if sub == 'a':
                    remaining = [n for n in failed_names if n != name and not self.journal.is_ignored(n)]
                    remaining.append(name)
                    added = self.journal.add_ignored_batch(remaining)
                    added_count += added
                    print(f"  ✓ Добавлено {added} репозиториев в игнор-лист")
                    break
                elif sub in ('', 'y', 'yes'):
                    self.journal.add_ignored(name)
                    added_count += 1
                    print(f"  ✓ Добавлен в игнор-лист")
                else:
                    print("  • Пропущен")
            if added_count:
                print(f"  ✓ Всего добавлено: {added_count}")

        else:
            print("  • Пропущено")

    def _manage_ignore_list(self):
        """Управление списком игнорирования"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("\n" + "═" * 60)
            print("Список игнорирования")
            print("═" * 60)

            ignored = self.journal.get_ignored()

            if not ignored:
                print("\n  Список игнорирования пуст.")
                input("\n  Нажмите Enter для возврата в меню...")
                return

            print(f"\n  Всего репозиториев: {len(ignored)}\n")
            for i, name in enumerate(ignored, 1):
                print(f"  {i}. {name}")

            print()
            print("  [1] Удалить из списка (по номеру)")
            print("  [2] Очистить весь список")
            print("  [3] Назад")
            print()

            choice = prompt_numeric_choice("Ваш выбор [1/2/3]", ["1", "2", "3"])

            if choice == '1':
                try:
                    num_input = input("\n  Введите номер для удаления: ").strip()
                    if not num_input.isdigit():
                        print("\n  ✗ Неверный ввод. Введите число.")
                    else:
                        num = int(num_input)
                        if 1 <= num <= len(ignored):
                            removed = ignored[num - 1]
                            self.journal.remove_ignored(removed)
                            print(f"\n  ✓ {removed} удалён из игнор-листа")
                        else:
                            print(f"\n  ✗ Неверный номер. Введите от 1 до {len(ignored)}")
                except ValueError:
                    print("\n  ✗ Неверный ввод")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '2':
                confirm = input("\n  Очистить весь список игнорирования? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes'):
                    cleared = self.journal.clear_ignored()
                    print(f"\n  ✓ Очищено {cleared} записей")
                    input("\n  Нажмите Enter для возврата в меню...")
                    return
                else:
                    print("\n  Отменено")
                    input("\n  Нажмите Enter для продолжения...")

            elif choice == '3':
                break

    def _manage_journals(self):
        """Управление очисткой журналов"""
        from media_archiver import MediaJournal
        from channel_downloader import DownloadJournal
        from backuper_journal import BackuperJournal
        from softportal_journal import SoftPortalJournal

        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("\n" + "═" * 60)
            print("Очистка журналов")
            print("═" * 60)

            # Получить статистику каждого журнала
            j_stats = self.journal.get_stats()
            mj = MediaJournal("media_journal.json")
            mj_stats = mj.get_stats()
            dj = DownloadJournal("download_journal.json")
            dj_stats = dj.get_stats()
            pj = PyPILibsJournal("pypi_libs_journal.json")
            pj_stats = pj.get_stats()
            bj = BackuperJournal("backuper_journal.json")
            bj_stats = bj.get_stats()
            cj = _GenericJournal("cargo_journal.json")
            cj_stats = cj.get_stats()
            nj = _GenericJournal("nuget_journal.json")
            nj_stats = nj.get_stats()
            rj = _GenericJournal("rubygems_journal.json")
            rj_stats = rj.get_stats()
            spj = SoftPortalJournal("softportal_journal.json")
            spj_stats = spj.get_stats()

            print(f"\n  Текущее состояние журналов:")
            print(f"  [1] journal.json — {j_stats['total']} репозиториев "
                  f"({j_stats['sent']} отправлено, {j_stats['failed']} ошибок)")
            print(f"  [2] media_journal.json — {mj_stats['total']} файлов "
                  f"({mj_stats['sent']} отправлено, {mj_stats['failed']} ошибок)")
            print(f"  [3] download_journal.json — {dj_stats['total']} файлов "
                  f"({dj_stats['downloaded']} скачано, {dj_stats['failed']} ошибок)")
            print(f"  [4] pypi_libs_journal.json — {pj_stats['total']} библиотек "
                  f"({pj_stats['sent']} отправлено, {pj_stats['failed']} ошибок)")
            print(f"  [5] backuper_journal.json — {bj_stats['total_backups']} бэкапов "
                  f"({bj_stats['uploaded']} отправлено, {bj_stats['failed']} ошибок)")
            print(f"  [6] cargo_journal.json — {cj_stats['total']} пакетов "
                  f"({cj_stats['sent']} отправлено, {cj_stats['failed']} ошибок)")
            print(f"  [7] nuget_journal.json — {nj_stats['total']} пакетов "
                  f"({nj_stats['sent']} отправлено, {nj_stats['failed']} ошибок)")
            print(f"  [8] rubygems_journal.json — {rj_stats['total']} пакетов "
                  f"({rj_stats['sent']} отправлено, {rj_stats['failed']} ошибок)")
            print(f"  [9] softportal_journal.json — {spj_stats['total']} программ "
                  f"({spj_stats.get('failed_count', 0)} неудачных)")

            print()
            print("  [1] Очистить journal.json")
            print("  [2] Очистить media_journal.json")
            print("  [3] Очистить download_journal.json")
            print("  [4] Очистить pypi_libs_journal.json")
            print("  [5] Очистить backuper_journal.json")
            print("  [6] Очистить cargo_journal.json")
            print("  [7] Очистить nuget_journal.json")
            print("  [8] Очистить rubygems_journal.json")
            print("  [9] Очистить softportal_journal.json")
            print("  [-] Очистить ВСЕ журналы")
            print("  [0] Назад")
            print()

            choice = input("  Ваш выбор: ").strip()

            if choice == '0':
                break

            if choice == '-':
                print("\n  ⚠ ВНИМАНИЕ: Будут очищены ВСЕ журналы!")
                confirm = input("  Введите 'ДА' для подтверждения: ").strip().lower()
                if confirm in ('да', 'yes', 'дa'):
                    self.journal.clear()
                    MediaJournal("media_journal.json").clear()
                    DownloadJournal("download_journal.json").clear()
                    PyPILibsJournal("pypi_libs_journal.json").clear()
                    BackuperJournal("backuper_journal.json").clear()
                    _GenericJournal("cargo_journal.json").clear()
                    _GenericJournal("nuget_journal.json").clear()
                    _GenericJournal("rubygems_journal.json").clear()
                    SoftPortalJournal("softportal_journal.json").reset()
                    print("  ✓ Все журналы очищены")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")
                continue

            elif choice == '1':
                confirm = input("\n  Очистить journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    self.journal.clear()
                    print("  ✓ journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '2':
                confirm = input("\n  Очистить media_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    MediaJournal("media_journal.json").clear()
                    print("  ✓ media_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '3':
                confirm = input("\n  Очистить download_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    DownloadJournal("download_journal.json").clear()
                    print("  ✓ download_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '4':
                confirm = input("\n  Очистить pypi_libs_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    PyPILibsJournal("pypi_libs_journal.json").clear()
                    print("  ✓ pypi_libs_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '5':
                confirm = input("\n  Очистить backuper_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    BackuperJournal("backuper_journal.json").clear()
                    print("  ✓ backuper_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '6':
                confirm = input("\n  Очистить cargo_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    _GenericJournal("cargo_journal.json").clear()
                    print("  ✓ cargo_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '7':
                confirm = input("\n  Очистить nuget_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    _GenericJournal("nuget_journal.json").clear()
                    print("  ✓ nuget_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '8':
                confirm = input("\n  Очистить rubygems_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    _GenericJournal("rubygems_journal.json").clear()
                    print("  ✓ rubygems_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '9':
                confirm = input("\n  Очистить softportal_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    SoftPortalJournal("softportal_journal.json").reset()
                    print("  ✓ softportal_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

    # ──────────────────────────────────────────────
    # Audit & Restore Publications
    # ──────────────────────────────────────────────

    def _extract_repo_from_filename(self, filename: str) -> str | None:
        """
        Extract owner/repo from a filename like 'owner-repo-main.zip'.
        Tries all possible split positions, checks against journal + GitHub API.
        """
        name = re.sub(r'\.zip(?:\.7z\.\d+)?$', '', filename)

        for suffix in ('-main', '-master'):
            if name.endswith(suffix):
                prefix = name[:-len(suffix)]
                break
        else:
            parts = name.rsplit('-', 1)
            if len(parts) == 2:
                prefix = parts[0]
            else:
                # Try underscore as separator for names like 'test_full'
                parts = name.rsplit('_', 1)
                if len(parts) != 2:
                    return None
                prefix = parts[0]

        parts = prefix.split('-')
        if len(parts) < 2:
            return None

        candidates = []
        for i in range(1, len(parts)):
            owner = '-'.join(parts[:i])
            repo = '-'.join(parts[i:])
            candidates.append(f"{owner}/{repo}")

        # 1) Check against known repos in journal (fastest)
        known = {e.get('full_name', '').lower()
                 for e in self.journal.get_all_repositories() if e.get('full_name')}
        for c in candidates:
            if c.lower() in known:
                return c

        # 2) Verify via GitHub API
        for c in candidates:
            try:
                o, r = c.split("/", 1)
                details = self.github.get_repository_details(o, r)
                if details and details.get('id'):
                    return c
            except Exception:
                continue

        # 3) Fallback: shortest owner first (most common pattern)
        return candidates[0]

    def _show_audit_table(self, grouped: dict):
        """Display audit results in a formatted table."""
        complete = grouped.get("complete", [])
        incomplete = grouped.get("incomplete", [])
        truly_missing = grouped.get("truly_missing", None)
        found_count = grouped.get("found_count", 0)
        total_known = grouped.get("total_known", 0)

        # If audit_channel_completeness was called with known_repos, use truly_missing
        # (more accurate — computed across all 3 sources). Otherwise fall back to
        # journal scan (only DOM-scanned repos).
        if truly_missing is not None:
            journal_missing_names = sorted(truly_missing)
        else:
            all_found = set()
            for item in [*complete, *incomplete]:
                fn = item.get("full_name", "")
                if "/" in fn:
                    all_found.add(fn)
            journal_missing_names = sorted(
                e.get('full_name', '') for e in self.journal.get_all_repositories()
                if e.get('full_name') and '/' in e['full_name'] and e['full_name'] not in all_found
            )

        print("\n" + "═" * 60)
        print("          АУДИТ ЦЕЛОСТНОСТИ ПУБЛИКАЦИЙ")
        print("═" * 60)
        print(f"  ✅ Полных публикаций: {len(complete)}")
        print(f"  ⚠ Неполных публикаций: {len(incomplete)}")
        if truly_missing is not None and total_known:
            print(f"  📊 Найдено: {found_count}/{total_known} репозиториев")
        if journal_missing_names:
            print(f"  ❌ Из журнала не опубликовано: {len(journal_missing_names)}")
        print("─" * 60)

        no_issues = not incomplete and not journal_missing_names
        if truly_missing is not None and total_known and found_count >= total_known:
            no_issues = True

        if no_issues:
            print("\n  ✓ Все публикации целостны!")
            return

        # ── Summary by issue type ──
        missing_file_items = [i for i in incomplete if i.get("issue") == "missing_file"]
        missing_volumes_items = [i for i in incomplete if i.get("issue") == "missing_volumes"]
        missing_text_items = [i for i in incomplete if i.get("issue") == "missing_text"]

        if missing_file_items:
            print(f"\n  📁 Нет файлов ({len(missing_file_items)}):")
            for item in missing_file_items:
                fn = item.get("full_name", "?")
                display = item.get("display_name", fn.split("/")[-1])
                print(f"    {display:20s}  ({fn})")

        if missing_volumes_items:
            print(f"\n  📦 Не все тома ({len(missing_volumes_items)}):")
            for item in missing_volumes_items:
                fn = item.get("full_name", "?")
                display = item.get("display_name", fn.split("/")[-1])
                missing = ", ".join(item.get("missing_volumes", []))
                have = len(item.get("file_idxs", []))
                print(f"    {display:20s}  есть {have} томов, не хватает: {missing}")

        if missing_text_items:
            orphan_groups: dict[str, int] = {}
            for item in missing_text_items:
                fn = item.get("full_name", item.get("display_name", "?"))
                orphan_groups[fn] = orphan_groups.get(fn, 0) + len(item.get("file_idxs", []))

            print(f"\n  🗑 Файлы-сироты (без описания):")
            for fn, count in sorted(orphan_groups.items(), key=lambda x: -x[1]):
                short = fn.replace("-main.zip", ".zip").replace("-master.zip", ".zip")
                short = short.replace(".7z.", ".")
                if short.endswith(".002") or short.endswith(".001"):
                    base = short.rsplit(".", 1)[0]
                    print(f"    {base:35s}  ({count} копий)")
                else:
                    print(f"    {short:40s}  ({count} копий)")

        if journal_missing_names:
            print(f"\n  ❌ Не найдены в канале ({len(journal_missing_names)}):")
            for fn in journal_missing_names[:20]:
                # Try to get stars from journal
                entry = self.journal.get_repository(fn)
                stars = entry.get("stars", 0) if entry else 0
                print(f"    {fn:45s}  ⭐ {stars}")
            if len(journal_missing_names) > 20:
                print(f"    ... и ещё {len(journal_missing_names) - 20}")

        print()

    def audit_and_restore_publications(self):
        """Audit the channel, display results, and restore incomplete publications."""
        print("\n" + "═" * 60)
        print("          АУДИТ — ОЧИСТКА / ВОССТАНОВЛЕНИЕ ПУБЛИКАЦИЙ")
        print("═" * 60)

        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        self._init_github()

        browser = None
        try:
            browser = self._ensure_max_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Build known_repos from journal for progress tracking
        journal_repos = {
            e['full_name'] for e in self.journal.get_all_repositories()
            if e.get('full_name') and '/' in e['full_name']
        }

        grouped = browser.audit_channel_completeness(known_repos=journal_repos)

        registry = ScrollRegistry()
        channel_msgs = grouped.pop("channel_messages", [])
        if channel_msgs:
            registry.from_messages(channel_msgs)

        self._show_audit_table(grouped)

        incomplete = grouped.get("incomplete", [])
        truly_missing = grouped.get("truly_missing", set())

        if not incomplete and not truly_missing:
            input("\n  Нажмите Enter для возврата в меню...")
            if browser:
                browser.close()
            return

        # Coverage check
        total_known = grouped.get("total_known", 0)
        found_count = grouped.get("found_count", 0)
        coverage = found_count / total_known if total_known > 0 else 0
        scan_complete = coverage >= 0.8
        can_restore_missing = scan_complete and bool(truly_missing)

        if not scan_complete:
            print(f"\n  ⚠ Сканирование неполное: найдено {found_count}/{total_known} "
                  f"({coverage:.0%})")
            print("  Потерянные репозитории не будут дозагружаться — "
                  "они могут быть в канале, но не загружены браузером")

        # Mode selection
        print()
        print("  Выберите режим работы:")
        print("  [1] Только очистка — удалить неполные/битые публикации из канала")
        if can_restore_missing:
            print("  [2] Полное восстановление — очистка + дозагрузка потерянных из журнала")
        elif incomplete:
            print("  [2] Полное восстановление — удалить и перезалить неполные")
        print("  [S] Пропустить")
        print()

        # Build valid mode list
        valid_modes = ['1', 's']
        if incomplete:
            valid_modes.append('2')

        mode = input(f"  Ваш выбор [{'/'.join(valid_modes)}]: ").strip().lower()

        if mode == 's':
            print("\n  Пропущено.")
            if browser:
                browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if mode == '1':
            # ── Cleanup mode ──
            self._cleanup_publications(browser, grouped, registry)

            print("\n" + "═" * 60)
            print("ОЧИСТКА ЗАВЕРШЕНА")
            print("═" * 60)

            print("\n  Выполняю финальную проверку...")
            final = browser.audit_channel_completeness(known_repos=journal_repos)
            remaining = len(final.get("incomplete", []))
            if remaining == 0:
                print("  ✓ Канал чист, неполных публикаций не осталось")
            else:
                print(f"  ⚠ Осталось {remaining} неполных публикаций (не удалось удалить)")

        elif mode == '2':
            # ── Restore mode ──
            restored_count = 0
            error_count = 0
            skipped_count = 0

            if incomplete:
                restored_count, error_count, skipped_count = self._restore_incomplete_publications(
                    browser, incomplete, registry
                )

            uploaded_count = 0
            missing_error_count = 0
            if can_restore_missing and truly_missing:
                print("\n" + "─" * 56)
                print("Дозагрузка потерянных репозиториев:")
                print("─" * 56)

                for full_name in sorted(truly_missing):
                    print(f"\n  📦 {full_name}")
                    success = self._upload_missing_publication(browser, full_name)
                    if success:
                        uploaded_count += 1
                        print(f"  ✓ Загружен")
                    else:
                        missing_error_count += 1
                        print(f"  ✗ Ошибка загрузки")

            print("\n" + "═" * 60)
            print("ВОССТАНОВЛЕНИЕ ЗАВЕРШЕНО")
            print(f"  Восстановлено неполных: {restored_count}")
            print(f"  Дозагружено потерянных: {uploaded_count}")
            if error_count or missing_error_count:
                print(f"  Ошибок: {error_count + missing_error_count}")
            print(f"  Пропущено: {skipped_count}")
            print("═" * 60)

            if restored_count > 0 or uploaded_count > 0:
                print("\n  Выполняю финальную верификацию...")
                final = browser.audit_channel_completeness(known_repos=journal_repos)
                remaining = len(final.get("incomplete", []))
                truly_missing_final = final.get("truly_missing", set())
                if remaining == 0:
                    print("  ✓ Все публикации целостны!")
                else:
                    print(f"  ⚠ Осталось {remaining} неполных публикаций")
                if truly_missing_final:
                    print(f"  ⚠ Не найдено в канале: {len(truly_missing_final)} репозиториев")

        if browser:
            browser.close()

        input("\n  Нажмите Enter для возврата в меню...")

    def _restore_incomplete_publications(self, browser, incomplete: list,
                                         registry: ScrollRegistry) -> tuple[int, int, int]:
        """
        Restore incomplete publications: bulk delete old messages,
        then re-download and re-upload per repo.

        Args:
            browser: Connected BrowserMAX instance
            incomplete: List of incomplete audit items
            registry: ScrollRegistry with all channel messages

        Returns:
            (restored_count, error_count, skipped_count)
        """
        # ── Step 1: Bulk delete all incomplete messages in one pass ──
        print("\n" + "─" * 56)
        print("Шаг 1: удаление старых неполных публикаций:")
        print("─" * 56)

        target_texts = registry.find_target_texts(incomplete)
        if target_texts:
            print(f"  → Найдено {len(target_texts)} сообщений для удаления")
            browser.delete_messages_by_texts(target_texts, label="восстановление")
        else:
            print("  ⚠ Нет текстов для удаления (реестр пуст)")

        # ── Step 2: Interactive re-upload ──
        restored_count = 0
        error_count = 0
        skipped_count = 0
        restored_repos: set[str] = set()
        restore_all = False

        print(f"\n  {'─' * 56}")
        print("Шаг 2: перезаливка репозиториев:")
        print("─" * 56)
        print("\n  Для каждого можно:")
        print("    [Enter] — переопубликовать")
        print("    [S]     — пропустить")
        print("    [A]     — восстановить все")
        print("    [Q]     — выйти из восстановления")
        print()

        for i, item in enumerate(incomplete, 1):
            fn = item.get("full_name", "?")
            display = item.get("display_name", fn.split("/")[-1])
            issue = item.get("issue", "?")

            # Resolve canonical repo name, especially for orphans (filename → owner/repo)
            canonical_fn = fn
            if canonical_fn and "/" not in canonical_fn:
                extracted = self._extract_repo_from_filename(canonical_fn)
                if extracted and "/" in extracted:
                    canonical_fn = extracted

            # Skip if this repo was already restored (prevents N uploads for N orphan copies)
            if canonical_fn and "/" in canonical_fn and canonical_fn in restored_repos:
                skipped_count += 1
                continue

            if restore_all:
                choice = 'y'
            else:
                print(f"\n  {'─' * 56}")
                print(f"  #{i}: {display} ({fn})")
                print(f"       причина: {issue}")
                print("       (старое сообщение уже удалено)")
                choice = input("  [Enter] восстановить | [S] пропустить | [A] все | [Q] выход: ").strip().lower()

            if choice == 'a':
                restore_all = True
                choice = 'y'

            if choice in ('', 'y', 'enter'):
                # Build repo context for restoration
                repo_ctx = self._build_restore_context(item)

                success = self._restore_publication(browser, item, repo_ctx, skip_delete=True)

                if success:
                    restored_count += 1
                    restored_repos.add(canonical_fn)
                    print(f"  ✓ {display} — восстановлен")
                else:
                    error_count += 1
                    print(f"  ✗ {display} — ошибка восстановления")
            elif choice == 's':
                skipped_count += 1
                print(f"  • Пропущен")
            elif choice == 'q':
                print("\n  Выход из восстановления...")
                break

        return restored_count, error_count, skipped_count

    def _cleanup_publications(self, browser, grouped: dict, registry: ScrollRegistry):
        """
        Cleanup mode: delete incomplete publications from channel in one pass,
        then mark in journal. Does NOT re-upload anything.

        Args:
            browser: Connected BrowserMAX instance
            grouped: Audit results dict with 'incomplete' key
            registry: ScrollRegistry with all channel messages
        """
        incomplete = grouped.get("incomplete", [])

        if not incomplete:
            print("  ✓ Нечего чистить")
            return

        print("\n" + "─" * 56)
        print("Удаление неполных публикаций из канала:")
        print("─" * 56)

        print(f"\n  Всего неполных: {len(incomplete)}")
        for i, item in enumerate(incomplete, 1):
            fn = item.get("full_name", "?")
            display = item.get("display_name", fn.split("/")[-1])
            issue = item.get("issue", "?")
            print(f"  #{i}: {display} ({fn}) — {issue}")

        target_texts = registry.find_target_texts(incomplete)
        if not target_texts:
            print("\n  ⚠ Нет текстов для удаления (реестр пуст)")
            return

        print(f"\n  → Найдено {len(target_texts)} сообщений для удаления")
        deleted_texts = browser.delete_messages_by_texts(target_texts, label="очистка")

        cleaned_repos: set[str] = set()
        for item in incomplete:
            fn = item.get("full_name", "")
            canonical = ""
            if fn and "/" in fn:
                canonical = fn
            elif fn:
                extracted = self._extract_repo_from_filename(fn)
                if extracted and "/" in extracted:
                    canonical = extracted
            if not canonical:
                continue

            item_texts: set[str] = set()
            text_idx = item.get("text_idx")
            if text_idx is not None and 0 <= text_idx < len(registry.messages):
                t = registry.messages[text_idx].get("text", "").strip()
                if t:
                    item_texts.add(t)
            for fidx in item.get("file_idxs", []):
                if 0 <= fidx < len(registry.messages):
                    t = registry.messages[fidx].get("text", "").strip()
                    if t:
                        item_texts.add(t)

            if item_texts and item_texts <= deleted_texts:
                cleaned_repos.add(canonical)

        for repo in cleaned_repos:
            self.journal.update_repository(repo, {"status": "cleaned"})

        print(f"\n  ✓ Очищено из канала: {len(cleaned_repos)} репозиториев, удалено сообщений: {len(deleted_texts)}")

    def _upload_missing_publication(self, browser, full_name: str) -> bool:
        """
        Upload a missing repository (truly_missing) — download from GitHub and send to MAX.

        Args:
            browser: Connected BrowserMAX instance
            full_name: Repository full name (owner/repo)

        Returns:
            True on success
        """
        item = {"full_name": full_name}
        repo_ctx = self._build_restore_context(item)
        if not repo_ctx or not repo_ctx.get("owner") or not repo_ctx.get("repo"):
            print(f"    ✗ Не удалось получить данные репозитория")
            return False

        owner = repo_ctx["owner"]
        repo_name = repo_ctx["repo"]
        branch = repo_ctx.get("default_branch", "main")
        display = repo_ctx.get("display_name", repo_name)

        print(f"    ↓ Скачиваю ZIP...")
        zip_path = self.github.download_zip(owner, repo_name, branch)

        if not zip_path or not os.path.exists(zip_path):
            print(f"    ✗ Не удалось скачать ZIP")
            return False

        zip_size = os.path.getsize(zip_path)
        zip_size_str = format_file_size(zip_size)
        print(f"    ✓ {zip_size_str}")

        repo_data = {
            "full_name": full_name,
            "display_name": display,
            "description": repo_ctx.get("description", ""),
            "version": repo_ctx.get("version", "unknown"),
            "version_type": repo_ctx.get("version_type", "unknown"),
            "stars": repo_ctx.get("stars", 0),
            "forks": repo_ctx.get("forks", 0),
            "github_url": f"https://github.com/{full_name}",
        }

        text = self._build_message_text(repo_data, zip_size)

        print(f"    → Отправляю в MAX...")

        try:
            browser.navigate()
            browser.wait_page_ready()
        except Exception:
            pass

        split_mode = get_split_mode(self.config, "archiver", default="auto")
        split_threshold_mb = self.config.get("archiver", {}).get("split_threshold_mb", 49)
        success, _ = browser.send_message_with_files(
            text=text,
            filepaths=[zip_path],
            retries=self.config.get("archiver", {}).get("retries", 3),
            retry_delay=self.config.get("archiver", {}).get("retry_delay", 10),
            split_threshold_mb=split_threshold_mb,
            split_mode=split_mode,
        )

        verified = False
        if success:
            for attempt in range(3):
                time.sleep(3)
                verified = browser.verify_repo_publication(full_name)
                if verified:
                    print(f"    ✓ Верификация пройдена (попытка {attempt + 1})")
                    break
                else:
                    print(f"    ⚠ Верификация: попытка {attempt + 1}/3 — не найдено")

        if success:
            self.journal.update_repository(full_name, {
                "version": repo_ctx.get("version", "unknown"),
                "status": "restored" if self.journal.is_in_journal(full_name) else "sent",
                "archive_size": zip_size,
                "restored_at": datetime.now().isoformat(),
            })
        else:
            self.journal.update_repository(full_name, {
                "status": "failed",
                "archive_size": zip_size,
            })

        if self._safe_remove_file(zip_path):
            pass  # File cleaned up silently
        else:
            print(f"    ⚠ Could not remove file: {os.path.basename(zip_path)}")

        return success and verified

    def _build_restore_context(self, item: dict) -> dict:
        """
        Build repo context from audit item for restoration.
        Tries to get data from journal first, then from GitHub API.
        """
        fn = item.get("full_name", "")

        # Orphaned file (full_name is actually a filename like owner-repo-main.zip)
        if fn and "/" not in fn:
            extracted = self._extract_repo_from_filename(fn)
            if extracted and "/" in extracted:
                fn = extracted
            else:
                return {}

        if not fn or "/" not in fn:
            return {}

        owner, repo_name = fn.split("/", 1)

        # Try journal first
        journal_entry = self.journal.get_repository(fn)
        if journal_entry:
            return {
                "full_name": fn,
                "owner": owner,
                "repo": repo_name,
                "display_name": journal_entry.get("display_name", repo_name),
                "description": journal_entry.get("description", ""),
                "version": journal_entry.get("version", ""),
                "version_type": journal_entry.get("version_type", ""),
                "stars": journal_entry.get("stars", 0),
                "forks": journal_entry.get("forks", 0),
                "default_branch": journal_entry.get("default_branch", "main"),
                "from_journal": True,
            }

        # Fetch from GitHub API
        return self._fetch_repo_from_github(fn, owner, repo_name)

    def _fetch_repo_from_github(self, full_name: str, owner: str, repo: str) -> dict:
        """Fetch repo details from GitHub API."""
        try:
            details = self.github.get_repository_details(owner, repo)
            if details:
                version, version_type = self.github.get_version_info(
                    owner, repo, details.get("default_branch", "main")
                )
                return {
                    "full_name": full_name,
                    "owner": owner,
                    "repo": repo,
                    "display_name": details.get("name", repo),
                    "description": details.get("description", "") or "Без описания",
                    "version": version,
                    "version_type": version_type,
                    "stars": details.get("stargazers_count", 0),
                    "forks": details.get("forks_count", 0),
                    "default_branch": details.get("default_branch", "main"),
                    "from_journal": False,
                }
        except Exception as e:
            self.logger.warning(f"Failed to fetch repo from GitHub: {e}")

        return {
            "full_name": full_name,
            "owner": owner,
            "repo": repo,
            "display_name": repo,
            "description": "Без описания",
            "version": "unknown",
            "version_type": "unknown",
            "stars": 0,
            "forks": 0,
            "default_branch": "main",
            "from_journal": False,
        }

    def _restore_publication(self, browser, item: dict, repo_ctx: dict,
                             skip_delete: bool = False) -> bool:
        """
        Re-publish the repo (download + upload). Optionally skip deletion
        when old messages were already removed in a bulk pass.

        Args:
            browser: BrowserMAX instance (connected)
            item: Audit item dict with message indices to delete
            repo_ctx: Repo context dict with owner, repo, branch, etc.
            skip_delete: If True, skip the deletion step (messages already gone)

        Returns:
            True on success
        """
        fn = repo_ctx.get("full_name", "")
        display = repo_ctx.get("display_name", fn.split("/")[-1]) if fn else "?"
        owner = repo_ctx.get("owner", "")
        repo_name = repo_ctx.get("repo", "")
        branch = repo_ctx.get("default_branch", "main")

        print(f"\n  {'═' * 56}")
        print(f"  Восстановление: {display if display != '?' else item.get('full_name', '?')}")
        print(f"  {'─' * 56}")

        if not fn or not owner or not repo_name:
            print(f"    ⚠ Не удалось определить репозиторий — пропускаю")
            return False

        # Step 1: Delete old messages (skipped if already done in bulk)
        if not skip_delete:
            search_terms = []
            if fn:
                search_terms.append(fn)
            if owner and repo_name:
                search_terms.append(f"{owner}-{repo_name}")

            if search_terms:
                print(f"    → Удаляю старые сообщения...")
                try:
                    browser.navigate()
                    browser.wait_page_ready()
                except Exception:
                    pass
                browser.scroll_to_top()
                deleted = browser.delete_messages_by_content(
                    search_terms, label=display
                )
                if deleted:
                    print(f"    ✓ Удалено: {deleted}")
                else:
                    print(f"    ⚠ Сообщения не найдены (возможно уже удалены)")
                time.sleep(1)

        # Step 2: Download fresh ZIP
        print(f"    ↓ Скачиваю ZIP...")
        zip_path = self.github.download_zip(owner, repo_name, branch)

        if not zip_path or not os.path.exists(zip_path):
            print(f"    ✗ Не удалось скачать ZIP")
            return False

        zip_size = os.path.getsize(zip_path)
        zip_size_str = format_file_size(zip_size)
        print(f"    ✓ {zip_size_str}")

        # Step 3: Build and send message
        repo_data = {
            "full_name": fn,
            "display_name": display,
            "description": repo_ctx.get("description", ""),
            "version": repo_ctx.get("version", "unknown"),
            "version_type": repo_ctx.get("version_type", "unknown"),
            "stars": repo_ctx.get("stars", 0),
            "forks": repo_ctx.get("forks", 0),
            "github_url": f"https://github.com/{fn}",
        }

        text = self._build_message_text(repo_data, zip_size)

        print(f"    → Отправляю в MAX...")

        # Navigate back to channel (page may have shifted after deletion)
        try:
            browser.navigate()
            browser.wait_page_ready()
        except Exception:
            pass

        split_mode = get_split_mode(self.config, "archiver", default="auto")
        split_threshold_mb = self.config.get("archiver", {}).get("split_threshold_mb", 49)
        success, _ = browser.send_message_with_files(
            text=text,
            filepaths=[zip_path],
            retries=self.config.get("archiver", {}).get("retries", 3),
            retry_delay=self.config.get("archiver", {}).get("retry_delay", 10),
            split_threshold_mb=split_threshold_mb,
            split_mode=split_mode,
        )

        # Step 4: Verify (retry up to 3 times with delay)
        verified = False
        if success:
            for attempt in range(3):
                time.sleep(3)
                verified = browser.verify_repo_publication(fn)
                if verified:
                    print(f"    ✓ Верификация пройдена (попытка {attempt + 1})")
                    break
                else:
                    print(f"    ⚠ Верификация: попытка {attempt + 1}/3 — не найдено")

        # Step 5: Update journal
        if success:
            new_status = "restored"
            self.journal.update_repository(fn, {
                "version": repo_ctx.get("version", "unknown"),
                "status": new_status,
                "archive_size": zip_size,
                "restored_at": datetime.now().isoformat(),
            })
        else:
            self.journal.update_repository(fn, {
                "status": "failed",
                "archive_size": zip_size,
            })

        # Cleanup
        if self._safe_remove_file(zip_path):
            pass  # File cleaned up silently
        else:
            print(f"    ⚠ Could not remove file: {os.path.basename(zip_path)}")

        return success and verified

    # ──────────────────────────────────────────────
    # Export All Messages to File
    # ──────────────────────────────────────────────

    def export_messages_to_file(self):
        """Export all messages from the MAX feed to a JSON/CSV file."""
        print("\n" + "═" * 60)
        print("          ЭКСПОРТ СООБЩЕНИЙ ИЗ ЛЕНТЫ")
        print("═" * 60)

        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        print("\n  Собирает все сообщения из ленты MAX со всеми деталями:")
        print("  • текст, отправитель, время, направление")
        print("  • вложения, реакции, флаги ответа/пересылки")
        print()

        # Connect to MAX
        browser = None
        try:
            browser = self._ensure_max_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Ask for output format
        print("\n  Выберите формат:")
        print("  [J] JSON (полные данные, по умолчанию)")
        print("  [C] CSV (для Excel)")
        try:
            fmt_choice = input("  Ваш выбор [J/C]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            if browser:
                browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        fmt = "csv" if fmt_choice == "c" else "json"
        ext = ".csv" if fmt == "csv" else ".json"

        # Ask for output path — default to export/ folder next to the script
        export_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export")
        os.makedirs(export_dir, exist_ok=True)
        default_path = os.path.join(export_dir, f"messages_export{ext}")
        try:
            path_input = input(f"  Путь к файлу [{default_path}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            if browser:
                browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        output_path = path_input if path_input else default_path

        # Ask for scroll passes
        try:
            passes_input = input("  Количество проходов скролла [3]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            if browser:
                browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if passes_input:
            try:
                scroll_passes = int(passes_input)
            except ValueError:
                print("  Неверный ввод, использую значение по умолчанию: 3")
                scroll_passes = 3
        else:
            scroll_passes = 3

        # Ask for max messages limit
        try:
            max_input = input("  Лимит сообщений (0 = без лимита) [0]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            if browser:
                browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if max_input:
            try:
                max_messages = int(max_input)
            except ValueError:
                print("  Неверный ввод, использую значение по умолчанию: 0 (без лимита)")
                max_messages = 0
        else:
            max_messages = 0

        # Ask about HTML inclusion
        try:
            html_input = input("  Включить HTML содержимое? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            if browser:
                browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        include_html = html_input in ("y", "yes", "д", "да")

        print(f"\n  Начинаю экспорт...")
        print("  Это может занять время в зависимости от количества сообщений.\n")

        try:
            count = browser.export_messages_to_file(
                output_path=output_path,
                format=fmt,
                scroll_passes=scroll_passes,
                include_html=include_html,
                max_messages=max_messages,
            )
            if count > 0:
                print(f"\n  ✓ Экспортировано {count} сообщений в {output_path}")
            else:
                print("\n  ⚠ Сообщений не найдено")
        except Exception as e:
            print(f"\n  ✗ Ошибка при экспорте: {e}")

        if browser:
            browser.close()

        input("\n  Нажмите Enter для возврата в меню...")

    # ──────────────────────────────────────────────
    # Delete All Messages
    # ──────────────────────────────────────────────

    def delete_all_messages_in_channel(self):
        """Delete ALL messages in the MAX channel with double user confirmation."""
        print("\n" + "═" * 60)
        print("          УДАЛЕНИЕ ВСЕХ СООБЩЕНИЙ")
        print("═" * 60)

        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        print("\n  ⚠ ВНИМАНИЕ: Это действие удалит ВСЕ сообщения в канале!")
        print("  Это необратимо — восстановить их будет невозможно.")
        print()

        # First confirmation
        try:
            confirm1 = input("  Вы уверены? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if confirm1 not in ('y', 'yes', 'д', 'да'):
            print("\n  Отменено.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Connect to MAX
        browser = None
        try:
            browser = self._ensure_max_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Get current message count to show user
        try:
            msg_count = browser.get_message_count()
            print(f"\n  В канале обнаружено ~{msg_count} сообщений.")
        except Exception:
            msg_count = 0
            print("\n  Не удалось определить количество сообщений.")

        print()
        print("  Это действие необратимо. Все сообщения будут удалены безвозвратно.")
        print()

        # Second confirmation — must type "ДА"
        try:
            confirm2 = input("  Введите 'ДА' (латиницей или кириллицей) для подтверждения: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            if browser:
                browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if confirm2 not in ('да', 'yes', 'дa'):
            print("\n  Отменено.")
            if browser:
                browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Proceed with deletion
        print()
        try:
            deleted = browser.delete_all_messages()
            print(f"\n  ✓ Удалено сообщений: {deleted}")
            print("  ⚠ Страница в браузере может потребовать перезагрузки.")
        except Exception as e:
            print(f"\n  ✗ Ошибка при удалении: {e}")

        if browser:
            browser.close()

        input("\n  Нажмите Enter для возврата в меню...")

    def run_media_archiver(self):
        """Загрузить медиафайлы из папки в MAX канал"""
        from media_archiver import MediaArchiver

        print("\n" + "═" * 60)
        print("  Загрузка медиа из папки")
        print("═" * 60)

        if not self._ensure_channel_ready("media", "Media канал", "media_archiver"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            media = MediaArchiver("config.yaml")
            media.run()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Media archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    # ──────────────────────────────────────────────
    # Channel File Downloader
    # ──────────────────────────────────────────────

    def download_channel_files(self):
        """Скачать все файлы из MAX канала в указанную папку"""
        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        from channel_downloader import ChannelDownloader

        try:
            downloader = ChannelDownloader("config.yaml")
            downloader.run()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Channel download error: {e}", exc_info=True)

        # Note: ChannelDownloader.run() handles its own "Press Enter" prompt

    def _show_auto_prompt(self):
        """Показать приветствие при первом запуске без настройки"""
        print("\n" + "╔" + "═" * 56 + "╗")
        print("║               ДОБРО ПОЖАЛОВАТЬ В GITHUB ARCHIVER             ║")
        print("║" + " " * 58 + "║")
        print("║  Программа не настроена. Для работы необходимо указать:      ║")
        print("║  • GitHub токен для доступа к API                            ║")
        print("║  • URL каналов MAX для разных типов архивов                  ║")
        print("║" + " " * 58 + "║")
        print("║  [Enter] Выполнить начальную настройку                       ║")
        print("║  [S] Пропустить (пункт настройки будет в меню)               ║")
        print("╚" + "═" * 56 + "╝")
        print()

        try:
            choice = input("  Ваш выбор [Enter/S]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return

        if choice != "s":
            self._initial_setup()

    def _initial_setup(self):
        """Интерактивный мастер начальной настройки (6 шагов)"""
        import tempfile
        import shutil
        from config_utils import set_env_value as _set_env

        print("\n" + "═" * 60)
        print("        НАЧАЛЬНАЯ НАСТРОЙКА")
        print("═" * 60)
        print()

        # ── Шаг 1: GitHub токен ──
        current_token = os.environ.get("GITHUB_TOKEN", "")
        if len(current_token) > 8:
            masked = current_token[:4] + "*" * (len(current_token) - 8) + current_token[-4:]
        elif current_token:
            masked = current_token[:4] + "****"
        else:
            masked = "(не указан)"
        print(f"  Шаг 1 из 6: GitHub токен")
        print(f"  Текущее: {masked}")
        try:
            val = input("  Введите токен (Enter = оставить): ").strip()
        except (EOFError, KeyboardInterrupt):
            val = ""
        if val:
            _set_env("GITHUB_TOKEN", val)
        elif not current_token:
            print("  ⚠ Токен не указан. Программа не сможет работать с GitHub API.")

        # ── Шаги 2-5: URL каналов с возможностью пропуска ──
        channel_steps = [
            ("max",   "MAX канал (GitHub архивы)"),
            ("pypi",  "PyPI канал"),
            ("media", "Media канал"),
            ("backup","Backup канал"),
        ]

        # Track skipped channels for this wizard session
        new_skipped = list(get_skipped_channels(self.config))
        total = 6

        for step_num, (ch_name, ch_label) in enumerate(channel_steps, 2):
            env_var = f"CHANNEL_{ch_name.upper()}"
            current_url = os.environ.get(env_var, "")
            if not current_url:
                channels = self.config.get("channels", {}) or {}
                current_url = channels.get(ch_name, "")
            display = current_url if current_url else "(не указан)"
            already_skipped = ch_name in new_skipped

            print(f"\n  Шаг {step_num} из {total}: {ch_label}")
            print(f"  Текущее: {display}")
            if already_skipped:
                print("  (модуль отключён)")
            print()
            print("  [Enter] Ввести URL / Оставить текущий")
            print("  [S] Пропустить — отключить модуль")
            if already_skipped:
                print("  [E] Включить модуль")
            print()

            try:
                choice = input("  Ваш выбор [Enter/S]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = ""

            if choice == "s":
                if ch_name not in new_skipped:
                    new_skipped.append(ch_name)
                print(f"  → Модуль \"{ch_label}\" отключён.")
            elif choice == "e" and already_skipped:
                new_skipped = [c for c in new_skipped if c != ch_name]
                try:
                    url = input(f"  Введите URL {ch_label}: ").strip()
                except (EOFError, KeyboardInterrupt):
                    url = ""
                if url:
                    _set_env(env_var, url)
            elif choice:
                # User entered a URL
                _set_env(env_var, choice)
                # If it was skipped, enable it
                if ch_name in new_skipped:
                    new_skipped = [c for c in new_skipped if c != ch_name]
            # Enter with existing URL = keep as-is
            # If currently skipped and user just pressed Enter, keep skipped

        # ── Шаг 6: Параметры архивации ──
        print(f"\n  Шаг 6 из 6: Параметры архивации")
        archiver_cfg = self.config.get("archiver", {})

        try:
            limit_str = input(f"  Лимит репозиториев [{archiver_cfg.get('limit', 100)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            limit_str = ""
        try:
            retries_str = input(f"  Retries [{archiver_cfg.get('retries', 3)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            retries_str = ""
        try:
            delay_str = input(f"  Задержка между репо (сек) [{archiver_cfg.get('repo_delay', 30)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            delay_str = ""
        try:
            split_str = input(f"  Порог разделения (MB) [{archiver_cfg.get('split_threshold_mb', 49)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            split_str = ""

        # Write step 6 to config.yaml (merge, preserve existing keys)
        yaml_config = {}
        if os.path.exists("config.yaml"):
            with open("config.yaml", "r", encoding="utf-8") as f:
                yaml_config = yaml.safe_load(f) or {}

        yaml_config.setdefault("archiver", {})
        if limit_str:
            try:
                yaml_config["archiver"]["limit"] = int(limit_str)
            except ValueError:
                print("  ⚠ Неверное значение лимита, пропускаю")
        if retries_str:
            try:
                yaml_config["archiver"]["retries"] = int(retries_str)
            except ValueError:
                print("  ⚠ Неверное значение retries, пропускаю")
        if delay_str:
            try:
                yaml_config["archiver"]["repo_delay"] = int(delay_str)
            except ValueError:
                print("  ⚠ Неверное значение задержки, пропускаю")
        if split_str:
            try:
                yaml_config["archiver"]["split_threshold_mb"] = int(split_str)
            except ValueError:
                print("  ⚠ Неверное значение порога, пропускаю")

        # ── Split mode prompt (step 6b) ──
        current_split_mode = archiver_cfg.get('split_mode', 'auto')
        mode_prompt = (
            f"  Режим разделения [{current_split_mode}]\n"
            f"    auto    — автоматически (если > порога)\n"
            f"    on      — дробить всегда\n"
            f"    off     — никогда не дробить\n"
            f"    prompt  — спрашивать для каждого файла\n"
        )
        try:
            mode_str = input(mode_prompt + "  Ваш выбор [Enter=" + current_split_mode + "]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            mode_str = ""

        if mode_str in ("auto", "on", "off", "prompt"):
            yaml_config["archiver"]["split_mode"] = mode_str

        # Write skipped channels to setup section
        yaml_config.setdefault("setup", {})["skipped_channels"] = sorted(new_skipped)

        # Atomic write for config.yaml
        fd, tmp_path = tempfile.mkstemp(suffix=".yaml", dir=".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(yaml_config, f, default_flow_style=False, allow_unicode=True)
            shutil.move(tmp_path, "config.yaml")
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        # ── Reload config to pick up all changes ──
        load_dotenv(override=True)
        from config import init_config, get_config
        init_config("config.yaml")
        self.config = get_config().model_dump()

        print(f"\n  ✓ Настройка завершена!")
        print(f"  Переменные сохранены в .env и config.yaml")
        input("\n  Нажмите Enter для продолжения...")

    def run(self):
        """Запустить главный цикл программы"""

        # Auto-prompt on first launch if setup is incomplete
        if not is_setup_complete(self.config):
            self._show_auto_prompt()

        while True:
            os.system('cls' if os.name == 'nt' else 'clear')

            self._show_main_menu()

            needs_setup = not is_setup_complete(self.config)
            if needs_setup:
                valid_opts = ["0", "x", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a"]
                prompt_text = "Выберите раздел [0/X,1-9,a]"
            else:
                valid_opts = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a"]
                prompt_text = "Выберите раздел [0-9,a]"
            choice = prompt_numeric_choice(prompt_text, valid_opts).lower()

            if not choice:
                break

            # ── Check if selected module is disabled ──
            if choice in ("1", "2", "3", "4", "6", "7", "8", "9", "a") and not self._is_module_enabled(choice):
                module_names = {"1": "GitHub", "2": "PyPI", "3": "Backuper", "4": "Файлы",
                            "6": "Cargo", "7": "NuGet", "8": "RubyGems", "9": "SoftPortal",
                            "a": "Thingiverse"}
                mod_name = module_names.get(choice, "")
                print(f"\n  ⚠ Модуль \"{mod_name}\" отключён в настройках.")
                print()
                print("  [Enter] Включить и настроить")
                print("  [S] Вернуться в меню")
                print()
                try:
                    sub = input("  Ваш выбор [Enter/S]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    sub = ""
                if sub != "s":
                    self._initial_setup()
                continue

            # ── State-aware dispatch ──
            if needs_setup and choice == '0':
                self._initial_setup()
            elif needs_setup and choice == 'x':
                print("\n  До свидания!\n")
                break
            elif not needs_setup and choice == '0':
                print("\n  До свидания!\n")
                break
            elif choice == '1':
                self._run_github_menu()
            elif choice == '2':
                self._run_pypi_menu()
            elif choice == '3':
                self._run_backuper_menu()
            elif choice == '4':
                self._run_files_menu()
            elif choice == '5':
                self._run_service_menu()
            elif choice == '6':
                self._run_cargo_menu()
            elif choice == '7':
                self._run_nuget_menu()
            elif choice == '8':
                self._run_rubygems_menu()
            elif choice == '9':
                self._run_softportal_menu()
            elif choice == 'a':
                self._run_thingiverse()

    def _run_github_menu(self):
        """Цикл подменю GitHub"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._github_menu()
            choice = prompt_numeric_choice("Выберите действие [0-9/a/b/c/d]", ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d"])

            if choice == '0':
                break
            elif choice == '1':
                self.sync_repositories(mode="step")
            elif choice == '2':
                self.sync_repositories(mode="auto")
            elif choice == '3':
                self.sync_repositories(mode="parallel")
            elif choice == '4':
                self.load_new_repositories(mode="step")
            elif choice == '5':
                self.load_new_repositories(mode="auto")
            elif choice == '6':
                self.load_new_repositories(mode="parallel")
            elif choice == '7':
                self.retry_failed_repositories()
            elif choice == '8':
                self._collect_repos()
            elif choice == '9':
                self._show_collector_status()
            elif choice == 'a':
                self.load_runtime()
            elif choice == 'b':
                self.sync_runtimes()
            elif choice == 'c':
                self._manage_ignore_list()
            elif choice == 'd':
                self.audit_and_restore_publications()

    def _collect_repos(self):
        """Собрать базу репозиториев до указанного лимита через оркестратор.

        Использует все доступные стратегии:
          1. GraphQL (cursor pagination, bypass 1000 cap)
          2. REST tiered search (stars + languages)
          3. Crawler (trending)
          4. Topic search

        Работает как с токеном, так и без него (REST/crawler работают без токена).
        """
        print("\n" + "═" * 60)
        print("Сбор базы репозиториев")
        print("═" * 60)

        self._init_github()

        rc_config = self.config.get('repo_collector', {})
        per_page = rc_config.get('per_page', 100)

        collector = RepoCollector(
            github_api=self.github,
            per_page=per_page,
        )

        # Show current status
        db_count = collector.database.get_count()
        print(f"\n  Текущая база: {db_count} репозиториев")

        # Prompt for target count
        default_target = self.config.get('archiver', {}).get('limit', 5000)
        while True:
            try:
                target_input = input(
                    f"\n  Целевой лимит репозиториев (по умолчанию {default_target}): "
                ).strip()
                if not target_input:
                    target_count = default_target
                else:
                    target_count = int(target_input)
                if target_count > 0:
                    break
                print("  Введите положительное число.")
            except ValueError:
                print("  Неверный формат. Введите число.")

        try:
            stats = collector.collect_all_strategies(target_count)
        except Exception as e:
            print(f"\n  ✗ Ошибка сбора: {e}")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        input("\n  Нажмите Enter для возврата в меню...")

    def _show_collector_status(self):
        """Показать статус коллектора"""
        from repo_collector import RepoDatabase, RepoCollectorState

        self._init_github()

        collector = RepoCollector(github_api=self.github)
        collector.show_status()

        input("\n  Нажмите Enter для возврата в меню...")

    def _run_pypi_menu(self):
        """Цикл подменю PyPI"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._pypi_menu()
            choice = prompt_numeric_choice("Выберите действие [0-4]", ["0", "1", "2", "3", "4"])

            if choice == '0':
                break
            elif choice == '1':
                self.run_pypi_libs_archiver()
            elif choice == '2':
                self.run_pypi_libs_sync()
            elif choice == '3':
                self.run_pypi_libs_load_runtime()
            elif choice == '4':
                self.run_pypi_libs_sync_runtime()

    def _backuper_menu(self):
        """Подменю Backuper"""
        print("\n" + "═" * 60)
        print("  Backuper — резервное хранение в MAX")
        print("─" * 60)
        print()
        print("  [1] Бэкап — архивировать папку в канал")
        print("  [2] Восстановление — скачать архивы из канала")
        print("  [0] Назад")
        print()

    def _run_backuper_menu(self):
        """Цикл подменю Backuper"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._backuper_menu()
            choice = prompt_numeric_choice("Выберите действие [0-2]", ["0", "1", "2"])

            if choice == '0':
                break
            elif choice == '1':
                self.run_backuper_backup()
            elif choice == '2':
                self.run_backuper_restore()

    def _run_files_menu(self):
        """Цикл подменю Файлы"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._files_menu()
            choice = prompt_numeric_choice("Выберите действие [0-4]", ["0", "1", "2", "3", "4"])

            if choice == '0':
                break
            elif choice == '1':
                self.run_media_archiver()
            elif choice == '2':
                self.download_channel_files()
            elif choice == '3':
                self.export_messages_to_file()
            elif choice == '4':
                self.delete_all_messages_in_channel()

    def _run_service_menu(self):
        """Цикл подменю Сервис"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._service_menu()
            setup_done = is_setup_complete(self.config)
            if setup_done:
                valid_opts = ["0", "1", "2", "3", "4", "5"]
                prompt_text = "Выберите действие [0-5]"
            else:
                valid_opts = ["0", "1", "3", "4", "5"]
                prompt_text = "Выберите действие [0-5]"
            choice = prompt_numeric_choice(prompt_text, valid_opts)

            if choice == '0':
                break
            elif choice == '1':
                self._manage_journals()
            elif choice == '2' and setup_done:
                self._initial_setup()
            elif choice == '3':
                channel_registry_menu()
            elif choice == '4':
                self._run_verifier()
            elif choice == '5':
                self._run_batch_mode()


    def _run_verifier(self):
        """Верификация журналов — сравнение с каналом"""
        from verifier import JournalChannelVerifier, VerifierMode
        from verifier.adapters_github import (
            GitHubChannelAdapter, GitHubJournalAdapter
        )
        from verifier.adapters_pypi import (
            PyPIChannelAdapter, PyPIJournalAdapter
        )
        from verifier.adapters_backuper import (
            BackuperChannelAdapter, BackuperJournalAdapter
        )
        from verifier.adapters_media import (
            MediaChannelAdapter, MediaJournalAdapter
        )

        print("\n" + "═" * 60)
        print("  Верификация журналов")
        print("─" * 60)
        print()
        print("  Выберите журнал для проверки:")
        print()

        from journal import Journal
        from pypi_libs_journal import PyPILibsJournal
        from backuper_journal import BackuperJournal
        from media_archiver import MediaJournal

        gh_journal = Journal("journal.json")
        pypi_journal = PyPILibsJournal("pypi_libs_journal.json")
        bp_journal = BackuperJournal("backuper_journal.json")
        md_journal = MediaJournal("media_journal.json")

        options = []
        if gh_journal.get_count() > 0:
            options.append(("1", "GitHub", gh_journal))
        if pypi_journal.get_count() > 0:
            options.append(("2", "PyPI", pypi_journal))
        if bp_journal.get_count() > 0:
            options.append(("3", "Backuper", bp_journal))
        if md_journal.get_count() > 0:
            options.append(("4", "Media", md_journal))

        if not options:
            print("  ⚠ Все журналы пусты. Верификация не требуется.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        for num, name, journal in options:
            stats = journal.get_stats()
            total = stats.get("total", stats.get("total_backups", 0))
            print(f"  [{num}] {name} — {total} записей")
        print()

        choice = input("  Выберите журнал [1-4]: ").strip()
        selected = None
        for num, name, journal in options:
            if choice == num:
                selected = (name, journal)
                break

        if not selected:
            print("  Неверный выбор.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        pub_name, journal = selected
        print(f"\n  Журнал: {pub_name}")

        # Select mode
        print("\n  Режим проверки:")
        print("  [Q] Quick   — быстрый (DOM-сканирование, ~30-60 сек)")
        print("  [T] Thorough — полный (3 источника, может занять время)")
        mode_choice = input("  Режим [Q/T]: ").strip().lower()
        mode = VerifierMode.THOROUGH if mode_choice == "t" else VerifierMode.QUICK

        # Get channel URL
        from config_utils import get_channel_url_for_channel_key
        channel_map = {
            "GitHub": "max",
            "PyPI": "pypi",
            "Backuper": "backup",
            "Media": "media",
        }
        channel_key = channel_map.get(pub_name, "max")
        channel_url = get_channel_url_for_channel_key(channel_key)

        if not channel_url:
            print(f"\n  ⚠ URL канала для {pub_name} не настроен.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Init browser
        print(f"\n  Подключение к браузеру...")
        try:
            browser = self.max_browser
            if not browser or not browser.page:
                from browser_init import BrowserInitMixin
                mixin = BrowserInitMixin()
                browser = mixin.init_browser(channel_url, self.config)
            else:
                browser.navigate(channel_url)
                browser.wait_page_ready()
        except Exception as e:
            print(f"\n  ✗ Ошибка подключения: {e}")
            print("  Убедитесь, что Chrome запущен с --remote-debugging-port=9222")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Create adapters
        adapter_map = {
            "GitHub": (GitHubChannelAdapter, GitHubJournalAdapter),
            "PyPI": (PyPIChannelAdapter, PyPIJournalAdapter),
            "Backuper": (BackuperChannelAdapter, BackuperJournalAdapter),
            "Media": (MediaChannelAdapter, MediaJournalAdapter),
        }
        CA, JA = adapter_map[pub_name]
        channel_adapter = CA(browser, channel_url)
        journal_adapter = JA(journal)

        # Run verification
        verifier = JournalChannelVerifier(
            channel_adapter, journal_adapter, pub_name
        )
        print(f"\n  Запуск проверки ({mode.value})...")
        print("  Пожалуйста, не трогайте браузер\n")

        diff = verifier.verify(mode)

        # Show report
        report = verifier.report(diff)
        print(report)

        # Offer fix
        if diff.has_issues:
            print("\n  Найдены расхождения. Исправить журнал?")
            print("  [R] Удалить — удалить записи, отсутствующие в канале")
            print("  [B] Двусторонняя синхронизация — удалить, добавить орфаны,")
            print("      обновить версии")
            print("  [N] Нет — только просмотр")
            fix_choice = input("  Ваш выбор [R/B/N]: ").strip().lower()
            if fix_choice == "r":
                removed = verifier.fix_journal(diff)
                print(f"\n  ✓ Удалено {removed} записей из журнала")
            elif fix_choice == "b":
                result = verifier.fix_journal_bidirectional(diff)
                print(f"\n  ✓ Двусторонняя синхронизация:")
                print(f"    Удалено: {result['removed']}")
                print(f"    Добавлено: {result['added']}")
                print(f"    Обновлено: {result['updated']}")
            else:
                print("\n  Журнал не изменён.")

        input("\n  Нажмите Enter для возврата в меню...")

    def run_pypi_libs_archiver(self):
        """Загрузить топ Python библиотек в MAX канал"""
        from pypi_libs_archiver import PyPILibsArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ Python библиотек")
        print("═" * 60)

        if not self._ensure_channel_ready("pypi", "PyPI канал", "pypi_libs"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = PyPILibsArchiver("config.yaml")
            archiver.load_top_libraries()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"PyPI libs archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_pypi_libs_sync(self):
        """Синхронизировать версии Python библиотек"""
        from pypi_libs_archiver import PyPILibsArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация Python библиотек")
        print("═" * 60)

        if not self._ensure_channel_ready("pypi", "PyPI канал", "pypi_libs"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = PyPILibsArchiver("config.yaml")
            archiver.sync_libraries()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"PyPI libs sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_pypi_libs_load_runtime(self):
        """Загрузить Python runtime (первичная загрузка)"""
        from pypi_libs_archiver import PyPILibsArchiver

        print("\n" + "═" * 60)
        print("  Загрузка Python runtime")
        print("═" * 60)

        if not self._ensure_channel_ready("pypi", "PyPI канал", "pypi_libs"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = PyPILibsArchiver("config.yaml")
            archiver.load_runtime()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"PyPI runtime load error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_pypi_libs_sync_runtime(self):
        """Синхронизировать Python runtime"""
        from pypi_libs_archiver import PyPILibsArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация Python runtime")
        print("═" * 60)

        if not self._ensure_channel_ready("pypi", "PyPI канал", "pypi_libs"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = PyPILibsArchiver("config.yaml")
            archiver.sync_runtimes()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"PyPI runtime sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_backuper_backup(self):
        """Запустить бэкап папки в канал"""
        from backuper import Backuper

        print("\n" + "═" * 60)
        print("  Бэкап — архивация папки в канал MAX")
        print("═" * 60)

        if not self._ensure_channel_ready("backup", "Backup канал", "backup"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            backuper = Backuper("config.yaml")
            backuper.run_backup()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Backuper backup error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_backuper_restore(self):
        """Запустить восстановление архивов из канала"""
        from backuper import Backuper

        print("\n" + "═" * 60)
        print("  Восстановление — скачивание архивов из канала MAX")
        print("═" * 60)

        if not self._ensure_channel_ready("backup", "Backup канал", "backup"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            backuper = Backuper("config.yaml")
            backuper.run_restore()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Backuper restore error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    # ──────────────────────────────────────────────
    # Runner methods for new archivers
    # ──────────────────────────────────────────────

    def _run_cargo_menu(self):
        """Цикл подменю Cargo (Rust)"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._cargo_menu()
            choice = prompt_numeric_choice("Выберите действие [0-4]", ["0", "1", "2", "3", "4"])

            if choice == '0':
                break
            elif choice == '1':
                self.run_cargo_archiver()
            elif choice == '2':
                self.run_cargo_sync()
            elif choice == '3':
                self.run_cargo_load_runtime()
            elif choice == '4':
                self.run_cargo_sync_runtime()

    def _run_nuget_menu(self):
        """Цикл подменю NuGet (.NET)"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._nuget_menu()
            choice = prompt_numeric_choice("Выберите действие [0-4]", ["0", "1", "2", "3", "4"])

            if choice == '0':
                break
            elif choice == '1':
                self.run_nuget_archiver()
            elif choice == '2':
                self.run_nuget_sync()
            elif choice == '3':
                self.run_nuget_load_runtime()
            elif choice == '4':
                self.run_nuget_sync_runtime()

    def _run_rubygems_menu(self):
        """Цикл подменю RubyGems"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._rubygems_menu()
            choice = prompt_numeric_choice("Выберите действие [0-4]", ["0", "1", "2", "3", "4"])

            if choice == '0':
                break
            elif choice == '1':
                self.run_rubygems_archiver()
            elif choice == '2':
                self.run_rubygems_sync()
            elif choice == '3':
                self.run_rubygems_load_runtime()
            elif choice == '4':
                self.run_rubygems_sync_runtime()

    def _run_softportal_menu(self):
        """Цикл подменю SoftPortal"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._softportal_menu()
            choice = prompt_numeric_choice("Выберите действие [0-3]", ["0", "1", "2", "3"])

            if choice == '0':
                break
            elif choice == '1':
                self.run_softportal_archiver()
            elif choice == '2':
                self.run_softportal_sync()
            elif choice == '3':
                self.run_softportal_categories()

    def _run_thingiverse(self):
        """Цикл подменю Thingiverse"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._thingiverse_menu()
            choice = prompt_numeric_choice("Выберите действие [0-4]", ["0", "1", "2", "3", "4"])

            if choice == '0':
                break
            elif choice == '1':
                self._run_thingiverse_popular()
            elif choice == '2':
                self._run_thingiverse_by_tag()
            elif choice == '3':
                self._run_thingiverse_by_category()
            elif choice == '4':
                self._run_thingiverse_by_author()

    # ── Batch Mode ────────────────────────────────────────────────

    def _batch_menu(self):
        """Подменю Batch Mode"""
        print("\n" + "═" * 60)
        print("  Batch Mode — параллельный запуск архиверов")
        print("─" * 60)
        print()
        print("  Выберите архиверы для запуска (множественный выбор):")
        print()
        print("  [1] GitHub — загрузка репозиториев")
        print("  [2] GitHub — синхронизация репозиториев")
        print("  [3] PyPI — загрузка библиотек")
        print("  [4] PyPI — синхронизация библиотек")
        print("  [5] Cargo — загрузка пакетов")
        print("  [6] Cargo — синхронизация пакетов")
        print("  [7] NuGet — загрузка пакетов")
        print("  [8] NuGet — синхронизация пакетов")
        print("  [9] RubyGems — загрузка пакетов")
        print("  [0] RubyGems — синхронизация пакетов")
        print("  [SP] SoftPortal — загрузка программ")
        print("  [SS] SoftPortal — синхронизация программ")
        print()
        print("  Runtime загрузка (первичная):")
        print("  [GL] GitHub — загрузка Git runtime")
        print("  [PL] PyPI — загрузка Python runtime")
        print("  [RL] Cargo — загрузка Rust runtime")
        print("  [NL] NuGet — загрузка .NET runtime")
        print("  [BL] RubyGems — загрузка Ruby runtime")
        print()
        print("  Runtime синхронизация:")
        print("  [GS] GitHub — синхронизация Git runtime")
        print("  [PS] PyPI — синхронизация Python runtime")
        print("  [RS] Cargo — синхронизация Rust runtime")
        print("  [NS] NuGet — синхронизация .NET runtime")
        print("  [BS] RubyGems — синхронизация Ruby runtime")
        print("  [V]  Все runtime (синхронизация)")
        print()
        print("  Введите номера через пробел, например: 1 3 5")
        print("  [A] Все архиверы (загрузка)")
        print("  [S] Все архиверы (синхронизация)")
        print("  [Q] Отмена")
        print()

    def _run_batch_mode(self):
        """Запустить batch mode — параллельный запуск архиверов."""
        from batch_runner import BatchRunner, BatchTask

        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._batch_menu()

            try:
                choice = input("  Ваш выбор: ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                print("\n  Отмена.")
                break

            if not choice:
                continue
            if choice == 'Q':
                break

            # Build task list
            tasks: list[BatchTask] = []

            # Map of choice -> (module, cls, method, label)
            task_map = {
                "1": ("github_archiver", "GitHubArchiver", "load_repositories", "GitHub load"),
                "2": ("github_archiver", "GitHubArchiver", "sync_repositories", "GitHub sync"),
                "3": ("pypi_libs_archiver", "PyPILibsArchiver", "load_top_libraries", "PyPI load"),
                "4": ("pypi_libs_archiver", "PyPILibsArchiver", "sync_libraries", "PyPI sync"),
                "5": ("cargo_archiver", "CargoArchiver", "load_top_packages", "Cargo load"),
                "6": ("cargo_archiver", "CargoArchiver", "sync_packages", "Cargo sync"),
                "7": ("nuget_archiver", "NuGetArchiver", "load_top_packages", "NuGet load"),
                "8": ("nuget_archiver", "NuGetArchiver", "sync_packages", "NuGet sync"),
                "9": ("rubygems_archiver", "RubyGemsArchiver", "load_top_packages", "RubyGems load"),
                "0": ("rubygems_archiver", "RubyGemsArchiver", "sync_packages", "RubyGems sync"),
                # Runtime load tasks (primary)
                "GL": ("github_archiver", "GitHubArchiver", "load_runtime", "GitHub runtime load"),
                "PL": ("pypi_libs_archiver", "PyPILibsArchiver", "load_runtime", "PyPI runtime load"),
                "RL": ("cargo_archiver", "CargoArchiver", "load_runtime", "Cargo runtime load"),
                "NL": ("nuget_archiver", "NuGetArchiver", "load_runtime", "NuGet runtime load"),
                "BL": ("rubygems_archiver", "RubyGemsArchiver", "load_runtime", "RubyGems runtime load"),
                # Runtime sync tasks
                "GS": ("github_archiver", "GitHubArchiver", "sync_runtimes", "GitHub runtime sync"),
                "PS": ("pypi_libs_archiver", "PyPILibsArchiver", "sync_runtimes", "PyPI runtime sync"),
                "RS": ("cargo_archiver", "CargoArchiver", "sync_runtimes", "Cargo runtime sync"),
                "NS": ("nuget_archiver", "NuGetArchiver", "sync_runtimes", "NuGet runtime sync"),
                "BS": ("rubygems_archiver", "RubyGemsArchiver", "sync_runtimes", "RubyGems runtime sync"),
                # SoftPortal tasks
                "SP": ("softportal_archiver", "SoftPortalArchiver", "load_top_programs", "SoftPortal load"),
                "SS": ("softportal_archiver", "SoftPortalArchiver", "sync_programs", "SoftPortal sync"),
            }

            if choice == 'A':
                # All load tasks
                for num in ("1", "3", "5", "7", "9"):
                    mod, cls, method, label = task_map[num]
                    tasks.append(BatchTask(module=mod, cls=cls, method=method, label=label))
            elif choice == 'S':
                # All sync tasks
                for num in ("2", "4", "6", "8", "0"):
                    mod, cls, method, label = task_map[num]
                    tasks.append(BatchTask(module=mod, cls=cls, method=method, label=label))
            elif choice == 'V':
                # All runtime sync tasks
                for letter in ("GS", "PS", "RS", "NS", "BS"):
                    mod, cls, method, label = task_map[letter]
                    tasks.append(BatchTask(module=mod, cls=cls, method=method, label=label))
            else:
                # Parse individual choices
                parts = choice.split()
                for part in parts:
                    part = part.strip()
                    if part in task_map:
                        mod, cls, method, label = task_map[part]
                        tasks.append(BatchTask(module=mod, cls=cls, method=method, label=label))

            if not tasks:
                print("\n  ⚠ Нет выбранных задач. Попробуйте снова.")
                input("\n  Нажмите Enter...")
                continue

            # Get batch config
            batch_cfg = self.config.get("batch", {})
            max_concurrent = batch_cfg.get("max_concurrent", 1)
            timeout = batch_cfg.get("timeout_seconds", 7200)

            # Run
            runner = BatchRunner(tasks, max_concurrent=max_concurrent, timeout_seconds=timeout)
            try:
                runner.run()
            except KeyboardInterrupt:
                print("\n  Batch прерван пользователем.")
            except Exception as e:
                print(f"\n  ✗ Ошибка batch: {e}")
                self.logger.error(f"Batch mode error: {e}", exc_info=True)

            input("\n  Нажмите Enter для возврата в меню...")
            break

    def run_cargo_archiver(self):
        """Загрузить топ Rust пакеты в MAX канал"""
        from cargo_archiver import CargoArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ Rust пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("cargo", "Cargo канал", "cargo"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = CargoArchiver("config.yaml")
            archiver.load_top_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Cargo archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_cargo_sync(self):
        """Синхронизировать версии Rust пакетов"""
        from cargo_archiver import CargoArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация Rust пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("cargo", "Cargo канал", "cargo"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = CargoArchiver("config.yaml")
            archiver.sync_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Cargo sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_cargo_load_runtime(self):
        """Загрузить Rust runtime (первичная загрузка)"""
        from cargo_archiver import CargoArchiver

        print("\n" + "═" * 60)
        print("  Загрузка Rust runtime")
        print("═" * 60)

        if not self._ensure_channel_ready("cargo", "Cargo канал", "cargo"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = CargoArchiver("config.yaml")
            archiver.load_runtime()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Cargo runtime load error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_cargo_sync_runtime(self):
        """Синхронизировать Rust runtime"""
        from cargo_archiver import CargoArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация Rust runtime")
        print("═" * 60)

        if not self._ensure_channel_ready("cargo", "Cargo канал", "cargo"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = CargoArchiver("config.yaml")
            archiver.sync_runtimes()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Cargo runtime sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_nuget_archiver(self):
        """Загрузить топ .NET пакеты в MAX канал"""
        from nuget_archiver import NuGetArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ .NET пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("nuget", "NuGet канал", "nuget"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = NuGetArchiver("config.yaml")
            archiver.load_top_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"NuGet archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_nuget_sync(self):
        """Синхронизировать версии .NET пакетов"""
        from nuget_archiver import NuGetArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация .NET пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("nuget", "NuGet канал", "nuget"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = NuGetArchiver("config.yaml")
            archiver.sync_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"NuGet sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_nuget_load_runtime(self):
        """Загрузить .NET runtime (первичная загрузка)"""
        from nuget_archiver import NuGetArchiver

        print("\n" + "═" * 60)
        print("  Загрузка .NET runtime")
        print("═" * 60)

        if not self._ensure_channel_ready("nuget", "NuGet канал", "nuget"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = NuGetArchiver("config.yaml")
            archiver.load_runtime()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"NuGet runtime load error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_nuget_sync_runtime(self):
        """Синхронизировать .NET runtime"""
        from nuget_archiver import NuGetArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация .NET runtime")
        print("═" * 60)

        if not self._ensure_channel_ready("nuget", "NuGet канал", "nuget"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = NuGetArchiver("config.yaml")
            archiver.sync_runtimes()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"NuGet runtime sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_rubygems_archiver(self):
        """Загрузить топ Ruby пакеты в MAX канал"""
        from rubygems_archiver import RubyGemsArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ Ruby пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("rubygems", "RubyGems канал", "rubygems"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = RubyGemsArchiver("config.yaml")
            archiver.load_top_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"RubyGems archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_rubygems_sync(self):
        """Синхронизировать версии Ruby пакетов"""
        from rubygems_archiver import RubyGemsArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация Ruby пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("rubygems", "RubyGems канал", "rubygems"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = RubyGemsArchiver("config.yaml")
            archiver.sync_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"RubyGems sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_rubygems_load_runtime(self):
        """Загрузить Ruby runtime (первичная загрузка)"""
        from rubygems_archiver import RubyGemsArchiver

        print("\n" + "═" * 60)
        print("  Загрузка Ruby runtime")
        print("═" * 60)

        if not self._ensure_channel_ready("rubygems", "RubyGems канал", "rubygems"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = RubyGemsArchiver("config.yaml")
            archiver.load_runtime()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"RubyGems runtime load error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_rubygems_sync_runtime(self):
        """Синхронизировать Ruby runtime"""
        from rubygems_archiver import RubyGemsArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация Ruby runtime")
        print("═" * 60)

        if not self._ensure_channel_ready("rubygems", "RubyGems канал", "rubygems"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = RubyGemsArchiver("config.yaml")
            archiver.sync_runtimes()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"RubyGems runtime sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_softportal_archiver(self):
        """Загрузить топ программы в MAX канал"""
        from softportal_archiver import SoftPortalArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ программ")
        print("═" * 60)

        if not self._ensure_channel_ready("softportal", "SoftPortal канал", "softportal"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = SoftPortalArchiver("config.yaml")
            archiver.load_top_programs()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"SoftPortal archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_softportal_sync(self):
        """Синхронизировать версии программ"""
        from softportal_archiver import SoftPortalArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация программ")
        print("═" * 60)

        if not self._ensure_channel_ready("softportal", "SoftPortal канал", "softportal"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = SoftPortalArchiver("config.yaml")
            archiver.sync_programs()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"SoftPortal sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_softportal_categories(self):
        """Обновить список категорий SoftPortal"""
        from softportal_archiver import SoftPortalArchiver

        print("\n" + "═" * 60)
        print("  Обновление списка категорий")
        print("═" * 60)

        try:
            archiver = SoftPortalArchiver("config.yaml")
            archiver.ensure_categories_configured()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"SoftPortal categories error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def _run_thingiverse_popular(self):
        """Загрузить популярные 3D модели"""
        from thingiverse_archiver import ThingiverseArchiver

        print("\n" + "═" * 60)
        print("  Загрузка популярных 3D моделей")
        print("═" * 60)

        if not self._ensure_channel_ready("thingiverse", "Thingiverse канал", "thingiverse"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = ThingiverseArchiver("config.yaml")
            archiver.run_popular()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Thingiverse popular error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def _run_thingiverse_by_tag(self):
        """Загрузить 3D модели по тегу"""
        from thingiverse_archiver import ThingiverseArchiver

        print("\n" + "═" * 60)
        print("  Загрузка 3D моделей по тегу")
        print("═" * 60)

        if not self._ensure_channel_ready("thingiverse", "Thingiverse канал", "thingiverse"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            tag = input("  Введите тег: ").strip()
            if not tag:
                print("  ⚠ Тег не указан.")
                input("\n  Нажмите Enter для возврата в меню...")
                return
            archiver = ThingiverseArchiver("config.yaml")
            archiver.run_by_tag(tag)
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Thingiverse by tag error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def _run_thingiverse_by_category(self):
        """Загрузить 3D модели по категории"""
        from thingiverse_archiver import ThingiverseArchiver

        print("\n" + "═" * 60)
        print("  Загрузка 3D моделей по категории")
        print("═" * 60)

        if not self._ensure_channel_ready("thingiverse", "Thingiverse канал", "thingiverse"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            category = input("  Введите категорию: ").strip()
            if not category:
                print("  ⚠ Категория не указана.")
                input("\n  Нажмите Enter для возврата в меню...")
                return
            archiver = ThingiverseArchiver("config.yaml")
            archiver.run_by_category(category)
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Thingiverse by category error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def _run_thingiverse_by_author(self):
        """Загрузить 3D модели по автору"""
        from thingiverse_archiver import ThingiverseArchiver

        print("\n" + "═" * 60)
        print("  Загрузка 3D моделей по автору")
        print("═" * 60)

        if not self._ensure_channel_ready("thingiverse", "Thingiverse канал", "thingiverse"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            author = input("  Введите имя автора: ").strip()
            if not author:
                print("  ⚠ Имя автора не указано.")
                input("\n  Нажмите Enter для возврата в меню...")
                return
            archiver = ThingiverseArchiver("config.yaml")
            archiver.run_by_author(author)
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Thingiverse by author error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")


# ─────────────────────────────────────────────────
# Lightweight generic journal for new archivers
# (full journal classes created in executor phase)
# ─────────────────────────────────────────────────

class _GenericJournal:
    """Minimal journal wrapper for Cargo, NuGet, RubyGems management menu."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {"packages": []}
        return {"packages": []}

    def get_stats(self) -> dict:
        entries = self.data.get("packages", [])
        sent = len([e for e in entries if e.get("status") == "sent"])
        failed = len([e for e in entries if e.get("status") == "failed"])
        return {
            "total": len(entries),
            "sent": sent,
            "failed": failed,
        }

    def clear(self):
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump({"packages": []}, f, ensure_ascii=False, indent=2)
        try:
            lock_path = f"{self.file_path}.lock"
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except OSError:
            pass


def main():
    """Точка входа"""
    load_dotenv()

    # Session capture — перехватывает весь print() в timestamped файл
    session = SessionCapture()
    session.start()
    print(f"📋 Session log: {session.path}")

    logger = setup_logging(log_file="archiver.log", level=10)
    config_path = "config.yaml"

    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    # Initialize config early for health checks
    from config import init_config
    init_config(config_path)

    # Health checks
    from health_check import run_health_checks
    checks_ok = run_health_checks()
    if not checks_ok:
        print("\n  Критические проверки не пройдены.")
        print("  Исправьте проблемы выше или запустите с --skip-health-check")
        sys.exit(1)

    archiver = None
    shutdown = None

    def signal_handler(signum, frame):
        logger.info("Received interrupt signal, shutting down gracefully...")
        if shutdown:
            shutdown.cleanup()
        session.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        archiver = GitHubArchiver(config_path)
        shutdown = GracefulShutdown(archiver)

        with shutdown:
            archiver.run()
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if shutdown:
            shutdown.cleanup()
        session.stop()


if __name__ == "__main__":
    main()