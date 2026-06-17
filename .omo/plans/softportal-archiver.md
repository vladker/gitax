# SoftPortal Archiver

## TL;DR

> **Quick Summary**: Добавить SoftPortal Archiver — скрепер ТОП-программ с softportal.com, который публикует посты в MAX канал. Полная глубина категорий через breadcrumb-парсинг страниц программ.
>
> **Deliverables**: 3 новых файла (softportal_api.py, softportal_journal.py, softportal_archiver.py) + интеграция в меню github_archiver.py
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 2 волны (Wave 1: API + Journal параллельно, Wave 2: Archiver + Menu)
> **Critical Path**: API → Archiver → Menu integration

---

## Context

### Original Request
Добавить SoftPortal Archiver: парсить ТОП-программы по категориям, отправлять посты в MAX, поддерживать sync, краулер категорий с выбором.

### Interview Summary
**Ключевые решения**:
- Только текст (без файлов/скриншотов)
- Полный путь категорий из breadcrumb (Windows → CD/DVD диски → Образы дисков)
- Краулер категорий при пустом конфиге, множественный выбор
- Sync обновляет ВСЁ из журнала (без фильтра по категориям)
- Паттерн дублирования (как cargo/pypi, без BaseArchiver)
- Без runtime методов (скреперу не нужны)

**Исследования**:
- SoftPortal: нет API, только HTML → requests + BeautifulSoup
- URL паттерны: `top-{id}-{page}.html`, `software-{id}-{slug}.html`, `dlcategory-{id}.html`
- Breadcrumb на странице программы: `Программы для Windows » CD/DVD диски » Образы дисков » Имя`
- Категории до 3 уровней: Платформа → Категория → Подкатегория

### Metis Review
**Принятые гайдлайны**:
- Пропустить runtime методы (3 пункта в меню вместо 4)
- Дублировать паттерн (не использовать BaseArchiver)
- Без verifier adapter
- Journal: `(RuntimeJournalMixin, BaseJournal)`, Archiver: `(LogMixin, BrowserInitMixin)`
- Runner методы с lazy import и `_ensure_channel_ready("softportal", ...)`

---

## Work Objectives

### Core Objective
Создать SoftPortal Archiver, который парсит ТОП-программы с softportal.com по выбранным категориям и публикует информативные посты в MAX канал.

### Concrete Deliverables
- `softportal_api.py` — SoftPortalAPI: парсинг категорий, ТОП страниц, страниц программ (breadcrumb)
- `softportal_journal.py` — SoftPortalJournal: JSON журнал, дедупликация по (id, platform_id)
- `softportal_archiver.py` — SoftPortalArchiver: load_top_programs(), sync_programs(), _ensure_categories_configured()
- `github_archiver.py` — Интеграция: подменю, runner методы, batch mode, task_map

### Definition of Done
- [ ] `python github_archiver.py` → пункт «SoftPortal Archiver» в меню
- [ ] Выбор «1. Загрузить программы» → краулер категорий → выбор → загрузка ТОП → отправка в MAX
- [ ] Выбор «2. Синхронизация» → обновление всех программ из журнала
- [ ] Выбор «3. Обновить категории» → перезагрузка списка категорий
- [ ] Batch mode: `SP` (load), `SS` (sync)
- [ ] Журнал `softportal_journal.json` ведёт учёт загруженных программ

### Must Have
- Парсинг ТОП страниц по категориям (с пагинацией)
- Парсинг breadcrumb на странице каждой программы (полный путь категорий)
- Краулер категорий с интерактивным выбором
- Дедупликация по (id, platform_id)
- Sync всех программ из журнала (без фильтра по категориям)
- Runner методы с lazy import (как cargo/pypi)
- `_ensure_channel_ready("softportal", ...)` в runner методах
- Config: `softportal_archiver.limit`, `softportal_archiver.categories`

### Must NOT Have (Guardrails)
- Runtime методы (load_runtime / sync_runtimes)
- BaseArchiver (дублируем паттерн)
- Verifier adapter
- Скачивание файлов программ
- Отправка скриншотов/вложений
- Авторизация на SoftPortal
- Поддержка поиска

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (bun test / pytest в проекте)
- **Automated tests**: None (follows existing pattern — cargo/pypi без тестов)
- **Agent-Executed QA**: YES — каждый таск включает QA-сценарии

