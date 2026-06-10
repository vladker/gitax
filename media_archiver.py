#!/usr/bin/env python3
"""
Media Archiver — Загрузка фото и видео из папки в MAX канал

Самостоятельный скрипт для отправки медиафайлов через BrowserMAX
"""

import os
import sys
import json
import time
import atexit
import signal
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from logging_config import setup_logging, LogMixin, SessionCapture

from browser_max import BrowserMAX


class MediaJournal:
    """Журнал отправленных медиафайлов"""

    def __init__(self, file_path: str = "media_journal.json"):
        self.file_path = file_path
        self._lock_file = f"{file_path}.lock"
        self.data = self._load()

    def _acquire_lock(self) -> bool:
        """Acquire exclusive lock for safe writes"""
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
        return {"entries": []}

    def clear(self):
        """Очистить журнал — сбросить все данные"""
        self.data = self._create_empty()
        self.save()

    def save(self):
        """Сохранить журнал в файл (атомарная запись)"""
        if not self._acquire_lock():
            return
        try:
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

    def is_sent(self, filename: str, size_bytes: int) -> bool:
        """Проверить, отправлен ли файл (по имени + размеру)"""
        for entry in self.data.get("entries", []):
            if (entry.get("filename") == filename
                    and entry.get("size_bytes") == size_bytes
                    and entry.get("status") == "sent"):
                return True
        return False

    def mark_sent(self, filename: str, size_bytes: int):
        """Отметить файл как отправленный"""
        self.data.setdefault("entries", []).append({
            "filename": filename,
            "size_bytes": size_bytes,
            "sent_at": datetime.now().isoformat(),
            "status": "sent"
        })
        self.save()

    def mark_failed(self, filename: str, size_bytes: int):
        """Отметить файл как ошибочный"""
        self.data.setdefault("entries", []).append({
            "filename": filename,
            "size_bytes": size_bytes,
            "sent_at": datetime.now().isoformat(),
            "status": "failed"
        })
        self.save()

    def get_stats(self) -> dict:
        """Получить статистику журнала"""
        entries = self.data.get("entries", [])
        return {
            "total": len(entries),
            "sent": len([e for e in entries if e.get("status") == "sent"]),
            "failed": len([e for e in entries if e.get("status") == "failed"]),
        }


