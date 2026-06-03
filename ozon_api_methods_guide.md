# Ozon Seller API - Сжатая инструкция по всем методам

**Базовый URL:** `https://api-seller.ozon.ru`

**Авторизация:** Заголовки `Client-Id` + `Api-Key`

---

## 📦 ТОВАРЫ (Products)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v3/product/list` | Список товаров |
| `POST` | `/v2/product/info` | Информация о товаре |
| `POST` | `/v1/product/info/list` | Информация о нескольких товарах |
| `POST` | `/v2/product/import` | Создать/обновить товары (до 100 шт) |
| `POST` | `/v1/product/import/info` | Статус импорта |
| `POST` | `/v4/product/info/prices` | Цены товаров |
| `POST` | `/v4/product/info/stocks` | Остатки товаров |
| `POST` | `/v2/products/stocks` | Обновить остатки |
| `POST` | `/v1/product/promo/deactivate` | Отключить товар от промо |
| `POST` | `/v1/product/attributes/upload` | Загрузить характеристики |

### Пример запроса списка товаров:
```json
{
  "filter": {"visibility": "ALL"},
  "last_id": "",
  "limit": 100
}
```

---

## 📋 ОТПРАВЛЕНИЯ / ЗАКАЗЫ (Postings)

### FBS (Fulfillment by Seller)
| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v3/posting/fbs/list` | Список отправлений |
| `POST` | `/v3/posting/fbs/get` | Информация об отправлении |
| `POST` | `/v3/posting/fbs/unfulfilled/list` | Необработанные отправления |
| `POST` | `/v4/posting/fbs/ship` | Собрать заказ |
| `POST` | `/v4/posting/fbs/ship/package` | Частичная сборка |
| `POST` | `/v2/posting/fbs/awaiting-delivery` | Передать к отгрузке |
| `POST` | `/v2/posting/fbs/arbitration` | Открыть спор |
| `POST` | `/v2/posting/fbs/cancel-reason/list` | Причины отмены |
| `POST` | `/v2/posting/fbs/package-label` | Этикетка |
| `POST` | `/v2/posting/fbs/act/create` | Создать акт отгрузки |
| `POST` | `/v2/posting/fbs/act/get-pdf` | Получить акт PDF |
| `POST` | `/v2/posting/fbs/get-by-barcode` | Найти по штрихкоду |
| `POST` | `/v1/posting/fbs/pick-up-code/verify` | Проверить код курьера |
| `POST` | `/v1/posting/fbs/timeslot/set` | Перенести доставку |
| `POST` | `/v3/posting/multiboxqty/set` | Количество коробок |

### FBO (Fulfillment by Ozon)
| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v2/posting/fbo/list` | Список отправлений FBO |

### rFBS (real FBS)
| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v2/fbs/posting/delivered` | Статус «Доставлено» |
| `POST` | `/v2/fbs/posting/delivering` | Статус «Доставляется» |
| `POST` | `/v2/fbs/posting/last-mile` | Статус «Последняя миля» |
| `POST` | `/v2/fbs/posting/sent-by-seller` | Статус «Отправлено продавцом» |
| `POST` | `/v2/fbs/posting/tracking-number/set` | Добавить трек-номер |
| `POST` | `/v1/posting/cutoff/set` | Уточнить дату отгрузки |

---

## 💰 ФИНАНСЫ (Finance)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v3/finance/transaction/list` | Список транзакций |
| `POST` | `/v3/finance/transaction/totals` | Суммы транзакций |
| `POST` | `/v2/finance/realization` | Отчёт о реализации |
| `POST` | `/v1/finance/realization` | Отчёт о реализации (v1) |
| `POST` | `/v1/finance/cash-flow-statement/list` | Финансовый отчёт |
| `POST` | `/v1/finance/document-b2b-sales` | Реестр продаж юрлицам |
| `POST` | `/v1/finance/mutual-settlement` | Отчёт о взаиморасчётах |

### Пример запроса транзакций:
```json
{
  "date": {
    "from": "2024-01-01T00:00:00.000Z",
    "to": "2024-01-31T23:59:59.000Z"
  },
  "operation_type": ["OperationAgentDeliveredToCustomer"],
  "page": 1,
  "page_size": 1000
}
```

---