### QA Policy
- **API**: Bash (python -c "import...") — импортировать, проверить методы, проверить парсинг
- **Archiver**: interactive_bash — запустить меню, проверить отправка в MAX
- **Menu**: interactive_bash — проверить интеграцию в главное меню

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (параллельно — foundation):
├── Task 1: softportal_api.py [deep]
├── Task 2: softportal_journal.py [quick]
└── Task 3: .env.example + config.yaml [quick]

Wave 2 (после Wave 1 — integration):
├── Task 4: softportal_archiver.py [deep]
└── Task 5: github_archiver.py — меню + runner [unspecified-high]

Wave FINAL (после Wave 2 — review):
├── F1: Plan Compliance Audit (oracle)
├── F2: Code Quality Review (unspecified-high)
├── F3: Real Manual QA (unspecified-high)
└── F4: Scope Fidelity Check (deep)
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| 1 | None | 4, 5 |
| 2 | None | 4, 5 |
| 3 | None | 4, 5 |
| 4 | 1, 2, 3 | 5 |
| 5 | 4 | F1-F4 |
| F1-F4 | 5 | user okay |

### Agent Dispatch Summary

- **Wave 1**: 3 таска — T1 → `deep`, T2 → `quick`, T3 → `quick`
- **Wave 2**: 2 таска — T4 → `deep`, T5 → `unspecified-high`
- **FINAL**: 4 ревью — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

> **FORMAT**: Labels MUST use bare numbers: `1.`, `2.`, etc.
> Final Wave labels: `F1.`, `F2.`, etc.

