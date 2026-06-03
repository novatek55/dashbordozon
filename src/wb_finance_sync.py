"""Raw ingestion for Wildberries finance report details."""
import logging
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import text
from tenacity import RetryError

from src.database import db_manager
from src.wb_finance_client import WBAPIError, WBFinanceClient

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


def _to_sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _raw_key_to_column(key: str) -> str:
    """Convert raw WB JSON key to stable SQL column name."""
    normalized = re.sub(r"[^0-9a-zA-Z_]+", "_", key).strip("_").lower()
    if not normalized:
        normalized = "empty_key"
    if normalized[0].isdigit():
        normalized = f"k_{normalized}"
    return f"raw_{normalized}"


async def ensure_wb_finance_tables() -> None:
    async with db_manager.session() as session:
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wb_raw_sales_report_details (
                    id BIGSERIAL PRIMARY KEY,
                    date_from TIMESTAMPTZ NOT NULL,
                    date_to TIMESTAMPTZ NOT NULL,
                    rrd_id BIGINT NOT NULL,
                    nm_id BIGINT NULL,
                    srid TEXT NULL,
                    row_json JSONB NOT NULL,
                    source TEXT NOT NULL DEFAULT 'finance-v1',
                    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (date_from, date_to, rrd_id)
                );
                """
            )
        )
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wb_fact_finance (
                    id BIGSERIAL PRIMARY KEY,
                    rrd_id BIGINT NOT NULL,
                    sale_dt TIMESTAMPTZ NULL,
                    create_dt TIMESTAMPTZ NULL,
                    doc_type_name TEXT NULL,
                    operation_name TEXT NULL,
                    nm_id BIGINT NULL,
                    sa_name TEXT NULL,
                    brand_name TEXT NULL,
                    quantity NUMERIC(15, 3) NULL,
                    retail_amount NUMERIC(15, 2) NULL,
                    sale_percent NUMERIC(10, 4) NULL,
                    commission_percent NUMERIC(10, 4) NULL,
                    ppvz_sales_commission NUMERIC(15, 2) NULL,
                    vw NUMERIC(15, 2) NULL,
                    delivery_amount NUMERIC(15, 2) NULL,
                    delivery_service NUMERIC(15, 2) NULL,
                    return_amount NUMERIC(15, 2) NULL,
                    rebill_logistic_cost NUMERIC(15, 2) NULL,
                    acquiring_fee NUMERIC(15, 2) NULL,
                    penalty NUMERIC(15, 2) NULL,
                    deduction NUMERIC(15, 2) NULL,
                    additional_payment NUMERIC(15, 2) NULL,
                    paid_storage NUMERIC(15, 2) NULL,
                    paid_acceptance NUMERIC(15, 2) NULL,
                    for_pay NUMERIC(15, 2) NULL,
                    ppvz_for_pay NUMERIC(15, 2) NULL,
                    raw_id BIGINT NOT NULL,
                    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )
        await session.execute(text("ALTER TABLE wb_fact_finance DROP CONSTRAINT IF EXISTS wb_fact_finance_rrd_id_key"))
        await session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_wb_fact_finance_raw_id ON wb_fact_finance(raw_id)"))
        await session.execute(text("CREATE INDEX IF NOT EXISTS idx_wb_fact_finance_rrd_id ON wb_fact_finance(rrd_id)"))
        await session.execute(text("ALTER TABLE wb_fact_finance ADD COLUMN IF NOT EXISTS vw NUMERIC(15,2) NULL"))
        await session.execute(text("ALTER TABLE wb_fact_finance ADD COLUMN IF NOT EXISTS delivery_service NUMERIC(15,2) NULL"))
        await session.execute(text("ALTER TABLE wb_fact_finance ADD COLUMN IF NOT EXISTS rebill_logistic_cost NUMERIC(15,2) NULL"))
        await session.execute(text("ALTER TABLE wb_fact_finance ADD COLUMN IF NOT EXISTS paid_storage NUMERIC(15,2) NULL"))
        await session.execute(text("ALTER TABLE wb_fact_finance ADD COLUMN IF NOT EXISTS paid_acceptance NUMERIC(15,2) NULL"))
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wb_finance_daily (
                    report_date DATE PRIMARY KEY,
                    gross_revenue NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    marketplace_commission NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    logistics_direct NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    logistics_reverse NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    acquiring NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    penalties NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    other_deductions NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    to_pay NUMERIC(15, 2) NOT NULL DEFAULT 0,
                    rows_count INTEGER NOT NULL DEFAULT 0,
                    is_final_day BOOLEAN NOT NULL DEFAULT FALSE,
                    finalized_after TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wb_etl_runs (
                    id BIGSERIAL PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    date_from TIMESTAMPTZ NOT NULL,
                    date_to TIMESTAMPTZ NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    rows_loaded INTEGER NOT NULL DEFAULT 0,
                    last_rrd_id BIGINT NOT NULL DEFAULT 0,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMPTZ NULL,
                    error_message TEXT NULL
                );
                """
            )
        )


