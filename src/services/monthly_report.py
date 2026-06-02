# src/services/monthly_report.py
"""Сборщик ежемесячного MD-отчёта для ИИ-анализа."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json as _json
from typing import Any, Dict, List, Tuple

import asyncpg

from src.dashboard.constants import MSK
from src.dashboard.helpers import safe_divide
from src.dashboard.routes.finance import build_rows_map_for_month


def _fmt_rub(value: Any) -> str:
    try:
        return f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value or 0) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _month_dates(month_value: str) -> Tuple[date, date]:
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

    first, last = _month_dates(month_value)
    first_utc = datetime(first.year, first.month, first.day, tzinfo=MSK).astimezone(timezone.utc)
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
        SELECT rating
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


async def _load_sales_by_article(
    conn: asyncpg.Connection, first_utc: datetime, end_utc: datetime
) -> Dict[str, Dict[str, float]]:
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


async def _load_accruals_by_article(
    conn: asyncpg.Connection, first_utc: datetime, end_utc: datetime
) -> Dict[str, Dict[str, float]]:
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


async def _load_ads_by_offer(
    conn: asyncpg.Connection, date_from: date, date_to_excl: date
) -> Dict[str, Dict[str, float]]:
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


async def _load_promos_by_offer(
    conn: asyncpg.Connection, first_utc: datetime, end_utc: datetime
) -> Dict[str, List[Dict[str, Any]]]:
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


async def _build_products_section(
    conn: asyncpg.Connection, month_value: str
) -> str:
    from src.services.report_services import load_stock_forecast_inputs

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

    all_offers = set(sales.keys()) | set(accruals.keys()) | set(ads.keys()) | set(promos.keys())
    for offer_id, info in stock_map.items():
        if float(info.get("stock") or 0) > 0:
            all_offers.add(offer_id.lower())

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

        lines.append("#### Продажи")
        lines.append("| Заказов шт. | Выручка ₽ | Ср. цена ₽ | Возвратов шт. | Возвратов % |")
        lines.append("|---|---|---|---|---|")
        lines.append(
            f"| {int(s.get('qty',0))} | {_fmt_rub(s.get('revenue',0))} | "
            f"{_fmt_rub(s.get('avg_price',0))} | {int(s.get('returns',0))} | "
            f"{s.get('returns_pct',0):.1f}% |\n"
        )

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

        sales_per_month = round(avg_daily * 30, 1)
        lines.append("#### Остатки")
        lines.append("| FBO+FBS шт. | Продаж/мес (расч.) | Дней запаса | Статус |")
        lines.append("|---|---|---|---|")
        lines.append(
            f"| {int(stock_total)} | {sales_per_month} | "
            f"{'∞' if days_of_stock >= 999 else days_of_stock} | {stock_status} |\n"
        )

        if ad:
            lines.append("#### Реклама")
            lines.append("| Расход ₽ | Показы | Клики | CTR % | Заказы | ДРР % |")
            lines.append("|---|---|---|---|---|---|")
            lines.append(
                f"| {_fmt_rub(ad.get('spent',0))} | {int(ad.get('views',0))} | "
                f"{int(ad.get('clicks',0))} | {ad.get('ctr',0):.2f}% | "
                f"{int(ad.get('orders',0))} | {ad.get('drr',0):.1f}% |\n"
            )

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
