---
date: 2026-06-10
topic: "Initial Setup & Lazy Config Prompt"
status: draft
---

## Problem Statement

GitHub Archiver currently has no guided initial setup. When config is missing (no `.env`, empty `config.yaml`), the app either crashes with `sys.exit(1)` or certain functions become inaccessible with no recovery path. Users must manually edit `.env` and `config.yaml` without guidance on what values are needed or where to get them.

We need a **setup wizard** that:
- Appears as menu item [0] at the top when first run
- Collects all required settings interactively
- Moves to the Service section after completion
- Still allows direct config file editing
- Falls back to interactive prompts if config is missing when a function is called

## Constraints

- No new external dependencies
- No new files for state tracking — infer setup state from actual config presence
- Must not break existing config loading chain (env var > config.yaml > default)
- Must handle `.env` file rotation (user may clear it and needs setup to reappear)
- Must work on Windows (the target platform)

## Approach

**Two-tier system:**

1. **Full setup wizard** — interactive menu item that collects all settings, writes to `.env` and `config.yaml`. Accessible as `[0]` before setup, moves to Service section after.

2. **Lazy per-function prompts** — when a specific upload function is called and its channel URL is missing, prompt the user interactively instead of crashing. Saves to `.env` for persistence.

**Why not just a config checker?** The user wants the setup to be a visible menu item that transitions. The lazy prompts are a safety net for edge cases (manual config clearing, partial config, etc.).

## Architecture

### 1. New utilities in `config_utils.py`

Three functions added:

| Function | Purpose |
|----------|---------|
| `set_env_value(key, value)` | Parse `.env` → update/add `KEY=VALUE` → save → reload env |
| `ensure_channel_url(config, channel_name, label)` | Get URL; if missing, prompt interactively and save to `.env` |
| `is_setup_complete(config)` | Check `GITHUB_TOKEN` + all 4 `CHANNEL_*` are configured |

**`set_env_value(key, value)` — behavior:**
- Read `.env` line by line, preserve comments and blank lines
- If key exists, update the value in-place
- If key doesn't exist, append at end of file (after a comment separator if first addition)
- Write back atomically (temp file + rename, matching journal pattern)
- Call `load_dotenv(override=True)` to pick up changes
- If `.env` doesn't exist, create it with a header comment

**`is_setup_complete(config)` — check sources:**
- `GITHUB_TOKEN` from env var (not from config.yaml — token should never be in yaml)
- `CHANNEL_max`, `CHANNEL_pypi`, `CHANNEL_media`, `CHANNEL_backup` from env var or `config.yaml channels.*`
- Returns `True` only if all 5 values are non-empty

### 2. `GitHubArchiver._initial_setup()` wizard

Interactive wizard with steps:

```
Шаг 1: GitHub токен
  Текущее: ghp_****1234
  Введите токен (Enter = оставить): _

Шаг 2: MAX канал (GitHub архивы)
  Текущее: https://web.max.ru/...
  Введите URL (Enter = оставить): _

Шаг 3: PyPI канал
  ...

Шаг 4: Media канал
  ...

Шаг 5: Backup канал
  ...

Шаг 6: Параметры архивации
  Лимит репозиториев [100]:
  Retries [3]:
  Задержка между репо [30]:
  Порог разделения (MB) [49]:
```

Writing behavior:
- Steps 1-5 → write to `.env` via `set_env_value()`
- Step 6 → write to `config.yaml` (read, update, save)
- Each step shows current value as default
- Empty input = keep current value
- After all steps: reload config, show summary, mark setup complete

### 3. Menu changes

**Main menu before setup:**
```
  ⚡ Требуется начальная настройка

  [0] ⚡ Начальная настройка
  [1] GitHub — репозитории
  [2] PyPI — Python библиотеки
  [3] Backuper — бэкап папок в канал
  [4] Файлы — медиа, скачивание, экспорт
  [5] Сервис — журналы, настройки
  [X] Выход
```

**Main menu after setup:**
```
  [1] GitHub — репозитории
  [2] PyPI — Python библиотеки
  [3] Backuper — бэкап папок в канал
  [4] Файлы — медиа, скачивание, экспорт
  [5] Сервис — журналы, настройки
  [0] Выход
```

**Service menu after setup adds:**
```
  [1] Очистить журналы
  [2] ⚙ Настройки
  [0] Назад
```

