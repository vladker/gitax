---
date: 2026-06-08
topic: Large File Upload via Local Browser Switch
status: draft
---

# Large File Upload — Local Browser Switch

## Problem Statement

Файлы > 50MB невозможно загрузить через CDP:

```
ERROR | Cannot transfer files larger than 50Mb to a browser not co-located with the server
```

Это **жёсткое ограничение Playwright CDP** — не баг, не настройка, архитектурное ограничение протокола.

Текущее поведение: 3 попытки → ошибка → файл помечается как failed → продолжается с другим файлом.

**Контекст:** Из 1419 файлов в `C:/Users/vldkr/Pictures` есть видеофайлы до 134MB. Без фикса эти файлы будут пропущены.

## Constraints

- **CDP ограничение 50MB** — невозможно обойти в CDP режиме
- **MAX сессия** — пользователь залогинен в существующем Chrome, нужно сохранить авторизацию
- **Chrome lock file** — два Chrome не могут использовать один `user-data-dir` одновременно
- **Не ломать фото** — 99% файлов < 50MB, должны работать через CDP как раньше

## Approach

**Основная идея:** Для файлов > 50MB временно переключаться с CDP на локальный браузер с тем же `user-data-dir`.

```
CDP режим (обычно)
  │
  ├─ файл < 50MB → загрузить через CDP ✓
  │
  └─ файл > 50MB →
      1. Сохранить состояние CDP
      2. Закрыть CDP соединение (не браузер!)
      3. Запустить локальный Chrome с --user-data-dir
      4. Навигировать на MAX, загрузить файл
      5. Закрыть локальный Chrome
      6. Подключиться обратно через CDP
```

**Ключевой insight:** Мы закрываем **CDP соединение**, а не Chrome. Браузер остаётся открытым, сессия жива. Локальный Chrome использует тот же `user-data-dir` → cookies и авторизация MAX доступны.

## Architecture

### Component: `_upload_large_file` (новый метод в BrowserMAX)

```
_upload_large_file(filepath, filename, file_size_bytes, retries, retry_delay, baseline_count):
  │
  ├─ 1. Сохранить baseline
  ├─ 2. self._disconnect_cdp()          # закрыть CDP, Chrome жив
  ├─ 3. self._launch_with_profile()     # новый Chrome, тот же профиль
  ├─ 4. self.navigate()                # открыть MAX канал
  ├─ 5. self.ensure_page_ready()       # подождать загрузки
  ├─ 6. _upload_single_file(...)       # обычный upload (локальный браузер!)
  ├─ 7. self._close_local_browser()    # закрыть локальный Chrome
  ├─ 8. self.connect()                 # reconnect CDP
  └─ 9. self.navigate()                # убедиться что на правильном канале
```

### Component: `_disconnect_cdp` (новый метод)

Закрывает CDP соединение, но **не закрывает Chrome**:

```
_disconnect_cdp():
  ├─ if self.page: self.page.close()
  ├─ if self.browser: self.browser.close()
  ├─ self.page = None
  ├─ self.browser = None
  └─ self._connected = False
```

**Важно:** `browser.close()` при CDP режиме закрывает **соединение**, а не браузер. Chrome продолжает работать на порту 9222.

### Component: `_launch_with_profile` (новый метод)

Запускает локальный Chrome с тем же user data directory:

```
_launch_with_profile():
  user_data_dir = self._get_user_data_dir()
  self.browser = self.playwright.chromium.launch(
      headless=False,
      args=[
          '--disable-blink-features=Automation',
          f'--user-data-dir={user_data_dir}',
      ]
  )
  context = self.browser.new_context()
  self.page = context.new_page()
  self._connected = True
```

### User Data Dir Resolution

**Проблема:** Chrome может использовать `User Data/Default` или `User Data/Profile 1` и т.д.

**Решение:** Конфигурация с умным дефолтом:

```yaml
# config.yaml
browser:
  user_data_dir: ""           # пустой = автоопределение
  profile_name: "Default"     # "Default", "Profile 1", etc.
```

Автоопределение:
1. По умолчанию: `~\AppData\Local\Google\Chrome\User Data\Default`
2. Если пользователь укажет `profile_name: "Profile 1"` → добавится `/Profile 1`
3. Если укажет полный путь в `user_data_dir` → используется как есть

### Large File Detection

**Где:** `media_archiver.py`, метод `run()`

**Порог:** 50MB (ограничение Playwright CDP)

```
Для каждого файла:
  ├─ size < 50MB → send_message_with_files() через CDP (как раньше)
  └─ size >= 50MB → browser._upload_large_file() (новый путь)
```

## Data Flow

### До (сломано для больших файлов)

```
MediaArchiver.run():
  for file in files:
      browser.send_message_with_files(text="", filepaths=[file])
      # CDP → ERROR для > 50MB
```

### После (работает для всех размеров)

```
MediaArchiver.run():
  for file in files:
      if file_size >= 50 * 1024 * 1024:
          browser._upload_large_file(file, ...)
          # CDP disconnect → local launch → upload → CDP reconnect
      else:
          browser.send_message_with_files(text="", filepaths=[file])
          # CDP как раньше
```

### Переключение Контекста (детально)

