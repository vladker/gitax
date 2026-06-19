---
date: 2026-06-19
topic: "Gitax High-Priority Improvements"
status: draft
---

## Problem Statement

The Gitax project has four critical issues that risk data integrity, reliability, and maintainability:

1. **Test Coverage Gap** — Core modules like `github_archiver.py` (4130 lines) and `browser_max.py` (6796 lines) lack integration tests
2. **Rate Limit Handling** — GitHub API rate limits cause mid-execution failures when processing large repo lists
3. **Upload Confirmation Reliability** — Filename matching can match old uploads, causing false positives
4. **Journal Versioning** — No version field in journals risks future schema incompatibility

These issues directly impact backup reliability and make the codebase fragile to change.

---

## Constraints

- Must run on Windows 10+ with Python 3.10+
- Existing CLI interface (menus, numeric choices) must remain unchanged
- No breaking changes to `.env` format or `config.yaml` schema
- All improvements must be backward-compatible with existing journals and config files

---

## Approach

We adopt a **test-driven reliability-first** strategy:

- Add integration tests for core flows (download → split → upload)
- Implement rate limit detection with exponential backoff
- Replace filename matching with message ID + hash verification
- Add journal versioning with migration support

Each improvement is isolated, reversible, and tested before deployment.

---

## Architecture Overview

```
+─────────────────────────────────────────────────────────────+
| CLI Layer (github_archiver.py)                              |
|   └─ ArchiverOrchestrator (new: coordinated execution)     |
+─────────────────────────────────────────────────────────────+
                    ↙                         ↘
+───────────────────────────────+    +────────────────────────────────┐
| Domain Services               |    │ Verification Layer             │
│   ├─ GitHubService            │    │   ├─ UploadVerifier (new)      │
│   ├─ BrowserService           │    │   ├─ ExtractionTester (new)    │
│   ├─ RateLimitManager (new)   │    │   └─ JournalMigrator (new)     │
│   └─ FileSplitterService      │    └──────────────────────────────┘
+───────────────────────────────+
                    ↘
+─────────────────────────────────────────────────────────────+
| Infrastructure Layer                                        |
│   ├─ Playwright CDP wrapper                                 │
│   ├─ FileSystem (temp dir, journal writes)                  │
│   └─ HTTP client (GitHub API with rate limiting)           │
+─────────────────────────────────────────────────────────────+
```

---

## Components and Responsibilities

### New: `RateLimitManager`

**Purpose:** Detect GitHub API rate limits and apply exponential backoff.

**Responsibilities:**
- Parse `X-RateLimit-Remaining`, `X-RateLimit-Reset` headers from responses
- Calculate wait time when remaining < threshold (e.g., < 50 requests)
- Exponential backoff: `wait = min(60, base_delay * 2^retry_count)`
- Resume after reset timestamp or calculated delay

**Dependencies:** `github_api.GitHubAPI`

---

### New: `UploadVerifier`

**Purpose:** Validate upload success with message ID + hash tracking.

**Responsibilities:**
- Generate per-upload session ID (UUID4)
- Store SHA256 hash of uploaded file in journal
- Wait for new message in feed using mutation observer
- Verify message contains correct filename AND matches stored hash
- Return `verified` or `failed` with error details

**Dependencies:** `browser_max.BrowserMAX`, `journal.Journal`

---

### New: `JournalMigrator`

**Purpose:** Handle journal version upgrades gracefully.

**Responsibilities:**
- Read journal version from file header (default 0 if missing)
- Apply migrations for version < current (`CURRENT_VERSION = 1`)
- Transform legacy fields to new schema
- Save migrated journal with new version
- Log migration actions

**Migrations (v0 → v1):**
- Add `session_id: str` field to all entries
- Convert `timestamp: datetime` to ISO 8601 string

---

### Modified: `ArchiverOrchestrator`

**Purpose:** Coordinate end-to-end flows with new verifiers.

**Responsibilities:**
- Call `RateLimitManager.check()` before each GitHub API call
- Use `UploadVerifier.verify()` after browser upload
- Apply `JournalMigrator.migrate()` when loading journals
- Track counts of successful / verified / failed / skipped

---

### Existing Components (Enhanced)

| Component | Enhancement |
|-----------|-------------|
| `journal.Journal` | Add `version = 1` constant; migrate on load |
| `browser_max.BrowserMAX` | Store message ID per upload session |
| `github_api.GitHubAPI` | Return rate limit headers with responses |

---

## Data Flow

### Upgrade: GitHub API with Rate Limiting

```
1. ArchiverOrchestrator → GitHubService.list_repos()
2. GitHubService calls GitHub REST API
3. RateLimitManager checks headers:
   ├─ X-RateLimit-Remaining >= 50 → proceed
   └─ < 50 → wait = min(60, base_delay * 2^retry_count)
4. Retry after delay or reset timestamp
5. Return repos list (or None if exhausted retries)
```

### Upgrade: Upload with Hash Verification

```
1. ArchiverOrchestrator → FileSplitterService.prepare(path)
2. Split returns [file_paths] (single ZIP or volume list)
3. For each file:
   ├─ Generate session_id = UUID4()
   ├─ Calculate hash = SHA256(file_path)
   ├─ Store entry: {session_id, hash, file_name}
   ├─ BrowserMAX.send_message(text, files) → upload completes
   └─ UploadVerifier.verify(session_id):
      ├─ MutationObserver detects new message in feed
      ├─ Extract filename from DOM
      ├─ Check filename matches stored file_name
      ├─ (Optional: fetch file hash from MAX if available)
      └─ Return verified if all checks pass
4. Journal entry updated with {status: "sent", session_id, verified_at}
```

