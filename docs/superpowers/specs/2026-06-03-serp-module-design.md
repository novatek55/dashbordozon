# SERP-модуль: дизайн-спека
**Дата:** 2026-06-03  
**Статус:** Approved

---

## Цель

Добавить в систему три взаимосвязанных компонента:
1. **Главный запрос SKU** — хранить и авто-определять главный поисковый запрос для каждого артикула
2. **SERP-снимки** — собирать топ-20 выдачи ozon.ru по запросу через Chrome-плагин, обогащать данными конкурентов из bestsellers
3. **Отчёт по артикулу (секция «Поиск»)** — позиция нашего товара + сравнение цен с конкурентами (снапшот)

---

## Архитектура

```
Дашборд (JS)
  → POST /api/serp/scrape
      → backend → chrome runtime message (через content.js на 127.0.0.1:8088)
          → background.js: action "scrape_serp"
              → _ensureTab(ozon.ru/search?text=...) [текущий профиль, без новых]
              → executeScript → богатый скрейп топ-20
          → background.js: action "enrich_bestsellers" (существующий fetchBestsellers)
              → выручка / продажи в день по конкурентам
      → backend сохраняет в БД
```

Связь дашборд ↔ плагин уже реализована через `content.js` (инжектируется на `127.0.0.1:8088`).

---

## База данных (4 новые таблицы)

### `serp_snapshots`
| Колонка | Тип | Описание |
|---------|-----|----------|
| id | SERIAL PK | |
| query_text | VARCHAR(500) | Поисковый запрос |
| scraped_at | TIMESTAMPTZ | Время сбора |
| position_count | INTEGER | Кол-во собранных позиций |
| raw_data | JSON | Сырой ответ плагина |

### `serp_positions`
| Колонка | Тип | Описание |
|---------|-----|----------|
| id | SERIAL PK | |
| snapshot_id | FK → serp_snapshots | |
| position | INTEGER | Место в выдаче (1-20) |
| sku | BIGINT | |
| title | VARCHAR(500) | |
| brand | VARCHAR(255) | |
| price | NUMERIC(15,2) | Цена со скидкой |
| price_before | NUMERIC(15,2) | Цена до скидки |
| rating | FLOAT | |
| review_count | INTEGER | |
| stock | INTEGER | Остаток (если виден) |
| promo_label | VARCHAR(100) | Распродажа / Вау-цены / Новинка |
| thumbnail_url | TEXT | |
| revenue_30d | NUMERIC(15,2) | Из bestsellers (если доступно) |
| sales_per_day | FLOAT | Из bestsellers (если доступно) |
| is_our_product | BOOLEAN | Определяется авто по offer_id из products |
| is_competitor | BOOLEAN | Пометка вручную |

Уникальность: `(snapshot_id, position)`

### `serp_competitors`
| Колонка | Тип | Описание |
|---------|-----|----------|
| sku | BIGINT PK | SKU конкурента |
| note | TEXT | Произвольная заметка |
| created_at | TIMESTAMPTZ | |

Глобальный справочник — помечаем конкурентов один раз, метка применяется ко всем снимкам.

### `sku_primary_query`
| Колонка | Тип | Описание |
|---------|-----|----------|
| sku | BIGINT PK | |
| offer_id | VARCHAR(255) | |
| query_text | VARCHAR(500) | Главный запрос |
| set_manually | BOOLEAN | true = выбран вручную |
| updated_at | TIMESTAMPTZ | |

**Авто-правило выбора:** `MAX(searches × conversion)` из `analytics_product_query_details` за последние 30 дней. Пересчитывается при обновлении аналитики или по запросу.

---

## Плагин Chrome (`chrome-extension/unitka/`)

### Новые actions в `background.js`

#### `scrape_serp({ query_text, limit = 20 })`
1. `_ensureTab("https://www.ozon.ru/search/?text=<query>")` — использует существующую вкладку или открывает в текущем профиле
2. Ждёт загрузки карточек (SPA) — polling `document.querySelectorAll` до появления ≥5 карточек или timeout 8с
3. `chrome.scripting.executeScript` с функцией `_scrapeSerpCards(limit)`:
   - Находит карточки по `a[href*="/product/"]`
   - Для каждой карточки извлекает: позицию (порядковый индекс), SKU из URL, название, бренд, цену, старую цену, рейтинг, кол-во отзывов, промо-лейбл, thumbnail URL
4. Возвращает массив позиций