- [x] 1. Создать softportal_api.py — SoftPortalAPI

  **What to do**:
  - Создать `softportal_api.py` с классом `SoftPortalAPI(LogMixin)`
  - `__init__(self, base_url, config)` — базовый URL, requests session, заголовки
  - `get_categories()` — парсит главную страницу, собирает все категории (id, name, parent_id)
    - Парсит блок «Категории» в сайдбаре: платформы (Android, macOS, iOS, Windows)
    - Для каждой платформы парсит подкатегории (Безопасность, Интернет, etc.)
    - Для каждой подкатегории парсит подподкатегории (если есть)
    - Возвращает: `List[dict]` с полями `id`, `name`, `parent_id`, `url`
  - `get_top_programs(category_id, category_name, limit)` — парсит ТОП страницу
    - URL: `top-{category_id}-{page}.html` (пагинация: page=1, 2, 3...)
    - Из карточки программы извлекает: `id`, `name`, `slug`, `version`, `description`, `rating`, `license`, `os`
    - Собирает `program_url` из `software-{id}-{slug}.html`
    - Возвращает: `List[dict]` программ
  - `get_program_detail(id, slug)` — парсит страницу программы
    - URL: `software-{id}-{slug}.html`
    - Извлекает **breadcrumb**: парсит все `<a>` в breadcrumb навигации до последнего сегмента
    - Формирует `full_category_path`: список `[(id, name), (id, name), ...]`
    - Извлекает `all_categories` из блока «Категории:»
    - Возвращает: dict с полными данными программы
  - Добавить rate limiting: задержка между запросами (configurable, default 1s)
  - Добавить retry логику (configurable, default 3 retries)

  **Must NOT do**:
  - Не использовать Playwright (только requests + BeautifulSoup)
  - Не скачивать файлы программ
  - Не авторизовываться на SoftPortal

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Сложный парсинг HTML с несколькими паттернами (breadcrumb, карточки, категории)
  - **Skills**: []
    - No specific skills needed — стандартный web scraping

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (с Task 2, Task 3)
  - **Blocks**: Task 4, Task 5
  - **Blocked By**: None

  **References**:
  - **Pattern References**:
    - `pypi_api.py:PyPIAPI` — паттерн API класса с LogMixin, rate limiting, retry
    - `cargo_api.py:CargoAPI` — аналогичный паттерн парсинга HTML
  - **External References**:
    - SoftPortal TOP page: `https://www.softportal.com/top-2-1.html` — структура карточек
    - SoftPortal program page: `https://www.softportal.com/software-10-daemon-tools.html` — breadcrumb, категории
    - SoftPortal homepage: `https://www.softportal.com/` — категории в сайдбаре

  **Acceptance Criteria**:
  - [ ] Класс `SoftPortalAPI(LogMixin)` существует в `softportal_api.py`
  - [ ] `get_categories()` возвращает список категорий с id, name, parent_id
  - [ ] `get_top_programs(2, "Windows", 10)` возвращает 10 программ с id, name, slug
  - [ ] `get_program_detail(10, "daemon-tools")` возвращает dict с `full_category_path`
  - [ ] Breadcrumb парсинг: `Windows → CD/DVD диски → Образы дисков`
  - [ ] Rate limiting работает (задержка между запросами)
  - [ ] Retry логика работает (3 попытки при ошибке)

  **QA Scenarios**:
  ```
  Scenario: API initialization and basic HTTP
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_api import SoftPortalAPI; api = SoftPortalAPI('https://www.softportal.com', {}); print(type(api))"
      2. Проверить, что объект создан без ошибок
    Expected Result: <class 'softportal_api.SoftPortalAPI'>
    Evidence: .omo/evidence/task-1-init.txt

  Scenario: get_categories returns valid data
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_api import SoftPortalAPI; api = SoftPortalAPI('https://www.softportal.com', {}); cats = api.get_categories(); print(f'{len(cats)} categories'); print(cats[0])"
      2. Проверить, что список не пустой, первый элемент имеет id, name, parent_id
    Expected Result: >0 категорий, первый элемент — dict с id, name, parent_id
    Evidence: .omo/evidence/task-1-categories.txt

  Scenario: get_top_programs returns programs with required fields
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_api import SoftPortalAPI; api = SoftPortalAPI('https://www.softportal.com', {}); progs = api.get_top_programs(2, 'Windows', 5); print(f'{len(progs)} programs'); print(progs[0].keys())"
      2. Проверить, что вернулся список из 5 программ, каждая имеет id, name, slug
    Expected Result: 5 программ, keys включают id, name, slug, version, description
    Evidence: .omo/evidence/task-1-top.txt

  Scenario: get_program_detail extracts breadcrumb correctly
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_api import SoftPortalAPI; api = SoftPortalAPI('https://www.softportal.com', {}); detail = api.get_program_detail(10, 'daemon-tools'); print(detail.get('full_category_path'))"
      2. Проверить, что full_category_path — список кортежей (id, name)
      3. Проверить, что путь содержит минимум 2 сегмента (платформа + категория)
    Expected Result: [('2', 'Windows'), ('271', 'CD/DVD диски'), ('274', 'Образы дисков')]
    Evidence: .omo/evidence/task-1-detail.txt

  Scenario: Rate limiting works
    Tool: Bash (python -c)
    Steps:
      1. python -c "import time; from softportal_api import SoftPortalAPI; api = SoftPortalAPI('https://www.softportal.com', {}); start = time.time(); [api.get_top_programs(2, 'Windows', 1) for _ in range(3)]; print(f'{time.time()-start:.1f}s')"
      2. Проверить, что 3 запроса заняли >2 секунд (rate limiting работает)
    Expected Result: >2 секунд на 3 запроса
    Evidence: .omo/evidence/task-1-rate.txt
  ```

  **Evidence to Capture**:
  - [ ] .omo/evidence/task-1-init.txt
  - [ ] .omo/evidence/task-1-categories.txt
  - [ ] .omo/evidence/task-1-top.txt
  - [ ] .omo/evidence/task-1-detail.txt
  - [ ] .omo/evidence/task-1-rate.txt

  **Commit**: YES
  - Message: `feat(softportal): add SoftPortalAPI with scraping methods`
  - Files: `softportal_api.py`

---

