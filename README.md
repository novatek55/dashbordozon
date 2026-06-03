# Ozon API Data Sync Tool

Программа для выгрузки всех данных из Ozon API (Seller API + Performance API) в локальную базу данных PostgreSQL для дальнейшего анализа и построения отчетов.

## 📋 Структура проекта

```
ozon_api_sync/
├── src/
│   ├── config.py          # Конфигурация приложения
│   ├── database.py        # Подключение к БД
│   ├── models.py          # Модели SQLAlchemy
│   ├── ozon_client.py     # Клиент для Ozon API
│   ├── sync_manager.py    # Логика синхронизации
│   ├── analytics.py       # Модуль аналитики
│   └── main.py            # Точка входа
├── config/
│   └── .env               # Переменные окружения
├── migrations/            # Миграции Alembic
├── logs/                  # Логи синхронизации
├── requirements.txt       # Зависимости
└── README.md             # Документация
```

## 🔧 Установка

1. **Клонируйте репозиторий:**
```bash
cd ozon_api_sync
```

2. **Создайте виртуальное окружение:**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows
```

3. **Установите зависимости:**
```bash
pip install -r requirements.txt
```

4. **Настройте переменные окружения:**
```bash
cp .env.example .env
# Отредактируйте .env файл
```

## ⚙️ Конфигурация

Создайте файл `.env` с вашими API ключами:

```env
# Ozon API Credentials
OZON_CLIENT_ID=your_client_id_here
OZON_API_KEY=your_api_key_here

# Performance API (опционально, для рекламной статистики)
OZON_PERFORMANCE_CLIENT_ID=your_performance_client_id
OZON_PERFORMANCE_CLIENT_SECRET=your_performance_client_secret

# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/ozon_analytics

# Sync Settings
SYNC_DAYS_BACK=30
BATCH_SIZE=1000
MAX_CONCURRENT_REQUESTS=5

# Logging
LOG_LEVEL=INFO
```

### Как получить API ключи:

**Seller API:**
1. Зайдите в личный кабинет seller.ozon.ru
2. Перейдите в "Настройки" → "Seller API"
3. Создайте ключ с ролью "Admin read only"
4. Скопируйте Client ID и API Key

**Performance API:**
1. Перейдите в "Настройки" → "Performance API"
2. Создайте сервисный аккаунт
3. Добавьте ключ
4. Скопируйте Client ID и Client Secret

## 🚀 Использование

### Полная синхронизация всех данных:
```bash
python -m src.main
```

### Синхронизация конкретных сущностей:
```bash
# Только товары
python -m src.main --mode products

# Только остатки
python -m src.main --mode stocks

# Только отправления (заказы)
python -m src.main --mode postings --days-back 7

# Только транзакции
python -m src.main --mode transactions --days-back 30

# Только возвраты
python -m src.main --mode returns