## 🔄 ВОЗВРАТЫ (Returns)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v3/returns/company/fbs` | Список возвратов FBS |
| `POST` | `/v3/returns/company/fbo` | Список возвратов FBO |
| `POST` | `/v1/return/giveout/barcode-reset` | Новый штрихкод возврата |

### rFBS Возвраты
| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v2/returns/rfbs/list` | Список заявок на возврат |
| `POST` | `/v2/returns/rfbs/get` | Информация о возврате |
| `POST` | `/v2/returns/rfbs/verify` | Одобрить возврат |
| `POST` | `/v2/returns/rfbs/reject` | Отклонить возврат |
| `POST` | `/v2/returns/rfbs/receive-return` | Подтвердить получение |
| `POST` | `/v2/returns/rfbs/return-money` | Вернуть деньги |
| `POST` | `/v2/returns/rfbs/compensate` | Компенсировать часть |

---

## 📊 АНАЛИТИКА (Analytics)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/analytics/data` | Данные аналитики |
| `POST` | `/v1/analytics/product-queries` | Запросы моих товаров (summary) |
| `POST` | `/v1/analytics/product-queries/details` | Запросы моих товаров (details) |
| `POST` | `/v1/analytics/stocks` | Актуальная аналитика остатков по SKU (замена stock_on_warehouses) |
| `POST` | `/v2/analytics/stock_on_warehouses` | Устаревающий метод, в будущем будет отключён |
| `POST` | `/v1/analytics/turnover/stocks` | Оборачиваемость |
| `POST` | `/v1/analytics/manage/stocks` | Управление остатками |

### Пример запроса аналитики:
```json
{
  "date_from": "2024-01-01",
  "date_to": "2024-01-31",
  "metrics": ["ordered_units", "revenue", "delivered_units"],
  "dimension": ["sku", "day"],
  "limit": 1000,
  "offset": 0
}
```

---

## 🏭 СКЛАДЫ И ПОСТАВКИ (Warehouses & Supplies)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/delivery-method/list` | ~~Методы доставки~~ (OBSOLETE) |
| `POST` | `/v2/delivery-method/list` | Методы доставки (v2, limit обязателен) |
| `POST` | `/v1/warehouse/fbo/list` | Поиск складов FBO (требует search + filter_by_supply_type:[1,2]) |
| `POST` | `/v1/warehouse/fbo/seller/list` | Склады продавца FBO/FBS (рабочий) |
| `POST` | `/v1/cluster/list` | Кластеры и склады |
| `POST` | `/v1/draft/create` | Создать черновик поставки |
| `POST` | `/v1/draft/supply/create` | Создать заявку на поставку |
| `POST` | `/v1/draft/timeslot/info` | Доступные таймслоты |
| `POST` | `/v1/supply-order/cancel` | Отменить заявку |
| `POST` | `/v1/supply-order/cancel/status` | Статус отмены |

---

## 🚚 ОТГРУЗКИ / ПЕРЕВОЗКИ (Carriage)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/carriage/create` | Создать отгрузку |
| `POST` | `/v1/carriage/approve` | Подтвердить отгрузку |
| `POST` | `/v1/carriage/cancel` | Отменить отгрузку |
| `POST` | `/v1/carriage/delivery/list` | Список методов доставки |
| `POST` | `/v1/carriage/set-postings` | Изменить состав отгрузки |
| `POST` | `/v1/carriage/pass/create` | Создать пропуск |
| `POST` | `/v1/carriage/pass/update` | Обновить пропуск |
| `POST` | `/v1/carriage/pass/delete` | Удалить пропуск |

---

## 💬 ЧАТЫ (Chat)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/chat/list` | Список чатов |
| `POST` | `/v1/chat/history` | История сообщений |
| `POST` | `/v1/chat/send` | Отправить сообщение |
| `POST` | `/v1/chat/updates` | Обновления чатов |
| `POST` | `/v1/chat/read` | Отметить прочитанным |

---

## ⭐ ОТЗЫВЫ (Reviews)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/review/list` | Список отзывов |
| `POST` | `/v1/review/info` | Информация об отзыве |
| `POST` | `/v1/review/count` | Количество отзывов |
| `POST` | `/v1/review/change-status` | Изменить статус |
| `POST` | `/v1/review/comment/list` | Комментарии к отзыву |
| `POST` | `/v1/review/comment/create` | Ответить на отзыв |
| `POST` | `/v1/review/comment/delete` | Удалить ответ |

---

