# Project Improvements — Design Document

**Date:** 2026-06-14  
**Topic:** Comprehensive improvements across codebase quality, multi-ecosystem support, runtime tracking, and verification  
**Status:** validated

---

## Problem Statement

The GitHub Archiver (gitax) started as a single-purpose tool for downloading GitHub repos and sending them to a MAX channel. Over time it grew to support PyPI packages, media files, folder backups, and channel downloading — but the codebase accumulated technical debt:

1. **Security issues** — passwords written to temp files, `sys.exit()` in constructors
2. **Architectural duplication** — 5 journal classes repeating lock/atomic-write/corruption-recovery, browser init duplicated across 4 archivers, signal handling duplicated 3 times
3. **Inconsistencies** — `print()` instead of logging, inline imports, missing `.env` loading
4. **Missing functionality** — no unified retry logic, daemon threads in parallel uploader, non-recursive walk hack
5. **Single ecosystem** — only GitHub + PyPI, no Rust/.NET/Ruby support
6. **No verification** — no way to compare journal state against actual MAX channel contents
7. **No runtime tracking** — packages sent without corresponding runtime installers

**Goal:** Systematically address all 7 areas without breaking existing functionality.

---

## Constraints

- **No new external dependencies** — only `pyyaml`, `requests`, `playwright`, `pydantic`, `python-dotenv`, `tqdm`
- **Backward compatibility** — existing `.env`, `config.yaml`, and journal formats must continue working
- **Python 3.10+** — type hints use `X | Y` union syntax
- **Windows-first** — 7-Zip, Chrome CDP, subprocess patterns all tested on Windows
- **Single Chrome instance** — all browser automation shares one CDP connection (port 9222)
- **Thread safety** — parallel uploads must not corrupt journals
- **TDD approach** — tests first, then implementation (pytest, no fixtures file, `tmp_path` for temp dirs, `MagicMock` for browser mocking)

---

## Approach Overview

The improvements are organized into **7 design areas**, each with its own sub-design document. This document summarizes the chosen approach and how the areas fit together.

| # | Area | Design Doc | Status |
|---|------|-----------|--------|
| 1 | Codebase Quality (5 phases, 17 tasks) | `2026-06-11-gitax-improvements.md` (plan) | ✅ Implemented |
| 2 | Multi-Channel + Parallel Upload | `2026-06-11-channel-registry-design.md` | ✅ Implemented |
| 3 | Multi-Ecosystem Phase 1 (Cargo, NuGet, RubyGems) | `2026-06-12-multi-ecosystem-phase1-design.md` | ✅ Implemented |
| 4 | Runtime Installers | `2026-06-13-runtime-installers-design.md` | ✅ Implemented |
| 5 | Journal-Channel Verifier | `2026-06-13-journal-verifier-design.md` | ✅ Implemented |
| 6 | Batch Runner | (inline) | ✅ Implemented |
| 7 | Config Standardization | `2026-06-10-config-standartization-design.md` | ✅ Implemented |

---

## Architecture

### High-Level Overview

