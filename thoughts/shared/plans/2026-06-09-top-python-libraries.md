# Top Python Libraries Archiver — Implementation Plan

**Goal:** Add a feature to archive the top N Python libraries (from the Hugovk dataset) and publish them (.tar.gz + .whl files + description text) to a dedicated MAX messenger channel.

**Architecture:** New `pypi_libs_archiver.py` module (following the `MediaArchiver` pattern from `media_archiver.py`) with `PyPILibsArchiver` + `PyPILibsJournal`. Reuses `PyPIAPI` from existing `pypi_api.py`. Two new menu options (11, 12) in `github_archiver.py` with lazy import.

**Design:** `thoughts/shared/designs/2026-06-09-top-python-libraries-design.md`

**Key decisions:**
- **Temp file location**: `pypi_api.download_package()` uses its own hardcoded `./temp_pypi/{name}/`. The archiver cleans up those files individually after each package + recursively on shutdown. `GracefulShutdown` in `github_archiver.py` also cleans `./temp_pypi/`.
- **Channel URL config**: `PyPILibsArchiver` loads `PYPI_LIBS_CHANNEL_URL` in its own `_load_config()` (same pattern as `MediaArchiver` loading `MEDIA_CHANNEL_URL`). No need to load it in `github_archiver.py` — the lazy-imported archiver handles its own config.
- **Journal dedup**: By `(name, version)` tuple — same version is blocked from re-sending.

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3, 1.4  [foundation — no deps, 4 implementers]
Batch 2 (parallel): 2.1, 2.2            [tests — depend on 1.1, 1.2, 2 implementers]
Batch 3 (sequential): 3.1               [integration — depends on all above, 1 implementer]
```

---

## Batch 1: Foundation (parallel — 4 implementers)

All tasks in this batch have NO dependencies and run simultaneously.

---

### Task 1.1: `pypi_libs_journal.py` — Journal module

**File:** `pypi_libs_journal.py` (new)
**Test:** `tests/test_pypi_libs_journal.py` (Task 2.1)
**Depends:** none

**Justification:** The design requires a dedicated journal for PyPI libraries (separate from the GitHub `Journal` and `MediaJournal`). Following the `MediaJournal` pattern from `media_archiver.py` — atomic writes via tempfile+rename, lock file, json storage.

```python
"""
Журнал отправленных PyPI библиотек

Хранится в pypi_libs_journal.json.
Дедупликация: (name, version) — повторная отправка той же версии блокируется.
Атомарная запись (write+rename), как в journal.py / MediaJournal.
"""

import json
import os
import time
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from logging_config import LogMixin


class PyPILibsJournal(LogMixin):
    """Журнал отправленных PyPI библиотек"""

    def __init__(self, file_path: str = "pypi_libs_journal.json"):
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
        return {"libraries": []}

    def clear(self):
        """Очистить журнал — сбросить все данные"""
        self.data = self._create_empty()
        self.save()
        self.logger.info("PyPI libs journal cleared")

    def save(self):
        """Сохранить журнал в файл (атомарная запись через write+rename)"""
        if not self._acquire_lock():
            self.logger.warning("Journal locked, skipping save")
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

    def add(self, name: str, version: str, description: str,
            downloads: int, files: list[str]) -> bool:
        """
        Добавить запись о библиотеке в журнал.

        Args:
            name: Имя библиотеки
            version: Версия
            description: Описание
            downloads: Количество загрузок за 365 дней
            files: Список имён отправленных файлов

        Returns:
            True если добавлена, False если (name, version) уже существует
        """
        if self.exists(name, version):
            return False

        self.data.setdefault("libraries", []).append({
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "status": "sent",
            "sent_at": datetime.now().isoformat(),
            "files": files,
        })
        self.save()
        return True

    def mark_failed(self, name: str, version: str, description: str = "",
                    downloads: int = 0):
        """Отметить пакет как ошибочный"""
        self.data.setdefault("libraries", []).append({
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "status": "failed",
            "sent_at": datetime.now().isoformat(),
            "files": [],
        })
        self.save()

    def update(self, name: str, version: str, updates: dict) -> bool:
        """
        Обновить запись библиотеки (по name + version).

        Args:
            name: Имя библиотеки
            version: Версия
            updates: Словарь с обновлениями

        Returns:
            True если обновлена, False если не найдена
        """
        for entry in self.data.setdefault("libraries", []):
            if entry.get("name") == name and entry.get("version") == version:
                entry.update(updates)
                entry["updated_at"] = datetime.now().isoformat()
                self.save()
                return True
        return False

    def exists(self, name: str, version: str) -> bool:
        """
        Проверить, есть ли библиотека (name, version) в журнале.
        Дедупликация: (name, version) — повторная отправка той же версии блокируется.
        """
        for entry in self.data.get("libraries", []):
            if entry.get("name") == name and entry.get("version") == version:
                return True
        return False

    def get(self, name: str) -> dict | None:
        """Получить последнюю запись библиотеки по имени (по latest version)."""
        latest = None
        for entry in self.data.get("libraries", []):
            if entry.get("name") == name:
                if latest is None:
                    latest = entry
                elif (entry.get("sent_at") or "") > (latest.get("sent_at") or ""):
                    latest = entry
        return latest

    def get_all(self) -> list[dict]:
        """Получить все записи"""
        return list(self.data.get("libraries", []))

    def get_count(self) -> int:
        """Получить количество записей"""
        return len(self.data.get("libraries", []))

    def get_stats(self) -> dict:
        """Получить статистику журнала"""
        entries = self.data.get("libraries", [])
        return {
            "total": len(entries),
            "sent": len([e for e in entries if e.get("status") == "sent"]),
            "failed": len([e for e in entries if e.get("status") == "failed"]),
        }
