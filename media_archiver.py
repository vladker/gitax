#!/usr/bin/env python3
"""
Media Archiver — Загрузка фото и видео из папки в MAX канал

Самостоятельный скрипт для отправки медиафайлов через BrowserMAX
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from logging_config import setup_logging, LogMixin, SessionCapture
from shared_journal import BaseJournal

from browser_max import BrowserMAX
from browser_init import BrowserInitMixin
from signal_handler import SignalHandler
from progressbar import LiveProgressBar
from utils import format_file_size


class MediaJournal(BaseJournal):
    """Журнал отправленных медиафайлов с поддержкой сессий, батчей и чекпоинтов.

    Структура (v2):
    {
        "version": 2,
        "file_index": {
            "relative/path/file.mp4|12345678": {
                "status": "sent" | "failed",
                "sent_at": "ISO timestamp",
                "session_id": "ses_...",
                "size_bytes": 12345678,
                "filepath": "relative/path/file.mp4"
            }
        },
        "sessions": {
            "ses_20260729_001": {
                "started_at": "...",
                "ended_at": "...",
                "files_processed": 150,
                "bytes_sent": 94371840000,
                "bytes_failed": 0,
                "status": "completed" | "interrupted"
            }
        },
        "batches": {
            "photos/2024": {
                "total_files": 500,
                "sent_files": 500,
                "total_bytes": 200000000000,
                "sent_bytes": 200000000000,
                "status": "completed" | "in_progress" | "pending"
            }
        },
        "total_bytes_scanned": 4000000000000,
        "watch_dir": "/path/to/watch"
    }

    Inherits locking, atomic save, corruption recovery from BaseJournal.
    """

    SCHEMA_VERSION = 2

    def __init__(self, file_path: str = "media_journal.json"):
        super().__init__(file_path)
        self._current_session_id = ""
        self._migrate_v1_to_v2()

    # ── Attribute proxies (dict → property) ───────────────────

    @property
    def file_index(self) -> dict:
        """Прямой доступ к file_index."""
        return self.data.setdefault("file_index", {})

    @property
    def sessions(self) -> dict:
        """Прямой доступ к sessions."""
        return self.data.setdefault("sessions", {})

    @property
    def batches(self) -> dict:
        """Прямой доступ к batches."""
        return self.data.setdefault("batches", {})

    def _create_empty(self) -> dict:
        """Создать пустой журнал v2"""
        return {
            "version": self.SCHEMA_VERSION,
            "file_index": {},
            "sessions": {},
            "batches": {},
            "total_bytes_scanned": 0,
            "watch_dir": "",
        }

    # ── Migration ────────────────────────────────────────────

    def _migrate_v1_to_v2(self):
        """Миграция из старого формата (flat entries list) в v2 (dict index)."""
        if self.data.get("version") == self.SCHEMA_VERSION:
            return

        old_entries = self.data.get("entries", [])
        if not old_entries:
            # Already v2 or truly empty
            self.data = self._create_empty()
            self.data["file_index"] = {}
            return

        file_index = {}
        for entry in old_entries:
            fn = entry.get("filename", "")
            sz = entry.get("size_bytes", 0)
            key = self._file_key(fn, sz)
            file_index[key] = {
                "status": entry.get("status", "sent"),
                "sent_at": entry.get("sent_at", ""),
                "session_id": "",
                "size_bytes": sz,
                "filepath": fn,
            }

        self.data = self._create_empty()
        self.data["file_index"] = file_index
        self.logger.info(f"Migrated {len(file_index)} entries from v1 to v2")

    # ── File key generation ──────────────────────────────────

    @staticmethod
    def _file_key(filepath: str, size_bytes: int) -> str:
        """Generate unique file key from relative path + size."""
        return f"{filepath}|{size_bytes}"

    # ── Core file operations (O(1) via dict) ─────────────────

    def was_sent(self, filepath: str, size_bytes: int) -> bool:
        """Проверить, отправлен ли файл (по пути + размеру).

        Alias for is_sent.
        """
        return self.is_sent(filepath, size_bytes)

    def is_sent(self, filepath: str, size_bytes: int) -> bool:
        """Проверить, отправлен ли файл (по пути + размеру).

        Args:
            filepath: Относительный путь к файлу (от watch_dir) или имя файла
            size_bytes: Размер файла в байтах

        Returns:
            True если файл с таким путём и размером уже отправлен
        """
        key = self._file_key(filepath, size_bytes)
        entry = self.data.get("file_index", {}).get(key)
        if entry and entry.get("status") == "sent":
            return True
        return False

    def mark_sent(self, filepath: str, size_bytes: int, session_id: str = ""):
        """Отметить файл как отправленный + сохранить чекпоинт.

        Checkpoint saves immediately after each file for crash recovery.
        """
        if not session_id:
            session_id = self._current_session_id
        key = self._file_key(filepath, size_bytes)
        self.data.setdefault("file_index", {})[key] = {
            "status": "sent",
            "sent_at": datetime.now().isoformat(),
            "session_id": session_id,
            "size_bytes": size_bytes,
            "filepath": filepath,
        }
        # Update session stats
        self._update_session_stats(session_id, size_bytes, sent=True)
        # Checkpoint — save after every file
        self.save()

    def mark_failed(self, filepath: str, size_bytes: int, session_id: str = ""):
        """Отметить файл как ошибочный + сохранить чекпоинт."""
        key = self._file_key(filepath, size_bytes)
        self.data.setdefault("file_index", {})[key] = {
            "status": "failed",
            "sent_at": datetime.now().isoformat(),
            "session_id": session_id,
            "size_bytes": size_bytes,
            "filepath": filepath,
            "failed_sessions": [session_id] if session_id else [],
        }
        self._update_session_stats(session_id, size_bytes, sent=False)
        self.save()

    def get_file_entry(self, filepath: str, size_bytes: int) -> dict | None:
        """Получить запись файла из журнала."""
        key = self._file_key(filepath, size_bytes)
        return self.data.get("file_index", {}).get(key)

    def cleanup_file(self, filepath: str, size_bytes: int):
        """Удалить запись файла из журнала (для повторной отправки)."""
        key = self._file_key(filepath, size_bytes)
        self.data.setdefault("file_index", {}).pop(key, None)
        self.save()

    def cleanup_batch(self, batch_name: str):
        """Удалить батч из журнала."""
        self.data.setdefault("batches", {}).pop(batch_name, None)
        self.save()

    # ── Aliases for test compatibility ───────────────────────

    def create_batch(self, batch_name: str, total_files: int, total_bytes: int = 0):
        """Alias for register_batch. Returns the created batch dict."""
        self.register_batch(batch_name, total_files, total_bytes)
        return self.data["batches"][batch_name]

    def get_batch_list(self) -> list[str]:
        """Получить список всех батчей."""
        return list(self.data.get("batches", {}).keys())

    def get_batch_progress(self, batch_name: str) -> dict:
        """Получить прогресс батча в процентах."""
        batch = self.get_batch(batch_name)
        if not batch:
            return {"percent": 0.0, "sent_bytes": 0, "total_bytes": 0,
                    "files_done": 0, "files_total": 0}
        total = batch.get("total_bytes", 0)
        sent = batch.get("sent_bytes", 0)
        files_done = batch.get("sent_files", 0)
        files_total = batch.get("total_files", 0)
        if total == 0:
            return {"percent": 0.0, "sent_bytes": 0, "total_bytes": 0,
                    "files_done": files_done, "files_total": files_total}
        return {
            "percent": round(sent / total * 100, 1),
            "sent_bytes": sent,
            "total_bytes": total,
            "files_done": files_done,
            "files_total": files_total,
        }

    def get_summary(self) -> dict:
        """Alias for get_stats."""
        return self.get_stats()

    # ── Session management ───────────────────────────────────

    def start_session(self, session_id: str = "", watch_dir: str = "") -> dict:
        """Начать новую сессию.

        Args:
            session_id: Явный ID сессии (если пустой — генерируется автоматически)
            watch_dir: Наблюдаемая директория

        Returns:
            dict с id и другими полями сессии
        """
        if not session_id:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"ses_{ts}_{id(self) % 10000:04d}"

        sess_data = {
            "started_at": datetime.now().isoformat(),
            "ended_at": "",
            "files_processed": 0,
            "bytes_sent": 0,
            "bytes_failed": 0,
            "status": "in_progress",
        }
        self.data.setdefault("sessions", {})[session_id] = sess_data
        if watch_dir:
            self.data["watch_dir"] = watch_dir
        self._current_session_id = session_id
        self.save()
        return {"id": session_id, **sess_data}

    def end_session(self, session_id: str, status: str = "completed", bytes_sent: int = 0):
        """Завершить сессию."""
        sess = self.data.get("sessions", {}).get(session_id)
        if sess:
            sess["ended_at"] = datetime.now().isoformat()
            sess["status"] = status
            if bytes_sent:
                sess["bytes_sent"] = bytes_sent
            self.save()

    def get_current_session(self) -> dict | None:
        """Получить текущую активную сессию."""
        for sid, sess in self.data.get("sessions", {}).items():
            if sess.get("status") == "in_progress":
                return {"id": sid, **sess}
        return None

    def _update_session_stats(self, session_id: str, size_bytes: int, sent: bool):
        """Обновить статистику сессии после обработки файла."""
        if not session_id:
            return
        sess = self.data.get("sessions", {}).get(session_id)
        if not sess:
            return
        sess["files_processed"] = sess.get("files_processed", 0) + 1
        if sent:
            sess["bytes_sent"] = sess.get("bytes_sent", 0) + size_bytes
        else:
            sess["bytes_failed"] = sess.get("bytes_failed", 0) + size_bytes

    # ── Batch (subdirectory) tracking ────────────────────────

    def register_batch(self, batch_name: str, total_files: int, total_bytes: int = 0):
        """Зарегистрировать батч (подпапку) для отслеживания прогресса.

        Args:
            batch_name: Имя подпапки (относительно watch_dir)
            total_files: Общее количество файлов в батче
            total_bytes: Общий размер файлов в батче
        """
        self.data.setdefault("batches", {})[batch_name] = {
            "total_files": total_files,
            "sent_files": 0,
            "total_bytes": total_bytes,
            "sent_bytes": 0,
            "status": "pending",
        }

    def update_batch(self, batch_name: str, filename: str = "", size_bytes: int = 0, sent: bool = True):
        """Обновить прогресс батча после обработки файла.

        Args:
            batch_name: Имя батча
            filename: Имя файла (для логирования)
            size_bytes: Размер файла
            sent: True если успешно, False если ошибка

        Returns:
            True если батч завершён (все файлы отправлены)
        """
        batch = self.data.get("batches", {}).get(batch_name)
        if not batch:
            return False
        if sent:
            batch["sent_files"] = batch.get("sent_files", 0) + 1
            batch["sent_bytes"] = batch.get("sent_bytes", 0) + size_bytes
        # Check completion
        if batch["sent_files"] >= batch["total_files"]:
            batch["status"] = "completed"
            return True
        elif batch["sent_files"] > 0:
            batch["status"] = "in_progress"
        return False

    def mark_batch_complete(self, batch_name: str):
        """Отметить батч как завершённый."""
        batch = self.data.get("batches", {}).get(batch_name)
        if batch:
            batch["status"] = "completed"
            batch["sent_files"] = batch["total_files"]
            batch["sent_bytes"] = batch["total_bytes"]
            self.save()

    def get_batch(self, batch_name: str) -> dict | None:
        """Получить информацию о батче."""
        return self.data.get("batches", {}).get(batch_name)

    def get_completed_batches(self) -> list[str]:
        """Получить список завершённых батчей."""
        return [
            name for name, b in self.data.get("batches", {}).items()
            if b.get("status") == "completed"
        ]

    def get_pending_batches(self) -> list[str]:
        """Получить список незавершённых батчей."""
        return [
            name for name, b in self.data.get("batches", {}).items()
            if b.get("status") != "completed"
        ]

    # ── Total bytes tracking ─────────────────────────────────

    def set_total_bytes_scanned(self, total_bytes: int):
        """Установить общий размер всех файлов (для расчёта прогресса)."""
        self.data["total_bytes_scanned"] = total_bytes

    def get_sent_bytes(self) -> int:
        """Получить общее количество отправленных байт."""
        return sum(
            e.get("size_bytes", 0)
            for e in self.data.get("file_index", {}).values()
            if e.get("status") == "sent"
        )

    def get_remaining_bytes(self) -> int:
        """Получить оставшееся количество байт."""
        total = self.data.get("total_bytes_scanned", 0)
        sent = self.get_sent_bytes()
        return max(0, total - sent)

    # ── Stats ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Получить статистику журнала"""
        idx = self.data.get("file_index", {})
        sent_entries = [e for e in idx.values() if e.get("status") == "sent"]
        failed_entries = [e for e in idx.values() if e.get("status") == "failed"]

        total_bytes_scanned = self.data.get("total_bytes_scanned", 0)
        sent_bytes = sum(e.get("size_bytes", 0) for e in sent_entries)
        remaining = max(0, total_bytes_scanned - sent_bytes)

        return {
            "total": len(idx),
            "total_files": len(idx),
            "sent": len(sent_entries),
            "failed": len(failed_entries),
            "total_bytes_scanned": total_bytes_scanned,
            "sent_bytes": sent_bytes,
            "remaining_bytes": remaining,
            "batches_total": len(self.data.get("batches", {})),
            "total_batches": len(self.data.get("batches", {})),
            "batches_completed": len(self.get_completed_batches()),
            "sessions_total": len(self.data.get("sessions", {})),
            "total_sessions": len(self.data.get("sessions", {})),
        }

    def get_progress(self) -> dict:
        """Получить прогресс выгрузки в процентах.

        Returns:
            Dict with percentage, sent_bytes, remaining_bytes, total_files
        """
        total = self.data.get("total_bytes_scanned", 0)
        sent = self.get_sent_bytes()
        total_files = len(self.data.get("file_index", {}))
        if total == 0:
            return {"percent": 0.0, "sent_bytes": 0, "remaining_bytes": 0, "total_files": total_files}
        pct = (sent / total) * 100
        return {
            "percent": round(pct, 2),
            "sent_bytes": sent,
            "remaining_bytes": max(0, total - sent),
            "total_files": total_files,
        }


