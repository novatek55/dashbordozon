# src/export_monthly_report.py
"""CLI: python -m src.export_monthly_report --month 2026-05

Сохраняет MD-отчёт в exports/monthly_report_YYYY-MM.md
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

from src.dashboard.helpers import month_bounds, to_asyncpg_dsn
from src.config import settings


async def _run(month_value: str) -> None:
    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    try:
        from src.services.monthly_report import build_monthly_report
        md_text = await build_monthly_report(conn, month_value)
    finally:
        await conn.close()

    out_dir = Path("exports")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"monthly_report_{month_value}.md"
    out_path.write_text(md_text, encoding="utf-8")
    print(f"Saved: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Ozon monthly MD report")
    parser.add_argument(
        "--month",
        default=datetime.now(timezone.utc).strftime("%Y-%m"),
        help="Month in YYYY-MM format (default: current month)",
    )
    args = parser.parse_args()

    try:
        month_bounds(args.month)
    except ValueError:
        print(f"Error: invalid month '{args.month}', expected YYYY-MM", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run(args.month))


if __name__ == "__main__":
    main()