- [x] 2. Создать softportal_journal.py — SoftPortalJournal

  **What to do**:
  - Создать `softportal_journal.py` с классом `SoftPortalJournal(RuntimeJournalMixin, BaseJournal)`
  - `__init__(self, journal_file="softportal_journal.json")` — путь к JSON файлу
  - Дедупликация по `(id, platform_id)` — программа считается новой, если её id или платформа не в журнале
  - `is_processed(id, platform_id)` — проверка, была ли программа загружена
  - `mark_processed(id, platform_id, program_data)` — запись в журнал
  - `get_all_processed()` — возвращает все загруженные программы (для sync)
  - `get_by_platform(platform_id)` — возвращает программы конкретной платформы
  - `get_stats()` — статистика для меню (всего, по платформам)
  - `reset()` — очистка журнала

  **Must NOT do**:
  - Не использовать BaseArchiver
  - Не добавлять сложную логику миграции (первый запуск)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Простой паттерн, копируется из cargo_journal / pypi_journal
  - **Skills**: []
    - No specific skills needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (с Task 1, Task 3)
  - **Blocks**: Task 4, Task 5
  - **Blocked By**: None

  **References**:
  - **Pattern References**:
    - `cargo_journal.py:CargoJournal` — точный паттерн наследования, дедупликация
    - `pypi_libs_journal.py:PyPIJournal` — аналогичный паттерн
  - **API References**:
    - `journal.py:BaseJournal` — базовый класс, RuntimeJournalMixin

  **Acceptance Criteria**:
  - [ ] Класс `SoftPortalJournal(RuntimeJournalMixin, BaseJournal)` существует
  - [ ] `is_processed(id, platform_id)` возвращает bool
  - [ ] `mark_processed()` записывает данные в JSON файл
  - [ ] `get_all_processed()` возвращает список всех программ
  - [ ] `get_stats()` возвращает dict со статистикой
  - [ ] Дедупликация работает: повторный вызов `is_processed` для той же программы → True

  **QA Scenarios**:
  ```
  Scenario: Journal initialization and basic operations
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_journal import SoftPortalJournal; j = SoftPortalJournal('test_journal.json'); print(type(j))"
      2. Проверить, что объект создан без ошибок
    Expected Result: <class 'softportal_journal.SoftPortalJournal'>
    Evidence: .omo/evidence/task-2-init.txt

  Scenario: Deduplication works correctly
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_journal import SoftPortalJournal; j = SoftPortalJournal('test_journal.json'); print(j.is_processed(10, 2)); j.mark_processed(10, 2, {'name': 'test'}); print(j.is_processed(10, 2))"
      2. Первый вызов → False, второй → True
    Expected Result: False, затем True
    Evidence: .omo/evidence/task-2-dedup.txt

  Scenario: Stats returns correct data
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_journal import SoftPortalJournal; j = SoftPortalJournal('test_journal.json'); j.mark_processed(1, 2, {}); j.mark_processed(2, 2, {}); j.mark_processed(3, 1649, {}); print(j.get_stats())"
      2. Проверить, что stats содержит total и breakdown по платформам
    Expected Result: {'total': 3, 'by_platform': {2: 2, 1649: 1}}
    Evidence: .omo/evidence/task-2-stats.txt
  ```

  **Evidence to Capture**:
  - [ ] .omo/evidence/task-2-init.txt
  - [ ] .omo/evidence/task-2-dedup.txt
  - [ ] .omo/evidence/task-2-stats.txt

  **Commit**: YES
  - Message: `feat(softportal): add SoftPortalJournal with deduplication`
  - Files: `softportal_journal.py`

---

- [x] 3. Обновить .env.example и config.yaml

  **What to do**:
  - Обновить `.env.example`: добавить `CHANNEL_softportal=https://web.max.ru/...`
  - Обновить `config.yaml`: добавить секцию `softportal_archiver`
    - `limit: 50` — количество программ для загрузки
    - `categories: [2, 1649]` — категории по умолчанию (Windows, Android)
    - `output_dir: ./temp_softportal` — директория для временных файлов
    - `retries: 3` — количество попыток
    - `retry_delay: 10` — задержка между попытками
    - `page_delay: 1` — задержка между страницами (секунды)
  - Обновить `.gitignore`: добавить `softportal_journal.json`

  **Must NOT do**:
  - Не добавлять чувствительные данные в .env.example
  - Не менять существующие секции конфига

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Простое добавление строк в конфигурационные файлы
  - **Skills**: []
    - No specific skills needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (с Task 1, Task 2)
  - **Blocks**: Task 4, Task 5
  - **Blocked By**: None

  **References**:
  - **Pattern References**:
    - `.env.example` — существующие CHANNEL_ переменные
    - `config.yaml` — существующие секции archiver
  - **API References**:
    - `config.py` — Pydantic модели конфигурации

  **Acceptance Criteria**:
  - [ ] `.env.example` содержит `CHANNEL_softportal`
  - [ ] `config.yaml` содержит секцию `softportal_archiver` с limit и categories
  - [ ] `.gitignore` содержит `softportal_journal.json`

  **QA Scenarios**:
  ```
  Scenario: Config files updated correctly
    Tool: Bash (grep)
    Steps:
      1. grep "CHANNEL_softportal" .env.example
      2. grep "softportal_archiver" config.yaml
      3. grep "softportal_journal.json" .gitignore
    Expected Result: Все 3 grep нашли совпадения
    Evidence: .omo/evidence/task-3-config.txt
  ```

  **Evidence to Capture**:
  - [ ] .omo/evidence/task-3-config.txt

  **Commit**: YES
  - Message: `chore(softportal): add config and env setup`
  - Files: `.env.example`, `config.yaml`, `.gitignore`

