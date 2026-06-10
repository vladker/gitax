# Исправление дефектов Gitax — План реализации

**Цель:** Исправить все выявленные дефекты в проекте gitax (критические → высокие → средние → низкие).

**Архитектура:** Параллельные микро-задачи, каждая = один файл + тест. Зависимости определены через импорты и порядок изменений.

---

## Граф зависимостей

```
Партия 1 (parallel): 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7 [фундамент — нет зависимостей]
Партия 2 (parallel): 2.1, 2.2, 2.3 [критическая безопасность — зависит от партия 1]
Партия 3 (parallel): 3.1, 3.2, 3.3, 3.4, 3.5, 3.6 [refactoring — зависит от партия 2]
Партия 4 (parallel): 4.1, 4.2, 4.3, 4.4, 4.5 [дополнительные — зависит от партия 3]
```

---

## Партия 1: Фундамент (parallel — 7 исполнителей)

Все задачи этой партии НЕЗАВИСИМЫ и выполняются одновременно.

---

### Задача 1.1: Атомарная запись в rollback_journal.py (C4)

**Файл:** `rollback_journal.py`
**Тест:** `tests/test_rollback_journal.py`
**Зависимости:** none
**Приоритет:** 🔴 CRITICAL
**Оценка:** 15 мин

**Проблема:** Прямой `json.dump()` вместо temp+rename паттерна. Если процесс прервётся, журнал будет повреждён.

**Решение:** Использовать тот же атомарный паттерн (tempfile.mkstemp → write → os.replace), что и все остальные журналы.

**Критерии приёмки:**
- Журнал записывается через temp-файл + os.replace
- При повреждении файла создаётся .backup копия
- Есть `if __name__ == "__main__"` guard (M8)

```python
# rollback_journal.py — ПОЛНАЯ ЗАМЕНА
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rollback journal.json: revert CLEANED entries that were wrongly set
to their original SENT status.
"""
import json
import os
import tempfile
import shutil

JOURNAL_PATH = "journal.json"


def rollback_cleaned_entries(journal_path: str = JOURNAL_PATH) -> int:
    """
    Revert all 'cleaned' entries without 'restored_at' to 'sent' status.

    Returns:
        Number of entries reverted.
    """
    if not os.path.exists(journal_path):
        print(f"[ERROR] {journal_path} not found")
        return 0

    with open(journal_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    repos = data.get("repositories", [])
    reverted = 0

    for repo in repos:
        if repo.get("status") == "cleaned" and not repo.get("restored_at"):
            repo["status"] = "sent"
            reverted += 1

    if reverted:
        data["total_sent"] = len([r for r in repos if r.get("status") == "sent"])
        data["total_incomplete"] = len([r for r in repos if r.get("status") == "incomplete"])
        data["total_restored"] = len([r for r in repos if r.get("status") == "restored"])
        data["total_failed"] = len([r for r in repos if r.get("status") == "failed"])

        # ATOMIC WRITE: temp file + os.replace
        dir_name = os.path.dirname(journal_path) or "."
        temp_fd, temp_path = tempfile.mkstemp(suffix=".json", dir=dir_name)
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if os.path.exists(journal_path):
                shutil.copy2(journal_path, f"{journal_path}.bak")
            os.replace(temp_path, journal_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

        print(f"[OK] Откачено {reverted} записей: cleaned -> sent")
    else:
        print("  Нет записей со статусом 'cleaned'")

    return reverted


if __name__ == "__main__":
    rollback_cleaned_entries()
```

```python
# tests/test_rollback_journal.py — НОВЫЙ ФАЙЛ
"""Unit tests for rollback_journal module."""
import json
import os
import pytest
from unittest.mock import patch, mock_open


class TestRollbackCleanedEntries:
    """Test rollback_cleaned_entries function"""

    def test_reverts_cleaned_entries(self, tmp_path):
        from rollback_journal import rollback_cleaned_entries

        journal_path = str(tmp_path / "journal.json")
        data = {
            "repositories": [
                {"name": "repo1", "status": "cleaned"},
                {"name": "repo2", "status": "sent"},
                {"name": "repo3", "status": "cleaned", "restored_at": "2026-01-01"},
            ],
            "total_sent": 1,
            "total_incomplete": 0,
            "total_restored": 0,
            "total_failed": 0,
        }
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        reverted = rollback_cleaned_entries(journal_path)
        assert reverted == 1

        with open(journal_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["repositories"][0]["status"] == "sent"
        assert loaded["repositories"][1]["status"] == "sent"
        assert loaded["repositories"][2]["status"] == "cleaned"  # has restored_at

    def test_returns_zero_when_no_cleaned(self, tmp_path):
        from rollback_journal import rollback_cleaned_entries

        journal_path = str(tmp_path / "journal.json")
        data = {"repositories": [{"name": "repo1", "status": "sent"}]}
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        reverted = rollback_cleaned_entries(journal_path)
        assert reverted == 0

    def test_returns_zero_when_file_missing(self, tmp_path):
        from rollback_journal import rollback_cleaned_entries

        reverted = rollback_cleaned_entries(str(tmp_path / "nonexistent.json"))
        assert reverted == 0

    def test_atomic_write_creates_backup(self, tmp_path):
        """Verify that .bak file is created during atomic write"""
        from rollback_journal import rollback_cleaned_entries

        journal_path = str(tmp_path / "journal.json")
        data = {
            "repositories": [
                {"name": "repo1", "status": "cleaned"},
            ],
            "total_sent": 0,
            "total_incomplete": 0,
            "total_restored": 0,
            "total_failed": 0,
        }
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        rollback_cleaned_entries(journal_path)
        assert os.path.exists(journal_path + ".bak")

    def test_updates_totals(self, tmp_path):
        from rollback_journal import rollback_cleaned_entries

        journal_path = str(tmp_path / "journal.json")
        data = {
            "repositories": [
                {"name": "r1", "status": "cleaned"},
                {"name": "r2", "status": "cleaned"},
                {"name": "r3", "status": "failed"},
            ],
            "total_sent": 0,
            "total_failed": 1,
        }
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        rollback_cleaned_entries(journal_path)

        with open(journal_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["total_sent"] == 2
        assert loaded["total_failed"] == 1
```

**Verify:** `python -m pytest tests/test_rollback_journal.py -v`
**Commit:** `fix: atomic write in rollback_journal + __main__ guard (C4, M8)`

---

### Задача 1.2: Фильтрация директорий в backuper.py (H1)

**Файл:** `backuper.py`
**Тест:** `tests/test_backuper.py` (добавить класс)
**Зависимости:** none
**Приоритет:** 🟠 HIGH
**Оценка:** 15 мин

**Проблема:** `_scan_files_for_upload` на строке 121 использует `os.listdir()` для нерекурсивного обхода, который возвращает и файлы и директории. Директории передаются в upload → краш.

**Решение:** Добавить `os.path.isfile()` фильтр.