```

**Verify:** `python -c "from pypi_libs_journal import PyPILibsJournal; j = PyPILibsJournal('test_journal.json'); print('OK'); j.clear(); import os; os.remove('test_journal.json')"`
**Commit:** `feat(pypi-libs): add PyPILibsJournal module`

---

### Task 1.2: `pypi_libs_archiver.py` — Main archiver module

**File:** `pypi_libs_archiver.py` (new)
**Test:** `tests/test_pypi_libs_archiver.py` (Task 2.2)
**Depends:** none (imports `pypi_api`, `browser_max`, `pypi_libs_journal`)

**Justification:** Following the `MediaArchiver` pattern — standalone module with its own `_load_config()`, `_init_browser()`, `_ensure_browser_connected()`, `run()`. Lazy-imported in `github_archiver.py`.

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyPI Libraries Archiver — Топ Python библиотек в MAX канал

Самостоятельный скрипт для загрузки топ N Python-библиотек
и их публикации в отдельный канал MAX.
Следует тому же паттерну, что MediaArchiver (media_archiver.py).
"""

import os
import sys
import time
import yaml
import atexit
import signal
import shutil
from datetime import datetime
from dotenv import load_dotenv
from logging_config import setup_logging, LogMixin, SessionCapture

from pypi_api import PyPIAPI
from browser_max import BrowserMAX
from pypi_libs_journal import PyPILibsJournal


class PyPILibsArchiver(LogMixin):
    """Архиватор топ Python библиотек в MAX канал"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.pypi = PyPIAPI()
        self.journal = PyPILibsJournal("pypi_libs_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        # Register cleanup handlers
        atexit.register(self._cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self._shutdown = True

    def _cleanup(self):
        """Clean up resources on exit"""
        # Clean up pypi_api temp directory — this is where download_package() puts files
        try:
            if os.path.exists("./temp_pypi"):
                shutil.rmtree("./temp_pypi")
                self.logger.info("Cleaned up ./temp_pypi/")
        except Exception as e:
            self.logger.warning(f"Failed to clean ./temp_pypi/: {e}")
        # Also clean configured output dir if different
        output_dir = self.config.get('pypi_libs_archiver', {}).get('output_dir', '')
        if output_dir and os.path.exists(output_dir) and output_dir != "./temp_pypi":
            try:
                shutil.rmtree(output_dir)
                self.logger.info(f"Cleaned up {output_dir}")
            except Exception as e:
                self.logger.warning(f"Failed to clean {output_dir}: {e}")
        # Close browser
        if self.browser:
            try:
                self.journal.save()
                self.browser.close()
            except Exception:
                pass

    def _load_config(self, config_path: str) -> dict:
        """Загрузить конфигурацию (.env + config.yaml)"""
        load_dotenv()
        config = {}

        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

        # PYPI_LIBS_CHANNEL_URL: env var > config.yaml
        channel_url = os.environ.get('PYPI_LIBS_CHANNEL_URL', '')
        if not channel_url:
            channel_url = config.get('pypi_libs', {}).get('channel_url', '')

        if not channel_url:
            print("✗ PYPI_LIBS_CHANNEL_URL не указан.")
            print("  Укажите в .env файле или переменной окружения")
            sys.exit(1)

        config.setdefault('pypi_libs', {})['channel_url'] = channel_url

        # Ensure output dir exists
        output_dir = config.get('pypi_libs_archiver', {}).get('output_dir', './temp_pypi_libs')
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        return config

    def _init_browser(self) -> BrowserMAX:
        """Инициализировать браузер MAX (реиспользует соединение, если живо)"""
        if self.browser is None:
            channel_url = self.config.get('pypi_libs', {}).get('channel_url', '')
            use_local = self.config.get('pypi_libs_archiver', {}).get(
                'use_local_browser',
                self.config.get('archiver', {}).get('use_local_browser', False)
            )
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

    # ── Formatting helpers ──

    @staticmethod
    def _format_downloads(count: int) -> str:
        """Форматировать количество загрузок"""
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B"
        elif count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    @staticmethod
    def _format_file_size(size_bytes: int) -> str:
        """Форматировать размер файла"""
        if size_bytes >= 1024 * 1024 * 1024:
            return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"
        elif size_bytes >= 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        elif size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} B"

    def _build_message_text(self, pkg_data: dict, file_sizes: list[int]) -> str:
        """
        Построить текст сообщения для MAX.

        Args:
            pkg_data: Словарь с данными пакета
                - name: str
                - latest_version: str
                - summary: str
                - downloads: int (загрузки за 365 дней)
                - license: str
            file_sizes: Список размеров файлов в байтах

        Returns:
            Отформатированный текст сообщения
        """
        name = pkg_data.get('name', '')
        version = pkg_data.get('latest_version', '')
        summary = pkg_data.get('summary', '') or 'Без описания'
        downloads = pkg_data.get('downloads', 0)
        license_str = pkg_data.get('license', '') or 'Unknown'
        pypi_url = f"https://pypi.org/project/{name}/"

        text = (
            f"🐍 {name} {version}\n\n"
            f"📝 {summary}\n\n"
            f"📥 Загрузки: {self._format_downloads(downloads)}\n"
            f"📜 Лицензия: {license_str}\n"
            f"🔗 PyPI: {pypi_url}"
        )

        if file_sizes:
            for i, size in enumerate(file_sizes):
                text += f"\n📦 Файл {i + 1}: {self._format_file_size(size)}"

        return text

    @staticmethod
    def _print_progress(current: int, total: int, sent: int, skipped: int,
                        status: str = ""):
        """Print progress bar for load operations"""
        if total == 0:
            return
        pct = int(current / total * 100)
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        print(f"\r  Прогресс: {current}/{total} | {bar} {pct}% | "
              f"✓{sent} | –{skipped} {status}",
              end="", flush=True)
        if current >= total:
            print()

    # ── Main operations ──

    def load_top_libraries(self, limit: int | None = None):
        """
        Загрузить топ N Python библиотек в MAX канал.

        Flow:
        1. Hugovk датасет → топ пакеты
        2. Фильтр по журналу (пропуск уже отправленных)
        3. Для каждого: get_package_info → download_package →
           build_message → send_message_with_files → journal.add()
        4. Очистка временных файлов после каждого пакета
        """
        if limit is None:
            limit = self.config.get('pypi_libs_archiver', {}).get('limit', 20)
        retries = self.config.get('pypi_libs_archiver', {}).get(
            'retries', self.config.get('archiver', {}).get('retries', 3))
        retry_delay = self.config.get('pypi_libs_archiver', {}).get(
            'retry_delay', self.config.get('archiver', {}).get('retry_delay', 10))

        print("\n" + "═" * 60)
        print("          Загрузка топ Python библиотек")
        print("═" * 60)

        print(f"\n  Получаю топ-{limit} библиотек из Hugovk датасета...")

        try:
            top_packages = self.pypi.fetch_top_packages(limit)
        except Exception as e:
            print(f"\n  ✗ Ошибка получения датасета: {e}")
            print("  Попробуйте повторить позже.")
            input("\n  Нажмите Enter для возврата...")
            return

        if not top_packages:
            print("\n  ✗ Не удалось получить список библиотек")
            input("\n  Нажмите Enter для возврата...")
            return

        print(f"  ✓ Получено {len(top_packages)} библиотек")

        # Фильтр: пропускаем уже отправленные (по name + version)
        packages_to_process = []
        skipped_in_journal = 0
        for pkg in top_packages:
            name = pkg.get('name', '')
            version = pkg.get('latest_version', '')
            if not name or not version:
                continue
            if self.journal.exists(name, version):
                skipped_in_journal += 1
                continue
            packages_to_process.append(pkg)

        print(f"  Уже в журнале: {skipped_in_journal}")
        print(f"  Осталось для загрузки: {len(packages_to_process)}\n")

        if not packages_to_process:
            print("  ✓ Все библиотеки уже отправлены!")
            input("\n  Нажмите Enter для возврата...")
            return

        # Подключаемся к MAX
        browser = None
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            return

        # Обрабатываем каждый пакет
        sent_count = 0
        error_count = 0
        total = len(packages_to_process)

        for i, pkg in enumerate(packages_to_process, 1):
            if self._shutdown:
                print(f"\n  ⚠ Прервано после {i - 1} пакетов")
                break

            name = pkg.get('name', '')
            version = pkg.get('latest_version', '')
            downloads = int(pkg.get('downloads_last_365_days', 0))

            print(f"\n  {'═' * 56}")
            print(f"  #{i}/{total} | {name} {version}")
            print(f"  {'─' * 56}")

            # Получаем детальную информацию
            try:
                info = self.pypi.get_package_info(name)
            except Exception as e:
                print(f"  ✗ Ошибка получения информации: {e}")
                self.journal.mark_failed(name, version, str(e))
                error_count += 1
                self._print_progress(i, total, sent_count, error_count, "✗")
                continue

            latest_version = info.get('latest_version', version)
            summary = info.get('info', {}).get('summary', '')
            license_str = info.get('info', {}).get('license', 'Unknown')

            # Скачиваем файлы
            print(f"  ↓ Скачиваю файлы...")
            try:
                file_paths = self.pypi.download_package(name)
            except ValueError as e:
                print(f"  ✗ Файлы не найдены: {e}")
                self.journal.mark_failed(name, latest_version, summary, downloads)
                error_count += 1
                self._print_progress(i, total, sent_count, error_count, "✗")
                continue
            except Exception as e:
                print(f"  ✗ Ошибка скачивания: {e}")
                self.journal.mark_failed(name, latest_version, summary, downloads)
                error_count += 1
                self._print_progress(i, total, sent_count, error_count, "✗")
                continue

            if not file_paths:
                print(f"  ⚠ Файлы не найдены для {name}")
                self.journal.mark_failed(name, latest_version, summary, downloads)
                error_count += 1
                continue

            # Показываем размеры файлов
            file_sizes = []
            for fp in file_paths:
                size = os.path.getsize(fp)
                file_sizes.append(size)
                print(f"    ✓ {os.path.basename(fp)} ({self._format_file_size(size)})")

            # Формируем текст сообщения
            pkg_data = {
                "name": name,
                "latest_version": latest_version,
                "summary": summary,
                "downloads": downloads,
                "license": license_str,
            }
            text = self._build_message_text(pkg_data, file_sizes)

            # Отправляем в MAX
            print(f"  → Отправляю в MAX...")
            try:
                success, _ = browser.send_message_with_files(
                    text=text,
                    filepaths=file_paths,
                    retries=retries,
                    retry_delay=retry_delay,
                    expected_extensions=['.tar.gz', '.whl']
                )
            except Exception as e:
                print(f"  ✗ Ошибка отправки: {e}")
                success = False

            # Обновляем журнал
            if success:
                filenames = [os.path.basename(fp) for fp in file_paths]
                self.journal.add(name, latest_version, summary, downloads, filenames)
                sent_count += 1
                print(f"  ✓ Отправлено")
            else:
                self.journal.mark_failed(name, latest_version, summary, downloads)
                error_count += 1
                print(f"  ✗ Ошибка загрузки в MAX")

            # Удаляем временные файлы после отправки
            for fp in file_paths:
                try:
                    if os.path.exists(fp):
                        os.remove(fp)
                except Exception as e:
                    self.logger.warning(f"Failed to remove {fp}: {e}")
            # Удаляем пустую директорию пакета
            pkg_dir = os.path.dirname(file_paths[0]) if file_paths else ''
            if pkg_dir and os.path.exists(pkg_dir):
                try:
                    if not os.listdir(pkg_dir):
                        os.rmdir(pkg_dir)
                except Exception:
                    pass

            self._print_progress(i, total, sent_count, error_count,
                                 "✓" if success else "✗")

        # Итог
        print()
        print("\n" + "═" * 60)
        print("Загрузка завершена")
        print(f"  Всего: {sent_count + error_count}")
        print(f"  Отправлено: {sent_count}")
        if error_count:
            print(f"  Ошибок: {error_count}")
        print("═" * 60)

        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass

        input("\n  Нажмите Enter для возврата в меню...")

    def sync_libraries(self):
        """
        Синхронизировать версии отправленных библиотек.

        Flow:
        1. Загрузить все записи из journal
        2. Для каждой: проверить latest_version на PyPI
        3. Показать таблицу изменений
        4. По подтверждению: download + send + journal.update()
        """
        print("\n" + "═" * 60)
        print("          Синхронизация версий библиотек")
        print("═" * 60)

        entries = self.journal.get_all()
        if not entries:
            print("\n  ⚠ Журнал пуст. Сначала загрузите библиотеки (пункт 11).")
            input("\n  Нажмите Enter для возврата...")
            return

        print(f"\n  Проверяю {len(entries)} библиотек на наличие новых версий...\n")

        # Phase 1: Проверяем все библиотеки
        updates = []
        checked = 0
        for entry in entries:
            name = entry.get('name', '')
            saved_version = entry.get('version', '')
            try:
                info = self.pypi.get_package_info(name)
                latest = info.get('latest_version', '')
            except Exception as e:
                print(f"  ✗ {name}: ошибка {e}")
                continue

            if latest and latest != saved_version:
                updates.append((entry, saved_version, latest))

            checked += 1
            pct = int(checked / len(entries) * 100)
            print(f"\r  Проверка: {checked}/{len(entries)} ({pct}%) | "
                  f"Обновлений: {len(updates)}",
                  end="", flush=True)

        print()

        if not updates:
            print("\n  ✓ Все библиотеки актуальны!")
            input("\n  Нажмите Enter для возврата...")
            return

        # Phase 2: Показываем таблицу
        print()
        print("  " + "─" * 74)
        print(f"  {'#':<4} {'Библиотека':<35} {'Было':<20} {'Стало':<20}")
        print("  " + "─" * 74)
        for i, (entry, old_ver, new_ver) in enumerate(updates, 1):
            name_display = entry.get('name', '')[:33]
            print(f"  {i:<4} {name_display:<35} {old_ver:<20} {new_ver:<20}")
        print("  " + "─" * 74)

        # Phase 3: Спрашиваем пользователя
        print("\n  [Enter] Обновить ВСЕ")
        print("  [S] Пропустить")
        try:
            choice = input("\n  Ваш выбор [Enter/S]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            return

        if choice == 's':
            print("\n  Синхронизация отменена.")
            return

        # Phase 4: Обновляем
        retries = self.config.get('pypi_libs_archiver', {}).get(
            'retries', self.config.get('archiver', {}).get('retries', 3))
        retry_delay = self.config.get('pypi_libs_archiver', {}).get(
            'retry_delay', self.config.get('archiver', {}).get('retry_delay', 10))

        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            return

        updated_count = 0
        error_count = 0

        for i, (entry, old_ver, new_ver) in enumerate(updates, 1):
            if self._shutdown:
                print(f"\n  ⚠ Прервано после {i - 1} обновлений")
                break

            name = entry.get('name', '')
            print(f"\n  [{i}/{len(updates)}] {name}: {old_ver} → {new_ver}")

            # Скачиваем новую версию
            try:
                file_paths = self.pypi.download_package(name)
            except Exception as e:
                print(f"  ✗ Ошибка скачивания: {e}")
                error_count += 1
                continue

            if not file_paths:
                print(f"  ⚠ Файлы не найдены")
                error_count += 1
                continue

            file_sizes = [os.path.getsize(fp) for fp in file_paths]

            # Формируем сообщение
            pkg_data = {
                "name": name,
                "latest_version": new_ver,
                "summary": entry.get('description', ''),
                "downloads": entry.get('downloads', 0),
                "license": '',
            }
            text = self._build_message_text(pkg_data, file_sizes)

            # Отправляем
            try:
                success, _ = browser.send_message_with_files(
                    text=text,
                    filepaths=file_paths,
                    retries=retries,
                    retry_delay=retry_delay,
                    expected_extensions=['.tar.gz', '.whl']
                )
            except Exception as e:
                print(f"  ✗ Ошибка отправки: {e}")
                success = False

            if success:
                filenames = [os.path.basename(fp) for fp in file_paths]
                self.journal.add(name, new_ver,
                                 entry.get('description', ''),
                                 entry.get('downloads', 0),
                                 filenames)
                updated_count += 1
                print(f"  ✓ Обновлено")
            else:
                error_count += 1
                print(f"  ✗ Ошибка")

            # Очистка
            for fp in file_paths:
                try:
                    if os.path.exists(fp):
                        os.remove(fp)
                except Exception:
                    pass

        print()
        print("\n" + "═" * 60)
        print("Синхронизация завершена")
        print(f"  Обновлено: {updated_count}")
        if error_count:
            print(f"  Ошибок: {error_count}")
        print("═" * 60)

        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass

        input("\n  Нажмите Enter для возврата в меню...")

    def run(self):
        """
        Основной метод — запуск архивации через меню.
        Показывает статистику журнала и предлагает выбор режима.
        """
        stats = self.journal.get_stats()

        print("\n" + "=" * 60)
        print("           PyPI Libraries Archiver")
        print("           Загрузка Python библиотек в MAX")
        print("=" * 60)
        print(f"  Журнал: {stats['total']} библиотек "
              f"({stats['sent']} отправлено, {stats['failed']} ошибок)")
        print("-" * 60)

        print("\n  [1] Загрузить топ библиотек")
        print("  [2] Синхронизировать версии")
        print("  [3] Выход")
        print()

        choice = input("  Ваш выбор [1/2/3]: ").strip()

        if choice == '1':
            self.load_top_libraries()
        elif choice == '2':
            self.sync_libraries()
        else:
            print("  Выход.")


def main():
    """Точка входа для самостоятельного запуска"""
    session = SessionCapture()
    session.start()
    print(f"📋 Session log: {session.path}")

    setup_logging()
    try:
        archiver = PyPILibsArchiver("config.yaml")
        archiver.run()
    finally:
        session.stop()


if __name__ == "__main__":
    main()
```

