# Monthly AI Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Генерировать MD-файл с данными Ozon за календарный месяц через endpoint `/api/monthly-report?month=YYYY-MM` и CLI `python -m src.export_monthly_report --month YYYY-MM`.

**Architecture:** Новый модуль `src/services/monthly_report.py` содержит всю логику сборки MD-строки. Он работает напрямую с `asyncpg.Connection`, переиспользует `build_rows_map_for_month` и `load_stock_forecast_inputs` из существующих модулей, остальные данные берёт прямыми SQL-запросами. Endpoint в `src/dashboard/routes/report.py` принимает запрос, вызывает сервис, отдаёт MD-файл. CLI-скрипт делает то же самое без веб-сервера.

**Tech Stack:** Python 3.11+, asyncpg, aiohttp, asyncio

---

## Файловая карта

| Файл | Действие | Ответственность |
|---|---|---|
| `src/services/monthly_report.py` | Создать | Вся логика сборки MD: секции 1–5 |
| `src/dashboard/routes/report.py` | Создать | HTTP endpoint `GET /api/monthly-report` |
| `src/export_monthly_report.py` | Создать | CLI точка входа `python -m` |
| `src/dashboard/app.py` | Изменить | Зарегистрировать маршрут |

---

## Task 1: Сервис — шапка и магазин-итого (Секция 1)

**Files:**
- Create: `src/services/monthly_report.py`

- [ ] **Step 1: Создать файл с заготовкой и функцией шапки**

```python
# src/services/monthly_report.py
"""Сборщик ежемесячного MD-отчёта для ИИ-анализа."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.dashboard.constants import MSK
from src.dashboard.helpers import month_bounds, safe_divide, to_asyncpg_dsn
from src.dashboard.routes.finance import build_rows_map_for_month
from src.services.report_services import load_stock_forecast_inputs
from src.config import settings


def _fmt_rub(value: Any) -> str:
    """Форматирует число как рубли с 2 знаками."""
    try:
        return f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_pct(value: Any) -> str:
    """Форматирует долю (0.0–1.0) как процент с 1 знаком."""
    try:
        return f"{float(value or 0) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _month_dates(month_value: str) -> Tuple[date, date]:
    """Возвращает (первый день, последний день) месяца включительно."""
    year, month = int(month_value[:4]), int(month_value[5:7])
    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return first, last


def _build_header(month_value: str) -> str:
    first, last = _month_dates(month_value)
    generated = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")
    return (
        f"# Ozon Monthly Report — {month_value}\n"
        f"Generated: {generated}  \n"
        f"Period: {first} — {last}\n\n"
    )
```

- [ ] **Step 2: Добавить функцию секции 1 — магазин-итого**

Добавить в конец `src/services/monthly_report.py`:

