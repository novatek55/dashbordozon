# Модуль «Юнитка» — design

**Статус:** draft · **Автор:** Claude (brainstorm) · **Дата:** 2026-04-17

## 1. Цель

Дать инструмент для юнит-экономики «на 1 единицу товара» в трёх режимах:

1. **Факт (В1)** — подгружается из истории продаж (accruals, 30 дней) по нашему артикулу, все поля редактируемые; пользователь меняет цены/комиссии/логистику и видит как меняется Валовая прибыль.
2. **Расчёт (В2)** — копия Факта или с нуля, для «что если»-сценариев. Логистика считается по прайсу (габариты + кластер).
3. **Конкурент (В3)** — через публичный API `calculator.ozon.ru`: по URL/SKU/тексту вытягиваем цену, габариты, комиссии FBO/FBS, считаем нашу экономику + показываем рядом справочные данные калькулятора Ozon.

Результаты не сохраняются — это «стенд» для тестирования гипотез. При F5 всё сбрасывается.

## 2. Общая архитектура

Новая вкладка `Юнитка` в [web/orders_dashboard.html](../../web/orders_dashboard.html), рядом с «Акции» и «Реклама». Всегда 2 блока side-by-side, структура идентичная, источник выбирается селектором в шапке каждого блока.

**Архитектурный паттерн — гибрид:**

- Формула юнит-экономики считается на клиенте (мгновенно на каждый ввод — без server round-trip).
- Тяжёлое — через тонкие серверные эндпоинты: lookup тарифа логистики, загрузка факта из БД, загрузка габаритов через Ozon API, парсинг калькулятора Ozon.
- Прайс логистики (~35 000 строк) **не** тащится на клиент — только нужная строка по запросу.
- [src/economics_engine.py](../../src/economics_engine.py) используется как эталон: есть парный тест, гарантирующий что JS-формула даёт те же цифры, что `calculate()`.

## 3. Данные

### 3.1 Новая таблица `logistics_tariffs`

```python
class LogisticsTariff(Base):
    __tablename__ = "logistics_tariffs"
    id = Column(Integer, primary_key=True)
    cluster_from = Column(String(128), nullable=False, index=True)
    cluster_to = Column(String(128), nullable=False, index=True)
    volume_min_l = Column(Numeric(8,3), nullable=False)
    volume_max_l = Column(Numeric(8,3), nullable=False)
    price_under_300 = Column(Numeric(10,2), nullable=False)
    price_over_300 = Column(Numeric(10,2), nullable=False)
    source_file = Column(String(128))
    imported_at = Column(DateTime, server_default=func.now())
    __table_args__ = (Index('idx_lt_lookup', 'cluster_from', 'cluster_to', 'volume_min_l'),)
```

Заполняется скриптом `src/import_logistics_tariffs.py`: читает xlsx, парсит два листа — «Тарифы на логистику» (пары кластеров) и «Универсальные тарифы» (fallback, пишем с `cluster_from='*'` и `cluster_to='*'`). Перезагрузка — `TRUNCATE + bulk insert`. Формат входа — `logistika-fbo-fbs-*.xlsx` (текущий файл в корне проекта: `logistika-fbo-fbs-06042026_1772454395.xlsx`).

### 3.2 Новая таблица `product_dimensions`

```python
class ProductDimension(Base):
    __tablename__ = "product_dimensions"
    id = Column(Integer, primary_key=True)
    offer_id = Column(String(128), unique=True, nullable=False, index=True)
    sku = Column(BigInteger)
    length_cm = Column(Numeric(8,2))
    width_cm = Column(Numeric(8,2))
    height_cm = Column(Numeric(8,2))
    weight_kg = Column(Numeric(8,3))
    volume_l = Column(Numeric(8,3))   # length*width*height / 1000, вычисляется в коде при INSERT/UPDATE
    source = Column(String(32))       # 'ozon_api_v4' | 'manual' | 'calculator_ozon'
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
```