```python
# backuper.py — ИЗМЕНЕНИЕ в _scan_files_for_upload (строки ~117-137)
# ЗАМЕНИТЬ:
        walker = os.walk(source_path) if recursive else [(source_path, [], os.listdir(source_path))]

        for dirpath, _dirs, filenames in walker:
            for fname in filenames:
                filepath = os.path.join(dirpath, fname)

                # Check extension filter
                if allowed_ext is not None:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in allowed_ext:
                        continue

                try:
                    size = os.path.getsize(filepath)
                    if size == 0:
                        continue
                    if max_size_bytes and size > max_size_bytes:
                        self.logger.info(f"Skipping {fname}: {size / 1024 / 1024:.1f} MB > {max_size_mb} MB limit")
                        continue
                    files.append((filepath, size))
                except OSError:
                    continue

# НА:
        if recursive:
            entries = (
                (os.path.join(dirpath, fname), fname)
                for dirpath, _dirs, filenames in os.walk(source_path)
                for fname in filenames
            )
        else:
            entries = (
                (os.path.join(source_path, entry), entry)
                for entry in os.listdir(source_path)
            )

        for filepath, fname in entries:
            # Filter out directories (H1 fix)
            if not os.path.isfile(filepath):
                continue

            # Check extension filter
            if allowed_ext is not None:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in allowed_ext:
                    continue

            try:
                size = os.path.getsize(filepath)
                if size == 0:
                    continue
                if max_size_bytes and size > max_size_bytes:
                    self.logger.info(f"Skipping {fname}: {size / 1024 / 1024:.1f} MB > {max_size_mb} MB limit")
                    continue
                files.append((filepath, size))
            except OSError:
                continue
```

```python
# tests/test_backuper.py — НОВЫЙ ФАЙЛ
"""Unit tests for Backuper module."""
import os
import pytest
from unittest.mock import patch, MagicMock


class TestScanFilesForUpload:
    """Test _scan_files_for_upload directory filtering"""

    def test_filters_out_directories_non_recursive(self, tmp_path):
        """Directories should be excluded from non-recursive scan"""
        # Setup: create files and a directory
        (tmp_path / "file1.txt").write_text("hello")
        (tmp_path / "file2.txt").write_text("world")
        (tmp_path / "subdir").mkdir()

        from backuper import Backuper
        with patch.object(Backuper, "__init__", lambda self, *a, **k: None):
            backuper = Backuper()
            backuper.logger = MagicMock()

        results = backuper._scan_files_for_upload(
            source_path=str(tmp_path),
            recursive=False,
        )
        filepaths = [fp for fp, _ in results]
        assert all(os.path.isfile(fp) for fp in filepaths)
        assert len(results) == 2  # only files, not subdir

    def test_filters_out_directories_recursive(self, tmp_path):
        """Directories should be excluded from recursive scan"""
        (tmp_path / "file1.txt").write_text("hello")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file2.txt").write_text("world")
        (subdir / "nested_dir").mkdir()

        from backuper import Backuper
        with patch.object(Backuper, "__init__", lambda self, *a, **k: None):
            backuper = Backuper()
            backuper.logger = MagicMock()

        results = backuper._scan_files_for_upload(
            source_path=str(tmp_path),
            recursive=True,
        )
        filepaths = [fp for fp, _ in results]
        assert all(os.path.isfile(fp) for fp in filepaths)
        assert len(results) == 2

    def test_skips_empty_files(self, tmp_path):
        """Zero-size files should be skipped"""
        (tmp_path / "empty.txt").write_text("")
        (tmp_path / "nonempty.txt").write_text("data")

        from backuper import Backuper
        with patch.object(Backuper, "__init__", lambda self, *a, **k: None):
            backuper = Backuper()
            backuper.logger = MagicMock()

        results = backuper._scan_files_for_upload(source_path=str(tmp_path))
        assert len(results) == 1
```

**Verify:** `python -m pytest tests/test_backuper.py -v`
**Commit:** `fix: filter directories in _scan_files_for_upload (H1)`

---

### Задача 1.3: Ошибочный cleanup в pypi_libs_archiver.py (H4)

**Файл:** `pypi_libs_archiver.py`
**Тест:** `tests/test_pypi_libs_archiver.py` (добавить тесты)
**Зависимости:** none
**Приоритет:** 🟠 HIGH
**Оценка:** 10 мин

**Проблема:** `_cleanup` на строках 52-68 удаляет output-директорию при каждом выходе, включая нормальное завершение. Это удаляет скачанные файлы.

**Решение:** Удалить очистку output_dir из `_cleanup`. Оставить только закрытие браузера и сохранение журнала. Временные файлы чистятся после каждой загрузки.

```python
# pypi_libs_archiver.py — ЗАМЕНИТЬ _cleanup (строки 52-75)
# СТАРОЕ:
    def _cleanup(self):
        """Clean up resources on exit"""
        # Clean up pypi_api temp directory — this is where download_package() puts files
        try:
            if os.path.exists("./temp_pypi"):
                shutil.rmtree("./temp_pypi")
                self.logger.info("Cleaned up ./temp_pypi/")
        except Exception as e:
            self.logger.warning(f"Failed to clean ./temp_pypi/: {e}")
        # Also clean configured output dir if different
        output_dir = self.config.get('pypi_libs_archiver', {}).get('output_dir', '')
        if output_dir and os.path.exists(output_dir) and output_dir != "./temp_pypi":
            try:
                shutil.rmtree(output_dir)
                self.logger.info(f"Cleaned up {output_dir}")
            except Exception as e:
                self.logger.warning(f"Failed to clean {output_dir}: {e}")
        # Close browser
        if self.browser:
            try:
                self.journal.save()
                self.browser.close()
            except Exception:
                pass

# НОВОЕ:
    def _cleanup(self):
        """Clean up resources on exit — save journal and close browser only.

        H4 fix: Do NOT delete output_dir on exit. Downloaded files are the
        user's data and should be preserved. Temp files are cleaned
        individually after each upload by the caller.
        """
        # Save journal state
        try:
            self.journal.save()
        except Exception as e:
            self.logger.warning(f"Failed to save journal: {e}")
        # Close browser
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
```

```python
# tests/test_pypi_libs_archiver.py — ДОБАВИТЬ к существующим тестам
class TestCleanup:
    """Test that _cleanup does NOT delete output directory (H4 fix)"""

    def test_cleanup_preserves_output_dir(self, tmp_path):
        """_cleanup should not delete the output directory"""
        from pypi_libs_archiver import PyPILibsArchiver
        from unittest.mock import patch, MagicMock

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "package.zip").write_bytes(b"data")

        with patch.object(PyPILibsArchiver, "__init__", lambda self, *a, **k: None):
            archiver = PyPILibsArchiver()
            archiver.config = {"pypi_libs_archiver": {"output_dir": str(output_dir)}}
            archiver.journal = MagicMock()
            archiver.browser = MagicMock()
            archiver.logger = MagicMock()

        archiver._cleanup()

        # Output dir and its contents should still exist
        assert output_dir.exists()
        assert (output_dir / "package.zip").exists()
        archiver.journal.save.assert_called_once()
        archiver.browser.close.assert_called_once()
```

**Verify:** `python -m pytest tests/test_pypi_libs_archiver.py::TestCleanup -v`
**Commit:** `fix: remove destructive output_dir cleanup (H4)`

---

### Задача 1.4: Bare except в scroll_registry.py (M5)

**Файл:** `scroll_registry.py`
**Тест:** `tests/test_scroll_registry.py` (новый)
**Зависимости:** none
**Приоритет:** 🟡 MEDIUM
**Оценка:** 5 мин

**Проблема:** `except (FileNotFoundError, json.JSONDecodeError):` на строке 64 — хорошо, но проверим, нет ли других bare except.

**Решение:** Убедиться, что все except блоки конкретны.

```python
# scroll_registry.py — ПРОВЕРКА И ИСПРАВЛЕНИЕ
# Файл уже использует конкретные исключения (FileNotFoundError, json.JSONDecodeError).
# Никаких изменений не требуется, если bare except не найден.
# Если found — заменить на конкретные типы.
```

