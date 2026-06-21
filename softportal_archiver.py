#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SoftPortal Archiver — Топ программ SoftPortal в MAX канал

Самостоятельный скрипт для загрузки топ программ из SoftPortal
и их публикации в отдельный канал MAX (текстовые сообщения).
"""

import os
import time
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
                self.logger.debug("Cleanup failed (ignored)")

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
    def _print_progress(current: int, total: int, sent: int, failed: int,
                        debt: int = 0, status: str = ""):
        """Print progress bar for load operations"""
        if total == 0:
            return
        pct = int(current / total * 100)
        filled = int(pct // 10)
        bar = "█" * filled + "░" * (10 - filled)
        parts = f"✓{sent} | ✗{failed}"
        if debt:
            parts += f" | ⏸{debt}"
        print(f"\r  Прогресс: {current}/{total} | {bar} {pct}% | "
              f"{parts} {status}",
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

    # ── File download and upload helpers ──

    def _download_file(self, url: str, filename: str, output_dir: str,
                       browser: BrowserMAX) -> str | None:
        """
        Скачать файл по URL через Playwright браузер.

        SoftPortal использует страницы с обратным отсчётом и редиректами
        перед началом реальной загрузки. requests.get() получает HTML вместо файла,
        поэтому используем браузер для прохождения редиректов.

        Для SoftPortal-hosted вариантов:
        1. Переходим на getsoft URL
        2. Ждём редиректа на страницу программы
        3. Кликаем кнопку скачивания
        4. Ждём начала загрузки

        Args:
            url: URL для скачивания (getsoft страница или прямой URL)
            filename: Имя сохраняемого файла
            output_dir: Директория для сохранения
            browser: Подключённый экземпляр BrowserMAX

        Returns:
            Полный путь к файлу или None при ошибке
        """
        try:
            os.makedirs(output_dir, exist_ok=True)
            file_path = os.path.join(output_dir, filename)
            self.logger.info(f"Downloading via browser: {url}")

            ctx = browser._get_context()
            if ctx is None:
                self.logger.error("No browser context available for download")
                return None

            dl_page = ctx.new_page()
            try:
                # Navigate to getsoft URL
                dl_page.goto(url, wait_until="commit", timeout=60000)

                # Wait for page to stabilize after redirect
                dl_page.wait_for_load_state("networkidle", timeout=10000)

                # Try clicking the download button
                btn = dl_page.query_selector('a.btn-download[href]')
                if btn:
                    btn_href = btn.get_attribute('href')
                    self.logger.info(f"Found download button: {btn_href}")

                    # Click button and wait for download
                    with dl_page.expect_download(timeout=60000) as dl_info:
                        btn.click()

                    download = dl_info.value
                    download.save_as(file_path)
                else:
                    # No button found, try direct download (for non-SoftPortal URLs)
                    with dl_page.expect_download(timeout=60000) as dl_info:
                        dl_page.goto(url, wait_until="commit", timeout=60000)

                    download = dl_info.value
                    download.save_as(file_path)

                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    self.logger.info(f"Downloaded: {file_path}")
                    return file_path
                self.logger.warning(f"Download produced empty file: {file_path}")
                return None
            except Exception as e:
                self.logger.error(f"Download failed {filename}: {e}")
                return None
            finally:
                try:
                    dl_page.close()
                except Exception:
                    pass
        except Exception as e:
            self.logger.error(f"Download error {filename}: {e}")
            return None

    def _wrap_in_zip(self, file_path: str) -> str | None:
        """
        Оборачивает .exe/.msi в .zip архив.

        Args:
            file_path: Путь к исходному файлу

        Returns:
            Путь к .zip архиву или None при ошибке
        """
        import zipfile
        lower = file_path.lower()
        if not (lower.endswith('.exe') or lower.endswith('.msi')):
            return file_path  # Не нужно оборачивать

        zip_path = file_path + '.zip'
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(file_path, os.path.basename(file_path))
            self.logger.info(f"Wrapped in zip: {os.path.basename(zip_path)}")
            return zip_path
        except Exception as e:
            self.logger.error(f"Zip error {file_path}: {e}")
            return None

    def _send_file_to_channel(self, browser: BrowserMAX, file_path: str,
                              message: str) -> bool:
        """
        Отправить файл в MAX канал.

        Args:
            browser: Экземпляр BrowserMAX
            file_path: Путь к файлу
            message: Текст сообщения к файлу

        Returns:
            True при успехе
        """
        try:
            retries = self.config.get('archiver', {}).get('retries', 3)
            retry_delay = self.config.get('archiver', {}).get('retry_delay', 10)
            success, _ = browser.send_message_with_files(
                text=message,
                filepaths=[file_path],
                retries=retries,
                retry_delay=retry_delay,
                expected_extensions=['.exe', '.msi', '.zip', '.7z', '.rar'],
            )
            return success
        except Exception as e:
            self.logger.error(f"Send file error {file_path}: {e}")
            return False

    def _cleanup_file(self, file_path: str):
        """Удалить временный файл."""
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                self.logger.debug(f"Cleaned up: {file_path}")
        except Exception:
            pass

    def _cleanup_files(self, *paths: str | None):
        """Удалить несколько временных файлов."""
        for p in paths:
            self._cleanup_file(p)

    # ── Category configuration ──

    def ensure_categories_configured(self) -> list[int]:
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
        print("  [0] Выбрать все")
        print()

        try:
            choice = input(
                "  Выберите платформы (номера через запятую, "
                "или Enter / 0 для всех): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            return []

        if choice == '' or choice == '0':
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
        # NOTE: SoftPortal TOP pages only work for platforms (Windows, Android, etc.)
        # Subcategories return 0 programs — they are skipped automatically but waste time.
        if subcategories:
            print()
            print("  ── Подкатегории (опционально) ──")
            print("  ⚠ TOP-страницы есть только для платформ. Подкатегории")
            print("    вернут 0 программ и будут пропущены.")
            for i, sc in enumerate(subcategories, 1):
                print(f"  [{i}] {sc['name']} (id={sc['id']})")
            print("  [0] Выбрать все (не рекомендуется)")
            print("  [Enter] Пропустить (рекомендуется)")
            print()

            try:
                sub_choice = input(
                    "  Добавить подкатегории (номера через запятую, "
                    "или Enter / 0 для всех): "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                sub_choice = ''

            if sub_choice == '' or sub_choice == '0':
                selected_ids.extend(sc['id'] for sc in subcategories)
            elif sub_choice:
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
        category_ids = self.ensure_categories_configured()
        if not category_ids:
            print("\n  ✗ Нет настроенных категорий")
            return

        print(f"\n  Категории: {len(category_ids)} | Лимит: {limit}")

        # Fetch top programs for each category
        # SoftPortal only has TOP pages for platforms, subcategories return 0 programs.
        all_programs = []
        empty_cats = 0
        for cat_id in category_ids:
            try:
                programs = self.sp.get_top_programs(cat_id, f"cat-{cat_id}", limit)
                if not programs:
                    empty_cats += 1
                    continue
                for p in programs:
                    p['_platform_id'] = cat_id
                all_programs.extend(programs)
                print(f"  ✓ cat-{cat_id}: {len(programs)} программ")
            except SoftPortalAPIError as e:
                print(f"  ✗ cat-{cat_id}: {e}")

        if empty_cats:
            print(f"  (пропущено {empty_cats} пустых категорий — TOP только для платформ)")

        if not all_programs:
            print("\n  ✗ Не удалось получить программы")
            return

        # Dedup by journal: (id, platform_id) — skip processed, include failed for retry
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

        # Config
        output_dir = self.config.get('softportal_archiver', {}).get(
            'output_dir', './temp_softportal')
        download_enabled = self.config.get('softportal_archiver', {}).get(
            'download_files', True)

        # Process each program
        sent = 0
        failed = 0
        debt = 0

        for i, prog in enumerate(to_process, 1):
            if self._shutdown:
                print(f"\n  ⚠ Прервано после {i - 1} программ")
                break

            name = prog.get('name', '')
            version = prog.get('version', '')
            prog_id = prog.get('id')
            slug = prog.get('slug', '')
            print(f"\n  [{i}/{total}] {name} {version}")

            # Get detail for breadcrumb path
            try:
                detail = self.sp.get_program_detail(prog_id, slug)
            except SoftPortalAPIError as e:
                print(f"  ✗ Детали: {e}")
                failed += 1
                self._print_progress(i, total, sent, failed, debt, "✗")
                continue

            # Platform ID from breadcrumb (first element = platform)
            breadcrumb = detail.get('full_category_path', [])
            platform_id = str(breadcrumb[0][0]) if breadcrumb else str(prog.get('_platform_id', ''))

            # ── Download file FIRST (before publishing anything) ──
            file_path = None
            zip_path = None
            file_ok = True

            if download_enabled:
                try:
                    dl_urls = self.sp.get_download_urls(prog_id, slug)
                except SoftPortalAPIError as e:
                    dl_urls = []
                    self.logger.warning(f"Download URLs error: {e}")

                if dl_urls:
                    # Prefer internal variant 3 (64-bit) > variant 1 (32-bit) > variant 2 (official)
                    candidates = sorted(dl_urls,
                        key=lambda v: (0 if not v['is_official'] else 1, -v['variant']))
                    best = candidates[0]
                    dl_url = best['url']
                    dl_filename = best['filename']

                    print(f"  ⬇ Скачиваю: {dl_filename}")
                    file_path = self._download_file(dl_url, dl_filename, output_dir, browser)

                    if file_path and os.path.exists(file_path):
                        zip_path = self._wrap_in_zip(file_path)
                        final_path = zip_path or file_path

                        # Verify the file is actually a real file (not HTML error page)
                        final_size = os.path.getsize(final_path)
                        if final_size < 1024:
                            # Too small — likely an HTML error page, not a real file
                            file_ok = False
                            self.logger.warning(f"File too small ({final_size}B), likely error page: {dl_filename}")
                            print(f"  ⚠ Файл подозрительно мал ({final_size} Б)")
                            self._cleanup_files(file_path, zip_path)
                            file_path = None
                            zip_path = None
                    else:
                        file_ok = False
                        print(f"  ⚠ Не удалось скачать файл")
                else:
                    file_ok = False
                    print(f"  ⚠ Нет ссылок для скачивания")
            else:
                print(f"  (скачивание файлов отключено)")

            # ── If download failed → mark as debt, don't publish anything ──
            if not file_ok or (download_enabled and file_path is None):
                self.journal.mark_failed(str(prog_id), platform_id, {
                    'name': name,
                    'version': version,
                    'detail': detail,
                })
                debt += 1
                print(f"  ⏸ Записано в долги (не будет опубликовано)")
                self._cleanup_files(file_path, zip_path)
                self._print_progress(i, total, sent, failed, debt, "⏸")
                continue

            # ── Download succeeded → publish text + file ──
            text = self._build_message_text({
                **detail,
                'program_url': prog.get('program_url', ''),
            })

            text_ok = self._send_text_message(browser, text)
            if not text_ok:
                print(f"  ✗ Ошибка отправки текста")
                failed += 1
                self._print_progress(i, total, sent, failed, debt, "✗")
                self._cleanup_files(file_path, zip_path)
                continue

            print(f"  ✓ Текст отправлен")

            # Send file (zip already created during download validation)
            file_sent = False
            final_path = zip_path or file_path
            if final_path:
                file_sent = self._send_file_to_channel(browser, final_path, "")

                if file_sent:
                    print(f"  📤 Файл отправлен")
                else:
                    print(f"  ⚠ Файл не отправлен в канал")

            # Cleanup
            self._cleanup_files(file_path, zip_path)

            if not file_sent and download_enabled:
                self.journal.mark_failed(str(prog_id), platform_id, {
                    'name': name,
                    'version': version,
                    'detail': detail,
                })
                debt += 1
                print(f"  ⏸ Записано в долги (файл не отправлен)")
            else:
                self.journal.mark_processed(str(prog_id), platform_id, {
                    'name': name,
                    'version': version,
                    'detail': detail,
                    'file_sent': file_sent,
                })
                sent += 1
                print(f"  ✓ Записано в журнал")

            self._print_progress(i, total, sent, failed, debt, "✓")

        # Summary
        print()
        print("\n" + "═" * 60)
        print("Загрузка завершена")
        print(f"  Обработано: {sent + failed + debt}")
        print(f"  Отправлено: {sent}")
        if failed:
            print(f"  Ошибок: {failed}")
        if debt:
            print(f"  В долгах: {debt} (перезагрузите позже)")
        print("═" * 60)

        self._close_browser()

    def retry_failed(self):
        """
        Перезагрузить программы из списка неудач.

        Flow:
        1. Load failed entries from journal
        2. For each: get detail → download file → send text + file
        3. On success: remove from failed, mark as processed
        4. On failure: keep in failed list
        """
        print("\n" + "═" * 60)
        print("          Дозагрузка неудачных программ")
        print("═" * 60)

        failed = self.journal.get_failed()
        if not failed:
            print("\n  ✓ Нет неудачных программ")
            return

        print(f"\n  Неудачных программ: {len(failed)}")
        print()

        # Show list
        for i, entry in enumerate(failed, 1):
            prog_data = entry.get('program_data', {})
            name = prog_data.get('name', 'Unknown')
            version = prog_data.get('version', '')
            print(f"  [{i}] {name} {version}")

        print()
        try:
            confirm = input("  Продолжить? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            return

        if confirm not in ('y', 'yes', 'д', 'да'):
            print("\n  Отменено.")
            return

        # Connect browser
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            return

        output_dir = self.config.get('softportal_archiver', {}).get(
            'output_dir', './temp_softportal')

        total = len(failed)
        sent = 0
        still_failed = 0

        for i, entry in enumerate(failed, 1):
            if self._shutdown:
                print(f"\n  ⚠ Прервано после {i - 1}")
                break

            prog_data = entry.get('program_data', {})
            name = prog_data.get('name', 'Unknown')
            version = prog_data.get('version', '')
            prog_id = entry.get('id')
            platform_id = entry.get('platform_id', '')
            detail = prog_data.get('detail', {})

            print(f"\n  [{i}/{total}] {name} {version}")

            # Get fresh detail
            try:
                fresh_detail = self.sp.get_program_detail(prog_id, '')
            except SoftPortalAPIError as e:
                print(f"  ✗ Детали: {e}")
                still_failed += 1
                continue

            # Get platform from breadcrumb
            breadcrumb = fresh_detail.get('full_category_path', [])
            platform_id = str(breadcrumb[0][0]) if breadcrumb else str(platform_id)

            # Download file
            file_path = None
            try:
                dl_urls = self.sp.get_download_urls(prog_id, '')
            except SoftPortalAPIError:
                dl_urls = []

            if dl_urls:
                candidates = sorted(dl_urls,
                    key=lambda v: (0 if not v['is_official'] else 1, -v['variant']))
                best = candidates[0]
                print(f"  ⬇ Скачиваю: {best['filename']}")
                file_path = self._download_file(best['url'], best['filename'],
                                               output_dir, browser)

                if file_path and os.path.exists(file_path):
                    final_size = os.path.getsize(file_path)
                    if final_size < 1024:
                        self.logger.warning(f"File too small: {final_size}B")
                        print(f"  ⚠ Файл подозрительно мал")
                        self._cleanup_file(file_path)
                        file_path = None

            if not file_path:
                print(f"  ⚠ Не удалось скачать, остаётся в долгах")
                still_failed += 1
                continue

            # Send text + file
            text = self._build_message_text({
                **fresh_detail,
                'program_url': f"/software-{prog_id}.html",
            })

            if not self._send_text_message(browser, text):
                print(f"  ✗ Ошибка отправки текста")
                self._cleanup_file(file_path)
                still_failed += 1
                continue

            print(f"  ✓ Текст отправлен")

            zip_path = self._wrap_in_zip(file_path)
            final_path = zip_path or file_path

            file_sent = self._send_file_to_channel(browser, final_path, "")
            if file_sent:
                print(f"  📤 Файл отправлен")
            else:
                print(f"  ⚠ Файл не отправлен")
                self._cleanup_files(file_path, zip_path)
                still_failed += 1
                continue

            # Cleanup
            self._cleanup_files(file_path, zip_path)

            # Remove from failed, mark as processed
            self.journal.remove_failed(str(prog_id), str(platform_id))
            self.journal.mark_processed(str(prog_id), str(platform_id), {
                'name': name,
                'version': version,
                'detail': fresh_detail,
                'file_sent': True,
            })
            sent += 1
            print(f"  ✓ Успешно дозагружено")

        # Summary
        print()
        print("\n" + "═" * 60)
        print("Дозагрузка завершена")
        print(f"  Успешно: {sent}")
        if still_failed:
            print(f"  Остаётся в долгах: {still_failed}")
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
        failed_count = stats.get('failed_count', 0)
        if failed_count:
            print(f"  Неудачные: {failed_count}")
        print("-" * 60)

        print("\n  [1] Загрузить топ программ")
        print("  [2] Синхронизировать программы")
        if failed_count:
            print("  [3] Дозагрузить неудачные")
        print("  [0] Выход")
        print()

        choice = input(f"  Ваш выбор [1/2{'/3' if failed_count else ''}/0]: ").strip()

        if choice == '1':
            self.load_top_programs()
        elif choice == '2':
            self.sync_programs()
        elif choice == '3' and failed_count:
            self.retry_failed()
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