**Verify:** `python -c "from pypi_libs_archiver import PyPILibsArchiver; print('OK')"`
**Commit:** `feat(pypi-libs): add PyPILibsArchiver module`

---

### Task 1.3: `config.yaml` — Rename pypi sections to pypi_libs

**File:** `config.yaml`
**Test:** none (config file)
**Depends:** none

**Decision:** The existing config has `pypi:` and `pypi_archiver:` sections (added as stubs). The design uses `pypi_libs:` and `pypi_libs_archiver:`. Renaming to match the design. The `output_dir` value is documentation primarily — actual dl's go through `pypi_api._get_output_dir()`. The archiver's `_cleanup` removes both `./temp_pypi/` and `./temp_pypi_libs/`.

**Edit instructions:**

Replace lines 16-23 (the existing `pypi:` and `pypi_archiver:` sections) with:

```yaml
pypi_libs:               # PyPI libs channel configuration
  channel_url: ""        # Из .env PYPI_LIBS_CHANNEL_URL

pypi_libs_archiver:      # PyPI libs archiver settings
  limit: 20              # Top N библиотек
  output_dir: "./temp_pypi_libs"  # Временная папка (документация — pypi_api использует ./temp_pypi)
  retries: 3             # Повторы при ошибке загрузки в MAX
  retry_delay: 10        # Пауза между повторами (сек)
```