Choice dispatch logic:
- When setup incomplete: `'0'` → setup wizard, `'x'` → exit, `'1'-'5'` → submenus
- When setup complete: `'0'` → exit, `'1'-'5'` → submenus (current behavior)
- Service menu: `'2'` → settings (re-runs setup wizard)

### 4. `_load_config()` changes

Current: `get_channel_url(config, "max", required=True)` → `sys.exit(1)` if missing.

New: `get_channel_url(config, "max", required=False)` → returns `""` if missing. Each channel is loaded with `required=False`. If token is still missing, `sys.exit(1)` (fundamental dependency — can't work without GitHub API).

### 5. Lazy prompts in upload functions

When a function needs a channel URL that's empty:

```
  ⚠ URL канала "MAX канал" не указан.

  [Enter] Ввести URL сейчас (сохранится в .env)
  [S] Пропустить — функция недоступна
```

On enter → prompt for URL → call `set_env_value()` → reload env → proceed with function.
On skip → print "Функция недоступна без URL канала" → return to menu.

Functions with lazy prompts:
- GitHub ops → ensure `CHANNEL_max`
- PyPI ops → ensure `CHANNEL_pypi`
- Media upload → ensure `CHANNEL_media`
- Backuper ops → ensure `CHANNEL_backup`
- Channel ops (download, export, delete) → ensure `CHANNEL_max`

### 6. Auto-prompt on first launch

In `run()`, before first menu display:
- If `not is_setup_complete()`: print banner
- `[Enter]` → run setup wizard
- `[S]` → skip to menu with [0] option

```
╔══════════════════════════════════════════════════════════════╗
║               ДОБРО ПОЖАЛОВАТЬ В GITHUB ARCHIVER             ║
║                                                              ║
║  Программа не настроена. Для работы необходимо указать:      ║
║  • GitHub токен для доступа к API                            ║
║  • URL каналов MAX для разных типов архивов                  ║
║                                                              ║
║  [Enter] Выполнить начальную настройку                       ║
║  [S] Пропустить (пункт настройки будет в меню)               ║
╚══════════════════════════════════════════════════════════════╝
```

## Components

| Component | Responsibility |
|-----------|---------------|
| `config_utils.set_env_value()` | Low-level `.env` file manipulation |
| `config_utils.ensure_channel_url()` | Interactive channel URL resolution |
| `config_utils.is_setup_complete()` | Setup state query |
| `GitHubArchiver._initial_setup()` | Full setup wizard |
| `GitHubArchiver._show_main_menu()` | State-aware menu rendering |
| `GitHubArchiver.run()` | Auto-prompt + state-aware dispatch |
| Upload functions (all) | Call `ensure_channel_url()` before proceeding |

## Data Flow

```
App start
  → run()
    → is_setup_complete()?
      → NO: show auto-prompt
        → setup wizard → set_env_value() x5 → reload
      → YES: show standard menu
    → user picks function
      → function checks its channel URL
        → missing: ensure_channel_url() → set_env_value() → proceed
        → present: proceed normally
```

## Error Handling

| Scenario | Behavior |
|----------|----------|
| User cancels setup mid-way | Partial config stays, setup remains incomplete |
| `.env` is read-only | Show error with path, suggest manual edit, continue to menu |
| Invalid URL entered | Warn but save — validation happens at connection time |
| `.env` doesn't exist | `set_env_value()` creates it with header |
| Lazy prompt cancelled | Function returns to menu gracefully |
| Token still missing after setup | `_load_config()` still calls `sys.exit(1)` — fundamental |

## Testing Strategy

**Unit tests:**
- `test_set_env_value_new_key()` — add key to existing `.env`
- `test_set_env_value_update_key()` — update existing key
- `test_set_env_value_new_file()` — create `.env` from scratch
- `test_set_env_value_preserve_comments()` — don't corrupt file structure
- `test_is_setup_complete_all()` — all values present
- `test_is_setup_complete_missing()` — various partial states

**Manual tests:**
- Fresh environment: run app, verify auto-prompt, run wizard, verify menu changes
- Restart after wizard: verify setup stays complete
- Clear `.env`: verify setup reappears
- Call function without config: verify lazy prompt
- Edit config files directly: verify setup state updates

## Open Questions

- Should the wizard validate the GitHub token format (regex for `ghp_*`/`github_pat_*`)?
  → Yes, basic validation (non-empty, known prefix) with a warning, not a hard block.
- Should `config.yaml` writing handle merging or full overwrite?
  → Merge: read existing, update specific keys, write back. Preserves unknown sections.
