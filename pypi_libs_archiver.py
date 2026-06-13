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
import atexit
import signal
import shutil
from datetime import datetime
from dotenv import load_dotenv
from logging_config import setup_logging, LogMixin, SessionCapture

from pypi_api import PyPIAPI
from browser_max import BrowserMAX
from browser_init import BrowserInitMixin
from pypi_libs_journal import PyPILibsJournal
from config_utils import get_split_mode
from progressbar import LiveProgressBar
from signal_handler import SignalHandler
from utils import format_file_size


class PyPILibsArchiver(LogMixin, BrowserInitMixin):
    """Архиватор топ Python библиотек в MAX канал"""

    _channel_key = "pypi"
    _section_key = None

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        # Ensure output dir exists (was in _load_config)
        output_dir = self.config.get('pypi_libs_archiver', {}).get('output_dir', './temp_pypi_libs')
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        self.pypi = PyPIAPI()
        self.journal = PyPILibsJournal("pypi_libs_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        # Register cleanup handlers
        SignalHandler().register(self, on_cleanup=self._cleanup)

    def _cleanup(self):
        """Clean up resources on exit"""
        # Close browser and save journal
        if self.browser:
            try:
                self.journal.save()
                self.browser.close()
            except Exception:
                pass

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
        text = (
            f"🐍 {name} {version}\n\n"
            f"📝 {summary}\n\n"
            f"📥 Загрузки: {self._format_downloads(downloads)}\n"
            f"📜 Лицензия: {license_str}"
        )

        if file_sizes:
            for i, size in enumerate(file_sizes):
                text += f"\n📦 Файл {i + 1}: {format_file_size(size)}"

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
        # В новом формате Hugovk версии нет — проверяем по имени
        packages_to_process = []
        skipped_in_journal = 0
        for pkg in top_packages:
            name = pkg.get('name', '')
            version = pkg.get('latest_version', '')
            if not name:
                continue
            # If version is available, check name+version; otherwise check name only
            if version:
                if self.journal.exists(name, version):
                    skipped_in_journal += 1
                    continue
            else:
                if self.journal.exists_by_name(name):
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

        with LiveProgressBar(total, "Загрузка PyPI пакетов") as bar:
            for i, pkg in enumerate(packages_to_process, 1):
                if self._shutdown:
                    print(f"\n  ⚠ Прервано после {i - 1} пакетов")
                    break

                name = pkg.get('name', '')
                version = pkg.get('latest_version', '')
                downloads = int(pkg.get('downloads_last_365_days', 0))

                bar.update(i, item_name=f"{name} {version}")
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
                    self._print_progress(i, total, sent_count, error_count, "✗")
                    continue

                # Показываем размеры файлов
                file_sizes = []
                for fp in file_paths:
                    size = os.path.getsize(fp)
                    file_sizes.append(size)
                    print(f"    ✓ {os.path.basename(fp)} ({format_file_size(size)})")

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
                    split_mode = get_split_mode(self.config, "pypi_libs_archiver", default="auto")
                    success, _ = browser.send_message_with_files(
                        text=text,
                        filepaths=file_paths,
                        retries=retries,
                        retry_delay=retry_delay,
                        split_mode=split_mode,
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

    def sync_runtimes(self):
        """Check and sync Python runtime installer if a newer version is available."""
        from datetime import datetime
        from runtime_api import RuntimeFactory, OSTarget

        runtime_cfg = self.config.get("runtime", {})
        if not runtime_cfg.get("enabled", True):
            self.logger.info("Runtime sync disabled in config")
            return

        runtime = RuntimeFactory.get_runtime("pypi")
        print(f"\n  {RuntimeFactory.get_icon('python')} Проверяю Python runtime...")

        latest = runtime.get_latest_version()
        if not latest:
            print("  ⚠ Не удалось получить версию Python. Пропуск.")
            return

        if not self.journal.should_update_runtime(latest):
            saved = self.journal.get_runtime_version()
            print(f"  ✓ Python {saved} — актуален")
            return

        print(f"  🆕 Python {latest} доступен (текущий: {self.journal.get_runtime_version() or 'не установлен'})")
        print("  Загрузка инсталляторов для всех ОС...")

        urls = runtime.get_download_urls(latest)
        os_targets = runtime_cfg.get("os_targets", ["windows", "macos", "linux"])
        urls = [u for u in urls if u["os"] in os_targets]

        browser = self._ensure_browser_connected()
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
                    message=f"Python {latest} — {os_name} installer\n\n{RuntimeFactory.get_download_page('python')}",
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
            print(f"\n  ✓ Python runtime {latest} обновлён в журнале")
        else:
            print("\n  ✗ Не удалось обновить runtime")

        self._close_browser()

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
                split_mode = get_split_mode(self.config, "pypi_libs_archiver", default="auto")
                success, _ = browser.send_message_with_files(
                    text=text,
                    filepaths=file_paths,
                    retries=retries,
                    retry_delay=retry_delay,
                    split_mode=split_mode,
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
    load_dotenv()
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
