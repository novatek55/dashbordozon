"""Dashboard routes/stocks.py handlers."""
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from aiohttp import web

from src.dashboard.constants import MSK, DELIVERED_STATUSES
from src.dashboard.helpers import (
    clean_nan_values, as_float, normalize_offer_id, parse_date_utc, build_where,
    _normalize_cluster_name, article_tags_from_offer_id,
)


async def get_warehouse_stock(request: web.Request) -> web.Response:
    offer_id = (request.query.get("offer_id") or "").strip()
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

    params: List[Any] = []
    conditions: List[str] = []
    idx = 1

    if offer_id:
        conditions.append(f"i.offer_id = ${idx}")
        params.append(offer_id)
        idx += 1
    if date_from is not None:
        conditions.append(f"r.created_at >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to_exclusive is not None:
        conditions.append(f"r.created_at < ${idx}")
        params.append(date_to_exclusive)
        idx += 1

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            i.warehouse_id,
            i.warehouse_name,
            i.offer_id,
            i.sku,
            i.product_name,
            i.stock_total,
            r.created_at AS report_created_at,
            i.last_synced_at
        FROM report_warehouse_stock_items i
        LEFT JOIN async_reports r ON r.report_id = i.report_id
        {where_sql}
        ORDER BY r.created_at DESC NULLS LAST, i.line_no ASC
        LIMIT {limit}
    """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "warehouse_id": r["warehouse_id"],
                "warehouse_name": r["warehouse_name"],
                "offer_id": r["offer_id"],
                "sku": r["sku"],
                "product_name": r["product_name"],
                "stock_total": r["stock_total"],
                "report_created_at": r["report_created_at"].isoformat() if r["report_created_at"] else None,
                "last_synced_at": r["last_synced_at"].isoformat() if r["last_synced_at"] else None,
            }
        )

    return web.json_response({"count": len(items), "items": items})


async def get_analytics_stocks(request: web.Request) -> web.Response:
    offer_id = (request.query.get("offer_id") or "").strip()
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    limit_raw = (request.query.get("limit") or "500").strip()
    target_days_raw = (request.query.get("target_days") or "28").strip()

    try:
        limit = max(1, min(2000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)
    try:
        target_days = max(1, min(180, int(target_days_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid target_days"}, status=400)

    try:
        date_from = parse_date_utc(date_from_raw, end_of_day=False) if date_from_raw else None
        date_to_exclusive = parse_date_utc(date_to_raw, end_of_day=True) if date_to_raw else None
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)

    params: List[Any] = []
    conditions: List[str] = []
    idx = 1

    if offer_id:
        conditions.append(f"offer_id = ${idx}")
        params.append(offer_id)
        idx += 1
    if date_from is not None:
        conditions.append(f"last_synced_at >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to_exclusive is not None:
        conditions.append(f"last_synced_at < ${idx}")
        params.append(date_to_exclusive)
        idx += 1

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    pool: asyncpg.Pool = request.app["pool"]
    sku_price_map: Dict[int, Dict[str, float]] = {}
    async with pool.acquire() as conn:
        analytics_rows = await conn.fetch(
            f"""
            SELECT
                sku,
                offer_id,
                name,
                warehouse_id,
                warehouse_name,
                cluster_id,
                cluster_name,
                available_stock_count,
                waiting_docs_stock_count,
                requested_stock_count,
                transit_stock_count,
                turnover_grade,
                ads,
                ads_cluster,
                idc,
                days_without_sales,
                last_synced_at
            FROM analytics_stocks
            {where_sql}
            ORDER BY last_synced_at DESC NULLS LAST, offer_id, warehouse_id
            LIMIT {limit}
            """,
            *params,
        )

        fbs_params: List[Any] = []
        fbs_conditions: List[str] = []
        fbs_idx = 1
        if offer_id:
            fbs_conditions.append(f"i.offer_id = ${fbs_idx}")
            fbs_params.append(offer_id)
            fbs_idx += 1
        if date_from is not None:
            fbs_conditions.append(
                f"i.last_synced_at >= ${fbs_idx}"
            )
            fbs_params.append(date_from)
            fbs_idx += 1
        if date_to_exclusive is not None:
            fbs_conditions.append(
                f"i.last_synced_at < ${fbs_idx}"
            )
            fbs_params.append(date_to_exclusive)
            fbs_idx += 1

        fbs_where_sql = ("WHERE " + " AND ".join(fbs_conditions)) if fbs_conditions else ""
        fbs_rows = await conn.fetch(
            f"""
            SELECT
                i.warehouse_id,
                i.warehouse_name,
                i.offer_id,
                i.sku,
                i.present,
                i.reserved,
                i.last_synced_at
            FROM fbs_warehouse_stocks i
            {fbs_where_sql}
            ORDER BY i.offer_id, i.warehouse_name, i.sku
            """,
            *fbs_params,
        )

        sales_statuses = [
            "delivered",
            "delivering",
            "awaiting_deliver",
            "awaiting_packaging",
            "driver_pickup",
            "доставлен",
            "доставляется",
            "ожидает в пвз",
            "у водителя",
            "ожидает отгрузки",
            "ожидает сборки",
        ]
        sales_conditions = [
            "o.created_at >= now() - interval '28 days'",
            f"coalesce(lower(o.status), '') = any(${1}::text[])",
        ]
        sales_params: List[Any] = [sales_statuses]
        sales_idx = 2
        if offer_id:
            sales_conditions.append(f"p.offer_id = ${sales_idx}")
            sales_params.append(offer_id)
            sales_idx += 1
        sales_where_sql = "WHERE " + " AND ".join(sales_conditions)
        sales_join_sql = """
            JOIN fact_orders o ON o.order_id = oi.order_id
            LEFT JOIN LATERAL (
                SELECT ozon_product_id
                FROM report_products_items
                WHERE (fbo_sku_id = oi.sku OR fbs_sku_id = oi.sku)
                  AND ozon_product_id IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
            ) rpi ON true
            LEFT JOIN products p ON p.product_id = rpi.ozon_product_id
        """
        sales_rows = await conn.fetch(
            f"""
            SELECT
                p.offer_id AS offer_id,
                o.delivery_schema,
                sum(coalesce(oi.quantity, 0))::float8 AS quantity_28d
            FROM fact_order_items oi
            {sales_join_sql}
            {sales_where_sql}
              AND p.offer_id IS NOT NULL
            GROUP BY p.offer_id, o.delivery_schema
            """,
            *sales_params,
        )
        has_delivery_cluster_to = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'fact_orders'
                  AND column_name = 'delivery_cluster_to'
            )
            """
        )
        sales_cluster_rows: List[asyncpg.Record] = []
        if has_delivery_cluster_to:
            sales_cluster_rows = await conn.fetch(
                f"""
                SELECT
                    p.offer_id AS offer_id,
                    o.delivery_schema,
                    o.delivery_cluster_to AS cluster_to,
                    sum(coalesce(oi.quantity, 0))::float8 AS quantity_28d
                FROM fact_order_items oi
                {sales_join_sql}
                {sales_where_sql}
                  AND p.offer_id IS NOT NULL
                  AND coalesce(trim(o.delivery_cluster_to), '') <> ''
                GROUP BY p.offer_id, o.delivery_schema, o.delivery_cluster_to
                """,
                *sales_params,
            )

        sales_daily_rows = await conn.fetch(
            f"""
            SELECT
                p.offer_id AS offer_id,
                (o.created_at AT TIME ZONE 'UTC')::date AS sale_date,
                sum(coalesce(oi.quantity, 0))::float8 AS quantity_day
            FROM fact_order_items oi
            {sales_join_sql}
            {sales_where_sql}
              AND p.offer_id IS NOT NULL
            GROUP BY p.offer_id, (o.created_at AT TIME ZONE 'UTC')::date
            """,
            *sales_params,
        )

        sku_candidates = sorted(
            {
                int(v)
                for v in (
                    [row["sku"] for row in analytics_rows if row["sku"] is not None]
                    + [row["sku"] for row in fbs_rows if row["sku"] is not None]
                )
                if v is not None
            }
        )
        if sku_candidates:
            product_rows = await conn.fetch(
                """
                WITH ranked AS (
                    SELECT
                        sku,
                        price_current,
                        price_base,
                        row_number() OVER (
                            PARTITION BY sku
                            ORDER BY
                                CASE
                                    WHEN coalesce(price_current, 0) > 0 THEN 0
                                    WHEN coalesce(price_base, 0) > 0 THEN 1
                                    ELSE 2
                                END,
                                last_synced_at DESC NULLS LAST
                        ) AS rn
                    FROM (
                        SELECT fbo_sku_id::bigint AS sku, price_current, price_base, last_synced_at
                        FROM report_products_items
                        WHERE fbo_sku_id IS NOT NULL
                        UNION ALL
                        SELECT fbs_sku_id::bigint AS sku, price_current, price_base, last_synced_at
                        FROM report_products_items
                        WHERE fbs_sku_id IS NOT NULL
                    ) src
                )
                SELECT sku, price_current, price_base
                FROM ranked
                WHERE sku = any($1::bigint[])
                  AND rn = 1
                """,
                sku_candidates,
            )
            sku_price_map = {
                int(r["sku"]): {
                    "price_current": as_float(r["price_current"], 0.0),
                    "price_base": as_float(r["price_base"], 0.0),
                }
                for r in product_rows
                if r["sku"] is not None
            }

    article_map: Dict[str, Dict[str, Any]] = {}
    has_fbo_stock_data = bool(analytics_rows)
    has_fbs_stock_data = bool(fbs_rows)
    today_utc = datetime.now(timezone.utc).date()
    sales_chart_dates = [today_utc - timedelta(days=offset) for offset in range(27, -1, -1)]
    sales_daily_map: Dict[str, Dict[date, float]] = {}
    article_cluster_sales_map: Dict[str, Dict[str, Dict[str, float]]] = {}

    def get_article(
        article_key: str,
        fallback_name: Optional[str] = None,
        display_offer_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        article = article_map.get(article_key)
        if article is None:
            article = {
                "offer_id": article_key,
                "display_offer_id": display_offer_id or article_key,
                "name": fallback_name,
                "sku": None,
                "article_tags": [],
                "fbo_stock": 0,
                "fbo_stock_available": 0,
                "fbo_supply_stock": 0,
                "fbo_transit_stock": 0,
                "fbo_acceptance_stock": 0,
                "fbs_stock": 0,
                "avg_daily_sales": 0.0,
                "avg_daily_sales_fbo": 0.0,
                "avg_daily_sales_fbs": 0.0,
                "recommended_supply": 0,
                "recommended_supply_fbo": 0,
                "recommended_supply_fbs": 0,
                "days_without_sales": 0,
                "turnover_grade": None,
                "idc": None,
                "last_synced_at": None,
                "price_current": 0.0,
                "price_base": 0.0,
                "sales_history_fbo": 0.0,
                "sales_history_fbs": 0.0,
                "analytics_requested_fbo": 0,
                "sales_series_28": [0.0] * len(sales_chart_dates),
                "sales_dates_28": [day.strftime("%d.%m") for day in sales_chart_dates],
                "depletion_date_label": None,
                "depletion_days_left": None,
                "depletion_within_horizon": False,
                "details": [],
            }
            article_map[article_key] = article
        elif fallback_name and not article.get("name"):
            article["name"] = fallback_name
        if display_offer_id and (
            not article.get("display_offer_id")
            or article.get("display_offer_id") == article_key
        ):
            article["display_offer_id"] = display_offer_id
        return article

    for row in analytics_rows:
        article_key = normalize_offer_id(row["offer_id"])
        if not article_key:
            continue
        article = get_article(article_key, row["name"], row["offer_id"])
        if article["sku"] is None and row["sku"] is not None:
            article["sku"] = int(row["sku"])
        sku_for_price = int(row["sku"]) if row["sku"] is not None else None
        if sku_for_price is not None and sku_for_price in sku_price_map:
            if float(article.get("price_current") or 0.0) <= 0:
                article["price_current"] = float(sku_price_map[sku_for_price].get("price_current") or 0.0)
            if float(article.get("price_base") or 0.0) <= 0:
                article["price_base"] = float(sku_price_map[sku_for_price].get("price_base") or 0.0)
        available = int(row["available_stock_count"] or 0)
        supply_stock = int(row["requested_stock_count"] or 0)
        transit_stock = int(row["transit_stock_count"] or 0)
        acceptance_stock = int(row["waiting_docs_stock_count"] or 0)
        fbo_total_stock = available + supply_stock + transit_stock + acceptance_stock
        requested = int(row["requested_stock_count"] or 0)
        ads_value = float(row["ads"]) if row["ads"] is not None else 0.0
        idc_value = float(row["idc"]) if row["idc"] is not None else None
        article["fbo_stock"] += fbo_total_stock
        article["fbo_stock_available"] += available
        article["fbo_supply_stock"] += supply_stock
        article["fbo_transit_stock"] += transit_stock
        article["fbo_acceptance_stock"] += acceptance_stock
        article["analytics_requested_fbo"] += requested
        article["days_without_sales"] = max(article["days_without_sales"], int(row["days_without_sales"] or 0))
        if article["turnover_grade"] is None and row["turnover_grade"]:
            article["turnover_grade"] = row["turnover_grade"]
        if article["idc"] is None and idc_value is not None:
            article["idc"] = idc_value
        if article["last_synced_at"] is None or (
            row["last_synced_at"] is not None and row["last_synced_at"] > article["last_synced_at"]
        ):
            article["last_synced_at"] = row["last_synced_at"]
        article["details"].append(
            {
                "stock_type": "FBO",
                "sku": int(row["sku"]) if row["sku"] is not None else None,
                "warehouse_id": row["warehouse_id"],
                "warehouse_name": row["warehouse_name"],
                "cluster_id": row["cluster_id"],
                "cluster_name": row["cluster_name"],
                "fbo_stock": fbo_total_stock,
                "fbo_stock_available": available,
                "fbo_supply_stock": supply_stock,
                "fbo_transit_stock": transit_stock,
                "fbo_acceptance_stock": acceptance_stock,
                "fbs_stock": 0,
                "avg_daily_sales": float(row["ads_cluster"] or 0.0),
                "avg_daily_sales_fbo": float(row["ads_cluster"] or 0.0),
                "avg_daily_sales_fbs": 0.0,
                "recommended_supply": 0,
                "recommended_supply_fbo": 0,
                "recommended_supply_fbs": 0,
                "days_without_sales": int(row["days_without_sales"] or 0),
                "turnover_grade": row["turnover_grade"],
                "idc": idc_value,
                "last_synced_at": row["last_synced_at"],
            }
        )

    latest_fbs_sync = None
    fbs_totals_by_article: Dict[str, int] = {}
    fbs_rows_by_article: Dict[str, List[Dict[str, Any]]] = {}
    for row in fbs_rows:
        article_key = normalize_offer_id(row["offer_id"])
        if not article_key:
            continue
        article = get_article(article_key, row["offer_id"], row["offer_id"])
        if article["sku"] is None and row["sku"] is not None:
            article["sku"] = int(row["sku"])
        sku_for_price = int(row["sku"]) if row["sku"] is not None else None
        if sku_for_price is not None and sku_for_price in sku_price_map:
            if float(article.get("price_current") or 0.0) <= 0:
                article["price_current"] = float(sku_price_map[sku_for_price].get("price_current") or 0.0)
            if float(article.get("price_base") or 0.0) <= 0:
                article["price_base"] = float(sku_price_map[sku_for_price].get("price_base") or 0.0)
        stock_total = int(row["present"] or 0)
        fbs_totals_by_article[article_key] = fbs_totals_by_article.get(article_key, 0) + stock_total
        fbs_rows_by_article.setdefault(article_key, []).append(
            {
                "stock_type": "FBS",
                "sku": int(row["sku"]) if row["sku"] is not None else None,
                "warehouse_id": row["warehouse_id"],
                "warehouse_name": row["warehouse_name"],
                "cluster_id": None,
                "cluster_name": None,
                "fbo_stock": 0,
                "fbs_stock": stock_total,
                "avg_daily_sales": 0.0,
                "avg_daily_sales_fbo": 0.0,
                "avg_daily_sales_fbs": 0.0,
                "recommended_supply": 0,
                "recommended_supply_fbo": 0,
                "recommended_supply_fbs": 0,
                "days_without_sales": None,
                "turnover_grade": None,
                "idc": None,
                "last_synced_at": row["last_synced_at"],
            }
        )
        if latest_fbs_sync is None or (
            row["last_synced_at"] is not None and row["last_synced_at"] > latest_fbs_sync
        ):
            latest_fbs_sync = row["last_synced_at"]

    for sales_row in sales_rows:
        article_key = normalize_offer_id(sales_row["offer_id"])
        if not article_key:
            continue
        article = get_article(article_key)
        avg_daily = float(sales_row["quantity_28d"] or 0.0) / 28.0
        schema_key = (sales_row["delivery_schema"] or "").upper()
        if schema_key == "FBO":
            article["sales_history_fbo"] += avg_daily
        elif schema_key in {"FBS", "RFBS"}:
            article["sales_history_fbs"] += avg_daily

    for cluster_row in sales_cluster_rows:
        article_key = normalize_offer_id(cluster_row["offer_id"])
        cluster_key = normalize_offer_id(cluster_row["cluster_to"])
        if not article_key or not cluster_key:
            continue
        avg_daily = float(cluster_row["quantity_28d"] or 0.0) / 28.0
        schema_key = (cluster_row["delivery_schema"] or "").upper()
        bucket = article_cluster_sales_map.setdefault(article_key, {}).setdefault(
            cluster_key,
            {"total": 0.0, "fbo": 0.0, "fbs": 0.0},
        )
        bucket["total"] += avg_daily
        if schema_key == "FBO":
            bucket["fbo"] += avg_daily
        elif schema_key in {"FBS", "RFBS"}:
            bucket["fbs"] += avg_daily

    for sales_daily_row in sales_daily_rows:
        article_key = normalize_offer_id(sales_daily_row["offer_id"])
        if not article_key:
            continue
        sale_date = sales_daily_row["sale_date"]
        if sale_date is None:
            continue
        sales_daily_map.setdefault(article_key, {})[sale_date] = float(sales_daily_row["quantity_day"] or 0.0)

    unified_moscow_warehouse_name = "Москва, МО и Дальние регионы"

    for article_key, article in article_map.items():
        article["article_tags"] = article_tags_from_offer_id(article.get("offer_id"))
        article["fbs_stock"] = fbs_totals_by_article.get(article_key, 0)
        article["avg_daily_sales_fbs"] = article["sales_history_fbs"]
        if article["sales_history_fbo"] > 0:
            article["avg_daily_sales_fbo"] = article["sales_history_fbo"]
        else:
            article["avg_daily_sales_fbo"] = 0.0
        if article["avg_daily_sales_fbo"] <= 0:
            # Fallback: if local fact_orders sales are empty, keep non-zero cluster speed from analytics_stocks (ads_cluster).
            fallback_ads_total = sum(
                float(detail.get("avg_daily_sales_fbo") or 0.0)
                for detail in article.get("details", [])
                if detail.get("stock_type") == "FBO"
            )
            if fallback_ads_total > 0:
                article["avg_daily_sales_fbo"] = fallback_ads_total
        article["avg_daily_sales"] = article["avg_daily_sales_fbo"] + article["avg_daily_sales_fbs"]
        article["sales_series_28"] = [
            round(float(sales_daily_map.get(article_key, {}).get(day, 0.0)), 4)
            for day in sales_chart_dates
        ]
        current_stock_total = int(article["fbo_stock"] or 0) + int(article["fbs_stock"] or 0)
        if article["avg_daily_sales"] > 0 and current_stock_total >= 0:
            depletion_days = current_stock_total / article["avg_daily_sales"] if article["avg_daily_sales"] > 0 else None
            if depletion_days is not None:
                depletion_date = today_utc + timedelta(days=depletion_days)
                article["depletion_days_left"] = round(depletion_days, 2)
                article["depletion_date_label"] = depletion_date.strftime("%d.%m")
                article["depletion_within_horizon"] = depletion_days <= target_days
        calculated_fbo_supply = max(
            0,
            int(math.ceil(article["avg_daily_sales"] * target_days - article["fbo_stock"])),
        )
        article["recommended_supply_fbo"] = calculated_fbo_supply
        article["recommended_supply_fbs"] = 0
        article["recommended_supply"] = article["recommended_supply_fbo"]

        fbo_details_raw = [detail for detail in article["details"] if detail["stock_type"] == "FBO"]
        non_fbo_details = [detail for detail in article["details"] if detail["stock_type"] != "FBO"]
        aggregated_fbo_details: List[Dict[str, Any]] = []
        moscow_bucket: Optional[Dict[str, Any]] = None
        for detail in fbo_details_raw:
            cluster_label = str(detail.get("cluster_name") or detail.get("warehouse_name") or "")
            normalized_cluster_label = _normalize_cluster_name(cluster_label)
            is_moscow_cluster = ("моск" in normalized_cluster_label) or ("moscow" in normalized_cluster_label)
            if not is_moscow_cluster:
                aggregated_fbo_details.append(detail)
                continue
            if moscow_bucket is None:
                moscow_bucket = {
                    "stock_type": "FBO",
                    "sku": detail.get("sku"),
                    "warehouse_id": None,
                    "warehouse_name": unified_moscow_warehouse_name,
                    "cluster_id": None,
                    "cluster_name": unified_moscow_warehouse_name,
                    "fbo_stock": 0,
                    "fbo_stock_available": 0,
                    "fbo_supply_stock": 0,
                    "fbo_transit_stock": 0,
                    "fbo_acceptance_stock": 0,
                    "fbs_stock": 0,
                    "avg_daily_sales": 0.0,
                    "avg_daily_sales_fbo": 0.0,
                    "avg_daily_sales_fbs": 0.0,
                    "recommended_supply": 0,
                    "recommended_supply_fbo": 0,
                    "recommended_supply_fbs": 0,
                    "days_without_sales": 0,
                    "turnover_grade": detail.get("turnover_grade"),
                    "idc": detail.get("idc"),
                    "last_synced_at": detail.get("last_synced_at"),
                }
            moscow_bucket["fbo_stock"] += int(detail.get("fbo_stock") or 0)
            moscow_bucket["fbo_stock_available"] += int(detail.get("fbo_stock_available") or 0)
            moscow_bucket["fbo_supply_stock"] += int(detail.get("fbo_supply_stock") or 0)
            moscow_bucket["fbo_transit_stock"] += int(detail.get("fbo_transit_stock") or 0)
            moscow_bucket["fbo_acceptance_stock"] += int(detail.get("fbo_acceptance_stock") or 0)
            moscow_bucket["avg_daily_sales"] += float(detail.get("avg_daily_sales") or 0.0)
            moscow_bucket["avg_daily_sales_fbo"] += float(detail.get("avg_daily_sales_fbo") or 0.0)
            moscow_bucket["days_without_sales"] = max(
                int(moscow_bucket.get("days_without_sales") or 0),
                int(detail.get("days_without_sales") or 0),
            )
            if moscow_bucket.get("turnover_grade") is None and detail.get("turnover_grade") is not None:
                moscow_bucket["turnover_grade"] = detail.get("turnover_grade")
            if moscow_bucket.get("idc") is None and detail.get("idc") is not None:
                moscow_bucket["idc"] = detail.get("idc")
            current_synced = moscow_bucket.get("last_synced_at")
            detail_synced = detail.get("last_synced_at")
            if current_synced is None or (detail_synced is not None and detail_synced > current_synced):
                moscow_bucket["last_synced_at"] = detail_synced
        if moscow_bucket is not None:
            aggregated_fbo_details.append(moscow_bucket)
        fbo_details = aggregated_fbo_details
        article["details"] = fbo_details + non_fbo_details

        def allocate_recommended_supply(detail_rows: List[Dict[str, Any]], total_supply: int, weight_values: List[float]) -> None:
            if not detail_rows:
                return
            total_supply = max(0, int(total_supply or 0))
            if total_supply <= 0:
                for row in detail_rows:
                    row["recommended_supply_fbo"] = 0
                    row["recommended_supply"] = 0
                return

            cleaned_weights = [max(0.0, float(w or 0.0)) for w in weight_values]
            weight_sum = sum(cleaned_weights)
            if weight_sum <= 0:
                cleaned_weights = [1.0] * len(detail_rows)
                weight_sum = float(len(detail_rows))

            raw_alloc = [total_supply * (w / weight_sum) for w in cleaned_weights]
            base_alloc = [int(math.floor(v)) for v in raw_alloc]
            allocated = sum(base_alloc)
            remainder = max(0, total_supply - allocated)

            # Largest-remainder method: keeps integer sum exactly equal to total_supply.
            order = sorted(
                range(len(detail_rows)),
                key=lambda i: (raw_alloc[i] - base_alloc[i], cleaned_weights[i]),
                reverse=True,
            )
            for i in order[:remainder]:
                base_alloc[i] += 1

            for idx, row in enumerate(detail_rows):
                value = int(base_alloc[idx])
                row["recommended_supply_fbo"] = value
                row["recommended_supply"] = value

        cluster_sales_for_article = article_cluster_sales_map.get(article_key, {})
        has_cluster_to_sales = bool(cluster_sales_for_article)
        if has_cluster_to_sales:
            for detail in fbo_details:
                cluster_key = normalize_offer_id(detail.get("cluster_name") or detail.get("warehouse_name"))
                cluster_sales = cluster_sales_for_article.get(cluster_key, {})
                detail["avg_daily_sales_fbo"] = float(cluster_sales.get("fbo") or 0.0)
                detail["avg_daily_sales_fbs"] = float(cluster_sales.get("fbs") or 0.0)
                detail["avg_daily_sales"] = float(cluster_sales.get("total") or 0.0)

            cluster_total_sales = sum(float(detail.get("avg_daily_sales") or 0.0) for detail in fbo_details)
            if cluster_total_sales > 0:
                weights = [float(detail.get("avg_daily_sales") or 0.0) for detail in fbo_details]
            else:
                total_fbo_stock = sum(float(item["fbo_stock"] or 0.0) for item in fbo_details)
                if total_fbo_stock > 0:
                    weights = [float(detail.get("fbo_stock") or 0.0) for detail in fbo_details]
                else:
                    weights = [1.0 for _ in fbo_details]
            allocate_recommended_supply(fbo_details, int(article["recommended_supply_fbo"] or 0), weights)
        else:
            cluster_fbo_total = sum(float(detail["avg_daily_sales_fbo"] or 0.0) for detail in fbo_details)
            for detail in fbo_details:
                if article["avg_daily_sales_fbo"] > 0:
                    if cluster_fbo_total > 0:
                        share = float(detail["avg_daily_sales_fbo"] or 0.0) / cluster_fbo_total
                    else:
                        total_fbo_stock = sum(float(item["fbo_stock"] or 0.0) for item in fbo_details)
                        share = (
                            float(detail["fbo_stock"] or 0.0) / total_fbo_stock
                            if total_fbo_stock > 0
                            else (1.0 / len(fbo_details) if fbo_details else 0.0)
                        )
                    detail["avg_daily_sales_fbo"] = article["avg_daily_sales_fbo"] * share
                    detail["avg_daily_sales"] = detail["avg_daily_sales_fbo"]
            if cluster_fbo_total > 0 and article["avg_daily_sales_fbo"] > 0:
                weights = [float(detail.get("avg_daily_sales_fbo") or 0.0) for detail in fbo_details]
            else:
                total_fbo_stock = sum(float(item["fbo_stock"] or 0.0) for item in fbo_details)
                if total_fbo_stock > 0:
                    weights = [float(detail.get("fbo_stock") or 0.0) for detail in fbo_details]
                else:
                    weights = [1.0 for _ in fbo_details]
            allocate_recommended_supply(fbo_details, int(article["recommended_supply_fbo"] or 0), weights)

        fbs_details = fbs_rows_by_article.get(article_key, [])
        fbs_total_stock = sum(int(item["fbs_stock"] or 0) for item in fbs_details)
        for detail in fbs_details:
            if has_cluster_to_sales:
                detail["avg_daily_sales"] = 0.0
                detail["avg_daily_sales_fbs"] = 0.0
            elif fbs_total_stock > 0:
                share = float(detail["fbs_stock"]) / float(fbs_total_stock)
                detail["avg_daily_sales"] = article["avg_daily_sales_fbs"] * share
                detail["avg_daily_sales_fbs"] = detail["avg_daily_sales"]
            else:
                share = 1.0 / len(fbs_details) if fbs_details else 0.0
                detail["avg_daily_sales"] = article["avg_daily_sales_fbs"] * share
                detail["avg_daily_sales_fbs"] = detail["avg_daily_sales"]
            detail["recommended_supply"] = 0
            detail["recommended_supply_fbs"] = 0
            article["details"].append(detail)

        if article["avg_daily_sales_fbs"] > 0 and not fbs_details and not has_cluster_to_sales:
            article["details"].append(
                {
                    "stock_type": "FBS",
                    "warehouse_id": None,
                    "warehouse_name": "FBS остатки не загружены",
                    "cluster_id": None,
                    "cluster_name": "Продажи есть, но складской отчёт пуст",
                    "fbo_stock": 0,
                    "fbs_stock": 0,
                    "avg_daily_sales": article["avg_daily_sales_fbs"],
                    "avg_daily_sales_fbo": 0.0,
                    "avg_daily_sales_fbs": article["avg_daily_sales_fbs"],
                    "recommended_supply": 0,
                    "recommended_supply_fbo": 0,
                    "recommended_supply_fbs": 0,
                    "days_without_sales": None,
                    "turnover_grade": None,
                    "idc": None,
                    "last_synced_at": latest_fbs_sync,
                }
            )

        article["details"].sort(
            key=lambda item: (
                -(float(item["avg_daily_sales"] or 0.0)),
                item["stock_type"] != "FBO",
                item["warehouse_name"] or "",
            )
        )

    items: List[Dict[str, Any]] = []
    for article in article_map.values():
        last_synced = article["last_synced_at"] or latest_fbs_sync
        items.append(
            {
                "offer_id": article.get("display_offer_id") or article["offer_id"],
                "name": article["name"],
                "sku": article["sku"],
                "article_tags": article["article_tags"],
                "stock_fbo": article["fbo_stock"],
                "stock_fbo_available": article["fbo_stock_available"],
                "stock_fbo_supply": article["fbo_supply_stock"],
                "stock_fbo_transit": article["fbo_transit_stock"],
                "stock_fbo_acceptance": article["fbo_acceptance_stock"],
                "stock_fbs": article["fbs_stock"],
                "price_current": round(float(article.get("price_current") or 0.0), 2),
                "price_base": round(float(article.get("price_base") or 0.0), 2),
                "avg_daily_sales": round(article["avg_daily_sales"], 4),
                "avg_daily_sales_fbo": round(article["avg_daily_sales_fbo"], 4),
                "avg_daily_sales_fbs": round(article["avg_daily_sales_fbs"], 4),
                "recommended_supply": article["recommended_supply"],
                "recommended_supply_fbo": article["recommended_supply_fbo"],
                "recommended_supply_fbs": article["recommended_supply_fbs"],
                "days_without_sales": article["days_without_sales"],
                "turnover_grade": article["turnover_grade"],
                "idc": article["idc"],
                "sales_series_28": article["sales_series_28"],
                "sales_dates_28": article["sales_dates_28"],
                "depletion_date_label": article["depletion_date_label"],
                "depletion_days_left": article["depletion_days_left"],
                "depletion_within_horizon": article["depletion_within_horizon"],
                "last_synced_at": last_synced.isoformat() if last_synced else None,
                "details": [
                    {
                        **detail,
                        "last_synced_at": detail["last_synced_at"].isoformat() if detail["last_synced_at"] else None,
                    }
                    for detail in article["details"]
                ],
            }
        )

    items.sort(
        key=lambda item: (
            -(item["recommended_supply"] or 0),
            item["offer_id"] or "",
        )
    )

    summary = {
        "articles": len(items),
        "stock_fbo": sum(int(item["stock_fbo"] or 0) for item in items),
        "stock_fbo_available": sum(int(item.get("stock_fbo_available") or 0) for item in items),
        "stock_fbo_supply": sum(int(item.get("stock_fbo_supply") or 0) for item in items),
        "stock_fbo_transit": sum(int(item.get("stock_fbo_transit") or 0) for item in items),
        "stock_fbo_acceptance": sum(int(item.get("stock_fbo_acceptance") or 0) for item in items),
        "stock_fbs": sum(int(item["stock_fbs"] or 0) for item in items),
        "recommended_supply": sum(int(item["recommended_supply"] or 0) for item in items),
        "recommended_supply_fbo": sum(int(item["recommended_supply_fbo"] or 0) for item in items),
        "recommended_supply_fbs": sum(int(item["recommended_supply_fbs"] or 0) for item in items),
        "target_days": target_days,
        "has_fbo_stock_data": has_fbo_stock_data,
        "has_fbs_stock_data": has_fbs_stock_data,
    }

    return web.json_response({"count": len(items), "items": items, "summary": summary})


async def get_stock_balances(request: web.Request) -> web.Response:
    """Stock balances by article with FBO/FBS split and cluster (region) breakdown."""
    offer_id = (request.query.get("offer_id") or "").strip()

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        # FBO stocks from analytics_stocks grouped by offer_id + cluster
        fbo_params: List[Any] = []
        fbo_conditions: List[str] = []
        fbo_idx = 1
        if offer_id:
            fbo_conditions.append(f"offer_id = ${fbo_idx}")
            fbo_params.append(offer_id)
            fbo_idx += 1
        fbo_where = ("WHERE " + " AND ".join(fbo_conditions)) if fbo_conditions else ""
        fbo_rows = await conn.fetch(
            f"""
            SELECT
                offer_id,
                name,
                cluster_name,
                sum(coalesce(available_stock_count, 0)) AS available,
                sum(coalesce(waiting_docs_stock_count, 0)) AS acceptance,
                sum(coalesce(requested_stock_count, 0)) AS supply,
                sum(coalesce(transit_stock_count, 0)) AS transit,
                sum(
                    coalesce(available_stock_count, 0) +
                    coalesce(waiting_docs_stock_count, 0) +
                    coalesce(requested_stock_count, 0) +
                    coalesce(transit_stock_count, 0)
                ) AS total,
                max(last_synced_at) AS last_synced_at
            FROM analytics_stocks
            {fbo_where}
            GROUP BY offer_id, name, cluster_name
            ORDER BY offer_id, cluster_name
            """,
            *fbo_params,
        )

        # FBS stocks from fbs_warehouse_stocks grouped by offer_id + warehouse_name
        fbs_params: List[Any] = []
        fbs_conditions: List[str] = []
        fbs_idx = 1
        if offer_id:
            fbs_conditions.append(f"offer_id = ${fbs_idx}")
            fbs_params.append(offer_id)
            fbs_idx += 1
        fbs_where = ("WHERE " + " AND ".join(fbs_conditions)) if fbs_conditions else ""
        fbs_rows = await conn.fetch(
            f"""
            SELECT
                offer_id,
                warehouse_id,
                warehouse_name,
                sum(coalesce(present, 0)) AS present,
                sum(coalesce(reserved, 0)) AS reserved,
                max(last_synced_at) AS last_synced_at
            FROM fbs_warehouse_stocks
            {fbs_where}
            GROUP BY offer_id, warehouse_id, warehouse_name
            ORDER BY offer_id, warehouse_id
            """,
            *fbs_params,
        )

    # Build article map
    article_map: Dict[str, Dict[str, Any]] = {}

    for row in fbo_rows:
        key = normalize_offer_id(row["offer_id"])
        if not key:
            continue
        if key not in article_map:
            article_map[key] = {
                "offer_id": row["offer_id"],
                "name": row["name"],
                "fbo_total": 0,
                "fbs_total": 0,
                "fbo_clusters": {},
                "fbs_warehouses": {},
                "last_synced_at": None,
            }
        art = article_map[key]
        if row["name"] and not art["name"]:
            art["name"] = row["name"]
        cluster = row["cluster_name"] or "Без кластера"
        stock_total = int(row["total"] or 0)
        art["fbo_total"] += stock_total
        if cluster in art["fbo_clusters"]:
            art["fbo_clusters"][cluster]["total"] += stock_total
            art["fbo_clusters"][cluster]["available"] += int(row["available"] or 0)
            art["fbo_clusters"][cluster]["acceptance"] += int(row["acceptance"] or 0)
            art["fbo_clusters"][cluster]["supply"] += int(row["supply"] or 0)
            art["fbo_clusters"][cluster]["transit"] += int(row["transit"] or 0)
        else:
            art["fbo_clusters"][cluster] = {
                "total": stock_total,
                "available": int(row["available"] or 0),
                "acceptance": int(row["acceptance"] or 0),
                "supply": int(row["supply"] or 0),
                "transit": int(row["transit"] or 0),
            }
        synced = row["last_synced_at"]
        if synced and (art["last_synced_at"] is None or synced > art["last_synced_at"]):
            art["last_synced_at"] = synced

    # Collect FBS warehouse id→name mapping
    fbs_wh_names: Dict[int, str] = {}
    for row in fbs_rows:
        wh_id = int(row["warehouse_id"]) if row["warehouse_id"] is not None else 0
        wh_name = row["warehouse_name"] or f"FBS #{wh_id}"
        fbs_wh_names[wh_id] = wh_name
        key = normalize_offer_id(row["offer_id"])
        if not key:
            continue
        if key not in article_map:
            article_map[key] = {
                "offer_id": row["offer_id"],
                "name": None,
                "fbo_total": 0,
                "fbs_total": 0,
                "fbo_clusters": {},
                "fbs_warehouses": {},
                "last_synced_at": None,
            }
        art = article_map[key]
        present = int(row["present"] or 0)
        reserved = int(row["reserved"] or 0)
        art["fbs_total"] += present
        if wh_id in art["fbs_warehouses"]:
            art["fbs_warehouses"][wh_id]["present"] += present
            art["fbs_warehouses"][wh_id]["reserved"] += reserved
        else:
            art["fbs_warehouses"][wh_id] = {
                "warehouse_id": wh_id,
                "warehouse_name": wh_name,
                "present": present,
                "reserved": reserved,
            }
        synced = row["last_synced_at"]
        if synced and (art["last_synced_at"] is None or synced > art["last_synced_at"]):
            art["last_synced_at"] = synced

    # Collect all unique cluster names across articles
    all_clusters: set = set()
    all_fbs_wh_ids: set = set()
    for art in article_map.values():
        all_clusters.update(art["fbo_clusters"].keys())
        all_fbs_wh_ids.update(art["fbs_warehouses"].keys())

    clusters_sorted = sorted(all_clusters)
    fbs_wh_sorted = sorted(all_fbs_wh_ids)

    items = []
    for art in article_map.values():
        total = art["fbo_total"] + art["fbs_total"]
        items.append({
            "offer_id": art["offer_id"],
            "name": art["name"],
            "total": total,
            "fbo_total": art["fbo_total"],
            "fbs_total": art["fbs_total"],
            "fbo_clusters_detail": {
                c: v for c, v in art["fbo_clusters"].items() if v.get("total", 0) > 0
            },
            "fbs_wh_detail": {
                str(wh_id): v for wh_id, v in art["fbs_warehouses"].items() if v.get("present", 0) > 0
            },
            "last_synced_at": art["last_synced_at"].isoformat() if art["last_synced_at"] else None,
        })

    items.sort(key=lambda x: -(x["total"] or 0))

    # FBS warehouses list with id+name for frontend
    fbs_warehouses_list = [
        {"warehouse_id": wh_id, "warehouse_name": fbs_wh_names.get(wh_id, f"FBS #{wh_id}")}
        for wh_id in fbs_wh_sorted
    ]

    summary = {
        "articles": len(items),
        "total": sum(x["total"] for x in items),
        "fbo_total": sum(x["fbo_total"] for x in items),
        "fbs_total": sum(x["fbs_total"] for x in items),
    }

    return web.json_response({
        "count": len(items),
        "items": items,
        "clusters": clusters_sorted,
        "fbs_warehouses": fbs_warehouses_list,
        "summary": summary,
    })


async def get_wb_stock_balances(request: web.Request) -> web.Response:
    """WB stock balances shaped like Ozon stock_balances for shared UI."""
    offer_id = (request.query.get("offer_id") or "").strip()

    params: List[Any] = []
    conditions: List[str] = []
    idx = 1
    if offer_id:
        conditions.append(f"(supplier_article = ${idx} OR nm_id::text = ${idx})")
        params.append(offer_id)
        idx += 1
    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                nm_id,
                supplier_article,
                max(coalesce(subject, category, brand, supplier_article, nm_id::text)) AS name,
                warehouse_name,
                sum(coalesce(quantity, 0))::bigint AS quantity,
                sum(coalesce(in_way_to_client, 0))::bigint AS in_way_to_client,
                sum(coalesce(in_way_from_client, 0))::bigint AS in_way_from_client,
                sum(coalesce(quantity_full, quantity, 0))::bigint AS quantity_full,
                max(last_synced_at) AS last_synced_at
            FROM wb_stocks
            {where_sql}
            GROUP BY nm_id, supplier_article, warehouse_name
            ORDER BY supplier_article, warehouse_name
            """,
            *params,
        )

    article_map: Dict[str, Dict[str, Any]] = {}
    warehouse_names: set = set()
    for row in rows:
        offer = str(row["supplier_article"] or row["nm_id"] or "").strip()
        key = normalize_offer_id(offer)
        if not key:
            continue
        if key not in article_map:
            article_map[key] = {
                "offer_id": offer,
                "name": row["name"],
                "nm_id": int(row["nm_id"] or 0),
                "fbo_total": 0,
                "fbs_total": 0,
                "fbo_clusters": {},
                "fbs_warehouses": {},
                "last_synced_at": None,
            }
        article = article_map[key]
        if row["name"] and not article.get("name"):
            article["name"] = row["name"]
        if row["nm_id"] and not article.get("nm_id"):
            article["nm_id"] = int(row["nm_id"])

        warehouse = row["warehouse_name"] or "WB"
        warehouse_names.add(warehouse)
        quantity = int(row["quantity"] or 0)
        quantity_full = int(row["quantity_full"] or quantity)
        in_way_to_client = int(row["in_way_to_client"] or 0)
        in_way_from_client = int(row["in_way_from_client"] or 0)
        article["fbo_total"] += quantity_full
        article["fbo_clusters"][warehouse] = {
            "total": quantity_full,
            "available": quantity,
            "acceptance": 0,
            "supply": in_way_from_client,
            "transit": in_way_to_client,
        }
        synced = row["last_synced_at"]
        if synced and (article["last_synced_at"] is None or synced > article["last_synced_at"]):
            article["last_synced_at"] = synced

    clusters_sorted = sorted(warehouse_names)
    items: List[Dict[str, Any]] = []
    for article in article_map.values():
        total = int(article["fbo_total"] or 0) + int(article["fbs_total"] or 0)
        items.append({
            "offer_id": article["offer_id"],
            "name": article["name"],
            "nm_id": article["nm_id"],
            "total": total,
            "fbo_total": article["fbo_total"],
            "fbs_total": article["fbs_total"],
            "fbo_clusters_detail": {
                c: v for c, v in article["fbo_clusters"].items() if int(v.get("total") or 0) > 0
            },
            "fbs_wh_detail": {},
            "last_synced_at": article["last_synced_at"].isoformat() if article["last_synced_at"] else None,
        })
    items.sort(key=lambda x: -(int(x["total"] or 0)))

    summary = {
        "articles": len(items),
        "total": sum(int(x["total"] or 0) for x in items),
        "fbo_total": sum(int(x["fbo_total"] or 0) for x in items),
        "fbs_total": 0,
    }

    return web.json_response(clean_nan_values({
        "marketplace": "wb",
        "count": len(items),
        "items": items,
        "clusters": clusters_sorted,
        "fbs_warehouses": [],
        "summary": summary,
    }))


async def get_analytics_turnover(request: web.Request) -> web.Response:
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

    params: List[Any] = []
    conditions: List[str] = []
    idx = 1

    if date_from is not None:
        conditions.append(f"date >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to_exclusive is not None:
        conditions.append(f"date < ${idx}")
        params.append(date_to_exclusive)
        idx += 1

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            date,
            sku,
            stock,
            sales_speed,
            days_in_stock,
            recommended_stock,
            recommended_supply,
            last_synced_at
        FROM analytics_turnover
        {where_sql}
        ORDER BY date DESC NULLS LAST, sku
        LIMIT {limit}
    """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "date": r["date"].isoformat() if r["date"] else None,
                "sku": r["sku"],
                "stock": r["stock"],
                "sales_speed": float(r["sales_speed"]) if r["sales_speed"] is not None else None,
                "days_in_stock": r["days_in_stock"],
                "recommended_stock": r["recommended_stock"],
                "recommended_supply": r["recommended_supply"],
                "last_synced_at": r["last_synced_at"].isoformat() if r["last_synced_at"] else None,
            }
        )
    return web.json_response({"count": len(items), "items": items})


