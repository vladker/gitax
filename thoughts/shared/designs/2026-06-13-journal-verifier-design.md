date: 2026-06-13
topic: Journal-Channel Verifier
status: validated

---

## Problem Statement

Нет универсального механизма сравнения журнала с реальным содержимым MAX-канала. Существующая `audit_channel_completeness()` заточена под GitHub-репозитории. Для PyPI, Backuper и Media аналогичных проверок нет. Нужно единое решение для всех 5 журналов.

## Constraints

- **Скорость** — quick-режим должен завершаться за 30-60 секунд на типичном канале
- **Точность** — thorough-режим использует три источника данных (API + page state + DOM)
- **Единый подход** — одинаковый интерфейс для всех публикаторов
- **Без изменений в journal.py** — адаптеры обёртывают существующие журналы
- **CDP-подключение** — требует работающего браузера на порту 9222

## Approach

**`JournalChannelVerifier`** с протоколами адаптеров для каждого публикатора.

Выбран потому что:
- Выносит общую логику сравнения в одно место (DRY)
- Адаптеры маленькие (~50 строк) и изолированные
- Легко добавить новый публикатор без изменений ядра
- Два режима скорости из коробки

## Architecture

```
verifier/
├── __init__.py
├── models.py          # ChannelFile, DiffResult, VerifierMode
├── adapters.py        # ChannelAdapter + JournalAdapter протоколы
├── core.py            # JournalChannelVerifier
├── adapters_github.py # GitHubJournalAdapter
├── adapters_pypi.py   # PyPIJournalAdapter
├── adapters_backuper.py # BackuperJournalAdapter
└── adapters_media.py  # MediaJournalAdapter
```

### Компоненты

**ChannelFile** — унифицированная модель файла в канале:
- `filename: str`
- `message_text: str`
- `timestamp: str`
- `size: str | None`

**DiffResult** — результат сравнения:
- `in_journal_not_in_channel: List[str]` — ключи пропущенных записей
- `in_channel_not_in_journal: List[str]` — орфаны в канале
- `version_mismatches: List[dict]` — расхождения версий
- `stats: dict` — сводка
- `incomplete_scan: bool` — флаг частичного скана

**ChannelAdapter (протокол):**
- `scan_files(mode: VerifierMode) -> List[ChannelFile]`
- Использует `scan_channel_for_files()` для quick, `audit_channel_completeness()` для thorough

**JournalAdapter (протокол):**
- `get_entries() -> List[dict]`
- `expected_filename(entry: dict) -> str | List[str]`
- `entry_key(entry: dict) -> str`
- `channel_to_key(filename: str) -> str | None`

**JournalChannelVerifier (ядро):**
- Принимает пару адаптеров
- `verify() -> DiffResult` — выполняет сравнение
- `fix_journal(diff: DiffResult)` — удаляет пропущенные записи из журнала
- `report(diff: DiffResult)` -> str — текстовый отчёт

### Data Flow

```
verify()
  → ChannelAdapter.scan_files(mode)
    → browser.scan_channel_for_files()  (quick)
    → browser.audit_channel_completeness()  (thorough)
    → normalize to [ChannelFile, ...]
  → JournalAdapter.get_entries()
  → Множественное сравнение:
    journal_keys = {adapter.entry_key(e)}
    channel_keys = {adapter.channel_to_key(f.filename)}
    diff = journal_keys - channel_keys
    orphans = channel_keys - journal_keys
  → DiffResult
```

## Error Handling

- **CDP недоступен** → `VerifierError` с suggestion перезапустить браузер
- **DOM таймаут** → `incomplete_scan=True`, вернуть частичный результат
- **Повреждённый журнал** → BaseJournal уже обрабатывает (.bak recovery)
- **Адаптер не найден** → `VerifierError` с именем публикатора

## Testing Strategy

1. Юнит-тесты протоколов (моки браузера)
2. Юнит-тесты сравнения (различные diff-сценарии)
3. Юнит-тесты каждого адаптера (маппинг имён)
4. Интеграционный тест с реалистичными данными

## Open Questions

- **Нужен ли режим "только отчёт" без исправления журнала?** → Да, по умолчанию только отчёт, исправление — отдельная опция
