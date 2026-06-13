"""Batch Runner — launch multiple archivers in parallel via multiprocessing.

Each archiver runs in its own process to isolate:
- BrowserMAX CDP connections (Playwright is not thread-safe)
- SignalHandler (global singleton per process)
- Config lru_cache (global state)

Usage:
    runner = BatchRunner(tasks, max_concurrent=1)
    runner.run()

Config (config.yaml):
    batch:
        max_concurrent: 1       # 1 = sequential (safe for single Chrome)
        timeout_seconds: 7200   # 2 hours per task
"""

from __future__ import annotations

import importlib
import os
import signal
import sys
import time
import traceback
import multiprocessing
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class BatchTask:
    """One archiver invocation."""
    module: str            # e.g. "pypi_libs_archiver"
    cls: str               # e.g. "PyPILibsArchiver"
    method: str            # e.g. "load_top_libraries"
    label: str             # display name, e.g. "PyPI load"
    config_path: str = "config.yaml"


@dataclass
class TaskProgress:
    """Progress message from a worker process."""
    task_id: int
    status: TaskStatus
    message: str = ""
    elapsed_sec: float = 0.0
    counter: int = 0       # items processed (repos, packages, etc.)
    error: str = ""


@dataclass
class BatchSummary:
    """Final summary of a batch run."""
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    timed_out: int = 0
    total_elapsed_sec: float = 0.0
    results: dict[int, TaskProgress] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Worker (runs in child process)
# ---------------------------------------------------------------------------

def _worker(target_id: int, task: BatchTask, queue: multiprocessing.Queue) -> None:  # type: ignore[type-arg]
    """Entry point for each child process.

    Reinitializes config, logging, and signal handler in the new process.
    """
    try:
        # Re-init dotenv + config in child process
        from dotenv import load_dotenv
        load_dotenv(override=True)

        from config import init_config
        init_config(task.config_path)

        from logging_config import setup_logging
        setup_logging()

        # Import and instantiate archiver
        mod = importlib.import_module(task.module)
        archiver_cls = getattr(mod, task.cls)
        archiver = archiver_cls(task.config_path)

        # Bind the method
        method = getattr(archiver, task.method)

        # Report start
        queue.put(TaskProgress(
            task_id=target_id,
            status=TaskStatus.RUNNING,
            message=f"Started {task.label}",
        ))

        start = time.monotonic()

        # Run — archiver methods print their own progress to stdout
        # We intercept via a counter hook where possible
        result = method()

        elapsed = time.monotonic() - start
        queue.put(TaskProgress(
            task_id=target_id,
            status=TaskStatus.SUCCESS,
            message=f"Finished {task.label}",
            elapsed_sec=round(elapsed, 1),
        ))

        # Cleanup browser if archiver has one
        if hasattr(archiver, "browser") and archiver.browser:
            try:
                archiver.browser.close()
            except Exception:
                pass

    except Exception as exc:
        elapsed = time.monotonic() - start if "start" in locals() else 0.0
        error_msg = f"{type(exc).__name__}: {exc}"
        tb = traceback.format_exc()
        queue.put(TaskProgress(
            task_id=target_id,
            status=TaskStatus.FAILED,
            message=f"Failed {task.label}",
            elapsed_sec=round(elapsed, 1),
            error=error_msg,
        ))
        # Write full traceback to log
        from logging_config import setup_logging
        logger = setup_logging()
        logger.error(f"Batch worker {target_id} ({task.label}) crashed:\n{tb}")


# ---------------------------------------------------------------------------
# Runner (parent process)
# ---------------------------------------------------------------------------

