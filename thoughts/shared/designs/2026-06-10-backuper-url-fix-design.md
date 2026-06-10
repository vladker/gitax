# Backuper — Fix: Download URL extraction for restore flow

**Дата:** 2026-06-10
**Статус:** validated

---

## Problem Statement

При восстановлении архива из канала MAX `run_restore()` не может найти download URL для томов:

```
[1/1] Screenshots_20260610_0953 (1 том(ов))
  ↓ Screenshots_20260610_0953.7z ... ✗ URL не найден
  ✗ Скачивание 'Screenshots_20260610_0953' прервано.
```

**Root cause:** `_find_download_url()` (backuper.py:482) пытается найти URL через JS-запрос к текущему DOM после того, как `collect_all_messages()` проскроллила канал и целевые сообщения уже не в DOM. Дополнительно — CSS-селекторы ищут только `<a>` теги, а MAX может рендерить файлы через другие элементы.

---

## Constraints

- Минимальные изменения существующего кода
- Обратная совместимость — `scan_channel_for_archives()` не ломает существующих вызовов
- URL должен собираться **во время сканирования**, когда DOM максимально полный
- Fallback на `_find_download_url()` для старых записей в журнале

---

## Approach

**Собирать download URL во время сканирования и хранить в метаданных архива.**

В `scan_channel_for_archives()` после группировки томов добавить JS evaluate для извлечения `{filename: download_url}` из DOM, используя те же селекторы что в `scan_channel_for_files()`.

Почему это, а не улучшение `_find_download_url()`:

| Подход | Минусы |
|--------|--------|
| Улучшать `_find_download_url()` | Всё равно работает с частичным DOM — ненадёжно |
| Вызывать `scan_channel_for_files()` | Повторное сканирование, другой формат данных |
| **Сбор URL в scan_channel_for_archives** | DOM полный, один проход, минимальные изменения |

---

## Architecture

### Изменяемые файлы

| Файл | Изменения |
|------|-----------|
| `browser_max.py` | Новый метод `_extract_file_urls()`, изменение `scan_channel_for_archives()` |
| `backuper.py` | Изменение `_find_download_url()`, изменение `run_restore()` |

### Новый метод: `_extract_file_urls()`

```
BrowserMAX._extract_file_urls() -> dict[str, str]
```

Однократный `page.evaluate()` который извлекает filename→download_url из всех сообщений в DOM.

Алгоритм JS:
1. Найти все message-like элементы (те же селекторы что `scan_channel_for_files`)
2. Для каждого: проверить `a[download]`, `a[href*="download"]`, `video[src]`, `img[src]` (excluding emoji/avatar), `[class*="file"]`
3. Если найден filename + url → добавить в результат
4. Дедупликация по filename
5. Вернуть объект `{filename: url}`

### Изменение: `scan_channel_for_archives()`

После группировки томов:

```python
# Extract URLs from DOM
url_map = self._extract_file_urls()

# Attach to each archive
for arch in result:
    arch["volume_urls"] = {}
    for vol in arch["volumes"]:
        if vol in url_map:
            arch["volume_urls"][vol] = url_map[vol]
```

Новое поле `volume_urls` в каждой записи архива.

### Изменение: `_find_download_url()`

```python
def _find_download_url(self, browser, filename, url_map=None):
    if url_map and filename in url_map:
        return url_map[filename]
    # Fallback: DOM query (unchanged)
```

### Изменение: `run_restore()`

При вызове `_find_download_url()` передавать `arch.get("volume_urls", {})`.

---

## Data Flow (исправленный)

```
run_restore()
  │
  ├─ 1. browser.scan_channel_for_archives()
  │     ├─ collect_all_messages()
  │     ├─ regex → .7z файлы
  │     ├─ group_volumes()
  │     ├─ _extract_file_urls() ← NEW
  │     └─ attach volume_urls to each archive
  │
  └─ 2. Для каждого тома:
        ├─ dl_url = arch["volume_urls"].get(vol_name)  ← NEW priority
        ├─ если нет: dl_url = _find_download_url()      ← fallback
        └─ _download_file(browser, dl_url, path)
```

---

## Error Handling

| Сценарий | Поведение |
|----------|-----------|
| `_extract_file_urls()` возвращает пустой dict | Fallback на DOM query в `_find_download_url()` |
| URL найден для одних томов, но не для других | Для найденных — прямой URL, для остальных — fallback |
| `page.evaluate()` падает с ошибкой | Возвращаем пустой dict, логируем warning, fallback |

---

## Testing Strategy

**Новые тесты для `_extract_file_urls()`:**

- Извлекает URL из `a[download]` элементов
- Извлекает URL из `a[href*="download"]` альтернативных ссылок
- Извлекает URL из `video[src]` элементов
- Извлекает URL из `img[src]` (non-emoji)
- Пустой DOM возвращает пустой dict
- Ошибка `page.evaluate()` возвращает пустой dict
- Дедупликация по filename

**Модифицированные тесты для `scan_channel_for_archives()`:**

- Проверить что `volume_urls` присутствует в результате
- Проверить что URL подставляются в `volume_urls` для соответствующих томов

**Существующие тесты (не менять):**

- `test_channel_scan.py` — все тесты остаются без изменений
- `test_backuper_journal.py` — без изменений
- `test_channel_downloader.py` — без изменений

---

## Open Questions

**Нет.** Решение однозначное: собирать URL во время сканирования, когда DOM полный. Fallback на старый DOM-запрос для безопасности.
