"""Dashboard routes/orders.py handlers."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import asyncpg
from aiohttp import web

from src.dashboard.constants import MSK, DELIVERED_STATUSES
from src.dashboard.helpers import normalize_offer_id, build_where, parse_date_utc


async def get_orders(request: web.Request) -> web.Response:
    schema = (request.query.get("schema") or "").strip().upper()
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

    where_sql, params = build_where(schema, date_from, date_to_exclusive, offer_id)
    sql = f"""
        SELECT
            order_id,
            posting_number,
            delivery_schema,
            status,
            created_at,
            shipment_date,
            delivered_at,
            items_total,
            discount_total,
            delivery_cost
        FROM fact_orders
        {where_sql}
        ORDER BY created_at DESC NULLS LAST
        LIMIT {limit}
    """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    result: List[Dict[str, Any]] = []
    for r in rows:
        result.append(
            {
                "order_id": r["order_id"],
                "posting_number": r["posting_number"],
                "delivery_schema": r["delivery_schema"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "shipment_date": r["shipment_date"].isoformat() if r["shipment_date"] else None,
                "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None,
                "items_total": float(r["items_total"]) if r["items_total"] is not None else None,
                "discount_total": float(r["discount_total"]) if r["discount_total"] is not None else None,
                "delivery_cost": float(r["delivery_cost"]) if r["delivery_cost"] is not None else None,
            }
        )

    return web.json_response({"count": len(result), "items": result})


async def get_sales(request: web.Request) -> web.Response:
    # Backward-compatible alias around orders/fact_sales view.
    return await get_orders(request)


async def get_articles(request: web.Request) -> web.Response:
    query = (request.query.get("query") or "").strip()
    source = (request.query.get("source") or "all").strip().lower()
    limit_raw = (request.query.get("limit") or "500").strip()

    try:
        limit = max(1, min(5000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    params: List[Any] = []
    idx = 1
    where_sales = ""
    where_returns = ""
    where_warehouse_stock = ""
    if query:
        where_sales = f"WHERE lower(coalesce(e->>'offer_id', '')) LIKE lower(${idx})"
        where_returns = f"WHERE lower(coalesce(offer_id, '')) LIKE lower(${idx})"
        where_warehouse_stock = f"WHERE lower(coalesce(offer_id, '')) LIKE lower(${idx})"
        params.append(f"%{query}%")
        idx += 1

    # Нормализация offer_id: убираем ведущие апострофы и пробелы
    _norm = "trim(both '''' from trim(coalesce({src}, '')))"

    if source == "current_products":
        sql = f"""
            SELECT DISTINCT {_norm.format(src='offer_id')} AS offer_id
            FROM products
            WHERE coalesce(trim(offer_id), '') <> ''
              AND coalesce(is_visible, true) IS TRUE
              AND lower(coalesce(status, '')) NOT IN ('archived', 'autoarchived', 'deleted')
              AND lower(coalesce(raw_data::jsonb->>'archived', 'false')) <> 'true'
              AND lower(coalesce(raw_data::jsonb->>'is_archived', 'false')) <> 'true'
              AND lower(coalesce(raw_data::jsonb->>'is_autoarchived', 'false')) <> 'true'
              {f"AND lower(coalesce(offer_id, '')) LIKE lower(${idx})" if query else ""}
            ORDER BY offer_id
            LIMIT {limit}
        """
    elif source == "sales":
        sql = f"""
            SELECT DISTINCT {_norm.format(src="e->>'offer_id'")} AS offer_id
            FROM fact_orders, jsonb_array_elements(items::jsonb) AS e
            {where_sales}
            ORDER BY offer_id
            LIMIT {limit}
        """
    elif source == "returns":
        sql = f"""
            SELECT DISTINCT offer_id
            FROM (
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM returns
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM returns_fbo
            ) u
            {where_returns}
            ORDER BY offer_id
            LIMIT {limit}
        """
    elif source == "warehouse_stock":
        sql = f"""
            SELECT DISTINCT offer_id
            FROM (
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM report_warehouse_stock_items
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM analytics_stocks
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM fbs_warehouse_stocks
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM realization_reports
            ) u
            {where_warehouse_stock}
            ORDER BY offer_id
            LIMIT {limit}
        """
    else:
        sql = f"""
            SELECT DISTINCT offer_id
            FROM (
                SELECT {_norm.format(src="e->>'offer_id'")} AS offer_id
                FROM fact_orders, jsonb_array_elements(items::jsonb) AS e
                {where_sales}
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM returns
                {where_returns if query else ""}
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM returns_fbo
                {where_returns if query else ""}
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM report_warehouse_stock_items
                {where_warehouse_stock if query else ""}
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM analytics_stocks
                {where_warehouse_stock if query else ""}
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM fbs_warehouse_stocks
                {where_warehouse_stock if query else ""}
                UNION
                SELECT {_norm.format(src='offer_id')} AS offer_id FROM realization_reports
                {where_returns if query else ""}
            ) u
            ORDER BY offer_id
            LIMIT {limit}
        """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    items = [r["offer_id"] for r in rows if r["offer_id"] and r["offer_id"].strip()]
    return web.json_response({"count": len(items), "items": items})