class MediaArchiver(LogMixin):
    """Архиватор медиафайлов — загрузка фото и видео в MAX канал"""

    MEDIA_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff',
        '.mp4', '.mov', '.avi', '.mkv', '.webm'
    }

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        # Large file threshold from config (default 50 MB)
        self.LARGE_FILE_THRESHOLD = (
            self.config.get('archiver', {}).get('large_file_threshold_mb', 50) * 1024 * 1024
        )
        # Validate watch_dir (was in _load_config)
        media_watch_dir = self.config.get('media_archiver', {}).get('watch_dir', '')
        if not media_watch_dir:
            print("✗ MEDIA_WATCH_DIR не указана.")
            print("  Укажите в .env файле или переменной окружения")
            sys.exit(1)
        if not os.path.isdir(media_watch_dir):
            print(f"✗ Папка медиа не найдена: {media_watch_dir}")
            sys.exit(1)
        self.journal = MediaJournal("media_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        # Register cleanup
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
        """Инициализировать браузер MAX"""
        if self.browser is None:
            channel_url = self.config.get('channels', {}).get('media', '')
            use_local = self.config.get('media_archiver', {}).get('use_local_browser',
                         self.config.get('archiver', {}).get('use_local_browser', False))
            self.browser = BrowserMAX(channel_url, use_local_browser=use_local)
        return self.browser

    def _ensure_browser_connected(self):
        """Ensure browser is connected and ready"""
        browser = self._init_browser()
        if not browser.keep_alive_connect():
            raise Exception("Failed to connect to MAX")
        browser.navigate()
        browser.ensure_page_ready()
        return browser

    def _format_file_size(self, size_bytes: int) -> str:
        """Форматировать размер файла"""
        if size_bytes >= 1024 * 1024 * 1024:
            return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"
        elif size_bytes >= 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        elif size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} B"

    def _scan_files(self, watch_dir: str) -> list[str]:
        """
        Сканировать папку на наличие медиафайлов.

        Returns:
            Список путей к файлам, отсортированных по времени создания (ascending)
        """
        files = []

        for root, _dirs, filenames in os.walk(watch_dir):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in self.MEDIA_EXTENSIONS:
                    continue

                filepath = os.path.join(root, fname)

                try:
                    size = os.path.getsize(filepath)
                    if size == 0:
                        continue
                    ctime = os.path.getctime(filepath)
                    files.append((ctime, filepath))
                except OSError:
                    continue

        # Sort by creation time ascending (oldest first)
        files.sort(key=lambda x: x[0])
        return [f[1] for f in files]

    def run(self):
        """Основной цикл работы"""
        stats = self.journal.get_stats()

        print("\n" + "=" * 60)
        print("           Media Archiver")
        print("           Загрузка медиа в MAX")
        print("=" * 60)
        print(f"  Журнал: {stats['total']} файлов "
              f"({stats['sent']} отправлено, {stats['failed']} ошибок)")
        print("-" * 60)

        watch_dir = self.config.get('media_archiver', {}).get('watch_dir', '')
        retries = self.config.get('media_archiver', {}).get('retries',
                    self.config.get('archiver', {}).get('retries', 3))
        retry_delay = self.config.get('media_archiver', {}).get('retry_delay',
                        self.config.get('archiver', {}).get('retry_delay', 10))

        print(f"\n  Папка: {watch_dir}")

        # Scan files
        print("  Сканирование файлов...")
        files = self._scan_files(watch_dir)
        print(f"  Найдено: {len(files)} медиафайлов")

        if not files:
            print("  ✓ Медиафайлов не найдено.")
            return

        # Connect to browser
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            return

        # Process each file
        sent_count = 0
        skipped_count = 0
        error_count = 0

        for i, filepath in enumerate(files, 1):
            if self._shutdown:
                print(f"\n  ⚠ Прерывание после {i - 1} файлов")
                break

            filename = os.path.basename(filepath)
            try:
                file_size = os.path.getsize(filepath)
            except OSError:
                file_size = 0

            file_size_str = self._format_file_size(file_size)
            ext = os.path.splitext(filename)[1].lower()

            print(f"\n  [{i}/{len(files)}] {filename} ({file_size_str}, {ext})")

            # Check journal for deduplication
            if self.journal.is_sent(filename, file_size):
                print(f"    ✓ Уже отправлено (журнал)")
                skipped_count += 1
                continue

            # Upload — route large files via local browser to bypass CDP 50MB limit
            try:
                if file_size >= self.LARGE_FILE_THRESHOLD:
                    print(f"    → Отправляю в MAX (большой файл)...")
                    success = browser._upload_large_file(
                        filepath, filename, file_size,
                        retries=retries,
                        retry_delay=retry_delay,
                        baseline_count=0,
                        expected_extensions=[ext]
                    )
                else:
                    print(f"    → Отправляю в MAX...")
                    success, _ = browser.send_message_with_files(
                        text="",
                        filepaths=[filepath],
                        retries=retries,
                        retry_delay=retry_delay,
                        split_mode="off",
                        expected_extensions=[ext]
                    )

                if success:
                    self.journal.mark_sent(filename, file_size)
                    sent_count += 1
                    print(f"    ✓ Отправлено")
                else:
                    self.journal.mark_failed(filename, file_size)
                    error_count += 1
                    print(f"    ✗ Ошибка загрузки")
            except Exception as e:
                self.journal.mark_failed(filename, file_size)
                error_count += 1
                print(f"    ✗ Ошибка: {e}")

        # Summary
        print()
        print("=" * 60)
        print("Загрузка завершена")
        print(f"  Отправлено: {sent_count}")
        print(f"  Пропущено: {skipped_count}")
        if error_count:
            print(f"  Ошибок: {error_count}")
        print("=" * 60)

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


def main():
    """Точка входа"""
    session = SessionCapture()
    session.start()
    print(f"📋 Session log: {session.path}")

    setup_logging()
    try:
        archiver = MediaArchiver("config.yaml")
        archiver.run()
    finally:
        session.stop()


if __name__ == "__main__":
    main()
