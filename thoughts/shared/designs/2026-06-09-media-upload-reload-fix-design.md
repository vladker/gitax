---
date: 2026-06-09
topic: "Media Upload — Page Reload & Silent Corruption Fix"
status: validated
---

## Problem Statement

При отправке медиафайлов (особенно больших видео) через MAX browser automation, программа:
1. **Постоянно перегружает страницу** — `page.reload()` срабатывает во время upload sequence
2. **Видеофайлы повреждаются** — обрыв загрузки из-за reload'а
3. **Программа считает отправку успешной** — false positives в confirmation логике

## Constraints

- Должно работать через CDP (Playwright connect_over_cdp) — основная конфигурация
- Большие файлы (>50MB) уже используют `_upload_large_file()` с переключением на локальный браузер — эту логику не ломаем
- Нужна обратная совместимость с существующими файлами <50MB
- MAX — веб-интерфейс, нет API для проверки загрузки

## Root Causes

Четыре связанные проблемы в `browser_max.py`:

| # | Проблема | Файл:Строка | Механизм |
|---|----------|-------------|----------|
| 1 | `page.reload()` в `_wait_for_file_message()` | 2462 | После reload_timeout (35-120s для больших файлов) — жёсткий reload страницы, обрывающий любую активную загрузку |
| 2 | `_wait_upload_complete()` возвращает True по no-activity heuristic | 1544-1546 | Для больших файлов: 30s без прогресса → считается успехом, хотя MAX может ещё обрабатывать файл |
| 3 | `_confirm_file_sent()` — hash-based false positives | 1817-1827 | SHA-256 последних 15 сообщений — любой change = success (включая сообщения других пользователей) |
| 4 | `_verify_composer_cleared()` — missing DOM = True | 1854 | Если композер пропал (например после reload), возвращает "чисто" |

## Approach

**Убираем `page.reload()` из upload confirmation pipeline полностью.** Reload — деструктивная операция, которая никогда не должна происходить во время загрузки. Вместо этого:

1. `_wait_for_file_message()` — только scroll-based rerender, без reload
2. Усиленные критерии успеха upload'а — для видео и больших файлов
3. Флаг `_upload_in_progress` — блокирует любые `page.reload()`/`page.goto()` во время upload
4. Медиа-тип детекция для адаптивных таймаутов

## Architecture

```
BrowserMAX
├─ Upload State Manager (новый)
│   ├─ _upload_in_progress: bool
│   ├─ _upload_file_path: str
│   ├─ _upload_file_name: str
│   ├─ _is_video: bool
│   └─ lock/unlock методы
│
├─ _upload_single_file() (изменён)
│   ├─ ställer _upload_in_progress = True в начале
│   ├─ выставляет _is_video по расширению
│   ├─ вызывает изменённый _wait_upload_complete()
│   ├─ вызывает изменённый _confirm_file_in_feed()
│   └─ ställer _upload_in_progress = False в конце
│
├─ _wait_upload_complete() (изменён)
│   ├─ для видео: extended timeout, media checks
│   └─ без no-activity exit для больших видео
│
├─ _wait_for_file_message() (изменён)
│   └─ SEC ция reload удалена — только rerender через скролл
│
├─ _confirm_file_in_feed() (расширен)
│   └─ поддержка <video>, <img>, <audio> тегов
│
├─ _confirm_file_sent() (изменён)
│   └─ используется только для файлов <50MB
│
└─ _ensure_alive(), navigate(), _try_navigate() (изменены)
    └─ проверяют _upload_in_progress перед reload/goto
```

## Components

### 1. Upload State Manager

Добавляет в `BrowserMAX.__init__()`:

```python
# Upload state
self._upload_in_progress = False
self._upload_file_size = 0
self._upload_file_name = ""
self._is_video = False
```

**Методы:**
- `_lock_upload_state(filepath)` — устанавливает флаги, логирует начало
- `_unlock_upload_state()` — сбрасывает флаги
- `_can_navigate()` — возвращает `not self._upload_in_progress`

### 2. Media Type Detection

Новый статический метод/утилита:

```python
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
ARCHIVE_EXTENSIONS = {'.zip', '.tar', '.gz', '.7z', '.rar', '.tar.gz', '.whl'}
```

Определяет `is_video` по расширению — влияет на таймауты и логику confirmation.

### 3. Изменённый `_wait_upload_complete()`

**Для видеофайлов:**
- Убираем выход по no-activity heuristic
- Добавляем проверку `document.querySelector('video[src*="blob:"]')` — если композер показывает video preview с blob URL, ждём пока blob станет полным
- Таймаут: `max(120000, file_size_mb * 10000)` — 2x от standard
- Максимальное ожидание: 30 минут для очень больших файлов

**Для не-видео:**
- Оставляем текущую логику
- Но поднимаем no-activity threshold с 30s до 45s для файлов > 50MB

