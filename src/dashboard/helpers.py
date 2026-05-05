"""Dashboard helper functions — shared utilities for route modules."""
import math
import os
import re
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import asyncpg

from src.config import settings
from src.dashboard.constants import (
    BASE_DIR, MSK,
    FINANCE_REPORT_ROWS, FINANCE_ROW_META, FINANCE_ZERO_ROWS,
    FINANCE_DESCRIPTION_FILTERS, ACCRUAL_COST_ROW_KEYS,
    AD_FINANCE_DESCRIPTIONS, PLAN_BASE_VALUES, PLAN_BASE_PCTS,
    PLAN_BASELINE_REVENUE,
)

def clean_nan_values(obj: Any) -> Any:
    """Рекурсивно заменяет NaN, Infinity, -Infinity на None."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: clean_nan_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_nan_values(item) for item in obj]
    return obj


def month_bounds(month_value: str) -> Tuple[datetime, datetime, List[str]]:
    year_str, month_str = month_value.split("-", 1)
    year = int(year_str)
    month = int(month_str)
    last_day = monthrange(year, month)[1]
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        month_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        month_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    days = [f"{year:04d}-{month:02d}-{day:02d}" for day in range(1, last_day + 1)]
    return month_start, month_end, days


def safe_divide(numerator: float, denominator: float) -> Optional[float]:
    if abs(denominator) < 1e-9:
        return None
    return numerator / denominator


def _calc_ad_kpis(views: int, clicks: int, spent: float,
                  orders: int = 0, revenue: float = 0.0) -> Dict[str, float]:
    """CTR/CPC/CPO/DRR из базовых рекламных метрик."""
    return {
        "ctr": round((clicks / views * 100) if views > 0 else 0.0, 2),
        "cpc": round((spent / clicks) if clicks > 0 else 0.0, 2),
        "cpo": round((spent / orders) if orders > 0 else 0.0, 2),
        "drr": round((spent / revenue * 100) if revenue > 0 else 0.0, 1),
    }


def normalize_offer_id(value: Optional[str]) -> str:
    if value is None:
        return ""
    normalized = str(value).strip()
    while normalized.startswith("'"):
        normalized = normalized[1:].strip()
    return normalized


def _normalize_column_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _pick_df_column(columns: List[str], candidates: Tuple[str, ...]) -> Optional[str]:
    normalized_map = {_normalize_column_name(col): col for col in columns}
    for candidate in candidates:
        col = normalized_map.get(_normalize_column_name(candidate))
        if col:
            return col
    for candidate in candidates:
        needle = _normalize_column_name(candidate)
        for normalized, original in normalized_map.items():
            if needle and needle in normalized:
                return original
    return None


def init_row(days: List[str], key: str) -> Dict[str, Any]:
    return {
        "key": key,
        "label": FINANCE_ROW_META[key]["label"],
        "kind": FINANCE_ROW_META[key]["kind"],
        "format": FINANCE_ROW_META[key].get("format", "number"),
        "daily": {day: 0.0 for day in days},
        "total": 0.0,
    }


def recalculate_row_total(row: Dict[str, Any], days: List[str]) -> None:
    row["total"] = float(sum(row["daily"][day] for day in days))


def set_row_from_formula(
    rows_map: Dict[str, Dict[str, Any]],
    target_key: str,
    days: List[str],
    formula,
) -> None:
    row = rows_map[target_key]
    for day in days:
        value = formula(day)
        row["daily"][day] = 0.0 if value is None else float(value)
    recalculate_row_total(row, days)


def append_finance_posting(
    postings: List[Dict[str, Any]],
    day: str,
    description: str,
    amount: float,
) -> None:
    description = fix_mojibake_cp1251_utf8(description)
    if not description or abs(amount) < 1e-9:
        return
    postings.append(
        {
            "day": day,
            "description": description,
            "amount": float(amount),
        }
    )


def fix_mojibake_cp1251_utf8(value: Optional[str]) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        return text.encode("cp1251").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def to_mojibake_cp1251_utf8(value: Optional[str]) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        return text.encode("utf-8").decode("cp1251")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def finance_row_key_for_compensation_article(article_name: Optional[str]) -> str:
    article = fix_mojibake_cp1251_utf8((article_name or "").strip()).lower()
    if "????????" in article:
        return "shortage_retention"
    if "????????????" in article and "?????" in article:
        return "other_accrual_adjustments"
    return "compensations"


def to_asyncpg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.split("://", 1)[1]
    return database_url


def parse_date_utc(value: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    day = datetime.strptime(value, "%Y-%m-%d").date()
    if end_of_day:
        # Exclusive upper bound.
        return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)


def month_start_msk(month_value: str) -> datetime:
    year_str, month_str = month_value.split("-", 1)
    return datetime(int(year_str), int(month_str), 1, tzinfo=MSK)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        result = float(value)
        # Проверяем на NaN и Inf
        if not math.isfinite(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def extract_item_article(item: Dict[str, Any]) -> Optional[str]:
    for key in ("offer_id", "offerId", "article", "offer", "sku"):
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _build_cost_description_whitelist() -> set[str]:
    descriptions: set[str] = set()
    for key in ACCRUAL_COST_ROW_KEYS:
        for value in FINANCE_DESCRIPTION_FILTERS.get(key, []):
            cleaned = str(value or "").strip().lower()
            if cleaned:
                descriptions.add(cleaned)
    # Variants used in raw transaction exports.
    descriptions.update(
        {
            "обработка отправления drop-off (пвз)",
            "обработка отправления drop-off (сц)",
            "логистика - отмена начисления",
            "выдача товара",
            "выдача товара - отмена начисления (сторно возвратов на пвз)",
        }
    )
    return descriptions


def _is_ad_description(description: str) -> bool:
    return description.strip().lower() in AD_FINANCE_DESCRIPTIONS


def normalize_article_key(value: Optional[str]) -> str:
    text = (value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def normalize_sku_value(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def article_tags_from_offer_id(value: Optional[str]) -> List[str]:
    text = str(value or "").strip().lower()
    if text.startswith("'"):
        text = text[1:].strip()

    tags: List[str] = []
    if text.startswith("1"):
        tags.append("подстолье")
    if text.startswith("2"):
        tags.append("дровницы")
    return tags


def build_cost_maps(article_cost_rows: List[Any]) -> Tuple[Dict[int, float], Dict[str, float]]:
    sku_cost_map: Dict[int, float] = {}
    article_cost_map: Dict[str, float] = {}
    for row in article_cost_rows:
        cost = as_float(row["unit_cost"])
        sku = normalize_sku_value(row["sku"])
        article = str(row["article"]).strip() if row["article"] else ""
        if sku is not None:
            sku_cost_map[sku] = cost
        if article:
            article_cost_map[normalize_article_key(article)] = cost
    return sku_cost_map, article_cost_map


async def load_sku_identity_map(
    conn: asyncpg.Connection,
    skus: List[int],
) -> Dict[int, Dict[str, Any]]:
    """Единый резолвер товара по SKU: offer_id/product_id/name/status.

    Приоритет источников:
    1) report_products_items (наиболее полный слепок каталога Ozon);
    2) article_characteristics (доп. атрибуты/артикул из API);
    3) fact_order_items + products (fallback по историческим заказам).
    """
    if not skus:
        return {}

    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (sku)
            sku,
            offer_id,
            product_id,
            product_name,
            product_status
        FROM (
            SELECT
                fbo_sku_id::bigint AS sku,
                offer_id,
                ozon_product_id::bigint AS product_id,
                product_name,
                product_status,
                1 AS prio,
                last_synced_at AS synced_at
            FROM report_products_items
            WHERE fbo_sku_id IS NOT NULL

            UNION ALL

            SELECT
                fbs_sku_id::bigint AS sku,
                offer_id,
                ozon_product_id::bigint AS product_id,
                product_name,
                product_status,
                1 AS prio,
                last_synced_at AS synced_at
            FROM report_products_items
            WHERE fbs_sku_id IS NOT NULL

            UNION ALL

            SELECT
                sku::bigint AS sku,
                offer_id,
                NULL::bigint AS product_id,
                article_name AS product_name,
                NULL::text AS product_status,
                2 AS prio,
                updated_at AS synced_at
            FROM article_characteristics
            WHERE sku IS NOT NULL

            UNION ALL

            SELECT
                foi.sku::bigint AS sku,
                foi.offer_id,
                p.product_id::bigint AS product_id,
                p.name AS product_name,
                p.status AS product_status,
                3 AS prio,
                foi.last_synced_at AS synced_at
            FROM fact_order_items foi
            LEFT JOIN products p ON p.offer_id = foi.offer_id
            WHERE foi.sku IS NOT NULL
        ) src
        WHERE sku = any($1::bigint[])
          AND coalesce(trim(offer_id), '') <> ''
        ORDER BY sku, prio, synced_at DESC NULLS LAST
        """,
        skus,
    )

    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        sku = normalize_sku_value(row["sku"])
        if sku is None:
            continue
        offer_id = normalize_offer_id(row["offer_id"])
        if not offer_id:
            continue
        out[sku] = {
            "sku": sku,
            "offer_id": offer_id,
            "product_id": normalize_sku_value(row["product_id"]),
            "product_name": str(row["product_name"] or "").strip() or None,
            "product_status": str(row["product_status"] or "").strip() or None,
        }
    return out


