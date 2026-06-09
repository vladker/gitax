---
date: 2026-06-09
topic: "Channel File Downloader — скачивание всех файлов из MAX канала"
status: draft
---

## Problem Statement

Текущий код умеет только **загружать** файлы в MAX (GitHub репозитории, PyPI пакеты, медиа из папки). Нет возможности **скачать** файлы обратно из канала на диск.

Это обратная операция: пользователь хочет иметь пункт меню, который сканирует указанный канал MAX, находит все файловые сообщения и скачивает их в указанную локальную папку.

## Constraints

- **Никаких новых зависимостей** — используем только `requests`, `playwright`, `tqdm` (уже в `requirements.txt`)
- **Работа через существующее CDP-подключение** — не запускаем новый браузер, используем уже открытую сессию
- **Поддержка больших файлов** — скачивание стримингом, без загрузки всего файла в RAM
- **Resume capability** — при повторном запуске не скачивать уже скачанные файлы
- **MAX session** — скачивание должно использовать авторизованную сессию браузера (cookies)

## Approach

**Chosen: DOM scan + cookies-based HTTP download**

### Why this approach?

1. **DOM scan** — MAX file messages содержат `<a download>` элементы с прямыми `href` ссылками на файлы. Мы извлекаем эти URL (уже есть CSS-селекторы в существующем коде).
2. **Cookies + requests** — извлекаем сессионные cookies из browser context (`page.context.cookies()`), скачиваем через `requests.get(url, stream=True)`. Это быстро, не нагружает браузер, поддерживает стриминг больших файлов.
3. **Fallback через браузер** — если URL оказался blob-URL или требует JS-обработчика, используем `page.evaluate()` для fetch внутри браузера (с порогом 50MB для base64).

### Alternatives rejected

- **Pure Playwright click download** ❌ — каждый файл требует отдельного клика + ожидания диалога, катастрофически медленно для 50+ файлов
- **API interceptor** ❌ — MAX API может не отдавать прямые ссылки в JSON-ответах, только в DOM
- **CDP Page.downloadBehavior** ❌ — нестабильно работает с существующим CDP на `connect_over_cdp`, усложняет архитектуру

## Architecture

```
github_archiver.py (menu)
       │
       ▼
channel_downloader.py
├── ChannelDownloader     — оркестратор
│   ├── _scan_channel()   — вызов BrowserMAX для сбора метаданных
│   └── _download_all()   — цикл скачивания с retry + прогресс
│
├── DownloadJournal       — JSON-журнал скачанных файлов
│   ├── is_downloaded()
│   ├── mark_downloaded()
│   └── mark_failed()
│
browser_max.py (1 новый метод)
└── scan_channel_for_files() — скроллинг + сбор файловых сообщений
```

### Нумерация меню (изменения)

```
Было:                          Стало:
[7] Удалить все сообщения      [7] Скачать все файлы из канала    ← NEW
[8] Выход                      [8] Удалить все сообщения в ленте  ← moved
                               [9] Выход
```

## Components

### 1. `BrowserMAX.scan_channel_for_files()` (~80 строк)

**Назначение:** Проскроллить весь канал, собрать метаданные всех файловых сообщений.

**Сигнатура:** `scan_channel_for_files() -> list[dict]`

**Алгоритм:**
1. Запоминаем baseline DOM (количество сообщений)
2. Много-проходной скроллинг вверх (reuse паттерна из `collect_all_messages()`)
3. На каждом проходе для каждого сообщения:
   - Ищем `a[download]` → извлекаем `href` (download URL) и `download` (filename)
   - Ищем `a[href*="download"]` → извлекаем `href`
   - Ищем `video[src]` для медиа-файлов
   - Ищем `[class*="file"]`, `[class*="attach"]` с `title`/`alt` (имя файла)
   - Ищем `[class*="size"]` внутри attachment (размер файла)
4. Дедупликация: tracking по `message_idx` + имени файла

**Возвращает:** список словарей:
```python
{
    "filename": "project-v1.0.0.zip",
    "download_url": "https://...",
    "file_size": 1234567,        # bytes, 0 если не определен
    "message_idx": 42,           # позиция в ленте
    "has_direct_url": True,      # True = можно requests, False = через браузер
    "media_type": "file"         # "file" | "video" | "image"
}
```

**Поведение при ошибках:**
- Если не удалось извлечь download_url (только имя файла): `has_direct_url = False`
- Если не удалось извлечь размер: `file_size = 0`
- Если сообщение не содержит ни одного файлового индикатора: пропускается

### 2. `ChannelDownloader` (~200 строк)

