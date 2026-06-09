# GitHub Archiver — TODO

## P0 — Критические (корректность)

- [x] Удалить дубликат `send_message_with_file` (`browser_max.py:1964` и `:2355`) — удалён старый, оставлена обёртка
- [x] Исправить `_wait_confirmation` — всегда возвращает `True` даже при таймауте — удалён целиком
- [x] Удалить неиспользуемый `_wait_confirmation` или интегрировать в основной поток

## P1 — Архитектура и надёжность

- [ ] Извлечь общую логику загрузки файла в `_do_upload(filepath)` — дублируется в `send_message_with_file` и `_upload_single_file`
- [ ] Вынести селекторы в константы класса: `MESSAGE_SELECTOR`, `INPUT_SELECTORS`, `UPLOAD_BUTTON_SELECTOR` и т.д.
- [ ] Проверить baseline count после отправки текстового сообщения — убедиться, что текст реально ушёл перед обновлением счётчика
- [ ] Добавить проверку свободного места на диск перед загрузкой больших репозиториев
- [ ] Перенести `pyperclip` import на верх уровня модуля с быстрым fail при отсутствии

## P2 — Тесты

- [ ] Тесты для `Journal` — save/load/corruption handling
- [ ] Тесты для `parse_message()` — классификация repo_text / file / other
- [ ] Тесты для `group_messages_by_repo()` — группировка, missing volumes, orphaned files
- [ ] Тесты для `GitHubAPI` — URL generation, rate limit handling
- [ ] Тесты для `_resolve_file_owner()` — matching filename to repo

## P3 — Код-качество

- [ ] Вынести магические числа в константы/конфиг: `300`, `60000`, `no_new >= 15`, `MAX_OVERSCROLL_STALE = 3` и т.д.
- [ ] Унифицировать return types — `send_message_with_file` возвращает `(bool, bool)`, `_upload_single_file` возвращает `bool`
- [ ] Добавить type hints ко всем public методам
- [ ] Заменить `except Exception` на конкретные исключения где это уместно
- [ ] Интегрировать `rollback_journal.py` как опцию меню в `github_archiver.py`

## P4 — Будущее

- [ ] Добавить progress bar для скачивания ZIP (tqdm)
- [ ] Конфигурируемые селекторы в `config.yaml` — при изменении DOM MAX не ломает код
- [ ] Rate limiting для GitHub API с exponential backoff
- [ ] Ресume прерванной загрузки — проверить, что файл уже частично загружен в MAX
- [ ] Добавить метрики: среднее время загрузки, процент успешных, время подтверждения
