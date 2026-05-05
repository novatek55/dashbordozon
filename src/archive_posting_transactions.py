"""Archive raw Finance API responses for posting_number values from transactions."""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config import settings
from src.database import close_database, db_manager, init_database
from src.models import PostingTransactionSnapshot, Transaction
from src.ozon_client import OzonClient


MSK = timezone(timedelta(hours=3))
logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )


def parse_day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def msk_day_start(value: str) -> datetime:
    return parse_day(value).replace(tzinfo=MSK).astimezone(timezone.utc)


def default_end_utc() -> datetime:
    now_msk = datetime.now(MSK)
    tomorrow_msk = (now_msk + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow_msk.astimezone(timezone.utc)


async def ensure_snapshot_table() -> None:
    async with db_manager.engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: PostingTransactionSnapshot.__table__.create(sync_conn, checkfirst=True)
        )


async def load_posting_numbers(start_utc: datetime, end_utc: datetime, limit: int | None) -> list[str]:
    async with db_manager.session() as session:
        stmt = (
            select(Transaction.posting_number)
            .where(Transaction.operation_date >= start_utc)
            .where(Transaction.operation_date < end_utc)
            .where(Transaction.posting_number.is_not(None))
            .where(Transaction.posting_number != "")
            .distinct()
            .order_by(Transaction.posting_number)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = await session.execute(stmt)
        return [value for value in rows.scalars().all() if value]


async def archive_posting(
    client: OzonClient,
    posting_number: str,
    start_utc: datetime,
    end_utc: datetime,
) -> int:
    response = await client.get_transaction_list(
        from_date=start_utc,
        to_date=end_utc,
        posting_number=posting_number,
        transaction_type="all",
        page=1,
        page_size=1000,
    )
    result_block = response.get("result", {}) if isinstance(response, dict) else {}
    row_count = int(result_block.get("row_count") or len(result_block.get("operations") or []))
    snapshot_dict = {
        "posting_number": posting_number,
        "date_from": start_utc,
        "date_to": end_utc,
        "requested_at": datetime.now(timezone.utc),
        "row_count": row_count,
        "response_json": response,
        "last_synced_at": datetime.now(timezone.utc),
    }

    async with db_manager.session() as session:
        stmt = pg_insert(PostingTransactionSnapshot).values(**snapshot_dict)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_posting_txn_snapshot_window",
            set_={
                "requested_at": snapshot_dict["requested_at"],
                "row_count": snapshot_dict["row_count"],
                "response_json": snapshot_dict["response_json"],
                "last_synced_at": snapshot_dict["last_synced_at"],
            },
        )
        await session.execute(stmt)
    return row_count


async def async_main(args: argparse.Namespace) -> int:
    setup_logging()

    start_utc = msk_day_start(args.start_date)
    end_utc = default_end_utc() if args.end_date is None else msk_day_start(args.end_date)

    logger.info("Archive window: %s -> %s", start_utc.isoformat(), end_utc.isoformat())

    await init_database()
    await ensure_snapshot_table()

    posting_numbers = await load_posting_numbers(start_utc, end_utc, args.limit)
    logger.info("Found %s posting_number values in transactions", len(posting_numbers))

    archived = 0
    failed = 0

    try:
        async with OzonClient(
            client_id=settings.ozon_client_id,
            api_key=settings.ozon_api_key,
            performance_client_id=settings.ozon_performance_client_id,
            performance_client_secret=settings.ozon_performance_client_secret,
            max_concurrent_requests=settings.max_concurrent_requests,
        ) as client:
            for index, posting_number in enumerate(posting_numbers, start=1):
                try:
                    row_count = await archive_posting(client, posting_number, start_utc, end_utc)
                    archived += 1
                    logger.info(
                        "[%s/%s] archived %s rows for %s",
                        index,
                        len(posting_numbers),
                        row_count,
                        posting_number,
                    )
                except Exception:
                    failed += 1
                    logger.exception("[%s/%s] failed to archive %s", index, len(posting_numbers), posting_number)
    finally:
        await close_database()

    logger.info("Archive completed: archived=%s failed=%s", archived, failed)
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive raw Finance API responses by posting_number")
    parser.add_argument("--start-date", default="2026-01-01", help="Start day in MSK, format YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="Exclusive end day in MSK, format YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None, help="Limit posting_number count for test runs")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(build_parser().parse_args())))
