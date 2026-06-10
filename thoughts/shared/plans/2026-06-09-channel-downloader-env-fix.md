# Channel Downloader ENV Fix — Implementation Plan

**Goal:** Fix `channel_downloader.py` so `MAX_CHANNEL_URL` is read from environment variables, matching the pattern used in `github_archiver.py`, `media_archiver.py`, and `pypi_libs_archiver.py`.

**Architecture:** One-line-injection into `_load_config()` + one new test verifying the injection works. All other archivers already have this pattern and are verified healthy.

---

## Dependency Graph

```
Batch 1 (parallel): 1.1 [fix + test — no deps]
Batch 2 (parallel): 2.1 [verification — depends on 1.1]
```

---

## Batch 1: Fix (parallel — 1 implementer)

### Task 1.1: Add MAX_CHANNEL_URL env injection to `_load_config()`

**File:** `channel_downloader.py`
**Test:** `tests/test_channel_downloader.py` (append new test class)
**Depends:** none

#### The Bug

`_load_config()` (lines 220-236) loads `config.yaml` and sets defaults for the `channel_downloader` section, but never injects `MAX_CHANNEL_URL` from environment variables into `config['max']['channel_url']`. Meanwhile, `_init_browser()` (line 241) reads `self.config.get('max', {}).get('channel_url', '')` — so the channel URL is silently empty when not present in `config.yaml`.

#### Verified: Other archivers are healthy

- **`media_archiver.py`** (lines 183-191): Reads `MEDIA_CHANNEL_URL` and `MEDIA_WATCH_DIR` from `os.environ`. Env var takes priority over `config.yaml`. Exits with error if missing. **No fix needed.**
- **`pypi_libs_archiver.py`** (lines 81-91): Reads `PYPI_LIBS_CHANNEL_URL` from `os.environ`. Falls back to `config.yaml`. Exits with error if missing. **No fix needed.**
- **`github_archiver.py`** (lines 253-255): Reference pattern — injects `MAX_CHANNEL_URL` via `config.setdefault('max', {})['channel_url'] = env_channel`.

#### Fix — Edit `channel_downloader.py`

Add env var injection after loading config.yaml, before setting defaults. Insert after line 228 (`config = yaml.safe_load(f) or {}` block ends), before line 230 (`config.setdefault('channel_downloader', {})`):

**Before (lines 220-236):**
```python
    def _load_config(self, config_path: str) -> dict:
        """Загрузить конфигурацию с дефолтами для channel_downloader"""
        load_dotenv()
        config = {}

        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

        # Set defaults for channel_downloader section
        config.setdefault('channel_downloader', {})
        cd_config = config['channel_downloader']
        cd_config.setdefault('output_dir', './downloads')
        cd_config.setdefault('retries', 3)
        cd_config.setdefault('retry_delay', 5)

        return config
```

**After:**
```python
    def _load_config(self, config_path: str) -> dict:
        """Загрузить конфигурацию с дефолтами для channel_downloader"""
        load_dotenv()
        config = {}

        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

        # Приоритет: .env / env var > config.yaml
        env_channel = os.environ.get('MAX_CHANNEL_URL')
        if env_channel:
            config.setdefault('max', {})['channel_url'] = env_channel

        # Set defaults for channel_downloader section
        config.setdefault('channel_downloader', {})
        cd_config = config['channel_downloader']
        cd_config.setdefault('output_dir', './downloads')
        cd_config.setdefault('retries', 3)
        cd_config.setdefault('retry_delay', 5)

        return config
```

The change is 4 lines inserted between the yaml load block and the defaults block. Matches the `github_archiver.py` pattern exactly (same comment style, same `setdefault` approach).

#### Test — Append to `tests/test_channel_downloader.py`

Append this new test class at the end of the file (after line 394):

