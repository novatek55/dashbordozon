"""Dashboard routes/finance.py handlers."""
import io
import json
import math
import re
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import pandas as pd
from aiohttp import web

from src.config import settings
from src.dashboard.constants import (
    BASE_DIR, MSK,
    FINANCE_REPORT_ROWS, FINANCE_ROW_META, FINANCE_ZERO_ROWS,
    FINANCE_DESCRIPTION_FILTERS, ACCRUAL_COST_ROW_KEYS,
    AD_FINANCE_DESCRIPTIONS, DELIVERED_STATUSES,
    PLAN_BASELINE_REVENUE, PLAN_BASE_VALUES, PLAN_BASE_PCTS,
    SUPPLY_CLUSTER_MARKUP_DEFAULTS,
)
from src.dashboard.helpers import (
    clean_nan_values, month_bounds, safe_divide, as_float,
    normalize_offer_id, normalize_article_key, normalize_sku_value,
    article_tags_from_offer_id, build_cost_maps, load_posting_context,
    load_sku_identity_map,
    lookup_unit_cost, init_row, recalculate_row_total, set_row_from_formula,
    append_finance_posting, finance_row_key_for_compensation_article,
    fix_mojibake_cp1251_utf8, to_mojibake_cp1251_utf8,
    month_timeline, scale_plan_value, build_kpi_summary, build_where,
    parse_date_utc, month_start_msk, extract_item_article,
    _is_ad_description, _normalize_cluster_name,
    ACCRUAL_COST_DESCRIPTION_WHITELIST,
)


