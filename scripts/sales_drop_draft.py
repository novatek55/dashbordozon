#!/usr/bin/env python3
"""Build a compact sales-drop diagnostic draft from analytics_data.

Outputs:
- summary.md
- daily_totals_7d.csv
- funnel_4d.csv
- top_drop_skus.csv
- top_drop_skus_trend_7d.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def load_database_url(env_path: Path) -> str:
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "DATABASE_URL":
                return value.strip().strip('"').strip("'")

    from_env = os.getenv("DATABASE_URL")
    if from_env:
        return from_env

    raise RuntimeError("DATABASE_URL not found in .env or environment")


def write_csv(path: Path, rows: Iterable[dict], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in headers})


@dataclass
class ReportContext:
    target_day: date
    compare_day: date
    trend_start: date
    trend_end: date
    funnel_start: date
    funnel_end: date


@dataclass
class SignalRow:
    article: str
    sku: int
    orders_prev: float
    orders_target: float
    orders_delta_pct: float
    impressions_prev: float
    impressions_target: float
    impressions_delta_pct: float
    clicks_prev: float
    clicks_target: float
    clicks_delta_pct: float
    tocart_prev: float
    tocart_target: float
    tocart_delta_pct: float
    demand_prev: float | None
    demand_target: float | None
    demand_delta_pct: float | None
    demand_filter_applied: bool
    demand_alignment: str
    impression_issue: bool
    ad_spend_prev: float | None
    ad_spend_target: float | None
    ad_spend_delta_pct: float | None
    ad_event_flag: str
    stock_available_now: float | None
    stock_event_flag: str
    position_target: float | None
    position_baseline: float | None
    position_event_flag: str
    competitor_event_flag: str
    rating_event_flag: str
    delivery_event_flag: str
    signal_status: str
    signal_reason: str


async def fetch_all(conn, sql: str, **params) -> list[dict]:
    result = await conn.execute(text(sql), params)
    return [dict(r._mapping) for r in result.fetchall()]


SQL_DAILY_TOTALS_7D = """
SELECT
  date::date AS day,
  SUM(COALESCE((metric_values->>'revenue')::numeric,revenue,0)) AS revenue,
  SUM(COALESCE((metric_values->>'ordered_units')::numeric,ordered_units,0)) AS ordered_units,
  SUM(COALESCE((metric_values->>'delivered_units')::numeric,delivered_units,0)) AS delivered_units,
  SUM(COALESCE((metric_values->>'returns')::numeric,returned_units,0)) AS returns_units,
  SUM(COALESCE((metric_values->>'cancellations')::numeric,0)) AS cancellations,
  SUM(COALESCE((metric_values->>'hits_view')::numeric,impressions,0)) AS hits_view,
  SUM(COALESCE((metric_values->>'session_view')::numeric,0)) AS session_view,
  SUM(COALESCE((metric_values->>'hits_tocart')::numeric,clicks,0)) AS hits_tocart
FROM analytics_data
WHERE date::date BETWEEN :trend_start AND :trend_end
GROUP BY 1
ORDER BY 1
"""


SQL_FUNNEL_4D = """
SELECT
  date::date AS day,
  SUM(COALESCE((metric_values->>'hits_view')::numeric,impressions,0)) AS hits_view,
  SUM(COALESCE((metric_values->>'session_view')::numeric,0)) AS session_view,
  SUM(COALESCE((metric_values->>'hits_tocart')::numeric,clicks,0)) AS hits_tocart,
  SUM(COALESCE((metric_values->>'ordered_units')::numeric,ordered_units,0)) AS ordered_units,
  SUM(COALESCE((metric_values->>'delivered_units')::numeric,delivered_units,0)) AS delivered_units,
  SUM(COALESCE((metric_values->>'returns')::numeric,returned_units,0)) AS returns_units,
  SUM(COALESCE((metric_values->>'cancellations')::numeric,0)) AS cancellations