```python


class TestChannelDownloaderEnvConfig:
    """Tests for MAX_CHANNEL_URL environment variable injection"""

    @patch('channel_downloader.load_dotenv')
    @patch('channel_downloader.os.path.exists')
    @patch('channel_downloader.yaml.safe_load')
    @patch('channel_downloader.os.environ.get')
    def test_channel_url_from_env_var(self, mock_env_get, mock_yaml_load,
                                      mock_exists, mock_load_dotenv):
        """MAX_CHANNEL_URL env var is injected into config['max']['channel_url']"""
        from channel_downloader import ChannelDownloader

        mock_exists.return_value = True
        mock_yaml_load.return_value = {}  # empty config.yaml
        mock_env_get.return_value = "https://web.max.ru/env-channel"

        cd = ChannelDownloader("config.yaml")
        assert cd.config['max']['channel_url'] == "https://web.max.ru/env-channel"

    @patch('channel_downloader.load_dotenv')
    @patch('channel_downloader.os.path.exists')
    @patch('channel_downloader.yaml.safe_load')
    @patch('channel_downloader.os.environ.get')
    def test_env_var_overrides_yaml(self, mock_env_get, mock_yaml_load,
                                    mock_exists, mock_load_dotenv):
        """Env var takes priority over config.yaml value"""
        from channel_downloader import ChannelDownloader

        mock_exists.return_value = True
        mock_yaml_load.return_value = {
            'max': {'channel_url': 'https://web.max.ru/yaml-channel'}
        }
        mock_env_get.return_value = "https://web.max.ru/env-channel"

        cd = ChannelDownloader("config.yaml")
        assert cd.config['max']['channel_url'] == "https://web.max.ru/env-channel"

    @patch('channel_downloader.load_dotenv')
    @patch('channel_downloader.os.path.exists')
    @patch('channel_downloader.yaml.safe_load')
    @patch('channel_downloader.os.environ.get')
    def test_yaml_fallback_when_no_env(self, mock_env_get, mock_yaml_load,
                                       mock_exists, mock_load_dotenv):
        """config.yaml value is used when env var is not set"""
        from channel_downloader import ChannelDownloader

        mock_exists.return_value = True
        mock_yaml_load.return_value = {
            'max': {'channel_url': 'https://web.max.ru/yaml-channel'}
        }
        mock_env_get.return_value = None  # no env var

        cd = ChannelDownloader("config.yaml")
        assert cd.config['max']['channel_url'] == "https://web.max.ru/yaml-channel"
```

**Verify:**
```bash
# Run just the new tests
python -m pytest tests/test_channel_downloader.py::TestChannelDownloaderEnvConfig -v

# Run all channel_downloader tests to ensure no regressions
python -m pytest tests/test_channel_downloader.py -v
```

**Commit:** `fix(channel_downloader): read MAX_CHANNEL_URL from environment variables`

---

## Batch 2: Verification (depends on 1.1)

### Task 2.1: Full test suite regression check

**Depends:** 1.1

Run the complete test suite to confirm the fix doesn't break anything else:

```bash
python -m pytest tests/ -v
```

Expected: All existing tests pass. The 3 new tests in `TestChannelDownloaderEnvConfig` pass. No regressions in `test_journal.py`, `test_media_archiver.py`, `test_pypi_libs_archiver.py`, etc.

If any test fails, the most likely cause is the `channel_downloader` fixture (line 194) which mocks `yaml.safe_load` to return a config — it should still work because the fixture provides `'max': {'channel_url': '...'}` in its mock, and the env var injection only overwrites when `os.environ.get('MAX_CHANNEL_URL')` returns a truthy value (which it won't in the test environment).

---

## Summary

| Task | File | Change | Lines |
|------|------|--------|-------|
| 1.1 fix | `channel_downloader.py` | Insert 4 lines in `_load_config()` | +4 |
| 1.1 test | `tests/test_channel_downloader.py` | Append `TestChannelDownloaderEnvConfig` class | +48 |
| 2.1 verify | `tests/` | Run full suite | 0 |

**Total implementation:** ~52 lines of change across 2 files.