```python
async def _build_shop_summary(conn: asyncpg.Connection, month_value: str) -> str:
    rows_map, days = await build_rows_map_for_month(conn, month_value)

    def total(key: str) -> float:
        return float(rows_map.get(key, {}).get("total") or 0)

    revenue = total("revenue")
    returns_rev = total("returns_revenue")
    net_revenue = revenue - returns_rev
    commission = total("ozon_fee_total")
    logistics = total("delivery_services_total")
    ads = total("promotion_total")
    other_mp = total("agent_services_total")
    total_mp_exp = total("marketplace_expenses")
    gross_profit = total("gross_profit")

    # Заказы и возвраты из fact_orders
    first, last = _month_dates(month_value)
    first_utc = datetime(first.year, first.month, first.day, tzinfo=MSK).astimezone(timezone.utc)
    last_utc = datetime(last.year, last.month, last.day + 1 if last.day < 28 else 1,
                        tzinfo=MSK).astimezone(timezone.utc)
    # Используем корректный конец месяца
    if last.month == 12:
        end_utc = datetime(last.year + 1, 1, 1, tzinfo=MSK).astimezone(timezone.utc)
    else:
        end_utc = datetime(last.year, last.month + 1, 1, tzinfo=MSK).astimezone(timezone.utc)

    order_row = await conn.fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE lower(coalesce(status,'')) IN (
                'delivered','delivering','awaiting_deliver','awaiting_packaging',
                'driver_pickup','доставлен','доставляется','ожидает в пвз',
                'у водителя','ожидает отгрузки','ожидает сборки'
            )) AS orders_cnt,
            count(*) FILTER (WHERE lower(coalesce(status,'')) IN (
                'cancelled','отменён','отменен'
            )) AS cancelled_cnt
        FROM fact_orders
        WHERE created_at >= $1 AND created_at < $2
        """,
        first_utc, end_utc,
    )
    orders_cnt = int(order_row["orders_cnt"] or 0)
    cancelled_cnt = int(order_row["cancelled_cnt"] or 0)

    returns_row = await conn.fetchrow(
        """
        SELECT count(*) AS cnt
        FROM returns
        WHERE accepted_at >= $1 AND accepted_at < $2
        """,
        first_utc, end_utc,
    )
    returns_cnt = int(returns_row["cnt"] or 0) if returns_row else 0
    returns_pct = safe_divide(returns_cnt, orders_cnt) * 100 if orders_cnt else 0.0

    rating_row = await conn.fetchrow(
        """
        SELECT rating, total_reviews, average_score
        FROM seller_rating_history
        ORDER BY recorded_at DESC
        LIMIT 1
        """
    )
    rating = float(rating_row["rating"] or 0) if rating_row else 0.0

    reviews_row = await conn.fetchrow(
        """
        SELECT
            count(*) AS new_reviews,
            round(avg(rating)::numeric, 2) AS avg_score
        FROM reviews
        WHERE published_at >= $1 AND published_at < $2
        """,
        first_utc, end_utc,
    )
    new_reviews = int(reviews_row["new_reviews"] or 0) if reviews_row else 0
    avg_score = float(reviews_row["avg_score"] or 0) if reviews_row else 0.0

    def pct_of_net(v: float) -> str:
        return _fmt_pct(safe_divide(v, net_revenue)) if net_revenue else "—"

    lines = [
        "## Магазин — итоги месяца\n",
        "| Метрика | Значение | % от чистой выручки |",
        "|---|---|---|",
        f"| Выручка (gross) | {_fmt_rub(revenue)} ₽ | — |",
        f"| Возвраты | {_fmt_rub(returns_rev)} ₽ | {pct_of_net(returns_rev)} |",
        f"| Чистая выручка | {_fmt_rub(net_revenue)} ₽ | — |",
        f"| Комиссия Ozon | {_fmt_rub(commission)} ₽ | {pct_of_net(commission)} |",
        f"| Логистика | {_fmt_rub(logistics)} ₽ | {pct_of_net(logistics)} |",
        f"| Реклама | {_fmt_rub(ads)} ₽ | {pct_of_net(ads)} |",
        f"| Прочие расходы MP | {_fmt_rub(other_mp)} ₽ | {pct_of_net(other_mp)} |",
        f"| Итого расходы MP | {_fmt_rub(total_mp_exp)} ₽ | {pct_of_net(total_mp_exp)} |",
        f"| Валовая прибыль | {_fmt_rub(gross_profit)} ₽ | {pct_of_net(gross_profit)} |",
        f"| Заказов (шт.) | {orders_cnt} | — |",
        f"| Отменено (шт.) | {cancelled_cnt} | — |",
        f"| Возвратов | {returns_cnt} шт. / {returns_pct:.1f}% | — |",
        f"| Рейтинг продавца | {rating:.2f} | — |",
        f"| Новых отзывов | {new_reviews} | — |",
        f"| Средняя оценка | {avg_score:.2f} | — |",
        "",
    ]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 3: Проверить импорты — запустить без ошибок**

```bash
PYTHONIOENCODING=utf-8 python -c "from src.services.monthly_report import _build_header, _build_shop_summary; print('OK')"
```

Ожидаем: `OK`

- [ ] **Step 4: Коммит**

```bash
git add src/services/monthly_report.py
git commit -m "feat: monthly report service — header + shop summary (section 1)"
```

---

## Task 2: Сервис — данные по товарам (Секция 2)

**Files:**
- Modify: `src/services/monthly_report.py`

- [ ] **Step 1: Добавить функцию загрузки данных о продажах и возвратах по артикулам**

Добавить в `src/services/monthly_report.py`:

```python
async def _load_sales_by_article(
    conn: asyncpg.Connection, first_utc: datetime, end_utc: datetime
) -> Dict[str, Dict[str, float]]:
    """Продажи и возвраты по offer_id за период."""
    rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g') AS offer_id,
            sum(coalesce(oi.quantity, 0)) FILTER (
                WHERE lower(coalesce(o.status,'')) IN (
                    'delivered','delivering','awaiting_deliver','awaiting_packaging',
                    'driver_pickup','доставлен','доставляется','ожидает в пвз',
                    'у водителя','ожидает отгрузки','ожидает сборки'
                )
            )::float8 AS qty_sold,
            sum(coalesce(oi.quantity, 0) * coalesce(oi.price, 0)) FILTER (
                WHERE lower(coalesce(o.status,'')) IN (
                    'delivered','delivering','awaiting_deliver','awaiting_packaging',
                    'driver_pickup','доставлен','доставляется','ожидает в пвз',
                    'у водителя','ожидает отгрузки','ожидает сборки'
                )
            )::float8 AS revenue,
            sum(coalesce(oi.quantity, 0)) FILTER (
                WHERE lower(coalesce(o.status,'')) IN ('cancelled','отменён','отменен')
            )::float8 AS qty_returned
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE o.created_at >= $1
          AND o.created_at < $2
          AND coalesce(trim(oi.offer_id), '') <> ''
        GROUP BY 1
        """,
        first_utc,
        end_utc,
    )
    result: Dict[str, Dict[str, float]] = {}
    for row in rows:
        key = str(row["offer_id"] or "").strip()
        if not key:
            continue
        qty = float(row["qty_sold"] or 0)
        rev = float(row["revenue"] or 0)
        ret = float(row["qty_returned"] or 0)
        result[key] = {
            "qty": qty,
            "revenue": rev,
            "avg_price": safe_divide(rev, qty),
            "returns": ret,
            "returns_pct": safe_divide(ret, qty + ret) * 100,
        }
    return result
```

- [ ] **Step 2: Добавить функцию загрузки начислений (экономика) по артикулам**

Добавить в `src/services/monthly_report.py`:

```python
async def _load_accruals_by_article(
    conn: asyncpg.Connection, first_utc: datetime, end_utc: datetime
) -> Dict[str, Dict[str, float]]:
    """Начисления, комиссии и логистика по offer_id из транзакций."""
    tx_rows = await conn.fetch(
        """
        SELECT operation_type, amount, raw_data
        FROM transactions
        WHERE operation_date >= $1
          AND operation_date < $2
        """,
        first_utc,
        end_utc,
    )

    import json as _json

    accruals: Dict[str, Dict[str, float]] = {}

    def _get(offer_id: str) -> Dict[str, float]:
        key = offer_id.strip().lower()
        if key not in accruals:
            accruals[key] = {"accrued": 0.0, "commission": 0.0, "logistics": 0.0, "net": 0.0}
        return accruals[key]

    DELIVERY_TYPES = {"OperationAgentDeliveredToCustomer", "MarketplaceServiceItemDelivToCustomer"}
    COMMISSION_TYPES = {"OperationAgentDeliveredToCustomer", "MarketplaceSellerCompensationReturnedGoods"}
    LOGISTICS_TYPES = {
        "MarketplaceServiceItemDirectFlowLogistic",
        "MarketplaceServiceItemReturnFlowLogistic",
        "MarketplaceServiceItemDelivToCustomer",
    }

    for row in tx_rows:
        op_type = str(row["operation_type"] or "").strip()
        amount = float(row["amount"] or 0)
        raw = row["raw_data"]
        if isinstance(raw, str):
            try:
                raw = _json.loads(raw)
            except Exception:
                raw = {}
        if not isinstance(raw, dict):
            raw = {}

        items = raw.get("items") or []
        if not isinstance(items, list):
            items = []

        for item in items:
            if not isinstance(item, dict):
                continue
            offer_id = str(item.get("offer_id") or item.get("sku") or "").strip()
            if not offer_id:
                continue
            item_amount = float(item.get("amount") or 0)
            bucket = _get(offer_id)
            if op_type in DELIVERY_TYPES:
                bucket["accrued"] += item_amount
            if op_type in COMMISSION_TYPES:
                commission = float(item.get("commission_amount") or 0)
                bucket["commission"] += abs(commission)
            if op_type in LOGISTICS_TYPES:
                bucket["logistics"] += abs(item_amount)

    for key, b in accruals.items():
        b["net"] = b["accrued"] - b["commission"] - b["logistics"]

    return accruals
```

- [ ] **Step 3: Добавить функцию загрузки рекламы по артикулам**

Добавить в `src/services/monthly_report.py`:

```python
async def _load_ads_by_offer(
    conn: asyncpg.Connection, date_from: date, date_to_excl: date
) -> Dict[str, Dict[str, float]]:
    """Расход и статистика рекламы по offer_id за период."""
    rows = await conn.fetch(
        """
        SELECT
            p.offer_id,
            sum(cs.spent::float8) AS spent,
            sum(cs.views)::int AS views,
            sum(cs.clicks)::int AS clicks,
            sum(cs.orders)::int AS orders,
            sum(cs.revenue::float8) AS ad_revenue
        FROM campaign_statistics cs
        JOIN (
            SELECT DISTINCT
                regexp_replace(lower(trim(both '''' from coalesce(offer_id,''))), '\\s+', ' ', 'g') AS offer_id,
                fbo_sku_id AS sku
            FROM report_products_items
            WHERE fbo_sku_id IS NOT NULL
            UNION
            SELECT DISTINCT
                regexp_replace(lower(trim(both '''' from coalesce(offer_id,''))), '\\s+', ' ', 'g') AS offer_id,
                fbs_sku_id AS sku
            FROM report_products_items
            WHERE fbs_sku_id IS NOT NULL
        ) p ON p.sku = cs.sku
        WHERE (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date >= $1
          AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date < $2
          AND coalesce(p.offer_id, '') <> ''
        GROUP BY p.offer_id
        HAVING sum(cs.spent::float8) > 0 OR sum(cs.orders)::int > 0
        """,
        date_from,
        date_to_excl,
    )
    result: Dict[str, Dict[str, float]] = {}
    for row in rows:
        key = str(row["offer_id"] or "").strip().lower()
        if not key:
            continue
        spent = float(row["spent"] or 0)
        ad_rev = float(row["ad_revenue"] or 0)
        orders = int(row["orders"] or 0)
        views = int(row["views"] or 0)
        clicks = int(row["clicks"] or 0)
        result[key] = {
            "spent": spent,
            "views": views,
            "clicks": clicks,
            "ctr": safe_divide(clicks, views) * 100,
            "orders": orders,
            "drr": safe_divide(spent, ad_rev) * 100 if ad_rev else 0.0,
        }
    return result
```

- [ ] **Step 4: Добавить функцию загрузки акций по артикулам**

Добавить в `src/services/monthly_report.py`:

```python
async def _load_promos_by_offer(
    conn: asyncpg.Connection, first_utc: datetime, end_utc: datetime
) -> Dict[str, List[Dict[str, Any]]]:
    """Акции (активные + кандидаты) по offer_id за период."""
    rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(pp.offer_id,''))), '\\s+', ' ', 'g') AS offer_id,
            pa.title,
            pp.action_price,
            pp.max_action_price,
            pp.is_participating,
            pp.is_candidate
        FROM promo_products pp
        JOIN promo_actions pa ON pa.action_id = pp.action_id
        WHERE pa.date_start <= $2
          AND (pa.date_end IS NULL OR pa.date_end >= $1)
          AND coalesce(pp.offer_id, '') <> ''
        ORDER BY pa.date_start DESC
        """,
        first_utc,
        end_utc,
    )
    result: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = str(row["offer_id"] or "").strip().lower()
        if not key:
            continue
        result.setdefault(key, []).append({
            "title": str(row["title"] or ""),
            "action_price": float(row["action_price"] or 0),
            "max_action_price": float(row["max_action_price"] or 0),
            "is_participating": bool(row["is_participating"]),
            "is_candidate": bool(row["is_candidate"]),
        })
    return result
```

- [ ] **Step 5: Добавить функцию рендеринга секции 2 (товары)**

Добавить в `src/services/monthly_report.py`:

```python
async def _build_products_section(
    conn: asyncpg.Connection, month_value: str
) -> str:
    first, last = _month_dates(month_value)
    if last.month == 12:
        end_utc = datetime(last.year + 1, 1, 1, tzinfo=MSK).astimezone(timezone.utc)
    else:
        end_utc = datetime(last.year, last.month + 1, 1, tzinfo=MSK).astimezone(timezone.utc)
    first_utc = datetime(first.year, first.month, 1, tzinfo=MSK).astimezone(timezone.utc)

    sales = await _load_sales_by_article(conn, first_utc, end_utc)
    accruals = await _load_accruals_by_article(conn, first_utc, end_utc)
    stock_map = await load_stock_forecast_inputs(conn)
    ads = await _load_ads_by_offer(conn, first, last + timedelta(days=1))
    promos = await _load_promos_by_offer(conn, first_utc, end_utc)

    # Собираем все активные offer_id
    all_offers = set(sales.keys()) | set(accruals.keys()) | set(ads.keys()) | set(promos.keys())
    # Добавляем товары с остатками > 0
    for offer_id, info in stock_map.items():
        if float(info.get("stock") or 0) > 0:
            all_offers.add(offer_id.lower())

    # Получаем названия товаров
    name_rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(offer_id,''))), '\\s+', ' ', 'g') AS offer_id,
            max(name) AS name
        FROM report_products_items
        WHERE coalesce(offer_id, '') <> ''
        GROUP BY 1
        """
    )
    names: Dict[str, str] = {
        str(r["offer_id"] or "").strip(): str(r["name"] or "")
        for r in name_rows
    }

    lines = ["## Товары\n"]

    for offer_id in sorted(all_offers):
        name = names.get(offer_id, offer_id)
        s = sales.get(offer_id, {})
        a = accruals.get(offer_id, {})
        ad = ads.get(offer_id, {})
        stock_info = stock_map.get(offer_id, {})
        promo_list = promos.get(offer_id, [])

        stock_total = float(stock_info.get("stock") or 0)
        avg_daily = float(stock_info.get("avg_daily_sales") or 0)
        days_of_stock = round(safe_divide(stock_total, avg_daily)) if avg_daily > 0 else 999
        if days_of_stock < 30:
            stock_status = "⚠ заканчивается"
        elif days_of_stock > 120:
            stock_status = "❄ залёживается"
        else:
            stock_status = "✓ норма"

        lines.append(f"### {name} [{offer_id}]\n")

        # Продажи
        lines.append("#### Продажи")
        lines.append("| Заказов шт. | Выручка ₽ | Ср. цена ₽ | Возвратов шт. | Возвратов % |")
        lines.append("|---|---|---|---|---|")
        lines.append(
            f"| {int(s.get('qty',0))} | {_fmt_rub(s.get('revenue',0))} | "
            f"{_fmt_rub(s.get('avg_price',0))} | {int(s.get('returns',0))} | "
            f"{s.get('returns_pct',0):.1f}% |\n"
        )

        # Экономика
        lines.append("#### Экономика (начисления)")
        lines.append("| Начислено ₽ | Комиссия ₽ | Логистика ₽ | Чистое ₽ | Маржа % |")
        lines.append("|---|---|---|---|---|")
        accrued = a.get("accrued", 0)
        net = a.get("net", 0)
        margin = safe_divide(net, accrued) * 100 if accrued else 0.0
        lines.append(
            f"| {_fmt_rub(accrued)} | {_fmt_rub(a.get('commission',0))} | "
            f"{_fmt_rub(a.get('logistics',0))} | {_fmt_rub(net)} | {margin:.1f}% |\n"
        )

        # Остатки
        sales_per_month = round(avg_daily * 30, 1)
        lines.append("#### Остатки")
        lines.append("| FBO+FBS шт. | Продаж/мес (расч.) | Дней запаса | Статус |")
        lines.append("|---|---|---|---|")
        lines.append(
            f"| {int(stock_total)} | {sales_per_month} | "
            f"{'∞' if days_of_stock >= 999 else days_of_stock} | {stock_status} |\n"
        )

        # Реклама
        if ad:
            lines.append("#### Реклама")
            lines.append("| Расход ₽ | Показы | Клики | CTR % | Заказы | ДРР % |")
            lines.append("|---|---|---|---|---|---|")
            lines.append(
                f"| {_fmt_rub(ad.get('spent',0))} | {int(ad.get('views',0))} | "
                f"{int(ad.get('clicks',0))} | {ad.get('ctr',0):.2f}% | "
                f"{int(ad.get('orders',0))} | {ad.get('drr',0):.1f}% |\n"
            )

        # Акции
        if promo_list:
            lines.append("#### Акции")
            lines.append("| Акция | Цена акц. ₽ | Макс. цена ₽ | В акции | Кандидат |")
            lines.append("|---|---|---|---|---|")
            for p in promo_list:
                lines.append(
                    f"| {p['title']} | {_fmt_rub(p['action_price'])} | "
                    f"{_fmt_rub(p['max_action_price'])} | "
                    f"{'Да' if p['is_participating'] else 'Нет'} | "
                    f"{'Да' if p['is_candidate'] else 'Нет'} |"
                )
            lines.append("")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 6: Проверить импорты**

```bash
PYTHONIOENCODING=utf-8 python -c "from src.services.monthly_report import _build_products_section; print('OK')"
```

Ожидаем: `OK`

- [ ] **Step 7: Коммит**

```bash
git add src/services/monthly_report.py
git commit -m "feat: monthly report — products section (section 2)"
```

---

## Task 3: Сервис — реклама-сводка, остатки, акции (Секции 3–5)

**Files:**
- Modify: `src/services/monthly_report.py`

- [ ] **Step 1: Добавить секцию 3 — реклама сводка**

Добавить в `src/services/monthly_report.py`:

```python
async def _build_ads_summary(
    conn: asyncpg.Connection, month_value: str
) -> str:
    first, last = _month_dates(month_value)
    date_to_excl = last + timedelta(days=1)

    campaign_rows = await conn.fetch(
        """
        SELECT
            c.campaign_name,
            c.campaign_type,
            sum(cs.spent::float8) AS spent,
            sum(cs.views)::int AS views,
            sum(cs.clicks)::int AS clicks,
            sum(cs.orders)::int AS orders,
            sum(cs.revenue::float8) AS ad_revenue
        FROM campaign_statistics cs
        JOIN campaigns c ON c.campaign_id = cs.campaign_id
        WHERE (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date >= $1
          AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date < $2
        GROUP BY c.campaign_name, c.campaign_type
        HAVING sum(cs.spent::float8) > 0
        ORDER BY sum(cs.spent::float8) DESC
        """,
        first,
        date_to_excl,
    )

    sku_rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(p.offer_id,''))), '\\s+', ' ', 'g') AS offer_id,
            sum(cs.spent::float8) AS spent,
            sum(cs.orders)::int AS orders,
            sum(cs.revenue::float8) AS ad_revenue
        FROM campaign_statistics cs
        JOIN (
            SELECT DISTINCT
                regexp_replace(lower(trim(both '''' from coalesce(offer_id,''))), '\\s+', ' ', 'g') AS offer_id,
                fbo_sku_id AS sku
            FROM report_products_items WHERE fbo_sku_id IS NOT NULL
            UNION
            SELECT DISTINCT
                regexp_replace(lower(trim(both '''' from coalesce(offer_id,''))), '\\s+', ' ', 'g') AS offer_id,
                fbs_sku_id AS sku
            FROM report_products_items WHERE fbs_sku_id IS NOT NULL
        ) p ON p.sku = cs.sku
        WHERE (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date >= $1
          AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date < $2
          AND coalesce(p.offer_id, '') <> ''
        GROUP BY p.offer_id
        HAVING sum(cs.spent::float8) > 0
        ORDER BY sum(cs.spent::float8) DESC
        """,
        first,
        date_to_excl,
    )

    lines = ["## Реклама — сводка\n"]
    lines.append("### По кампаниям")
    lines.append("| Кампания | Тип | Расход ₽ | Показы | Клики | Заказы | ДРР % |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in campaign_rows:
        spent = float(r["spent"] or 0)
        ad_rev = float(r["ad_revenue"] or 0)
        drr = safe_divide(spent, ad_rev) * 100 if ad_rev else 0.0
        lines.append(
            f"| {r['campaign_name']} | {r['campaign_type']} | {_fmt_rub(spent)} | "
            f"{int(r['views'] or 0)} | {int(r['clicks'] or 0)} | "
            f"{int(r['orders'] or 0)} | {drr:.1f}% |"
        )
    lines.append("")

    lines.append("### Топ-5 по расходу")
    lines.append("| Товар | Расход ₽ | Заказы | ДРР % |")
    lines.append("|---|---|---|---|")
    for r in sku_rows[:5]:
        spent = float(r["spent"] or 0)
        ad_rev = float(r["ad_revenue"] or 0)
        drr = safe_divide(spent, ad_rev) * 100 if ad_rev else 0.0
        lines.append(f"| {r['offer_id']} | {_fmt_rub(spent)} | {int(r['orders'] or 0)} | {drr:.1f}% |")
    lines.append("")

    # Топ-5 по ДРР (только у кого есть заказы — иначе ДРР бесконечность)
    with_orders = [r for r in sku_rows if int(r["orders"] or 0) > 0 and float(r["ad_revenue"] or 0) > 0]
    worst_drr = sorted(
        with_orders,
        key=lambda r: safe_divide(float(r["spent"] or 0), float(r["ad_revenue"] or 0)),
        reverse=True,
    )[:5]

    lines.append("### Топ-5 по ДРР (худшие)")
    lines.append("| Товар | ДРР % | Расход ₽ | Заказы |")
    lines.append("|---|---|---|---|")
    for r in worst_drr:
        spent = float(r["spent"] or 0)
        ad_rev = float(r["ad_revenue"] or 0)
        drr = safe_divide(spent, ad_rev) * 100
        lines.append(f"| {r['offer_id']} | {drr:.1f}% | {_fmt_rub(spent)} | {int(r['orders'] or 0)} |")
    lines.append("")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 2: Добавить секцию 4 — остатки с отклонениями**

Добавить в `src/services/monthly_report.py`:

```python
async def _build_stock_section(conn: asyncpg.Connection) -> str:
    stock_map = await load_stock_forecast_inputs(conn)

    ending: List[Tuple[str, Dict]] = []
    overstocked: List[Tuple[str, Dict]] = []

    for offer_id, info in stock_map.items():
        stock = float(info.get("stock") or 0)
        avg_daily = float(info.get("avg_daily_sales") or 0)
        if stock <= 0 and avg_daily <= 0:
            continue
        days = round(safe_divide(stock, avg_daily)) if avg_daily > 0 else 999
        sales_month = round(avg_daily * 30, 1)
        entry = (offer_id, stock, days, sales_month)
        if days < 30:
            ending.append(entry)
        elif days > 120:
            overstocked.append(entry)

    ending.sort(key=lambda x: x[2])
    overstocked.sort(key=lambda x: x[2], reverse=True)

    lines = ["## Остатки и оборачиваемость\n"]

    lines.append("### ⚠ Заканчиваются (< 30 дней)")
    lines.append("| Товар | Остаток шт. | Дней запаса | Продаж/мес |")
    lines.append("|---|---|---|---|")
    for offer_id, stock, days, sales_month in ending:
        lines.append(f"| {offer_id} | {int(stock)} | {days} | {sales_month} |")
    if not ending:
        lines.append("| — | — | — | — |")
    lines.append("")

    lines.append("### ❄ Залёживаются (> 120 дней)")
    lines.append("| Товар | Остаток шт. | Дней запаса | Продаж/мес |")
    lines.append("|---|---|---|---|")
    for offer_id, stock, days, sales_month in overstocked:
        d = "∞" if days >= 999 else str(days)
        lines.append(f"| {offer_id} | {int(stock)} | {d} | {sales_month} |")
    if not overstocked:
        lines.append("| — | — | — | — |")
    lines.append("")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 3: Добавить секцию 5 — акции**

Добавить в `src/services/monthly_report.py`:

```python
async def _build_promos_section(
    conn: asyncpg.Connection, month_value: str
) -> str:
    first, last = _month_dates(month_value)
    if last.month == 12:
        end_utc = datetime(last.year + 1, 1, 1, tzinfo=MSK).astimezone(timezone.utc)
    else:
        end_utc = datetime(last.year, last.month + 1, 1, tzinfo=MSK).astimezone(timezone.utc)
    first_utc = datetime(first.year, first.month, 1, tzinfo=MSK).astimezone(timezone.utc)

    action_rows = await conn.fetch(
        """
        SELECT
            title,
            date_start,
            date_end,
            count(pp.id) AS product_count
        FROM promo_actions pa
        LEFT JOIN promo_products pp ON pp.action_id = pa.action_id AND pp.is_participating = true
        WHERE pa.date_start <= $2
          AND (pa.date_end IS NULL OR pa.date_end >= $1)
        GROUP BY pa.action_id, pa.title, pa.date_start, pa.date_end
        ORDER BY pa.date_start DESC
        """,
        first_utc,
        end_utc,
    )

    candidate_rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(pp.offer_id,''))), '\\s+', ' ', 'g') AS offer_id,
            pp.max_action_price,
            coalesce(rp.price_current, 0) AS current_price
        FROM promo_products pp
        JOIN promo_actions pa ON pa.action_id = pp.action_id
        LEFT JOIN (
            SELECT
                regexp_replace(lower(trim(both '''' from coalesce(offer_id,''))), '\\s+', ' ', 'g') AS offer_id,
                max(price_current) AS price_current
            FROM report_products_items
            GROUP BY 1
        ) rp ON rp.offer_id = regexp_replace(lower(trim(both '''' from coalesce(pp.offer_id,''))), '\\s+', ' ', 'g')
        WHERE pp.is_candidate = true
          AND pp.is_participating = false
          AND pa.date_start <= $2
          AND (pa.date_end IS NULL OR pa.date_end >= $1)
          AND coalesce(pp.offer_id, '') <> ''
        ORDER BY (coalesce(rp.price_current, 0) - pp.max_action_price) / NULLIF(rp.price_current, 0) ASC
        """,
        first_utc,
        end_utc,
    )

    lines = ["## Акции\n"]

    lines.append("### Активные акции периода")
    lines.append("| Акция | Дата начала | Дата конца | Товаров в акции |")
    lines.append("|---|---|---|---|")
    for r in action_rows:
        date_end = str(r["date_end"])[:10] if r["date_end"] else "—"
        lines.append(
            f"| {r['title']} | {str(r['date_start'])[:10]} | {date_end} | {int(r['product_count'] or 0)} |"
        )
    if not action_rows:
        lines.append("| — | — | — | — |")
    lines.append("")

    lines.append("### Товары-кандидаты (не вошли в акцию)")
    lines.append("| Товар | Тек. цена ₽ | Макс. цена акц. ₽ | Разница % |")
    lines.append("|---|---|---|---|")
    for r in candidate_rows[:20]:
        cur = float(r["current_price"] or 0)
        max_p = float(r["max_action_price"] or 0)
        diff_pct = safe_divide(cur - max_p, cur) * 100 if cur > 0 else 0.0
        lines.append(
            f"| {r['offer_id']} | {_fmt_rub(cur)} | {_fmt_rub(max_p)} | {diff_pct:.1f}% |"
        )
    if not candidate_rows:
        lines.append("| — | — | — | — |")
    lines.append("")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Добавить главную функцию сборки отчёта**

Добавить в конец `src/services/monthly_report.py`:

```python
async def build_monthly_report(conn: asyncpg.Connection, month_value: str) -> str:
    """Собирает полный MD-отчёт за месяц. month_value формат: YYYY-MM."""
    parts = [
        _build_header(month_value),
        await _build_shop_summary(conn, month_value),
        await _build_products_section(conn, month_value),
        await _build_ads_summary(conn, month_value),
        await _build_stock_section(conn),
        await _build_promos_section(conn, month_value),
    ]
    return "\n".join(parts)
```

- [ ] **Step 5: Проверить импорты**

```bash
PYTHONIOENCODING=utf-8 python -c "from src.services.monthly_report import build_monthly_report; print('OK')"
```

Ожидаем: `OK`

- [ ] **Step 6: Коммит**

```bash
git add src/services/monthly_report.py
git commit -m "feat: monthly report — ads/stock/promos sections + build_monthly_report"
```

---

## Task 4: HTTP Endpoint

**Files:**
- Create: `src/dashboard/routes/report.py`
- Modify: `src/dashboard/app.py`

- [ ] **Step 1: Создать route-файл**

```python
# src/dashboard/routes/report.py
"""GET /api/monthly-report — скачать MD-отчёт за месяц."""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
from aiohttp import web

from src.dashboard.helpers import month_bounds


async def get_monthly_report(request: web.Request) -> web.Response:
    month_value = (request.query.get("month") or "").strip()
    if not month_value:
        month_value = datetime.now(timezone.utc).strftime("%Y-%m")

    try:
        month_bounds(month_value)
    except ValueError:
        return web.Response(text="Invalid month format, expected YYYY-MM", status=400)

    from src.services.monthly_report import build_monthly_report

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        md_text = await build_monthly_report(conn, month_value)

    filename = f"ozon_monthly_report_{month_value}.md"
    return web.Response(
        text=md_text,
        content_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 2: Зарегистрировать маршрут в app.py**

В файле `src/dashboard/app.py` добавить импорт после блока импортов из `routes.supply_chrome`:

```python
from src.dashboard.routes.report import get_monthly_report
```

В функции `create_app()` добавить строку после `app.router.add_get("/api/health", health)`:

```python
    app.router.add_get("/api/monthly-report", get_monthly_report)
```

- [ ] **Step 3: Проверить импорты**

```bash
PYTHONIOENCODING=utf-8 python -c "from src.dashboard.app import create_app; create_app(); print('OK')"
```

Ожидаем: `OK`

- [ ] **Step 4: Коммит**

```bash
git add src/dashboard/routes/report.py src/dashboard/app.py
git commit -m "feat: add GET /api/monthly-report endpoint"
```

---

## Task 5: CLI-скрипт

**Files:**
- Create: `src/export_monthly_report.py`

- [ ] **Step 1: Создать CLI**

```python
# src/export_monthly_report.py
"""CLI: python -m src.export_monthly_report --month 2026-05

Сохраняет MD-отчёт в exports/monthly_report_YYYY-MM.md
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

from src.dashboard.helpers import month_bounds, to_asyncpg_dsn
from src.config import settings


async def _run(month_value: str) -> None:
    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    try:
        from src.services.monthly_report import build_monthly_report
        md_text = await build_monthly_report(conn, month_value)
    finally:
        await conn.close()

    out_dir = Path("exports")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"monthly_report_{month_value}.md"
    out_path.write_text(md_text, encoding="utf-8")
    print(f"Saved: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Ozon monthly MD report")
    parser.add_argument(
        "--month",
        default=datetime.now(timezone.utc).strftime("%Y-%m"),
        help="Month in YYYY-MM format (default: current month)",
    )
    args = parser.parse_args()

    try:
        month_bounds(args.month)
    except ValueError:
        print(f"Error: invalid month '{args.month}', expected YYYY-MM", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run(args.month))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Проверить импорт**

```bash
PYTHONIOENCODING=utf-8 python -c "import src.export_monthly_report; print('OK')"
```

Ожидаем: `OK`

- [ ] **Step 3: Коммит**

```bash
git add src/export_monthly_report.py
git commit -m "feat: CLI export_monthly_report — saves to exports/monthly_report_YYYY-MM.md"
```

---

## Task 6: Smoke-тест сквозной генерации

- [ ] **Step 1: Запустить CLI для текущего месяца**

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -m src.export_monthly_report --month 2026-05
```

Ожидаем: `Saved: exports/monthly_report_2026-05.md`

- [ ] **Step 2: Проверить структуру файла**

```bash
PYTHONIOENCODING=utf-8 python -c "
content = open('exports/monthly_report_2026-05.md', encoding='utf-8').read()
assert '# Ozon Monthly Report' in content
assert '## Магазин' in content
assert '## Товары' in content
assert '## Реклама' in content
assert '## Остатки' in content
assert '## Акции' in content
print('Structure OK, size:', len(content), 'chars')
"
```

Ожидаем: `Structure OK, size: <N> chars`

- [ ] **Step 3: Проверить endpoint (если сервер запущен)**

Открыть в браузере: `http://localhost:8000/api/monthly-report?month=2026-05`

Браузер должен предложить скачать файл `ozon_monthly_report_2026-05.md`.

- [ ] **Step 4: Финальный коммит**

```bash
git add .
git commit -m "feat: monthly AI report complete — endpoint + CLI"
```
