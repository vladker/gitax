---
date: 2026-06-11
topic: "Multi-Channel Support with Parallel Uploads"
status: validated
---

## Problem Statement

GitHub Archiver поддерживал только один канал MAX на функцию (GitHub, PyPI, Media, Backup). Пользователь хотел:
1. **Добавлять новые каналы** прямо из CLI, без редактирования config.yaml
2. **Выбирать канал**, если для функции настроено несколько
3. **Параллельная загрузка** — отправлять один и тот же файл во все каналы одновременно, переиспользуя temp файлы

## Constraints

- **Backward compatibility**: существующие пользователи с `channels.max` в config.yaml или `CHANNEL_MAX` в .env не должны ничего менять
- **Single source of truth**: реестр каналов в `channel_registry` секции config.yaml
- **No code duplication**: все архиверы (GitHub, PyPI, Media, Backuper) должны использовать общую инфраструктуру
- **Temp file reuse**: при параллельной загрузке файл скачивается один раз, загружается в N каналов

## Approach

**Реестр каналов** — Pydantic модель `ChannelRegistry` с маппингом функции → список `ChannelEntry`. Каждый канал имеет `enabled` флаг для включения/отключения.

**Миграция при загрузке**: legacy каналы (`channels.max`) автоматически копируются в `channel_registry.github` при запуске. Legacy каналы **не очищаются** для обратной совместимости.

**Интерактивный выбор**: `select_channel(function, allow_add=False)` показывает список активных каналов и позволяет выбрать один. Если `allow_add=True`, можно добавить новый канал прямо из меню.

**Параллельная загрузка**: новый режим в каждом архивере — скачать файл один раз, загрузить во все каналы функции, затем удалить temp файл.

## Architecture

### Channel Registry (config.py)

```
ChannelEntry
├── url: str
├── label: str (auto-generated from function name + index)
└── enabled: bool = True

ChannelRegistry
├── github: List[ChannelEntry]  # auto-mapped from channels.max
├── pypi: List[ChannelEntry]
├── media: List[ChannelEntry]
└── backup: List[ChannelEntry]
```

### Channel Registry UI (channel_registry_ui.py)

```
channel_registry_menu()
├── Показать все каналы по функциям
├── Добавить канал (выбор функции → URL → label)
├── Удалить канал (по индексу)
├── Toggle enabled/disabled
└── Выход

select_channel(function, allow_add=False)
├── 0 каналов + allow_add → _prompt_add_channel
├── 1 канал → return немедленно (no prompt)
├── 2+ каналов → интерактивный выбор
└── allow_add=True → опция "+ Добавить новый"
```

### Config Utils (config_utils.py)

```
get_channels_for_function(function: str) -> List[ChannelEntry]
└── Returns all enabled channels for a function

get_channel_for_function(function: str) -> ChannelEntry | None
└── Returns first enabled channel or None (for backward compat)
```

### Parallel Upload Flow

```
For each repo/file:
1. Download to temp location (once)
2. For each enabled channel:
   a. Connect browser to channel URL
   b. Upload file via browser automation
   c. Wait for upload confirmation
   d. Disconnect
3. Delete temp file(s)
4. Mark as processed in journal
```

## Components

### 1. ChannelRegistry (Pydantic Model)

Хранит список каналов для каждой функции. Поддерживает:
- Добавление новых каналов
- Удаление каналов
- Toggle enabled/disabled
- Auto-label генерацию ("GitHub #1", "PyPI #2")

### 2. ChannelRegistryUI

CLI интерфейс для управления каналами:
- Просмотр всех каналов с цветовой индикацией enabled/disabled
- Добавление нового канала с выбором функции
- Удаление канала по индексу
- Toggle enabled/disabled по индексу

### 3. Channel Selector

Интерактивный выбор канала при запуске архивера:
- 1 канал → прозрачный проход, без вопросов
- 2+ канала → нумерованный список, пользователь выбирает
- allow_add=True → опция добавления нового канала inline

### 4. Parallel Upload Mode

Новый режим в каждом архивере:
- Скачивает файл/репозиторий один раз
- Перебирает все активные каналы функции
- Для каждого канала: подключается, загружает, подтверждает
- Удаляет temp файлы после всех загрузок

## Data Flow

### Adding a Channel

```
User → "Управление каналами" → "Добавить канал"
  → Выбор функции (github/pypi/media/backup)
  → Ввод URL (валидация: начинается с https://)
  → Ввод label (опционально, auto-generate если пустой)
  → ChannelEntry created → added to registry
  → config.yaml saved
```

### Selecting Channel (Single Upload)

```
User → запускает архивер
  → get_channels_for_function("github")
  → 1 канал → return немедленно
  → 2+ каналов → select_channel() → пользователь выбирает
  → Архивер использует выбранный канал
```

### Parallel Upload

```
User → запускает архивер → chooses "P" (parallel)
  → get_channels_for_function("github") → [ch1, ch2, ch3]
  → For each repo:
    a. Download repo.zip to temp/
    b. For ch1: connect → upload → confirm → disconnect
    c. For ch2: connect → upload → confirm → disconnect
    d. For ch3: connect → upload → confirm → disconnect
    e. Delete temp/repo.zip
    f. Journal.mark_processed(repo_name)
```

## Error Handling

### Channel URL Validation

- URL должен начинаться с `https://`
- Пустой URL → ошибка, канал не добавлен
- Неверный формат → повторяет запрос

### Parallel Upload Failures

- Если один канал упал → продолжает с остальными
- Счётчик ошибок (`error_count`) отдельно от успешных
- После всех каналов: показывает статистику (успех/ошибки)
- Репозиторий помечается как processed только если хотя бы одна загрузка успешна

### No Channels Configured

- При запуске архивера без каналов → предлагает добавить inline
- При параллельной загрузке с 1 каналом → предлагает добавить ещё один

## Testing Strategy

### Unit Tests

- `test_channel_selector.py`: 7 тестов
  - Single channel no prompt
  - Multiple channels shows options
  - No channels returns empty
  - Disabled channels excluded
  - allow_add with existing channels
  - allow_add with no channels
  - allow_add default label

- `test_channel_registry_ui.py`: 6 тестов
  - Add channel
  - Remove channel
  - Toggle channel
  - Select channel (single, multiple, none enabled)

### Integration Tests

- `test_channel_migration.py`: миграция legacy → registry
- `test_channel_downloader.py`: config resolution
- `test_pypi_libs_archiver.py`: PyPI-specific channel handling
- `test_config_loader.py`: env var → config mapping

## Open Questions

1. **Browser pooling**: при параллельной загрузке браузер переподключается к каждому каналу. Можно ли держать одно соединение и просто менять URL? → TODO
2. **Async parallelism**: сейчас каналы обрабатываются последовательно. Можно ли загружать в несколько каналов одновременно? → TODO (требует async browser automation)
3. **Channel groups**: пользователь может хотеть группы каналов ("основные", "архивные") → future feature
