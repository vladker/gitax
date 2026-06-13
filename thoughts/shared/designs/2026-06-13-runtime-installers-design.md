# Runtime Installers — Design

**Date:** 2026-06-13  
**Topic:** Runtime installer download and version sync for all archivers  
**Status:** validated

---

## Problem Statement

Каждый архивер отправляет библиотеки/пакеты в свой MAX-канал, но не отправляет **установочный файл соответствующего рантайма**. Получатель канала видит `flask-3.1.0.tar.gz`, но не знает, какой Python ему нужен.

**Цель:** при каждом sync проверять актуальность рантайма и, если вышла новая версия — скачать инсталлеры для всех ОС и отправить в тот же канал.

| Архивер | Рантайм | Инсталлеры |
|---------|---------|------------|
| PyPI | Python | Windows `.exe`, macOS `.pkg`, Linux `.tar.xz` |
| Cargo | Rust (rustup) | Windows `.exe`, macOS `.sh`, Linux `.sh` |
| NuGet | .NET SDK | Windows `.exe`, macOS `.pkg`, Linux `.sh` / `.tar.gz` |
| RubyGems | Ruby | Windows `.exe`, macOS (Homebrew), Linux (RVM/source) |
| GitHub | Git | Windows `.exe`, macOS `.pkg`, Linux `.tar.gz` |

## Constraints

- **Не менять существующие API-модули** — `runtime_api.py` полностью независим
- **Следовать паттерну журналов** — расширение через `runtime` ключ в JSON
- **Все ОС** — Windows, macOS, Linux для каждого рантайма
- **При каждом sync** — проверка версии рантайма запускается автоматически перед синхронизацией пакетов
- **Split mode** — инсталлеры > 49MB разбиваются на 7z тома (существующая логика)
- **Один модуль, одна точка поддержки** — DRY

## Approach

**Вынести рантайм-логику в `runtime_api.py`** — общий модуль для всех архиверов. Каждый архивер получает `sync_runtimes()` метод, который:

1. Спрашивает `RuntimeAPI` об актуальной версии
2. Сравнивает с `journal.get_runtime_version()`
3. При расхождении: скачивает → отправляет → обновляет журнал

**Почему не встраивать в каждый `*_api.py`:**
- Парсинг версий разных рантаймов — общий паттерн (HTTP → parse → compare)
- Скачивание инсталлеров — идентичный flow во всех архиверах
- Один модуль проще поддерживать и тестировать

**Почему не отдельный "RuntimeArchiver":**
- Рантайм должен идти в тот же канал, что и пакеты
- Пользователь ожидает Python installer в PyPI-канале, не в отдельном канале

## Architecture

```
runtime_api.py
├── RuntimeAPI (base)
│   ├── PythonRuntime    → python.org + GitHub releases
│   ├── RustRuntime      → rust-lang.org + GitHub releases
│   ├── DotNetRuntime    → dotnet.microsoft.com releases API
│   ├── RubyRuntime      → ruby-lang.org + rubyinstaller.org
│   └── GitRuntime       → git-scm.com + GitHub releases
│
├── RuntimeFactory       → get_runtime("pypi") → PythonRuntime
│
└── OS = {windows, macos, linux}  # enum

Each archiver journal:
├── .get_runtime_version() → str | None
├── .set_runtime_version(version, files, os_target)
└── .should_update_runtime() → bool

Each archiver:
├── sync_runtimes() → check → download → send → journal
└── sync_*() → теперь вызывает sync_runtimes() первым шагом
```

## Components

### 1. `runtime_api.py` — API клиент для всех рантаймов

**Базовый класс `RuntimeAPI(LogMixin)`:**
- `name: str` — идентификатор ("python", "rust", "dotnet", "ruby", "git")
- `get_latest_version() -> str` — актуальная стабильная версия
- `get_download_urls(version: str) -> list[dict]` — `{os, url, filename, size_hint}`
- `_parse_version(version_str: str) -> tuple[int, ...]` — для сравнения

**Конкретные реализации:**

| Класс | Версия (источник) | Скачивание (источник) |
|-------|-------------------|----------------------|
| `PythonRuntime` | GitHub releases `python/cpython` → tag `v3.x.y` | python.org → парсинг страницы releases |
| `RustRuntime` | GitHub releases `rust-lang/rust` → tag `2024-XX-XX` | win.rustup.rs / sh.rustup.rs |
| `DotNetRuntime` | `api.dotnet.microsoft.com/download/dotnet/releases.json` → latest LTS | dotnet.microsoft.com/download |
| `RubyRuntime` | GitHub releases `ruby/ruby` → tag `v3.x.y` | rubyinstaller.org (win) + ruby-lang.org (mac/linux) |
| `GitRuntime` | GitHub releases `git/git` → tag `v2.x.x.windows` | git-scm.com/downloads |

**`RuntimeFactory`:**
- `get_runtime(channel_key: str) -> RuntimeAPI` — маппинг `pypi → PythonRuntime`, `cargo → RustRuntime`, etc.

### 2. Journal расширения

Каждый существующий журнал (`PyPILibsJournal`, `CargoJournal`, `NuGetJournal`, `RubyGemsJournal`, `Journal`) получает три метода:

```
get_runtime_version() -> str | None
  → читает data["runtime"]["version"]

set_runtime_version(version: str, entries: list[dict])
  → записывает data["runtime"] = {version, entries: [{os, filename, sent_at}]}

should_update_runtime(latest_version: str) -> bool
  → сравнивает latest_version с saved version
```

