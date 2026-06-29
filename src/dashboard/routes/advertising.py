"""Dashboard routes/advertising.py handlers."""
import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from aiohttp import web

from src.config import settings
from src.dashboard.constants import MSK
from src.dashboard.helpers import (
    clean_nan_values, normalize_offer_id, _calc_ad_kpis, _get_ozon_credentials, load_sku_identity_map,
)

logger = logging.getLogger(__name__)


async def get_wb_advertising_report(request: web.Request) -> web.Response:
    """WB advertising daily report from wb_advertising_daily."""
    month_raw = (request.query.get("month") or "").strip()
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    advert_id_raw = (request.query.get("advert_id") or "").strip()
    advert_id = 0
    if advert_id_raw:
        try:
            advert_id = int(advert_id_raw)
        except ValueError:
            return web.json_response({"error": "Invalid advert_id"}, status=400)
    try:
        if month_raw and not date_from_raw and not date_to_raw:
            year_s, month_s = month_raw.split("-", 1)
            year, month = int(year_s), int(month_s)
            date_from = date(year, month, 1)
            if month == 12:
                date_to = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                date_to = date(year, month + 1, 1) - timedelta(days=1)
        else:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date() if date_from_raw else (datetime.now(MSK) - timedelta(days=30)).date()
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date() if date_to_raw else (datetime.now(MSK) - timedelta(days=1)).date()
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD or month YYYY-MM"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        daily_rows = await conn.fetch(
            """
            SELECT
                d.report_date,
                SUM(d.views)::bigint AS views,
                SUM(d.clicks)::bigint AS clicks,
                SUM(d.carts)::bigint AS carts,
                SUM(d.orders)::bigint AS orders,
                SUM(d.shks)::bigint AS shks,
                SUM(d.canceled)::bigint AS canceled,
                SUM(d.spend)::float8 AS spend,
                SUM(d.stats_spend)::float8 AS stats_spend,
                AVG(NULLIF(d.avg_position, 0))::float8 AS avg_position,
                SUM(d.revenue)::float8 AS revenue
            FROM wb_advertising_daily d
            WHERE d.report_date >= $1
              AND d.report_date <= $2
              AND ($3::bigint = 0 OR d.advert_id = $3::bigint)
            GROUP BY d.report_date
            ORDER BY d.report_date
            """,
            date_from,
            date_to,
            advert_id,
        )
        campaign_rows = await conn.fetch(
            """
            SELECT
                d.advert_id,
                COALESCE(c.name, d.advert_id::text) AS name,
                COALESCE(c.type, '') AS type,
                COALESCE(c.status, '') AS status,
                SUM(d.views)::bigint AS views,
                SUM(d.clicks)::bigint AS clicks,
                SUM(d.carts)::bigint AS carts,
                SUM(d.orders)::bigint AS orders,
                SUM(d.shks)::bigint AS shks,
                SUM(d.canceled)::bigint AS canceled,
                SUM(d.spend)::float8 AS spend,
                SUM(d.stats_spend)::float8 AS stats_spend,
                AVG(NULLIF(d.avg_position, 0))::float8 AS avg_position,
                SUM(d.revenue)::float8 AS revenue
            FROM wb_advertising_daily d
            LEFT JOIN wb_advertising_campaigns c ON c.advert_id = d.advert_id
            WHERE d.report_date >= $1
              AND d.report_date <= $2
              AND ($3::bigint = 0 OR d.advert_id = $3::bigint)
            GROUP BY d.advert_id, c.name, c.type, c.status
            ORDER BY SUM(d.spend) DESC
            """,
            date_from,
            date_to,
            advert_id,
        )
        daily_by_campaign_rows = await conn.fetch(
            """
            SELECT
                d.advert_id,
                d.report_date,
                COALESCE(c.name, d.advert_id::text) AS name,
                SUM(d.views)::bigint AS views,
                SUM(d.clicks)::bigint AS clicks,
                SUM(d.carts)::bigint AS carts,
                SUM(d.orders)::bigint AS orders,
                SUM(d.shks)::bigint AS shks,
                SUM(d.canceled)::bigint AS canceled,
                SUM(d.spend)::float8 AS spend,
                SUM(d.stats_spend)::float8 AS stats_spend,
                AVG(NULLIF(d.avg_position, 0))::float8 AS avg_position,
                SUM(d.revenue)::float8 AS revenue
            FROM wb_advertising_daily d
            LEFT JOIN wb_advertising_campaigns c ON c.advert_id = d.advert_id
            WHERE d.report_date >= $1
              AND d.report_date <= $2
              AND ($3::bigint = 0 OR d.advert_id = $3::bigint)
            GROUP BY d.advert_id, d.report_date, c.name
            ORDER BY d.report_date, d.advert_id
            """,
            date_from,
            date_to,
            advert_id,
        )
        product_rows = await conn.fetch(
            """
            WITH stock AS (
                SELECT nm_id, sum(coalesce(quantity_full, quantity, 0))::bigint AS stock_total
                FROM wb_stocks
                GROUP BY nm_id
            )
            SELECT
                n.nm_id,
                COALESCE(NULLIF(n.name, ''), n.nm_id::text) AS name,
                SUM(n.views)::bigint AS views,
                SUM(n.clicks)::bigint AS clicks,
                SUM(n.carts)::bigint AS carts,
                SUM(n.orders)::bigint AS orders,
                SUM(n.shks)::bigint AS shks,
                SUM(n.canceled)::bigint AS canceled,
                SUM(n.stats_spend)::float8 AS stats_spend,
                SUM(n.revenue)::float8 AS revenue,
                AVG(NULLIF(d.avg_position, 0))::float8 AS avg_position,
                ARRAY_AGG(DISTINCT n.advert_id ORDER BY n.advert_id) AS advert_ids,
                max(coalesce(stock.stock_total, 0))::bigint AS stock_total
            FROM wb_advertising_nm_daily n
            LEFT JOIN wb_advertising_daily d
              ON d.advert_id = n.advert_id
             AND d.report_date = n.report_date
            LEFT JOIN stock ON stock.nm_id = n.nm_id
            WHERE n.report_date >= $1
              AND n.report_date <= $2
              AND ($3::bigint = 0 OR n.advert_id = $3::bigint)
            GROUP BY n.nm_id, COALESCE(NULLIF(n.name, ''), n.nm_id::text)
            ORDER BY SUM(n.stats_spend) DESC, SUM(n.views) DESC
            LIMIT 500
            """,
            date_from,
            date_to,
            advert_id,
        )

    daily = [
        {
            "date": r["report_date"].isoformat() if r["report_date"] else None,
            "date_label": r["report_date"].isoformat()[5:] if r["report_date"] else None,
            "views": int(r["views"] or 0),
            "clicks": int(r["clicks"] or 0),
            "carts": int(r["carts"] or 0),
            "orders": int(r["orders"] or 0),
            "shks": int(r["shks"] or 0),
            "canceled": int(r["canceled"] or 0),
            "spend": float(r["spend"] or 0.0),
            "stats_spend": float(r["stats_spend"] or 0.0),
            "avg_position": float(r["avg_position"] or 0.0),
            "revenue": float(r["revenue"] or 0.0),
        }
        for r in daily_rows
    ]
    campaigns = [
        {
            "advert_id": int(r["advert_id"]),
            "name": r["name"],
            "type": r["type"],
            "status": r["status"],
            "views": int(r["views"] or 0),
            "clicks": int(r["clicks"] or 0),
            "carts": int(r["carts"] or 0),
            "orders": int(r["orders"] or 0),
            "shks": int(r["shks"] or 0),
            "canceled": int(r["canceled"] or 0),
            "spend": float(r["spend"] or 0.0),
            "stats_spend": float(r["stats_spend"] or 0.0),
            "avg_position": float(r["avg_position"] or 0.0),
            "revenue": float(r["revenue"] or 0.0),
        }
        for r in campaign_rows
    ]
    products = [
        {
            "nm_id": int(r["nm_id"]),
            "name": r["name"],
            "views": int(r["views"] or 0),
            "clicks": int(r["clicks"] or 0),
            "carts": int(r["carts"] or 0),
            "orders": int(r["orders"] or 0),
            "shks": int(r["shks"] or 0),
            "canceled": int(r["canceled"] or 0),
            "stats_spend": float(r["stats_spend"] or 0.0),
            "avg_position": float(r["avg_position"] or 0.0),
            "revenue": float(r["revenue"] or 0.0),
            "advert_ids": [int(v) for v in (r["advert_ids"] or []) if v],
            "stock_total": int(r["stock_total"] or 0),
        }
        for r in product_rows
    ]
    daily_by_campaign: Dict[str, List[Dict[str, Any]]] = {}
    daily_campaigns: Dict[str, List[Dict[str, Any]]] = {}
    for r in daily_by_campaign_rows:
        day = r["report_date"].isoformat() if r["report_date"] else None
        if not day:
            continue
        campaign_advert_id = int(r["advert_id"])
        row = {
            "date": day,
            "date_label": day[5:],
            "advert_id": campaign_advert_id,
            "name": r["name"],
            "views": int(r["views"] or 0),
            "clicks": int(r["clicks"] or 0),
            "carts": int(r["carts"] or 0),
            "orders": int(r["orders"] or 0),
            "shks": int(r["shks"] or 0),
            "canceled": int(r["canceled"] or 0),
            "spend": float(r["spend"] or 0.0),
            "stats_spend": float(r["stats_spend"] or 0.0),
            "avg_position": float(r["avg_position"] or 0.0),
            "revenue": float(r["revenue"] or 0.0),
        }
        daily_by_campaign.setdefault(str(campaign_advert_id), []).append(row)
        daily_campaigns.setdefault(day, []).append({
            "advert_id": campaign_advert_id,
            "name": r["name"],
        })
    totals = {
        "views": sum(row["views"] for row in daily),
        "clicks": sum(row["clicks"] for row in daily),
        "carts": sum(row["carts"] for row in daily),
        "orders": sum(row["orders"] for row in daily),
        "shks": sum(row["shks"] for row in daily),
        "canceled": sum(row["canceled"] for row in daily),
        "spend": sum(row["spend"] for row in daily),
        "stats_spend": sum(row["stats_spend"] for row in daily),
        "avg_position": (
            sum(row["avg_position"] for row in daily if row["avg_position"] > 0)
            / max(1, sum(1 for row in daily if row["avg_position"] > 0))
        ),
        "revenue": sum(row["revenue"] for row in daily),
    }
    return web.json_response(clean_nan_values({
        "marketplace": "wb",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "advert_id": advert_id or None,
        "count": len(campaigns),
        "daily": daily,
        "campaigns": campaigns,
        "products": products,
        "daily_by_campaign": daily_by_campaign,
        "daily_campaigns": daily_campaigns,
        "totals": totals,
    }))


