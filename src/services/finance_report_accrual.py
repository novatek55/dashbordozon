"""Finance Report РІ СЂРµР¶РёРјРµ accrual.

РЎС‡РёС‚Р°РµС‚ P&L РїРѕ СѓР¶Рµ СЂР°Р·РјРµС‰С‘РЅРЅС‹Рј Р·Р°РєР°Р·Р°Рј РјРµСЃСЏС†Р°:
- РІС‹СЂСѓС‡РєСѓ вЂ” РїРѕ С„Р°РєС‚Сѓ Р·Р°РєР°Р·Р°РЅРЅС‹С… SKU Г— (1 - % РїРѕС‚РµСЂСЊ SKU 30Рґ)
- СЂР°СЃС…РѕРґС‹ Ozon вЂ” СѓРґРµР»СЊРЅС‹Рµ СЃСЂРµРґРЅРёРµ РЅР° РµРґРёРЅРёС†Сѓ (30Рґ) Г— РїСЂРѕРіРЅРѕР· РІС‹РєСѓРїРѕРІ
- СЃРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ вЂ” cost_price SKU Г— РїСЂРѕРіРЅРѕР· РІС‹РєСѓРїРѕРІ
- СЂРµРєР»Р°РјСѓ вЂ” С„Р°РєС‚ РёР· v1 (РЅРµ РґРѕСЃС‡РёС‚С‹РІР°РµС‚СЃСЏ)

Р­С‚Рѕ В«СЌРєРѕРЅРѕРјРёС‡РµСЃРєРёР№В» РІР·РіР»СЏРґ РІ РїСЂРѕС‚РёРІРѕРІРµСЃ В«РєР°СЃСЃРѕРІРѕРјСѓВ» v1, РєРѕС‚РѕСЂС‹Р№ СЃСѓРјРјРёСЂСѓРµС‚
РЅР°С‡РёСЃР»РµРЅРёСЏ РїРѕ РґР°С‚Рµ С‚СЂР°РЅР·Р°РєС†РёРё.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

import asyncpg
import aiohttp

from src.dashboard.constants import (
    FINANCE_REPORT_ROWS,
    ACCRUAL_COST_ROW_KEYS,
    MSK,
)
from src.dashboard.helpers import (
    init_row,
    month_bounds,
    month_start_msk,
    load_sku_identity_map,
    normalize_offer_id,
    normalize_sku_value,
    recalculate_row_total,
    safe_divide,
    set_row_from_formula,
)
from src.dashboard.routes.finance import build_rows_map_for_month
from src.services.report_services import (
    aggregate_recent_30d_totals,
    render_finance_rows,
    build_kpi_summary,
    finance_report_notes,
    load_prev_month_pcts,
)

ACCRUAL_SHARE_KEYS = [*ACCRUAL_COST_ROW_KEYS, "ad_spend"]
INTERNAL_BASE_URL = "http://127.0.0.1:8088"


ACCRUAL_NOTES = [
    "Р РµР¶РёРј accrual: P&L СЃС‡РёС‚Р°РµС‚СЃСЏ РїРѕ Р·Р°РєР°Р·Р°Рј С‚РµРєСѓС‰РµРіРѕ РјРµСЃСЏС†Р°, Р° РЅРµ РїРѕ РґР°С‚Рµ РЅР°С‡РёСЃР»РµРЅРёР№ Ozon.",
    "Р’С‹СЂСѓС‡РєР° = Р·Р°РєР°Р·Р°РЅРЅС‹Рµ РµРґ Г— С†РµРЅР° Г— (1 в€’ % РѕС‚РјРµРЅ SKU) Г— (1 в€’ % РЅРµРІС‹РєСѓРїР° SKU) Р·Р° 30Рґ.",
    "Р Р°СЃС…РѕРґС‹ Ozon вЂ” СѓРґРµР»СЊРЅС‹Рµ СЃСЂРµРґРЅРёРµ РЅР° РІС‹РєСѓРїР»РµРЅРЅСѓСЋ РµРґРёРЅРёС†Сѓ Р·Р° 30Рґ, РїСЂРёРјРµРЅРµРЅС‹ Рє РїСЂРѕРіРЅРѕР·Сѓ РІС‹РєСѓРїРѕРІ.",
    "Р РµРєР»Р°РјР° РѕС‚РѕР±СЂР°Р¶Р°РµС‚СЃСЏ РїРѕ С„Р°РєС‚Сѓ (РЅРµ РїСЂРѕРіРЅРѕР·).",
    "РљРѕСЌС„С„РёС†РёРµРЅС‚С‹ РІС‹РєСѓРїР°: per-SKU РїСЂРё в‰Ґ5 Р·Р°РєР°Р·РѕРІ Р·Р° 30Рґ, РёРЅР°С‡Рµ fallback РЅР° СЃСЂРµРґРЅРµРµ РїРѕ СЃС…РµРјРµ (FBO/FBS).",
]

# РњРёРЅРёРјСѓРј Р·Р°РєР°Р·РѕРІ Р·Р° 30Рґ РґР»СЏ СЂР°СЃС‡С‘С‚Р° РєРѕСЌС„С„РёС†РёРµРЅС‚РѕРІ per-SKU.
MIN_SKU_SAMPLE = 5

# РЎС‚Р°С‚СѓСЃС‹ Р·Р°РєР°Р·РѕРІ РІ fact_orders вЂ” С…СЂР°РЅСЏС‚СЃСЏ РЅР° СЂСѓСЃСЃРєРѕРј.
ORDER_STATUSES_INCLUDE = (
    "\u0434\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d",
    "\u0434\u043e\u0441\u0442\u0430\u0432\u043b\u044f\u0435\u0442\u0441\u044f",
    "\u043e\u0436\u0438\u0434\u0430\u0435\u0442 \u043e\u0442\u0433\u0440\u0443\u0437\u043a\u0438",
    "\u043e\u0436\u0438\u0434\u0430\u0435\u0442 \u0441\u0431\u043e\u0440\u043a\u0438",
    "\u043e\u0436\u0438\u0434\u0430\u0435\u0442 \u0432 \u043f\u0432\u0437",
    "\u0443 \u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044f",
    "\u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0451\u043d",
    "\u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0435\u043d",
    "\u043e\u0442\u043c\u0435\u043d\u0451\u043d",
    "\u043e\u0442\u043c\u0435\u043d\u0435\u043d",
)

STATUS_DELIVERED = ("\u0434\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d",)
STATUS_CANCELLED = ("\u043e\u0442\u043c\u0435\u043d\u0451\u043d", "\u043e\u0442\u043c\u0435\u043d\u0435\u043d")
STATUS_RETURNED = ("\u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0451\u043d", "\u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0435\u043d")


def _product_key(sku: Any, offer_id: Any) -> str:
    sku_val = normalize_sku_value(sku)
    if sku_val is not None:
        return f"sku:{int(sku_val)}"
    return normalize_offer_id(offer_id)


async def load_orders_for_month(
    conn: asyncpg.Connection,
    month_value: str,
) -> List[Dict[str, Any]]:
    """Р—Р°РєР°Р·С‹ РјРµСЃСЏС†Р°, Р°РіСЂРµРіРёСЂРѕРІР°РЅРЅС‹Рµ РїРѕ (offer_id, day). Р’РєР»СЋС‡Р°РµС‚ cancelled РґР»СЏ СЂР°СЃС‡С‘С‚Р°."""
    rows = await conn.fetch(
        """
        SELECT
            oi.sku::bigint AS sku,
            min(oi.offer_id) AS offer_id,
            to_char((o.created_at AT TIME ZONE 'Europe/Moscow'), 'YYYY-MM-DD') AS day,
            upper(coalesce(o.delivery_schema, '')) AS delivery_schema,
            lower(coalesce(o.status, '')) AS status,
            sum(coalesce(oi.quantity, 0))::float8 AS quantity,
            sum(coalesce(oi.quantity, 0) * coalesce(oi.price, 0))::float8 AS gross_revenue
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE to_char((o.created_at AT TIME ZONE 'Europe/Moscow'), 'YYYY-MM') = $1
          AND coalesce(lower(o.status), '') = ANY($2::text[])
        GROUP BY 1, 3, 4, 5
        HAVING oi.sku IS NOT NULL
        """,
        month_value,
        list(ORDER_STATUSES_INCLUDE),
    )
    return [dict(r) for r in rows]


async def compute_loss_rates(
    conn: asyncpg.Connection,
    reference_date: date,
) -> Dict[str, Any]:
    """РљРѕСЌС„С„РёС†РёРµРЅС‚С‹ РѕС‚РјРµРЅ Рё РЅРµРІС‹РєСѓРїР°.

    РћС‚РјРµРЅС‹ (cancel_rate) вЂ” РёР· fact_orders.status Р·Р° 30 РґРЅРµР№ (РѕРїРµСЂР°С‚РёРІРЅРѕ).
    Р’РѕР·РІСЂР°С‚С‹ (buyout_loss_rate) вЂ” Р·Р° 90 РґРЅРµР№ РёР· returns + returns_fbo,
    РґРµР»РёРј РЅР° delivered Р·Р° 90Рґ. РћРєРЅРѕ СЂР°СЃС€РёСЂРµРЅРѕ РїРѕС‚РѕРјСѓ С‡С‚Рѕ Р»Р°Рі Р·Р°РєР°Р·в†’РІРѕР·РІСЂР°С‚
    С‡Р°СЃС‚Рѕ 30-60 РґРЅРµР№ вЂ” РІ 30-РґРЅРµРІРЅРѕРј РѕРєРЅРµ Р·РЅР°РјРµРЅР°С‚РµР»СЊ СЂР°СЃСЃРёРЅС…СЂРѕРЅРёР·РёСЂРѕРІР°РЅ СЃ С‡РёСЃР»РёС‚РµР»РµРј.
    """
    order_rows_30d = await conn.fetch(
        """
        SELECT
            oi.sku::bigint AS sku,
            CASE WHEN upper(coalesce(o.delivery_schema, '')) LIKE 'FBS%' THEN 'fbs' ELSE 'fbo' END AS scheme,
            lower(coalesce(o.status, '')) AS status,
            coalesce(oi.quantity, 0)::float8 AS qty
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE o.created_at >= (($1::date - interval '30 days') AT TIME ZONE 'Europe/Moscow')
          AND o.created_at <  (($1::date + interval '1 day') AT TIME ZONE 'Europe/Moscow')
        """,
        reference_date,
    )

    delivered_rows_90d = await conn.fetch(
        """
        SELECT
            oi.sku::bigint AS sku,
            CASE WHEN upper(coalesce(o.delivery_schema, '')) LIKE 'FBS%' THEN 'fbs' ELSE 'fbo' END AS scheme,
            sum(coalesce(oi.quantity, 0))::float8 AS delivered_qty
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE o.created_at >= (($1::date - interval '90 days') AT TIME ZONE 'Europe/Moscow')
          AND o.created_at <  (($1::date + interval '1 day') AT TIME ZONE 'Europe/Moscow')
          AND lower(coalesce(o.status, '')) = ANY($2::text[])
        GROUP BY 1, 2
        HAVING oi.sku IS NOT NULL
        """,
        reference_date,
        list(STATUS_DELIVERED),
    )

    # Р’РѕР·РІСЂР°С‚С‹ Р·Р° 90 РґРЅРµР№ вЂ” РРЎРљР›Р®Р§РђР•Рњ РѕС‚РјРµРЅС‘РЅРЅС‹Рµ Р·Р°РєР°Р·С‹, С‡С‚РѕР±С‹ РЅРµ СЃС‡РёС‚Р°С‚СЊ
    # РѕРґРЅРё Рё С‚Рµ Р¶Рµ posting РґРІР°Р¶РґС‹ (РѕС‚РјРµРЅС‹ СѓР¶Рµ СѓС‡С‚РµРЅС‹ РІ cancel_rate).
    return_rows_90d = await conn.fetch(
        """
        WITH returns_all AS (
            SELECT sku, posting_number, quantity, returned_at FROM returns
            WHERE returned_at >= (($1::date - interval '90 days') AT TIME ZONE 'Europe/Moscow')
              AND returned_at <  (($1::date + interval '1 day') AT TIME ZONE 'Europe/Moscow')
            UNION ALL
            SELECT sku, posting_number, quantity, returned_at FROM returns_fbo
            WHERE returned_at >= (($1::date - interval '90 days') AT TIME ZONE 'Europe/Moscow')
              AND returned_at <  (($1::date + interval '1 day') AT TIME ZONE 'Europe/Moscow')
        )
        SELECT
            r.sku::bigint AS sku,
            sum(coalesce(r.quantity, 0))::float8 AS returned_qty
        FROM returns_all r
        LEFT JOIN fact_orders o ON o.posting_number = r.posting_number
        WHERE lower(coalesce(o.status, '')) NOT IN ('отменён', 'отменен')
        GROUP BY 1
        HAVING r.sku IS NOT NULL
        """,
        reference_date,
    )
    # Offer РјРѕР¶РµС‚ РїСЂРѕРґР°РІР°С‚СЊСЃСЏ РѕРґРЅРѕРІСЂРµРјРµРЅРЅРѕ РІ FBO Рё FBS вЂ” Р°РіСЂРµРіРёСЂСѓРµРј delivered РїРѕ РІСЃРµРј СЃС…РµРјР°Рј,
    # Р° 'primary scheme' РІС‹Р±РёСЂР°РµРј РєР°Рє С‚Сѓ РіРґРµ Р±РѕР»СЊС€Рµ РїРѕСЃС‚Р°РІРѕРє (РґР»СЏ fallback).
    delivered_90d_by_offer: Dict[str, Dict[str, Any]] = {}
    for r in delivered_rows_90d:
        offer = _product_key(r.get("sku"), None)
        qty = float(r["delivered_qty"] or 0.0)
        entry = delivered_90d_by_offer.setdefault(offer, {"scheme": r["scheme"], "qty": 0.0, "scheme_qty": {"fbo": 0.0, "fbs": 0.0}})
        entry["qty"] += qty
        entry["scheme_qty"][r["scheme"]] += qty
        if entry["scheme_qty"][r["scheme"]] > entry["scheme_qty"].get(entry["scheme"], 0.0):
            entry["scheme"] = r["scheme"]
    returned_90d_by_offer: Dict[str, float] = {
        _product_key(r.get("sku"), None): float(r["returned_qty"] or 0.0)
        for r in return_rows_90d
        if _product_key(r.get("sku"), None)
    }

    per_sku_raw: Dict[str, Dict[str, float]] = {}
    scheme_totals: Dict[str, Dict[str, float]] = {
        "fbo": {"total_30d": 0.0, "cancelled_30d": 0.0, "delivered_90d": 0.0, "returned_90d": 0.0},
        "fbs": {"total_30d": 0.0, "cancelled_30d": 0.0, "delivered_90d": 0.0, "returned_90d": 0.0},
    }

    # 30-РґРЅРµРІРЅС‹Рµ Р°РіСЂРµРіР°С‚С‹ РґР»СЏ cancel_rate
    for r in order_rows_30d:
        offer = _product_key(r.get("sku"), None) or ""
        scheme = r["scheme"]
        status = r["status"]
        qty = float(r["qty"] or 0)
        bucket = per_sku_raw.setdefault(offer, {
            "scheme": scheme,
            "total_30d": 0.0,
            "cancelled_30d": 0.0,
            "delivered_90d": 0.0,
            "returned_90d": 0.0,
        })
        bucket["total_30d"] += qty
        scheme_totals[scheme]["total_30d"] += qty
        if status in STATUS_CANCELLED:
            bucket["cancelled_30d"] += qty
            scheme_totals[scheme]["cancelled_30d"] += qty

    # 90-РґРЅРµРІРЅС‹Рµ РґРѕСЃС‚Р°РІР»РµРЅРЅС‹Рµ (РґР»СЏ Р·РЅР°РјРµРЅР°С‚РµР»СЏ buyout_loss_rate)
    for offer, info in delivered_90d_by_offer.items():
        scheme = info["scheme"]
        qty = info["qty"]
        bucket = per_sku_raw.setdefault(offer, {
            "scheme": scheme,
            "total_30d": 0.0,
            "cancelled_30d": 0.0,
            "delivered_90d": 0.0,
            "returned_90d": 0.0,
        })
        bucket["delivered_90d"] = qty
        scheme_totals[scheme]["delivered_90d"] += qty

    # 90-РґРЅРµРІРЅС‹Рµ РІРѕР·РІСЂР°С‚С‹ (С‡РёСЃР»РёС‚РµР»СЊ buyout_loss_rate)
    for offer, returned_qty in returned_90d_by_offer.items():
        bucket = per_sku_raw.get(offer)
        if bucket is None:
            continue
        bucket["returned_90d"] = returned_qty
        scheme_totals[bucket["scheme"]]["returned_90d"] += returned_qty

    per_scheme: Dict[str, Dict[str, float]] = {}
    for scheme, t in scheme_totals.items():
        per_scheme[scheme] = {
            "cancel_rate": _safe_rate(t["cancelled_30d"], t["total_30d"]),
            "buyout_loss_rate": _safe_rate(t["returned_90d"], t["delivered_90d"]),
        }

    per_sku: Dict[str, Dict[str, Any]] = {}
    for offer, b in per_sku_raw.items():
        if not offer:
            continue
        if b["total_30d"] >= MIN_SKU_SAMPLE:
            per_sku[offer] = {
                "cancel_rate": _safe_rate(b["cancelled_30d"], b["total_30d"]),
                "buyout_loss_rate": _safe_rate(b["returned_90d"], b["delivered_90d"]),
                "scheme": b["scheme"],
                "source": "sku",
            }
        else:
            scheme_rates = per_scheme[b["scheme"]]
            per_sku[offer] = {
                "cancel_rate": scheme_rates["cancel_rate"],
                "buyout_loss_rate": scheme_rates["buyout_loss_rate"],
                "scheme": b["scheme"],
                "source": "fallback_scheme",
            }

    return {"per_sku": per_sku, "per_scheme": per_scheme}


async def load_cost_prices(conn: asyncpg.Connection) -> Dict[str, float]:
    """РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ РїРѕ offer_id РёР· finance_article_costs."""
    rows = await conn.fetch(
        """
        SELECT
            sku::bigint AS sku,
            regexp_replace(lower(trim(both '''' from coalesce(article, ''))), '\\s+', ' ', 'g') AS offer_id,
            max(unit_cost)::float8 AS unit_cost
        FROM finance_article_costs
        WHERE unit_cost IS NOT NULL
        GROUP BY 1, 2
        """
    )
    out: Dict[str, float] = {}
    for r in rows:
        key = _product_key(r.get("sku"), r.get("offer_id"))
        if key:
            out[key] = float(r["unit_cost"] or 0.0)
    return out


async def compute_unit_costs_global(
    conn: asyncpg.Connection,
    reference_date: date,
) -> Dict[str, float]:
    """Р“Р»РѕР±Р°Р»СЊРЅС‹Рµ СЃСЂРµРґРЅРёРµ СѓРґРµР»СЊРЅС‹Рµ СЂР°СЃС…РѕРґС‹ РЅР° 1 РІС‹РєСѓРїР»РµРЅРЅСѓСЋ РµРґРёРЅРёС†Сѓ Р·Р° 30 РґРЅРµР№.

    Р”Р»СЏ РєР°Р¶РґРѕРіРѕ row_key РёР· ACCRUAL_COST_ROW_KEYS Р±РµСЂС‘Рј СЃСѓРјРјСѓ Р·Р° 30Рґ Рё РґРµР»РёРј
    РЅР° СЃСѓРјРјР°СЂРЅРѕРµ С‡РёСЃР»Рѕ РґРѕСЃС‚Р°РІР»РµРЅРЅС‹С… РµРґРёРЅРёС†. Р­С‚Рѕ MVP-РїСЂРёР±Р»РёР¶РµРЅРёРµ вЂ” per-SKU
    СѓС‚РѕС‡РЅРµРЅРёСЏ РїРѕС‚СЂРµР±СѓСЋС‚ Р°Р»Р»РѕРєР°С†РёРё СѓСЃР»СѓРі РїРѕ items postinРіР°, С‡С‚Рѕ Р·РґРµСЃСЊ РЅРµ РґРµР»Р°РµРј.
    """
    totals_30d = await aggregate_recent_30d_totals(conn, reference_date)
    delivered_row = await conn.fetchrow(
        """
        SELECT sum(coalesce(oi.quantity, 0))::float8 AS delivered_qty
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE o.created_at >= (($1::date - interval '30 days') AT TIME ZONE 'Europe/Moscow')
          AND o.created_at <  (($1::date + interval '1 day') AT TIME ZONE 'Europe/Moscow')
          AND lower(coalesce(o.status, '')) = ANY($2::text[])
        """,
        reference_date,
        list(STATUS_DELIVERED),
    )
    delivered_qty = float((delivered_row["delivered_qty"] if delivered_row else 0.0) or 0.0)

    unit: Dict[str, float] = {}
    if delivered_qty <= 0:
        for key in ACCRUAL_COST_ROW_KEYS:
            unit[key] = 0.0
        return unit

    for key in ACCRUAL_COST_ROW_KEYS:
        unit[key] = float(totals_30d.get(key, 0.0) or 0.0) / delivered_qty
    return unit


async def compute_cash_expense_shares_30d(
    conn: asyncpg.Connection,
    reference_date: date,
) -> Dict[str, float]:
    """Р”РѕР»Рё СЂР°СЃС…РѕРґРЅС‹С… СЃС‚Р°С‚РµР№ РѕС‚ РІС‹СЂСѓС‡РєРё/РїСЂРѕРґР°Р¶ РїРѕ РєР°СЃСЃРµ Р·Р° РїРѕСЃР»РµРґРЅРёРµ 30 РґРЅРµР№."""
    totals_30d = await aggregate_recent_30d_totals(conn, reference_date)
    revenue_sales_30d = float(totals_30d.get("revenue_sales", 0.0) or 0.0)
    if revenue_sales_30d <= 0:
        return {key: 0.0 for key in ACCRUAL_SHARE_KEYS}
    shares: Dict[str, float] = {}
    for key in ACCRUAL_SHARE_KEYS:
        shares[key] = float(totals_30d.get(key, 0.0) or 0.0) / revenue_sales_30d
    return shares


async def load_cash_expense_shares_30d_by_offer(
    date_from: date,
    date_to: date,
) -> Dict[str, Dict[str, float]]:
    """Р”РѕР»Рё СЂР°СЃС…РѕРґРЅС‹С… СЃС‚Р°С‚РµР№ РѕС‚ РІС‹СЂСѓС‡РєРё РїРѕ РєР°Р¶РґРѕРјСѓ Р°СЂС‚РёРєСѓР»Сѓ РёР· cash-РѕС‚С‡РµС‚Р° Р·Р° 30Рґ."""
    params = {
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
        "distribute_no_article": "1",
        "limit": "5000",
    }
    out: Dict[str, Dict[str, float]] = {}
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{INTERNAL_BASE_URL}/api/accruals-comp-by-article", params=params) as resp:
                if resp.status != 200:
                    return out
                payload = await resp.json()
    except Exception:
        return out

    for item in (payload.get("items") or []):
        offer = str(item.get("offer_id_normalized") or item.get("offer_id") or "").strip().lower()
        if not offer:
            continue
        values = item.get("values") or {}
        revenue_sales = float(values.get("revenue_sales", 0.0) or 0.0)
        if revenue_sales <= 0:
            continue
        shares = {k: float(values.get(k, 0.0) or 0.0) / revenue_sales for k in ACCRUAL_SHARE_KEYS}
        out[offer] = shares
    return out


def _safe_rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))


def _init_accrual_rows_map(days: List[str]) -> Dict[str, Dict[str, Any]]:
    rows_map: Dict[str, Dict[str, Any]] = {}
    for row_meta in FINANCE_REPORT_ROWS:
        if row_meta["kind"] in {"section", "spacer"}:
            continue
        rows_map[row_meta["key"]] = init_row(days, row_meta["key"])
    return rows_map


def _copy_from_cash(
    rows_map: Dict[str, Dict[str, Any]],
    cash_rows_map: Dict[str, Dict[str, Any]],
    keys: List[str],
    days: List[str],
) -> None:
    for key in keys:
        if key not in rows_map or key not in cash_rows_map:
            continue
        for day in days:
            rows_map[key]["daily"][day] = float(cash_rows_map[key]["daily"].get(day, 0.0) or 0.0)
        rows_map[key]["total"] = float(cash_rows_map[key].get("total") or 0.0)


def _apply_finance_formulas(rows_map: Dict[str, Dict[str, Any]], days: List[str]) -> None:
    """Р¤РёРЅР°Р»СЊРЅС‹Рµ С„РѕСЂРјСѓР»С‹ вЂ” РєРѕРїРёСЏ РёР· build_rows_map_for_month (report_services.py:540-610)."""
    set_row_from_formula(rows_map, "sales_total", days,
        lambda day: rows_map["revenue"]["daily"][day] - rows_map["returns_revenue"]["daily"][day])
    set_row_from_formula(rows_map, "returns_total", days,
        lambda day: rows_map["returns_revenue"]["daily"][day])
    set_row_from_formula(rows_map, "revenue_sales", days,
        lambda day: rows_map["sales_total"]["daily"][day])
    set_row_from_formula(rows_map, "delivery_services_total", days,
        lambda day: rows_map["courier_departure"]["daily"][day]
        + rows_map["dropoff_processing"]["daily"][day]
        + rows_map["logistics"]["daily"][day]
        + rows_map["reverse_logistics"]["daily"][day]
        + rows_map["pickup_courier_delivery"]["daily"][day]
        + rows_map["pickup_processing"]["daily"][day])
    set_row_from_formula(rows_map, "agent_services_total", days,
        lambda day: rows_map["star_products"]["daily"][day]
        + rows_map["delivery_to_pickup"]["daily"][day]
        + rows_map["partner_returns_processing"]["daily"][day]
        + rows_map["acquiring"]["daily"][day]
        + rows_map["partner_dropoff_processing"]["daily"][day]
        + rows_map["partner_packaging"]["daily"][day]
        + rows_map["temporary_partner_storage"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_cargo_processing", days,
        lambda day: rows_map["piece_acceptance"]["daily"][day]
        + rows_map["zone_sorting"]["daily"][day]
        + rows_map["excess_processing"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_acceptance_services", days,
        lambda day: rows_map["fbo_cargo_processing"]["daily"][day]
        + rows_map["fbo_booking_slot_staff"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_delivery_to_warehouse", days,
        lambda day: rows_map["cross_docking"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_storage_services", days,
        lambda day: rows_map["warehouse_placement"]["daily"][day]
        + rows_map["valid_preparation"]["daily"][day]
        + rows_map["ozon_delivery_to_pvz"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_services_total", days,
        lambda day: rows_map["fbo_acceptance_services"]["daily"][day]
        + rows_map["fbo_delivery_to_warehouse"]["daily"][day]
        + rows_map["fbo_storage_services"]["daily"][day])
    set_row_from_formula(rows_map, "promotion_total", days,
        lambda day: rows_map["premium_plus_subscription"]["daily"][day]
        + rows_map["pay_per_click"]["daily"][day]
        + rows_map["review_points"]["daily"][day])
    set_row_from_formula(rows_map, "penalties_total", days,
        lambda day: rows_map["penalty_non_recommended_slot"]["daily"][day])
    set_row_from_formula(rows_map, "other_services_misc", days,
        lambda day: rows_map["utilization"]["daily"][day]
        + rows_map["packaging_materials"]["daily"][day]
        + rows_map["operational_errors"]["daily"][day]
        + rows_map["temporary_sc_storage"]["daily"][day])
    set_row_from_formula(rows_map, "other_services", days,
        lambda day: rows_map["penalties_total"]["daily"][day]
        + rows_map["other_services_misc"]["daily"][day])
    set_row_from_formula(rows_map, "ozon_fee_total", days,
        lambda day: rows_map["sale_commission"]["daily"][day]
        - rows_map["return_commission"]["daily"][day])
    set_row_from_formula(rows_map, "all_expenses", days,
        lambda day: rows_map["ozon_fee_total"]["daily"][day]
        + rows_map["delivery_services_total"]["daily"][day]
        + rows_map["agent_services_total"]["daily"][day]
        + rows_map["fbo_services_total"]["daily"][day]
        + rows_map["promotion_total"]["daily"][day]
        + rows_map["other_services"]["daily"][day])
    set_row_from_formula(rows_map, "marketplace_expenses", days,
        lambda day: rows_map["returns_revenue"]["daily"][day]
        + rows_map["ozon_fee_total"]["daily"][day]
        + rows_map["delivery_services_total"]["daily"][day]
        + rows_map["agent_services_total"]["daily"][day]
        + rows_map["fbo_services_total"]["daily"][day]
        + rows_map["promotion_total"]["daily"][day]
        + rows_map["other_services"]["daily"][day])
    set_row_from_formula(rows_map, "marketplace_expenses_pct", days,
        lambda day: safe_divide(rows_map["marketplace_expenses"]["daily"][day], rows_map["revenue_sales"]["daily"][day]))
    marketing_daily = {
        day: (
            rows_map["pay_per_click"]["daily"][day]
            + rows_map["review_points"]["daily"][day]
            + rows_map["premium_plus_subscription"]["daily"][day]
        )
        for day in days
    }
    set_row_from_formula(rows_map, "marketing_pct", days,
        lambda day: safe_divide(marketing_daily[day], rows_map["revenue_sales"]["daily"][day]))
    set_row_from_formula(rows_map, "accrued", days,
        lambda day: rows_map["revenue_sales"]["daily"][day] - rows_map["marketplace_expenses"]["daily"][day])
    set_row_from_formula(rows_map, "gross_profit", days,
        lambda day: rows_map["accrued"]["daily"][day] - rows_map["material_cost"]["daily"][day])
    set_row_from_formula(rows_map, "gross_profit_pct_oz", days,
        lambda day: safe_divide(rows_map["gross_profit"]["daily"][day], rows_map["revenue_sales"]["daily"][day]))
    set_row_from_formula(rows_map, "gross_profit_pct_accrued", days,
        lambda day: safe_divide(rows_map["gross_profit"]["daily"][day], rows_map["accrued"]["daily"][day]))

    cumulative_revenue = 0.0
    cumulative_gross = 0.0
    for day in days:
        cumulative_revenue += rows_map["revenue_sales"]["daily"][day]
        cumulative_gross += rows_map["gross_profit"]["daily"][day]
        rows_map["revenue_cumulative"]["daily"][day] = cumulative_revenue
        rows_map["gross_profit_cumulative"]["daily"][day] = cumulative_gross
    rows_map["revenue_cumulative"]["total"] = None
    rows_map["gross_profit_cumulative"]["total"] = None
    rows_map["marketplace_expenses_pct"]["total"] = safe_divide(
        rows_map["marketplace_expenses"]["total"], rows_map["revenue_sales"]["total"]
    )
    rows_map["marketing_pct"]["total"] = safe_divide(
        sum(marketing_daily[day] for day in days), rows_map["revenue_sales"]["total"]
    )
    rows_map["gross_profit_pct_oz"]["total"] = safe_divide(
        rows_map["gross_profit"]["total"], rows_map["revenue_sales"]["total"]
    )
    rows_map["gross_profit_pct_accrued"]["total"] = safe_divide(
        rows_map["gross_profit"]["total"], rows_map["accrued"]["total"]
    )

    revenue_plan_total = float(rows_map["revenue_plan"]["daily"][days[-1]]) if days else 0.0
    gross_profit_plan_total = revenue_plan_total * 0.20  # РіСЂСѓР±С‹Р№ placeholder, v1 РґРµР»Р°РµС‚ С‚Р°РєР¶Рµ
    if days:
        daily_gross_plan = gross_profit_plan_total / len(days)
        cumulative_gross_plan = 0.0
        for day in days:
            cumulative_gross_plan += daily_gross_plan
            rows_map["gross_profit_plan"]["daily"][day] = cumulative_gross_plan
    rows_map["gross_profit_plan"]["total"] = None


async def get_finance_report_accrual_data(
    conn: asyncpg.Connection,
    month_value: str,
) -> Dict[str, Any]:
    month_bounds(month_value)
    year_str, month_str = month_value.split("-", 1)
    year = int(year_str)
    month = int(month_str)

    # 1. Р‘Р°Р·РѕРІС‹Р№ v1-СЂР°СЃС‡С‘С‚ вЂ” РёСЃРїРѕР»СЊР·СѓРµРј РґР»СЏ: days, revenue_plan, advertising С„Р°РєС‚, prev_month_pcts
    cash_rows_map, days = await build_rows_map_for_month(conn, month_value)

    # 2. Р’С…РѕРґРЅС‹Рµ РґР°РЅРЅС‹Рµ РґР»СЏ accrual
    ref_date = datetime.now(MSK).date()
    orders = await load_orders_for_month(conn, month_value)
    rates = await compute_loss_rates(conn, ref_date)
    unit_costs = await compute_unit_costs_global(conn, ref_date)
    cost_prices = await load_cost_prices(conn)

    # 3. РРЅРёС†РёР°Р»РёР·Р°С†РёСЏ accrual rows_map
    rows_map = _init_accrual_rows_map(days)

    # 4. РџР»Р°РЅ РІС‹СЂСѓС‡РєРё вЂ” РєРѕРїРёСЂСѓРµРј РЅР°РєРѕРїРёС‚РµР»СЊРЅРѕ РёР· v1
    _copy_from_cash(rows_map, cash_rows_map, ["revenue_plan"], days)

    # 5. Р РµРєР»Р°РјР° вЂ” С„Р°РєС‚ РёР· v1
    _copy_from_cash(rows_map, cash_rows_map,
                    ["pay_per_click", "review_points", "premium_plus_subscription", "ad_spend"], days)

    # 6. Р’РѕР·РІСЂР°С‚С‹-РІС‹СЂСѓС‡РєР° / РІРѕР·РІСЂР°С‚С‹-Р±Р°Р»Р»С‹ вЂ” РѕСЃС‚Р°РІР»СЏРµРј 0 РІ accrual
    # (РІРѕР·РІСЂР°С‚С‹ СѓР¶Рµ СѓС‡С‚РµРЅС‹ С‡РµСЂРµР· buyout_loss_rate РІ paid_factor; РЅРµ РґРІРѕР№РЅРѕР№ СЃС‡С‘С‚)

    # 7. РџСЂРёРјРµРЅСЏРµРј Р·Р°РєР°Р·С‹
    default_rates = rates["per_scheme"].get("fbo", {"cancel_rate": 0.0, "buyout_loss_rate": 0.0})
    gross_daily: Dict[str, float] = {}
    for row in orders:
        offer = _product_key(row.get("sku"), row.get("offer_id"))
        day = row["day"]
        if day not in rows_map["revenue"]["daily"]:
            continue
        sku_rate = rates["per_sku"].get(offer, {
            "cancel_rate": default_rates["cancel_rate"],
            "buyout_loss_rate": default_rates["buyout_loss_rate"],
        })
        paid_factor = (1.0 - float(sku_rate["cancel_rate"])) * (1.0 - float(sku_rate["buyout_loss_rate"]))
        gross_qty = float(row["quantity"] or 0)
        gross_revenue = float(row["gross_revenue"] or 0)
        expected_units = gross_qty * paid_factor
        expected_revenue = gross_revenue * paid_factor

        # revenue/ordered_units вЂ” netto (РїРѕСЃР»Рµ РїСЂРѕРіРЅРѕР·Р° РѕС‚РјРµРЅ/РЅРµРІС‹РєСѓРїРѕРІ),
        # С‡С‚РѕР±С‹ marketplace_expenses/gross_profit СЃРѕС€Р»РёСЃСЊ Р±РµР· РґРІРѕР№РЅРѕРіРѕ СѓС‡С‘С‚Р°.
        # Gross-Р·РЅР°С‡РµРЅРёСЏ (РєР°Рє РІ Ozon UI В«Р—Р°РєР°Р·Р°РЅРѕВ») вЂ” РІ accrual_gross РґР»СЏ СЃРїСЂР°РІРєРё.
        rows_map["revenue"]["daily"][day] += expected_revenue
        rows_map["ordered_units"]["daily"][day] += gross_qty  # gross вЂ” "Р·Р°РєР°Р·Р°РЅРѕ С€С‚"

        # Р Р°СЃС…РѕРґС‹ вЂ” СѓРґРµР»СЊРЅС‹Рµ Г— РїСЂРѕРіРЅРѕР· РІС‹РєСѓРїРѕРІ
        for key in ACCRUAL_COST_ROW_KEYS:
            if key in rows_map:
                rows_map[key]["daily"][day] += expected_units * float(unit_costs.get(key, 0.0))

        # РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ вЂ” РЅР° РїСЂРѕРіРЅРѕР· РІС‹РєСѓРїРѕРІ
        cost_price = float(cost_prices.get(offer, 0.0))
        rows_map["material_cost"]["daily"][day] += expected_units * cost_price

        # РЎРѕР±РёСЂР°РµРј gross РґР»СЏ РѕС‚РґРµР»СЊРЅРѕРіРѕ Р±Р»РѕРєР° В«Р—Р°РєР°Р·Р°РЅРѕВ»
        gross_daily[day] = gross_daily.get(day, 0.0) + gross_revenue

    # 8. РџРµСЂРµСЃС‡С‘С‚ totals РґР»СЏ value-СЃС‚СЂРѕРє
    for row_key, row_data in rows_map.items():
        if row_data.get("kind") == "value":
            recalculate_row_total(row_data, days)

    # 9. Р¤РёРЅР°Р»СЊРЅС‹Рµ С„РѕСЂРјСѓР»С‹
    _apply_finance_formulas(rows_map, days)

    # 10. KPI СЃРІРѕРґРєР°
    prev_month_pcts = await load_prev_month_pcts(conn, year, month)
    marketing_daily = {
        day: (
            rows_map["pay_per_click"]["daily"][day]
            + rows_map["review_points"]["daily"][day]
            + rows_map["premium_plus_subscription"]["daily"][day]
        )
        for day in days
    }
    revenue_plan_total = float(rows_map["revenue_plan"]["daily"][days[-1]]) if days else 0.0
    now_msk = datetime.now(MSK)
    month_msk = month_start_msk(month_value)
    plan_editable = month_msk.year == now_msk.year and month_msk.month == now_msk.month

    notes = finance_report_notes() + ACCRUAL_NOTES

    return {
        "month": month_value,
        "days": days,
        "rows": render_finance_rows(rows_map, days),
        "notes": notes,
        "variant": "accrual",
        "kpi_summary": build_kpi_summary(
            month_value=month_value,
            rows_map=rows_map,
            marketing_daily=marketing_daily,
            revenue_plan_total=revenue_plan_total,
            plan_editable=plan_editable,
            prev_month_pcts=prev_month_pcts,
        ),
        "plan": {
            "revenue_mp": revenue_plan_total,
            "editable": plan_editable,
        },
        "loss_rates_summary": {
            "fbo": rates["per_scheme"]["fbo"],
            "fbs": rates["per_scheme"]["fbs"],
            "skus_with_own_rates": sum(1 for v in rates["per_sku"].values() if v["source"] == "sku"),
            "skus_with_fallback": sum(1 for v in rates["per_sku"].values() if v["source"] == "fallback_scheme"),
        },
        "settlement": _settlement_summary(cash_rows_map, rows_map),
        "gross_ordered": {
            "daily": [gross_daily.get(day, 0.0) for day in days],
            "total": sum(gross_daily.values()),
        },
        "kpi_tiles": await _build_kpi_tiles(conn, rows_map, days, ref_date),
    }


async def _build_kpi_tiles(
    conn: asyncpg.Connection,
    rows_map: Dict[str, Dict[str, Any]],
    days: List[str],
    ref_date: date,
) -> Dict[str, Any]:
    """4 KPI РґР»СЏ РїР»РёС‚РѕРє РЅР°Рґ Finance Report accrual."""
    gross_daily = rows_map["gross_profit"]["daily"]
    revenue_daily = rows_map["revenue_sales"]["daily"]

    # 1. РџСЂРёР±С‹Р»СЊ Р·Р° РјРµСЃСЏС† (СѓР¶Рµ РµСЃС‚СЊ total)
    month_gp = float(rows_map["gross_profit"]["total"] or 0.0)
    month_rev = float(rows_map["revenue_sales"]["total"] or 0.0)
    month_margin_pct = (month_gp / month_rev) if month_rev else 0.0

    # 2. РњР°СЂР¶Р° 7Рґ vs РїСЂРµРґ. 7Рґ (С‚РѕР»СЊРєРѕ Р·Р°РІРµСЂС€С‘РЅРЅС‹Рµ РґРЅРё в‰¤ ref_date)
    today_str = ref_date.strftime("%Y-%m-%d")
    past_days = [d for d in days if d < today_str]  # РёСЃРєР»СЋС‡Р°РµРј СЃРµРіРѕРґРЅСЏ
    last_7 = past_days[-7:] if len(past_days) >= 7 else past_days
    prev_7 = past_days[-14:-7] if len(past_days) >= 14 else []

    def period_margin(day_list):
        gp = sum(float(gross_daily.get(d, 0.0) or 0.0) for d in day_list)
        rev = sum(float(revenue_daily.get(d, 0.0) or 0.0) for d in day_list)
        pct = (gp / rev) if rev else 0.0
        return {"gp": gp, "revenue": rev, "margin_pct": pct, "days": len(day_list)}

    last_7_m = period_margin(last_7)
    prev_7_m = period_margin(prev_7)
    margin_delta_pp = (last_7_m["margin_pct"] - prev_7_m["margin_pct"]) * 100.0 if prev_7 else 0.0

    # 3. РЎР»РµРґСѓСЋС‰Р°СЏ РІС‹РїР»Р°С‚Р° вЂ” РїСЂРѕРіРЅРѕР· Ozon payout РґР»СЏ Р·Р°РєР°Р·РѕРІ 7-РґРЅРµРІРЅРѕРіРѕ РѕРєРЅР°.
    # Р¦РёРєР» Ozon: РѕС‚С‡С‘С‚РЅС‹Р№ РїРµСЂРёРѕРґ 7 РґРЅРµР№ в†’ РІС‹РїР»Р°С‚Р° ~ С‡РµСЂРµР· 24 РґРЅСЏ.
    # Р‘РµСЂС‘Рј РїРѕСЃР»РµРґРЅРёР№ Р·Р°РІРµСЂС€С‘РЅРЅС‹Р№ 7-РґРЅРµРІРЅС‹Р№ Р±Р»РѕРє Рё РїСЂРёРјРµРЅСЏРµРј paid_factor + РєРѕРјРёСЃСЃРёСЋ.
    payouts = await _forecast_next_payouts(conn, ref_date)

    # 4. РЈР±С‹С‚РѕС‡РЅС‹Рµ SKU Р·Р° РјРµСЃСЏС† (accrual gross_profit < 0)
    # РџРѕР»СѓС‡Р°РµРј С‡РµСЂРµР· СЃРµСЂРІРёСЃ per-SKU (РёСЃРїРѕР»СЊР·СѓРµРј С‚Рµ Р¶Рµ РґР°РЅРЅС‹Рµ)
    loss_skus = await _count_loss_skus_for_month(conn, days)

    return {
        "month_profit": {
            "gross_profit": month_gp,
            "revenue": month_rev,
            "margin_pct": month_margin_pct,
        },
        "margin_7d": {
            "current": last_7_m,
            "previous": prev_7_m,
            "delta_pp": margin_delta_pp,
        },
        "next_payouts": payouts,
        "loss_skus": loss_skus,
    }


async def _forecast_next_payouts(
    conn: asyncpg.Connection,
    ref_date: date,
) -> List[Dict[str, Any]]:
    """РџСЂРѕРіРЅРѕР· 3 Р±Р»РёР¶Р°Р№С€РёС… РІС‹РїР»Р°С‚ Ozon.

    Ozon С†РёРєР»: РѕС‚С‡С‘С‚РЅС‹Р№ РїРµСЂРёРѕРґ РїРЅ-РІСЃ (7 РґРЅРµР№), РІС‹РїР»Р°С‚Р° ~С‡РµСЂРµР· 24 РґРЅСЏ РїРѕСЃР»Рµ РєРѕРЅС†Р° РїРµСЂРёРѕРґР°.
    Р”Р»СЏ РєР°Р¶РґРѕРіРѕ РїРµСЂРёРѕРґР° РїСЂРёРјРµРЅСЏРµРј per-SKU accrual-РјРѕРґРµР»СЊ (С‚Рµ Р¶Рµ rates/unit_costs),
    РїРѕР»СѓС‡Р°РµРј РїСЂРѕРіРЅРѕР· gross_profit, РґРёСЃРєРѕРЅС‚РёСЂСѓРµРј РЅР° РєРѕРјРёСЃСЃРёСЋ/РЅР°Р»РѕРі РґР»СЏ payout.
    """
    rates = await compute_loss_rates(conn, ref_date)
    unit_costs = await compute_unit_costs_global(conn, ref_date)
    cost_prices = await load_cost_prices(conn)
    default_rates = rates["per_scheme"].get("fbo", {"cancel_rate": 0.0, "buyout_loss_rate": 0.0})

    periods: List[Dict[str, Any]] = []
    # РќР°С…РѕРґРёРј РїРѕСЃР»РµРґРЅРµРµ РІРѕСЃРєСЂРµСЃРµРЅСЊРµ (РєРѕРЅРµС† РїРѕСЃР»РµРґРЅРµРіРѕ РїРѕР»РЅРѕРіРѕ РЅРµРґРµР»СЊРЅРѕРіРѕ РїРµСЂРёРѕРґР°)
    weekday = ref_date.weekday()  # 0=РїРЅ
    if weekday == 6:
        last_sunday = ref_date
    else:
        last_sunday = ref_date - timedelta(days=weekday + 1)

    for i in range(3):
        period_end = last_sunday - timedelta(days=i * 7)
        period_start = period_end - timedelta(days=6)
        # payout в‰€ period_end + 21 РґРµРЅСЊ (РїРѕ РЅР°Р±Р»СЋРґРµРЅРёСЋ Р·Р° Ozon)
        payout_date = period_end + timedelta(days=21)

        forecast = await _period_payout_forecast(
            conn, period_start, period_end, rates, unit_costs, cost_prices, default_rates
        )
        periods.append({
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "payout_date": payout_date.isoformat(),
            "forecast_amount": forecast["accrued"],
            "gross_profit": forecast["gross_profit"],
            "revenue_netto": forecast["revenue_netto"],
            "orders_count": forecast["orders_count"],
        })
    periods.sort(key=lambda p: p["payout_date"])
    return periods


async def _period_payout_forecast(
    conn: asyncpg.Connection,
    period_start: date,
    period_end: date,
    rates: Dict[str, Any],
    unit_costs: Dict[str, float],
    cost_prices: Dict[str, float],
    default_rates: Dict[str, float],
) -> Dict[str, float]:
    """Per-SKU accrual-РїСЂРѕРіРЅРѕР· РґР»СЏ РЅРµРґРµР»СЊРЅРѕРіРѕ РїРµСЂРёРѕРґР°: РІРѕР·РІСЂР°С‰Р°РµС‚ accrued+gp+revenue."""
    orders = await conn.fetch(
        """
        SELECT
            oi.sku::bigint AS sku,
            min(oi.offer_id) AS offer_id,
            sum(coalesce(oi.quantity, 0))::float8 AS quantity,
            sum(coalesce(oi.quantity, 0) * coalesce(oi.price, 0))::float8 AS gross_revenue
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE o.created_at >= (($1::date) AT TIME ZONE 'Europe/Moscow')
          AND o.created_at <  (($2::date + interval '1 day') AT TIME ZONE 'Europe/Moscow')
          AND lower(coalesce(o.status, '')) = ANY($3::text[])
        GROUP BY 1
        HAVING oi.sku IS NOT NULL
        """,
        period_start, period_end, list(ORDER_STATUSES_INCLUDE),
    )

    total_accrued = 0.0
    total_gp = 0.0
    total_rev = 0.0
    total_orders = 0

    for r in orders:
        offer = _product_key(r.get("sku"), r.get("offer_id"))
        qty = float(r["quantity"] or 0.0)
        gross = float(r["gross_revenue"] or 0.0)
        if qty <= 0:
            continue
        total_orders += int(qty)

        sku_rate = rates["per_sku"].get(offer, default_rates)
        paid_factor = (1.0 - float(sku_rate["cancel_rate"])) * (1.0 - float(sku_rate["buyout_loss_rate"]))
        expected_units = qty * paid_factor
        expected_revenue = gross * paid_factor

        # РЈРґРµР»СЊРЅС‹Рµ СЂР°СЃС…РѕРґС‹ вЂ” РІСЃРµ РїРѕР»РѕР¶РёС‚РµР»СЊРЅС‹Рµ, СЃСѓРјРјР° СЌС‚Рѕ marketplace_expenses
        expenses = sum(expected_units * float(unit_costs.get(k, 0.0)) for k in ACCRUAL_COST_ROW_KEYS)

        cost_price = float(cost_prices.get(offer, 0.0))
        cogs = expected_units * cost_price

        accrued = expected_revenue - expenses
        gp = accrued - cogs

        total_accrued += accrued
        total_gp += gp
        total_rev += expected_revenue

    return {
        "accrued": total_accrued,
        "gross_profit": total_gp,
        "revenue_netto": total_rev,
        "orders_count": total_orders,
    }


async def _count_loss_skus_for_month(
    conn: asyncpg.Connection,
    days: List[str],
) -> Dict[str, Any]:
    """РљРѕР»РёС‡РµСЃС‚РІРѕ SKU СЃ РѕС‚СЂРёС†Р°С‚РµР»СЊРЅРѕР№ accrual gross_profit Р·Р° РїРµСЂРёРѕРґ.

    РџРµСЂРµРёСЃРїРѕР»СЊР·СѓРµС‚ per-SKU accrual-СЂР°СЃС‡С‘С‚. Р”Р»СЏ РїСЂРѕРёР·РІРѕРґРёС‚РµР»СЊРЅРѕСЃС‚Рё вЂ” СѓРїСЂРѕС‰С‘РЅРЅР°СЏ РІРµСЂСЃРёСЏ.
    """
    if not days:
        return {"count": 0, "top_offers": []}
    date_from = datetime.strptime(days[0], "%Y-%m-%d").replace(tzinfo=MSK).astimezone(timezone.utc)
    date_to = datetime.strptime(days[-1], "%Y-%m-%d").replace(tzinfo=MSK).astimezone(timezone.utc) + timedelta(days=1)
    data = await get_accruals_by_article_accrual_data(conn, date_from, date_to)
    loss_items = [it for it in data.get("items", []) if (it["values"].get("gross_profit", 0.0) or 0.0) < 0]
    loss_items.sort(key=lambda x: x["values"].get("gross_profit", 0.0))
    return {
        "count": len(loss_items),
        "top_offers": [
            {
                "offer_id": it["offer_id"],
                "gross_profit": it["values"].get("gross_profit", 0.0),
                "revenue": it["values"].get("revenue", 0.0),
                "ordered_units": it["values"].get("ordered_units", 0.0),
            }
            for it in loss_items[:5]
        ],
    }


async def get_accruals_by_article_accrual_data(
    conn: asyncpg.Connection,
    date_from: datetime,
    date_to_exclusive: datetime,
) -> Dict[str, Any]:
    """РђРЅР°Р»РѕРі /api/accruals-comp-by-article, РЅРѕ Р·РЅР°С‡РµРЅРёСЏ РїРѕ РєР°Р¶РґРѕРјСѓ SKU СЃС‡РёС‚Р°СЋС‚СЃСЏ
    РёР· Р·Р°РєР°Р·РѕРІ (fact_order_items) + СЃРёРЅС‚РµС‚РёС‡РµСЃРєРёРµ СѓРґРµР»СЊРЅС‹Рµ СЂР°СЃС…РѕРґС‹ 30Рґ + cost_price.

    Р РµС€Р°РµС‚ РїСЂРѕР±Р»РµРјСѓ РЅРѕРІС‹С…/РјР°Р»РѕРїСЂРѕРґР°РІР°РµРјС‹С… SKU, РїРѕ РєРѕС‚РѕСЂС‹Рј РЅР°С‡РёСЃР»РµРЅРёР№ РµС‰С‘ РЅРµС‚:
    cash-СЂРµР¶РёРј РїРѕРєР°Р¶РµС‚ Р»РѕР¶РЅРѕ-РїРѕР·РёС‚РёРІРЅСѓСЋ РјР°СЂР¶Сѓ (РІС‹СЂСѓС‡РєР° РµСЃС‚СЊ, СЂР°СЃС…РѕРґС‹ РЅРµ РЅР°С‡РёСЃР»РµРЅС‹),
    accrual-СЂРµР¶РёРј РґР°С‘С‚ С‡РµСЃС‚РЅС‹Р№ РїСЂРѕРіРЅРѕР·.
    """
    ref_date = datetime.now(MSK).date()
    rates = await compute_loss_rates(conn, ref_date)
    expense_shares_global = await compute_cash_expense_shares_30d(conn, ref_date)
    cash_shares_by_offer = await load_cash_expense_shares_30d_by_offer(ref_date - timedelta(days=29), ref_date)
    cost_prices = await load_cost_prices(conn)

    # Р—Р°РєР°Р·С‹ РїРµСЂРёРѕРґР° РїРѕ SKU
    order_rows = await conn.fetch(
        """
        SELECT
            oi.sku::bigint AS sku,
            min(oi.offer_id) AS offer_id,
            sum(coalesce(oi.quantity, 0))::float8 AS quantity,
            sum(coalesce(oi.quantity, 0) * coalesce(oi.price, 0))::float8 AS gross_revenue
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE o.created_at >= $1
          AND o.created_at <  $2
        GROUP BY 1
        HAVING oi.sku IS NOT NULL
        """,
        date_from,
        date_to_exclusive,
    )
    sku_identity_map: Dict[int, Dict[str, Any]] = {}
    known_skus = sorted(
        {
            int(row["sku"])
            for row in order_rows
            if row.get("sku") is not None
        }
    )
    if known_skus:
        sku_identity_map = await load_sku_identity_map(conn, known_skus)

    # Р’РѕР·РІСЂР°С‚С‹ РїРµСЂРёРѕРґР° РїРѕ SKU вЂ” РёСЃРєР»СЋС‡Р°РµРј РІРѕР·РІСЂР°С‚С‹ РѕС‚РјРµРЅС‘РЅРЅС‹С… Р·Р°РєР°Р·РѕРІ
    # (РѕРЅРё СѓР¶Рµ РѕС‚СЂР°Р¶РµРЅС‹ РІ СЃС‚Р°С‚СѓСЃРµ 'РѕС‚РјРµРЅС‘РЅ' Сѓ fact_orders Рё РЅРµ СЏРІР»СЏСЋС‚СЃСЏ "РЅРµРІС‹РєСѓРїРѕРј").
    return_rows = await conn.fetch(
        """
        WITH returns_all AS (
            SELECT sku, posting_number, quantity, returned_at FROM returns
            WHERE returned_at >= $1 AND returned_at < $2
            UNION ALL
            SELECT sku, posting_number, quantity, returned_at FROM returns_fbo
            WHERE returned_at >= $1 AND returned_at < $2
        )
        SELECT
            r.sku::bigint AS sku,
            sum(coalesce(r.quantity, 0))::float8 AS returned_qty
        FROM returns_all r
        LEFT JOIN fact_orders o ON o.posting_number = r.posting_number
        WHERE lower(coalesce(o.status, '')) NOT IN ('отменён', 'отменен')
        GROUP BY 1
        HAVING r.sku IS NOT NULL
        """,
        date_from,
        date_to_exclusive,
    )
    returns_by_offer: Dict[str, float] = {
        _product_key(r.get("sku"), None): float(r["returned_qty"] or 0.0)
        for r in return_rows
        if _product_key(r.get("sku"), None)
    }

    default_rates = rates["per_scheme"].get("fbo", {"cancel_rate": 0.0, "buyout_loss_rate": 0.0})
    items: List[Dict[str, Any]] = []

    for r in order_rows:
        offer = _product_key(r.get("sku"), r.get("offer_id"))
        sku_val = normalize_sku_value(r.get("sku"))
        mapped_offer = normalize_offer_id(
            (sku_identity_map.get(int(sku_val or 0)) or {}).get("offer_id")
        ) if sku_val is not None else ""
        raw_offer = normalize_offer_id(r.get("offer_id"))
        offer_display = mapped_offer or raw_offer or offer
        qty = float(r["quantity"] or 0.0)
        gross_revenue = float(r["gross_revenue"] or 0.0)
        if qty <= 0:
            continue

        sku_rate = rates["per_sku"].get(offer, {
            "cancel_rate": default_rates["cancel_rate"],
            "buyout_loss_rate": default_rates["buyout_loss_rate"],
        })
        paid_factor = (1.0 - float(sku_rate["cancel_rate"])) * (1.0 - float(sku_rate["buyout_loss_rate"]))
        expected_units = qty * paid_factor
        expected_revenue = gross_revenue * paid_factor

        # РРЅРёС†РёР°Р»РёР·РёСЂСѓРµРј values РґР»СЏ SKU
        values: Dict[str, float] = {}
        for row_meta in FINANCE_REPORT_ROWS:
            if row_meta["kind"] in {"section", "spacer"}:
                continue
            values[row_meta["key"]] = 0.0

        # РћСЃРЅРѕРІРЅС‹Рµ РїРѕР»СЏ
        # Р’ СЌС‚РѕРј РѕС‚С‡С‘С‚Рµ СЃС‚СЂСѓРєС‚СѓСЂР° 1-РІ-1 СЃ cash-РІРµСЂСЃРёРµР№: РІС‹СЂСѓС‡РєР°/Р·Р°РєР°Р·Р°РЅРѕ = gross.
        # РњРµРЅСЏСЋС‚СЃСЏ С‚РѕР»СЊРєРѕ СЂР°СЃС…РѕРґС‹ (СЃРёРЅС‚РµС‚РёРєР° РЅР° expected РІС‹РєСѓРїС‹).
        values["ordered_units"] = qty  # gross вЂ” "Р·Р°РєР°Р·Р°РЅРѕ"
        values["returned_units"] = float(returns_by_offer.get(offer, 0.0))  # С„Р°РєС‚ РІРѕР·РІСЂР°С‚РѕРІ РїРµСЂРёРѕРґР°
        values["returns_pct"] = (
            values["returned_units"] / qty if qty > 0 else 0.0
        )
        values["revenue"] = gross_revenue  # gross вЂ” СЃСѓРјРјР° СЂР°Р·РјРµС‰С‘РЅРЅС‹С… Р·Р°РєР°Р·РѕРІ
        values["returns_revenue"] = 0.0  # РІРѕР·РІСЂР°С‚С‹ РІС‹СЂСѓС‡РєРё РѕС‚РґРµР»СЊРЅРѕР№ СЃС‚Р°С‚СЊС‘Р№ РЅРµ СѓС‡РёС‚С‹РІР°РµРј
        offer_shares = cash_shares_by_offer.get(offer) or expense_shares_global

        # Р Р°СЃС…РѕРґС‹ РІ СЂРµР¶РёРјРµ "РџРѕ Р·Р°РєР°Р·Р°Рј":
        # РґРѕР»СЏ РєР°Р¶РґРѕР№ СЃС‚Р°С‚СЊРё РёР· РєР°СЃСЃС‹ Р·Р° 30Рґ Г— СЃСѓРјРјР° Р·Р°РєР°Р·Р°РЅРЅС‹С… С‚РѕРІР°СЂРѕРІ РїРµСЂРёРѕРґР°.
        for key in ACCRUAL_SHARE_KEYS:
            if key in values:
                values[key] = gross_revenue * float(offer_shares.get(key, 0.0))
        # Маркетинг и реклама в режиме "По заказам" считаются по долям из кассы (30д),
        # применённым к заказанной выручке артикула.
        values["pay_per_click"] = values["ad_spend"]
        values["review_points"] = 0.0
        values["premium_plus_subscription"] = 0.0

        # РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ
        values["material_cost"] = expected_units * float(cost_prices.get(offer, 0.0))

        # РџСЂРёРјРµРЅСЏРµРј С‚Рµ Р¶Рµ С„РѕСЂРјСѓР»С‹ Р°РіСЂРµРіР°С†РёРё, С‡С‚Рѕ РІ Finance Report
        _apply_formulas_to_values(values)

        items.append({
            "offer_id": offer_display,
            "offer_id_normalized": normalize_offer_id(offer_display) or offer,
            "values": values,
            "sku_source": sku_rate.get("source", "fallback_scheme"),
            "paid_factor": paid_factor,
        })

    # РЎРѕСЂС‚РёСЂРѕРІРєР° РїРѕ revenue (РєР°Рє РІ cash-РІРµСЂСЃРёРё)
    items.sort(key=lambda x: x["values"].get("revenue", 0.0), reverse=True)

    # РљРѕР»РѕРЅРєРё вЂ” С‚РѕС‡РЅС‹Р№ РЅР°Р±РѕСЂ, С‡С‚Рѕ РѕС‚РґР°С‘С‚ cash-СЌРЅРґРїРѕРёРЅС‚ (37 С€С‚).
    # Р”РµСЂР¶РёРј С‚РѕС‚ Р¶Рµ РїРѕСЂСЏРґРѕРє/С„РѕСЂРјР°С‚ С‡С‚РѕР±С‹ frontend renderAccrualsByArticle
    # РѕС‚СЂРёСЃРѕРІР°Р» РёРґРµРЅС‚РёС‡РЅСѓСЋ С‚Р°Р±Р»РёС†Сѓ.
    CASH_COLUMNS_KEYS = [
        ("ordered_units", "Продано", "number"),
        ("returned_units", "Возвраты", "number"),
        ("returns_pct", "Возвраты %", "percent"),
        ("revenue_sales", "−Выручка / продажи - возвраты", "number"),
        ("marketplace_expenses", "Расходы МП", "number"),
        ("revenue", "Выручка", "number"),
        ("returns_total", "−Возвраты, руб.", "number"),
        ("ozon_fee_total", "−Вознаграждение Ozon", "number"),
        ("sale_commission", "Вознаграждение за продажу", "number"),
        ("return_commission", "Возврат вознаграждения", "number"),
        ("delivery_services_total", "−Услуги доставки", "number"),
        ("dropoff_processing", "Обработка отправления Drop-off", "number"),
        ("logistics", "Логистика", "number"),
        ("reverse_logistics", "Обратная логистика", "number"),
        ("agent_services_total", "−Услуги партнёров", "number"),
        ("partner_returns_processing", "Обработка возвратов, отмен и невыкупов партнёрами", "number"),
        ("temporary_partner_storage", "Временное размещение товара партнёрами", "number"),
        ("partner_dropoff_processing", "Обработка отправления Drop-off партнёрами", "number"),
        ("delivery_to_pickup", "Доставка до места выдачи", "number"),
        ("acquiring", "Эквайринг", "number"),
        ("fbo_services_total", "−Услуги FBO", "number"),
        ("cross_docking", "Кросс-докинг", "number"),
        ("fbo_acceptance_services", "Услуги приемки", "number"),
        ("fbo_delivery_to_warehouse", "Доставка до склада", "number"),
        ("piece_acceptance", "Обработка товара в составе грузоместа: Поштучная приёмка", "number"),
        ("promotion_total", "−Реклама (НАЧ)", "number"),
        ("ad_spend", "−Реклама", "number"),
        ("premium_plus_subscription", "Подписка Premium Plus", "number"),
        ("pay_per_click", "Оплата за клик", "number"),
        ("review_points", "Баллы за отзывы", "number"),
        ("marketplace_expenses_pct", "Расходы МП, %", "percent"),
        ("marketing_pct", "Маркетинг, %", "percent"),
        ("accrued", "Начислено", "number"),
        ("material_cost", "Себестоимость - возвраты", "number"),
        ("gross_profit", "Валовая прибыль", "number"),
        ("gross_profit_pct_oz", "Валовая прибыль, % к OZ", "percent"),
        ("gross_profit_pct_accrued", "Валовая прибыль, % к Р/С", "percent"),
    ]
    columns = [{"key": k, "label": l, "format": f} for k, l, f in CASH_COLUMNS_KEYS]

    # РС‚РѕРіРѕРІР°СЏ СЃС‚СЂРѕРєР° вЂ” СЃСѓРјРјР° РїРѕ РІСЃРµРј SKU РґР»СЏ РєР°Р¶РґРѕР№ РјРµС‚СЂРёРєРё
    total_row: Dict[str, float] = {}
    # РЎРЅР°С‡Р°Р»Р° СЃРѕР±РёСЂР°РµРј СЃСѓРјРјС‹ РїСЂРѕСЃС‚С‹С… РјРµС‚СЂРёРє
    all_keys = set()
    for it in items:
        all_keys.update(it["values"].keys())
    for key in all_keys:
        total_row[key] = sum(float(it["values"].get(key, 0.0) or 0.0) for it in items)
    # Р”Р»СЏ percent-РјРµС‚СЂРёРє РїРµСЂРµСЃС‡РёС‚С‹РІР°РµРј РѕС‚ РёС‚РѕРіРѕРІС‹С… С‡РёСЃР»РёС‚РµР»СЏ/Р·РЅР°РјРµРЅР°С‚РµР»СЏ
    if total_row.get("revenue_sales"):
        total_row["marketplace_expenses_pct"] = total_row["marketplace_expenses"] / total_row["revenue_sales"]
        total_row["marketing_pct"] = total_row.get("ad_spend", 0.0) / total_row["revenue_sales"]
        total_row["gross_profit_pct_oz"] = total_row["gross_profit"] / total_row["revenue_sales"]
    if total_row.get("accrued"):
        total_row["gross_profit_pct_accrued"] = total_row["gross_profit"] / total_row["accrued"]
    if total_row.get("ordered_units"):
        total_row["returns_pct"] = total_row.get("returned_units", 0.0) / total_row["ordered_units"]

    summary = {
        "date_from": date_from.astimezone(MSK).strftime("%Y-%m-%d"),
        "date_to": (date_to_exclusive - timedelta(days=1)).astimezone(MSK).strftime("%Y-%m-%d"),
        "mode": "accrual",
        "no_article_accruals_total": 0.0,
        "no_article_compensations_total": 0.0,
        "ads_finance_total": 0.0,
        "ads_source_total": 0.0,
        "ads_delta": 0.0,
        "total_row": total_row,
    }

    return {
        "count": len(items),
        "items": items,
        "summary": summary,
        "columns": columns,
        "variant": "accrual",
        "loss_rates_summary": {
            "fbo": rates["per_scheme"]["fbo"],
            "fbs": rates["per_scheme"]["fbs"],
        },
    }


def _apply_formulas_to_values(v: Dict[str, float]) -> None:
    """РџСЂРёРјРµРЅСЏРµС‚ Р°РіСЂРµРіРёСЂСѓСЋС‰РёРµ С„РѕСЂРјСѓР»С‹ v1 Finance Report Рє РїР»РѕСЃРєРѕРјСѓ dict-Сѓ РѕРґРЅРѕРіРѕ SKU."""
    v["sales_total"] = v.get("revenue", 0.0) - v.get("returns_revenue", 0.0)
    v["revenue_sales"] = v["sales_total"]
    v["returns_total"] = v.get("returns_revenue", 0.0)
    v["delivery_services_total"] = (
        v.get("courier_departure", 0.0) + v.get("dropoff_processing", 0.0)
        + v.get("logistics", 0.0) + v.get("reverse_logistics", 0.0)
        + v.get("pickup_courier_delivery", 0.0) + v.get("pickup_processing", 0.0)
    )
    v["agent_services_total"] = (
        v.get("star_products", 0.0) + v.get("delivery_to_pickup", 0.0)
        + v.get("partner_returns_processing", 0.0) + v.get("acquiring", 0.0)
        + v.get("partner_dropoff_processing", 0.0) + v.get("partner_packaging", 0.0)
        + v.get("temporary_partner_storage", 0.0)
    )
    v["fbo_cargo_processing"] = (
        v.get("piece_acceptance", 0.0) + v.get("zone_sorting", 0.0) + v.get("excess_processing", 0.0)
    )
    v["fbo_acceptance_services"] = v["fbo_cargo_processing"] + v.get("fbo_booking_slot_staff", 0.0)
    v["fbo_delivery_to_warehouse"] = v.get("cross_docking", 0.0)
    v["fbo_storage_services"] = (
        v.get("warehouse_placement", 0.0) + v.get("valid_preparation", 0.0) + v.get("ozon_delivery_to_pvz", 0.0)
    )
    v["fbo_services_total"] = (
        v["fbo_acceptance_services"] + v["fbo_delivery_to_warehouse"] + v["fbo_storage_services"]
    )
    v["promotion_total"] = (
        v.get("premium_plus_subscription", 0.0) + v.get("pay_per_click", 0.0) + v.get("review_points", 0.0)
    )
    v["penalties_total"] = v.get("penalty_non_recommended_slot", 0.0)
    v["other_services_misc"] = (
        v.get("utilization", 0.0) + v.get("packaging_materials", 0.0)
        + v.get("operational_errors", 0.0) + v.get("temporary_sc_storage", 0.0)
    )
    v["other_services"] = v["penalties_total"] + v["other_services_misc"]
    v["ozon_fee_total"] = v.get("sale_commission", 0.0) - v.get("return_commission", 0.0)
    v["all_expenses"] = (
        v["ozon_fee_total"] + v["delivery_services_total"] + v["agent_services_total"]
        + v["fbo_services_total"] + v["ad_spend"] + v["promotion_total"] + v["other_services"]
    )
    v["marketplace_expenses"] = (
        v.get("returns_revenue", 0.0) + v["ozon_fee_total"] + v["delivery_services_total"]
        + v["agent_services_total"] + v["fbo_services_total"] + v["ad_spend"] + v["promotion_total"] + v["other_services"]
    )
    v["marketplace_expenses_pct"] = (
        v["marketplace_expenses"] / v["revenue_sales"] if v.get("revenue_sales") else 0.0
    )
    marketing = v.get("ad_spend", 0.0)
    v["marketing_pct"] = marketing / v["revenue_sales"] if v.get("revenue_sales") else 0.0
    v["accrued"] = v["revenue_sales"] - v["marketplace_expenses"]
    v["gross_profit"] = v["accrued"] - v.get("material_cost", 0.0)
    v["gross_profit_pct_oz"] = (
        v["gross_profit"] / v["revenue_sales"] if v.get("revenue_sales") else 0.0
    )
    v["gross_profit_pct_accrued"] = (
        v["gross_profit"] / v["accrued"] if v.get("accrued") else 0.0
    )


def _settlement_summary(
    cash_rows_map: Dict[str, Dict[str, Any]],
    accrual_rows_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """РЎС…РѕРґРёРјРѕСЃС‚СЊ: СЃРєРѕР»СЊРєРѕ РёР· РїСЂРѕРіРЅРѕР·РЅРѕР№ accrual-РІР°Р»РѕРІРѕР№ СѓР¶Рµ РїСЂРёР»РµС‚РµР»Рѕ РєР°СЃСЃРѕР№."""
    def total(m, key):
        row = m.get(key) or {}
        t = row.get("total")
        return float(t) if isinstance(t, (int, float)) else 0.0

    return {
        "accrual_revenue": total(accrual_rows_map, "revenue_sales"),
        "cash_revenue": total(cash_rows_map, "revenue_sales"),
        "accrual_accrued": total(accrual_rows_map, "accrued"),
        "cash_accrued": total(cash_rows_map, "accrued"),
        "accrual_gross_profit": total(accrual_rows_map, "gross_profit"),
        "cash_gross_profit": total(cash_rows_map, "gross_profit"),
        "settled_pct_of_accrued": (
            total(cash_rows_map, "accrued") / total(accrual_rows_map, "accrued")
            if total(accrual_rows_map, "accrued") else 0.0
        ),
    }

