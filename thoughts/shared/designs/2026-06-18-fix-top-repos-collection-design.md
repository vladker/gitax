# Fix: Top Repositories Collection — Root Cause и Решение

**Дата:** 2026-06-18
**Статус:** validated
**Триггер:** Программа собрала 10000 репозиториев, но в работу взяла только 100 без звёзд.

---

## Problem Statement

При `limit > 1000` (например, 10000) функция `get_top_repositories()` переключается на `/repositories` endpoint. Этот endpoint:

1. **НЕ поддерживает `sort: "stars"`** — допустимые значения: `created`, `updated`, `pushed`, `full_name`. Параметр `stars` молча игнорируется.
2. **НЕ возвращает `stargazers_count`** — ответ содержит минимальные данные без количества звёзд.
3. **Возвращает репозитории по дате создания** — фактически самые старые репо GitHub.

Результат: 10000 репозиториев "собрано", но это старейшие репо без звёзд. После journal-фильтрации (многие уже есть в журнале) в работу попало ~100 новых — все без звёзд.

---

## Root Cause Analysis

| Файл | Строка | Проблема |
|------|--------|----------|
| `github_api.py:159` | `if limit > 1000:` | Переключение на `/repositories` при limit > 1000 |
| `github_api.py:171-177` | `/repositories?sort=stars` | GitHub игнорирует `sort=stars`, возвращает по дате создания |
| `config/model.py:14` | `limit: int = 5000` | Дефолт 5000 > 1000 → всегда попадает в сломанный путь |
| `github_archiver.py:1076` | `self.config.get('archiver', {}).get('limit', 100)` | Читает limit из config (у пользователя 10000) |

---

## Constraints

- **GitHub Search API** (`/search/repositories`) — единственный endpoint, который сортирует по звёздам
- **Search API hard cap:** максимум 1000 результатов (documented GitHub limitation)
- **Без токена:** rate limit 10 req/min → 10 страниц × 100 = 1000 req за ~1.5 мин (в лимите)
- **С токеном:** rate limit 30 req/min → тот же запрос проходит легко

---

## Approach

**Удалить `/repositories` branch entirely.** Оставить только `/search/repositories`.

**Почему:** `/repositories` — это не "альтернатива" для больших лимитов. Это **сломанный** endpoint для нашей задачи. Он не сортирует по звёздам и не возвращает star count. Лучше честно вернуть 1000 лучших репо, чем 10000 случайных.

**Изменения:**

1. `config/model.py`: `limit` дефолт 1000, `field_validator` caps > 1000 → 1000 с warning
2. `github_api.py`: Удалить ветку `if limit > 1000`, оставить только search API
3. `github_archiver.py`: При cap → 1000 показать предупреждение в консоли
4. Убрать `use_search_api` flag (больше не нужен — только один путь)

---

## Architecture

```
Пользователь задаёт limit (например, 10000)
    ↓
config/model.py: field_validator cap → 1000 + warning в лог
    ↓
github_archiver.py: читает limit=1000, показывает "Запрашиваю топ-1000..."
    ↓
github_api.py: /search/repositories?q=stars:>1000&sort=stars&order=desc
    ↓ (10 страниц × 100 = 1000 репо)
    ↓
Dedup по full_name → unique repos
    ↓
Journal filtering → новые репо для загрузки
```

---

## Components

### config/model.py
- `ArchiverConfig.limit`: дефолт 1000 (было 5000)
- `field_validator('limit')`: if > 1000 → log.warning + return 1000
- Убрать `use_search_api` (больше не нужен)
- Убрать `max_per_page` (используется только внутри search API, хардкод 100)

### github_api.py
- `get_top_repositories()`: одна ветка — search API
- Убрать `if limit > 1000` ветку полностью
- Dedup логика остаётся
- 403 handling остаётся

### github_archiver.py
- `load_new_repositories()`: при старте проверить, был ли cap
- Показать "⚠ Запрошено N, но GitHub Search API ограничивает до 1000"

---

## Data Flow

1. Config загружается → `limit` валидируется → cap на 1000
2. Archiver читает `limit` из config dict
3. GitHub API получает `limit=1000` → 10 страниц × 100
4. Каждая страница: search query `stars:>1000`, sort=stars, order=desc
5. Dedup по `full_name` → уникальные репо
6. Journal filtering → новые репо
7. Загрузка и отправка

---

## Error Handling

- **Rate limit (403):** сообщение на русском + graceful stop
- **Config limit > 1000:** warning в лог + автоматический cap
- **Empty results:** сообщение "Не удалось получить репозитории"

---

## Testing Strategy

- [ ] Syntax check всех изменённых файлов
- [ ] Config default: `limit == 1000`
- [ ] Config cap: `limit=10000` → становится 1000 с warning
- [ ] Import test: `GitHubAPI` и `GitHubArchiver` импортируются
- [ ] API test: `get_top_repositories(100)` возвращает репо со звёздами

---

## Open Questions

**Нужно ли добавить опцию "собирать больше 1000" через альтернативные методы?**
→ Нет. GitHub Search API — единственный способ сортировать по звёздам. Альтернативы (GraphQL, third-party) требуют дополнительной инфраструктуры. 1000 лучших репо — это достаточно.

**Что делать с уже собранными "неправильными" репо в журнале?**
→ Не трогать. Они останутся в журнале. При следующем запуске новые 1000 правильных репо добавятся. Пользователь может очистить журнал при необходимости.
