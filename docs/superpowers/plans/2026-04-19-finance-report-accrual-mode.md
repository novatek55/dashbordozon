# Finance Report — Accrual Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить режим accrual в финансовый отчёт — P&L по уже размещённым заказам текущего месяца, с досчётом ожидаемых расходов из SKU-level начислений за 30 дней и коррекцией на % отмен/невыкупа.

**Architecture:** Отдельный пункт в селекторе отчётов фронтенда (`finance_report_accrual`) с собственным URL `/api/finance-report-accrual`. Отдельный сервисный модуль рассчёта, переиспользующий утилиты build_rows_map_for_month и render_finance_rows, чтобы вернуть ту же структуру `{rows, days, kpi_summary, notes}` и чтобы UI-рендер отчёта работал без изменений.

**Tech Stack:** Python 3 + aiohttp + asyncpg; PostgreSQL (таблицы `fact_orders`, `fact_order_items`, `accruals_comp_by_article_*` источники); Vanilla JS во фронтенде.

---

## File Structure

**Create:**
- `src/services/finance_report_accrual.py` — модуль расчёта accrual-режима
- `tests/services/test_finance_report_accrual.py` — unit-тесты расчётной логики

**Modify:**
- `src/dashboard/routes/finance.py` — новый хэндлер `get_finance_report_accrual`
- `src/dashboard/app.py` — регистрация роутов/импорт
- `web/orders_dashboard.html` — новый option в dropdown + маршрутизация

---

### Task 1: Каркас сервиса accrual + тест базового контракта

**Files:**
- Create: `src/services/finance_report_accrual.py`
- Test: `tests/services/test_finance_report_accrual.py`

- [ ] **Step 1: Написать падающий тест контракта**

```python
# tests/services/test_finance_report_accrual.py
import pytest
from src.services.finance_report_accrual import get_finance_report_accrual_data


@pytest.mark.asyncio
async def test_accrual_report_returns_expected_keys(sample_conn_with_orders):
    """Контракт: возвращаемая структура совместима с UI finance_report."""
    data = await get_finance_report_accrual_data(sample_conn_with_orders, "2026-04")
    assert set(data.keys()) >= {"month", "days", "rows", "notes", "variant"}
    assert data["variant"] == "accrual"
    assert data["month"] == "2026-04"
    assert isinstance(data["days"], list) and data["days"]
    assert isinstance(data["rows"], list)
```

- [ ] **Step 2: Запустить тест — убедиться что падает**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.services.finance_report_accrual'`)

- [ ] **Step 3: Минимальная реализация для зелёного теста**

```python
# src/services/finance_report_accrual.py
"""Finance Report в режиме accrual: P&L по уже размещённым заказам месяца
с досчётом ожидаемых расходов из 30д начислений по SKU и коррекцией на
% отмен/невыкупа.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict

import asyncpg

from src.services.report_services import (
    build_rows_map_for_month,
    render_finance_rows,
    month_bounds,
    finance_report_notes,
)


ACCRUAL_NOTES = [
    "Режим accrual: расходы на уже размещённые заказы месяца досчитываются по SKU-level начислениям за последние 30 дней.",
    "Прогноз выкупов = заказы × (1 − % отмен SKU) × (1 − % невыкупа SKU) за 30д.",
]


async def get_finance_report_accrual_data(
    conn: asyncpg.Connection,
    month_value: str,
) -> Dict[str, Any]:
    month_bounds(month_value)  # валидация
    rows_map, days = await build_rows_map_for_month(conn, month_value)
    return {
        "month": month_value,
        "days": days,
        "rows": render_finance_rows(rows_map, days),
        "notes": finance_report_notes() + ACCRUAL_NOTES,
        "variant": "accrual",
    }
```

