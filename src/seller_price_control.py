"""Capture and parse Ozon Seller price-control customer prices.

This module works with an already authenticated seller.ozon.ru browser tab
through the local CDP relay. It intentionally avoids HTML scraping: the first
stage captures JSON network responses, then extracts records with offer IDs and
explicit customer/buyer/final price fields.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert


CUSTOMER_PRICE_KEYS = {
    "customer_price",
    "customerPrice",
    "buyer_price",
    "buyerPrice",
    "client_price",
    "clientPrice",
    "final_price",
    "finalPrice",
    "price_for_buyer",
    "priceForBuyer",
    "price_for_customer",
    "priceForCustomer",
    "customerPriceWithDiscount",
}

OFFER_ID_KEYS = {"offer_id", "offerId", "article", "vendorCode"}


@dataclass(frozen=True)
class SellerCustomerPrice:
    offer_id: str
    customer_price: float
    source_url: str = ""
    source_key: str = ""
    raw_data: Optional[dict[str, Any]] = None


def parse_money_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, dict):
        if "units" in value:
            try:
                units = float(value.get("units") or 0)
                nanos = float(value.get("nanos") or 0) / 1_000_000_000
            except (TypeError, ValueError):
                return None
            parsed = units + nanos
            return parsed if parsed > 0 else None
        for key in ("value", "price", "amount", "amountValue", "rub", "text"):
            parsed = parse_money_value(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, str):
        cleaned = value.replace("\u00a0", " ").replace("₽", "")
        cleaned = re.sub(r"[^\d,.\-]", "", cleaned)
        if not cleaned:
            return None
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(" ", "").replace(",", "")
        else:
            cleaned = cleaned.replace(" ", "").replace(",", ".")
        try:
            parsed = float(cleaned)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _offer_id_from_dict(data: dict[str, Any]) -> str:
    for key in OFFER_ID_KEYS:
        value = data.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _customer_price_from_dict(data: dict[str, Any]) -> tuple[Optional[float], str]:
    for key in CUSTOMER_PRICE_KEYS:
        if key in data:
            parsed = parse_money_value(data.get(key))
            if parsed is not None:
                return parsed, key
    return None, ""


def _extract_price_control_products(payload: Any, source_url: str) -> list[SellerCustomerPrice]:
    if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
        return []

    records: list[SellerCustomerPrice] = []
    for product in payload["products"]:
        if not isinstance(product, dict):
            continue
        part_item = product.get("part_item") or {}
        if not isinstance(part_item, dict):
            continue
        offer_id = str(part_item.get("offer_id") or "").strip()
        if not offer_id:
            continue
        marketing_price = product.get("part_marketing_price") or {}
        if not isinstance(marketing_price, dict):
            continue
        customer_price = parse_money_value(marketing_price.get("oa_price"))
        if customer_price is None:
            continue
        records.append(
            SellerCustomerPrice(
                offer_id=offer_id,
                customer_price=customer_price,
                source_url=source_url,
                source_key="part_marketing_price.oa_price",
                raw_data=product,
            )
        )
    return records


def extract_customer_prices_from_payload(payload: Any, source_url: str = "") -> list[SellerCustomerPrice]:
    records: dict[str, SellerCustomerPrice] = {}
    for record in _extract_price_control_products(payload, source_url):
        records[record.offer_id] = record
    for data in _iter_dicts(payload):
        offer_id = _offer_id_from_dict(data)
        if not offer_id:
            continue
        customer_price, source_key = _customer_price_from_dict(data)
        if customer_price is None:
            continue
        records[offer_id] = SellerCustomerPrice(
            offer_id=offer_id,
            customer_price=customer_price,
            source_url=source_url,
            source_key=source_key,
            raw_data=data,
        )
    return list(records.values())


def _decode_response_body(event: dict[str, Any]) -> Any:
    body = event.get("responseBody")
    if not body:
        return None
    try:
        return json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return None


def extract_customer_prices_from_capture(capture: dict[str, Any]) -> list[SellerCustomerPrice]:
    records: dict[str, SellerCustomerPrice] = {}
    for event in capture.get("events", []):
        payload = _decode_response_body(event)
        if payload is None:
            continue
        for record in extract_customer_prices_from_payload(payload, str(event.get("url") or "")):
            records[record.offer_id] = record
    return list(records.values())


def find_known_price_contexts(capture: dict[str, Any], prices: set[int]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if not prices:
        return matches
    patterns = {price: re.compile(rf"(?<!\d){price}(?!\d)") for price in prices}
    for event in capture.get("events", []):
        body = str(event.get("responseBody") or "")
        hit_prices = [price for price, pattern in patterns.items() if pattern.search(body)]
        if not hit_prices:
            continue
        matches.append(
            {
                "url": event.get("url"),
                "status": event.get("status"),
                "mimeType": event.get("mimeType"),
                "matchedPrices": hit_prices,
                "bodySnippet": body[:5000],
            }
        )
    return matches


async def upsert_customer_prices(records: list[SellerCustomerPrice]) -> int:
    from src.database import db_manager
    from src.models import ProductPriceDetail

    if not records:
        return 0
    now = datetime.now()
    rows = [
        {
            "sku": None,
            "offer_id": record.offer_id,
            "customer_price": record.customer_price,
            "price": None,
            "price_indexes": None,
            "details_status": "ok",
            "error_message": None,
            "raw_data": {
                "source": "seller_price_control",
                "source_url": record.source_url,
                "source_key": record.source_key,
                "raw_data": record.raw_data,
            },
            "last_synced_at": now,
        }
        for record in records
    ]
    async with db_manager.session() as session:
        stmt = pg_insert(ProductPriceDetail).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["offer_id"],
            index_where=ProductPriceDetail.offer_id.isnot(None),
            set_={
                "customer_price": stmt.excluded.customer_price,
                "details_status": stmt.excluded.details_status,
                "error_message": stmt.excluded.error_message,
                "raw_data": stmt.excluded.raw_data,
                "last_synced_at": stmt.excluded.last_synced_at,
            },
        )
        await session.execute(stmt)
    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse captured Ozon Seller price-control JSON.")
    parser.add_argument("--capture", required=True, help="Path to cdp_network_capture JSON output")
    parser.add_argument("--known-price", action="append", type=int, default=[])
    parser.add_argument("--candidates-output", default="exports/price_control_candidates.json")
    parser.add_argument("--apply", action="store_true", help="Write extracted prices to database")
    return parser.parse_args()


async def _apply_records(records: list[SellerCustomerPrice]) -> int:
    from src.database import db_manager

    await db_manager.initialize()
    try:
        return await upsert_customer_prices(records)
    finally:
        await db_manager.close()


def main() -> None:
    args = parse_args()
    capture = json.loads(Path(args.capture).read_text(encoding="utf-8"))
    records = extract_customer_prices_from_capture(capture)
    contexts = find_known_price_contexts(capture, set(args.known_price))
    output = {
        "records": [record.__dict__ for record in records],
        "knownPriceContexts": contexts,
    }
    out_path = Path(args.candidates_output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Extracted records: {len(records)}")
    print(f"Known price contexts: {len(contexts)}")
    print(f"Saved: {out_path}")
    if args.apply:
        updated = asyncio.run(_apply_records(records))
        print(f"Upserted customer prices: {updated}")


if __name__ == "__main__":
    main()
