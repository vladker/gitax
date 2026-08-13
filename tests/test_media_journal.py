"""Tests for MediaJournal v2 — schema, batching, checkpoint, progress."""

import json
import os
import pathlib
import tempfile
import time

import pytest

from media_archiver import MediaJournal


@pytest.fixture
def journal_file(tmp_path):
    """Временный путь для журнала."""
    return str(tmp_path / "media_journal_test.json")


@pytest.fixture
def journal(journal_file):
    """Свежий журнал на временном файле."""
    j = MediaJournal(journal_file)
    yield j
    # cleanup happens when tmp_path is removed


# ── Schema & Initialization ────────────────────────────────────────

class TestSchema:
    def test_version_is_2(self, journal):
        assert journal.data["version"] == 2

    def test_has_file_index(self, journal):
        assert "file_index" in journal.data
        assert isinstance(journal.data["file_index"], dict)

    def test_has_sessions(self, journal):
        assert "sessions" in journal.data
        assert isinstance(journal.data["sessions"], dict)

    def test_has_batches(self, journal):
        assert "batches" in journal.data
        assert isinstance(journal.data["batches"], dict)

    def test_has_total_bytes_scanned(self, journal):
        assert "total_bytes_scanned" in journal.data

    def test_has_watch_dir(self, journal):
        assert "watch_dir" in journal.data


# ── File Index ─────────────────────────────────────────────────────

class TestFileIndex:
    def test_mark_sent(self, journal):
        journal.mark_sent("photo.jpg", 1000, session_id="test_ses")
        entry = journal.get_file_entry("photo.jpg", 1000)
        assert entry is not None
        assert entry["status"] == "sent"
        assert entry["size_bytes"] == 1000

    def test_was_sent(self, journal):
        journal.mark_sent("photo.jpg", 1000, session_id="test_ses")
        assert journal.was_sent("photo.jpg", 1000) is True

    def test_was_sent_not_in_journal(self, journal):
        assert journal.was_sent("unknown.jpg", 500) is False

    def test_mark_failed(self, journal):
        journal.mark_failed("video.mp4", 500, session_id="test_ses")
        entry = journal.get_file_entry("video.mp4", 500)
        assert entry is not None
        assert entry["status"] == "failed"

    def test_file_key_uniqueness(self, journal):
        """Разные файлы с одинаковым размером — разные ключи."""
        journal.mark_sent("a.jpg", 1000, session_id="s")
        journal.mark_sent("b.jpg", 1000, session_id="s")
        assert len(journal.data["file_index"]) == 2

    def test_file_key_same_file_different_size(self, journal):
        """Один файл, разный размер — разные записи."""
        journal.mark_sent("a.jpg", 1000, session_id="s")
        journal.mark_sent("a.jpg", 2000, session_id="s")
        assert len(journal.data["file_index"]) == 2


# ── Session Management ─────────────────────────────────────────────

class TestSession:
    def test_start_session_returns_dict(self, journal):
        result = journal.start_session(watch_dir="/test")
        assert isinstance(result, dict)
        assert "id" in result
        assert result["status"] == "in_progress"

    def test_start_session_creates_session(self, journal):
        sid = journal.start_session()["id"]
        assert sid in journal.data["sessions"]

    def test_end_session(self, journal):
        sid = journal.start_session()["id"]
        journal.end_session(sid, status="completed", bytes_sent=1000)
        sess = journal.data["sessions"][sid]
        assert sess["status"] == "completed"
        assert sess["bytes_sent"] == 1000
        assert sess["ended_at"] != ""

    def test_session_stats_update_on_mark_sent(self, journal):
        sid = journal.start_session()["id"]
        journal.mark_sent("file.jpg", 1000, session_id=sid)
        sess = journal.data["sessions"][sid]
        assert sess["files_processed"] == 1
        assert sess["bytes_sent"] == 1000

    def test_session_stats_update_on_mark_failed(self, journal):
        sid = journal.start_session()["id"]
        journal.mark_failed("file.jpg", 500, session_id=sid)
        sess = journal.data["sessions"][sid]
        assert sess["files_processed"] == 1
        assert sess["bytes_failed"] == 500

    def test_session_id_auto_assign(self, journal):
        """Файл без session_id получает id текущей сессии."""
        sid = journal.start_session()["id"]
        journal.mark_sent("f.jpg", 100)
        entry = journal.get_file_entry("f.jpg", 100)
        assert entry["session_id"] == sid