async def load_posting_context(
    conn: asyncpg.Connection,
    posting_numbers: List[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], set[str], set[str], Dict[str, List[Dict[str, Any]]]]:
    posting_items_map: Dict[str, List[Dict[str, Any]]] = {}
    delivered_postings: set[str] = set()
    returned_postings: set[str] = set()
    snapshot_items_map: Dict[str, List[Dict[str, Any]]] = {}

    if not posting_numbers:
        return posting_items_map, delivered_postings, returned_postings, snapshot_items_map

    uniq_postings = sorted({pn for pn in posting_numbers if pn})
    item_rows = await conn.fetch(
        """
        SELECT posting_number, line_no, offer_id, sku, quantity, price
        FROM fact_order_items
        WHERE posting_number = ANY($1)
        ORDER BY posting_number, line_no, last_synced_at DESC
        """,
        uniq_postings,
    )
    best_item_rows: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for row in item_rows:
        posting_number = row["posting_number"]
        if not posting_number:
            continue
        line_no = int(row["line_no"] or 0)
        row_data = {
            "offer_id": row["offer_id"],
            "sku": row["sku"],
            "quantity": row["quantity"],
            "price": row["price"],
        }
        score = sum(1 for value in (row["offer_id"], row["sku"], row["quantity"], row["price"]) if value not in (None, ""))
        key = (posting_number, line_no)
        existing = best_item_rows.get(key)
        if existing is None or score > existing["_score"]:
            best_item_rows[key] = {"_score": score, **row_data}

    for (posting_number, _line_no), row in sorted(best_item_rows.items()):
        posting_items_map.setdefault(posting_number, []).append(
            {
                "offer_id": row["offer_id"],
                "sku": row["sku"],
                "quantity": row["quantity"],
                "price": row["price"],
            }
        )

    delivered_rows = await conn.fetch(
        """
        SELECT posting_number
        FROM fact_orders
        WHERE posting_number = ANY($1)
          AND (
              status = convert_from(decode('c4eef1f2e0e2ebe5ed', 'hex'), 'WIN1251')
              OR lower(status) = 'delivered'
          )
        """,
        uniq_postings,
    )
    delivered_postings = {row["posting_number"] for row in delivered_rows if row["posting_number"]}

    returned_rows = await conn.fetch(
        """
        WITH returned AS (
            SELECT posting_number
            FROM returns
            WHERE posting_number = ANY($1)
            UNION
            SELECT posting_number
            FROM returns_fbo
            WHERE posting_number = ANY($1)
        )
        SELECT posting_number
        FROM returned
        """,
        uniq_postings,
    )
    returned_postings = {row["posting_number"] for row in returned_rows if row["posting_number"]}

    snapshot_rows = await conn.fetch(
        """
        SELECT DISTINCT ON (posting_number)
            posting_number,
            response_json
        FROM posting_transaction_snapshots
        WHERE posting_number = ANY($1)
        ORDER BY posting_number, requested_at DESC
        """,
        uniq_postings,
    )
    for row in snapshot_rows:
        posting_number = row["posting_number"]
        response_json = row["response_json"]
        if not posting_number or not isinstance(response_json, dict):
            continue
        result = response_json.get("result")
        operations = result.get("operations") if isinstance(result, dict) else []
        if not isinstance(operations, list):
            continue
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            items = operation.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    snapshot_items_map.setdefault(posting_number, []).append(item)

    return posting_items_map, delivered_postings, returned_postings, snapshot_items_map


