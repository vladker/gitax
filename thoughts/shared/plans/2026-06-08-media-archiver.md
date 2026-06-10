# Media Archiver — Implementation Plan

## Overview

Новый модуль `media_archiver.py` — самостоятельный скрипт для загрузки фото и видео из папки в MAX канал.

## Micro Tasks

### Task 1: Create `media_archiver.py`

**File:** `media_archiver.py` (новый)

**Responsibilities:**
- Load config: `load_dotenv()` + `yaml.safe_load(config.yaml)`
- Read `MEDIA_CHANNEL_URL` from env, `MEDIA_WATCH_DIR` from env or config
- Validate both values exist
- Initialize `BrowserMAX` with `MEDIA_CHANNEL_URL`
- Initialize media journal (`media_journal.json`)
- Scan `MEDIA_WATCH_DIR` for media files by extension
- Sort files by creation time (ascending)
- For each file: check journal → upload → record → continue
- Save journal on exit

**Key implementation details:**

```
Class: MediaArchiver
  __init__(config_path): load config, init journal, init browser
  _load_config(): dotenv + yaml, read MEDIA_CHANNEL_URL + MEDIA_WATCH_DIR
  _scan_files(): os.walk + extension filter + ctime sort
  _is_sent(filename, size): check media_journal.json
  _mark_sent(filename, size): write to journal
  _mark_failed(filename, size): write to journal
  run(): main loop — scan → sort → upload each → save → exit
```

**Config loading pattern** (follows `github_archiver.py`):
- `load_dotenv()` first
- Load `config.yaml` if exists
- Env vars override yaml values
- Exit with error if `MEDIA_CHANNEL_URL` or `MEDIA_WATCH_DIR` missing

**Journal format** (`media_journal.json`):
```json
{
  "entries": [
    {"filename": "photo.jpg", "size_bytes": 4829312, "sent_at": "2026-06-08T14:32:00", "status": "sent"},
    {"filename": "video.mp4", "size_bytes": 52428800, "sent_at": "2026-06-08T14:35:00", "status": "failed"}
  ]
}
```

**Deduplication key:** `(filename, size_bytes)` tuple. If file with same name AND size exists in journal with status "sent", skip.

**File scanning:**
- Extensions: `.jpg, .jpeg, .png, .gif, .webp, .bmp, .tiff, .mp4, .mov, .avi, .mkv, .webm`
- Recursive scan via `os.walk()`
- Skip files with size 0
- Sort by `os.path.getctime()` ascending (oldest first)

**Upload flow per file:**
1. Check `(filename, size)` in journal → if sent, skip
2. `browser.send_message_with_files("", [filepath], split_threshold_mb=999999, expected_extensions=[file_ext])`
3. If success → `_mark_sent()`, log
4. If fail after retries → `_mark_failed()`, log, continue to next
5. File stays on disk (no delete, no move)

**Journal atomic writes** (follow existing `journal.py` pattern):
- Write to temp file → `os.replace()` to target
- Lock file to prevent concurrent writes

**Graceful exit:**
- Save journal before exit
- Close browser connection
- Use `atexit.register()` for cleanup

### Task 2: Update `.env.example`

**File:** `.env.example` (edit)

Add after existing entries:
```env
# Media archiver
MEDIA_CHANNEL_URL=
MEDIA_WATCH_DIR=
```

### Task 3: Update `config.yaml`

**File:** `config.yaml` (edit if exists, else skip)

Add section:
```yaml
media_archiver:
  watch_dir: null
  extensions:
    images: [.jpg, .jpeg, .png, .gif, .webp, .bmp, .tiff]
    videos: [.mp4, .mov, .avi, .mkv, .webm]
  retries: 3
  retry_delay: 10
```

## Dependencies

- Task 1 depends on nothing (independent)
- Task 2 depends on nothing (independent)
- Task 3 depends on nothing (independent)

**All tasks can run in parallel.**

## Testing

- Verify `media_archiver.py` imports without error
- Verify config loading reads env vars correctly
- Verify journal deduplication logic