```
github_archiver.py          ← Main entry point, menus, channel selector
├── github_api.py           ← GitHub REST API
├── pypi_api.py             ← PyPI / Hugovk dataset API
├── cargo_api.py            ← crates.io API (NEW)
├── nuget_api.py            ← NuGet v3 API (NEW)
├── rubygems_api.py         ← RubyGems API (NEW)
├── runtime_api.py          ← Runtime version checkers (NEW)
│
├── github_archiver/        ← GitHub archiver module
├── pypi_libs_archiver.py   ← PyPI archiver
├── cargo_archiver.py       ← Cargo archiver (NEW)
├── nuget_archiver.py       ← NuGet archiver (NEW)
├── rubygems_archiver.py    ← RubyGems archiver (NEW)
├── media_archiver.py       ← Media watcher + uploader
├── backuper.py             ← Folder backup
├── channel_downloader.py   ← Download from MAX channel
│
├── browser_max.py          ← MAX automation (Playwright + CDP)
├── browser_init.py         ← BrowserInitMixin (NEW — DRY)
├── parallel_uploader.py    ← ParallelGroupUploader (NEW)
│
├── shared_journal.py       ← BaseJournal + RuntimeJournalMixin (NEW — DRY)
├── journal.py              ← GitHub journal (inherits BaseJournal)
├── pypi_libs_journal.py    ← PyPI journal (inherits BaseJournal)
├── cargo_journal.py        ← Cargo journal (NEW)
├── nuget_journal.py        ← NuGet journal (NEW)
├── rubygems_journal.py     ← RubyGems journal (NEW)
├── media_journal.py        ← Media journal (inherits BaseJournal)
├── backuper_journal.py     ← Backuper journal (inherits BaseJournal)
├── download_journal.py     ← Download journal (inherits BaseJournal)
│
├── config/                 ← Pydantic config models (NEW — standardized)
│   ├── __init__.py
│   ├── model.py            ← AppConfig + all sub-models
│   └── loader.py           ← YAML loader + env overrides + migration
├── config_utils.py         ← get_channel_url(), get_channels_for_function()
├── channel_registry_ui.py  ← Channel management CLI (NEW)
│
├── verifier/               ← Journal-Channel verifier (NEW)
│   ├── __init__.py
│   ├── models.py           ← ChannelFile, DiffResult
│   ├── core.py             ← JournalChannelVerifier
│   ├── adapters.py         ← ChannelAdapter + JournalAdapter protocols
│   ├── adapters_github.py  ← GitHub adapter
│   ├── adapters_pypi.py    ← PyPI adapter
│   ├── adapters_backuper.py ← Backuper adapter
│   └── adapters_media.py   ← Media adapter
│
├── signal_handler.py       ← SignalHandler (NEW — DRY)
├── retry.py                ← retry decorator (NEW — DRY)
├── health_check.py         ← Startup health checks (NEW)
├── batch_runner.py         ← Batch runner for multiple archivers (NEW)
├── progressbar.py          ← Progress bar utilities
│
├── logging_config.py       ← LogMixin + setup_logging()
├── utils.py                ← format_file_size(), ConfigurationError (NEW)
├── scroll_registry.py      ← Scroll position tracking
└── rollback_journal.py     ← Rollback support
```

---

## Area 1: Codebase Quality (5 Phases, 17 Tasks)

### Phase 1: Critical — Security & Crash Fixes

| Task | File | Change |
|------|------|--------|
| 1.1 | `backuper.py` | Pass 7z password via `-p` CLI flag instead of temp file |
| 1.2 | `media_archiver.py`, `utils.py` | Replace `sys.exit()` with `ConfigurationError` exception |
| 1.3 | `github_archiver.py` | Add `logging.warning` for silent cleanup errors |

### Phase 2: Architectural DRY

| Task | New File | Change |
|------|----------|--------|
| 2.1 | `shared_journal.py` | `BaseJournal` — shared lock/atomic-write/corruption-recovery |
| 2.2 | — | Replace duplicate `_format_file_size` with `utils.format_file_size()` |
| 2.3 | `browser_init.py` | `BrowserInitMixin` — shared browser init across 4 archivers |
| 2.4 | `signal_handler.py` | `SignalHandler` — shared signal handling + atexit cleanup |

### Phase 3: Consistency

| Task | File | Change |
|------|------|--------|
| 3.1 | `scroll_registry.py`, `rollback_journal.py` | Replace `print()` with `logging.getLogger("gitax")` |
| 3.2 | `browser_max.py`, `github_archiver.py` | Move `import glob` to module level |

### Phase 4: Functional

| Task | File | Change |
|------|------|--------|
| 4.1 | `retry.py` | Unified `@retry` decorator with exponential backoff |
| 4.2 | `parallel_uploader.py` | Remove `daemon=True` from threads |
| 4.3 | `backuper.py` | Replace non-recursive walk hack with `dirs[:] = []` pruning |
| 4.4 | `pypi_libs_archiver.py` | Add `SessionCapture` logging |
| 4.5 | `media_archiver.py` | Add `load_dotenv()` call |

### Phase 5: Nice-to-Have

