"""Dashboard routes/advertising.py handlers."""
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
            WHERE (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date >= $1
              AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date < $2
            GROUP BY cs.sku
            HAVING sum(cs.spent::float8) > 0 OR sum(cs.orders)::int > 0
            ORDER BY sum(cs.spent::float8) DESC
            """,
            date_from,
            date_to_exclusive,
        )

        if not ad_rows:
            return web.json_response({
                "items": [], "date_from": str(date_from),
                "date_to": str(date_to), "num_days": num_days,
            })

        # РЎРѕР±СЂР°С‚СЊ РІСЃРµ SKU
        all_skus = [int(r["sku"]) for r in ad_rows if r["sku"]]

        # 2. SKU в†’ offer_id РјР°РїРїРёРЅРі
        sku_identity_map = await load_sku_identity_map(conn, all_skus)
        sku_to_offer = {sku: v["offer_id"] for sku, v in sku_identity_map.items() if v.get("offer_id")}
        sku_to_name = {sku: v["product_name"] for sku, v in sku_identity_map.items() if v.get("product_name")}

        # 3. РћР±С‰РёРµ Р·Р°РєР°Р·С‹ РїРѕ СЌС‚РёРј SKU Р·Р° РїРµСЂРёРѕРґ
        total_rows = await conn.fetch(
            """
            SELECT
                foi.sku,
                sum(coalesce(foi.quantity, 0))::int AS total_qty,
                sum(coalesce(foi.price, 0) * coalesce(foi.quantity, 0))::float8 AS total_revenue
            FROM fact_order_items foi
            JOIN fact_orders fo ON fo.order_id = foi.order_id
            WHERE foi.sku = any($1::bigint[])
              AND (fo.created_at AT TIME ZONE 'UTC')::date >= $2
              AND (fo.created_at AT TIME ZONE 'UTC')::date < $3
              AND coalesce(foi.quantity, 0) > 0
            GROUP BY foi.sku
            """,
            all_skus,
            date_from,
            date_to_exclusive,
        )
        total_by_sku = {int(r["sku"]): r for r in total_rows}

        # 4. РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ
        cost_rows = await conn.fetch(
            """
            SELECT lower(trim(article)) AS article, unit_cost::float8 AS unit_cost
            FROM finance_article_costs
            """,
        )
        cost_by_article = {r["article"]: float(r["unit_cost"]) for r in cost_rows if r["unit_cost"]}

    # РђРіСЂРµРіР°С†РёСЏ РїРѕ offer_id
    offer_agg = defaultdict(lambda: {
        "views": 0, "clicks": 0, "adds_to_cart": 0, "spent": 0.0,
        "ad_orders": 0, "ad_revenue": 0.0, "ad_orders_cpo": 0, "ad_revenue_cpo": 0.0,
        "total_qty": 0, "total_revenue": 0.0,
        "product_name": "", "skus": [],
    })

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
            "organic_qty": max(0, tq - ao),
            "organic_revenue": round(max(0.0, tr_ - ar), 2),
            "ad_share_pct": round((ao / tq * 100) if tq > 0 else 0.0, 1),
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

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        # в”Ђв”Ђ 1. SKU mapping: offer_id в†’ list of SKUs в”Ђв”Ђ
        all_sku_rows = await conn.fetch("""
            SELECT DISTINCT sku::bigint AS sku
            FROM (
                SELECT sku FROM article_characteristics WHERE sku IS NOT NULL
                UNION ALL
                SELECT fbo_sku_id AS sku FROM report_products_items WHERE fbo_sku_id IS NOT NULL
                UNION ALL
                SELECT fbs_sku_id AS sku FROM report_products_items WHERE fbs_sku_id IS NOT NULL
                UNION ALL
                SELECT sku FROM fact_order_items WHERE sku IS NOT NULL
            ) src
            """)
        all_skus = [int(r["sku"]) for r in all_sku_rows if r["sku"] is not None]
        identity_map = await load_sku_identity_map(conn, all_skus)
        target_skus = sorted([
            sku for sku, identity in identity_map.items()
            if str(identity.get("offer_id") or "").strip().lower() == offer_id_raw.lower()
        ])
        if not target_skus:
            return web.json_response({"error": f"No SKUs found for offer_id={offer_id_raw}"}, status=404)

        # в”Ђв”Ђ 2. Campaign statistics per day (aggregated across all campaigns) в”Ђв”Ђ
        stat_rows = await conn.fetch(
            """
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
              AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date >= $2
              AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date < $3
            GROUP BY (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date
            ORDER BY day
            """,
            target_skus,
            date_from,
            date_to_exclusive,
        )

        # в”Ђв”Ђ 3. Campaigns breakdown в”Ђв”Ђ
        campaign_rows = await conn.fetch(
            """
            SELECT
                c.campaign_id AS external_campaign_id,
                c.title,
                c.adv_object_type,
                c.state,
                sum(cs.views)::int AS views,
                sum(cs.clicks)::int AS clicks,
                sum(cs.spent::float8) AS spent,
                sum(cs.orders)::int AS orders,
                sum(cs.revenue::float8) AS revenue
            FROM campaign_statistics cs
            JOIN campaigns c ON c.id = cs.campaign_id
            WHERE cs.sku = any($1::bigint[])
              AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date >= $2
              AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date < $3
            GROUP BY c.campaign_id, c.title, c.adv_object_type, c.state
            ORDER BY sum(cs.spent::float8) DESC
            """,
            target_skus,
            date_from,
            date_to_exclusive,
        )

        # в”Ђв”Ђ 3b. Daily stats by campaign type (adv_object_type) в”Ђв”Ђ
        daily_by_type_rows = await conn.fetch(
            """
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
              AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date >= $2
              AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date < $3
            GROUP BY c.adv_object_type, (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date
            ORDER BY c.adv_object_type, day
            """,
            target_skus,
            date_from,
            date_to_exclusive,
        )

        # в”Ђв”Ђ 3c. Daily stats by campaign в”Ђв”Ђ
        daily_by_campaign_rows = await conn.fetch(
            """
            SELECT
                c.campaign_id AS external_campaign_id,
                c.title,
                c.adv_object_type,
                c.state,
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
              AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date >= $2
              AND (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date < $3
            GROUP BY c.campaign_id, c.title, c.adv_object_type, c.state,
                     (cs.date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')::date
            ORDER BY c.campaign_id, day
            """,
            target_skus,
            date_from,
            date_to_exclusive,
        )

        # в”Ђв”Ђ 4. Average price per day (same as article analytics) в”Ђв”Ђ
        price_rows = await conn.fetch(
            """
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
              AND (fo.created_at AT TIME ZONE 'UTC')::date >= $2
              AND (fo.created_at AT TIME ZONE 'UTC')::date < $3
              AND coalesce(foi.quantity, 0) > 0
            GROUP BY (fo.created_at AT TIME ZONE 'UTC')::date
            ORDER BY day
            """,
            target_skus,
            date_from,
            date_to_exclusive,
        )

        # в”Ђв”Ђ 5. Total orders (all channels) for the article в”Ђв”Ђ
        total_orders_rows = await conn.fetch(
            """
            SELECT
                (fo.created_at AT TIME ZONE 'UTC')::date AS day,
                sum(coalesce(foi.quantity, 0))::int AS total_qty,
                sum(coalesce(foi.price, 0) * coalesce(foi.quantity, 0))::float8 AS total_revenue
            FROM fact_order_items foi
            JOIN fact_orders fo ON fo.order_id = foi.order_id
            WHERE foi.sku = any($1::bigint[])
              AND (fo.created_at AT TIME ZONE 'UTC')::date >= $2
              AND (fo.created_at AT TIME ZONE 'UTC')::date < $3
              AND coalesce(foi.quantity, 0) > 0
            GROUP BY (fo.created_at AT TIME ZONE 'UTC')::date
            ORDER BY day
            """,
            target_skus,
            date_from,
            date_to_exclusive,
        )

        # в”Ђв”Ђ 6. Unit cost from finance_article_costs в”Ђв”Ђ
        cost_row = await conn.fetchrow(
            """
            SELECT unit_cost::float8 AS unit_cost
            FROM finance_article_costs
            WHERE lower(trim(article)) = lower($1)
            LIMIT 1
            """,
            offer_id_raw,
        )
        unit_cost = float(cost_row["unit_cost"]) if cost_row and cost_row["unit_cost"] else 0.0

        # в”Ђв”Ђ 7. Product name в”Ђв”Ђ
        product_name = offer_id_raw
        for sku in target_skus:
            name = (identity_map.get(sku) or {}).get("product_name")
            if name:
                product_name = str(name)
                break

        # в”Ђв”Ђ 8. Promo markers вЂ” РёСЃРїРѕР»СЊР·СѓРµРј С‚Сѓ Р¶Рµ Р»РѕРіРёРєСѓ С‡С‚Рѕ РІ В«Р”РёР°РіРЅРѕСЃС‚РёРєР° SKUВ»:
        # РІС…РѕРґ = РїРµСЂРІРѕРµ ADDED-СЃРѕР±С‹С‚РёРµ РР›Р promo_products.first_seen_at;
        # РІС‹С…РѕРґ = РїРѕСЃР»РµРґРЅРµРµ REMOVED-СЃРѕР±С‹С‚РёРµ (РµСЃР»Рё РµСЃС‚СЊ).
        # Р”Р°С‚С‹ date_start/date_end РЎРђРњРћР™ РђРљР¦РР РЅРµ РёСЃРїРѕР»СЊР·СѓРµРј вЂ” СЌС‚Рѕ Р¶РёР·РЅРµРЅРЅС‹Р№ С†РёРєР» Р°РєС†РёРё,
        # Р° РЅРµ РѕРєРЅРѕ СѓС‡Р°СЃС‚РёСЏ РєРѕРЅРєСЂРµС‚РЅРѕРіРѕ С‚РѕРІР°СЂР° РІ РЅРµР№.
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
    organic_qty = max(0, t["total_qty"] - t["ad_orders"])
    organic_rev = max(0.0, t["total_revenue"] - t["ad_revenue"])
    ad_share_pct = (t["ad_orders"] / t["total_qty"] * 100) if t["total_qty"] > 0 else 0.0
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
        "ad_orders_total": t["ad_orders"] + t["ad_orders_cpo"],
        "ad_revenue_total": round(t["ad_revenue"] + t["ad_revenue_cpo"], 2),
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


