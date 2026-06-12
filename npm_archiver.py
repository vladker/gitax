"""
NPM Archiver — download top Node.js packages and send to MAX channel.

Follows the same pattern as PyPILibsArchiver:
- Fetches top packages via NPMAPI
- Downloads tarballs
- Sends to MAX via BrowserMAX with message formatting
- Tracks uploads in NpmJournal for deduplication
- Supports 7z splitting for large files
"""

import os
import time
import shutil
from pathlib import Path
from config import get_config, init_config
from config_utils import get_config_value, get_split_mode
from npm_api import NPMAPI
from npm_journal import NpmJournal
from logging_config import LogMixin
from browser_max import BrowserMAX


class NpmArchiver(LogMixin):
    """
    NPM package archiver.

    Downloads top NPM packages and sends them to a MAX channel.
    """

    JOURNAL_FILE = "npm_journal.json"

    def __init__(self, config_path: str = "config.yaml"):
        init_config(config_path)
        self.config = get_config().model_dump()
        self.api = NPMAPI()
        self.journal = NpmJournal(self.JOURNAL_FILE)
        self.browser = None
        self._channel_url = ""
        self._output_dir = self.config.get("npm_archiver", {}).get("output_dir", "./temp_npm")
        self._limit = self.config.get("npm_archiver", {}).get("limit", 20)
        self._retries = self.config.get("npm_archiver", {}).get("retries", 3)
        self._retry_delay = self.config.get("npm_archiver", {}).get("retry_delay", 10)
        self._split_mode = get_split_mode(self.config, "npm_archiver", "auto")

    def _cleanup(self):
        """Clean up resources."""
        if self.browser:
            self.browser.close()
            self.browser = None

    @staticmethod
    def _format_downloads(count: int) -> str:
        """Format download count for display."""
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B"
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _build_message_text(self, pkg_data: dict, file_sizes: list[int]) -> str:
        """Build message text for MAX upload."""
        name = pkg_data.get("name", "unknown")
        version = pkg_data.get("latest_version", "unknown")
        description = pkg_data.get("description", "")
        downloads = pkg_data.get("downloads_last_365_days", 0)

        lines = [
            f"📦 npm: {name}@{version}",
        ]
        if description:
            lines.append(f"   {description}")
        if downloads:
            lines.append(f"   ⬇ {self._format_downloads(downloads)} downloads/year")

        total_mb = sum(s / (1024 * 1024) for s in file_sizes)
        if file_sizes:
            if len(file_sizes) == 1:
                lines.append(f"   📎 {total_mb:.2f} MB")
            else:
                lines.append(f"   📎 {len(file_sizes)} volumes, {total_mb:.2f} MB total")

        return "\n".join(lines)

    @staticmethod
    def _print_progress(current: int, total: int, sent: int, skipped: int, failed: int):
        """Print progress bar."""
        pct = (current / total * 100) if total else 0
        bar_len = 30
        filled = int(bar_len * current / total) if total else 0
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  [{bar}] {current}/{total} ({pct:.0f}%) "
              f"✓{sent} ⊘{skipped} ✗{failed}", end="", flush=True)

    def _init_browser(self, channel_url: str):
        """Initialize browser connection."""
        self._channel_url = channel_url
        self.browser = BrowserMAX("config.yaml")
        self.browser.init_browser(channel_url)

    def _should_split(self, file_path: str) -> bool:
        """Determine if a file should be split into volumes."""
        split_threshold = self.config.get("archiver", {}).get("split_threshold_mb", 49)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        if self._split_mode == "on":
            return True
        if self._split_mode == "off":
            return False
        if self._split_mode == "prompt":
            try:
                resp = input(f"  Разделить {file_path} ({file_size_mb:.1f} MB)? [Y/n]: ").strip().lower()
                return resp not in ("n", "no")
            except (EOFError, KeyboardInterrupt):
                return file_size_mb > split_threshold
        # auto mode
        return file_size_mb > split_threshold

    def _upload_file(self, file_path: str, message_text: str) -> bool:
        """Upload a single file or split into volumes."""
        if not self._should_split(file_path):
            return self._upload_single(file_path, message_text)

        return self._upload_split(file_path, message_text)

    def _upload_single(self, file_path: str, message_text: str) -> bool:
        """Upload a single file."""
        for attempt in range(self._retries):
            try:
                self.browser.send_message(message_text)
                self.browser.upload_file(file_path)
                return True
            except Exception as e:
                self.logger.error(f"Upload attempt {attempt + 1} failed: {e}")
                if attempt < self._retries - 1:
                    time.sleep(self._retry_delay)
        return False

    def _upload_split(self, file_path: str, message_text: str) -> bool:
        """Split file into 7z volumes and upload sequentially."""
        base_name = os.path.splitext(file_path)[0]
        volume_size = self.config.get("backuper", {}).get("default_volume_size", "49M")
        seven_zip = self.config.get("backuper", {}).get("seven_zip_exe", "7z")

        try:
            cmd = [seven_zip, "a", "-v" + volume_size, f"{base_name}.7z", file_path]
            result = os.popen(" ".join(cmd)).read()
        except Exception as e:
            self.logger.error(f"7z split failed: {e}")
            return False

        volumes = sorted(
            [f for f in Path(base_name).parent.glob(f"{os.path.basename(base_name)}.7z.*")],
            key=lambda x: x.name,
        )

        all_success = True
        for volume in volumes:
            vol_msg = f"{message_text}\n   📎 Volume: {volume.name}"
            success = self._upload_single(str(volume), vol_msg)
            if not success:
                all_success = False
                break
            # Delete volume after successful upload
            try:
                os.remove(str(volume))
            except OSError:
                pass

        # Clean up remaining volumes
        for volume in volumes:
            try:
                os.remove(str(volume))
            except OSError:
                pass

        return all_success

    def load_top_packages(self, limit: int | None = None):
        """
        Load top NPM packages and upload to MAX.

        Args:
            limit: Number of packages to process (default: from config)
        """
        limit = limit or self._limit
        packages = self.api.fetch_top_packages(limit)

        if not packages:
            print("\n  ⚠ Не удалось получить список пакетов.")
            return

        print(f"\n  Загружено {len(packages)} пакетов для обработки")
        print("  Начинаю загрузку...\n")

        sent = 0
        skipped = 0
        failed = 0

        for i, pkg in enumerate(packages, 1):
            name = pkg["name"]
            version = pkg["latest_version"]

            # Check dedup
            if self.journal.exists(name, version):
                self._print_progress(i, len(packages), sent, skipped + 1, failed)
                skipped += 1
                continue

            try:
                # Download
                file_paths = self.api.download_package(name)
                if not file_paths:
                    raise ValueError(f"No files downloaded for {name}")

                file_sizes = [os.path.getsize(p) for p in file_paths]
                message = self._build_message_text(pkg, file_sizes)

                # Upload
                success = False
                for file_path in file_paths:
                    if self._upload_file(file_path, message):
                        success = True

                if success:
                    self.journal.add(
                        name=name,
                        version=version,
                        description=pkg.get("description", ""),
                        downloads=pkg.get("downloads_last_365_days", 0),
                        files=file_paths,
                    )
                    sent += 1
                    # Clean up downloaded files
                    for fp in file_paths:
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
                else:
                    self.journal.mark_failed(name, version, "Upload failed")
                    failed += 1

            except Exception as e:
                self.logger.error(f"Error processing {name}: {e}")
                self.journal.mark_failed(name, version, str(e))
                failed += 1

            self._print_progress(i, len(packages), sent, skipped, failed)

        print(f"\n\n  ✓ Завершено: {sent} загружено, {skipped} пропущено, {failed} ошибок")

    def sync_packages(self):
        """
        Sync NPM packages — check for new versions of already-uploaded packages.
        """
        packages = self.journal.get_all()
        if not packages:
            print("\n  ⚠ Журнал пуст. Нет пакетов для синхронизации.")
            return

        print(f"\n  Проверка {len(packages)} пакетов на обновления...\n")

        updated = 0
        no_change = 0
        failed = 0

        for i, pkg in enumerate(packages, 1):
            name = pkg["name"]
            saved_version = pkg["version"]

            try:
                info = self.api.get_package_info(name)
                latest_version = info["latest_version"]

                if latest_version != saved_version:
                    print(f"  🔄 {name}: {saved_version} → {latest_version}")
                    # Download and upload new version
                    file_paths = self.api.download_package(name)
                    if file_paths:
                        file_sizes = [os.path.getsize(p) for p in file_paths]
                        message = self._build_message_text(
                            {"name": name, "latest_version": latest_version,
                             "description": info.get("description", ""),
                             "downloads_last_365_days": 0},
                            file_sizes,
                        )
                        success = False
                        for file_path in file_paths:
                            if self._upload_file(file_path, message):
                                success = True

                        if success:
                            self.journal.add(
                                name=name,
                                version=latest_version,
                                description=info.get("description", ""),
                                downloads=0,
                                files=file_paths,
                            )
                            updated += 1
                            for fp in file_paths:
                                try:
                                    os.remove(fp)
                                except OSError:
                                    pass
                        else:
                            failed += 1
                    else:
                        failed += 1
                else:
                    no_change += 1

            except Exception as e:
                self.logger.error(f"Error syncing {name}: {e}")
                failed += 1

        print(f"\n  ✓ Синхронизация завершена: {updated} обновлено, "
              f"{no_change} без изменений, {failed} ошибок")

    def run(self):
        """Run the archiver with browser initialization."""
        from config_utils import get_channel_url

        channel_url = get_channel_url(
            self.config, "npm", label="NPM канал", required=False
        )
        if not channel_url:
            print("\n  ⚠ URL NPM канала не указан.")
            return

        try:
            self._init_browser(channel_url)
            self.load_top_packages()
        finally:
            self._cleanup()


def main():
    """Standalone entry point for NPM archiver."""
    archiver = NpmArchiver("config.yaml")
    archiver.run()


if __name__ == "__main__":
    main()
