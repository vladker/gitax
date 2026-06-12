# NPM Archiver — Design

**Date:** 2026-06-12  
**Topic:** NPM packages archiver (аналог PyPI)  
**Status:** validated

---

## Problem Statement

Добавить модуль для скачивания популярных NPM-пакетов и отправки их в отдельный канал MAX. UX повторяет PyPI-архиватор: меню → загрузить топ / синхронизировать → браузерная отправка.

## Constraints

- **Не менять существующие модули** — только добавлять новый, минимальные интеграционные правки
- **Следовать паттерну PyPI** — та же архитектура, те же миксины, тот же UX
- **NPM tarball — единственный формат** — в отличие от PyPI (source + wheel), npm хранит один tarball на версию
- **Search API ограничен** — 25 результатов/страница, пагинация через `from=N`

## Approach

**Копируем PyPI-архиватор 1:1**, заменяя PyPI API на npm registry API.

**Почему:** PyPI-паттерн уже работает, проверен, интегрирован в меню и конфиг. Повторение — cheapest решение.

**Альтернативы:**
- *Общий абстрактный "PackageArchiver"* — слишком много изменений в существующем коде, неоправданный оверхед для одного нового пакета
- *Встраивание в PyPI модуль* — нарушает SRP, путает пользователей

## Architecture

```
github_archiver.py
  ├── [3] NPM → _run_npm_menu()
  │    ├── run_npm_archiver()  → NpmArchiver.load_top_packages()
  │    └── run_npm_sync()      → NpmArchiver.sync_packages()
  │
npm_archiver.py
  ├── NPMAPI          — fetch, info, download
  ├── NpmJournal      — dedup (name, version)
  └── BrowserMAX      — shared upload (send_message_with_files)
```

## Components

### 1. `npm_api.py` — API клиент

**Исключения:** `NPMError → NetworkError, RateLimitError`

**Класс `NPMAPI(LogMixin)`:**
- `BASE_URL = "https://registry.npmjs.org"`
- `_request_with_backoff()` — экспоненциальная откатка (429, 5xx)
- `fetch_top_packages(limit)` — пагинация через `/-/v1/search?sort=downloads&order=desc`, возвращает `[name, version, description, weekly_downloads]`
- `get_package_info(name)` — GET `/{name}`, кэш `_cache: dict`
- `download_package(name, output_dir)` — скачивает `dist.tarball` latest версии

### 2. `npm_journal.py` — журнал

**Класс `NpmJournal(BaseJournal)`:**
- Файл: `npm_journal.json`
- Дедуп: `(name, version)`
- Методы: `add`, `mark_failed`, `exists`, `exists_by_name`, `get_all_entries`, `update`

### 3. `npm_archiver.py` — оркестратор

**Класс `NpmArchiver(LogMixin, BrowserInitMixin)`:**
- `_channel_key = "npm"`
- `load_top_packages()` — fetch → filter → download → upload → journal → cleanup
- `sync_packages()` — проверить обновления версий
- `_build_message_text()` — форматирование для MAX

### 4. Интеграция в меню

**Главное меню:** `[3] NPM — JavaScript пакеты`, перенумерация 4→5, 5→6

**Подменю NPM:**
- `[1] Загрузить топ NPM пакетов`
- `[2] Синхронизировать NPM пакеты`
- `[0] Назад`

### 5. Конфиг

- `NpmArchiverConfig` в `config/model.py` (limit=20, output_dir, retries, split_mode)
- `ChannelsConfig.npm: str = ""`
- `ChannelRegistry.npm: list[ChannelEntry]`
- `VALID_CHANNEL_FUNCTIONS += ("npm",)`
- `_CHANNEL_MIGRATION_MAP["npm"]` в loader.py
- `is_setup_complete` проверяет `"npm"`
- `.env.example` → `CHANNEL_npm`

## Data Flow

```
[3] NPM → [1] Загрузить топ
  ↓
_ensure_channel_ready("npm")
  ↓
NPMAPI.fetch_top_packages(limit)
  ↓
Filter по NpmJournal (skip sent)
  ↓
For each package:
  get_package_info() → download_package() → build_message()
    → browser.send_message_with_files()
    → journal.add() → cleanup temp
  ↓
Print stats: sent / skipped / failed
```

## Error Handling

- **429 rate limit:** экспоненциальная откатка (как PyPI)
- **Сетевые ошибки:** 3 ретрая, 10с задержка (из конфига)
- **Пакет не загружен:** `mark_failed`, продолжить
- **MAX отправка не удалась:** ретраим, затем `mark_failed`
- **Graceful shutdown:** SIGINT/SIGTERM → cleanup + save journal

## Testing Strategy

- `tests/test_npm_api.py` — моки requests, кэш, 429/5xx, пустые результаты
- `tests/test_npm_journal.py` — дедуп, mark_failed, update, коррупция JSON

## File Changes

| File | Action |
|------|--------|
| `npm_api.py` | Create |
| `npm_journal.py` | Create |
| `npm_archiver.py` | Create |
| `github_archiver.py` | Modify (menu + dispatch) |
| `config/model.py` | Modify (NpmArchiverConfig + npm channel) |
| `config/loader.py` | Modify (migration map) |
| `config_utils.py` | Modify (setup check + mapping) |
| `.env.example` | Modify (CHANNEL_npm) |
| `tests/test_npm_api.py` | Create |
| `tests/test_npm_journal.py` | Create |

**3 новых файла, 5 изменений.**
