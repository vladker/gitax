#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Channel Downloader — Скачивание всех файлов из MAX канала в локальную папку

Самостоятельный скрипт для скачивания файлов через BrowserMAX.
Следует тому же паттерну, что media_archiver.py.
"""

import os
import sys
import json
import time

import atexit
import signal
import tempfile
import shutil
import requests
from datetime import datetime
from pathlib import Path

from logging_config import setup_logging, LogMixin, SessionCapture

from browser_max import BrowserMAX



class DownloadJournal:
    """Журнал скачанных файлов — отслеживает какие файлы уже скачаны

    Структура JSON:
    {
        "files": {
            "filename.zip": {
                "filename": "filename.zip",
                "size_bytes": 1234567,
                "downloaded_at": "2026-06-09T12:00:00",
                "output_path": "./downloads/filename.zip",
                "status": "downloaded"
            }
        },
        "stats": {
            "total": 50,
            "downloaded": 45,
            "failed": 3,
            "skipped": 2
        }
    }
    """

    def __init__(self, file_path: str = "download_journal.json"):
        self.file_path = file_path
        self._lock_file = f"{file_path}.lock"
        self.data = self._load()

    def _acquire_lock(self) -> bool:
        """Acquire exclusive lock for safe writes (5 min stale timeout)"""
        try:
            if os.path.exists(self._lock_file):
                lock_age = time.time() - os.path.getmtime(self._lock_file)
                if lock_age > 300:
                    self._release_lock()
                else:
                    return False
            Path(self._lock_file).touch()
            return True
        except Exception:
            return False

    def _release_lock(self):
        """Release lock file"""
        try:
            if os.path.exists(self._lock_file):
                os.remove(self._lock_file)
        except Exception:
            pass

    def _load(self) -> dict:
        """Загрузить журнал из файла"""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                backup_path = f"{self.file_path}.backup"
                if os.path.exists(self.file_path):
                    os.rename(self.file_path, backup_path)
                return self._create_empty()
        return self._create_empty()

    def _create_empty(self) -> dict:
        """Создать пустой журнал"""
        return {
            "files": {},
            "stats": {
                "total": 0,
                "downloaded": 0,
                "failed": 0,
                "skipped": 0
            }
        }

    def clear(self):
        """Очистить журнал — сбросить все данные"""
        self.data = self._create_empty()
        self.save()

    def save(self):
        """Сохранить журнал в файл (атомарная запись)"""
        if not self._acquire_lock():
            return
        try:
            self._update_stats()
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.json',
                dir=os.path.dirname(self.file_path) or '.'
            )
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.file_path):
                    backup_path = f"{self.file_path}.bak"
                    shutil.copy2(self.file_path, backup_path)
                os.replace(temp_path, self.file_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        finally:
            self._release_lock()

    def _update_stats(self):
        """Обновить статистику на основе текущих данных"""
        files = self.data.get("files", {})
        stats = self.data.setdefault("stats", {})
        downloaded = sum(1 for f in files.values() if f.get("status") == "downloaded")
        failed = sum(1 for f in files.values() if f.get("status") == "failed")
        stats["total"] = len(files)
        stats["downloaded"] = downloaded
        stats["failed"] = failed

    def is_downloaded(self, filename: str, size_bytes: int) -> bool:
        """Проверить, скачан ли файл (по имени + размеру)"""
        entry = self.data.get("files", {}).get(filename)
        if entry and entry.get("size_bytes") == size_bytes and entry.get("status") == "downloaded":
            return True
        return False

    def mark_downloaded(self, filename: str, size_bytes: int, output_path: str):
        """Отметить файл как скачанный"""
        self.data.setdefault("files", {})[filename] = {
            "filename": filename,
            "size_bytes": size_bytes,
            "downloaded_at": datetime.now().isoformat(),
            "output_path": output_path,
            "status": "downloaded"
        }
        self.save()

    def mark_failed(self, filename: str, size_bytes: int, error: str = ""):
        """Отметить файл как ошибочный"""
        self.data.setdefault("files", {})[filename] = {
            "filename": filename,
            "size_bytes": size_bytes,
            "downloaded_at": datetime.now().isoformat(),
            "error": error,
            "status": "failed"
        }
        self.save()

    def get_stats(self) -> dict:
        """Получить статистику журнала"""
        files = self.data.get("files", {})
        downloaded = sum(1 for f in files.values() if f.get("status") == "downloaded")
        failed = sum(1 for f in files.values() if f.get("status") == "failed")
        return {
            "total": len(files),
            "downloaded": downloaded,
            "failed": failed,
            "skipped": self.data.get("stats", {}).get("skipped", 0)
        }


class ChannelDownloader(LogMixin):
    """Скачивание всех файлов из MAX канала в локальную папку

    Оркестрирует процесс:
    1. Подключение к браузеру MAX через CDP
    2. Сканирование канала — сбор метаданных файлов
    3. Показ списка пользователю
    4. Скачивание через requests + cookies из браузера
    5. Ведение журнала скачанных файлов (resume support)
    """

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        self.journal = DownloadJournal("download_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        # Register cleanup handlers (same pattern as media_archiver.py)
        atexit.register(self._cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self._shutdown = True

    def _cleanup(self):
        """Clean up resources on exit"""
        if self.browser:
            try:
                self.journal.save()
                self.browser.close()
            except Exception:
                pass

    def _init_browser(self) -> BrowserMAX:
        """Инициализировать браузер MAX (реюз подключения)"""
        if self.browser is None:
            channel_url = self.config.get('channels', {}).get('max', '')
            use_local = self.config.get('archiver', {}).get('use_local_browser', False)
            self.browser = BrowserMAX(channel_url, use_local_browser=use_local)
        return self.browser

    def _ensure_browser_connected(self):
        """Подключиться к MAX и перейти в канал"""
        browser = self._init_browser()
        if not browser.keep_alive_connect():
            raise Exception("Failed to connect to MAX")
        browser.navigate()
        browser.ensure_page_ready()
        return browser

    def _format_file_size(self, size_bytes: int) -> str:
        """Форматировать размер файла (человекочитаемый)"""
        if size_bytes >= 1073741824:
            return f"{size_bytes / 1073741824:.1f} GB"
        elif size_bytes >= 1048576:
            return f"{size_bytes / 1048576:.1f} MB"
        elif size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} B"

    def _get_output_dir(self) -> str:
        """Спросить у пользователя папку для скачивания (дефолт из config)"""
        default = self.config.get('channel_downloader', {}).get('output_dir', './downloads')
        try:
            user_dir = input(f"  Папка для скачивания [{default}]: ").strip()
            return user_dir if user_dir else default
        except (EOFError, KeyboardInterrupt):
            return default

    def _download_with_requests(self, browser: BrowserMAX, url: str, output_path: str):
        """
        Скачать файл через requests + cookies из браузера.

        Args:
            browser: BrowserMAX instance (uses page.context.cookies())
            url: Download URL
            output_path: Where to save the file

        Raises:
            ConnectionError: On network errors
            IOError: On content-length mismatch or write errors
            requests.HTTPError: On HTTP errors (4xx, 5xx)
        """
        # Extract cookies from browser context
        cookies = browser.page.context.cookies()
        jar = requests.cookies.RequestsCookieJar()
        for c in cookies:
            jar.set(
                c['name'], c['value'],
                domain=c.get('domain', ''),
                path=c.get('path', '/')
            )

        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/125.0.0.0 Safari/537.36'
            )
        }

        response = requests.get(
            url, stream=True, timeout=300,
            cookies=jar, headers=headers
        )
        response.raise_for_status()

        # Check Content-Length for validation
        content_length = response.headers.get('Content-Length')
        expected = int(content_length) if content_length else None

        # Stream write to disk
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # Verify size after download
        if expected:
            actual = os.path.getsize(output_path)
            if actual != expected:
                os.remove(output_path)
                raise IOError(
                    f"Content-Length mismatch: expected {expected}, got {actual}"
                )

    def run(self):
        """Основной метод — сканирование канала и скачивание файлов"""
        stats = self.journal.get_stats()

        print("\n" + "═" * 60)
        print("  Скачивание файлов из канала MAX")
        print("═" * 60)
        print(f"  Журнал: {stats['total']} файлов "
              f"({stats['downloaded']} скачано, {stats['failed']} ошибок)")
        print("─" * 60)

        # 1. Get output directory from user
        output_dir = self._get_output_dir()
        print(f"  Папка: {output_dir}")

        # 2. Connect to browser
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            return

        # 3. Scan channel for files
        print("\n  Сканирование канала...")
        try:
            files = browser.scan_channel_for_files()
        except Exception as e:
            print(f"\n  ✗ Ошибка сканирования: {e}")
            self.logger.error(f"Scan error: {e}", exc_info=True)
            if self.browser:
                self.browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if not files:
            print("  ✓ В канале не найдено файловых сообщений.")
            if self.browser:
                self.browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # 4. Show file summary
        total_size = sum(f.get("file_size", 0) for f in files)
        print(f"  Найдено: {len(files)} файлов ({self._format_file_size(total_size)})")

        # Show table
        print(f"\n  {'#':>3}  {'Имя файла':<50} {'Размер':>10}")
        print(f"  {'─'*3}  {'─'*50} {'─'*10}")
        for i, f in enumerate(files, 1):
            fname = f.get("filename", "?")
            fsize = self._format_file_size(f.get("file_size", 0))
            display_name = fname[:47] + "..." if len(fname) > 50 else fname
            print(f"  {i:>3}  {display_name:<50} {fsize:>10}")

        # 5. User confirmation
        try:
            confirm = input(
                f"\n  Скачать {len(files)} файлов в '{output_dir}'? [y/N]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            if self.browser:
                self.browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if confirm not in ('y', 'yes', 'д', 'да'):
            print("\n  Отменено.")
            if self.browser:
                self.browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # 6. Download files
        os.makedirs(output_dir, exist_ok=True)
        retries = self.config.get('channel_downloader', {}).get('retries', 3)
        retry_delay = self.config.get('channel_downloader', {}).get('retry_delay', 5)
        large_file_threshold = (
            self.config.get('archiver', {}).get('large_file_threshold_mb', 50) * 1024 * 1024
        )

        downloaded_count = 0
        skipped_count = 0
        error_count = 0

        for i, file_info in enumerate(files, 1):
            if self._shutdown:
                print(f"\n  ⚠ Прерывание после {i - 1} файлов")
                break

            filename = file_info.get("filename", f"file_{i}")
            file_size = file_info.get("file_size", 0)
            download_url = file_info.get("download_url", "")
            has_direct_url = file_info.get("has_direct_url", False)
            file_size_str = self._format_file_size(file_size)

            # Check journal for deduplication
            if self.journal.is_downloaded(filename, file_size):
                print(f"  [{i}/{len(files)}] {filename} — ✓ уже скачан (журнал)")
                skipped_count += 1
                continue

            # Check if file exists on disk with matching size
            output_path = os.path.join(output_dir, filename)
            if os.path.exists(output_path):
                existing_size = os.path.getsize(output_path)
                if existing_size == file_size:
                    print(f"  [{i}/{len(files)}] {filename} — ✓ уже существует на диске")
                    self.journal.mark_downloaded(filename, file_size, output_path)
                    skipped_count += 1
                    continue
                else:
                    # Size mismatch — add suffix to avoid overwrite
                    base, ext = os.path.splitext(filename)
                    suffix = 1
                    while os.path.exists(os.path.join(output_dir, f"{base}_{suffix}{ext}")):
                        suffix += 1
                    output_path = os.path.join(output_dir, f"{base}_{suffix}{ext}")

            print(f"\n  [{i}/{len(files)}] {filename} ({file_size_str})")

            # Download with retry
            success = False
            for attempt in range(1, retries + 1):
                if self._shutdown:
                    break
                try:
                    if has_direct_url and download_url:
                        self._download_with_requests(browser, download_url, output_path)
                        success = True
                        break
                    else:
                        # Fallback via browser evaluate for files below threshold
                        if file_size < large_file_threshold:
                            print(f"    → Fallback: загрузка через браузер...")
                            raise NotImplementedError(
                                "Browser-based download fallback not yet implemented"
                            )
                        else:
                            threshold_mb = large_file_threshold // (1024 * 1024)
                            print(f"    ✗ Нет URL для скачивания (файл >{threshold_mb}MB)")
                            break

                except (ConnectionError, TimeoutError) as e:
                    if attempt < retries:
                        print(f"    ⚠ Ошибка: {e}, попытка {attempt + 1}/{retries}...")
                        time.sleep(retry_delay)
                        # _download_with_requests fetches fresh cookies on each call
                    else:
                        print(f"    ✗ Ошибка после {retries} попыток: {e}")
                        error_count += 1
                except requests.HTTPError as e:
                    if e.response.status_code == 403 or e.response.status_code == 401:
                        print(f"    ✗ Ошибка авторизации (HTTP {e.response.status_code})")
                    elif e.response.status_code == 404:
                        print(f"    ✗ Файл не найден (HTTP 404)")
                    else:
                        print(f"    ✗ HTTP ошибка: {e}")
                    error_count += 1
                    break
                except Exception as e:
                    print(f"    ✗ Ошибка: {e}")
                    self.logger.error(f"Download error for {filename}: {e}", exc_info=True)
                    error_count += 1
                    break

            if success:
                self.journal.mark_downloaded(filename, file_size, output_path)
                downloaded_count += 1
                print(f"    ✓ Скачано")

        # 7. Final summary
        print()
        print("═" * 60)
        print("Скачивание завершено")
        print(f"  Скачано: {downloaded_count}")
        print(f"  Пропущено: {skipped_count}")
        if error_count:
            print(f"  Ошибок: {error_count}")
        print("═" * 60)

        # Final save
        try:
            self.journal.save()
        except Exception:
            pass

        # Close browser
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass

        input("\n  Нажмите Enter для возврата в меню...")


def main():
    """Точка входа для запуска как standalone"""
    session = SessionCapture()
    session.start()
    print(f" Session log: {session.path}")

    setup_logging()
    try:
        downloader = ChannelDownloader("config.yaml")
        downloader.run()
    finally:
        session.stop()


if __name__ == "__main__":
    main()
