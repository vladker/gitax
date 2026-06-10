#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backuper — Архивация папок в канал MAX и восстановление из канала.

Два режима:
  Backup  — архивировать папку → отправить тома в канал
  Restore — сканировать канал → скачать архивы → извлечь
"""

import os
import sys
import time
import yaml
import glob
import shutil
import requests
from datetime import datetime

from dotenv import load_dotenv
from logging_config import LogMixin

from browser_max import (
    BrowserMAX,
    archive_directory_to_volumes,
    cleanup_volumes,
)
from backuper_journal import BackuperJournal
from config_utils import get_channel_url, get_config_value


class Backuper(LogMixin):
    """Архивация папок в MAX канал и восстановление"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.journal = BackuperJournal("backuper_journal.json")
        self.browser: BrowserMAX | None = None

        # Ensure dirs exist
        output_dir = self.config.get("backuper", {}).get("output_dir", "./temp_backups")
        os.makedirs(output_dir, exist_ok=True)
        download_dir = self.config.get("backuper", {}).get("download_dir", "./restored")
        os.makedirs(download_dir, exist_ok=True)

    # ── Config ──

    @staticmethod
    def _load_config(config_path: str) -> dict:
        load_dotenv()
        config = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

        channel_url = get_channel_url(config, "backup", label="Backup канал")
        config.setdefault("backup", {})["channel_url"] = channel_url

        config.setdefault("backuper", {})
        bk = config["backuper"]
        bk.setdefault("default_volume_size", "49M")
        bk.setdefault("compression_level", "5")
        bk.setdefault("output_dir", "./temp_backups")
        bk.setdefault("download_dir", "./restored")
        bk.setdefault("page_size", 10)
        bk.setdefault("retries", 3)
        bk.setdefault("retry_delay", 10)

        return config

    # ── Browser ──

    def _init_browser(self) -> BrowserMAX:
        if self.browser is None:
            channel_url = self.config.get("backup", {}).get("channel_url", "")
            use_local = self.config.get("archiver", {}).get("use_local_browser", False)
            self.browser = BrowserMAX(channel_url, use_local_browser=use_local)
        return self.browser

    def _ensure_browser_connected(self) -> BrowserMAX:
        browser = self._init_browser()
        if not browser.keep_alive_connect():
            raise ConnectionError("Не удалось подключиться к MAX")
        browser.navigate()
        browser.ensure_page_ready()
        return browser

    def _close_browser(self):
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None

    # ── Formatting ──

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 ** 3:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        else:
            return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"

    @staticmethod
    def _dir_size(path: str) -> int:
        total = 0
        for dirpath, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
        return total

    # ── Backup Mode ──

    def run_backup(self):
        """Интерактивный режим бэкапа: папка → архив → канал"""
        print("\n" + "=" * 60)
        print("  Бэкап — архивация папки в канал MAX")
        print("=" * 60)

        # 1. Source path
        print()
        source_path = input("  Папка для бэкапа: ").strip().strip('"').strip("'")
        if not source_path or not os.path.isdir(source_path):
            print("  ✗ Папка не найдена или не является директорией.")
            return

        src_size = self._dir_size(source_path)
        if src_size == 0:
            print("  ✗ Папка пуста. Нечего архивировать.")
            return

        # 2. Archive name
        default_name = f"{os.path.basename(source_path)}_{datetime.now().strftime('%Y%m%d_%H%M')}"
        archive_name = input(f"  Имя архива [{default_name}]: ").strip()
        if not archive_name:
            archive_name = default_name

        # 3. Description
        print()
        print("  Описание архива (необязательно):")
        print("  Будет добавлено в сообщение при отправке в канал.")
        description = input("  Описание: ").strip()

        # 4. Volume mode
        print("\n  Режим томов:")
        print("    [1] Однотомный архив")
        print("    [2] Многотомный (размер из конфига)")
        print("    [3] Многотомный (свой размер)")
        vol_choice = input("  Выбор [1-3]: ").strip()

        volume_size = None
        if vol_choice == "2":
            volume_size = self.config.get("backuper", {}).get("default_volume_size", "49M")
            print(f"  Размер тома: {volume_size}")
        elif vol_choice == "3":
            volume_size = input('  Размер тома (напр. "49M", "100M"): ').strip()
            if not volume_size:
                volume_size = self.config.get("backuper", {}).get("default_volume_size", "49M")
        elif vol_choice != "1":
            print("  Неверный выбор, использую однотомный архив.")

        # 5. Password
        use_password = input("  Использовать пароль? [y/N]: ").strip().lower() == "y"
        password = None
        password_hint = None
        if use_password:
            password = input("  Пароль: ").strip()
            if not password:
                print("  Пароль пустой, отменяю шифрование.")
                use_password = False
            else:
                hint = input("  Подсказка для пароля (оставьте пустым, если не нужна): ").strip()
                if hint:
                    password_hint = hint

        # 6. Duplicate check
        content_hash = self.journal.compute_content_hash(source_path)
        if self.journal.is_duplicate_by_hash(content_hash):
            confirm = input("  ⚠ Уже есть бэкап с таким содержимым. Переписать? [y/N]: ").strip().lower()
            if confirm != "y":
                print("  Отменено.")
                return

        # 7. Archive
        output_dir = self.config.get("backuper", {}).get("output_dir", "./temp_backups")
        output_base = os.path.join(output_dir, archive_name) + ".7z"
        comp_level = int(self.config.get("backuper", {}).get("compression_level", "5"))

        print(f"\n  Архивация '{source_path}' → {archive_name}.7z ...")
        start = time.time()
        volumes = archive_directory_to_volumes(
            source_dir=source_path,
            output_base=output_base,
            volume_size=volume_size,
            compression_level=comp_level,
            password=password,
            clean_existing=True,
        )
        elapsed = time.time() - start

        if not volumes:
            print("  ✗ Архивация не удалась. Проверьте логи.")
            return

        total_size = sum(os.path.getsize(v) for v in volumes if os.path.exists(v))
        print(f"  ✓ {len(volumes)} том(ов), {self._format_size(total_size)} за {elapsed:.0f}с")

        # 7. Upload to channel
        print("\n  Отправка в канал MAX ...")
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"  ✗ Не удалось подключиться к MAX: {e}")
            cleanup_volumes(volumes)
            return

        msg_text = (
            f"📦 Бэкап: {archive_name}\n"
            f"📁 Источник: {os.path.basename(source_path)}\n"
            f"📊 Томов: {len(volumes)} | {self._format_size(total_size)}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        if description:
            msg_text = f"📝 {description}\n\n{msg_text}"
        if password_hint:
            msg_text += f"\n🔑 Подсказка: {password_hint}"

        retries = int(self.config.get("backuper", {}).get("retries", 3))
        retry_delay = int(self.config.get("backuper", {}).get("retry_delay", 10))

        success, _ = browser.send_message_with_files(
            text=msg_text,
            filepaths=volumes,
            retries=retries,
            retry_delay=retry_delay,
            split_threshold_mb=9999,  # Already split, don't split again
            expected_extensions=[".7z"],
        )

        if success:
            print(f"  ✓ Бэкап '{archive_name}' отправлен в канал")
            self.journal.add_backup({
                "archive_name": archive_name,
                "source_path": source_path,
                "content_hash": content_hash,
                "volume_count": len(volumes),
                "encrypted": use_password,
                "total_size": total_size,
                "status": "uploaded",
                "description": description or "",
            })
            if use_password:
                self.journal.store_password(archive_name, password, hint=password_hint)
        else:
            print("  ✗ Отправка не удалась. Удаляю тома.")
            self.journal.add_backup({
                "archive_name": archive_name,
                "source_path": source_path,
                "content_hash": content_hash,
                "volume_count": len(volumes),
                "encrypted": use_password,
                "total_size": total_size,
                "status": "failed",
                "description": description or "",
            })

        cleanup_volumes(volumes)
        self._close_browser()

    # ── Restore Mode ──

    def run_restore(self):
        """Интерактивный режим восстановления: канал → скачать → извлечь"""
        print("\n" + "=" * 60)
        print("  Восстановление — скачивание архивов из канала MAX")
        print("=" * 60)

        # 1. Connect
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            return

        # 2. Scan
        print("\n  Сканирование канала ...")
        try:
            archives = browser.scan_channel_for_archives()
        except Exception as e:
            print(f"\n  ✗ Ошибка сканирования: {e}")
            self._close_browser()
            return

        if not archives:
            print("  В канале не найдено архивов.")
            self._close_browser()
            return

        # 3. Show paginated list
        page_size = int(self.config.get("backuper", {}).get("page_size", 10))
        page = 0  # 0-indexed

        while True:
            start_idx = page * page_size
            end_idx = start_idx + page_size
            total_pages = (len(archives) + page_size - 1) // page_size

            print(f"\n  Страница {page + 1}/{total_pages}")
            print(f"  {'#':>3}  {'Имя архива':<40} {'Томов':>5}  {'Дата':>10}")
            print(f"  {'─' * 3}  {'─' * 40} {'─' * 5}  {'─' * 10}")

            page_archives = archives[start_idx:end_idx]
            for i, arch in enumerate(page_archives, start_idx + 1):
                name = arch.get("archive_name", "?")
                vol_count = arch.get("volume_count", 1)
                hint = self.journal.get_password_hint(name)
                # Try to get description from journal
                entry = self.journal.get_backup(name)
                desc = (entry or {}).get("description", "") or ""
                display = name[:37] + "..." if len(name) > 40 else name
                if desc:
                    display += f" [{desc[:20]}]"
                if hint:
                    display += f" 🔑{hint[:15]}"
                print(f"  {i:>3}  {display:<60} {vol_count:>5}")

            print(f"\n  [←] Пред.  [→] След.  [1] Скачать выбранные  [2] Скачать все  [0] Выход")
            action = input("  Действие: ").strip().lower()

            if action == "<" or action == "prev":
                page = max(0, page - 1)
                continue
            elif action == ">" or action == "next":
                if page < total_pages - 1:
                    page += 1
                continue
            elif action == "0":
                print("  Отменено.")
                self._close_browser()
                return
            elif action == "1":
                # Selected archives
                nums_str = input("  Номера через запятую (напр. 1,3,5): ").strip()
                try:
                    selected_nums = [int(n.strip()) for n in nums_str.split(",") if n.strip()]
                except ValueError:
                    print("  Неверный формат.")
                    continue
                selected = []
                for n in selected_nums:
                    if 1 <= n <= len(archives):
                        selected.append(archives[n - 1])
                if not selected:
                    print("  Не выбрано архивов.")
                    continue
                break
            elif action == "2":
                selected = archives
                break
            else:
                print("  Неверное действие.")
                continue

        # 4. Output directory
        default_download = self.config.get("backuper", {}).get("download_dir", "./restored")
        download_dir = input(f"  Папка для сохранения [{default_download}]: ").strip()
        if not download_dir:
            download_dir = default_download
        os.makedirs(download_dir, exist_ok=True)

        # 5. Password mode
        has_encrypted = False
        for arch in selected:
            # We can't know for sure from scan alone, so ask
            pass
        # Ask user how to handle passwords
        print("\n  Режим паролей:")
        print("    [1] Один пароль на все архивы")
        print("    [2] Пароль для каждого архива отдельно")
        print("    [3] Без пароля (попытка извлечь без пароля)")
        pw_mode = input("  Выбор [1-3]: ").strip()

        global_password = None
        if pw_mode == "1":
            global_password = input("  Пароль: ").strip()

        # 6. Download and extract
        print(f"\n  Скачивание и извлечение в '{download_dir}' ...\n")
        downloaded_ok = 0
        downloaded_fail = 0

        for arch_idx, arch in enumerate(selected, 1):
            archive_name = arch.get("archive_name", f"unknown_{arch_idx}")
            vol_count = arch.get("volume_count", 1)
            volumes = arch.get("volumes", [])

            print(f"  [{arch_idx}/{len(selected)}] {archive_name} ({vol_count} том(ов))")

            # Determine password for this archive
            pw = None
            if pw_mode == "1" and global_password:
                pw = global_password
                # Store global password + hint for this archive
                if not self.journal.has_password(archive_name):
                    self.journal.store_password(archive_name, global_password)
            elif pw_mode == "2":
                # Check journal first
                saved_pw = self.journal.get_password(archive_name)
                if saved_pw:
                    use_saved = input(f"    Использовать сохранённый пароль для '{archive_name}'? [Y/n]: ").strip().lower()
                    if use_saved != "n":
                        pw = saved_pw
                        hint = self.journal.get_password_hint(archive_name)
                        if hint:
                            print(f"    Подсказка: {hint}")
                    else:
                        pw = input(f"    Новый пароль для '{archive_name}': ").strip()
                        if pw:
                            self.journal.store_password(archive_name, pw)
                else:
                    pw = input(f"    Пароль для '{archive_name}' (пусто = без пароля): ").strip()
                    if pw:
                        self.journal.store_password(archive_name, pw)

            # Download all volumes
            temp_vol_dir = os.path.join(download_dir, ".tmp_download")
            os.makedirs(temp_vol_dir, exist_ok=True)

            all_vols_downloaded = True
            downloaded_paths = []

            for vol_name in volumes:
                vol_path = os.path.join(temp_vol_dir, vol_name)
                print(f"    ↓ {vol_name} ...", end="", flush=True)

                # Try URL-based download first (for old journal entries)
                dl_url = self._find_download_url(browser, vol_name, arch.get("volume_urls", {}))
                if dl_url:
                    try:
                        self._download_file(browser, dl_url, vol_path)
                        print(" ✓")
                        downloaded_paths.append(vol_path)
                        continue
                    except Exception as e:
                        print(f" ✗ {e}")
                        all_vols_downloaded = False
                        break

                # Fallback: click MAX's download button and capture via Playwright
                if self._download_via_playwright(browser, vol_name, vol_path):
                    print(" ✓")
                    downloaded_paths.append(vol_path)
                else:
                    print(" ✗ URL не найден")
                    all_vols_downloaded = False
                    break

            if not all_vols_downloaded:
                print(f"    ✗ Скачивание '{archive_name}' прервано.")
                downloaded_fail += 1
                # Clean up partial downloads
                for dp in downloaded_paths:
                    try:
                        os.remove(dp)
                    except OSError:
                        pass
                continue

            # Extract
            extract_dir = os.path.join(download_dir, archive_name)
            os.makedirs(extract_dir, exist_ok=True)

            if vol_count == 1:
                archive_file = downloaded_paths[0]
            else:
                # Multi-volume: 7z extracts from first volume
                archive_file = downloaded_paths[0]

            print(f"    Извлечение ...", end="", flush=True)
            extract_ok = self._extract_7z(archive_file, extract_dir, pw)

            if extract_ok:
                print(" ✓")
                downloaded_ok += 1
                self.journal.add_download({
                    "archive_name": archive_name,
                    "volumes_downloaded": vol_count,
                    "extracted_to": extract_dir,
                    "status": "completed",
                })
                # Remove volume files after successful extract
                for dp in downloaded_paths:
                    try:
                        os.remove(dp)
                    except OSError:
                        pass
            else:
                print(" ✗ (неверный пароль или повреждённый архив)")
                downloaded_fail += 1
                self.journal.add_download({
                    "archive_name": archive_name,
                    "volumes_downloaded": vol_count,
                    "extracted_to": extract_dir,
                    "status": "failed",
                })

        # Summary
        print(f"\n  Готово: {downloaded_ok} успешно, {downloaded_fail} ошибок.")
        print(f"  Архивы в: {download_dir}")
        self._close_browser()

    def _find_download_url(self, browser: BrowserMAX, filename: str, url_map: dict | None = None) -> str | None:
        """Find download URL for a specific filename in the channel"""
        # Fast path: use pre-extracted URL map from scan phase
        if url_map and filename in url_map:
            return url_map[filename]

        self.logger.debug(f"_find_download_url: fast path miss for '{filename}', trying DOM fallback")
        # Fallback: direct DOM query (for old journal entries, manual calls)
        try:
            url = browser.page.evaluate("""
                (filename) => {
                    const messages = document.querySelectorAll('[class*="message"]');
                    for (const msg of messages) {
                        const text = msg.textContent || '';
                        if (text.includes(filename)) {
                            const link = msg.querySelector('a[href*="download"], a[href*="file"], a[href*="attachment"]');
                            if (link) return link.href;
                            // Try any link that looks like a file URL
                            const allLinks = msg.querySelectorAll('a');
                            for (const a of allLinks) {
                                const href = a.href || '';
                                if (href.includes('.7z') || href.includes('download') || href.includes('file')) {
                                    return href;
                                }
                            }
                        }
                    }
                    return null;
                }
            """, filename)
            if url:
                self.logger.info(f"_find_download_url: DOM fallback found URL for '{filename}'")
                return url
            else:
                self.logger.warning(
                    f"_find_download_url: DOM fallback returned null for '{filename}'."
                )
        except Exception as e:
            self.logger.warning(f"Failed to find download URL for {filename}: {e}")

        # ── Diagnostic summary ──
        try:
            cached_debug = browser.page.evaluate("() => window.__gitax_url_extract_debug || null")
            if cached_debug and isinstance(cached_debug, dict):
                total_msgs = cached_debug.get("totalMessages", 0)
                strat = cached_debug.get("byStrategy", {})
                n_7z = len(cached_debug.get("archiveMsgSamples", []))
                print(f"  [DEBUG] При сканировании: {total_msgs} сообщений, "
                      f"{n_7z} с .7z, URL не найдены "
                      f"(стратегии: a[download]={strat.get('a_download',0)}, "
                      f"a[href]={strat.get('a_href_download',0)}, "
                      f"genericFile={strat.get('genericFile',0)})")
                print(f"  [DEBUG] MAX не хранит download URL в DOM "
                      f"— используется Playwright click-to-download")
        except Exception:
            pass

        return None

    def _download_via_playwright(self, browser: BrowserMAX, filename: str, output_path: str) -> bool:
        """Download file by clicking MAX download button and capturing via Playwright

        MAX doesn't embed download URLs in the DOM — the download button is a
        <button> with a JavaScript click handler. This method clicks the button
        and uses Playwright's download event to capture the file.
        """
        try:
            # Find the download button within the message containing the filename
            button = (
                browser.page.locator('[class*="message"]')
                .filter(has_text=filename)
                .locator('button[aria-label="Скачать"]')
                .first
            )

            # Click and capture download via Playwright
            with browser.page.expect_download() as download_info:
                button.click(timeout=10000)

            download = download_info.value
            download.save_as(output_path)
            self.logger.info(f"Downloaded '{filename}' via Playwright: {output_path}")
            return True

        except Exception as e:
            self.logger.warning(f"Playwright download failed for '{filename}': {e}")
            return False

    def _download_file(self, browser: BrowserMAX, url: str, output_path: str):
        """Download file using requests + browser cookies"""
        cookies = browser.page.context.cookies()
        jar = requests.cookies.RequestsCookieJar()
        for c in cookies:
            jar.set(c['name'], c['value'], domain=c.get('domain', ''), path=c.get('path', '/'))

        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/125.0.0.0 Safari/537.36'
            )
        }

        response = requests.get(url, stream=True, timeout=300, cookies=jar, headers=headers)
        response.raise_for_status()

        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    def _extract_7z(self, archive_path: str, extract_dir: str, password: str | None = None) -> bool:
        """Extract 7z archive to directory"""
        import subprocess
        from browser_max import SEVEN_ZIP_EXE

        if not os.path.exists(SEVEN_ZIP_EXE):
            self.logger.error(f"7z not found at {SEVEN_ZIP_EXE}")
            return False

        cmd = [SEVEN_ZIP_EXE, "x", archive_path, f"-o{extract_dir}", "-y"]
        if password:
            cmd.extend([f"-p{password}"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            self.logger.error("7z extract timeout")
            return False
        except Exception as e:
            self.logger.error(f"7z extract error: {e}")
            return False

    # ── Main Menu ──

    def run(self):
        """Главный цикл backuper"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._show_menu()
            choice = input("  Выберите действие [0-2]: ").strip()

            if choice == "0":
                break
            elif choice == "1":
                self.run_backup()
            elif choice == "2":
                self.run_restore()
            else:
                print("\n  Неверный выбор. Нажмите 0..2.")
                time.sleep(1)

        self._close_browser()

    def _show_menu(self):
        print("\n" + "=" * 60)
        print("  Backuper — резервное хранение в MAX")
        print("=" * 60)

        stats = self.journal.get_stats()
        print(f"  Журнал: {stats['total_backups']} бэкапов "
              f"({stats['uploaded']} отправлено, {stats['failed']} ошибок)")
        print(f"  Восстановлено: {stats['completed_downloads']} "
              f"из {stats['total_downloads']} попыток")
        print(f"  Сохранённых паролей: {stats['passwords_stored']}")
        print("─" * 60)
        print()
        print("  [1] Бэкап — архивировать папку в канал")
        print("  [2] Восстановление — скачать архивы из канала")
        print("  [0] Назад")
        print()


def main():
    """Точка входа для standalone запуска"""
    from logging_config import setup_logging, SessionCapture
    import signal

    load_dotenv()
    session = SessionCapture()
    session.start()
    print(f"Session log: {session.path}")

    logger = setup_logging(log_file="archiver.log", level=10)
    config_path = "config.yaml"

    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    try:
        backuper = Backuper(config_path)
        backuper.run()
    except KeyboardInterrupt:
        print("\n  Прервано.")
        sys.exit(0)
    except Exception as e:
        print(f"\n  ✗ Критическая ошибка: {e}")
        sys.exit(1)
    finally:
        session.stop()


if __name__ == "__main__":
    main()