---

- [x] 4. Создать softportal_archiver.py — SoftPortalArchiver

  **What to do**:
  - Создать `softportal_archiver.py` с классом `SoftPortalArchiver(LogMixin, BrowserInitMixin)`
  - `_channel_key = "softportal"` — ключ для channel URL
  - `__init__(self, config)` — инициализация API, журнала, конфига
  - `load_top_programs()` — основная логика загрузки:
    1. `_ensure_categories_configured()` — если categories пуст, запускает краулер + выбор
    2. Для каждой категории: `api.get_top_programs(cat_id, cat_name, limit)`
    3. Для каждой новой программы: `api.get_program_detail(id, slug)` → breadcrumb
    4. Формирует текст сообщения: `_build_message_text(program_data)`
    5. Отправляет в MAX через `browser_max`
    6. Записывает в журнал: `journal.mark_processed(id, platform_id, data)`
  - `sync_programs()` — обновление всех программ из журнала:
    1. `journal.get_all_processed()` → список всех программ
    2. Для каждой: `api.get_program_detail(id, slug)` → проверяет изменения
    3. Если версия/рейтинг изменились → обновляет в MAX
  - `_ensure_categories_configured()` — интерактивный выбор категорий:
    1. Проверяет `config.softportal_archiver.categories`
    2. Если пусто: `api.get_categories()` → выводит пронумерованный список
    3. Пользователь вводит номера через пробел
    4. Сохраняет выбранные ID в конфиг
  - `_build_message_text(program_data)` — форматирование сообщения:
    ```
    📦 {name} {version}
    📝 {description}
    📂 {full_category_path}        ← "Windows → CD/DVD диски → Образы дисков"
    🖥 {os} | {license} | ⭐{rating}
    🔗 {url}
    ```
  - `_print_progress(current, total, sent, skipped, status)` — прогресс-бар

  **Must NOT do**:
  - Не использовать BaseArchiver
  - Не добавлять runtime методы
  - Не скачивать файлы программ

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Основная бизнес-логика с интеграцией API, журнала, MAX, интерактивным выбором
  - **Skills**: []
    - No specific skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO (зависит от Task 1, 2, 3)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 5
  - **Blocked By**: Task 1, Task 2, Task 3

  **References**:
  - **Pattern References**:
    - `cargo_archiver.py:CargoArchiver` — паттерн load/sync, _channel_key, _build_message_text
    - `pypi_libs_archiver.py:PyPIArchiver` — аналогичный паттерн
  - **API References**:
    - `browser_max.py` — отправка сообщений в MAX
    - `logging_config.py:LogMixin` — логирование
    - `config.py` — чтение конфига

  **Acceptance Criteria**:
  - [ ] Класс `SoftPortalArchiver(LogMixin, BrowserInitMixin)` существует
  - [ ] `load_top_programs()` загружает ТОП программы, отправляет в MAX
  - [ ] `sync_programs()` обновляет программы из журнала
  - [ ] `_ensure_categories_configured()` работает при пустом конфиге
  - [ ] `_build_message_text()` формирует сообщение с breadcrumb
  - [ ] Прогресс-бар отображается во время загрузки

  **QA Scenarios**:
  ```
  Scenario: Archiver initialization
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_archiver import SoftPortalArchiver; archiver = SoftPortalArchiver({}); print(type(archiver))"
      2. Проверить, что объект создан без ошибок
    Expected Result: <class 'softportal_archiver.SoftPortalArchiver'>
    Evidence: .omo/evidence/task-4-init.txt

  Scenario: Message text formatting with category path
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_archiver import SoftPortalArchiver; a = SoftPortalArchiver({}); msg = a._build_message_text({'name': 'Test', 'version': '1.0', 'description': 'desc', 'full_category_path': [('2','Windows'),('271','CD/DVD')], 'os': 'Windows 10', 'license': 'Free', 'rating': '4.0', 'url': 'https://test.com'}); print(msg)"
      2. Проверить, что сообщение содержит все поля и breadcrumb
    Expected Result: Сообщение с 📦 Test 1.0, 📂 Windows → CD/DVD, 🖥 Windows 10 | Free | ⭐4.0
    Evidence: .omo/evidence/task-4-message.txt

  Scenario: Category crawler returns valid data
    Tool: Bash (python -c)
    Steps:
      1. python -c "from softportal_archiver import SoftPortalArchiver; a = SoftPortalArchiver({}); cats = a._api.get_categories(); print(f'{len(cats)} categories found'); print([c['name'] for c in cats[:5]])"
      2. Проверить, что категории распознаны
    Expected Result: >10 категорий, первые включают Windows, Android, macOS
    Evidence: .omo/evidence/task-4-crawler.txt
  ```

  **Evidence to Capture**:
  - [ ] .omo/evidence/task-4-init.txt
  - [ ] .omo/evidence/task-4-message.txt
  - [ ] .omo/evidence/task-4-crawler.txt

  **Commit**: YES
  - Message: `feat(softportal): add SoftPortalArchiver with load/sync`
  - Files: `softportal_archiver.py`

