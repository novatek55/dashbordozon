"""Backfill missing fact_order_items from Ozon posting APIs."""

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config import settings
from src.database import close_database, db_manager, init_database
from src.models import FactOrder, FactOrderItem, ReportProductItem, Transaction
from src.ozon_client import OzonAPIError, OzonClient


logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )


def parse_day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def msk_day_start(value: str) -> datetime:
    return parse_day(value).replace(tzinfo=MSK).astimezone(timezone.utc)


def to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def to_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except Exception:
        return None


def to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


async def load_missing_postings(
    start_utc: datetime,
    end_utc: datetime,
    limit: Optional[int],
) -> List[str]:
    async with db_manager.session() as session:
        stmt = (
            select(Transaction.posting_number)
            .where(Transaction.operation_date >= start_utc)
            .where(Transaction.operation_date < end_utc)
            .where(Transaction.operation_type == "OperationAgentDeliveredToCustomer")
            .where(Transaction.posting_number.is_not(None))
            .where(Transaction.posting_number != "")
            .distinct()
            .order_by(Transaction.posting_number)
        )
        rows = (await session.execute(stmt)).scalars().all()
        postings = [
            posting
            for posting in rows
            if posting
        ]

        missing: List[str] = []
        for posting in postings:
            item_stmt = (
                select(FactOrderItem.id)
                .where(FactOrderItem.posting_number == posting)
                .limit(1)
            )
            existing = (await session.execute(item_stmt)).scalar_one_or_none()
            if existing is None:
                missing.append(posting)
            if limit is not None and len(missing) >= limit:
                break
        return missing


async def load_posting_schemas(
    posting_numbers: Sequence[str],
) -> Dict[str, str]:
    if not posting_numbers:
        return {}
    async with db_manager.session() as session:
        rows = (
            await session.execute(
                select(Transaction.posting_number, Transaction.raw_data)
                .where(Transaction.posting_number.in_(list(posting_numbers)))
                .where(Transaction.operation_type == "OperationAgentDeliveredToCustomer")
            )
        ).all()

    schema_map: Dict[str, str] = {}
    for posting_number, raw_data in rows:
        payload = raw_data if isinstance(raw_data, dict) else {}
        posting = payload.get("posting") if isinstance(payload.get("posting"), dict) else {}
        schema = str(posting.get("delivery_schema") or "").upper().strip()
        if posting_number and schema and posting_number not in schema_map:
            schema_map[posting_number] = schema
    return schema_map