async def get_advertising_summary(request: web.Request) -> web.Response:
    """РЎРІРѕРґРЅР°СЏ С‚Р°Р±Р»РёС†Р° РїРѕ Р’РЎР•Рњ Р°СЂС‚РёРєСѓР»Р°Рј РІ СЂРµРєР»Р°РјРµ Р·Р° РїРµСЂРёРѕРґ."""
    MSK = timezone(timedelta(hours=3))

    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()

    try:
        if date_from_raw:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date()
        else:
            date_from = (datetime.now(MSK) - timedelta(days=30)).date()
        if date_to_raw:
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date()
        else:
            date_to = (datetime.now(MSK) - timedelta(days=1)).date()
    except ValueError:
        return web.json_response({"error": "Invalid date format, use YYYY-MM-DD"}, status=400)

    date_to_exclusive = date_to + timedelta(days=1)
    num_days = (date_to - date_from).days + 1

    # UTC bounds for campaign_statistics.date (stored as UTC timestamp, МСК = UTC+3)
    utc_from = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc) - timedelta(hours=3)
    utc_to = datetime(date_to_exclusive.year, date_to_exclusive.month, date_to_exclusive.day, tzinfo=timezone.utc) - timedelta(hours=3) + timedelta(hours=24)

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        # 1. Р РµРєР»Р°РјРЅР°СЏ СЃС‚Р°С‚РёСЃС‚РёРєР° РїРѕ SKU Р·Р° РїРµСЂРёРѕРґ
        ad_rows = await conn.fetch(
            """
            SELECT
                cs.sku,
                sum(cs.views)::int AS views,
                sum(cs.clicks)::int AS clicks,
                sum(coalesce(cs.adds_to_cart, 0))::int AS adds_to_cart,
                sum(cs.spent::float8) AS spent,
                sum(cs.orders)::int AS ad_orders,
                sum(cs.revenue::float8) AS ad_revenue,
                sum(coalesce(nullif(cs.raw_data->>'models', '')::int, 0))::int AS ad_orders_cpo,
                sum(
                    coalesce(
                        nullif(replace(cs.raw_data->>'modelsMoney', ',', '.'), '')::float8,
                        0.0
                    )
                ) AS ad_revenue_cpo
            FROM campaign_statistics cs
            WHERE cs.date >= $1 AND cs.date < $2
            GROUP BY cs.sku
            HAVING sum(cs.spent::float8) > 0 OR sum(cs.orders)::int > 0
            ORDER BY sum(cs.spent::float8) DESC
            """,
            utc_from,
            utc_to,
        )

        stock_rows = await conn.fetch(
            """
            WITH stock AS (
                SELECT
                    lower(trim(offer_id)) AS offer_key,
                    sum(
                        coalesce(available_stock_count, 0) +
                        coalesce(waiting_docs_stock_count, 0) +
                        coalesce(requested_stock_count, 0) +
                        coalesce(transit_stock_count, 0)
                    )::bigint AS total_stock
                FROM analytics_stocks
                WHERE coalesce(trim(offer_id), '') <> ''
                GROUP BY lower(trim(offer_id))
                UNION ALL
                SELECT
                    lower(trim(offer_id)) AS offer_key,
                    sum(coalesce(present, 0))::bigint AS total_stock
                FROM fbs_warehouse_stocks
                WHERE coalesce(trim(offer_id), '') <> ''
                GROUP BY lower(trim(offer_id))
            ),
            stock_sum AS (
                SELECT offer_key, sum(total_stock)::bigint AS total_stock
                FROM stock
                GROUP BY offer_key
                HAVING sum(total_stock) > 0
            ),
            product_ref AS (
                SELECT DISTINCT ON (lower(trim(offer_id)))
                    lower(trim(offer_id)) AS offer_key,
                    trim(offer_id) AS offer_id,
                    sku::bigint AS sku,
                    product_name
                FROM (
                    SELECT offer_id, fbo_sku_id::bigint AS sku, product_name, last_synced_at
                    FROM report_products_items
                    WHERE fbo_sku_id IS NOT NULL AND coalesce(trim(offer_id), '') <> ''
                    UNION ALL
                    SELECT offer_id, fbs_sku_id::bigint AS sku, product_name, last_synced_at
                    FROM report_products_items
                    WHERE fbs_sku_id IS NOT NULL AND coalesce(trim(offer_id), '') <> ''
                    UNION ALL
                    SELECT offer_id, sku::bigint AS sku, NULL AS product_name, updated_at AS last_synced_at
                    FROM article_characteristics
                    WHERE sku IS NOT NULL AND coalesce(trim(offer_id), '') <> ''
                ) src
                ORDER BY lower(trim(offer_id)), last_synced_at DESC NULLS LAST
            )
            SELECT
                ss.offer_key,
                coalesce(pr.offer_id, ss.offer_key) AS offer_id,
                pr.sku,
                pr.product_name,
                ss.total_stock
            FROM stock_sum ss
            LEFT JOIN product_ref pr ON pr.offer_key = ss.offer_key
            ORDER BY ss.total_stock DESC
            """,
        )

        # РЎРѕР±СЂР°С‚СЊ РІСЃРµ SKU: РёР· СЂРµРєР»Р°РјС‹ + С‚РѕРІР°СЂС‹ СЃ РѕСЃС‚Р°С‚РєР°РјРё
        all_skus = sorted({
            int(r["sku"])
            for r in [*ad_rows, *stock_rows]
            if r["sku"] is not None
        })

        # 2. SKU в†’ offer_id РјР°РїРїРёРЅРі
        sku_identity_map = await load_sku_identity_map(conn, all_skus) if all_skus else {}
        sku_to_offer = {sku: v["offer_id"] for sku, v in sku_identity_map.items() if v.get("offer_id")}
        sku_to_name = {sku: v["product_name"] for sku, v in sku_identity_map.items() if v.get("product_name")}
        for row in stock_rows:
            sku_val = row["sku"]
            if sku_val is None:
                continue
            sku = int(sku_val)
            if row["offer_id"]:
                sku_to_offer.setdefault(sku, str(row["offer_id"]))
            if row["product_name"]:
                sku_to_name.setdefault(sku, str(row["product_name"]))

        # 3. Общие заказы/выручка по SKU за период (тот же источник: Performance analytics_data)
        total_rows = await conn.fetch(
            """
            SELECT
                ad.sku,
                sum(coalesce((ad.metric_values ->> 'ordered_units')::numeric, ad.ordered_units, 0))::int AS total_qty,
                sum(coalesce((ad.metric_values ->> 'revenue')::numeric, ad.revenue, 0))::float8 AS total_revenue
            FROM analytics_data ad
            WHERE ad.sku = any($1::bigint[])
              AND ad.date::date >= $2::date
              AND ad.date::date < $3::date
            GROUP BY ad.sku
            """,
            all_skus,
            date_from,
            date_to_exclusive,
        )
        total_by_sku = {int(r["sku"]): dict(r) for r in total_rows}

        # 4. РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ
        cost_rows = await conn.fetch(
            """
            SELECT lower(trim(article)) AS article, unit_cost::float8 AS unit_cost
            FROM finance_article_costs
            """,
        )
        cost_by_article = {r["article"]: float(r["unit_cost"]) for r in cost_rows if r["unit_cost"]}

        # 5. РЎС‚Р°С‚СѓСЃС‹ РѕСЃС‚Р°С‚РєРѕРІ, СЂРµРєР»Р°РјС‹ Рё Р°РєС†РёР№ РїРѕ Р°СЂС‚РёРєСѓР»Р°Рј.
        stock_total_by_offer: Dict[str, int] = {}
        stock_meta_by_offer: Dict[str, Dict[str, Any]] = {}
        for r in stock_rows:
            key = str(r["offer_key"] or "").strip().lower()
            if not key:
                continue
            stock_total_by_offer[key] = stock_total_by_offer.get(key, 0) + int(r["total_stock"] or 0)
            stock_meta_by_offer.setdefault(key, {
                "offer_id": str(r["offer_id"] or key),
                "product_name": str(r["product_name"] or ""),
                "sku": int(r["sku"]) if r["sku"] is not None else None,
            })

        ad_status_rows = await conn.fetch(
            """
            SELECT DISTINCT co.sku
            FROM campaign_objects co
            JOIN campaigns c ON c.id = co.campaign_id
            WHERE co.sku = any($1::bigint[])
              AND c.state = 'CAMPAIGN_STATE_RUNNING'
              AND coalesce(co.status, 'ACTIVE') NOT IN ('PAUSED', 'STOPPED', 'INACTIVE', 'DISABLED')
            """,
            all_skus,
        )
        ad_enabled_skus = {int(r["sku"]) for r in ad_status_rows if r["sku"] is not None}

        promo_status_rows = await conn.fetch(
            """
            WITH sku_to_product AS (
                SELECT DISTINCT ON (sku) sku, ozon_product_id AS product_id, lower(trim(offer_id)) AS offer_key
                FROM (
                    SELECT fbo_sku_id::bigint AS sku, ozon_product_id, offer_id, last_synced_at
                    FROM report_products_items
                    WHERE fbo_sku_id IS NOT NULL AND ozon_product_id IS NOT NULL
                    UNION ALL
                    SELECT fbs_sku_id::bigint AS sku, ozon_product_id, offer_id, last_synced_at
                    FROM report_products_items
                    WHERE fbs_sku_id IS NOT NULL AND ozon_product_id IS NOT NULL
                ) x
                WHERE sku = any($1::bigint[])
                ORDER BY sku, last_synced_at DESC NULLS LAST
            )
            SELECT DISTINCT stp.offer_key
            FROM sku_to_product stp
            JOIN promo_products pp ON pp.sku = stp.product_id
            WHERE pp.is_participating IS TRUE
            """,
            all_skus,
        )
        promo_enabled_by_offer = {
            str(r["offer_key"] or "").strip().lower()
            for r in promo_status_rows
            if str(r["offer_key"] or "").strip()
        }

    # РђРіСЂРµРіР°С†РёСЏ РїРѕ offer_id
    offer_agg = defaultdict(lambda: {
        "views": 0, "clicks": 0, "adds_to_cart": 0, "spent": 0.0,
        "ad_orders": 0, "ad_revenue": 0.0, "ad_orders_cpo": 0, "ad_revenue_cpo": 0.0,
        "total_qty": 0, "total_revenue": 0.0,
        "product_name": "", "skus": [], "ad_enabled": False,
    })

    for oid_lower, meta in stock_meta_by_offer.items():
        agg = offer_agg[oid_lower]
        agg["offer_id"] = meta["offer_id"]
        if meta.get("product_name"):
            agg["product_name"] = meta["product_name"]
        sku = meta.get("sku")
        if sku is not None and sku not in agg["skus"]:
            agg["skus"].append(sku)

    for row in ad_rows:
        sku = int(row["sku"])
        offer_id = sku_to_offer.get(sku)
        if not offer_id:
            offer_id = str(sku)
        oid_lower = offer_id.lower()
        agg = offer_agg[oid_lower]
        agg["offer_id"] = offer_id
        agg["views"] += int(row["views"] or 0)
        agg["clicks"] += int(row["clicks"] or 0)
        agg["adds_to_cart"] += int(row["adds_to_cart"] or 0)
        agg["spent"] += float(row["spent"] or 0.0)
        agg["ad_orders"] += int(row["ad_orders"] or 0)
        agg["ad_revenue"] += float(row["ad_revenue"] or 0.0)
        agg["ad_orders_cpo"] += int(row["ad_orders_cpo"] or 0)
        agg["ad_revenue_cpo"] += float(row["ad_revenue_cpo"] or 0.0)
        if not agg["product_name"] and sku in sku_to_name:
            agg["product_name"] = sku_to_name[sku]
        if sku not in agg["skus"]:
            agg["skus"].append(sku)
        if sku in ad_enabled_skus:
            agg["ad_enabled"] = True

        tot = total_by_sku.get(sku, {})
        agg["total_qty"] += int(tot.get("total_qty") or 0)
        agg["total_revenue"] += float(tot.get("total_revenue") or 0.0)

    items = []
    for oid_lower, agg in offer_agg.items():
        v = agg["views"]
        c = agg["clicks"]
        sp = agg["spent"]
        ao = agg["ad_orders"]
        ar = agg["ad_revenue"]
        ao_cpo = agg["ad_orders_cpo"]
        ar_cpo = agg["ad_revenue_cpo"]
        tq = agg["total_qty"]
        tr_ = agg["total_revenue"]
        uc = cost_by_article.get(oid_lower, 0.0)

        items.append({
            "offer_id": agg["offer_id"],
            "product_name": agg["product_name"],
            "stock_total": int(stock_total_by_offer.get(oid_lower, 0)),
            "ad_enabled": bool(agg["ad_enabled"]),
            "promo_enabled": oid_lower in promo_enabled_by_offer,
            "views": v,
            "clicks": c,
            "adds_to_cart": agg["adds_to_cart"],
            "spent": round(sp, 2),
            "ad_orders": ao,
            "ad_revenue": round(ar, 2),
            "ad_orders_cpo": ao_cpo,
            "ad_revenue_cpo": round(ar_cpo, 2),
            "ad_orders_total": ao + ao_cpo,
            "ad_revenue_total": round(ar + ar_cpo, 2),
            "total_qty": tq,
            "total_revenue": round(tr_, 2),
            "organic_qty": max(0, tq - (ao + ao_cpo)),
            "organic_revenue": round(max(0.0, tr_ - (ar + ar_cpo)), 2),
            "ad_share_pct": round(((ao + ao_cpo) / tq * 100) if tq > 0 else 0.0, 1),
            "ctr": (kpis := _calc_ad_kpis(v, c, sp, ao, ar))["ctr"],
            "cpc": kpis["cpc"],
            "cpo": kpis["cpo"],
            "drr_ad": kpis["drr"],
            "drr_total": round((sp / tr_ * 100) if tr_ > 0 else 0.0, 1),
            "unit_cost": round(uc, 2),
        })

    # РЎРѕСЂС‚РёСЂРѕРІРєР° РїРѕ СЂР°СЃС…РѕРґР°Рј DESC
    items.sort(key=lambda x: x["spent"], reverse=True)

    return web.json_response(clean_nan_values({
        "items": items,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "num_days": num_days,
    }))