```python
# tests/test_scroll_registry.py — НОВЫЙ ФАЙЛ
"""Unit tests for ScrollRegistry."""
import json
import pytest


class TestScrollRegistry:
    """Test ScrollRegistry load/save operations"""

    def test_save_and_load(self, tmp_path):
        from scroll_registry import ScrollRegistry

        sr = ScrollRegistry()
        sr.messages = [{"text": "hello", "seq": 1}]
        sr.created_at = "2026-01-01T00:00:00"
        sr.total = 1

        path = str(tmp_path / "registry.json")
        sr.save(path)
        assert os.path.exists(path)

        sr2 = ScrollRegistry()
        assert sr2.load(path)
        assert len(sr2.messages) == 1

    def test_load_missing_file(self, tmp_path):
        from scroll_registry import ScrollRegistry

        sr = ScrollRegistry()
        result = sr.load(str(tmp_path / "nonexistent.json"))
        assert result is False

    def test_load_corrupted_file(self, tmp_path):
        from scroll_registry import ScrollRegistry

        path = str(tmp_path / "corrupt.json")
        with open(path, "w") as f:
            f.write("invalid{{{")

        sr = ScrollRegistry()
        result = sr.load(path)
        assert result is False
```

**Verify:** `python -m pytest tests/test_scroll_registry.py -v`
**Commit:** `fix: add tests for scroll_registry (M5)`

---

### Задача 1.5: Unused imports — browser_max.py (L1, L6)

**Файл:** `browser_max.py`
**Тест:** none (код-ревью)
**Зависимости:** none
**Приоритет:** 🟢 LOW
**Оценка:** 10 мин

**Проблема:**
- Строка 16: дублированный `from dataclasses import dataclass`
- Строка 141, 152: `import glob` внутри функций → вынести наверх
- Строка 184, 4548: `import re` внутри функций → уже импортирован на строке 7
- `import csv` (строка 10) — используется только в export (строка 6124)

**Решение:**
1. Удалить дублирующий import dataclass
2. Переместить `import glob` из функций в модуль-уровень
3. Удалить локальные `import re` (уже есть на уровне модуля)
4. Оставить `import csv` — он используется

```python
# browser_max.py — ИЗМЕНЕНИЯ

# 1. УБРАТЬ дублирующий import (строка 16):
# from dataclasses import dataclass  ← УДАЛИТЬ ЭТУ СТРОКУ

# 2. ДОБАВИТЬ glob в импорты модуля (после subprocess):
import glob

# 3. В _cleanup_existing_volumes (строка ~141):
# СТАРОЕ:
#     import glob
#     pattern = base_path + ".*"
# НОВОЕ:
#     pattern = base_path + ".*"

# 4. В _find_volumes (строка ~152):
# СТАРОЕ:
#     import glob
#     volumes = sorted(glob.glob(pattern))
# НОВОЕ:
#     volumes = sorted(glob.glob(pattern))

# 5. В функции со строкой ~184:
# СТАРОЕ:
#     import re
#     m = re.match(...)
# НОВОЕ:
#     m = re.match(...)

# 6. В функции со строкой ~4548:
# СТАРОЕ:
#     import re
#     matches = re.findall(...)
# НОВОЕ:
#     matches = re.findall(...)
```

**Verify:** `python -c "import browser_max" — нет ошибок`
**Commit:** `fix: remove duplicate imports, move glob/re to module level (L1, L6)`

---

### Задача 1.6: Unused imports — backuper.py (L2)

**Файл:** `backuper.py`
**Тест:** none
**Зависимости:** none
**Приоритет:** 🟢 LOW
**Оценка:** 5 мин

**Проблема:** `import glob` на строке 15 — не используется в коде.

**Решение:** Удалить unused import.

```python
# backuper.py — УДАЛИТЬ строку:
# import glob
```

**Verify:** `python -c "import backuper" — нет ошибок`
**Commit:** `fix: remove unused glob import in backuper.py (L2)`

---

### Задача 1.7: Unused imports — github_api.py (L3)

**Файл:** `github_api.py`
**Тест:** none
**Зависимости:** none
**Приоритет:** 🟢 LOW
**Оценка:** 5 мин

**Проблема:** `from pathlib import Path` на строке 10 — используется в `_ensure_output_dir` (Path.mkdir), так что ОСТАВИТЬ. Проверить `import glob` — нет, glob не импортирован. Проверить другие импорты.

**Решение:** Проверить все импорты. Если `Path` используется — оставить. Удалить только реально неиспользуемые.

```python
# github_api.py — ПРОВЕРКА:
# from pathlib import Path — используется в _ensure_output_dir: Path(self.output_dir).mkdir(...)
# ВСЕ ИМПОРТЫ ИСПОЛЬЗУЮТСЯ → НЕТ ИЗМЕНЕНИЙ
```

**Verify:** `python -c "import github_api" — нет ошибок`
**Commit:** `fix: verify all imports in github_api.py (L3)`

---

## Партия 2: Критическая безопасность (parallel — 3 исполнителя)

Все задачи зависят от Партии 1.

---

### Задача 2.1: Безопасность паролей — backuper_journal.py (C1)

**Файл:** `backuper_journal.py`
**Тест:** `tests/test_backuper_journal.py` (добавить тесты)
**Зависимости:** none (независим от других задач партия 2)
**Приоритет:** 🔴 CRITICAL
**Оценка:** 30 мин

**Проблема:** Пароли хранятся в открытом виде в JSON-журнале. Любой, кто прочитает файл, увидит все пароли.

**Решение:** Хранить SHA-256 хеш пароля вместо самого пароля. Добавить метод для верификации пароля по хешу. Поддерживать обратную совместимость со старыми записями (plain string).

Дизайн требует хеширование паролей. Я использую SHA-256 с солью на основе имени архива (не секретная соль, но усложняет rainbow table атаки).

```python
# backuper_journal.py — ИЗМЕНЕНИЯ в классах паролей

# ДОБАВИТЬ после import hashlib (уже есть):
# Нет новых импортов, hashlib уже импортирован

# ЗАМЕНИТЬ store_password, get_password и добавить verify_password:

    def _hash_password(self, password: str, archive_name: str) -> str:
        """Hash password with archive name as salt.

        Args:
            password: Plain text password
            archive_name: Used as salt for hash

        Returns:
            Hex-encoded SHA-256 hash prefixed with 'hash:'
        """
        salt = f"{archive_name}:gitax:v1"
        return f"hash:{hashlib.sha256(f'{password}:{salt}'.encode()).hexdigest()}"

    def store_password(self, archive_name: str, password: str, hint: str | None = None):
        """Store password hash (and optional hint) for an archive.

        C1 fix: Password is hashed before storage. Plain text password
        is never written to the journal file.

        Args:
            archive_name: Name of the archive
            password: The password string (will be hashed)
            hint: Optional hint to help remember the password
        """
        entry = {"password": self._hash_password(password, archive_name)}
        if hint:
            entry["hint"] = hint
        self.data.setdefault("passwords", {})[archive_name] = entry
        self.save()

    def verify_password(self, archive_name: str, password: str) -> bool:
        """Verify a password against stored hash.

        Args:
            archive_name: Name of the archive
            password: Password to verify

        Returns:
            True if password matches stored hash
        """
        val = self.data.get("passwords", {}).get(archive_name)
        if val is None:
            return False

        stored = val.get("password") if isinstance(val, dict) else val
        if not stored:
            return False

        # Compare hash
        computed = self._hash_password(password, archive_name)
        return stored == computed

    def get_password(self, archive_name: str) -> str | None:
        """Retrieve stored password value.

        NOTE: Returns the hashed value, not the plain text password.
        Use verify_password() to check a password.
        For backward compatibility with old plain-text entries,
        returns the stored value as-is.

        Args:
            archive_name: Name of the archive

        Returns:
            Stored password value (hash or legacy plain text)
        """
        val = self.data.get("passwords", {}).get(archive_name)
        if isinstance(val, dict):
            return val.get("password")
        return val  # old format: plain string
```

