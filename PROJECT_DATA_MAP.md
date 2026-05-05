# Карта проекта и источников данных

Документ описывает:
- какие файлы за что отвечают;
- какие таблицы и значения используются;
- откуда именно берутся данные (Ozon API -> таблицы БД -> строки отчета).

## 1) Ключевые файлы проекта

- `src/main.py`
  - CLI-точка входа синхронизации.
  - Режимы запуска через `--mode`: `full`, `products`, `transactions`, `returns`, `returns_fbo`, `cash_flow`, `promo`, `report_postings`, `report_products`, `report_returns`, `report_compensation`, `report_warehouse_stock`, `analytics_stocks`, `analytics_turnover`, `average_delivery_time`, `realization_v2`.

- `src/config.py`
  - Читает настройки из `.env`.
  - Основные переменные:
    - `OZON_CLIENT_ID`, `OZON_API_KEY`
    - `OZON_PERFORMANCE_CLIENT_ID`, `OZON_PERFORMANCE_CLIENT_SECRET`
    - `DATABASE_URL`
    - `SYNC_DAYS_BACK`, `BATCH_SIZE`, `MAX_CONCURRENT_REQUESTS`
    - `LOG_LEVEL`

- `src/ozon_client.py`
  - HTTP-клиент Ozon Seller API/Performance API.
  - Здесь объявлены методы к endpoint-ам Ozon.

- `src/sync_manager.py`
  - Основная логика загрузки и upsert в БД.
  - Вызывает методы `ozon_client`, парсит, нормализует, сохраняет.

- `src/models.py`
  - SQLAlchemy-модели таблиц.
  - В том числе отчеты: `report_returns_items`, `report_warehouse_stock_items`, `report_compensation_items`.

- `src/database.py`
  - Инициализация подключения.
  - Список таблиц, которые реально создаются при `init_database()` (множество `USED_TABLES`).

- `orders_dashboard.py`
  - Aiohttp-сервер отчетов.
  - REST-эндпоинты `/api/...`.
  - Логика расчета финансового отчета (`/api/finance-report`) и формулы агрегатов.

- `web/orders_dashboard.html`
  - UI дашборда.

## 2) REST API сервера отчетов (`orders_dashboard.py`)

Поднятые маршруты:
- `/api/orders`
- `/api/sales`
- `/api/actions`
- `/api/action-products`
- `/api/returns`
- `/api/cash-flow`
- `/api/finance-report`
- `/api/warehouse-stock`
- `/api/analytics-stocks`
- `/api/analytics-turnover`
- `/api/average-delivery-time`
- `/api/realization-v2`
- `/api/articles`

## 3) Основные таблицы, которые используются в текущей сборке

Список из `src/database.py -> USED_TABLES`:
- `sync_logs`
- `products`
- `fact_orders`
- `fact_order_items`
- `transactions`
- `transaction_items`
- `transaction_services`
- `returns`
- `returns_fbo`
- `cash_flow_statements`
- `promo_actions`
- `promo_products`
- `async_reports`
- `report_products_items`
- `report_returns_items`
- `report_warehouse_stock_items`
- `report_compensation_items`
- `report_download_retries`
- `analytics_stocks`
- `analytics_turnover`
- `analytics_average_delivery_time`
- `realization_reports`
- `realization_report_details`

## 4) Источники Ozon API -> таблицы

Ниже ключевые каналы загрузки, используемые в проекте:

- Транзакции:
  - API: `POST /v3/finance/transaction/list`
  - Таблицы: `transactions` (+ нормализация в `transaction_items`, `transaction_services`)

- Возвраты:
  - API: `POST /v1/returns/list`
  - Таблицы: `returns`, `returns_fbo`

- Cash flow:
  - API: `POST /v1/finance/cash-flow-statement/list`
  - Таблица: `cash_flow_statements`

- Realization:
  - API: `POST /v2/finance/realization`
  - Таблицы: `realization_reports`, `realization_report_details`

- Асинхронные отчеты Ozon (через `async_reports` + скачивание файла):
  - Товары: create products report -> `report_products_items`
  - Возвраты: `POST /v2/report/returns/create` -> `report_returns_items`
  - Складские остатки: create warehouse stock report -> `report_warehouse_stock_items`
  - Компенсации: `POST /v1/finance/compensation` -> `report_compensation_items`
  - Декомпенсации: `POST /v1/finance/decompensation` -> `report_compensation_items` (`report_kind = decompensation`)
  - Для получения ссылки на файл используется `POST /v1/report/info`

