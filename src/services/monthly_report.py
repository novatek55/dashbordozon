# src/services/monthly_report.py
"""Сборщик ежемесячного MD-отчёта для ИИ-анализа."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Tuple

import asyncpg

from src.dashboard.constants import MSK
from src.dashboard.helpers import safe_divide
from src.dashboard.routes.finance import build_rows_map_for_month


def _fmt_rub(value: Any) -> str:
    try:
        return f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value or 0) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _month_dates(month_value: str) -> Tuple[date, date]:
    year, month = int(month_value[:4]), int(month_value[5:7])
    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return first, last


def _build_header(month_value: str) -> str:
    first, last = _month_dates(month_value)
    generated = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")
    return (
        f"# Ozon Monthly Report — {month_value}\n"
        f"Generated: {generated}  \n"
        f"Period: {first} — {last}\n\n"
    )


async def _build_shop_summary(conn: asyncpg.Connection, month_value: str) -> str:
    rows_map, days = await build_rows_map_for_month(conn, month_value)

    def total(key: str) -> float:
        return float(rows_map.get(key, {}).get("total") or 0)

    revenue = total("revenue")
    returns_rev = total("returns_revenue")
    net_revenue = revenue - returns_rev
    commission = total("ozon_fee_total")
    logistics = total("delivery_services_total")
    ads = total("promotion_total")
    other_mp = total("agent_services_total")
    total_mp_exp = total("marketplace_expenses")
    gross_profit = total("gross_profit")

    first, last = _month_dates(month_value)
    first_utc = datetime(first.year, first.month, first.day, tzinfo=MSK).astimezone(timezone.utc)
    if last.month == 12:
        end_utc = datetime(last.year + 1, 1, 1, tzinfo=MSK).astimezone(timezone.utc)
    else:
        end_utc = datetime(last.year, last.month + 1, 1, tzinfo=MSK).astimezone(timezone.utc)

    order_row = await conn.fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE lower(coalesce(status,'')) IN (
                'delivered','delivering','awaiting_deliver','awaiting_packaging',
                'driver_pickup','доставлен','доставляется','ожидает в пвз',
                'у водителя','ожидает отгрузки','ожидает сборки'
            )) AS orders_cnt,
            count(*) FILTER (WHERE lower(coalesce(status,'')) IN (
                'cancelled','отменён','отменен'
            )) AS cancelled_cnt
        FROM fact_orders
        WHERE created_at >= $1 AND created_at < $2
        """,
        first_utc, end_utc,
    )
    orders_cnt = int(order_row["orders_cnt"] or 0)
    cancelled_cnt = int(order_row["cancelled_cnt"] or 0)

    returns_row = await conn.fetchrow(
        """
        SELECT count(*) AS cnt
        FROM returns
        WHERE accepted_at >= $1 AND accepted_at < $2
        """,
        first_utc, end_utc,
    )
    returns_cnt = int(returns_row["cnt"] or 0) if returns_row else 0
    returns_pct = safe_divide(returns_cnt, orders_cnt) * 100 if orders_cnt else 0.0

    rating_row = await conn.fetchrow(
        """
        SELECT rating
        FROM seller_rating_history
        ORDER BY recorded_at DESC
        LIMIT 1
        """
    )
    rating = float(rating_row["rating"] or 0) if rating_row else 0.0

    reviews_row = await conn.fetchrow(
        """
        SELECT
            count(*) AS new_reviews,
            round(avg(rating)::numeric, 2) AS avg_score
        FROM reviews
        WHERE published_at >= $1 AND published_at < $2
        """,
        first_utc, end_utc,
    )
    new_reviews = int(reviews_row["new_reviews"] or 0) if reviews_row else 0
    avg_score = float(reviews_row["avg_score"] or 0) if reviews_row else 0.0

    def pct_of_net(v: float) -> str:
        return _fmt_pct(safe_divide(v, net_revenue)) if net_revenue else "—"

    lines = [
        "## Магазин — итоги месяца\n",
        "| Метрика | Значение | % от чистой выручки |",
        "|---|---|---|",
        f"| Выручка (gross) | {_fmt_rub(revenue)} ₽ | — |",
        f"| Возвраты | {_fmt_rub(returns_rev)} ₽ | {pct_of_net(returns_rev)} |",
        f"| Чистая выручка | {_fmt_rub(net_revenue)} ₽ | — |",
        f"| Комиссия Ozon | {_fmt_rub(commission)} ₽ | {pct_of_net(commission)} |",
        f"| Логистика | {_fmt_rub(logistics)} ₽ | {pct_of_net(logistics)} |",
        f"| Реклама | {_fmt_rub(ads)} ₽ | {pct_of_net(ads)} |",
        f"| Прочие расходы MP | {_fmt_rub(other_mp)} ₽ | {pct_of_net(other_mp)} |",
        f"| Итого расходы MP | {_fmt_rub(total_mp_exp)} ₽ | {pct_of_net(total_mp_exp)} |",
        f"| Валовая прибыль | {_fmt_rub(gross_profit)} ₽ | {pct_of_net(gross_profit)} |",
        f"| Заказов (шт.) | {orders_cnt} | — |",
        f"| Отменено (шт.) | {cancelled_cnt} | — |",
        f"| Возвратов | {returns_cnt} шт. / {returns_pct:.1f}% | — |",
        f"| Рейтинг продавца | {rating:.2f} | — |",
        f"| Новых отзывов | {new_reviews} | — |",
        f"| Средняя оценка | {avg_score:.2f} | — |",
        "",
    ]
    return "\n".join(lines) + "\n"