---

- [x] 5. Интегрировать в github_archiver.py — меню + runner методы

  **What to do**:
  - Добавить runner методы в `github_archiver.py` (как для cargo/pypi):
    - `run_softportal_archiver()` — lazy import, `_ensure_channel_ready("softportal", ...)`, вызывает `load_top_programs()`
    - `run_softportal_sync()` — аналогично, вызывает `sync_programs()`
    - `run_softportal_categories()` — обновляет список категорий
  - Добавить `_run_softportal_menu()` — подменю:
    - `1` → Загрузить программы
    - `2` → Синхронизация
    - `3` → Обновить список категорий
    - `0` → Назад
  - Обновить `task_map` — добавить (softportal_archiver, SoftPortalArchiver, load_top_programs, "SoftPortal Archiver")
  - Обновить batch mode — добавить `SP` (load) и `SS` (sync)
  - Добавить пункт в главное меню

  **Must NOT do**:
  - Не менять существующие runner методы
  - Не добавлять runtime методы
  - Не менять структуру меню

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Интеграция в существующий код, много файлов, требует понимания структуры
  - **Skills**: []
    - No specific skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO (зависит от Task 4)
  - **Parallel Group**: Wave 2 (последний таск)
  - **Blocks**: Final verification
  - **Blocked By**: Task 4

  **References**:
  - **Pattern References**:
    - `github_archiver.py:_run_cargo_menu()` — точный паттерн подменю
    - `github_archiver.py:run_cargo_archiver()` — паттерн runner метода
    - `github_archiver.py:task_map` — структура task_map
    - `github_archiver.py:batch mode` — batch runner
  - **API References**:
    - `github_archiver.py:_ensure_channel_ready()` — проверка канала

  **Acceptance Criteria**:
  - [ ] `python github_archiver.py` → пункт «SoftPortal Archiver» виден в меню
  - [ ] Выбор пункта открывает подменю с 3 опциями
  - [ ] Runner методы вызываются корректно (lazy import работает)
  - [ ] Batch mode: `SP` и `SS` работают
  - [ ] `_ensure_channel_ready("softportal", ...)` вызывается перед загрузкой

  **QA Scenarios**:
  ```
  Scenario: Menu integration — SoftPortal visible in main menu
    Tool: interactive_bash (tmux)
    Steps:
      1. Запустить python github_archiver.py
      2. Проверить, что пункт "SoftPortal Archiver" присутствует в меню
      3. Сделать скриншот меню
    Expected Result: Пункт "SoftPortal Archiver" виден в списке
    Evidence: .omo/evidence/task-5-menu.png

  Scenario: Submenu opens correctly
    Tool: interactive_bash (tmux)
    Steps:
      1. Запустить python github_archiver.py
      2. Выбрать "SoftPortal Archiver"
      3. Проверить, что открылось подменю с 3 пунктами
      4. Сделать скриншот подменю
    Expected Result: Подменю с "1. Загрузить программы", "2. Синхронизация", "3. Обновить категории"
    Evidence: .omo/evidence/task-5-submenu.png

  Scenario: Runner method lazy import works
    Tool: Bash (python -c)
    Steps:
      1. python -c "from github_archiver import run_softportal_archiver; print(type(run_softportal_archiver))"
      2. Проверить, что функция импортируется без ошибок
    Expected Result: <class 'function'>
    Evidence: .omo/evidence/task-5-runner.txt

  Scenario: Batch mode accepts SP command
    Tool: interactive_bash (tmux)
    Steps:
      1. Запустить python github_archiver.py в batch mode
      2. Ввести "SP"
      3. Проверить, что команда распознана
    Expected Result: Команда SP распознана, начинается загрузка
    Evidence: .omo/evidence/task-5-batch.txt
  ```

  **Evidence to Capture**:
  - [ ] .omo/evidence/task-5-menu.png
  - [ ] .omo/evidence/task-5-submenu.png
  - [ ] .omo/evidence/task-5-runner.txt
  - [ ] .omo/evidence/task-5-batch.txt

  **Commit**: YES
  - Message: `feat(softportal): integrate menu and runner methods`
  - Files: `github_archiver.py`