| Task | File | Change |
|------|------|--------|
| 5.1 | `channel_downloader.py` | Remove dead `NotImplementedError` |
| 5.2 | `pypi_libs_archiver.py` | Add confirmation before journal.clear() |
| 5.3 | `health_check.py` | Startup health-check (7z, Chrome CDP, config) |

**Key architectural decisions:**
- `BaseJournal` uses **file-level lock** (`.lock` file with 5-min stale timeout) + **class-level threading lock** for thread safety
- `BrowserInitMixin` parameterizes `_channel_key` and `_section_key` to support different config lookups per archiver
- `SignalHandler` prevents double-registration via `_registered` flag
- `retry` decorator supports custom `on_retry` callback for per-retry actions

---

## Area 2: Multi-Channel + Parallel Upload

### Channel Registry Model

```
ChannelEntry {
  url: str              # URL MAX-канала
  label: str            # человекочитаемое имя
  enabled: bool         # можно отключить без удаления
}

ChannelRegistry {
  github: [ChannelEntry, ...]
  pypi:   [ChannelEntry, ...]
  media:  [ChannelEntry, ...]
  backup: [ChannelEntry, ...]
}
```

### Migration Path

Legacy `channels.{key}` in config.yaml / `.env` automatically migrate to `channel_registry` on first load. Legacy keys are **not** cleared for backward compatibility.

### Parallel Upload Flow

```
User picks "All channels" (or 2+ channels)
    ↓
ParallelGroupUploader(items=[...], channels=[ch1, ch2, ch3])
    ↓
Phase 1 — Download (single-threaded)
  ├─ repo_A.zip → temp/repo_A.zip
  ├─ repo_B.zip → temp/repo_B.zip
    ↓
Phase 2 — Upload (multi-threaded, 1 thread per channel)
  ├─ Thread-1: BrowserMAX(ch1) → upload all → journal(ch1)
  ├─ Thread-2: BrowserMAX(ch2) → upload all → journal(ch2)
  └─ Thread-3: BrowserMAX(ch3) → upload all → journal(ch3)
    ↓
Phase 3 — Cleanup
  ├─ repo_A: ≥1 success → DELETE
  ├─ repo_B: ≥1 success → DELETE
  └─ repo_C: ALL failed → KEEP + status=failed
```

**Thread safety:**
- `threading.Lock` around journal write operations
- `stagger_delay_sec` (configurable, default 2s) between thread starts
- Threads are **not daemon** — proper cleanup via `join(timeout=600)`

---

## Area 3: Multi-Ecosystem Phase 1

### Three New Archivers

| Archiver | Ecosystem | API | Artifact | Download URL |
|----------|-----------|-----|----------|-------------|
| Cargo | Rust / crates.io | `crates.io/api/v1/crates` | `.crate` | `crates.io/api/v1/crates/{name}/{version}/download` |
| NuGet | .NET / nuget.org | NuGet v3 registration API | `.nupkg` | `globalcdn.nuget.org/packages/{id}.{version}.nupkg` |
| RubyGems | Ruby / rubygems.org | `rubygems.org/api/v1/gems` | `.gem` | `rubygems.org/downloads/{name}-{version}.gem` |

### Architecture Pattern (per archiver)

```
{ecosystem}_api.py       → fetches top packages + metadata + downloads
{ecosystem}_archiver.py  → CLI menu, orchestration, upload to MAX
{ecosystem}_journal.py   → JSON-backed deduplication (inherits BaseJournal)
```

Each archiver:
- Inherits `BaseJournal` + `RuntimeJournalMixin`
- Uses `BrowserInitMixin` for browser initialization
- Uses `SignalHandler` for graceful shutdown
- Has its own `output_dir` and `limit` in config
- Gets its own MAX channel from `channel_registry`

### Message Format (per ecosystem)

```
🦀 serde 1.0.204                    🟣 Newtonsoft.Json 13.0.3           💎 rails 7.1.3.4
📝 A generic serialization...       📝 Popular JSON framework...        📝 Full-stack web framework
📥 Downloads: 215,678,901          📥 Downloads: 182,450,123          📥 Downloads: 98,234,567
📦 serde-1.0.204.crate (145 KB)    📦 Newtonsoft.Json.13.0.3.nupkg    📦 rails-7.1.3.4.gem (245 KB)
🔗 crates.io/crates/serde          🔗 nuget.org/packages/...           🔗 rubygems.org/gems/rails
```