## ❓ ВОПРОСЫ (Q&A)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/question/list` | Список вопросов |
| `POST` | `/v1/question/info` | Информация о вопросе |
| `POST` | `/v1/question/count` | Количество вопросов |
| `POST` | `/v1/question/change_status` | Изменить статус |
| `POST` | `/v1/question/answer/create` | Ответить на вопрос |
| `POST` | `/v1/question/answer/list` | Список ответов |
| `POST` | `/v1/question/answer/delete` | Удалить ответ |
| `POST` | `/v1/question/top_sku` | Товары с вопросами |

---

## 📄 ОТЧЁТЫ (Reports)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/report/products/create` | Отчёт по товарам |
| `POST` | `/v1/report/postings/create` | Отчёт об отправлениях |
| `POST` | `/v2/report/returns/create` | Отчёт о возвратах |
| `POST` | `/v1/report/discounted/create` | Отчёт об уценённых |
| `POST` | `/v1/report/warehouse/stock` | Остатки на FBS-складе |
| `POST` | `/v1/report/list` | Список отчётов |
| `POST` | `/v1/report/info` | Информация об отчёте |

---

## 🏷️ ШТРИХКОДЫ (Barcodes)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/barcode/add` | Привязать штрихкод |
| `POST` | `/v1/barcode/generate` | Сгенерировать штрихкод |

---

## 🔖 МАРКИРОВКА (Marks/Честный ЗНАК)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v5/fbs/posting/product/exemplar/create-or-get` | Получить экземпляры |
| `POST` | `/v5/fbs/posting/product/exemplar/set` | Сохранить экземпляры |
| `POST` | `/v4/fbs/posting/product/exemplar/validate` | Валидация марок |
| `POST` | `/v4/fbs/posting/product/exemplar/status` | Статус добавления |

---

## ⭐ РЕЙТИНГ ПРОДАВЦА (Seller Rating)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/rating/summary` | Текущий рейтинг |
| `POST` | `/v1/rating/history` | История рейтинга |

---

## 🎁 АКЦИИ И ПРОМО (Promo)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/actions` | Список акций |
| `POST` | `/v1/actions/candidates` | Товары-кандидаты |
| `POST` | `/v1/actions/products` | Товары в акции |
| `POST` | `/v1/actions/activate` | Активировать акцию |
| `POST` | `/v1/actions/deactivate` | Деактивировать акцию |
| `POST` | `/v1/product/action/timer/status` | Статус таймера цены |
| `POST` | `/v1/product/action/timer/update` | Обновить таймер |

---

## 🔐 СЕРТИФИКАТЫ (Certificates)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/certification/list` | Список сертификатов |
| `POST` | `/v1/certification/confirm` | Подтвердить сертификат |
| `POST` | `/v1/brand/company-certification/list` | Бренды для сертификации |

---

## 📦 КВАНТЫ (Quants)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/quant/ship` | Собрать квант |
| `POST` | `/v1/quant/status` | Статус кванта |

---

## 🗺️ ПОЛИГОНЫ (Polygons)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/polygon/bind` | Привязать полигон |

---

## 📋 СЧЕТА-ФАКТУРЫ (Invoices)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v2/invoice/create-or-update` | Создать/обновить счёт |
| `POST` | `/v2/invoice/get` | Получить счёт |
| `POST` | `/v1/invoice/delete` | Удалить счёт |
| `POST` | `/v1/invoice/file/upload` | Загрузить файл |

---

## 🚫 ОТМЕНЫ (Cancellations)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/conditional-cancellation/approve` | Подтвердить отмену |
| `POST` | `/v1/conditional-cancellation/list` | Список заявок на отмену |

---

## 🌐 ГЛОБАЛЬНАЯ ДОСТАВКА (Global)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/v1/posting/global/etgb` | Таможенные декларации |

---

## 📊 PERFORMANCE API (Реклама)

**Базовый URL:** `https://api-performance.ozon.ru`

**Авторизация:** OAuth2 (Bearer token)

### Кампании

| Метод | URL | Описание |
|-------|-----|----------|
| `GET` | `/api/client/campaign` | Список кампаний |
| `GET` | `/api/client/campaign/{id}` | Информация о кампании |
| `GET` | `/api/client/campaign/{id}/objects` | Объекты кампании |
| `POST` | `/api/client/campaign/{id}/activate` | Активировать |
| `POST` | `/api/client/campaign/{id}/deactivate` | Деактивировать |
| `PATCH` | `/api/client/campaign/{id}` | Изменить параметры кампании |
| `POST` | `/api/client/campaign/cpc/v2/product` | Создать кампанию (CPC) |
| `POST` | `/api/client/campaign/{id}/products` | Добавить товары в кампанию |
| `PUT` | `/api/client/campaign/{id}/products` | Обновить ставки товаров |

