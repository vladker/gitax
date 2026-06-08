---
date: 2026-06-08
topic: Upload Monitoring FALLBACK Fix — Media Archiver
status: draft
---

# Upload Monitoring FALLBACK Fix

## Problem Statement

При загрузке медиафайлов через `media_archiver.py`, **каждый второй файл** попадает в FALLBACK-ветку мониторинга:
- 30 секунд ожидания → force re-render (PageDown/Home/End)
- 45 секунд ожидания → full page reload
- Добавляет **45-75 секунд** к обработке каждого второго файла

При 1419 файлах это добавляет ~1.5-2 часа лишнего времени.

**Наблюдаемый паттерн** (чередующийся success/fallback):
```
[33] initial scan → FOUND ✓       (baseline обновлён после предыдущего reload)
[34] initial scan → EMPTY → FALLBACK 45s → reload → FOUND
[35] initial scan → FOUND ✓       (baseline чистый после reload)
[36] initial scan → EMPTY → FALLBACK 45s → reload → FOUND
```

## Root Cause Analysis

### Architecture Context

Мониторинг загрузки — трёхслойная система в `browser_max.py`:

```
Layer 1: _wait_upload_complete()   — ждёт завершения загрузки в composer
Layer 2: _send_message()           — нажимает Enter, отправляет файл
Layer 3: _wait_for_file_message()  — подтверждает появление в ленте
```

Проблема в **Layer 3** (`_wait_for_file_message`, строка 1650).

### Why The Alternating Pattern?

**MAX использует виртуальный скроллинг** — DOM-элементов всегда ~129, новые сообщения не увеличивают `querySelectorAll` count.

Последовательность для файла N:

1. `_pre_upload_msg_count = 129` (снят ДО загрузки)
2. Файл загружается, отправляется
3. `_wait_for_file_message()` запускается:
   - `base_count = 129` (DOM не изменился из-за виртуального скролла)
   - Initial scan: `[129, 129)` → **ПУСТОЙ ДИАПАЗОН**
   - fast_mode polls: `current_total` всё ещё 129 → скан пропускается
   - Monitoring loop: snapshot не меняется → 30s → re-render → 45s → reload
   - После reload: сканирует ВСЕ сообщения → находит файл ✓
   - `_pre_upload_msg_count` обновляется до 129

**Для файла N+1:**
- После reload DOM «свежий», baseline чистый
- Initial scan находит файл сразу ✓
- Но после быстрого успеха DOM может НЕ успеть обновиться
- Файл N+2 снова попадает в пустой диапазон → FALLBACK

### Three Contributing Factors

**1. Пустой диапазон initial scan**
- `baseline_count` (129) >= `base_count` (129) из-за виртуального скролла
- Диапазон `[baseline, base)` пуст → скан не выполняется
- Код на строке 1713: `if baseline_count < base_count` — условие ложно

**2. Отсутствует задержка между отправкой и сканированием**
- `_send_message()` (строка 2370) → сразу `_wait_for_file_message()` (строка 2373)
- MAX не успевает отрендерить сообщение о файле
- Для больших архивов это не критично (загрузка занимает время)
- Для фото 1-2MB загрузка почти мгновенная → race condition

**3. fast_mode не делает полный скан при провале**
- fast_mode (строка 1728-1746) проверяет только `scan_start < current_total`
- Если count не изменился — пропускает скан entirely
- При провале fast_mode падает в monitoring loop (ещё 45 секунд)
- Не делает «полный скан по имени файла» как fallback

## Constraints

- **Не ломать GitHub archiver** — работает с большими архивами (до 500MB), там загрузка занимает минуты, race condition не проявляется
- **Сохранить надёжность** — FALLBACK существует не зря, иногда MAX действительно зависает
- **Минимальные изменения** — изменить `_wait_for_file_message` и `_upload_single_file`, не трогать `_wait_upload_complete`
- **Журнал работает** — deduplication по journal корректна, не трогать

## Approach