Заполняется расширением `SyncManager` — метод `sync_product_dimensions()`: батчи по 100 offer_id через `/v4/product/info/list`, парсим `dimensions`. CLI: `python -m src.main --mode dimensions` (регистрируется в `src/main.py`). Разовый прогон при первом запуске + опционально в `scheduler.py`.

## 4. Бэкенд — новые endpoints

Файл `src/dashboard/routes/unitka.py` (по образцу `actions.py`/`advertising.py`), подключение в [src/dashboard/app.py](../../src/dashboard/app.py).

### 4.1 `GET /api/unitka/load-fact?offer_id=X&days=30`

Возвращает:
```json
{
  "offer_id": "215", "title": "адидас с полками", "sku": 12345,
  "base_30d": { /* поля ProductEconomicsBase, сериализованные для UI: on-unit */
    "price_seller": 10520, "price_buyer": 9990,
    "commission_abs": 4495, "commission_pct": 42.7,
    "logistics": 631, "partners": 83, "fbo": 260, "ads": 943,
    "cost": 1150, "tax_pct": 10
  },
  "buyer_to_seller_ratio": 0.95,
  "dimensions": {"length": 30, "width": 20, "height": 10, "weight_kg": 0.5, "volume_l": 6.0}
}
```

Внутри: тот же SQL что в `/api/accruals-comp-by-article`, прогоняем через `base_from_accrual_values()`, делим агрегаты на `ordered_units` для представления «на 1 ед.».

`buyer_to_seller_ratio` — best-effort: если в данных периода есть записи с `promo_compensation > 0` (Ozon-кофинансируемая скидка), считаем как `(revenue_sales − promo_compensation) / revenue_sales`. Если таких записей нет — возвращаем `1.0` и отдельный флаг `ratio_source: 'no_ozon_discount_detected'`, фронт в UI покажет инфо-подсказку у галочки связи цен («Ozon-скидка не обнаружена; укажите вручную, если применимо»).

### 4.2 `GET /api/unitka/logistics-tariff?cluster_from=X&cluster_to=Y&volume_l=2.5&price=10520`

Логика:

1. Нормализуем имена кластеров через `_cluster_alias()` (таблица алиасов в коде: `Мск` → `Москва, МО и Дальние регионы`, `Нск` → `Новосибирск`, `СПб` → `Санкт-Петербург и СЗО`).
2. SELECT одной строки по `(cluster_from, cluster_to, volume_min_l ≤ volume_l ≤ volume_max_l)`.
3. Если нет — fallback на `cluster_from='*' AND cluster_to='*'`. Если и там нет — 404.
4. Берём `price_over_300` если `price > 300`, иначе `price_under_300`.
5. Если `cluster_from != cluster_to` → `cross_cluster_surcharge = round(price * 0.08, 2)`.
6. Ответ: `{ base_tariff: 631, cross_cluster_surcharge: 842, total: 1473, matched_volume_bucket: "2.001-3 л", fallback_used: false }`.

### 4.3 `POST /api/unitka/competitor-lookup` body: `{"query": "..."}`

Проксирует к `calculator.ozon.ru/p-api/the-calculator-ozon-ru/api/item-search`, нормализует ответ. query — URL товара, SKU (число) или произвольный текст-поиск (до 20 товаров). Детали — §7.

### 4.4 `GET /api/unitka/offer-search?q=X`

Autocomplete для дропдауна «артикул» в режиме Факт. Возвращает до 20 offer_id с match'ем по префиксу / substring. Источник — таблица `products` (существующая).

### 4.5 `GET /api/unitka/clusters`

`SELECT DISTINCT cluster_from FROM logistics_tariffs WHERE cluster_from != '*' ORDER BY cluster_from`. Для выпадающих списков кластеров в фронте.

### 4.6 `GET /api/unitka/fetch-dimensions?offer_id=X`

Точечная синхронизация одного артикула (кейс: габариты ещё не успели засинхрониться или были null). Дёргает `/v4/product/info/list`, обновляет `product_dimensions`, возвращает свежую запись.

## 5. Фронтенд

### 5.1 Файловая структура