- [ ] **Step 4: Запустить тест — убедиться что проходит**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/services/finance_report_accrual.py tests/services/test_finance_report_accrual.py
git commit -m "feat(finance-accrual): каркас сервиса финотчёта в режиме accrual"
```

---

### Task 2: Выгрузка заказов месяца по SKU (FBO+FBS)

**Files:**
- Modify: `src/services/finance_report_accrual.py`
- Test: `tests/services/test_finance_report_accrual.py`

**Контекст:** Нужно получить заказы текущего месяца из `fact_order_items` + `fact_orders`, сгруппировать по (sku/offer_id, day), сохранить количество и валовую выручку. Исключить только явно cancelled; остальные (в пути, доставлен, возвращён) включить — % потерь применим на следующем этапе.

- [ ] **Step 1: Падающий тест**

```python
# tests/services/test_finance_report_accrual.py
@pytest.mark.asyncio
async def test_load_orders_for_month_groups_by_offer_day(sample_conn_with_orders):
    from src.services.finance_report_accrual import load_orders_for_month
    rows = await load_orders_for_month(sample_conn_with_orders, "2026-04")
    # Фикстура: 2 заказа 2026-04-05 по offer_id='AAA' по 2 шт на 500 и 3 шт на 500
    bucket = [r for r in rows if r["offer_id"] == "aaa" and r["day"] == "2026-04-05"]
    assert len(bucket) == 1
    assert bucket[0]["quantity"] == 5
    assert bucket[0]["gross_revenue"] == pytest.approx(2500.0)
```

- [ ] **Step 2: Запустить — FAIL (нет функции)**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py::test_load_orders_for_month_groups_by_offer_day -v`
Expected: FAIL

- [ ] **Step 3: Реализация**

```python
# в src/services/finance_report_accrual.py
from typing import List

# Статусы, которые считаем «потенциально оплачиваемыми» (исключаем cancelled)
ORDER_STATUSES_INCLUDE = (
    "delivered", "delivering", "awaiting_deliver", "awaiting_packaging",
    "acceptance_in_progress", "sent_by_seller", "not_accepted",
    "arbitration", "driver_pickup", "returned",
)

async def load_orders_for_month(
    conn: asyncpg.Connection,
    month_value: str,
) -> List[Dict[str, Any]]:
    """Заказы месяца по (offer_id, day) — quantity и gross_revenue."""
    rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g') AS offer_id,
            to_char(o.created_at AT TIME ZONE 'Europe/Moscow', 'YYYY-MM-DD') AS day,
            sum(coalesce(oi.quantity, 0))::float8 AS quantity,
            sum(coalesce(oi.quantity, 0) * coalesce(oi.price, 0))::float8 AS gross_revenue
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE to_char(o.created_at AT TIME ZONE 'Europe/Moscow', 'YYYY-MM') = $1
          AND coalesce(lower(o.status), '') = ANY($2::text[])
          AND coalesce(lower(o.status), '') <> 'cancelled'
        GROUP BY 1, 2
        HAVING regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g') <> ''
        """,
        month_value,
        list(ORDER_STATUSES_INCLUDE),
    )
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Запустить — PASS**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py::test_load_orders_for_month_groups_by_offer_day -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/services/finance_report_accrual.py tests/services/test_finance_report_accrual.py
git commit -m "feat(finance-accrual): выгрузка заказов месяца по SKU из fact_order_items"
```

---

### Task 3: Коэффициенты потерь (отмены/невыкупы) по SKU за 30 дней

**Files:**
- Modify: `src/services/finance_report_accrual.py`
- Test: `tests/services/test_finance_report_accrual.py`

**Контекст:** Для каждого SKU вычислить `cancel_rate = cancelled_qty / total_qty` и `buyout_loss_rate = returned_qty / delivered_qty` за последние 30 дней. При недостатке данных (<5 заказов) — fallback на общий средний по той же схеме (FBO/FBS). Итоговый `paid_out_rate = (1 − cancel_rate) × (1 − buyout_loss_rate)`.

- [ ] **Step 1: Падающий тест**

```python
# tests/services/test_finance_report_accrual.py
@pytest.mark.asyncio
async def test_compute_loss_rates_per_sku_with_fallback(sample_conn_with_history):
    from src.services.finance_report_accrual import compute_loss_rates
    rates = await compute_loss_rates(sample_conn_with_history, reference_date="2026-04-19")
    # Фикстура: offer 'AAA' за 30д — 10 создано, 1 cancelled, 8 delivered, 1 returned
    assert rates["per_sku"]["aaa"]["cancel_rate"] == pytest.approx(0.1)
    assert rates["per_sku"]["aaa"]["buyout_loss_rate"] == pytest.approx(1/8)
    # offer 'RARE' — 2 заказа, fallback на схему
    assert rates["per_sku"]["rare"]["source"] == "fallback_scheme"
```

