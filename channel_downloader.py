#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Channel Downloader — Скачивание всех файлов из MAX канала в локальную папку

Самостоятельный скрипт для скачивания файлов через BrowserMAX.
Следует тому же паттерну, что media_archiver.py.
"""

import os
import sys
import time

import atexit
import signal
import requests
from datetime import datetime

from dotenv import load_dotenv

from logging_config import setup_logging, LogMixin, SessionCapture
from shared_journal import BaseJournal

from browser_max import BrowserMAX
from browser_init import BrowserInitMixin
from signal_handler import SignalHandler
from progressbar import LiveProgressBar
from utils import format_file_size
from retry import retry



class DownloadJournal(BaseJournal):
    """Журнал скачанных файлов — отслеживает какие файлы уже скачаны

    Inherits locking, atomic save, corruption recovery from BaseJournal.
    """

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

    def _pre_save(self):
        """Update stats before every save (hook from BaseJournal)"""
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


class ChannelDownloader(BrowserInitMixin, LogMixin):
    """Скачивание всех файлов из MAX канала в локальную папку

    Оркестрирует процесс:
    1. Подключение к браузеру MAX через CDP
    2. Сканирование канала — сбор метаданных файлов
    3. Показ списка пользователю
    4. Скачивание через requests + cookies из браузера
    5. Ведение журнала скачанных файлов (resume support)
    """

    _channel_key = "max"
    _section_key = None

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        self.journal = DownloadJournal("download_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        # Register cleanup handlers (same pattern as media_archiver.py)
        SignalHandler().register(self, on_cleanup=self._cleanup)

    def _cleanup(self):
        """Clean up resources on exit"""
        if self.browser:
            try:
                self.journal.save()
                self.browser.close()
            except Exception:
                pass

    def _get_output_dir(self) -> str:
        """Спросить у пользователя папку для скачивания (дефолт из config)"""
        default = self.config.get('channel_downloader', {}).get('output_dir', './downloads')
        try:
            user_dir = input(f"  Папка для скачивания [{default}]: ").strip()
            return user_dir if user_dir else default
        except (EOFError, KeyboardInterrupt):
            return default

    @retry(
        max_retries=3,
        delay=5.0,
        backoff=1.0,
        exceptions=(ConnectionError, TimeoutError),
    )
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
        print(f"  Найдено: {len(files)} файлов ({format_file_size(total_size)})")

        # Show table
        print(f"\n  {'#':>3}  {'Имя файла':<50} {'Размер':>10}")
        print(f"  {'─'*3}  {'─'*50} {'─'*10}")
        for i, f in enumerate(files, 1):
            fname = f.get("filename", "?")
            fsize = format_file_size(f.get("file_size", 0))
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

        with LiveProgressBar(len(files), "Скачивание файлов") as bar:
            for i, file_info in enumerate(files, 1):
                if self._shutdown:
                    print(f"\n  ⚠ Прерывание после {i - 1} файлов")
                    break

                filename = file_info.get("filename", f"file_{i}")
                file_size = file_info.get("file_size", 0)
                download_url = file_info.get("download_url", "")
                has_direct_url = file_info.get("has_direct_url", False)
                file_size_str = format_file_size(file_size)
                bar.update(i, item_name=filename)

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
                            # No direct download URL available
                            threshold_mb = large_file_threshold // (1024 * 1024)
                            print(f"    ✗ Нет URL для скачивания (файл <{threshold_mb}MB, browser fallback N/A)")
                            error_count += 1
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
    load_dotenv()
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