### Статистика (асинхронные отчёты)

Асинхронный флоу: запрос → UUID → поллинг статуса → скачивание.  
Лимит: **1 активный запрос** одновременно.

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/api/client/statistics` | Запросить отчёт по кампании (CSV) |
| `POST` | `/api/client/statistics/json` | Запросить отчёт по кампании (JSON) |
| `GET` | `/api/client/statistics/{UUID}` | Статус отчёта (state: NOT_STARTED/IN_PROGRESS/OK/ERROR) |
| `GET` | `/api/client/statistics/report?UUID=` | Скачать готовый отчёт (CSV/JSON) |
| `POST` | `/api/client/statistics/video` | Статистика видеобаннеров (CSV) |
| `POST` | `/api/client/statistics/video/json` | Статистика видеобаннеров (JSON) |
| `POST` | `/api/client/statistics/attribution` | Отчёт по заказам для баннеров |
| `POST` | `/api/client/statistics/attribution/json` | Отчёт по заказам (JSON) |
| `POST` | `/api/client/statistics/phrases` | Отчёт по поисковым фразам |
| `POST` | `/api/client/statistics/phrases/json` | Отчёт по поисковым фразам (JSON) |
| `GET` | `/api/client/statistics/list` | Список отчётов (из интерфейса) |
| `GET` | `/api/client/statistics/externallist` | Список отчётов (из API) |

### Синхронные отчёты (CSV, без UUID)

| Метод | URL | Описание |
|-------|-----|----------|
| `GET` | `/api/client/statistics/campaign/product` | Статистика CPC-кампаний |
| `GET` | `/api/client/statistics/campaign/product/json` | Статистика CPC-кампаний (JSON) |
| `GET` | `/api/client/statistics/campaign/media` | Статистика медийных кампаний |
| `GET` | `/api/client/statistics/campaign/media/json` | Статистика медийных кампаний (JSON) |
| `GET` | `/api/client/statistics/expense` | Расход по кампаниям |
| `GET` | `/api/client/statistics/expense/json` | Расход по кампаниям (JSON) |
| `GET` | `/api/client/statistics/daily` | Дневная статистика |
| `GET` | `/api/client/statistics/daily/json` | Дневная статистика (JSON) |

### Оплата за заказ

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/api/client/statistic/orders/generate` | Отчёт по заказам (выбранные товары) |
| `POST` | `/api/client/statistic/products/generate` | Отчёт по товарам (выбранные товары) |
| `GET` | `/api/client/statistics/all_sku_promo/orders/generate` | Отчёт по заказам (все товары) |
| `GET` | `/api/client/statistics/all_sku_promo/products/generate` | Отчёт по товарам (все товары) |

### Ключевые слова

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/api/client/keyword` | Список фраз |
| `PUT` | `/api/client/keyword/{id}/bid` | Изменить ставку |

### Параметры запроса статистики (`/api/client/statistics[/json]`)

```json
{
  "campaigns": ["12345", "67890"],  // Array of strings (uint64)!
  "dateFrom": "2026-03-01",         // ГГГГ-ММ-ДД
  "dateTo": "2026-04-01",           // макс. период 62 дня
  "groupBy": "DATE"                 // NO_GROUP_BY | DATE | START_OF_WEEK | START_OF_MONTH
}
```

---

## 🔧 ПОЛЕЗНЫЕ ЗАГОЛОВКИ

```http
Client-Id: 12345
Api-Key: your_api_key_here
Content-Type: application/json
```

---

## 📏 ЛИМИТЫ API

| Тип | Лимит |
|-----|-------|
| Товары (список) | 1000 за запрос |
| Отправления | 1000 за запрос |
| Транзакции | 1000 за запрос |
| Импорт товаров | 100 за запрос |
| Частота запросов | 20/сек (рекомендуется) |

---

## 🔗 ССЫЛКИ

- Документация: https://docs.ozon.ru/api/seller/
- Performance API: https://docs.ozon.ru/api/performance/
- Личный кабинет: https://seller.ozon.ru/
