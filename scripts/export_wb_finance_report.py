"""Export WB finance report from wb_finance_daily to CSV."""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import date, datetime
from pathlib import Path
import sys

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import settings


def month_bounds(month_value: str) -> tuple[date, date]:
    start = datetime.strptime(month_value, "%Y-%m").date().replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1, day=1)
    else:
        next_month = start.replace(month=start.month + 1, day=1)
    end = next_month.fromordinal(next_month.toordinal() - 1)
    return start, end


async def export_report(month_value: str, final_only: bool, output_path: Path) -> None:
    start_date, end_date = month_bounds(month_value)
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT
                report_date,
                gross_revenue,
                marketplace_commission,
                logistics_direct,
                logistics_reverse,
                acquiring,
                penalties,
                other_deductions,
                to_pay,
                rows_count,
                is_final_day
            FROM wb_finance_daily
            WHERE report_date BETWEEN $1 AND $2
              AND ($3::bool = false OR is_final_day = true)
            ORDER BY report_date
            """,
            start_date,
            end_date,
            final_only,
        )
    finally:
        await conn.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            [
                "report_date",
                "gross_revenue",
                "marketplace_commission",
                "logistics_direct",
                "logistics_reverse",
                "acquiring",
                "penalties",
                "other_deductions",
                "to_pay",
                "rows_count",
                "is_final_day",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r["report_date"],
                    r["gross_revenue"],
                    r["marketplace_commission"],
                    r["logistics_direct"],
                    r["logistics_reverse"],
                    r["acquiring"],
                    r["penalties"],
                    r["other_deductions"],
                    r["to_pay"],
                    r["rows_count"],
                    r["is_final_day"],
                ]
            )

    print(f"Exported {len(rows)} rows to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export WB finance report to CSV")
    parser.add_argument("--month", required=True, help="Month in YYYY-MM format")
    parser.add_argument("--include-non-final", action="store_true", help="Include non-final days")
    parser.add_argument("--out", default=None, help="Output CSV path")
    args = parser.parse_args()

    month_value = args.month
    output = Path(args.out) if args.out else Path("exports") / f"wb_finance_report_{month_value}.csv"
    asyncio.run(export_report(month_value, not args.include_non_final, output))


if __name__ == "__main__":
    main()
