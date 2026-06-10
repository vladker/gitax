# GitHub Archiver

Back up GitHub repositories (and more) to MAX Messenger via browser automation.

## Features

- **GitHub Archiver** — Download repos as ZIP, auto-split large files (>49 MB) into 7z volumes, send to MAX channel
- **PyPI Archiver** — Download top Python packages (source + wheel) from Hugovk dataset
- **Backuper** — Archive local folders to MAX with compression and volume splitting
- **Media Archiver** — Watch a folder, upload images/videos to MAX
- **Channel Downloader** — Download all files from a MAX channel

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Configure (copy template and fill in your tokens)
cp .env.example .env
# Edit .env with your GITHUB_TOKEN and MAX_CHANNEL_URL

# Run
python github_archiver.py
```

## Configuration

| File | Purpose |
|------|---------|
| `.env` | **Secrets** — tokens, channel URLs (gitignored) |
| `.env.example` | Template for `.env` |
| `config.yaml` | Non-sensitive settings (gitignored) |

### Required `.env` variables

```env
GITHUB_TOKEN=github_pat_xxx        # GitHub Personal Access Token (repo scope)
CHANNEL_max=https://web.max.ru/... # MAX channel URL for GitHub archives
```

### Optional `.env` variables

```env
CHANNEL_pypi=https://web.max.ru/...   # PyPI packages channel
CHANNEL_media=https://web.max.ru/...  # Media files channel
CHANNEL_backup=https://web.max.ru/... # Folder backups channel
MEDIA_WATCH_DIR=C:/path/to/watch      # Folder to watch for media
```

All config options can also be set in `config.yaml` (see `.env.example` for the full list).

## Browser Automation

- Uses **Playwright** with CDP to connect to an existing Chrome instance (port 9222)
- **Before running:** Open Chrome with `--remote-debugging-port=9222` and navigate to your MAX channel URL
- This preserves your login session — no need to re-authenticate

```bash
# Start Chrome with debugging port
chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\chrome-debug"
```

## How It Works

1. **GitHub Archiver** fetches top repos (or your starred/owned repos) via GitHub REST API
2. Downloads each repo as ZIP (default branch)
3. If ZIP > 49 MB, splits into `.7z.001`, `.7z.002`... volumes using 7-Zip
4. Sends each volume as a separate message to MAX via browser automation
5. Tracks processed repos in `journal.json` to avoid duplicates

## Project Structure

```
github_archiver.py      # Main entry point + menus
github_api.py           # GitHub REST API wrapper
browser_max.py          # MAX automation (Playwright + CDP)
journal.py              # JSON journal for tracking
config.py               # Pydantic config models
config_utils.py         # Config helpers
logging_config.py       # Logging setup
pypi_api.py             # PyPI / Hugovk dataset API
pypi_libs_archiver.py   # PyPI archiver logic
pypi_libs_journal.py    # PyPI journal
backuper.py             # Folder backup to MAX
backuper_journal.py     # Backup journal
media_archiver.py       # Media file watcher + uploader
media_journal.py        # Media journal
channel_downloader.py   # Download files from MAX channel
scroll_registry.py      # Scroll position tracking
rollback_journal.py     # Rollback support
requirements.txt        # Python dependencies
.env.example            # Config template (safe to commit)
.gitignore              # Excludes secrets, logs, cache, journals
```

## Requirements

- Python 3.10+
- 7-Zip (`7z` in PATH) for volume splitting
- Chrome/Chromium with `--remote-debugging-port=9222`
- MAX account with channel access

## License

MIT