```
web/unitka/
  unitka.js      # state + rendering + handlers + формула (~400-600 строк)
  unitka.css     # стили блоков
```

HTML-каркас вкладки — inline в [web/orders_dashboard.html](../../web/orders_dashboard.html):

```html
<section id="unitka-screen" class="screen" hidden>
  <div class="unitka-blocks">
    <div class="unitka-block" data-block="A"></div>
    <div class="unitka-block" data-block="B"></div>
  </div>
</section>
```

Инициализация — в навигации дашборда добавляется пункт «Юнитка», его обработчик импортирует `unitka.js` и вызывает `initUnitka()`.

### 5.2 Модель состояния

```js
const state = { A: emptyBlock(), B: emptyBlock() };
function emptyBlock() {
  return {
    source: 'empty',             // 'empty' | 'fact' | 'calc' | 'competitor'
    offerId: null,
    competitorUrl: null,
    title: '',
    cluster: { from: 'Москва, МО и Дальние регионы', to: 'Москва, МО и Дальние регионы' },
    fulfillment: 'fbo',          // 'fbo' | 'fbs' (тумблер, видим только при source='competitor')
    linkPrices: true,            // галочка связи priceBuyer ↔ priceSeller
    buyerToSellerRatio: 0.6,     // 40% дефолт; для fact — берём фактический
    fields: {
      priceBuyer: 0, priceSeller: 0,
      commissionAbs: 0, commissionPct: 0,
      logistics: 0, partners: 0, fbo: 0, ads: 0,
      cost: 0, taxPct: 10,
    },
    derived: { accrued: 0, taxAbs: 0, gross: 0, grossPct: 0 },
    dims: { length: 0, width: 0, height: 0, weight: 0, volumeL: 0 },
    ref: null,                   // справочные данные calculator.ozon.ru (source='competitor')
    loading: false, error: null,
  };
}
```

При открытии вкладки — оба блока `source='empty'`. При F5 — всё сбрасывается, ничего не сохраняем (ни в localStorage, ни на сервере).

### 5.3 Взаимодействия

**Смена источника** (dropdown в шапке блока):

- `fact`: появляется autocomplete-инпут «артикул» → после выбора GET `/api/unitka/load-fact` → заполняется `fields`, `dims`, `buyerToSellerRatio = ratio из ответа`.
- `calc`: выбрать артикул как в `fact`; стартовые значения полей и `buyerToSellerRatio` берутся из того же `/load-fact`, но логистика пересчитывается по прайсу (через `/logistics-tariff`, а не берётся из accruals). Позволяет «моделировать» смену цены/кластера с перерасчётом логистики. Если артикула в факте нет (или юзер снимает артикул) — ratio скатывается к `0.6` (дефолт для ручного ввода).
- `competitor`: поле ввода `URL/SKU/текст` → кнопка «Загрузить» → POST `/api/unitka/competitor-lookup`. Если ответ — список (>1) → показываем выпадашку с карточками (thumbnail + name + price); выбор → заполняет блок. Если 1 товар — сразу заполняет.

**Правка поля** — единая точка `onChange(block, field, value)`:

1. Запись в `state[block].fields[field]`.
2. Связь цен (если `linkPrices=true`):
   - Меняли `priceSeller` → `priceBuyer = priceSeller * buyerToSellerRatio`.
   - Меняли `priceBuyer` → `priceSeller = priceBuyer / buyerToSellerRatio`.
3. Связь комиссии:
   - Меняли `commissionPct` → `commissionAbs = priceSeller * commissionPct / 100`.
   - Меняли `commissionAbs` → `commissionPct = commissionAbs / priceSeller * 100`.
