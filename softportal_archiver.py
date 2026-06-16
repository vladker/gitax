#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SoftPortal Archiver — Топ программ SoftPortal в MAX канал

Самостоятельный скрипт для загрузки топ программ из SoftPortal
и их публикации в отдельный канал MAX (текстовые сообщения).
"""

import os
import sys
import time
from datetime import datetime
from dotenv import load_dotenv
from logging_config import setup_logging, LogMixin, SessionCapture

from softportal_api import SoftPortalAPI, SoftPortalAPIError
from browser_max import BrowserMAX
from browser_init import BrowserInitMixin
from softportal_journal import SoftPortalJournal
from signal_handler import SignalHandler


class SoftPortalArchiver(LogMixin, BrowserInitMixin):
    """Архиватор топ программ SoftPortal в MAX канал"""

    _channel_key = "softportal"
    _section_key = None

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        self.sp = SoftPortalAPI()
        self.journal = SoftPortalJournal("softportal_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        SignalHandler().register(self, on_cleanup=self._cleanup)

    def _cleanup(self):
        """Clean up resources on exit"""
        if self.browser:
            try:
                self.journal.save()
                self.browser.close()
            except Exception:
                pass

    # ── Formatting helpers ──

    def _build_message_text(self, detail: dict) -> str:
        """
        Построить текст сообщения для MAX.

        Args:
            detail: Словарь с get_program_detail данными
                - name, version, description, rating, license, os
                - full_category_path: list[(cat_id, name)]

        Returns:
            Отформатированный текст сообщения
        """
        name = detail.get('name', '')
        version = detail.get('version', '')
        description = detail.get('description', '') or 'Без описания'
        rating = detail.get('rating', 0.0)
        license_type = detail.get('license', '') or 'Неизвестно'
        os_info = detail.get('os', '') or 'Неизвестно'

        # Breadcrumb path: "Windows → CD/DVD диски → Образы дисков"
        path_parts = [n for _, n in detail.get('full_category_path', [])]
        full_path = ' → '.join(path_parts) if path_parts else 'Без категории'

        text = (
            f"📦 {name} {version}\n"
            f"📝 {description}\n"
            f"📂 {full_path}\n"
            f"🖥 {os_info} | {license_type} | ⭐{rating}\n"
            f"🔗 https://www.softportal.com{detail.get('program_url', '')}"
        )
        return text

    @staticmethod
    def _print_progress(current: int, total: int, sent: int, skipped: int,
                        status: str = ""):
        """Print progress bar for load operations"""
        if total == 0:
            return
        pct = int(current / total * 100)
        filled = int(pct // 10)
        bar = "█" * filled + "░" * (10 - filled)
        print(f"\r  Прогресс: {current}/{total} | {bar} {pct}% | "
              f"✓{sent} | –{skipped} {status}",
              end="", flush=True)
        if current >= total:
            print()

    # ── Browser helpers (text-only) ──

    def _send_text_message(self, browser: BrowserMAX, text: str) -> bool:
        """
        Send a text-only message via browser automation.

        Returns True on success.
        """
        try:
            input_elem = browser._find_message_input()
            if not input_elem:
                self.logger.error("Cannot find message input element")
                return False
            browser._type_message(text, input_elem)
            browser._send_message()
            time.sleep(1)
            return True
        except Exception as e:
            self.logger.error(f"Send text error: {e}")
            return False

    # ── Category configuration ──

    def _ensure_categories_configured(self) -> list[int]:
        """
        Interactive category selection when config is empty.

        Returns:
            List of selected category IDs
        """
        sp_cfg = self.config.get('softportal_archiver', {})
        categories = sp_cfg.get('categories', [])

        if categories:
            return categories

        print("\n  ⚙️ Категории не настроены. Выберите категории для загрузки:")
        print()

        try:
            all_categories = self.sp.get_categories()
        except Exception as e:
            print(f"  ✗ Не удалось получить категории: {e}")
            return []

        platforms = [c for c in all_categories if c.get('is_platform')]
        subcategories = [c for c in all_categories if not c.get('is_platform')]

        if not platforms:
            print("  ✗ Категории не найдены")
            return []

        # Step 1: Show platforms
        print("  ── Платформы ──")
        for i, p in enumerate(platforms, 1):
            print(f"  [{i}] {p['name']} (id={p['id']})")
        print()

        try:
            choice = input(
                "  Выберите платформы (номера через запятую, "
                "или Enter для всех): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            return []

        if choice == '':
            selected_ids = [p['id'] for p in platforms]
        else:
            try:
                indices = [int(x.strip()) for x in choice.split(',')]
                selected_ids = [platforms[i - 1]['id'] for i in indices
                               if 1 <= i <= len(platforms)]
            except (ValueError, IndexError):
                print("  ✗ Неверный ввод. Используются все платформы.")
                selected_ids = [p['id'] for p in platforms]

        # Step 2: Show subcategories (optional)
        if subcategories:
            print()
            print("  ── Подкатегории (опционально) ──")
            for i, sc in enumerate(subcategories, 1):
                print(f"  [{i}] {sc['name']} (id={sc['id']})")
            print()

            try:
                sub_choice = input(
                    "  Добавить подкатегории (номера через запятую, "
                    "или Enter для пропуска): "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                sub_choice = ''

            if sub_choice:
                try:
                    sub_indices = [int(x.strip()) for x in sub_choice.split(',')]
                    sub_ids = [subcategories[i - 1]['id']
                              for i in sub_indices
                              if 1 <= i <= len(subcategories)]
                    selected_ids.extend(sub_ids)
                except (ValueError, IndexError):
                    pass

        # Update config in memory
        sp_cfg['categories'] = selected_ids
        self.config['softportal_archiver'] = sp_cfg

        print(f"\n  ✓ Выбрано {len(selected_ids)} категорий")
        return selected_ids

    # ── Main operations ──

    def load_top_programs(self, limit: int | None = None):
        """
        Загрузить топ программ из SoftPortal в MAX канал.

        Flow:
        1. Ensure categories configured
        2. For each category: get_top_programs → dedup by journal
        3. For each new program: get_program_detail → build_message →
           send_text → journal.mark_processed()
        """
        if limit is None:
            limit = self.config.get('softportal_archiver', {}).get('limit', 50)

        print("\n" + "═" * 60)
        print("          Загрузка топ программ SoftPortal")
        print("═" * 60)

        # Ensure categories are configured
        category_ids = self._ensure_categories_configured()
        if not category_ids:
            print("\n  ✗ Нет настроенных категорий")
            return

        print(f"\n  Категории: {len(category_ids)} | Лимит: {limit}")

        # Fetch top programs for each category
        all_programs = []
        for cat_id in category_ids:
            try:
                programs = self.sp.get_top_programs(cat_id, f"cat-{cat_id}", limit)
                for p in programs:
                    p['_platform_id'] = cat_id
                all_programs.extend(programs)
                print(f"  ✓ cat-{cat_id}: {len(programs)} программ")
            except SoftPortalAPIError as e:
                print(f"  ✗ cat-{cat_id}: {e}")

        if not all_programs:
            print("\n  ✗ Не удалось получить программы")
            return

        # Dedup by journal: (id, platform_id)
        to_process = []
        skipped = 0
        for prog in all_programs:
            pid = prog.get('id')
            platform_id = prog.get('_platform_id', '')
            if pid and not self.journal.is_processed(str(pid), str(platform_id)):
                to_process.append(prog)
            else:
                skipped += 1

        total = len(to_process)
        print(f"\n  Всего: {len(all_programs)} | В журнале: {skipped} | "
              f"К отправке: {total}")

        if not to_process:
            print("\n  ✓ Все программы уже в журнале")
            return

        # Connect browser
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            return

        # Process each program
        sent = 0
        failed = 0

        for i, prog in enumerate(to_process, 1):
            if self._shutdown:
                print(f"\n  ⚠ Прервано после {i - 1} программ")
                break

            name = prog.get('name', '')
            version = prog.get('version', '')
            print(f"\n  [{i}/{total}] {name} {version}")

            # Get detail for breadcrumb path
            try:
                detail = self.sp.get_program_detail(
                    prog['id'], prog.get('slug', ''))
            except SoftPortalAPIError as e:
                print(f"  ✗ Детали: {e}")
                failed += 1
                self._print_progress(i, total, sent, failed, "✗")
                continue

            # Platform ID from breadcrumb (first element = platform)
            breadcrumb = detail.get('full_category_path', [])
            platform_id = str(breadcrumb[0][0]) if breadcrumb else str(prog.get('_platform_id', ''))

            # Build and send message
            text = self._build_message_text({
                **detail,
                'program_url': prog.get('program_url', ''),
            })

            success = self._send_text_message(browser, text)

            if success:
                self.journal.mark_processed(
                    str(prog['id']), platform_id,
                    {'name': name, 'version': version, 'detail': detail})
                sent += 1
                print(f"  ✓ Отправлено")
            else:
                failed += 1
                print(f"  ✗ Ошибка отправки")

            self._print_progress(i, total, sent, failed,
                                "✓" if success else "✗")

        # Summary
        print()
        print("\n" + "═" * 60)
        print("Загрузка завершена")
        print(f"  Всего: {sent + failed}")
        print(f"  Отправлено: {sent}")
        if failed:
            print(f"  Ошибок: {failed}")
        print("═" * 60)

        self._close_browser()

    def sync_programs(self):
        """
        Обновить программы из журнала.

        Flow:
        1. Load all entries from journal
        2. For each: get_program_detail → check version change
        3. Show update table → confirm → send updated messages
        """
        print("\n" + "═" * 60)
        print("          Синхронизация программ")
        print("═" * 60)

        entries = self.journal.get_all_processed()
        if not entries:
            print("\n  ⚠ Журнал пуст. Сначала загрузите программы (пункт 1).")
            return

        print(f"\n  Проверяю {len(entries)} программ на наличие обновлений...\n")

        # Check for updates
        updates = []
        checked = 0
        for entry in entries:
            prog_data = entry.get('program_data', {})
            name = prog_data.get('name', '')
            saved_version = prog_data.get('version', '')
            prog_id = entry.get('id')
            platform_id = entry.get('platform_id', '')

            if not prog_id:
                continue

            try:
                detail = self.sp.get_program_detail(prog_id, '')
                latest = detail.get('version', '')
            except SoftPortalAPIError:
                continue

            if latest and latest != saved_version:
                updates.append({
                    'entry': entry,
                    'old_version': saved_version,
                    'new_version': latest,
                    'name': name,
                    'detail': detail,
                })

            checked += 1
            print(f"\r  Проверка: {checked}/{len(entries)} "
                  f"({int(checked / len(entries) * 100)}%) | "
                  f"Обновлений: {len(updates)}",
                  end="", flush=True)

        print()

        if not updates:
            print("\n  ✓ Все программы актуальны!")
            return

        # Show update table
        print()
        print("  " + "─" * 74)
        print(f"  {'#':<4} {'Программа':<35} {'Было':<15} {'Стало':<15}")
        print("  " + "─" * 74)
        for i, u in enumerate(updates, 1):
            name_display = u['name'][:33]
            print(f"  {i:<4} {name_display:<35} "
                  f"{u['old_version']:<15} {u['new_version']:<15}")
        print("  " + "─" * 74)

        # Confirm
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

        # Connect browser
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            return

        updated = 0
        failed = 0

        for i, u in enumerate(updates, 1):
            if self._shutdown:
                print(f"\n  ⚠ Прервано после {i - 1} обновлений")
                break

            name = u['name']
            print(f"\n  [{i}/{len(updates)}] {name}: "
                  f"{u['old_version']} → {u['new_version']}")

            detail = u['detail']
            text = self._build_message_text(detail)

            success = self._send_text_message(browser, text)

            if success:
                self.journal.mark_processed(
                    str(u['entry'].get('id', '')),
                    str(u['entry'].get('platform_id', '')),
                    {'name': name, 'version': u['new_version'], 'detail': detail})
                updated += 1
                print(f"  ✓ Обновлено")
            else:
                failed += 1
                print(f"  ✗ Ошибка")

        # Summary
        print()
        print("\n" + "═" * 60)
        print("Синхронизация завершена")
        print(f"  Обновлено: {updated}")
        if failed:
            print(f"  Ошибок: {failed}")
        print("═" * 60)

        self._close_browser()

    def run(self):
        """
        Основной метод — запуск архивации через меню.
        Показывает статистику журнала и предлагает выбор режима.
        """
        stats = self.journal.get_stats()

        print("\n" + "=" * 60)
        print("           SoftPortal Archiver")
        print("           Загрузка программ в MAX")
        print("=" * 60)
        print(f"  Журнал: {stats['total']} программ")
        print("-" * 60)

        print("\n  [1] Загрузить топ программ")
        print("  [2] Синхронизировать программы")
        print("  [3] Выход")
        print()

        choice = input("  Ваш выбор [1/2/3]: ").strip()

        if choice == '1':
            self.load_top_programs()
        elif choice == '2':
            self.sync_programs()
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
        archiver = SoftPortalArchiver("config.yaml")
        archiver.run()
    finally:
        session.stop()


if __name__ == "__main__":
    main()
