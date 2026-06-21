# -*- coding: utf-8 -*-
"""
7-Zip utility functions for file splitting and archiving.

Extracted from browser_max.py for modular architecture.
Used by: backuper.py, browser_max.py
"""

from __future__ import annotations

import glob
import os
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

_logger = logging.getLogger("gitax")


@dataclass
class ContentSnapshot:
    """Content snapshot with hash for change detection."""
    hash: str
    file_count: int


def _get_seven_zip_exe() -> str:
    """Get 7z executable path from config."""
    from config import get_config
    return get_config().backuper.seven_zip_exe


def _get_large_file_threshold() -> int:
    """Get large file threshold in bytes from config (default 50 MB)."""
    from config import get_config
    return get_config().archiver.large_file_threshold_mb * 1024 * 1024


def split_file_with_7z(filepath: str, volume_size: str | None = None) -> list[str]:
    """
    Split a file into volumes using 7z.

    Args:
        filepath: Path to file to split
        volume_size: Volume size (e.g., "49M", "100M"). Default: from config.

    Returns:
        List of volume file paths (e.g., ['file.7z.001', 'file.7z.002', ...])
        Returns empty list if split failed or file is small enough.
    """
    if volume_size is None:
        from config import get_config
        volume_size = get_config().backuper.default_volume_size
    seven_zip_exe = _get_seven_zip_exe()

    if not os.path.exists(filepath):
        return []

    file_size = os.path.getsize(filepath)
    volume_bytes = _parse_size(volume_size)

    # No split needed if file is smaller than threshold
    if file_size <= volume_bytes:
        return []

    filename = os.path.basename(filepath)
    output_base = filepath + ".7z"

    # Remove any existing volumes with same base
    _cleanup_existing_volumes(output_base)

    cmd = [
        seven_zip_exe,
        "a",
        "-v" + volume_size,  # Volume size (e.g., -v49m)
        "-mx=0",             # No compression (faster, raw split)
        output_base,
        filepath
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour max for large files
        )

        if result.returncode != 0:
            _logger.warning(f"7z split failed: {result.stderr}")
            _cleanup_existing_volumes(output_base)
            return []

        # Find created volumes
        volumes = _find_volumes(output_base)

        if volumes:
            _logger.info(f"Split into {len(volumes)} volumes: {volumes[0]}...")
            return volumes
        else:
            _logger.warning("7z succeeded but no volumes found")
            return []

    except subprocess.TimeoutExpired:
        _logger.error("7z split timeout")
        _cleanup_existing_volumes(output_base)
        return []
    except FileNotFoundError:
        _logger.error(f"7z not found at {seven_zip_exe}")
        return []
    except Exception as e:
        _logger.error(f"7z split error: {e}")
        _cleanup_existing_volumes(output_base)
        return []


def _parse_size(size_str: str) -> int:
    """Parse size string like '49M' or '1G' to bytes"""
    size_str = size_str.upper().strip()
    multipliers = {
        'K': 1024,
        'M': 1024 * 1024,
        'G': 1024 * 1024 * 1024
    }

    for suffix, mult in multipliers.items():
        if size_str.endswith(suffix):
            try:
                return int(float(size_str[:-1]) * mult)
            except ValueError:
                pass

    # Plain number
    try:
        return int(size_str)
    except ValueError:
        return 0


def _cleanup_existing_volumes(base_path: str):
    """Remove any existing volume files matching base pattern"""
    pattern = base_path + ".*"
    for f in glob.glob(pattern):
        try:
            os.remove(f)
        except Exception:
            pass


def _find_volumes(base_path: str) -> list[str]:
    """Find all volume files matching base.7z.xxx pattern, sorted"""
    pattern = base_path + ".*"
    volumes = sorted(glob.glob(pattern))
    return volumes


def _retry_delete(filepath: str, max_retries: int = 5, delay: float = 1.0) -> bool:
    """Delete a file with retries — handles Windows file-lock race conditions."""
    import time
    for attempt in range(1, max_retries + 1):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
            return True  # already gone
        except (OSError, PermissionError) as e:
            if attempt < max_retries:
                _logger.debug(f"Retry delete {os.path.basename(filepath)} ({attempt}/{max_retries}): {e}")
                time.sleep(delay)
            else:
                _logger.warning(f"Failed to delete {filepath} after {max_retries} attempts: {e}")
                return False
    return False


def cleanup_volumes(volume_paths: list[str]):
    """Remove volume files after successful upload"""
    for vp in volume_paths:
        _retry_delete(vp)


def group_volumes(filenames: list[str]) -> list[dict]:
    """
    Group 7z volume files by base archive name.

    Groups:
    - "documents.7z.001", "documents.7z.002" -> base "documents.7z"
    - "photos.7z" -> base "photos.7z"

    Args:
        filenames: List of 7z-related filenames

    Returns:
        List of dicts: [{"base_name": "docs.7z", "volume_count": 3,
                         "volumes": ["docs.7z.001", ...]}, ...]
    """
    import re
    groups: dict[str, list[str]] = {}
    for fn in filenames:
        m = re.match(r'^(.+\.7z)(\.\d+)?$', fn)
        if m:
            base = m.group(1)
            groups.setdefault(base, []).append(fn)
    result = []
    for base, volumes in groups.items():
        result.append({
            "base_name": base,
            "volume_count": len(volumes),
            "volumes": sorted(volumes),
        })
    return result