The full edited section (lines 16-23) should become:
```yaml
pypi_libs:               # PyPI libs channel configuration
  channel_url: ""        # Из .env PYPI_LIBS_CHANNEL_URL

pypi_libs_archiver:      # PyPI libs archiver settings
  limit: 20              # Top N библиотек
  output_dir: "./temp_pypi_libs"  # Временная папка (документация — pypi_api использует ./temp_pypi)
  retries: 3             # Повторы при ошибке загрузки в MAX
  retry_delay: 10        # Пауза между повторами (сек)
```

**Verify:** `python -c "import yaml; c=yaml.safe_load(open('config.yaml')); print('pypi_libs' in c, 'pypi_libs_archiver' in c)"`
**Commit:** `feat(config): rename pypi sections to pypi_libs`

---

### Task 1.4: `.env.example` — Add PYPI_LIBS_CHANNEL_URL

**File:** `.env.example`
**Test:** none (env template)
**Depends:** none

**Edit instructions:** Append the following lines after line 9 (after `MEDIA_WATCH_DIR=`):

```env
# PyPI libraries archiver
PYPI_LIBS_CHANNEL_URL=
```

The file should look like:
```env
# GitHub
GITHUB_TOKEN=

# MAX
MAX_CHANNEL_URL=

# Media archiver
MEDIA_CHANNEL_URL=
MEDIA_WATCH_DIR=

# PyPI libraries archiver
PYPI_LIBS_CHANNEL_URL=
```

