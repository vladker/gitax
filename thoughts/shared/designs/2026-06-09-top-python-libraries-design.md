---
date: 2026-06-09
topic: "Top Python Libraries Archiver"
status: validated
---

# Top Python Libraries Archiver — Design Doc

## Problem Statement

Добавить в GitHub Archiver функцию сборки топа N Python-библиотек и публикации их в отдельный канал MAX. Функция должна следовать тем же паттернам, что и существующий GitHub-архиватор: скачивание файлов, отправка текста + файлов, отслеживание в журнале.

## Constraints

- **Источник данных**: Hugovk датасет (top-pypi-packages) — уже реализован в `pypi_api.py`
- **Публикация**: текст с описанием + файлы пакета (.tar.gz и .whl)
- **Канал**: отдельный MAX channel, отдельный от GitHub
- **Конфиг**: секция `pypi_libs` (channel_url) + `pypi_libs_archiver` (настройки)
- **Журнал**: отдельный `pypi_libs_journal.json` для отслеживания отправленного
- **Меню**: новые пункты (11 и 12) в главном меню `github_archiver.py`

## Approach

Создать новый модуль `pypi_libs_archiver.py` с классом `PyPILibsArchiver`, следуя паттерну `MediaArchiver`:

- Модуль сам загружает конфиг, создаёт свой `BrowserMAX` и журнал
- Импортируется лениво в `github_archiver.py` через `from pypi_libs_archiver import ...`
- Переиспользует `PyPIAPI` из существующего `pypi_api.py`
- Добавляет 2 новых пункта меню: загрузка топа и синхронизация версий

**Почему не расширение `pypi_api.py`**: API-модуль должен оставаться чистой прослойкой над PyPI. Архивная логика (BrowserMAX, journal) — отдельная ответственность.

**Почему не встраивание в `GitHubArchiver`**: класс и так 2119 строк. Выделение в отдельный модуль сохраняет читаемость.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   github_archiver.py                        │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  [11] Load top Python libs                              │  │
│  │  [12] Sync Python libs                                  │  │
│  └──────────────┬────────────────────────────────────────┘  │
└─────────────────┼───────────────────────────────────────────┘
                  │ import
┌─────────────────▼───────────────────────────────────────────┐
│                  pypi_libs_archiver.py                       │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  PyPILibsArchiver                                     │   │
│  │  ┌──────────────────┐  ┌──────────────────┐          │   │
│  │  │  PyPIAPI         │  │  BrowserMAX      │          │   │
│  │  │  (pypi_api.py)   │  │  (browser_max.py)│          │   │
│  │  └──────────────────┘  └──────────────────┘          │   │
│  │  ┌──────────────────┐  ┌──────────────────┐          │   │
│  │  │  PyPILibsJournal │  │  Config (.env +   │          │   │
│  │  │  (journal)       │  │   config.yaml)    │          │   │
│  │  └──────────────────┘  └──────────────────┘          │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. `pypi_libs_archiver.py` — новый модуль

Класс `PyPILibsArchiver`:

- **`__init__(config_path)`**: загружает конфиг через `load_dotenv()` + `yaml.safe_load()`, создаёт `PyPIAPI`, `BrowserMAX` (с `pypi_libs.channel_url`), `PyPILibsJournal`.
- **`load_top_libraries()`**: основной метод. Получает топ N из Hugovk, фильтрует по журналу, для каждого нового пакета: получает инфу → скачивает файлы → формирует сообщение → отправляет в MAX → записывает в журнал.
- **`sync_libraries()`**: проверяет уже отправленные пакеты на наличие новой версии на PyPI.
- **`_build_message_text(pkg_data, file_sizes)`**: формирует текст с именем, версией, описанием, загрузками, ссылкой на PyPI.
- **`_ensure_max_connected()`**: по аналогии с GitHubArchiver._ensure_max_connected().
- **`run()`**: точка входа для работы через меню.

### 2. `PyPILibsJournal` — журнал отправленных библиотек

