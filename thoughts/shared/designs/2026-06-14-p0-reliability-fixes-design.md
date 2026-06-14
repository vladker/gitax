date: 2026-06-14
topic: "P0 Reliability Fixes — Error Handling, SignalHandler, Browser Cleanup, Retry"
status: validated

---

## Problem Statement

Проект gitax содержит системные проблемы надёжности, которые приводят к молчаливым сбоям, утечкам ресурсов и потере данных при нештатных ситуациях. Аудит выявил **179 `except Exception`** блоков, из которых **49 заглушают исключения полностью** (`pass`). SignalHandler перезаписывает обработчики вместо того чтобы накапливать. Browser cleanup не гарантирован при исключениях. Retry декоратор ловит `KeyboardInterrupt`.

**Цель:** Сделать систему устойчивой к ошибкам — ничего не теряется молча, ресурсы всегда чистятся, сигнал завершения чистит ВСЕ архиваторы.

---

## Constraints

- **Не менять публичные API** — архиваторы вызываются из меню и batch_runner, сигнатуры сохраняем
- **Не ломать существующие тесты** — 40+ тестовых файлов должны продолжать работать
- **Python 3.14** — используем современный синтаксис где нужно
- **Windows-first** — проект работает на Windows (PowerShell, msvcrt)
- **Минимальный blast radius** — каждое изменение локализовано, не касаемся рабочего кода

---

## Approach

**4 независимых фикса**, каждый из которых можно применить отдельно и протестировать:

| Фикс | Файлы | Суть |
|------|-------|------|
| **P0-1: Retry** | `retry.py` | Исключить `BaseException` из ловушки |
| **P0-2: SignalHandler** | `signal_handler.py` | Глобальный синглтон со списком callback'ов |
| **P0-3: Browser cleanup** | `browser_max.py`, `github_archiver.py`, `channel_downloader.py`, `batch_runner.py` | `finally` блоки для `.close()` |
| **P0-4: Bare except** | Все `.py` файлы (кроме tests/) | Конкретные исключения + логирование |

**Почему такой порядок:** P0-1 и P0-2 — однофайловые изменения, минимальный риск. P0-3 — структурные изменения в cleanup путях. P0-4 — массовый find-replace по всему проекту.

---

## Architecture

### P0-1: Retry — исключение BaseException

**Проблема:** `exceptions=(Exception,)` по умолчанию ловит ВСЁ, включая `KeyboardInterrupt`, `SystemExit`, `MemoryError`.

**Решение:**
- Добавить белый список исключений, которые **не** должны ловиться: `(KeyboardInterrupt, SystemExit, GeneratorExit)`
- Перед проверкой `isinstance(exc, exceptions)`, проверять что это НЕ `BaseException` подкласс
- По умолчанию оставить `(Exception,)` для backwards compat, но добавить `safe=True` флаг

**Поведение после фикса:**
```
retry() → ловит Exception, но НЕ KeyboardInterrupt/SystemExit
retry(safe=True) → то же + дополнительно фильтрует MemoryError/RecursionError
```

### P0-2: SignalHandler — глобальный синглтон

**Проблема:** Каждый архиватор делает `SignalHandler().register(...)`, создавая новый экземпляр. Последний зарегистрированный обработчик перезаписывает предыдущие.

**Решение:**
- Сделать SignalHandler **настоящим синглтоном** через module-level instance или `__new__`
- Поддерживать **список** cleanup callback'ов вместо одного
- Signal handler итерит по всем callback'ов при SIGINT/SIGTERM
- Каждый callback оборачивается в try/except — один не должен ломать остальные

**Поведение после фикса:**
```
SignalHandler().register(on_cleanup=func_a)  # добавляет func_a
SignalHandler().register(on_cleanup=func_b)  # добавляет func_b (НЕ перезаписывает)
# При SIGINT: func_a() → func_b() (оба выполняются)
```

### P0-3: Browser cleanup — finally блоки

**Проблема:** 15+ мест где `browser.close()` вызывается БЕЗ `finally`. Если исключение между открытием и закрытием — браузер течёт.

**Решение:**
- Все паттерны `browser = None; try: browser = connect(); ...; if browser: browser.close()` переделать на `try/finally`
- `batch_runner._worker` — перенести browser.close из success path в `finally`
- `github_archiver.py` — 5 bare `browser.close()` обернуть в `try/finally`
- `channel_downloader.py` — 4 bare `self.browser.close()` обернуть в `try/finally`

**Паттерн после фикса:**
```python
# ДО
browser = self._ensure_max_connected()
try:
    do_work()
except Exception as e:
    log_error(e)
if browser:
    browser.close()  # <-- не выполнится если do_work() выбросил

# ПОСЛЕ
browser = self._ensure_max_connected()
try:
    do_work()
except Exception as e:
    log_error(e)
finally:
    if browser:
        try:
            browser.close()
        except Exception:
            pass
```

### P0-4: Bare except — конкретные исключения + логирование

**Проблема:** 179 `except Exception`, из которых 49 — `pass` без логирования.

**Стратегия:** Разделить на 3 категории и обработать каждую:

**Категория A — Cleanup code (49 `pass`):**
- Контекст: удаление temp файлов, закрытие browser, остановка observer
- Фикс: добавить `logger.debug(f"cleanup: {e}")` — не error, а debug, т.к. cleanup может легитимно падать
- Шаблон: `except Exception as e: logger.debug("cleanup skipped: %s", e); pass`

