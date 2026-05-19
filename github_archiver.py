#!/usr/bin/env python3
"""
GitHub Archiver — Резервное копирование репозиториев в MAX Messenger

Главный скрипт с меню и основной логикой
"""

import os
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


class GracefulShutdown:
    """Context manager for graceful shutdown"""
    def __init__(self, archiver):
        self.archiver = archiver
        self.interrupted = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def cleanup(self):
        """Clean up resources on shutdown"""
        if self.interrupted:
            return

        self.interrupted = True
        if self.archiver.max_browser:
            try:
                self.archiver.max_browser.close()
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

        print(f"\n  ⚠ Found {len(orphaned)} orphaned 7z volume file(s):")
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
        size_str = f"\n📦 Размер: {self._format_file_size(zip_size)}" if zip_size else ""

        text = f"""📦 {repo_data.get('display_name', '')}

📝 {desc}
{size_str}
🔖 Версия: {repo_data.get('version', 'unknown')} ({repo_data.get('version_type', 'unknown')})
⭐ Звёзды: {self._format_stars(repo_data.get('stars', 0))}
🍴 Форки: {self._format_stars(repo_data.get('forks', 0))}
🔗 GitHub: {repo_data.get('github_url', '')}"""

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
        print("  [4] Выход")
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
                    'status': 'sent'
                })
                updated_count += 1
            else:
                self.journal.update_repository(full_name, {'status': 'failed'})
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
                # Репозиторий уже есть - проверить дату
                existing_updated = existing.get('updated_at', '')
                if existing_updated == updated_at:
                    # Та же версия (по дате) - пропускаем
                    skipped_already_sent += 1
                else:
                    # Версия изменилась - это обновление для sync_repositories
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

        # Скачать ZIP
        zip_path = self.github.download_zip(owner, repo_name, default_branch)  # type: ignore

        if not zip_path or not os.path.exists(zip_path):
            print("    ✗ Не удалось скачать ZIP")
            return False

        # Подготовить данные для сообщения
        text = self._build_message_text(repo_data)

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
                'status': 'sent'
            })
        else:
            self.journal.update_repository(full_name, {
                'status': 'failed'
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

    def run(self):
        """Запустить главный цикл программы"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')

            self._show_menu()

            choice = input("  Выберите действие [1-4]: ").strip()

            if choice == '1':
                self.sync_repositories()
            elif choice == '2':
                self.load_new_repositories()
            elif choice == '3':
                self._manage_ignore_list()
            elif choice == '4':
                print("\n  До свидания!\n")
                break
            else:
                print("\n  Неверный выбор. Нажмите 1, 2, 3 или 4.")
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