FROM analytics_data
WHERE date::date BETWEEN :funnel_start AND :funnel_end
GROUP BY 1
ORDER BY 1
"""


SQL_TOP_DROPS = """
WITH sku_map AS (
    SELECT DISTINCT ON (sku) sku, offer_id
    FROM (
        SELECT fbo_sku_id::bigint AS sku, trim(offer_id) AS offer_id, last_synced_at
        FROM report_products_items
        WHERE fbo_sku_id IS NOT NULL AND COALESCE(trim(offer_id), '') <> ''
        UNION ALL
        SELECT fbs_sku_id::bigint AS sku, trim(offer_id) AS offer_id, last_synced_at
        FROM report_products_items
        WHERE fbs_sku_id IS NOT NULL AND COALESCE(trim(offer_id), '') <> ''
    ) s
    ORDER BY sku, last_synced_at DESC NULLS LAST
), d AS (
    SELECT
      ad.date::date AS day,
      ad.sku,
      COALESCE(sm.offer_id, 'sku_'||ad.sku::text) AS article,
      COALESCE((ad.metric_values->>'revenue')::numeric, ad.revenue, 0) AS revenue,
      COALESCE((ad.metric_values->>'ordered_units')::numeric, ad.ordered_units, 0) AS ordered_units,
      COALESCE((ad.metric_values->>'hits_view')::numeric, ad.impressions, 0) AS hits_view,
      COALESCE((ad.metric_values->>'session_view')::numeric, 0) AS session_view,
      COALESCE((ad.metric_values->>'hits_tocart')::numeric, ad.clicks, 0) AS hits_tocart,
      COALESCE((ad.metric_values->>'cancellations')::numeric, 0) AS cancellations,
      COALESCE((ad.metric_values->>'returns')::numeric, ad.returned_units, 0) AS returns_units
    FROM analytics_data ad
    LEFT JOIN sku_map sm ON sm.sku = ad.sku
    WHERE ad.date::date BETWEEN :trend_start AND :trend_end
), by_sku AS (
    SELECT
      COALESCE(a.article,b.article) AS article,
      COALESCE(a.sku,b.sku) AS sku,
      COALESCE(b.revenue,0) AS rev_prev,
      COALESCE(a.revenue,0) AS rev_target,
      COALESCE(a.revenue,0)-COALESCE(b.revenue,0) AS delta_revenue,
      COALESCE(b.ordered_units,0) AS orders_prev,
      COALESCE(a.ordered_units,0) AS orders_target,
      COALESCE(b.hits_view,0) AS hits_prev,
      COALESCE(a.hits_view,0) AS hits_target,
      COALESCE(b.hits_tocart,0) AS tocart_prev,
      COALESCE(a.hits_tocart,0) AS tocart_target,
      COALESCE(b.session_view,0) AS sessions_prev,
      COALESCE(a.session_view,0) AS sessions_target,
      COALESCE(a.cancellations,0) AS cancellations_target,
      COALESCE(a.returns_units,0) AS returns_target
    FROM (SELECT * FROM d WHERE day = :target_day) a
    FULL JOIN (SELECT * FROM d WHERE day = :compare_day) b ON a.sku = b.sku
), total_drop AS (
    SELECT ABS(SUM(CASE WHEN delta_revenue < 0 THEN delta_revenue ELSE 0 END)) AS total_neg
    FROM by_sku
)
SELECT
  s.article,
  s.sku,
  s.orders_prev,
  s.orders_target,
  s.delta_revenue,
  ROUND(100 * ABS(s.delta_revenue) / NULLIF(t.total_neg,0), 2) AS contribution_pct,
  s.hits_prev,
  s.hits_target,
  s.tocart_prev,
  s.tocart_target,
  s.sessions_prev,
  s.sessions_target,
  s.cancellations_target,
  s.returns_target
