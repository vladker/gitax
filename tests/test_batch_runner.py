# -*- coding: utf-8 -*-
"""Tests for BatchRunner — multiprocessing batch archiver launcher."""

import os
import sys
import time
import pytest
import multiprocessing
from unittest.mock import patch, MagicMock

from batch_runner import (
    BatchRunner,
    BatchTask,
    BatchSummary,
    TaskProgress,
    TaskStatus,
    _worker,
)


# ---------------------------------------------------------------------------
# Mock archiver for testing
# ---------------------------------------------------------------------------

class MockArchiver:
    """Mock archiver that succeeds or fails on demand."""
    should_fail = False
    delay = 0.0

    def __init__(self, config_path="config.yaml"):
        self.config_path = config_path

    def load_top_packages(self):
        if self.should_fail:
            raise RuntimeError("Mock failure")
        if self.delay:
            time.sleep(self.delay)
        return True

    def load_top_libraries(self):
        return self.load_top_packages()

    def sync_packages(self):
        return self.load_top_packages()

    def sync_libraries(self):
        return self.load_top_packages()

    def sync_repositories(self):
        return self.load_top_packages()

    def load_repositories(self):
        return self.load_top_packages()


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestBatchTask:
    def test_creation(self):
        task = BatchTask(
            module="test_module",
            cls="TestClass",
            method="test_method",
            label="Test Label",
        )
        assert task.module == "test_module"
        assert task.cls == "TestClass"
        assert task.method == "test_method"
        assert task.label == "Test Label"
        assert task.config_path == "config.yaml"


class TestTaskProgress:
    def test_creation(self):
        prog = TaskProgress(task_id=0, status=TaskStatus.RUNNING)
        assert prog.task_id == 0
        assert prog.status == TaskStatus.RUNNING
        assert prog.message == ""
        assert prog.elapsed_sec == 0.0


class TestBatchSummary:
    def test_empty(self):
        summary = BatchSummary()
        assert summary.total == 0
        assert summary.succeeded == 0
        assert summary.failed == 0

    def test_counts(self):
        summary = BatchSummary(
            total=5,
            succeeded=3,
            failed=1,
            skipped=1,
            timed_out=0,
        )
        assert summary.total == 5
        assert summary.succeeded == 3
        assert summary.failed == 1
        assert summary.skipped == 1


class TestBatchRunnerEmpty:
    def test_no_tasks(self):
        runner = BatchRunner(tasks=[], max_concurrent=1)
        summary = runner.run()
        assert summary.total == 0


class TestBatchRunnerWorker:
    """Test _worker function directly (without multiprocessing)."""

    def test_worker_success(self, tmp_path, monkeypatch):
        """Worker reports SUCCESS on successful execution."""
        queue = multiprocessing.Queue()

        task = BatchTask(
            module="tests.test_batch_runner",
            cls="MockArchiver",
            method="load_top_packages",
            label="Mock Test",
        )

        # Patch config init to avoid real config loading
        monkeypatch.setattr("config.init_config", lambda x: None)
        monkeypatch.setattr("logging_config.setup_logging", lambda: MagicMock())

        _worker(0, task, queue)

        # Worker sends RUNNING then SUCCESS
        msg1 = queue.get(timeout=2)
        assert msg1.task_id == 0
        assert msg1.status == TaskStatus.RUNNING

        msg2 = queue.get(timeout=2)
        assert msg2.task_id == 0
        assert msg2.status == TaskStatus.SUCCESS
        assert msg2.elapsed_sec >= 0.0

    def test_worker_failure(self, tmp_path, monkeypatch):
        """Worker reports FAILED on exception."""
        queue = multiprocessing.Queue()

        task = BatchTask(
            module="tests.test_batch_runner",
            cls="MockArchiver",
            method="nonexistent_method",
            label="Mock Fail",
        )

        monkeypatch.setattr("config.init_config", lambda x: None)
        monkeypatch.setattr("logging_config.setup_logging", lambda: MagicMock())

        _worker(0, task, queue)

        msg = queue.get(timeout=2)
        assert msg.task_id == 0
        assert msg.status == TaskStatus.FAILED
        assert "AttributeError" in msg.error


class TestBatchRunnerConfig:
    def test_max_concurrent_clamp(self):
        """max_concurrent < 1 is clamped to 1."""
        runner = BatchRunner(tasks=[], max_concurrent=0)
        assert runner.max_concurrent == 1

        runner = BatchRunner(tasks=[], max_concurrent=-5)
        assert runner.max_concurrent == 1

    def test_timeout_default(self):
        runner = BatchRunner(tasks=[])
        assert runner.timeout_seconds == 7200


class TestTaskStatusEnum:
    def test_all_statuses(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.SUCCESS == "success"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.TIMEOUT == "timeout"
        assert TaskStatus.SKIPPED == "skipped"