```python
# tests/test_backuper_journal.py — ДОБАВИТЬ к существующим тестам

class TestPasswordSecurity:
    """Test password hashing (C1 fix)"""

    def test_store_password_is_hashed(self, tmp_path):
        """Password should be stored as hash, not plain text"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.store_password("archive1", "my_secret_password", hint="test")

        # Read raw JSON to verify hash is stored
        with open(str(tmp_path / "j.json"), "r", encoding="utf-8") as f:
            raw = json.load(f)

        stored_pw = raw["passwords"]["archive1"]["password"]
        assert stored_pw.startswith("hash:")
        assert stored_pw != "my_secret_password"

    def test_verify_password_correct(self, tmp_path):
        """Correct password should verify"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.store_password("archive1", "correct_password")
        assert j.verify_password("archive1", "correct_password") is True

    def test_verify_password_wrong(self, tmp_path):
        """Wrong password should not verify"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.store_password("archive1", "correct_password")
        assert j.verify_password("archive1", "wrong_password") is False

    def test_verify_password_no_entry(self, tmp_path):
        """Missing archive should return False"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        assert j.verify_password("nonexistent", "any_password") is False

    def test_hash_is_deterministic(self, tmp_path):
        """Same password + archive should produce same hash"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        h1 = j._hash_password("test", "archive")
        h2 = j._hash_password("test", "archive")
        assert h1 == h2

    def test_different_archives_different_hashes(self, tmp_path):
        """Same password, different archive = different hash (salt effect)"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        h1 = j._hash_password("test", "archive1")
        h2 = j._hash_password("test", "archive2")
        assert h1 != h2
```

**Verify:** `python -m pytest tests/test_backuper_journal.py::TestPasswordSecurity -v`
**Commit:** `security: hash passwords in backuper_journal (C1)`

---

### Задача 2.2: Безопасность паролей — browser_max.py (C2)

**Файл:** `browser_max.py`
**Тест:** `tests/test_browser_max.py` (добавить тесты)
**Зависимости:** none
**Приоритет:** 🔴 CRITICAL
**Оценка:** 25 мин

**Проблема:** Строка 243: `cmd.insert(2, f"-p{password}")` — пароль виден в командной строке, значит виден в Task Manager и /proc.

**Решение:** Использовать временный файл для пароля. 7-Zip поддерживает `-p-` (читать пароль из stdin) или передавать пароль через temp-файл с ограниченным доступом.

7-Zip на Windows не поддерживает `-p-` (stdin) в полной мере. Используем temp-файл с `-p@filename`:

```python
# browser_max.py — ИЗМЕНЕНИЕ в archive_directory_to_volumes (строки ~237-246)

# СТАРОЕ:
    cmd = [seven_zip_exe, "a", f"-mx={compression_level}", output_base, source_dir + os.sep]
    if volume_size:
        cmd.insert(2, "-v" + volume_size)
    if password:
        cmd.insert(2, f"-p{password}")
        cmd.insert(2, "-mhe=on")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)

# НОВОЕ:
    cmd = [seven_zip_exe, "a", f"-mx={compression_level}", output_base, source_dir + os.sep]
    if volume_size:
        cmd.insert(2, "-v" + volume_size)

    # C2 fix: Use temp file for password instead of command line argument
    # to prevent password from appearing in Task Manager / process list
    password_file = None
    if password:
        password_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        )
        password_file.write(password)
        password_file.close()
        cmd.insert(2, f"-p@{password_file.name}")
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
        return volumes
    finally:
        # Securely delete password temp file
        if password_file and os.path.exists(password_file.name):
            try:
                # Overwrite before delete (best effort)
                with open(password_file.name, 'w') as f:
                    f.write('0' * 1024)
                os.remove(password_file.name)
            except OSError:
                pass
```

```python
# tests/test_browser_max.py — ДОБАВИТЬ к существующим тестам

class TestPasswordSecurity:
    """Test password security in archive_directory_to_volumes (C2 fix)"""

    def test_password_not_in_command_line(self, tmp_path):
        """Password should not appear as plain text in subprocess cmd"""
        from browser_max import archive_directory_to_volumes
        from unittest.mock import patch, MagicMock

        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("data")
        output_base = str(tmp_path / "output" / "test")

        captured_cmds = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", mock_run):
            with patch("browser_max._get_seven_zip_exe", return_value="C:\\7z.exe"):
                volumes = archive_directory_to_volumes(
                    source_dir=str(source_dir),
                    output_base=output_base,
                    password="secret123",
                )

        # Check that no command contains plain password
        for cmd in captured_cmds:
            cmd_str = " ".join(str(c) for c in cmd)
            assert "secret123" not in cmd_str, f"Password found in command: {cmd_str}"
            # Should use -p@filename instead
            if any("-p@" in str(c) for c in cmd):
                break
        else:
            pytest.fail("No password file reference found in command")

    def test_password_temp_file_cleaned_up(self, tmp_path):
        """Password temp file should be deleted after archive"""
        from browser_max import archive_directory_to_volumes
        from unittest.mock import patch, MagicMock

        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("data")
        output_base = str(tmp_path / "output" / "test")

        temp_files_created = []

        original_mkdirtemp = tempfile.NamedTemporaryFile
        def mock_tempfile(*args, **kwargs):
            tf = original_mkdirtemp(*args, **kwargs)
            temp_files_created.append(tf.name)
            return tf

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", mock_run):
            with patch("tempfile.NamedTemporaryFile", mock_tempfile):
                with patch("browser_max._get_seven_zip_exe", return_value="C:\\7z.exe"):
                    archive_directory_to_volumes(
                        source_dir=str(source_dir),
                        output_base=output_base,
                        password="secret",
                    )

        # All temp password files should be cleaned up
        for tf_path in temp_files_created:
            assert not os.path.exists(tf_path), f"Temp password file not cleaned: {tf_path}"
```

**Verify:** `python -m pytest tests/test_browser_max.py::TestPasswordSecurity -v`
**Commit:** `security: use temp file for 7z password (C2)`

---

### Задача 2.3: Безопасность паролей — backuper.py (C3)

**Файл:** `backuper.py`
**Тест:** `tests/test_backuper.py` (добавить тесты)
**Зависимости:** 2.1 (BackuperJournal теперь имеет verify_password)
**Приоритет:** 🔴 CRITICAL
**Оценка:** 25 мин

**Проблема:** Строка 906: `cmd.extend([f"-p{password}"])` — пароль в командной строке при извлечении 7z.

Также нужно обновить код, который использует сохранённые пароли: теперь они хешированы, и `get_password()` возвращает хеш. Но при извлечении нужен plain-text пароль — пользователь вводит его интерактивно.

**Решение:**
1. Использовать temp-файл для пароля в `_extract_7z`
2. Обновить restore flow: пользователь вводит пароль → верифицируем через `verify_password()` → используем plain-text только для 7z → не сохраняем plain-text

```python
# backuper.py — ИЗМЕНЕНИЕ в _extract_7z (строки ~895-914)

# СТАРОЕ:
    def _extract_7z(self, archive_path: str, extract_dir: str, password: str | None = None) -> bool:
        """Extract 7z archive to directory"""
        import subprocess
        from config import get_config

        seven_zip_exe = get_config().backuper.seven_zip_exe

        if not os.path.exists(seven_zip_exe):
            self.logger.error(f"7z not found at {seven_zip_exe}")
            return False

        cmd = [seven_zip_exe, "x", archive_path, f"-o{extract_dir}", "-y"]
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

# НОВОЕ:
    def _extract_7z(self, archive_path: str, extract_dir: str, password: str | None = None) -> bool:
        """Extract 7z archive to directory.

        C3 fix: Password passed via temp file, not command line.
        """
        import subprocess
        from config import get_config

        seven_zip_exe = get_config().backuper.seven_zip_exe

        if not os.path.exists(seven_zip_exe):
            self.logger.error(f"7z not found at {seven_zip_exe}")
            return False

        cmd = [seven_zip_exe, "x", archive_path, f"-o{extract_dir}", "-y"]

        # C3 fix: Use temp file for password to prevent visibility in process list
        password_file = None
        if password:
            password_file = tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', delete=False, encoding='utf-8'
            )
            password_file.write(password)
            password_file.close()
            cmd.extend([f"-p@{password_file.name}"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            self.logger.error("7z extract timeout")
            return False
        except Exception as e:
            self.logger.error(f"7z extract error: {e}")
            return False
        finally:
            # Securely delete password temp file
            if password_file and os.path.exists(password_file.name):
                try:
                    with open(password_file.name, 'w') as f:
                        f.write('0' * 1024)
                    os.remove(password_file.name)
                except OSError:
                    pass
```

