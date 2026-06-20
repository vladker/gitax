---
date: 2026-06-20
topic: Bidirectional Journal-Channel Verification
status: validated
---

# Двусторонняя сверка журнала и канала

## Problem Statement

Журнал (`journal.json`) и MAX-канал могут рассинхронизироваться в **оба** направления:

1. **Журнал → Канал**: Запись в журнале (`status=sent`), но файла в канале нет (кэш MAX истёк, канал очищен и т.д.)
2. **Канал → Журнал**: Файл в канале есть, но в журнале нет записи (крах до сохранения, ручной upload, миграция данных)

Текущий верификатор обрабатывает только случай 1 (удаляет из журнала записи, отсутствующие в канале). Нужно добавить обработку случая 2 — обнаружение и восстановление записей, которые есть в канале, но потеряны в журнале.

## Constraints

- Не менять текущее поведение удаления `journal_only` записей
- Использовать существующую инфраструктуру: `verifier/`, `group_messages_by_repo()`, `parse_message()`
- Поддержка всех 4 типов журналов: GitHub, PyPI, Backuper, Media
- Безопасность: orphan-файлы без текстового контекста не добавлять автоматически
- Атомарность: каждая операция fix должна быть откатываемой через существующий `rollback_journal.py`

## Approach

**Двустороннее сравнение** через множества:
- `J` = множество репос в журнале (ключ = `full_name`)
- `C` = множество репос в канале (из `group_messages_by_repo`)

Три зоны расхождений:

| Зона | Описание | Действие |
|------|----------|----------|
| `J - C` | В журнале есть, в канале нет | **Удалить** из журнала (текущее поведение) |
| `C - J` | В канале есть, в журнале нет | **Добавить** в журнал как `restored` |
| `J ∩ C` (версия не совпадает) | Оба есть, версия разная | **Обновить** версию в журнале |

## Architecture

Изменения в существующем модуле `verifier/`:

### Новые модели (`verifier/models.py`)

**`ChannelRepo`**: Структурированные данные репо из канала:
- `full_name: str` — owner/repo
- `version: str` — извлечено из текста сообщения
- `display_name: str`
- `has_file: bool` — есть ли файл (zip/7z)
- `files_complete: bool` — все ли объёмы на месте

**Расширение `JournalDiff`**:
- Новое поле `channel_only: list[ChannelRepo]` — репос только в канале
- Новое поле `version_mismatches: list[VersionMismatch]` — разные версии

**`VersionMismatch`**:
- `full_name: str`
- `journal_version: str`
- `channel_version: str`

### Логика сверки (`verifier/main.py`)

**В `verify()`**: добавить третье сравнение:
```
C_only = C - J  →  diff.channel_only
J_and_C = J ∩ C → сравнение версий → diff.version_mismatches
```

**Новый метод `fix_journal_bidirectional(diff)`**:
- **Удалить** `journal_only` из журнала
- **Добавить** `channel_only` в журнал со статусом `restored`
- **Обновить** `version_mismatches` — поставить версию из канала

### Протокол адаптеров (`verifier/adapters.py`)

**Расширение `ChannelAdapter`**:
- `get_channel_repos() -> list[ChannelRepo]` — структурированные данные о всех репос в канале

**Расширение `JournalAdapter`**:
- `add_entry(full_name, version, display_name) -> bool` — добавить запись
- `update_version(full_name, new_version) -> bool` — обновить версию

### Реализация для всех типов

Каждый адаптер (GitHub, PyPI, Backuper, Media) получает реализации новых методов.

## Data Flow

```
[Канал] → ChannelAdapter.get_channel_repos() → list[ChannelRepo]
[Журнал] → JournalAdapter.get_entries() → list[dict]

[Сверка] → J = {entry_key}
          → C = {full_name}
          → J - C → diff.journal_only
          → C - J → diff.channel_only
          → J ∩ C → сравнение версий → diff.version_mismatches

[Fix]    → journal_only:   remove_entry()
          → channel_only:  add_entry(status="restored")
          → mismatches:    update_version()
```

## Error Handling

- **Канал недоступен**: Верификация не запускается, явная ошибка
- **Журнал повреждён**: `BaseJournal._load()` восстанавливает из бэкапа
- **Race condition при add_entry**: Логировать предупреждение, продолжать
- **Версия "unknown" в канале**: Не обновлять журнал, пропустить с предупреждением
- **Orphan файлы без текста**: Не добавлять автоматически — слишком рискованно

## Testing Strategy

1. **Юнит-тест на `JournalDiff`**: `channel_only` и `version_mismatches` правильно заполняются
2. **Юнит-тест на `fix_journal_bidirectional`**: Mock-адаптеры, проверить вызовы `add_entry` и `update_version`
3. **Интеграционный тест**: Реальный журнал + mock-канал с известными репос

## Design Decisions

1. **Статус `restored` для `channel_only`**: Отличить восстановленные записи от нормально отправленных. Пользователь видит статистику "сколько восстановлено"
2. **Обновлять только версию**: Stars/forks могут устареть на GitHub. В канале они "на момент публикации". Обновлять всё некорректно
3. **Orphan файлы игнорировать**: Без текстового контекста невозможно надёжно определить `full_name` и `version`