def lookup_unit_cost(
    sku_cost_map: Dict[int, float],
    article_cost_map: Dict[str, float],
    *,
    sku: Any = None,
    article: Optional[str] = None,
) -> Optional[float]:
    normalized_sku = normalize_sku_value(sku)
    if normalized_sku is not None and normalized_sku in sku_cost_map:
        return sku_cost_map[normalized_sku]
    if article:
        return article_cost_map.get(normalize_article_key(article))
    return None


def month_timeline(month_value: str) -> Tuple[int, int]:
    year_str, month_str = month_value.split("-", 1)
    year = int(year_str)
    month = int(month_str)
    days_total = monthrange(year, month)[1]

    now_msk = datetime.now(MSK)
    current_month = (now_msk.year, now_msk.month)
    selected_month = (year, month)
    if selected_month < current_month:
        return days_total, days_total
    if selected_month > current_month:
        return 0, days_total
    return min(now_msk.day, days_total), days_total


def scale_plan_value(base_value: float, revenue_plan: float) -> float:
    if PLAN_BASELINE_REVENUE <= 0:
        return 0.0
    return base_value * (revenue_plan / PLAN_BASELINE_REVENUE)


def build_kpi_summary(
    month_value: str,
    rows_map: Dict[str, Dict[str, Any]],
    marketing_daily: Dict[str, float],
    revenue_plan_total: float,
    plan_editable: bool,
    prev_month_pcts: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    days_passed, days_total = month_timeline(month_value)

    # Fact values should mirror totals from the finance table above.
    # Используем as_float для защиты от None/NaN
    fact_revenue = as_float(rows_map["revenue_sales"]["total"])
    fact_expenses_mp = as_float(rows_map["marketplace_expenses"]["total"])
    fact_marketing = as_float(rows_map["promotion_total"]["total"])
    fact_expenses_total = as_float(rows_map["all_expenses"]["total"])
    fact_money_on_account = as_float(rows_map["accrued"]["total"])
    fact_material_cost = as_float(rows_map["material_cost"]["total"])
    fact_gross_profit = as_float(rows_map["gross_profit"]["total"])

    def forecast_value(value: float) -> Optional[float]:
        if days_passed <= 0:
            return None
        return value / days_passed * days_total

    forecast_revenue = forecast_value(fact_revenue)
    forecast_expenses_mp = forecast_value(fact_expenses_mp)
    forecast_marketing = forecast_value(fact_marketing)
    forecast_expenses_total = None
    forecast_money_on_account = forecast_value(fact_money_on_account)
    forecast_material_cost = forecast_value(fact_material_cost)
    forecast_gross_profit = forecast_value(fact_gross_profit)
    if forecast_expenses_mp is not None and forecast_marketing is not None:
        forecast_expenses_total = forecast_expenses_mp + forecast_marketing

    plan_revenue = revenue_plan_total
    
    # Raschet plana na osnove procentov predydushchego mesyaca
    # Formula: Pokazatel (plan) = Vyruchka MP (plan) * Pokazatel % (fakt predydushchego mesyaca)
    if prev_month_pcts:
        # Procenty iz predydushchego mesyaca
        pct_expenses_mp = prev_month_pcts.get("expenses_mp", 0.60)
        pct_commission = prev_month_pcts.get("commission", 0.35)
        pct_logistics = prev_month_pcts.get("logistics", 0.10)
        pct_ads = prev_month_pcts.get("ads", 0.15)
        pct_total_expenses = prev_month_pcts.get("total_expenses", 0.60)
        
        # Raschet absolyutnyh znacheniy plana
        plan_commission = plan_revenue * pct_commission
        plan_logistics = plan_revenue * pct_logistics
        plan_marketing = plan_revenue * pct_ads
        plan_expenses_mp = plan_revenue * pct_expenses_mp  # Obshchie rashody MP
        plan_expenses_total = plan_expenses_mp + plan_marketing  # Itogo rashody
        
        # Drugie pokazateli
        plan_material_cost = plan_revenue * prev_month_pcts.get("material_cost", 0.15)
        plan_money_on_account = plan_revenue * prev_month_pcts.get("money_on_account", 0.40)
        plan_gross_profit = plan_revenue * prev_month_pcts.get("gross_profit", 0.25)
    else:
        # Esli net dannyh predydushchego mesyaca, planovye procenty ne rasschityvayutsya.
        plan_commission = None
        plan_logistics = None
        plan_marketing = None
        plan_expenses_mp = None
        plan_expenses_total = None
        plan_money_on_account = None
        plan_material_cost = None
        plan_gross_profit = None

    # Procenty plana = procenty predydushchego mesyaca (fakt)
    if prev_month_pcts:
        plan_expenses_mp_pct = prev_month_pcts.get("expenses_mp", 0.60)
        plan_marketing_pct = prev_month_pcts.get("ads", 0.15)
        plan_total_expenses_pct = prev_month_pcts.get("total_expenses", 0.60)
        plan_material_cost_pct = prev_month_pcts.get("material_cost", 0.15)
        plan_gross_to_money_pct = prev_month_pcts.get("gross_to_money_pct")
        plan_gross_to_revenue_pct = prev_month_pcts.get("gross_to_revenue_pct")
    else:
        # Net predydushchego mesyaca - planovye procenty ne rasschityvayutsya
        plan_expenses_mp_pct = None
        plan_marketing_pct = None
        plan_total_expenses_pct = None
        plan_material_cost_pct = None
        plan_gross_to_money_pct = None
        plan_gross_to_revenue_pct = None
    
    plan_expenses_total_pct = plan_total_expenses_pct
    
    # Берём проценты из уже рассчитанных строк таблицы выше
    fact_marketing_pct = as_float(rows_map["marketing_pct"]["total"])
    fact_expenses_mp_pct = as_float(rows_map["marketplace_expenses_pct"]["total"])
    fact_expenses_total_pct = as_float(rows_map["all_expenses"]["total"]) / fact_revenue if fact_revenue > 0 else 0.0
    fact_material_cost_pct = safe_divide(fact_material_cost, fact_revenue)
    fact_gross_to_money_pct = safe_divide(fact_gross_profit, fact_money_on_account)
    fact_gross_to_revenue_pct = safe_divide(fact_gross_profit, fact_revenue)
    
    return [
        {"key": "revenue_mp", "label": "Выручка МП", "format": "number", "fact": fact_revenue, "forecast": forecast_revenue, "plan": plan_revenue, "plan_editable": plan_editable},
        {"key": "expenses_mp", "label": "Расходы МП", "format": "number", "fact": fact_expenses_mp, "forecast": forecast_expenses_mp, "plan": plan_expenses_mp},
        {"key": "expenses_mp_pct", "label": "Расходы МП %", "format": "percent", "fact": fact_expenses_mp_pct, "forecast": fact_expenses_mp_pct, "plan": plan_expenses_mp_pct},
        {"key": "marketing", "label": "Маркетинг", "format": "number", "fact": fact_marketing, "forecast": forecast_marketing, "plan": plan_marketing},
        {"key": "marketing_pct", "label": "Маркетинг %", "format": "percent", "fact": fact_marketing_pct, "forecast": fact_marketing_pct, "plan": plan_marketing_pct},
        {"key": "expenses_total", "label": "ИТОГО расходы МП", "format": "number", "fact": fact_expenses_total, "forecast": forecast_expenses_total, "plan": plan_expenses_total},
        {"key": "expenses_total_pct", "label": "ИТОГО расходы МП %", "format": "percent", "fact": fact_expenses_total_pct, "forecast": fact_expenses_total_pct, "plan": plan_expenses_total_pct},
        {"key": "money_on_account", "label": "Деньги на счет", "format": "number", "fact": fact_money_on_account, "forecast": forecast_money_on_account, "plan": plan_money_on_account},
        {"key": "material_cost", "label": "Себестоимость", "format": "number", "fact": fact_material_cost, "forecast": forecast_material_cost, "plan": plan_material_cost},
        {"key": "material_cost_pct", "label": "Себестоимость %", "format": "percent", "fact": fact_material_cost_pct, "forecast": fact_material_cost_pct, "plan": plan_material_cost_pct},
        {"key": "gross_profit", "label": "Валовая прибыль", "format": "number", "fact": fact_gross_profit, "forecast": forecast_gross_profit, "plan": plan_gross_profit},
        {"key": "gross_to_money_pct", "label": "Валовая к деньгам на счет", "format": "percent", "fact": fact_gross_to_money_pct, "forecast": fact_gross_to_money_pct, "plan": plan_gross_to_money_pct},
        {"key": "gross_to_revenue_pct", "label": "Валовая к выручке МП", "format": "percent", "fact": fact_gross_to_revenue_pct, "forecast": fact_gross_to_revenue_pct, "plan": plan_gross_to_revenue_pct},
    ]


def build_where(
    schema: Optional[str],
    date_from: Optional[datetime],
    date_to_exclusive: Optional[datetime],
    offer_id: Optional[str],
) -> Tuple[str, List[Any]]:
    conditions: List[str] = []
    params: List[Any] = []
    idx = 1

    if schema in {"FBO", "FBS"}:
        conditions.append(f"delivery_schema = ${idx}")
        params.append(schema)
        idx += 1

    if date_from is not None:
        conditions.append(f"created_at >= ${idx}")
        params.append(date_from)
        idx += 1

    if date_to_exclusive is not None:
        conditions.append(f"created_at < ${idx}")
        params.append(date_to_exclusive)
        idx += 1

    if offer_id:
        # Exact match by item offer_id from JSON array.
        conditions.append(
            f"""EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(items::jsonb) AS e
                    WHERE lower(e->>'offer_id') = lower(${idx})
                )"""
        )
        params.append(offer_id)

    if not conditions:
        return "", params
    return "WHERE " + " AND ".join(conditions), params


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


async def _ozon_supply_post(
    session: aiohttp.ClientSession,
    endpoint: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    retries: int = 7,
    retry_delay_seconds: float = 8.0,
    retry_backoff_factor: float = 1.7,
    retry_max_delay: float = 60.0,
) -> Tuple[int, Dict[str, Any]]:
    url = f"https://api-seller.ozon.ru{endpoint}"
    last_status = 0
    last_data: Dict[str, Any] = {}

    for attempt in range(1, retries + 1):
        async with session.post(url, headers=headers, json=body) as resp:
            last_status = resp.status
            retry_after_header = resp.headers.get("Retry-After")
            text = await resp.text()
            try:
                data: Dict[str, Any] = json.loads(text)
            except Exception:
                data = {"raw": text}

        last_data = data
        if last_status == 429 and attempt < retries:
            delay = retry_delay_seconds * (retry_backoff_factor ** (attempt - 1))
            delay = min(delay, retry_max_delay)
            try:
                retry_after = float(retry_after_header or 0)
                if retry_after > 0:
                    delay = max(delay, retry_after)
            except Exception:
                pass
            jitter = max(0.0, delay * 0.15)
            await asyncio.sleep(delay + random.uniform(0.0, jitter))
            continue
        return last_status, last_data

    return last_status, last_data


def _extract_supply_clusters(supply_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_cluster: Dict[str, Dict[str, Any]] = {}

    for item in supply_items:
        item_sku = _to_int(item.get("sku"))
        details = item.get("details") or []
        if not isinstance(details, list):
            continue

        for detail in details:
            if not isinstance(detail, dict):
                continue
            allocated = _to_int(detail.get("allocated_supply")) or 0
            if allocated <= 0:
                continue

            cluster_name = str(
                detail.get("cluster_name") or detail.get("warehouse_name") or ""
            ).strip()
            if not cluster_name:
                continue

            sku = _to_int(detail.get("sku")) or item_sku
            if not sku:
                continue

            bucket = by_cluster.setdefault(
                cluster_name,
                {"cluster_name": cluster_name, "allocated_total": 0, "sku_set": set()},
            )
            bucket["allocated_total"] += allocated
            bucket["sku_set"].add(sku)

    clusters: List[Dict[str, Any]] = []
    for cluster_name, raw in by_cluster.items():
        sku_list = sorted(raw["sku_set"])
        if not sku_list:
            continue
        clusters.append(
            {
                "cluster_name": cluster_name,
                "allocated_total": int(raw["allocated_total"]),
                "skus": sku_list,
            }
        )

    clusters.sort(key=lambda x: (-x["allocated_total"], x["cluster_name"]))
    return clusters


def _normalize_cluster_name(name: str) -> str:
    normalized = (name or "").strip().lower().replace("ё", "е")
    normalized = normalized.replace("_", " ").replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _normalize_text_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def _get_env_from_dotenv(key: str) -> str:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return ""
    try:
        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, env_val = line.split("=", 1)
            if env_key.strip() == key:
                return env_val.strip()
    except Exception:
        return ""
    return ""


def _get_ozon_credentials() -> Tuple[str, str]:
    """Return (client_id, api_key) from env/settings/.env."""
    client_id = (
        os.getenv("OZON_CLIENT_ID")
        or getattr(settings, "ozon_client_id", "")
        or _get_env_from_dotenv("OZON_CLIENT_ID")
        or ""
    ).strip()
    api_key = (
        os.getenv("OZON_API_KEY")
        or getattr(settings, "ozon_api_key", "")
        or _get_env_from_dotenv("OZON_API_KEY")
        or ""
    ).strip()
    return client_id, api_key


async def _ozon_post_json(
    session: aiohttp.ClientSession,
    endpoint: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    retries: int = 6,
    delay_seconds: float = 4.0,
) -> Tuple[int, Dict[str, Any]]:
    url = f"https://api-seller.ozon.ru{endpoint}"
    last_status = 0
    last_data: Dict[str, Any] = {}
    for attempt in range(1, retries + 1):
        async with session.post(url, headers=headers, json=body) as resp:
            last_status = resp.status
            text = await resp.text()
            try:
                last_data = json.loads(text)
            except Exception:
                last_data = {"raw": text}
        if last_status == 429 and attempt < retries:
            await asyncio.sleep(delay_seconds * attempt)
            continue
        return last_status, last_data
    return last_status, last_data


ACCRUAL_COST_DESCRIPTION_WHITELIST = _build_cost_description_whitelist()