4. `recalcDerived(block)` — пересчёт `accrued`, `taxAbs`, `gross`, `grossPct`.
5. `renderBlock(block)` — rerender одного блока (не обоих: иначе сбивается фокус в input'ах второго).
6. `renderDeltas()` — в правом блоке бейджи `+565` / `−21` для `accrued` и `gross` (относительно левого). Если левый `source='empty'` — дельты скрыты.

**Чипы кластера (Мск↔Мск / Мск↔Нск):**

- Видны всегда. Для `source='fact'` — disabled (логистика берётся из факта и не пересчитывается, tooltip «для Факта логистика фиксирована»).
- Для `calc`/`competitor` — переключает кластер в state, GET `/logistics-tariff` → обновляет `fields.logistics`.
- Если cross-cluster → в подсказке строки «Логистика» показываем разбивку: `631 + 842 (8% cross) = 1 473`.

**Тумблер FBO/FBS (только `source='competitor'`):**

- Переключает `commissionPct` между `fbo_rate` и `fbs_rate`.
- Справочник `ref` показывает соответствующую колонку (FBO или FBS).

### 5.4 Формула (JS)

```js
function recalcDerived(b) {
  const f = state[b].fields;
  const accrued = f.priceSeller - f.commissionAbs - f.logistics - f.partners - f.fbo - f.ads;
  const taxAbs = f.priceSeller * (f.taxPct / 100);
  const gross = accrued - f.cost - taxAbs;
  const grossPct = f.priceSeller ? gross / f.priceSeller * 100 : 0;
  state[b].derived = { accrued, taxAbs, gross, grossPct };
}
```

Проценты для строк-расходов в колонке `%` — `value / priceSeller * 100`.

### 5.5 Вёрстка

Эталон: мокап `.superpowers/brainstorm/2023-*/content/unitka-two-blocks-v2.html` (за этот чат). Ключевые визуальные решения:

- Карточка-блок ~350px, padding 14/16, тень `0 1px 3px rgba(20,30,50,0.06)`.
- Шапка: селектор источника (цвет от выбранного — зелёный для fact, синий для calc, оранжевый для competitor, серый для empty) + инпут названия артикула.
- Tools-строка под шапкой: слева «на 1 ед. · база 30д / N шт», справа чипы кластера.
- Строка «Цена для покупателя» — фиолетовый фон (`#f5f0fa`).
- Строка «Цена продажи (продавцу)» — светло-серый фон.
- Строка «Логистика» — бежевый фон (`#fffaf0`).
- Строка «Начислено» — слабо-серый фон, жирным.
- Итоговая строка «Валовая прибыль» — зелёный фон (`#f3faf6`), жирным, в 13px, сверху зелёная граница.

Все числовые поля редактируемые — `<input class="input-inline">` с моноширинным шрифтом, правое выравнивание, 72px width (44px для `.pct`).

## 6. Логика расчёта

### 6.1 Входные поля

| Поле | Ед. | По умолчанию |
|---|---|---|
| priceBuyer | ₽ | Факт/Calc: priceSeller × ratio (ratio из истории либо 1.0); Competitor: из calculator.ozon.ru; Empty: 0 |
| priceSeller | ₽ | Факт/Calc: revenue/units; Competitor: priceBuyer / ratio (ratio=0.6 дефолт); Empty: 0 |
| commissionPct / commissionAbs | % / ₽ | Факт: из accruals; Competitor: fboCommissionRate или fbsCommissionRate |
| logistics | ₽ | Факт: из accruals (фикс); Calc/Competitor: lookup по прайсу + cross-cluster surcharge |
| partners | ₽ | Факт: agent_services_other |
| fbo | ₽ | Факт: fbo_services / units |
| ads | ₽ | Факт: ad_spend / units |
| cost | ₽ | Факт: material_cost / units; остальное — 0 (юзер вводит) |
| taxPct | % | 10 (УСН-упрощёнка) |
| gross (₽) | ₽ | производное; **также редактируемое** — backsolve на priceSeller (§6.3) |
| grossPct (%) | % | производное; **также редактируемое** — backsolve на priceSeller (§6.3) |

### 6.2 Производные

```
accrued  = priceSeller − commissionAbs − logistics − partners − fbo − ads
taxAbs   = priceSeller × taxPct / 100
gross    = accrued − cost − taxAbs
grossPct = gross / priceSeller × 100
```

### 6.3 Обратный расчёт от Валовой прибыли (goal seek)

Ячейки **Валовая прибыль (₽)** и **Валовая прибыль (%)** — тоже `<input>`. Правка любой из них делает backsolve на `priceSeller`:

```
denom = 1 − commissionPct/100 − taxPct/100

# если меняли gross в ₽
priceSeller = (target_gross + cost + logistics + partners + fbo + ads) / denom

# если меняли grossPct в %
target_gross = priceSeller_current × grossPct / 100   — нет, иначе рекурсия;
решаем через:   gross = priceSeller × grossPct/100
             →  priceSeller × grossPct/100 = priceSeller × denom − (cost+logistics+partners+fbo+ads)
             →  priceSeller = (cost+logistics+partners+fbo+ads) / (denom − grossPct/100)
```

После backsolve:
1. `priceSeller` обновлён.
2. Если `linkPrices=true` → `priceBuyer = priceSeller × buyerToSellerRatio`.
3. Если `commissionAbs` связан через `%` — пересчитывается `commissionAbs = priceSeller × commissionPct/100`.
4. `recalcDerived()` пересчитывает оставшиеся цифры (начислено, налог).

**Защита от невозможных значений:**
- Если `denom ≤ 0` (комиссия + налог ≥ 100%) → backsolve невозможен, UI подсвечивает Валовую красным с тултипом «Комиссия + налог ≥ 100%, снизьте ставки».
- Если результат `priceSeller < 0` (целевая Валовая недостижима при текущих расходах) → показать инфо «Невозможно: фикс-расходы больше выручки при любой цене».
- В режиме `source='fact'` backsolve включён, но логистика фиксирована по факту — пользователь получит достижимую цену при именно этой логистике.

### 6.4 Связь цен

Галочка «🔗 Связать цены» над ценами (дефолт — включена). Правка одной цены пересчитывает другую через `buyerToSellerRatio`. Правка ratio через кнопку-карандаш — инпут «% скидки Ozon», ratio = (100 − pct) / 100.

### 6.5 Связь `buyerToSellerRatio`

Для `source='fact'` и `source='calc'` ratio приходит из `/load-fact` (best-effort из `promo_compensation`; если не определился — `1.0`, UI показывает подсказку «Ozon-скидка не обнаружена»). Для `source='competitor'` и `source='empty'` дефолт `0.6` (скидка 40%).

### 6.6 Объём упаковки

`volumeL = length_cm × width_cm × height_cm / 1000`, округляем до 3 знаков.

### 6.7 Cross-cluster logistics

При `cluster_from != cluster_to`: к базовому тарифу добавляется `priceSeller × 0.08` (last-mile-подобная наценка 8%). Считается на сервере (эндпоинт `/logistics-tariff` возвращает раздельно `base_tariff` и `cross_cluster_surcharge`), фронт складывает и показывает в строке «Логистика».

### 6.8 Парность JS и `economics_engine.calculate`

Контрольный кейс в `tests/test_unitka_formula_parity.py` (данные артикула 215, снапшотные числа). Аналогичный HTML-тест `tests/test_unitka_formula_parity.html` — открыть руками, сверить визуально что JS даёт те же значения.

## 7. Режим Конкурент

### 7.1 Эндпоинт

`POST /api/unitka/competitor-lookup` body: `{"query": "..."}`.

На сервере:

```python
async with aiohttp.ClientSession() as s:
    async with s.post(
        'https://calculator.ozon.ru/p-api/the-calculator-ozon-ru/api/item-search',
        json={'query': query},
        headers={'Content-Type': 'application/json', 'X-O3-App-Name': 'calculator-ui'},
        timeout=15,
    ) as r:
        raw = await r.json()
items = [_map_competitor_item(x) for x in raw]
return web.json_response({'items': items})
```

Без авторизации, без сессий. Если сеть упала / timeout — 502 `{error: "Калькулятор Ozon недоступен"}`.

### 7.2 Маппинг ответа

`_map_competitor_item(x)` → одна позиция:

```json
{
  "sku": "1911445160", "name": "Телевизор Tuvio 4K...", "subtitle": "Tuvio",
  "thumbnail_url": "...",
  "price_buyer": 38626,
  "price_seller_estimated": 64377,     // price_buyer / 0.6
  "weight_kg": 21.7,
  "dimensions": {"length": 161, "width": 16.5, "height": 97.4},
  "volume_l": 258.86,
  "fbo_commission_rate": 0.34,
  "fbs_commission_rate": 0.40,
  "ref": {
    "fbo": {"price": ..., "commission": ..., "commission_pct": ...,
            "acquiring": 147, "processing": 0, "logistics": ...,
            "delivery_pickup": 25, "total_ozon_costs": ..., "net_accrued": ...},
    "fbs": {...}
  }
}
```

Заметка про `ref`: публичный API калькулятора отдаёт только raw-поля товара — не готовый расчёт. Мы сами считаем `ref` по тем же формулам, что и наш блок, и показываем как справочник (чтобы пользователь видел: «если зайти напрямую через витрину, что насчитал бы Ozon»). Это полезно для сверки нашей логики.

Логистика для `ref` считается через ту же функцию lookup (Москва→Москва по умолчанию для ref, независимо от чипа в блоке).

Unknown keys в raw → `logger.warning`, но не падаем — берём что есть.

### 7.3 UI

- Поле ввода URL/SKU/текст + кнопка «Загрузить».
- Если >1 товара — выпадашка с карточками (thumb + name + price), клик → заполняет блок.
- Если 1 — сразу:
  - `priceBuyer = price_buyer`, `priceSeller = price_seller_estimated` (редактируемое);
  - `commissionPct = fbo_commission_rate × 100` по умолчанию (тумблер FBO/FBS переключает);
  - `dims` ← `dimensions, weight_kg, volume_l`;
  - `logistics` ← результат `/logistics-tariff` по текущему кластеру;
  - `cost = 0` (юзер ставит своё);
  - `ref` сохраняется в state.
- Тумблер FBO/FBS над блоком, переключает `commissionPct` + активную колонку `ref`.
- Inline-справка под каждой строкой, которой есть соответствие в `ref`: серый шрифт 10.5px, `ref (FBO): −3 969 ₽ · 27%`. Соответствие строк:
  - Вознаграждение Ozon → `ref.commission`;
  - Партнёры (там эквайринг) → `ref.acquiring`;
  - Логистика → `ref.logistics`;
  - FBO → `ref.processing`.

### 7.4 Что НЕ сохраняем

- Ответы `/competitor-lookup` в БД не пишем.
- Габариты конкурентов в `product_dimensions` не пишем (там только наши offer_id).
- Сессионный кэш на сервере — нет (запрос стоит ~200мс, YAGNI).

## 8. Тестирование

### 8.1 pytest

| Файл | Проверяет |
|---|---|
| `tests/test_import_logistics_tariffs.py` | фикстура-xlsx на 10 строк → корректная запись в БД, парсинг диапазона объёма, алиас универсальных тарифов |
| `tests/test_unitka_logistics_tariff_lookup.py` | lookup по (from, to, volume_l, price) → возвращает ожидаемую строку; fallback на `*/*`; cross-cluster = +8% от `price` |
| `tests/test_unitka_competitor_mapping.py` | fixture `tests/fixtures/competitor_response.json` → `_map_competitor_item` даёт ожидаемые поля; unknown keys не ломают маппинг |
| `tests/test_unitka_formula_parity.py` | снапшот: `ProductEconomicsBase` артикула 215 → `calculate()` даёт ожидаемые `gross`, `accrued`, `gross_pct` |
| `tests/test_unitka_load_fact.py` | мок-БД с accruals → `/api/unitka/load-fact?offer_id=215` возвращает нормализованную структуру с полями на 1 ед. |

### 8.2 Ручной JS-тест

`tests/test_unitka_formula_parity.html` — статика, открывается в браузере, гоняет JS-формулу на тех же числах что Python-тест, выводит результат. Сверяем визуально при правке формулы.

### 8.3 Смоук-чеклист (после деплоя)

1. Открыть вкладку «Юнитка» — 2 пустых блока.
2. Блок А: `source=Факт` → выбрать артикул 215 → цифры совпадают с отчётом «Акции» за 30д.
3. Блок Б: `source=Расчёт` → выбрать 215, изменить цену продажи → Валовая прибыль и дельта пересчитались.
4. Чип «Мск→Нск» в блоке Б → логистика +8% от цены, tooltip показывает разбивку.
5. Блок Б: `source=Конкурент` → вставить URL `https://www.ozon.ru/product/1911445160` → поля заполнились; inline-справка FBO видна под строкой Вознаграждение.
6. Тумблер FBO/FBS в блоке Б → комиссия переключилась, справка показывает FBS.
7. Дельты Начислено/Валовая отображаются в правом блоке с правильным знаком.

## 9. Milestones

| # | Содержание | DoD |
|---|---|---|
| M1 | Миграция (`logistics_tariffs`, `product_dimensions`), импорт прайса, синк габаритов, CLI `--mode dimensions` | Прайс залит, габариты засинхронены, все pytest M1 зелёные |
| M2 | `src/dashboard/routes/unitka.py` с 5 эндпоинтами + тесты | Все pytest M2 зелёные; эндпоинты доступны через curl |
| M3 | Вкладка «Юнитка», `unitka.js`/`.css`, `source=Пусто + Факт` + дельты | Сценарии 1–3 смоука работают |
| M4 | Режимы Calc и Competitor, калькулятор логистики с чипами, inline-справка FBO/FBS | Сценарии 4–6 смоука работают |
| M5 | Связь цен, tooltip разбивки логистики, «Загрузить габариты» для пустых, полировка | Чеклист смоука пройден целиком |

## 10. Риски и митигации

| Риск | Митигация |
|---|---|
| API `calculator.ozon.ru` меняет формат | Unknown keys логируем в warning; fixture-тест `test_unitka_competitor_mapping` красит сборку; маппинг чиним в одном месте |
| Алиасы кластеров в прайсе не бьются с UI | `_cluster_alias()` — словарь алиасов в `unitka.py`; тест на неизвестный алиас (fallback на `*/*`) |
| `dimensions` у Ozon API бывают null | Source='manual' в `product_dimensions`; UI показывает предупреждение «габариты не загружены» + кнопку ручной синхронизации |
| JS-формула расходится с `economics_engine` | Тест-парность (`test_unitka_formula_parity`); при падении — чиним до релиза |
| Прайс устареет | Скрипт импорта перезаливает `TRUNCATE + INSERT`; пользователь запускает вручную по мере обновления тарифов Ozon |

## 11. Что НЕ входит в scope (YAGNI)

- Сохранение сценариев в БД / share-ссылки / история правок.
- Авторасчёт оптимальной цены (оптимизация Валовой).
- Больше 2 блоков одновременно.
- Batch-сравнение N товаров.
- Deep-link «Открыть в Юнитке» из SKU-диагностики / Акций.
- Сохранение карточек конкурентов.
- Мультитенантность, undo/redo.

## 12. Связанные файлы

- Основа эконом-движка: [src/economics_engine.py](../../src/economics_engine.py)
- Текущий эндпоинт accruals: `get_accruals_comp_by_article` в [src/dashboard/routes/finance.py](../../src/dashboard/routes/finance.py)
- Похожий по структуре отчёт: [src/dashboard/routes/actions.py](../../src/dashboard/routes/actions.py) (образец для `unitka.py`)
- Прайс логистики: `logistika-fbo-fbs-06042026_1772454395.xlsx` в корне проекта
- Дашборд-каркас: [web/orders_dashboard.html](../../web/orders_dashboard.html)
- Ozon-клиент: [src/ozon_client.py](../../src/ozon_client.py)
- Sync-оркестратор: [src/sync_manager.py](../../src/sync_manager.py)
- Мокап для вёрстки (не в гите): `.superpowers/brainstorm/*/content/unitka-two-blocks-v2.html`