**Категория B — Graceful degradation (21 `return`):**
- Контекст: DOM queries, JS evaluation, API interception
- Фикс: сузить до `(PlaywrightException, TimeoutError, RuntimeError)` где возможно
- Оставить `except Exception` где тип заранее неизвестен, но добавить logging

**Категория C — Business logic (90 `log + return`):**
- Уже логируют — сузить типы исключений где контекст ясен
- `(requests.RequestException, ConnectionError)` для HTTP
- `(FileNotFoundError, PermissionError, OSError)` для файлов
- `(PlaywrightException, TimeoutError)` для браузера

---

## Components

### retry.py

| Изменение | Описание |
|-----------|----------|
| Добавить `_DONT_RETRY` tuple | `(KeyboardInterrupt, SystemExit, GeneratorExit, MemoryError, RecursionError)` |
| Проверка перед catch | `if isinstance(exc, _DONT_RETRY): raise` |
| backwards compat | Старый код работает без изменений |

### signal_handler.py

| Изменение | Описание |
|-----------|----------|
| `__new__` синглтон | Все `SignalHandler()` возвращают один объект |
| `_cleanup_callbacks: list` | Список вместо одного callback |
| `_signal_callbacks: list` | Список on_signal callback'ов |
| try/except на каждый | Один callback не ломает остальные |

### browser_max.py + callers

| Файл | Изменение |
|-------|-----------|
| `browser_max.py` | `_disconnect_cdp()` — добавить `finally` для state reset |
| `github_archiver.py` | 5 bare `browser.close()` → `try/finally` |
| `channel_downloader.py` | 4 bare `self.browser.close()` → `try/finally` |
| `batch_runner.py` | Browser cleanup из success path → `finally` |
| `parallel_uploader.py` | Уже использует `finally` — оставить как есть |

### Все файлы — bare except

| Файл | Кол-во | Приоритет |
|------|--------|-----------|
| `browser_max.py` | 68 | Высокий — core module |
| `github_archiver.py` | 47 | Высокий — main entry |
| `pypi_libs_archiver.py` | 18 | Средний |
| `backuper.py` | 14 | Средний |
| `cargo_archiver.py` | 10 | Средний |
| `channel_downloader.py` | 6 | Средний |
| `media_archiver.py` | 5 | Низкий |
| Остальные | ~11 | Низкий |

---

## Data Flow

### Signal propagation (до и после)

**ДО:**
```
SIGINT → SignalHandler_3.on_signal() → archiver_3._shutdown = True
          ↑ только последний регистр!
archiver_1, archiver_2 — НЕ чистятся
```

**ПОСЛЕ:**
```
SIGINT → GlobalSignalHandler._handler()
         → iterates all registered objects:
           → archiver_1._shutdown = True
           → archiver_2._shutdown = True
           → archiver_3._shutdown = True
         → iterates all on_signal callbacks:
           → callback_1(signum, frame)
           → callback_2(signum, frame)
atexit → iterates all on_cleanup callbacks:
         → cleanup_1() [try/except]
         → cleanup_2() [try/except]
         → cleanup_3() [try/except]
```

### Browser lifecycle (до и после)

**ДО:**
```
connect() → work() → [EXCEPTION] → browser.close() SKIPPED → LEAK
```

**ПОСЛЕ:**
```
connect() → try: work() → finally: browser.close() → CLEAN
                    → [EXCEPTION] → finally: browser.close() → CLEAN
```

---

## Error Handling Strategy

### Иерархия обработки

1. **Специфичные исключения** — первичный выбор везде
   - HTTP: `(requests.RequestException, ConnectionError, TimeoutError)`
   - Файлы: `(FileNotFoundError, PermissionError, OSError)`
   - Браузер: `(PlaywrightException, TimeoutError)`
   - JSON: `(json.JSONDecodeError, ValueError)`

2. **Общий `except Exception`** — только где тип заранее неизвестен
   - **Всегда** с `as e` и логированием
   - Никогда без логирования (даже debug level)

3. **`except Exception: pass`** — разрешено ТОЛЬКО в cleanup коде
   - Где fallback — это "ничего не делать" (файл уже удалён, браузер уже закрыт)
   - С `logger.debug` минимум

### Retry исключения

| Тип | Поведение |
|-----|-----------|
| `requests.RequestException` | Ретрается (transient network error) |
| `PlaywrightException` | Ретрается (browser may recover) |
| `FileNotFoundError` | Ретрается (temp file race) |
| `KeyboardInterrupt` | **НЕ ретрается** — сразу вверх |
| `SystemExit` | **НЕ ретрается** — сразу вверх |
| `MemoryError` | **НЕ ретрается** — retry не поможет |

---

## Testing Strategy

### Существующие тесты

- **40+ тестов** должны продолжать работать без изменений
- Тесты на retry (`test_retry.py`) — проверить что KeyboardInterrupt пробрасывается
- Тесты на journal (`test_shared_journal.py`) — проверить что lock работает

### Новые тесты

| Тест | Что проверяет |
|------|---------------|
| `test_retry_does_not_catch_keyboard_interrupt` | KeyboardInterrupt пробрасывается |
| `test_signal_handler_multiple_callbacks` | Все callback'ы выполняются |
| `test_signal_handler_singleton` | Все экземпляры — один объект |
| `test_browser_cleanup_on_exception` | finally закрывает браузер |
| `test_batch_runner_cleanup_on_failure` | cleanup при исключении в worker |

---

## Open Questions

**Никаких.** Все 4 фикса детерминированы и не требуют уточнений.
