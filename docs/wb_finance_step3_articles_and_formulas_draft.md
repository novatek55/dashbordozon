# WB Finance Report - Этап 3: статьи и формулы (черновик)

Дата: 2026-05-07
Источник данных: `wb_raw_sales_report_details` (пилотная загрузка за 30 дней)

## Фактический профиль данных (пилот)

- Загружено строк: `526`
- Ключи в raw JSON: `87` полей
- По `docTypeName`:
  - пустое значение: `444`
  - `Продажа`: `82`
- `supplierOperName`: в текущей выборке не заполнено (`null`/пусто)

Важно: на текущем объеме данных классификацию нельзя строить только на `supplierOperName`.

## Базовый набор статей WB (v0)

1. `gross_revenue` (Валовая выручка)
2. `marketplace_commission` (Комиссия WB)
3. `logistics_direct` (Прямая логистика/доставка)
4. `logistics_reverse` (Обратная логистика/возвраты)
5. `acquiring` (Эквайринг)
6. `penalties` (Штрафы/удержания)
7. `other_deductions` (Прочие удержания)
8. `to_pay` (Итого к выплате)

## Расчетные формулы (v0, для согласования)

- `gross_revenue` = `SUM(retailAmount)`
- `marketplace_commission` = `SUM(ppvzSalesCommission)` (если поле есть в строке)
- `logistics_direct` = `SUM(deliveryAmount)`
- `logistics_reverse` = `SUM(returnAmount)` или fallback через `docTypeName` + знаки сумм
- `acquiring` = `SUM(acquiringFee)`
- `penalties` = `SUM(penalty)`
- `other_deductions` = `SUM(deduction + additionalPayment)` с учетом знаков
- `to_pay` = `SUM(forPay)` (или `SUM(ppvzForPay)`, выбрать после сверки с ЛК)

## Классификация строк в статью (v0)

Приоритет правил:

1. Если `docTypeName` заполнен и однозначно указывает тип операции -> маппинг по `docTypeName`.
2. Если `docTypeName` пустой -> маппинг по комбинации денежных полей (`deliveryAmount`, `acquiringFee`, `penalty`, `deduction`, `forPay`).
3. Если строка не распознана -> статья `other_deductions` + флаг `unmapped=1`.

## Что сделать перед финальным согласованием

1. Догрузить минимум 90 дней (чтобы поймать больше типов операций).
2. Построить частоты по:
   - `docTypeName`,
   - `supplierOperName`,
   - сочетаниям денежных полей и знаков.
3. Сформировать `wb_dim_article_mapping` (таблица маппинга), версия `v1`.
4. Сверить агрегаты `to_pay` с кабинетом WB по 1-2 неделям.

## Решение по рискам

- Если WB продолжает отдавать `429`, запускать инкрементально несколько раз в день.
- Статус `partial_success` считать нормальным техническим состоянием ETL, если есть прирост `last_rrd_id`.
