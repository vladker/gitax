---
date: 2026-06-17
topic: "Gitax Project Improvements"
status: draft
---

## Problem Statement

The current GitHub Archiver mixes UI, business logic and low‑level I/O in a single script. This makes testing hard, error handling inconsistent, and future extensions (new sources, better config) brittle.

**Goal:** Introduce a clean layered architecture that separates concerns, strengthens configuration validation, and improves reliability while preserving existing functionality.

## Constraints

- Must run on Windows 10+ with Python 3.10+. 
- Existing environment variables (`GITHUB_TOKEN`, channel URLs) cannot be altered by users.
- No external services beyond GitHub API and MAX messenger.
- Keep backward‑compatible CLI commands (menus, numeric choices).

## Approach

We adopt a **service‑oriented architecture** with four layers: CLI, Application Service, Domain Services, Infrastructure. Configuration handling is centralized in a `ConfigService` using a Pydantic model for strict validation. All side‑effects are confined to the Infrastructure layer, making higher layers pure and easily unit‑tested.

## Architecture

```
CLI Layer (github_archiver.py) → Application Service (ArchiverOrchestrator)
    ↳ Domain Services: GitHubService, BrowserService, FileSplitterService, ConfigService
        ↳ Infrastructure adapters: HTTP client, Playwright CDP wrapper, filesystem utils, logging
```

## Components

- **ConfigService** – loads `.env` and optional `config.yaml`, validates required keys, exposes read‑only dict.
- **GitHubService** – thin wrapper over `github_api.GitHubAPI`; provides high‑level methods (`list_top_repos`, `download_zip`).
- **BrowserService** – encapsulates Playwright CDP connection; handles channel URL resolution and file upload with retry logic.
- **FileSplitterService** – decides whether to split a file (> `split_threshold_mb`) using 7‑Zip, returns list of paths.
- **JournalRepository** – thread‑safe JSON journal accessor (`journal.json`, `pypi_journal.json`).
- **ArchiverOrchestrator** – coordinates the end‑to‑end flow: fetch → split → upload → journal update; reports a summary object.
- **CLIController** – renders menus, parses numeric input, forwards commands to the orchestrator.

## Data Flow

1. `ConfigService` loads config at startup.
2. User selects *Sync Repositories* → `CLIController` calls `ArchiverOrchestrator.sync()`.
3. Orchestrator queries `GitHubService.list_top_repos(limit)`.
4. For each repo not ignored, `GitHubService.download_zip(repo)` returns a local ZIP path.
5. `FileSplitterService.prepare(path)` returns `[path]` or volume list.
6. `BrowserService.send_message(text, files)` uploads; on success the journal entry is marked *sent*.
7. Errors are caught, logged, and the repo entry gets status *failed* with error details.
8. After processing all items, a summary (counts of sent / failed / skipped) is returned to CLI for display.

## Error Handling Strategy

- Each domain service defines specific exception classes (`GitHubError`, `BrowserError`, `SplitError`).
- The orchestrator catches exceptions per repo, logs with context (`logger.error`), updates the journal, and continues processing remaining repos.
- Critical failures (missing token, inability to connect to Chrome) abort the whole operation with a clear console message and non‑zero exit code.
- All uncaught exceptions are handled by a top‑level `try/except` in `main`, ensuring the process never crashes silently.

## Testing Strategy

| Level | Focus |
|------|-------|
| Unit | Individual services with mocked dependencies (e.g., mock HTTP responses, mock Playwright). |
| Integration | Full sync flow using a temporary headless Chrome and a sandbox GitHub token; validates end‑to‑end file upload. |
| Property‑Based | `FileSplitterService` – ensures split count matches size / threshold logic. |
| Regression | Existing test suite retained; new tests added for orchestrator decision‑making and config validation. |

Coverage target: **≥ 85 %** on core modules.

## Open Questions

- **Plugin Hook:** Should we expose a lightweight plugin loader now to allow future source types (GitLab, Bitbucket) without code changes?  *(deferred to next iteration)*
- **Rate‑limit Back‑off:** `GitHubService` currently retries on HTTP errors; do we need exponential back‑off for 403 rate‑limit responses?  *(plan to add configurable back‑off strategy).*