**Журнальная структура:**
```json
{
  "libraries": [...],
  "runtime": {
    "version": "3.13.1",
    "last_updated": "2026-06-12T10:00:00",
    "entries": [
      {"os": "windows", "filename": "python-3.13.1-amd64.exe", "sent_at": "..."},
      {"os": "macos", "filename": "python-3.13.1-macos.pkg", "sent_at": "..."},
      {"os": "linux", "filename": "Python-3.13.1.tar.xz", "sent_at": "..."}
    ]
  }
}
```

### 3. `sync_runtimes()` — метод в каждом архивере

**Flow:**
```
sync_runtimes():
  1. runtime = RuntimeFactory.get_runtime(self._channel_key)
  2. latest = runtime.get_latest_version()
  3. saved = self.journal.get_runtime_version()
  4. Если latest == saved:
       print("✓ Рантайм актуален")
       return True
  5. urls = runtime.get_download_urls(latest)
  6. Для каждого URL:
       a. Download to temp dir
       b. Split if > threshold
  7. Build message: "🐍 Python 3.13.2 (обновление с 3.13.1)"
  8. browser.send_message_with_files(text, files)
  9. journal.set_runtime_version(latest, entries)
  10. Cleanup temp files
```

**Сообщение в MAX:**
```
🐍 Python 3.13.2

📝 Стабильная версия Python (обновление с 3.13.1)
🔧 Установочные файлы для всех платформ

🪟 Windows (amd64): 27.4 MB
🍎 macOS (Universal): 31.2 MB
🐧 Linux (source): 24.1 MB

🔗 python.org/downloads
```

Иконки: 🐍 Python, 🦀 Rust, ⚡ .NET, 💎 Ruby, 📦 Git

### 4. Интеграция в sync flow

Каждый `sync_*()` метод архивера получает **pre-sync** шаг:

```python
def sync_packages(self):
    # NEW: проверить рантайм первым
    self.sync_runtimes()
    # ... существующий flow проверки пакетов ...
```

### 5. Меню

Подменю каждого архивера получает пункт `[3]`:

```
  [1] Загрузить топ пакеты
  [2] Синхронизировать пакеты
  [3] Синхронизировать рантайм (отдельно)
  [4] Выход
```

Пункт `[3]` — для ручной проверки рантайма без синка пакетов.

### 6. Конфигурация

```yaml
runtime:
  enabled: true              # глобальный переключатель
  os_targets:                # какие ОС скачивать (по умолчанию все)
    - windows
    - macos
    - linux
  check_on_sync: true        # проверять при каждом sync пакетов
  output_dir: "./temp_runtime"
```

`config/model.py` получает `RuntimeConfig(BaseModel)`.

### 7. Batch runner интеграция

`batch_runner.py` при запуске sync для архивера автоматически включает runtime check:
- Если `runtime.check_on_sync = true` → `sync_runtimes()` вызывается перед `sync_packages()`
- Если `false` → только пакеты

## Data Flow

```
User: нажимает [2] Sync packages
    ↓
Archiver.sync_packages()
    ↓
┌─ sync_runtimes() ──────────────────────────┐
│                                             │
│  RuntimeFactory.get_runtime("pypi")         │
│    ↓                                       │
│  PythonRuntime.get_latest_version()         │
│    ↓  HTTP GET python/cpython releases      │
│  "3.13.2"                                  │
│    ↓                                       │
│  journal.get_runtime_version()              │
│    ↓  JSON read                             │
│  "3.13.1"                                  │
│    ↓                                       │
│  "3.13.2" != "3.13.1" → UPDATE             │
│    ↓                                       │
│  PythonRuntime.get_download_urls("3.13.2")  │
│    ↓                                       │
│  [win.exe, mac.pkg, linux.tar.xz]           │
│    ↓                                       │
│  Download each → temp_runtime/              │
│    ↓                                       │
│  Build message with all 3 files             │
│    ↓                                       │
│  browser.send_message_with_files()          │
│    ↓                                       │
│  journal.set_runtime_version("3.13.2")      │
│    ↓                                       │
│  Cleanup temp files                         │
└─────────────────────────────────────────────┘
    ↓
Existing sync flow: check package versions → download → send
```

## Error Handling

- **Network error при проверке версии:** log warning, продолжить sync пакетов без рантайма
- **Download error для одной ОС:** log error, отправить остальные ОС, отметить failed в журнале
- **Browser disconnect:** стандартный retry (3 попытки с delay)
- **Version parse error:** fallback на string comparison (lexicographic)

## Testing Strategy

- **Unit tests для RuntimeAPI:** mock HTTP responses, проверка парсинга версий для каждого рантайма
- **Unit tests для RuntimeFactory:** правильный маппинг channel_key → runtime
- **Unit tests для journal runtime methods:** запись/чтение `runtime` ключа, миграция старых журналов
- **Integration test:** mock browser, проверка что файлы скачались и переданы в `send_message_with_files`

## Migration

Существующие журналы не имеют `runtime` ключа. При первом запуске:
- `get_runtime_version()` возвращает `None`
- `should_update_runtime()` возвращает `True`
- Первый sync отправляет актуальный рантайм автоматически

Никаких breaking changes — новый ключ просто отсутствует в старых файлах.

## Open Questions

**Нет всех resolved.**