# ── Batching ───────────────────────────────────────────────────────

class TestBatching:
    def test_create_batch(self, journal):
        batch = journal.create_batch("photos", 100, total_bytes=100_000)
        assert batch["total_files"] == 100
        assert batch["total_bytes"] == 100_000
        assert batch["status"] == "pending"

    def test_update_batch_sent(self, journal):
        journal.create_batch("photos", 100)
        journal.update_batch("photos", "file1.jpg", 1000, sent=True)
        batch = journal.data["batches"]["photos"]
        assert batch["sent_files"] == 1
        assert batch["sent_bytes"] == 1000

    def test_update_batch_failed(self, journal):
        journal.create_batch("photos", 100)
        journal.update_batch("photos", "file1.jpg", 1000, sent=False)
        batch = journal.data["batches"]["photos"]
        assert batch["sent_files"] == 0

    def test_batch_completion(self, journal):
        journal.create_batch("photos", 2)
        journal.update_batch("photos", "f1.jpg", 500, sent=True)
        completed = journal.update_batch("photos", "f2.jpg", 500, sent=True)
        assert completed is True
        assert journal.data["batches"]["photos"]["status"] == "completed"

    def test_mark_batch_complete(self, journal):
        journal.create_batch("photos", 100, total_bytes=1_000_000)
        journal.mark_batch_complete("photos")
        batch = journal.data["batches"]["photos"]
        assert batch["status"] == "completed"
        assert batch["sent_files"] == 100

    def test_batch_list(self, journal):
        journal.create_batch("a", 10)
        journal.create_batch("b", 20)
        journal.update_batch("a", "f.jpg", 100, sent=True)
        journal.update_batch("a", "f.jpg", 100, sent=True)
        batches = journal.get_batch_list()
        assert len(batches) == 2

    def test_get_batch(self, journal):
        journal.create_batch("photos", 50)
        b = journal.get_batch("photos")
        assert b is not None
        assert b["total_files"] == 50

    def test_get_batch_nonexistent(self, journal):
        assert journal.get_batch("missing") is None


# ── Checkpoint & Recovery ──────────────────────────────────────────

class TestCheckpoint:
    def test_save_creates_file(self, journal, journal_file):
        journal.mark_sent("f.jpg", 100)
        assert os.path.exists(journal_file)

    def test_persists_across_instances(self, journal, journal_file):
        journal.mark_sent("persist.jpg", 2000, session_id="s1")
        # New instance reads same file
        j2 = MediaJournal(journal_file)
        assert j2.was_sent("persist.jpg", 2000)

    def test_checkpoint_after_each_file(self, journal, journal_file):
        """После каждого mark_sent файл сохраняется."""
        journal.mark_sent("c1.jpg", 100)
        mtime1 = os.path.getmtime(journal_file)
        time.sleep(0.05)
        journal.mark_sent("c2.jpg", 200)
        mtime2 = os.path.getmtime(journal_file)
        assert mtime2 > mtime1

    def test_recovery_after_crash_simulation(self, journal, journal_file):
        """Симуляция: процесс упал после mark_sent, новый процесс продолжает."""
        journal.mark_sent("recovery.jpg", 5000, session_id="crash_ses")
        # "Crash" — создать новый экземпляр
        j2 = MediaJournal(journal_file)
        assert j2.was_sent("recovery.jpg", 5000)
        entry = j2.get_file_entry("recovery.jpg", 5000)
        assert entry["session_id"] == "crash_ses"


# ── Progress ───────────────────────────────────────────────────────

class TestProgress:
    def test_initial_progress(self, journal):
        prog = journal.get_progress()
        assert prog["percent"] == 0.0
        assert "total_files" in prog

    def test_progress_with_sent_files(self, journal):
        journal.data["total_bytes_scanned"] = 10_000
        journal.mark_sent("f.jpg", 3000, session_id="s")
        prog = journal.get_progress()
        assert prog["percent"] == 30.0
        assert prog["sent_bytes"] == 3000
        assert prog["remaining_bytes"] == 7000

    def test_progress_total_files(self, journal):
        journal.mark_sent("a.jpg", 1000, session_id="s")
        journal.mark_sent("b.jpg", 2000, session_id="s")
        prog = journal.get_progress()
        assert prog["total_files"] == 2


