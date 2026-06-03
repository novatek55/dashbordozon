# src/export_article_abc_report.py
"""CLI: python -m src.export_article_abc_report --month 2026-05 [--compare 2026-04]

Сохраняет АВС-анализ в exports/article_abc_YYYY-MM.md
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


async def _run(month: str, compare: str | None) -> None:
    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    try:
        from src.services.article_abc_report import build_article_abc_report
        md = await build_article_abc_report(conn, month, compare)
    finally:
        await conn.close()

    out_dir = Path("exports")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"article_abc_{month}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"Saved: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Ozon Article ABC MD report")
    parser.add_argument(
        "--month",
        default=datetime.now(timezone.utc).strftime("%Y-%m"),
        help="Отчётный месяц YYYY-MM (по умолчанию — текущий)",
    )
    parser.add_argument(
        "--compare",
        default=None,
        help="Месяц для сравнения YYYY-MM (опционально)",
    )
    args = parser.parse_args()

    for m in filter(None, [args.month, args.compare]):
        try:
            month_bounds(m)
        except ValueError:
            print(f"Error: неверный формат месяца '{m}', ожидается YYYY-MM", file=sys.stderr)
            sys.exit(1)

    asyncio.run(_run(args.month, args.compare))


if __name__ == "__main__":
    main()