async def ensure_finance_report_tables(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_article_costs (
                article TEXT PRIMARY KEY,
                sku BIGINT,
                unit_cost NUMERIC(15, 2) NOT NULL,
                source_file TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE finance_article_costs
            ADD COLUMN IF NOT EXISTS sku BIGINT
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_finance_article_costs_sku
            ON finance_article_costs (sku)
            """
        )
        await conn.execute(
            """
            WITH offer_sku AS (
                SELECT
                    lower(regexp_replace(trim(offer_id), '\\s+', ' ', 'g')) AS article_key,
                    max(sku) AS sku
                FROM fact_order_items
                WHERE offer_id IS NOT NULL
                  AND sku IS NOT NULL
                GROUP BY 1
                HAVING count(DISTINCT sku) = 1
            )
            UPDATE finance_article_costs fac
            SET sku = offer_sku.sku
            FROM offer_sku
            WHERE fac.sku IS NULL
              AND lower(regexp_replace(trim(fac.article), '\\s+', ' ', 'g')) = offer_sku.article_key
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_month_plan (
                month_start DATE PRIMARY KEY,
                revenue_plan NUMERIC(15, 2) NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE finance_month_plan
            ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon'
            """
        )
        await conn.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'finance_month_plan'::regclass
                      AND conname = 'finance_month_plan_pkey'
                      AND pg_get_constraintdef(oid) = 'PRIMARY KEY (month_start)'
                ) THEN
                    ALTER TABLE finance_month_plan DROP CONSTRAINT finance_month_plan_pkey;
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'finance_month_plan'::regclass
                      AND conname = 'finance_month_plan_marketplace_month_start_pkey'
                ) THEN
                    ALTER TABLE finance_month_plan
                    ADD CONSTRAINT finance_month_plan_marketplace_month_start_pkey
                    PRIMARY KEY (marketplace, month_start);
                END IF;
            END $$;
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS supply_plan_state (
                offer_id TEXT PRIMARY KEY,
                product_id BIGINT,
                supply_stock INTEGER NOT NULL DEFAULT 0,
                hidden BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE supply_plan_state
            ADD COLUMN IF NOT EXISTS product_id BIGINT
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_supply_plan_state_product_id
            ON supply_plan_state (product_id)
            WHERE product_id IS NOT NULL
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS article_characteristics (
                sku BIGINT PRIMARY KEY,
                offer_id TEXT,
                article_name TEXT,
                is_kgt BOOLEAN,
                volume_weight NUMERIC(15, 3),
                height_mm INTEGER,
                width_mm INTEGER,
                depth_mm INTEGER,
                weight_g INTEGER,
                shipment_type TEXT,
                raw_v3 JSONB,
                raw_v4 JSONB,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_article_characteristics_offer
            ON article_characteristics (offer_id)
            """
        )
        await conn.execute(
            """
            CREATE OR REPLACE VIEW analytics_product_queries_daily_view AS
            SELECT
                d.period_start,
                d.period_end,
                d.granularity,
                d.sku,
                coalesce(d.offer_id, s.offer_id) AS offer_id,
                coalesce(d.product_name, s.product_name) AS product_name,
                d.query_text,
                d.searches,
                d.views,
                d.avg_position,
                d.conversion,
                d.gmv,
                s.searches AS sku_searches,
                s.views AS sku_views,
                s.avg_position AS sku_avg_position,
                s.conversion AS sku_conversion,
                s.gmv AS sku_gmv,
                d.last_synced_at
            FROM analytics_product_query_details d
            LEFT JOIN analytics_product_query_summary s
                ON s.period_start = d.period_start
               AND s.period_end = d.period_end
               AND s.granularity = d.granularity
               AND s.sku = d.sku
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_article_characteristics_kgt
            ON article_characteristics (is_kgt)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS palletization_product_params (
                product_id BIGINT PRIMARY KEY REFERENCES products(product_id) ON DELETE CASCADE,
                items_per_layer INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_palletization_product_params_updated
            ON palletization_product_params (updated_at)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ozon_clusters_directory (
                macrolocal_cluster_id BIGINT PRIMARY KEY,
                cluster_id BIGINT,
                cluster_name TEXT NOT NULL,
                cluster_type TEXT,
                raw_data JSONB,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ozon_cluster_warehouses (
                macrolocal_cluster_id BIGINT NOT NULL,
                logistic_cluster_index INTEGER NOT NULL,
                warehouse_id BIGINT NOT NULL,
                warehouse_type TEXT,
                warehouse_name TEXT,
                raw_data JSONB,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (macrolocal_cluster_id, logistic_cluster_index, warehouse_id)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ozon_cluster_warehouses_wh_id
            ON ozon_cluster_warehouses (warehouse_id)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ozon_cluster_warehouses_cluster
            ON ozon_cluster_warehouses (macrolocal_cluster_id)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS supply_cluster_markup_tariffs (
                cluster_name_norm TEXT PRIMARY KEY,
                cluster_name TEXT NOT NULL,
                markup_pct NUMERIC(6, 2) NOT NULL DEFAULT 0,
                source TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        if SUPPLY_CLUSTER_MARKUP_DEFAULTS:
            await conn.executemany(
                """
                INSERT INTO supply_cluster_markup_tariffs (
                    cluster_name_norm,
                    cluster_name,
                    markup_pct,
                    source,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, now())
                ON CONFLICT (cluster_name_norm) DO NOTHING
                """,
                [
                    (
                        _normalize_cluster_name(cluster_name),
                        cluster_name,
                        float(markup_pct),
                        "default_ozon_nonlocal_markup",
                    )
                    for cluster_name, markup_pct in SUPPLY_CLUSTER_MARKUP_DEFAULTS
                ],
            )


async def get_finance_costs(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT article, sku, unit_cost, updated_at
            FROM finance_article_costs
            ORDER BY sku NULLS LAST, article
            """
        )
    items = [
        {
            "article": row["article"],
            "sku": row["sku"],
            "unit_cost": as_float(row["unit_cost"]),
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]
    # РћС‡РёС‰Р°РµРј NaN Р·РЅР°С‡РµРЅРёСЏ РїРµСЂРµРґ СЃРµСЂРёР°Р»РёР·Р°С†РёРµР№
    data = clean_nan_values({"count": len(items), "items": items})
    return web.json_response(data)


async def get_settings_costs(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                p.offer_id AS article,
                p.product_id AS sku,
                p.name,
                fac.unit_cost,
                fac.updated_at
            FROM products p
            LEFT JOIN LATERAL (
                SELECT f.unit_cost, f.updated_at
                FROM finance_article_costs f
                WHERE lower(trim(f.article)) = lower(trim(p.offer_id))
                   OR (f.sku IS NOT NULL AND f.sku = p.product_id)
                ORDER BY CASE WHEN lower(trim(f.article)) = lower(trim(p.offer_id)) THEN 0 ELSE 1 END, f.updated_at DESC
                LIMIT 1
            ) fac ON true
            ORDER BY p.name NULLS LAST, p.offer_id
            """
        )
    items = [
        {
            "article": row["article"],
            "sku": row["sku"],
            "name": row["name"],
            "unit_cost": as_float(row["unit_cost"]),
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]
    return web.json_response(clean_nan_values({"count": len(items), "items": items}))


async def save_settings_cost(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Ожидается JSON body."}, status=400)

    article = str(payload.get("article") or "").strip()
    if not article:
        return web.json_response({"error": "Поле 'article' обязательно."}, status=400)

    sku = normalize_sku_value(payload.get("sku"))
    unit_cost = as_float(payload.get("unit_cost"), default=-1.0)
    if unit_cost < 0:
        return web.json_response({"error": "Поле 'unit_cost' должно быть >= 0."}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO finance_article_costs (article, sku, unit_cost, source_file, updated_at)
            VALUES ($1, $2, $3, 'dashboard_manual_edit', now())
            ON CONFLICT (article) DO UPDATE
            SET sku = COALESCE(EXCLUDED.sku, finance_article_costs.sku),
                unit_cost = EXCLUDED.unit_cost,
                source_file = EXCLUDED.source_file,
                updated_at = now()
            """,
            article,
            sku,
            unit_cost,
        )
    return web.json_response({"status": "ok", "article": article, "sku": sku, "unit_cost": unit_cost})


async def upload_finance_costs(request: web.Request) -> web.Response:
    reader = await request.multipart()
    file_part = await reader.next()
    if file_part is None or file_part.name != "file":
        return web.json_response({"error": "РўСЂРµР±СѓРµС‚СЃСЏ multipart field 'file'."}, status=400)

    filename = file_part.filename or "uploaded.xlsx"
    content = await file_part.read(decode=False)
    if not content:
        return web.json_response({"error": "Р¤Р°Р№Р» РїСѓСЃС‚РѕР№."}, status=400)

    try:
        frame = pd.read_excel(io.BytesIO(content))
    except Exception as exc:
        return web.json_response({"error": f"РќРµ СѓРґР°Р»РѕСЃСЊ РїСЂРѕС‡РёС‚Р°С‚СЊ Excel: {exc}"}, status=400)

    normalized_columns: Dict[str, str] = {}
    for column in frame.columns:
        if column is None:
            continue
        key = str(column).strip().lower().replace("С‘", "Рµ")
        normalized_columns[key] = column

    article_column = None
    sku_column = None
    cost_column = None
    for candidate in ("Р°СЂС‚РёРєСѓР»", "offer_id", "article", "offer id"):
        if candidate in normalized_columns:
            article_column = normalized_columns[candidate]
            break
    for candidate in ("sku", "ozon sku id", "sku id", "fbo ozon sku id", "fbs ozon sku id"):
        if candidate in normalized_columns:
            sku_column = normalized_columns[candidate]
            break
    for candidate in ("СЃРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ РЅР° РµРґ.", "СЃРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ РЅР° РµРґ", "СЃРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ", "cost", "unit_cost"):
        if candidate in normalized_columns:
            cost_column = normalized_columns[candidate]
            break

    if article_column is None or cost_column is None:
        return web.json_response(
            {
                "error": "РќСѓР¶РЅС‹ РєРѕР»РѕРЅРєРё: 'РђСЂС‚РёРєСѓР»' Рё 'РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ РЅР° РµРґ.'. РљРѕР»РѕРЅРєР° 'SKU' РѕРїС†РёРѕРЅР°Р»СЊРЅР°.",
                "columns": [str(column) for column in frame.columns],
            },
            status=400,
        )

    upsert_payload: List[Tuple[str, Optional[int], float]] = []
    for _, row in frame.iterrows():
        article = str(row.get(article_column, "")).strip()
        if not article or article.lower() == "nan":
            continue
        sku = normalize_sku_value(row.get(sku_column)) if sku_column else None
        cost = as_float(row.get(cost_column), default=-1.0)
        if cost < 0:
            continue
        upsert_payload.append((article, sku, cost))

    if not upsert_payload:
        return web.json_response({"error": "РќРµ РЅР°Р№РґРµРЅРѕ РєРѕСЂСЂРµРєС‚РЅС‹С… СЃС‚СЂРѕРє РґР»СЏ Р·Р°РіСЂСѓР·РєРё."}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                """
                INSERT INTO finance_article_costs (article, sku, unit_cost, source_file, updated_at)
                VALUES ($1, $2, $3, $4, now())
                ON CONFLICT (article) DO UPDATE
                SET sku = COALESCE(EXCLUDED.sku, finance_article_costs.sku),
                    unit_cost = EXCLUDED.unit_cost,
                    source_file = EXCLUDED.source_file,
                    updated_at = now()
                """,
                [(article, sku, cost, filename) for article, sku, cost in upsert_payload],
            )

    data = clean_nan_values({"status": "ok", "loaded": len(upsert_payload), "filename": filename})
    return web.json_response(data)


async def save_finance_plan(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "РћР¶РёРґР°РµС‚СЃСЏ JSON body."}, status=400)

    month_value = str(payload.get("month") or "").strip()
    marketplace = str(payload.get("marketplace") or request.query.get("marketplace") or "ozon").strip().lower()
    revenue_plan = as_float(payload.get("revenue_plan"), default=-1.0)
    if not month_value:
        return web.json_response({"error": "РџРѕР»Рµ 'month' РѕР±СЏР·Р°С‚РµР»СЊРЅРѕ."}, status=400)
    if marketplace not in {"ozon", "wb"}:
        return web.json_response({"error": "Invalid marketplace, expected ozon or wb"}, status=400)
    if revenue_plan < 0:
        return web.json_response({"error": "РџРѕР»Рµ 'revenue_plan' РґРѕР»Р¶РЅРѕ Р±С‹С‚СЊ >= 0."}, status=400)

    try:
        month_start = month_start_msk(month_value).date()
    except ValueError:
        return web.json_response({"error": "Invalid month format, expected YYYY-MM"}, status=400)

    now_msk = datetime.now(MSK)
    if month_start.year != now_msk.year or month_start.month != now_msk.month:
        return web.json_response(
            {"error": "РџР»Р°РЅ РјРѕР¶РЅРѕ РёР·РјРµРЅСЏС‚СЊ С‚РѕР»СЊРєРѕ РґР»СЏ С‚РµРєСѓС‰РµРіРѕ РјРµСЃСЏС†Р°."},
            status=403,
        )

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO finance_month_plan (marketplace, month_start, revenue_plan, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (marketplace, month_start) DO UPDATE
            SET revenue_plan = EXCLUDED.revenue_plan,
                updated_at = now()
            """,
            marketplace,
            month_start,
            revenue_plan,
        )
    return web.json_response({"status": "ok", "marketplace": marketplace, "month": month_value, "revenue_plan": revenue_plan})


async def get_cash_flow(request: web.Request) -> web.Response:
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    limit_raw = (request.query.get("limit") or "500").strip()

    try:
        limit = max(1, min(2000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    try:
        date_from = parse_date_utc(date_from_raw, end_of_day=False) if date_from_raw else None
        date_to_exclusive = parse_date_utc(date_to_raw, end_of_day=True) if date_to_raw else None
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)

    params: List[Any] = []
    conditions: List[str] = []
    idx = 1

    if date_from is not None:
        conditions.append(f"date >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to_exclusive is not None:
        conditions.append(f"date < ${idx}")
        params.append(date_to_exclusive)
        idx += 1

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            date,
            revenue,
            return_cost,
            commission,
            other_costs,
            delivery_cost,
            net_amount
        FROM cash_flow_statements
        {where_sql}
        ORDER BY date DESC
        LIMIT {limit}
    """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "date": r["date"].isoformat() if r["date"] else None,
                "revenue": float(r["revenue"]) if r["revenue"] is not None else None,
                "return_cost": float(r["return_cost"]) if r["return_cost"] is not None else None,
                "commission": float(r["commission"]) if r["commission"] is not None else None,
                "other_costs": float(r["other_costs"]) if r["other_costs"] is not None else None,
                "delivery_cost": float(r["delivery_cost"]) if r["delivery_cost"] is not None else None,
                "net_amount": float(r["net_amount"]) if r["net_amount"] is not None else None,
            }
        )

    return web.json_response({"count": len(items), "items": items})


async def build_rows_map_for_month(
    conn: asyncpg.Connection,
    month_value: str,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    month_start, month_end, days = month_bounds(month_value)
    year_str, month_str = month_value.split("-", 1)
    year = int(year_str)
    month = int(month_str)
    month_start_local = datetime(year, month, 1, tzinfo=MSK)
    if month == 12:
        month_end_local = datetime(year + 1, 1, 1, tzinfo=MSK)
    else:
        month_end_local = datetime(year, month + 1, 1, tzinfo=MSK)
    query_start = month_start_local.astimezone(timezone.utc)
    query_end = month_end_local.astimezone(timezone.utc)

    transaction_rows = await conn.fetch(
        """
        SELECT
            operation_date,
            operation_type,
            posting_number,
            description,
            amount,
            raw_data
        FROM transactions
        WHERE operation_date >= $1
          AND operation_date < $2
        ORDER BY operation_date
        """,
        query_start,
        query_end,
    )
    posting_numbers = [
        r["posting_number"]
        for r in transaction_rows
        if r["posting_number"]
        and r["operation_type"] in {"OperationAgentDeliveredToCustomer", "ClientReturnAgentOperation"}
    ]
    posting_items_map, delivered_postings, returned_postings, snapshot_items_map = await load_posting_context(
        conn,
        posting_numbers,
    )
    compensation_rows = await conn.fetch(
        """
        SELECT
            effective_date,
            article_name,
            amount
        FROM report_compensation_items
        WHERE effective_date >= $1
          AND effective_date < $2
        ORDER BY effective_date
        """,
        query_start,
        query_end,
    )
    returns_rows = await conn.fetch(
        """
        WITH returns_all AS (
            SELECT
                offer_id,
                sku,
                quantity,
                returned_at
            FROM returns
            WHERE returned_at >= $1
              AND returned_at < $2
            UNION ALL
            SELECT
                offer_id,
                sku,
                quantity,
                returned_at
            FROM returns_fbo
            WHERE returned_at >= $1
              AND returned_at < $2
        )
        SELECT offer_id, sku, quantity, returned_at
        FROM returns_all
        """,
        query_start,
        query_end,
    )
    article_cost_rows = await conn.fetch(
        """
        SELECT article, sku, unit_cost
        FROM finance_article_costs
        """
    )
    plan_row = await conn.fetchrow(
        """
        SELECT revenue_plan
        FROM finance_month_plan
        WHERE marketplace = 'ozon'
          AND month_start = $1
        """,
        month_start_msk(month_value).date(),
    )

    rows_map: Dict[str, Dict[str, Any]] = {
        row_meta["key"]: init_row(days, row_meta["key"])
        for row_meta in FINANCE_REPORT_ROWS
        if row_meta["kind"] != "section" and row_meta["kind"] != "spacer"
    }

    for key in FINANCE_ZERO_ROWS:
        recalculate_row_total(rows_map[key], days)

    ordered_units_row = rows_map["ordered_units"]
    sku_cost_map, article_cost_map = build_cost_maps(article_cost_rows)
    revenue_plan_total = as_float(plan_row["revenue_plan"]) if plan_row else PLAN_BASE_VALUES["revenue_mp"]
    daily_plan = revenue_plan_total / len(days)
    cumulative_plan = 0.0
    for day in days:
        cumulative_plan += daily_plan
        rows_map["revenue_plan"]["daily"][day] = cumulative_plan
    # РС‚РѕРіРѕ = РјРµСЃСЏС‡РЅС‹Р№ РїР»Р°РЅ РІС‹СЂСѓС‡РєРё РњРџ (Р° РЅРµ СЃСѓРјРјР° РЅР°РєРѕРїРёС‚РµР»СЊРЅС‹С… Р·РЅР°С‡РµРЅРёР№)
    rows_map["revenue_plan"]["total"] = revenue_plan_total

    service_description_map = {
        "MarketplaceServiceItemDirectFlowLogistic": "Р›РѕРіРёСЃС‚РёРєР°",
        "MarketplaceServiceItemReturnFlowLogistic": "РћР±СЂР°С‚РЅР°СЏ Р»РѕРіРёСЃС‚РёРєР°",
        "MarketplaceServiceItemDropoffPVZ": "РћР±СЂР°Р±РѕС‚РєР° РѕС‚РїСЂР°РІР»РµРЅРёСЏ Drop-off",
        "MarketplaceServiceItemDropoffSC": "РћР±СЂР°Р±РѕС‚РєР° РѕС‚РїСЂР°РІР»РµРЅРёСЏ Drop-off",
        "MarketplaceServiceItemRedistributionReturnsPVZ": "РћР±СЂР°Р±РѕС‚РєР° РІРѕР·РІСЂР°С‚РѕРІ, РѕС‚РјРµРЅ Рё РЅРµРІС‹РєСѓРїРѕРІ РїР°СЂС‚РЅС‘СЂР°РјРё",
        "MarketplaceServiceItemRedistributionDropOffApvz": "РћР±СЂР°Р±РѕС‚РєР° РѕС‚РїСЂР°РІР»РµРЅРёСЏ Drop-off РїР°СЂС‚РЅС‘СЂР°РјРё (РђРџР’Р—)",
        "MarketplaceServiceItemRedistributionLastMileCourier": "Р”РѕСЃС‚Р°РІРєР° РґРѕ РјРµСЃС‚Р° РІС‹РґР°С‡Рё",
        "MarketplaceServiceItemRedistributionLastMilePVZ": "Р”РѕСЃС‚Р°РІРєР° РґРѕ РјРµСЃС‚Р° РІС‹РґР°С‡Рё",
        "MarketplaceServiceItemTemporaryStorageRedistribution": "Р’СЂРµРјРµРЅРЅРѕРµ СЂР°Р·РјРµС‰РµРЅРёРµ С‚РѕРІР°СЂР° РїР°СЂС‚РЅРµСЂР°РјРё",
        "MarketplaceServiceItemPackageRedistribution": "РЈРїР°РєРѕРІРєР° С‚РѕРІР°СЂР° РїР°СЂС‚РЅС‘СЂР°РјРё",
        "MarketplaceRedistributionOfAcquiringOperation": "Р­РєРІР°Р№СЂРёРЅРі",
    }

    finance_postings: List[Dict[str, Any]] = []
    delivered_postings_by_day: Dict[str, set[str]] = defaultdict(set)
    returned_postings_by_day: Dict[str, set[str]] = defaultdict(set)
    has_transaction_items = False

    for record in transaction_rows:
        day = record["operation_date"].astimezone(MSK).strftime("%Y-%m-%d") if record["operation_date"] else None
        if not day or day not in ordered_units_row["daily"]:
            continue

        raw_data = record["raw_data"]
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                raw_data = {}
        if not isinstance(raw_data, dict):
            raw_data = {}

        description = to_mojibake_cp1251_utf8((record["description"] or "").strip())
        operation_type = (record["operation_type"] or "").strip()
        amount = as_float(record["amount"])
        accruals_for_sale = as_float(raw_data.get("accruals_for_sale"))
        sale_commission = as_float(raw_data.get("sale_commission"))
        items = raw_data.get("items") if isinstance(raw_data.get("items"), list) else []
        posting_number = record["posting_number"]
        services = raw_data.get("services") if isinstance(raw_data.get("services"), list) else []

        if operation_type == "OperationAgentDeliveredToCustomer":
            if not posting_number:
                continue
            delivered_postings_by_day[day].add(posting_number)

            items_source = posting_items_map.get(posting_number, [])
            if not items_source:
                items_source = [item for item in items if isinstance(item, dict)]
            if not items_source and posting_number in snapshot_items_map:
                items_source = snapshot_items_map[posting_number]

            if accruals_for_sale > 0:
                if items_source and posting_number and posting_number in posting_items_map:
                    has_transaction_items = True

                if items_source:
                    ordered_quantity = sum(
                        as_float(item.get("quantity"), default=1.0)
                        for item in items_source
                        if isinstance(item, dict)
                    )
                    append_finance_posting(finance_postings, day, "Р·Р°РєР°Р·Р°РЅРѕ", ordered_quantity)
                append_finance_posting(
                    finance_postings,
                    day,
                    "Р’С‹СЂСѓС‡РєР°",
                    max(accruals_for_sale, 0.0),
                )
                append_finance_posting(finance_postings, day, "Р’РѕР·РЅР°РіСЂР°Р¶РґРµРЅРёРµ Р·Р° РїСЂРѕРґР°Р¶Сѓ", abs(min(sale_commission, 0.0)))

                for item in items_source:
                    if not isinstance(item, dict):
                        continue
                    article = extract_item_article(item) if "offer_id" not in item else (item.get("offer_id") or "").strip()
                    if not article:
                        continue
                    item_qty = as_float(item.get("quantity"), default=1.0)
                    if item_qty <= 0:
                        continue
                    item_sku = item.get("sku") if isinstance(item, dict) else None
                    unit_cost = lookup_unit_cost(sku_cost_map, article_cost_map, sku=item_sku, article=article)
                    if unit_cost is None:
                        continue
                    has_transaction_items = True
                    append_finance_posting(finance_postings, day, "РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ", item_qty * unit_cost)

            for service in services:
                service_name = service.get("name")
                service_price = abs(as_float(service.get("price")))
                append_finance_posting(
                    finance_postings,
                    day,
                    service_description_map.get(service_name, "Р”СЂСѓРіРёРµ СѓСЃР»СѓРіРё"),
                    service_price,
                )
            continue

        if operation_type == "ClientReturnAgentOperation":
            if posting_number:
                returned_postings_by_day[day].add(posting_number)
            return_items_source = posting_items_map.get(posting_number, [])
            if not return_items_source:
                return_items_source = [item for item in items if isinstance(item, dict)]
            if not return_items_source and posting_number in snapshot_items_map:
                return_items_source = snapshot_items_map[posting_number]

            return_quantity = (
                sum(
                    as_float(item.get("quantity"), default=1.0)
                    for item in return_items_source
                    if isinstance(item, dict)
                )
                if return_items_source
                else 0.0
            )
            if return_quantity > 0:
                append_finance_posting(finance_postings, day, "Р’РѕР·РІСЂР°С‚С‹", return_quantity)
            append_finance_posting(
                finance_postings,
                day,
                "Р’РѕР·РІСЂР°С‚ РІС‹СЂСѓС‡РєРё",
                abs(min(accruals_for_sale, 0.0)),
            )
            append_finance_posting(finance_postings, day, "Р’РѕР·РІСЂР°С‚ РІРѕР·РЅР°РіСЂР°Р¶РґРµРЅРёСЏ", max(sale_commission, 0.0))
            for item in return_items_source:
                if not isinstance(item, dict):
                    continue
                article = extract_item_article(item)
                if not article:
                    continue
                item_qty = as_float(item.get("quantity"), default=1.0)
                if item_qty <= 0:
                    continue
                item_sku = item.get("sku") if isinstance(item, dict) else None
                unit_cost = lookup_unit_cost(sku_cost_map, article_cost_map, sku=item_sku, article=article)
                if unit_cost is None:
                    continue
                # Vozvraty vychitaem iz sebestoimosti
                append_finance_posting(finance_postings, day, "РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ", -item_qty * unit_cost)
            continue

        # РќР°С‡РёСЃР»РµРЅРёСЏ Р±РµР· posting_number/Р°СЂС‚РёРєСѓР»Р°: РґРѕР»Р¶РЅС‹ РїРѕРїР°РґР°С‚СЊ РІ Р±Р»РѕРє "РЈСЃР»СѓРіРё РґРѕСЃС‚Р°РІРєРё".
        if operation_type == "OperationCourierArrangement":
            append_finance_posting(finance_postings, day, "РћСЂРіР°РЅРёР·Р°С†РёСЏ РІС‹РµР·РґР° РєСѓСЂСЊРµСЂР°", abs(amount))
            continue

        if operation_type == "OperationCourierPickUpDelivery":
            append_finance_posting(finance_postings, day, "Р”РѕСЃС‚Р°РІРєР° РєСѓСЂСЊРµСЂРѕРј Pick-up", abs(amount))
            continue

        if description == "Р”РѕСЃС‚Р°РІРєР° Рё РѕР±СЂР°Р±РѕС‚РєР° РІРѕР·РІСЂР°С‚Р°, РѕС‚РјРµРЅС‹, РЅРµРІС‹РєСѓРїР°":
            matched_total = 0.0
            for service in services:
                service_name = service.get("name")
                service_price = abs(as_float(service.get("price")))
                matched_total += service_price
                append_finance_posting(
                    finance_postings,
                    day,
                    service_description_map.get(service_name, "РћР±СЂР°С‚РЅР°СЏ Р»РѕРіРёСЃС‚РёРєР°"),
                    service_price,
                )
            residual = abs(amount) - matched_total
            if residual > 1e-9:
                append_finance_posting(finance_postings, day, "РћР±СЂР°С‚РЅР°СЏ Р»РѕРіРёСЃС‚РёРєР°", residual)
            continue

        if description == "РћРїР»Р°С‚Р° СЌРєРІР°Р№СЂРёРЅРіР°":
            append_finance_posting(finance_postings, day, "Р­РєРІР°Р№СЂРёРЅРі", -amount)
            continue

        if description == "РџРѕРґРїРёСЃРєР° Premium Plus":
            append_finance_posting(finance_postings, day, "РџРѕРґРїРёСЃРєР° Premium Plus", abs(amount))
            continue

        if description == "РћРїР»Р°С‚Р° Р·Р° РєР»РёРє":
            append_finance_posting(finance_postings, day, "РћРїР»Р°С‚Р° Р·Р° РєР»РёРє", abs(amount))
            continue

        if description == "Р—Р°РєСЂРµРїР»РµРЅРёРµ РѕС‚Р·С‹РІР°":
            append_finance_posting(finance_postings, day, "Р—Р°РєСЂРµРїР»РµРЅРёРµ РѕС‚Р·С‹РІР°", abs(amount))
            continue
        if description == "РџСЂРѕРґРІРёР¶РµРЅРёРµ СЃ РѕРїР»Р°С‚РѕР№ Р·Р° Р·Р°РєР°Р·":
            # В агрегированном Finance Report учитываем как отдельную рекламную статью.
            append_finance_posting(finance_postings, day, "РџСЂРѕРґРІРёР¶РµРЅРёРµ СЃ РѕРїР»Р°С‚РѕР№ Р·Р° Р·Р°РєР°Р·", abs(amount))
            continue
        if description == "РЈСЃРєРѕСЂРµРЅРЅС‹Р№ СЃР±РѕСЂ РѕС‚Р·С‹РІРѕРІ":
            append_finance_posting(finance_postings, day, "РЈСЃРєРѕСЂРµРЅРЅС‹Р№ СЃР±РѕСЂ РѕС‚Р·С‹РІРѕРІ", abs(amount))
            continue
        if description == "РћС‚РіСЂСѓР·РєР° РІ РЅРµСЂРµРєРѕРјРµРЅРґРѕРІР°РЅРЅС‹Р№ СЃР»РѕС‚" or description == "РћС‚РіСЂСѓР·РєР° РІ РЅРµСЂРµРєРѕРјРµРЅРґРѕРІР°РЅРЅС‹Р№ СЃР»РѕС‚ - РѕС‚РјРµРЅР° РЅР°С‡РёСЃР»РµРЅРёСЏ":
            append_finance_posting(finance_postings, day, "РћС‚РіСЂСѓР·РєР° РІ РЅРµСЂРµРєРѕРјРµРЅРґРѕРІР°РЅРЅС‹Р№ СЃР»РѕС‚", -amount)
            continue
        if description.startswith("РџСЂРµРІС‹С€РµРЅРёРµ РёРЅРґРµРєСЃР° РѕС€РёР±РѕРє"):
            append_finance_posting(finance_postings, day, "РћС‚РіСЂСѓР·РєР° РІ РЅРµСЂРµРєРѕРјРµРЅРґРѕРІР°РЅРЅС‹Р№ СЃР»РѕС‚", -amount)
            continue
        if description in {"РљРѕСЂСЂРµРєС‚РёСЂРѕРІРєР° СЃС‚РѕРёРјРѕСЃС‚Рё СѓСЃР»СѓРі", "РљРѕСЂСЂРµРєС‚РёСЂРѕРІРєРё СЃС‚РѕРёРјРѕСЃС‚Рё СѓСЃР»СѓРі"}:
            append_finance_posting(finance_postings, day, "РџСЂРѕС‡РёРµ РЅР°С‡РёСЃР»РµРЅРёСЏ - РљРѕСЂСЂРµРєС‚РёСЂРѕРІРєР° СЃС‚РѕРёРјРѕСЃС‚Рё СѓСЃР»СѓРі", -amount)
            continue
        if description in {"Р§Р°СЃС‚РёС‡РЅР°СЏ РєРѕРјРїРµРЅСЃР°С†РёСЏ РїРѕРєСѓРїР°С‚РµР»СЋ", "РџРµСЂРµС‡РёСЃР»РµРЅРёСЏ С‡Р°СЃС‚РёС‡РЅС‹С… РєРѕРјРїРµРЅСЃР°С†РёР№ РїРѕРєСѓРїР°С‚РµР»СЏРј"}:
            append_finance_posting(finance_postings, day, "РљРѕРјРїРµРЅСЃР°С†РёРё Рё РґРµРєРѕРјРїРµРЅСЃР°С†РёРё", -amount)
            continue

        if description == "Р‘Р°Р»Р»С‹ Р·Р° РѕС‚Р·С‹РІС‹":
            append_finance_posting(finance_postings, day, "Р‘Р°Р»Р»С‹ Р·Р° РѕС‚Р·С‹РІ", abs(amount))
            continue

        if description == "РљСЂРѕСЃСЃ-РґРѕРєРёРЅРі":
            append_finance_posting(finance_postings, day, "РљСЂРѕСЃСЃ-РґРѕРєРёРЅРі", abs(amount))
            continue

        if operation_type == "InsuranceServiceSellerItem" or description == "РЎС‚СЂР°С…РѕРІР°РЅРёРµ С‚РѕРІР°СЂР° РѕС‚ РјР°СЃСЃРѕРІС‹С… РїРѕРІСЂРµР¶РґРµРЅРёР№":
            append_finance_posting(finance_postings, day, "РЎС‚СЂР°С…РѕРІР°РЅРёРµ С‚РѕРІР°СЂР° РѕС‚ РјР°СЃСЃРѕРІС‹С… РїРѕРІСЂРµР¶РґРµРЅРёР№", abs(amount))
            continue

        if operation_type == "OperationMarketplaceServiceStorage" or description in {
            "РЈСЃР»СѓРіР° СЂР°Р·РјРµС‰РµРЅРёСЏ С‚РѕРІР°СЂРѕРІ РЅР° СЃРєР»Р°РґРµ",
            "Р Р°Р·РјРµС‰РµРЅРёРµ С‚РѕРІР°СЂРѕРІ РЅР° СЃРєР»Р°РґР°С… Ozon",
        }:
            append_finance_posting(finance_postings, day, "Р Р°Р·РјРµС‰РµРЅРёРµ С‚РѕРІР°СЂРѕРІ РЅР° СЃРєР»Р°РґР°С… Ozon", abs(amount))
            continue

        if description.startswith("РћР±СЂР°Р±РѕС‚РєР° С‚РѕРІР°СЂР° РІ СЃРѕСЃС‚Р°РІРµ РіСЂСѓР·РѕРјРµСЃС‚Р° РЅР° FBO"):
            append_finance_posting(finance_postings, day, "РћР±СЂР°Р±РѕС‚РєР° С‚РѕРІР°СЂР° РІ СЃРѕСЃС‚Р°РІРµ РіСЂСѓР·РѕРјРµСЃС‚Р°: РџРѕС€С‚СѓС‡РЅР°СЏ РїСЂРёС‘РјРєР°", abs(amount))
            continue

        if description == "Р’СЂРµРјРµРЅРЅРѕРµ СЂР°Р·РјРµС‰РµРЅРёРµ С‚РѕРІР°СЂР° РїР°СЂС‚РЅРµСЂР°РјРё":
            append_finance_posting(finance_postings, day, "Р’СЂРµРјРµРЅРЅРѕРµ СЂР°Р·РјРµС‰РµРЅРёРµ С‚РѕРІР°СЂР° РїР°СЂС‚РЅРµСЂР°РјРё", abs(amount))
            continue

        if description == "РЈРїР°РєРѕРІРєР° С‚РѕРІР°СЂР° РїР°СЂС‚РЅС‘СЂР°РјРё":
            append_finance_posting(finance_postings, day, "РЈРїР°РєРѕРІРєР° С‚РѕРІР°СЂР° РїР°СЂС‚РЅС‘СЂР°РјРё", abs(amount))
            continue
        if description.lower().startswith("РѕР±РµСЃРїРµС‡РµРЅРёРµ РјР°С‚РµСЂРёР°Р»Р°РјРё РґР»СЏ СѓРїР°РєРѕРІРєРё С‚РѕРІР°СЂР°"):
            append_finance_posting(finance_postings, day, "РћР±РµСЃРїРµС‡РµРЅРёРµ РјР°С‚РµСЂРёР°Р»Р°РјРё РґР»СЏ СѓРїР°РєРѕРІРєРё С‚РѕРІР°СЂР°", abs(amount))
            continue

        if description.startswith("РћР±СЂР°Р±РѕС‚РєР° РѕРїРµСЂР°С†РёРѕРЅРЅС‹С… РѕС€РёР±РѕРє РїСЂРѕРґР°РІС†Р°") or description.startswith("Р–Р°Р»РѕР±С‹ РїРѕРєСѓРїР°С‚РµР»РµР№"):
            append_finance_posting(finance_postings, day, "РћР±СЂР°Р±РѕС‚РєР° РѕРїРµСЂР°С†РёРѕРЅРЅС‹С… РѕС€РёР±РѕРє РїСЂРѕРґР°РІС†Р°", -amount)
            continue

        if "РїРѕС‚РµСЂСЏ РїРѕ РІРёРЅРµ ozon" in description.lower() or "РєРѕРјРїРµРЅСЃР°С†" in description.lower():
            # Propuskaem - eti dannye idut iz report_compensation_items (bolee detalno)
            continue

        append_finance_posting(finance_postings, day, "Р”СЂСѓРіРёРµ СѓСЃР»СѓРіРё", amount)

    for record in compensation_rows:
        effective_date = record["effective_date"]
        if effective_date is None:
            continue
        day = effective_date.astimezone(MSK).strftime("%Y-%m-%d")
        if day not in ordered_units_row["daily"]:
            continue
        row_key = finance_row_key_for_compensation_article(record["article_name"])
        append_finance_posting(
            finance_postings,
            day,
            FINANCE_ROW_META[row_key]["label"],
            abs(as_float(record["amount"])),
        )

    if not has_transaction_items:
        order_items_rows = await conn.fetch(
            """
            SELECT oi.offer_id, oi.sku, oi.quantity, o.created_at
            FROM fact_order_items oi
            JOIN fact_orders o ON o.posting_number = oi.posting_number
            WHERE o.created_at >= $1
              AND o.created_at < $2
            """,
            query_start,
            query_end,
        )
        for record in order_items_rows:
            created_at = record["created_at"]
            if created_at is None:
                continue
            day = created_at.astimezone(MSK).strftime("%Y-%m-%d")
            if day not in ordered_units_row["daily"]:
                continue
            article = (record["offer_id"] or "").strip()
            if not article:
                continue
            item_qty = as_float(record["quantity"], default=0.0)
            if item_qty <= 0:
                continue
            unit_cost = lookup_unit_cost(sku_cost_map, article_cost_map, sku=record["sku"], article=article)
            if unit_cost is None:
                continue
            append_finance_posting(finance_postings, day, "РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ", item_qty * unit_cost)

    for record in returns_rows:
        returned_at = record["returned_at"]
        if returned_at is None:
            continue
        day = returned_at.astimezone(MSK).strftime("%Y-%m-%d")
        if day not in ordered_units_row["daily"]:
            continue
        article = (record["offer_id"] or "").strip()
        if not article:
            continue
        item_qty = as_float(record["quantity"], default=0.0)
        if item_qty <= 0:
            continue
        unit_cost = lookup_unit_cost(sku_cost_map, article_cost_map, sku=record["sku"], article=article)
        if unit_cost is None:
            continue
        # Vozvraty vychitaem iz sebestoimosti
        append_finance_posting(finance_postings, day, "РЎРµР±РµСЃС‚РѕРёРјРѕСЃС‚СЊ", -item_qty * unit_cost)

    for posting in finance_postings:
        description = posting["description"]
        day = posting["day"]
        amount = posting["amount"]
        for row_key, filters in FINANCE_DESCRIPTION_FILTERS.items():
            if description in filters and day in rows_map[row_key]["daily"]:
                rows_map[row_key]["daily"][day] += amount

    client_revenue_days = sorted(set(delivered_postings_by_day.keys()) | set(returned_postings_by_day.keys()))
    posting_day_pairs: List[Tuple[str, str, int]] = []
    for day in client_revenue_days:
        for posting_number in sorted(delivered_postings_by_day.get(day, set())):
            posting_day_pairs.append((day, posting_number, 1))
        for posting_number in sorted(returned_postings_by_day.get(day, set())):
            posting_day_pairs.append((day, posting_number, -1))
    if posting_day_pairs:
        client_revenue_rows = await conn.fetch(
            """
            WITH posting_days(day_key, posting_number, sign) AS (
                SELECT x.day_key::text, x.posting_number::text, x.sign::int
                FROM unnest($1::text[], $2::text[], $3::int[]) AS x(day_key, posting_number, sign)
            )
            SELECT
                pd.day_key,
                sum(pd.sign * coalesce(oi.buyer_paid, oi.price, 0) * abs(coalesce(oi.quantity, 0)))::float8 AS client_revenue
            FROM posting_days pd
            JOIN fact_order_items oi ON oi.posting_number = pd.posting_number
            GROUP BY pd.day_key
            """,
            [row[0] for row in posting_day_pairs],
            [row[1] for row in posting_day_pairs],
            [row[2] for row in posting_day_pairs],
        )
        for record in client_revenue_rows:
            day = str(record["day_key"] or "").strip()
            if day in rows_map["client_revenue"]["daily"]:
                rows_map["client_revenue"]["daily"][day] += float(record["client_revenue"] or 0.0)

    for row_key in rows_map:
        recalculate_row_total(rows_map[row_key], days)

    set_row_from_formula(rows_map, "sales_total", days, lambda day: rows_map["revenue"]["daily"][day] - rows_map["returns_revenue"]["daily"][day])
    set_row_from_formula(rows_map, "returns_total", days, lambda day: rows_map["returns_revenue"]["daily"][day])
    set_row_from_formula(rows_map, "revenue_sales", days, lambda day: rows_map["sales_total"]["daily"][day])
    set_row_from_formula(rows_map, "delivery_services_total", days, lambda day: rows_map["courier_departure"]["daily"][day] + rows_map["dropoff_processing"]["daily"][day] + rows_map["logistics"]["daily"][day] + rows_map["reverse_logistics"]["daily"][day] + rows_map["pickup_courier_delivery"]["daily"][day])
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
        lambda day: rows_map["penalties_total"]["daily"][day]
        + rows_map["other_services_misc"]["daily"][day],
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
    # Vse rashody marketpleysa (vklyuchaya vozvraty)
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
            + rows_map["review_pin"]["daily"][day]
            + rows_map["accelerated_reviews"]["daily"][day]
            + rows_map["premium_plus_subscription"]["daily"][day]
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
    set_row_from_formula(rows_map, "vat_5", days, lambda day: rows_map["client_revenue"]["daily"][day] * 0.05)
    set_row_from_formula(rows_map, "gross_profit", days, lambda day: rows_map["accrued"]["daily"][day] - rows_map["material_cost"]["daily"][day] - rows_map["vat_5"]["daily"][day])
    set_row_from_formula(rows_map, "gross_profit_pct_oz", days, lambda day: safe_divide(rows_map["gross_profit"]["daily"][day], rows_map["revenue_sales"]["daily"][day]))
    set_row_from_formula(rows_map, "gross_profit_pct_accrued", days, lambda day: 0.0 if rows_map["gross_profit"]["daily"][day] < 0 else safe_divide(rows_map["gross_profit"]["daily"][day], rows_map["accrued"]["daily"][day]))

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
    rows_map["gross_profit_pct_accrued"]["total"] = 0.0 if float(rows_map["gross_profit"]["total"] or 0.0) < 0 else safe_divide(rows_map["gross_profit"]["total"], rows_map["accrued"]["total"])

    gross_profit_plan_total = scale_plan_value(PLAN_BASE_VALUES["gross_profit"], revenue_plan_total)
    daily_gross_plan = gross_profit_plan_total / len(days)
    cumulative_gross_plan = 0.0
    for day in days:
        cumulative_gross_plan += daily_gross_plan
        rows_map["gross_profit_plan"]["daily"][day] = cumulative_gross_plan
    rows_map["gross_profit_plan"]["total"] = None  # РќР°РєРѕРїРёС‚РµР»СЊРЅР°СЏ СЃС‚СЂРѕРєР° - РёС‚РѕРіРѕ РЅРµ РїРѕРєР°Р·С‹РІР°РµРј

    return rows_map, days


async def get_finance_report(request: web.Request) -> web.Response:
    month_value = (request.query.get("month") or "").strip()
    if not month_value:
        month_value = datetime.now(timezone.utc).strftime("%Y-%m")

    try:
        month_bounds(month_value)
    except ValueError:
        return web.json_response({"error": "Invalid month format, expected YYYY-MM"}, status=400)

    from src.services.report_services import get_finance_report_data

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        data = await get_finance_report_data(conn, month_value)
    return web.json_response(data)


async def get_finance_report_accrual(request: web.Request) -> web.Response:
    month_value = (request.query.get("month") or "").strip()
    if not month_value:
        month_value = datetime.now(timezone.utc).strftime("%Y-%m")

    try:
        month_bounds(month_value)
    except ValueError:
        return web.json_response({"error": "Invalid month format, expected YYYY-MM"}, status=400)

    from src.services.finance_report_accrual import get_finance_report_accrual_data

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        data = await get_finance_report_accrual_data(conn, month_value)
    return web.json_response(data)


async def get_returns_analytics(request: web.Request) -> web.Response:
    """РћС‚С‡С‘С‚ В«Р’РѕР·РІСЂР°С‚С‹ Рё РѕС‚РјРµРЅС‹В» вЂ” СЃРѕР±С‹С‚РёСЏ Р·Р° РїРµСЂРёРѕРґ + СЃРІРѕРґРєР° РїРѕ РєР»Р°СЃС‚РµСЂР°Рј + РїРѕРІС‚РѕСЂРЅС‹Рµ РєР»РёРµРЅС‚С‹."""
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    try:
        if date_from_raw:
            d = datetime.strptime(date_from_raw, "%Y-%m-%d")
            date_from = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=MSK).astimezone(timezone.utc)
        else:
            date_from = (datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
                         - timedelta(days=29)).astimezone(timezone.utc)
        if date_to_raw:
            d = datetime.strptime(date_to_raw, "%Y-%m-%d")
            date_to_exclusive = (datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=MSK)
                                 + timedelta(days=1)).astimezone(timezone.utc)
        else:
            date_to_exclusive = (datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
                                 + timedelta(days=1)).astimezone(timezone.utc)
    except ValueError:
        return web.json_response({"error": "Invalid date format YYYY-MM-DD"}, status=400)

    from src.services.returns_analytics import get_returns_analytics_data

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        data = await get_returns_analytics_data(conn, date_from, date_to_exclusive)
    return web.json_response(data)


