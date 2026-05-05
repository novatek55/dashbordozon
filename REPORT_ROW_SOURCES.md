# Источники строк Finance Report

Ниже зафиксировано, как **сейчас** собирается каждая строка отчета в [orders_dashboard.py](/E:/скрипты%20OZ/ozonapi/orders_dashboard.py#L1258).

## Базовые таблицы и поля

| Таблица | Поля |
|---|---|
| `transactions` | `operation_date`, `operation_type`, `posting_number`, `description`, `amount`, `raw_data` |
| `fact_orders` | `posting_number`, `status`, `created_at` |
| `fact_order_items` | `posting_number`, `offer_id`, `sku`, `quantity` |
| `posting_transaction_snapshots` | `posting_number`, `response_json`, `requested_at` |
| `report_compensation_items` | `effective_date`, `article_name`, `amount` |
| `returns` | `posting_number`, `offer_id`, `sku`, `quantity`, `returned_at` |
| `returns_fbo` | `posting_number`, `offer_id`, `sku`, `quantity`, `returned_at` |
| `finance_article_costs` | `article`, `sku`, `unit_cost` |
| `finance_month_plan` | `month_start`, `revenue_plan` |

## Общие правила

- Все строки месяца сначала собираются по дням.
- Основной источник денег: `transactions`.
- Для продажи `"Доставка покупателю"` используются:
  - `transactions.raw_data.accruals_for_sale`
  - `transactions.raw_data.sale_commission`
  - `transactions.raw_data.services[*].name`
  - `transactions.raw_data.services[*].price`
- В продажу попадают только отправления, которые:
  - есть в `fact_orders`
  - имеют `fact_orders.status = 'Доставлен'`
  - отсутствуют в `returns` и `returns_fbo`
- Для количества и себестоимости по продаже используются:
  - сначала `fact_order_items.offer_id/sku/quantity`
  - если пусто, то `transactions.raw_data.items`
  - если и там пусто, то `posting_transaction_snapshots.response_json.items`
- Для себестоимости используется `finance_article_costs.unit_cost`, поиск по `sku`, затем по `article/offer_id`.

## Строки отчета

| Строка | Как считается сейчас | Таблица | Поля |
|---|---|---|---|
| `Озон` | Секция, без расчета | - | - |
| `Выручка план` | Накопительный план по месяцу | `finance_month_plan` | `revenue_plan`; если записи нет, берется `PLAN_BASE_VALUES["revenue_mp"]` |
| `заказано` | Сумма `quantity` по доставленным и не возвращенным отправлениям из `"Доставка покупателю"` | `fact_order_items`, fallback `transactions.raw_data.items`, fallback `posting_transaction_snapshots` | `quantity`, `posting_number`, `response_json.items` |
| `Выручка накопительно` | Накопительная сумма строки `выручка / продажи` | производная | `revenue_sales` |
| `выручка / продажи` | `выручка - Возврат выручки` | производная | `revenue - returns_revenue` |
| `выручка` | По `"Доставка покупателю"` добавляется `max(raw_data.accruals_for_sale, 0)` | `transactions` | `description`, `raw_data.accruals_for_sale`, `posting_number`, `operation_date` |
| `Возвраты` | Равна строке `Возврат выручки` | производная | `returns_revenue` |
| `Возврат выручки` | По `"Получение возврата, отмены, невыкупа от покупателя"` добавляется `abs(min(raw_data.accruals_for_sale, 0))` | `transactions` | `description`, `raw_data.accruals_for_sale`, `operation_date` |
| `Вознаграждение Ozon` | `Вознаграждение за продажу - Возврат вознаграждения` | производная | `sale_commission - return_commission` |
| `Вознаграждение за продажу` | По `"Доставка покупателю"` добавляется `abs(min(raw_data.sale_commission, 0))` | `transactions` | `description`, `raw_data.sale_commission`, `posting_number`, `operation_date` |
| `Возврат вознаграждения` | По возврату добавляется `max(raw_data.sale_commission, 0)` | `transactions` | `description`, `raw_data.sale_commission`, `operation_date` |
| `Услуги доставки` | Сумма 6 строк доставки ниже | производная | `pickup_processing + courier_departure + dropoff_processing + logistics + reverse_logistics + pickup_courier_delivery` |
| `Обработка отправления Pick-up` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Организация выезда курьера` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Обработка отправления Drop-off` | Сервисы `MarketplaceServiceItemDropoffPVZ` и `MarketplaceServiceItemDropoffSC` внутри `"Доставка покупателю"` | `transactions.raw_data.services` | `name`, `price` |
| `Логистика` | Сервис `MarketplaceServiceItemDirectFlowLogistic` внутри `"Доставка покупателю"` | `transactions.raw_data.services` | `name`, `price` |
| `Обратная логистика` | По `"Доставка и обработка возврата, отмены, невыкупа"`: сумма сервисов `MarketplaceServiceItemReturnFlowLogistic` и остаток `abs(amount) - matched_total` | `transactions`, `transactions.raw_data.services` | `description`, `amount`, `services.name`, `services.price` |
| `Доставка курьером Pick-up` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Услуги агентов` | Сумма 6 агентских строк ниже | производная | `partner_returns_processing + star_products + temporary_partner_storage + partner_dropoff_processing + delivery_to_pickup + acquiring` |
| `Обработка возвратов, отмен и невыкупов партнёрами` | Сервис `MarketplaceServiceItemRedistributionReturnsPVZ` | `transactions.raw_data.services` | `name`, `price` |
| `Звёздные товары` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Временное размещение товара партнерами` | Сервис `MarketplaceServiceItemTemporaryStorageRedistribution` или строка `description == "Временное размещение товара партнерами"` | `transactions`, `transactions.raw_data.services` | `description`, `amount`, `services.name`, `services.price` |
| `Обработка отправления Drop-off партнёрами (АПВЗ)` | Сервис `MarketplaceServiceItemRedistributionDropOffApvz` | `transactions.raw_data.services` | `name`, `price` |
| `Доставка до места выдачи` | Сервис `MarketplaceServiceItemRedistributionLastMileCourier` | `transactions.raw_data.services` | `name`, `price` |
| `Эквайринг` | Либо `description == "Оплата эквайринга"`, либо сервис `MarketplaceRedistributionOfAcquiringOperation` | `transactions`, `transactions.raw_data.services` | `amount`, `services.name`, `services.price` |
| `Услуги FBO` | Сумма 7 FBO-строк ниже | производная | `cross_docking + valid_preparation + ozon_delivery_to_pvz + warehouse_placement + piece_acceptance + zone_sorting + excess_processing` |
| `Кросс-докинг` | `description == "Кросс-докинг"` | `transactions` | `description`, `amount` |
| `Подготовка товара к вывозу: Валид` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Вывоз товара со склада силами Ozon: Доставка до ПВЗ` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Размещение товаров на складах Ozon` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Обработка товара в составе грузоместа: Поштучная приёмка` | `description.startswith("Обработка товара в составе грузоместа на FBO")` | `transactions` | `description`, `amount` |
| `Обработка товара в составе грузоместа: Сортировка по зонам размещения` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Обработка опознанных излишков в составе грузоместа` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Продвижение и реклама` | Сумма 9 строк продвижения ниже | производная | `seller_bonus_mailing + seller_bonus + premium_subscription + premium_plus_subscription + pay_per_click + review_pin + top_search + internet_ads + review_points` |
| `Бонусы продавца - рассылка` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Бонусы продавца` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Подписка Premium` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Подписка Premium Plus` | `description == "Подписка Premium Plus"` | `transactions` | `description`, `amount` |
| `Оплата за клик` | `description == "Оплата за клик"` | `transactions` | `description`, `amount` |
| `Закрепление отзыва` | `description == "Закрепление отзыва"` | `transactions` | `description`, `amount` |
| `Вывод в топ` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Реклама в сети интернет на сайте` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Баллы за отзыв` | `description == "Баллы за отзывы"` | `transactions` | `description`, `amount` |
| `Другие услуги` | Сейчас это сумма блока прочих строк ниже, а не "хвост" из `transactions` | производная | `operational_errors + temporary_sc_storage + utilization + loans_factoring + other_accrual_adjustments + shortage_retention + compensations` |
| `Расходы маркетплейса (все)` | `Возврат выручки + Вознаграждение Ozon + Услуги доставки + Услуги агентов + Услуги FBO + Продвижение и реклама + Другие услуги` | производная | сумма указанных строк |
| `Обработка операционных ошибок продавца` | `description.startswith("Обработка операционных ошибок продавца")` или `description.startswith("Жалобы покупателей")` | `transactions` | `description`, `amount` |
| `Временное размещение товара в СЦ/ПВЗ` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Утилизация товаров` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Займы и факторинг` | Сейчас не заполняется, всегда `0` | - | Нет маппинга |
| `Прочие начисления - Корректировка стоимости услуг` | Из `report_compensation_items`, если в `article_name` есть `"корректировк"` и `"услуг"` | `report_compensation_items` | `effective_date`, `article_name`, `amount` |
| `Удержание за недовложение товара` | Из `report_compensation_items`, если в `article_name` есть `"недовлож"` | `report_compensation_items` | `effective_date`, `article_name`, `amount` |
| `Компенсации и декомпенсации` | Все прочие строки из `report_compensation_items`; строки `transactions` с `"компенсац"` и `"потеря по вине ozon"` пропускаются специально | `report_compensation_items` | `effective_date`, `article_name`, `amount` |
| `Расходы МП` | `Вознаграждение Ozon + Услуги доставки + Услуги агентов + Услуги FBO + Продвижение и реклама + Другие услуги` | производная | сумма указанных строк |
| `Расходы МП, %` | `Расходы МП / выручка / продажи` | производная | `marketplace_expenses / revenue_sales` |
| `Маркетинг, %` | `(Оплата за клик + Закрепление отзыва + Вывод в топ + Реклама в сети интернет на сайте) / выручка / продажи` | производная | сумма указанных строк / `revenue_sales` |
| `Начислено` | `выручка / продажи - Возвраты - Расходы МП` | производная | `revenue_sales - returns_total - marketplace_expenses` |
| `Себестоимость` | По продажам: `quantity * finance_article_costs.unit_cost`; по возвратам та же сумма вычитается со знаком `-` | `fact_order_items`, `transactions.raw_data.items`, `posting_transaction_snapshots`, `returns`, `returns_fbo`, `finance_article_costs` | `quantity`, `offer_id`, `sku`, `returned_at`, `unit_cost` |
| `Валовая прибыль` | `Начислено - Себестоимость` | производная | `accrued - material_cost` |
| `Валовая прибыль, % к OZ` | `Валовая прибыль / выручка / продажи` | производная | `gross_profit / revenue_sales` |
| `Валовая прибыль, % к РС` | `Валовая прибыль / Начислено` | производная | `gross_profit / accrued` |
| `Валовая накопительно` | Накопительная сумма `Валовая прибыль` | производная | `gross_profit` |
| `Валовая план` | Накопительный план от `PLAN_BASE_VALUES["gross_profit"]`, масштабированный к месячному плану выручки | `finance_month_plan` + код | `revenue_plan`, `PLAN_BASE_VALUES["gross_profit"]` |

## Что особенно важно проверить

- `выручка` сейчас берется только из `transactions.raw_data.accruals_for_sale`, а не из структуры `Баланс магазина`.
- `Вознаграждение Ozon` сейчас считается только как `sale_commission - return_commission`, без части сервисов/fee из `Баланс магазина`.
- Много строк пока не заполняются вообще и всегда равны `0`.
- `Другие услуги` в коде сначала может получать хвостовые суммы из `transactions`, но потом полностью перезаписывается формулой из компенсаций и прочих блоков.
- Компенсации из `transactions` намеренно игнорируются, приоритет отдан `report_compensation_items`.