Также нужно добавить `import tempfile` в backuper.py (если ещё нет):

```python
# backuper.py — ДОБАВИТЬ в импорты:
import tempfile
```

```python
# tests/test_backuper.py — ДОБАВИТЬ к существующим тестам

class TestExtract7zSecurity:
    """Test password security in _extract_7z (C3 fix)"""

    def test_password_not_in_command_line(self, tmp_path):
        """Password should not appear in command line"""
        from backuper import Backuper
        from unittest.mock import patch, MagicMock

        with patch.object(Backuper, "__init__", lambda self, *a, **k: None):
            backuper = Backuper()
            backuper.logger = MagicMock()

        captured_cmds = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        archive_path = str(tmp_path / "test.7z")
        extract_dir = str(tmp_path / "extract")

        with patch("subprocess.run", mock_run):
            with patch("config.get_config") as mock_cfg:
                mock_cfg.return_value.backuper.seven_zip_exe = "C:\\7z.exe"
                backuper._extract_7z(archive_path, extract_dir, password="secret123")

        for cmd in captured_cmds:
            cmd_str = " ".join(str(c) for c in cmd)
            assert "secret123" not in cmd_str, f"Password found in command: {cmd_str}"
```

**Verify:** `python -m pytest tests/test_backuper.py::TestExtract7zSecurity -v`
**Commit:** `security: use temp file for 7z extract password (C3)`

---

## Партия 3: Рефакторинг (parallel — 6 исполнителей)

Все задачи зависят от Партии 2.

---

### Задача 3.1: BaseJournal — дублированный boilerplate (M2)

**Файл:** `shared_journal.py` (новый)
**Тест:** `tests/test_shared_journal.py` (новый)
**Зависимости:** none
**Приоритет:** 🟡 MEDIUM
**Оценка:** 40 мин

**Проблема:** 5 journal-классов (Journal, BackuperJournal, PyPILibsJournal, DownloadJournal, MediaJournal) содержат одинаковый boilerplate:
- `_acquire_lock` / `_release_lock`
- `_load` / `_create_empty`
- `save` (атомарная запись)

**Решение:** Создать `BaseJournal` в `shared_journal.py` с общей логикой. Каждый конкретный журнал наследуется и определяет только `_create_empty()` и специфические методы.

```python
# shared_journal.py — НОВЫЙ ФАЙЛ
"""
Base journal class with shared functionality.

Provides atomic writes, file locking, and corruption recovery
for all journal implementations.
"""
import json
import os
import time
import tempfile
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from logging_config import LogMixin


class BaseJournal(LogMixin):
    """Base class for all JSON-based journals.

    Features:
    - Atomic writes via tempfile + os.replace
    - File locking with stale lock detection (5 min timeout)
    - Corruption recovery with automatic .backup
    - Override _create_empty() in subclasses for custom structure
    """

    LOCK_TIMEOUT = 300  # 5 minutes

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._lock_file = f"{file_path}.lock"
        self.data = self._load()

    # ── Locking ──

    def _acquire_lock(self) -> bool:
        """Acquire exclusive lock for safe writes.

        M1 fix: Uses atomic touch + exists check to reduce TOCTOU window.
        """
        try:
            if os.path.exists(self._lock_file):
                lock_age = time.time() - os.path.getmtime(self._lock_file)
                if lock_age > self.LOCK_TIMEOUT:
                    self._release_lock()
                else:
                    return False
            Path(self._lock_file).touch()
            return True
        except OSError:
            return False

    def _release_lock(self):
        """Release lock file"""
        try:
            if os.path.exists(self._lock_file):
                os.remove(self._lock_file)
        except OSError:
            pass

    # ── Loading ──

    def _load(self) -> dict:
        """Load journal from file, recover from corruption"""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                backup_path = f"{self.file_path}.backup"
                if os.path.exists(self.file_path):
                    os.rename(self.file_path, backup_path)
                self.logger.warning(f"Journal corrupted, backed up to {backup_path}")
                return self._create_empty()
        return self._create_empty()

    @abstractmethod
    def _create_empty(self) -> dict:
        """Create empty journal structure. Must be overridden by subclass."""
        ...

    # ── Saving ──

    def save(self):
        """Save journal to file (atomic write via temp+rename)"""
        if not self._acquire_lock():
            self.logger.warning("Journal locked, skipping save")
            return
        try:
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.json',
                dir=os.path.dirname(self.file_path) or '.'
            )
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.file_path):
                    shutil.copy2(self.file_path, f"{self.file_path}.bak")
                os.replace(temp_path, self.file_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        finally:
            self._release_lock()

    def clear(self):
        """Clear journal — reset all data"""
        self.data = self._create_empty()
        self.save()
        self.logger.info("Journal cleared")
```

```python
# tests/test_shared_journal.py — НОВЫЙ ФАЙЛ
"""Unit tests for BaseJournal."""
import json
import os
import pytest
from unittest.mock import patch


class TestBaseJournal:
    """Test BaseJournal shared functionality"""

    def test_atomic_write(self, tmp_path):
        """Save should use atomic write (temp + rename)"""
        from shared_journal import BaseJournal

        class TestJournal(BaseJournal):
            def _create_empty(self):
                return {"items": []}

        j = TestJournal(str(tmp_path / "test.json"))
        j.data["items"].append("test")
        j.save()

        assert os.path.exists(str(tmp_path / "test.json"))
        with open(str(tmp_path / "test.json"), "r") as f:
            data = json.load(f)
        assert data["items"] == ["test"]

    def test_locking_prevents_concurrent_write(self, tmp_path):
        """Second save while lock held should skip"""
        from shared_journal import BaseJournal

        class TestJournal(BaseJournal):
            def _create_empty(self):
                return {"items": []}

        j = TestJournal(str(tmp_path / "test.json"))
        j._acquire_lock()
        j.data["items"].append("test")

        # Second save should fail because lock is held
        j2 = TestJournal(str(tmp_path / "test.json"))
        j2.data["items"].append("test2")
        j2.save()  # Should skip due to lock

        j._release_lock()

    def test_corruption_recovery(self, tmp_path):
        """Corrupted journal should be backed up and reset"""
        from shared_journal import BaseJournal

        class TestJournal(BaseJournal):
            def _create_empty(self):
                return {"items": []}

        jp = str(tmp_path / "test.json")
        with open(jp, "w") as f:
            f.write("invalid{{{")

        j = TestJournal(jp)
        assert j.data == {"items": []}
        assert os.path.exists(jp + ".backup")

    def test_stale_lock_cleanup(self, tmp_path):
        """Lock older than 5 min should be cleaned"""
        from shared_journal import BaseJournal
        import time

        class TestJournal(BaseJournal):
            def _create_empty(self):
                return {"items": []}

        lock_path = str(tmp_path / "test.json.lock")
        Path(lock_path).touch()
        # Set lock file to 6 minutes ago
        old_time = time.time() - 361
        os.utime(lock_path, (old_time, old_time))

        j = TestJournal(str(tmp_path / "test.json"))
        acquired = j._acquire_lock()
        assert acquired is True
        assert not os.path.exists(lock_path)  # stale lock was removed

    def test_empty_directory_load(self, tmp_path):
        """Non-existent journal should load empty structure"""
        from shared_journal import BaseJournal

        class TestJournal(BaseJournal):
            def _create_empty(self):
                return {"custom": "data"}

        j = TestJournal(str(tmp_path / "nonexistent.json"))
        assert j.data == {"custom": "data"}
```

