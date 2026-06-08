date: 2026-06-08
topic: Fast Upload Confirmation — Delta Snapshot
status: draft

---

## Problem Statement

Каждый файл confirmation занимает **18 секунд** из-за многоступенчатого мониторинга, где:
- 4 секунды — retry scan (сканирует пустой диапазон)
- 5 секунд — fast mode polls (count не меняется из-за виртуального скролла)
- 5-9 секунд — full scan по 129 сообщениям с filename match (который не работает)

**Тертиарный fallback (file+download heuristic) работает на 100%** — но до него доходят только после 9 секунд бесполезных фаз.

**Корневая причина:** Все фазы основаны на `message_count > baseline`. Виртуальный скролл держит count постоянным → все фазы проваливаются → падают в full scan.

## Constraints

- **Виртуальный скролл MAX** — DOM count стабилен (~129), элементы перерабатываются
- **Filename не в textContent** — MAX рендерит имя файла в атрибутах/innerHTML, не в raw text
- **Не ломать GitHub archiver** — большие архивы (>10MB) используют другую ветку (без fast_mode)
- **Надёжность** — confirmation должен быть гарантированным, не «надеюсь что сработало»
- **Файлы отправляются по одному** — sequential, не параллельно

## Approach

**Дельта-снэпшот:** Снять hash контента ДО отправки → нажать Enter → проверить, что hash изменился.

**Почему это работает:**
- `_wait_upload_complete()` уже подтвердил, что файл на сервере MAX
- Мы нажали Enter — файл отправлен
- Если контент ленты изменился → новое сообщение появилось → это наше файл-сообщение
- Имя файла проверять НЕ нужно — мы отправляем последовательно, между отправками нет других источников

**Почему это быстро:**
- 1× `page.evaluate()` для snapshot ДО (уже есть в `_capture_pre_upload_state`)
- sleep(0.5-1.5s) для рендера
- 1× `page.evaluate()` для snapshot ПОСЛЕ
- Сравнение hash — мгновенно в Python
- Если hash изменился → ✓ готово (1-2 секунды)

## Architecture

### Current Flow (Slow)

```
_upload_single_file():
  _wait_upload_complete()     → OK (0s for photo)
  _send_message()             → Enter
  sleep(2)                    → 2s
  _wait_for_file_message():
    initial scan              → empty range (baseline==base)
    retry 2×sleep(2)+scan     → 4s, тот же диапазон
    fast polls 5×sleep(1)     → 5s, count не меняется
    full scan [0, 129)        → 5-9s, 129 × page.evaluate()
    monitoring loop           → если full scan не нашёл
      snapshot poll           → 2s × N
      rerender at 8s          → 3s
      reload at 12s           → 8s
```

### New Flow (Fast)

```
_upload_single_file():
  pre_snapshot = _take_content_snapshot()   # ← НОВОЕ: до отправки
  _wait_upload_complete()                   → OK (0s for photo)
  _send_message()                           → Enter
  _confirm_file_sent(pre_snapshot)           # ← НОВОЕ: дельта-проверка
    sleep(1)                                # дать MAX рендернуть
    post_snapshot = _take_content_snapshot()
    if pre_snapshot.hash != post_snapshot.hash:
      return True                           # ← 1 секунда!
    for retry in range(2):
      sleep(1)
      post_snapshot = _take_content_snapshot()
      if pre_snapshot.hash != post_snapshot.hash:
        return True                         # ← 2-3 секунды
    return False                            # → fallback
  if confirmed:
    return True                             # ← ГОТОВО, skip _wait_for_file_message
  _wait_for_file_message()                  # ← существующий fallback для edge cases
```

### Component: `_confirm_file_sent(pre_snapshot)`

**Новый метод в `BrowserMAX`.** Лёгкая проверка «контент изменился?»

```
_confirm_file_sent(pre_snapshot, file_size_bytes):
  # Адаптивная задержка: фото рендерятся быстрее чем видео
  if file_size_bytes < 5MB:
    initial_wait = 0.5s
  elif file_size_bytes < 50MB:
    initial_wait = 1.0s
  else:
    initial_wait = 2.0s

  time.sleep(initial_wait)

  # Проверка дельты
  post = _take_content_snapshot()
  if post and post.hash != pre_snapshot.hash:
    return True

  # Retry с нарастающей задержкой
  for attempt in range(2):
    time.sleep(1.0)
    post = _take_content_snapshot()
    if post and post.hash != pre_snapshot.hash:
      return True

  return False  # → вызывающий код использует _wait_for_file_message как fallback
```

**Почему adaptive wait:** Фото 3MB рендерятся практически мгновенно. Видео 100MB могут занять больше времени на генерацию превью. 0.5s для фото, 2.0s для видео.

### Component: Модификация `_upload_single_file()`

**Изменение:** Добавить snapshot до отправки + вызвать `_confirm_file_sent()` после.

```
_upload_single_file(filepath, filename, file_size_bytes, ...):
  for attempt in range(retries):
    _ensure_alive()
    _try_navigate()

    # ← НОВОЕ: snapshot до начала загрузки
    pre_snapshot = self._take_content_snapshot()

    # File selection + upload (существующий код)
    file_chooser → set_files()
    _wait_upload_complete()
    _send_message()

    # ← НОВОЕ: быстрая подтверждение
    confirmed = self._confirm_file_sent(pre_snapshot, file_size_bytes)
    if confirmed:
      print(f"  [OK] File confirmed (delta check, {elapsed}s)")
      return True

    # ← СУЩЕСТВУЮЩИЙ: fallback для edge cases
    _wait_for_file_message(...)
```