- [ ] **Step 2: Запустить — FAIL**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py::test_compute_loss_rates_per_sku_with_fallback -v`
Expected: FAIL

- [ ] **Step 3: Реализация**

```python
# в src/services/finance_report_accrual.py
MIN_SKU_SAMPLE = 5  # ниже этого — fallback на средний по схеме

async def compute_loss_rates(
    conn: asyncpg.Connection,
    reference_date: str,
) -> Dict[str, Any]:
    """Возвращает {per_sku: {offer_id: {cancel_rate, buyout_loss_rate, source}},
    per_scheme: {fbo: {...}, fbs: {...}}} за 30 дней до reference_date."""
    rows = await conn.fetch(
        """
        WITH window AS (
            SELECT
                regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g') AS offer_id,
                lower(coalesce(o.tpl_integration_type, o.delivery_schema, 'unknown')) AS scheme_raw,
                lower(coalesce(o.status, '')) AS status,
                coalesce(oi.quantity, 0) AS qty
            FROM fact_order_items oi
            JOIN fact_orders o ON o.order_id = oi.order_id
            WHERE o.created_at >= ($1::date - interval '30 days')
              AND o.created_at <  ($1::date + interval '1 day')
        )
        SELECT
            offer_id,
            CASE WHEN scheme_raw LIKE '%fbs%' THEN 'fbs' ELSE 'fbo' END AS scheme,
            sum(qty) FILTER (WHERE status <> '') AS total_qty,
            sum(qty) FILTER (WHERE status = 'cancelled') AS cancelled_qty,
            sum(qty) FILTER (WHERE status = 'delivered') AS delivered_qty,
            sum(qty) FILTER (WHERE status = 'returned') AS returned_qty
        FROM window
        GROUP BY 1, 2
        """,
        reference_date,
    )

    per_sku: Dict[str, Dict[str, Any]] = {}
    scheme_totals: Dict[str, Dict[str, float]] = {
        "fbo": {"total": 0.0, "cancelled": 0.0, "delivered": 0.0, "returned": 0.0},
        "fbs": {"total": 0.0, "cancelled": 0.0, "delivered": 0.0, "returned": 0.0},
    }
    for r in rows:
        offer = r["offer_id"] or ""
        if not offer:
            continue
        scheme = r["scheme"]
        total = float(r["total_qty"] or 0)
        cancelled = float(r["cancelled_qty"] or 0)
        delivered = float(r["delivered_qty"] or 0)
        returned = float(r["returned_qty"] or 0)
        scheme_totals[scheme]["total"] += total
        scheme_totals[scheme]["cancelled"] += cancelled
        scheme_totals[scheme]["delivered"] += delivered
        scheme_totals[scheme]["returned"] += returned
        if total >= MIN_SKU_SAMPLE:
            per_sku[offer] = {
                "cancel_rate": _safe_rate(cancelled, total),
                "buyout_loss_rate": _safe_rate(returned, delivered),
                "scheme": scheme,
                "source": "sku",
            }

    per_scheme: Dict[str, Dict[str, float]] = {}
    for scheme, t in scheme_totals.items():
        per_scheme[scheme] = {
            "cancel_rate": _safe_rate(t["cancelled"], t["total"]),
            "buyout_loss_rate": _safe_rate(t["returned"], t["delivered"]),
        }

    # Проставляем fallback для SKU ниже порога — им вернём scheme-rate на этапе расчёта
    # Здесь помечаем в per_sku явно, чтобы тест видел source
    for r in rows:
        offer = r["offer_id"] or ""
        if offer and offer not in per_sku:
            scheme = r["scheme"]
            per_sku[offer] = {
                "cancel_rate": per_scheme[scheme]["cancel_rate"],
                "buyout_loss_rate": per_scheme[scheme]["buyout_loss_rate"],
                "scheme": scheme,
                "source": "fallback_scheme",
            }

    return {"per_sku": per_sku, "per_scheme": per_scheme}


def _safe_rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))
```

- [ ] **Step 4: Запустить — PASS**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/services/finance_report_accrual.py tests/services/test_finance_report_accrual.py
git commit -m "feat(finance-accrual): расчёт % отмен и невыкупа по SKU с fallback на схему"
```

---

### Task 4: Удельные расходы на 1 выкупленный заказ из accruals_comp_by_article