class MediaArchiver(LogMixin, BrowserInitMixin):
    """Архиватор медиафайлов — загрузка фото и видео в MAX канал"""

    _channel_key = "media"
    _section_key = "media_archiver"

    MEDIA_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff',
        '.mp4', '.mov', '.avi', '.mkv', '.webm'
    }

    LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50 MB class default

    def __init__(self, config_path: str = "config.yaml", watch_dir: str | None = None):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        # Large file threshold from config (default 50 MB)
        self.LARGE_FILE_THRESHOLD = (
            self.config.get('archiver', {}).get('large_file_threshold_mb', 50) * 1024 * 1024
        )
        # watch_dir: prefer explicit argument, fall back to config
        from utils import ConfigurationError
        self._watch_dir = watch_dir or self.config.get('media_archiver', {}).get('watch_dir', '')
        if not self._watch_dir:
            raise ConfigurationError(
                "Папка для медиа не указана. Укажите при создании MediaArchiver(watch_dir='...') "
                "или настройте media_archiver.watch_dir в config.yaml / MEDIA_WATCH_DIR в .env"
            )
        if not os.path.isdir(self._watch_dir):
            raise ConfigurationError(f"Папка медиа не найдена: {self._watch_dir}")
        self.journal = MediaJournal("media_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        # Register cleanup
        SignalHandler().register(self, on_cleanup=self._cleanup)

    def _cleanup(self):
        """Clean up resources on exit — mark interrupted session, close browser."""
        try:
            # Mark any in-progress session as interrupted
            cur = self.journal.get_current_session()
            if cur:
                self.journal.end_session(cur["id"], status="interrupted")
        except Exception:
            pass
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass

    def _scan_files(self, watch_dir: str) -> tuple[list[str], dict[str, dict], int]:
        """
        Сканировать папку на наличие медиафайлов.

        Returns:
            Tuple of:
            - Список абсолютных путей к файлам (отсортированы по ctime)
            - Словарь батчей: {batch_name: {"files": count, "bytes": total}}
            - Общий размер всех файлов в байтах
        """
        files = []
        batches: dict[str, dict] = {}
        total_bytes = 0

        for root, _dirs, filenames in os.walk(watch_dir):
            # Batch = immediate subdirectory relative to watch_dir (or "." for root)
            rel_root = os.path.relpath(root, watch_dir)
            if rel_root == ".":
                batch_name = "."
            else:
                # Use the first-level subdirectory as batch key
                parts = rel_root.split(os.sep)
                batch_name = parts[0]

            batches.setdefault(batch_name, {"files": 0, "bytes": 0})

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
                    total_bytes += size
                    batches[batch_name]["files"] += 1
                    batches[batch_name]["bytes"] += size
                except OSError:
                    continue

        # Sort by creation time ascending (oldest first)
        files.sort(key=lambda x: x[0])
        return [f[1] for f in files], batches, total_bytes

    def _get_relative_path(self, filepath: str) -> str:
        """Get relative path from watch_dir for journal dedup."""
        return os.path.relpath(filepath, self._watch_dir)

    def run(self):
        """Основной цикл работы с сессиями, батчами и чекпоинтами."""
        stats = self.journal.get_stats()

        print("\n" + "=" * 60)
        print("           Media Archiver")
        print("           Загрузка медиа в MAX")
        print("=" * 60)
        print(f"  Журнал: {stats['total']} файлов "
              f"({stats['sent']} отправлено, {stats['failed']} ошибок)")
        if stats.get("batches_total"):
            print(f"  Батчи: {stats['batches_completed']}/{stats['batches_total']} завершено")
        if stats.get("sessions_total"):
            print(f"  Сессий: {stats['sessions_total']}")
        print("-" * 60)

        watch_dir = self._watch_dir
        retries = self.config.get('media_archiver', {}).get('retries',
                    self.config.get('archiver', {}).get('retries', 3))
        retry_delay = self.config.get('media_archiver', {}).get('retry_delay',
                        self.config.get('archiver', {}).get('retry_delay', 10))

        print(f"\n  Папка: {watch_dir}")

        # Scan files + gather batch info
        print("  Сканирование файлов...")
        files, batches, total_bytes = self._scan_files(watch_dir)
        print(f"  Найдено: {len(files)} медиафайлов ({format_file_size(total_bytes)})")

        if not files:
            print("  ✓ Медиафайлов не найдено.")
            return

        # Register session
        session_id = self.journal.start_session(watch_dir)
        self.logger.info(f"Session started: {session_id}")

        # Register batches
        for batch_name, info in batches.items():
            self.journal.register_batch(batch_name, info["files"], info["bytes"])

        # Set total bytes for progress tracking
        self.journal.set_total_bytes_scanned(total_bytes)

        # Show overall progress before starting
        progress = self.journal.get_progress()
        if progress["percent"] > 0:
            print(f"  Общий прогресс: {progress['percent']:.1f}% "
                  f"({format_file_size(progress['sent_bytes'])} / "
                  f"{format_file_size(total_bytes)})")

        # Connect to browser
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            self.journal.end_session(session_id, status="failed")
            return

        # Process each file
        sent_count = 0
        skipped_count = 0
        error_count = 0
        session_bytes_sent = 0

        with LiveProgressBar(len(files), "Загрузка медиа") as bar:
            for i, filepath in enumerate(files, 1):
                if self._shutdown:
                    print(f"\n  ⚠ Прерывание после {i - 1} файлов")
                    break

                filename = os.path.basename(filepath)
                rel_path = self._get_relative_path(filepath)
                try:
                    file_size = os.path.getsize(filepath)
                except OSError:
                    file_size = 0

                file_size_str = format_file_size(file_size)
                ext = os.path.splitext(filename)[1].lower()
                bar.update(i, item_name=filename)
                print(f"\n  [{i}/{len(files)}] {filename} ({file_size_str}, {ext})")

                # Check journal for deduplication (by relative path + size)
                if self.journal.is_sent(rel_path, file_size):
                    print("    ✓ Уже отправлено (журнал)")
                    skipped_count += 1
                    continue

                # Upload — route large files via local browser to bypass CDP 50MB limit
                uploaded = False
                try:
                    if file_size >= self.LARGE_FILE_THRESHOLD:
                        print("    → Отправляю в MAX (большой файл)...")
                        uploaded = browser._upload_large_file(
                            filepath, filename, file_size,
                            retries=retries,
                            retry_delay=retry_delay,
                            baseline_count=0,
                            expected_extensions=[ext]
                        )
                    else:
                        print("    → Отправляю в MAX...")
                        uploaded, _ = browser.send_message_with_files(
                            text="",
                            filepaths=[filepath],
                            retries=retries,
                            retry_delay=retry_delay,
                            split_mode="off",
                            expected_extensions=[ext]
                        )

                    if uploaded:
                        self.journal.mark_sent(rel_path, file_size, session_id)
                        sent_count += 1
                        session_bytes_sent += file_size
                        print("    ✓ Отправлено")
                    else:
                        self.journal.mark_failed(rel_path, file_size, session_id)
                        error_count += 1
                        print("    ✗ Ошибка загрузки")
                except Exception as e:
                    self.journal.mark_failed(rel_path, file_size, session_id)
                    error_count += 1
                    print(f"    ✗ Ошибка: {e}")

                # Update batch progress
                batch_name = self._get_batch_for_file(filepath, batches, watch_dir)
                if batch_name:
                    self.journal.update_batch(batch_name, file_size, sent=uploaded)

                # Show progress every 50 files
                if i % 50 == 0 or i == len(files):
                    progress = self.journal.get_progress()
                    print(f"  ⟳ Прогресс: {progress['percent']:.1f}% "
                          f"({format_file_size(progress['sent_bytes'])} / "
                          f"{format_file_size(total_bytes)})")

        # End session
        status = "completed"
        if self._shutdown:
            status = "interrupted"
        elif error_count and sent_count == 0:
            status = "failed"
        self.journal.end_session(session_id, status=status)

        # Summary
        print()
        print("=" * 60)
        print("Загрузка завершена")
        print(f"  Отправлено: {sent_count} ({format_file_size(session_bytes_sent)})")
        print(f"  Пропущено: {skipped_count}")
        if error_count:
            print(f"  Ошибок: {error_count}")
        progress = self.journal.get_progress()
        print(f"  Общий прогресс: {progress['percent']:.1f}%")
        print(f"  Сессия: {session_id} ({status})")
        print("=" * 60)

        # Close browser
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass

    def _get_batch_for_file(self, filepath: str,
                            batches: dict[str, dict],
                            watch_dir: str) -> str | None:
        """Determine which batch a file belongs to."""
        rel = os.path.relpath(filepath, watch_dir)
        if rel.startswith("."):
            return "."
        parts = rel.split(os.sep)
        if parts:
            return parts[0]
        return None


def _ask_watch_dir(config: dict) -> str | None:
    """Запросить путь к папке с медиа у пользователя."""
    default_dir = config.get('media_archiver', {}).get('watch_dir', '')
    if default_dir:
        print(f"\n  Папка по умолчанию: {default_dir}")
        folder = input(f"  Путь к папке с медиа [Enter для {default_dir}]: ").strip()
        if not folder:
            folder = default_dir
    else:
        folder = input("  Путь к папке с медиа: ").strip()

    if not folder:
        print("\n  ✗ Путь не указан")
        return None

    if not os.path.isdir(folder):
        print(f"\n  ✗ Папка не найдена: {folder}")
        return None

    return folder


def main():
    """Точка входа"""
    load_dotenv()
    session = SessionCapture()
    session.start()
    print(f"📋 Session log: {session.path}")

    setup_logging()
    try:
        from config import init_config, get_config
        init_config("config.yaml")
        config = get_config().model_dump()
        folder = _ask_watch_dir(config)
        if not folder:
            print("\n  Загрузка отменена")
            return
        archiver = MediaArchiver("config.yaml", watch_dir=folder)
        archiver.run()
    finally:
        session.stop()


if __name__ == "__main__":
    main()