FROM by_sku s
CROSS JOIN total_drop t
WHERE s.delta_revenue < 0
ORDER BY s.delta_revenue ASC
LIMIT 20
"""


SQL_TOP_DROPS_TREND_TEMPLATE = """
WITH top_skus AS (
    SELECT sku::bigint
    FROM (VALUES __SKU_VALUES__) AS t(sku)
), sku_map AS (
    SELECT DISTINCT ON (sku) sku, offer_id
    FROM (
        SELECT fbo_sku_id::bigint AS sku, trim(offer_id) AS offer_id, last_synced_at
        FROM report_products_items
        WHERE fbo_sku_id IS NOT NULL AND COALESCE(trim(offer_id), '') <> ''
        UNION ALL
        SELECT fbs_sku_id::bigint AS sku, trim(offer_id) AS offer_id, last_synced_at
        FROM report_products_items
        WHERE fbs_sku_id IS NOT NULL AND COALESCE(trim(offer_id), '') <> ''
    ) s
    ORDER BY sku, last_synced_at DESC NULLS LAST
)
SELECT
  ad.date::date AS day,
  ad.sku,
  COALESCE(sm.offer_id, 'sku_'||ad.sku::text) AS article,
  COALESCE((ad.metric_values->>'revenue')::numeric, ad.revenue, 0) AS revenue,
  COALESCE((ad.metric_values->>'ordered_units')::numeric, ad.ordered_units, 0) AS ordered_units,
  COALESCE((ad.metric_values->>'hits_view')::numeric, ad.impressions, 0) AS hits_view,
  COALESCE((ad.metric_values->>'hits_tocart')::numeric, ad.clicks, 0) AS hits_tocart,
  COALESCE((ad.metric_values->>'session_view')::numeric, 0) AS session_view
FROM analytics_data ad
JOIN top_skus t ON t.sku = ad.sku
LEFT JOIN sku_map sm ON sm.sku = ad.sku
WHERE ad.date::date BETWEEN :trend_start AND :trend_end
ORDER BY article, day
"""


SQL_DEMAND_COMPARE_TEMPLATE = """
WITH top_skus AS (
    SELECT sku::bigint
    FROM (VALUES __SKU_VALUES__) AS t(sku)
),
d AS (
    SELECT
      period_end::date AS day,
      sku,
      SUM(COALESCE(searches, 0)) AS demand_searches
    FROM analytics_product_query_summary
    WHERE period_end::date IN (:target_day, :compare_day)
      AND sku IN (SELECT sku FROM top_skus)
    GROUP BY 1, 2
)
SELECT
  sku,
  SUM(CASE WHEN day = :compare_day THEN demand_searches ELSE 0 END) AS demand_prev,
  SUM(CASE WHEN day = :target_day THEN demand_searches ELSE 0 END) AS demand_target
FROM d
GROUP BY sku
"""


SQL_AD_SPEND_COMPARE_TEMPLATE = """
WITH top_skus AS (
    SELECT sku::bigint
    FROM (VALUES __SKU_VALUES__) AS t(sku)
),
d AS (
    SELECT
      date::date AS day,
      sku,
      SUM(COALESCE(spent, 0)) AS ad_spend
    FROM campaign_statistics
    WHERE date::date IN (:target_day, :compare_day)
      AND sku IN (SELECT sku FROM top_skus)
    GROUP BY 1, 2
)
SELECT
  sku,
  SUM(CASE WHEN day = :compare_day THEN ad_spend ELSE 0 END) AS ad_spend_prev,
  SUM(CASE WHEN day = :target_day THEN ad_spend ELSE 0 END) AS ad_spend_target
FROM d
GROUP BY sku
"""


SQL_STOCK_NOW_TEMPLATE = """
WITH top_skus AS (
    SELECT sku::bigint
    FROM (VALUES __SKU_VALUES__) AS t(sku)
)
SELECT
  sku,
  SUM(COALESCE(available_stock_count, 0)) AS stock_available_now
