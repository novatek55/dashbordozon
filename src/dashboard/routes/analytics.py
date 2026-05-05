"""Dashboard routes/analytics.py handlers."""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import aiohttp
from aiohttp import web

from src.config import settings
from src.dashboard.constants import MSK, DELIVERED_STATUSES
from src.dashboard.helpers import (
    clean_nan_values, as_float, normalize_offer_id, safe_divide,
    parse_date_utc, build_where, _ozon_post_json, _get_env_from_dotenv, _to_int,
    load_sku_identity_map,
)


async def get_analytics_product_queries(request: web.Request) -> web.Response:
    sku_raw = (request.query.get("sku") or "").strip()
    offer_id_raw = normalize_offer_id((request.query.get("offer_id") or "").strip())
    query_raw = (request.query.get("query") or "").strip()
    granularity_raw = (request.query.get("granularity") or "").strip().lower()
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    limit_raw = (request.query.get("limit") or "500").strip()

    try:
        limit = max(1, min(5000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    sku_filter = _to_int(sku_raw) if sku_raw else None
    if sku_raw and not sku_filter:
        return web.json_response({"error": "Invalid sku"}, status=400)

    if granularity_raw and granularity_raw not in {"day", "week"}:
        return web.json_response({"error": "Invalid granularity, expected day or week"}, status=400)

    try:
        date_from = parse_date_utc(date_from_raw, end_of_day=False) if date_from_raw else None
        date_to_exclusive = parse_date_utc(date_to_raw, end_of_day=True) if date_to_raw else None
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)

    params: List[Any] = []
    conditions: List[str] = []
    idx = 1

    if sku_filter is not None:
        conditions.append(f"sku = ${idx}")
        params.append(sku_filter)
        idx += 1
    if offer_id_raw:
        conditions.append(f"offer_id = ${idx}")
        params.append(offer_id_raw)
        idx += 1
    if query_raw:
        conditions.append(f"lower(coalesce(query_text, '')) LIKE lower(${idx})")
        params.append(f"%{query_raw}%")
        idx += 1
    if granularity_raw:
        conditions.append(f"granularity = ${idx}")
        params.append(granularity_raw)
        idx += 1
    if date_from is not None:
        conditions.append(f"period_start >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to_exclusive is not None:
        conditions.append(f"period_start < ${idx}")
        params.append(date_to_exclusive)
        idx += 1

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            period_start,
            period_end,
            granularity,
            sku,
            offer_id,
            product_name,
            query_text,
            searches,
            views,
            avg_position,
            conversion,
            gmv,
            sku_searches,
            sku_views,
            sku_avg_position,
            sku_conversion,
            sku_gmv,
            last_synced_at
        FROM analytics_product_queries_daily_view
        {where_sql}
        ORDER BY period_start DESC, sku, searches DESC NULLS LAST, query_text
        LIMIT {limit}
    """

    summary_sql = f"""
        SELECT
            count(*)::int AS rows_total,
            count(distinct sku)::int AS sku_total,
            count(distinct query_text)::int AS query_total,
            sum(coalesce(searches, 0))::bigint AS searches_total,
            sum(coalesce(views, 0))::bigint AS views_total,
            sum(coalesce(gmv, 0))::numeric AS gmv_total,
            avg(nullif(avg_position, 0))::float8 AS avg_position_mean
        FROM analytics_product_queries_daily_view
        {where_sql}
    """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
        summary_row = await conn.fetchrow(summary_sql, *params)

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "period_start": r["period_start"].isoformat() if r["period_start"] else None,
                "period_end": r["period_end"].isoformat() if r["period_end"] else None,
                "granularity": r["granularity"],
                "sku": r["sku"],
                "offer_id": r["offer_id"],
                "product_name": r["product_name"],
                "query_text": r["query_text"],
                "searches": r["searches"],
                "views": r["views"],
                "avg_position": as_float(r["avg_position"]) if r["avg_position"] is not None else None,
                "conversion": as_float(r["conversion"]) if r["conversion"] is not None else None,
                "gmv": as_float(r["gmv"]) if r["gmv"] is not None else None,
                "sku_searches": r["sku_searches"],
                "sku_views": r["sku_views"],
                "sku_avg_position": as_float(r["sku_avg_position"]) if r["sku_avg_position"] is not None else None,
                "sku_conversion": as_float(r["sku_conversion"]) if r["sku_conversion"] is not None else None,
                "sku_gmv": as_float(r["sku_gmv"]) if r["sku_gmv"] is not None else None,
                "last_synced_at": r["last_synced_at"].isoformat() if r["last_synced_at"] else None,
            }
        )

    summary = {
        "rows_total": int(summary_row["rows_total"] or 0) if summary_row else 0,
        "sku_total": int(summary_row["sku_total"] or 0) if summary_row else 0,
        "query_total": int(summary_row["query_total"] or 0) if summary_row else 0,
        "searches_total": int(summary_row["searches_total"] or 0) if summary_row else 0,
        "views_total": int(summary_row["views_total"] or 0) if summary_row else 0,
        "gmv_total": as_float(summary_row["gmv_total"]) if summary_row and summary_row["gmv_total"] is not None else 0.0,
        "avg_position_mean": as_float(summary_row["avg_position_mean"]) if summary_row and summary_row["avg_position_mean"] is not None else None,
    }
    return web.json_response({"count": len(items), "items": items, "summary": summary})


async def get_article_query_matrix(request: web.Request) -> web.Response:
    offer_filter = normalize_offer_id((request.query.get("offer_id") or "").strip())
    sku_raw = (request.query.get("sku") or "").strip()
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    limit_raw = (request.query.get("limit") or "30").strip()

    try:
        limit = max(1, min(200, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    sku_filter = _to_int(sku_raw) if sku_raw else None
    if sku_raw and not sku_filter:
        return web.json_response({"error": "Invalid sku"}, status=400)

    try:
        date_from_dt = parse_date_utc(date_from_raw, end_of_day=False) if date_from_raw else None
        date_to_exclusive = parse_date_utc(date_to_raw, end_of_day=True) if date_to_raw else None
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)

    today_utc = datetime.now(timezone.utc).date()
    if date_to_exclusive is None:
        date_to_exclusive = datetime.combine(today_utc + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    if date_from_dt is None:
        date_from_dt = date_to_exclusive - timedelta(days=30)
    if date_from_dt >= date_to_exclusive:
        return web.json_response({"error": "date_from must be <= date_to"}, status=400)

    start_day = date_from_dt.date()
    end_day = (date_to_exclusive - timedelta(days=1)).date()
    day_list = [start_day + timedelta(days=i) for i in range((end_day - start_day).days + 1)]
    if not day_list:
        return web.json_response({"count": 0, "items": [], "summary": {"days": []}})

    pool: asyncpg.Pool = request.app["pool"]
    params: List[Any] = [start_day, date_to_exclusive.date()]
    conditions = [
        "period_start::date >= $1::date",
        "period_start::date < $2::date",
        "granularity = 'day'",
    ]
    idx = 3
    if sku_filter is not None:
        conditions.append(f"sku = ${idx}")
        params.append(sku_filter)
        idx += 1
    if offer_filter:
        conditions.append(f"offer_id = ${idx}")
        params.append(offer_filter)
        idx += 1
    where_sql = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT
            period_start::date AS day,
            sku,
            offer_id,
            product_name,
            query_text,
            coalesce(searches, 0) AS searches,
            coalesce(views, 0) AS views,
            avg_position,
            conversion,
            coalesce(gmv, 0) AS gmv
        FROM analytics_product_queries_daily_view
        {where_sql}
        ORDER BY sku, query_text, day
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    if not rows:
        return web.json_response(
            {
                "count": 0,
                "items": [],
                "summary": {
                    "days": [d.isoformat() for d in day_list],
                    "date_from": start_day.isoformat(),
                    "date_to": end_day.isoformat(),
                    "default_metric": "coverage",
                },
            }
        )

    def _safe_ratio(num: float, den: float) -> float:
        if den <= 0:
            return 0.0
        return num / den

    def _pct_delta(current: float, previous: float) -> Optional[float]:
        if abs(previous) < 1e-9:
            return None
        return ((current - previous) / previous) * 100.0

    window = min(7, max(1, len(day_list) // 2 if len(day_list) < 14 else 7))
    current_days = set(day_list[-window:])
    previous_days = set(day_list[-(window * 2):-window]) if len(day_list) > window else set()

    query_map: Dict[Tuple[int, str], Dict[str, Any]] = {}
    sku_offer_map: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        sku = int(r["sku"])
        query_text = str(r["query_text"] or "").strip()
        if not query_text:
            continue
        sku_offer_map.setdefault(
            sku,
            {
                "sku": sku,
                "offer_id": r["offer_id"],
                "product_name": r["product_name"],
            },
        )
        key = (sku, query_text)
        bucket = query_map.setdefault(
            key,
            {
                "sku": sku,
                "offer_id": r["offer_id"],
                "product_name": r["product_name"],
                "query_text": query_text,
                "by_day": {},
            },
        )
        day = r["day"]
        searches = int(r["searches"] or 0)
        views = int(r["views"] or 0)
        coverage = _safe_ratio(float(views), float(searches)) * 100.0
        bucket["by_day"][day] = {
            "searches": searches,
            "views": views,
            "coverage": coverage,
            "avg_position": as_float(r["avg_position"]) if r["avg_position"] is not None else 0.0,
            "conversion": as_float(r["conversion"]) if r["conversion"] is not None else 0.0,
            "gmv": as_float(r["gmv"]) if r["gmv"] is not None else 0.0,
        }

    items: List[Dict[str, Any]] = []
    summary_searches_total = 0
    summary_views_total = 0
    summary_gmv_total = 0.0
    for (_, query_text), query_row in query_map.items():
        by_day = query_row["by_day"]
        daily = []
        searches_total = 0
        views_total = 0
        gmv_total = 0.0
        coverage_values: List[float] = []
        position_values: List[float] = []
        conversion_values: List[float] = []
        for day in day_list:
            vals = by_day.get(
                day,
                {
                    "searches": 0,
                    "views": 0,
                    "coverage": 0.0,
                    "avg_position": 0.0,
                    "conversion": 0.0,
                    "gmv": 0.0,
                },
            )
            searches_total += int(vals["searches"])
            views_total += int(vals["views"])
            gmv_total += as_float(vals["gmv"])
            if vals["searches"] > 0:
                coverage_values.append(as_float(vals["coverage"]))
            if vals["avg_position"] > 0:
                position_values.append(as_float(vals["avg_position"]))
            if vals["conversion"] > 0:
                conversion_values.append(as_float(vals["conversion"]))
            daily.append(
                {
                    "day": day.isoformat(),
                    "searches": int(vals["searches"]),
                    "views": int(vals["views"]),
                    "coverage": as_float(vals["coverage"]),
                    "avg_position": as_float(vals["avg_position"]),
                    "conversion": as_float(vals["conversion"]),
                    "gmv": as_float(vals["gmv"]),
                }
            )

        current_searches = sum(int(by_day.get(day, {}).get("searches", 0)) for day in current_days)
        previous_searches = sum(int(by_day.get(day, {}).get("searches", 0)) for day in previous_days)
        current_views = sum(int(by_day.get(day, {}).get("views", 0)) for day in current_days)
        previous_views = sum(int(by_day.get(day, {}).get("views", 0)) for day in previous_days)
        current_gmv = sum(as_float(by_day.get(day, {}).get("gmv", 0.0)) for day in current_days)
        previous_gmv = sum(as_float(by_day.get(day, {}).get("gmv", 0.0)) for day in previous_days)
        current_coverage = _safe_ratio(float(current_views), float(current_searches)) * 100.0
        previous_coverage = _safe_ratio(float(previous_views), float(previous_searches)) * 100.0
        current_positions = [as_float(by_day.get(day, {}).get("avg_position", 0.0)) for day in current_days if as_float(by_day.get(day, {}).get("avg_position", 0.0)) > 0]
        previous_positions = [as_float(by_day.get(day, {}).get("avg_position", 0.0)) for day in previous_days if as_float(by_day.get(day, {}).get("avg_position", 0.0)) > 0]
        current_conversion_values = [as_float(by_day.get(day, {}).get("conversion", 0.0)) for day in current_days if as_float(by_day.get(day, {}).get("conversion", 0.0)) > 0]
        previous_conversion_values = [as_float(by_day.get(day, {}).get("conversion", 0.0)) for day in previous_days if as_float(by_day.get(day, {}).get("conversion", 0.0)) > 0]

        item = {
            "sku": query_row["sku"],
            "offer_id": query_row["offer_id"],
            "product_name": query_row["product_name"],
            "query_text": query_text,
            "searches_total": searches_total,
            "views_total": views_total,
            "coverage_total": _safe_ratio(float(views_total), float(searches_total)) * 100.0,
            "avg_position_total": (sum(position_values) / len(position_values)) if position_values else 0.0,
            "conversion_total": (sum(conversion_values) / len(conversion_values)) if conversion_values else 0.0,
            "gmv_total": gmv_total,
            "searches_delta_7v7": _pct_delta(float(current_searches), float(previous_searches)),
            "views_delta_7v7": _pct_delta(float(current_views), float(previous_views)),
            "coverage_delta_7v7": _pct_delta(current_coverage, previous_coverage),
            "position_delta_7v7": _pct_delta(
                (sum(current_positions) / len(current_positions)) if current_positions else 0.0,
                (sum(previous_positions) / len(previous_positions)) if previous_positions else 0.0,
            ),
            "conversion_delta_7v7": _pct_delta(
                (sum(current_conversion_values) / len(current_conversion_values)) if current_conversion_values else 0.0,
                (sum(previous_conversion_values) / len(previous_conversion_values)) if previous_conversion_values else 0.0,
            ),
            "gmv_delta_7v7": _pct_delta(current_gmv, previous_gmv),
            "searches_cur7": int(current_searches),
            "searches_prev7": int(previous_searches),
            "views_cur7": int(current_views),
            "views_prev7": int(previous_views),
            "coverage_cur7": round(current_coverage, 2),
            "coverage_prev7": round(previous_coverage, 2),
            "position_cur7": round((sum(current_positions) / len(current_positions)) if current_positions else 0.0, 2),
            "position_prev7": round((sum(previous_positions) / len(previous_positions)) if previous_positions else 0.0, 2),
            "conversion_cur7": round((sum(current_conversion_values) / len(current_conversion_values)) if current_conversion_values else 0.0, 2),
            "conversion_prev7": round((sum(previous_conversion_values) / len(previous_conversion_values)) if previous_conversion_values else 0.0, 2),
            "gmv_cur7": round(current_gmv, 0),
            "gmv_prev7": round(previous_gmv, 0),
            "daily": daily,
        }
        items.append(item)
        summary_searches_total += searches_total
        summary_views_total += views_total
        summary_gmv_total += gmv_total

    items.sort(key=lambda item: (-as_float(item.get("views_total"), 0.0), -as_float(item.get("gmv_total"), 0.0), str(item.get("query_text") or "")))
    items = items[:limit]

    summary = {
        "days": [d.isoformat() for d in day_list],
        "date_from": start_day.isoformat(),
        "date_to": end_day.isoformat(),
        "default_metric": "coverage",
        "metric_options": ["coverage", "views", "searches", "avg_position", "conversion", "gmv"],
        "queries_total": len(items),
        "searches_total": summary_searches_total,
        "views_total": summary_views_total,
        "coverage_total": _safe_ratio(float(summary_views_total), float(summary_searches_total)) * 100.0,
        "gmv_total": summary_gmv_total,
        "trend_window_days": window,
    }
    return web.json_response(clean_nan_values({"count": len(items), "items": items, "summary": summary}))


async def get_article_analytics(request: web.Request) -> web.Response:
    offer_filter = normalize_offer_id((request.query.get("offer_id") or "").strip()).lower()
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    limit_raw = (request.query.get("limit") or "500").strip()

    try:
        limit = max(1, min(5000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    try:
        date_from_dt = parse_date_utc(date_from_raw, end_of_day=False) if date_from_raw else None
        date_to_exclusive = parse_date_utc(date_to_raw, end_of_day=True) if date_to_raw else None
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)

    today_utc = datetime.now(timezone.utc).date()
    if date_to_exclusive is None:
        date_to_exclusive = datetime.combine(today_utc + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    if date_from_dt is None:
        date_from_dt = date_to_exclusive - timedelta(days=30)
    if date_from_dt >= date_to_exclusive:
        return web.json_response({"error": "date_from must be <= date_to"}, status=400)

    start_day = date_from_dt.date()
    end_day = (date_to_exclusive - timedelta(days=1)).date()
    day_list = [start_day + timedelta(days=i) for i in range((end_day - start_day).days + 1)]
    days_total = len(day_list)
    window = min(7, max(1, 7 if days_total >= 14 else max(1, days_total // 2)))
    trend_window = 3 if days_total >= 6 else max(1, days_total // 2)
    current_days = set(day_list[-window:])
    previous_days = set(day_list[-(window * 2):-window]) if days_total > window else set()

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async def _fetch_optional(sql: str, *params):
            try:
                return await conn.fetch(sql, *params)
            except asyncpg.UndefinedTableError:
                return []

        raw_daily_rows = await conn.fetch(
            """
            SELECT
                ad.date::date AS day,
                ad.sku,
                coalesce((ad.metric_values ->> 'revenue')::numeric, ad.revenue, 0) AS revenue,
                coalesce((ad.metric_values ->> 'ordered_units')::numeric, ad.ordered_units, 0) AS ordered_units,
                coalesce((ad.metric_values ->> 'hits_view_search')::numeric, 0) AS hits_view_search,
                coalesce((ad.metric_values ->> 'hits_view_pdp')::numeric, 0) AS hits_view_pdp,
                coalesce((ad.metric_values ->> 'hits_view')::numeric, ad.impressions, 0) AS hits_view,
                coalesce((ad.metric_values ->> 'hits_tocart_search')::numeric, 0) AS hits_tocart_search,
                coalesce((ad.metric_values ->> 'hits_tocart_pdp')::numeric, 0) AS hits_tocart_pdp,
                coalesce((ad.metric_values ->> 'hits_tocart')::numeric, ad.clicks, 0) AS hits_tocart,
                coalesce((ad.metric_values ->> 'session_view_search')::numeric, 0) AS session_view_search,
                coalesce((ad.metric_values ->> 'session_view_pdp')::numeric, 0) AS session_view_pdp,
                coalesce((ad.metric_values ->> 'session_view')::numeric, 0) AS session_view,
                coalesce((ad.metric_values ->> 'conv_tocart_search')::numeric, 0) AS conv_tocart_search,
                coalesce((ad.metric_values ->> 'conv_tocart_pdp')::numeric, 0) AS conv_tocart_pdp,
                coalesce((ad.metric_values ->> 'conv_tocart')::numeric, ad.ctr, 0) AS conv_tocart,
                coalesce((ad.metric_values ->> 'returns')::numeric, ad.returned_units, 0) AS returns_units,
                coalesce((ad.metric_values ->> 'cancellations')::numeric, 0) AS cancellations,
                coalesce((ad.metric_values ->> 'delivered_units')::numeric, ad.delivered_units, 0) AS delivered_units,
                coalesce((ad.metric_values ->> 'position_category')::numeric, ad.position_category, ad.position, 0) AS position_category
            FROM analytics_data ad
            WHERE ad.date::date >= $1::date
              AND ad.date::date < $2::date
            ORDER BY ad.sku, day
            """,
            start_day,
            date_to_exclusive.date(),
        )

        raw_sku_list = sorted({int(r["sku"]) for r in raw_daily_rows if r["sku"] is not None})
        identity_map = await load_sku_identity_map(conn, raw_sku_list)
        daily_rows: List[Dict[str, Any]] = []
        for row in raw_daily_rows:
            sku_val = _to_int(row["sku"])
            if not sku_val:
                continue
            offer_id = (identity_map.get(sku_val) or {}).get("offer_id") or f"sku_{sku_val}"
            if offer_filter and offer_id.lower() != offer_filter:
                continue
            row_copy = dict(row)
            row_copy["offer_id"] = offer_id
            daily_rows.append(row_copy)

        if not daily_rows:
            return web.json_response(
                {
                    "count": 0,
                    "items": [],
                    "summary": {
                        "period_days": days_total,
                        "date_from": start_day.isoformat(),
                        "date_to": end_day.isoformat(),
                    },
                }
            )

        sku_list = sorted({int(r["sku"]) for r in daily_rows})
        if set(sku_list) != set(raw_sku_list):
            identity_map = await load_sku_identity_map(conn, sku_list)
        price_rows = await conn.fetch(
            """
            WITH ranked AS (
                SELECT
                    sku,
                    price_current,
                    price_base,
                    row_number() OVER (
                        PARTITION BY sku
                        ORDER BY last_synced_at DESC NULLS LAST
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
            WHERE sku = any($1::bigint[]) AND rn = 1
            """,
            sku_list,
        )
        price_by_sku: Dict[int, Dict[str, Any]] = {
            int(r["sku"]): {
                "price_current": r["price_current"],
                "price_base": r["price_base"],
            }
            for r in price_rows
            if r["sku"] is not None
        }
        product_rows = [
            {
                "sku": sku,
                "offer_id": (identity_map.get(sku) or {}).get("offer_id"),
                "product_name": (identity_map.get(sku) or {}).get("product_name"),
                "price_current": (price_by_sku.get(sku) or {}).get("price_current"),
                "price_base": (price_by_sku.get(sku) or {}).get("price_base"),
            }
            for sku in sku_list
        ]

        ads_rows = await _fetch_optional(
            """
            SELECT
                sku,
                date::date AS day,
                sum(coalesce(views, 0))::float8 AS ad_impressions,
                sum(coalesce(clicks, 0))::float8 AS clicks,
                sum(coalesce(spent, 0))::float8 AS spent,
                sum(coalesce(revenue, 0))::float8 AS ad_revenue
            FROM campaign_statistics
            WHERE sku = any($1::bigint[])
              AND date::date >= $2::date
              AND date::date < $3::date
            GROUP BY sku, date::date
            """,
            sku_list,
            start_day,
            date_to_exclusive.date(),
        )

        stock_rows = await _fetch_optional(
            """
            WITH fbo AS (
                SELECT
                    sku,
                    sum(
                        coalesce(available_stock_count, 0)
                        + coalesce(requested_stock_count, 0)
                        + coalesce(transit_stock_count, 0)
                        + coalesce(waiting_docs_stock_count, 0)
                    )::float8 AS stock_fbo,
                    sum(coalesce(available_stock_count, 0))::float8 AS stock_fbo_available,
                    sum(coalesce(requested_stock_count, 0))::float8 AS stock_fbo_supply,
                    sum(coalesce(transit_stock_count, 0))::float8 AS stock_fbo_transit,
                    sum(coalesce(waiting_docs_stock_count, 0))::float8 AS stock_fbo_acceptance
                FROM analytics_stocks
                WHERE sku = any($1::bigint[])
                GROUP BY sku
            ),
            fbs AS (
                SELECT sku, sum(coalesce(present, 0))::float8 AS stock_fbs
                FROM fbs_warehouse_stocks
                WHERE sku = any($1::bigint[])
                GROUP BY sku
            )
            SELECT
                coalesce(fbo.sku, fbs.sku) AS sku,
                coalesce(fbo.stock_fbo, 0) AS stock_fbo,
                coalesce(fbo.stock_fbo_available, 0) AS stock_fbo_available,
                coalesce(fbo.stock_fbo_supply, 0) AS stock_fbo_supply,
                coalesce(fbo.stock_fbo_transit, 0) AS stock_fbo_transit,
                coalesce(fbo.stock_fbo_acceptance, 0) AS stock_fbo_acceptance,
                coalesce(fbs.stock_fbs, 0) AS stock_fbs
            FROM fbo
            FULL JOIN fbs ON fbs.sku = fbo.sku
            """,
            sku_list,
        )

        # РћСЃС‚Р°С‚РєРё РїРѕ РєР»Р°СЃС‚РµСЂР°Рј СЃ AoT (СЃСЂРµРґРЅРµРµ РІСЂРµРјСЏ РґРѕСЃС‚Р°РІРєРё РєР»Р°СЃС‚РµСЂР°)
        cluster_stock_rows = await _fetch_optional(
            """
            SELECT
                ast.sku,
                ast.cluster_id,
                ast.cluster_name,
                sum(coalesce(ast.available_stock_count, 0))::float8 AS available,
                sum(
                    coalesce(ast.available_stock_count, 0)
                    + coalesce(ast.requested_stock_count, 0)
                    + coalesce(ast.transit_stock_count, 0)
                    + coalesce(ast.waiting_docs_stock_count, 0)
                )::float8 AS stock_total,
                avg(adt.average_delivery_time)::float8 AS avg_delivery_time,
                max(adt.average_delivery_time_status) AS delivery_status
            FROM analytics_stocks ast
            LEFT JOIN analytics_average_delivery_time adt
              ON adt.delivery_cluster_id = ast.cluster_id
            WHERE ast.sku = any($1::bigint[])
              AND ast.cluster_id IS NOT NULL
            GROUP BY ast.sku, ast.cluster_id, ast.cluster_name
            ORDER BY ast.sku, sum(coalesce(ast.available_stock_count, 0)) DESC
            """,
            sku_list,
        )

        review_rows = await _fetch_optional(
            """
            SELECT
                sku,
                avg(rating)::float8 AS rating_avg,
                count(*)::int AS reviews_total,
                count(*) FILTER (WHERE published_at::date >= $2::date AND published_at::date < $3::date)::int AS reviews_30d,
                count(*) FILTER (WHERE published_at::date >= ($2::date - interval '30 days') AND published_at::date < $2::date)::int AS reviews_prev_30d
            FROM reviews
            WHERE sku = any($1::bigint[])
            GROUP BY sku
            """,
            sku_list,
            start_day,
            date_to_exclusive.date(),
        )

        # Р”РЅРµРІРЅР°СЏ СЂР°Р·Р±РёРІРєР° РѕС‚Р·С‹РІРѕРІ РїРѕ SKU: СЃРєРѕР»СЊРєРѕ РґРѕР±Р°РІР»РµРЅРѕ Р·Р° РґРµРЅСЊ Рё РЅР°РєРѕРїРёС‚РµР»СЊРЅС‹Р№ СЃСЂРµРґРЅРёР№ СЂРµР№С‚РёРЅРі РґРѕ РєРѕРЅС†Р° РґРЅСЏ
        review_daily_rows = await _fetch_optional(
            """
            SELECT
                sku,
                published_at::date AS day,
                count(*)::int AS reviews_added,
                avg(rating)::float8 AS rating_day,
                sum(rating)::float8 AS rating_sum,
                count(*) FILTER (WHERE published_at::date < $2::date)::int AS reviews_before_period
            FROM reviews
            WHERE sku = any($1::bigint[])
              AND published_at IS NOT NULL
              AND published_at::date <= $3::date
            GROUP BY sku, published_at::date
            ORDER BY sku, day
            """,
            sku_list,
            start_day,
            (date_to_exclusive - timedelta(days=1)).date(),
        )

        # Р‘Р°Р·РѕРІРѕРµ СЃРѕСЃС‚РѕСЏРЅРёРµ (РЅР°РєРѕРїР»РµРЅРЅС‹Рµ count Рё sum СЂРµР№С‚РёРЅРіР° РґРѕ start_day) вЂ” РѕС‚РґРµР»СЊРЅС‹Рј Р·Р°РїСЂРѕСЃРѕРј
        review_baseline_rows = await _fetch_optional(
            """
            SELECT
                sku,
                count(*)::int AS cum_count,
                sum(rating)::float8 AS cum_sum
            FROM reviews
            WHERE sku = any($1::bigint[])
              AND published_at IS NOT NULL
              AND published_at::date < $2::date
            GROUP BY sku
            """,
            sku_list,
            start_day,
        )

        # promo_products.sku хранит product_id Ozon; сопоставляем через identity_map.
        sku_to_product_id: Dict[int, int] = {
            int(sku): int(data["product_id"])
            for sku, data in identity_map.items()
            if data.get("product_id") is not None
        }
        product_id_to_skus: Dict[int, List[int]] = {}
        for sku, product_id in sku_to_product_id.items():
            product_id_to_skus.setdefault(product_id, []).append(sku)
        promo_product_ids = sorted(product_id_to_skus.keys())

        promo_rows: List[Dict[str, Any]] = []
        promo_event_rows: List[Dict[str, Any]] = []
        if promo_product_ids:
            raw_promo_rows = await _fetch_optional(
                """
                SELECT
                    pp.sku::bigint AS product_id,
                    pa.action_id,
                    pa.title,
                    pa.action_type,
                    pa.date_start,
                    pa.date_end,
                    pp.regular_price,
                    pp.action_price,
                    pp.discount_percent,
                    pp.is_participating,
                    pp.is_candidate,
                    pp.stock,
                    pp.min_stock,
                    pp.max_action_price,
                    pp.first_seen_at
                FROM promo_products pp
                JOIN promo_actions pa ON pa.id = pp.action_id
                WHERE pp.sku = any($1::bigint[])
                  AND (pa.date_end IS NULL OR pa.date_end >= now())
                ORDER BY pp.is_participating DESC, pp.discount_percent DESC NULLS LAST
                """,
                promo_product_ids,
            )
            for r in raw_promo_rows:
                product_id = _to_int(r.get("product_id"))
                if not product_id:
                    continue
                for sku in product_id_to_skus.get(product_id, []):
                    row_copy = dict(r)
                    row_copy["sku"] = sku
                    promo_rows.append(row_copy)

            raw_promo_event_rows = await _fetch_optional(
                """
                SELECT pe.action_id, pe.sku::bigint AS product_id, pe.event_type, pe.source, pe.detected_at
                FROM promo_product_events pe
                WHERE pe.sku = any($1::bigint[])
                ORDER BY pe.detected_at
                """,
                promo_product_ids,
            )
            for r in raw_promo_event_rows:
                product_id = _to_int(r.get("product_id"))
                if not product_id:
                    continue
                for sku in product_id_to_skus.get(product_id, []):
                    promo_event_rows.append(
                        {
                            "action_id": r["action_id"],
                            "sku": sku,
                            "event_type": r["event_type"],
                            "source": r["source"],
                            "detected_at": r["detected_at"],
                        }
                    )

        price_daily_rows = await _fetch_optional(
            """
            SELECT
                foi.sku,
                (fo.created_at AT TIME ZONE 'UTC')::date AS day,
                sum(coalesce(foi.price, 0) * coalesce(foi.quantity, 0))::float8
                    / nullif(sum(coalesce(foi.quantity, 0)), 0)::float8 AS avg_seller_price,
                sum(coalesce(foi.buyer_paid, 0) * coalesce(foi.quantity, 0))::float8
                    / nullif(sum(coalesce(foi.quantity, 0)), 0)::float8 AS avg_buyer_paid,
                sum(coalesce(foi.quantity, 0))::float8 AS quantity_day
            FROM fact_order_items foi
            JOIN fact_orders fo ON fo.order_id = foi.order_id
            WHERE foi.sku = any($1::bigint[])
              AND (fo.created_at AT TIME ZONE 'UTC')::date >= $2::date
              AND (fo.created_at AT TIME ZONE 'UTC')::date < $3::date
              AND coalesce(foi.quantity, 0) > 0
            GROUP BY foi.sku, (fo.created_at AT TIME ZONE 'UTC')::date
            """,
            sku_list,
            start_day,
            date_to_exclusive.date(),
        )

        stock_split_daily_rows = await _fetch_optional(
            """
            SELECT
                foi.sku,
                (fo.created_at AT TIME ZONE 'UTC')::date AS day,
                upper(coalesce(fo.delivery_schema, '')) AS delivery_schema,
                sum(coalesce(foi.quantity, 0))::float8 AS ordered_units_day
            FROM fact_order_items foi
            JOIN fact_orders fo ON fo.order_id = foi.order_id
            WHERE foi.sku = any($1::bigint[])
              AND (fo.created_at AT TIME ZONE 'UTC')::date >= $2::date
              AND (fo.created_at AT TIME ZONE 'UTC')::date < $3::date
              AND coalesce(foi.quantity, 0) > 0
            GROUP BY foi.sku, (fo.created_at AT TIME ZONE 'UTC')::date, upper(coalesce(fo.delivery_schema, ''))
            """,
            sku_list,
            start_day,
            date_to_exclusive.date(),
        )

    product_map: Dict[int, Dict[str, Any]] = {
        int(r["sku"]): {
            "offer_id": r["offer_id"],
            "name": r["product_name"],
            "price_current": as_float(r["price_current"], 0.0),
            "price_base": as_float(r["price_base"], 0.0),
        }
        for r in product_rows
    }
    stock_map: Dict[int, Dict[str, float]] = {
        int(r["sku"]): {
            "stock_fbo": as_float(r["stock_fbo"], 0.0),
            "stock_fbo_available": as_float(r["stock_fbo_available"], 0.0),
            "stock_fbo_supply": as_float(r["stock_fbo_supply"], 0.0),
            "stock_fbo_transit": as_float(r["stock_fbo_transit"], 0.0),
            "stock_fbo_acceptance": as_float(r["stock_fbo_acceptance"], 0.0),
            "stock_fbs": as_float(r["stock_fbs"], 0.0),
        }
        for r in stock_rows
    }
    cluster_stock_map: Dict[int, List[Dict[str, Any]]] = {}
    for r in cluster_stock_rows:
        cluster_stock_map.setdefault(int(r["sku"]), []).append({
            "cluster_id": int(r["cluster_id"]),
            "cluster_name": r["cluster_name"] or "",
            "available": as_float(r["available"], 0.0),
            "stock_total": as_float(r["stock_total"], 0.0),
            "avg_delivery_time": as_float(r["avg_delivery_time"], 0.0) if r["avg_delivery_time"] is not None else None,
            "delivery_status": r["delivery_status"] or "",
        })
    review_map: Dict[int, Dict[str, Any]] = {
        int(r["sku"]): {
            "rating": as_float(r["rating_avg"], 0.0),
            "reviews_total": int(r["reviews_total"] or 0),
            "reviews_delta_30d": int(r["reviews_30d"] or 0) - int(r["reviews_prev_30d"] or 0),
        }
        for r in review_rows
    }

    # РќР°РєРѕРїРёС‚РµР»СЊРЅС‹Р№ (cumulative) СЃСЂРµРґРЅРёР№ СЂРµР№С‚РёРЅРі РїРѕ РґРЅСЏРј РїРµСЂРёРѕРґР° РґР»СЏ РєР°Р¶РґРѕРіРѕ SKU.
    # РќР° РєР°Р¶РґС‹Р№ РґРµРЅСЊ РїРµСЂРёРѕРґР°: СЃРєРѕР»СЊРєРѕ РѕС‚Р·С‹РІРѕРІ РІСЃРµРіРѕ Рє РєРѕРЅС†Сѓ РґРЅСЏ Рё РЅР°РєРѕРїР»РµРЅРЅС‹Р№ СЃСЂРµРґРЅРёР№ СЂРµР№С‚РёРЅРі.
    # Р”РЅРё, РіРґРµ РѕС‚Р·С‹РІРѕРІ РЅРµ РґРѕР±Р°РІР»СЏР»РѕСЃСЊ, РЅР°СЃР»РµРґСѓСЋС‚ Р·РЅР°С‡РµРЅРёСЏ РїСЂРµРґС‹РґСѓС‰РµРіРѕ РґРЅСЏ.
    review_baseline_map: Dict[int, Dict[str, float]] = {
        int(r["sku"]): {"cum_count": int(r["cum_count"] or 0), "cum_sum": as_float(r["cum_sum"], 0.0)}
        for r in review_baseline_rows
    }
    review_day_added: Dict[int, Dict[Any, Dict[str, float]]] = {}
    for r in review_daily_rows:
        review_day_added.setdefault(int(r["sku"]), {})[r["day"]] = {
            "added": int(r["reviews_added"] or 0),
            "sum": as_float(r["rating_sum"], 0.0),
            "avg_day": as_float(r["rating_day"], 0.0),
        }
    review_daily_map: Dict[int, Dict[Any, Dict[str, float]]] = {}
    for sku in sku_list:
        baseline = review_baseline_map.get(sku, {"cum_count": 0, "cum_sum": 0.0})
        cum_cnt = baseline["cum_count"]
        cum_sum = baseline["cum_sum"]
        per_day = review_day_added.get(sku, {})
        series: Dict[Any, Dict[str, float]] = {}
        for d in day_list:
            day_data = per_day.get(d)
            if day_data:
                cum_cnt += day_data["added"]
                cum_sum += day_data["sum"]
                added_today = day_data["added"]
            else:
                added_today = 0
            series[d] = {
                "review_count_day": added_today,
                "review_count_cum": cum_cnt,
                "review_avg_cum": (cum_sum / cum_cnt) if cum_cnt > 0 else 0.0,
            }
        review_daily_map[sku] = series
    # РРЅРґРµРєСЃ СЃРѕР±С‹С‚РёР№ РїРѕ (action_id, sku) в†’ СЃРїРёСЃРѕРє events
    promo_events_by_key: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for r in promo_event_rows:
        key = (int(r["action_id"]), int(r["sku"]))
        promo_events_by_key.setdefault(key, []).append({
            "event_type": r["event_type"],
            "source": r["source"],
            "detected_at": r["detected_at"].isoformat() if r["detected_at"] else None,
        })

    promo_map: Dict[int, List[Dict[str, Any]]] = {}
    for r in promo_rows:
        action_id_val = int(r["action_id"]) if r["action_id"] is not None else None
        sku_val = int(r["sku"])
        # РЎРѕР±С‹С‚РёСЏ РґР»СЏ РґР°РЅРЅРѕРіРѕ С‚РѕРІР°СЂР° РІ РґР°РЅРЅРѕР№ Р°РєС†РёРё
        events_key = (action_id_val, sku_val) if action_id_val else None
        events = promo_events_by_key.get(events_key, []) if events_key else []
        promo_map.setdefault(sku_val, []).append({
            "action_id": action_id_val,
            "title": r["title"] or "",
            "action_type": r["action_type"] or "",
            "date_start": r["date_start"].isoformat() if r["date_start"] else None,
            "date_end": r["date_end"].isoformat() if r["date_end"] else None,
            "regular_price": as_float(r["regular_price"], 0.0),
            "action_price": as_float(r["action_price"], 0.0),
            "discount_percent": as_float(r["discount_percent"], 0.0),
            "is_participating": bool(r["is_participating"]),
            "is_candidate": bool(r["is_candidate"]),
            "stock": int(r["stock"]) if r["stock"] is not None else None,
            "min_stock": int(r["min_stock"]) if r["min_stock"] is not None else None,
            "max_action_price": as_float(r["max_action_price"], 0.0),
            "first_seen_at": r["first_seen_at"].isoformat() if r["first_seen_at"] else None,
            "events": events,
        })
    ad_daily_map: Dict[int, Dict[date, Dict[str, float]]] = {}
    for r in ads_rows:
        sku = int(r["sku"])
        ad_daily_map.setdefault(sku, {})[r["day"]] = {
            "ad_impressions": as_float(r["ad_impressions"], 0.0),
            "ad_clicks": as_float(r["clicks"], 0.0) if "clicks" in r else 0.0,
            "ad_spend": as_float(r["spent"], 0.0),
            "ad_revenue": as_float(r["ad_revenue"], 0.0),
        }
    price_daily_map: Dict[int, Dict[date, Dict[str, float]]] = {}
    for r in price_daily_rows:
        sku = int(r["sku"])
        price_daily_map.setdefault(sku, {})[r["day"]] = {
            "avg_seller_price": as_float(r["avg_seller_price"], 0.0),
            "avg_buyer_paid": as_float(r["avg_buyer_paid"], 0.0),
            "quantity_day": as_float(r["quantity_day"], 0.0),
        }
    stock_split_daily_map: Dict[int, Dict[date, Dict[str, float]]] = {}
    for r in stock_split_daily_rows:
        sku = int(r["sku"])
        day = r["day"]
        schema = str(r["delivery_schema"] or "").upper()
        bucket = stock_split_daily_map.setdefault(sku, {}).setdefault(
            day,
            {"ordered_units_fbo": 0.0, "ordered_units_fbs": 0.0},
        )
        qty = as_float(r["ordered_units_day"], 0.0)
        if schema == "FBO":
            bucket["ordered_units_fbo"] += qty
        elif schema in {"FBS", "RFBS"}:
            bucket["ordered_units_fbs"] += qty

    sku_daily: Dict[Tuple[int, str], Dict[date, Dict[str, float]]] = {}
    for r in daily_rows:
        sku = int(r["sku"])
        offer_norm = normalize_offer_id(r["offer_id"])
        key = (sku, offer_norm)
        sku_daily.setdefault(key, {})[r["day"]] = {
            "revenue": as_float(r["revenue"], 0.0),
            "ordered_units": as_float(r["ordered_units"], 0.0),
            "hits_view_search": as_float(r["hits_view_search"], 0.0),
            "hits_view_pdp": as_float(r["hits_view_pdp"], 0.0),
            "hits_view": as_float(r["hits_view"], 0.0),
            "hits_tocart_search": as_float(r["hits_tocart_search"], 0.0),
            "hits_tocart_pdp": as_float(r["hits_tocart_pdp"], 0.0),
            "hits_tocart": as_float(r["hits_tocart"], 0.0),
            "session_view_search": as_float(r["session_view_search"], 0.0),
            "session_view_pdp": as_float(r["session_view_pdp"], 0.0),
            "session_view": as_float(r["session_view"], 0.0),
            "conv_tocart_search": as_float(r["conv_tocart_search"], 0.0),
            "conv_tocart_pdp": as_float(r["conv_tocart_pdp"], 0.0),
            "conv_tocart": as_float(r["conv_tocart"], 0.0),
            "returns_units": as_float(r["returns_units"], 0.0),
            "cancellations": as_float(r["cancellations"], 0.0),
            "delivered_units": as_float(r["delivered_units"], 0.0),
            "position_category": as_float(r["position_category"], 0.0),
        }

    def _sum_metric(day_map: Dict[date, Dict[str, float]], metric: str, days: Optional[set] = None) -> float:
        return sum(as_float(vals.get(metric), 0.0) for d, vals in day_map.items() if days is None or d in days)

    def _avg_metric(day_map: Dict[date, Dict[str, float]], metric: str, days: Optional[set] = None) -> float:
        values = [as_float(vals.get(metric), 0.0) for d, vals in day_map.items() if (days is None or d in days) and as_float(vals.get(metric), 0.0) > 0]
        return (sum(values) / len(values)) if values else 0.0

    def _pct_delta(current: float, previous: float) -> Optional[float]:
        if abs(previous) < 1e-9:
            return None
        return ((current - previous) / previous) * 100.0

    def _trend_3v3(points: List[Dict[str, Any]], key: str, epsilon: float = 0.1) -> Optional[float]:
        if trend_window <= 0:
            return None
        if len(points) < trend_window * 2:
            return None
        current_slice = points[-trend_window:]
        previous_slice = points[-(trend_window * 2):-trend_window]
        if not previous_slice:
            return None
        current_avg = sum(as_float(p.get(key), 0.0) for p in current_slice) / max(len(current_slice), 1)
        previous_avg = sum(as_float(p.get(key), 0.0) for p in previous_slice) / max(len(previous_slice), 1)
        if abs(previous_avg) < 1e-9:
            return None
        return ((current_avg - previous_avg) / (previous_avg + epsilon)) * 100.0

    items: List[Dict[str, Any]] = []
    for (sku, offer_norm), day_map in sku_daily.items():
        product_info = product_map.get(sku, {})
        stock_info = stock_map.get(
            sku,
            {
                "stock_fbo": 0.0,
                "stock_fbo_available": 0.0,
                "stock_fbo_supply": 0.0,
                "stock_fbo_transit": 0.0,
                "stock_fbo_acceptance": 0.0,
                "stock_fbs": 0.0,
            },
        )
        review_info = review_map.get(sku, {"rating": 0.0, "reviews_total": 0, "reviews_delta_30d": 0})
        ad_by_day = ad_daily_map.get(sku, {})
        price_by_day = price_daily_map.get(sku, {})
        stock_split_by_day = stock_split_daily_map.get(sku, {})
        review_by_day = review_daily_map.get(sku, {})

        daily_points: List[Dict[str, Any]] = []
        for d in day_list:
            vals = day_map.get(d, {})
            ad_vals = ad_by_day.get(d, {})
            price_vals = price_by_day.get(d, {})
            stock_split_vals = stock_split_by_day.get(d, {})
            impressions_day = as_float(vals.get("hits_view"), 0.0)
            ad_impressions_day = as_float(ad_vals.get("ad_impressions"), 0.0)
            seo_impressions_day = max(0.0, impressions_day - ad_impressions_day)
            pdp_visitors_day = as_float(vals.get("session_view_pdp"), 0.0)
            pdp_ad_clicks_day = as_float(ad_vals.get("ad_clicks"), 0.0)
            pdp_seo_visitors_day = max(0.0, pdp_visitors_day - pdp_ad_clicks_day)
            clicks_day = as_float(vals.get("hits_tocart"), 0.0)
            orders_day = as_float(vals.get("ordered_units"), 0.0)
            orders_fbo_day = as_float(stock_split_vals.get("ordered_units_fbo"), 0.0)
            orders_fbs_day = as_float(stock_split_vals.get("ordered_units_fbs"), 0.0)
            ctr_day = (safe_divide(clicks_day, impressions_day) or 0.0) * 100.0
            conversion_day = (safe_divide(orders_day, clicks_day) or 0.0) * 100.0
            daily_points.append(
                {
                    "day": d.isoformat(),
                    "hits_view": impressions_day,
                    "seo_impressions": seo_impressions_day,
                    "ad_impressions": ad_impressions_day,
                    "session_view_pdp": pdp_visitors_day,
                    "pdp_ad_clicks": pdp_ad_clicks_day,
                    "pdp_seo_visitors": pdp_seo_visitors_day,
                    "hits_tocart": clicks_day,
                    "ordered_units": orders_day,
                    "ordered_units_fbo": orders_fbo_day,
                    "ordered_units_fbs": orders_fbs_day,
                    "revenue": as_float(vals.get("revenue"), 0.0),
                    "ctr": ctr_day,
                    "conversion": conversion_day,
                    "position_category": as_float(vals.get("position_category"), 0.0),
                    "ad_spend": as_float(ad_vals.get("ad_spend"), 0.0),
                    "ad_revenue": as_float(ad_vals.get("ad_revenue"), 0.0),
                    "avg_seller_price": as_float(price_vals.get("avg_seller_price"), 0.0),
                    "avg_buyer_paid": as_float(price_vals.get("avg_buyer_paid"), 0.0),
                    "price_current": as_float(product_info.get("price_current"), 0.0),
                    "review_count_day": int(review_by_day.get(d, {}).get("review_count_day", 0) or 0),
                    "review_count_cum": int(review_by_day.get(d, {}).get("review_count_cum", 0) or 0),
                    "review_avg_cum": as_float(review_by_day.get(d, {}).get("review_avg_cum", 0.0), 0.0),
                }
            )

        impressions_30d = _sum_metric(day_map, "hits_view")
        clicks_30d = _sum_metric(day_map, "hits_tocart")
        orders_30d = _sum_metric(day_map, "ordered_units")
        revenue_30d = _sum_metric(day_map, "revenue")
        delivered_units_30d = _sum_metric(day_map, "delivered_units")
        returns_30d = _sum_metric(day_map, "returns_units")
        cancellations_30d = _sum_metric(day_map, "cancellations")
        ad_impressions_30d = sum(as_float(v.get("ad_impressions"), 0.0) for v in ad_by_day.values())
        ad_clicks_30d = sum(as_float(v.get("ad_clicks"), 0.0) for v in ad_by_day.values())
        seo_impressions_30d = max(0.0, impressions_30d - ad_impressions_30d)

        ctr_30d = (safe_divide(clicks_30d, impressions_30d) or 0.0) * 100.0
        conversion_30d = (safe_divide(orders_30d, clicks_30d) or 0.0) * 100.0
        avg_check_30d = safe_divide(revenue_30d, orders_30d) or 0.0

        ctr_current = safe_divide(_sum_metric(day_map, "hits_tocart", current_days), _sum_metric(day_map, "hits_view", current_days)) or 0.0
        ctr_previous = safe_divide(_sum_metric(day_map, "hits_tocart", previous_days), _sum_metric(day_map, "hits_view", previous_days)) or 0.0
        conv_current = safe_divide(_sum_metric(day_map, "ordered_units", current_days), _sum_metric(day_map, "hits_tocart", current_days)) or 0.0
        conv_previous = safe_divide(_sum_metric(day_map, "ordered_units", previous_days), _sum_metric(day_map, "hits_tocart", previous_days)) or 0.0
        revenue_current = _sum_metric(day_map, "revenue", current_days)
        revenue_previous = _sum_metric(day_map, "revenue", previous_days)
        pos_current = _avg_metric(day_map, "position_category", current_days)
        pos_previous = _avg_metric(day_map, "position_category", previous_days)

        ctr_delta = _pct_delta(ctr_current, ctr_previous)
        conversion_delta = _pct_delta(conv_current, conv_previous)
        revenue_delta = _pct_delta(revenue_current, revenue_previous)
        position_delta = _pct_delta(pos_current, pos_previous)
        impressions_trend_3v3 = _trend_3v3(daily_points, "hits_view")
        orders_trend_3v3 = _trend_3v3(daily_points, "ordered_units")
        revenue_trend_3v3 = _trend_3v3(daily_points, "revenue")
        ctr_trend_3v3 = _trend_3v3(daily_points, "ctr")
        conversion_trend_3v3 = _trend_3v3(daily_points, "conversion")

        def _trend_cur_prev(points: List[Dict[str, Any]], key: str) -> tuple:
            if trend_window <= 0 or len(points) < trend_window * 2:
                return (None, None)
            cur_slice = points[-trend_window:]
            prev_slice = points[-(trend_window * 2):-trend_window]
            cur_avg = sum(as_float(p.get(key), 0.0) for p in cur_slice) / max(len(cur_slice), 1)
            prev_avg = sum(as_float(p.get(key), 0.0) for p in prev_slice) / max(len(prev_slice), 1)
            return (round(cur_avg, 2), round(prev_avg, 2))

        orders_cur3, orders_prev3 = _trend_cur_prev(daily_points, "ordered_units")
        revenue_cur3, revenue_prev3 = _trend_cur_prev(daily_points, "revenue")
        impressions_cur3, impressions_prev3 = _trend_cur_prev(daily_points, "hits_view")
        ctr_cur3, ctr_prev3 = _trend_cur_prev(daily_points, "ctr")
        conversion_cur3, conversion_prev3 = _trend_cur_prev(daily_points, "conversion")

        ad_spend_30d = sum(as_float(v.get("ad_spend"), 0.0) for v in ad_by_day.values())
        ad_revenue_30d = sum(as_float(v.get("ad_revenue"), 0.0) for v in ad_by_day.values())
        drr = (safe_divide(ad_spend_30d, revenue_30d) or 0.0) * 100.0
        ad_share = (safe_divide(ad_revenue_30d, revenue_30d) or 0.0) * 100.0

        seller_price = as_float(product_info.get("price_base"), 0.0)
        client_price = as_float(product_info.get("price_current"), 0.0)
        discount_pct = (1.0 - (client_price / seller_price)) * 100.0 if seller_price > 0 else 0.0

        stock_fbo_total = as_float(stock_info.get("stock_fbo"), 0.0)
        stock_fbs_total = as_float(stock_info.get("stock_fbs"), 0.0)
        stock_now = stock_fbo_total + stock_fbs_total
        stock_sellable = as_float(stock_info.get("stock_fbo_available"), 0.0) + stock_fbs_total
        avg_daily_sales = orders_30d / max(days_total, 1)
        days_to_oos = safe_divide(stock_sellable, avg_daily_sales) if avg_daily_sales > 0 else None
        oos_days_30d = 0 if avg_daily_sales <= 0 else max(0, days_total - int(stock_now / max(avg_daily_sales, 1e-9)))

        alerts: List[str] = []
        score = 100.0
        if ctr_delta is not None and ctr_delta <= -20:
            alerts.append("CTR в†“ > 20%")
            score -= 20
        if conversion_delta is not None and conversion_delta <= -20:
            alerts.append("РљРѕРЅРІРµСЂСЃРёСЏ в†“ > 20%")
            score -= 25
        if drr > 35:
            alerts.append("Р”Р Р  РІС‹С€Рµ РјР°СЂР¶РёРЅР°Р»СЊРЅРѕРіРѕ СѓСЂРѕРІРЅСЏ")
            score -= 25
        if days_to_oos is not None and days_to_oos < 5:
            alerts.append("РћСЃС‚Р°С‚РѕРє < 5 РґРЅРµР№")
            score -= 25
        if discount_pct > 25:
            alerts.append("Р’С‹СЃРѕРєР°СЏ СЃРєРёРґРєР° РјР°СЂРєРµС‚РїР»РµР№СЃР°")
            score -= 10
        if position_delta is not None and position_delta > 20:
            alerts.append("РџРѕР·РёС†РёРё СѓС…СѓРґС€РёР»РёСЃСЊ")
            score -= 10
        if revenue_delta is not None and revenue_delta > 10:
            score += 5
        score = max(0.0, min(100.0, score))
        kpi_status = "green" if score >= 70 else "yellow" if score >= 45 else "red"

        items.append(
            {
                "sku": sku,
                "offer_id": product_info.get("offer_id") or offer_norm or f"sku_{sku}",
                "name": product_info.get("name") or "",
                "category": "",
                "impressions_30d": impressions_30d,
                "clicks_30d": clicks_30d,
                "ctr_30d": ctr_30d,
                "ctr_delta_7v7": ctr_delta,
                "ctr_trend_3v3": ctr_trend_3v3,
                "orders_30d": orders_30d,
                "conversion_30d": conversion_30d,
                "conversion_delta_7v7": conversion_delta,
                "conversion_trend_3v3": conversion_trend_3v3,
                "revenue_30d": revenue_30d,
                "avg_check_30d": avg_check_30d,
                "revenue_delta_7v7": revenue_delta,
                "revenue_trend_3v3": revenue_trend_3v3,
                "impressions_trend_3v3": impressions_trend_3v3,
                "orders_trend_3v3": orders_trend_3v3,
                "orders_cur3": orders_cur3,
                "orders_prev3": orders_prev3,
                "revenue_cur3": revenue_cur3,
                "revenue_prev3": revenue_prev3,
                "impressions_cur3": impressions_cur3,
                "impressions_prev3": impressions_prev3,
                "ctr_cur3": ctr_cur3,
                "ctr_prev3": ctr_prev3,
                "conversion_cur3": conversion_cur3,
                "conversion_prev3": conversion_prev3,
                "seller_price": seller_price,
                "client_price": client_price,
                "marketplace_discount_pct": discount_pct,
                "price_delta_30d": None,
                "ad_spend_30d": ad_spend_30d,
                "drr": drr,
                "ad_revenue_30d": ad_revenue_30d,
                "ad_share": ad_share,
                "stock_now": stock_now,
                "stock_fbo": as_float(stock_info.get("stock_fbo"), 0.0),
                "stock_fbo_available": as_float(stock_info.get("stock_fbo_available"), 0.0),
                "stock_fbo_supply": as_float(stock_info.get("stock_fbo_supply"), 0.0),
                "stock_fbo_transit": as_float(stock_info.get("stock_fbo_transit"), 0.0),
                "stock_fbo_acceptance": as_float(stock_info.get("stock_fbo_acceptance"), 0.0),
                "stock_fbs": as_float(stock_info.get("stock_fbs"), 0.0),
                "clusters": cluster_stock_map.get(sku, []),
                "days_to_oos": days_to_oos,
                "oos_days_30d": oos_days_30d,
                "rating": review_info.get("rating", 0.0),
                "reviews_count": int(review_info.get("reviews_total", 0) or 0),
                "reviews_delta_30d": int(review_info.get("reviews_delta_30d", 0) or 0),
                "returns_30d": returns_30d,
                "cancellations_30d": cancellations_30d,
                "delivered_units_30d": delivered_units_30d,
                "position_category_30d": _avg_metric(day_map, "position_category"),
                "trend_window_days": trend_window,
                "kpi_score": score,
                "kpi_status": kpi_status,
                "alerts": alerts,
                "traffic_sources": {
                    "search_views_30d": _sum_metric(day_map, "hits_view_search"),
                    "pdp_views_30d": _sum_metric(day_map, "hits_view_pdp"),
                    "pdp_visitors_30d": _sum_metric(day_map, "session_view_pdp"),
                    "pdp_ad_clicks_30d": ad_clicks_30d,
                    "pdp_seo_visitors_30d": max(0.0, _sum_metric(day_map, "session_view_pdp") - ad_clicks_30d),
                    "seo_views_30d": seo_impressions_30d,
                    "ad_views_30d": ad_impressions_30d,
                    "search_tocart_30d": _sum_metric(day_map, "hits_tocart_search"),
                    "pdp_tocart_30d": _sum_metric(day_map, "hits_tocart_pdp"),
                    "session_search_30d": _sum_metric(day_map, "session_view_search"),
                    "session_pdp_30d": _sum_metric(day_map, "session_view_pdp"),
                },
                "promos": promo_map.get(sku, []),
                "daily": daily_points,
            }
        )

    items.sort(key=lambda r: (-as_float(r.get("revenue_30d"), 0.0), str(r.get("offer_id") or ""), int(r.get("sku") or 0)))
    items = items[:limit]
    summary = {
        "period_days": days_total,
        "date_from": start_day.isoformat(),
        "date_to": end_day.isoformat(),
        "items_total": len(items),
        "revenue_total": float(sum(as_float(i.get("revenue_30d"), 0.0) for i in items)),
        "ad_spend_total": float(sum(as_float(i.get("ad_spend_30d"), 0.0) for i in items)),
        "red_count": sum(1 for i in items if i.get("kpi_status") == "red"),
        "yellow_count": sum(1 for i in items if i.get("kpi_status") == "yellow"),
        "green_count": sum(1 for i in items if i.get("kpi_status") == "green"),
    }
    return web.json_response(clean_nan_values({"count": len(items), "items": items, "summary": summary}))


async def get_article_characteristics(request: web.Request) -> web.Response:
    limit_raw = (request.query.get("limit") or "2000").strip()
    kgt_raw = (request.query.get("is_kgt") or "").strip().lower()
    try:
        limit = max(1, min(10000, int(limit_raw)))
    except Exception:
        limit = 2000

    conditions: List[str] = []
    params: List[Any] = []
    idx = 1
    if kgt_raw in {"1", "true", "yes"}:
        conditions.append(f"is_kgt = ${idx}")
        params.append(True)
        idx += 1
    elif kgt_raw in {"0", "false", "no"}:
        conditions.append(f"is_kgt = ${idx}")
        params.append(False)
        idx += 1

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        SELECT
            sku,
            offer_id,
            article_name,
            is_kgt,
            volume_weight,
            height_mm,
            width_mm,
            depth_mm,
            weight_g,
            shipment_type,
            updated_at
        FROM article_characteristics
        {where_sql}
        ORDER BY updated_at DESC NULLS LAST, sku
        LIMIT {limit}
    """
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    items = [
        {
            "sku": row["sku"],
            "offer_id": row["offer_id"],
            "article_name": row["article_name"],
            "is_kgt": row["is_kgt"],
            "volume_weight": as_float(row["volume_weight"]) if row["volume_weight"] is not None else None,
            "height_mm": row["height_mm"],
            "width_mm": row["width_mm"],
            "depth_mm": row["depth_mm"],
            "weight_g": row["weight_g"],
            "shipment_type": row["shipment_type"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]
    return web.json_response({"count": len(items), "items": items})


async def refresh_article_characteristics(request: web.Request) -> web.Response:
    body = await request.json() if request.body_exists else {}
    limit_raw = str(body.get("limit") or "0").strip()
    force_skus = body.get("skus") if isinstance(body.get("skus"), list) else []

    client_id = (
        os.getenv("OZON_CLIENT_ID")
        or getattr(settings, "ozon_client_id", "")
        or _get_env_from_dotenv("OZON_CLIENT_ID")
        or ""
    ).strip()
    api_key = (
        os.getenv("OZON_API_KEY")
        or _get_env_from_dotenv("OZON_API_KEY")
        or getattr(settings, "ozon_api_key", "")
        or ""
    ).strip()
    if not client_id or not api_key:
        return web.json_response({"error": "Missing OZON_CLIENT_ID/OZON_API_KEY"}, status=400)

    try:
        limit = max(0, int(limit_raw))
    except Exception:
        limit = 0

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        if force_skus:
            sku_rows = []
            for s in force_skus:
                sku_val = _to_int(s)
                if sku_val and sku_val > 0:
                    sku_rows.append((sku_val, None, None))
        else:
            sql = """
                SELECT
                    sku,
                    max(offer_id) AS offer_id,
                    max(article_name) AS article_name
                FROM (
                    SELECT sku, offer_id, name AS article_name
                    FROM analytics_stocks
                    WHERE sku IS NOT NULL
                    UNION ALL
                    SELECT fbo_sku_id AS sku, offer_id, product_name AS article_name
                    FROM report_products_items
                    WHERE fbo_sku_id IS NOT NULL
                    UNION ALL
                    SELECT fbs_sku_id AS sku, offer_id, product_name AS article_name
                    FROM report_products_items
                    WHERE fbs_sku_id IS NOT NULL
                ) t
                GROUP BY sku
                ORDER BY sku
            """
            if limit > 0:
                sql += f" LIMIT {limit}"
            rows = await conn.fetch(sql)
            sku_rows = []
            for r in rows:
                sku_val = _to_int(r["sku"])
                if sku_val and sku_val > 0:
                    sku_rows.append((sku_val, r["offer_id"], r["article_name"]))

    if not sku_rows:
        return web.json_response({"updated": 0, "requested": 0, "note": "No SKU source rows"})

    sku_meta: Dict[int, Dict[str, Any]] = {
        sku: {"offer_id": offer_id, "article_name": article_name}
        for sku, offer_id, article_name in sku_rows
    }
    skus = sorted(sku_meta.keys())

    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=90)
    v3_map: Dict[int, Dict[str, Any]] = {}
    v4_map: Dict[int, Dict[str, Any]] = {}
    errors: List[Dict[str, Any]] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        chunk_size = 100
        for i in range(0, len(skus), chunk_size):
            chunk = skus[i : i + chunk_size]
            st3, d3 = await _ozon_post_json(
                session,
                "/v3/product/info/list",
                headers,
                {"sku": chunk},
                retries=6,
                delay_seconds=4.0,
            )
            if st3 == 200:
                for item in d3.get("items", []) or []:
                    sku = _to_int(item.get("sku"))
                    if sku:
                        v3_map[sku] = item
            else:
                errors.append({"endpoint": "/v3/product/info/list", "status": st3, "details": d3, "chunk_size": len(chunk)})

            st4, d4 = await _ozon_post_json(
                session,
                "/v4/product/info/attributes",
                headers,
                {"filter": {"sku": chunk}, "limit": 1000, "last_id": ""},
                retries=6,
                delay_seconds=4.0,
            )
            if st4 == 200:
                for item in d4.get("result", []) or []:
                    sku = _to_int(item.get("sku"))
                    if sku:
                        v4_map[sku] = item
            else:
                errors.append({"endpoint": "/v4/product/info/attributes", "status": st4, "details": d4, "chunk_size": len(chunk)})

    updated = 0
    async with pool.acquire() as conn:
        for sku in skus:
            v3_item = v3_map.get(sku) or {}
            v4_item = v4_map.get(sku) or {}
            source = sku_meta.get(sku) or {}
            offer_id = v3_item.get("offer_id") or v4_item.get("offer_id") or source.get("offer_id")
            article_name = v3_item.get("name") or v4_item.get("name") or source.get("article_name")
            shipment_type = None
            sources = v3_item.get("sources") or []
            if isinstance(sources, list) and sources:
                shipment_type = (sources[0] or {}).get("shipment_type")

            await conn.execute(
                """
                INSERT INTO article_characteristics (
                    sku,
                    offer_id,
                    article_name,
                    is_kgt,
                    volume_weight,
                    height_mm,
                    width_mm,
                    depth_mm,
                    weight_g,
                    shipment_type,
                    raw_v3,
                    raw_v4,
                    updated_at
                )
                VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,now()
                )
                ON CONFLICT (sku)
                DO UPDATE SET
                    offer_id = EXCLUDED.offer_id,
                    article_name = EXCLUDED.article_name,
                    is_kgt = EXCLUDED.is_kgt,
                    volume_weight = EXCLUDED.volume_weight,
                    height_mm = EXCLUDED.height_mm,
                    width_mm = EXCLUDED.width_mm,
                    depth_mm = EXCLUDED.depth_mm,
                    weight_g = EXCLUDED.weight_g,
                    shipment_type = EXCLUDED.shipment_type,
                    raw_v3 = EXCLUDED.raw_v3,
                    raw_v4 = EXCLUDED.raw_v4,
                    updated_at = now()
                """,
                sku,
                offer_id,
                article_name,
                v3_item.get("is_kgt"),
                as_float(v3_item.get("volume_weight")) if v3_item.get("volume_weight") is not None else None,
                _to_int(v4_item.get("height")),
                _to_int(v4_item.get("width")),
                _to_int(v4_item.get("depth")),
                _to_int(v4_item.get("weight")),
                shipment_type,
                json.dumps(v3_item, ensure_ascii=False),
                json.dumps(v4_item, ensure_ascii=False),
            )
            updated += 1

    return web.json_response(
        {
            "updated": updated,
            "requested": len(skus),
            "v3_found": len(v3_map),
            "v4_found": len(v4_map),
            "errors": errors,
        }
    )


