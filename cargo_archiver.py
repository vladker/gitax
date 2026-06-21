#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cargo (Rust) Archiver — Топ Rust пакетов в MAX канал

Самостоятельный скрипт для загрузки топ N Rust-пакетов
и их публикации в отдельный канал MAX.
"""

import os
import sys
import time
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from logging_config import setup_logging, LogMixin, SessionCapture

from cargo_api import CratesIOAPI
from browser_max import BrowserMAX
from browser_init import BrowserInitMixin
from cargo_journal import CargoJournal
from config_utils import get_split_mode
from progressbar import LiveProgressBar
from signal_handler import SignalHandler
from utils import format_file_size


class CargoArchiver(LogMixin, BrowserInitMixin):
    """Архиватор топ Rust пакетов в MAX канал"""

    _channel_key = "cargo"
    _section_key = None

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        output_dir = self.config.get('cargo_archiver', {}).get('output_dir', './temp_cargo')
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        self.cargo = CratesIOAPI()
        self.journal = CargoJournal("cargo_journal.json")
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
        downloads = pkg_data.get('downloads', 0)
        crates_url = f"https://crates.io/crates/{name}"

        text = (
            f"🦀 {name} {version}\n\n"
            f"📝 {summary}\n\n"
            f"📥 Загрузки: {self._format_downloads(downloads)}\n"
            f"🔗 Crates.io: {crates_url}"
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

    # ── Runtime helpers ──

    def _download_file(self, url: str, filename: str, output_dir: str) -> str | None:
        """Download a file from URL to output_dir. Returns file path or None on error."""
        import requests
        import time as _time
        from tqdm import tqdm
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, filename)
        max_retries = 5
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
        }
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.get(url, timeout=300, stream=True, headers=headers)
                resp.raise_for_status()
                total = int(resp.headers.get('Content-Length', 0))
                desc = f"  ⬇ {filename}"
                with tqdm(total=total, unit='B', unit_scale=True, desc=desc, leave=False) as pbar:
                    with open(file_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                            pbar.update(len(chunk))
                return file_path
            except Exception as e:
                self.logger.warning(
                    f"Download error {filename} (attempt {attempt}/{max_retries}): {e}"
                )
                if attempt < max_retries:
                    delay = 5 * (2 ** (attempt - 1))  # 5, 10, 20, 40, 80
                    self.logger.info(f"Retrying in {delay}s...")
                    _time.sleep(delay)
                else:
                    self.logger.error(f"Download failed {filename} after {max_retries} attempts")
                    # Clean up partial download
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except Exception:
                            pass
        return None

    def _send_file_to_channel(self, file_path: str, message: str, browser) -> bool:
        """Send a file to MAX channel. Returns True on success."""
        retries = self.config.get('cargo_archiver', {}).get(
            'retries', self.config.get('archiver', {}).get('retries', 3))
        retry_delay = self.config.get('cargo_archiver', {}).get(
            'retry_delay', self.config.get('archiver', {}).get('retry_delay', 10))
        try:
            success, _ = browser.send_message_with_files(
                text=message,
                filepaths=[file_path],
                retries=retries,
                retry_delay=retry_delay,
                split_mode="auto",
                expected_extensions=['.exe', '.sh', '.pkg', '.tar.xz', '.tar.gz'],
                fast_mode=True,
            )
            return success
        except Exception as e:
            self.logger.error(f"Send error {file_path}: {e}")
            return False

    def _cleanup_file(self, file_path: str):
        """Remove a temporary file."""
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    def load_top_packages(self, limit: int | None = None):
        """Загрузить топ N Rust пакетов в MAX канал."""
        if limit is None:
            limit = self.config.get('cargo_archiver', {}).get('limit', 50)
        retries = self.config.get('cargo_archiver', {}).get(
            'retries', self.config.get('archiver', {}).get('retries', 3))
        retry_delay = self.config.get('cargo_archiver', {}).get(
            'retry_delay', self.config.get('archiver', {}).get('retry_delay', 10))
        output_dir = self.config.get('cargo_archiver', {}).get('output_dir', './temp_cargo')
        split_mode = get_split_mode(self.config, "cargo_archiver", default="off")

        print("\n" + "═" * 60)
        print("          Загрузка топ Rust пакетов")
        print("═" * 60)

        browser = self._ensure_browser_connected()

        # Fetch top crates
        print(f"\n  Загрузка списка из crates.io (до {limit} пакетов)...")
        crates = self.cargo.get_top_crates(limit=limit)
        print(f"  Получено {len(crates)} пакетов")

        if not crates:
            print("\n  ✗ Не удалось получить пакеты")
            self._close_browser()
            return

        # Filter by journal
        to_process = []
        for crate in crates:
            if not self.journal.exists_by_name(crate["name"]):
                to_process.append(crate)

        total = len(to_process)
        print(f"\n  К обработке: {total} (пропущено: {len(crates) - total})")

        if not to_process:
            print("\n  Все пакеты уже в журнале")
            self._close_browser()
            return

        # ─────────────────────────────────────────────────────
        # Phase 1: Параллельное скачивание ВСЕХ кратов
        # ─────────────────────────────────────────────────────
        print(f"\n  ⬇ Параллельное скачивание {total} кратов...")

        def _dl_crate(crate: dict) -> dict | None:
            name = crate["name"]
            version = crate["version"]
            real_url = self.cargo.get_real_crate_url(name, version)
            if not real_url:
                return None
            filename = f"{name}-{version}.crate"
            filepath = os.path.join(output_dir, filename)
            try:
                resp = self.cargo.session.get(real_url, timeout=120, stream=True)
                resp.raise_for_status()
                with open(filepath, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                return {
                    "crate": crate,
                    "filepath": filepath,
                    "size": os.path.getsize(filepath),
                }
            except Exception as e:
                self.logger.error(f"Download error {name} {version}: {e}")
                return None

        downloaded = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_dl_crate, c): c for c in to_process}
            with LiveProgressBar(total, "Скачивание") as bar:
                done_count = 0
                for future in as_completed(futures):
                    done_count += 1
                    result = future.result()
                    if result:
                        downloaded.append(result)
                    else:
                        crate = futures[future]
                        self.journal.mark_failed(
                            crate["name"], crate["version"],
                            crate.get("description", ""), crate.get("downloads", 0)
                        )
                    bar.update(done_count)

        print(f"\n  Скачано: {len(downloaded)}/{total}")

        if not downloaded:
            print("\n  ✗ Не удалось скачать ни одного крата")
            self._close_browser()
            return

        # ─────────────────────────────────────────────────────
        # Phase 2: Индивидуальная отправка каждого крата
        # ─────────────────────────────────────────────────────
        sent = 0
        failed = 0

        print(f"\n  📤 Отправка {len(downloaded)} кратов...\n")

        with LiveProgressBar(len(downloaded), "Отправка") as bar:
            for idx, d in enumerate(downloaded, 1):
                crate = d["crate"]
                name = crate["name"]
                version = crate["version"]

                msg_text = self._build_message_text(crate, [d["size"]])

                success = False
                for attempt in range(1, retries + 1):
                    try:
                        success, _ = browser.send_message_with_files(
                            text=msg_text,
                            filepaths=[d["filepath"]],
                            retries=1,
                            retry_delay=0,
                            split_mode=split_mode,
                            expected_extensions=['.crate'],
                            fast_mode=True,
                        )
                        break
                    except Exception as e:
                        if attempt < retries:
                            time.sleep(retry_delay)

                if success:
                    self.journal.add(name, version, crate.get("description", ""),
                                     crate.get("downloads", 0), [d["filepath"]])
                    sent += 1
                else:
                    self.journal.mark_failed(name, version, crate.get("description", ""),
                                             crate.get("downloads", 0))
                    failed += 1

                # Cleanup immediately
                try:
                    if os.path.exists(d["filepath"]):
                        os.remove(d["filepath"])
                except Exception:
                    pass

                bar.update(idx, item_name=f"{name} {'✓' if success else '✗'}")
                # NO delay between crates!

        print(f"\n  Итого: ✓{sent} | ✗{failed}")
        if total > 0:
            pct = int(sent / total * 100)
            print(f"  Эффективность: {pct}% ({sent}/{total})")
        self._close_browser()

    def load_runtime(self):
        """Загрузить Rust runtime installer (первичная загрузка)."""
        from datetime import datetime
        from runtime_api import RuntimeFactory, OSTarget

        runtime_cfg = self.config.get("runtime", {})
        if not runtime_cfg.get("enabled", True):
            self.logger.info("Runtime sync disabled in config")
            return

        runtime = RuntimeFactory.get_runtime("cargo")
        print(f"\n  {RuntimeFactory.get_icon('rust')} Загрузка Rust runtime...")

        latest = runtime.get_latest_version()
        if not latest:
            print("  ⚠ Не удалось получить версию Rust. Пропуск.")
            return

        print(f"  📦 Rust {latest} — загрузка инсталляторов для всех ОС...")

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
                    message=f"Rust {latest} — {os_name} installer\n\n{RuntimeFactory.get_download_page('rust')}",
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
            print(f"\n  ✓ Rust runtime {latest} загружен в журнал")
        else:
            print("\n  ✗ Не удалось загрузить runtime")

        self._close_browser()

    def sync_runtimes(self):
        """Check and sync Rust runtime installer if a newer version is available."""
        from datetime import datetime
        from runtime_api import RuntimeFactory, OSTarget

        runtime_cfg = self.config.get("runtime", {})
        if not runtime_cfg.get("enabled", True):
            self.logger.info("Runtime sync disabled in config")
            return

        runtime = RuntimeFactory.get_runtime("cargo")
        print(f"\n  {RuntimeFactory.get_icon('rust')} Проверяю Rust runtime...")

        latest = runtime.get_latest_version()
        if not latest:
            print("  ⚠ Не удалось получить версию Rust. Пропуск.")
            return

        if not self.journal.should_update_runtime(latest):
            saved = self.journal.get_runtime_version()
            print(f"  ✓ Rust {saved} — актуален")
            return

        print(f"  🆕 Rust {latest} доступен (текущий: {self.journal.get_runtime_version() or 'не установлен'})")
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
                    message=f"Rust {latest} — {os_name} installer\n\n{RuntimeFactory.get_download_page('rust')}",
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
            print(f"\n  ✓ Rust runtime {latest} обновлён в журнале")
        else:
            print("\n  ✗ Не удалось обновить runtime")

        self._close_browser()

    def sync_packages(self):
        """Синхронизировать версии Rust пакетов."""
        print("\n" + "═" * 60)
        print("          Синхронизация Rust пакетов")
        print("═" * 60)

        browser = self._ensure_browser_connected()
        entries = self.journal.get_all()
        if not entries:
            print("\n  Журнал пуст. Нечего синхронизировать.")
            self._close_browser()
            return

        output_dir = self.config.get('cargo_archiver', {}).get('output_dir', './temp_cargo')
        retries = self.config.get('cargo_archiver', {}).get(
            'retries', self.config.get('archiver', {}).get('retries', 3))
        retry_delay = self.config.get('cargo_archiver', {}).get(
            'retry_delay', self.config.get('archiver', {}).get('retry_delay', 10))
        split_mode = get_split_mode(self.config, "cargo_archiver", default="off")

        updated = 0
        unchanged = 0
        failed = 0

        for idx, entry in enumerate(entries, 1):
            name = entry.get("name", "")
            old_version = entry.get("version", "")
            print(f"\n  [{idx}/{len(entries)}] Проверка {name} {old_version}...")

            latest = self.cargo.get_latest_version(name)
            if not latest or latest == old_version:
                unchanged += 1
                print(f"  – Актуальная версия")
                continue

            print(f"  ⬆ Обновление: {old_version} → {latest}")

            # Получаем реальный URL .crate файла (API возвращает JSON с URL, не редирект)
            real_url = self.cargo.get_real_crate_url(name, latest)
            if not real_url:
                print(f"  ✗ Не удалось получить URL скачивания для {name} {latest}")
                failed += 1
                continue

            filename = f"{name}-{latest}.crate"
            filepath = os.path.join(output_dir, filename)

            try:
                resp = self.cargo.session.get(real_url, timeout=120, stream=True)
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
                base = filepath.replace('.crate', '')
                volumes = self._split_to_volumes(base)
                if volumes:
                    files_to_send = volumes
                    file_sizes = [os.path.getsize(v) for v in volumes]

            msg_text = self._build_message_text({
                "name": name,
                "version": latest,
                "description": entry.get("description", ""),
                "downloads": entry.get("downloads", 0),
            }, file_sizes)

            success = False
            for attempt in range(1, retries + 1):
                try:
                    success, _ = browser.send_message_with_files(
                        text=msg_text,
                        filepaths=files_to_send,
                        retries=1,
                        retry_delay=0,
                        split_mode=split_mode,
                        expected_extensions=['.crate', '.7z', '.7z.001'],
                        fast_mode=True,
                    )
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

        print(f"\n  Итого: ⬆{updated} | ={unchanged} | ✗{failed}")
        self._close_browser()

    def _split_to_volumes(self, filepath: str) -> list[str]:
        """Split file into 7z volumes."""
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
        """Remove temporary files."""
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

    logger = setup_logging(log_file="cargo_archiver.log", level=10)
    archiver = CargoArchiver("config.yaml")
    archiver.load_top_packages()

    session.stop()


if __name__ == "__main__":
    main()