async def get_accruals_comp_by_article_accrual(request: web.Request) -> web.Response:
    """Accrual-СЂРµР¶РёРј РѕС‚С‡С‘С‚Р° В«РќР°С‡РёСЃР»РµРЅРёСЏ РїРѕ Р°СЂС‚РёРєСѓР»Р°РјВ»: Р·РЅР°С‡РµРЅРёСЏ РёР· Р·Р°РєР°Р·РѕРІ,
    СЂР°СЃС…РѕРґС‹ СЃРёРЅС‚РµС‚РёС‡РµСЃРєРёРµ РїРѕ 30Рґ. Р¤РѕСЂРјР°С‚ РѕС‚РІРµС‚Р° СЃРѕРІРјРµСЃС‚РёРј СЃ /api/accruals-comp-by-article.
    """
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    month_raw = (request.query.get("month") or "").strip()
    offer_id_raw = (request.query.get("offer_id") or "").strip()
    offer_id_filter = re.sub(r"\s+", " ", normalize_offer_id(offer_id_raw)).strip().lower()
    limit_raw = (request.query.get("limit") or "1000").strip()

    try:
        limit = max(1, min(5000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    try:
        if month_raw and not date_from_raw and not date_to_raw:
            date_from, date_to_exclusive, _ = month_bounds(month_raw)
        elif date_from_raw:
            date_from_local = datetime.strptime(date_from_raw, "%Y-%m-%d")
            date_from = datetime(
                date_from_local.year, date_from_local.month, date_from_local.day,
                0, 0, 0, tzinfo=MSK,
            ).astimezone(timezone.utc)
        else:
            date_from = (datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
                         - timedelta(days=29)).astimezone(timezone.utc)

        if date_to_raw:
            date_to_local = datetime.strptime(date_to_raw, "%Y-%m-%d")
            date_to_exclusive = (datetime(
                date_to_local.year, date_to_local.month, date_to_local.day,
                0, 0, 0, tzinfo=MSK,
            ) + timedelta(days=1)).astimezone(timezone.utc)
        elif not month_raw:
            date_to_exclusive = (datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
                                 + timedelta(days=1)).astimezone(timezone.utc)
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD or month=YYYY-MM"}, status=400)

    if date_from >= date_to_exclusive:
        return web.json_response({"error": "date_from must be <= date_to"}, status=400)

    from src.services.finance_report_accrual import get_accruals_by_article_accrual_data

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        data = await get_accruals_by_article_accrual_data(conn, date_from, date_to_exclusive)

    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            if offer_id_filter:
                filtered_items: List[Dict[str, Any]] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    display_offer = re.sub(r"\s+", " ", normalize_offer_id(item.get("offer_id"))).strip().lower()
                    normalized_offer = re.sub(
                        r"\s+",
                        " ",
                        normalize_offer_id(item.get("offer_id_normalized") or item.get("offer_id")),
                    ).strip().lower()
                    if offer_id_filter in display_offer or offer_id_filter in normalized_offer:
                        filtered_items.append(item)
                items = filtered_items
            if limit > 0:
                items = items[:limit]
            data["items"] = items
            data["count"] = len(items)
    return web.json_response(data)


async def get_accruals_comp_by_article(request: web.Request) -> web.Response:
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    month_raw = (request.query.get("month") or "").strip()
    offer_id_raw = normalize_offer_id((request.query.get("offer_id") or "").strip())
    offer_id_filter = re.sub(r"\s+", " ", offer_id_raw).strip().lower()
    distribute_raw = (request.query.get("distribute_no_article") or "").strip().lower()
    distribute_no_article = distribute_raw in {"", "1", "true", "yes", "on"}
    limit_raw = (request.query.get("limit") or "1000").strip()

    try:
        limit = max(1, min(5000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    try:
        if month_raw and not date_from_raw and not date_to_raw:
            date_from, date_to_exclusive, _ = month_bounds(month_raw)
        elif date_from_raw:
            date_from_local = datetime.strptime(date_from_raw, "%Y-%m-%d")
            date_from = datetime(
                date_from_local.year,
                date_from_local.month,
                date_from_local.day,
                0,
                0,
                0,
                tzinfo=MSK,
            ).astimezone(timezone.utc)
        else:
            date_from = datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc) - timedelta(days=29)
        if date_to_raw:
            date_to_local = datetime.strptime(date_to_raw, "%Y-%m-%d")
            date_to_exclusive = (
                datetime(
                    date_to_local.year,
                    date_to_local.month,
                    date_to_local.day,
                    0,
                    0,
                    0,
                    tzinfo=MSK,
                )
                + timedelta(days=1)
            ).astimezone(timezone.utc)
        elif not month_raw:
            date_to_exclusive = datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc) + timedelta(days=1)
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD or month=YYYY-MM"}, status=400)

    if date_from is None or date_to_exclusive is None:
        return web.json_response({"error": "Invalid date bounds"}, status=400)
    if date_from >= date_to_exclusive:
        return web.json_response({"error": "date_from must be <= date_to"}, status=400)

    def parse_items_from_raw(raw_data: Any) -> List[Dict[str, Any]]:
        payload = raw_data
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return []
        if not isinstance(payload, dict):
            return []
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        result: List[Dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                result.append(item)
        return result

    def append_amount(bucket: Dict[str, float], offer_id: str, amount: float) -> None:
        if not offer_id:
            return
        bucket[offer_id] = float(bucket.get(offer_id, 0.0) + amount)

    def append_detail(bucket: Dict[str, Dict[str, float]], offer_id: str, label: str, amount: float) -> None:
        if not offer_id or not label:
            return
        nested = bucket.setdefault(offer_id, {})
        nested[label] = float(nested.get(label, 0.0) + amount)

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        canonical_offer_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (offer_key)
                offer_key,
                canonical_offer_id
            FROM (
                SELECT
                    trim(both '''' from trim(coalesce(offer_id, ''))) AS offer_key,
                    trim(both '''' from coalesce(offer_id, '')) AS canonical_offer_id,
                    updated_at AS synced_at
                FROM products
                WHERE coalesce(trim(offer_id), '') <> ''
            ) src
            WHERE offer_key <> ''
              AND canonical_offer_id <> ''
            ORDER BY offer_key, synced_at DESC NULLS LAST
            """
        )
        canonical_offer_by_norm: Dict[str, str] = {}
        for row in canonical_offer_rows:
            offer_key = str(row["offer_key"] or "").strip()
            canonical_offer_id = str(row["canonical_offer_id"] or "").strip()
            if offer_key and canonical_offer_id:
                canonical_offer_by_norm[offer_key] = canonical_offer_id

        sku_rows = await conn.fetch(
            """
            SELECT DISTINCT sku::bigint AS sku
            FROM (
                SELECT sku FROM article_characteristics WHERE sku IS NOT NULL
                UNION ALL
                SELECT fbo_sku_id AS sku FROM report_products_items WHERE fbo_sku_id IS NOT NULL
                UNION ALL
                SELECT fbs_sku_id AS sku FROM report_products_items WHERE fbs_sku_id IS NOT NULL
                UNION ALL
                SELECT sku FROM fact_order_items WHERE sku IS NOT NULL
            ) src
            """
        )
        all_known_skus = sorted(
            {
                int(row["sku"])
                for row in sku_rows
                if row["sku"] is not None
            }
        )
        identity_map = await load_sku_identity_map(conn, all_known_skus)
        sku_to_offer: Dict[str, str] = {
            str(sku): str(data["offer_id"])
            for sku, data in identity_map.items()
            if data.get("offer_id")
        }
        offer_to_sku: Dict[str, str] = {}
        for sku_key, offer_value in sku_to_offer.items():
            norm_offer = normalize_offer_id(offer_value)
            if norm_offer and norm_offer not in offer_to_sku:
                offer_to_sku[norm_offer] = sku_key
        display_offer_by_key: Dict[str, str] = {
            f"sku:{sku_key}": offer_value
            for sku_key, offer_value in sku_to_offer.items()
            if sku_key and offer_value
        }

        def normalize_article(value: Optional[str]) -> str:
            normalized = normalize_offer_id(value)
            if not normalized:
                return ""
            if normalized.lower().startswith("sku:"):
                raw_sku = normalized.split(":", 1)[1].strip()
                if raw_sku.isdigit():
                    # Short numeric markers like "sku:202" usually come from
                    # noisy transaction payloads and should be treated as article codes.
                    if len(raw_sku) <= 6:
                        mapped_short = offer_to_sku.get(raw_sku)
                        if mapped_short:
                            return f"sku:{mapped_short}"
                        return raw_sku
                    if raw_sku in sku_to_offer:
                        return f"sku:{raw_sku}"
                return normalized
            if normalized.isdigit():
                # Legacy short numeric article codes ("202", "403") should stay article-like.
                if len(normalized) <= 6:
                    mapped_short = offer_to_sku.get(normalized)
                    if mapped_short:
                        return f"sku:{mapped_short}"
                    return normalized
                if normalized in sku_to_offer:
                    return f"sku:{normalized}"
            mapped_sku = offer_to_sku.get(normalized)
            if mapped_sku:
                return f"sku:{mapped_sku}"
            return normalized

        tx_rows = await conn.fetch(
            """
            SELECT operation_date, operation_type, description, amount, posting_number, raw_data
            FROM transactions
            WHERE operation_date >= $1
              AND operation_date < $2
            ORDER BY operation_date
            """,
            date_from,
            date_to_exclusive,
        )
        posting_numbers = sorted({str(r["posting_number"]).strip() for r in tx_rows if r["posting_number"]})
        delivered_posting_numbers = sorted(
            {
                str(r["posting_number"]).strip()
                for r in tx_rows
                if r["posting_number"] and str(r["operation_type"] or "").strip() == "OperationAgentDeliveredToCustomer"
            }
        )
        returned_posting_numbers = sorted(
            {
                str(r["posting_number"]).strip()
                for r in tx_rows
                if r["posting_number"] and str(r["operation_type"] or "").strip() == "ClientReturnAgentOperation"
            }
        )
        posting_items_ctx_map: Dict[str, List[Dict[str, Any]]] = {}
        posting_snapshot_ctx_map: Dict[str, List[Dict[str, Any]]] = {}
        if posting_numbers:
            posting_items_ctx_map, _, _, posting_snapshot_ctx_map = await load_posting_context(conn, posting_numbers)

        posting_offer_rows: List[asyncpg.Record] = []
        posting_schema_map: Dict[str, str] = {}
        if posting_numbers:
            posting_schema_rows = await conn.fetch(
                """
                SELECT posting_number, upper(coalesce(delivery_schema, '')) AS delivery_schema
                FROM fact_orders
                WHERE posting_number = any($1::text[])
                """,
                posting_numbers,
            )
            posting_schema_map = {
                str(row["posting_number"] or "").strip(): str(row["delivery_schema"] or "").strip()
                for row in posting_schema_rows
                if str(row["posting_number"] or "").strip()
            }
            posting_offer_rows = await conn.fetch(
                """
                SELECT
                    oi.posting_number,
                    trim(both '''' from trim(coalesce(oi.offer_id, ''))) AS offer_id,
                    sum(abs(coalesce(oi.quantity, 0)))::float8 AS qty,
                    sum(
                        CASE
                            WHEN coalesce(oi.price, 0) <> 0 THEN abs(coalesce(oi.quantity, 0) * oi.price)
                            ELSE abs(coalesce(oi.quantity, 0))
                        END
                    )::float8 AS weight
                FROM fact_order_items oi
                WHERE oi.posting_number = any($1::text[])
                  AND coalesce(trim(oi.offer_id), '') <> ''
                GROUP BY oi.posting_number, trim(both '''' from trim(coalesce(oi.offer_id, '')))
                """,
                posting_numbers,
            )

        posting_offer_map: Dict[str, List[Tuple[str, float]]] = {}
        posting_qty_map: Dict[str, List[Tuple[str, float]]] = {}
        for row in posting_offer_rows:
            posting = str(row["posting_number"] or "").strip()
            offer = normalize_article(row["offer_id"])
            if not posting or not offer:
                continue
            posting_offer_map.setdefault(posting, []).append((offer, float(row["weight"] or 0.0)))
            posting_qty_map.setdefault(posting, []).append((offer, float(row["qty"] or 0.0)))
        for posting in posting_numbers:
            if posting in posting_offer_map and posting in posting_qty_map:
                continue
            items_source = posting_items_ctx_map.get(posting) or posting_snapshot_ctx_map.get(posting) or []
            if not items_source:
                continue
            local_weights: Dict[str, float] = {}
            local_qty: Dict[str, float] = {}
            for item in items_source:
                if not isinstance(item, dict):
                    continue
                article = normalize_article(item.get("offer_id") or extract_item_article(item))
                if not article:
                    continue
                qty = abs(as_float(item.get("quantity"), default=1.0))
                weight = abs(as_float(item.get("price"))) * max(1.0, qty)
                if weight <= 0:
                    weight = max(1.0, qty)
                local_weights[article] = float(local_weights.get(article, 0.0) + weight)
                local_qty[article] = float(local_qty.get(article, 0.0) + qty)
            if local_weights and posting not in posting_offer_map:
                posting_offer_map[posting] = [(offer, value) for offer, value in local_weights.items()]
            if local_qty and posting not in posting_qty_map:
                posting_qty_map[posting] = [(offer, value) for offer, value in local_qty.items()]

        cpc_spent_rows = await conn.fetch(
            """
            SELECT
                cs.sku::bigint AS sku,
                sum(coalesce(cs.spent, 0))::float8 AS ad_spent
            FROM campaign_statistics cs
            JOIN campaigns c ON c.id = cs.campaign_id
            WHERE cs.date >= $1
              AND cs.date < $2
              AND cs.sku IS NOT NULL
              AND (
                c.adv_object_type IS NULL
                OR upper(c.adv_object_type) NOT LIKE '%SEARCH_PROMO%'
              )
            GROUP BY cs.sku
            """,
            date_from,
            date_to_exclusive,
        )
        cpo_spent_rows: List[asyncpg.Record] = []
        try:
            cpo_spent_rows = await conn.fetch(
                """
                SELECT
                    coalesce(promoted_sku, sku)::bigint AS sku,
                    sum(coalesce(expense, 0))::float8 AS ad_spent
                FROM campaign_cpo_orders
                WHERE report_date >= $1::date
                  AND report_date < $2::date
                  AND coalesce(promoted_sku, sku) IS NOT NULL
                GROUP BY coalesce(promoted_sku, sku)
                """,
                date_from.astimezone(MSK).date(),
                date_to_exclusive.astimezone(MSK).date(),
            )
        except Exception:
            cpo_spent_rows = []

        ad_by_offer: Dict[str, float] = {}
        for row in list(cpc_spent_rows) + list(cpo_spent_rows):
            sku_val = normalize_sku_value(row["sku"])
            if sku_val is None:
                continue
            offer_id = (identity_map.get(sku_val) or {}).get("offer_id")
            offer_norm = normalize_offer_id(offer_id)
            if not offer_norm:
                continue
            ad_by_offer[offer_norm] = float(ad_by_offer.get(offer_norm, 0.0) + float(row["ad_spent"] or 0.0))
        ad_rows: List[Dict[str, Any]] = [
            {"offer_id": offer, "ad_spent": amount}
            for offer, amount in ad_by_offer.items()
            if abs(amount) > 1e-9
        ]
        comp_rows = await conn.fetch(
            """
            SELECT offer_id, article_name, amount
            FROM report_compensation_items
            WHERE effective_date >= $1
              AND effective_date < $2
            """,
            date_from,
            date_to_exclusive,
        )
        returns_rows = await conn.fetch(
            """
            WITH returns_all AS (
                SELECT offer_id, sku, quantity, returned_at
                FROM returns
                WHERE returned_at >= $1
                  AND returned_at < $2
                UNION ALL
                SELECT offer_id, sku, quantity, returned_at
                FROM returns_fbo
                WHERE returned_at >= $1
                  AND returned_at < $2
            )
            SELECT offer_id, sku, quantity, returned_at
            FROM returns_all
            """,
            date_from,
            date_to_exclusive,
        )
        order_items_rows = await conn.fetch(
            """
            SELECT oi.offer_id, oi.sku, oi.quantity, o.created_at
            FROM fact_order_items oi
            JOIN fact_orders o ON o.posting_number = oi.posting_number
            WHERE o.created_at >= $1
              AND o.created_at < $2
            """,
            date_from,
            date_to_exclusive,
        )
        fbo_revenue_basis_rows = await conn.fetch(
            """
            SELECT
                trim(both '''' from trim(coalesce(oi.offer_id, ''))) AS offer_id,
                sum(
                    CASE
                        WHEN coalesce(oi.price, 0) <> 0 THEN abs(coalesce(oi.quantity, 0) * oi.price)
                        ELSE abs(coalesce(oi.quantity, 0))
                    END
                )::float8 AS revenue_basis
            FROM fact_order_items oi
            JOIN fact_orders o ON o.posting_number = oi.posting_number
            WHERE o.created_at >= $1
              AND o.created_at < $2
              AND upper(coalesce(o.delivery_schema, '')) = 'FBO'
              AND coalesce(trim(oi.offer_id), '') <> ''
            GROUP BY trim(both '''' from trim(coalesce(oi.offer_id, '')))
            """,
            date_from,
            date_to_exclusive,
        )
        client_revenue_rows = await conn.fetch(
            """
            SELECT
                trim(both '''' from trim(coalesce(oi.offer_id, ''))) AS offer_id,
                sum(
                    CASE
                        WHEN oi.posting_number = ANY($1::text[]) THEN coalesce(oi.buyer_paid, oi.price, 0) * abs(coalesce(oi.quantity, 0))
                        WHEN oi.posting_number = ANY($2::text[]) THEN -coalesce(oi.buyer_paid, oi.price, 0) * abs(coalesce(oi.quantity, 0))
                        ELSE 0
                    END
                )::float8 AS client_revenue
            FROM fact_order_items oi
            WHERE oi.posting_number = ANY($3::text[])
              AND coalesce(trim(oi.offer_id), '') <> ''
            GROUP BY trim(both '''' from trim(coalesce(oi.offer_id, '')))
            """,
            delivered_posting_numbers,
            returned_posting_numbers,
            posting_numbers,
        ) if posting_numbers else []
        article_cost_rows = await conn.fetch(
            """
            SELECT article, sku, unit_cost
            FROM finance_article_costs
            """
        )

    row_values_by_article: Dict[str, Dict[str, float]] = {}
    no_article_by_key: Dict[str, float] = {}
    no_article_ordered_units = 0.0
    no_article_returned_units = 0.0
    sku_cost_map, article_cost_map = build_cost_maps(article_cost_rows)
    has_transaction_items = False
    tx_ordered_units_by_article: Dict[str, float] = {}
    tx_returned_units_by_article: Dict[str, float] = {}
    fbo_revenue_basis_by_article: Dict[str, float] = {
        normalize_article(row["offer_id"]): float(row["revenue_basis"] or 0.0)
        for row in fbo_revenue_basis_rows
        if normalize_article(row["offer_id"]) and float(row["revenue_basis"] or 0.0) > 0
    }
    client_revenue_by_article: Dict[str, float] = {
        normalize_article(row["offer_id"]): float(row["client_revenue"] or 0.0)
        for row in client_revenue_rows
        if normalize_article(row["offer_id"]) and abs(float(row["client_revenue"] or 0.0)) > 1e-9
    }
    fbo_only_distribution_keys = {"piece_acceptance", "zone_sorting", "excess_processing", "fbo_booking_slot_staff", "cross_docking", "warehouse_placement", "valid_preparation", "ozon_delivery_to_pvz"}

    desc_to_key: Dict[str, str] = {}
    for row_key, values in FINANCE_DESCRIPTION_FILTERS.items():
        for value in values:
            normalized = str(value or "").strip().lower()
            if normalized:
                desc_to_key[normalized] = row_key

    service_description_map = {
        "MarketplaceServiceItemDirectFlowLogistic": "Р›РѕРіРёСЃС‚РёРєР°",
        "MarketplaceServiceItemReturnFlowLogistic": "РћР±СЂР°С‚РЅР°СЏ Р»РѕРіРёСЃС‚РёРєР°",
        "MarketplaceServiceItemDropoffPVZ": "РћР±СЂР°Р±РѕС‚РєР° РѕС‚РїСЂР°РІР»РµРЅРёСЏ Drop-off",
        "MarketplaceServiceItemDropoffSC": "РћР±СЂР°Р±РѕС‚РєР° РѕС‚РїСЂР°РІР»РµРЅРёСЏ Drop-off",
        "MarketplaceServiceItemRedistributionReturnsPVZ": "РћР±СЂР°Р±РѕС‚РєР° РІРѕР·РІСЂР°С‚РѕРІ, РѕС‚РјРµРЅ Рё РЅРµРІС‹РєСѓРїРѕРІ РїР°СЂС‚РЅС‘СЂР°РјРё",
        "MarketplaceServiceItemRedistributionDropOffApvz": "РћР±СЂР°Р±РѕС‚РєР° РѕС‚РїСЂР°РІР»РµРЅРёСЏ Drop-off РїР°СЂС‚РЅС‘СЂР°РјРё (РђРџР’Р—)",
        "MarketplaceServiceItemRedistributionLastMileCourier": "Р”РѕСЃС‚Р°РІРєР° РґРѕ РјРµСЃС‚Р° РІС‹РґР°С‡Рё",
        "MarketplaceServiceItemRedistributionLastMilePVZ": "Р”РѕСЃС‚Р°РІРєР° РґРѕ РјРµСЃС‚Р° РІС‹РґР°С‡Рё",
        "MarketplaceServiceItemTemporaryStorageRedistribution": "Р’СЂРµРјРµРЅРЅРѕРµ СЂР°Р·РјРµС‰РµРЅРёРµ С‚РѕРІР°СЂР° РїР°СЂС‚РЅРµСЂР°РјРё",
        "MarketplaceServiceItemPackageRedistribution": "РЈРїР°РєРѕРІРєР° С‚РѕРІР°СЂР° РїР°СЂС‚РЅС‘СЂР°РјРё",
        "MarketplaceRedistributionOfAcquiringOperation": "Р­РєРІР°Р№СЂРёРЅРі",
    }

    def add_row_value(offer: str, key: str, amount: float) -> None:
        if not offer or not key or abs(amount) <= 1e-9:
            return
        article_values = row_values_by_article.setdefault(offer, {})
        article_values[key] = float(article_values.get(key, 0.0) + amount)

    def add_no_article(key: str, amount: float) -> None:
        if not key or abs(amount) <= 1e-9:
            return
        no_article_by_key[key] = float(no_article_by_key.get(key, 0.0) + amount)

    def append_units(bucket: Dict[str, float], offer_id: str, qty: float) -> None:
        offer = normalize_article(offer_id)
        if not offer:
            return
        if abs(qty) <= 1e-9:
            return
        bucket[offer] = float(bucket.get(offer, 0.0) + qty)

    def item_weights(items: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
        weighted: List[Tuple[str, float]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            article_raw = item.get("offer_id") or extract_item_article(item)
            article = normalize_article(article_raw)
            if not article:
                continue
            qty = abs(as_float(item.get("quantity"), default=1.0))
            weight = abs(as_float(item.get("price"))) * max(1.0, qty)
            if weight <= 0:
                weight = max(1.0, qty)
            weighted.append((article, weight))
        return weighted

    def distribute_amount(key: str, amount: float, offers_with_weights: List[Tuple[str, float]]) -> None:
        if abs(amount) <= 1e-9:
            return
        if not offers_with_weights:
            add_no_article(key, amount)
            return
        total_weight = float(sum(max(0.0, w) for _, w in offers_with_weights))
        if total_weight <= 0:
            total_weight = float(len(offers_with_weights))
            offers_with_weights = [(offer, 1.0) for offer, _ in offers_with_weights]
        for offer, weight in offers_with_weights:
            if not offer:
                continue
            share = amount * (max(0.0, weight) / total_weight)
            add_row_value(offer, key, share)

    for tx in tx_rows:
        operation_type = str(tx["operation_type"] or "").strip()
        description = to_mojibake_cp1251_utf8(str(tx["description"] or "").strip())
        amount = as_float(tx["amount"])
        amount_abs = abs(amount)
        posting_number = str(tx["posting_number"] or "").strip()

        payload = tx["raw_data"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload_items = parse_items_from_raw(payload)
        items_source = posting_items_ctx_map.get(posting_number) or payload_items or posting_snapshot_ctx_map.get(posting_number) or []
        offers_with_weights = item_weights(items_source)
        if not offers_with_weights and posting_number and posting_number in posting_offer_map:
            offers_with_weights = posting_offer_map[posting_number]
        offers_with_weights_fbo = offers_with_weights if posting_schema_map.get(posting_number) == "FBO" else []

        if operation_type == "OperationCourierArrangement":
            distribute_amount("courier_departure", amount_abs, offers_with_weights)
            continue

        if operation_type == "OperationCourierPickUpDelivery":
            distribute_amount("pickup_courier_delivery", amount_abs, offers_with_weights)
            continue

        if operation_type == "OperationMarketplaceServiceStorage" or description in {
            "РЈСЃР»СѓРіР° СЂР°Р·РјРµС‰РµРЅРёСЏ С‚РѕРІР°СЂРѕРІ РЅР° СЃРєР»Р°РґРµ",
            "Р Р°Р·РјРµС‰РµРЅРёРµ С‚РѕРІР°СЂРѕРІ РЅР° СЃРєР»Р°РґР°С… Ozon",
        }:
            distribute_amount("warehouse_placement", amount_abs, offers_with_weights_fbo)
            continue

        if operation_type == "OperationAgentDeliveredToCustomer":
            accruals_for_sale = as_float(payload.get("accruals_for_sale"))
            sale_commission = as_float(payload.get("sale_commission"))
            if accruals_for_sale > 0:
                if items_source and posting_number and posting_number in posting_items_ctx_map:
                    has_transaction_items = True
                if items_source:
                    for item in items_source:
                        article_raw = item.get("offer_id") or extract_item_article(item)
                        qty = as_float(item.get("quantity"), default=1.0)
                        if normalize_article(article_raw):
                            append_units(tx_ordered_units_by_article, article_raw, qty)
                        else:
                            no_article_ordered_units += qty
                elif posting_number and posting_number in posting_qty_map:
                    for article, qty in posting_qty_map[posting_number]:
                        if normalize_article(article):
                            append_units(tx_ordered_units_by_article, article, qty)
                        else:
                            no_article_ordered_units += qty

                distribute_amount("revenue", max(accruals_for_sale, 0.0), offers_with_weights)
                distribute_amount("sale_commission", abs(min(sale_commission, 0.0)), offers_with_weights)

                for item in items_source:
                    if not isinstance(item, dict):
                        continue
                    article_raw = item.get("offer_id") or extract_item_article(item)
                    article = normalize_article(article_raw)
                    qty = as_float(item.get("quantity"), default=1.0)
                    if not article or qty <= 0:
                        continue
                    unit_cost = lookup_unit_cost(
                        sku_cost_map,
                        article_cost_map,
                        sku=item.get("sku"),
                        article=article_raw,
                    )
                    if unit_cost is not None:
                        add_row_value(article, "material_cost", qty * unit_cost)

            services = payload.get("services") if isinstance(payload.get("services"), list) else []
            for service in services:
                service_name = service.get("name")
                service_price = abs(as_float(service.get("price")))
                service_desc = fix_mojibake_cp1251_utf8(service_description_map.get(service_name, "Р”СЂСѓРіРёРµ СѓСЃР»СѓРіРё"))
                row_key = desc_to_key.get(service_desc.strip().lower())
                if row_key:
                    distribute_amount(
                        row_key,
                        service_price,
                        offers_with_weights_fbo if row_key in fbo_only_distribution_keys else offers_with_weights,
                    )
            continue

        if operation_type == "ClientReturnAgentOperation":
            accruals_for_sale = as_float(payload.get("accruals_for_sale"))
            sale_commission = as_float(payload.get("sale_commission"))
            if abs(accruals_for_sale) <= 1e-9 and abs(sale_commission) <= 1e-9:
                continue

            if items_source:
                for item in items_source:
                    article_raw = item.get("offer_id") or extract_item_article(item)
                    qty = as_float(item.get("quantity"), default=1.0)
                    if normalize_article(article_raw):
                        append_units(tx_returned_units_by_article, article_raw, qty)
                    else:
                        no_article_returned_units += qty
            elif posting_number and posting_number in posting_qty_map:
                for article, qty in posting_qty_map[posting_number]:
                    if normalize_article(article):
                        append_units(tx_returned_units_by_article, article, qty)
                    else:
                        no_article_returned_units += qty

            distribute_amount("returns_revenue", abs(min(accruals_for_sale, 0.0)), offers_with_weights)
            distribute_amount("return_commission", max(sale_commission, 0.0), offers_with_weights)

            for item in items_source:
                if not isinstance(item, dict):
                    continue
                article_raw = item.get("offer_id") or extract_item_article(item)
                article = normalize_article(article_raw)
                qty = as_float(item.get("quantity"), default=1.0)
                if not article or qty <= 0:
                    continue
                unit_cost = lookup_unit_cost(
                    sku_cost_map,
                    article_cost_map,
                    sku=item.get("sku"),
                    article=article_raw,
                )
                if unit_cost is not None:
                    add_row_value(article, "material_cost", -qty * unit_cost)
            continue

        if description == "Р”РѕСЃС‚Р°РІРєР° Рё РѕР±СЂР°Р±РѕС‚РєР° РІРѕР·РІСЂР°С‚Р°, РѕС‚РјРµРЅС‹, РЅРµРІС‹РєСѓРїР°":
            services = payload.get("services") if isinstance(payload.get("services"), list) else []
            matched_total = 0.0
            for service in services:
                service_name = service.get("name")
                service_price = abs(as_float(service.get("price")))
                matched_total += service_price
                service_desc = fix_mojibake_cp1251_utf8(service_description_map.get(service_name, "РћР±СЂР°С‚РЅР°СЏ Р»РѕРіРёСЃС‚РёРєР°"))
                row_key = desc_to_key.get(service_desc.strip().lower())
                if row_key:
                    distribute_amount(row_key, service_price, offers_with_weights)
            residual = abs(amount) - matched_total
            if residual > 1e-9:
                distribute_amount("reverse_logistics", residual, offers_with_weights)
            continue

        if description == "РћРїР»Р°С‚Р° СЌРєРІР°Р№СЂРёРЅРіР°":
            distribute_amount("acquiring", -amount, offers_with_weights)
            continue
        if description == "РџРѕРґРїРёСЃРєР° Premium Plus":
            distribute_amount("premium_plus_subscription", abs(amount), offers_with_weights)
            continue
        if description == "РћРїР»Р°С‚Р° Р·Р° РєР»РёРє":
            distribute_amount("pay_per_click", abs(amount), offers_with_weights)
            continue
        if description == "Р—Р°РєСЂРµРїР»РµРЅРёРµ РѕС‚Р·С‹РІР°":
            distribute_amount("review_pin", abs(amount), offers_with_weights)
            continue
        if description == "РџСЂРѕРґРІРёР¶РµРЅРёРµ СЃ РѕРїР»Р°С‚РѕР№ Р·Р° Р·Р°РєР°Р·":
            # Не используем начисления этого типа как источник:
            # корректно разложить их по артикулам нельзя.
            # Рекламу берём из campaign_statistics.
            continue
        if description == "РЈСЃРєРѕСЂРµРЅРЅС‹Р№ СЃР±РѕСЂ РѕС‚Р·С‹РІРѕРІ":
            distribute_amount("accelerated_reviews", abs(amount), offers_with_weights)
            continue
        if description == "РћС‚РіСЂСѓР·РєР° РІ РЅРµСЂРµРєРѕРјРµРЅРґРѕРІР°РЅРЅС‹Р№ СЃР»РѕС‚" or description == "РћС‚РіСЂСѓР·РєР° РІ РЅРµСЂРµРєРѕРјРµРЅРґРѕРІР°РЅРЅС‹Р№ СЃР»РѕС‚ - РѕС‚РјРµРЅР° РЅР°С‡РёСЃР»РµРЅРёСЏ":
            distribute_amount("penalty_non_recommended_slot", -amount, offers_with_weights)
            continue
        if description.startswith("РџСЂРµРІС‹С€РµРЅРёРµ РёРЅРґРµРєСЃР° РѕС€РёР±РѕРє"):
            distribute_amount("penalty_non_recommended_slot", -amount, offers_with_weights)
            continue
        if description in {"РљРѕСЂСЂРµРєС‚РёСЂРѕРІРєР° СЃС‚РѕРёРјРѕСЃС‚Рё СѓСЃР»СѓРі", "РљРѕСЂСЂРµРєС‚РёСЂРѕРІРєРё СЃС‚РѕРёРјРѕСЃС‚Рё СѓСЃР»СѓРі"}:
            distribute_amount("other_accrual_adjustments", -amount, offers_with_weights)
            continue
        if description in {"Р§Р°СЃС‚РёС‡РЅР°СЏ РєРѕРјРїРµРЅСЃР°С†РёСЏ РїРѕРєСѓРїР°С‚РµР»СЋ", "РџРµСЂРµС‡РёСЃР»РµРЅРёСЏ С‡Р°СЃС‚РёС‡РЅС‹С… РєРѕРјРїРµРЅСЃР°С†РёР№ РїРѕРєСѓРїР°С‚РµР»СЏРј"}:
            distribute_amount("compensations", -amount, offers_with_weights)
            continue
        if description == "Р‘Р°Р»Р»С‹ Р·Р° РѕС‚Р·С‹РІС‹":
            distribute_amount("review_points", abs(amount), offers_with_weights)
            continue
        if description == "РљСЂРѕСЃСЃ-РґРѕРєРёРЅРі":
            distribute_amount("cross_docking", abs(amount), offers_with_weights)
            continue
        if operation_type == "InsuranceServiceSellerItem" or description == "РЎС‚СЂР°С…РѕРІР°РЅРёРµ С‚РѕРІР°СЂР° РѕС‚ РјР°СЃСЃРѕРІС‹С… РїРѕРІСЂРµР¶РґРµРЅРёР№":
            distribute_amount("star_products", abs(amount), offers_with_weights)
            continue
        if description.startswith("РћР±СЂР°Р±РѕС‚РєР° С‚РѕРІР°СЂР° РІ СЃРѕСЃС‚Р°РІРµ РіСЂСѓР·РѕРјРµСЃС‚Р° РЅР° FBO"):
            distribute_amount("piece_acceptance", abs(amount), offers_with_weights_fbo)
            continue
        if description == "Р’СЂРµРјРµРЅРЅРѕРµ СЂР°Р·РјРµС‰РµРЅРёРµ С‚РѕРІР°СЂР° РїР°СЂС‚РЅРµСЂР°РјРё":
            distribute_amount("temporary_partner_storage", abs(amount), offers_with_weights)
            continue
        if description == "РЈРїР°РєРѕРІРєР° С‚РѕРІР°СЂР° РїР°СЂС‚РЅС‘СЂР°РјРё":
            distribute_amount("partner_packaging", abs(amount), offers_with_weights)
            continue
        if description.lower().startswith("РѕР±РµСЃРїРµС‡РµРЅРёРµ РјР°С‚РµСЂРёР°Р»Р°РјРё РґР»СЏ СѓРїР°РєРѕРІРєРё С‚РѕРІР°СЂР°"):
            distribute_amount("packaging_materials", abs(amount), offers_with_weights)
            continue
        if description.startswith("РћР±СЂР°Р±РѕС‚РєР° РѕРїРµСЂР°С†РёРѕРЅРЅС‹С… РѕС€РёР±РѕРє РїСЂРѕРґР°РІС†Р°") or description.startswith("Р–Р°Р»РѕР±С‹ РїРѕРєСѓРїР°С‚РµР»РµР№"):
            distribute_amount("operational_errors", -amount, offers_with_weights)
            continue
        if "РїРѕС‚РµСЂСЏ РїРѕ РІРёРЅРµ ozon" in description.lower() or "РєРѕРјРїРµРЅСЃР°С†" in description.lower():
            continue

        distribute_amount("other_services", amount, offers_with_weights)

    for row in comp_rows:
        row_key = finance_row_key_for_compensation_article(row["article_name"])
        offer = normalize_article(row["offer_id"])
        amount_abs = abs(as_float(row["amount"]))
        if amount_abs <= 0:
            continue
        if offer:
            add_row_value(offer, row_key, amount_abs)
        else:
            add_no_article(row_key, amount_abs)

    for row in ad_rows:
        offer = normalize_article(row["offer_id"])
        amount_abs = abs(as_float(row["ad_spent"]))
        if not offer or amount_abs <= 0:
            continue
        add_row_value(offer, "ad_spend", amount_abs)

    if not has_transaction_items:
        for record in order_items_rows:
            article_raw = record["offer_id"]
            article = normalize_article(article_raw)
            qty = as_float(record["quantity"], default=0.0)
            if not article or qty <= 0:
                continue
            unit_cost = lookup_unit_cost(
                sku_cost_map,
                article_cost_map,
                sku=record["sku"],
                article=article_raw,
            )
            if unit_cost is not None:
                add_row_value(article, "material_cost", qty * unit_cost)

    for record in returns_rows:
        article_raw = record["offer_id"]
        article = normalize_article(article_raw)
        qty = as_float(record["quantity"], default=0.0)
        if not article or qty <= 0:
            continue
        unit_cost = lookup_unit_cost(
            sku_cost_map,
            article_cost_map,
            sku=record["sku"],
            article=article_raw,
        )
        if unit_cost is not None:
            add_row_value(article, "material_cost", -qty * unit_cost)

    sold_units_by_article: Dict[str, float] = dict(tx_ordered_units_by_article)
    returned_units_by_article: Dict[str, float] = dict(tx_returned_units_by_article)

    # Canonical weights for courier pickup distribution by sold units (214/210 only).
    courier_pickup_units_weights: Dict[str, float] = {"sku:1847228789": 0.0, "sku:1563922702": 0.0}
    for offer_key, qty in sold_units_by_article.items():
        text = str(offer_key or "").strip().lower()
        if text == "sku:1847228789":
            courier_pickup_units_weights["sku:1847228789"] += float(qty or 0.0)
        elif text == "sku:1563922702":
            courier_pickup_units_weights["sku:1563922702"] += float(qty or 0.0)
    courier_pickup_units_weights = {k: v for k, v in courier_pickup_units_weights.items() if v > 0}

    revenue_by_article: Dict[str, float] = {
        offer: float(values.get("revenue", 0.0))
        for offer, values in row_values_by_article.items()
        if float(values.get("revenue", 0.0)) > 0
    }

    # Спец-распределение курьерского забора только по двум SKU:
    # 214 уличный 700 (1847228789) и 210 уличный 500 (1563922702).
    target_sku_to_offer = {
        1847228789: "sku:1847228789",
        1563922702: "sku:1563922702",
    }
    target_courier_offers = set(target_sku_to_offer.values())
    target_courier_offers = {offer for offer in target_courier_offers if offer}
    courier_pickup_revenue_by_article: Dict[str, float] = {}
    try:
        courier_rows = await conn.fetch(
            """
            SELECT
                trim(both '''' from trim(coalesce(oi.offer_id, ''))) AS offer_id,
                sum(
                    CASE
                        WHEN coalesce(oi.buyer_paid, 0) <> 0 THEN abs(coalesce(oi.quantity, 0) * oi.buyer_paid)
                        WHEN coalesce(oi.price, 0) <> 0 THEN abs(coalesce(oi.quantity, 0) * oi.price)
                        ELSE abs(coalesce(oi.quantity, 0))
                    END
                )::float8 AS revenue_basis
            FROM fact_order_items oi
            JOIN fact_orders fo ON fo.posting_number = oi.posting_number
            LEFT JOIN postings p ON p.posting_number = oi.posting_number
            WHERE fo.created_at >= $1
              AND fo.created_at < $2
              AND upper(coalesce(fo.delivery_schema, '')) = 'FBS'
              AND coalesce(trim(oi.offer_id), '') <> ''
              AND (
                    coalesce(fo.shipping_warehouse_name, '') ILIKE '%РћРґРёРЅС†РѕРІРѕ%'
                    OR coalesce(fo.shipping_warehouse_name, '') ILIKE '%Рђ2 Р­РєСЃРїСЂРµСЃСЃ%'
                    OR p.delivery_method_name = 'Р”РѕСЃС‚Р°РІРєР° Ozon РєСѓСЂСЊРµСЂСѓ, РћРґРёРЅС†РѕРІРѕ'
              )
            GROUP BY trim(both '''' from trim(coalesce(oi.offer_id, '')))
            """,
            date_from,
            date_to_exclusive,
        )
        target_qty_rows = await conn.fetch(
            """
            SELECT oi.sku::bigint AS sku, sum(abs(coalesce(oi.quantity, 0)))::float8 AS qty
            FROM fact_order_items oi
            JOIN fact_orders fo ON fo.posting_number = oi.posting_number
            WHERE fo.created_at >= $1
              AND fo.created_at < $2
              AND oi.sku IS NOT NULL
              AND oi.sku::bigint = any($3::bigint[])
            GROUP BY oi.sku::bigint
            """,
            date_from,
            date_to_exclusive,
            list(target_sku_to_offer.keys()),
        )
        raw_courier_pickup_revenue_by_article = {
            normalize_article(row["offer_id"]): float(row["revenue_basis"] or 0.0)
            for row in courier_rows
            if normalize_article(row["offer_id"]) and float(row["revenue_basis"] or 0.0) > 0
        }
        # keep only 214*/210* and map to canonical target offers
        courier_pickup_revenue_by_article = {"sku:1847228789": 0.0, "sku:1563922702": 0.0}
        for offer, revenue in raw_courier_pickup_revenue_by_article.items():
            text = str(offer or "").strip().lower()
            if text == "sku:1847228789":
                courier_pickup_revenue_by_article["sku:1847228789"] += float(revenue)
            elif text == "sku:1563922702":
                courier_pickup_revenue_by_article["sku:1563922702"] += float(revenue)
        courier_pickup_revenue_by_article = {k: v for k, v in courier_pickup_revenue_by_article.items() if v > 0}
        target_sold_units: Dict[str, float] = {}
        for rec in target_qty_rows:
            sku_val = normalize_sku_value(rec["sku"])
            target_offer = target_sku_to_offer.get(int(sku_val)) if sku_val is not None else None
            if not target_offer:
                continue
            qty = abs(as_float(rec["qty"], default=0.0))
            if qty <= 0:
                continue
            target_sold_units[target_offer] = float(target_sold_units.get(target_offer, 0.0) + qty)
        if target_sold_units:
            courier_pickup_revenue_by_article = target_sold_units
        elif not courier_pickup_revenue_by_article:
            courier_pickup_revenue_by_article = {offer: 1.0 for offer in target_courier_offers}
    except Exception:
        # РќРµ Р±Р»РѕРєРёСЂСѓРµРј РѕС‚С‡С‘С‚, РµСЃР»Рё СЃРїРµС†-РІС‹Р±РѕСЂРєР° РЅРµРґРѕСЃС‚СѓРїРЅР°; РїСЂРѕСЃС‚Рѕ РёСЃРїРѕР»СЊР·СѓРµРј РѕР±С‰РёР№ fallback.
        courier_pickup_revenue_by_article = {offer: 1.0 for offer in target_courier_offers}

    if distribute_no_article and no_article_by_key:
        if courier_pickup_units_weights:
            courier_pickup_revenue_by_article = dict(courier_pickup_units_weights)
        for row_key, total_amount in no_article_by_key.items():
            if abs(total_amount) <= 1e-9:
                continue
            if row_key in {"courier_departure", "pickup_courier_delivery"} and courier_pickup_revenue_by_article:
                revenue_source = courier_pickup_revenue_by_article
            else:
                revenue_source = fbo_revenue_basis_by_article if row_key in fbo_only_distribution_keys else revenue_by_article
            revenue_base_total = float(sum(max(0.0, value) for value in revenue_source.values()))
            if revenue_base_total <= 0:
                continue
            for offer, revenue in revenue_source.items():
                if revenue <= 0:
                    continue
                share = revenue / revenue_base_total
                add_row_value(offer, row_key, total_amount * share)

    column_keys = [
        "ordered_units", "returned_units", "returns_pct", "revenue_sales", "client_revenue", "marketplace_expenses", "revenue", "returns_total", "returns_revenue",
        "ozon_fee_total", "sale_commission", "return_commission", "delivery_services_total", "pickup_processing", "courier_departure",
        "dropoff_processing", "logistics", "reverse_logistics", "pickup_courier_delivery", "agent_services_total", "partner_returns_processing",
        "star_products", "temporary_partner_storage", "partner_dropoff_processing", "partner_packaging", "delivery_to_pickup", "acquiring", "fbo_services_total",
        "fbo_booking_slot_staff", "cross_docking", "fbo_acceptance_services", "fbo_delivery_to_warehouse",
        "fbo_storage_services", "valid_preparation", "ozon_delivery_to_pvz", "warehouse_placement", "piece_acceptance", "zone_sorting",
        "excess_processing", "promotion_total", "ad_spend", "premium_plus_subscription", "pay_per_click", "review_points", "review_pin", "accelerated_reviews", "other_services",
        "pickup_collection_total", "other_grouped", "marketplace_expenses_pct", "marketing_pct",
        "accrued", "material_cost", "vat_5", "gross_profit", "gross_profit_pct_oz", "gross_profit_pct_accrued",
    ]
    label_overrides = {
        "revenue_sales": "−Выручка / продажи - возвраты",
        "client_revenue": "Оплачено клиентом (факт)",
        "returns_total": "−Возвраты, руб.",
        "ozon_fee_total": "−Вознаграждение Ozon",
        "delivery_services_total": "−Услуги доставки",
        "agent_services_total": "−Услуги партнёров",
        "fbo_services_total": "−Услуги FBO",
        "promotion_total": "−Реклама (НАЧ)",
        "ad_spend": "−Реклама",
        "other_services": "−Другие услуги и штрафы",
        "all_expenses": "−Расходы маркетплейса (все)",
    }
    columns = []
    for key in column_keys:
        meta = FINANCE_ROW_META.get(key, {"label": key, "format": "number"})
        label = label_overrides.get(key, meta["label"])
        if key == "pickup_collection_total":
            label = "−Забор товара курьером Ozon"
        elif key == "other_grouped":
            label = "−Прочее (сгруппировано)"
        columns.append({"key": key, "label": label, "format": meta.get("format", "number")})

    def compute_derived(values: Dict[str, float]) -> Dict[str, float]:
        v = dict(values)
        v["returns_pct"] = float(safe_divide(v.get("returned_units", 0.0), v.get("ordered_units", 0.0)) or 0.0)
        v["sales_total"] = float(v.get("revenue", 0.0) - v.get("returns_revenue", 0.0))
        v["returns_total"] = float(v.get("returns_revenue", 0.0))
        v["revenue_sales"] = float(v.get("sales_total", 0.0))
        v["ozon_fee_total"] = float(v.get("sale_commission", 0.0) - v.get("return_commission", 0.0))
        v["delivery_services_total"] = float(v.get("pickup_processing", 0.0) + v.get("courier_departure", 0.0) + v.get("dropoff_processing", 0.0) + v.get("logistics", 0.0) + v.get("reverse_logistics", 0.0) + v.get("pickup_courier_delivery", 0.0))
        v["agent_services_total"] = float(v.get("partner_returns_processing", 0.0) + v.get("star_products", 0.0) + v.get("temporary_partner_storage", 0.0) + v.get("partner_dropoff_processing", 0.0) + v.get("partner_packaging", 0.0) + v.get("delivery_to_pickup", 0.0) + v.get("acquiring", 0.0))
        v["fbo_cargo_processing"] = float(v.get("piece_acceptance", 0.0) + v.get("zone_sorting", 0.0) + v.get("excess_processing", 0.0))
        v["fbo_acceptance_services"] = float(v.get("fbo_cargo_processing", 0.0) + v.get("fbo_booking_slot_staff", 0.0))
        v["fbo_delivery_to_warehouse"] = float(v.get("cross_docking", 0.0))
        v["fbo_storage_services"] = float(v.get("warehouse_placement", 0.0) + v.get("valid_preparation", 0.0) + v.get("ozon_delivery_to_pvz", 0.0))
        v["fbo_services_total"] = float(v.get("fbo_acceptance_services", 0.0) + v.get("fbo_delivery_to_warehouse", 0.0) + v.get("fbo_storage_services", 0.0))
        # Для cash-витрины берём рекламу по факту campaign_statistics (по SKU/артикулу):
        # "−Реклама (НАЧ)" = ad_spend, чтобы не было пропорционального распределения из начислений.
        v["promotion_total"] = float(v.get("ad_spend", 0.0))
        v["penalties_total"] = float(v.get("penalty_non_recommended_slot", 0.0))
        v["other_services_misc"] = float(v.get("utilization", 0.0) + v.get("packaging_materials", 0.0) + v.get("operational_errors", 0.0) + v.get("temporary_sc_storage", 0.0))
        v["other_services"] = float(
            v.get("penalties_total", 0.0)
            + v.get("other_services_misc", 0.0)
        )
        v["pickup_collection_total"] = float(v.get("courier_departure", 0.0) + v.get("pickup_courier_delivery", 0.0))
        v["other_grouped"] = float(
            v.get("other_services", 0.0)
            + v.get("compensations", 0.0)
            + v.get("other_accrual_adjustments", 0.0)
            + v.get("shortage_retention", 0.0)
            + v.get("loans_factoring", 0.0)
        )
        v["marketplace_expenses"] = float(
            v.get("ozon_fee_total", 0.0)
            + v.get("delivery_services_total", 0.0)
            + v.get("agent_services_total", 0.0)
            + v.get("fbo_services_total", 0.0)
            + v.get("promotion_total", 0.0)
            + v.get("other_services", 0.0)
        )
        v["all_expenses"] = float(v.get("returns_revenue", 0.0) + v.get("marketplace_expenses", 0.0))
        v["marketplace_expenses_pct"] = float(safe_divide(v["marketplace_expenses"], v["revenue_sales"]) or 0.0)
        marketing_value = float(v.get("ad_spend", 0.0))
        v["marketing_pct"] = float(safe_divide(marketing_value, v["revenue_sales"]) or 0.0)
        v["accrued"] = float(
            v["revenue_sales"]
            - v["marketplace_expenses"]
            - v.get("compensations", 0.0)
            - v.get("other_accrual_adjustments", 0.0)
        )
        v["vat_5"] = float(v.get("client_revenue", 0.0) * 0.05)
        v["gross_profit"] = float(v["accrued"] - v.get("material_cost", 0.0) - v["vat_5"])
        v["gross_profit_pct_oz"] = float(safe_divide(v["gross_profit"], v["revenue_sales"]) or 0.0)
        v["gross_profit_pct_accrued"] = 0.0 if v["gross_profit"] < 0 else float(safe_divide(v["gross_profit"], v["accrued"]) or 0.0)
        return v

    all_value_keys = set(column_keys) | set(FINANCE_ROW_META.keys())
    all_offers = set(row_values_by_article.keys()) | set(sold_units_by_article.keys()) | set(returned_units_by_article.keys()) | set(client_revenue_by_article.keys())
    def resolve_display_offer(offer_key: str) -> str:
        text = str(offer_key or "").strip()
        if text.lower().startswith("sku:"):
            sku_raw = text.split(":", 1)[1].strip()
            if sku_raw.isdigit() and len(sku_raw) <= 6:
                return sku_raw
            sku_val = normalize_sku_value(sku_raw)
            if sku_val is not None:
                identity_offer = normalize_offer_id((identity_map.get(int(sku_val)) or {}).get("offer_id"))
                if identity_offer:
                    return identity_offer
        mapped = display_offer_by_key.get(text)
        if mapped:
            return str(mapped)
        return canonical_offer_by_norm.get(text, text)

    items_transposed: List[Dict[str, Any]] = []
    total_values: Dict[str, float] = {}
    for offer in all_offers:
        values = {key: 0.0 for key in all_value_keys}
        values["ordered_units"] = float(sold_units_by_article.get(offer, 0.0))
        values["returned_units"] = float(returned_units_by_article.get(offer, 0.0))
        values["client_revenue"] = float(client_revenue_by_article.get(offer, 0.0))
        for key, amount in row_values_by_article.get(offer, {}).items():
            if key in values:
                values[key] = float(values[key] + amount)
        values = compute_derived(values)
        for key, value in values.items():
            if str(key).endswith("_pct"):
                continue
            total_values[key] = float(total_values.get(key, 0.0) + float(value or 0.0))
        items_transposed.append(
            {
                "offer_id": resolve_display_offer(offer),
                "offer_id_normalized": offer,
                "values": values,
            }
        )

    items_transposed.sort(key=lambda x: (-abs(float(x["values"].get("accrued", 0.0))), str(x["offer_id"] or "")))
    if offer_id_filter:
        filtered_items: List[Dict[str, Any]] = []
        for item in items_transposed:
            normalized_key = re.sub(r"\s+", " ", normalize_offer_id(item.get("offer_id_normalized"))).strip().lower()
            display_offer = re.sub(r"\s+", " ", normalize_offer_id(item.get("offer_id"))).strip().lower()
            if offer_id_filter in normalized_key or offer_id_filter in display_offer:
                filtered_items.append(item)
        items_transposed = filtered_items
    if limit > 0:
        items_transposed = items_transposed[:limit]
    total_values["ordered_units"] = float(total_values.get("ordered_units", 0.0) + no_article_ordered_units)
    total_values["returned_units"] = float(total_values.get("returned_units", 0.0) + no_article_returned_units)
    total_values = compute_derived(total_values)

    # For month-based requests, keep summary totals fully aligned with Finance Report.
    if month_raw and not date_from_raw and not date_to_raw:
        async with pool.acquire() as conn_sync:
            month_rows_map, _ = await build_rows_map_for_month(conn_sync, month_raw)
        for row_key, row_data in month_rows_map.items():
            total = row_data.get("total")
            if total is None:
                continue
            total_values[row_key] = float(total)

    finance_ads_total = float(
        total_values.get("pay_per_click", 0.0)
        + total_values.get("review_points", 0.0)
        + total_values.get("premium_plus_subscription", 0.0)
    )
    ads_source_total = float(
        sum(float(r["ad_spent"] or 0.0) for r in ad_rows if normalize_article(r["offer_id"]))
    )
    compensation_keys = {"compensations", "shortage_retention", "other_accrual_adjustments"}
    no_article_compensations_total = float(
        sum(v for k, v in no_article_by_key.items() if k in compensation_keys)
    )
    no_article_accruals_total = float(
        sum(v for k, v in no_article_by_key.items() if k not in compensation_keys)
    )
    summary = {
        "date_from": date_from.astimezone(MSK).date().isoformat(),
        "date_to": (date_to_exclusive - timedelta(days=1)).astimezone(MSK).date().isoformat(),
        "mode": "distributed" if distribute_no_article else "exclude_no_article",
        "no_article_accruals_total": float(no_article_accruals_total),
        "no_article_compensations_total": float(no_article_compensations_total),
        "ads_finance_total": float(finance_ads_total),
        "ads_source_total": float(ads_source_total),
        "ads_delta": float(finance_ads_total - ads_source_total),
        "total_row": total_values,
    }
    eps = 1e-9
    has_returns_total = any(str(col.get("key", "")) == "returns_total" for col in columns)
    filtered_columns = []
    for column in columns:
        key = str(column.get("key", ""))
        if has_returns_total and key == "returns_revenue":
            continue
        total_value = float(total_values.get(key, 0.0) or 0.0)
        has_total = abs(total_value) > eps
        has_row_values = any(abs(float((item.get("values") or {}).get(key, 0.0) or 0.0)) > eps for item in items_transposed)
        if has_total or has_row_values:
            filtered_columns.append(column)

    return web.json_response({"count": len(items_transposed), "items": items_transposed, "summary": summary, "columns": filtered_columns})


async def get_realization_v2(request: web.Request) -> web.Response:
    offer_id = (request.query.get("offer_id") or "").strip()
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    limit_raw = (request.query.get("limit") or "500").strip()

    try:
        limit = max(1, min(2000, int(limit_raw)))
    except ValueError:
        return web.json_response({"error": "Invalid limit"}, status=400)

    try:
        date_from = parse_date_utc(date_from_raw, end_of_day=False) if date_from_raw else None
        date_to_exclusive = parse_date_utc(date_to_raw, end_of_day=True) if date_to_raw else None
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)

    params: List[Any] = []
    conditions: List[str] = []
    idx = 1
    if offer_id:
        conditions.append(f"offer_id = ${idx}")
        params.append(offer_id)
        idx += 1
    if date_from is not None:
        conditions.append(f"date >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to_exclusive is not None:
        conditions.append(f"date < ${idx}")
        params.append(date_to_exclusive)
        idx += 1

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            date,
            sku,
            offer_id,
            name,
            quantity,
            price,
            total_amount,
            commission_percent,
            commission_amount,
            payout_amount,
            delivery_cost,
            total_payout,
            last_synced_at
        FROM realization_reports
        {where_sql}
        ORDER BY date DESC NULLS LAST, sku
        LIMIT {limit}
    """
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "date": r["date"].isoformat() if r["date"] else None,
                "sku": r["sku"],
                "offer_id": r["offer_id"],
                "name": r["name"],
                "quantity": r["quantity"],
                "price": float(r["price"]) if r["price"] is not None else None,
                "total_amount": float(r["total_amount"]) if r["total_amount"] is not None else None,
                "commission_percent": float(r["commission_percent"]) if r["commission_percent"] is not None else None,
                "commission_amount": float(r["commission_amount"]) if r["commission_amount"] is not None else None,
                "payout_amount": float(r["payout_amount"]) if r["payout_amount"] is not None else None,
                "delivery_cost": float(r["delivery_cost"]) if r["delivery_cost"] is not None else None,
                "total_payout": float(r["total_payout"]) if r["total_payout"] is not None else None,
                "last_synced_at": r["last_synced_at"].isoformat() if r["last_synced_at"] else None,
            }
        )
    return web.json_response({"count": len(items), "items": items})


async def get_wb_finance_report_daily(request: web.Request) -> web.Response:
    """WB finance daily report from wb_finance_daily vitrine."""
    month_raw = (request.query.get("month") or "").strip()
    date_from_raw = (request.query.get("date_from") or "").strip()
    date_to_raw = (request.query.get("date_to") or "").strip()
    final_only_raw = (request.query.get("final_only") or "1").strip().lower()

    try:
        if month_raw and not date_from_raw and not date_to_raw:
            month_start, month_end, _ = month_bounds(month_raw)
            date_from = month_start.date()
            date_to = (month_end - timedelta(days=1)).date()
        else:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date() if date_from_raw else None
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date() if date_to_raw else None
    except ValueError:
        return web.json_response({"error": "Invalid date format, expected YYYY-MM-DD or month YYYY-MM"}, status=400)

    final_only = final_only_raw not in {"0", "false", "no"}

    params: List[Any] = []
    conditions: List[str] = []
    idx = 1
    if date_from is not None:
        conditions.append(f"report_date >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to is not None:
        conditions.append(f"report_date <= ${idx}")
        params.append(date_to)
        idx += 1
    if final_only:
        conditions.append("NOW() >= ((report_date + INTERVAL '1 day') + TIME '12:00') AT TIME ZONE 'Europe/Moscow'")

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        WITH cost_map AS (
            SELECT lower(trim(article)) AS article_key, MAX(unit_cost) AS unit_cost
            FROM finance_article_costs
            WHERE nullif(trim(article), '') IS NOT NULL
            GROUP BY lower(trim(article))
        ),
        src AS (
            SELECT
                (f.sale_dt AT TIME ZONE 'Europe/Moscow')::date AS report_date,
                COALESCE(NULLIF(trim((CASE
                    WHEN jsonb_typeof(r.row_json)='string'
                        THEN (trim(both '"' from r.row_json::text))::jsonb
                    ELSE r.row_json
                END)->>'sellerOperName'), ''), '<пусто>') AS seller_oper_name,
                COALESCE(f.retail_amount, 0) AS retail_amount,
                COALESCE(f.for_pay, 0) AS for_pay,
                COALESCE(f.vw, 0) AS vw,
                COALESCE(f.delivery_service, 0) AS delivery_service,
                COALESCE(f.rebill_logistic_cost, 0) AS rebill_logistic_cost,
                COALESCE(f.return_amount, 0) AS return_amount,
                COALESCE(f.acquiring_fee, 0) AS acquiring_fee,
                COALESCE(f.penalty, 0) AS penalty,
                COALESCE(f.deduction, 0) AS deduction,
                COALESCE(f.additional_payment, 0) AS additional_payment,
                COALESCE(f.paid_storage, 0) AS paid_storage,
                COALESCE(f.paid_acceptance, 0) AS paid_acceptance,
                CASE
                    WHEN lower(COALESCE(f.operation_name, f.raw_selleropername, '')) = 'возврат'
                        THEN -ABS(COALESCE(f.quantity, 0)) * COALESCE(c.unit_cost, 0)
                    WHEN lower(COALESCE(f.operation_name, f.raw_selleropername, '')) = 'продажа'
                        THEN ABS(COALESCE(f.quantity, 0)) * COALESCE(c.unit_cost, 0)
                    ELSE 0
                END AS material_cost
            FROM wb_fact_finance f
            JOIN wb_raw_sales_report_details r ON r.id = f.raw_id
            LEFT JOIN cost_map c ON c.article_key = lower(trim(COALESCE(f.raw_vendorcode, f.sa_name, '')))
            WHERE f.sale_dt IS NOT NULL
        ),
        agg AS (
            SELECT
                report_date,
                seller_oper_name,
                SUM(CASE
                    WHEN lower(seller_oper_name) = 'возврат' THEN -ABS(retail_amount)
                    ELSE retail_amount
                END) AS gross_revenue,
                SUM(vw) AS marketplace_commission,
                SUM(delivery_service) AS logistics_direct,
                SUM(rebill_logistic_cost + return_amount) AS logistics_reverse,
                SUM(acquiring_fee) AS acquiring,
                SUM(penalty) AS penalties,
                SUM(deduction + additional_payment + paid_storage + paid_acceptance) AS other_deductions,
                SUM(material_cost) AS material_cost,
                SUM(CASE
                    WHEN lower(seller_oper_name) = 'возврат' THEN -ABS(for_pay)
                    ELSE for_pay
                END) AS to_pay,
                COUNT(*)::int AS rows_count
            FROM src
            GROUP BY report_date, seller_oper_name
        )
        SELECT
            report_date,
            seller_oper_name,
            gross_revenue,
            marketplace_commission,
            logistics_direct,
            logistics_reverse,
            acquiring,
            penalties,
            other_deductions,
            (ABS(marketplace_commission) + ABS(logistics_direct) + ABS(logistics_reverse) + ABS(acquiring) + ABS(penalties) + ABS(other_deductions)) AS marketplace_expenses_total,
            material_cost,
            to_pay,
            rows_count,
            NOW() >= ((report_date + INTERVAL '1 day') + TIME '12:00') AT TIME ZONE 'Europe/Moscow' AS is_final_day,
            (((report_date + INTERVAL '1 day') + TIME '12:00') AT TIME ZONE 'Europe/Moscow') AS finalized_after,
            NOW() AS updated_at
        FROM agg
        {where_sql}
        ORDER BY report_date, seller_oper_name
    """

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
        plan_month = None
        if date_from is not None:
            plan_month = f"{date_from.year:04d}-{date_from.month:02d}"
        elif rows:
            first_date = rows[0]["report_date"]
            if first_date:
                plan_month = f"{first_date.year:04d}-{first_date.month:02d}"
        if plan_month:
            plan_row = await conn.fetchrow(
                """
                SELECT revenue_plan
                FROM finance_month_plan
                WHERE marketplace = 'wb'
                  AND month_start = $1
                """,
                month_start_msk(plan_month).date(),
            )
        else:
            plan_row = None
        if date_from is not None and date_to is not None:
            ad_rows = await conn.fetch(
                """
                SELECT
                    report_date,
                    SUM(spend)::float8 AS spend,
                    SUM(views)::bigint AS views,
                    SUM(clicks)::bigint AS clicks,
                    SUM(carts)::bigint AS carts,
                    SUM(orders)::bigint AS orders,
                    SUM(revenue)::float8 AS revenue
                FROM wb_advertising_daily
                WHERE report_date >= $1
                  AND report_date <= $2
                GROUP BY report_date
                ORDER BY report_date
                """,
                date_from,
                date_to,
            )
        else:
            ad_rows = []

    items: List[Dict[str, Any]] = []
    totals = {
        "gross_revenue": 0.0,
        "marketplace_commission": 0.0,
        "logistics_direct": 0.0,
        "logistics_reverse": 0.0,
        "acquiring": 0.0,
        "penalties": 0.0,
        "other_deductions": 0.0,
        "marketplace_expenses_total": 0.0,
        "material_cost": 0.0,
        "advertising_spend": 0.0,
        "to_pay": 0.0,
        "rows_count": 0,
    }
    for r in rows:
        item = {
            "report_date": r["report_date"].isoformat() if r["report_date"] else None,
            "seller_oper_name": r["seller_oper_name"],
            "gross_revenue": as_float(r["gross_revenue"]),
            "marketplace_commission": as_float(r["marketplace_commission"]),
            "logistics_direct": as_float(r["logistics_direct"]),
            "logistics_reverse": as_float(r["logistics_reverse"]),
            "acquiring": as_float(r["acquiring"]),
            "penalties": as_float(r["penalties"]),
            "other_deductions": as_float(r["other_deductions"]),
            "marketplace_expenses_total": as_float(r["marketplace_expenses_total"]),
            "material_cost": as_float(r["material_cost"]),
            "to_pay": as_float(r["to_pay"]),
            "rows_count": int(r["rows_count"] or 0),
            "is_final_day": bool(r["is_final_day"]),
            "finalized_after": r["finalized_after"].isoformat() if r["finalized_after"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        items.append(item)
        totals["gross_revenue"] += float(item["gross_revenue"] or 0.0)
        totals["marketplace_commission"] += float(item["marketplace_commission"] or 0.0)
        totals["logistics_direct"] += float(item["logistics_direct"] or 0.0)
        totals["logistics_reverse"] += float(item["logistics_reverse"] or 0.0)
        totals["acquiring"] += float(item["acquiring"] or 0.0)
        totals["penalties"] += float(item["penalties"] or 0.0)
        totals["other_deductions"] += float(item["other_deductions"] or 0.0)
        totals["marketplace_expenses_total"] += float(item["marketplace_expenses_total"] or 0.0)
        totals["material_cost"] += float(item["material_cost"] or 0.0)
        totals["to_pay"] += float(item["to_pay"] or 0.0)
        totals["rows_count"] += int(item["rows_count"] or 0)

    advertising_daily: List[Dict[str, Any]] = []
    for r in ad_rows:
        spend = as_float(r["spend"])
        advertising_daily.append(
            {
                "report_date": r["report_date"].isoformat() if r["report_date"] else None,
                "spend": spend,
                "views": int(r["views"] or 0),
                "clicks": int(r["clicks"] or 0),
                "carts": int(r["carts"] or 0),
                "orders": int(r["orders"] or 0),
                "revenue": as_float(r["revenue"]),
            }
        )
        totals["advertising_spend"] += float(spend or 0.0)

    if plan_month:
        plan_start, _, plan_days = month_bounds(plan_month)
        now_msk = datetime.now(MSK)
        plan_month_dt = month_start_msk(plan_month)
        plan_editable = plan_month_dt.year == now_msk.year and plan_month_dt.month == now_msk.month
        if plan_row:
            revenue_plan_total = as_float(plan_row["revenue_plan"])
        elif plan_editable:
            revenue_plan_total = PLAN_BASE_VALUES["revenue_mp"]
        else:
            revenue_plan_total = None
        gross_profit_plan_total = (
            scale_plan_value(PLAN_BASE_VALUES["gross_profit"], revenue_plan_total)
            if revenue_plan_total is not None
            else None
        )
        plan_payload = {
            "marketplace": "wb",
            "month": plan_month,
            "month_start": plan_start.isoformat(),
            "month_days": len(plan_days),
            "revenue_mp": revenue_plan_total,
            "gross_profit": gross_profit_plan_total,
            "editable": plan_editable,
            "exists": bool(plan_row),
        }
    else:
        plan_payload = {
            "marketplace": "wb",
            "month": "",
            "month_start": "",
            "month_days": 0,
            "revenue_mp": None,
            "gross_profit": None,
            "editable": False,
            "exists": False,
        }

    data = clean_nan_values(
        {
            "count": len(items),
            "final_only": final_only,
            "marketplace": "wb",
            "month": plan_payload["month"],
            "plan": plan_payload,
            "items": items,
            "advertising_daily": advertising_daily,
            "totals": totals,
        }
    )
    return web.json_response(data)


async def analyze_finance_data(request: web.Request) -> web.Response:
    """Analiz finansovyh dannyh za period."""
    month_value = (request.query.get("month") or "").strip()
    if not month_value:
        month_value = datetime.now(timezone.utc).strftime("%Y-%m")
    
    try:
        year, month = map(int, month_value.split("-"))
        month_start = datetime(year, month, 1, tzinfo=MSK).astimezone(timezone.utc)
        if month == 12:
            month_end = datetime(year + 1, 1, 1, tzinfo=MSK).astimezone(timezone.utc)
        else:
            month_end = datetime(year, month + 1, 1, tzinfo=MSK).astimezone(timezone.utc)
    except ValueError:
        return web.json_response({"error": "Invalid month format"}, status=400)
    
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        # 1. Osnovnye pokazateli
        main_stats = await conn.fetch(
            """
            SELECT 
                description,
                type,
                COUNT(*) as cnt,
                SUM(amount) as total,
                SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) as prihod,
                SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) as rashod
            FROM transactions
            WHERE operation_date >= $1 AND operation_date < $2
            GROUP BY description, type
            HAVING ABS(SUM(amount)) > 100
            ORDER BY ABS(SUM(amount)) DESC
            """,
            month_start, month_end
        )
        
        # 2. Vyruchka i rashody
        revenue_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(amount), 0) as rev,
                   COUNT(*) as cnt
            FROM transactions
            WHERE operation_date >= $1 AND operation_date < $2
              AND description = 'Р”РѕСЃС‚Р°РІРєР° РїРѕРєСѓРїР°С‚РµР»СЋ'
              AND type = 'orders'
              AND (raw_data->>'accruals_for_sale')::numeric > 0
            """,
            month_start, month_end
        )
        revenue = float(revenue_row["rev"]) if revenue_row else 0
        
        # 3. Komissiya
        comm_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(
                CASE WHEN (raw_data->>'sale_commission')::numeric < 0 
                THEN (raw_data->>'sale_commission')::numeric ELSE 0 END
            ), 0) as comm
            FROM transactions
            WHERE operation_date >= $1 AND operation_date < $2
              AND description = 'Р”РѕСЃС‚Р°РІРєР° РїРѕРєСѓРїР°С‚РµР»СЋ'
            """,
            month_start, month_end
        )
        commission = abs(float(comm_row["comm"])) if comm_row else 0
        
        # 4. Logistika
        log_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(s.price), 0) as log
            FROM transactions t
            JOIN transaction_services s ON t.transaction_id = s.transaction_id
            WHERE t.operation_date >= $1 AND t.operation_date < $2
              AND s.service_name LIKE '%Logistic%'
            """,
            month_start, month_end
        )
        logistics = float(log_row["log"]) if log_row else 0
        
        # 5. Reklama
        ads_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(amount), 0) as ads
            FROM transactions
            WHERE operation_date >= $1 AND operation_date < $2
              AND description IN ('РћРїР»Р°С‚Р° Р·Р° РєР»РёРє', 'Р—Р°РєСЂРµРїР»РµРЅРёРµ РѕС‚Р·С‹РІР°', 
                  'Р’С‹РІРѕРґ РІ С‚РѕРї', 'Р РµРєР»Р°РјР° РІ СЃРµС‚Рё РёРЅС‚РµСЂРЅРµС‚ РЅР° СЃР°Р№С‚Рµ')
            """,
            month_start, month_end
        )
        ads = abs(float(ads_row["ads"])) if ads_row else 0
        
        # 6. Vozvraty
        ret_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(amount), 0) as ret, COUNT(*) as cnt
            FROM transactions
            WHERE operation_date >= $1 AND operation_date < $2
              AND description = 'РџРѕР»СѓС‡РµРЅРёРµ РІРѕР·РІСЂР°С‚Р°, РѕС‚РјРµРЅС‹, РЅРµРІС‹РєСѓРїР° РѕС‚ РїРѕРєСѓРїР°С‚РµР»СЏ'
            """,
            month_start, month_end
        )
        returns = abs(float(ret_row["ret"])) if ret_row else 0
        
        # 7. Dinamika po dnyam
        daily = await conn.fetch(
            """
            SELECT 
                DATE(operation_date AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') as day,
                SUM(amount) as total
            FROM transactions
            WHERE operation_date >= $1 AND operation_date < $2
            GROUP BY day
            ORDER BY day
            """,
            month_start, month_end
        )
        
        positive_days = sum(1 for r in daily if r["total"] > 0)
        negative_days = sum(1 for r in daily if r["total"] < 0)
        best_day = max(daily, key=lambda x: x["total"]) if daily else None
        
        # Formiruem otvet
        total_expenses = commission + logistics + ads
        net_profit = revenue - total_expenses - returns
        
        analysis = {
            "month": month_value,
            "revenue": round(revenue, 2),
            "commission": round(commission, 2),
            "commission_pct": round(commission / revenue * 100, 1) if revenue else 0,
            "logistics": round(logistics, 2),
            "logistics_pct": round(logistics / revenue * 100, 1) if revenue else 0,
            "ads": round(ads, 2),
            "ads_pct": round(ads / revenue * 100, 1) if revenue else 0,
            "returns": round(returns, 2),
            "returns_pct": round(returns / revenue * 100, 1) if revenue else 0,
            "total_expenses": round(total_expenses, 2),
            "net_profit": round(net_profit, 2),
            "net_profit_pct": round(net_profit / revenue * 100, 1) if revenue else 0,
            "positive_days": positive_days,
            "negative_days": negative_days,
            "best_day": str(best_day["day"]) if best_day else None,
            "best_day_amount": round(float(best_day["total"]), 2) if best_day else 0,
            "main_operations": [
                {
                    "description": row["description"],
                    "type": row["type"],
                    "count": row["cnt"],
                    "total": round(float(row["total"]), 2),
                    "prihod": round(float(row["prihod"]), 2) if row["prihod"] else 0,
                    "rashod": round(float(row["rashod"]), 2) if row["rashod"] else 0
                }
                for row in main_stats[:10]
            ],
            "recommendations": []
        }
        
        # Rekomendacii
        recs = []
        if analysis["commission_pct"] > 25:
            recs.append(f"вљ пёЏ Р’С‹СЃРѕРєР°СЏ РєРѕРјРёСЃСЃРёСЏ OZON ({analysis['commission_pct']:.1f}%). РџСЂРѕРІРµСЂСЊС‚Рµ РєР°С‚РµРіРѕСЂРёРё С‚РѕРІР°СЂРѕРІ.")
        if analysis["returns_pct"] > 5:
            recs.append(f"вљ пёЏ Р’С‹СЃРѕРєРёР№ СѓСЂРѕРІРµРЅСЊ РІРѕР·РІСЂР°С‚РѕРІ ({analysis['returns_pct']:.1f}%). РџСЂРѕР°РЅР°Р»РёР·РёСЂСѓР№С‚Рµ РїСЂРёС‡РёРЅС‹.")
        if analysis["logistics_pct"] > 20:
            recs.append(f"вљ пёЏ Р’С‹СЃРѕРєРёРµ СЂР°СЃС…РѕРґС‹ РЅР° Р»РѕРіРёСЃС‚РёРєСѓ ({analysis['logistics_pct']:.1f}%).")
        if analysis["ads_pct"] > 15:
            recs.append(f"вљ пёЏ Р’С‹СЃРѕРєРёРµ СЂР°СЃС…РѕРґС‹ РЅР° СЂРµРєР»Р°РјСѓ ({analysis['ads_pct']:.1f}%).")
        if analysis["net_profit_pct"] < 10:
            recs.append(f"вљ пёЏ РќРёР·РєР°СЏ РјР°СЂР¶Р° ({analysis['net_profit_pct']:.1f}%). РўСЂРµР±СѓРµС‚СЃСЏ РѕРїС‚РёРјРёР·Р°С†РёСЏ.")
        if not recs:
            recs.append("вњ… РџРѕРєР°Р·Р°С‚РµР»Рё РІ РЅРѕСЂРјРµ.")
        
        analysis["recommendations"] = recs
        
        return web.json_response(analysis)