async def get_average_delivery_time(request: web.Request) -> web.Response:
    limit_raw = (request.query.get("limit") or "500").strip()
    try:
        limit = max(1, min(2000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    sql = f"""
        SELECT
            delivery_cluster_id,
            average_delivery_time,
            average_delivery_time_status,
            lost_profit,
            exact_impact_share,
            attention_level,
            recommended_supply,
            orders_total,
            orders_fast,
            orders_medium,
            orders_long,
            last_synced_at
        FROM analytics_average_delivery_time
        ORDER BY last_synced_at DESC NULLS LAST, delivery_cluster_id
        LIMIT {limit}
    """
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "delivery_cluster_id": r["delivery_cluster_id"],
                "average_delivery_time": float(r["average_delivery_time"]) if r["average_delivery_time"] is not None else None,
                "average_delivery_time_status": r["average_delivery_time_status"],
                "lost_profit": float(r["lost_profit"]) if r["lost_profit"] is not None else None,
                "exact_impact_share": float(r["exact_impact_share"]) if r["exact_impact_share"] is not None else None,
                "attention_level": r["attention_level"],
                "recommended_supply": r["recommended_supply"],
                "orders_total": r["orders_total"],
                "orders_fast": r["orders_fast"],
                "orders_medium": r["orders_medium"],
                "orders_long": r["orders_long"],
                "last_synced_at": r["last_synced_at"].isoformat() if r["last_synced_at"] else None,
            }
        )
    return web.json_response({"count": len(items), "items": items})