#### `enrich_with_bestsellers({ skus })`
Переиспользует существующий `fetchBestsellers` — делает поиск по каждому SKU, возвращает `{ sku → { revenue_30d, sales_per_day } }`.

### Обновления `content.js`
Добавить обработку новых сообщений от дашборда:
- `{ action: "scrape_serp", query_text, limit }` → проксирует в background, возвращает результат
- `{ action: "enrich_with_bestsellers", skus }` → проксирует в background

### Обновления `ozon-overlay.js`
Существующая кнопка «Собрать со страницы» расширяется: передаёт позицию, старую цену, рейтинг, отзывы, промо-лейбл (было только SKU+название+цена). Отправляет на `/api/serp/save-from-overlay` вместо `/api/unitka/import/competitor`.

---

## Backend

### `src/services/serp_service.py`

```python
class SerpService:
    async def scrape_and_save(pool, query_text) -> dict
    async def save_snapshot(pool, query_text, positions) -> int  # snapshot_id
    async def get_latest_snapshot(pool, query_text) -> dict
    async def mark_competitor(pool, sku, is_competitor, note) -> None
    async def get_primary_query(pool, sku) -> str | None
    async def set_primary_query(pool, sku, query_text, manual) -> None
    async def recalculate_primary_queries(pool) -> int  # кол-во обновлённых
    async def get_article_report(pool, sku) -> dict
```

### `src/dashboard/routes/serp.py`

| Метод | URL | Описание |
|-------|-----|----------|
| POST | `/api/serp/scrape` | Запустить скрейп `{query_text}` |
| POST | `/api/serp/scrape-by-sku` | Скрейп по главному запросу `{sku}` |
| GET | `/api/serp/snapshot?query=...` | Последний снимок выдачи |
| POST | `/api/serp/save-from-overlay` | Сохранить данные от overlay |
| GET | `/api/serp/primary-query?sku=...` | Главный запрос артикула |
| PUT | `/api/serp/primary-query` | Сменить `{sku, query_text}` |
| POST | `/api/serp/competitor` | Пометить конкурента `{sku, is_competitor, note}` |
| GET | `/api/serp/competitors` | Список всех конкурентов |
| GET | `/api/serp/article-report?sku=...` | Снапшот: позиция + цены конкурентов |

Эндпоинт `/api/serp/scrape`:
1. Отправляет сообщение плагину через CDP (существующий `_ozon_post_json` или аналог)
2. Получает топ-20 позиций
3. Для позиций помеченных конкурентов — запрашивает обогащение через bestsellers
4. Сохраняет снимок в БД
5. Возвращает `{ snapshot_id, positions }`

### Миграция БД
Новый файл: `migrations/add_serp_tables.sql` — создаёт 4 таблицы с индексами.

---

## Frontend (`web/orders_dashboard.html`)

### Новая вкладка «Выдача»

- Поле ввода запроса + кнопка «Обновить выдачу»
- Дата последнего снимка
- Таблица позиций:
  - Колонки: Позиция | Фото | SKU | Бренд | Цена | До скидки | Выручка 30д | Прод/день | Рейтинг | Отзывы | Акция | Метка
  - Зелёная строка = наш товар (`is_our_product`)
  - Оранжевая строка = конкурент (`is_competitor`)
  - Кнопка на строке: «★ Конкурент» / «✕ Снять»
  - Фильтр: Все / Наши / Конкуренты
- Кнопка «Экспорт»

### Новая секция «Поиск» в отчёте по артикулу

- Главный запрос + кнопка «Изменить» (dropdown из топ-запросов по `searches × conversion`)
- Наша позиция в последнем снимке
- Мини-таблица: Позиция | Бренд | SKU | Цена | Рейтинг | Отзывы (наш товар выделен)
- Кнопка «Обновить» → `/api/serp/scrape-by-sku`
- Дата снимка

---

## Что НЕ входит в скоуп

- История изменения цен по дням (только последний снимок)
- Автоматический scheduled-скрейп (только ручной триггер)
- Интеграция с MPStats

---

## Порядок реализации

1. Миграция БД (4 таблицы)
2. Обновить `background.js` — новые actions `scrape_serp`, `enrich_with_bestsellers`
3. Обновить `content.js` — проксирование новых actions
4. `src/services/serp_service.py`
5. `src/dashboard/routes/serp.py` + регистрация в `__init__.py`
6. Обновить `ozon-overlay.js` — богатый скрейп
7. Frontend: вкладка «Выдача»
8. Frontend: секция «Поиск» в отчёте по артикулу