**Verify:** `grep -c "PYPI_LIBS_CHANNEL_URL" .env.example` should output `1`
**Commit:** `feat(env): add PYPI_LIBS_CHANNEL_URL template`

---

## Batch 2: Tests (parallel — 2 implementers)

All tasks in this batch depend on Batch 1 completing (files from 1.1 and 1.2 must exist).

---

### Task 2.1: `tests/test_pypi_libs_journal.py` — Journal tests

**File:** `tests/test_pypi_libs_journal.py` (new)
**Depends:** 1.1 (`pypi_libs_journal.py` exists)

**Justification:** Following the same testing pattern as `test_pypi_api.py` — pytest with test classes, mocking file I/O.

```python
"""
Unit tests for PyPILibsJournal class.

Tests cover:
- Initialization and empty journal structure
- add() new library entry
- add() deduplication — same (name, version) blocked
- exists() check
- get() latest entry by name
- get_all() returns all entries
- get_stats() counters
- update() existing entry
- mark_failed() adds failed entry
- clear() resets journal
- Corrupted JSON recovery
"""

import json
import os
import time
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock


class TestPyPILibsJournalInit:
    """Test journal initialization"""

    def test_init_creates_empty(self, tmp_path):
        """Test new journal creates empty structure"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        assert journal.data == {"libraries": []}

    def test_init_loads_existing(self, tmp_path):
        """Test init loads existing journal file"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        # Pre-create journal
        existing = {"libraries": [
            {"name": "requests", "version": "2.31.0", "status": "sent"}
        ]}
        with open(journal_path, 'w', encoding='utf-8') as f:
            json.dump(existing, f)

        journal = PyPILibsJournal(journal_path)
        assert len(journal.data["libraries"]) == 1
        assert journal.data["libraries"][0]["name"] == "requests"

    def test_init_handles_corrupted_json(self, tmp_path):
        """Test init handles corrupted JSON by creating backup"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        with open(journal_path, 'w', encoding='utf-8') as f:
            f.write("not valid json{{{}")

        journal = PyPILibsJournal(journal_path)
        assert journal.data == {"libraries": []}
        # Backup should exist
        assert os.path.exists(journal_path + ".backup")

    def test_logger_property(self):
        """Test logger property returns correct logger"""
        from pypi_libs_journal import PyPILibsJournal
        journal = PyPILibsJournal("test_logger_journal.json")
        assert journal.logger.name == "gitax"
        journal.clear()
        os.remove("test_logger_journal.json")


class TestPyPILibsJournalAdd:
    """Test add() method"""

    def test_add_new_entry(self, tmp_path):
        """Test adding a new library entry"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        result = journal.add(
            name="requests",
            version="2.31.0",
            description="HTTP for humans",
            downloads=982742658,
            files=["requests-2.31.0.tar.gz", "requests-2.31.0-py3-none-any.whl"]
        )
        assert result is True
        assert len(journal.data["libraries"]) == 1
        assert journal.data["libraries"][0]["name"] == "requests"
        assert journal.data["libraries"][0]["version"] == "2.31.0"
        assert journal.data["libraries"][0]["status"] == "sent"
        assert "sent_at" in journal.data["libraries"][0]

    def test_add_duplicate_blocked(self, tmp_path):
        """Test adding same (name, version) is blocked"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        journal.add("requests", "2.31.0", "desc", 100, ["file.tar.gz"])
        result = journal.add("requests", "2.31.0", "desc", 100, ["file.tar.gz"])
        assert result is False
        assert len(journal.data["libraries"]) == 1

    def test_add_same_name_different_version(self, tmp_path):
        """Test adding same name but different version is allowed"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        journal.add("requests", "2.31.0", "desc", 100, ["file.tar.gz"])
        result = journal.add("requests", "3.0.0", "desc", 200, ["file.tar.gz"])
        assert result is True
        assert len(journal.data["libraries"]) == 2


class TestPyPILibsJournalExists:
    """Test exists() method"""

    def test_exists_returns_true(self, tmp_path):
        """Test exists returns True for existing entry"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.31.0", "desc", 100, [])

        assert journal.exists("requests", "2.31.0") is True

    def test_exists_returns_false(self, tmp_path):
        """Test exists returns False for missing entry"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        assert journal.exists("nonexistent", "1.0.0") is False
        assert journal.exists("requests", "9.9.9") is False


class TestPyPILibsJournalGet:
    """Test get() method"""

    def test_get_latest_version(self, tmp_path):
        """Test get returns latest version of a library"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.30.0", "desc", 100, [])
        journal.add("requests", "2.31.0", "desc", 200, [])

        result = journal.get("requests")
        assert result is not None
        assert result["version"] == "2.31.0"

    def test_get_returns_none_for_missing(self, tmp_path):
        """Test get returns None for unknown library"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        assert journal.get("nonexistent") is None


class TestPyPILibsJournalStats:
    """Test get_stats() and get_count() methods"""

    def test_get_stats_empty(self, tmp_path):
        """Test stats for empty journal"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        stats = journal.get_stats()
        assert stats["total"] == 0
        assert stats["sent"] == 0
        assert stats["failed"] == 0

    def test_get_stats_with_entries(self, tmp_path):
        """Test stats with mixed entries"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.31.0", "desc", 100, [])
        journal.mark_failed("bad-pkg", "1.0.0", "error")

        stats = journal.get_stats()
        assert stats["total"] == 2
        assert stats["sent"] == 1
        assert stats["failed"] == 1

    def test_get_count(self, tmp_path):
        """Test get_count returns correct count"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        assert journal.get_count() == 0
        journal.add("a", "1", "", 0, [])
        assert journal.get_count() == 1
        journal.add("b", "2", "", 0, [])
        assert journal.get_count() == 2

    def test_get_all(self, tmp_path):
        """Test get_all returns all entries"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("a", "1", "", 0, [])
        journal.add("b", "2", "", 0, [])
        all_entries = journal.get_all()
        assert len(all_entries) == 2


class TestPyPILibsJournalUpdate:
    """Test update() method"""

    def test_update_existing(self, tmp_path):
        """Test updating an existing entry"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.31.0", "desc", 100, [])

        result = journal.update("requests", "2.31.0", {"status": "updated"})
        assert result is True
        entry = journal.get("requests")
        assert entry["status"] == "updated"
        assert "updated_at" in entry

    def test_update_missing(self, tmp_path):
        """Test updating a non-existent entry returns False"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        result = journal.update("nonexistent", "1.0", {"status": "x"})
        assert result is False


class TestPyPILibsJournalClear:
    """Test clear() method"""

    def test_clear(self, tmp_path):
        """Test clear resets journal"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.31.0", "desc", 100, [])

        journal.clear()
        assert journal.data == {"libraries": []}
        assert journal.get_count() == 0


class TestPyPILibsJournalMarkFailed:
    """Test mark_failed() method"""

    def test_mark_failed(self, tmp_path):
        """Test marking a package as failed"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.mark_failed("broken-pkg", "0.1", "Download error")

        assert journal.exists("broken-pkg", "0.1")
        entry = journal.get("broken-pkg")
        assert entry["status"] == "failed"
        assert entry["files"] == []
```

