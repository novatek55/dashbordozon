"""Dashboard routes/returns.py handlers."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import asyncpg
from aiohttp import web

from src.dashboard.constants import MSK
from src.dashboard.helpers import parse_date_utc


async def get_returns(request: web.Request) -> web.Response:
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

    params: List[Any] = []
    conditions: List[str] = []
    idx = 1

    if schema in {"FBO", "FBS"}:
        conditions.append(f"return_schema = ${idx}")
        params.append(schema)
        idx += 1

    if date_from is not None:
        conditions.append(f"returned_at >= ${idx}")
        params.append(date_from)
        idx += 1

    if date_to_exclusive is not None:
        conditions.append(f"returned_at < ${idx}")
        params.append(date_to_exclusive)
        idx += 1

    if offer_id:
        conditions.append(f"offer_id = ${idx}")
        params.append(offer_id)
        idx += 1

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        WITH returns_all AS (
            SELECT
                r.return_id,
                r.posting_number,
                COALESCE(NULLIF(upper(r.raw_data->>'schema'), ''), NULLIF(upper(r.raw_data->>'return_schema'), ''), 'UNKNOWN') AS return_schema,
                r.status,
                r.returned_at,
                r.offer_id,
                r.product_name,
                r.quantity,
                r.return_reason,
                r.refund_amount,
                r.last_synced_at
            FROM returns r
            UNION ALL
            SELECT
                rf.return_id,
                rf.posting_number,
                'FBO' AS return_schema,
                rf.status,
                rf.returned_at,
                rf.offer_id,
                rf.product_name,
                rf.quantity,
                rf.return_reason,
                rf.refund_amount,
                rf.last_synced_at
            FROM returns_fbo rf
            WHERE NOT EXISTS (
                SELECT 1 FROM returns r WHERE r.return_id = rf.return_id
            )
        )
        SELECT
            return_id,
            posting_number,
            return_schema,
            status,
            returned_at,
            offer_id,
            product_name,
            quantity,
            return_reason,
            refund_amount,
            last_synced_at
        FROM returns_all
        {where_sql}
        ORDER BY returned_at DESC NULLS LAST
        LIMIT {limit}
    """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "return_id": r["return_id"],
                "posting_number": r["posting_number"],
                "return_schema": r["return_schema"],
                "status": r["status"],
                "returned_at": r["returned_at"].isoformat() if r["returned_at"] else None,
                "offer_id": r["offer_id"],
                "product_name": r["product_name"],
                "quantity": r["quantity"],
                "return_reason": r["return_reason"],
                "refund_amount": float(r["refund_amount"]) if r["refund_amount"] is not None else None,
                "last_synced_at": r["last_synced_at"].isoformat() if r["last_synced_at"] else None,
            }
        )

    return web.json_response({"count": len(items), "items": items})