async def get_advertising_report(request: web.Request) -> web.Response:
    """РћС‚С‡С‘С‚ В«Р РµРєР»Р°РјР°В» РїРѕ РІС‹Р±СЂР°РЅРЅРѕРјСѓ Р°СЂС‚РёРєСѓР»Сѓ.

    РџР°СЂР°РјРµС‚СЂС‹:
      offer_id  вЂ” Р°СЂС‚РёРєСѓР» С‚РѕРІР°СЂР° (РѕР±СЏР·Р°С‚РµР»СЊРЅС‹Р№)
      date_from вЂ” РЅР°С‡Р°Р»Рѕ РїРµСЂРёРѕРґР° YYYY-MM-DD (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ в€’30 РґРЅРµР№)
      date_to   вЂ” РєРѕРЅРµС† РїРµСЂРёРѕРґР° YYYY-MM-DD (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ СЃРµРіРѕРґРЅСЏ)
    """
    MSK = timezone(timedelta(hours=3))

    offer_id_raw = normalize_offer_id((request.query.get("offer_id") or "").strip())
    if not offer_id_raw:
        return web.json_response({"error": "offer_id is required"}, status=400)

    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()

    try:
        if date_from_raw:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date()
        else:
            date_from = (datetime.now(MSK) - timedelta(days=30)).date()
        if date_to_raw:
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date()
        else:
            date_to = (datetime.now(MSK) - timedelta(days=1)).date()
    except ValueError:
        return web.json_response({"error": "Invalid date format, use YYYY-MM-DD"}, status=400)

    date_to_exclusive = date_to + timedelta(days=1)
    num_days = (date_to - date_from).days + 1

    # UTC bounds for campaign_statistics.date (МСК = UTC+3)
    utc_from = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc) - timedelta(hours=3)
    utc_to = datetime(date_to_exclusive.year, date_to_exclusive.month, date_to_exclusive.day, tzinfo=timezone.utc) - timedelta(hours=3) + timedelta(hours=24)

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        # 1. SKU mapping: прямой запрос по offer_id (не грузим весь каталог)
        target_skus_rows = await conn.fetch(
            """
            SELECT DISTINCT sku::bigint AS sku, offer_id, product_name
            FROM (
                SELECT fbo_sku_id::bigint AS sku, offer_id, product_name
                FROM report_products_items
                WHERE fbo_sku_id IS NOT NULL AND lower(trim(offer_id)) = lower($1)
                UNION ALL
                SELECT fbs_sku_id::bigint AS sku, offer_id, product_name
                FROM report_products_items
                WHERE fbs_sku_id IS NOT NULL AND lower(trim(offer_id)) = lower($1)
                UNION ALL
                SELECT sku::bigint, offer_id, NULL AS product_name
                FROM article_characteristics
                WHERE sku IS NOT NULL AND lower(trim(offer_id)) = lower($1)
                UNION ALL
                SELECT foi.sku::bigint, foi.offer_id, NULL AS product_name
                FROM fact_order_items foi
                WHERE foi.sku IS NOT NULL AND lower(trim(foi.offer_id)) = lower($1)
            ) src
            WHERE sku IS NOT NULL
            """,
            offer_id_raw,
        )
        target_skus = sorted({int(r["sku"]) for r in target_skus_rows})
        if not target_skus:
            return web.json_response({"error": f"No SKUs found for offer_id={offer_id_raw}"}, status=404)

        product_name = offer_id_raw
        for r in target_skus_rows:
            if r["product_name"]:
                product_name = str(r["product_name"])
                break

        identity_map = {int(r["sku"]): {"offer_id": r["offer_id"], "product_name": r["product_name"]} for r in target_skus_rows}

        # 2-7. Параллельный запуск независимых запросов
        SQL_STAT = """
            SELECT
                (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date AS day,
                sum(cs.views)::int AS views,
                sum(cs.clicks)::int AS clicks,
                sum(coalesce(cs.adds_to_cart, 0))::int AS adds_to_cart,
                sum(cs.spent::float8) AS spent,
                sum(cs.orders)::int AS orders,
                sum(cs.revenue::float8) AS revenue,
                sum(coalesce(nullif(cs.raw_data->>'models', '')::int, 0))::int AS orders_cpo,
                sum(
                    coalesce(
                        nullif(replace(cs.raw_data->>'modelsMoney', ',', '.'), '')::float8,
                        0.0
                    )
                ) AS revenue_cpo
            FROM campaign_statistics cs
            WHERE cs.sku = any($1::bigint[])
              AND cs.date >= $2 AND cs.date < $3
            GROUP BY (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date
            ORDER BY day
        """
        SQL_CAMPAIGNS = """
            SELECT
                c.campaign_id AS external_campaign_id,
                c.title, c.adv_object_type, c.state,
                sum(cs.views)::int AS views,
                sum(cs.clicks)::int AS clicks,
                sum(cs.spent::float8) AS spent,
                sum(cs.orders)::int AS orders,
                sum(cs.revenue::float8) AS revenue
            FROM campaign_statistics cs
            JOIN campaigns c ON c.id = cs.campaign_id
            WHERE cs.sku = any($1::bigint[])
              AND cs.date >= $2 AND cs.date < $3
            GROUP BY c.campaign_id, c.title, c.adv_object_type, c.state
            ORDER BY sum(cs.spent::float8) DESC
        """
        SQL_DAILY_BY_TYPE = """
            SELECT
                c.adv_object_type,
                (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date AS day,
                sum(cs.views)::int AS views,
                sum(cs.clicks)::int AS clicks,
                sum(coalesce(cs.adds_to_cart, 0))::int AS adds_to_cart,
                sum(cs.spent::float8) AS spent,
                sum(cs.orders)::int AS orders,
                sum(cs.revenue::float8) AS revenue
            FROM campaign_statistics cs
            JOIN campaigns c ON c.id = cs.campaign_id
            WHERE cs.sku = any($1::bigint[])
              AND cs.date >= $2 AND cs.date < $3
            GROUP BY c.adv_object_type, (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date
            ORDER BY c.adv_object_type, day
        """
        SQL_DAILY_BY_CAMPAIGN = """
            SELECT
                c.campaign_id AS external_campaign_id,
                c.title, c.adv_object_type, c.state,
                (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date AS day,
                sum(cs.views)::int AS views,
                sum(cs.clicks)::int AS clicks,
                sum(coalesce(cs.adds_to_cart, 0))::int AS adds_to_cart,
                sum(cs.spent::float8) AS spent,
                sum(cs.orders)::int AS orders,
                sum(cs.revenue::float8) AS revenue
            FROM campaign_statistics cs
            JOIN campaigns c ON c.id = cs.campaign_id
            WHERE cs.sku = any($1::bigint[])
              AND cs.date >= $2 AND cs.date < $3
            GROUP BY c.campaign_id, c.title, c.adv_object_type, c.state,
                     (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date
            ORDER BY c.campaign_id, day
        """
        SQL_PRICE = """
            SELECT
                (fo.created_at AT TIME ZONE 'UTC')::date AS day,
                sum(coalesce(foi.price, 0) * coalesce(foi.quantity, 0))::float8
                    / nullif(sum(coalesce(foi.quantity, 0)), 0)::float8 AS avg_seller_price,
                sum(coalesce(foi.buyer_paid, 0) * coalesce(foi.quantity, 0))::float8
                    / nullif(sum(coalesce(foi.quantity, 0)), 0)::float8 AS avg_buyer_paid,
                sum(coalesce(foi.quantity, 0))::int AS qty
            FROM fact_order_items foi
            JOIN fact_orders fo ON fo.order_id = foi.order_id
            WHERE foi.sku = any($1::bigint[])
              AND fo.created_at >= $2 AND fo.created_at < $3
              AND coalesce(foi.quantity, 0) > 0
            GROUP BY (fo.created_at AT TIME ZONE 'UTC')::date
            ORDER BY day
        """
        SQL_TOTAL = """
            SELECT
                ad.date::date AS day,
                sum(coalesce((ad.metric_values ->> 'ordered_units')::numeric, ad.ordered_units, 0))::int AS total_qty,
                sum(coalesce((ad.metric_values ->> 'revenue')::numeric, ad.revenue, 0))::float8 AS total_revenue
            FROM analytics_data ad
            WHERE ad.sku = any($1::bigint[])
              AND ad.date::date >= $2::date
              AND ad.date::date < $3::date
            GROUP BY ad.date::date
            ORDER BY day
        """
        SQL_COST = """
            SELECT unit_cost::float8 AS unit_cost
            FROM finance_article_costs
            WHERE lower(trim(article)) = lower($1)
            LIMIT 1
        """

        stat_rows = await conn.fetch(SQL_STAT, target_skus, utc_from, utc_to)
        campaign_rows = await conn.fetch(SQL_CAMPAIGNS, target_skus, utc_from, utc_to)
        daily_by_type_rows = await conn.fetch(SQL_DAILY_BY_TYPE, target_skus, utc_from, utc_to)
        daily_by_campaign_rows = await conn.fetch(SQL_DAILY_BY_CAMPAIGN, target_skus, utc_from, utc_to)
        price_rows = await conn.fetch(SQL_PRICE, target_skus, utc_from, utc_to)
        total_orders_rows = await conn.fetch(SQL_TOTAL, target_skus, date_from, date_to_exclusive)
        cost_row = await conn.fetchrow(SQL_COST, offer_id_raw)
        unit_cost = float(cost_row["unit_cost"]) if cost_row and cost_row["unit_cost"] else 0.0

        promo_rows = await conn.fetch(
            """
            WITH sku_to_product AS (
                SELECT DISTINCT ON (sku) sku, ozon_product_id AS product_id
                FROM (
                    SELECT fbo_sku_id::bigint AS sku, ozon_product_id FROM report_products_items
                      WHERE fbo_sku_id IS NOT NULL AND ozon_product_id IS NOT NULL
                    UNION ALL
                    SELECT fbs_sku_id::bigint AS sku, ozon_product_id FROM report_products_items
                      WHERE fbs_sku_id IS NOT NULL AND ozon_product_id IS NOT NULL
                ) x
                WHERE sku = any($1::bigint[])
                ORDER BY sku
            )
            SELECT DISTINCT
                pa.action_id,
                pa.title,
                pa.discount_percent::float8 AS discount_percent,
                pp.is_participating,
                pp.is_candidate,
                pp.first_seen_at,
                stp.product_id AS product_id
            FROM promo_products pp
            JOIN sku_to_product stp ON stp.product_id = pp.sku
            JOIN promo_actions pa ON pa.id = pp.action_id
            """,
            target_skus,
        )

        product_ids = sorted({int(r["product_id"]) for r in promo_rows if r["product_id"]})

        promo_event_rows = []
        if product_ids:
            promo_event_rows = await conn.fetch(
                """
                SELECT pe.action_id, pe.sku, pe.event_type, pe.detected_at
                FROM promo_product_events pe
                WHERE pe.sku = any($1::bigint[])
                ORDER BY pe.detected_at
                """,
                product_ids,
            )

    # в”Ђв”Ђ Build response в”Ђв”Ђ

    # Index daily data
    stat_by_day = {str(r["day"]): r for r in stat_rows}
    price_by_day = {str(r["day"]): r for r in price_rows}
    total_orders_by_day = {str(r["day"]): r for r in total_orders_rows}

    # Build daily points
    daily = []
    totals = {
        "views": 0, "clicks": 0, "adds_to_cart": 0, "spent": 0.0, "ad_orders": 0,
        "ad_revenue": 0.0, "ad_orders_cpo": 0, "ad_revenue_cpo": 0.0,
        "total_qty": 0, "total_revenue": 0.0,
    }

    for i in range(num_days):
        day = date_from + timedelta(days=i)
        day_str = str(day)
        stat = stat_by_day.get(day_str, {})
        price = price_by_day.get(day_str, {})
        total_ord = total_orders_by_day.get(day_str, {})

        views = int(stat.get("views") or 0)
        clicks = int(stat.get("clicks") or 0)
        adds_to_cart = int(stat.get("adds_to_cart") or 0)
        spent = float(stat.get("spent") or 0.0)
        ad_orders = int(stat.get("orders") or 0)
        ad_revenue = float(stat.get("revenue") or 0.0)
        ad_orders_cpo = int(stat.get("orders_cpo") or 0)
        ad_revenue_cpo = float(stat.get("revenue_cpo") or 0.0)
        total_qty = int(total_ord.get("total_qty") or 0)
        total_rev = float(total_ord.get("total_revenue") or 0.0)
        avg_seller = float(price.get("avg_seller_price") or 0.0)
        avg_buyer = float(price.get("avg_buyer_paid") or 0.0)

        day_kpis = _calc_ad_kpis(views, clicks, spent, ad_orders, ad_revenue)

        totals["views"] += views
        totals["clicks"] += clicks
        totals["adds_to_cart"] += adds_to_cart
        totals["spent"] += spent
        totals["ad_orders"] += ad_orders
        totals["ad_revenue"] += ad_revenue
        totals["ad_orders_cpo"] += ad_orders_cpo
        totals["ad_revenue_cpo"] += ad_revenue_cpo
        totals["total_qty"] += total_qty
        totals["total_revenue"] += total_rev

        daily.append({
            "date": day_str,
            "date_label": f"{day.day:02d}.{day.month:02d}",
            "views": views,
            "clicks": clicks,
            "adds_to_cart": adds_to_cart,
            "spent": round(spent, 2),
            "ad_orders": ad_orders,
            "ad_revenue": round(ad_revenue, 2),
            "ad_orders_cpo": ad_orders_cpo,
            "ad_revenue_cpo": round(ad_revenue_cpo, 2),
            "ad_orders_total": ad_orders + ad_orders_cpo,
            "ad_revenue_total": round(ad_revenue + ad_revenue_cpo, 2),
            "total_qty": total_qty,
            "total_revenue": round(total_rev, 2),
            "avg_seller_price": round(avg_seller, 2),
            "avg_buyer_paid": round(avg_buyer, 2),
            "ctr": day_kpis["ctr"],
            "cpc": day_kpis["cpc"],
            "drr_day": day_kpis["drr"],
        })

    # Forward fill prices: if a day has no orders, carry price from previous day
    prev_seller = 0.0
    prev_buyer = 0.0
    for d in daily:
        if d["avg_seller_price"] > 0:
            prev_seller = d["avg_seller_price"]
            prev_buyer = d["avg_buyer_paid"]
        else:
            d["avg_seller_price"] = prev_seller
            d["avg_buyer_paid"] = prev_buyer

    # Summary KPIs (raw data вЂ” economics calculated on frontend with accruals)
    t = totals
    ad_orders_total = t["ad_orders"] + t["ad_orders_cpo"]
    ad_revenue_total = t["ad_revenue"] + t["ad_revenue_cpo"]
    organic_qty = max(0, t["total_qty"] - ad_orders_total)
    organic_rev = max(0.0, t["total_revenue"] - ad_revenue_total)
    ad_share_pct = (ad_orders_total / t["total_qty"] * 100) if t["total_qty"] > 0 else 0.0
    total_kpis = _calc_ad_kpis(t["views"], t["clicks"], t["spent"], t["ad_orders"], t["ad_revenue"])
    drr_total = (t["spent"] / t["total_revenue"] * 100) if t["total_revenue"] > 0 else 0.0

    # РЎСЂРµРґРЅСЏСЏ С†РµРЅР° Р·Р° РµРґРёРЅРёС†Сѓ Р·Р° РІРµСЃСЊ РїРµСЂРёРѕРґ
    avg_price_total = (t["total_revenue"] / t["total_qty"]) if t["total_qty"] > 0 else 0.0

    summary = {
        "spent": round(t["spent"], 2),
        "ad_orders": t["ad_orders"],
        "ad_revenue": round(t["ad_revenue"], 2),
        "ad_orders_cpo": t["ad_orders_cpo"],
        "ad_revenue_cpo": round(t["ad_revenue_cpo"], 2),
        "ad_orders_total": ad_orders_total,
        "ad_revenue_total": round(ad_revenue_total, 2),
        "unit_cost": round(unit_cost, 2),
        "drr_ad": total_kpis["drr"],
        "total_qty": t["total_qty"],
        "total_revenue": round(t["total_revenue"], 2),
        "organic_qty": organic_qty,
        "organic_revenue": round(organic_rev, 2),
        "ad_share_pct": round(ad_share_pct, 1),
        "drr_total": round(drr_total, 1),
        "avg_price": round(avg_price_total, 2),
        # funnel
        "views": t["views"],
        "clicks": t["clicks"],
        "ctr": total_kpis["ctr"],
        "cpc": total_kpis["cpc"],
        "cpo": total_kpis["cpo"],
    }

    # Daily by campaign type
    type_day_map = defaultdict(dict)  # {adv_type: {day_str: {views, clicks, ...}}}
    adv_types_set = set()
    for row in daily_by_type_rows:
        atype = str(row["adv_object_type"] or "OTHER")
        day_str = str(row["day"])
        adv_types_set.add(atype)
        type_day_map[atype][day_str] = {
            "views": int(row["views"] or 0),
            "clicks": int(row["clicks"] or 0),
            "adds_to_cart": int(row["adds_to_cart"] or 0),
            "spent": float(row["spent"] or 0.0),
            "orders": int(row["orders"] or 0),
            "revenue": float(row["revenue"] or 0.0),
        }

    daily_by_type = {}
    for atype in sorted(adv_types_set):
        type_daily = []
        for i in range(num_days):
            day = date_from + timedelta(days=i)
            day_str = str(day)
            td = type_day_map[atype].get(day_str, {})
            views = td.get("views", 0)
            clicks = td.get("clicks", 0)
            atc = td.get("adds_to_cart", 0)
            spent = td.get("spent", 0.0)
            orders = td.get("orders", 0)
            revenue = td.get("revenue", 0.0)
            tk = _calc_ad_kpis(views, clicks, spent, orders, revenue)
            type_daily.append({
                "date": day_str,
                "date_label": f"{day.day:02d}.{day.month:02d}",
                "views": views,
                "clicks": clicks,
                "adds_to_cart": atc,
                "spent": round(spent, 2),
                "ad_orders": orders,
                "ad_revenue": round(revenue, 2),
                "ctr": tk["ctr"],
                "cpc": tk["cpc"],
                "drr_day": tk["drr"],
            })
        daily_by_type[atype] = type_daily

    # Summary by campaign type
    summary_by_type = {}
    for atype in sorted(adv_types_set):
        rows_t = type_day_map[atype]
        t_views = sum(d.get("views", 0) for d in rows_t.values())
        t_clicks = sum(d.get("clicks", 0) for d in rows_t.values())
        t_spent = sum(d.get("spent", 0.0) for d in rows_t.values())
        t_orders = sum(d.get("orders", 0) for d in rows_t.values())
        t_revenue = sum(d.get("revenue", 0.0) for d in rows_t.values())
        stk = _calc_ad_kpis(t_views, t_clicks, t_spent, t_orders, t_revenue)
        summary_by_type[atype] = {
            "views": t_views,
            "clicks": t_clicks,
            "spent": round(t_spent, 2),
            "orders": t_orders,
            "revenue": round(t_revenue, 2),
            **stk,
        }

    # Campaigns breakdown
    campaigns_out = []
    campaigns_by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in campaign_rows:
        c_views = int(row["views"] or 0)
        c_clicks = int(row["clicks"] or 0)
        c_spent = float(row["spent"] or 0.0)
        c_orders = int(row["orders"] or 0)
        c_revenue = float(row["revenue"] or 0.0)
        ck = _calc_ad_kpis(c_views, c_clicks, c_spent, c_orders, c_revenue)
        c_state = str(row["state"] or "")
        c_type = str(row["adv_object_type"] or "")
        c_entry = {
            "campaign_id": int(row["external_campaign_id"]),
            "title": str(row["title"] or ""),
            "adv_object_type": c_type,
            "state": c_state,
            "views": c_views,
            "clicks": c_clicks,
            "spent": round(c_spent, 2),
            "orders": c_orders,
            "revenue": round(c_revenue, 2),
            **ck,
        }
        campaigns_out.append(c_entry)
        if c_type:
            campaigns_by_type[c_type].append({
                "campaign_id": c_entry["campaign_id"],
                "title": c_entry["title"],
                "state": c_state,
            })

    # Daily by campaign + campaigns worked per day
    campaign_day_map: Dict[int, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    campaign_meta_map: Dict[int, Dict[str, Any]] = {}
    daily_campaigns_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in daily_by_campaign_rows:
        cid = int(row["external_campaign_id"])
        day_str = str(row["day"])
        c_title = str(row["title"] or "")
        c_type = str(row["adv_object_type"] or "")
        c_state = str(row["state"] or "")
        views = int(row["views"] or 0)
        clicks = int(row["clicks"] or 0)
        atc = int(row["adds_to_cart"] or 0)
        spent = float(row["spent"] or 0.0)
        orders = int(row["orders"] or 0)
        revenue = float(row["revenue"] or 0.0)
        ck = _calc_ad_kpis(views, clicks, spent, orders, revenue)

        campaign_meta_map[cid] = {
            "campaign_id": cid,
            "title": c_title,
            "adv_object_type": c_type,
            "state": c_state,
        }
        campaign_day_map[cid][day_str] = {
            "views": views,
            "clicks": clicks,
            "adds_to_cart": atc,
            "spent": spent,
            "orders": orders,
            "revenue": revenue,
            "kpis": ck,
        }
        # Р”Р»СЏ СЃРїРёСЃРєР° "РєР°РєРёРµ РєР°РјРїР°РЅРёРё СЂР°Р±РѕС‚Р°Р»Рё РІ РґРµРЅСЊ" СѓС‡РёС‚С‹РІР°РµРј С‚РѕР»СЊРєРѕ С„Р°РєС‚РёС‡РµСЃРєСѓСЋ Р°РєС‚РёРІРЅРѕСЃС‚СЊ:
        # СЂР°СЃС…РѕРґ > 0. Р—Р°РєР°Р·С‹/РІС‹СЂСѓС‡РєР° РјРѕРіСѓС‚ Р±С‹С‚СЊ РїРѕСЃС‚-Р°С‚СЂРёР±СѓС†РёРµР№ РїСЂРё СѓР¶Рµ РІС‹РєР»СЋС‡РµРЅРЅРѕР№ РєР°РјРїР°РЅРёРё.
        if spent > 0:
            daily_campaigns_map[day_str].append({
                "campaign_id": cid,
                "title": c_title,
                "adv_object_type": c_type,
                "state": c_state,
                "views": views,
                "clicks": clicks,
                "adds_to_cart": atc,
                "spent": round(spent, 2),
                "orders": orders,
                "revenue": round(revenue, 2),
                "ctr": ck["ctr"],
                "cpc": ck["cpc"],
                "drr": ck["drr"],
            })

    daily_by_campaign: Dict[str, List[Dict[str, Any]]] = {}
    for cid in sorted(campaign_meta_map.keys()):
        meta = campaign_meta_map[cid]
        rows_c = []
        for i in range(num_days):
            day = date_from + timedelta(days=i)
            day_str = str(day)
            td = campaign_day_map[cid].get(day_str, {})
            views = int(td.get("views", 0))
            clicks = int(td.get("clicks", 0))
            atc = int(td.get("adds_to_cart", 0))
            spent = float(td.get("spent", 0.0))
            orders = int(td.get("orders", 0))
            revenue = float(td.get("revenue", 0.0))
            tk = _calc_ad_kpis(views, clicks, spent, orders, revenue)
            rows_c.append({
                "date": day_str,
                "date_label": f"{day.day:02d}.{day.month:02d}",
                "views": views,
                "clicks": clicks,
                "adds_to_cart": atc,
                "spent": round(spent, 2),
                "ad_orders": orders,
                "ad_revenue": round(revenue, 2),
                "ctr": tk["ctr"],
                "cpc": tk["cpc"],
                "drr_day": tk["drr"],
            })
        daily_by_campaign[str(cid)] = rows_c

    daily_campaigns = {
        day: sorted(
            rows,
            key=lambda x: (float(x.get("spent") or 0.0), int(x.get("orders") or 0)),
            reverse=True,
        )
        for day, rows in daily_campaigns_map.items()
    }

    # РџСЂРѕРєРёРґС‹РІР°РµРј СЃРїРёСЃРѕРє РєР°РјРїР°РЅРёР№ Рё Р°РіСЂРµРіРёСЂРѕРІР°РЅРЅРѕРµ СЃРѕСЃС‚РѕСЏРЅРёРµ РІ summary_by_type.
    # Ozon РёСЃРїРѕР»СЊР·СѓРµС‚ СЂР°Р·РЅС‹Рµ РёРјРµРЅР° СЃС‚Р°С‚СѓСЃРѕРІ: RUNNING / STOPPED / INACTIVE / ARCHIVED / FINISHED.
    # Р”Р»СЏ UI РЅСѓР¶РЅС‹ РІСЃРµРіРѕ РґРІР°: "РІРєР»СЋС‡РµРЅРѕ" Рё "РІС‹РєР»СЋС‡РµРЅРѕ".
    _off_states = {"CAMPAIGN_STATE_STOPPED", "CAMPAIGN_STATE_INACTIVE"}
    for atype, entry in summary_by_type.items():
        clist = campaigns_by_type.get(atype, [])
        entry["campaigns"] = clist
        running = [c for c in clist if c["state"] == "CAMPAIGN_STATE_RUNNING"]
        stopped = [c for c in clist if c["state"] in _off_states]
        if running and not stopped:
            entry["state_agg"] = "RUNNING"
        elif stopped and not running:
            entry["state_agg"] = "STOPPED"
        elif running and stopped:
            entry["state_agg"] = "MIXED"
        else:
            entry["state_agg"] = "OTHER"  # ARCHIVED/FINISHED Рё С‚.Рґ.

    # в”Ђв”Ђ Promo markers (РєР°Рє РІ В«Р”РёР°РіРЅРѕСЃС‚РёРєР° SKUВ») в”Ђв”Ђ
    # Р“СЂСѓРїРїРёСЂСѓРµРј СЃРѕР±С‹С‚РёСЏ РїРѕ (action_id, sku)
    events_by_key: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    for r in promo_event_rows:
        aid = int(r["action_id"]) if r["action_id"] is not None else None
        sku = int(r["sku"]) if r["sku"] is not None else None
        if aid is None or sku is None:
            continue
        events_by_key[(aid, sku)].append({
            "event_type": str(r["event_type"]),
            "detected_at": r["detected_at"],
        })

    promo_markers: List[Dict[str, Any]] = []
    seen_marker = set()  # РґРµРґСѓРїР»РёРєР°С†РёСЏ (action_id, date, type) РґР»СЏ СЃР»СѓС‡Р°РµРІ multi-SKU
    for row in promo_rows:
        action_id = int(row["action_id"]) if row["action_id"] is not None else None
        if action_id is None:
            continue
        sku_val = int(row["product_id"])
        is_participating = bool(row["is_participating"])
        evs = events_by_key.get((action_id, sku_val), [])
        added = sorted(
            (e["detected_at"] for e in evs if e["event_type"] == "ADDED" and e["detected_at"]),
        )
        removed = sorted(
            (e["detected_at"] for e in evs if e["event_type"] == "REMOVED" and e["detected_at"]),
        )
        # РљР°РЅРґРёРґР°С‚ Р±РµР· СЃРѕР±С‹С‚РёР№ вЂ” РїСЂРѕРїСѓСЃРєР°РµРј (С‚РѕРІР°СЂР° С„Р°РєС‚РёС‡РµСЃРєРё РІ Р°РєС†РёРё РЅРµ Р±С‹Р»Рѕ).
        if not is_participating and not evs:
            continue

        entry_dt = added[0] if added else row["first_seen_at"]
        exit_dt = removed[-1] if removed else None
        if not is_participating and exit_dt is None:
            # РЅРµ СѓС‡Р°СЃС‚РІСѓРµС‚ Рё РЅРµС‚ REMOVED вЂ” РЅРµ Р·РЅР°РµРј РєРѕРіРґР° РІС‹С€РµР», РїСЂРѕРїСѓСЃРєР°РµРј
            continue

        title = str(row["title"] or f"РђРєС†РёСЏ #{action_id}")
        disc = float(row["discount_percent"] or 0.0)

        def _to_msk_date(val):
            if val is None:
                return None
            try:
                if val.tzinfo is None:
                    val = val.replace(tzinfo=timezone.utc)
                return val.astimezone(MSK).date()
            except Exception:
                return None

        entry_day = _to_msk_date(entry_dt)
        exit_day = _to_msk_date(exit_dt)

        if entry_day and date_from <= entry_day <= date_to:
            key = (action_id, str(entry_day), "enter")
            if key not in seen_marker:
                seen_marker.add(key)
                promo_markers.append({
                    "date": str(entry_day),
                    "type": "enter",
                    "action_id": action_id,
                    "action_title": title,
                    "discount_percent": round(disc, 1) if disc else None,
                })
        if exit_day and date_from <= exit_day <= date_to:
            key = (action_id, str(exit_day), "exit")
            if key not in seen_marker:
                seen_marker.add(key)
                promo_markers.append({
                    "date": str(exit_day),
                    "type": "exit",
                    "action_id": action_id,
                    "action_title": title,
                    "discount_percent": round(disc, 1) if disc else None,
                })

    promo_markers.sort(key=lambda m: (m["date"], 0 if m["type"] == "exit" else 1))

    return web.json_response(clean_nan_values({
        "offer_id": offer_id_raw,
        "product_name": product_name,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "num_days": num_days,
        "skus": target_skus,
        "summary": summary,
        "daily": daily,
        "campaigns": campaigns_out,
        "daily_by_type": daily_by_type,
        "daily_by_campaign": daily_by_campaign,
        "daily_campaigns": daily_campaigns,
        "summary_by_type": summary_by_type,
        "promo_markers": promo_markers,
    }))


async def toggle_campaign(request: web.Request) -> web.Response:
    """Vkljuchit'/vykljuchit' spisok reklamnyh kampanij cherez Performance API.

    Body:
      {
        "campaign_ids": [123, 456],
        "action": "activate" | "deactivate"
      }
    """
    from src.ozon_client import OzonClient

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    campaign_ids_raw = body.get("campaign_ids") or []
    action = str(body.get("action") or "").strip().lower()

    if action not in ("activate", "deactivate"):
        return web.json_response({"error": "action must be 'activate' or 'deactivate'"}, status=400)
    try:
        campaign_ids = [int(cid) for cid in campaign_ids_raw]
    except (TypeError, ValueError):
        return web.json_response({"error": "campaign_ids must be list of integers"}, status=400)
    if not campaign_ids:
        return web.json_response({"error": "campaign_ids is empty"}, status=400)

    client_id, api_key = _get_ozon_credentials()
    perf_id = (settings.ozon_performance_client_id or "").strip()
    perf_secret = (settings.ozon_performance_client_secret or "").strip()
    if not perf_id or not perf_secret:
        return web.json_response(
            {"error": "OZON_PERFORMANCE_CLIENT_ID/OZON_PERFORMANCE_CLIENT_SECRET not configured"},
            status=500,
        )

    results: List[Dict[str, Any]] = []
    new_state = "CAMPAIGN_STATE_RUNNING" if action == "activate" else "CAMPAIGN_STATE_STOPPED"

    try:
        async with OzonClient(
            client_id or "",
            api_key or "",
            performance_client_id=perf_id,
            performance_client_secret=perf_secret,
        ) as client:
            for cid in campaign_ids:
                try:
                    if action == "activate":
                        resp = await client.activate_campaign(cid)
                    else:
                        resp = await client.deactivate_campaign(cid)
                    results.append({"campaign_id": cid, "ok": True, "response": resp})
                except Exception as e:
                    logger.error("toggle_campaign %s failed for %s: %s", action, cid, e)
                    results.append({"campaign_id": cid, "ok": False, "error": str(e)})
    except Exception as e:
        logger.error("toggle_campaign client init failed: %s", e)
        return web.json_response({"error": f"Ozon client error: {e}"}, status=500)

    # РћР±РЅРѕРІРёРј Р»РѕРєР°Р»СЊРЅС‹Р№ state РІ campaigns РґР»СЏ СѓСЃРїРµС€РЅС‹С… РєР°РјРїР°РЅРёР№, С‡С‚РѕР±С‹ UI СЃСЂР°Р·Сѓ РїРѕРєР°Р·Р°Р» Р°РєС‚СѓР°Р».
    successful = [r["campaign_id"] for r in results if r.get("ok")]
    if successful:
        try:
            pool: asyncpg.Pool = request.app["pool"]
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE campaigns
                       SET state = $2, last_synced_at = now()
                     WHERE campaign_id = any($1::bigint[])
                    """,
                    successful,
                    new_state,
                )
        except Exception as e:
            logger.warning("toggle_campaign: failed to update local state: %s", e)

    return web.json_response({"results": results, "new_state": new_state})


async def disable_ad_for_sku(request: web.Request) -> web.Response:
    """Отключить все активные рекламные кампании, где встречался SKU."""
    from src.ozon_client import OzonClient

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    sku_raw = body.get("sku")
    try:
        sku = int(sku_raw)
    except (TypeError, ValueError):
        return web.json_response({"error": "sku must be integer"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT c.campaign_id
            FROM campaign_statistics cs
            JOIN campaigns c ON c.id = cs.campaign_id
            WHERE cs.sku = $1
              AND coalesce(c.state, '') NOT IN (
                'CAMPAIGN_STATE_STOPPED',
                'CAMPAIGN_STATE_INACTIVE',
                'CAMPAIGN_STATE_ARCHIVED',
                'CAMPAIGN_STATE_FINISHED'
              )
            """,
            sku,
        )
    campaign_ids = [int(r["campaign_id"]) for r in rows if r.get("campaign_id") is not None]
    if not campaign_ids:
        return web.json_response({"ok": True, "sku": sku, "campaign_ids": [], "results": []})

    client_id, api_key = _get_ozon_credentials()
    perf_id = (settings.ozon_performance_client_id or "").strip()
    perf_secret = (settings.ozon_performance_client_secret or "").strip()
    if not perf_id or not perf_secret:
        return web.json_response(
            {"error": "OZON_PERFORMANCE_CLIENT_ID/OZON_PERFORMANCE_CLIENT_SECRET not configured"},
            status=500,
        )

    results: List[Dict[str, Any]] = []
    try:
        async with OzonClient(
            client_id or "",
            api_key or "",
            performance_client_id=perf_id,
            performance_client_secret=perf_secret,
        ) as client:
            for cid in campaign_ids:
                try:
                    resp = await client.deactivate_campaign(cid)
                    results.append({"campaign_id": cid, "ok": True, "response": resp})
                except Exception as e:
                    logger.error("disable_ad_for_sku failed for campaign %s: %s", cid, e)
                    results.append({"campaign_id": cid, "ok": False, "error": str(e)})
    except Exception as e:
        logger.error("disable_ad_for_sku client init failed: %s", e)
        return web.json_response({"error": f"Ozon client error: {e}"}, status=500)

    successful = [r["campaign_id"] for r in results if r.get("ok")]
    if successful:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE campaigns
                       SET state = 'CAMPAIGN_STATE_STOPPED', last_synced_at = now()
                     WHERE campaign_id = any($1::bigint[])
                    """,
                    successful,
                )
        except Exception as e:
            logger.warning("disable_ad_for_sku: local campaigns state update failed: %s", e)

    return web.json_response({"ok": True, "sku": sku, "campaign_ids": campaign_ids, "results": results})


async def remove_sku_from_all_promos(request: web.Request) -> web.Response:
    """Убрать товар (SKU) из всех акций, где он участвует."""
    from src.ozon_client import OzonClient

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    sku_raw = body.get("sku")
    try:
        sku = int(sku_raw)
    except (TypeError, ValueError):
        return web.json_response({"error": "sku must be integer"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        product_rows = await conn.fetch(
            """
            SELECT DISTINCT ozon_product_id::bigint AS product_id
            FROM report_products_items
            WHERE ozon_product_id IS NOT NULL
              AND (fbo_sku_id = $1 OR fbs_sku_id = $1)
            """,
            sku,
        )
        product_ids = [int(r["product_id"]) for r in product_rows if r.get("product_id") is not None]
        if not product_ids:
            return web.json_response({"ok": True, "sku": sku, "product_ids": [], "actions": []})

        promo_rows = await conn.fetch(
            """
            SELECT
                pp.action_id::int AS db_action_id,
                pa.action_id::bigint AS action_id,
                pp.sku::bigint AS product_id
            FROM promo_products pp
            JOIN promo_actions pa ON pa.id = pp.action_id
            WHERE pp.is_participating = TRUE
              AND pp.sku = any($1::bigint[])
            """,
            product_ids,
        )

    by_action: Dict[int, Dict[str, Any]] = {}
    for row in promo_rows:
        aid = int(row["action_id"])
        db_aid = int(row["db_action_id"])
        pid = int(row["product_id"])
        entry = by_action.setdefault(aid, {"db_action_id": db_aid, "product_ids": []})
        if pid not in entry["product_ids"]:
            entry["product_ids"].append(pid)

    if not by_action:
        return web.json_response({"ok": True, "sku": sku, "product_ids": product_ids, "actions": []})

    client_id, api_key = _get_ozon_credentials()
    if not client_id or not api_key:
        return web.json_response({"error": "OZON_CLIENT_ID/OZON_API_KEY not configured"}, status=500)

    action_results: List[Dict[str, Any]] = []
    try:
        async with OzonClient(client_id, api_key) as client:
            for action_id, action_entry in by_action.items():
                pids = action_entry["product_ids"]
                try:
                    resp = await client.deactivate_action_products(action_id=action_id, product_ids=pids)
                    action_results.append({
                        "action_id": action_id,
                        "db_action_id": action_entry["db_action_id"],
                        "product_ids": pids,
                        "ok": True,
                        "response": resp,
                    })
                except Exception as e:
                    logger.error("remove_sku_from_all_promos failed for action %s: %s", action_id, e)
                    action_results.append({
                        "action_id": action_id,
                        "db_action_id": action_entry["db_action_id"],
                        "product_ids": pids,
                        "ok": False,
                        "error": str(e),
                    })
    except Exception as e:
        logger.error("remove_sku_from_all_promos client init failed: %s", e)
        return web.json_response({"error": f"Ozon client error: {e}"}, status=500)

    success_count = sum(1 for entry in action_results if entry.get("ok"))
    if success_count == 0:
        errors = "; ".join(str(entry.get("error") or "unknown error") for entry in action_results[:3])
        return web.json_response({
            "ok": False,
            "error": errors or "Failed to remove sku from promos",
            "sku": sku,
            "product_ids": product_ids,
            "actions": action_results,
        }, status=502)

    successful_records = [
        (entry["db_action_id"], entry["action_id"], pid)
        for entry in action_results
        if entry.get("ok")
        for pid in entry.get("product_ids", [])
    ]
    if successful_records:
        try:
            async with pool.acquire() as conn:
                for db_action_id, _action_id, pid in successful_records:
                    await conn.execute(
                        """
                        UPDATE promo_products
                           SET is_participating = FALSE
                         WHERE action_id = $1
                           AND sku = $2
                        """,
                        int(db_action_id),
                        int(pid),
                    )
                for _db_action_id, action_id, pid in successful_records:
                    await conn.execute(
                        """INSERT INTO promo_product_events (action_id, sku, event_type, source)
                           VALUES ($1, $2, 'REMOVED', 'manual-rnp')""",
                        int(action_id),
                        int(pid),
                    )
        except Exception as e:
            logger.warning("remove_sku_from_all_promos: failed to insert promo_product_events: %s", e)

    return web.json_response({
        "ok": True,
        "sku": sku,
        "product_ids": product_ids,
        "actions": action_results,
    })