**Verify:** `python -m pytest tests/test_pypi_libs_journal.py -v`
**Commit:** `test(pypi-libs): add PyPILibsJournal tests`

---

### Task 2.2: `tests/test_pypi_libs_archiver.py` — Archiver tests

**File:** `tests/test_pypi_libs_archiver.py` (new)
**Depends:** 1.1 (`pypi_libs_journal.py`), 1.2 (`pypi_libs_archiver.py`)

**Justification:** Testing `_build_message_text()` formatting with various inputs, and verifying that the archiver handles missing config gracefully.

```python
"""
Unit tests for PyPILibsArchiver class.

Tests cover:
- _build_message_text() formatting
- _format_downloads() helper
- _format_file_size() helper
- Config validation (missing channel_url)
- Journal integration (dedup check in load path)
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock


class TestFormatDownloads:
    """Test _format_downloads static method"""

    def test_format_downloads_billions(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_downloads(1_500_000_000) == "1.5B"
        assert PyPILibsArchiver._format_downloads(10_000_000_000) == "10.0B"

    def test_format_downloads_millions(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_downloads(1_500_000) == "1.5M"
        assert PyPILibsArchiver._format_downloads(982_742_658) == "982.7M"

    def test_format_downloads_thousands(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_downloads(1_500) == "1.5K"
        assert PyPILibsArchiver._format_downloads(999) == "999"

    def test_format_downloads_small(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_downloads(0) == "0"
        assert PyPILibsArchiver._format_downloads(42) == "42"
        assert PyPILibsArchiver._format_downloads(999) == "999"


class TestFormatFileSize:
    """Test _format_file_size static method"""

    def test_format_file_size_gb(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert "GB" in PyPILibsArchiver._format_file_size(2_000_000_000)

    def test_format_file_size_mb(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_file_size(1_048_576) == "1.0 MB"
        assert "MB" in PyPILibsArchiver._format_file_size(50_000_000)

    def test_format_file_size_kb(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert "KB" in PyPILibsArchiver._format_file_size(1_024)
        assert PyPILibsArchiver._format_file_size(500) == "500 B"

    def test_format_file_size_bytes(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_file_size(0) == "0 B"
        assert PyPILibsArchiver._format_file_size(100) == "100 B"


class TestBuildMessageText:
    """Test _build_message_text() — the core message formatting"""

    def test_basic_message(self):
        """Test basic message with all fields"""
        from pypi_libs_archiver import PyPILibsArchiver
        archiver = PyPILibsArchiver.__new__(PyPILibsArchiver)

        pkg_data = {
            "name": "requests",
            "latest_version": "2.31.0",
            "summary": "Python HTTP for Humans.",
            "downloads": 982742658,
            "license": "Apache-2.0",
        }
        text = archiver._build_message_text(pkg_data, [])

        assert "requests" in text
        assert "2.31.0" in text
        assert "Python HTTP for Humans." in text
        assert "982.7M" in text  # formatted downloads
        assert "Apache-2.0" in text
        assert "pypi.org/project/requests/" in text

    def test_message_with_file_sizes(self):
        """Test message includes file sizes when provided"""
        from pypi_libs_archiver import PyPILibsArchiver
        archiver = PyPILibsArchiver.__new__(PyPILibsArchiver)

        pkg_data = {
            "name": "requests",
            "latest_version": "2.31.0",
            "summary": "HTTP library",
            "downloads": 1000000,
            "license": "MIT",
        }
        text = archiver._build_message_text(pkg_data, [1_048_576, 512_000])

        assert "Файл 1" in text
        assert "Файл 2" in text
        assert "1.0 MB" in text
        assert "512.0 KB" in text

    def test_message_no_description(self):
        """Test message with missing description"""
        from pypi_libs_archiver import PyPILibsArchiver
        archiver = PyPILibsArchiver.__new__(PyPILibsArchiver)

        pkg_data = {
            "name": "unknown",
            "latest_version": "0.1",
            "summary": "",
            "downloads": 0,
            "license": "",
        }
        text = archiver._build_message_text(pkg_data, [])

        assert "unknown" in text
        assert "Без описания" in text

    def test_message_pypi_url(self):
        """Test PyPI URL is properly constructed"""
        from pypi_libs_archiver import PyPILibsArchiver
        archiver = PyPILibsArchiver.__new__(PyPILibsArchiver)

        pkg_data = {
            "name": "django",
            "latest_version": "5.0",
            "summary": "Web framework",
            "downloads": 50000000,
            "license": "BSD",
        }
        text = archiver._build_message_text(pkg_data, [])
        assert "https://pypi.org/project/django/" in text


class TestConfigValidation:
    """Test config loading with missing settings"""

    @patch('pypi_libs_archiver.load_dotenv')
    @patch('pypi_libs_archiver.os.path.exists')
    @patch('pypi_libs_archiver.yaml.safe_load')
    def test_missing_channel_url_exits(self, mock_yaml_load, mock_exists,
                                       mock_load_dotenv):
        """Test missing PYPI_LIBS_CHANNEL_URL causes exit"""
        from pypi_libs_archiver import PyPILibsArchiver

        # Simulate empty config with no pypi_libs section
        mock_exists.return_value = True
        mock_yaml_load.return_value = {}

        with pytest.raises(SystemExit):
            archiver = PyPILibsArchiver("config.yaml")

    @patch('pypi_libs_archiver.load_dotenv')
    @patch('pypi_libs_archiver.os.path.exists')
    @patch('pypi_libs_archiver.yaml.safe_load')
    @patch('pypi_libs_archiver.os.environ.get')
    def test_channel_url_from_env(self, mock_env_get, mock_yaml_load,
                                  mock_exists, mock_load_dotenv):
        """Test channel URL is read from env var"""
        from pypi_libs_archiver import PyPILibsArchiver

        mock_exists.return_value = True
        mock_yaml_load.return_value = {}
        mock_env_get.return_value = "https://web.max.ru/pypi-channel"

        archiver = PyPILibsArchiver("config.yaml")
        assert archiver.config['pypi_libs']['channel_url'] == "https://web.max.ru/pypi-channel"

    @patch('pypi_libs_archiver.load_dotenv')
    @patch('pypi_libs_archiver.os.path.exists')
    @patch('pypi_libs_archiver.yaml.safe_load')
    @patch('pypi_libs_archiver.os.environ.get')
    def test_channel_url_from_yaml(self, mock_env_get, mock_yaml_load,
                                   mock_exists, mock_load_dotenv):
        """Test channel URL fallback to config.yaml"""
        from pypi_libs_archiver import PyPILibsArchiver

        mock_exists.return_value = True
        mock_yaml_load.return_value = {
            "pypi_libs": {"channel_url": "https://web.max.ru/pypi-channel"}
        }
        mock_env_get.return_value = ""  # env var empty

        archiver = PyPILibsArchiver("config.yaml")
        assert archiver.config['pypi_libs']['channel_url'] == "https://web.max.ru/pypi-channel"


class TestJournalIntegration:
    """Test how archiver interacts with the journal"""

    def test_archiver_creates_journal(self):
        """Test archiver creates a PyPILibsJournal instance"""
        from pypi_libs_archiver import PyPILibsArchiver
        from pypi_libs_journal import PyPILibsJournal

        with patch('pypi_libs_archiver.PyPILibsArchiver._load_config') as mock_load:
            mock_load.return_value = {
                'pypi_libs': {'channel_url': 'https://example.com'},
                'pypi_libs_archiver': {'limit': 20}
            }
            archiver = PyPILibsArchiver()
            assert isinstance(archiver.journal, PyPILibsJournal)
            assert archiver.journal.file_path == "pypi_libs_journal.json"
            archiver.journal.clear()
            os.remove("pypi_libs_journal.json")
```

