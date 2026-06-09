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
        config.setdefault('pypi_libs_archiver', {})
        output_dir = config['pypi_libs_archiver'].get('output_dir', './temp_pypi_libs')
        config['pypi_libs_archiver']['output_dir'] = output_dir
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
                self._print_progress(i, total, sent_count, error_count, "✗")
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
