"""Price report route handlers."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import asyncpg
from aiohttp import web


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _first_present(data: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _market_card_from_price_index(price_indexes: Any, key: str) -> Dict[str, Any]:
    if isinstance(price_indexes, str):
        try:
            price_indexes = json.loads(price_indexes)
        except json.JSONDecodeError:
            return {"status": "missing", "index": None, "price": None, "source": "", "link": ""}
    if not isinstance(price_indexes, dict):
        return {"status": "missing", "index": None, "price": None, "source": "", "link": ""}
    block = price_indexes.get(key) or {}
    if not isinstance(block, dict):
        return {"status": "missing", "index": None, "price": None, "source": "", "link": ""}
    price = _to_float(_first_present(block, ("minimal_price", "min_price", "price")))
    index = _to_float(_first_present(block, ("price_index_value", "index", "value")))
    if price is not None and price <= 0:
        price = None
    if index is not None and index <= 0:
        index = None
    link = str(_first_present(block, ("minimal_price_link", "link", "url", "product_url")) or "")
    source = str(_first_present(block, ("source", "marketplace", "marketplace_name", "domain")) or "")
    if not source and link:
        source = _source_from_url(link)
    return {
        "status": "ok" if price is not None or index is not None else "missing",
        "index": index,
        "price": price,
        "source": source,
        "link": link,
    }


def build_price_report_item(row: Any) -> Dict[str, Any]:
    current_price = _to_float(row["price_current"])
    customer_price = _to_float(_row_get(row, "customer_price"))
    recommended_price = _to_float(row["price_recommended"])
    recommended_price_link = row["recommended_price_link"] or ""
    customer_price_status = _row_get(row, "price_details_status") or ("ok" if customer_price is not None else "missing")
    price_details_synced_at = _row_get(row, "price_details_synced_at")
    price_indexes = _row_get(row, "price_indexes")
    is_beneficial: Optional[bool] = None
    if current_price is not None and recommended_price is not None and recommended_price > 0:
        is_beneficial = current_price <= recommended_price
    price_index = None
    if current_price is not None and recommended_price is not None and recommended_price > 0:
        price_index = round(current_price / recommended_price, 2)
    fallback_other_marketplace = {
        "status": "ok" if recommended_price is not None else "missing",
        "index": price_index,
        "price": recommended_price,
        "source": _source_from_url(recommended_price_link) if recommended_price_link else "",
        "link": recommended_price_link,
    }
    ozon_competitor_prices = _market_card_from_price_index(price_indexes, "ozon_index_data")
    own_other_marketplace_prices = _market_card_from_price_index(price_indexes, "self_marketplaces_index_data")
    other_marketplace_competitor_prices = _market_card_from_price_index(price_indexes, "external_index_data")
    if own_other_marketplace_prices["status"] == "missing":
        own_other_marketplace_prices = fallback_other_marketplace
    if other_marketplace_competitor_prices["status"] == "missing":
        other_marketplace_competitor_prices = fallback_other_marketplace.copy()

    return {
        "offer_id": row["offer_id"],
        "product_name": row["product_name"],
        "ozon_product_id": row["ozon_product_id"],
        "fbo_sku_id": row["fbo_sku_id"],
        "fbs_sku_id": row["fbs_sku_id"],
        "price_current": current_price,
        "customer_price": customer_price,
        "customer_price_status": customer_price_status,
        "price_base": _to_float(row["price_base"]),
        "price_recommended": recommended_price,
        "recommended_price_link": recommended_price_link,
        "price_index": price_index,
        "price_index_status": "beneficial" if is_beneficial is True else "not_beneficial" if is_beneficial is False else "no_index",
        "ozon_competitor_prices": ozon_competitor_prices,
        "own_other_marketplace_prices": own_other_marketplace_prices,
        "other_marketplace_competitor_prices": other_marketplace_competitor_prices,
        "is_beneficial_price": is_beneficial,
        "beneficial_price_status": "Да" if is_beneficial is True else "Нет" if is_beneficial is False else "",
        "price_details_synced_at": price_details_synced_at.isoformat() if price_details_synced_at else None,
        "last_synced_at": row["last_synced_at"].isoformat() if row["last_synced_at"] else None,
    }


async def get_price_report(request: web.Request) -> web.Response:
    """Return latest Ozon product-price report rows from report_products_items."""
    limit_raw = (request.query.get("limit") or "500").strip()
    try:
        limit = max(1, min(2000, int(limit_raw)))
    except ValueError:
        limit = 500
    offer_id = (request.query.get("offer_id") or "").strip()

    latest_report_sql = """
        SELECT report_id
        FROM report_products_items
        ORDER BY last_synced_at DESC NULLS LAST, report_id DESC
        LIMIT 1
    """
    where_parts = [f"rpi.report_id = ({latest_report_sql})"]
    params: list[Any] = []
    if offer_id:
        params.append(f"%{offer_id}%")
        where_parts.append(f"rpi.offer_id ILIKE ${len(params)}")
    params.append(limit)
    limit_placeholder = f"${len(params)}"

    sql = f"""
        SELECT
            rpi.offer_id,
            rpi.product_name,
            rpi.ozon_product_id,
            rpi.fbo_sku_id,
            rpi.fbs_sku_id,
            rpi.price_current,
            rpi.price_base,
            ppd.customer_price,
            ppd.price_indexes,
            ppd.details_status AS price_details_status,
            ppd.last_synced_at AS price_details_synced_at,
            rpi.price_recommended,
            rpi.recommended_price_link,
            rpi.last_synced_at
        FROM report_products_items rpi
        LEFT JOIN LATERAL (
            SELECT customer_price, price_indexes, details_status, last_synced_at
            FROM product_price_details ppd
            WHERE ppd.sku IN (rpi.fbo_sku_id, rpi.fbs_sku_id)
               OR ppd.offer_id = rpi.offer_id
            ORDER BY ppd.last_synced_at DESC NULLS LAST
            LIMIT 1
        ) ppd ON TRUE
        WHERE {" AND ".join(where_parts)}
        ORDER BY
            CASE WHEN rpi.price_recommended IS NOT NULL AND rpi.recommended_price_link IS NOT NULL THEN 0 ELSE 1 END,
            rpi.offer_id NULLS LAST,
            rpi.line_no
        LIMIT {limit_placeholder}
    """

    try:
        pool: asyncpg.Pool = request.app["pool"]
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            source = await conn.fetchrow(
                """
                SELECT
                    report_id,
                    max(last_synced_at) AS last_synced_at,
                    count(*)::int AS total_rows
                FROM report_products_items
                WHERE report_id = (
                    SELECT report_id
                    FROM report_products_items
                    ORDER BY last_synced_at DESC NULLS LAST, report_id DESC
                    LIMIT 1
                )
                GROUP BY report_id
                """
            )
    except asyncpg.UndefinedTableError:
        return web.json_response(
            {
                "items": [],
                "count": 0,
                "source": {"type": "ozon_report_products", "status": "missing_table"},
            }
        )

    items = [build_price_report_item(row) for row in rows]
    return web.json_response(
        {
            "items": items,
            "count": len(items),
            "source": {
                "type": "ozon_report_products",
                "table": "report_products_items",
                "report_id": source["report_id"] if source else None,
                "last_synced_at": source["last_synced_at"].isoformat() if source and source["last_synced_at"] else None,
                "total_rows": int(source["total_rows"] or 0) if source else 0,
            },
        }
    )