def build_items(products: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        items.append(
            {
                "offer_id": product.get("offer_id"),
                "sku": to_int(product.get("sku")),
                "name": product.get("name"),
                "quantity": to_decimal(product.get("quantity")) or Decimal("1"),
                "price": to_decimal(product.get("price")),
                "buyer_paid": to_decimal(product.get("buyer_paid")) or to_decimal(product.get("price")),
            }
        )
    return items


async def upsert_fact_order(
    order_id: str,
    posting_number: str,
    schema: str,
    payload: Dict[str, Any],
    items: Sequence[Dict[str, Any]],
) -> None:
    fact_data = {
        "order_id": order_id,
        "posting_number": posting_number,
        "delivery_schema": schema,
        "status": payload.get("status"),
        "substatus": payload.get("substatus"),
        "created_at": to_datetime(payload.get("created_at")),
        "in_process_at": to_datetime(payload.get("in_process_at")),
        "shipment_date": to_datetime(payload.get("shipment_date")),
        "delivered_at": to_datetime(
            payload.get("delivered_at")
            or payload.get("fact_delivery_date")
            or payload.get("delivering_date")
        ),
        "cancelled_at": None,
        "items_total": to_decimal(payload.get("price")),
        "discount_total": to_decimal(payload.get("discount_amount")),
        "delivery_cost": to_decimal(payload.get("delivery_price")),
        "commission_total": None,
        "payout_total": None,
        "is_on_time": None,
        "sla_hours": None,
        "is_returned": None,
        "return_amount": None,
        "items": json_safe(list(items)),
        "customer_name": ((payload.get("customer") or {}).get("name") if isinstance(payload.get("customer"), dict) else None),
        "region": (
            ((payload.get("customer") or {}).get("address") or {}).get("region")
            if isinstance((payload.get("customer") or {}).get("address"), dict)
            else None
        ),
        "city": (
            ((payload.get("customer") or {}).get("address") or {}).get("city")
            if isinstance((payload.get("customer") or {}).get("address"), dict)
            else None
        ),
        "raw_data": json_safe(payload),
        "last_synced_at": datetime.now(timezone.utc),
    }

    async with db_manager.session() as session:
        await session.execute(delete(FactOrderItem).where(FactOrderItem.posting_number == posting_number))
        await session.execute(
            delete(FactOrder).where(
                FactOrder.posting_number == posting_number,
                FactOrder.order_id != order_id,
            )
        )
        stmt = pg_insert(FactOrder).values(**fact_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["order_id"],
            set_={k: v for k, v in fact_data.items() if k != "order_id"},
        )
        await session.execute(stmt)

        await session.execute(delete(FactOrderItem).where(FactOrderItem.order_id == order_id))
        for line_no, item in enumerate(items, start=1):
            row_data = {
                "order_id": order_id,
                "posting_number": posting_number,
                "line_no": line_no,
                "offer_id": item.get("offer_id"),
                "sku": item.get("sku"),
                "product_name": item.get("name"),
                "quantity": item.get("quantity"),
                "price": item.get("price"),
                "buyer_paid": item.get("buyer_paid"),
                "raw_data": json_safe(item),
                "last_synced_at": datetime.now(timezone.utc),
            }
            await session.execute(insert(FactOrderItem).values(**row_data))


async def choose_fact_order_id(
    posting_number: str,
    api_order_id: Optional[str],
) -> str:
    candidate = str(api_order_id).strip() if api_order_id else ""
    if not candidate:
        return posting_number

    async with db_manager.session() as session:
        existing = (
            await session.execute(
                select(FactOrder.posting_number)
                .where(FactOrder.order_id == candidate)
                .limit(1)
            )
        ).scalar_one_or_none()

    if existing and existing != posting_number:
        return posting_number
    return candidate


async def fetch_fbs_posting(client: OzonClient, posting_number: str) -> Optional[Dict[str, Any]]:
    try:
        response = await client.get_posting_details(posting_number)
    except OzonAPIError as exc:
        if exc.status_code in {404, 409}:
            return None
        raise
    result = response.get("result", {}) if isinstance(response, dict) else {}
    if not isinstance(result, dict) or not result.get("posting_number"):
        return None
    return result


async def fetch_fbo_postings_map(
    client: OzonClient,
    start_utc: datetime,
    end_utc: datetime,
) -> Dict[str, Dict[str, Any]]:
    postings_map: Dict[str, Dict[str, Any]] = {}
    async for postings in client.get_all_postings_fbo(start_utc, end_utc):
        for posting in postings:
            posting_number = posting.get("posting_number")
            if posting_number:
                postings_map[posting_number] = posting
    return postings_map


async def count_missing_sales(start_utc: datetime, end_utc: datetime) -> Dict[str, int]:
    async with db_manager.session() as session:
        postings = (
            await session.execute(
                select(Transaction.posting_number)
                .where(Transaction.operation_date >= start_utc)
                .where(Transaction.operation_date < end_utc)
                .where(Transaction.operation_type == "OperationAgentDeliveredToCustomer")
                .where(Transaction.posting_number.is_not(None))
                .where(Transaction.posting_number != "")
                .distinct()
            )
        ).scalars().all()
        postings = [posting for posting in postings if posting]

        missing = 0
        for posting in postings:
            existing = (
                await session.execute(
                    select(FactOrderItem.id)
                    .where(FactOrderItem.posting_number == posting)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is None:
                missing += 1

        return {"sales_postings": len(postings), "missing_items": missing}


async def load_sku_offer_map() -> Dict[int, str]:
    async with db_manager.session() as session:
        rows = (
            await session.execute(
                select(
                    ReportProductItem.offer_id,
                    ReportProductItem.fbo_sku_id,
                    ReportProductItem.fbs_sku_id,
                )
            )
        ).all()
    mapping: Dict[int, str] = {}
    for offer_id, fbo_sku_id, fbs_sku_id in rows:
        if offer_id and fbo_sku_id and int(fbo_sku_id) not in mapping:
            mapping[int(fbo_sku_id)] = str(offer_id)
        if offer_id and fbs_sku_id and int(fbs_sku_id) not in mapping:
            mapping[int(fbs_sku_id)] = str(offer_id)
    return mapping


async def load_missing_fbo_transactions(
    start_utc: datetime,
    end_utc: datetime,
) -> List[Dict[str, Any]]:
    async with db_manager.session() as session:
        postings = await load_missing_postings(start_utc, end_utc, None)
        if not postings:
            return []
        rows = (
            await session.execute(
                select(
                    Transaction.posting_number,
                    Transaction.operation_date,
                    Transaction.raw_data,
                )
                .where(Transaction.posting_number.in_(postings))
                .where(Transaction.operation_type == "OperationAgentDeliveredToCustomer")
            )
        ).all()
    result: List[Dict[str, Any]] = []
    for posting_number, operation_date, raw_data in rows:
        payload = raw_data if isinstance(raw_data, dict) else {}
        posting = payload.get("posting") if isinstance(payload.get("posting"), dict) else {}
        if posting.get("delivery_schema") != "FBO":
            continue
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        if not items:
            continue
        result.append(
            {
                "posting_number": posting_number,
                "operation_date": operation_date,
                "raw_data": payload,
            }
        )
    return result


async def async_main(args: argparse.Namespace) -> int:
    setup_logging()
    start_utc = msk_day_start(args.start_date)
    end_utc = msk_day_start(args.end_date)

    logger.info("Backfill window: %s -> %s", start_utc.isoformat(), end_utc.isoformat())

    await init_database()
    before = await count_missing_sales(start_utc, end_utc)
    logger.info("Before backfill: %s", before)

    missing_postings = await load_missing_postings(start_utc, end_utc, args.limit)
    posting_schemas = await load_posting_schemas(missing_postings)
    logger.info("Missing sales postings to backfill: %s", len(missing_postings))

    if not missing_postings:
        await close_database()
        return 0

    fbo_map: Dict[str, Dict[str, Any]] = {}
    resolved_fbs = 0
    resolved_fbo = 0
    resolved_fbo_from_transactions = 0
    unresolved: List[str] = []

    try:
        async with OzonClient(
            client_id=settings.ozon_client_id,
            api_key=settings.ozon_api_key,
            performance_client_id=settings.ozon_performance_client_id,
            performance_client_secret=settings.ozon_performance_client_secret,
            max_concurrent_requests=settings.max_concurrent_requests,
        ) as client:
            for idx, posting_number in enumerate(missing_postings, start=1):
                schema = posting_schemas.get(posting_number, "")
                if schema == "FBO":
                    unresolved.append(posting_number)
                    continue

                posting = await fetch_fbs_posting(client, posting_number)
                if posting is not None:
                    items = build_items(posting.get("products") or [])
                    if items:
                        order_id = await choose_fact_order_id(
                            posting_number,
                            posting.get("order_id") or posting.get("order_number"),
                        )
                        await upsert_fact_order(order_id, posting_number, "FBS", posting, items)
                        resolved_fbs += 1
                        logger.info("[%s/%s] FBS resolved %s", idx, len(missing_postings), posting_number)
                        continue
                unresolved.append(posting_number)

            if unresolved:
                logger.info("Trying FBO list for %s unresolved postings", len(unresolved))
                fbo_map = await fetch_fbo_postings_map(client, start_utc, end_utc)
                for posting_number in unresolved[:]:
                    posting = fbo_map.get(posting_number)
                    if posting is None:
                        continue
                    items = build_items(posting.get("products") or [])
                    if not items:
                        continue
                    order_id = await choose_fact_order_id(
                        posting_number,
                        posting.get("order_id") or posting.get("order_number"),
                    )
                    await upsert_fact_order(order_id, posting_number, "FBO", posting, items)
                    resolved_fbo += 1
                    unresolved.remove(posting_number)
                    logger.info("FBO resolved %s", posting_number)

        if unresolved:
            logger.info("Trying FBO fallback from transactions for %s unresolved postings", len(unresolved))
            sku_offer_map = await load_sku_offer_map()
            tx_rows = await load_missing_fbo_transactions(start_utc, end_utc)
            tx_by_posting = {row["posting_number"]: row for row in tx_rows}
            for posting_number in unresolved[:]:
                row = tx_by_posting.get(posting_number)
                if row is None:
                    continue
                payload = row["raw_data"]
                posting = payload.get("posting") if isinstance(payload.get("posting"), dict) else {}
                items_payload = payload.get("items") if isinstance(payload.get("items"), list) else []
                items: List[Dict[str, Any]] = []
                accruals = to_decimal(payload.get("accruals_for_sale")) or to_decimal(payload.get("amount"))
                for raw_item in items_payload:
                    if not isinstance(raw_item, dict):
                        continue
                    sku = to_int(raw_item.get("sku"))
                    quantity = to_decimal(raw_item.get("quantity")) or Decimal("1")
                    offer_id = raw_item.get("offer_id")
                    if not offer_id and sku is not None:
                        offer_id = sku_offer_map.get(sku)
                    price = None
                    if accruals is not None and quantity:
                        price = accruals / quantity
                    items.append(
                        {
                            "offer_id": offer_id,
                            "sku": sku,
                            "name": raw_item.get("name"),
                            "quantity": quantity,
                            "price": price,
                            "buyer_paid": price,
                        }
                    )
                if not items:
                    continue
                order_id = await choose_fact_order_id(
                    posting_number,
                    posting.get("posting_number") or posting_number,
                )
                fact_payload = {
                    "status": "delivered",
                    "substatus": None,
                    "created_at": posting.get("order_date"),
                    "in_process_at": None,
                    "shipment_date": None,
                    "fact_delivery_date": row["operation_date"].isoformat() if row["operation_date"] else None,
                    "price": str(accruals) if accruals is not None else None,
                    "discount_amount": None,
                    "delivery_price": None,
                    "customer": None,
                }
                await upsert_fact_order(order_id, posting_number, "FBO", fact_payload, items)
                resolved_fbo_from_transactions += 1
                unresolved.remove(posting_number)
                logger.info("FBO fallback resolved %s", posting_number)
    finally:
        after = await count_missing_sales(start_utc, end_utc)
        logger.info("After backfill: %s", after)
        await close_database()

    logger.info(
        "Backfill finished: resolved_fbs=%s resolved_fbo=%s resolved_fbo_from_transactions=%s unresolved=%s",
        resolved_fbs,
        resolved_fbo,
        resolved_fbo_from_transactions,
        len(unresolved),
    )
    if unresolved:
        logger.warning("Still unresolved postings: %s", unresolved[:20])
    return 0 if not unresolved else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill missing fact_order_items from posting APIs")
    parser.add_argument("--start-date", required=True, help="Start day in MSK, format YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Exclusive end day in MSK, format YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None, help="Limit missing posting count for test runs")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(build_parser().parse_args())))
