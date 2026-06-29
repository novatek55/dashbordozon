"""Sync Wildberries stock balances into Postgres."""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from src.database import db_manager
from src.wb_stocks_client import WBStocksClient

logger = logging.getLogger(__name__)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text_value = str(value).strip().lower()
    if text_value in {"true", "1", "yes", "да"}:
        return True
    if text_value in {"false", "0", "no", "нет"}:
        return False
    return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        normalized = text_value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "nm_id": _as_int(row.get("nmId") or row.get("nm_id")),
        "supplier_article": str(row.get("supplierArticle") or row.get("supplier_article") or "").strip(),
        "barcode": str(row.get("barcode") or "").strip(),
        "warehouse_name": str(row.get("warehouseName") or row.get("warehouse_name") or "").strip(),
        "category": row.get("category"),
        "subject": row.get("subject"),
        "brand": row.get("brand"),
        "tech_size": row.get("techSize") or row.get("tech_size"),
        "quantity": _as_int(row.get("quantity")),
        "in_way_to_client": _as_int(row.get("inWayToClient") or row.get("in_way_to_client")),
        "in_way_from_client": _as_int(row.get("inWayFromClient") or row.get("in_way_from_client")),
        "quantity_full": _as_int(row.get("quantityFull") or row.get("quantity_full") or row.get("quantity")),
        "price": _as_float(row.get("Price") or row.get("price")),
        "discount": _as_float(row.get("Discount") or row.get("discount")),
        "is_supply": _as_bool(row.get("isSupply") or row.get("is_supply")),
        "is_realization": _as_bool(row.get("isRealization") or row.get("is_realization")),
        "sc_code": row.get("SCCode") or row.get("sc_code"),
        "last_change_date": _parse_dt(row.get("lastChangeDate") or row.get("last_change_date")),
        "raw_data": json.dumps(row, ensure_ascii=False),
    }


async def sync_wb_stocks(api_key: str, date_from: str = "2019-01-01") -> Dict[str, Any]:
    """Fetch current WB stock balances and replace local snapshot."""
    async with WBStocksClient(api_key) as client:
        rows = await client.get_supplier_stocks(date_from=date_from)

    normalized = [
        item for item in (_normalize_row(row) for row in rows if isinstance(row, dict))
        if item["nm_id"] or item["supplier_article"] or item["barcode"]
    ]

    async with db_manager.session() as session:
        await session.execute(text("DELETE FROM wb_stocks"))
        for item in normalized:
            await session.execute(
                text(
                    """
                    INSERT INTO wb_stocks (
                        nm_id, supplier_article, barcode, warehouse_name,
                        category, subject, brand, tech_size,
                        quantity, in_way_to_client, in_way_from_client, quantity_full,
                        price, discount, is_supply, is_realization, sc_code,
                        last_change_date, raw_data, last_synced_at
                    )
                    VALUES (
                        :nm_id, :supplier_article, :barcode, :warehouse_name,
                        :category, :subject, :brand, :tech_size,
                        :quantity, :in_way_to_client, :in_way_from_client, :quantity_full,
                        :price, :discount, :is_supply, :is_realization, :sc_code,
                        :last_change_date, CAST(:raw_data AS JSONB), NOW()
                    )
                    ON CONFLICT (nm_id, supplier_article, barcode, warehouse_name) DO UPDATE
                    SET
                        category = EXCLUDED.category,
                        subject = EXCLUDED.subject,
                        brand = EXCLUDED.brand,
                        tech_size = EXCLUDED.tech_size,
                        quantity = EXCLUDED.quantity,
                        in_way_to_client = EXCLUDED.in_way_to_client,
                        in_way_from_client = EXCLUDED.in_way_from_client,
                        quantity_full = EXCLUDED.quantity_full,
                        price = EXCLUDED.price,
                        discount = EXCLUDED.discount,
                        is_supply = EXCLUDED.is_supply,
                        is_realization = EXCLUDED.is_realization,
                        sc_code = EXCLUDED.sc_code,
                        last_change_date = EXCLUDED.last_change_date,
                        raw_data = EXCLUDED.raw_data,
                        last_synced_at = NOW()
                    """
                ),
                item,
            )
    total_quantity = sum(int(item.get("quantity") or 0) for item in normalized)
    total_quantity_full = sum(int(item.get("quantity_full") or 0) for item in normalized)
    logger.info("WB stocks synced: rows=%s quantity=%s quantity_full=%s", len(normalized), total_quantity, total_quantity_full)
    return {
        "rows": len(normalized),
        "quantity": total_quantity,
        "quantity_full": total_quantity_full,
        "date_from": date_from,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