**Убирается:** `time.sleep(2)` для файлов <10MB — больше не нужен, `_confirm_file_sent` делает свою задержку.

### Component: Оптимизация `_wait_for_file_message()`

**Изменение:** Убрать бесполезные фазы при виртуальном скролле.

**Текущая проблема:** Retry scan и fast mode polls проверяют `if baseline_count < base_count`. При виртуальном скролле `baseline == base` ВСЕГДА → эти фазы тратят 9 секунд на пустые проверки.

**Решение:** Обнаружить виртуальный скролл и пропустить бесполезные фазы.

```
_wait_for_file_message(...):
  base_count = msg_count()

  # ← НОВОЕ: Обнаружение виртуального скролла
  is_virtual_scroll = (base_count <= 150)  # стабильный count = виртуальный скролл

  if is_virtual_scroll:
    # Пропускаем retry scan и fast mode — они бесполезны
    # Сразу переходим к content-based monitoring
    goto monitoring_loop
  else:
    # Существующая логика для обычного скролла
    initial_scan → retry → fast_mode → full_scan → monitoring_loop
```

**Почему threshold 150:** MAX виртуальный скролл держит ~129 элементов. 150 — безопасный порог. Если count > 150, это не виртуальный скролл (много сообщений без виртуализации).

### Мониторинг Loop (улучшенный)

Когда дельта-проверка не сработала (редкий edge case), monitoring loop становится последним resort. Улучшаем:

**Adaptive timeout для фото:**
- Текущий: rerender 8s, reload 12s для <5MB
- Новый: rerender 3s, reload 6s для <5MB
- Обоснование: если дельта-проверка не нашла изменение за 3 секунды, проблема не в «MAX медленно рендерит», а в чём-то другом (сбой, disconnect)

## Data Flow

### Best Case (фото 3MB, 95% случаев)

```
t=0.0s  pre_snapshot captured (hash=A)
t=0.0s  upload starts
t=0.1s  upload complete
t=0.1s  Enter pressed
t=0.1s  sleep(0.5s) — wait for render
t=0.6s  post_snapshot (hash=B)
t=0.6s  A != B → ✓ CONFIRMED
        ─────────────────────
        ИТОГО: 0.6 секунды!
```

### Typical Case (нужен 1 retry)

```
t=0.0s  pre_snapshot (hash=A)
t=0.1s  upload + send
t=0.1s  sleep(0.5s)
t=0.6s  post_snapshot (hash=A) — ещё не обновили
t=0.6s  sleep(1.0s) — retry 1
t=1.6s  post_snapshot (hash=B)
t=1.6s  A != B → ✓ CONFIRMED
        ─────────────────────
        ИТОГО: 1.6 секунды!
```

### Worst Case (падает в monitoring)

```
t=0.0s  pre_snapshot
t=0.1s  upload + send
t=0.1s  delta check: 3 retries × 1s = 3s → не нашёл
t=3.1s  _wait_for_file_message() → monitoring loop
t=3.1s  snapshot poll → change detected → scan → found
        ─────────────────────
        ИТОГО: ~5-7 секунд (против 18s сейчас)
```

## Components Affected

| Файл | Изменение | Тип |
|------|-----------|-----|
| `browser_max.py` | Новый метод `_confirm_file_sent()` | Добавление |
| `browser_max.py` | `_upload_single_file()` — добавить pre-snapshot + delta check | Изменение |
| `browser_max.py` | `_wait_for_file_message()` — убрать useless фазы при виртуальном скролле | Изменение |
| `browser_max.py` | `_take_content_snapshot()` — вернуть hash как отдельное поле | Минорное изменение |

**Не затрагиваются:**
- `_wait_upload_complete()` — работает корректно
- `_check_dom_upload_ready()` — работает корректно
- `_scan_messages_for_file()` — используется только в fallback
- `_match_filename_in_message()` — используется только в fallback
- `media_archiver.py` — не требует изменений
- GitHub archiver — не затрагивается (другая ветка, без fast_mode, без delta check)

## Error Handling

| Ситуация | Стратегия |
|----------|-----------|
| Delta check не нашёл изменение за 3 retries | Fallback в `_wait_for_file_message()` — полная надёжность |
| Snapshot вернул `None` (ошибка JS) | Считать как «не изменилось» → retry → fallback |
| Hash изменился, но это не наше сообщение | Принять как success — sequential upload гарантирует, что это наше |
| Browser disconnect во время delta check | `_ensure_alive()` в `_wait_for_file_message()` reconnect → continue |
| Virtual scroll detection false positive | Не критично — пропуск retry scan не ломает ничего, monitoring loop работает |

## Testing Strategy

1. **Manual test:** 20 фото в тестовой папке → замерить среднее время на файл (должно быть <3s)
2. **Regression:** 5 репозиториев через GitHub archiver → проверить что большие архивы работают
3. **Edge case:** Отправить файл когда MAX медленный (弱 интернет) → проверить что fallback срабатывает
4. **Virtual scroll detection:** Проверить threshold 150 на реальном MAX (count ~129)

## Open Questions

1. **Threshold для virtual scroll:** 150 — достаточно? На реальном MAX count = 129. Можно сделать config-able.
2. **Delta check для GitHub archiver:** Применить ту же логику? Для больших архивов pre-snapshot до загрузки имеет больше смысла (загрузка занимает минуты, monitoring loop актуален). Рекомендация: пока только для media archiver, потом оценить.
3. **Hash collision:** SHA-256 collision на 15×100 chars — теоретически невозможно, не беспокоимся.