class BatchRunner:
    """Orchestrates batch archiver runs via multiprocessing."""

    def __init__(
        self,
        tasks: list[BatchTask],
        max_concurrent: int = 1,
        timeout_seconds: int = 7200,
    ):
        self.tasks = tasks
        self.max_concurrent = max(1, max_concurrent)
        self.timeout_seconds = timeout_seconds
        self._queue: multiprocessing.Queue = multiprocessing.Queue()  # type: ignore[type-arg]
        self._results: dict[int, TaskProgress] = {}
        self._shutdown = False

    # ---- public API ----

    def run(self) -> BatchSummary:
        """Execute all tasks and return summary."""
        if not self.tasks:
            print("\n  ⚠ Нет задач для выполнения.")
            return BatchSummary()

        self._print_header()
        self._register_shutdown()

        start = time.monotonic()
        semaphore = multiprocessing.Semaphore(self.max_concurrent)

        processes: list[multiprocessing.Process] = []
        for i, task in enumerate(self.tasks):
            if self._shutdown:
                self._results[i] = TaskProgress(
                    task_id=i, status=TaskStatus.SKIPPED, message="Cancelled"
                )
                continue

            proc = multiprocessing.Process(
                target=self._run_with_semaphore,
                args=(i, task, semaphore),
                daemon=True,
            )
            processes.append(proc)
            self._results[i] = TaskProgress(task_id=i, status=TaskStatus.PENDING, message=task.label)

        # Start all processes
        for proc in processes:
            if not self._shutdown:
                proc.start()
            else:
                break

        # Collect progress from queue
        self._drain_queue(len(self.tasks), timeout=self.timeout_seconds)

        # Wait for processes
        for proc in processes:
            if proc.is_alive():
                proc.join(timeout=10)
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=5)

        total_elapsed = time.monotonic() - start
        self._print_summary(total_elapsed)

        return self._build_summary(total_elapsed)

    # ---- internals ----

    def _run_with_semaphore(
        self,
        task_id: int,
        task: BatchTask,
        semaphore: multiprocessing.Semaphore,
    ) -> None:
        """Acquire semaphore, run worker, release."""
        semaphore.acquire()
        try:
            _worker(task_id, task, self._queue)
        finally:
            semaphore.release()

    def _drain_queue(self, expected: int, timeout: int) -> None:
        """Read progress messages from queue with timeout."""
        completed = 0
        deadline = time.monotonic() + timeout

        while completed < expected and time.monotonic() < deadline:
            try:
                msg = self._queue.get(timeout=2.0)
                self._results[msg.task_id] = msg
                if msg.status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
                    completed += 1
                self._print_progress(msg)
            except multiprocessing.queues.Empty:
                continue

        # Mark remaining as timed out
        for i, prog in self._results.items():
            if prog.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                self._results[i] = TaskProgress(
                    task_id=i,
                    status=TaskStatus.TIMEOUT,
                    message=f"Timed out: {self.tasks[i].label if i < len(self.tasks) else '?'}",
                )
                completed += 1

    def _build_summary(self, elapsed: float) -> BatchSummary:
        summary = BatchSummary(
            total=len(self.tasks),
            total_elapsed_sec=round(elapsed, 1),
            results=self._results,
        )
        for prog in self._results.values():
            if prog.status == TaskStatus.SUCCESS:
                summary.succeeded += 1
            elif prog.status == TaskStatus.FAILED:
                summary.failed += 1
            elif prog.status == TaskStatus.SKIPPED:
                summary.skipped += 1
            elif prog.status == TaskStatus.TIMEOUT:
                summary.timed_out += 1
        return summary

    # ---- display ----

    def _print_header(self) -> None:
        print()
        print("  " + "═" * 58)
        print("  BATCH MODE — параллельный запуск архиверов")
        print("  " + "═" * 58)
        print(f"  Задач: {len(self.tasks)}  |  Одновременно: {self.max_concurrent}")
        print("  " + "─" * 58)
        for i, task in enumerate(self.tasks):
            print(f"  [{i}] {task.label}")
        print("  " + "─" * 58)
        print()

    def _print_progress(self, msg: TaskProgress) -> None:
        status_icon = {
            TaskStatus.RUNNING: "▶",
            TaskStatus.SUCCESS: "✓",
            TaskStatus.FAILED: "✗",
            TaskStatus.TIMEOUT: "⏱",
            TaskStatus.SKIPPED: "—",
        }.get(msg.status, "?")

        elapsed_str = f" ({msg.elapsed_sec}s)" if msg.elapsed_sec else ""
        print(f"  [{msg.task_id}] {status_icon} {msg.message}{elapsed_str}")
        if msg.error:
            print(f"         ⚠ {msg.error[:120]}")

    def _print_summary(self, elapsed: float) -> None:
        print()
        print("  " + "═" * 58)
        succ = sum(1 for p in self._results.values() if p.status == TaskStatus.SUCCESS)
        fail = sum(1 for p in self._results.values() if p.status == TaskStatus.FAILED)
        skip = sum(1 for p in self._results.values() if p.status == TaskStatus.SKIPPED)
        tout = sum(1 for p in self._results.values() if p.status == TaskStatus.TIMEOUT)

        print(f"  Batch завершён за {elapsed:.0f}s")
        print(f"  ✓ Успешно: {succ}  ✗ Ошибки: {fail}  — Пропущено: {skip}  ⏱ Таймаут: {tout}")
        print("  " + "═" * 58)
        print()

    # ---- shutdown ----

    def _register_shutdown(self) -> None:
        original = signal.getsignal(signal.SIGINT)

        def handler(signum: int, frame: Any) -> None:
            print("\n  ⚠ Прерывание... Остановка batch.")
            self._shutdown = True
            # Restore default to allow exit
            signal.signal(signal.SIGINT, original)
            os.kill(os.getpid(), signal.SIGINT)

        signal.signal(signal.SIGINT, handler)
