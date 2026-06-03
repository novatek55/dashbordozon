"""Dashboard routes/actions.py handlers."""
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import aiohttp
from aiohttp import web

from src.config import settings
from src.dashboard.constants import MSK, PROMO_EVENT_ADDED, PROMO_EVENT_REMOVED
from src.dashboard.helpers import (
    clean_nan_values, as_float, normalize_offer_id, parse_date_utc,
    _get_env_from_dotenv, _get_ozon_credentials, _calc_ad_kpis, load_sku_identity_map,
)


async def get_actions(request: web.Request) -> web.Response:
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    limit_raw = (request.query.get("limit") or "500").strip()

    try:
        limit = max(1, min(2000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    try:
        date_from = parse_date_utc(date_from_raw, end_of_day=False) if date_from_raw else None
        date_to_exclusive = parse_date_utc(date_to_raw, end_of_day=True) if date_to_raw else None
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)

    conditions: List[str] = []
    params: List[Any] = []
    idx = 1
    if date_from is not None:
        conditions.append(f"date_start >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to_exclusive is not None:
        conditions.append(f"date_start < ${idx}")
        params.append(date_to_exclusive)

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            action_id,
            title,
            action_type,
            status,
            is_participating,
            discount_percent,
            date_start,
            date_end,
            last_synced_at
        FROM promo_actions
        {where_sql}
        ORDER BY date_start DESC NULLS LAST
        LIMIT {limit}
    """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "action_id": r["action_id"],
                "title": r["title"],
                "action_type": r["action_type"],
                "status": r["status"],
                "is_participating": r["is_participating"],
                "discount_percent": float(r["discount_percent"]) if r["discount_percent"] is not None else None,
                "date_start": r["date_start"].isoformat() if r["date_start"] else None,
                "date_end": r["date_end"].isoformat() if r["date_end"] else None,
                "last_synced_at": r["last_synced_at"].isoformat() if r["last_synced_at"] else None,
            }
        )

    return web.json_response({"count": len(items), "items": items})


async def get_action_products(request: web.Request) -> web.Response:
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    limit_raw = (request.query.get("limit") or "500").strip()

    try:
        limit = max(1, min(2000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    try:
        date_from = parse_date_utc(date_from_raw, end_of_day=False) if date_from_raw else None
        date_to_exclusive = parse_date_utc(date_to_raw, end_of_day=True) if date_to_raw else None
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)

    conditions: List[str] = []
    params: List[Any] = []
    idx = 1
    if date_from is not None:
        conditions.append(f"a.date_start >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to_exclusive is not None:
        conditions.append(f"a.date_start < ${idx}")
        params.append(date_to_exclusive)

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            a.action_id AS ext_action_id,
            a.title AS action_title,
            a.action_type,
            p.sku,
            p.regular_price,
            p.action_price,
            p.discount_percent,
            p.is_participating,
            p.last_synced_at
        FROM promo_products p
        JOIN promo_actions a ON a.id = p.action_id
        {where_sql}
        ORDER BY a.date_start DESC NULLS LAST, p.id DESC
        LIMIT {limit}
    """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "action_id": r["ext_action_id"],
                "action_title": r["action_title"],
                "action_type": r["action_type"],
                "sku": r["sku"],
                "regular_price": float(r["regular_price"]) if r["regular_price"] is not None else None,
                "action_price": float(r["action_price"]) if r["action_price"] is not None else None,
                "discount_percent": float(r["discount_percent"]) if r["discount_percent"] is not None else None,
                "is_participating": r["is_participating"],
                "last_synced_at": r["last_synced_at"].isoformat() if r["last_synced_at"] else None,
            }
        )

    return web.json_response({"count": len(items), "items": items})


async def _fetch_accruals_for_period(
    base_url: str,
    date_from: date,
    date_to: date,
) -> Dict[str, Any]:
    """Р’РЅСѓС‚СЂРµРЅРЅРёР№ HTTP-РІС‹Р·РѕРІ Рє /api/accruals-comp-by-article Р·Р° РїРµСЂРёРѕРґ.

    Р’РѕР·РІСЂР°С‰Р°РµС‚ {items, summary, columns}. РћРґРёРЅ РІС‹Р·РѕРІ = РІСЃРµ С‚РѕРІР°СЂС‹ Р·Р° РїРµСЂРёРѕРґ.
    """
    url = f"{base_url}/api/accruals-comp-by-article"
    params = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "distribute_no_article": "1",
        "limit": "5000",
    }
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                txt = await resp.text()
                logger.warning(f"accruals endpoint returned {resp.status}: {txt[:200]}")
                return {"items": [], "summary": {}, "columns": []}
            return await resp.json()