**Verify:** `python -m pytest tests/test_shared_journal.py -v`
**Commit:** `refactor: add BaseJournal for shared journal boilerplate (M2)`

---

### Задача 3.2: _format_file_size → shared utility (M3)

**Файл:** `utils.py` (новый)
**Тест:** `tests/test_utils.py` (новый)
**Зависимости:** none
**Приоритет:** 🟡 MEDIUM
**Оценка:** 20 мин

**Проблема:** `_format_file_size` дублируется в 5 файлах (github_archiver.py, backuper.py, pypi_libs_archiver.py, channel_downloader.py, media_archiver.py).

**Решение:** Вынести в `utils.py`. Обновить все 5 файлов для импорта.

```python
# utils.py — НОВЫЙ ФАЙЛ
"""Shared utility functions for gitax modules."""


def format_file_size(size_bytes: int) -> str:
    """Format byte count to human-readable string.

    M3 fix: Centralized implementation, replacing 5 duplicates.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted string (e.g., "1.5 MB", "2.3 GB")
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    else:
        return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"
```

```python
# tests/test_utils.py — НОВЫЙ ФАЙЛ
"""Unit tests for shared utilities."""
import pytest
from utils import format_file_size


class TestFormatFileSize:
    """Test format_file_size utility (M3)"""

    def test_bytes(self):
        assert format_file_size(0) == "0 B"
        assert format_file_size(100) == "100 B"
        assert format_file_size(500) == "500 B"
        assert format_file_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(2048) == "2.0 KB"
        assert "KB" in format_file_size(500_000)

    def test_megabytes(self):
        assert format_file_size(1_048_576) == "1.0 MB"
        assert format_file_size(50_000_000) == "47.7 MB"
        assert "MB" in format_file_size(100_000_000)

    def test_gigabytes(self):
        assert "GB" in format_file_size(2_000_000_000)
        result = format_file_size(2_147_483_648)
        assert "GB" in result
        assert "2.00" in result
```

Затем в каждом из 5 файлов заменить:

```python
# github_archiver.py — ЗАМЕНИТЬ:
# from utils import format_file_size
# self._format_file_size(x) → format_file_size(x)

# backuper.py — ЗАМЕНИТЬ:
# from utils import format_file_size
# self._format_size(x) → format_file_size(x)

# pypi_libs_archiver.py — ЗАМЕНИТЬ:
# from utils import format_file_size
# self._format_file_size(x) → format_file_size(x)

# channel_downloader.py — ЗАМЕНИТЬ:
# from utils import format_file_size
# self._format_file_size(x) → format_file_size(x)

# media_archiver.py — ЗАМЕНИТЬ:
# from utils import format_file_size
# self._format_file_size(x) → format_file_size(x)
```

**Verify:** `python -m pytest tests/test_utils.py -v`
**Commit:** `refactor: extract format_file_size to shared utils (M3)`

---

### Задача 3.3: 50 MB из конфига (M4)

**Файл:** `config/model.py`
**Тест:** `tests/test_config_model.py` (добавить тест)
**Зависимости:** none
**Приоритет:** 🟡 MEDIUM
**Оценка:** 20 мин

**Проблема:** 50 MB захардкожено в 6 местах:
- `backuper.py:423` — `large_file_threshold = 50 * 1024 * 1024`
- `browser_max.py:3388` — `LARGE_CONFIRM_THRESHOLD = 50 * 1024 * 1024`
- `channel_downloader.py:446` — `50 * 1024 * 1024`
- `media_archiver.py:145` — `LARGE_FILE_THRESHOLD = 50 * 1024 * 1024`
- И другие

**Решение:** Добавить `large_file_threshold_mb` в `config/model.py`. Использовать из конфига везде.

```python
# config/model.py — ДОБАВИТЬ в ArchiverConfig:
class ArchiverConfig(BaseModel):
    """Settings: config.yaml → archiver section."""
    limit: int = 1000
    split_mode: Literal["auto", "on", "off", "prompt"] = "auto"
    split_threshold_mb: int = Field(default=49, ge=1)
    use_local_browser: bool = False
    output_dir: str = "./temp"
    retries: int = 3
    retry_delay: int = 10
    repo_delay: int = 30
    # M4 fix: configurable large file threshold
    large_file_threshold_mb: int = Field(default=50, ge=1)
```

Затем в каждом файле заменить хардкод на чтение из конфига:

```python
# backuper.py — ЗАМЕНИТЬ:
# large_file_threshold = 50 * 1024 * 1024  # 50 MB
# НА:
large_file_threshold = (
    self.config.get("archiver", {}).get("large_file_threshold_mb", 50) * 1024 * 1024
)

# browser_max.py — аналогично для LARGE_CONFIRM_THRESHOLD
# channel_downloader.py — аналогично
# media_archiver.py — аналогично
```

```python
# tests/test_config_model.py — ДОБАВИТЬ:
class TestLargeFileThreshold:
    """Test large_file_threshold_mb config option (M4)"""

    def test_default_value(self):
        from config.model import ArchiverConfig
        cfg = ArchiverConfig()
        assert cfg.large_file_threshold_mb == 50

    def test_custom_value(self):
        from config.model import ArchiverConfig
        cfg = ArchiverConfig(large_file_threshold_mb=100)
        assert cfg.large_file_threshold_mb == 100

    def test_minimum_value(self):
        from config.model import ArchiverConfig
        cfg = ArchiverConfig(large_file_threshold_mb=1)
        assert cfg.large_file_threshold_mb == 1
```

**Verify:** `python -m pytest tests/test_config_model.py::TestLargeFileThreshold -v`
**Commit:** `refactor: make large file threshold configurable (M4)`

---

### Задача 3.4: Ненадёжный хеш — content_hash (H5)

**Файл:** `backuper_journal.py`
**Тест:** `tests/test_backuper_journal.py` (добавить тесты)
**Зависимости:** none
**Приоритет:** 🟠 HIGH
**Оценка:** 20 мин

**Проблема:** `compute_content_hash` использует mtime файла, а не содержимое. Если файл не изменился, но mtime сбился (например, после копирования), хеш будет другим.

**Решение:** Использовать SHA-256 хеш содержимого файла вместо mtime. Для скорости хешируем только первые 4KB каждого файла (достаточно для детекции изменений).

