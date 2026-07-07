# План: Thingiverse Архиватор

## TL;DR

> **Цель**: Добавить архиватор для скачивания 3D-моделей с Thingiverse и отправки в MAX канал.
>
> **Деливерables**: 3 новых файла (api, journal, archiver) + интеграция в конфиг и меню
>
> **Усилия**: Medium (~6-8 файлов, следует существующему паттерну PyPI архиватора)
> **Параллелизм**: YES — 2 волны

---

## Context

### Исходный запрос
Сделать публикатор для Thingiverse — скачивать 3D-модели и отправлять в MAX.

### Решения
- **Направление**: Thingiverse → MAX (как GitHub/PyPI архиваторы)
- **Источники**: Popular (основной), по тегам, по категориям, по авторам — выбор из меню
- **Форматы**: Все доступные (STL, OBJ, SCAD, STEP, 3MF и др.)
- **Интеграция**: Паттерн проекта (config.yaml, .env, Pydantic, журнал, меню)

### Metis Review — Критические находки
- ⚠️ **API требует токен!** — THINGIVERSE_TOKEN нужен в .env (регистрация app на thingiverse.com/apps)
- ⚠️ **Мультифайл бандлинг** — вещи с несколькими файлами → ZIP перед отправкой в MAX
- ⚠️ **Санитизация имён** — Win-совместимость (убрать `/:*?"<>|`)
- ✅ `config/model.py` уже имеет placeholder `thingiverse: str = ""` в ChannelsConfig

---

## Work Objectives

### Core Objective
Добавить Thingiverse архиватор: fetch 3D-модели → download → bundle в ZIP → split если большой → отправить в MAX → записать в журнал.

### Concrete Deliverables
- `thingiverse_api.py` — API обёртка
- `thingiverse_journal.py` — журнал
- `thingiverse_archiver.py` — основной класс
- Обновления: `config/model.py`, `config_utils.py`, `.env.example`, `github_archiver.py` (меню), `requirements.txt`

### Must Have
- Токен auth (THINGIVERSE_TOKEN в .env)
- 4 режима: popular, по тегам, по категориям, по авторам
- Мультифайл бандлинг (ZIP)
- 7z splitting для больших файлов
- Журнал с dedup
- Меню на русском

### Must NOT Have
- Изменений в `browser_max.py`, `split_util.py`
- Изменений в существующих архиваторах
- Тестов (в проекте нет тестовой инфраструктуры)

---

## Verification Strategy

- **Тесты**: Нет (проект без тестов)
- **QA**: Agent-executed — каждый task включает сценарии проверки

---

## Execution Strategy

### Волна 1 (параллельно):
- Task 1: thingiverse_api.py
- Task 2: thingiverse_journal.py
- Task 3: config/model.py + config_utils.py
- Task 4: .env.example + requirements.txt

### Волна 2 (после волны 1):
- Task 5: thingiverse_archiver.py
- Task 6: github_archiver.py (меню + dispatch)

---

## TODOs

- [x] 1. thingiverse_api.py — API обёртка

  **Что делать**:
  - Создать `ThingiverseAPI` класс
  - Auth: Bearer token из .env
  - Методы: `get_popular(limit)`, `get_by_tag(tag, limit)`, `get_by_category(slug, limit)`, `get_by_author(username, limit)`
  - `download_thing(thing_id)` — скачать все файлы вещи, собрать в ZIP
  - Санитизация имён файлов (Win)
  - Retry + rate limit handling (как в github_api.py)

  **Референс**: `pypi_api.py` (356 строк), `github_api.py`

  **QA**: `python -c "from thingiverse_api import ThingiverseAPI; api=ThingiverseAPI(); print(len(api.get_popular(3)))"`

  **Коммит**: YES — `feat(thingiverse): add API wrapper`

---

- [x] 2. thingiverse_journal.py — журнал

  **Что делать**:
  - `ThingiverseJournal(RuntimeJournalMixin, BaseJournal)`
  - Track by `thing_id` (unique key)
  - `is_processed(thing_id)`, `mark_sent(thing_id)`, `mark_failed(thing_id)`
  - JSON file: `thingiverse_journal.json`

  **Референс**: `pypi_libs_journal.py` (150 строк)

  **QA**: After run, `thingiverse_journal.json` exists, has entries

  **Коммит**: YES — `feat(thingiverse): add journal`

---

- [x] 3. Config модели + registry

  **Что делать**:
  - `ThingiverseArchiverConfig` в `config/model.py`: limit, output_dir, thingiverse_delay, split_mode
  - Добавить `"thingiverse"` в `ALL_CHANNELS` в `config_utils.py`
  - Добавить `"thingiverse": "thingiverse"` в `_CHANNEL_TO_FUNCTION`

  **Референс**: `NpmArchiverConfig` в model.py, `ALL_CHANNELS` в config_utils.py

  **Коммит**: YES — `feat(thingiverse): add config models`

---

- [x] 4. .env.example + requirements.txt

  **Что делать**:
  - Добавить `THINGIVERSE_TOKEN` и `CHANNEL_thingiverse` в `.env.example`
  - Добавить `thingiverse-client` в `requirements.txt`

  **Коммит**: YES — `feat(thingiverse): add env vars and dependency`

---

- [x] 5. thingiverse_archiver.py — основной класс

  **Что делать**:
  - `ThingiverseArchiver(LogMixin, BrowserInitMixin)`
  - `_channel_key = "thingiverse"`
  - `run_popular()`, `run_by_tag()`, `run_by_category()`, `run_by_author()`
  - Flow: fetch → journal check → download all files → bundle ZIP → split if large → send to MAX → journal → cleanup
  - Мультифайл бандлинг (ZIP)
  - 7z splitting для файлов > threshold

  **Референс**: `pypi_libs_archiver.py` (842 строки)

  **QA**: Run popular mode, limit=1 → verify file sent to MAX, journal updated, temp cleaned

  **Коммит**: YES — `feat(thingiverse): add archiver`

---

- [x] 6. github_archiver.py — меню + dispatch

  **Что делать**:
  - Добавить Thingiverse в главное меню (на русском)
  - Подменю: Popular, по тегам, по категориям, по авторам
  - Dispatch method для запуска архиватора

  **Референс**: Существующие меню для PyPI, NuGet

  **QA**: `python github_archiver.py` → меню показывает Thingiverse без краша

  **Коммит**: YES — `feat(thingiverse): integrate into menu`

---

## Final Verification Wave

- [x] F1. Plan Compliance — oracle
- [x] F2. Code Quality — unspecified-high
- [x] F3. Real QA — unspecified-high
- [x] F4. Scope Fidelity — deep

---

## Commit Strategy
- Каждый task = отдельный коммит
- Формат: `feat(thingiverse): ...`

## Success Criteria
- `python github_archiver.py` → Thingiverse в меню
- Запуск → скачивает модели → отправляет в MAX → журнал обновляется
- Temp файлы очищены после запуска