**Verify:** `python -m pytest tests/test_pypi_libs_archiver.py -v`
**Commit:** `test(pypi-libs): add PyPILibsArchiver tests`

---

## Batch 3: Integration (sequential — 1 implementer)

### Task 3.1: `github_archiver.py` — All integration changes

**File:** `github_archiver.py`
**Depends:** 1.1, 1.2, 1.3, 1.4 (new modules + config must exist)

This task makes 5 coordinated edits to the same file. They must be applied in order.

#### Edit 1: Import `PyPILibsJournal` at the top (after line 23)

Replace:
```python
from journal import Journal
from github_api import GitHubAPI
from browser_max import BrowserMAX
from scroll_registry import ScrollRegistry
```

With:
```python
from journal import Journal
from github_api import GitHubAPI
from browser_max import BrowserMAX
from scroll_registry import ScrollRegistry
from pypi_libs_journal import PyPILibsJournal
```

#### Edit 2: `_show_menu()` — Add items 11 and 12 (after line 359)

Replace:
```python
        print(f"  [3] Список игнорирования{ignored_str}")
        print("  [4] Аудит — очистка / восстановление публикаций")
        print("  [5] Экспорт всех сообщений в файл")
        print("  [6] Загрузить медиа из папки")
        print("  [7] Скачать все файлы из канала")
        print("  [8] Удалить все сообщения в ленте")
        print("  [9] Выход")
        print("  [10] Очистить журналы")
        print()
```

With:
```python
        print(f"  [3] Список игнорирования{ignored_str}")
        print("  [4] Аудит — очистка / восстановление публикаций")
        print("  [5] Экспорт всех сообщений в файл")
        print("  [6] Загрузить медиа из папки")
        print("  [7] Скачать все файлы из канала")
        print("  [8] Удалить все сообщения в ленте")
        print("  [9] Выход")
        print("  [10] Очистить журналы")
        print("  [11] Загрузить топ Python библиотек")
        print("  [12] Синхронизировать Python библиотеки")
        print()
```

#### Edit 3: `_manage_journals()` — Add pypi_libs journal display and clear options

Replace the `_manage_journals` method (lines 967-1043) with the updated version that includes pypi_libs journal:

Replace the section starting at:
```python
    def _manage_journals(self):
        """Управление очисткой журналов"""
        from media_archiver import MediaJournal
        from channel_downloader import DownloadJournal
```

With the updated method that includes `PyPILibsJournal` + renumbers options:

