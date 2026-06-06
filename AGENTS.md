# GitHub Archiver — AGENTS.md

Simple Python CLI tool that downloads GitHub repositories as ZIP and sends them to the MAX messenger via browser automation.

## Quick Start

```bash
pip install -r requirements.txt

# Create .env with your tokens (see .env.example)
copy .env.example .env
# Edit .env file with your GITHUB_TOKEN and MAX_CHANNEL_URL

python github_archiver.py
```

## Key Files

| File | Purpose |
|------|---------|
| `.env` | Tokens/URLs — **not committed** (gitignored) |
| `.env.example` | Template for .env (safe to commit) |
| `github_archiver.py` | Entry point — run this |
| `github_api.py` | GitHub API (REST v3) |
| `browser_max.py` | MAX browser automation via Playwright + CDP |
| `journal.py` | Tracks processed repos (JSON) |
| `logging_config.py` | Logging setup (archiver.log) |
| `config.yaml` | Non-sensitive settings — optional |

## Token Setup

Create a `.env` file in the project root (or set environment variables):

```env
GITHUB_TOKEN=github_pat_...
MAX_CHANNEL_URL=https://web.max.ru/...
```

Priority: `.env` file / env var > `config.yaml`

## Browser Automation

- Uses **Playwright** with CDP to an existing Chrome instance (port 9222)
- Browser must be open at the MAX channel URL before running
- Connects via `connect_over_cdp` — preserves existing session/cookies, no login needed

### Upload Flow (single file)

1. Connect to Chrome via CDP (`localhost:9222`)
2. Navigate to the MAX channel URL
3. Type repository description message, send it (Enter)
4. Click upload button → file chooser → select file
5. Wait for upload to complete (MutationObserver monitors DOM)
6. Press Enter to send the file message
7. Wait for file message confirmation in the feed (scans new messages with **filename matching**)

### Multi-Volume (7z split)

Files larger than **49 MB** are split into `.7z.001`, `.7z.002`, etc. volumes using 7-Zip (threshold via `config.yaml`):

- Each volume is uploaded as a separate message.
- Volumes are sent sequentially (`.001` → `.002` → `.003` ...).
- Each volume is **deleted immediately** after upload confirmation.

### Upload Confirmation

The confirmation system checks that the **specific filename** (including volume number `.7z.003`) appears in the feed, preventing false matches from previously uploaded volumes:

1. MutationObserver tracks DOM changes for new file messages
2. `_check_upload_in_lenta(expected_filename)` scans the feed for a message containing the exact filename
3. `_wait_for_file_message()` monitors new messages from a baseline count, matching by filename
4. All three checkpoints validate the **current file's name**, not just any `.zip` message

## Config Options (config.yaml)

```yaml
archiver:
  limit: 1000               # How many repos to fetch from GitHub
  retries: 3                # Upload retry count
  retry_delay: 10           # Delay between retries (seconds)
  repo_delay: 30            # Pause between repos (seconds)
  split_threshold_mb: 49    # Files above this size are split into 7z volumes
  use_local_browser: false  # false = CDP to existing Chrome, true = launch new
  output_dir: "./temp"      # Temp directory for downloads
```

## PyPI Archiver

Downloads top Python packages from Hugovk dataset and sends them to a separate MAX channel.

### Configuration
- `pypi.channel_url` — MAX channel URL for PyPI packages
- `pypi_archiver.limit` — Default number of packages
- `pypi_archiver.output_dir` — Temp download directory
- Both .tar.gz (source) and .whl (wheel) distributions are downloaded

### Token Setup
Add to `.env`:
```env
PYPI_CHANNEL_URL=https://web.max.ru/...
```

## .gitignore

Sensitive files that must never be committed:
- `.env`, `.env.*` (tokens, secrets)
- `config.yaml`, `*.yaml`, `*.yml`
- `*.json` (journal, auth-state)
- `*.log`, `archiver.log`
- `temp/`, `*.zip`

## Agent-Browser Skill

For browser automation details: `C:/Users/vldkr/.agents/skills/agent-browser/SKILL.md`