---

## Area 4: Runtime Installers

### Architecture

```
runtime_api.py
├── RuntimeAPI (base, LogMixin + ABC)
│   ├── PythonRuntime    → python.org + GitHub releases
│   ├── RustRuntime      → rust-lang.org + GitHub releases
│   ├── DotNetRuntime    → dotnet.microsoft.com releases API
│   ├── RubyRuntime      → ruby-lang.org + rubyinstaller.org
│   └── GitRuntime       → git-scm.com + GitHub releases
│
├── RuntimeFactory       → get_runtime("pypi") → PythonRuntime
│
└── OSTarget = {windows, macos, linux}  # enum
```

### Runtime Sync Flow

```
sync_runtimes():
  1. runtime = RuntimeFactory.get_runtime(self._channel_key)
  2. latest = runtime.get_latest_version()
  3. saved = self.journal.get_runtime_version()
  4. If latest == saved:
       print("✓ Рантайм актуален")
       return True
  5. urls = runtime.get_download_urls(latest)
  6. For each URL:
       a. Download to temp dir
       b. Split if > threshold
  7. Build message: "🐍 Python 3.13.2 (обновление с 3.13.1)"
  8. browser.send_message_with_files(text, files)
  9. journal.set_runtime_version(latest, entries)
 10. Cleanup temp files
```

### Journal Extension

`RuntimeJournalMixin` provides 4 methods to any journal:

```python
get_runtime_version() -> str | None
set_runtime_version(version: str, entries: list[dict])
should_update_runtime(latest_version: str) -> bool
get_runtime_entries() -> list[dict]
```

Journal stores runtime data under `data["runtime"]`:
```json
{
  "runtime": {
    "version": "3.13.2",
    "last_updated": "2026-06-13T10:00:00",
    "entries": [
      {"os": "windows", "filename": "python-3.13.2-amd64.exe", "sent_at": "..."},
      {"os": "macos", "filename": "python-3.13.2-macos.pkg", "sent_at": "..."},
      {"os": "linux", "filename": "Python-3.13.2.tar.xz", "sent_at": "..."}
    ]
  }
}
```

---

## Area 5: Journal-Channel Verifier

### Architecture

```
verifier/
├── __init__.py
├── models.py          # ChannelFile, DiffResult, VerifierMode
├── adapters.py        # ChannelAdapter + JournalAdapter protocols
├── core.py            # JournalChannelVerifier
├── adapters_github.py # GitHubJournalAdapter
├── adapters_pypi.py   # PyPIJournalAdapter
├── adapters_backuper.py # BackuperJournalAdapter
└── adapters_media.py  # MediaJournalAdapter
```

### Two Verification Modes

| Mode | Speed | Data Sources |
|------|-------|-------------|
| `quick` | 30-60s | Browser DOM scan only |
| `thorough` | 2-5 min | API + page state + DOM (3 sources) |

### Diff Result

```python
DiffResult {
  in_journal_not_in_channel: List[str]   # keys of missing entries
  in_channel_not_in_journal: List[str]   # orphans in channel
  version_mismatches: List[dict]         # version discrepancies
  stats: dict                            # summary statistics
  incomplete_scan: bool                  # flag for partial scans
}
```

### Adapter Protocol

Each adapter implements:
```python
ChannelAdapter:
  scan_files(mode: VerifierMode) -> List[ChannelFile]

JournalAdapter:
  get_entries() -> List[dict]
  expected_filename(entry: dict) -> str | List[str]
  entry_key(entry: dict) -> str
  channel_to_key(filename: str) -> str | None
```

---

## Area 6: Batch Runner

### Purpose

Run multiple archivers sequentially or in batch mode without manual menu navigation.

### Architecture

