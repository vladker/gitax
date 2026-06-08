#!/usr/bin/env python3
"""
GitHub Archiver — Резервное копирование репозиториев в MAX Messenger

Главный скрипт с меню и основной логикой
"""

import os
import re
import sys
import yaml
import time
import shutil
import atexit
import signal
from datetime import datetime
from dotenv import load_dotenv
from logging_config import setup_logging

from journal import Journal
from github_api import GitHubAPI
from browser_max import BrowserMAX
from scroll_registry import ScrollRegistry


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
        """Clean up resources on shutdown"""
        if self.interrupted:
            return

        self.interrupted = True
        for browser in self.browsers:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass


class GitHubArchiver:
    """Главный класс программы"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.journal = Journal("journal.json")
        self.github = None
        self.max_browser = None

        # Проверить и создать временную папку
        output_dir = self.config.get('archiver', {}).get('output_dir', './temp')
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Check for orphaned 7z volumes from interrupted sessions
        self._check_orphaned_volumes(output_dir)

    def _check_orphaned_volumes(self, output_dir: str):
        """
        Check for orphaned 7z volume files from interrupted sessions.
        Offer to clean them up.

        Args:
            output_dir: Directory to check for orphaned volumes
        """
        import glob
        import logging
        logger = logging.getLogger("gitax")

        # Find all .7z.xxx files in temp directory
        pattern = os.path.join(output_dir, "*.7z.*")
        orphaned = sorted(glob.glob(pattern))

        if not orphaned:
            return

        print(f"\n  Found {len(orphaned)} orphaned 7z volume file(s):")
        for f in orphaned[:10]:  # Show first 10
            size_mb = os.path.getsize(f) / 1024 / 1024
            print(f"    - {os.path.basename(f)} ({size_mb:.1f} MB)")
        if len(orphaned) > 10:
            print(f"    ... and {len(orphaned) - 10} more")

        print("\n  These files are from interrupted upload sessions and should be cleaned up.")
        print("  [1] Delete all orphaned volumes")
        print("  [2] Keep for manual recovery")
        print("  [3] Don't ask again this session")

        try:
            choice = input("  Choose [1/2/3]: ").strip()
            if choice == '1':
                for f in orphaned:
                    try:
                        os.remove(f)
                        logger.info(f"Deleted orphaned: {f}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {f}: {e}")
                print(f"  ✓ Deleted {len(orphaned)} orphaned file(s)")
            elif choice == '3':
                print("  Will not ask again this session")
        except Exception as e:
            logger.warning(f"Orphaned volume check error: {e}")

    def _load_config(self, config_path: str) -> dict:
        """Загрузить конфигурацию (config.yaml опционален)"""
        load_dotenv()
        config = {}

        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

        # Приоритет: .env / env var > config.yaml
        env_token = os.environ.get('GITHUB_TOKEN')
        if env_token:
            config.setdefault('github', {})['token'] = env_token

        env_channel = os.environ.get('MAX_CHANNEL_URL')
        if env_channel:
            config.setdefault('max', {})['channel_url'] = env_channel

        if not config.get('github', {}).get('token'):
            print("✗ GitHub token не указан.")
            print("  Укажите GITHUB_TOKEN в .env файле или переменной окружения")
            sys.exit(1)

        return config

    def _init_github(self) -> GitHubAPI:
        """Инициализировать GitHub API"""
        if self.github is None:
            token = self.config['github']['token']
            output_dir = self.config.get('archiver', {}).get('output_dir', './temp')
            self.github = GitHubAPI(token, output_dir)
        return self.github

    def _init_max_browser(self) -> BrowserMAX:
        """Initialize MAX browser (reuses connection if alive)"""
        if self.max_browser is None:
            channel_url = self.config.get('max', {}).get('channel_url', '')
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

    def _format_file_size(self, size_bytes: int) -> str:
        """Форматировать размер файла"""
        if size_bytes >= 1024 * 1024 * 1024:
            return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"
        elif size_bytes >= 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        elif size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} B"

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
            text += f"\n📦 Размер: {self._format_file_size(zip_size)}"

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

    def _show_menu(self):
        """Показать главное меню"""
        self._show_header()
        ignored_count = self.journal.get_ignored_count()
        ignored_str = f" ({ignored_count} в игноре)" if ignored_count else ""
        print()
        print("  [1] Синхронизировать репозитории")
        print("  [2] Загрузить новые репозитории")
        print(f"  [3] Список игнорирования{ignored_str}")
        print("  [4] Аудит — очистка / восстановление публикаций")
        print("  [5] Экспорт всех сообщений в файл")
        print("  [6] Загрузить медиа из папки")
        print("  [7] Удалить все сообщения в ленте")
        print("  [8] Выход")
        print()

    def _get_user_choice(self, options: list, prompt: str = "Выберите действие") -> str:
        """Получить выбор пользователя"""
        while True:
            choice = input(f"  {prompt}: ").strip().lower()
            if choice in options:
                return choice
            print(f"  Неверный выбор. Доступно: {', '.join(options)}")

    def sync_repositories(self):
        """Синхронизация репозиториев - проверка обновлений"""
        print("\n" + "═" * 60)
        print("Синхронизация репозиториев")
        print("═" * 60)

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

        for i, repo in enumerate(repos, 1):
            full_name = repo.get('full_name', '')
            display_name = repo.get('display_name', '')
            saved_version = repo.get('version', '')
            default_branch = repo.get('default_branch', 'main')
            owner, repo_name = full_name.split('/', 1)

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

        # Phase 3: Интерактивное обновление
        print("\n  Выберите действие:")
        print("  [Enter] Обновить ВСЕ с новыми версиями")
        print("  [S] Пропустить синхронизацию")
        print()

        choice = input("  Ваш выбор [Enter/S]: ").strip().lower()

        if choice == 's':
            print("\n  Синхронизация отменена.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Обновляем ВСЕ с новыми версиями
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

        for i, (repo, has_new, latest_version) in enumerate(repo_updates, 1):
            if not has_new:
                skipped_count += 1
                continue

            full_name = repo.get('full_name', '')
            display_name = repo.get('display_name', '')
            saved_version = repo.get('version', '')
            default_branch = repo.get('default_branch', 'main')
            owner, repo_name = full_name.split('/', 1)
            stars = repo.get('stars', 0)
            forks = repo.get('forks', 0)
            desc = repo.get('description', '') or 'Без описания'

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
            zip_size_str = self._format_file_size(zip_size)
            print(f"    ✓ {zip_size_str}")

            text = self._build_message_text(repo_update, zip_size)

            print(f"    → Отправляю в MAX...")
            success, _ = browser.send_message_with_file(
                text=text,
                filepath=zip_path,
                retries=self.config.get('archiver', {}).get('retries', 3),
                retry_delay=self.config.get('archiver', {}).get('retry_delay', 10)
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
                try:
                    os.remove(zip_path)
                except Exception:
                    pass

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

    def load_new_repositories(self):
        """Загрузка новых репозиториев"""
        print("\n" + "═" * 60)
        print("Загрузка новых репозиториев")
        print("═" * 60)

        limit = self.config.get('archiver', {}).get('limit', 100)

        self._init_github()

        print(f"\n  Запрашиваю топ-{limit} репозиториев с GitHub...")

        try:
            top_repos = self.github.get_top_repositories(limit)
        except Exception as e:
            print(f"\n  ✗ Ошибка загрузки с GitHub: {e}")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if not top_repos:
            print("\n  ✗ Не удалось получить репозитории")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        print(f"  ✓ Получено {len(top_repos)} репозиториев")

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

        auto_load = False
        loaded_count = 0
        error_count = 0
        failed_names = []
        repo_delay = self.config.get('archiver', {}).get('repo_delay', 30)

        browser = None
        try:
            browser = self._ensure_max_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        for i, repo_info in enumerate(repos_to_process, 1):
            full_name = repo_info.get('full_name', '')
            display_name = repo_info.get('name', '')
            stars = repo_info.get('stargazers_count', 0)
            desc = repo_info.get('description', '') or 'Без описания'

            print(f"\n  {'═' * 56}")
            print(f"  #{i} из {len(repos_to_process)} | {display_name}")
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

        success, _ = browser.send_message_with_file(
            text=text,
            filepath=zip_path,
            retries=self.config.get('archiver', {}).get('retries', 3),
            retry_delay=self.config.get('archiver', {}).get('retry_delay', 10)
        )

        # Удалить временный файл
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
                print("    ✓ Временный файл удалён")
        except Exception as e:
            print(f"    ⚠ Не удалось удалить файл: {e}")

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

        print("    ↓ Скачиваю ZIP...")
        zip_path = self.github.download_zip(owner, repo_name, default_branch)

        if not zip_path or not os.path.exists(zip_path):
            print("    ✗ Не удалось скачать ZIP")
            return False

        zip_size = os.path.getsize(zip_path)
        zip_size_str = self._format_file_size(zip_size)
        print(f"    ✓ {zip_size_str}")

        text = self._build_message_text(repo_data, zip_size)

        print(f"    → Отправляю в MAX...")

        # Send message with file - returns (success, file_deletable)
        # Note: send_message_with_file confirms file message appears in chat before returning
        success, _ = browser.send_message_with_file(
            text=text,
            filepath=zip_path,
            retries=self.config.get('archiver', {}).get('retries', 3),
            retry_delay=self.config.get('archiver', {}).get('retry_delay', 10)
        )

        # If upload failed, log and move on
        if not success:
            print(f"    ⚠ Upload failed, skipping file cleanup")
            repo_data['status'] = 'failed'
            repo_data['version'] = repo_data.get('version', '') or 'unknown'
            repo_data['archive_size'] = zip_size
            self.journal.add_repository(repo_data)
            return False

        # Combined monitoring: wait for file to be deletable (message already confirmed by send_message_with_file)
        print(f"    ⏳ Waiting for file to be released...")
        wait_count = 0
        max_wait = 600  # Max 10 minutes

        while wait_count < max_wait:
            # Check: File deletable
            file_deletable = True
            if os.path.exists(zip_path):
                try:
                    with open(zip_path, 'rb') as f:
                        pass  # Read-only check is safer
                except (PermissionError, OSError):
                    file_deletable = False
                except Exception:
                    file_deletable = True  # Other errors, assume deletable

            # Log progress every 15 seconds
            if wait_count % 15 == 0 and wait_count > 0:
                status = "file_deletable" if file_deletable else "file_locked"
                print(f"    ⏳ Status: {status} ({wait_count}s)")

            # File deletable - we're done!
            if file_deletable:
                print(f"    ✓ Upload complete and file released ({wait_count}s)")
                break

            wait_count += 1
            time.sleep(1)

        # Delete file if possible
        removed = False
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
                print("    ✓ Temp file removed")
                removed = True
            except PermissionError:
                print(f"    ⚠ File still locked after {wait_count}s, will retry next cycle")
            except Exception as e:
                print(f"    ⚠ Failed to remove file: {e}")

        # If can't delete, move to backup
        if not removed and os.path.exists(zip_path):
            try:
                import hashlib
                lock_suffix = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
                locked_path = zip_path + f".locked_{lock_suffix}"
                os.rename(zip_path, locked_path)
                print(f"    ✓ File moved to: {os.path.basename(locked_path)}")
            except Exception as e2:
                print(f"    ⚠ Could not move locked file: {e2}")

        repo_data['status'] = 'sent' if success else 'failed'
        repo_data['version'] = repo_data.get('version', '') or 'unknown'
        repo_data['archive_size'] = zip_size
        self.journal.add_repository(repo_data)

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
            choice = input("  Ваш выбор [1/2/3]: ").strip()
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

            choice = input("  Ваш выбор [1/2/3]: ").strip()

            if choice == '1':
                try:
                    num = int(input("\n  Введите номер для удаления: ").strip())
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
        zip_size_str = self._format_file_size(zip_size)
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

        split_threshold_mb = self.config.get("archiver", {}).get("split_threshold_mb", 49)
        success, _ = browser.send_message_with_files(
            text=text,
            filepaths=[zip_path],
            retries=self.config.get("archiver", {}).get("retries", 3),
            retry_delay=self.config.get("archiver", {}).get("retry_delay", 10),
            split_threshold_mb=split_threshold_mb,
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

        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except Exception:
            pass

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
        zip_size_str = self._format_file_size(zip_size)
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

        split_threshold_mb = self.config.get("archiver", {}).get("split_threshold_mb", 49)
        success, _ = browser.send_message_with_files(
            text=text,
            filepaths=[zip_path],
            retries=self.config.get("archiver", {}).get("retries", 3),
            retry_delay=self.config.get("archiver", {}).get("retry_delay", 10),
            split_threshold_mb=split_threshold_mb,
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
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except Exception:
            pass

        return success and verified

    # ──────────────────────────────────────────────
    # Export All Messages to File
    # ──────────────────────────────────────────────

    def export_messages_to_file(self):
        """Export all messages from the MAX feed to a JSON/CSV file."""
        print("\n" + "═" * 60)
        print("          ЭКСПОРТ СООБЩЕНИЙ ИЗ ЛЕНТЫ")
        print("═" * 60)

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

        scroll_passes = int(passes_input) if passes_input else 3

        # Ask for max messages limit
        try:
            max_input = input("  Лимит сообщений (0 = без лимита) [0]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            if browser:
                browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        max_messages = int(max_input) if max_input else 0

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

        try:
            media = MediaArchiver("config.yaml")
            media.run()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Media archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run(self):
        """Запустить главный цикл программы"""

        while True:
            os.system('cls' if os.name == 'nt' else 'clear')

            self._show_menu()

            choice = input("  Выберите действие [1-8]: ").strip()

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
                self.delete_all_messages_in_channel()
            elif choice == '8':
                print("\n  До свидания!\n")
                break
            else:
                print("\n  Неверный выбор. Нажмите 1, 2, 3, 4, 5, 6, 7 или 8.")
                time.sleep(1)


def main():
    """Точка входа"""
    load_dotenv()
    logger = setup_logging(log_file="archiver.log", level=10)
    config_path = "config.yaml"

    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    archiver = None
    shutdown = None

    def signal_handler(signum, frame):
        logger.info("Received interrupt signal, shutting down gracefully...")
        if shutdown:
            shutdown.cleanup()
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


if __name__ == "__main__":
    main()