def _norm_offer(value: Any) -> str:
    """РќРѕСЂРјР°Р»РёР·Р°С†РёСЏ offer_id РґР»СЏ СЃСЂР°РІРЅРµРЅРёСЏ: lower + collapse spaces."""
    import re as _re
    s = str(value or "").strip().lower()
    s = _re.sub(r"\s+", " ", s)
    return s


def _index_accrual_items_by_offer(accrual_response: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Индекс начислений по нескольким ключам: offer_id и sku:<id>."""
    out: Dict[str, Dict[str, Any]] = {}
    for item in accrual_response.get("items", []) or []:
        norm_offer = _norm_offer(item.get("offer_id"))
        norm_offer_id = _norm_offer(item.get("offer_id_normalized"))
        if norm_offer:
            out[norm_offer] = item
        if norm_offer_id:
            out[norm_offer_id] = item
    return out


def _find_accrual_item(
    accrual_index: Dict[str, Dict[str, Any]],
    offer_norm: str,
    sku_val: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Поиск начислений: сначала по offer_id, затем по sku-ключу."""
    if offer_norm:
        item = accrual_index.get(offer_norm)
        if item:
            return item
    if sku_val is not None:
        return accrual_index.get(f"sku:{int(sku_val)}")
    return None


async def get_actions_report(request: web.Request) -> web.Response:
    """РћС‚С‡С‘С‚ В«РђРєС†РёРёВ».

    РџР°СЂР°РјРµС‚СЂС‹:
      mode = current | simulation
      days_back = 30 | 60 (РґР»СЏ simulation)
      tax_rate = float (0..100)
    """
    from src.economics_engine import (
        ProductEconomicsBase,
        EconomicsScenario,
        calculate as econ_calculate,
        base_from_accrual_values,
    )

    mode = (request.query.get("mode") or "current").strip().lower()
    if mode not in ("current", "simulation"):
        return web.json_response({"error": "mode must be current or simulation"}, status=400)

    try:
        days_back = int(request.query.get("days_back") or "30")
        if days_back not in (30, 60):
            days_back = 30
    except ValueError:
        days_back = 30

    try:
        tax_rate = float(request.query.get("tax_rate") or "0")
    except ValueError:
        tax_rate = 0.0

    # Р‘РµСЂС‘Рј РїРѕСЂС‚ РёР· app config вЂ” РІРЅСѓС‚СЂРµРЅРЅРёР№ URL
    base_url = "http://127.0.0.1:8088"

    pool: asyncpg.Pool = request.app["pool"]
    today_msk = datetime.now(MSK).date()

    async with pool.acquire() as conn:
        # РњРёРЅРёРјР°Р»СЊРЅР°СЏ РґР°С‚Р° С‚СЂР°РЅР·Р°РєС†РёР№ вЂ” РґР»СЏ РѕРіСЂР°РЅРёС‡РµРЅРёСЏ РґРѕР°РєС†РёРѕРЅРЅРѕРіРѕ РїРµСЂРёРѕРґР°
        min_tx_row = await conn.fetchrow(
            "SELECT MIN(operation_date)::date AS min_date FROM transactions"
        )
        min_tx_date: Optional[date] = min_tx_row["min_date"] if min_tx_row and min_tx_row["min_date"] else None

        if mode == "current":
            # РўРѕР»СЊРєРѕ Р°РєС†РёРё РІ РєРѕС‚РѕСЂС‹С… СѓС‡Р°СЃС‚РІСѓРµРј, Рё РЅРµ Р·Р°РєРѕРЅС‡РёРІС€РёРµСЃСЏ СЃР»РёС€РєРѕРј РґР°РІРЅРѕ
            actions_rows = await conn.fetch(
                """
                SELECT id, action_id, title, action_type, date_start, date_end,
                       is_participating, participating_products_count, potential_products_count,
                       description, discount_type, discount_value, with_targeting, order_amount
                FROM promo_actions
                WHERE is_participating = true
                  AND (date_end IS NULL OR date_end >= now() - interval '90 days')
                ORDER BY date_start DESC NULLS LAST
                """
            )
        else:
            # РЎРёРјСѓР»СЏС†РёСЏ вЂ” РІСЃРµ Р°РєС‚РёРІРЅС‹Рµ Р°РєС†РёРё
            actions_rows = await conn.fetch(
                """
                SELECT id, action_id, title, action_type, date_start, date_end,
                       is_participating, participating_products_count, potential_products_count,
                       description, discount_type, discount_value, with_targeting, order_amount
                FROM promo_actions
                WHERE (date_end IS NULL OR date_end >= now())
                ORDER BY is_participating DESC, date_start DESC NULLS LAST
                """
            )

        if not actions_rows:
            return web.json_response({"mode": mode, "actions": [], "tax_rate": tax_rate, "days_back": days_back})

        action_pk_list = [int(r["id"]) for r in actions_rows]

        # РўРѕРІР°СЂС‹ Р°РєС†РёР№
        if mode == "current":
            product_filter_sql = "AND pp.is_participating = true"
        else:
            product_filter_sql = "AND (pp.is_participating = true OR pp.is_candidate = true)"

        product_rows = await conn.fetch(
            f"""
            WITH ranked AS (
                SELECT
                    ozon_product_id,
                    fbo_sku_id::bigint AS fbo_sku,
                    fbs_sku_id::bigint AS fbs_sku,
                    trim(both '''' from coalesce(offer_id, '')) AS offer_id,
                    product_name,
                    price_current,
                    row_number() OVER (
                        PARTITION BY ozon_product_id
                        ORDER BY (CASE WHEN coalesce(trim(offer_id), '') <> '' THEN 0 ELSE 1 END),
                                 last_synced_at DESC NULLS LAST
                    ) AS rn
                FROM report_products_items
                WHERE ozon_product_id IS NOT NULL
            ),
            sku_to_offer AS (
                SELECT ozon_product_id, fbo_sku, fbs_sku, offer_id, product_name, price_current
                FROM ranked
                WHERE rn = 1
            ),
            real_stocks AS (
                SELECT sku, coalesce(sum(available_stock_count), 0) AS real_stock
                FROM analytics_stocks
                GROUP BY sku
            )
            SELECT
                pp.action_id AS action_pk,
                pp.sku AS product_id,
                sto.fbo_sku AS fbo_sku,
                sto.offer_id AS offer_id,
                sto.product_name AS product_name,
                sto.price_current AS current_price,
                pp.regular_price,
                pp.action_price,
                pp.max_action_price,
                pp.discount_percent,
                pp.is_participating,
                pp.is_candidate,
                coalesce(rs.real_stock, pp.stock) AS stock,
                pp.min_stock,
                pp.current_boost,
                pp.min_boost,
                pp.max_boost,
                pp.first_seen_at,
                ev_added.detected_at AS event_added_at,
                ev_removed.detected_at AS event_last_removed_at
            FROM promo_products pp
            JOIN promo_actions pa ON pa.id = pp.action_id
            LEFT JOIN sku_to_offer sto ON sto.ozon_product_id = pp.sku
            LEFT JOIN real_stocks rs ON rs.sku = sto.fbo_sku
            LEFT JOIN LATERAL (
                SELECT detected_at FROM promo_product_events
                WHERE action_id = pa.action_id AND sku = pp.sku AND event_type = 'ADDED'
                ORDER BY detected_at DESC LIMIT 1
            ) ev_added ON true
            LEFT JOIN LATERAL (
                SELECT detected_at FROM promo_product_events
                WHERE action_id = pa.action_id AND sku = pp.sku AND event_type = 'REMOVED'
                ORDER BY detected_at DESC LIMIT 1
            ) ev_removed ON true
            WHERE pp.action_id = any($1::int[])
              {product_filter_sql}
            """,
            action_pk_list,
        )

    # Р“СЂСѓРїРїРёСЂСѓРµРј С‚РѕРІР°СЂС‹ РїРѕ action_pk
    async with pool.acquire() as conn:
        promo_skus = sorted({int(r["fbo_sku"]) for r in product_rows if r.get("fbo_sku") is not None})
        promo_identity_map = await load_sku_identity_map(conn, promo_skus)

    products_by_action: Dict[int, List[Dict[str, Any]]] = {}
    for r in product_rows:
        row_dict = dict(r)
        sku_val = int(row_dict["fbo_sku"]) if row_dict.get("fbo_sku") is not None else None
        identity = promo_identity_map.get(sku_val or 0) if sku_val is not None else None
        if identity:
            row_dict["offer_id"] = identity.get("offer_id") or row_dict.get("offer_id")
            row_dict["product_name"] = identity.get("product_name") or row_dict.get("product_name")
        products_by_action.setdefault(int(r["action_pk"]), []).append(row_dict)

    # в”Ђв”Ђв”Ђ РЎР±РѕСЂ СЌРєРѕРЅРѕРјРёРєРё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    actions_out: List[Dict[str, Any]] = []

    if mode == "simulation":
        # РћРґРёРЅ РѕР±С‰РёР№ Р·Р°РїСЂРѕСЃ Р·Р° days_back РґРЅРµР№ вЂ” РґР°РЅРЅС‹Рµ РЅР° РІСЃРµ С‚РѕРІР°СЂС‹ СЃСЂР°Р·Сѓ
        date_from_sim = today_msk - timedelta(days=days_back)
        accrual_resp = await _fetch_accruals_for_period(base_url, date_from_sim, today_msk)
        accrual_index = _index_accrual_items_by_offer(accrual_resp)

        # РЎСЂРµРґРЅРёРµ РїРѕРєР°Р·Р°С‚РµР»Рё РІРѕСЂРѕРЅРєРё Р·Р° days_back РґРЅРµР№ РґР»СЏ РєР°Р¶РґРѕРіРѕ SKU
        all_sku_set: set = set()
        for prods_list in products_by_action.values():
            for p in prods_list:
                if p.get("fbo_sku"):
                    all_sku_set.add(int(p["fbo_sku"]))
        funnel_by_sku: Dict[int, Dict[str, float]] = {}
        if all_sku_set:
            async with pool.acquire() as conn2:
                funnel_rows = await conn2.fetch(
                    """
                    SELECT ad.sku,
                           count(*) AS active_days,
                           coalesce(sum(coalesce((ad.metric_values ->> 'hits_view')::numeric, ad.impressions, 0)), 0) AS total_views,
                           coalesce(sum(coalesce((ad.metric_values ->> 'hits_tocart')::numeric, ad.clicks, 0)), 0) AS total_clicks,
                           coalesce(sum(coalesce((ad.metric_values ->> 'ordered_units')::numeric, ad.ordered_units, 0)), 0) AS total_orders
                    FROM analytics_data ad
                    WHERE ad.date::date >= $1::date
                      AND ad.date::date <= $2::date
                      AND ad.sku = any($3::bigint[])
                    GROUP BY ad.sku
                    """,
                    date_from_sim,
                    today_msk,
                    list(all_sku_set),
                )
            for fr in funnel_rows:
                sku_val = int(fr["sku"])
                days_active = int(fr["active_days"]) or 1
                total_views = float(fr["total_views"])
                total_clicks = float(fr["total_clicks"])
                total_orders = float(fr["total_orders"])
                cr_view_to_cart = total_clicks / total_views if total_views > 0 else 0
                cr_cart_to_order = total_orders / total_clicks if total_clicks > 0 else 0
                funnel_by_sku[sku_val] = {
                    "days": days_active,
                    "views_per_day": total_views / days_active,
                    "clicks_per_day": total_clicks / days_active,
                    "orders_per_day": total_orders / days_active,
                    "total_views": total_views,
                    "total_clicks": total_clicks,
                    "total_orders": total_orders,
                    "cr_view_to_cart": cr_view_to_cart,
                    "cr_cart_to_order": cr_cart_to_order,
                }

        for action in actions_rows:
            action_pk = int(action["id"])
            prods = products_by_action.get(action_pk, [])
            out_products: List[Dict[str, Any]] = []
            for p in prods:
                offer_id = (p.get("offer_id") or "").strip()
                offer_norm = _norm_offer(offer_id) if offer_id else ""
                accrual_item = _find_accrual_item(accrual_index, offer_norm, sku_val)
                values = (accrual_item or {}).get("values", {}) if accrual_item else {}
                sku_val = int(p["fbo_sku"]) if p.get("fbo_sku") else None
                base_econ = base_from_accrual_values(
                    offer_id=offer_id,
                    values=values,
                    period_days=days_back,
                    sku=sku_val,
                )
                # РЎС†РµРЅР°СЂРёР№: РїРѕРґСЃС‚Р°РІР»СЏРµРј action_price (РґР»СЏ СѓС‡Р°СЃС‚РЅРёРєРѕРІ) РёР»Рё max_action_price (РґР»СЏ РєР°РЅРґРёРґР°С‚РѕРІ)
                action_price_val = p.get("action_price") or 0
                if not action_price_val or float(action_price_val) <= 0:
                    action_price_val = p.get("max_action_price") or 0
                action_price_val = float(action_price_val or 0)
                scenario = EconomicsScenario(
                    price=action_price_val if action_price_val > 0 else None,
                    tax_rate_pct=tax_rate,
                )
                # Р‘Р°Р·РѕРІС‹Р№ СЂР°СЃС‡С‘С‚ (С„Р°РєС‚) вЂ” С‚РѕР¶Рµ С‡РµСЂРµР· РґРІРёР¶РѕРє С‡С‚РѕР±С‹ РЅР°Р»РѕРі РїСЂРёРјРµРЅРёС‚СЊ
                fact_result = econ_calculate(base_econ, EconomicsScenario(tax_rate_pct=tax_rate))
                sim_result = econ_calculate(base_econ, scenario)

                # в”Ђв”Ђв”Ђ Breakeven: СЃРєРѕР»СЊРєРѕ С€С‚СѓРє РїРѕ Р°РєС†РёРѕРЅРЅРѕР№ С†РµРЅРµ = РІР°Р»РѕРІРѕР№ Р±РµР· Р°РєС†РёРё в”Ђв”Ђв”Ђ
                breakeven_data: Optional[Dict[str, Any]] = None
                fact_gross = fact_result.gross_profit
                sim_price = sim_result.price
                if sim_price > 0 and fact_gross > 0 and sim_result.gross_profit < fact_gross:
                    # РњР°СЂР¶Р° РЅР° РµРґРёРЅРёС†Сѓ РїРѕ Р°РєС†РёРѕРЅРЅРѕР№ С†РµРЅРµ
                    sim_commission_pct = base_econ.commission_pct
                    sim_acquiring_pct = base_econ.acquiring_pct
                    sim_tax_pct = tax_rate / 100.0
                    margin_per_unit = (
                        sim_price * (1 - sim_commission_pct - sim_acquiring_pct - sim_tax_pct)
                        - base_econ.fixed_cost_per_unit
                        - base_econ.material_cost_per_unit
                    )
                    if margin_per_unit > 0:
                        # РќСѓР¶РЅРѕ РїРѕРєСЂС‹С‚СЊ СЂРµРєР»Р°РјСѓ + РЅР°Р±СЂР°С‚СЊ fact_gross
                        breakeven_units = (fact_gross + base_econ.ad_spend) / margin_per_unit
                        breakeven_units = round(breakeven_units, 1)
                        units_growth_pct = ((breakeven_units / fact_result.units) - 1) * 100 if fact_result.units > 0 else None

                        # Р’РѕСЂРѕРЅРєР°: РЅР° СЃРєРѕР»СЊРєРѕ РґРѕР»Р¶РЅС‹ РІС‹СЂР°СЃС‚Рё РїРѕРєР°Р·С‹ / CTR
                        funnel = funnel_by_sku.get(sku_val) if sku_val else None
                        views_growth_pct = None
                        ctr_growth_pct = None
                        breakeven_views_per_day = None
                        breakeven_ctr = None
                        if funnel and funnel["total_orders"] > 0 and funnel["total_views"] > 0:
                            orders_per_day_now = funnel["orders_per_day"]
                            breakeven_orders_per_day = breakeven_units / days_back
                            # Р’Р°СЂРёР°РЅС‚ 1: СЂРѕСЃС‚ РїРѕРєР°Р·РѕРІ РїСЂРё С‚РѕРј Р¶Рµ CTR
                            if orders_per_day_now > 0:
                                views_growth_pct = ((breakeven_orders_per_day / orders_per_day_now) - 1) * 100
                                breakeven_views_per_day = funnel["views_per_day"] * (breakeven_orders_per_day / orders_per_day_now)
                            # Р’Р°СЂРёР°РЅС‚ 2: СЂРѕСЃС‚ CTR (viewв†’cart) РїСЂРё С‚РµС… Р¶Рµ РїРѕРєР°Р·Р°С…
                            cr_v2c = funnel["cr_view_to_cart"]
                            cr_c2o = funnel["cr_cart_to_order"]
                            if cr_v2c > 0 and cr_c2o > 0 and funnel["views_per_day"] > 0:
                                needed_ctr = breakeven_orders_per_day / (funnel["views_per_day"] * cr_c2o)
                                ctr_growth_pct = ((needed_ctr / cr_v2c) - 1) * 100
                                breakeven_ctr = needed_ctr

                        breakeven_data = {
                            "units": breakeven_units,
                            "units_growth_pct": round(units_growth_pct, 1) if units_growth_pct is not None else None,
                            "views_growth_pct": round(views_growth_pct, 1) if views_growth_pct is not None else None,
                            "ctr_growth_pct": round(ctr_growth_pct, 1) if ctr_growth_pct is not None else None,
                            "breakeven_views_per_day": round(breakeven_views_per_day, 0) if breakeven_views_per_day is not None else None,
                            "breakeven_ctr": round(breakeven_ctr * 100, 2) if breakeven_ctr is not None else None,
                        }

                funnel_info = funnel_by_sku.get(sku_val) if sku_val else None
                out_products.append({
                    "offer_id": offer_id or f"sku_{p.get('fbo_sku') or p.get('product_id')}",
                    "product_id": int(p["product_id"]) if p.get("product_id") else None,
                    "sku": int(p["fbo_sku"]) if p.get("fbo_sku") else None,
                    "name": p.get("product_name") or "",
                    "regular_price": float(p.get("regular_price") or 0),
                    "current_price": float(p.get("current_price") or 0),
                    "action_price": float(p.get("action_price") or 0),
                    "max_action_price": float(p.get("max_action_price") or 0),
                    "discount_percent": float(p.get("discount_percent") or 0),
                    "is_participating": bool(p.get("is_participating")),
                    "is_candidate": bool(p.get("is_candidate")),
                    "stock": int(p["stock"]) if p.get("stock") is not None else None,
                    "min_stock": int(p["min_stock"]) if p.get("min_stock") is not None else None,
                    "current_boost": float(p.get("current_boost") or 0),
                    "max_boost": float(p.get("max_boost") or 0),
                    "base_values": dict(values),
                    "fact": fact_result.to_dict(),
                    "simulation": sim_result.to_dict(),
                    "breakeven": breakeven_data,
                    "funnel": {
                        "views_per_day": round(funnel_info["views_per_day"], 0),
                        "clicks_per_day": round(funnel_info["clicks_per_day"], 0),
                        "orders_per_day": round(funnel_info["orders_per_day"], 1),
                        "cr_view_to_cart_pct": round(funnel_info["cr_view_to_cart"] * 100, 2),
                        "cr_cart_to_order_pct": round(funnel_info["cr_cart_to_order"] * 100, 2),
                    } if funnel_info else None,
                    "has_data": bool(values and float(values.get("ordered_units", 0) or 0) > 0),
                })
            actions_out.append({
                "action_id": int(action["action_id"]),
                "title": action["title"] or "",
                "action_type": action["action_type"] or "",
                "description": action["description"] or "",
                "discount_type": action["discount_type"] or "",
                "discount_value": float(action["discount_value"] or 0),
                "with_targeting": bool(action["with_targeting"]),
                "order_amount": float(action["order_amount"] or 0),
                "date_start": action["date_start"].isoformat() if action["date_start"] else None,
                "date_end": action["date_end"].isoformat() if action["date_end"] else None,
                "is_participating": bool(action["is_participating"]),
                "participating_count": int(action["participating_products_count"] or 0),
                "potential_count": int(action["potential_products_count"] or 0),
                "products": out_products,
            })

    else:  # mode == current
        # Р”Р»СЏ РєР°Р¶РґРѕР№ Р°РєС†РёРё вЂ” СЃРІРѕР№ РїРµСЂРёРѕРґ + РґРѕР°РєС†РёРѕРЅРЅР°СЏ Р±Р°Р·Р° РґР»СЏ СЃСЂР°РІРЅРµРЅРёСЏ
        for action in actions_rows:
            action_pk = int(action["id"])
            prods = products_by_action.get(action_pk, [])
            if not prods:
                continue
            ds = action["date_start"]
            de = action["date_end"]
            if not ds:
                continue
            date_from_a = ds.astimezone(MSK).date() if hasattr(ds, 'astimezone') else ds
            date_to_a = de.astimezone(MSK).date() if de and hasattr(de, 'astimezone') else today_msk
            if date_to_a > today_msk:
                date_to_a = today_msk
            if date_from_a > today_msk:
                continue

            action_days = (date_to_a - date_from_a).days + 1
            # Р”Р»СЏ СЃСЂР°РІРЅРµРЅРёСЏ Р±РµСЂС‘Рј С‚РѕС‚ Р¶Рµ РїРµСЂРёРѕРґ, РЅРѕ РЅРµ Р±РѕР»РµРµ 60 РґРЅРµР№
            compare_days = min(action_days, 60)

            # Р¤РђРљРў: РґР°РЅРЅС‹Рµ Р·Р° РїРµСЂРёРѕРґ Р°РєС†РёРё (РїРѕСЃР»РµРґРЅРёРµ compare_days РґРЅРµР№ РµСЃР»Рё Р°РєС†РёСЏ РґР»РёРЅРЅР°СЏ)
            fact_date_from = date_to_a - timedelta(days=compare_days - 1) if action_days > compare_days else date_from_a
            accrual_resp = await _fetch_accruals_for_period(base_url, fact_date_from, date_to_a)
            accrual_index = _index_accrual_items_by_offer(accrual_resp)

            # Р‘РђР—Рђ (РґРѕ Р°РєС†РёРё): per-product РЅР° РѕСЃРЅРѕРІРµ first_seen_at
            # РљСЌС€ pre-accrual Р·Р°РїСЂРѕСЃРѕРІ РїРѕ (pre_date_from, pre_date_to)
            pre_accrual_cache: Dict[Tuple[date, date], Dict[str, Dict[str, Any]]] = {}

            out_products: List[Dict[str, Any]] = []
            for p in prods:
                offer_id = (p.get("offer_id") or "").strip()
                offer_norm = _norm_offer(offer_id) if offer_id else ""
                sku_val = int(p["fbo_sku"]) if p.get("fbo_sku") else None

                # Р¤РђРљРў вЂ” Р·Р° РїРµСЂРёРѕРґ Р°РєС†РёРё (РїРѕСЃР»РµРґРЅРёРµ compare_days РґРЅРµР№)
                accrual_item = _find_accrual_item(accrual_index, offer_norm, sku_val)
                values = (accrual_item or {}).get("values", {}) if accrual_item else {}
                base_econ = base_from_accrual_values(
                    offer_id=offer_id,
                    values=values,
                    period_days=compare_days,
                    sku=sku_val,
                )
                fact_result = econ_calculate(base_econ, EconomicsScenario(tax_rate_pct=tax_rate))

                # Р”Р°С‚Р° РґРѕР±Р°РІР»РµРЅРёСЏ С‚РѕРІР°СЂР° РІ Р°РєС†РёСЋ:
                # РїСЂРёРѕСЂРёС‚РµС‚: event ADDED (С‚РѕС‡РЅР°СЏ) > first_seen_at (В±СЃРёРЅРє) > date_start Р°РєС†РёРё
                ev_added = p.get("event_added_at")
                fs = p.get("first_seen_at")
                joined_ts = ev_added or fs
                if joined_ts and hasattr(joined_ts, 'astimezone'):
                    product_joined = joined_ts.astimezone(MSK).date()
                elif joined_ts and hasattr(joined_ts, 'date'):
                    product_joined = joined_ts.date()
                else:
                    product_joined = date_from_a  # fallback РЅР° РґР°С‚Сѓ Р°РєС†РёРё

                # Р”РѕР°РєС†РёРѕРЅРЅС‹Р№ РїРµСЂРёРѕРґ: compare_days РґРЅРµР№ РґРѕ РґРѕР±Р°РІР»РµРЅРёСЏ С‚РѕРІР°СЂР° РІ Р°РєС†РёСЋ
                p_pre_date_to = product_joined - timedelta(days=1)
                p_pre_date_from = p_pre_date_to - timedelta(days=compare_days - 1)
                # РћРіСЂР°РЅРёС‡РёРІР°РµРј РґРѕСЃС‚СѓРїРЅС‹РјРё РґР°РЅРЅС‹РјРё
                if min_tx_date and p_pre_date_to < min_tx_date:
                    pre_values = {}
                    pre_compare_days = compare_days
                    p_pre_date_from = product_joined
                    p_pre_date_to = product_joined
                else:
                    if min_tx_date and p_pre_date_from < min_tx_date:
                        p_pre_date_from = min_tx_date
                    pre_compare_days = (p_pre_date_to - p_pre_date_from).days + 1

                    # РљСЌС€РёСЂРѕРІР°РЅРЅС‹Р№ Р·Р°РїСЂРѕСЃ accruals РґР»СЏ СЌС‚РѕРіРѕ pre-РїРµСЂРёРѕРґР°
                    cache_key = (p_pre_date_from, p_pre_date_to)
                    if cache_key not in pre_accrual_cache:
                        pre_resp = await _fetch_accruals_for_period(base_url, p_pre_date_from, p_pre_date_to)
                        pre_accrual_cache[cache_key] = _index_accrual_items_by_offer(pre_resp)
                    pre_accrual_idx = pre_accrual_cache[cache_key]
                    pre_item = _find_accrual_item(pre_accrual_idx, offer_norm, sku_val)
                    pre_values = (pre_item or {}).get("values", {}) if pre_item else {}

                # Р‘РђР—Рђ (РґРѕ Р°РєС†РёРё) вЂ” Р·Р° СЌРєРІРёРІР°Р»РµРЅС‚РЅС‹Р№ РїРµСЂРёРѕРґ РїРµСЂРµРґ РґРѕР±Р°РІР»РµРЅРёРµРј С‚РѕРІР°СЂР°
                pre_econ = base_from_accrual_values(
                    offer_id=offer_id,
                    values=pre_values,
                    period_days=pre_compare_days,
                    sku=sku_val,
                )
                pre_result = econ_calculate(pre_econ, EconomicsScenario(tax_rate_pct=tax_rate))

                out_products.append({
                    "offer_id": offer_id or f"sku_{p.get('fbo_sku') or p.get('product_id')}",
                    "product_id": int(p["product_id"]) if p.get("product_id") else None,
                    "sku": int(p["fbo_sku"]) if p.get("fbo_sku") else None,
                    "name": p.get("product_name") or "",
                    "regular_price": float(p.get("regular_price") or 0),
                    "current_price": float(p.get("current_price") or 0),
                    "action_price": float(p.get("action_price") or 0),
                    "max_action_price": float(p.get("max_action_price") or 0),
                    "discount_percent": float(p.get("discount_percent") or 0),
                    "is_participating": bool(p.get("is_participating")),
                    "is_candidate": bool(p.get("is_candidate")),
                    "stock": int(p["stock"]) if p.get("stock") is not None else None,
                    "current_boost": float(p.get("current_boost") or 0),
                    "max_boost": float(p.get("max_boost") or 0),
                    "base_values": dict(values),
                    "fact": fact_result.to_dict(),
                    "pre_action": pre_result.to_dict(),
                    "has_data": bool(values and float(values.get("ordered_units", 0) or 0) > 0),
                    "has_pre_data": bool(pre_values and float(pre_values.get("ordered_units", 0) or 0) > 0),
                    "joined_at": product_joined.isoformat(),
                    "joined_source": "event" if ev_added else ("sync" if fs else "action_start"),
                    "pre_period_from": p_pre_date_from.isoformat(),
                    "pre_period_to": p_pre_date_to.isoformat(),
                })
            actions_out.append({
                "action_id": int(action["action_id"]),
                "title": action["title"] or "",
                "action_type": action["action_type"] or "",
                "description": action["description"] or "",
                "discount_type": action["discount_type"] or "",
                "discount_value": float(action["discount_value"] or 0),
                "with_targeting": bool(action["with_targeting"]),
                "order_amount": float(action["order_amount"] or 0),
                "date_start": action["date_start"].isoformat() if action["date_start"] else None,
                "date_end": action["date_end"].isoformat() if action["date_end"] else None,
                "is_participating": bool(action["is_participating"]),
                "participating_count": int(action["participating_products_count"] or 0),
                "potential_count": int(action["potential_products_count"] or 0),
                "period_used_from": fact_date_from.isoformat(),
                "period_used_to": date_to_a.isoformat(),
                "compare_days": compare_days,
                "products": out_products,
            })

    return web.json_response(clean_nan_values({
        "mode": mode,
        "tax_rate": tax_rate,
        "days_back": days_back,
        "actions": actions_out,
    }))


async def activate_action_products(request: web.Request) -> web.Response:
    """Р”РѕР±Р°РІРёС‚СЊ РІС‹Р±СЂР°РЅРЅС‹Рµ С‚РѕРІР°СЂС‹ РІ Р°РєС†РёСЋ С‡РµСЂРµР· Ozon API."""
    from src.ozon_client import OzonClient

    body = await request.json()
    action_id = body.get("action_id")
    products = body.get("products")  # [{"product_id": int, "action_price": float}, ...]
    if not action_id or not products:
        return web.json_response({"error": "action_id and products required"}, status=400)

    client_id, api_key = _get_ozon_credentials()
    if not client_id or not api_key:
        return web.json_response({"error": "OZON_CLIENT_ID/OZON_API_KEY not configured"}, status=500)

    try:
        async with OzonClient(client_id, api_key) as client:
            result = await client.activate_action_products(
                action_id=int(action_id),
                products=[
                    {"product_id": int(p["product_id"]), "action_price": float(p["action_price"])}
                    for p in products
                ],
            )
        # Р—Р°РїРёСЃС‹РІР°РµРј СЃРѕР±С‹С‚РёРµ ADDED РґР»СЏ РєР°Р¶РґРѕРіРѕ С‚РѕРІР°СЂР°
        pool: asyncpg.Pool = request.app["pool"]
        async with pool.acquire() as conn:
            for p in products:
                await conn.execute(
                    """INSERT INTO promo_product_events (action_id, sku, event_type, source)
                       VALUES ($1, $2, $3, 'manual')""",
                    int(action_id), int(p["product_id"]), PROMO_EVENT_ADDED,
                )
        return web.json_response({"ok": True, "result": result})
    except Exception as e:
        print(f"[ERROR] activate_action_products: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def deactivate_action_products(request: web.Request) -> web.Response:
    """РЈР±СЂР°С‚СЊ С‚РѕРІР°СЂС‹ РёР· Р°РєС†РёРё С‡РµСЂРµР· Ozon API."""
    from src.ozon_client import OzonClient

    body = await request.json()
    action_id = body.get("action_id")
    product_ids = body.get("product_ids")  # [int, ...]
    if not action_id or not product_ids:
        return web.json_response({"error": "action_id and product_ids required"}, status=400)

    client_id, api_key = _get_ozon_credentials()
    if not client_id or not api_key:
        return web.json_response({"error": "OZON_CLIENT_ID/OZON_API_KEY not configured"}, status=500)

    try:
        async with OzonClient(client_id, api_key) as client:
            result = await client.deactivate_action_products(
                action_id=int(action_id),
                product_ids=[int(pid) for pid in product_ids],
            )
        # Р—Р°РїРёСЃС‹РІР°РµРј СЃРѕР±С‹С‚РёРµ REMOVED РґР»СЏ РєР°Р¶РґРѕРіРѕ С‚РѕРІР°СЂР°
        pool: asyncpg.Pool = request.app["pool"]
        async with pool.acquire() as conn:
            for pid in product_ids:
                await conn.execute(
                    """INSERT INTO promo_product_events (action_id, sku, event_type, source)
                       VALUES ($1, $2, $3, 'manual')""",
                    int(action_id), int(pid), PROMO_EVENT_REMOVED,
                )
        return web.json_response({"ok": True, "result": result})
    except Exception as e:
        print(f"[ERROR] deactivate_action_products: {e}")
        return web.json_response({"error": str(e)}, status=500)