# Только рекламные кампании
python -m src.main --mode campaigns
```

## 📊 Доступные данные

### 1. **Товары (Products)**
- `/v3/product/list` - список всех товаров
- `/v2/product/info` - детали товара
- `/v4/product/info/prices` - цены
- `/v4/product/info/stocks` - остатки

**Сохраняемые поля:**
- product_id, offer_id, name, barcode
- category_id, type_id
- price, old_price, retail_price
- stock_fbo, stock_fbs
- is_visible, status

### 2. **Отправления (Postings/Заказы)**
- `/v3/posting/fbs/list` - список отправлений FBS
- `/v2/posting/fbo/list` - список отправлений FBO

**Сохраняемые поля:**
- posting_number, order_id, status
- created_at, shipment_date, delivered_at
- delivery_schema (FBO/FBS)
- total_price, total_discount
- customer данные, адрес доставки
- Товары в заказе (PostingItem)

### 3. **Транзакции (Finance)**
- `/v3/finance/transaction/list` - список транзакций

**Сохраняемые поля:**
- transaction_id, operation_id
- operation_type, operation_date
- amount, currency
- posting_number (связь с заказом)
- description, type

### 4. **Возвраты (Returns)**
- `/v3/returns/company/fbs` - возвраты FBS
- `/v3/returns/company/fbo` - возвраты FBO

**Сохраняемые поля:**
- return_id, posting_number
- sku, offer_id, product_name
- quantity, return_reason
- status, returned_at
- refund_amount

### 5. **Рекламные кампании (Performance API)**
- `/api/client/campaign` - список кампаний
- `/api/client/statistics/json` - статистика

**Сохраняемые поля:**
- campaign_id, title, state
- adv_object_type (SKU/SEARCH_PROMO)
- daily_budget, total_budget
- created_at, started_at, ended_at
- Статистика: views, clicks, spent, orders, revenue

### 6. **Аналитика**
- `/v1/analytics/data` - данные аналитики (актуально; limit 1..1000, dimension, metrics)
- `/v1/analytics/product-queries` - агрегированная аналитика поисковых запросов по SKU (актуально)
- `/v1/analytics/product-queries/details` - детализация поисковых запросов по SKU (актуально)
- `/v1/analytics/stocks` - аналитика остатков по SKU (актуально, основная замена stock_on_warehouses)
- `/v1/analytics/turnover/stocks` - оборачиваемость по SKU (актуально)
- `/v2/analytics/stock_on_warehouses` - устаревающий метод, используйте `/v1/analytics/stocks`

### 7. **Рейтинг продавца**
- `/v1/rating/summary` - сводка по рейтингу
- `/v1/rating/history` - история рейтинга

## 📈 Аналитика и отчеты

После синхронизации данных можно использовать модуль `analytics.py`:

```python
from src.analytics import OzonAnalytics
from src.database import init_database

async def main():
    await init_database()
    analytics = OzonAnalytics()
    
    # Сводка по товарам
    products_summary = await analytics.get_products_summary()
    
    # Товары с низкими остатками
    low_stock = await analytics.get_low_stock_products(threshold=10)
    
    # Топ товаров по выручке
    top_products = await analytics.get_top_products_by_revenue(days=30)
    
    # Сводка по продажам
    sales_summary = await analytics.get_sales_summary(days=30)
    
    # Продажи по дням
    sales_by_day = await analytics.get_sales_by_day(days=30)
    
    # Финансовая сводка
    financial = await analytics.get_financial_summary(days=30)
    
    # Анализ возвратов
    returns = await analytics.get_returns_summary(days=30)
    
    # Рекламная статистика
    advertising = await analytics.get_advertising_summary(days=30)
    
    # Полный отчет
    full_report = await analytics.generate_full_report(days=30)