**Files:**
- Modify: `src/services/finance_report_accrual.py`
- Test: `tests/services/test_finance_report_accrual.py`

**Контекст:** Переиспользовать уже работающий source — `get_accruals_comp_by_article_data` ([src/dashboard/routes/finance.py:1001](src/dashboard/routes/finance.py#L1001)) или его внутренний запрос. За последние 30 дней по каждому SKU посчитать сумму начислений по каждой статье расходов и разделить на количество выкупленных за тот же период заказов. Получаем удельные коэффициенты: `commission_per_unit`, `logistics_per_unit`, `last_mile_per_unit`, `acquiring_per_unit`, `returns_per_unit`, `other_per_unit`.

**Важно:** Статьи расходов брать из справочника, используемого в `aggregate_recent_30d_totals` ([src/services/report_services.py:204](src/services/report_services.py#L204)). Он уже знает мэппинг `accrual_type → row_key`.

- [ ] **Step 1: Падающий тест**

```python
# tests/services/test_finance_report_accrual.py
@pytest.mark.asyncio
async def test_compute_unit_costs_per_sku(sample_conn_with_accruals):
    from src.services.finance_report_accrual import compute_unit_costs
    unit = await compute_unit_costs(sample_conn_with_accruals, reference_date="2026-04-19")
    # Фикстура: offer 'AAA' — 100 выкупленных ед за 30д, комиссия -5000 руб
    assert unit["per_sku"]["aaa"]["sale_commission"] == pytest.approx(-50.0)
    # fallback среднего по всем SKU для редких
    assert "_fallback" in unit
```

- [ ] **Step 2: FAIL**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py::test_compute_unit_costs_per_sku -v`
Expected: FAIL

- [ ] **Step 3: Реализация**

```python
# в src/services/finance_report_accrual.py
# Статьи-расходы, которые пересчитываем в удельные (подмножество FINANCE_REPORT_ROWS).
# Валидировать список по src/services/report_services.py FINANCE_REPORT_ROWS перед коммитом.
UNIT_COST_KEYS = (
    "sale_commission", "return_commission",
    "courier_departure", "dropoff_processing", "logistics", "reverse_logistics",
    "pickup_courier_delivery", "pickup_processing",
    "acquiring", "delivery_to_pickup", "partner_returns_processing",
    "partner_dropoff_processing", "partner_packaging", "temporary_partner_storage",
    "star_products",
    "piece_acceptance", "zone_sorting", "excess_processing",
    "cross_docking", "warehouse_placement", "valid_preparation", "ozon_delivery_to_pvz",
    "utilization", "packaging_materials", "operational_errors", "temporary_sc_storage",
    "penalty_non_recommended_slot",
)


async def compute_unit_costs(
    conn: asyncpg.Connection,
    reference_date: str,
) -> Dict[str, Any]:
    """Удельные расходы на 1 выкупленный заказ по SKU за 30 дней.
    Использует тот же источник, что и /api/accruals-comp-by-article.
    """
    # Источник: функция get_accruals_comp_by_article_data — вернуть её выход и
    # перераспределить по offer_id. Если функция не публичная — скопировать
    # SQL-запрос внутрь модуля. Проверить её сигнатуру в
    # src/dashboard/routes/finance.py:1001 перед выбором пути.
    from src.dashboard.routes.finance import build_accruals_comp_by_article_query  # при отсутствии — см. fallback ниже

    rows = await conn.fetch(
        build_accruals_comp_by_article_query(),
        reference_date,  # подставить интервал [-30d, reference_date]
    )

    # Параллельно — выкупленные количества за тот же период (delivered)
    units = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g') AS offer_id,
            sum(coalesce(oi.quantity, 0))::float8 AS delivered_qty
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE o.created_at >= ($1::date - interval '30 days')
          AND o.created_at <  ($1::date + interval '1 day')
          AND lower(coalesce(o.status, '')) = 'delivered'
        GROUP BY 1
        HAVING regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g') <> ''
        """,
        reference_date,
    )
    units_map: Dict[str, float] = {r["offer_id"]: float(r["delivered_qty"] or 0) for r in units}

    per_sku: Dict[str, Dict[str, float]] = {}
    totals: Dict[str, float] = {k: 0.0 for k in UNIT_COST_KEYS}
    total_units = 0.0
    for r in rows:
        offer = r["offer_id"]
        qty = units_map.get(offer, 0.0)
        if qty <= 0:
            continue
        per_sku.setdefault(offer, {})
        for key in UNIT_COST_KEYS:
            amount = float(r.get(key, 0.0) or 0.0)
            per_sku[offer][key] = amount / qty
            totals[key] += amount
            # total_units прибавляем один раз ниже
        total_units += qty

    fallback = {k: (totals[k] / total_units if total_units > 0 else 0.0) for k in UNIT_COST_KEYS}
    return {"per_sku": per_sku, "_fallback": fallback}
```

**Важно:** Если `build_accruals_comp_by_article_query` / аналогичной публичной утилиты нет — нужно перенести SQL из `get_accruals_comp_by_article` ([src/dashboard/routes/finance.py:1001](src/dashboard/routes/finance.py#L1001)) в модуль/хелпер без изменения поведения существующего эндпоинта. Если SQL слишком тесно связан с запросом — переписать свой аналогичный запрос, берущий суммы по offer_id из `archive_posting_transactions` / `accruals_comp_by_article` (проверить фактический источник — см. `src/archive_posting_transactions.py` и существующий эндпоинт).

- [ ] **Step 4: PASS**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py::test_compute_unit_costs_per_sku -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add src/services/finance_report_accrual.py tests/services/test_finance_report_accrual.py
git commit -m "feat(finance-accrual): удельные расходы на ед. по SKU из начислений 30д"
```

---

### Task 5: Сборка rows_map для accrual-режима

**Files:**
- Modify: `src/services/finance_report_accrual.py`
- Test: `tests/services/test_finance_report_accrual.py`

**Контекст:** Собрать финальный `rows_map` в формате, который принимает `render_finance_rows` (см. [src/services/report_services.py:99](src/services/report_services.py#L99) и структуру формул на [src/services/report_services.py:540-610](src/services/report_services.py#L540-L610)). Для каждого дня месяца и каждого SKU:
1. `expected_units = orders_qty × (1 − cancel_rate) × (1 − buyout_loss_rate)`
2. `revenue` = `gross_revenue × (1 − cancel_rate) × (1 − buyout_loss_rate)` (только выкупленные)
3. По каждой статье расходов: `amount = expected_units × unit_cost_per_sku` (fallback — _fallback)
4. `material_cost` = `expected_units × cost_price` (источник `cost_price` — взять из `report_products_items` или другой таблицы, использовать тот же источник, что UI диагностики SKU; проверить в `src/dashboard/routes/finance.py` как получается cost для KPI).
5. Реклама (`pay_per_click`, `review_points`, `premium_plus_subscription`) — **факт за период**, не трогаем, получаем стандартным `build_rows_map_for_month` и копируем поле как есть.
6. Вызвать те же формулы-агрегаты, что в [src/services/report_services.py:540-610](src/services/report_services.py#L540-L610), чтобы получить `ozon_fee_total`, `marketplace_expenses`, `accrued`, `gross_profit`, `gross_profit_pct_*`.

- [ ] **Step 1: Падающий тест e2e-контракта**

```python
# tests/services/test_finance_report_accrual.py
@pytest.mark.asyncio
async def test_accrual_report_row_keys_match_finance_v1(sample_conn_full):
    from src.services.finance_report_accrual import get_finance_report_accrual_data
    data = await get_finance_report_accrual_data(sample_conn_full, "2026-04")
    row_keys = {r["key"] for r in data["rows"]}
    required = {
        "revenue", "revenue_sales", "sale_commission", "ozon_fee_total",
        "delivery_services_total", "marketplace_expenses",
        "accrued", "material_cost", "gross_profit",
        "gross_profit_pct_oz", "gross_profit_pct_accrued",
    }
    assert required <= row_keys
    gross = next(r for r in data["rows"] if r["key"] == "gross_profit")
    assert isinstance(gross["total"], (int, float))
```

- [ ] **Step 2: FAIL (метрики пока совпадают с v1 cash — формально тест пройдёт на заглушке; до шага 3 обязательно добавить ассерт расхождения с v1)**

Добавь дополнительный ассерт, что в accrual `revenue` отличается от v1 cash при той же фикстуре (иначе caricature test):

```python
    from src.services.report_services import get_finance_report_data
    cash = await get_finance_report_data(sample_conn_full, "2026-04")
    cash_revenue = next(r for r in cash["rows"] if r["key"] == "revenue")["total"]
    accrual_revenue = next(r for r in data["rows"] if r["key"] == "revenue")["total"]
    assert accrual_revenue != cash_revenue  # разные базы — значения должны расходиться
```

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py::test_accrual_report_row_keys_match_finance_v1 -v`
Expected: FAIL (на заглушке revenue = cash revenue из build_rows_map_for_month)

- [ ] **Step 3: Полная реализация**

Заменить заглушку `get_finance_report_accrual_data` на реальную сборку:

```python
# src/services/finance_report_accrual.py — заменить целиком функцию
from datetime import datetime
from src.services.report_services import (
    build_rows_map_for_month, render_finance_rows, month_bounds,
    finance_report_notes, FINANCE_REPORT_ROWS, recalculate_row_total,
    set_row_from_formula, safe_divide,
)


async def get_finance_report_accrual_data(
    conn: asyncpg.Connection,
    month_value: str,
) -> Dict[str, Any]:
    month_bounds(month_value)

    # 1. Базовый rows_map (возьмём структуру/календарь и пустые value-строки)
    cash_rows_map, days = await build_rows_map_for_month(conn, month_value)

    # 2. Исходные данные
    ref_date = datetime.utcnow().date().isoformat()
    orders = await load_orders_for_month(conn, month_value)
    rates = await compute_loss_rates(conn, ref_date)
    unit_costs = await compute_unit_costs(conn, ref_date)
    cost_prices = await load_cost_prices(conn)  # {offer_id: cost_price}

    # 3. Строим свежий rows_map: копируем рекламу из cash (факт) + пересчитываем всё остальное
    rows_map = _init_rows_map(days)
    _copy_advertising_from_cash(rows_map, cash_rows_map, days)

    for row in orders:
        offer = row["offer_id"]
        day = row["day"]
        if day not in rows_map["revenue"]["daily"]:
            continue
        sku_rate = rates["per_sku"].get(offer) or {
            "cancel_rate": rates["per_scheme"]["fbo"]["cancel_rate"],
            "buyout_loss_rate": rates["per_scheme"]["fbo"]["buyout_loss_rate"],
        }
        paid_factor = (1.0 - sku_rate["cancel_rate"]) * (1.0 - sku_rate["buyout_loss_rate"])
        expected_units = row["quantity"] * paid_factor
        expected_revenue = row["gross_revenue"] * paid_factor

        rows_map["revenue"]["daily"][day] += expected_revenue

        sku_unit = unit_costs["per_sku"].get(offer, unit_costs["_fallback"])
        for key in UNIT_COST_KEYS:
            rows_map[key]["daily"][day] += expected_units * sku_unit.get(key, 0.0)

        cost_price = cost_prices.get(offer, 0.0)
        rows_map["material_cost"]["daily"][day] += expected_units * cost_price

    # 4. Пересчитать totals value-строк
    for row_key, row in rows_map.items():
        if row["kind"] == "value":
            recalculate_row_total(row, days)

    # 5. Применить те же формулы, что v1 — см. report_services.py:540-610
    _apply_finance_formulas(rows_map, days)

    notes = finance_report_notes() + ACCRUAL_NOTES
    return {
        "month": month_value,
        "days": days,
        "rows": render_finance_rows(rows_map, days),
        "notes": notes,
        "variant": "accrual",
    }


def _init_rows_map(days):
    rows_map = {}
    for row in FINANCE_REPORT_ROWS:
        rows_map[row["key"]] = {
            "kind": row["kind"],
            "daily": {day: 0.0 for day in days},
            "total": 0.0,
        }
    return rows_map


def _copy_advertising_from_cash(rows_map, cash_rows_map, days):
    for key in ("pay_per_click", "review_points", "premium_plus_subscription"):
        for day in days:
            rows_map[key]["daily"][day] = cash_rows_map[key]["daily"].get(day, 0.0)
        rows_map[key]["total"] = sum(rows_map[key]["daily"].values())


def _apply_finance_formulas(rows_map, days):
    """Точная копия финальных формул v1: build_rows_map_for_month:540-610."""
    # ВАЖНО: экстрагировать в общий хелпер в report_services.py (рефакторинг v1),
    # либо продублировать. См. оригинал — не изобретать новые формулы.
    # Псевдокод:
    set_row_from_formula(rows_map, "sales_total", days,
        lambda day: rows_map["revenue"]["daily"][day] - rows_map["returns_revenue"]["daily"][day])
    set_row_from_formula(rows_map, "revenue_sales", days,
        lambda day: rows_map["sales_total"]["daily"][day])
    # ... остальные формулы — см. report_services.py:540-610


async def load_cost_prices(conn) -> Dict[str, float]:
    rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(offer_id, ''))), '\\s+', ' ', 'g') AS offer_id,
            max(cost_price)::float8 AS cost_price
        FROM report_products_items
        WHERE cost_price IS NOT NULL
        GROUP BY 1
        """
    )
    return {r["offer_id"]: float(r["cost_price"] or 0.0) for r in rows}
```

**Примечание по формулам:** Список формул в `_apply_finance_formulas` должен точно копировать строки 540–610 из [src/services/report_services.py](src/services/report_services.py). Чтобы не плодить дубликат — **вынести** этот блок в публичный хелпер `apply_finance_formulas(rows_map, days)` в `report_services.py` и вызвать его в обоих местах (в `build_rows_map_for_month` и в `_apply_finance_formulas`).

- [ ] **Step 4: PASS (все тесты)**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/services/test_finance_report_accrual.py -v`
Expected: все PASS

- [ ] **Step 5: Коммит**

```bash
git add src/services/finance_report_accrual.py src/services/report_services.py tests/services/test_finance_report_accrual.py
git commit -m "feat(finance-accrual): полный расчёт rows_map с прогнозом выкупов и удельных расходов"
```

---

### Task 6: HTTP endpoint + регистрация роута

**Files:**
- Modify: `src/dashboard/routes/finance.py:965`
- Modify: `src/dashboard/app.py:89` (добавить роут)

- [ ] **Step 1: Добавить хэндлер**

```python
# src/dashboard/routes/finance.py — добавить ниже get_finance_report_v2 (около строки 998)
async def get_finance_report_accrual(request: web.Request) -> web.Response:
    month_value = (request.query.get("month") or "").strip()
    if not month_value:
        month_value = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        month_bounds(month_value)
    except ValueError:
        return web.json_response({"error": "Invalid month format, expected YYYY-MM"}, status=400)
    from src.services.finance_report_accrual import get_finance_report_accrual_data
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        data = await get_finance_report_accrual_data(conn, month_value)
    return web.json_response(data)
```

- [ ] **Step 2: Зарегистрировать роут в app.py**

```python
# src/dashboard/app.py — в импорте:
from src.dashboard.routes.finance import (
    get_cash_flow, get_finance_report, get_finance_report_v2,
    get_finance_report_accrual,  # NEW
    get_accruals_comp_by_article, ensure_finance_report_tables,
    ...
)
# ниже в регистрации роутов (около строки 89):
app.router.add_get("/api/finance-report", get_finance_report)
app.router.add_get("/api/finance-report-v2", get_finance_report_v2)
app.router.add_get("/api/finance-report-accrual", get_finance_report_accrual)  # NEW
```

- [ ] **Step 3: E2E проверка через реальный сервер**

```bash
# поднять дашборд (run_dashboard.cmd) либо переиспользовать уже запущенный
PYTHONIOENCODING=utf-8 python -c "import urllib.request, json; r=urllib.request.urlopen('http://localhost:5005/api/finance-report-accrual?month=2026-04'); d=json.loads(r.read()); print('variant:', d['variant']); print('rows:', len(d['rows'])); print('gross_profit total:', next(x for x in d['rows'] if x['key']=='gross_profit')['total'])"
```

Expected: `variant: accrual`, rows > 0, `gross_profit total:` — число (не error).

- [ ] **Step 4: Коммит**

```bash
git add src/dashboard/routes/finance.py src/dashboard/app.py
git commit -m "feat(finance-accrual): endpoint /api/finance-report-accrual"
```

---

### Task 7: UI — новый пункт в селекторе отчётов

**Files:**
- Modify: `web/orders_dashboard.html:1303` (добавить option), `:2749` (URL mapping), `:13434+` (рендер ветки)

**Контекст:** Фронтенд уже умеет рендерить `finance_report` через ту же таблицу — accrual-режим возвращает совместимую структуру, так что дополнительная логика рендера не нужна. Нужно только:
1. Добавить `<option value="finance_report_accrual">Finance Report (Accrual)</option>` в селектор.
2. Зарегистрировать URL `/api/finance-report-accrual` в URL-map.
3. Переиспользовать тот же ветвлящийся код, что для `finance_report` — но обновить условия `reportType.value === "finance_report"` на множество `["finance_report", "finance_report_v2", "finance_report_accrual"].includes(...)` там, где логика должна действовать для всех трёх.

- [ ] **Step 1: Добавить option в селектор**

```html
<!-- web/orders_dashboard.html — около строки 1303 -->
        <option value="finance_report">Finance Report</option>
        <option value="finance_report_v2">Finance Report V2</option>
        <option value="finance_report_accrual">Finance Report (Accrual)</option>
```

- [ ] **Step 2: URL mapping**

```js
// около строки 2749
      finance_report: "/api/finance-report",
      finance_report_v2: "/api/finance-report-v2",
      finance_report_accrual: "/api/finance-report-accrual",  // NEW
```

- [ ] **Step 3: Включить accrual во все «финансовые» ветки**

Во всех местах, где есть условия вида `reportType.value === "finance_report" || reportType.value === "finance_report_v2"` (строки 12064, 13434, 13459, 13489, 13734, 13742, 13748, 13753, 13758), добавить `|| reportType.value === "finance_report_accrual"`. 

Также в
```js
// около строки 2709
      finance_report: [],
      finance_report_v2: [],
      finance_report_accrual: [],  // NEW
```

- [ ] **Step 4: Проверка в браузере**

1. Запустить дашборд: `run_dashboard.cmd`
2. Открыть в браузере страницу дашборда
3. Выбрать в селекторе "Finance Report (Accrual)"
4. Месяц = текущий (2026-04)
5. Убедиться: таблица рендерится, строки `Выручка МП`, `Валовая прибыль` содержат числа, отличные от режима `Finance Report`
6. Проверить что на notes появляется строка "Режим accrual: расходы...".

- [ ] **Step 5: Коммит**

```bash
git add web/orders_dashboard.html
git commit -m "feat(finance-accrual): UI — опция Finance Report (Accrual) в селекторе отчётов"
```

---

## Self-Review Checklist

- [ ] Все 7 задач покрывают разделы дизайна: база заказов (T2), коэффициенты потерь (T3), удельные расходы (T4), сборка rows_map (T5), endpoint (T6), UI (T7).
- [ ] Нет "TBD" / "implement later".
- [ ] Имена функций консистентны: `get_finance_report_accrual_data`, `load_orders_for_month`, `compute_loss_rates`, `compute_unit_costs`, `load_cost_prices`, `_apply_finance_formulas`.
- [ ] Все файловые пути абсолютные относительно корня репо.
- [ ] E2E-проверка через реальный endpoint (T6 Step 3 + T7 Step 4) — в соответствии с правилом проекта «Тестировать фикс end-to-end».
- [ ] Реклама остаётся фактической (не модельной) — см. T5 `_copy_advertising_from_cash`.
- [ ] Формулы totals не дублируются — Task 5 предписывает вынести блок 540–610 из `report_services.py` в общий хелпер `apply_finance_formulas`.

## Known Unknowns для исполнителя

Перед началом Task 4 проверить фактическую схему `accruals_comp_by_article` в БД и сигнатуру `get_accruals_comp_by_article_data` — если функция не экспортирует переиспользуемый SQL, скопировать запрос в модуль. Скоростной способ — `grep -n "accruals_comp_by_article" src/` и открыть файл, где идёт SQL.

Перед Task 5 убедиться, что `FINANCE_REPORT_ROWS`, `recalculate_row_total`, `set_row_from_formula`, `safe_divide` действительно экспортируются из `src.services.report_services`; если нет — добавить их в `__all__` или убрать `_` префикс.

Перед Task 3 проверить, есть ли у `fact_orders` колонка `tpl_integration_type` или `delivery_schema`, которая различает FBO/FBS; если нет — взять схему из `posting_number`-поля или маппинга в `fact_orders` (`schema` / `shipment_type`). Скорректировать SQL под реальное имя.