```
t=0s:   CDP подключён, обрабатываем фото #64
t=1s:   Следующий файл 134MB → большой
t=1s:   _disconnect_cdp() → CDP отключён, Chrome жив на :9222
t=2s:   _launch_with_profile() → новый Chrome процесс, тот же профиль
t=3s:   navigate() → MAX канал загружается
t=5s:   ensure_page_ready() → страница готова
t=6s:   _upload_single_file() → загрузка через локальный браузер
t=30s:  Upload complete (134MB видео)
t=31s:  _close_local_browser() → локальный Chrome закрыт
t=32s:  connect() → CDP reconnect к Chrome на :9222
t=33s:  navigate() → MAX канал готов
t=34s:  Следующий файл (фото) → обрабатываем через CDP
```

**Общее время на переключение:** ~5 секунд overhead на файл. Для 1-2 видео из 1419 файлов — незначительно.

## Components Affected

| Файл | Изменение | Тип |
|------|-----------|-----|
| `browser_max.py` | Новый метод `_upload_large_file()` | Добавление |
| `browser_max.py` | Новый метод `_disconnect_cdp()` | Добавление |
| `browser_max.py` | Новый метод `_launch_with_profile()` | Добавление |
| `browser_max.py` | Новый метод `_close_local_browser()` | Добавление |
| `browser_max.py` | Новый property `_get_user_data_dir()` | Добавление |
| `media_archiver.py` | Проверка размера файла, ветвление | Изменение |
| `config.yaml` | Новый раздел `browser.user_data_dir` | Добавление |

**Не затрагиваются:**
- `_upload_single_file()` — работает одинаково для CDP и local
- `_wait_for_file_message()` — работает одинаково
- `send_message_with_files()` — не меняется
- GitHub archiver flow — не нужен (архивы < 50MB после сплита)

## Error Handling Strategy

### Chrome Lock File Conflict

**Проблема:** Если существующий Chrome заблокировал `user-data-dir`, новый Chrome не запустится.

**Решение:** `_disconnect_cdp()` закрывает CDP соединение, но Chrome продолжает работать. Lock file остаётся.

**Два варианта:**

**Вариант A (выбран):** Использовать временную копию профиля
```
1. Сохранить cookies из CDP сессии (page.evaluate → document.cookie)
2. _disconnect_cdp()
3. _launch_with_profile(temp_dir) → новый Chrome с temp_dir
4. Восстановить cookies в новой сессии
5. После upload → закрыть, удалить temp_dir
```

**Вариант B:** Закрыть существующий Chrome полностью
```
1. _disconnect_cdp()
2. Закрыть Chrome процесс (kill process on port 9222)
3. _launch_with_profile() → тот же профиль, без lock
4. После upload → reconnect CDP
```

**Выбираю Вариант B** — проще, надёжнее. Cookies сохраняются в profile, Chrome перезапускается с тем же профилем.

**Обновлённый flow:**
```
_upload_large_file():
  1. _disconnect_cdp()           # отключить CDP
  2. _close_remote_chrome()     # закрыть Chrome на :9222 (graceful)
  3. sleep(2)                   # дать время освободить lock file
  4. _launch_with_profile()     # новый Chrome, тот же профиль
  5. navigate(), ensure_page_ready()
  6. _upload_single_file()
  7. _close_local_browser()
  8. _launch_chrome_cdp()       # перезапустить Chrome с CDP
  9. sleep(2)                   # дать запуститься
  10. connect()                 # reconnect CDP
  11. navigate()
```

### Recovery on Failure

Если что-то пошло не так при обработке большого файла:

```
try:
    _upload_large_file(...)
except LargeFileUploadError:
    # Попытка восстановления CDP
    self._close_local_browser()   # если локальный Chrome жив
    self._launch_chrome_cdp()     # перезапустить Chrome с CDP
    self.connect()               # reconnect
    self.navigate()
    mark_failed(filename)        # пометить файл как failed
```

### Timeout Handling

Большие файлы загружаются дольше. Для файлов > 50MB:
- Upload timeout: `max(60000, file_size_mb * 10000)` — удваиваем множитель (10s/MB вместо 5s/MB)
- Monitor timeout: 600s (10 мин) вместо 300s (5 мин)

## Testing Strategy

**Unit tests:**
1. `_get_user_data_dir()` возвращает правильный путь
2. `_disconnect_cdp()` устанавливает state в None
3. `_upload_large_file()` вызывает правильную последовательность методов

**Integration test (ручной):**
1. Положить один файл 60MB в тестовую папку
2. Запустить media archiver
3. Проверить что файл загрузился успешно
4. Проверить что после загрузки CDP работает для следующих файлов

**Regression:**
1. Запустить на папке с только фото (< 50MB)
2. Проверить что ничего не изменилось — CDP используется全程

## Open Questions

1. **Закрытие Chrome:** Graceful close vs force kill. Рекомендация: попробовать `page.close()` → `browser.close()` → если не сработало за 5с → kill process.
2. **Сколько больших файлов:** Если 5+ видео > 50MB, 5× переключение может быть раздражающим. Рекомендация: batch mode — собрать все большие файлы, переключиться один раз, загрузить все, переключиться обратно.
3. **Firefox/Edge:** Пользователь может использовать другой браузер. Рекомендация: пока только Chrome, добавить позже если нужно.