FROM analytics_stocks
WHERE sku IN (SELECT sku FROM top_skus)
GROUP BY sku
"""


SQL_POSITION_COMPARE_TEMPLATE = """
WITH top_skus AS (
    SELECT sku::bigint
    FROM (VALUES __SKU_VALUES__) AS t(sku)
)
SELECT
  ad.sku,
  COALESCE(NULLIF((ad.metric_values->>'position_category')::numeric, 0),
           ad.position_category, ad.position, 0) AS position_target,
  bp.position_baseline
FROM analytics_data ad
JOIN top_skus t ON t.sku = ad.sku
LEFT JOIN LATERAL (
    SELECT AVG(
        COALESCE(NULLIF((a2.metric_values->>'position_category')::numeric, 0),
                 a2.position_category, a2.position, 0)
    ) FILTER (WHERE COALESCE(NULLIF((a2.metric_values->>'position_category')::numeric, 0),
                              a2.position_category, a2.position, 0) > 2)
    AS position_baseline
    FROM analytics_data a2
    WHERE a2.sku = ad.sku
      AND a2.date::date BETWEEN :baseline_start AND :compare_day
) bp ON TRUE
WHERE ad.date::date = :target_day
"""


def num(v) -> float:
    return float(v or 0)


def pct(n: float, d: float) -> float:
    return (n / d * 100) if d else 0.0


def pct_change(curr: float, prev: float) -> float:
    if prev == 0:
        return 0.0 if curr == 0 else 100.0
    return (curr - prev) / prev * 100


def classify_signal(
    row: dict,
    demand: dict | None,
    demand_filter_applied: bool,
    ad_spend: dict | None,
    stock_now: dict | None,
    position: dict | None = None,
) -> SignalRow:
    orders_prev = num(row.get("orders_prev"))
    orders_target = num(row.get("orders_target"))
    impressions_prev = num(row.get("hits_prev"))
    impressions_target = num(row.get("hits_target"))
    clicks_prev = num(row.get("sessions_prev"))
    clicks_target = num(row.get("sessions_target"))
    tocart_prev = num(row.get("tocart_prev"))
    tocart_target = num(row.get("tocart_target"))

    orders_delta_pct = pct_change(orders_target, orders_prev)
    impressions_delta_pct = pct_change(impressions_target, impressions_prev)
    clicks_delta_pct = pct_change(clicks_target, clicks_prev)
    tocart_delta_pct = pct_change(tocart_target, tocart_prev)

    demand_prev = None
    demand_target = None
    demand_delta_pct = None
    demand_alignment = "not_checked"
    if demand:
        demand_prev = num(demand.get("demand_prev"))
        demand_target = num(demand.get("demand_target"))
        demand_delta_pct = pct_change(demand_target, demand_prev)
        if demand_filter_applied:
            if demand_delta_pct <= -10 and abs(demand_delta_pct - impressions_delta_pct) <= 3:
                demand_alignment = "aligned_drop_ok"
            elif demand_delta_pct > -10 and impressions_delta_pct <= -10:
                demand_alignment = "impressions_drop_without_demand_drop"
            else:
                demand_alignment = "mixed"

    impression_issue = impressions_delta_pct <= -10

    ad_spend_prev = None
    ad_spend_target = None
    ad_spend_delta_pct = None
    ad_event_flag = "no_data"
    if ad_spend:
        ad_spend_prev = num(ad_spend.get("ad_spend_prev"))
        ad_spend_target = num(ad_spend.get("ad_spend_target"))
        ad_spend_delta_pct = pct_change(ad_spend_target, ad_spend_prev)
        ad_event_flag = "ok"
        if ad_spend_target == 0 and ad_spend_prev > 0:
            ad_event_flag = "ads_off"
        elif ad_spend_delta_pct <= -20:
            ad_event_flag = "ads_down"

    stock_available_now = None
    stock_event_flag = "no_data"
    if stock_now:
        stock_available_now = num(stock_now.get("stock_available_now"))
        stock_event_flag = "stockout_now" if stock_available_now <= 0 else "ok"

    # Позиция — косвенный признак аутофстока
    position_target = None
    position_baseline = None
    position_event_flag = "no_data"
    if position:
        position_target = num(position.get("position_target"))
        position_baseline = num(position.get("position_baseline"))
        if position_target > 0 and position_target <= 2 and position_baseline > 5:
            position_event_flag = "position_collapsed"
        elif position_target > 0 and position_baseline > 0:
            position_event_flag = "ok"

    competitor_event_flag = "no_data"
    rating_event_flag = "no_data"
    delivery_event_flag = "no_data"

    # Порядок проверки: 1) остаток → 2) позиция → 3) спрос/показы → 4) воронка
    if stock_event_flag == "stockout_now":
        signal_status = "stockout"
        signal_reason = "Аутофсток: доступный остаток = 0. Пополни запас."
    elif position_event_flag == "position_collapsed":
        signal_status = "stockout_probable"
        signal_reason = (
            f"Позиция слетела ({position_target:.0f} vs ср. {position_baseline:.0f})"
            " → вероятный аутофсток. Проверь остаток на складе."
        )
    elif demand_filter_applied and demand_alignment == "aligned_drop_ok":
        signal_status = "no_incident"
        signal_reason = "Спрос и показы падают синхронно (допуск ±3 п.п.)"
    elif impression_issue:
        if demand_filter_applied and demand_alignment == "impressions_drop_without_demand_drop":
            signal_status = "incident_check_events"
            signal_reason = "Показы упали без сопоставимого падения спроса"
        elif not demand_filter_applied:
            signal_status = "incident_check_events_no_demand_yet"
            signal_reason = "Вчерашний день: спрос с лагом, проверяем негативные события"
        else:
            signal_status = "incident_check_events"
            signal_reason = "Падение показов требует проверки факторов"
    else:
        signal_status = "funnel_check"
        signal_reason = "Показы стабильны, проверяем клики/корзины и конверсию"

    return SignalRow(
        article=str(row.get("article") or ""),
        sku=int(row.get("sku") or 0),
        orders_prev=orders_prev,
        orders_target=orders_target,
        orders_delta_pct=orders_delta_pct,
        impressions_prev=impressions_prev,
        impressions_target=impressions_target,
        impressions_delta_pct=impressions_delta_pct,
        clicks_prev=clicks_prev,
        clicks_target=clicks_target,
        clicks_delta_pct=clicks_delta_pct,
        tocart_prev=tocart_prev,
        tocart_target=tocart_target,
        tocart_delta_pct=tocart_delta_pct,
        demand_prev=demand_prev,
        demand_target=demand_target,
        demand_delta_pct=demand_delta_pct,
        demand_filter_applied=demand_filter_applied,
        demand_alignment=demand_alignment,
        impression_issue=impression_issue,
        ad_spend_prev=ad_spend_prev,
        ad_spend_target=ad_spend_target,
        ad_spend_delta_pct=ad_spend_delta_pct,
        ad_event_flag=ad_event_flag,
        stock_available_now=stock_available_now,
        stock_event_flag=stock_event_flag,
        position_target=position_target,
        position_baseline=position_baseline,
        position_event_flag=position_event_flag,
        competitor_event_flag=competitor_event_flag,
        rating_event_flag=rating_event_flag,
        delivery_event_flag=delivery_event_flag,
        signal_status=signal_status,
        signal_reason=signal_reason,
    )


async def build_report(ctx: ReportContext, output_dir: Path, db_url: str) -> None:
    engine = create_async_engine(db_url)
    async with engine.connect() as conn:
        daily = await fetch_all(
            conn,
            SQL_DAILY_TOTALS_7D,
            trend_start=ctx.trend_start,
            trend_end=ctx.trend_end,
        )
        funnel = await fetch_all(
            conn,
            SQL_FUNNEL_4D,
            funnel_start=ctx.funnel_start,
            funnel_end=ctx.funnel_end,
        )
        top_drops = await fetch_all(
            conn,
            SQL_TOP_DROPS,
            trend_start=ctx.trend_start,
            trend_end=ctx.trend_end,
            target_day=ctx.target_day,
            compare_day=ctx.compare_day,
        )

        top_skus = [int(r["sku"]) for r in top_drops[:6] if r.get("sku") is not None]
        trend_rows: list[dict] = []
        demand_rows: list[dict] = []
        ad_spend_rows: list[dict] = []
        stock_rows: list[dict] = []
        position_rows: list[dict] = []
        if top_skus:
            sku_values_sql = ",".join(f"({sku})" for sku in top_skus)
            trend_sql = SQL_TOP_DROPS_TREND_TEMPLATE.replace("__SKU_VALUES__", sku_values_sql)
            trend_rows = await fetch_all(
                conn,
                trend_sql,
                trend_start=ctx.trend_start,
                trend_end=ctx.trend_end,
            )
            demand_sql = SQL_DEMAND_COMPARE_TEMPLATE.replace("__SKU_VALUES__", sku_values_sql)
            demand_rows = await fetch_all(
                conn,
                demand_sql,
                target_day=ctx.target_day,
                compare_day=ctx.compare_day,
            )
            ad_spend_sql = SQL_AD_SPEND_COMPARE_TEMPLATE.replace("__SKU_VALUES__", sku_values_sql)
            ad_spend_rows = await fetch_all(
                conn,
                ad_spend_sql,
                target_day=ctx.target_day,
                compare_day=ctx.compare_day,
            )
            stock_sql = SQL_STOCK_NOW_TEMPLATE.replace("__SKU_VALUES__", sku_values_sql)
            stock_rows = await fetch_all(conn, stock_sql)
            position_sql = SQL_POSITION_COMPARE_TEMPLATE.replace("__SKU_VALUES__", sku_values_sql)
            position_rows = await fetch_all(
                conn,
                position_sql,
                target_day=ctx.target_day,
                compare_day=ctx.compare_day,
                baseline_start=ctx.trend_start,
            )

    await engine.dispose()

    for row in daily:
        row["conv_tocart_pct"] = round(pct(num(row["hits_tocart"]), num(row["hits_view"])), 2)
        row["conv_order_from_tocart_pct"] = round(
            pct(num(row["ordered_units"]), num(row["hits_tocart"])), 2
        )

    for row in funnel:
        row["conv_tocart_pct"] = round(pct(num(row["hits_tocart"]), num(row["hits_view"])), 2)
        row["conv_order_from_tocart_pct"] = round(
            pct(num(row["ordered_units"]), num(row["hits_tocart"])), 2
        )

    write_csv(
        output_dir / "daily_totals_7d.csv",
        daily,
        [
            "day",
            "ordered_units",
            "delivered_units",
            "returns_units",
            "cancellations",
            "hits_view",
            "session_view",
            "hits_tocart",
            "conv_tocart_pct",
            "conv_order_from_tocart_pct",
        ],
    )
    write_csv(
        output_dir / "funnel_4d.csv",
        funnel,
        [
            "day",
            "hits_view",
            "session_view",
            "hits_tocart",
            "ordered_units",
            "delivered_units",
            "returns_units",
            "cancellations",
            "conv_tocart_pct",
            "conv_order_from_tocart_pct",
        ],
    )
    write_csv(
        output_dir / "top_drop_skus.csv",
        top_drops,
        [
            "article",
            "sku",
            "orders_prev",
            "orders_target",
            "delta_orders",
            "contribution_pct",
            "hits_prev",
            "hits_target",
            "tocart_prev",
            "tocart_target",
            "sessions_prev",
            "sessions_target",
            "cancellations_target",
            "returns_target",
        ],
    )
    if trend_rows:
        write_csv(
            output_dir / "top_drop_skus_trend_7d.csv",
            trend_rows,
            [
                "day",
                "article",
                "sku",
                "revenue",
                "ordered_units",
                "hits_view",
                "hits_tocart",
                "session_view",
            ],
        )

    today = date.today()
    is_yesterday_target = ctx.target_day == (today - timedelta(days=1))
    demand_filter_applied = not is_yesterday_target
    demand_by_sku = {int(r["sku"]): r for r in demand_rows if r.get("sku") is not None}
    ad_spend_by_sku = {int(r["sku"]): r for r in ad_spend_rows if r.get("sku") is not None}
    stock_by_sku = {int(r["sku"]): r for r in stock_rows if r.get("sku") is not None}
    position_by_sku = {int(r["sku"]): r for r in position_rows if r.get("sku") is not None}
    signal_rows = [
        classify_signal(
            row=r,
            demand=demand_by_sku.get(int(r["sku"])),
            demand_filter_applied=demand_filter_applied,
            ad_spend=ad_spend_by_sku.get(int(r["sku"])),
            stock_now=stock_by_sku.get(int(r["sku"])),
            position=position_by_sku.get(int(r["sku"])),
        )
        for r in top_drops
    ]
    write_csv(
        output_dir / "sales_drop_signals.csv",
        [s.__dict__ for s in signal_rows],
        [
            "article",
            "sku",
            "orders_prev",
            "orders_target",
            "orders_delta_pct",
            "impressions_prev",
            "impressions_target",
            "impressions_delta_pct",
            "clicks_prev",
            "clicks_target",
            "clicks_delta_pct",
            "tocart_prev",
            "tocart_target",
            "tocart_delta_pct",
            "demand_prev",
            "demand_target",
            "demand_delta_pct",
            "demand_filter_applied",
            "demand_alignment",
            "impression_issue",
            "ad_spend_prev",
            "ad_spend_target",
            "ad_spend_delta_pct",
            "ad_event_flag",
            "stock_available_now",
            "stock_event_flag",
            "position_target",
            "position_baseline",
            "position_event_flag",
            "competitor_event_flag",
            "rating_event_flag",
            "delivery_event_flag",
            "signal_status",
            "signal_reason",
        ],
    )

    daily_by_day = {r["day"]: r for r in daily}
    target = daily_by_day.get(ctx.target_day)
    prev = daily_by_day.get(ctx.compare_day)

    summary_lines = [
        "# Sales Drop Diagnostic Draft",
        "",
        f"Target day: {ctx.target_day}",
        f"Comparison day: {ctx.compare_day}",
        f"Trend window (7d): {ctx.trend_start} .. {ctx.trend_end}",
        f"Funnel window (4d): {ctx.funnel_start} .. {ctx.funnel_end}",
        "",
    ]

    if target and prev:
        rev_target = num(target["revenue"])
        rev_prev = num(prev["revenue"])
        rev_delta_pct = pct_change(rev_target, rev_prev)
        trigger_active = rev_delta_pct < 0
        orders_target = num(target["ordered_units"])
        orders_prev = num(prev["ordered_units"])
        summary_lines.extend(
            [
                "## Key movement",
                "",
                (
                    f"Revenue trigger: {'ACTIVE' if trigger_active else 'inactive'} "
                    f"({rev_delta_pct:+.2f}%)"
                ),
                (
                    f"Orders: {orders_target:,.0f} vs {orders_prev:,.0f} "
                    f"({orders_target - orders_prev:+,.0f}; {pct(orders_target - orders_prev, orders_prev):+.2f}%)"
                ),
                (
                    f"Impressions: {num(target['hits_view']):,.0f} vs {num(prev['hits_view']):,.0f} "
                    f"({pct_change(num(target['hits_view']), num(prev['hits_view'])):+.2f}%)"
                ),
                (
                    f"Clicks (session_view): {num(target['session_view']):,.0f} vs {num(prev['session_view']):,.0f} "
                    f"({pct_change(num(target['session_view']), num(prev['session_view'])):+.2f}%)"
                ),
                (
                    f"ToCart: {num(target['hits_tocart']):,.0f} vs {num(prev['hits_tocart']):,.0f} "
                    f"({pct_change(num(target['hits_tocart']), num(prev['hits_tocart'])):+.2f}%)"
                ),
                (
                    f"ToCart conversion: {pct(num(target['hits_tocart']), num(target['hits_view'])):.2f}% "
                    f"vs {pct(num(prev['hits_tocart']), num(prev['hits_view'])):.2f}%"
                ),
                (
                    f"Order/ToCart conversion: {pct(num(target['ordered_units']), num(target['hits_tocart'])):.2f}% "
                    f"vs {pct(num(prev['ordered_units']), num(prev['hits_tocart'])):.2f}%"
                ),
                "",
            ]
        )

    summary_lines.extend(
        [
            "## Demand Filter Rule",
            "",
            (
                "Demand filter: skipped for yesterday (data lag), applied for older dates."
                if is_yesterday_target
                else "Demand filter: applied."
            ),
            "Rule: if demand <= -10% and impressions change matches demand within ±3 p.p., no incident.",
            "",
            "## Top SKU sales-drop signals",
            "",
        ]
    )
    if signal_rows:
        for s in signal_rows[:6]:
            demand_part = (
                f"demand {s.demand_prev:.0f}->{s.demand_target:.0f} ({(s.demand_delta_pct or 0):+.2f}%)"
                if s.demand_prev is not None and s.demand_target is not None and s.demand_delta_pct is not None
                else "demand n/a"
            )
            summary_lines.append(
                (
                    f"- {s.article} (sku {s.sku}): "
                    f"orders {s.orders_prev:.0f}->{s.orders_target:.0f} ({s.orders_delta_pct:+.2f}%), "
                    f"impressions {s.impressions_prev:.0f}->{s.impressions_target:.0f} ({s.impressions_delta_pct:+.2f}%), "
                    f"{demand_part}, status={s.signal_status}, "
                    f"ads={s.ad_event_flag}, stock={s.stock_event_flag}"
                )
            )
    else:
        summary_lines.append("- No negative sales SKU delta found for this day pair.")

    summary_lines.extend(
        [
            "",
            "## Produced files",
            "",
            "- daily_totals_7d.csv",
            "- funnel_4d.csv",
            "- top_drop_skus.csv",
            "- top_drop_skus_trend_7d.csv (if top SKU list is not empty)",
            "- sales_drop_signals.csv",
        ]
    )

    (output_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a compact sales drop diagnostic draft")
    parser.add_argument(
        "--target-day",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Target day in YYYY-MM-DD format (default: yesterday)",
    )
    parser.add_argument(
        "--out-dir",
        default="exports/sales_drop_draft",
        help="Output directory",
    )
    return parser.parse_args()


def build_context(target_day: date) -> ReportContext:
    compare_day = target_day - timedelta(days=1)
    trend_start = target_day - timedelta(days=6)
    trend_end = target_day
    funnel_start = target_day - timedelta(days=2)
    funnel_end = target_day + timedelta(days=1)
    return ReportContext(
        target_day=target_day,
        compare_day=compare_day,
        trend_start=trend_start,
        trend_end=trend_end,
        funnel_start=funnel_start,
        funnel_end=funnel_end,
    )


def main() -> None:
    args = parse_args()
    target_day = datetime.strptime(args.target_day, "%Y-%m-%d").date()
    ctx = build_context(target_day)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.out_dir) / f"{target_day}_{stamp}"
    db_url = load_database_url(Path(".env"))
    asyncio.run(build_report(ctx, output_dir, db_url))
    print(output_dir)


if __name__ == "__main__":
    main()