### Upgrade: Journal Loading with Migration

```
1. ArchiverOrchestrator → Journal.load()
2. JournalMigrator.migrate(path):
   ├─ Read file header → version = 0 (if missing)
   ├─ If version < CURRENT_VERSION:
      │  ├─ Transform fields (add session_id, ISO timestamps)
      │  └─ Save migrated version
   └─ Return Journal instance with current schema
3. Journal used in orchestrator flow
```

---

## Error Handling Strategy

| Error Type | Handler | Action |
|------------|---------|--------|
| Rate limit exceeded (403) | `RateLimitManager` | Wait, retry exponential backoff; abort if exhausted |
| Upload timeout | `BrowserMAX` | Retry up to config retries; mark failed in journal |
| Hash mismatch | `UploadVerifier` | Mark failed with error "hash mismatch"; log details |
| Journal corruption | `JournalMigrator` | Backup old journal; create new empty journal with warning |
| Browser disconnect | `BrowserMAX` | Attempt reconnect; fallback to local browser if enabled |

**Critical failures:** Missing GitHub token, Chrome not available → abort with clear console message and non-zero exit code.

---

## Testing Strategy

### Unit Tests (New)

```
tests/
├─ test_rate_limit_manager.py
│  ├─ test_detect_rate_limit_header() → returns True when remaining < threshold
│  ├─ test_calculate_backoff_time() → exponential growth validated
│  └─ test_reset_after_timestamp() → waits until reset time
├─ test_upload_verifier.py
│  ├─ test_generate_session_id() → UUID4 format
│  ├─ test_store_hash_in_journal() → SHA256 stored correctly
│  └─ test_verify_with_match() → returns verified when filename + hash match
├─ test_journal_migrator.py
│  ├─ test_read_version() → defaults to 0 if missing
│  ├─ test_migrate_v0_to_v1() → adds session_id, ISO timestamps
│  └─ test_skip_already_upgraded() → no-op when version == current
```

### Integration Tests (New)

```
tests/
├─ test_github_rate_limit_integration.py
│  └─ test_sync_with_rate_limit() → processes repos with backoff pauses
├─ test_upload_verification_integration.py
│  ├─ test_single_file_upload() → verifies upload + hash match
│  └─ test_volume_upload_sequence() → verifies all volumes in sequence
└─ test_journal_migration_integration.py
   └─ test_load_legacy_journal() → migrates old format, loads successfully
```

---

## Implementation Plan

### Phase 1: Rate Limit Manager (Low effort, High impact)

**Tasks:**
- Add `RateLimitManager` class to `github_api.py`
- Parse headers in GitHub API response wrapper
- Integrate into `ArchiverOrchestrator` before each API call
- Add unit tests for backoff calculation

**Estimated:** 2-3 hours

---

### Phase 2: Upload Verifier (Medium effort, High impact)

**Tasks:**
- Modify `browser_max.py` to store message ID per upload session
- Add `UploadVerifier` class with hash verification logic
- Integrate into orchestrator after upload completes
- Update journal schema to include `session_id`, `hash`, `verified_at`
- Add integration tests for verify flow

**Estimated:** 4-5 hours

---

### Phase 3: Journal Migration (Low effort, Medium impact)

**Tasks:**
- Add `CURRENT_VERSION = 1` constant to `journal.py`
- Create `JournalMigrator` class
- Migrate on journal load (v0 → v1: add session_id, ISO timestamps)
- Test with legacy journal file

**Estimated:** 1 hour

---

### Phase 4: Orchestrator Integration (Medium effort)

**Tasks:**
- Refactor `ArchiverOrchestrator` to use new verifiers
- Update error handling per verifier outputs
- Add progress tracking for verified vs unverified counts
- Test end-to-end sync with all improvements active

**Estimated:** 3 hours

---

## Open Questions

1. **Hash Verification Source:** Should we fetch the hash from MAX's server (if available) or rely on client-side SHA256?
   - *Decision:* Client-side SHA256 only — MAX API doesn't expose file hashes; this is sufficient for verifying upload integrity.

2. **Rate Limit Threshold:** What `X-RateLimit-Remaining` threshold triggers backoff?
   - *Decision:* < 50 requests triggers backoff. This leaves buffer for other operations (repo metadata, downloads).

3. **Journal Migration: Should we keep legacy journals as backups?**
   - *Decision:* Yes — backup to `journal.json.bak` before migration with warning log.

4. **Session ID Storage:** Where to store session IDs?
   - *Decision:* In journal entries as `session_id: str`. Allows cross-reference between upload and verification logs.

---

## Success Criteria

| Criterion | Metric |
|-----------|--------|
| Rate limit handling | 0 rate-limit failures in 1000-repo sync test |
| Upload verification | 0 false positives (old filename matches) in 50 uploads |
| Journal compatibility | Legacy journal loads successfully with migration |
| Test coverage | ≥ 85% on core modules after new tests added |

---

## Rollback Plan

Each improvement is isolated and reversible:

- **Rate limit manager:** Remove `RateLimitManager` calls from orchestrator
- **Upload verifier:** Revert to filename-only matching in browser_max.py
- **Journal migration:** Delete migrated files; restore from `.bak` backup

No database schema changes — all changes are additive or replaceable.