```python
    def _manage_journals(self):
        """Управление очисткой журналов"""
        from media_archiver import MediaJournal
        from channel_downloader import DownloadJournal

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

            print(f"\n  Текущее состояние журналов:")
            print(f"  [1] journal.json — {j_stats['total']} репозиториев "
                  f"({j_stats['sent']} отправлено, {j_stats['failed']} ошибок)")
            print(f"  [2] media_journal.json — {mj_stats['total']} файлов "
                  f"({mj_stats['sent']} отправлено, {mj_stats['failed']} ошибок)")
            print(f"  [3] download_journal.json — {dj_stats['total']} файлов "
                  f"({dj_stats['downloaded']} скачано, {dj_stats['failed']} ошибок)")
            print(f"  [4] pypi_libs_journal.json — {pj_stats['total']} библиотек "
                  f"({pj_stats['sent']} отправлено, {pj_stats['failed']} ошибок)")

            print()
            print("  [1] Очистить journal.json")
            print("  [2] Очистить media_journal.json")
            print("  [3] Очистить download_journal.json")
            print("  [4] Очистить pypi_libs_journal.json")
            print("  [5] Очистить ВСЕ журналы")
            print("  [0] Назад")
            print()

            choice = input("  Ваш выбор [0/1/2/3/4/5]: ").strip()

            if choice == '0':
                break

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
                print("\n  ⚠ ВНИМАНИЕ: Будут очищены ВСЕ журналы!")
                confirm = input("  Введите 'ДА' для подтверждения: ").strip().lower()
                if confirm in ('да', 'yes', 'дa'):
                    self.journal.clear()
                    MediaJournal("media_journal.json").clear()
                    DownloadJournal("download_journal.json").clear()
                    PyPILibsJournal("pypi_libs_journal.json").clear()
                    print("  ✓ Все журналы очищены")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")
```

#### Edit 4: `run()` — Add choices 11 and 12 handlers

Replace the `run()` method (lines 2036-2069) with:

```python
    def run(self):
        """Запустить главный цикл программы"""

        while True:
            os.system('cls' if os.name == 'nt' else 'clear')

            self._show_menu()

            choice = input("  Выберите действие [1-12]: ").strip()

            if choice == '1':
                self.sync_repositories()
            elif choice == '2':
                self.load_new_repositories()
            elif choice == '3':
                self._manage_ignore_list()
            elif choice == '4':
                self.audit_and_restore_publications()
            elif choice == '5':
                self.export_messages_to_file()
            elif choice == '6':
                self.run_media_archiver()
            elif choice == '7':
                self.download_channel_files()
            elif choice == '8':
                self.delete_all_messages_in_channel()
            elif choice == '9':
                print("\n  До свидания!\n")
                break
            elif choice == '10':
                self._manage_journals()
            elif choice == '11':
                self.run_pypi_libs_archiver()
            elif choice == '12':
                self.run_pypi_libs_sync()
            else:
                print("\n  Неверный выбор. Нажмите 1..12.")
                time.sleep(1)
```

#### Edit 5: Add `run_pypi_libs_archiver()` and `run_pypi_libs_sync()` methods

Insert these two new methods before the `main()` function (before `def main():` at line 2072):

```python
    def run_pypi_libs_archiver(self):
        """Загрузить топ Python библиотек в MAX канал"""
        from pypi_libs_archiver import PyPILibsArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ Python библиотек")
        print("═" * 60)

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

        try:
            archiver = PyPILibsArchiver("config.yaml")
            archiver.sync_libraries()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"PyPI libs sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")
```

#### Edit 6: `GracefulShutdown._cleanup_temp_files()` — Add pypi_libs temp dir cleanup

Replace the `_cleanup_temp_files` method (lines 59-96) to also clean the `./temp_pypi/` directory (where `pypi_api.download_package()` puts files):

In the `_cleanup_temp_files` method, after the existing patterns block (around line 74), add:

```python
        # Add PyPI temp files
        patterns.append(os.path.join("temp_pypi", "**", "*"))  # pypi libs downloads
```

And at the end of the method, add cleanup of the entire `./temp_pypi/` tree:

```python
        # Clean up entire pypi_api temp directory
        temp_pypi = os.path.join(os.getcwd(), "temp_pypi")
        if os.path.exists(temp_pypi):
            try:
                import shutil
                shutil.rmtree(temp_pypi)
                logger.info("Cleaned up ./temp_pypi/ directory")
            except Exception as e:
                logger.warning(f"Failed to clean ./temp_pypi/: {e}")
```

The full updated method should look like:

```python
    def _cleanup_temp_files(self):
        """Remove any remaining files in the temp directory"""
        import glob
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
```

**Verify:** `python -c "from github_archiver import GitHubArchiver; ga = GitHubArchiver.__new__(GitHubArchiver); ga._load_config = lambda x: {}; print('OK')"`
**Verify (menu):** `python -c "from github_archiver import GitHubArchiver; ga = GitHubArchiver.__new__(GitHubArchiver); ga.journal = None; ga._show_header = lambda: None; ga._show_menu = lambda: None; print('Menu OK')"`
**Verify (pypi methods):** `python -c "from github_archiver import GitHubArchiver; assert hasattr(GitHubArchiver, 'run_pypi_libs_archiver'); assert hasattr(GitHubArchiver, 'run_pypi_libs_sync'); print('Methods OK')"`
**Commit:** `feat(github-archiver): integrate PyPI libs archiver menu items 11-12`

---

## Verification Checklist

After all tasks complete:

```bash
# 1. Import checks
python -c "from pypi_libs_journal import PyPILibsJournal; print('journal OK')"
python -c "from pypi_libs_archiver import PyPILibsArchiver; print('archiver OK')"

# 2. Config check
python -c "import yaml; c=yaml.safe_load(open('config.yaml')); assert 'pypi_libs' in c; assert 'pypi_libs_archiver' in c; print('config OK')"

# 3. Env check
python -c "assert open('.env.example').read().count('PYPI_LIBS_CHANNEL_URL') == 1; print('env OK')"

# 4. Test suite
python -m pytest tests/test_pypi_libs_journal.py -v
python -m pytest tests/test_pypi_libs_archiver.py -v

# 5. GitHub archiver integration
python -c "from github_archiver import GitHubArchiver; assert hasattr(GitHubArchiver, 'run_pypi_libs_archiver'); assert hasattr(GitHubArchiver, 'run_pypi_libs_sync'); print('integration OK')"
```

## End-to-End Manual Test

1. Set `PYPI_LIBS_CHANNEL_URL` in `.env`
2. Run `python github_archiver.py`
3. Verify menu shows `[11] Загрузить топ Python библиотек` and `[12] Синхронизировать Python библиотеки`
4. Choose `[10] Очистить журналы` — verify pypi_libs_journal.json is listed
5. Choose `[11]` — verify it fetches top packages, shows progress, sends files
6. Choose `[11]` again — verify already-sent packages are skipped
7. Choose `[12]` — verify it checks for version updates
8. Verify `pypi_libs_journal.json` is created with sent entries
