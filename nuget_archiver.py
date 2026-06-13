#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NuGet (.NET) Archiver — Топ .NET пакетов в MAX канал

Самостоятельный скрипт для загрузки топ N .NET-пакетов
и их публикации в отдельный канал MAX.
"""

import os
import sys
import time
import shutil
from datetime import datetime
from logging_config import setup_logging, LogMixin, SessionCapture

from nuget_api import NuGetAPI
from browser_max import BrowserMAX
from browser_init import BrowserInitMixin
from nuget_journal import NuGetJournal
from config_utils import get_split_mode
from signal_handler import SignalHandler
from utils import format_file_size


class NuGetArchiver(LogMixin, BrowserInitMixin):
    """Архиватор топ .NET пакетов в MAX канал"""

    _channel_key = "nuget"
    _section_key = None

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        output_dir = self.config.get('nuget_archiver', {}).get('output_dir', './temp_nuget')
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        self.nuget = NuGetAPI()
        self.journal = NuGetJournal("nuget_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        SignalHandler().register(self, on_cleanup=self._cleanup)

    def _cleanup(self):
        if self.browser:
            try:
                self.journal.save()
                self.browser.close()
            except Exception:
                pass

    @staticmethod
    def _format_downloads(count: int) -> str:
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B"
        elif count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _build_message_text(self, pkg_data: dict, file_sizes: list[int]) -> str:
        name = pkg_data.get('name', '')
        version = pkg_data.get('version', '')
        summary = pkg_data.get('description', '') or 'Без описания'
        downloads = pkg_data.get('total_downloads', 0)
        nuget_url = f"https://www.nuget.org/packages/{name}/{version}"

        text = (
            f"🟣 {name} {version}\n\n"
            f"📝 {summary}\n\n"
            f"📥 Загрузки: {self._format_downloads(downloads)}\n"
            f"🔗 NuGet: {nuget_url}"
        )

        if file_sizes:
            for i, size in enumerate(file_sizes):
                text += f"\n📦 Файл {i + 1}: {format_file_size(size)}"

        return text

    @staticmethod
    def _print_progress(current: int, total: int, sent: int, skipped: int,
                        status: str = ""):
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

    def load_top_packages(self, limit: int | None = None):
        """Загрузить топ N .NET пакетов в MAX канал."""
        if limit is None:
            limit = self.config.get('nuget_archiver', {}).get('limit', 50)
        retries = self.config.get('nuget_archiver', {}).get(
            'retries', self.config.get('archiver', {}).get('retries', 3))
        retry_delay = self.config.get('nuget_archiver', {}).get(
            'retry_delay', self.config.get('archiver', {}).get('retry_delay', 10))
        repo_delay = self.config.get('nuget_archiver', {}).get(
            'repo_delay', self.config.get('archiver', {}).get('repo_delay', 30))
        output_dir = self.config.get('nuget_archiver', {}).get('output_dir', './temp_nuget')
        split_mode = get_split_mode(self.config)

        print("\n" + "═" * 60)
        print("          Загрузка топ .NET пакетов")
        print("═" * 60)

        browser = self._ensure_browser_connected()

        print(f"\n  Загрузка списка из nuget.org (до {limit} пакетов)...")
        packages = self.nuget.get_top_packages(limit=limit)
        print(f"  Получено {len(packages)} пакетов")

        if not packages:
            print("\n  ✗ Не удалось получить пакеты")
            self._close_browser()
            return

        to_process = []
        for pkg in packages:
            if not self.journal.exists_by_name(pkg["id"]):
                to_process.append(pkg)

        print(f"\n  К обработке: {len(to_process)} (пропущено: {len(packages) - len(to_process)})")

        if not to_process:
            print("\n  Все пакеты уже в журнале")
            self._close_browser()
            return

        sent = 0
        skipped = 0
        failed = 0

        for idx, pkg in enumerate(to_process, 1):
            name = pkg["id"]
            version = pkg["version"]
            print(f"\n  [{idx}/{len(to_process)}] {name} {version}")

            download_url = self.nuget.get_package_download_url(name, version)
            filename = f"{name}.{version}.nupkg"
            filepath = os.path.join(output_dir, filename)

            try:
                resp = self.nuget.session.get(download_url, timeout=120, stream=True)
                resp.raise_for_status()
                with open(filepath, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                actual_size = os.path.getsize(filepath)
                print(f"  ⬇ Скачано: {format_file_size(actual_size)}")

            except Exception as e:
                print(f"  ✗ Ошибка скачивания: {e}")
                self.journal.mark_failed(name, version, pkg.get("description", ""),
                                        pkg.get("total_downloads", 0))
                failed += 1
                self._print_progress(idx, len(to_process), sent, skipped + failed, "✗")
                continue

            file_sizes = [actual_size]
            files_to_send = [filepath]

            if split_mode and actual_size > self.config.get('archiver', {}).get('split_threshold_mb', 49) * 1024 * 1024:
                base = filepath.replace('.nupkg', '')
                volumes = self._split_to_volumes(base)
                if volumes:
                    files_to_send = volumes
                    file_sizes = [os.path.getsize(v) for v in volumes]

            msg_text = self._build_message_text(pkg, file_sizes)

            success = False
            for attempt in range(1, retries + 1):
                try:
                    browser.send_message(msg_text)
                    for fp in files_to_send:
                        browser.send_file_message(fp)
                    success = True
                    break
                except Exception as e:
                    print(f"  ⚠ Попытка {attempt}/{retries}: {e}")
                    if attempt < retries:
                        time.sleep(retry_delay)

            if success:
                self.journal.add(name, version, pkg.get("description", ""),
                                 pkg.get("total_downloads", 0), [f for f in files_to_send])
                sent += 1
                print(f"  ✓ Отправлено")
            else:
                self.journal.mark_failed(name, version, pkg.get("description", ""),
                                        pkg.get("total_downloads", 0))
                failed += 1
                print(f"  ✗ Ошибка отправки после {retries} попыток")

            self._cleanup_files(files_to_send)
            self._print_progress(idx, len(to_process), sent, skipped + failed)

            if idx < len(to_process):
                time.sleep(repo_delay)

        print(f"\n  Итого: ✓{sent} | ✗{failed}")
        self._close_browser()

    def sync_packages(self):
        """Синхронизировать версии .NET пакетов."""
        print("\n" + "═" * 60)
        print("          Синхронизация .NET пакетов")
        print("═" * 60)

        browser = self._ensure_browser_connected()
        entries = self.journal.get_all()
        if not entries:
            print("\n  Журнал пуст. Нечего синхронизировать.")
            self._close_browser()
            return

        output_dir = self.config.get('nuget_archiver', {}).get('output_dir', './temp_nuget')
        retries = self.config.get('nuget_archiver', {}).get(
            'retries', self.config.get('archiver', {}).get('retries', 3))
        retry_delay = self.config.get('nuget_archiver', {}).get(
            'retry_delay', self.config.get('archiver', {}).get('retry_delay', 10))
        repo_delay = self.config.get('nuget_archiver', {}).get(
            'repo_delay', self.config.get('archiver', {}).get('repo_delay', 30))
        split_mode = get_split_mode(self.config)

        updated = 0
        unchanged = 0
        failed = 0

        for idx, entry in enumerate(entries, 1):
            name = entry.get("name", "")
            old_version = entry.get("version", "")
            print(f"\n  [{idx}/{len(entries)}] Проверка {name} {old_version}...")

            latest = self.nuget.get_latest_version(name)
            if not latest or latest == old_version:
                unchanged += 1
                print(f"  – Актуальная версия")
                continue

            print(f"  ⬆ Обновление: {old_version} → {latest}")

            download_url = self.nuget.get_package_download_url(name, latest)
            filename = f"{name}.{latest}.nupkg"
            filepath = os.path.join(output_dir, filename)

            try:
                resp = self.nuget.session.get(download_url, timeout=120, stream=True)
                resp.raise_for_status()
                with open(filepath, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                actual_size = os.path.getsize(filepath)

            except Exception as e:
                print(f"  ✗ Ошибка скачивания: {e}")
                failed += 1
                continue

            file_sizes = [actual_size]
            files_to_send = [filepath]

            if split_mode and actual_size > self.config.get('archiver', {}).get('split_threshold_mb', 49) * 1024 * 1024:
                base = filepath.replace('.nupkg', '')
                volumes = self._split_to_volumes(base)
                if volumes:
                    files_to_send = volumes
                    file_sizes = [os.path.getsize(v) for v in volumes]

            msg_text = self._build_message_text({
                "name": name,
                "version": latest,
                "description": entry.get("description", ""),
                "total_downloads": entry.get("downloads", 0),
            }, file_sizes)

            success = False
            for attempt in range(1, retries + 1):
                try:
                    browser.send_message(msg_text)
                    for fp in files_to_send:
                        browser.send_file_message(fp)
                    success = True
                    break
                except Exception as e:
                    if attempt < retries:
                        time.sleep(retry_delay)

            if success:
                self.journal.add(name, latest, entry.get("description", ""),
                                 entry.get("downloads", 0), [f for f in files_to_send])
                updated += 1
                print(f"  ✓ Обновлено и отправлено")
            else:
                failed += 1
                print(f"  ✗ Ошибка отправки")

            self._cleanup_files(files_to_send)

            if idx < len(entries):
                time.sleep(repo_delay)

        print(f"\n  Итого: ⬆{updated} | ={unchanged} | ✗{failed}")
        self._close_browser()

    def _split_to_volumes(self, filepath: str) -> list[str]:
        volumes = []
        try:
            vol_size = self.config.get('archiver', {}).get('split_threshold_mb', 49)
            result = os.system(f'7z a -t7z -v{vol_size}m "{filepath}.7z" "{filepath}" >nul 2>&1')
            if result == 0:
                import glob as globmod
                vol_pattern = f"{filepath}.7z.*"
                volumes = sorted(globmod.glob(vol_pattern))
                if volumes:
                    os.remove(filepath)
        except Exception as e:
            self.logger.error(f"Split error: {e}")
        return volumes

    def _cleanup_files(self, files: list[str]):
        for fp in files:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass


def main():
    load_dotenv = __import__('dotenv').load_dotenv
    load_dotenv()

    session = SessionCapture()
    session.start()
    print(f"📋 Session log: {session.path}")

    logger = setup_logging(log_file="nuget_archiver.log", level=10)
    archiver = NuGetArchiver("config.yaml")
    archiver.load_top_packages()

    session.stop()


if __name__ == "__main__":
    main()