**Основной подход:** Добавить «умный» retry-механизм в initial scan и ускорить fallback для мелких файлов.

**Выбранная стратегия:** 4 взаимосвязанных фикса, каждый решает свою часть проблемы.

---

## Fix 1: Retry Initial Scan с Задержкой

**Где:** `_wait_for_file_message()`, строка 1708-1721

**Проблема:** Когда `baseline_count >= base_count`, диапазон пуст и код сразу переходит к monitoring loop.

**Решение:** Добавить 1-2 попытки с задержкой перед переходом к monitoring:

```
Текущее поведение:
  if baseline_count < base_count:
      scan(...)
  else:
      "No new messages yet"  # → сразу в monitoring loop

Новое поведение:
  if baseline_count < base_count:
      scan(...)
  else:
      "No new messages yet, waiting for DOM update..."
      for retry in range(2):
          sleep(2)
          new_count = msg_count()
          scan_start = max(baseline_count, new_count - 15)
          if scan_start < new_count:
              found = scan(scan_start, new_count, search_name)
              if found: return found
      # Если не нашли — переходим к monitoring
```

**Почему это работает:** Даёт MAX 2-4 секунды на рендер сообщения. Снимает count заново и сканирует последние 15 сообщений (а не только `[baseline, current)`).

---

## Fix 2: Задержка Перед Мониторингом в `_upload_single_file`

**Где:** `_upload_single_file()`, строка 2369-2373

**Проблема:** `_send_message()` сразу вызывает `_wait_for_file_message()` без задержки.

**Решение:** Добавить короткую задержку (2 секунды) между отправкой и мониторингом для мелких файлов:

```
Текущее:
  self._send_message()
  found, reason, msg_idx = self._wait_for_file_message(...)

Новое:
  self._send_message()
  if file_size_bytes < 10 * 1024 * 1024:  # < 10MB
      time.sleep(2)  # дать MAX время на рендер
  found, reason, msg_idx = self._wait_for_file_message(...)
```

**Почему 2 секунды:** Фото 1-2MB загружаются практически мгновенно. 2 секунды достаточно для рендера, но не замедляют процесс значительно. Для архивов > 10MB задержка не нужна — загрузка сама по себе занимает время.

---

## Fix 3: Adaptive Fallback Timers

**Где:** `_wait_for_file_message()`, строка 1812-1852

**Проблема:** Таймеры 30с/45с одинаковы для всех файлов. Для фото 2MB это перебор.

**Решение:** Передать `file_size_bytes` в `_wait_for_file_message` и адаптировать таймеры:

```
Размер файла     Re-render    Reload
< 5 MB           8s           12s
5-50 MB         15s          20s
50-200 MB       25s          35s
> 200 MB        30s          45s  (текущие значения)
```

**Реализация:** Добавить параметр `file_size_bytes` в `_wait_for_file_message`. Вычислить `rerender_timeout` и `reload_timeout` на основе размера.

---

## Fix 4: Full Scan Fallback в fast_mode

**Где:** `_wait_for_file_message()`, строка 1728-1746

**Проблема:** fast_mode при провале падает в monitoring loop. Не пытается найти файл по имени.

**Решение:** Перед переходом к monitoring loop, сделать один полный скан по имени файла:

```
Текущее fast_mode:
  for attempt in range(5):
      if current_total > baseline:
          scan(...)
  # → падает в monitoring loop

Новое fast_mode:
  for attempt in range(5):
      if current_total > baseline:
          scan(...)
  # Full scan fallback — ищем по имени во ВСЕХ сообщениях
  total = msg_count()
  found, msg_idx, detail = self._scan_messages_for_file(0, total, search_name)
  if found:
      return (True, "found", msg_idx)
  # → теперь в monitoring loop
```

**Почему это работает:** После reload (от предыдущего файла) DOM содержит все сообщения. Скан `[0, total)` с `search_name` найдёт файл даже если baseline устарел. Это «страховка» которая стоит ~1 секунду (сканирует ~129 сообщений).

