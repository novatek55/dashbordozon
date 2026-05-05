from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Set

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dashboard.constants import FINANCE_DESCRIPTION_FILTERS


KNOWN_SERVICE_NAMES: Set[str] = {
    "MarketplaceServiceItemDirectFlowLogistic",
    "MarketplaceServiceItemReturnFlowLogistic",
    "MarketplaceServiceItemDropoffPVZ",
    "MarketplaceServiceItemDropoffSC",
    "MarketplaceServiceItemRedistributionReturnsPVZ",
    "MarketplaceServiceItemRedistributionDropOffApvz",
    "MarketplaceServiceItemRedistributionLastMileCourier",
    "MarketplaceServiceItemRedistributionLastMilePVZ",
    "MarketplaceServiceItemTemporaryStorageRedistribution",
    "MarketplaceServiceItemPackageRedistribution",
    "MarketplaceRedistributionOfAcquiringOperation",
}

# Операции, которые в коде маппятся отдельной логикой и не считаются "неизвестными".
KNOWN_DESCRIPTIONS: Set[str] = {
    "Доставка и обработка возврата, отмены, невыкупа",
    "Оплата эквайринга",
    "Подписка Premium Plus",
    "Оплата за клик",
    "Закрепление отзыва",
    "Продвижение с оплатой за заказ",
    "Ускоренный сбор отзывов",
    "Баллы за отзывы",
    "Кросс-докинг",
    "Временное размещение товара партнерами",
    "Временное размещение товара партнёрами",
    "Упаковка товара партнёрами",
}


def month_bounds_utc(month: str) -> tuple[datetime, datetime]:
    year, mon = month.split("-", 1)
    y = int(year)
    m = int(mon)
    start_local = datetime(y, m, 1, tzinfo=timezone(timedelta(hours=3)))
    if m == 12:
        end_local = datetime(y + 1, 1, 1, tzinfo=timezone(timedelta(hours=3)))
    else:
        end_local = datetime(y, m + 1, 1, tzinfo=timezone(timedelta(hours=3)))
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Find expense articles not explicitly mapped.")
    parser.add_argument("--month", default=datetime.now().strftime("%Y-%m"))
    parser.add_argument("--dsn", default="postgresql://postgres:123456@localhost:5432/ozon_analytics")
    args = parser.parse_args()

    date_from, date_to = month_bounds_utc(args.month)
    conn = await asyncpg.connect(args.dsn)
    try:
        mapped_desc: Set[str] = set()
        for values in FINANCE_DESCRIPTION_FILTERS.values():
            for v in values:
                mapped_desc.add(str(v))

        unknown_desc = await conn.fetch(
            """
            SELECT description, sum(abs(amount::numeric)) AS total, count(*) AS cnt
            FROM transactions
            WHERE operation_date >= $1
              AND operation_date < $2
              AND coalesce(description, '') <> ''
            GROUP BY description
            ORDER BY total DESC
            """,
            date_from,
            date_to,
        )

        unknown_service = await conn.fetch(
            """
            SELECT service_name, sum(abs(price::numeric)) AS total, count(*) AS cnt
            FROM transaction_services
            WHERE operation_date >= $1
              AND operation_date < $2
              AND coalesce(service_name, '') <> ''
            GROUP BY service_name
            ORDER BY total DESC
            """,
            date_from,
            date_to,
        )

        print(f"Period: {args.month} ({date_from.isoformat()} .. {date_to.isoformat()})")
        print("\nUnknown descriptions (will fall back to 'Другие услуги'):")
        shown = 0
        for row in unknown_desc:
            desc = str(row["description"] or "")
            if desc in mapped_desc or desc in KNOWN_DESCRIPTIONS:
                continue
            shown += 1
            print(f"- {desc} | total={float(row['total'] or 0):.2f} | cnt={int(row['cnt'] or 0)}")
        if shown == 0:
            print("- none")

        print("\nUnknown service names (will fall back to 'Другие услуги'):")
        shown = 0
        for row in unknown_service:
            name = str(row["service_name"] or "")
            if name in KNOWN_SERVICE_NAMES:
                continue
            shown += 1
            print(f"- {name} | total={float(row['total'] or 0):.2f} | cnt={int(row['cnt'] or 0)}")
        if shown == 0:
            print("- none")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
