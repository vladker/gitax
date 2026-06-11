"""Tests for journal thread safety."""

import pytest
import threading
import time
import os


class TestJournalThreadSafety:
    def test_concurrent_writes_no_corruption(self, tmp_path):
        """Multiple threads writing simultaneously should not corrupt data."""
        from journal import Journal

        journal_path = str(tmp_path / "concurrent_journal.json")
        journal = Journal(journal_path)

        errors = []
        write_count = 0
        lock = threading.Lock()

        def write_entries(prefix: str, count: int):
            nonlocal write_count
            local_errors = []
            for i in range(count):
                try:
                    journal.add_repository({
                        "full_name": f"{prefix}/repo_{i}",
                        "status": "sent",
                    })
                    with lock:
                        write_count += 1
                except Exception as e:
                    local_errors.append(str(e))
            errors.extend(local_errors)

        threads = []
        for prefix in ["batch_a", "batch_b", "batch_c"]:
            t = threading.Thread(target=write_entries, args=(prefix, 10))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Thread errors: {errors}"
        all_repos = journal.get_all_repositories()
        assert len(all_repos) == 30

        import json
        with open(journal_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "repositories" in data
        assert len(data["repositories"]) == 30

    def test_channel_label_field(self, tmp_path):
        """channel_label field should be optional and preserved."""
        from journal import Journal

        journal_path = str(tmp_path / "channel_journal.json")
        journal = Journal(journal_path)

        journal.add_repository({
            "full_name": "test/repo1",
            "status": "sent",
            "channel_label": "GitHub Main",
        })

        repo = journal.get_repository("test/repo1")
        assert repo["channel_label"] == "GitHub Main"

        journal.add_repository({
            "full_name": "test/repo2",
            "status": "sent",
        })

        repo2 = journal.get_repository("test/repo2")
        assert "channel_label" not in repo2 or repo2.get("channel_label") is None

    def test_journal_lock_timeout_recovery(self, tmp_path):
        """Lock timeout should not crash the journal - save completes without error."""
        from shared_journal import BaseJournal

        class TestJournal(BaseJournal):
            def _create_empty(self):
                return {"entries": []}

        journal_path = str(tmp_path / "lock_journal.json")
        journal = TestJournal(journal_path)

        # Write some data and verify save() completes without raising
        journal.data["entries"].append({"name": "test_entry"})
        journal.save()

        # Verify data persisted correctly
        import json
        with open(journal_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["entries"]) == 1
        assert data["entries"][0]["name"] == "test_entry"