```
batch_runner.py
├── BatchRunner
│   ├── run(archivers: List[str])
│   ├── run_single(archiver_name: str)
│   └── run_all()
│
├── ArchiverRegistry
│   ├── register(name, archiver_class, config_key)
│   └── get(name) -> archiver instance
│
└── BatchConfig
    ├── archivers: List[str]
    ├── sequential: bool
    └── timeout: int
```

### Flow

```
batch_runner.py
  → For each archiver in config:
      1. Instantiate archiver
      2. SignalHandler.register(archiver)
      3. archiver.sync_packages()  # includes runtime sync
      4. archiver._cleanup()
  → Summary report
```

---

## Area 7: Config Standardization

### Pydantic Config Models

```
config/
├── __init__.py        ← init_config(), get_config()
├── model.py           ← Pydantic models
│   ├── AppConfig (root)
│   ├── ArchiverConfig
│   ├── GitHubConfig
│   ├── PyPILibsArchiverConfig
│   ├── CargoArchiverConfig
│   ├── NuGetArchiverConfig
│   ├── RubyGemsArchiverConfig
│   ├── MediaArchiverConfig
│   ├── BackuperConfig
│   ├── BrowserConfig
│   ├── ChannelRegistry
│   ├── ChannelEntry
│   └── RuntimeConfig
└── loader.py          ← YAML loader + env var overrides + migration
```

### Config Loading Priority

1. `.env` file / environment variables (highest priority)
2. `config.yaml` (default values)
3. Pydantic model defaults (fallback)

### Env Var Mapping

```
CHANNEL_max          → channels.max
CHANNEL_pypi         → channels.pypi
CHANNEL_media        → channels.media
CHANNEL_backup       → channels.backup
GITHUB_TOKEN         → github.token
MEDIA_WATCH_DIR      → media_archiver.watch_dir
```

---

## Data Flow — Complete Sync Pipeline

```
User: launches github_archiver.py
    ↓
┌─ health_check.run_health_checks() ──────────────┐
│  ✓ 7-Zip available                               │
│  ✓ Chrome CDP reachable                          │
│  ✓ GITHUB_TOKEN configured                       │
│  ⚠ CHANNEL_pypi not set (non-critical)           │
└──────────────────────────────────────────────────┘
    ↓
User: selects "PyPI Archiver" → "Sync packages"
    ↓
┌─ PyPILibsArchiver.sync_packages() ───────────────┐
│                                                   │
│  1. Runtime sync (pre-sync step)                  │
│     ├─ RuntimeFactory.get_runtime("pypi")         │
│     │  → PythonRuntime                           │
│     ├─ PythonRuntime.get_latest_version()         │
│     │  → "3.13.2"                                │
│     ├─ journal.get_runtime_version()              │
│     │  → "3.13.1"                                │
│     ├─ "3.13.2" != "3.13.1" → UPDATE             │
│     ├─ Download installers (win/mac/linux)        │
│     ├─ Send to MAX channel                        │
│     └─ journal.set_runtime_version("3.13.2")      │
│                                                   │
│  2. Package sync (existing flow)                  │
│     ├─ PyPIAPI.fetch_top_packages(limit=500)      │
│     ├─ For each package:                          │
│     │  ├─ journal.exists(name, version)? → skip   │
│     │  ├─ Download .tar.gz + .whl                 │
│     │  ├─ Channel selector (1 channel → auto)     │
│     │  ├─ Upload via BrowserMAX                   │
│     │  └─ journal.add(name, version, status)      │
│     └─ Summary report                             │
│                                                   │
│  3. Cleanup temp files                            │
└───────────────────────────────────────────────────┘
    ↓
User: selects "Verify journal"
    ↓
┌─ JournalChannelVerifier.verify() ────────────────┐
│  → PyPIJournalAdapter.get_entries()               │
│  → PyPIChannelAdapter.scan_files(mode="quick")    │
│  → Diff comparison                                │
│  → DiffResult:                                    │
│    ├─ in_journal_not_in_channel: 2 entries        │
│    ├─ in_channel_not_in_journal: 0 orphans        │
│    └─ stats: {total: 498, matched: 496, ...}     │
└───────────────────────────────────────────────────┘
```

---

## Error Handling Strategy