### 4. Изменённый `_wait_for_file_message()`

**Удаляем секцию reload** (строки ~2459-2479):

```
[УДАЛЕНО]
if elapsed >= reload_timeout and elapsed < reload_timeout + 5:
    self.page.reload(...)
```

**Вместо неё:**
- Увеличиваем `snapshot_depth` с 15 до 30 для лучшего покрытия
- После `rerender_timeout` → `_force_rerender()` (как сейчас)
- После `rerender_timeout * 2` (3x для видео) → возвращаем `(False, "not_found", -1)`
- Никогда не reload'им страницу

### 5. Изменённый `_confirm_file_in_feed()`

**Добавляем** support для медиа-тегов:

```javascript
// При поиске файла в сообщении, проверяем:
// 1. <video> с poster/src содержащим filename
// 2. <img> с alt/title содержащим filename
// 3. [class*="file"] [class*="name"] содержащий filename
// 4. Ссылка с [download] атрибутом
// 5. Текстовое содержание сообщения с filename
```

**Увеличиваем** initial wait для больших файлов:
- <50MB: 5s (как сейчас)
- 50-200MB: 15s (было 5s)
- 200-500MB: 30s (было 10s)
- >=500MB: 60s (было 15s)

### 6. Guard в Navigation Methods

В `navigate()`, `_try_navigate()`, `_ensure_alive()`:

```python
if self._upload_in_progress:
    self.logger.warning(
        f"BLOCKED navigation during upload: {self._upload_file_name}"
    )
    return False  # или raise UploadInProgressError
```

### 7. Изменённый `_confirm_file_sent()`

Оставляем только для файлов < 50MB. Для >= 50MB сразу переходим к `_confirm_file_in_feed()`.

## Data Flow

```
send_message_with_files()
  │
  ├─ Для каждого filepath:
  │   ├─ _lock_upload_state(filepath)
  │   ├─ split_file_with_7z() если > 49MB
  │   │
  │   ├─ Для каждого volume:
  │   │   ├─ _upload_single_file(volume_path)
  │   │   │   ├─ _try_navigate() → guard: если upload in progress, skip
  │   │   │   ├─ _click_upload_button() + set_files()
  │   │   │   ├─ _wait_upload_complete()
  │   │   │   │   ├─ video: extended timeout, media checks
  │   │   │   │   └─ no-activity exit только для не-видео
  │   │   │   ├─ _send_message() (Enter)
  │   │   │   ├─ _verify_composer_cleared()
  │   │   │   │
  │   │   │   ├─ confirmation:
  │   │   │   │   ├─ >= 50MB: _confirm_file_in_feed() (filename + media)
  │   │   │   │   └─ < 50MB: _confirm_file_sent() (hash) → fallback to _confirm_file_in_feed()
  │   │   │   │
  │   │   │   └─ если оба провалились:
  │   │   │       └─ _wait_for_file_message() → без reload, только scroll
  │   │   │
  │   │   └─ delete volume после подтверждения
  │   │
  │   └─ _unlock_upload_state()
  │
  └─ cleanup volumes
```

## Error Handling

| Ситуация | Поведение |
|----------|-----------|
| Upload прерван, но композер показывает превью | Ждём дольше (extended timeout для видео) |
| File not found в ленте после rerender_timeout * 2 | Возвращаем False — не успех, не reload |
| Видео подтвердилось в композере, но не отправилось Enter'ом | Retry через `_upload_single_file()` loop |
| Navigation вызван во время upload | Блокируется, логгируется warning |
| CDP disconnect во время upload | `_ensure_alive()` проверяет флаг, не делает goto |

## Testing Strategy

### Unit tests
- `test_upload_state_lock()` — флаг блокирует navigate/reload
- `test_media_classification()` — `.mp4` → video, `.zip` → archive
- `test_confirm_file_in_feed_video()` — поиск `<video>` тегов
- `test_compute_monitor_timeouts_video()` — extended таймауты для видео
- `test_no_reload_in_monitoring()` — `_wait_for_file_message()` не вызывает reload

### Mock/integration tests
- `test_large_video_upload_flow()` — полный flow с mock page
- `test_upload_interrupted_by_navigate()` — navigate блокируется флагом
- `test_small_file_unchanged()` — регрессия: файлы <50MB продолжают работать

### Manual tests
- Реальный `.mp4` 200MB через MAX
- `.zip` 100MB (регрессия для архивов)
- Множество маленьких файлов (регрессия batch upload)

## Open Questions

- Нужно ли добавить эвристику "видео в процессе загрузки" через Network мониторинг (response pending)? — **Нет**, слишком сложно и хрупко для CDP. Используем только DOM-based проверки + адаптивные таймауты.
- Должен ли `_ensure_alive()` переподключаться если `_upload_in_progress`? — **Нет**, должен вернуть текущее состояние без side effects.