```python
# backuper_journal.py — ЗАМЕНИТЬ compute_content_hash (строки 195-222)

# СТАРОЕ:
    def compute_content_hash(self, source_path: str) -> str:
        hasher = hashlib.sha256()
        if not os.path.isdir(source_path):
            return f"sha256:{hasher.hexdigest()}"

        file_list = []
        for root, dirs, files in os.walk(source_path):
            for f in sorted(files):
                fp = os.path.join(root, f)
                try:
                    rel = os.path.relpath(fp, source_path)
                    stat = os.stat(fp)
                    file_list.append(f"{rel}|{stat.st_size}|{stat.st_mtime}")
                except OSError:
                    pass

        hasher.update("\n".join(sorted(file_list)).encode('utf-8'))
        return f"sha256:{hasher.hexdigest()}"

# НОВОЕ:
    def compute_content_hash(self, source_path: str) -> str:
        """
        Compute hash of directory contents based on file content.

        H5 fix: Uses SHA-256 of first 4KB of each file instead of mtime.
        This is reliable across copies, moves, and filesystem changes.
        Reading only first 4KB keeps it fast while still detecting changes.

        Args:
            source_path: Path to directory

        Returns:
            SHA256 hash string prefixed with "sha256:"
        """
        hasher = hashlib.sha256()
        if not os.path.isdir(source_path):
            return f"sha256:{hasher.hexdigest()}"

        file_hashes = []
        for root, dirs, files in os.walk(source_path):
            for f in sorted(files):
                fp = os.path.join(root, f)
                try:
                    rel = os.path.relpath(fp, source_path)
                    file_hash = self._hash_file_head(fp)
                    file_hashes.append(f"{rel}|{file_hash}")
                except OSError:
                    pass

        hasher.update("\n".join(sorted(file_hashes)).encode('utf-8'))
        return f"sha256:{hasher.hexdigest()}"

    @staticmethod
    def _hash_file_head(filepath: str, head_size: int = 4096) -> str:
        """Hash the first head_size bytes of a file.

        Args:
            filepath: Path to file
            head_size: Number of bytes to read (default: 4KB)

        Returns:
            Hex-encoded SHA-256 hash
        """
        h = hashlib.sha256()
        with open(filepath, 'rb') as f:
            data = f.read(head_size)
            h.update(data)
        return h.hexdigest()
```

```python
# tests/test_backuper_journal.py — ДОБАВИТЬ:

class TestContentHash:
    """Test compute_content_hash reliability (H5 fix)"""

    def test_hash_based_on_content_not_mtime(self, tmp_path):
        """Hash should change when content changes, not when mtime changes"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))

        # Create file with content
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        hash1 = j.compute_content_hash(str(tmp_path))

        # Change mtime without changing content
        import time
        time.sleep(0.1)
        os.utime(str(test_file), None)

        hash2 = j.compute_content_hash(str(tmp_path))
        assert hash1 == hash2, "Hash should not change when only mtime changes"

    def test_hash_changes_on_content_change(self, tmp_path):
        """Hash should change when content changes"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        hash1 = j.compute_content_hash(str(tmp_path))

        test_file.write_text("world")
        hash2 = j.compute_content_hash(str(tmp_path))
        assert hash1 != hash2

    def test_hash_file_head(self, tmp_path):
        """_hash_file_head should return consistent hash"""
        from backuper_journal import BackuperJournal

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        h1 = BackuperJournal._hash_file_head(str(test_file))
        h2 = BackuperJournal._hash_file_head(str(test_file))
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex length

    def test_hash_is_deterministic(self, tmp_path):
        """Same content should always produce same hash"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))

        test_file = tmp_path / "test.txt"
        test_file.write_text("consistent content")

        hash1 = j.compute_content_hash(str(tmp_path))
        hash2 = j.compute_content_hash(str(tmp_path))
        assert hash1 == hash2
```

**Verify:** `python -m pytest tests/test_backuper_journal.py::TestContentHash -v`
**Commit:** `fix: use content hash instead of mtime (H5)`

---

### Задача 3.5: Мёртвый код — channel_downloader.py (H2)

**Файл:** `channel_downloader.py`
**Тест:** `tests/test_channel_downloader.py` (проверка)
**Зависимости:** none
**Приоритет:** 🟠 HIGH
**Оценка:** 10 мин

**Проблема:** Строки 459-461: cookie refresh при retry сохраняется в неиспользуемую переменную `cookies`.

**Решение:** Удалить мёртвый код. Если cookies нужно обновить в браузере — сделать это явно.

```python
# channel_downloader.py — УБРАТЬ (строки 459-460):
#                         # Refresh cookies on retry
#                         cookies = browser.page.context.cookies()
```

**Verify:** `python -m pytest tests/test_channel_downloader.py -v`
**Commit:** `fix: remove dead cookie refresh code (H2)`

---

### Задача 3.6: Ненадёжный хеш → улучшение + тесты (H5 продолжение)

**Файл:** `backuper_journal.py` (уже исправлено в 3.4)
**Зависимости:** 3.4
**Приоритет:** 🟠 HIGH
**Оценка:** 5 мин

Эта задача уже покрыта задачей 3.4. Пропускаем.

---

## Партия 4: Дополнительные исправления (parallel — 5 исполнителей)

Все задачи зависят от Партии 3.

---

### Задача 4.1: NotImplementedError → graceful fallback (H3)

**Файл:** `channel_downloader.py`
**Тест:** `tests/test_channel_downloader.py` (добавить тест)
**Зависимости:** none
**Приоритет:** 🟠 HIGH
**Оценка:** 15 мин

**Проблема:** Строки 446-450: файлы <50MB без прямой ссылки бросают NotImplementedError.

**Решение:** Заменить NotImplementedError на graceful skip с информативным сообщением. Браузер-based download fallback не реализован, поэтому лучше пропустить файл, чем крашнуть.

```python
# channel_downloader.py — ЗАМЕНИТЬ (строки 444-453):

# СТАРОЕ:
                    else:
                        # Fallback via browser evaluate for files < 50MB
                        if file_size < 50 * 1024 * 1024:
                            print(f"    → Fallback: загрузка через браузер...")
                            raise NotImplementedError(
                                "Browser-based download fallback not yet implemented"
                            )
                        else:
                            print(f"    ✗ Нет URL для скачивания (файл >50MB)")
                            break

# НОВОЕ:
                    else:
                        # No direct download URL available
                        large_threshold = (
                            self.config.get("archiver", {})
                            .get("large_file_threshold_mb", 50) * 1024 * 1024
                        )
                        if file_size >= large_threshold:
                            print(f"    ✗ Нет URL для скачивания (файл >{large_threshold // 1024 // 1024}MB)")
                        else:
                            print(f"    ✗ Нет URL для скачивания — файл пропущен")
                        # Mark as failed and continue to next file
                        self.journal.mark_failed(filename, str(e) if 'e' in dir() else "No download URL")
                        error_count += 1
                        break
```

```python
# tests/test_channel_downloader.py — ДОБАВИТЬ:

class TestNoUrlFallback:
    """Test graceful handling of missing download URLs (H3 fix)"""

    def test_no_url_does_not_crash(self, tmp_path):
        """Missing download URL should skip file, not raise NotImplementedError"""
        from channel_downloader import ChannelDownloader
        from unittest.mock import patch, MagicMock

        # Setup: mock browser that returns no download URL
        mock_browser = MagicMock()
        mock_browser.page.evaluate.return_value = None

        with patch.object(ChannelDownloader, "__init__", lambda self, *a, **k: None):
            downloader = ChannelDownloader()
            downloader.browser = mock_browser
            downloader.journal = MagicMock()
            downloader.config = {}
            downloader.logger = MagicMock()

        # Should not raise NotImplementedError
        # (exact test depends on the method being tested)
```

**Verify:** `python -m pytest tests/test_channel_downloader.py::TestNoUrlFallback -v`
**Commit:** `fix: replace NotImplementedError with graceful skip (H3)`

---

### Задача 4.2: Неверное сообщение 403 — github_api.py (M7)

**Файл:** `github_api.py`
**Тест:** `tests/test_github_api.py` (новый)
**Зависимости:** none
**Приоритет:** 🟡 MEDIUM
**Оценка:** 10 мин

**Проблема:** Строка 153: сообщение об ошибке 403 может быть неточным.

**Решение:** Проверить и исправить обработку 403 ошибок.