| Scenario | Behavior |
|----------|----------|
| Network error during API call | `@retry` decorator: 3 attempts, exponential backoff |
| Download failure | Skip package, mark `status: "failed"` in journal, continue |
| Upload failure | Retry per config, then mark journal entry as failed |
| Browser disconnect | Reconnect via `_ensure_browser_connected()`, retry upload |
| Journal corruption | `BaseJournal._load()` → rename to `.backup` → create empty |
| Parallel upload: 1 channel failed | File = "sent" (≥1 success), delete temp, journal: ch1=failed, ch2=ok |
| Parallel upload: ALL channels failed | File = "failed", keep temp for retry |
| Runtime version check fails | Log warning, continue sync without runtime update |
| Signal received (SIGINT/SIGTERM) | `SignalHandler` sets `_shutdown=True`, completes current upload, cleanup |
| Config missing | `ConfigurationError` raised (not `sys.exit()`) |

---

## Testing Strategy

### Test Organization

```
tests/
├── test_journal_base.py        ← BaseJournal (init, save, clear, thread safety)
├── test_browser_init.py        ← BrowserInitMixin (init, reuse, close)
├── test_signal_handler.py      ← SignalHandler (signal, cleanup, double-reg)
├── test_retry.py               ← retry decorator (success, retry, max attempts, backoff)
├── test_health_check.py        ← health checks (7z, CDP, config)
├── test_utils.py               ← format_file_size, ConfigurationError
├── test_backuper_extract.py    ← 7z password security
├── test_backuper_scan.py       ← recursive/non-recursive scan
├── test_github_archiver_cleanup.py ← orphaned file cleanup logging
├── test_media_archiver.py      ← ConfigurationError in __init__
├── test_parallel_uploader.py   ← daemon thread fix
├── test_channel_selector.py    ← channel selection logic
├── test_channel_registry_ui.py ← channel CRUD
├── test_pypi_api.py            ← PyPI API mocking
├── test_export_messages.py     ← message export (25 tests)
├── test_cargo_api.py           ← Cargo API mocking (NEW)
├── test_nuget_api.py           ← NuGet API mocking (NEW)
├── test_rubygems_api.py        ← RubyGems API mocking (NEW)
├── test_runtime_api.py         ← Runtime version checking (NEW)
└── test_verifier.py            ← JournalChannelVerifier (NEW)
```

### Testing Patterns

- **Unit tests:** Mock HTTP responses, verify data structures
- **Integration tests:** Mock browser, verify file download → upload flow
- **Thread safety tests:** Concurrent journal writes, parallel upload simulation
- **Config tests:** Pydantic model defaults, env overrides, migration
- **No fixtures file:** Each test class is self-contained
- **`tmp_path` for temp dirs:** pytest built-in fixture
- **`MagicMock` for browser:** Avoid real browser dependency in unit tests

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Breaking existing tests | Each task preserves existing behavior, only changes internals |
| Config path changes | All config access patterns preserved (dict access + Pydantic) |
| Browser mixin changes | Mixin is additive — archivers keep their own logic |
| 7z password via CLI | Safe on Windows (subprocess args not visible in process listings) |
| Signal handler consolidation | `GracefulShutdown` in `github_archiver.py` remains unchanged |
| Journal migration | `BaseJournal` handles corrupted/missing data gracefully |
| Parallel uploads | Thread-safe journal writes, non-daemon threads with explicit join |

---

## Rollback Strategy

Each improvement is small and self-contained. If any change breaks functionality:
1. Revert the single commit
2. The change affects at most 1-3 files
3. Existing tests catch regressions immediately

---

## Open Questions

1. **Browser pooling:** При параллельной загрузке браузер переподключается к каждому каналу. Можно ли держать одно соединение и просто менять URL? → TODO
2. **Async parallelism:** Сейчас каналы обрабатываются последовательно/потоками. Можно ли загружать в несколько каналов одновременно через async? → TODO (требует async browser automation)
3. **Channel groups:** Пользователь может хотеть группы каналов ("основные", "архивные") → future feature
4. **Phase 2 ecosystems:** Conda, Conan, Maven, npm — когда добавлять? → после стабилизации Phase 1
5. **Verifier auto-fix:** Должен ли verifier автоматически исправлять рассинхрон? → сейчас только отчёт, исправление — ручное

