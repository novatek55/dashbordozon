"""Wildberries advertising report ingestion."""
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Set

from sqlalchemy import text
from tenacity import RetryError

from src.database import db_manager
from src.wb_advertising_client import WBAdvertisingClient
from src.wb_finance_client import WBAPIError

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


def _as_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _extract_campaign_ids(value: Any) -> Set[int]:
    ids: Set[int] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"advertId", "advert_id", "id"}:
                try:
                    ids.add(int(nested))
                except (TypeError, ValueError):
                    pass
            else:
                ids.update(_extract_campaign_ids(nested))
    elif isinstance(value, list):
        for item in value:
            ids.update(_extract_campaign_ids(item))
    return ids


def _chunks(values: List[int], size: int) -> Iterable[List[int]]:
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]


def _extract_fullstats_daily_metrics(rows: List[Dict[str, Any]]) -> Dict[tuple[int, str], Dict[str, Any]]:
    metrics: Dict[tuple[int, str], Dict[str, Any]] = {}
    for row in rows or []:
        advert_id = _as_int(row.get("advertId") or row.get("advert_id") or row.get("id"))
        if not advert_id:
            continue
        days = row.get("days")
        if not isinstance(days, list):
            days = [row]
        for day_row in days:
            if not isinstance(day_row, dict):
                continue
            report_date = str(day_row.get("date") or day_row.get("day") or day_row.get("report_date") or "")[:10]
            if not report_date:
                continue
            key = (advert_id, report_date)
            entry = metrics.setdefault(
                key,
                {
                    "views": 0,
                    "clicks": 0,
                    "carts": 0,
                    "orders": 0,
                    "shks": 0,
                    "canceled": 0,
                    "stats_spend": 0.0,
                    "revenue": 0.0,
                    "avg_position": 0.0,
                    "_position_sum": 0.0,
                    "_position_count": 0,
                    "raw": [],
                },
            )
            entry["views"] += _as_int(day_row.get("views"))
            entry["clicks"] += _as_int(day_row.get("clicks"))
            entry["carts"] += _as_int(day_row.get("atbs") or day_row.get("carts") or day_row.get("addToCart"))
            entry["orders"] += _as_int(day_row.get("orders"))
            entry["shks"] += _as_int(day_row.get("shks"))
            entry["canceled"] += _as_int(day_row.get("canceled"))
            entry["stats_spend"] += _as_float(day_row.get("sum") or day_row.get("stats_spend"))
            entry["revenue"] += _as_float(
                day_row.get("sum_price")
                or day_row.get("sumPrice")
                or day_row.get("revenue")
                or day_row.get("ordered_sum")
            )
            entry["raw"].append(day_row)
        for booster_row in row.get("boosterStats") or []:
            if not isinstance(booster_row, dict):
                continue
            report_date = str(booster_row.get("date") or "")[:10]
            if not report_date:
                continue
            key = (advert_id, report_date)
            entry = metrics.setdefault(
                key,
                {
                    "views": 0,
                    "clicks": 0,
                    "carts": 0,
                    "orders": 0,
                    "shks": 0,
                    "canceled": 0,
                    "stats_spend": 0.0,
                    "revenue": 0.0,
                    "avg_position": 0.0,
                    "_position_sum": 0.0,
                    "_position_count": 0,
                    "raw": [],
                },
            )
            position = _as_float(booster_row.get("avg_position"))
            if position > 0:
                entry["_position_sum"] += position
                entry["_position_count"] += 1
    for entry in metrics.values():
        count = int(entry.pop("_position_count", 0) or 0)
        position_sum = float(entry.pop("_position_sum", 0.0) or 0.0)
        entry["avg_position"] = round(position_sum / count, 2) if count else 0.0
    return metrics