def archive_file_as_7z(
    file_path: str,
    password: str,
    compression_level: int = 5,
    output_dir: str | None = None,
) -> str | None:
    """
    Create a single-volume password-protected 7z archive from a single file.

    The archive is created alongside the original file (or in output_dir) with
    ``.7z`` appended to the original filename.

    Args:
        file_path: Path to the file to archive.
        password: Encryption password.
        compression_level: 7z compression level 0-9 (default 5).
        output_dir: Optional output directory; defaults to the source file's directory.

    Returns:
        Path to the created archive, or None on failure.
    """
    if not os.path.isfile(file_path):
        _logger.error(f"File not found: {file_path}")
        return None

    seven_zip_exe = _get_seven_zip_exe()
    if not os.path.exists(seven_zip_exe):
        _logger.error(f"7z not found at {seven_zip_exe}")
        return None

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        archive_name = os.path.basename(file_path) + ".7z"
        archive_path = os.path.join(output_dir, archive_name)
    else:
        archive_path = file_path + ".7z"

    # Clean up any leftover archive from a previous attempt
    if os.path.exists(archive_path):
        try:
            os.remove(archive_path)
        except OSError:
            pass

    cmd = [seven_zip_exe, "a", f"-mx={compression_level}", archive_path, file_path]

    # Use password file to avoid leaking in process list
    password_tempfile = None
    try:
        password_tempfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        password_tempfile.write(password)
        password_tempfile.close()
        try:
            os.chmod(password_tempfile.name, 0o600)
        except OSError:
            pass
        cmd.insert(2, f"-p@{password_tempfile.name}")
        cmd.insert(2, "-mhe=on")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)

        if result.returncode != 0:
            _logger.warning(f"7z single-file archive failed: {result.stderr}")
            if os.path.exists(archive_path):
                os.remove(archive_path)
            return None

        if os.path.exists(archive_path):
            _logger.info(
                f"Created password-protected archive: {archive_path} "
                f"({os.path.getsize(archive_path) / 1024:.1f} KB)"
            )
            return archive_path
        else:
            _logger.warning("7z succeeded but no output file found")
            return None

    except subprocess.TimeoutExpired:
        _logger.error("7z single-file archive timeout")
        if os.path.exists(archive_path):
            os.remove(archive_path)
        return None
    except FileNotFoundError:
        _logger.error(f"7z not found at {seven_zip_exe}")
        return None
    except Exception as e:
        _logger.error(f"7z single-file archive error: {e}")
        if os.path.exists(archive_path):
            os.remove(archive_path)
        return None
    finally:
        if password_tempfile is not None:
            try:
                os.unlink(password_tempfile.name)
            except OSError:
                pass


def archive_directory_to_volumes(
    source_dir: str,
    output_base: str,
    volume_size: str | None = None,
    compression_level: int = 5,
    password: str | None = None,
    clean_existing: bool = True
) -> list[str]:
    """
    Archive an entire directory into 7z volumes with compression and optional password.

    Unlike split_file_with_7z() which does raw split (-mx=0), this creates
    proper compressed 7z archives with encryption.

    Args:
        source_dir: Path to directory to archive
        output_base: Base path for output (e.g., "./temp/name.7z")
        volume_size: Volume size string (e.g., "49M"). None for single archive.
        compression_level: 7z compression level 0-9 (default 5)
        password: Optional encryption password
        clean_existing: Remove existing volumes before archiving

    Returns:
        List of volume file paths, or empty list on failure.
    """
    if volume_size is None:
        from config import get_config
        volume_size = get_config().backuper.default_volume_size
    seven_zip_exe = _get_seven_zip_exe()

    if not os.path.isdir(source_dir):
        _logger.error(f"Source directory not found: {source_dir}")
        return []
    if not os.path.exists(seven_zip_exe):
        _logger.error(f"7z not found at {seven_zip_exe}")
        return []
    if clean_existing:
        _cleanup_existing_volumes(output_base)
    cmd = [seven_zip_exe, "a", f"-mx={compression_level}", output_base, source_dir + os.sep]
    if volume_size:
        cmd.insert(2, "-v" + volume_size)

    # Use password file instead of CLI argument to avoid leaking in process list
    password_tempfile = None
    if password:
        password_tempfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        password_tempfile.write(password)
        password_tempfile.close()
        try:
            os.chmod(password_tempfile.name, 0o600)
        except OSError:
            pass  # Windows may not support chmod; file is still private via temp dir
        cmd.insert(2, f"-p@{password_tempfile.name}")
        cmd.insert(2, "-mhe=on")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if result.returncode != 0:
            _logger.warning(f"7z archive failed: {result.stderr}")
            if clean_existing:
                _cleanup_existing_volumes(output_base)
            return []
        volumes = _find_volumes(output_base)
        if not volumes and not volume_size:
            single = output_base if output_base.endswith('.7z') else output_base + '.7z'
            if os.path.exists(single):
                volumes = [single]
        if volumes:
            total_size = sum(os.path.getsize(v) for v in volumes if os.path.exists(v))
            _logger.info(
                f"Archived {source_dir} -> {len(volumes)} volume(s), "
                f"total {total_size / 1024 / 1024:.1f} MB"
            )
            return volumes
        else:
            _logger.warning("7z succeeded but no output files found")
            return []
    except subprocess.TimeoutExpired:
        _logger.error("7z archive timeout")
        _cleanup_existing_volumes(output_base)
        return []
    except FileNotFoundError:
        _logger.error(f"7z not found at {seven_zip_exe}")
        return []
    except Exception as e:
        _logger.error(f"7z archive error: {e}")
        _cleanup_existing_volumes(output_base)
        return []
    finally:
        if password_tempfile is not None:
            try:
                os.unlink(password_tempfile.name)
            except OSError:
                pass