---

## File Inventory

### New files created by improvements

| File | Purpose |
|------|---------|
| `shared_journal.py` | BaseJournal + RuntimeJournalMixin |
| `browser_init.py` | BrowserInitMixin |
| `signal_handler.py` | SignalHandler |
| `retry.py` | @retry decorator |
| `health_check.py` | Startup health checks |
| `parallel_uploader.py` | ParallelGroupUploader |
| `channel_registry_ui.py` | Channel management CLI |
| `config/model.py` | Pydantic config models |
| `config/loader.py` | YAML loader + env overrides |
| `runtime_api.py` | Runtime version checkers |
| `batch_runner.py` | Batch runner |
| `verifier/` (7 files) | Journal-Channel verifier |
| `cargo_api.py`, `cargo_archiver.py`, `cargo_journal.py` | Cargo archiver |
| `nuget_api.py`, `nuget_archiver.py`, `nuget_journal.py` | NuGet archiver |
| `rubygems_api.py`, `rubygems_archiver.py`, `rubygems_journal.py` | RubyGems archiver |

### Modified files

| File | Changes |
|------|---------|
| `utils.py` | Added `ConfigurationError` |
| `backuper.py` | Password via CLI, walk fix, BrowserInitMixin, SignalHandler |
| `media_archiver.py` | ConfigurationError, BrowserInitMixin, SignalHandler, load_dotenv |
| `pypi_libs_archiver.py` | BrowserInitMixin, SignalHandler, SessionCapture, confirm clear |
| `channel_downloader.py` | BrowserInitMixin, SignalHandler, remove dead code |
| `github_archiver.py` | Logging warnings, glob import, channel selector |
| `browser_max.py` | Glob import module-level |
| `scroll_registry.py` | print() → logger |
| `rollback_journal.py` | print() → logger |
| `journal.py` + siblings | Inherit BaseJournal + RuntimeJournalMixin |

### Test files

| File | Tests |
|------|-------|
| `tests/test_journal_base.py` | 10 tests (init, save, clear, thread safety) |
| `tests/test_browser_init.py` | 5 tests (init, reuse, connect, close) |
| `tests/test_signal_handler.py` | 4 tests (signal, cleanup, double-reg, custom attr) |
| `tests/test_retry.py` | 5 tests (success, retry, max, backoff, exceptions) |
| `tests/test_health_check.py` | 7 tests (7z, CDP, config, full run) |
| `tests/test_utils.py` | 6 tests (format size, ConfigurationError) |
| `tests/test_backuper_extract.py` | 7 tests (password security) |
| `tests/test_backuper_scan.py` | 3 tests (recursive/non-recursive) |
| `tests/test_github_archiver_cleanup.py` | 3 tests (logging warnings) |
| `tests/test_media_archiver.py` | 3 tests (ConfigurationError) |
| `tests/test_parallel_uploader.py` | 1 test added (daemon fix) |
| `tests/test_channel_selector.py` | 7 tests |
| `tests/test_channel_registry_ui.py` | 6 tests |
| `tests/test_runtime_api.py` | 5 tests (NEW) |
| `tests/test_verifier.py` | 5 tests (NEW) |

---

## Summary

The improvements transform gitax from a collection of independent scripts into a **cohesive multi-ecosystem archiving platform** with:

- **5 archivers** (GitHub, PyPI, Cargo, NuGet, RubyGems) sharing common infrastructure
- **DRY architecture** — BaseJournal, BrowserInitMixin, SignalHandler, retry decorator
- **Multi-channel support** — ChannelRegistry with parallel uploads
- **Runtime tracking** — automatic runtime installer sync per ecosystem
- **Verification** — JournalChannelVerifier for all archivers
- **Health checks** — startup validation of 7z, Chrome CDP, and config
- **Batch runner** — automated multi-archiver execution
- **Standardized config** — Pydantic models with env var overrides

Total: ~17 codebase quality tasks + ~9 new modules + ~5 new archiver modules + ~15 test files.
