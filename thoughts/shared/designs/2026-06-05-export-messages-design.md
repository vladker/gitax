---
date: 2026-06-05
topic: Export all messages from MAX chat feed to file
status: draft
---

## Problem Statement

Нужна функция которая считывает **все сообщения** из ленты MAX мессенджера и выводит их в файл со всеми подробностями.

Существующий `collect_all_messages()` собирает только `text` + `html` + `classes`. Нужно извлекать полные данные: отправитель, время, тип, вложения, реакции.

## Constraints

- Работает через Playwright + CDP (существующий Chrome)
- MAX — SPA, сообщения подгружаются по скроллу
- DOM структура сообщений определяется runtime (классы типа `message--out`, `message__time`, `message__sender`)
- Не должно ломать существующую функциональность
- Файл должен быть читаемым и структурированным

## Approach

**Гибридный скролл + JS-парсер.**

Почему не API перехват: MAX может кэшировать ответы, не все сообщения приходят через один endpoint. DOM — единиственный источник правды.

Почему не page state: React/Vue state может быть очищен после рендера, не содержит всех деталей.

### Архитектура

```
export_messages_to_file()
├── _scroll_and_collect_full()     # скроллит ленту, собирает полные данные
│   ├── _collect_full_batch()      # JS-парсер: извлекает данные из видимых сообщений
│   ├── _scroll_up()               # существующая логика скролла
│   └── _deduplicate()             # по сигнатуре текста
├── _write_json()                  # запись в JSON файл
└── _write_csv()                   # альтернативный CSV формат
```

### Данные извлекаемые из каждого сообщения

| Поле | Источник | Описание |
|------|----------|----------|
| `index` | порядок сбора | Порядковый номер в ленте |
| `text` | `textContent` | Полный текст сообщения |
| `html` | `innerHTML` | HTML содержимое (опционально) |
| `classes` | `className` | CSS классы элемента |
| `sender` | `[class*="sender"], [class*="author"], [class*="name"]` | Имя отправителя |
| `timestamp` | `[class*="time"], [class*="date"]` | Время отправки |
| `direction` | `message--out` / `message--in` | Исходящее или входящее |
| `attachments` | `[class*="file"], [class*="attach"], [class*="preview"]` | Вложения с именами |
| `reactions` | `[class*="reaction"], [class*="emoji"]` | Реакции на сообщение |
| `is_reply` | `[class*="reply"], [class*="forward"]` | Флаг ответа/пересылки |

### JS-парсер стратегия

Один `page.evaluate()` блок который:
1. Находит все `[class*="message"]` элементы
2. Для каждого извлекает данные из дочерних элементов
3. Возвращает массив сериализуемых объектов
4. Обрабатывает ошибки gracefully (если элемент недоступен — пропускает)

### Деdupликация

По сигнатуре `text[:120]` — как в существующей логике. Новые сообщения добавляются, старые пропускаются.

### Формат вывода

**JSON (основной):**
```json
{
  "metadata": {
    "exported_at": "2026-06-05T12:00:00",
    "channel_url": "https://web.max.ru/...",
    "total_messages": 1234,
    "format_version": "1.0"
  },
  "messages": [
    {
      "index": 0,
      "text": "📦 some-repo ...",
      "sender": "GitHub Archiver",
      "timestamp": "2026-06-04T15:30:00",
      "direction": "out",
      "type": "text_with_file",
      "attachments": [
        {"name": "some-repo.zip", "size": "45 MB"}
      ],
      "classes": "message message--out message--with-file"
    }
  ]
}
```

**CSV (альтернативный):**
- Упрощённый формат: index, sender, timestamp, text, type, attachments
- HTML не включается (слишком большой)
- Пригоден для открытия в Excel

## Components

### Новый метод `export_messages_to_file()`

Публичный метод в `BrowserMAX`. Параметры:
- `output_path` — путь к файлу (default: `messages_export.json`)
- `format` — `json` или `csv`
- `scroll_passes` — количество проходов скролла
- `include_html` — включать ли HTML в вывод (экономит место)
- `max_messages` — лимит сообщений (0 = без лимита)

### Новый метод `_collect_full_batch()`

Внутренний метод. Выполняет JS-скрипт который:
1. Находит все message-элементы в DOM
2. Для каждого собирает данные из дочерних элементов
3. Обрабатывает различные DOM-структуры MAX (может меняться)
4. Возвращает список словарей

### Новый метод `_scroll_and_collect_full()`

Адаптация существующей скролл-логики:
- Скроллит вверх, собирает полные данные на каждом шаге
- Деdupлицирует по сигнатуре
- Останавливается когда нет новых сообщений

## Data Flow

```
BrowserMAX.connect()
    → navigate() к каналу
    → export_messages_to_file()
        → _scroll_and_collect_full()
            → loop:
                → _collect_full_batch()  [JS в DOM]
                → _deduplicate()
                → _scroll_up()
                → repeat until no new
        → _write_json() / _write_csv()
        → return total_count
```

## Error Handling

- **DOM недоступен** — graceful degradation, собирает что может
- **Скролл не работает** — fallback на PageUp (как сейчас)
- **Файл не записывается** — пишет в temp директорию
- **Память** — пишет порциями, не держит всё в памяти при больших лентах

## Testing Strategy

1. **Ручной тест** — запустить на реальном канале, проверить вывод
2. **Проверка формата** — JSON валидируется, CSV открывается в Excel
3. **Полнота данных** — все поля заполнены для типовых сообщений
4. **Деdupликация** — нет дубликатов при нескольких проходах

## Open Questions

- **Глубина скролла**: сколько проходов нужно для полной ленты? (зависит от MAX)
- **Размер файла**: при 1000+ сообщениях с HTML файл может быть большим → `include_html=False` по умолчанию?
- **Инкрементальный экспорт**: нужно ли поддерживать "только новые" сообщения?