def _extract_fullstats_nm_metrics(rows: List[Dict[str, Any]]) -> Dict[tuple[int, str, int], Dict[str, Any]]:
    metrics: Dict[tuple[int, str, int], Dict[str, Any]] = {}
    for row in rows or []:
        advert_id = _as_int(row.get("advertId") or row.get("advert_id") or row.get("id"))
        if not advert_id:
            continue
        for day_row in row.get("days") or []:
            if not isinstance(day_row, dict):
                continue
            report_date = str(day_row.get("date") or "")[:10]
            if not report_date:
                continue
            for app_row in day_row.get("apps") or []:
                if not isinstance(app_row, dict):
                    continue
                for nm_row in app_row.get("nms") or []:
                    if not isinstance(nm_row, dict):
                        continue
                    nm_id = _as_int(nm_row.get("nmId") or nm_row.get("nm") or nm_row.get("nm_id"))
                    if not nm_id:
                        continue
                    key = (advert_id, report_date, nm_id)
                    entry = metrics.setdefault(
                        key,
                        {
                            "name": "",
                            "views": 0,
                            "clicks": 0,
                            "carts": 0,
                            "orders": 0,
                            "shks": 0,
                            "canceled": 0,
                            "stats_spend": 0.0,
                            "revenue": 0.0,
                            "raw": [],
                        },
                    )
                    if nm_row.get("name"):
                        entry["name"] = str(nm_row.get("name"))
                    entry["views"] += _as_int(nm_row.get("views"))
                    entry["clicks"] += _as_int(nm_row.get("clicks"))
                    entry["carts"] += _as_int(nm_row.get("atbs") or nm_row.get("carts") or nm_row.get("addToCart"))
                    entry["orders"] += _as_int(nm_row.get("orders"))
                    entry["shks"] += _as_int(nm_row.get("shks"))
                    entry["canceled"] += _as_int(nm_row.get("canceled"))
                    entry["stats_spend"] += _as_float(nm_row.get("sum") or nm_row.get("stats_spend"))
                    entry["revenue"] += _as_float(nm_row.get("sum_price") or nm_row.get("sumPrice") or nm_row.get("revenue"))
                    entry["raw"].append(nm_row)
    return metrics


def _period_bounds(days_back: int) -> tuple[datetime, datetime]:
    today_msk = datetime.now(MSK).date()
    date_to = today_msk - timedelta(days=1)
    date_from = date_to - timedelta(days=max(1, int(days_back or 30)) - 1)
    return (
        datetime(date_from.year, date_from.month, date_from.day, tzinfo=MSK),
        datetime(date_to.year, date_to.month, date_to.day, tzinfo=MSK),
    )


async def ensure_wb_advertising_tables() -> None:
    async with db_manager.session() as session:
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wb_advertising_campaigns (
                    advert_id BIGINT PRIMARY KEY,
                    name TEXT NULL,
                    type TEXT NULL,
                    status TEXT NULL,
                    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wb_advertising_daily (
                    advert_id BIGINT NOT NULL,
                    report_date DATE NOT NULL,
                    views BIGINT NOT NULL DEFAULT 0,
                    clicks BIGINT NOT NULL DEFAULT 0,
                    carts BIGINT NOT NULL DEFAULT 0,
                    orders BIGINT NOT NULL DEFAULT 0,
                    shks BIGINT NOT NULL DEFAULT 0,
                    canceled BIGINT NOT NULL DEFAULT 0,
                    spend NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    stats_spend NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    revenue NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    avg_position NUMERIC(10, 2) NOT NULL DEFAULT 0,
                    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (advert_id, report_date)
                );
                """
            )
        )
        for ddl in [
            "ALTER TABLE wb_advertising_daily ADD COLUMN IF NOT EXISTS shks BIGINT NOT NULL DEFAULT 0",
            "ALTER TABLE wb_advertising_daily ADD COLUMN IF NOT EXISTS canceled BIGINT NOT NULL DEFAULT 0",
            "ALTER TABLE wb_advertising_daily ADD COLUMN IF NOT EXISTS stats_spend NUMERIC(15, 2) NOT NULL DEFAULT 0",
            "ALTER TABLE wb_advertising_daily ADD COLUMN IF NOT EXISTS avg_position NUMERIC(10, 2) NOT NULL DEFAULT 0",
        ]:
            await session.execute(text(ddl))
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wb_advertising_nm_daily (
                    advert_id BIGINT NOT NULL,
                    report_date DATE NOT NULL,
                    nm_id BIGINT NOT NULL,
                    name TEXT NULL,
                    views BIGINT NOT NULL DEFAULT 0,
                    clicks BIGINT NOT NULL DEFAULT 0,
                    carts BIGINT NOT NULL DEFAULT 0,
                    orders BIGINT NOT NULL DEFAULT 0,
                    shks BIGINT NOT NULL DEFAULT 0,
                    canceled BIGINT NOT NULL DEFAULT 0,
                    stats_spend NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    revenue NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (advert_id, report_date, nm_id)
                );
                """
            )
        )
        await session.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_wb_advertising_daily_report_date
                ON wb_advertising_daily (report_date);
                """
            )
        )
        await session.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_wb_advertising_nm_daily_report_date
                ON wb_advertising_nm_daily (report_date);
                """
            )
        )