---

## Data Flow (After Fixes)

```
_upload_single_file(filepath, size=1.8MB):
  │
  ├─ click upload → select file
  ├─ _wait_upload_complete()      # ждёт загрузки
  ├─ _send_message()              # Enter, отправляет
  ├─ time.sleep(2)               # FIX 2: задержка для рендера
  │
  └─ _wait_for_file_message(baseline=129, file_size=1.8MB):
      │
      ├─ base_count = 129        # DOM ещё не обновился
      ├─ initial scan [129, 129) → ПУСТО
      │
      ├─ FIX 1: retry с задержкой
      │   ├─ sleep(2), recount → 129, scan [114, 129) → FOUND ✓
      │   └─ return (True, "found", msg_idx)
      │
      └─ Если не нашли:
          ├─ fast_mode polls (5 × 1s)
          ├─ FIX 4: full scan [0, 129) по имени
          └─ monitoring loop:
              ├─ FIX 3: rerender_timeout = 8s (для 1.8MB)
              └─ FIX 3: reload_timeout = 12s (для 1.8MB)
```

**Ожидаемый результат:** 90%+ фото найдётся на этапе «retry initial scan» (FIX 1). Остальные — на full scan (FIX 4). FALLBACK reload станет редкостью.

---

## Components Affected

| Файл | Функция | Изменение |
|------|---------|----------|
| `browser_max.py` | `_wait_for_file_message()` | FIX 1 (retry scan), FIX 3 (adaptive timers), FIX 4 (full scan) |
| `browser_max.py` | `_upload_single_file()` | FIX 2 (delay before monitoring) |
| `browser_max.py` | сигнатуры | Добавить `file_size_bytes` параметр |

**Не затрагиваются:**
- `_wait_upload_complete()` — работает корректно
- `_scan_messages_for_file()` — работает корректно
- `_take_content_snapshot()` — работает корректно
- `media_archiver.py` — не требует изменений
- `media_journal.json` — формат не меняется
- GitHub archiver flow — не затрагивается (архивы > 10MB, другая ветка логики)

---

## Error Handling Strategy

**FIX 1 (retry scan):**
- Если recount выбрасывает исключение — перехватываем, логируем warning, переходим к monitoring
- Не прерываем загрузку из-за ошибки сканирования

**FIX 2 (delay):**
- `time.sleep()` не может выбросить исключение (кроме KeyboardInterrupt, который обрабатывается глобально)

**FIX 3 (adaptive timers):**
- Если `file_size_bytes` не передан — используем defaults (30s/45s) для backwards compatibility

**FIX 4 (full scan):**
- Скан `[0, total)` может быть медленным при большом количестве сообщений
- Ограничиваем: если `total > 500`, сканируем только `[total-50, total)`

---

## Testing Strategy

**Unit tests** (`tests/test_upload_monitor.py`):
1. Тест: retry initial scan находит файл после задержки
2. Тест: adaptive timers выбирают правильные значения для разных размеров
3. Тест: full scan fallback находит файл по имени
4. Тест: delay перед мониторингом для мелкиx файлов

**Integration test** (ручной):
1. Запустить media archiver на папке с 20 фото
2. Проверить что FALLBACK случается < 2 раз (против 10 ранее)
3. Проверить что общее время сократилось

**Regression:**
1. Запустить GitHub archiver на 5 репозиториев
2. Проверить что большие архивы (>50MB) работают как раньше

---

## Open Questions

1. **Оптимальная задержка в FIX 2:** 2 секунды — достаточно? Может быть 3? Рекомендация: начать с 2, замерить, при необходимости увеличить.
2. **Глубина retry в FIX 1:** 2 попытки по 2 секунды = 4 секунды. Достаточно? Рекомендация: да, 4 секунды покрывает 95% случаев рендера.
3. **Порог для adaptive timers:** 5MB / 50MB / 200MB —合理? Можно обсудить после первых тестов.