**Назначение:** Оркестрация процесса сканирования и скачивания.

**Сигнатура:** `ChannelDownloader(config_path="config.yaml")`

**Метод `run()`:**
1. Инициализация BrowserMAX через `_init_browser()`
2. Подключение к каналу (`keep_alive_connect()` + `navigate()`)
3. Запрос у пользователя `output_dir` (с дефолтом из config)
4. `_scan_channel()` — получение списка файлов
5. Показ таблицы найденных файлов (имя, размер, кол-во)
6. Подтверждение: "Найдено N файлов (X MB). Скачать? [y/N]"
7. `_download_all(files)` — цикл скачивания
8. Показ итоговой статистики

**Метод `_download_all(files)`:**
```
Для каждого файла:
  1. Проверить DownloadJournal.is_downloaded()
     → Если да: skip,计入 stats.skipped
  2. Попытаться скачать через requests + cookies:
     a. Cookies из браузера → requests.Session
     b. GET с stream=True, timeout=300
     c. Запись чанками на диск
     d. Проверка Content-Length (если есть)
  3. Если не получилось (нет URL или 403/401):
     → Fallback через page.evaluate() (только <50MB)
  4. Если успешно: DownloadJournal.mark_downloaded()
  5. Если ошибка: DownloadJournal.mark_failed(), retry logic
  6. tqdm progress bar для визуализации
```

**Конфигурация (из config.yaml):**
```yaml
channel_downloader:
  output_dir: "./downloads"
  retries: 3
  retry_delay: 5
  max_concurrent: 1
```

### 3. `DownloadJournal` (~100 строк)

**Назначение:** JSON-файл для отслеживания скачанных файлов.

**Файл:** `download_journal.json` (в корне проекта)

**Структура:**
```json
{
  "files": {
    "repo-name-v1.0.0.zip": {
      "filename": "repo-name-v1.0.0.zip",
      "size_bytes": 1234567,
      "downloaded_at": "2026-06-09T12:00:00",
      "output_path": "./downloads/repo-name-v1.0.0.zip",
      "status": "downloaded"
    }
  },
  "stats": {
    "total": 50,
    "downloaded": 45,
    "failed": 3,
    "skipped": 2
  }
}
```

**Методы:**
- `is_downloaded(filename, size_bytes)` — проверка по имени + размеру
- `mark_downloaded(filename, size_bytes, output_path)`
- `mark_failed(filename, size_bytes, error)`
- `get_stats()` — подсчёт статистики
- `_load()` / `save()` — атомарная запись (как в `Journal`)

**Атомарность записи:** временный файл + `os.replace()`, блокировка через `.lock` файл (5 мин stale timeout) — копируем паттерн из `Journal`.

### 4. Изменения в `github_archiver.py` (~50 строк)

**Новый метод `download_channel_files()`:**
```python
def download_channel_files(self):
    """Скачать все файлы из MAX канала в указанную папку"""
    from channel_downloader import ChannelDownloader
    
    print("\n" + "═" * 60)
    print("  Скачивание файлов из канала MAX")
    print("═" * 60)
    
    try:
        downloader = ChannelDownloader("config.yaml")
        downloader.run()
    except Exception as e:
        print(f"\n  ✗ Ошибка: {e}")
        self.logger.error(f"Channel download error: {e}", exc_info=True)
    
    input("\n  Нажмите Enter для возврата в меню...")
```

**Изменение `run()` и `_show_menu()`:**
- `choice == '7'` → `self.download_channel_files()`
- `choice == '8'` → `self.delete_all_messages_in_channel()`
- `choice == '9'` → выход
- Обновить текст подсказки

### 5. Изменения в `config.yaml` (~6 строк)

```yaml
channel_downloader:
  output_dir: "./downloads"    # Папка для скачанных файлов
  retries: 3                   # Повторы при ошибке скачивания
  retry_delay: 5               # Пауза между повторами (сек)
```

## Data Flow

