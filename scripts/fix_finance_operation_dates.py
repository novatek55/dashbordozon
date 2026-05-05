from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

MSK = timezone(timedelta(hours=3))


def parse_operation_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S",
                "%d.%m.%Y %H:%M:%S",
                "%d.%m.%Y",
                "%Y-%m-%d",
            ):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    dt = None
            if dt is None:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt.astimezone(timezone.utc)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Fix finance operation_date timezone normalization.")
    parser.add_argument("--dsn", default="postgresql://postgres:123456@localhost:5432/ozon_analytics")
    parser.add_argument("--apply", action="store_true", help="Apply updates. Default is dry-run.")
    args = parser.parse_args()

    conn = await asyncpg.connect(args.dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT transaction_id, operation_date, raw_data
            FROM transactions
            WHERE raw_data IS NOT NULL
            """
        )

        to_update: list[tuple[int, datetime]] = []
        for r in rows:
            payload = r["raw_data"] if isinstance(r["raw_data"], dict) else {}
            raw_dt = payload.get("operation_date")
            parsed = parse_operation_datetime(raw_dt)
            if parsed is None:
                continue
            current = r["operation_date"]
            if current is None:
                to_update.append((int(r["transaction_id"]), parsed))
                continue
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if abs((current - parsed).total_seconds()) >= 1:
                to_update.append((int(r["transaction_id"]), parsed))

        print(f"transactions total={len(rows)} updates_needed={len(to_update)}")
        if to_update:
            print("sample:")
            for tx_id, dt in to_update[:10]:
                print(tx_id, dt.isoformat())

        if not args.apply:
            print("dry-run mode: no changes applied")
            return

        async with conn.transaction():
            for tx_id, dt in to_update:
                await conn.execute(
                    "UPDATE transactions SET operation_date=$1, last_synced_at=now() WHERE transaction_id=$2",
                    dt,
                    tx_id,
                )
                await conn.execute(
                    "UPDATE transaction_items SET operation_date=$1, last_synced_at=now() WHERE transaction_id=$2",
                    dt,
                    tx_id,
                )
                await conn.execute(
                    "UPDATE transaction_services SET operation_date=$1, last_synced_at=now() WHERE transaction_id=$2",
                    dt,
                    tx_id,
                )
        print(f"applied updates: {len(to_update)}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