async def sync_wb_advertising(api_key: str, days_back: int = 30) -> Dict[str, Any]:
    await ensure_wb_advertising_tables()
    date_from, date_to = _period_bounds(days_back)
    date_from_s = date_from.date().isoformat()
    date_to_s = date_to.date().isoformat()
    campaigns_seen = 0
    detail_rows = 0
    daily_rows = 0
    stats_rows = 0
    nm_rows = 0

    try:
        async with WBAdvertisingClient(api_key=api_key) as client:
            expense_rows = await client.get_expense_history(date_from_s, date_to_s)
            logger.info("WB advertising expense rows found: %s", len(expense_rows))

            async with db_manager.session() as session:
                seen_campaigns: set[int] = set()
                daily_spend: Dict[tuple[int, str], float] = {}
                daily_raw: Dict[tuple[int, str], list[Dict[str, Any]]] = {}
                for row in expense_rows:
                    advert_id = int(row.get("advertId") or row.get("advert_id") or row.get("id") or 0)
                    if not advert_id:
                        continue
                    seen_campaigns.add(advert_id)
                    upd_time = str(row.get("updTime") or row.get("date") or "")[:10]
                    if not upd_time:
                        continue
                    await session.execute(
                        text(
                            """
                            INSERT INTO wb_advertising_campaigns
                            (advert_id, name, type, status, raw_data, last_synced_at)
                            VALUES (:advert_id, :name, :type, :status, CAST(:raw_data AS JSONB), NOW())
                            ON CONFLICT (advert_id) DO UPDATE
                            SET name = COALESCE(EXCLUDED.name, wb_advertising_campaigns.name),
                                type = COALESCE(EXCLUDED.type, wb_advertising_campaigns.type),
                                status = COALESCE(EXCLUDED.status, wb_advertising_campaigns.status),
                                raw_data = EXCLUDED.raw_data,
                                last_synced_at = NOW()
                            """
                        ),
                        {
                            "advert_id": advert_id,
                            "name": row.get("campName"),
                            "type": str(row.get("advertType") or ""),
                            "status": str(row.get("advertStatus") or ""),
                            "raw_data": json.dumps(row, ensure_ascii=False),
                        },
                    )
                    daily_key = (advert_id, upd_time)
                    daily_spend[daily_key] = daily_spend.get(daily_key, 0.0) + _as_float(row.get("updSum") or row.get("sum") or row.get("spend"))
                    daily_raw.setdefault(daily_key, []).append(row)
                for (advert_id, report_date), spend in daily_spend.items():
                    await session.execute(
                        text(
                            """
                            INSERT INTO wb_advertising_daily
                            (advert_id, report_date, views, clicks, carts, orders, shks, canceled, spend, stats_spend, revenue, avg_position, raw_data, last_synced_at)
                            VALUES
                            (:advert_id, :report_date, 0, 0, 0, 0, 0, 0, :spend, 0, 0, 0, CAST(:raw_data AS JSONB), NOW())
                            ON CONFLICT (advert_id, report_date) DO UPDATE
                            SET spend = EXCLUDED.spend,
                                raw_data = EXCLUDED.raw_data,
                                last_synced_at = NOW()
                            """
                        ),
                        {
                            "advert_id": advert_id,
                            "report_date": date.fromisoformat(report_date),
                            "spend": spend,
                            "raw_data": json.dumps(daily_raw.get((advert_id, report_date), []), ensure_ascii=False),
                        },
                    )
                    daily_rows += 1

                fullstats_rows: List[Dict[str, Any]] = []
                for campaign_chunk in _chunks(sorted(seen_campaigns), 100):
                    try:
                        chunk_rows = await client.get_fullstats(campaign_chunk, date_from_s, date_to_s)
                    except Exception as exc:
                        logger.warning("WB advertising fullstats failed for %s campaigns: %s", len(campaign_chunk), exc)
                        continue
                    fullstats_rows.extend(chunk_rows)
                stats_by_day = _extract_fullstats_daily_metrics(fullstats_rows)
                stats_by_nm = _extract_fullstats_nm_metrics(fullstats_rows)
                logger.info("WB advertising fullstats daily rows found: %s", len(stats_by_day))
                for (advert_id, report_date), stat in stats_by_day.items():
                    await session.execute(
                        text(
                            """
                            INSERT INTO wb_advertising_daily
                            (advert_id, report_date, views, clicks, carts, orders, shks, canceled, spend, stats_spend, revenue, avg_position, raw_data, last_synced_at)
                            VALUES
                            (:advert_id, :report_date, :views, :clicks, :carts, :orders, :shks, :canceled, 0, :stats_spend, :revenue, :avg_position, CAST(:raw_data AS JSONB), NOW())
                            ON CONFLICT (advert_id, report_date) DO UPDATE
                            SET views = EXCLUDED.views,
                                clicks = EXCLUDED.clicks,
                                carts = EXCLUDED.carts,
                                orders = EXCLUDED.orders,
                                shks = EXCLUDED.shks,
                                canceled = EXCLUDED.canceled,
                                stats_spend = EXCLUDED.stats_spend,
                                revenue = EXCLUDED.revenue,
                                avg_position = EXCLUDED.avg_position,
                                raw_data = EXCLUDED.raw_data,
                                last_synced_at = NOW()
                            """
                        ),
                        {
                            "advert_id": advert_id,
                            "report_date": date.fromisoformat(report_date),
                            "views": int(stat.get("views") or 0),
                            "clicks": int(stat.get("clicks") or 0),
                            "carts": int(stat.get("carts") or 0),
                            "orders": int(stat.get("orders") or 0),
                            "shks": int(stat.get("shks") or 0),
                            "canceled": int(stat.get("canceled") or 0),
                            "stats_spend": float(stat.get("stats_spend") or 0.0),
                            "revenue": float(stat.get("revenue") or 0.0),
                            "avg_position": float(stat.get("avg_position") or 0.0),
                            "raw_data": json.dumps({"fullstats": stat.get("raw") or []}, ensure_ascii=False),
                        },
                    )
                    stats_rows += 1
                logger.info("WB advertising fullstats nm rows found: %s", len(stats_by_nm))
                for (advert_id, report_date, nm_id), stat in stats_by_nm.items():
                    await session.execute(
                        text(
                            """
                            INSERT INTO wb_advertising_nm_daily
                            (advert_id, report_date, nm_id, name, views, clicks, carts, orders, shks, canceled, stats_spend, revenue, raw_data, last_synced_at)
                            VALUES
                            (:advert_id, :report_date, :nm_id, :name, :views, :clicks, :carts, :orders, :shks, :canceled, :stats_spend, :revenue, CAST(:raw_data AS JSONB), NOW())
                            ON CONFLICT (advert_id, report_date, nm_id) DO UPDATE
                            SET name = COALESCE(EXCLUDED.name, wb_advertising_nm_daily.name),
                                views = EXCLUDED.views,
                                clicks = EXCLUDED.clicks,
                                carts = EXCLUDED.carts,
                                orders = EXCLUDED.orders,
                                shks = EXCLUDED.shks,
                                canceled = EXCLUDED.canceled,
                                stats_spend = EXCLUDED.stats_spend,
                                revenue = EXCLUDED.revenue,
                                raw_data = EXCLUDED.raw_data,
                                last_synced_at = NOW()
                            """
                        ),
                        {
                            "advert_id": advert_id,
                            "report_date": date.fromisoformat(report_date),
                            "nm_id": nm_id,
                            "name": stat.get("name") or None,
                            "views": int(stat.get("views") or 0),
                            "clicks": int(stat.get("clicks") or 0),
                            "carts": int(stat.get("carts") or 0),
                            "orders": int(stat.get("orders") or 0),
                            "shks": int(stat.get("shks") or 0),
                            "canceled": int(stat.get("canceled") or 0),
                            "stats_spend": float(stat.get("stats_spend") or 0.0),
                            "revenue": float(stat.get("revenue") or 0.0),
                            "raw_data": json.dumps({"nms": stat.get("raw") or []}, ensure_ascii=False),
                        },
                    )
                    nm_rows += 1
                campaigns_seen = len(seen_campaigns)
                detail_rows = len(expense_rows)
    except Exception as exc:
        root_exc = exc.last_attempt.exception() if isinstance(exc, RetryError) else exc
        if isinstance(root_exc, WBAPIError) and root_exc.status_code in {401, 403}:
            raise RuntimeError(
                "WB Promotion API token scope is not allowed. Add a WB token with Promotion/Advertising access."
            ) from root_exc
        raise

    return {
        "status": "success",
        "date_from": date_from_s,
        "date_to": date_to_s,
        "campaigns": campaigns_seen,
        "campaign_details": detail_rows,
        "daily_rows": daily_rows,
        "stats_rows": stats_rows,
        "nm_rows": nm_rows,
    }
