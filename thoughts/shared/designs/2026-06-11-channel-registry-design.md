# Channel Registry — Multi-Channel Management & Parallel Upload

**Date:** 2026-06-11
**Status:** validated

---

## Problem Statement

Сейчас каждая функция (GitHub, PyPI, Media, Backup) жёстко привязана к одному каналу через `channels.{key} = url`. Пользователь не может:

- Создать несколько каналов для одной функции
- Выбрать канал перед загрузкой
- Загрузить один и тот же контент в несколько каналов параллельно
- Переиспользовать temp файлы при параллельной выкладке

Нужна гибкая система управления каналами с поддержкой параллельных загрузок.

---

## Constraints

- **Backward compatibility:** старые `.env` и `config.yaml` без миграции должны продолжать работать
- **BrowserMAX sync:** текущий BrowserMAX синхронный — не рефакторим в async
- **Single Chrome instance:** все каналы работают через один Chrome (CDP port 9222)
- **Journal integrity:** параллельные записи в journal не должны corrupt данные
- **Temp file safety:** файлы удаляются только когда ВСЕ успешные каналы подтвердили

---

## Approach

**Реестр каналов** (`ChannelRegistry`) заменяет плоскую карту `function → url` на `function → [ChannelEntry]`.

Каждый `ChannelEntry` содержит URL, label и enabled-флаг. Миграция старого формата происходит автоматически при первом запуске.

Для параллельных загрузок — **ParallelGroupUploader** на `threading.Thread` (I/O-bound, GIL не проблема). Файлы скачиваются один раз, загружаются в N каналов параллельно, удаляются после подтверждения хотя бы одного канала.

---

## Architecture

### Channel Registry Model

```
ChannelEntry {
  url: str              # URL MAX-канала
  label: str            # человекочитаемое имя ("GitHub Main", "PyPI Backup")
  enabled: bool         # можно отключить без удаления
}

ChannelRegistry {
  github: [ChannelEntry, ...]
  pypi:   [ChannelEntry, ...]
  media:  [ChannelEntry, ...]
  backup: [ChannelEntry, ...]
}
```

**Storage:** `config.yaml`, секция `channel_registry`. Loader добавляет новый Pydantic model.

### Component Changes

| Component | Change |
|-----------|--------|
| `config/model.py` | Новый `ChannelEntry` + `ChannelRegistry`. `ChannelsConfig` остаётся для backward compat |
| `config/loader.py` | Auto-migrate: если `channels.max` заполнен и `channel_registry` пуст → копируем |
| `config_utils.py` | `get_channel_url()` читает registry[0].url. Новый `get_channels_for_function()` |
| `github_archiver.py` | Новый пункт меню "Управление каналами". Channel selection перед запуском архивера |
| `parallel_uploader.py` | Новая: `ParallelGroupUploader` с download → upload → cleanup фазами |
| `journal.py` + siblings | Optional `channel_label` field в entries. `threading.Lock` для параллельных записей |

---

## Data Flow

### Single channel (unchanged behavior)

```
User → menu → function → 1 канал → обычный upload flow → journal → cleanup
```

**Ноль изменений в UX** когда 1 канал — никаких лишних вопросов.

### Multi-channel selection

```
User → menu → function
    ↓
2+ каналов? → Да
    ↓
Channel selector:
 [1] GitHub Main
 [2] GitHub Archive
 [0] Все каналы (параллельно)
    ↓
User picks → upload flow
```

### Parallel upload (new)

```
User picks "All channels" (или 2+ канала)
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
    ↓
Summary: per-channel results + overall stats
```

---

## Components

### 1. ChannelRegistry (config/model.py)

Pydantic model, добавляется в `AppConfig`. Поддерживает сериализацию в YAML и обратно.

### 2. ChannelMigration (config/loader.py)

При загрузке config:
- Проверяет, заполнен ли старый `channels.{key}`
- Если да и `channel_registry` пуст → создаёт `ChannelEntry` с label="default"
- Логирует warning один раз

### 3. ChannelManager CLI (github_archiver.py)

Service-меню, пункт 6. CRUD операции над каналами:
- **Add:** выбираем функцию → вводим URL → label (авто-генерация если пусто)
- **List:** таблица функция | label | URL | enabled
- **Toggle:** вкл/выкл канал
- **Delete:** удалить канал (с подтверждением)

### 4. ChannelSelector (github_archiver.py)

Перед запуском любого архивера:
- Считаем enabled каналы для функции
- **0 каналов** → error, предложит добавить
- **1 канал** → прозрачный pass-through
- **2+ канала** → показываем список + опция "Все"

### 5. ParallelGroupUploader (parallel_uploader.py)

Новый модуль. Три фазы:

**Download phase:**
- Делегирует текущую логику скачивания (GitHubAPI, PyPIAPI и т.д.)
- Файлы в `temp/` — один экземпляр на repo/package

**Upload phase:**
- `threading.Thread` на каждый канал
- Каждый thread создаёт свой `BrowserMAX(channel_url)`
- Подключается к тому же Chrome через CDP
- Upload-ит те же файлы с диска
- Результаты в thread-safe dict: `{channel_label: {success: bool, errors: []}}`

**Cleanup phase:**
- Файл удаляется если ≥1 канал подтвердил
- Failed файлы остаются для ручного retry

**Thread safety:**
- `threading.Lock` вокруг journal write операций
- `parallel_delay` (configurable, default 2s) stagger между стартом потоков

### 6. Journal Extensions

- Optional `channel_label` field во всех journal-ах
- Когда параллельный upload → одна запись на канал
- Backward compat: поле не required, старые записи без него валидны

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| 1 канал упал, 2 прошёл | Файл = "sent", temp удалён, journal: ch1=failed, ch2=ok |
| ВСЕ каналы упали | Файл = "failed", temp сохранён, retry доступен |
| Thread exception | Catch в thread wrapper, log error, channel marked failed |
| Browser disconnect | Существующий retry logic в BrowserMAX, 3 попытки per-channel |
| Journal lock timeout | Retry write с backoff (max 5s) |

---

## Testing Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| ChannelRegistry CRUD | Unit | Add, list, toggle, delete entries |
| Migration v1→v2 | Unit | Old channels.* → new registry |
| ChannelSelector 1 channel | Unit | No prompt when single channel |
| ChannelSelector 2+ channels | Unit | Prompt appears, correct options |
| ParallelGroupUploader mock | Integration | 3 threads, shared temp, cleanup logic |
| Journal thread safety | Integration | Concurrent writes don't corrupt |
| Parallel delay stagger | Unit | Threads start with configured delay |

---

## Config Migration Path

```
Before:
  channels:
    max: "https://web.max.ru/abc"
    pypi: "https://web.max.ru/def"

After (auto-migrated):
  channel_registry:
    github:
      - url: "https://web.max.ru/abc"
        label: "GitHub Main"
        enabled: true
    pypi:
      - url: "https://web.max.ru/def"
        label: "PyPI Main"
        enabled: true
```

---

## Open Questions

1. **Parallel delay default:** 2 секунды между потоками достаточно для MAX rate limit? Можно сделать configurable через `parallel_upload.stagger_delay_sec`.
2. **Max concurrent channels:** Ограничить до 3-5 параллельных потоков? Предложение: hard limit 5, configurable.
3. **Channel-specific split settings:** Каждый канал может иметь свой split_threshold? Предложение: нет, split — свойство файла, а не канала.
