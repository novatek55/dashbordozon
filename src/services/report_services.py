"""Service layer for finance reports.

This module intentionally keeps report-building logic independent from web
handlers so the same calculations can be reused across HTTP entrypoints.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from src.config import settings
from src.dashboard.constants import (
    FINANCE_REPORT_ROWS,
    MSK,
    PLAN_BASE_VALUES,
)
from src.dashboard.helpers import (
    as_float,
    build_kpi_summary,
    init_row,
    month_bounds,
    month_start_msk,
    normalize_offer_id,
    recalculate_row_total,
    safe_divide,
    scale_plan_value,
    set_row_from_formula,
    to_asyncpg_dsn,
)
from src.dashboard.routes.finance import (
    build_rows_map_for_month,
    ensure_finance_report_tables,
)


SALES_STATUSES = [
    "delivered",
    "delivering",
    "awaiting_deliver",
    "awaiting_packaging",
    "driver_pickup",
    "доставлен",
    "доставляется",
    "ожидает в пвз",
    "у водителя",
    "ожидает отгрузки",
    "ожидает сборки",
]

# Строки, которые участвуют в расчётах, но не должны отображаться:
# - piece_acceptance / zone_sorting / excess_processing — дублируют fbo_cargo_processing
# - pickup_processing — нет маппинга, всегда 0
FINANCE_RENDER_SKIP = {
    "revenue_sales",
    "returns_total",
    "all_expenses",
    "piece_acceptance",
    "zone_sorting",
    "excess_processing",
    "pickup_processing",
    "sales_discount_points",
    "sales_partner_programs",
    "returns_discount_points",
    "returns_partner_programs",
}

FORECAST_EXPENSE_KEYS = [
    row["key"]
    for row in FINANCE_REPORT_ROWS
    if row["kind"] == "value"
    and row["key"] not in {"revenue_plan", "ordered_units", "returned_units", "revenue", "returns_revenue"}
]


def finance_report_notes() -> List[str]:
    return [
        "Источник Finance Report: transactions + fallback на posting_transaction_snapshots по posting_number.",
        "В 'заказано' попадают только posting_number со статусом 'Доставлен' в fact_orders.",
        "posting_number, найденные в returns / returns_fbo, исключаются из продаж.",
        "Количество берётся из fact_order_items, а если их нет, то из items в архиве Finance API.",
        "Себестоимость считается как Σ(количество по артикулу * себестоимость за ед.) из загруженного Excel-справочника.",
        "Compensation rows also use report_compensation_items loaded from /v1/finance/compensation and /v1/finance/decompensation.",
    ]


def finance_report_v2_notes() -> List[str]:
    return [
        "V2: будущая выручка строится от текущих остатков и средней скорости продаж из отчёта по остаткам.",
        "Для прогноза берётся средняя цена продажи по артикулу за последние 30 дней; если продаж не было, выручка по артикулу не прогнозируется.",
        "Расходные строки раскладываются по средним долям последних 30 дней относительно чистой выручки.",
        "Будущие дни в таблице помечены тёмно-серым.",
    ]


def render_finance_rows(
    rows_map: Dict[str, Dict[str, Any]],
    days: List[str],
) -> List[Dict[str, Any]]:
    rendered_rows: List[Dict[str, Any]] = []
    for row_meta in FINANCE_REPORT_ROWS:
        if row_meta["key"] in FINANCE_RENDER_SKIP:
            continue
        kind = row_meta["kind"]
        if kind in {"section", "spacer"}:
            rendered_rows.append(
                {
                    "key": row_meta["key"],
                    "label": row_meta["label"],
                    "kind": kind,
                    "format": row_meta.get("format", "number"),
                    "total": None,
                    "daily": [],
                }
            )
            continue

        row = rows_map[row_meta["key"]]
        rendered_rows.append(
            {
                "key": row["key"],
                "label": row["label"],
                "kind": kind,
                "format": row["format"],
                "total": row["total"],
                "daily": [row["daily"][day] for day in days],
            }
        )
    return rendered_rows


def month_values_in_range(start_day: date, end_day: date) -> List[str]:
    values: List[str] = []
    cursor = date(start_day.year, start_day.month, 1)
    end_month = date(end_day.year, end_day.month, 1)
    while cursor <= end_month:
        values.append(f"{cursor.year:04d}-{cursor.month:02d}")
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return values


def current_forecast_days(month_value: str, days: List[str]) -> List[str]:
    if not days:
        return []
    month_date = month_start_msk(month_value).date()
    today = datetime.now(MSK).date()
    month_tuple = (month_date.year, month_date.month)
    today_tuple = (today.year, today.month)
    if month_tuple < today_tuple:
        return []
    if month_tuple > today_tuple:
        return list(days)
    today_str = today.strftime("%Y-%m-%d")
    return [day for day in days if day > today_str]


def visible_report_days(month_value: str, days: List[str]) -> List[str]:
    if not days:
        return []
    month_date = month_start_msk(month_value).date()
    today = datetime.now(MSK).date()
    if (month_date.year, month_date.month) != (today.year, today.month):
        return list(days)
    today_str = today.strftime("%Y-%m-%d")
    return [day for day in days if day < today_str]


def visible_report_days_v2(month_value: str, days: List[str]) -> List[str]:
    if not days:
        return []
    month_date = month_start_msk(month_value).date()
    today = datetime.now(MSK).date()
    if (month_date.year, month_date.month) != (today.year, today.month):
        return list(days)
    today_str = today.strftime("%Y-%m-%d")
    return [day for day in days if day != today_str]


def trim_rows_map_to_days(
    rows_map: Dict[str, Dict[str, Any]],
    source_days: List[str],
    report_days: List[str],
) -> Dict[str, Dict[str, Any]]:
    if report_days == source_days:
        return rows_map

    trimmed_rows_map = copy.deepcopy(rows_map)
    report_day_set = set(report_days)
    for row in trimmed_rows_map.values():
        row["daily"] = {day: row["daily"][day] for day in report_days}
        if row["key"] in {"revenue_cumulative", "gross_profit_cumulative", "revenue_plan", "gross_profit_plan"}:
            row["total"] = None
        else:
            row["total"] = float(sum(row["daily"][day] for day in report_days))
    return trimmed_rows_map


async def aggregate_recent_30d_totals(
    conn: asyncpg.Connection,
    end_day: date,
) -> Dict[str, float]:
    start_day = end_day - timedelta(days=29)
    totals: Dict[str, float] = {
        row["key"]: 0.0
        for row in FINANCE_REPORT_ROWS
        if row["kind"] not in {"section", "spacer"}
    }
    for month_value in month_values_in_range(start_day, end_day):
        month_rows_map, _ = await build_rows_map_for_month(conn, month_value)
        cursor = max(start_day, month_start_msk(month_value).date())
        month_start = month_start_msk(month_value).date()
        if month_start.month == 12:
            month_end = date(month_start.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)
        final_day = min(end_day, month_end)
        while cursor <= final_day:
            day_key = cursor.strftime("%Y-%m-%d")
            for key in totals:
                totals[key] += float(month_rows_map[key]["daily"].get(day_key, 0.0) or 0.0)
            cursor += timedelta(days=1)
    return totals


async def load_stock_forecast_inputs(conn: asyncpg.Connection) -> Dict[str, Dict[str, Any]]:
    analytics_rows = await conn.fetch(
        """
        SELECT offer_id, available_stock_count
        FROM analytics_stocks
        """
    )
    fbs_rows = await conn.fetch(
        """
        SELECT offer_id, present
        FROM fbs_warehouse_stocks
        """
    )
    sales_rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g') AS offer_id,
            sum(coalesce(oi.quantity, 0))::float8 AS quantity_28d
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE o.created_at >= now() - interval '28 days'
          AND coalesce(lower(o.status), '') = any($1::text[])
        GROUP BY regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g')
        """,
        SALES_STATUSES,
    )
    price_rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g') AS offer_id,
            sum(coalesce(oi.quantity, 0) * coalesce(oi.price, 0))::float8 AS revenue_30d,
            sum(coalesce(oi.quantity, 0))::float8 AS quantity_30d
        FROM fact_order_items oi
        JOIN fact_orders o ON o.order_id = oi.order_id
        WHERE o.created_at >= now() - interval '30 days'
          AND coalesce(lower(o.status), '') = any($1::text[])
        GROUP BY regexp_replace(lower(trim(both '''' from coalesce(oi.offer_id, ''))), '\\s+', ' ', 'g')
        """,
        SALES_STATUSES,
    )
    fallback_price_rows = await conn.fetch(
        """
        SELECT
            regexp_replace(lower(trim(both '''' from coalesce(offer_id, ''))), '\\s+', ' ', 'g') AS offer_id,
            max(price_current)::float8 AS price_current
        FROM report_products_items
        WHERE price_current IS NOT NULL
        GROUP BY regexp_replace(lower(trim(both '''' from coalesce(offer_id, ''))), '\\s+', ' ', 'g')
        """
    )

    article_map: Dict[str, Dict[str, Any]] = {}

    def get_article(article_key: str, display_offer_id: Optional[str] = None) -> Dict[str, Any]:
        article = article_map.get(article_key)
        if article is None:
            article = {
                "offer_id": article_key,
                "display_offer_id": display_offer_id or article_key,
                "stock": 0.0,
                "avg_daily_sales": 0.0,
                "avg_price": 0.0,
            }
            article_map[article_key] = article
        elif display_offer_id and (not article.get("display_offer_id") or article["display_offer_id"] == article_key):
            article["display_offer_id"] = display_offer_id
        return article

    # SQL-запросы выше уже понижают offer_id через lower(...), поэтому здесь
    # тоже приводим ключ к нижнему регистру — иначе один и тот же артикул
    # с разным регистром раздваивается на две записи.
    def _norm_key(value: Any) -> str:
        key = normalize_offer_id(value)
        return key.lower() if key else ""

    for row in analytics_rows:
        article_key = _norm_key(row["offer_id"])
        if not article_key:
            continue
        get_article(article_key, row["offer_id"])["stock"] += float(row["available_stock_count"] or 0)

    for row in fbs_rows:
        article_key = _norm_key(row["offer_id"])
        if not article_key:
            continue
        get_article(article_key, row["offer_id"])["stock"] += float(row["present"] or 0)

    for row in sales_rows:
        article_key = _norm_key(row["offer_id"])
        if not article_key:
            continue
        get_article(article_key, row["offer_id"])["avg_daily_sales"] = float(row["quantity_28d"] or 0.0) / 28.0

    for row in price_rows:
        article_key = _norm_key(row["offer_id"])
        if not article_key:
            continue
        quantity = float(row["quantity_30d"] or 0.0)
        if quantity > 0:
            get_article(article_key, row["offer_id"])["avg_price"] = float(row["revenue_30d"] or 0.0) / quantity

    for row in fallback_price_rows:
        article_key = _norm_key(row["offer_id"])
        if not article_key:
            continue
        article = get_article(article_key, row["offer_id"])
        if article["avg_price"] <= 0:
            article["avg_price"] = float(row["price_current"] or 0.0)

    return article_map


async def load_fact_revenue_by_article_day(
    conn: asyncpg.Connection,
    month_value: str,
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Возвращает {article_key: {"display": offer_id, "daily_revenue": {day: rub}, "daily_units": {day: qty}}} за месяц."""
    month_start_dt, month_end_dt, month_days = month_bounds(month_value)
    # Расширяем диапазон на сутки с обеих сторон, чтобы захватить пограничные UTC/MSK
    query_start = month_start_dt - timedelta(days=1)
    query_end = month_end_dt + timedelta(days=1)
    valid_days = set(month_days)
    rows = await conn.fetch(
        """
        SELECT
            oi.offer_id AS offer_id,
            oi.quantity AS quantity,
            oi.price AS price,
            o.created_at AS created_at
        FROM fact_order_items oi
        JOIN fact_orders o ON o.posting_number = oi.posting_number
        WHERE o.created_at >= $1
          AND o.created_at < $2
          AND coalesce(lower(o.status), '') = any($3::text[])
        """,
        query_start,
        query_end,
        SALES_STATUSES,
    )
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        offer_id_raw = row["offer_id"]
        if not offer_id_raw:
            continue
        article_key = normalize_offer_id(offer_id_raw)
        if not article_key:
            continue
        created_at = row["created_at"]
        if created_at is None:
            continue
        day = created_at.astimezone(MSK).strftime("%Y-%m-%d")
        if day not in valid_days:
            continue
        qty = float(row["quantity"] or 0.0)
        price = float(row["price"] or 0.0)
        revenue = qty * price
        bucket = result.setdefault(
            article_key,
            {"display": offer_id_raw, "daily_revenue": {}, "daily_units": {}},
        )
        bucket["display"] = offer_id_raw
        bucket["daily_revenue"][day] = bucket["daily_revenue"].get(day, 0.0) + revenue
        bucket["daily_units"][day] = bucket["daily_units"].get(day, 0.0) + qty
    return result


async def build_stock_based_daily_forecast(
    conn: asyncpg.Connection,
    forecast_days: List[str],
) -> Dict[str, Any]:
    units_by_day = {day: 0.0 for day in forecast_days}
    revenue_by_day = {day: 0.0 for day in forecast_days}
    if not forecast_days:
        return {"units": units_by_day, "revenue": revenue_by_day, "articles": []}

    article_map = await load_stock_forecast_inputs(conn)
    article_details: List[Dict[str, Any]] = []
    for article in article_map.values():
        remaining_stock = max(0.0, float(article.get("stock") or 0.0))
        avg_daily_sales = max(0.0, float(article.get("avg_daily_sales") or 0.0))
        avg_price = max(0.0, float(article.get("avg_price") or 0.0))
        if remaining_stock <= 0 or avg_daily_sales <= 0 or avg_price <= 0:
            continue
        article_units_by_day = {day: 0.0 for day in forecast_days}
        article_revenue_by_day = {day: 0.0 for day in forecast_days}
        depletion_day: Optional[str] = None
        for day in forecast_days:
            if remaining_stock <= 0:
                break
            sold_qty = min(avg_daily_sales, remaining_stock)
            units_by_day[day] += sold_qty
            revenue_by_day[day] += sold_qty * avg_price
            article_units_by_day[day] = sold_qty
            article_revenue_by_day[day] = sold_qty * avg_price
            remaining_stock -= sold_qty
            if remaining_stock <= 0 and depletion_day is None:
                depletion_day = day

        article_total_revenue = float(sum(article_revenue_by_day.values()))
        article_total_units = float(sum(article_units_by_day.values()))
        if article_total_revenue <= 0:
            continue
        article_details.append(
            {
                "offer_id": article.get("display_offer_id") or article.get("offer_id"),
                "stock": float(article.get("stock") or 0.0),
                "avg_daily_sales": avg_daily_sales,
                "avg_price": avg_price,
                "days_left": (float(article.get("stock") or 0.0) / avg_daily_sales) if avg_daily_sales > 0 else None,
                "depletion_day": depletion_day,
                "daily_units": article_units_by_day,
                "daily_revenue": article_revenue_by_day,
                "forecast_units_total": article_total_units,
                "forecast_revenue_total": article_total_revenue,
            }
        )

    article_details.sort(key=lambda item: (-float(item["forecast_revenue_total"] or 0.0), str(item["offer_id"] or "")))
    return {"units": units_by_day, "revenue": revenue_by_day, "articles": article_details}


def build_revenue_breakdown(
    forecast_daily: Dict[str, Any],
    report_days: List[str],
    recent_totals: Dict[str, float],
    fact_by_article: Optional[Dict[str, Dict[str, Any]]] = None,
    forecast_days: Optional[List[str]] = None,
) -> Dict[str, Any]:
    ordered_total_30d = max(0.0, float(recent_totals.get("ordered_units", 0.0) or 0.0))
    revenue_total_30d = max(0.0, float(recent_totals.get("revenue", 0.0) or 0.0))
    returned_units_ratio = (float(recent_totals.get("returned_units", 0.0) or 0.0) / ordered_total_30d) if ordered_total_30d > 0 else 0.0
    returns_revenue_ratio = (float(recent_totals.get("returns_revenue", 0.0) or 0.0) / revenue_total_30d) if revenue_total_30d > 0 else 0.0
    details: List[Dict[str, Any]] = []

    fact_by_article = fact_by_article or {}
    forecast_days_set = set(forecast_days or [])

    # Объединяем артикулы из факта и из прогноза по нормализованному ключу
    forecast_by_key: Dict[str, Dict[str, Any]] = {}
    for article in forecast_daily.get("articles", []):
        key = normalize_offer_id(article.get("offer_id") or "")
        if key:
            forecast_by_key[key] = article

    all_keys = set(fact_by_article.keys()) | set(forecast_by_key.keys())

    for key in all_keys:
        fact_entry = fact_by_article.get(key) or {}
        forecast_entry = forecast_by_key.get(key) or {}
        fact_revenue = fact_entry.get("daily_revenue") or {}
        fact_units = fact_entry.get("daily_units") or {}
        fc_revenue = forecast_entry.get("daily_revenue") or {}
        fc_units = forecast_entry.get("daily_units") or {}

        net_daily: List[float] = []
        gross_daily_rendered: List[float] = []
        units_daily_rendered: List[float] = []
        for day in report_days:
            if day in forecast_days_set:
                gross_value = float(fc_revenue.get(day, 0.0) or 0.0)
                units_value = float(fc_units.get(day, 0.0) or 0.0)
            else:
                gross_value = float(fact_revenue.get(day, 0.0) or 0.0)
                units_value = float(fact_units.get(day, 0.0) or 0.0)
            gross_daily_rendered.append(gross_value)
            net_daily.append(max(0.0, gross_value * (1.0 - returns_revenue_ratio)))
            units_daily_rendered.append(units_value)

        net_total = float(sum(net_daily))
        if net_total <= 0:
            continue
        display_offer = (
            forecast_entry.get("offer_id")
            or fact_entry.get("display")
            or key
        )
        details.append(
            {
                "offer_id": display_offer,
                "label": display_offer,
                "stock": float(forecast_entry.get("stock") or 0.0),
                "avg_daily_sales": float(forecast_entry.get("avg_daily_sales") or 0.0),
                "avg_price": float(forecast_entry.get("avg_price") or 0.0),
                "days_left": forecast_entry.get("days_left"),
                "depletion_day": forecast_entry.get("depletion_day"),
                "forecast_units_total": float(forecast_entry.get("forecast_units_total") or 0.0),
                "gross_total": float(sum(gross_daily_rendered)),
                "net_total": net_total,
                "returned_units_ratio": returned_units_ratio,
                "returns_revenue_ratio": returns_revenue_ratio,
                "daily": net_daily,
                "daily_gross": gross_daily_rendered,
                "daily_units": units_daily_rendered,
            }
        )

    details.sort(key=lambda item: (-float(item["net_total"] or 0.0), str(item["offer_id"] or "")))
    return {
        "articles": details,
        "returns_revenue_ratio": returns_revenue_ratio,
        "returned_units_ratio": returned_units_ratio,
    }


def rebuild_finance_derived_rows(rows_map: Dict[str, Dict[str, Any]], days: List[str]) -> None:
    for row_key in rows_map:
        if rows_map[row_key]["kind"] == "value":
            recalculate_row_total(rows_map[row_key], days)

    set_row_from_formula(rows_map, "sales_total", days, lambda day: rows_map["revenue"]["daily"][day] - rows_map["returns_revenue"]["daily"][day])
    set_row_from_formula(rows_map, "returns_total", days, lambda day: rows_map["returns_revenue"]["daily"][day])
    set_row_from_formula(rows_map, "revenue_sales", days, lambda day: rows_map["sales_total"]["daily"][day])
    set_row_from_formula(rows_map, "delivery_services_total", days, lambda day: rows_map["courier_departure"]["daily"][day] + rows_map["dropoff_processing"]["daily"][day] + rows_map["logistics"]["daily"][day] + rows_map["reverse_logistics"]["daily"][day] + rows_map["pickup_courier_delivery"]["daily"][day] + rows_map["pickup_processing"]["daily"][day])
    set_row_from_formula(rows_map, "agent_services_total", days, lambda day: rows_map["star_products"]["daily"][day] + rows_map["delivery_to_pickup"]["daily"][day] + rows_map["partner_returns_processing"]["daily"][day] + rows_map["acquiring"]["daily"][day] + rows_map["partner_dropoff_processing"]["daily"][day] + rows_map["partner_packaging"]["daily"][day] + rows_map["temporary_partner_storage"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_cargo_processing", days, lambda day: rows_map["piece_acceptance"]["daily"][day] + rows_map["zone_sorting"]["daily"][day] + rows_map["excess_processing"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_acceptance_services", days, lambda day: rows_map["fbo_cargo_processing"]["daily"][day] + rows_map["fbo_booking_slot_staff"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_delivery_to_warehouse", days, lambda day: rows_map["cross_docking"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_storage_services", days, lambda day: rows_map["warehouse_placement"]["daily"][day] + rows_map["valid_preparation"]["daily"][day] + rows_map["ozon_delivery_to_pvz"]["daily"][day])
    set_row_from_formula(rows_map, "fbo_services_total", days, lambda day: rows_map["fbo_acceptance_services"]["daily"][day] + rows_map["fbo_delivery_to_warehouse"]["daily"][day] + rows_map["fbo_storage_services"]["daily"][day])
    set_row_from_formula(
        rows_map,
        "promotion_total",
        days,
        lambda day: rows_map["premium_plus_subscription"]["daily"][day]
        + rows_map["pay_per_click"]["daily"][day]
        + rows_map["review_points"]["daily"][day]
        + rows_map["review_pin"]["daily"][day]
        + rows_map["accelerated_reviews"]["daily"][day],
    )
    set_row_from_formula(rows_map, "penalties_total", days, lambda day: rows_map["penalty_non_recommended_slot"]["daily"][day])
    set_row_from_formula(rows_map, "other_services_misc", days, lambda day: rows_map["utilization"]["daily"][day] + rows_map["packaging_materials"]["daily"][day] + rows_map["operational_errors"]["daily"][day] + rows_map["temporary_sc_storage"]["daily"][day])
    set_row_from_formula(
        rows_map,
        "other_services",
        days,
        lambda day: rows_map["penalties_total"]["daily"][day] + rows_map["other_services_misc"]["daily"][day],
    )
    set_row_from_formula(rows_map, "ozon_fee_total", days, lambda day: rows_map["sale_commission"]["daily"][day] - rows_map["return_commission"]["daily"][day])
    set_row_from_formula(
        rows_map,
        "marketplace_expenses",
        days,
        lambda day: rows_map["ozon_fee_total"]["daily"][day]
        + rows_map["delivery_services_total"]["daily"][day]
        + rows_map["agent_services_total"]["daily"][day]
        + rows_map["fbo_services_total"]["daily"][day]
        + rows_map["promotion_total"]["daily"][day]
        + rows_map["other_services"]["daily"][day],
    )
    set_row_from_formula(
        rows_map,
        "all_expenses",
        days,
        lambda day: rows_map["returns_revenue"]["daily"][day]
        + rows_map["ozon_fee_total"]["daily"][day]
        + rows_map["delivery_services_total"]["daily"][day]
        + rows_map["agent_services_total"]["daily"][day]
        + rows_map["fbo_services_total"]["daily"][day]
        + rows_map["promotion_total"]["daily"][day]
        + rows_map["other_services"]["daily"][day],
    )
    set_row_from_formula(rows_map, "marketplace_expenses_pct", days, lambda day: safe_divide(rows_map["marketplace_expenses"]["daily"][day], rows_map["revenue_sales"]["daily"][day]))
    marketing_daily = {
        day: (
            rows_map["pay_per_click"]["daily"][day]
            + rows_map["review_points"]["daily"][day]
            + rows_map["premium_plus_subscription"]["daily"][day]
            + rows_map["review_pin"]["daily"][day]
            + rows_map["accelerated_reviews"]["daily"][day]
        )
        for day in days
    }
    set_row_from_formula(rows_map, "marketing_pct", days, lambda day: safe_divide(marketing_daily[day], rows_map["revenue_sales"]["daily"][day]))
    set_row_from_formula(
        rows_map,
        "accrued",
        days,
        lambda day: rows_map["revenue_sales"]["daily"][day]
        - rows_map["marketplace_expenses"]["daily"][day]
        - rows_map["compensations"]["daily"][day]
        - rows_map["other_accrual_adjustments"]["daily"][day],
    )
    set_row_from_formula(rows_map, "gross_profit", days, lambda day: rows_map["accrued"]["daily"][day] - rows_map["material_cost"]["daily"][day])
    set_row_from_formula(rows_map, "gross_profit_pct_oz", days, lambda day: safe_divide(rows_map["gross_profit"]["daily"][day], rows_map["revenue_sales"]["daily"][day]))
    set_row_from_formula(rows_map, "gross_profit_pct_accrued", days, lambda day: safe_divide(rows_map["gross_profit"]["daily"][day], rows_map["accrued"]["daily"][day]))

    cumulative_revenue = 0.0
    cumulative_gross = 0.0
    for day in days:
        cumulative_revenue += rows_map["revenue_sales"]["daily"][day]
        cumulative_gross += rows_map["gross_profit"]["daily"][day]
        rows_map["revenue_cumulative"]["daily"][day] = cumulative_revenue
        rows_map["gross_profit_cumulative"]["daily"][day] = cumulative_gross
    rows_map["revenue_cumulative"]["total"] = None
    rows_map["gross_profit_cumulative"]["total"] = None
    rows_map["marketplace_expenses_pct"]["total"] = safe_divide(rows_map["marketplace_expenses"]["total"], rows_map["revenue_sales"]["total"])
    rows_map["marketing_pct"]["total"] = safe_divide(sum(marketing_daily[day] for day in days), rows_map["revenue_sales"]["total"])
    rows_map["gross_profit_pct_oz"]["total"] = safe_divide(rows_map["gross_profit"]["total"], rows_map["revenue_sales"]["total"])
    rows_map["gross_profit_pct_accrued"]["total"] = safe_divide(rows_map["gross_profit"]["total"], rows_map["accrued"]["total"])

    revenue_plan_total = float(rows_map["revenue_plan"]["daily"][days[-1]]) if days else 0.0
    gross_profit_plan_total = scale_plan_value(PLAN_BASE_VALUES["gross_profit"], revenue_plan_total)
    daily_gross_plan = gross_profit_plan_total / len(days) if days else 0.0
    cumulative_gross_plan = 0.0
    for day in days:
        cumulative_gross_plan += daily_gross_plan
        rows_map["gross_profit_plan"]["daily"][day] = cumulative_gross_plan
    rows_map["gross_profit_plan"]["total"] = None


def build_rows_map_skeleton(days: List[str]) -> Dict[str, Dict[str, Any]]:
    return {
        row_meta["key"]: init_row(days, row_meta["key"])
        for row_meta in FINANCE_REPORT_ROWS
        if row_meta["kind"] not in {"section", "spacer"}
    }


def apply_finance_report_v2_forecast(
    rows_map: Dict[str, Dict[str, Any]],
    days: List[str],
    forecast_days: List[str],
    recent_totals: Dict[str, float],
    forecast_daily: Dict[str, Dict[str, float]],
) -> Dict[str, Dict[str, Any]]:
    forecast_rows_map = copy.deepcopy(rows_map)
    if not forecast_days:
        return forecast_rows_map

    ordered_total_30d = max(0.0, float(recent_totals.get("ordered_units", 0.0) or 0.0))
    revenue_total_30d = max(0.0, float(recent_totals.get("revenue", 0.0) or 0.0))
    net_revenue_total_30d = max(0.0, float(recent_totals.get("revenue_sales", 0.0) or 0.0))
    returned_units_ratio = (float(recent_totals.get("returned_units", 0.0) or 0.0) / ordered_total_30d) if ordered_total_30d > 0 else 0.0
    returns_revenue_ratio = (float(recent_totals.get("returns_revenue", 0.0) or 0.0) / revenue_total_30d) if revenue_total_30d > 0 else 0.0
    expense_ratios = {
        key: (float(recent_totals.get(key, 0.0) or 0.0) / net_revenue_total_30d) if net_revenue_total_30d > 0 else 0.0
        for key in FORECAST_EXPENSE_KEYS
    }

    for day in forecast_days:
        gross_revenue = float(forecast_daily["revenue"].get(day, 0.0) or 0.0)
        ordered_units = float(forecast_daily["units"].get(day, 0.0) or 0.0)
        returns_revenue = gross_revenue * returns_revenue_ratio
        net_revenue = max(0.0, gross_revenue - returns_revenue)
        forecast_rows_map["ordered_units"]["daily"][day] = ordered_units
        forecast_rows_map["returned_units"]["daily"][day] = ordered_units * returned_units_ratio
        forecast_rows_map["revenue"]["daily"][day] = gross_revenue
        forecast_rows_map["returns_revenue"]["daily"][day] = returns_revenue
        for row_key in FORECAST_EXPENSE_KEYS:
            forecast_rows_map[row_key]["daily"][day] = net_revenue * expense_ratios.get(row_key, 0.0)

    rebuild_finance_derived_rows(forecast_rows_map, days)
    return forecast_rows_map


async def load_prev_month_pcts(
    conn: asyncpg.Connection,
    year: int,
    month: int,
) -> Optional[Dict[str, float]]:
    prev_month_pcts = None
    try:
        prev_year, prev_month = year, month - 1
        if prev_month == 0:
            prev_year -= 1
            prev_month = 12
        prev_month_value = f"{prev_year:04d}-{prev_month:02d}"
        prev_rows_map, _ = await build_rows_map_for_month(conn, prev_month_value)
        prev_revenue = float(prev_rows_map["revenue_sales"]["total"] or 0)
        prev_money_on_account = float(prev_rows_map["accrued"]["total"] or 0)
        if prev_revenue > 0:
            prev_month_pcts = {
                "expenses_mp": float(prev_rows_map["marketplace_expenses"]["total"] or 0) / prev_revenue,
                "commission": float(prev_rows_map["ozon_fee_total"]["total"] or 0) / prev_revenue,
                "logistics": float(prev_rows_map["delivery_services_total"]["total"] or 0) / prev_revenue,
                "ads": float(prev_rows_map["promotion_total"]["total"] or 0) / prev_revenue,
                "total_expenses": float(prev_rows_map["all_expenses"]["total"] or 0) / prev_revenue,
                "material_cost": float(prev_rows_map["material_cost"]["total"] or 0) / prev_revenue,
                "money_on_account": prev_money_on_account / prev_revenue,
                "gross_profit": float(prev_rows_map["gross_profit"]["total"] or 0) / prev_revenue,
                "gross_to_money_pct": (
                    float(prev_rows_map["gross_profit"]["total"] or 0) / prev_money_on_account
                    if prev_money_on_account
                    else None
                ),
                "gross_to_revenue_pct": float(prev_rows_map["gross_profit"]["total"] or 0) / prev_revenue,
            }
    except Exception:
        prev_month_pcts = None
    return prev_month_pcts


def build_kpi_summary_v2(
    fact_rows_map: Dict[str, Dict[str, Any]],
    forecast_rows_map: Dict[str, Dict[str, Any]],
    revenue_plan_total: float,
    plan_editable: bool,
    prev_month_pcts: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    fact_revenue = as_float(fact_rows_map["revenue_sales"]["total"])
    fact_expenses_mp = as_float(fact_rows_map["marketplace_expenses"]["total"])
    fact_marketing = as_float(fact_rows_map["promotion_total"]["total"])
    fact_expenses_total = as_float(fact_rows_map["all_expenses"]["total"])
    fact_money_on_account = as_float(fact_rows_map["accrued"]["total"])
    fact_material_cost = as_float(fact_rows_map["material_cost"]["total"])
    fact_gross_profit = as_float(fact_rows_map["gross_profit"]["total"])

    forecast_revenue = as_float(forecast_rows_map["revenue_sales"]["total"])
    forecast_expenses_mp = as_float(forecast_rows_map["marketplace_expenses"]["total"])
    forecast_marketing = as_float(forecast_rows_map["promotion_total"]["total"])
    forecast_expenses_total = as_float(forecast_rows_map["all_expenses"]["total"])
    forecast_money_on_account = as_float(forecast_rows_map["accrued"]["total"])
    forecast_material_cost = as_float(forecast_rows_map["material_cost"]["total"])
    forecast_gross_profit = as_float(forecast_rows_map["gross_profit"]["total"])

    plan_revenue = revenue_plan_total
    if prev_month_pcts:
        plan_expenses_mp = plan_revenue * prev_month_pcts.get("expenses_mp", 0.60)
        plan_marketing = plan_revenue * prev_month_pcts.get("ads", 0.15)
        plan_expenses_total = plan_expenses_mp + plan_marketing
        plan_money_on_account = plan_revenue * prev_month_pcts.get("money_on_account", 0.40)
        plan_material_cost = plan_revenue * prev_month_pcts.get("material_cost", 0.15)
        plan_gross_profit = plan_revenue * prev_month_pcts.get("gross_profit", 0.25)
        plan_expenses_mp_pct = prev_month_pcts.get("expenses_mp", 0.60)
        plan_marketing_pct = prev_month_pcts.get("ads", 0.15)
        plan_total_expenses_pct = prev_month_pcts.get("total_expenses", 0.60)
        plan_material_cost_pct = prev_month_pcts.get("material_cost", 0.15)
        plan_gross_to_money_pct = prev_month_pcts.get("gross_to_money_pct")
        plan_gross_to_revenue_pct = prev_month_pcts.get("gross_to_revenue_pct")
    else:
        plan_expenses_mp = None
        plan_marketing = None
        plan_expenses_total = None
        plan_money_on_account = None
        plan_material_cost = None
        plan_gross_profit = None
        plan_expenses_mp_pct = None
        plan_marketing_pct = None
        plan_total_expenses_pct = None
        plan_material_cost_pct = None
        plan_gross_to_money_pct = None
        plan_gross_to_revenue_pct = None

    fact_marketing_pct = as_float(fact_rows_map["marketing_pct"]["total"])
    fact_expenses_mp_pct = as_float(fact_rows_map["marketplace_expenses_pct"]["total"])
    fact_expenses_total_pct = safe_divide(as_float(fact_rows_map["all_expenses"]["total"]), fact_revenue)
    fact_material_cost_pct = safe_divide(fact_material_cost, fact_revenue)
    fact_gross_to_money_pct = safe_divide(fact_gross_profit, fact_money_on_account)
    fact_gross_to_revenue_pct = safe_divide(fact_gross_profit, fact_revenue)

    forecast_marketing_pct = as_float(forecast_rows_map["marketing_pct"]["total"])
    forecast_expenses_mp_pct = as_float(forecast_rows_map["marketplace_expenses_pct"]["total"])
    forecast_expenses_total_pct = safe_divide(as_float(forecast_rows_map["all_expenses"]["total"]), forecast_revenue)
    forecast_material_cost_pct = safe_divide(forecast_material_cost, forecast_revenue)
    forecast_gross_to_money_pct = safe_divide(forecast_gross_profit, forecast_money_on_account)
    forecast_gross_to_revenue_pct = safe_divide(forecast_gross_profit, forecast_revenue)

    return [
        {"key": "revenue_mp", "label": "Выручка МП", "format": "number", "fact": fact_revenue, "forecast": forecast_revenue, "plan": plan_revenue, "plan_editable": plan_editable},
        {"key": "expenses_mp", "label": "Расходы МП", "format": "number", "fact": fact_expenses_mp, "forecast": forecast_expenses_mp, "plan": plan_expenses_mp},
        {"key": "expenses_mp_pct", "label": "Расходы МП %", "format": "percent", "fact": fact_expenses_mp_pct, "forecast": forecast_expenses_mp_pct, "plan": plan_expenses_mp_pct},
        {"key": "marketing", "label": "Маркетинг", "format": "number", "fact": fact_marketing, "forecast": forecast_marketing, "plan": plan_marketing},
        {"key": "marketing_pct", "label": "Маркетинг %", "format": "percent", "fact": fact_marketing_pct, "forecast": forecast_marketing_pct, "plan": plan_marketing_pct},
        {"key": "expenses_total", "label": "ИТОГО расходы МП", "format": "number", "fact": fact_expenses_total, "forecast": forecast_expenses_total, "plan": plan_expenses_total},
        {"key": "expenses_total_pct", "label": "ИТОГО расходы МП %", "format": "percent", "fact": fact_expenses_total_pct, "forecast": forecast_expenses_total_pct, "plan": plan_total_expenses_pct},
        {"key": "money_on_account", "label": "Деньги на счет", "format": "number", "fact": fact_money_on_account, "forecast": forecast_money_on_account, "plan": plan_money_on_account},
        {"key": "material_cost", "label": "Себестоимость", "format": "number", "fact": fact_material_cost, "forecast": forecast_material_cost, "plan": plan_material_cost},
        {"key": "material_cost_pct", "label": "Себестоимость %", "format": "percent", "fact": fact_material_cost_pct, "forecast": forecast_material_cost_pct, "plan": plan_material_cost_pct},
        {"key": "gross_profit", "label": "Валовая прибыль", "format": "number", "fact": fact_gross_profit, "forecast": forecast_gross_profit, "plan": plan_gross_profit},
        {"key": "gross_to_money_pct", "label": "Валовая к деньгам на счет", "format": "percent", "fact": fact_gross_to_money_pct, "forecast": forecast_gross_to_money_pct, "plan": plan_gross_to_money_pct},
        {"key": "gross_to_revenue_pct", "label": "Валовая к выручке МП", "format": "percent", "fact": fact_gross_to_revenue_pct, "forecast": forecast_gross_to_revenue_pct, "plan": plan_gross_to_revenue_pct},
    ]


async def get_finance_report_data(
    conn: asyncpg.Connection,
    month_value: str,
) -> Dict[str, Any]:
    try:
        month_bounds(month_value)
    except ValueError as exc:
        raise ValueError("Invalid month format, expected YYYY-MM") from exc

    year_str, month_str = month_value.split("-", 1)
    year = int(year_str)
    month = int(month_str)
    query_start = datetime(year, month, 1, tzinfo=MSK).astimezone(timezone.utc)
    if month == 12:
        query_end = datetime(year + 1, 1, 1, tzinfo=MSK).astimezone(timezone.utc)
    else:
        query_end = datetime(year, month + 1, 1, tzinfo=MSK).astimezone(timezone.utc)

    prev_month_pcts = await load_prev_month_pcts(conn, year, month)

    rows_map, days = await build_rows_map_for_month(conn, month_value)
    report_days = list(days)
    notes = finance_report_notes()
    revenue_plan_total = float(rows_map["revenue_plan"]["daily"][report_days[-1]]) if report_days else 0.0
    now_msk = datetime.now(MSK)
    month_msk = month_start_msk(month_value)
    plan_editable = month_msk.year == now_msk.year and month_msk.month == now_msk.month
    marketing_daily = {
        day: (
            rows_map["pay_per_click"]["daily"][day]
            + rows_map["review_points"]["daily"][day]
            + rows_map["premium_plus_subscription"]["daily"][day]
            + rows_map["review_pin"]["daily"][day]
            + rows_map["accelerated_reviews"]["daily"][day]
        )
        for day in report_days
    }

    return {
        "month": month_value,
        "days": report_days,
        "rows": render_finance_rows(rows_map, report_days),
        "notes": notes,
        "kpi_summary": build_kpi_summary(
            month_value=month_value,
            rows_map=rows_map,
            marketing_daily=marketing_daily,
            revenue_plan_total=revenue_plan_total,
            plan_editable=plan_editable,
            prev_month_pcts=prev_month_pcts,
        ),
        "plan": {
            "revenue_mp": revenue_plan_total,
            "editable": plan_editable,
        },
    }


async def get_finance_report_v2_data(
    conn: asyncpg.Connection,
    month_value: str,
) -> Dict[str, Any]:
    try:
        month_bounds(month_value)
    except ValueError as exc:
        raise ValueError("Invalid month format, expected YYYY-MM") from exc

    year_str, month_str = month_value.split("-", 1)
    year = int(year_str)
    month = int(month_str)
    rows_map, days = await build_rows_map_for_month(conn, month_value)
    report_days = visible_report_days_v2(month_value, days)
    rows_map = trim_rows_map_to_days(rows_map, days, report_days)
    forecast_days = current_forecast_days(month_value, report_days)
    recent_totals = await aggregate_recent_30d_totals(conn, datetime.now(MSK).date())
    forecast_daily = await build_stock_based_daily_forecast(conn, forecast_days)
    fact_by_article = await load_fact_revenue_by_article_day(conn, month_value)
    revenue_breakdown = build_revenue_breakdown(
        forecast_daily=forecast_daily,
        report_days=report_days,
        recent_totals=recent_totals,
        fact_by_article=fact_by_article,
        forecast_days=forecast_days,
    )
    forecast_rows_map = apply_finance_report_v2_forecast(
        rows_map=rows_map,
        days=report_days,
        forecast_days=forecast_days,
        recent_totals=recent_totals,
        forecast_daily=forecast_daily,
    )
    prev_month_pcts = await load_prev_month_pcts(conn, year, month)
    revenue_plan_total = float(rows_map["revenue_plan"]["daily"][report_days[-1]]) if report_days else 0.0
    now_msk = datetime.now(MSK)
    month_msk = month_start_msk(month_value)
    plan_editable = month_msk.year == now_msk.year and month_msk.month == now_msk.month

    notes = finance_report_notes() + finance_report_v2_notes()
    if report_days != days:
        notes.append("Текущий день по МСК исключён из отчёта, пока он не завершён.")
    if not forecast_days:
        notes.append("Для прошедших месяцев V2 показывает факт без будущего прогноза.")

    return {
        "month": month_value,
        "days": report_days,
        "rows": render_finance_rows(forecast_rows_map, report_days),
        "notes": notes,
        "forecast_days": forecast_days,
        "variant": "v2",
        "revenue_breakdown": revenue_breakdown,
        "kpi_summary": build_kpi_summary_v2(
            fact_rows_map=rows_map,
            forecast_rows_map=forecast_rows_map,
            revenue_plan_total=revenue_plan_total,
            plan_editable=plan_editable,
            prev_month_pcts=prev_month_pcts,
        ),
        "plan": {
            "revenue_mp": revenue_plan_total,
            "editable": plan_editable,
        },
    }


async def create_report_pool() -> asyncpg.Pool:
    dsn = to_asyncpg_dsn(settings.database_url)
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
    await ensure_finance_report_tables(pool)
    return pool


def load_finance_report(month_value: str) -> Dict[str, Any]:
    async def _run() -> Dict[str, Any]:
        pool = await create_report_pool()
        try:
            async with pool.acquire() as conn:
                return await get_finance_report_data(conn, month_value)
        finally:
            await pool.close()

    return asyncio.run(_run())