## 5) Финансовый отчет: откуда берется каждая группа значений

Эндпоинт: `/api/finance-report?month=YYYY-MM`.

### 5.1 Базовые источники

- Основной источник: таблица `transactions` за период месяца.
- Дополнительный источник для компенсаций: `report_compensation_items` (по `effective_date`).
- Поле `transactions.raw_data` используется для:
  - `accruals_for_sale`
  - `sale_commission`
  - детализации `items`
  - детализации `services`

### 5.2 Принципы разборки транзакций

- Продажа (`description = "Доставка покупателю"`):
  - `заказано` = количество `items` (минимум 1)
  - `Выручка` = `max(accruals_for_sale, 0)`
  - `Вознаграждение за продажу` = `abs(min(sale_commission, 0))`
  - услуги из `raw_data.services` маппятся по `service_name` в строки логистики/агентских услуг.

- Возврат от покупателя (`description = "Получение возврата, отмены, невыкупа от покупателя"`):
  - `Возврат выручки` = `abs(min(accruals_for_sale, 0))`
  - `Возврат вознаграждения` = `max(sale_commission, 0)`

- Эквайринг (`description = "Оплата эквайринга"`):
  - строка `Эквайринг` получает `-amount` (нетто по знаку операции).

- Компенсации по описанию транзакции:
  - если в description есть `потеря по вине ozon` или `компенсац`, строка `Компенсации и декомпенсации` получает `-amount`.
  - отдельным проходом добавляются строки из `report_compensation_items`, где статья маппится так:
    - содержит `недовлож` -> `Удержание за недовложение товара`
    - содержит `корректировк` и `услуг` -> `Прочие начисления - Корректировка стоимости услуг`
    - иначе -> `Компенсации и декомпенсации`

### 5.3 Формулы агрегатов в отчете

- `выручка / продажи` = `выручка - возврат выручки`
- `Услуги доставки` = сумма:
  - обработка Pick-up
  - выезд курьера
  - обработка Drop-off
  - логистика
  - обратная логистика
  - доставка курьером Pick-up
- `Услуги агентов` = сумма:
  - обработка возвратов/отмен/невыкупов партнерами
  - звездные товары
  - временное размещение товара партнерами
  - обработка Drop-off партнерами (АПВЗ)
  - доставка до места выдачи
  - эквайринг
- `Услуги FBO` = сумма FBO-услуг
- `Продвижение и реклама` = сумма рекламных строк
- `Другие услуги` = сумма:
  - обработка операционных ошибок продавца
  - временное размещение товара в СЦ/ПВЗ
  - утилизация товаров
  - займы и факторинг
  - прочие начисления - корректировка стоимости услуг
  - удержание за недовложение товара
  - минус компенсации и декомпенсации
- `Возвраты` = `Возврат выручки`
- `Вознаграждение Ozon` = `Вознаграждение за продажу - Возврат вознаграждения`
- `Расходы МП` = сумма:
  - `Вознаграждение Ozon`
  - `Услуги доставки`
  - `Услуги агентов`
  - `Услуги FBO`
  - `Продвижение и реклама`
  - `Другие услуги`
- `Расходы МП, %` = `Расходы МП / выручка / продажи`
- `Маркетинг, %` = `(Оплата за клик + Закрепление отзыва + Вывод в топ + Реклама в сети интернет) / выручка / продажи`
- `Начислено` = `выручка / продажи - возвраты - расходы МП`
- `Валовая прибыль` = `Начислено - Себестоимость`

Примечание:
- строка `Маркетинг` как отдельная строка отключена;
- оставлен только показатель `Маркетинг, %`.

## 6) Важные ограничения текущих данных

- `Себестоимость` пока не загружается отдельным источником, поэтому в finance-report сейчас 0.
- Для некоторых кабинетов Ozon endpoint `/v1/finance/decompensation` может отвечать `404`; в этом случае значения приходят только из `/v1/finance/compensation`.
- Часть строк может зависеть от текста `description` в транзакциях, поэтому при изменениях формулировок Ozon требуется обновлять маппинг.

## 7) Практическое использование

- Полная загрузка:
  - `python -m src.main --mode full`

- Только компенсации/декомпенсации:
  - `python -m src.main --mode report_compensation`

- Пересчет финансового отчета:
  - Запустить сервер: `python orders_dashboard.py`
  - Открыть: `http://127.0.0.1:8088/api/finance-report?month=2026-02`