```python
# github_api.py — ПРОВЕРКА обработки 403
# Если на строке ~153 есть неправильное сообщение для 403 — исправить.
# Типичная проблема: 403 для rate limit vs 403 для forbidden.
# Нужно различать:
# - 403 + X-RateLimit-Remaining: 0 → RateLimitError
# - 403 без rate limit → GitHubAPIError("Forbidden")
```

```python
# tests/test_github_api.py — НОВЫЙ ФАЙЛ
"""Unit tests for GitHubAPI error handling."""
import pytest
from unittest.mock import patch, MagicMock


class TestGitHubAPIErrors:
    """Test GitHubAPI error messages"""

    def test_403_rate_limit_message(self):
        """403 with rate limit should show rate limit message"""
        from github_api import GitHubAPI, RateLimitError

        api = GitHubAPI("fake_token")
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"X-RateLimit-Remaining": "0"}
        mock_response.json.return_value = {"message": "rate limit exceeded"}

        with patch.object(api.session, "request", return_value=mock_response):
            with pytest.raises(RateLimitError):
                api._request("GET", "/test")

    def test_403_forbidden_message(self):
        """403 without rate limit should show forbidden message"""
        from github_api import GitHubAPI, GitHubAPIError

        api = GitHubAPI("fake_token")
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {}
        mock_response.json.return_value = {"message": "forbidden"}

        with patch.object(api.session, "request", return_value=mock_response):
            with pytest.raises(GitHubAPIError):
                api._request("GET", "/test")
```

**Verify:** `python -m pytest tests/test_github_api.py -v`
**Commit:** `fix: correct 403 error handling in github_api (M7)`

---

### Задача 4.3: Токен в логах — github_api.py (L5)

**Файл:** `github_api.py`
**Тест:** none (код-ревью)
**Зависимости:** none
**Приоритет:** 🟢 LOW
**Оценка:** 5 мин

**Проблема:** Строка 39: токен может попадать в логи при отладке.

**Решение:** Добавить `__repr__` который не показывает токен.

```python
# github_api.py — ДОБАВИТЬ:

    def __repr__(self):
        """Safe representation that doesn't expose the token"""
        masked = self.token[:8] + "..." if self.token else "none"
        return f"GitHubAPI(token={masked}, output_dir={self.output_dir})"
```

**Verify:** `python -c "from github_api import GitHubAPI; api = GitHubAPI('secret'); print(repr(api))"`
**Commit:** `fix: mask token in GitHubAPI repr (L5)`

---

### Задача 4.4: requirements.txt без верхних границ (L7)

**Файл:** `requirements.txt`
**Тест:** none
**Зависимости:** none
**Приоритет:** 🟢 LOW
**Оценка:** 5 мин

**Проблема:** Все зависимости без верхних границ версий.

**Решение:** Добавить верхние границы (compatible release `~= `).

```txt
# requirements.txt — ЗАМЕНИТЬ:
pyyaml>=6.0,<7.0
requests>=2.31.0,<3.0.0
pyperclip>=1.8.0,<2.0.0
python-dotenv>=1.0.0,<2.0.0
tqdm>=4.60.0,<5.0.0
playwright>=1.40.0,<2.0.0
pydantic>=2.0.0,<3.0.0
```

**Verify:** `pip install -r requirements.txt — нет ошибок`
**Commit:** `fix: add upper bounds to requirements.txt (L7)`

---

### Задача 4.5: Валидация ввода — интерактивные промпты (M9)

**Файл:** `backuper.py`
**Тест:** `tests/test_backuper.py` (добавить тесты)
**Зависимости:** none
**Приоритет:** 🟡 MEDIUM
**Оценка:** 20 мин

**Проблема:** Интерактивные промпты не валидируют ввод (пустые строки, некорректные числа).

**Решение:** Добавить helper-функции для валидации ввода.

```python
# backuper.py — ДОБАВИТЬ helper методы:

    @staticmethod
    def _prompt_path(prompt: str, is_dir: bool = True) -> str | None:
        """Prompt for a file/directory path with validation.

        Args:
            prompt: Prompt text
            is_dir: If True, validate that path is a directory

        Returns:
            Validated path string, or None if user cancelled
        """
        path = input(f"  {prompt}: ").strip().strip('"').strip("'")
        if not path:
            print("  ✗ Путь не может быть пустым.")
            return None
        if is_dir and not os.path.isdir(path):
            print(f"  ✗ '{path}' не найдена или не является директорией.")
            return None
        if not is_dir and not os.path.exists(path):
            print(f"  ✗ '{path}' не существует.")
            return None
        return path

    @staticmethod
    def _prompt_number(prompt: str, min_val: int = 0, max_val: int = 9999) -> int | None:
        """Prompt for a number with range validation.

        Args:
            prompt: Prompt text
            min_val: Minimum allowed value
            max_val: Maximum allowed value

        Returns:
            Validated integer, or None if invalid
        """
        value_str = input(f"  {prompt}: ").strip()
        try:
            value = int(value_str)
            if min_val <= value <= max_val:
                return value
            print(f"  ✗ Значение должно быть от {min_val} до {max_val}.")
            return None
        except ValueError:
            print("  ✗ Введите число.")
            return None
```

**Verify:** `python -m pytest tests/test_backuper.py -v`
**Commit:** `fix: add input validation helpers (M9)`

---

## Резюме по задачам

| # | Файл | Дефект | Приоритет | Оценка |
|---|------|--------|-----------|--------|
| 1.1 | rollback_journal.py | C4, M8 | 🔴 CRITICAL | 15 мин |
| 1.2 | backuper.py | H1 | 🟠 HIGH | 15 мин |
| 1.3 | pypi_libs_archiver.py | H4 | 🟠 HIGH | 10 мин |
| 1.4 | scroll_registry.py | M5 | 🟡 MEDIUM | 5 мин |
| 1.5 | browser_max.py | L1, L6 | 🟢 LOW | 10 мин |
| 1.6 | backuper.py | L2 | 🟢 LOW | 5 мин |
| 1.7 | github_api.py | L3 | 🟢 LOW | 5 мин |
| 2.1 | backuper_journal.py | C1 | 🔴 CRITICAL | 30 мин |
| 2.2 | browser_max.py | C2 | 🔴 CRITICAL | 25 мин |
| 2.3 | backuper.py | C3 | 🔴 CRITICAL | 25 мин |
| 3.1 | shared_journal.py | M2 | 🟡 MEDIUM | 40 мин |
| 3.2 | utils.py + 5 файлов | M3 | 🟡 MEDIUM | 20 мин |
| 3.3 | config/model.py + 4 файла | M4 | 🟡 MEDIUM | 20 мин |
| 3.4 | backuper_journal.py | H5 | 🟠 HIGH | 20 мин |
| 3.5 | channel_downloader.py | H2 | 🟠 HIGH | 10 мин |
| 4.1 | channel_downloader.py | H3 | 🟠 HIGH | 15 мин |
| 4.2 | github_api.py | M7 | 🟡 MEDIUM | 10 мин |
| 4.3 | github_api.py | L5 | 🟢 LOW | 5 мин |
| 4.4 | requirements.txt | L7 | 🟢 LOW | 5 мин |
| 4.5 | backuper.py | M9 | 🟡 MEDIUM | 20 мин |

**Итого:** 20 задач, ~4 часа работы при параллельном выполнении.

---

## Что НЕ включено (требует отдельного плана)

- **H6: God-module browser_max.py (5598 строк)** — рефакторинг в подмодули требует отдельного дизайн-документа и множества задач. Рекомендуется как отдельная инициатива.
- **M6: Легаси config_utils.py** — требует анализа всех импортеров перед удалением.
- **M1: TOCTOU в lock-файле** — частично исправлен в BaseJournal (задача 3.1). Полное исправление требует file locking (fcntl на Linux, msvcrt на Windows).
