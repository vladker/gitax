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
from datetime import datetime

from journal import Journal
from github_api import GitHubAPI
from browser_max import BrowserMAX


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

    def _load_config(self, config_path: str) -> dict:
        """Загрузить конфигурацию"""
        if not os.path.exists(config_path):
            print(f"✗ Файл конфигурации не найден: {config_path}")
            print("  Создайте config.yaml на основе примера")
            sys.exit(1)

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # Проверить обязательные параметры
        if not config.get('github', {}).get('token'):
            print("✗ GitHub token не указан в config.yaml")
            print("  Добавьте: github.token: 'ghp_...'")
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
        """Инициализировать браузер MAX"""
        if self.max_browser is None:
            channel_url = self.config.get('max', {}).get('channel_url', '')
            self.max_browser = BrowserMAX(channel_url)
        return self.max_browser

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

    def _build_message_text(self, repo_data: dict) -> str:
        """Построить текст сообщения для MAX"""
        desc = self._format_description(repo_data.get('description', ''))

        text = f"""📦 {repo_data.get('display_name', '')}

📝 {desc}

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
        print()
        print("  [1] Синхронизировать репозитории")
        print("  [2] Загрузить новые репозитории")
        print("  [3] Выход")
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
        print(f"\n  Загружено {len(repos)} репозиториев из журнала")
        print("  Проверяю актуальные версии на GitHub...\n")

        # Флаг для автоматического режима
        auto_update = False
        updated_count = 0
        skipped_count = 0
        error_count = 0

        for i, repo in enumerate(repos, 1):
            full_name = repo.get('full_name', '')
            display_name = repo.get('display_name', '')
            saved_version = repo.get('version', '')
            default_branch = repo.get('default_branch', 'main')

            print(f"  {'─' * 56}")
            print(f"  Проверка: {display_name} ({full_name})")
            print(f"    📌 Сохранённая версия: {saved_version}")

            # Получить актуальную версию
            owner, repo_name = full_name.split('/', 1)

            try:
                has_new, latest_version = self.github.check_new_version(  # type: ignore
                    owner, repo_name, default_branch, saved_version
                )
            except Exception as e:
                print(f"    ✗ Ошибка проверки: {e}")
                error_count += 1
                continue

            print(f"    🔍 Актуальная версия: {latest_version}")

            if not has_new:
                print(f"    ✓ Версия актуальна, пропускаю")
                skipped_count += 1
                continue

            # Есть обновление - спросить пользователя
            if auto_update:
                choice = 'y'
            else:
                print()
                choice = input("    [Y] Обновить | [N] Пропустить | [A] Все | [S] Стоп: ").strip().lower()

                if choice == 'a':
                    auto_update = True
                    choice = 'y'

            if choice == 'y':
                # Обновить репозиторий
                print(f"\n    → Обновляю: {saved_version} → {latest_version}")

                # Подготовить данные для сообщения
                repo_update = dict(repo)
                repo_update['version'] = latest_version
                text = self._build_message_text(repo_update)

                # Скачать ZIP
                print("    ↓ Скачиваю ZIP...")
                zip_path = self.github.download_zip(owner, repo_name, default_branch)  # type: ignore

                if not zip_path or not os.path.exists(zip_path):
                    print("    ✗ Не удалось скачать ZIP")
                    error_count += 1
                    continue

                # Отправить в MAX
                browser = self._init_max_browser()  # type: ignore
                print("    → Отправляю в MAX...")

                success = browser.send_message_with_file(
                    text=text,
                    filepath=zip_path,
                    retries=self.config.get('archiver', {}).get('retries', 3),
                    retry_delay=self.config.get('archiver', {}).get('retry_delay', 10)
                )

                # Удалить временный файл
                if os.path.exists(zip_path):
                    try:
                        os.remove(zip_path)
                        print("    ✓ Временный файл удалён")
                    except Exception as e:
                        print(f"    ⚠ Не удалось удалить файл: {e}")

                # Обновить журнал
                if success:
                    self.journal.update_repository(full_name, {
                        'version': latest_version,
                        'status': 'sent'
                    })
                    updated_count += 1
                    print(f"    ✓ {display_name} обновлён")
                else:
                    self.journal.update_repository(full_name, {'status': 'failed'})
                    error_count += 1
                    print(f"    ✗ Ошибка обновления")

            elif choice == 's':
                print("\n  Останавливаю проверку...")
                break

            # Не 'n' явно пропускаем
            time.sleep(0.5)

        # Итоги
        print("\n" + "═" * 60)
        print("Синхронизация завершена")
        print(f"  Обновлено: {updated_count}")
        print(f"  Пропущено: {skipped_count}")
        print(f"  Ошибок: {error_count}")
        print("═" * 60)

        input("\n  Нажмите Enter для возврата в меню...")

    def load_new_repositories(self):
        """Загрузка новых репозиториев"""
        print("\n" + "═" * 60)
        print("Загрузка новых репозиториев")
        print("═" * 60)

        limit = self.config.get('archiver', {}).get('limit', 100)

        # Инициализация
        self._init_github()

        # Получить топ репозиториев
        print(f"\n  Запрашиваю топ-{limit} репозиториев с GitHub...")

        try:
            top_repos = self.github.get_top_repositories(limit)  # type: ignore
        except Exception as e:
            print(f"\n  ✗ Ошибка загрузки с GitHub: {e}")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if not top_repos:
            print("\n  ✗ Не удалось получить репозитории")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        print(f"  ✓ Получено {len(top_repos)} репозиториев")

        # Фильтровать уже загруженные
        processed_names = self.journal.get_processed_names()
        new_repos = [r for r in top_repos if r.get('full_name') not in processed_names]

        print(f"  Уже в журнале: {len(processed_names)}")
        print(f"  Осталось для загрузки: {len(new_repos)}\n")

        if not new_repos:
            print("  ✓ Все репозитории уже загружены!")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Флаг автоматической загрузки
        auto_load = False
        loaded_count = 0
        error_count = 0

        for i, repo_info in enumerate(new_repos, 1):
            full_name = repo_info.get('full_name', '')
            display_name = repo_info.get('name', '')
            stars = repo_info.get('stargazers_count', 0)
            desc = repo_info.get('description', '') or 'Без описания'

            print(f"  {'═' * 56}")
            print(f"  #{i} из {len(new_repos)} | {display_name}")
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
                # Загрузить репозиторий
                success = self._download_and_send_repo_info(repo_info)

                if success:
                    loaded_count += 1
                    print(f"\n  ✓ Загружено ({loaded_count}/{len(new_repos)})")
                else:
                    error_count += 1
                    print(f"\n  ✗ Ошибка загрузки")
            else:
                # Пустой ввод = загрузить
                success = self._download_and_send_repo_info(repo_info)
                if success:
                    loaded_count += 1

            time.sleep(0.5)

        # Итоги
        print("\n" + "═" * 60)
        print("Загрузка завершена")
        print(f"  Обработано: {loaded_count + error_count}")
        print(f"  Успешно: {loaded_count}")
        print(f"  Ошибок: {error_count}")
        print("═" * 60)

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

        success = browser.send_message_with_file(
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
        Скачать и отправить новый репозиторий (из API данных)

        Args:
            repo_info: Данные от GitHub API

        Returns:
            True при успехе
        """
        full_name = repo_info.get('full_name', '')
        owner, repo_name = full_name.split('/', 1)
        default_branch = repo_info.get('default_branch', 'main')

        # Построить полные данные
        repo_data = self.github.build_repo_data(repo_info)  # type: ignore

        # Скачать ZIP
        print("    ↓ Скачиваю ZIP...")
        zip_path = self.github.download_zip(owner, repo_name, default_branch)  # type: ignore

        if not zip_path or not os.path.exists(zip_path):
            print("    ✗ Не удалось скачать ZIP")
            return False

        # Подготовить текст сообщения
        text = self._build_message_text(repo_data)
        print("    ✓ Текст подготовлен")

        # Отправить в MAX
        self._init_max_browser()
        print("    → Отправляю в MAX...")

        browser = self._init_max_browser()  # type: ignore
        success = browser.send_message_with_file(
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

        # Добавить в журнал
        repo_data['status'] = 'sent' if success else 'failed'
        repo_data['version'] = repo_data.get('version', '') or 'unknown'
        self.journal.add_repository(repo_data)

        return success

    def run(self):
        """Запустить главный цикл программы"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')

            self._show_menu()

            choice = input("  Выберите действие [1-3]: ").strip()

            if choice == '1':
                self.sync_repositories()
            elif choice == '2':
                self.load_new_repositories()
            elif choice == '3':
                print("\n  До свидания!\n")
                break
            else:
                print("\n  Неверный выбор. Нажмите 1, 2 или 3.")
                time.sleep(1)


def main():
    """Точка входа"""
    config_path = "config.yaml"

    # Проверить аргументы командной строки
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    try:
        archiver = GitHubArchiver(config_path)
        archiver.run()
    except KeyboardInterrupt:
        print("\n\n  Программа прервана пользователем.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n  ✗ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()