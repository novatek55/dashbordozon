"""GET /api/monthly-report — скачать MD-отчёт за месяц."""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
from aiohttp import web

from src.dashboard.helpers import month_bounds


async def get_monthly_report(request: web.Request) -> web.Response:
    month_value = (request.query.get("month") or "").strip()
    if not month_value:
        month_value = datetime.now(timezone.utc).strftime("%Y-%m")

    try:
        month_bounds(month_value)
    except ValueError:
        return web.Response(text="Invalid month format, expected YYYY-MM", status=400)

    from src.services.monthly_report import build_monthly_report

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        md_text = await build_monthly_report(conn, month_value)

    filename = f"ozon_monthly_report_{month_value}.md"
    return web.Response(
        text=md_text,
        content_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
