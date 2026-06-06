date: 2026-06-06
topic: Upload Monitor Scanning Fix
status: validated

---

## Problem Statement

The upload confirmation monitor (`_wait_for_file_message`) consistently times out at 300s per file, even though files ARE successfully uploaded. The fallback "last 20 msgs" check finds the files, proving they exist in the feed — but the live monitoring never detects them.

**Impact:** Each file upload takes ~5 minutes of unnecessary waiting. For 89 repos, that's ~7 hours of wasted time.

## Constraints

- MAX uses **virtual scrolling** — DOM elements are recycled, not added. Message count stays constant even as new messages appear.
- Cannot modify MAX's DOM — we can only observe and interact via Playwright CDP.
- Must maintain backward compatibility with the existing upload flow.
- Must handle both single files and multi-volume 7z splits.

## Approach

**Lead with content-based scanning instead of count-based monitoring.** Since virtual scrolling keeps DOM count stable, we need to detect new messages by their CONTENT, not by element count changes.

**I considered** adding a MutationObserver for node additions — but virtual scrolling recycles nodes, so additions don't fire. **I considered** polling the entire message list every N seconds — but that's expensive and slow. **I chose** a hybrid: periodic content snapshots of the last N messages, comparing text hashes to detect changes.

## Architecture

### Current Flow (Broken)

```
Upload → Wait for count increase → Scan new indices → Timeout → Fallback scan → Found
         ^^^^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^
         NEVER HAPPENS              EMPTY RANGE
```

**Проблема:** Счётчик не меняется (виртуальный скроллинг). Диапазон сканирования пуст.

### Fixed Flow

```
Upload → Periodic content snapshot → Hash comparison → New content detected → Filename match → Found
```

**Решение:** Периодически снимает "снимок" последних N сообщений, сравнивает хеши текста, при изменении — проверяет совпадение по filename.

### Key Changes

1. **`_wait_for_file_message`** — полностью переработан мониторинг:
   - Убрать зависимость от `current_count > baseline_count`
   - Добавить периодические снимки последних N сообщений
   - Сравнение по хешу текста вместо счётчика элементов
   - При обнаружении нового контента — проверяем filename match

2. **Начальный скан** — исправлен диапазон:
   - Было: `range(base_count)` с `skip if < baseline` → пустой диапазон
   - Стало: `range(baseline_count, base_count)` → правильный диапазон

3. **Filename matching** — усилена проверка:
   - Regex `r'([a-z0-9\-_.]+\.zip(?:\.7z\.\d+)?)'` работает для `.zip` и `.zip.7z.001`
   - Но не работает для сообщений, где filename отображается в ином формате
   - Добавляем fallback: проверяем `search_name` как substring в `textContent`

## Components

### Content Snapshot Mechanism

```
┌─────────────────────────┐
│  Last N messages        │
│  (N = 15, configurable) │
└────────┬────────────────┘
         │ page.evaluate()
         ▼
┌─────────────────────────┐
│  Text hash per message  │
│  (first 100 chars)      │
└────────┬────────────────┘
         │ compare with previous snapshot
         ▼
┌─────────────────────────┐
│  Changed?               │
│  YES → Check filename   │
│  NO  → Wait & retry     │
└─────────────────────────┘
```

**Параметры:**
- `snapshot_interval`: 2 секунды (быстрее чем текущие 30с)
- `snapshot_depth`: последние 15 сообщений (хватает для виртуального скролла)
- `hash_window`: первые 100 символов textContent (достаточно для сравнения)

### Filename Matching Strategy

**Primary:** Regex extraction + substring match (существующий, усиленный).

**Secondary:** Direct substring search in textContent. Если regex не сработал, ищем `search_name` прямо в тексте сообщения.

**Tertiary:** Проверка на наличие `.zip` / `.7z` + "download" / "скачать" без filename (для fallback).

## Data Flow

1. **Upload initiated** → `_upload_single_file` вызывается с `baseline_count`
2. **Initial snapshot** → Снимаем хеши последних `snapshot_depth` сообщений
3. **Upload proceeds** → MAX загружает файл в фоне
4. **Polling loop** → каждые 2 секунды:
   - Снимаем новый snapshot
   - Сравниваем хеши с предыдущим
   - Если есть изменения → проверяем filename match
   - Если match → возвращаем `True`
5. **Timeout fallback** → если 300с прошло без успеха → сканируем последние 20 сообщений

## Error Handling

- **Connection lost:** `_ensure_alive()` проверяется каждый цикл. При потере — reconnect и продолжаем.
- **Page navigation:** Если страница перезагружена, snapshot сбрасывается и начинается заново.
- **Timeout:** Fallback на последние 20 сообщений (существующий механизм, работает).
- **No match after timeout:** Возвращаем `False`, вызывающий код решает retry или skip.

## Testing Strategy

1. **Unit test** для content snapshot — проверяем что хеширование работает корректно.
2. **Integration test** — симулируем виртуальный скроллинг (DOM count constant, content changes).
3. **Regression test** — existing behavior с реальным MAX (ручной тест на 3 репозиториях).
4. **Edge case:** Файл с похожим именем (проверяем что false positive не происходит).

## Open Questions

- **Оптимальный snapshot_interval:** 2 секунды vs 1 секунда. Меньше → быстрее обнаружение, больше → меньше нагрузка на браузер.
- **Кэширование snapshot:** Стоит ли кэшировать DOM элементы между снимками или каждый раз делать `querySelectorAll`?