```

## 🗄️ Структура базы данных

### Основные таблицы:

| Таблица | Описание |
|---------|----------|
| `products` | Товары Ozon |
| `postings` | Отправления (заказы) |
| `posting_items` | Товары в отправлениях |
| `transactions` | Финансовые транзакции |
| `returns` | Возвраты товаров |
| `campaigns` | Рекламные кампании |
| `campaign_statistics` | Статистика кампаний |
| `stock_history` | История остатков |
| `sync_logs` | Логи синхронизации |

## 🔄 Режимы синхронизации

### Полная синхронизация
Загружает все данные за указанный период (по умолчанию 30 дней).

### Инкрементальная синхронизация
При повторном запуске:
- Обновляет существующие записи
- Добавляет новые записи
- Сохраняет историю изменений

### Параллельная загрузка
- Использует asyncio для параллельных запросов
- Настраиваемое количество одновременных запросов
- Автоматический retry при ошибках

## 📋 План разработки

### ✅ Реализовано:
- [x] Клиент для Ozon Seller API
- [x] Клиент для Ozon Performance API
- [x] Модели базы данных
- [x] Синхронизация товаров
- [x] Синхронизация остатков
- [x] Синхронизация отправлений
- [x] Синхронизация транзакций
- [x] Синхронизация возвратов
- [x] Синхронизация рекламных кампаний
- [x] Модуль аналитики

### 📌 В планах:
- [ ] Синхронизация чатов с покупателями
- [ ] Синхронизация отзывов
- [ ] Синхронизация акций (Hot Sales)
- [ ] Синхронизация сертификатов
- [ ] Экспорт отчетов в Excel
- [ ] Веб-интерфейс для просмотра данных
- [ ] Графики и визуализация
- [ ] Автоматическая синхронизация по расписанию

## 🧩 Варианты работы с SQL

Ниже практичные варианты, чтобы работать с SQL в этом проекте.  
`PHP` ставить не обязательно: основной стек проекта уже на `Python + PostgreSQL`.

### 1. Через Python (рекомендуется для этого репозитория)
- Подходит для автоматизации, ETL и отчетов.
- Используется текущим кодом (`asyncpg`, `SQLAlchemy`).
- Примеры: `python -m src.main`, `python orders_dashboard.py`.

### 2. Через SQL-клиент (быстро для ручного анализа)
- `DBeaver`, `pgAdmin`, `DataGrip` или `psql`.
- Подключение берите из `.env` (`DATABASE_URL`), например:
  - `host=localhost`
  - `port=5432`
  - `database=ozon_analytics`
  - `user/password` из вашей конфигурации.

### 3. Через готовый web BI (без кодинга)
- В проекте есть `docker-compose.metabase-local.yml` для локального `Metabase`.
- Хорошо подходит для дашбордов, фильтров и SQL-визуализации.

### 4. Через локальный web UI проекта
- `orders_dashboard.py` поднимает web-интерфейс для отчетов и выборок.
- Это удобный путь для готовых отчетов без ручного SQL.

### 5. Через PHP (если нужен именно PHP-стек)
- Используйте `PDO` + `pdo_pgsql`.
- Подходит, если хотите писать отдельный сайт/панель на PHP.
- Для текущего репозитория это дополнительный слой, а не необходимость.

Пример минимального подключения на PHP:

```php
<?php
$dsn = "pgsql:host=localhost;port=5432;dbname=ozon_analytics";
$pdo = new PDO($dsn, "user", "password", [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
]);

$stmt = $pdo->query("SELECT now() AS server_time");
print_r($stmt->fetch());
```

## 🔍 Полезные SQL-запросы

### Продажи по дням:
```sql
SELECT 
    DATE(created_at) as date,
    COUNT(*) as orders_count,
    SUM(total_price) as revenue
FROM postings
WHERE status != 'cancelled'
GROUP BY DATE(created_at)
ORDER BY date DESC;
```

### Топ-10 товаров по выручке:
```sql
SELECT 
    p.name,
    SUM(pi.quantity) as total_quantity,
    SUM(pi.quantity * pi.price) as total_revenue
FROM posting_items pi
JOIN products p ON p.id = pi.product_id
JOIN postings po ON po.id = pi.posting_id
WHERE po.status != 'cancelled'
GROUP BY p.id, p.name
ORDER BY total_revenue DESC
LIMIT 10;
```

### Расходы на рекламу:
```sql
SELECT 
    c.title,
    SUM(cs.spent) as total_spent,
    SUM(cs.views) as total_views,
    SUM(cs.clicks) as total_clicks,
    SUM(cs.orders) as total_orders,
    SUM(cs.revenue) as total_revenue,
    CASE 
        WHEN SUM(cs.spent) > 0 THEN SUM(cs.revenue) / SUM(cs.spent)
        ELSE 0 
    END as roas
FROM campaigns c
JOIN campaign_statistics cs ON cs.campaign_id = c.id
GROUP BY c.id, c.title
ORDER BY total_spent DESC;
```

## 🐛 Отладка

Логи сохраняются в папку `logs/`:
- `ozon_sync_YYYYMMDD.log` - логи синхронизации

Уровень логирования настраивается в `.env`:
```env
LOG_LEVEL=DEBUG  # DEBUG, INFO, WARNING, ERROR
```

## 📚 Полезные ссылки

- [Ozon Seller API Documentation](https://docs.ozon.ru/api/seller/)
- [Ozon Performance API Documentation](https://docs.ozon.ru/api/performance/)
- [API Keys Settings](https://seller.ozon.ru/settings/api-keys)

## 📝 Лицензия

MIT License