def _period_bounds(days_back: int) -> tuple[datetime, datetime]:
    now_msk = datetime.now(MSK)
    date_to = now_msk.replace(hour=23, minute=59, second=59, microsecond=0)
    date_from = (now_msk - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
    return date_from, date_to


async def _get_last_rrd_id(date_from: datetime, date_to: datetime) -> int:
    async with db_manager.session() as session:
        result = await session.execute(
            text(
                """
                SELECT COALESCE(MAX(rrd_id), 0)
                FROM wb_raw_sales_report_details
                WHERE date_from = :date_from AND date_to = :date_to
                """
            ),
            {"date_from": date_from, "date_to": date_to},
        )
        return int(result.scalar() or 0)


async def normalize_wb_finance_raw_to_fact() -> Dict[str, Any]:
    await ensure_wb_finance_tables()
    async with db_manager.session() as session:
        result = await session.execute(
            text(
                """
                WITH src AS (
                    SELECT
                        id AS raw_id,
                        CASE WHEN jsonb_typeof(row_json) = 'string'
                             THEN (trim(both '"' from row_json::text))::jsonb
                             ELSE row_json
                        END AS j
                    FROM wb_raw_sales_report_details
                ),
                upserted AS (
                    INSERT INTO wb_fact_finance (
                        rrd_id, sale_dt, create_dt, doc_type_name, operation_name, nm_id,
                        sa_name, brand_name, quantity, retail_amount, sale_percent,
                        commission_percent, ppvz_sales_commission, vw, delivery_amount, delivery_service, return_amount, rebill_logistic_cost,
                        acquiring_fee, penalty, deduction, additional_payment, for_pay, ppvz_for_pay,
                        paid_storage, paid_acceptance, raw_id, updated_at
                    )
                    SELECT
                        NULLIF(j->>'rrdId', '')::bigint,
                        NULLIF(j->>'saleDt', '')::timestamptz,
                        NULLIF(j->>'createDate', '')::timestamptz,
                        NULLIF(j->>'docTypeName', ''),
                        NULLIF(j->>'supplierOperName', ''),
                        NULLIF(j->>'nmId', '')::bigint,
                        NULLIF(j->>'saName', ''),
                        NULLIF(j->>'brandName', ''),
                        NULLIF(j->>'quantity', '')::numeric,
                        NULLIF(j->>'retailAmount', '')::numeric,
                        NULLIF(j->>'salePercent', '')::numeric,
                        NULLIF(j->>'commissionPercent', '')::numeric,
                        NULLIF(j->>'ppvzSalesCommission', '')::numeric,
                        NULLIF(j->>'vw', '')::numeric,
                        NULLIF(j->>'deliveryAmount', '')::numeric,
                        NULLIF(j->>'deliveryService', '')::numeric,
                        NULLIF(j->>'returnAmount', '')::numeric,
                        NULLIF(j->>'rebillLogisticCost', '')::numeric,
                        NULLIF(j->>'acquiringFee', '')::numeric,
                        NULLIF(j->>'penalty', '')::numeric,
                        NULLIF(j->>'deduction', '')::numeric,
                        NULLIF(j->>'additionalPayment', '')::numeric,
                        NULLIF(j->>'forPay', '')::numeric,
                        NULLIF(j->>'ppvzForPay', '')::numeric,
                        NULLIF(j->>'paidStorage', '')::numeric,
                        NULLIF(j->>'paidAcceptance', '')::numeric,
                        raw_id,
                        NOW()
                    FROM src
                    WHERE NULLIF(j->>'rrdId', '') IS NOT NULL
                    ON CONFLICT (raw_id) DO UPDATE
                    SET
                        rrd_id = EXCLUDED.rrd_id,
                        sale_dt = EXCLUDED.sale_dt,
                        create_dt = EXCLUDED.create_dt,
                        doc_type_name = EXCLUDED.doc_type_name,
                        operation_name = EXCLUDED.operation_name,
                        nm_id = EXCLUDED.nm_id,
                        sa_name = EXCLUDED.sa_name,
                        brand_name = EXCLUDED.brand_name,
                        quantity = EXCLUDED.quantity,
                        retail_amount = EXCLUDED.retail_amount,
                        sale_percent = EXCLUDED.sale_percent,
                        commission_percent = EXCLUDED.commission_percent,
                        ppvz_sales_commission = EXCLUDED.ppvz_sales_commission,
                        vw = EXCLUDED.vw,
                        delivery_amount = EXCLUDED.delivery_amount,
                        delivery_service = EXCLUDED.delivery_service,
                        return_amount = EXCLUDED.return_amount,
                        rebill_logistic_cost = EXCLUDED.rebill_logistic_cost,
                        acquiring_fee = EXCLUDED.acquiring_fee,
                        penalty = EXCLUDED.penalty,
                        deduction = EXCLUDED.deduction,
                        additional_payment = EXCLUDED.additional_payment,
                        for_pay = EXCLUDED.for_pay,
                        ppvz_for_pay = EXCLUDED.ppvz_for_pay,
                        paid_storage = EXCLUDED.paid_storage,
                        paid_acceptance = EXCLUDED.paid_acceptance,
                        raw_id = EXCLUDED.raw_id,
                        updated_at = NOW()
                    RETURNING 1
                )
                SELECT COUNT(*)::int FROM upserted
                """
            )
        )
        affected = int(result.scalar() or 0)

        # 2) Ensure wb_fact_finance has all row_json fields as split columns (TEXT)
        keys_result = await session.execute(
            text(
                """
                WITH s AS (
                    SELECT CASE
                        WHEN jsonb_typeof(row_json)='string' THEN (trim(both '"' from row_json::text))::jsonb
                        ELSE row_json
                    END AS j
                    FROM wb_raw_sales_report_details
                )
                SELECT DISTINCT jsonb_object_keys(j) AS key
                FROM s
                ORDER BY 1
                """
            )
        )
        raw_keys = [str(r[0]) for r in keys_result.fetchall()]
        dynamic_columns = [(_raw_key_to_column(k), k) for k in raw_keys]

        existing_result = await session.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'wb_fact_finance'
                """
            )
        )
        existing_columns = {str(r[0]) for r in existing_result.fetchall()}

        added_columns = 0
        for col_name, _ in dynamic_columns:
            if col_name in existing_columns:
                continue
            await session.execute(
                text(f"ALTER TABLE wb_fact_finance ADD COLUMN {_to_sql_ident(col_name)} TEXT NULL")
            )
            added_columns += 1

        # 3) Fill dynamic columns from row_json for all rows
        set_parts = []
        for col_name, raw_key in dynamic_columns:
            set_parts.append(
                f"{_to_sql_ident(col_name)} = src.j ->> '{raw_key}'"
            )
        if set_parts:
            set_sql = ",\n                        ".join(set_parts)
            await session.execute(
                text(
                    f"""
                    UPDATE wb_fact_finance f
                    SET
                        {set_sql},
                        updated_at = NOW()
                    FROM (
                        SELECT
                            id AS raw_id,
                            CASE
                                WHEN jsonb_typeof(row_json)='string' THEN (trim(both '"' from row_json::text))::jsonb
                                ELSE row_json
                            END AS j
                        FROM wb_raw_sales_report_details
                    ) src
                    WHERE f.raw_id = src.raw_id
                    """
                )
            )

        total_result = await session.execute(text("SELECT COUNT(*) FROM wb_fact_finance"))
        total = int(total_result.scalar() or 0)

    return {
        "status": "success",
        "rows_upserted": affected,
        "fact_total_rows": total,
        "dynamic_raw_columns": len(dynamic_columns),
        "dynamic_raw_columns_added": added_columns,
    }


async def rebuild_wb_finance_daily_vitrine() -> Dict[str, Any]:
    await ensure_wb_finance_tables()
    async with db_manager.session() as session:
        await session.execute(text("TRUNCATE TABLE wb_finance_daily"))
        await session.execute(
            text(
                """
                WITH src AS (
                    SELECT
                        f.sale_dt,
                        f.rrd_id,
                        f.for_pay,
                        f.retail_amount,
                        f.return_amount,
                        f.acquiring_fee,
                        f.penalty,
                        f.deduction,
                        f.additional_payment,
                        f.paid_storage,
                        f.paid_acceptance,
                        f.rebill_logistic_cost,
                        f.delivery_service,
                        f.vw,
                        lower(regexp_replace(coalesce(nullif(trim((case when jsonb_typeof(r.row_json)='string' then (trim(both '"' from r.row_json::text))::jsonb else r.row_json end)->>'sellerOperName'),''), '<пусто>'), '\\s+', ' ', 'g')) AS seller_oper_name_norm
                    FROM wb_fact_finance f
                    JOIN wb_raw_sales_report_details r ON r.id = f.raw_id
                    WHERE f.sale_dt IS NOT NULL
                ),
                mapped AS (
                    SELECT
                        s.*,
                        COALESCE(m.report_article, 'Прочее') AS report_article
                    FROM src s
                    LEFT JOIN wb_dim_article_mapping m
                      ON m.seller_oper_name_norm = s.seller_oper_name_norm
                     AND COALESCE(m.is_active, TRUE) = TRUE
                )
                INSERT INTO wb_finance_daily (
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
                    is_final_day,
                    finalized_after,
                    updated_at
                )
                SELECT
                    (sale_dt AT TIME ZONE 'Europe/Moscow')::date AS report_date,
                    COALESCE(SUM(CASE WHEN report_article IN ('Валовая выручка', 'Возвраты') THEN COALESCE(retail_amount, 0) ELSE 0 END), 0) AS gross_revenue,
                    COALESCE(SUM(CASE WHEN report_article = 'Комиссия маркетплейса' THEN COALESCE(vw, 0) ELSE 0 END), 0) AS marketplace_commission,
                    COALESCE(SUM(CASE WHEN report_article = 'Логистика прямая' THEN COALESCE(delivery_service, 0) ELSE 0 END), 0) AS logistics_direct,
                    COALESCE(SUM(CASE WHEN report_article = 'Логистика обратная' THEN COALESCE(rebill_logistic_cost, 0) + COALESCE(return_amount, 0) ELSE 0 END), 0) AS logistics_reverse,
                    COALESCE(SUM(CASE WHEN report_article IN ('Валовая выручка', 'Возвраты') THEN COALESCE(acquiring_fee, 0) ELSE 0 END), 0) AS acquiring,
                    COALESCE(SUM(CASE WHEN report_article = 'Штрафы' THEN COALESCE(penalty, 0) ELSE 0 END), 0) AS penalties,
                    COALESCE(SUM(CASE WHEN report_article = 'Прочие удержания' THEN COALESCE(deduction, 0) + COALESCE(additional_payment, 0) + COALESCE(paid_storage, 0) + COALESCE(paid_acceptance, 0) ELSE 0 END), 0) AS other_deductions,
                    COALESCE(SUM(CASE WHEN report_article IN ('Валовая выручка', 'Возвраты') THEN COALESCE(for_pay, 0) ELSE 0 END), 0) AS to_pay,
                    COUNT(*)::int AS rows_count,
                    NOW() >= (((sale_dt AT TIME ZONE 'Europe/Moscow')::date + INTERVAL '1 day') + TIME '12:00') AT TIME ZONE 'Europe/Moscow' AS is_final_day,
                    ((((sale_dt AT TIME ZONE 'Europe/Moscow')::date + INTERVAL '1 day') + TIME '12:00') AT TIME ZONE 'Europe/Moscow') AS finalized_after,
                    NOW() AS updated_at
                FROM mapped
                GROUP BY (sale_dt AT TIME ZONE 'Europe/Moscow')::date
                """
            )
        )
        count_result = await session.execute(text("SELECT COUNT(*) FROM wb_finance_daily"))
        days = int(count_result.scalar() or 0)
    return {"status": "success", "days_rebuilt": days}


async def sync_wb_finance_raw(api_key: str, days_back: int = 30, limit: int = 100000) -> Dict[str, Any]:
    await ensure_wb_finance_tables()
    date_from, date_to = _period_bounds(days_back)
    start_rrd_id = await _get_last_rrd_id(date_from, date_to)
    loaded = 0
    last_rrd_id = start_rrd_id

    async with db_manager.session() as session:
        run_result = await session.execute(
            text(
                """
                INSERT INTO wb_etl_runs (job_name, date_from, date_to, status, source, last_rrd_id)
                VALUES ('wb_finance_raw', :date_from, :date_to, 'running', 'finance-v1', :last_rrd_id)
                RETURNING id
                """
            ),
            {"date_from": date_from, "date_to": date_to, "last_rrd_id": last_rrd_id},
        )
        run_id = int(run_result.scalar())

    partial_warning: str | None = None
    try:
        async with WBFinanceClient(api_key=api_key) as client:
            while True:
                try:
                    rows = await client.get_sales_report_detailed(
                        date_from=date_from.isoformat(),
                        date_to=date_to.isoformat(),
                        rrd_id=last_rrd_id,
                        limit=limit,
                    )
                except Exception as exc:
                    root_exc = exc.last_attempt.exception() if isinstance(exc, RetryError) else exc
                    is_rate_limit = isinstance(root_exc, WBAPIError) and root_exc.status_code == 429
                    if is_rate_limit and loaded > 0:
                        partial_warning = "Stopped by WB 429 rate limit; partial batch saved."
                        logger.warning(partial_warning)
                        break
                    raise
                if not rows:
                    break

                async with db_manager.session() as session:
                    for row in rows:
                        row_rrd_id = int(row.get("rrd_id") or row.get("rrdId") or 0)
                        row_nm_id = row.get("nm_id") or row.get("nmId")
                        row_srid = row.get("srid")
                        await session.execute(
                            text(
                                """
                                INSERT INTO wb_raw_sales_report_details
                                (date_from, date_to, rrd_id, nm_id, srid, row_json, source)
                                VALUES (:date_from, :date_to, :rrd_id, :nm_id, :srid, CAST(:row_json AS JSONB), 'finance-v1')
                                ON CONFLICT (date_from, date_to, rrd_id) DO NOTHING
                                """
                            ),
                            {
                                "date_from": date_from,
                                "date_to": date_to,
                                "rrd_id": row_rrd_id,
                                "nm_id": row_nm_id,
                                "srid": row_srid,
                                "row_json": json.dumps(row, ensure_ascii=False),
                            },
                        )
                        if row_rrd_id > last_rrd_id:
                            last_rrd_id = row_rrd_id
                    loaded += len(rows)
                logger.info("WB raw chunk loaded: rows=%s, last_rrd_id=%s", len(rows), last_rrd_id)

        async with db_manager.session() as session:
            await session.execute(
                text(
                    """
                    UPDATE wb_etl_runs
                    SET status=:status,
                        rows_loaded=:rows_loaded,
                        last_rrd_id=:last_rrd_id,
                        finished_at=NOW(),
                        error_message=:error_message
                    WHERE id=:run_id
                    """
                ),
                {
                    "status": "partial_success" if partial_warning else "success",
                    "rows_loaded": loaded,
                    "last_rrd_id": last_rrd_id,
                    "error_message": partial_warning,
                    "run_id": run_id,
                },
            )

        return {
            "status": "partial_success" if partial_warning else "success",
            "source": "finance-v1",
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "rows_loaded": loaded,
            "start_rrd_id": start_rrd_id,
            "last_rrd_id": last_rrd_id,
            "warning": partial_warning,
        }
    except Exception as exc:
        async with db_manager.session() as session:
            await session.execute(
                text(
                    """
                    UPDATE wb_etl_runs
                    SET status='failed',
                        rows_loaded=:rows_loaded,
                        last_rrd_id=:last_rrd_id,
                        finished_at=NOW(),
                        error_message=:error_message
                    WHERE id=:run_id
                    """
                ),
                {
                    "rows_loaded": loaded,
                    "last_rrd_id": last_rrd_id,
                    "error_message": str(exc)[:4000],
                    "run_id": run_id,
                },
            )
        raise
