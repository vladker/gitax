date: 2026-06-10
topic: "Interactive Split Mode for File Upload"
status: draft

## Problem Statement

При выкладке файлов в MAX (GitHub репозитории, PyPI пакеты) дробление на тома происходит **автоматически** — если файл превышает `split_threshold_mb` (49MB), он режется 7z на тома. Нет возможности перед отправкой указать — дробить файл или нет.

Backuper уже имеет интерактивный выбор (`[1] Однотомный / [2] Многотомный / [3] Свой размер`). Нужно распространить эту возможность на все архиваторы.

## Constraints

- Полная обратная совместимость — существующие вызовы не должны ломаться
- Режим `"auto"` (текущее поведение) остаётся дефолтным
- Паттерн промпта должен совпадать с backuper (3 опции)
- Поддержка batch-режима — если пользователь обрабатывает 50 репо, не спрашивать 50 раз. Только для `split_mode="prompt"`

## Approach

Добавляем параметр `split_mode` в `send_message_with_files()` в `browser_max.py`:

- **`"auto"`** — текущее поведение: дробить если > split_threshold_mb
- **`"on"`** — дробить всегда (даже маленькие файлы)
- **`"off"`** — никогда не дробить (заменяет хак `split_threshold_mb=999999`)
- **`"prompt"`** — спросить пользователя для каждого файла

В конфиг добавляется `archiver.split_mode` со значениями `auto | on | off | prompt`.

## Architecture

### Изменения в `browser_max.py`

**Новый метод `_prompt_split_mode(filename, file_size_mb) -> int`:**
- Показывает: имя файла, размер
- `[1]` — без дробления
- `[2]` — дробить (размер тома из конфига)
- `[3]` — дробить (свой размер тома)
- Возвращает 1, 2, или 3

**Модификация `send_message_with_files()`:**
- Новый параметр `split_mode: str = "auto"`
- Вместо жёсткой проверки `file_size_mb > split_threshold_mb` — переключатель по `split_mode`
- В режиме `"prompt"` вызывается `_prompt_split_mode()` перед каждым файлом
- В режиме `"on"` — split для всех файлов независимо от размера
- В режиме `"off"` — split никогда не выполняется
- `SEVEN_ZIP_VOLUME_SIZE` остаётся "49M" для режимов `"auto"`, `"on"`, и опции 2

### Изменения в `config.yaml`

```yaml
archiver:
  split_mode: auto          # NEW: auto | on | off | prompt
  split_threshold_mb: 49
pypi_libs_archiver:
  split_mode: auto          # NEW
  ...
```

### Изменения в архиваторах

**`github_archiver.py`:**
- При `_send_repo()` читает `config["archiver"]["split_mode"]` (default `"auto"`)
- Передаёт `split_mode` в `send_message_with_files()`
- `split_threshold_mb` передаётся только если `split_mode="auto"`

**`pypi_libs_archiver.py`:**
- Читает `config["pypi_libs_archiver"]["split_mode"]` (default `"auto"`)
- Передаёт `split_mode` в `send_message_with_files()`

**`media_archiver.py`:**
- Заменяет `split_threshold_mb=999999` на `split_mode="off"`

## Components

| Компонент | Ответственность |
|-----------|-----------------|
| `BrowserMAX.send_message_with_files()` | Принимает `split_mode`, решает дробить или нет, вызывает `_prompt_split_mode()` если нужно |
| `BrowserMAX._prompt_split_mode()` | Показывает 3-опционный диалог, возвращает выбор |
| `config.yaml` | Хранит `split_mode` для каждого архиватора |
| `github_archiver.py` | Читает `split_mode` из конфига, передаёт в browser |
| `pypi_libs_archiver.py` | То же |
| `media_archiver.py` | Использует `split_mode="off"` |
| `config_utils.py` | Возможно, хелпер `get_split_mode()` |

## Data Flow

```
config.yaml → archiver.split_mode = "prompt"
                  ↓
github_archiver._send_repo()
                  ↓
browser.send_message_with_files(split_mode="prompt", ...)
                  ↓
Для каждого файла:
  _prompt_split_mode(filename, size)
    → [1] → no split, upload as-is
    → [2] → split_file_with_7z(fp, "49M")
    → [3] → prompt for size → split_file_with_7z(fp, custom_size)
                  ↓
_upload_single_file() для каждого тома/файла
```

## Error Handling

- **Неверный ввод в промпте:** повторный запрос (цикл до корректного `1-3`)
- **Неверный размер тома (опция 3):** если не парсится — попросить ввести снова с примером
- **Backuper не меняется** — его промпт остаётся на месте, он уже передаёт готовые тома в `send_message_with_files()` с `split_threshold_mb=9999`
- Если `split_mode="on"`, но `split_file_with_7z()` вернул пустой список — логируем ошибку, пробуем отправить оригинал

## Testing Strategy

- **`test_prompt_split_mode()`** — проверка `_prompt_split_mode()` с мокированным `input()`:
  - "1" → return 1
  - "2" → return 2
  - "3" → return 3
  - Неверный ввод → повтор
- **`test_send_message_with_files_split_mode()`** — проверка всех режимов:
  - `"off"` — файл 100MB → не дробится
  - `"on"` — файл 1MB → дробится
  - `"auto"` — файл 10MB при пороге 49 → не дробится; файл 100MB → дробится
  - `"prompt"` — с мокированным `_prompt_split_mode()` → дробится/не дробится по ответу
- **Интеграционные тесты** — существующие тесты `test_large_file_upload.py` должны продолжать работать

## Open Questions

- Нужно ли добавить `split_mode` в setup-визард (`github_archiver.py:2350`)? **Решение: да, добавить как шаг 6b после настройки порога**.
- Для batch-режима (50 репо) стоит ли предложить "применить ко всем"? **Решение: нет, в первой версии спрашиваем для каждого файла. Можно добавить опцию позже.**
- Как быть с `PyPI sync` — там тоже вызывается `send_message_with_files()`. **Решение: наследует `split_mode` из конфига `pypi_libs_archiver.split_mode`**.