```
Пользователь выбирает [7]
         │
         ▼
ChannelDownloader.run()
         │
         ├─ 1. Инициализация BrowserMAX + навигация в канал
         │
         ├─ 2. _scan_channel() ──────────────────────────────┐
         │      │                                             │
         │      └─ BrowserMAX.scan_channel_for_files()        │
         │            │                                       │
         │            ├─ scroll_to_top()                      │
         │            ├─ для каждого сообщения:                │
         │            │   ├─ найти a[download] → href + name   │
         │            │   ├─ найти [class*="size"] → size      │
         │            │   └─ добавить в список                │
         │            └─ return list[dict] ◄──────────────────┘
         │
         ├─ 3. Показ списка пользователю
         │      "Найдено 42 файла (2.3 GB)"
         │
         ├─ 4. Подтверждение "Скачать? [y/N]"
         │
         ├─ 5. _download_all(files) ─────────────────────────┐
         │      │                                             │
         │      ├─ для каждого файла:                         │
         │      │   ├─ DownloadJournal.is_downloaded()?       │
         │      │   │   └─ yes → skip, continue               │
         │      │   ├─ _download_single(file_info)            │
         │      │   │   ├─ cookies из browser context         │
         │      │   │   ├─ requests.get(url, stream=True)    │
         │      │   │   ├─ запись чанками на диск            │
         │      │   │   └─ DownloadJournal.mark_downloaded() │
         │      │   └─ tqdm.update()                         │
         │      └─ итоговая статистика ◄──────────────────────┘
         │
         └─ 6. "Скачано: 40, Пропущено: 2, Ошибок: 0"
```

## Error Handling

| Ситуация | Действие |
|----------|----------|
| Cookie истекли / сессия потеряна | `_ensure_max_connected()` — переподключение |
| Файл не найден (404) | Пропустить, `DownloadJournal.mark_failed(404)` |
| Сетевая ошибка (ConnectionError) | Retry до `retries` раз, пауза `retry_delay` |
| Content-Length не совпадает после загрузки | Удалить частичный файл, retry |
| Нет download_url в DOM (blob) | Fallback: `page.evaluate(fetch → base64)` для файлов <50MB |
| download_url ведёт на страницу, а не файл | Логировать URL, пропустить с пометкой `manual` |
| Нет прав на запись в output_dir | Сообщить об ошибке, предложить другой путь |
| Файл с таким именем уже существует | Проверить размер. Если совпадает — skip, если нет — добавить суффикс `_1` |
| Пустой канал / нет файлов | Сообщить "В канале не найдено файловых сообщений" |

### Retry strategy

```python
for attempt in range(1, retries + 1):
    try:
        return _do_download(url, output_path, cookies)
    except (ConnectionError, Timeout) as e:
        if attempt == retries:
            raise
        time.sleep(retry_delay)
        # Refresh cookies on retry (session might have renewed)
        cookies = _get_browser_cookies()
```

## Testing Strategy

### Модульные тесты (`test_channel_downloader.py`)

1. **DownloadJournal:**
   - `is_downloaded()` возвращает `True` для записанного файла
   - `is_downloaded()` возвращает `False` для неизвестного файла
   - `mark_downloaded()` корректно обновляет JSON
   - `mark_failed()` корректно обновляет JSON
   - `get_stats()` считает правильно
   - Атомарная запись — не создаёт битый JSON при сбое

2. **Парсинг URL из DOM (mock):**
   - Извлечение `href` из `a[download]`
   - Извлечение имени файла из `download` атрибута
   - Обработка сообщения без файла (не добавляется в результат)
   - Дедупликация по имени файла

3. **ChannelDownloader (mock BrowserMAX):**
   - Обработка пустого списка файлов
   - Пропуск уже скачанных (check `DownloadJournal`)
   - Retry logic при ошибке

### Интеграционные тесты

4. **Сквозной тест с реальным BrowserMAX:**
   - Подключение к каналу с 3 известными файлами
   - Сканирование → должно найти все 3
   - Скачивание → верификация по sha256

5. **Resume тест:**
   - Первый запуск: скачать 2 из 3 файлов (симулировать ошибку на 3-м)
   - Второй запуск: должен пропустить 2, скачать 1

### Ручное тестирование

- Канал с 1 файлом → скачивается корректно
- Канал с 50+ файлами → все скачиваются, прогресс отображается
- Канал с файлами >100MB → стриминг работает, RAM не растёт
- Повторный запуск → уже скачанное пропускается
- Отмена во время скачивания (Ctrl+C) → частичные файлы не остаются

## Open Questions

1. **Какие типы файлов поддерживать?** — На первом этапе: все файлы с `a[download]` в DOM. Потенциально: медиа (video[src], img[src]).
2. **Нужен ли параллельный download (`max_concurrent > 1`)?** — Для первого релиза нет (sequential). Можно добавить позже.
3. **Как быть с файлами, у которых нет download_url в DOM (только blob)?** — Fallback через `page.evaluate()` с fetch + base64. Пока не ясно, бывают ли такие в MAX.
4. **Показывать ли файлы в порядке от новых к старым?** — Да, сканируем снизу вверх (как collect_all_messages). Пользователь видит самые свежие файлы первыми.
5. **Нужен ли фильтр по расширениям?** — Пока нет, скачиваем всё. Можно добавить в будущем.
