date: 2026-06-07
topic: Upload Monitor Fix — Content Snapshot Hang
status: validated

---

## Problem Statement

**Симптом:** `_wait_for_file_message` висит бесконечно (210+ секунд и более) на content-based monitoring. Upload завершается успешно, но подтверждение в ленте MAX не обнаруживается.

**Конкретный кейс:** Файл `labuladong-fucking-algorithm.zip` (53.9 MB, разбит на 7z тома). Upload подтвердился через extended wait (~33s). `_send_message()` нажал Enter. Монитор начал сканирование с baseline=129, current=129. Хеш контента **не изменился** за 210+ секунд.

**Корневая причина:** Content snapshot hash не меняется, потому что:
1. MAX использует виртуальный скроллинг — DOM элементы перерабатываются, не добавляются
2. Новое сообщение может появиться, но последние 15 сообщений формально те же (индексы сдвигаются)
3. Либо Enter не отправил файл (файл остался в композере, отправлено пустое сообщение)

**Влияние:** Каждая загрузка висит 3-5 минут на подтверждении. Для 38 репозиториев — это 2-3 часа простоя.

## Constraints

- MAX использует **виртуальный скроллинг** — DOM count стабилен, элементы перерабатываются
- Нельзя модифицировать DOM MAX — только наблюдать и взаимодействовать через Playwright CDP
- Должна работать как для одиночных файлов, так и для multi-volume 7z splits
- Должна быть backward compatible с существующим потоком

## Approach

**Две проблемы, два исправления:**

### Проблема 1: Enter не отправляет файл

После `_wait_upload_complete()` файл загружен на сервер, но может не быть прикреплён к сообщению. `_send_message()` нажимает Enter — в MAX это может отправить пустое текстовое сообщение, а файл остаётся в композере.

**Решение:** Добавить **верификацию отправки** между `_send_message()` и `_wait_for_file_message()`. Проверить, что композер очищен и файл реально ушёл.

### Проблема 2: Content snapshot не обнаруживает изменения

Хеш SHA-256 последних 15 сообщений может не меняться при виртуальном скроллинге, потому что DOM элементы перерабатываются и индексы сдвигаются.

**Решение:** Добавить **независимые сигналы изменения** — не только хеш текста, но и `scrollTop`, timestamp последнего сообщения, count элементов с `class*="file"`.

### Проблема 3: Нет fallback'а при залипании DOM

Если DOM не обновляется после 60 секунд — монитор просто ждёт таймаут (300 сек).

**Решение:** Добавить **принудительный re-render** — скролл вниз-вверх, затем повторный snapshot. Если не помогло — `page.reload()`.

## Architecture

### Current Flow (Broken)

```
Upload → _wait_upload_complete → OK (33s)
         → _send_message() → Enter
         → _wait_for_file_message()
            → baseline=129, current=129
            → snapshot hash = 26db3f23...
            → wait... hash = 26db3f23... (same!)
            → wait... hash = 26db3f23... (same!)
            → 210s, still same → TIMEOUT
```

### Fixed Flow

```
Upload → _wait_upload_complete → OK
         → _send_message() → Enter
         → _verify_message_sent() → [NEW]
            → check composer cleared
            → if NOT cleared → try alt send (button click)
         → _wait_for_file_message()
            → snapshot hash + scrollTop + file_count
            → ANY signal changes → scan for filename
            → if 60s no change → force scroll → re-snapshot
            → if 120s no change → reload page → re-scan
```

## Components

### 1. `_verify_message_sent()` — новое

**Расположение:** `browser_max.py` после `_send_message()` в `_upload_single_file()`

**Что делает:**
- Проверяет, что композер очищен (нет текста, нет прикреплённых файлов)
- Проверяет, что сообщение счётчик вырос (хотя бы на 1)
- Если композер НЕ очищен → пытается альтернативный метод отправки (клик на кнопку "Отправить")
- Timeout: 10 секунд

**Результат:** Если после 10 сек композер всё ещё занят → возвращает `False`, вызывающий код делает retry

### 2. Усиленный `_take_content_snapshot()`

**Расположение:** `browser_max.py` модификация существующего

**Добавляет к текущему snapshot'у:**
- `scrollTop` значение чата (независимый сигнал скроллинга)
- Timestamp атрибут последнего сообщения (если есть в DOM)
- Count элементов с `class*="file"` или `class*="attach"`

**Возвращает:** Tuple `(hash, scroll_top, file_count)` вместо строки хеша. Изменение ЛЮБОГО из трёх сигналов → trigger scan.

### 3. Fallback re-render

**Расположение:** `browser_max.py` в цикле `_wait_for_file_message()`

**Логика:**
- После 60 секунд без изменений → скролл вниз (PageDown x3), пауза 2 сек, скролл вверх (Home), пауза 1 сек
- Повторный snapshot → если изменилось → scan
- После 120 секунд без изменений → `page.reload()`, ждать загрузки (10 сек), сканировать все сообщения

### 4. Улучшенное логирование

**Расположение:** `browser_max.py` в `_wait_for_file_message()`

**Добавляет периодический дамп каждые 30 секунд:**
- Текущий DOM count
- Текст последнего сообщения (первые 100 символов)
- Значение `scrollTop`
- Результат `_check_dom_upload_ready()`
- File count в композере

## Data Flow

### Upload Single File (с исправлениями)

```
1. _upload_single_file() вызывается
2. → File chooser → file selected
3. → _wait_upload_complete() → OK (33s)
4. → _send_message() → Enter
5. → _verify_message_sent() → [NEW]
   a. composer cleared? YES → proceed
   b. composer cleared? NO → try button click → retry
6. → _wait_for_file_message()
   a. snapshot (hash, scrollTop, file_count)
   b. poll каждые 2 сек
   c. ANY signal changes → scan for filename
   d. 60s no change → force scroll → re-snapshot
   e. 120s no change → reload → re-scan
   f. filename found → return True
   g. 300s timeout → fallback last 20 → return result
```

### Multi-Volume 7z Split

```
1. split_file_with_7z() → [.7z.001, .7z.002, .7z.003]
2. Для каждого тома:
   a. _upload_single_file() → steps 1-7 выше
   b. baseline_count обновляется после успеха
   c. том удаляется сразу после подтверждения
3. Все тома OK → return True
4. Любой том FAIL → return False, retry
```

## Error Handling

| Сценарий | Обработка |
|----------|-----------|
| Композер не очищен после Enter | `_verify_message_sent()` → alt send (button click) → retry |
| Snapshot hash не меняется 60с | Force scroll → re-snapshot |
| Snapshot hash не меняется 120с | `page.reload()` → re-scan |
| Connection lost | `_ensure_alive()` → reconnect → continue |
| Timeout 300с | Fallback last 20 → если нет → return False → retry |
| All retries exhausted | Return False → journal marked 'failed' |

## Testing Strategy

1. **Unit test** для `_verify_message_sent()` — mock page, проверить что композер check работает
2. **Unit test** для усиленного snapshot — проверить что tuple возвращается корректно
3. **Integration test** — симулировать виртуальный скроллинг (DOM count constant, content changes via scrollTop)
4. **Regression test** — 3 репозитория на реальном MAX, проверить что подтверждение работает за <30 сек
5. **Edge case:** Файл с похожим именем — проверить что false positive не происходит

## Open Questions

- **Оптимальный interval:** 2 секунды vs 1 секунда для polling
- **Reload safety:** `page.reload()` может потерять состояние — нужно убедиться что reconnect работает
- **Scroll trigger:** Достаточно ли PageDown x3 для re-render или нужно больше?