---

## Final Verification Wave (MANDATORY)

> 4 ревью-эйджента запускаются параллельно. ВСЕ должны одобрить.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Прочитать план целиком. Проверить каждый "Must Have": реализация существует. Проверить каждый "Must NOT Have": запрещённых паттернов нет. Проверить, что evidence файлы существуют.
  Вывод: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Проверить все файлы на: type suppression, empty catches, debug logging, unused imports. Проверить AI slop: excessive comments, over-abstraction, generic names.
  Вывод: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [x] F3. **Real Manual QA** — `unspecified-high`
  Запустить github_archiver.py. Проверить каждую QA сцену из каждого таска. Проверить интеграцию (меню → подменю → загрузка). Сохранить evidence.
  Вывод: `Scenarios [N/N pass] | Integration [N/N] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  Для каждого таска: прочитать "What to do", прочитать actual diff. Проверить 1:1 — всё из спецификации построено, ничего лишнего. Проверить "Must NOT do" compliance.
  Вывод: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | VERDICT`

---

## Commit Strategy

| # | Message | Files |
|---|---------|-------|
| 1 | `feat(softportal): add SoftPortalAPI with scraping methods` | `softportal_api.py` |
| 2 | `feat(softportal): add SoftPortalJournal with deduplication` | `softportal_journal.py` |
| 3 | `chore(softportal): add config and env setup` | `.env.example`, `config.yaml`, `.gitignore` |
| 4 | `feat(softportal): add SoftPortalArchiver with load/sync` | `softportal_archiver.py` |
| 5 | `feat(softportal): integrate menu and runner methods` | `github_archiver.py` |

---

## Success Criteria

### Verification Commands
```bash
# Проверить, что модули импортируются
python -c "from softportal_api import SoftPortalAPI; print('API OK')"
python -c "from softportal_journal import SoftPortalJournal; print('Journal OK')"
python -c "from softportal_archiver import SoftPortalArchiver; print('Archiver OK')"

# Проверить, что меню работает
python github_archiver.py  # → пункт "SoftPortal Archiver" в меню
```

### Final Checklist
- [ ] Все "Must Have" реализованы
- [ ] Все "Must NOT Have" отсутствуют
- [ ] Все QA сценарии прошли
- [ ] Evidence файлы существуют в `.omo/evidence/`
- [ ] Меню интегрировано корректно
- [ ] Batch mode работает (`SP`, `SS`)