Хранится в `pypi_libs_journal.json`. Структура записи:

```json
{
    "name": "requests",
    "version": "2.31.0",
    "description": "HTTP library for human beings",
    "downloads": 123456789,
    "status": "sent",
    "sent_at": "2026-06-09T12:00:00",
    "files": ["requests-2.31.0.tar.gz", "requests-2.31.0-py3-none-any.whl"]
}
```

- Дедупликация: `(name, version)` — повторная отправка той же версии блокируется
- Методы: `add()`, `get_all()`, `get_stats()`, `exists(name, version)`, `clear()`
- Атомарная запись (write+rename), как в `journal.py`

### 3. Конфигурация

**`config.yaml`** — добавляем:
```yaml
pypi_libs:
  channel_url: ""  # Из .env PYPI_LIBS_CHANNEL_URL

pypi_libs_archiver:
  limit: 20             # Top N библиотек
  output_dir: "./temp_pypi_libs"
  retries: 3
  retry_delay: 10
```

**`.env.example`** — добавляем:
```
PYPI_LIBS_CHANNEL_URL=
```

## Data Flow

### Flow: Загрузка топа (меню пункт 11)

```
1. User → выбирает [11]
2. PyPILibsArchiver.__init__() → загружает config, создаёт PyPIAPI, BrowserMAX, journal
3. PyPIAPI.fetch_top_packages(limit=20) → список топ пакетов
4. Для каждого пакета:
   a. Проверка journal.exists(name, version) → если уже есть — пропуск
   b. PyPIAPI.get_package_info(name) → детальная информация
   c. PyPIAPI.download_package(name) → [.tar.gz, .whl]
   d. _build_message_text() → текст сообщения
   e. browser.send_message_with_files(text, files) → отправка в MAX канал pypi_libs
   f. journal.add(name, version, ...) → запись в журнал
   g. Удаление временных файлов
5. Вывод статистики: "Отправлено X из Y"
```

### Flow: Синхронизация (меню пункт 12)

```
1. User → выбирает [12]
2. Загружаем все записи из journal
3. Для каждой записи:
   a. PyPIAPI.get_package_info(name) → проверяем latest_version
   b. Если version != latest_version → есть обновление
   c. Показываем таблицу изменений
   d. По подтверждению: download_package + send_message_with_files + journal.update()
4. Вывод статистики
```

## Error Handling

| Ситуация | Реакция |
|----------|---------|
| `pypi_libs.channel_url` не указан | Выход с сообщением "Укажите PYPI_LIBS_CHANNEL_URL в .env" |
| Hugovk датасет недоступен | Ошибка с предложением повторить позже |
| PyPI JSON API ошибка | `_request_with_backoff()` — ретраи с exponential backoff |
| Файл пакета не найден на PyPI | Пропускаем пакет, логируем, продолжаем со следующим |
| MAX браузер не подключился | retry из конфига, затем ошибка пользователю |
| Отправка файла не удалась | retry из конфига через `BrowserMAX` |
| Временные файлы | Удаляются после отправки (как в GitHub flow). `GracefulShutdown` чистит остатки. |

## Testing Strategy

- **Модульные тесты для `PyPILibsJournal`**: add, exists, get_stats, clear, dedup
- **Тесты `_build_message_text()`**: форматирование сообщения с разными входными данными
- **Интеграционные тесты `PyPIAPI`**: уже есть `test_pypi_api.py` — дополнить
- **Ручное тестирование**: запуск пунктов меню 11 и 12 с реальным MAX каналом

## Open Questions

1. **Единовременная отправка всех файлов**: `send_message_with_files` отправляет текст, затем файлы последовательно. Это корректно — как для GitHub.
2. **Размер файлов > 49MB**: `BrowserMAX` автоматом делит на 7z тома. Для .whl это маловероятно (обычно < 2MB), но для .tar.gz больших библиотек может быть актуально.
3. **Синхронизация**: имеет смысл только если между запусками менялась версия пакета на PyPI. Можно добавить опцию "проверять все" или "только выбранные".