# ── Batch Progress ─────────────────────────────────────────────────

class TestBatchProgress:
    def test_batch_progress_initial(self, journal):
        journal.create_batch("photos", 100, total_bytes=100_000)
        bp = journal.get_batch_progress("photos")
        assert bp["percent"] == 0.0
        assert bp["files_done"] == 0
        assert bp["files_total"] == 100

    def test_batch_progress_partial(self, journal):
        journal.create_batch("photos", 10, total_bytes=10_000)
        for i in range(3):
            journal.update_batch("photos", f"f{i}.jpg", 1000, sent=True)
        bp = journal.get_batch_progress("photos")
        assert bp["percent"] == 30.0
        assert bp["files_done"] == 3

    def test_batch_progress_complete(self, journal):
        journal.create_batch("photos", 2, total_bytes=2000)
        journal.update_batch("photos", "f1.jpg", 1000, sent=True)
        journal.update_batch("photos", "f2.jpg", 1000, sent=True)
        bp = journal.get_batch_progress("photos")
        assert bp["percent"] == 100.0

    def test_batch_progress_nonexistent(self, journal):
        bp = journal.get_batch_progress("missing")
        assert bp["percent"] == 0.0


# ── Summary ────────────────────────────────────────────────────────

class TestSummary:
    def test_summary_contains_required_fields(self, journal):
        journal.create_batch("photos", 100)
        summary = journal.get_summary()
        assert "total_files" in summary
        assert "total_batches" in summary
        assert "total_sessions" in summary

    def test_summary_counts(self, journal):
        journal.mark_sent("a.jpg", 1000, session_id="s1")
        journal.mark_sent("b.jpg", 2000, session_id="s1")
        journal.create_batch("photos", 50)
        summary = journal.get_summary()
        assert summary["total_files"] == 2
        assert summary["total_batches"] == 1


# ── Legacy Migration ──────────────────────────────────────────────

class TestLegacyMigration:
    def test_migrates_v1_entries_to_file_index(self, journal_file):
        """Старый формат с entries должен быть миграчен в file_index."""
        old_data = {
            "version": 1,
            "entries": [
                {"filename": "old.jpg", "size_bytes": 5000, "status": "sent", "sent_at": "2025-01-01"},
                {"filename": "old2.jpg", "size_bytes": 3000, "status": "sent", "sent_at": "2025-01-02"},
            ],
        }
        with open(journal_file, "w") as f:
            json.dump(old_data, f)

        j = MediaJournal(journal_file)
        assert j.data["version"] == 2
        assert len(j.data["file_index"]) == 2
        assert j.was_sent("old.jpg", 5000)

    def test_empty_migration(self, journal_file):
        """Пустой файл без версии создаёт чистый v2."""
        with open(journal_file, "w") as f:
            json.dump({}, f)

        j = MediaJournal(journal_file)
        assert j.data["version"] == 2
        assert j.data["file_index"] == {}


# ── Property Proxies ──────────────────────────────────────────────

class TestPropertyProxies:
    def test_file_index_property(self, journal):
        fi = journal.file_index
        assert isinstance(fi, dict)

    def test_sessions_property(self, journal):
        s = journal.sessions
        assert isinstance(s, dict)

    def test_batches_property(self, journal):
        b = journal.batches
        assert isinstance(b, dict)


# ── Cleanup ────────────────────────────────────────────────────────

class TestCleanup:
    def test_cleanup_file_removes_entry(self, journal):
        journal.mark_sent("cleanup.jpg", 1000, session_id="s")
        journal.cleanup_file("cleanup.jpg", 1000)
        assert journal.get_file_entry("cleanup.jpg", 1000) is None

    def test_cleanup_batch_removes_batch(self, journal):
        journal.create_batch("tmp", 10)
        journal.cleanup_batch("tmp")
        assert "tmp" not in journal.data.get("batches", {})
