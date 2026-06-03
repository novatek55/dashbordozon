"""Dashboard routes/unitka.py — эндпоинты модуля «Юнитка».

Endpoints:
- GET  /api/unitka/clusters          — список кластеров для dropdown
- GET  /api/unitka/offer-search?q=X  — autocomplete по offer_id
- GET  /api/unitka/logistics-tariff  — lookup по прайсу (cluster_from, cluster_to, volume_l, price)
- GET  /api/unitka/load-fact?offer_id=X&days=30  — фактическая экономика на 1 ед.
- GET  /api/unitka/fetch-dimensions?offer_id=X   — точечная синхронизация габаритов
- POST /api/unitka/competitor-lookup            — прокси к calculator.ozon.ru
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import aiohttp
import asyncpg
from aiohttp import web

from src.dashboard.constants import MSK
from src.dashboard.helpers import normalize_offer_id

logger = logging.getLogger(__name__)


# ====================================================================
# Константы / алиасы кластеров
# ====================================================================

CLUSTER_ALIASES = {
    "мск": "Москва, МО и Дальние регионы",
    "москва": "Москва, МО и Дальние регионы",
    "спб": "Санкт-Петербург и СЗО",
    "питер": "Санкт-Петербург и СЗО",
    "нск": "Новосибирск",
    "екб": "Екатеринбург",
    "кзн": "Казань",
    "рнд": "Ростов",
    "крд": "Краснодар",
    "смр": "Самара",
}

CROSS_CLUSTER_RATE = Decimal("0.08")  # 8% наценка при cluster_from != cluster_to
INTERNAL_BASE_URL = "http://127.0.0.1:8088"


def _cluster_alias(name: Optional[str]) -> str:
    """Нормализует короткий алиас к полному имени кластера в прайсе."""
    if not name:
        return ""
    s = str(name).strip()
    return CLUSTER_ALIASES.get(s.lower(), s)


# ====================================================================
# /api/unitka/clusters
# ====================================================================

async def get_unitka_clusters(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT cluster_from FROM logistics_tariffs "
            "WHERE cluster_from <> '*' ORDER BY cluster_from"
        )
    return web.json_response({"clusters": [r[0] for r in rows]})


# ====================================================================
# /api/unitka/offer-search
# ====================================================================

async def get_unitka_offer_search(request: web.Request) -> web.Response:
    q = (request.query.get("q") or "").strip()
    if len(q) < 1:
        return web.json_response({"items": []})

    pool: asyncpg.Pool = request.app["pool"]
    like = f"%{q.lower()}%"
    prefix = f"{q.lower()}%"
    # Ищем по offer_id, name, product_id И по sku (sku лежит в raw_data.product_info_v3.sku).
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT offer_id, name,
                   (raw_data::jsonb)->'product_info_v3'->>'sku' AS sku
            FROM products
            WHERE offer_id IS NOT NULL AND offer_id <> ''
              AND COALESCE(is_visible, TRUE) = TRUE
              AND NOT (
                COALESCE(lower(status), '') LIKE '%archiv%'
                OR COALESCE(lower(status), '') LIKE '%delete%'
                OR COALESCE(lower(status), '') LIKE '%не прода%'
                OR COALESCE(lower(status), '') LIKE '%not for sale%'
                OR COALESCE(lower(status), '') LIKE '%removed%'
              )
              AND (
                lower(offer_id) LIKE $1
                OR lower(name) LIKE $2
                OR product_id::text LIKE $1
                OR (raw_data::jsonb)->'product_info_v3'->>'sku' LIKE $1
              )
            ORDER BY
              (CASE WHEN lower(offer_id) LIKE $3 THEN 0
                    WHEN (raw_data::jsonb)->'product_info_v3'->>'sku' LIKE $3 THEN 1
                    ELSE 2 END),
              offer_id
            LIMIT 20
            """,
            like, like, prefix,
        )
    items = [{"offer_id": r["offer_id"], "name": r["name"] or "",
              "sku": r["sku"] or ""} for r in rows]
    return web.json_response({"items": items})


# ====================================================================
# /api/unitka/logistics-tariff
# ====================================================================

async def get_unitka_logistics_tariff(request: web.Request) -> web.Response:
    cluster_from_raw = (request.query.get("cluster_from") or "").strip()
    cluster_to_raw = (request.query.get("cluster_to") or "").strip()
    volume_raw = (request.query.get("volume_l") or "").strip()
    price_raw = (request.query.get("price") or "0").strip()

    if not cluster_from_raw or not cluster_to_raw:
        return web.json_response({"error": "cluster_from и cluster_to обязательны"}, status=400)
    try:
        volume_l = Decimal(volume_raw.replace(",", "."))
        price = Decimal(price_raw.replace(",", "."))
    except Exception:
        return web.json_response({"error": "volume_l/price должны быть числами"}, status=400)
    if volume_l <= 0:
        return web.json_response({"error": "volume_l должен быть > 0"}, status=400)

    c_from = _cluster_alias(cluster_from_raw)
    c_to = _cluster_alias(cluster_to_raw)

    pool: asyncpg.Pool = request.app["pool"]
    fallback_used = False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT price_under_300, price_over_300, volume_min_l, volume_max_l
            FROM logistics_tariffs
            WHERE cluster_from = $1 AND cluster_to = $2
              AND volume_min_l <= $3 AND volume_max_l >= $3
            ORDER BY volume_min_l DESC
            LIMIT 1
            """,
            c_from, c_to, volume_l,
        )
        if row is None:
            row = await conn.fetchrow(
                """
                SELECT price_under_300, price_over_300, volume_min_l, volume_max_l
                FROM logistics_tariffs
                WHERE cluster_from = '*' AND cluster_to = '*'
                  AND volume_min_l <= $1 AND volume_max_l >= $1
                ORDER BY volume_min_l DESC
                LIMIT 1
                """,
                volume_l,
            )
            fallback_used = True

    if row is None:
        return web.json_response(
            {"error": f"Тариф не найден: {c_from} → {c_to}, объём {volume_l} л"},
            status=404,
        )

    base_tariff = row["price_over_300"] if price > Decimal("300") else row["price_under_300"]
    base_tariff = Decimal(str(base_tariff))

    cross_surcharge = Decimal("0")
    if c_from != c_to:
        cross_surcharge = (price * CROSS_CLUSTER_RATE).quantize(Decimal("0.01"))

    total = (base_tariff + cross_surcharge).quantize(Decimal("0.01"))

    return web.json_response({
        "base_tariff": float(base_tariff),
        "cross_cluster_surcharge": float(cross_surcharge),
        "total": float(total),
        "matched_volume_bucket": f"{row['volume_min_l']}-{row['volume_max_l']} л",
        "fallback_used": fallback_used,
        "cluster_from_resolved": c_from,
        "cluster_to_resolved": c_to,
    })


# ====================================================================
# /api/unitka/load-fact
# ====================================================================

async def _fetch_accruals_for_offer(
    base_url: str, offer_id: str, date_from: date, date_to: date,
) -> Optional[Dict[str, Any]]:
    """Запрашивает accruals-comp-by-article и ищет в items строку по offer_id."""
    url = f"{base_url}/api/accruals-comp-by-article"
    params = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "offer_id": offer_id,
        "distribute_no_article": "1",
        "limit": "100",
    }
    timeout = aiohttp.ClientTimeout(total=120)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning("accruals returned %s for offer %s", resp.status, offer_id)
                    return None
                data = await resp.json()
    except Exception as e:
        logger.warning("accruals fetch failed: %s", e)
        return None

    target_norm = normalize_offer_id(offer_id).lower()
    for item in data.get("items", []) or []:
        cur = normalize_offer_id(item.get("offer_id_normalized") or item.get("offer_id") or "").lower()
        if cur == target_norm:
            return item
    return None


async def get_unitka_load_fact(request: web.Request) -> web.Response:
    offer_id_raw = (request.query.get("offer_id") or "").strip()
    if not offer_id_raw:
        return web.json_response({"error": "offer_id обязателен"}, status=400)

    try:
        days = int(request.query.get("days") or "30")
    except ValueError:
        days = 30
    days = max(1, min(365, days))

    today_msk = datetime.now(MSK).date()
    date_from = today_msk - timedelta(days=days - 1)
    date_to = today_msk

    offer_id = normalize_offer_id(offer_id_raw)

    # Accruals — берём raw-значения напрямую, как в отчёте «Начисления по артикулам»:
    #   Вознаграждение  = ozon_fee_total          (без эквайринга)
    #   Партнёры        = agent_services_total    (ВКЛЮЧАЕТ эквайринг)
    #   Реклама         = promotion_total         (premium+PPC+review)
    accrual_item = await _fetch_accruals_for_offer(INTERNAL_BASE_URL, offer_id, date_from, date_to)
    values = (accrual_item or {}).get("values") or {}

    def _f(k: str) -> float:
        try:
            return float(values.get(k, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    ordered_units = _f("ordered_units")
    revenue_sales = _f("revenue_sales")
    has_units = ordered_units > 0
    units = ordered_units or 1.0

    def per_unit(x: float) -> float:
        return round(float(x) / units, 2) if units else 0.0

    price_seller = per_unit(revenue_sales) if has_units else 0.0
    commission_abs = per_unit(_f("ozon_fee_total")) if has_units else 0.0
    commission_pct = (commission_abs / price_seller * 100.0) if price_seller else 0.0
    logistics = per_unit(_f("delivery_services_total")) if has_units else 0.0
    partners = per_unit(_f("agent_services_total")) if has_units else 0.0
    fbo = per_unit(_f("fbo_services_total")) if has_units else 0.0
    # Реклама: используем ad_spend (как в формуле accrued отчёта «Начисления»:
    # marketplace_expenses = ozon_fee + delivery + agent + fbo + ad_spend).
    # Строка «Продвижение и реклама» в отчёте показывает promotion_total для информации,
    # но в сумме вычитается именно ad_spend — иначе «Начислено» не сходится.
    ads = per_unit(_f("ad_spend")) if has_units else 0.0
    cost = per_unit(_f("material_cost")) if has_units else 0.0

    # Фактическая цена покупателя: fact_order_items.buyer_paid (что реально заплатил клиент после SPP).
    ratio = 1.0
    ratio_source = "no_ozon_discount_detected"
    price_buyer = price_seller

    pool_tmp: asyncpg.Pool = request.app["pool"]
    async with pool_tmp.acquire() as conn_p:
        price_row = await conn_p.fetchrow(
            """
            SELECT
              sum(coalesce(foi.price, 0) * coalesce(foi.quantity, 0))::float8
                / nullif(sum(coalesce(foi.quantity, 0)), 0)::float8 AS avg_seller,
              sum(coalesce(foi.buyer_paid, 0) * coalesce(foi.quantity, 0))::float8
                / nullif(sum(coalesce(foi.quantity, 0)), 0)::float8 AS avg_buyer,
              sum(coalesce(foi.quantity, 0))::int AS qty
            FROM fact_order_items foi
            JOIN fact_orders fo ON fo.order_id = foi.order_id
            WHERE foi.offer_id = $1
              AND (fo.created_at AT TIME ZONE 'UTC')::date >= $2
              AND (fo.created_at AT TIME ZONE 'UTC')::date < $3
              AND coalesce(foi.quantity, 0) > 0
            """,
            offer_id, date_from, date_to + timedelta(days=1),
        )
    if price_row and price_row["qty"] and price_row["avg_seller"] and price_row["avg_buyer"]:
        avg_seller = float(price_row["avg_seller"])
        avg_buyer = float(price_row["avg_buyer"])
        if avg_seller > 0 and avg_buyer > 0:
            price_buyer = round(avg_buyer, 2)
            ratio = max(0.01, min(1.5, avg_buyer / avg_seller))
            ratio_source = "fact_order_items.buyer_paid"

    # Name + dims
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        product_row = await conn.fetchrow(
            "SELECT name, raw_data->'resolved_ids'->>'sku' AS sku "
            "FROM products WHERE offer_id = $1 LIMIT 1",
            offer_id,
        )
        dim_row = await conn.fetchrow(
            "SELECT length_cm, width_cm, height_cm, weight_kg, volume_l "
            "FROM product_dimensions WHERE offer_id = $1",
            offer_id,
        )

    title = (product_row["name"] if product_row else None) or offer_id
    sku_val = None
    if product_row and product_row["sku"]:
        try:
            sku_val = int(product_row["sku"])
        except (TypeError, ValueError):
            sku_val = None

    dimensions = None
    if dim_row:
        dimensions = {
            "length": float(dim_row["length_cm"]) if dim_row["length_cm"] is not None else None,
            "width": float(dim_row["width_cm"]) if dim_row["width_cm"] is not None else None,
            "height": float(dim_row["height_cm"]) if dim_row["height_cm"] is not None else None,
            "weight_kg": float(dim_row["weight_kg"]) if dim_row["weight_kg"] is not None else None,
            "volume_l": float(dim_row["volume_l"]) if dim_row["volume_l"] is not None else None,
        }

    return web.json_response({
        "offer_id": offer_id,
        "title": title,
        "sku": sku_val,
        "period_days": days,
        "has_units": has_units,
        "base_30d": {
            "price_seller": price_seller,
            "price_buyer": price_buyer,
            "commission_abs": commission_abs,
            "commission_pct": round(commission_pct, 2),
            "logistics": logistics,
            "partners": partners,
            "fbo": fbo,
            "ads": ads,
            "cost": cost,
            "tax_pct": 10,
            "ordered_units": ordered_units,
        },
        "buyer_to_seller_ratio": round(ratio, 4),
        "ratio_source": ratio_source,
        "dimensions": dimensions,
    })


# ====================================================================
# /api/unitka/fetch-dimensions
# ====================================================================

async def get_unitka_fetch_dimensions(request: web.Request) -> web.Response:
    """Точечная синхронизация габаритов одного артикула (из products.raw_data)."""
    offer_id = (request.query.get("offer_id") or "").strip()
    if not offer_id:
        return web.json_response({"error": "offer_id обязателен"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT raw_data->'product_attributes_v4' AS v4, "
            "       raw_data->'resolved_ids'->>'sku' AS sku "
            "FROM products WHERE offer_id = $1",
            offer_id,
        )
    if row is None:
        return web.json_response({"error": "offer_id не найден в products"}, status=404)

    v4 = row["v4"]
    if isinstance(v4, str):
        import json as _json
        try:
            v4 = _json.loads(v4)
        except Exception:
            v4 = None
    if not isinstance(v4, dict):
        return web.json_response({"error": "нет данных v4 для этого артикула"}, status=404)

    raw_h, raw_d, raw_w = v4.get("height"), v4.get("depth"), v4.get("width")
    raw_wt = v4.get("weight")
    dim_unit = (v4.get("dimension_unit") or "mm").lower()
    wt_unit = (v4.get("weight_unit") or "g").lower()

    if raw_h is None or raw_d is None or raw_w is None:
        return web.json_response({"error": "габариты в Ozon API не заполнены"}, status=404)

    try:
        h, d, w = float(raw_h), float(raw_d), float(raw_w)
    except (TypeError, ValueError):
        return web.json_response({"error": "не удалось распарсить габариты"}, status=500)

    if dim_unit == "mm":
        length_cm, width_cm, height_cm = d / 10.0, w / 10.0, h / 10.0
    elif dim_unit in ("cm", "см"):
        length_cm, width_cm, height_cm = d, w, h
    elif dim_unit in ("m", "м"):
        length_cm, width_cm, height_cm = d * 100, w * 100, h * 100
    else:
        length_cm, width_cm, height_cm = d / 10.0, w / 10.0, h / 10.0

    weight_kg = None
    if raw_wt is not None:
        try:
            wt = float(raw_wt)
            weight_kg = wt / 1000.0 if wt_unit == "g" else (wt if wt_unit == "kg" else wt / 1000.0)
        except (TypeError, ValueError):
            weight_kg = None

    volume_l = round(length_cm * width_cm * height_cm / 1000.0, 3)
    sku_val = None
    if row["sku"]:
        try:
            sku_val = int(row["sku"])
        except (TypeError, ValueError):
            sku_val = None

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO product_dimensions
                (offer_id, sku, length_cm, width_cm, height_cm, weight_kg, volume_l, source, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'ozon_api_v4', now())
            ON CONFLICT (offer_id) DO UPDATE SET
                sku=EXCLUDED.sku,
                length_cm=EXCLUDED.length_cm,
                width_cm=EXCLUDED.width_cm,
                height_cm=EXCLUDED.height_cm,
                weight_kg=EXCLUDED.weight_kg,
                volume_l=EXCLUDED.volume_l,
                source=EXCLUDED.source,
                updated_at=now()
            """,
            offer_id, sku_val,
            round(length_cm, 2), round(width_cm, 2), round(height_cm, 2),
            round(weight_kg, 3) if weight_kg is not None else None,
            volume_l,
        )

    return web.json_response({
        "offer_id": offer_id,
        "sku": sku_val,
        "length": round(length_cm, 2),
        "width": round(width_cm, 2),
        "height": round(height_cm, 2),
        "weight_kg": round(weight_kg, 3) if weight_kg is not None else None,
        "volume_l": volume_l,
        "source": "ozon_api_v4",
    })


# ====================================================================
# /api/unitka/shop-averages
# ====================================================================

async def get_unitka_shop_averages(request: web.Request) -> web.Response:
    """Средние значения по магазину на 1 единицу за период.

    Используется в UI как «запасной» вариант, если у товара ещё нет начислений.
    Считается агрегацией всех items из accruals-comp-by-article: Σ(метрика) / Σ(ordered_units).
    """
    try:
        days = int(request.query.get("days") or "30")
    except ValueError:
        days = 30
    days = max(1, min(365, days))

    today_msk = datetime.now(MSK).date()
    date_from = today_msk - timedelta(days=days - 1)
    date_to = today_msk

    url = f"{INTERNAL_BASE_URL}/api/accruals-comp-by-article"
    params = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "distribute_no_article": "1",
        "limit": "5000",
    }
    timeout = aiohttp.ClientTimeout(total=120)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, params=params) as resp:
                if resp.status != 200:
                    return web.json_response({"error": f"accruals HTTP {resp.status}"}, status=502)
                data = await resp.json()
    except Exception as e:
        return web.json_response({"error": f"network: {type(e).__name__}"}, status=502)

    total_units = 0.0
    total_revenue = 0.0
    sum_commission = 0.0
    sum_logistics = 0.0
    sum_partners = 0.0
    sum_fbo = 0.0
    sum_ads = 0.0

    def _f(values, k):
        try:
            return float(values.get(k, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    for item in data.get("items", []) or []:
        values = item.get("values") or {}
        u = _f(values, "ordered_units")
        if u <= 0:
            continue
        total_units += u
        total_revenue += _f(values, "revenue_sales")
        sum_commission += _f(values, "ozon_fee_total")
        sum_logistics += _f(values, "delivery_services_total")
        sum_partners += _f(values, "agent_services_total")
        sum_fbo += _f(values, "fbo_services_total")
        sum_ads += _f(values, "ad_spend")

    def per_unit(x: float) -> float:
        return round(x / total_units, 2) if total_units > 0 else 0.0

    avg_price = per_unit(total_revenue)
    avg_commission_abs = per_unit(sum_commission)

    def pct_of_revenue(x: float) -> float:
        return round(x / total_revenue * 100, 2) if total_revenue > 0 else 0.0

    return web.json_response({
        "period_days": days,
        "total_units": total_units,
        "avg_price_seller": avg_price,
        "avg_commission_abs": avg_commission_abs,
        # Проценты от выручки магазина — «магазинные ставки» расходов
        "avg_commission_pct": pct_of_revenue(sum_commission),
        "avg_logistics_pct": pct_of_revenue(sum_logistics),
        "avg_partners_pct": pct_of_revenue(sum_partners),
        "avg_fbo_pct": pct_of_revenue(sum_fbo),
        "avg_ads_pct": pct_of_revenue(sum_ads),
        # Абсолютные на 1 шт при средней цене магазина
        "avg_logistics": per_unit(sum_logistics),
        "avg_partners": per_unit(sum_partners),
        "avg_fbo": per_unit(sum_fbo),
        "avg_ads": per_unit(sum_ads),
    })


# ====================================================================
# /api/unitka/metrics — метрики товара (30д): продажи, выкуп, позиция
#   Для конкурента — последний snapshot из competitor_snapshots (bestsellers)
#   Для своего (offer_id) — агрегат из fact_order_items за 30д
# ====================================================================

async def get_unitka_metrics(request: web.Request) -> web.Response:
    sku = (request.query.get("sku") or "").strip()
    offer_id = (request.query.get("offer_id") or "").strip()
    days = 30
    try:
        days = max(1, min(365, int(request.query.get("days") or "30")))
    except ValueError:
        days = 30

    pool: asyncpg.Pool = request.app["pool"]
    out: Dict[str, Any] = {"source": None, "period_days": days}

    # 1. Приоритет: bestsellers snapshot для этого SKU.
    #    Дополнительные поля (views, drr, discount, и т.п.) тянем из raw_data jsonb,
    #    чтобы не плодить колонки.
    async def _bestsellers_row(conn, sku_val):
        return await conn.fetchrow(
            """
            SELECT sold_sum, sold_units, avg_price, min_price,
                   session_count, conv_to_cart, buyout_rate, lost_sales,
                   days_without_stock, daily_sales, search_position, dynamic_pct,
                   captured_at, raw_data
            FROM competitor_snapshots
            WHERE sku = $1 AND source = 'bestsellers'
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            sku_val,
        )

    def _pack_bestsellers(row):
        rd = row["raw_data"]
        if isinstance(rd, str):
            import json as _json
            try: rd = _json.loads(rd)
            except Exception: rd = {}
        if not isinstance(rd, dict):
            rd = {}
        def _fnum(*keys):
            for k in keys:
                v = rd.get(k)
                if v is None: continue
                try: return float(v)
                except (TypeError, ValueError):
                    try: return float(str(v).replace(",", ".").replace(" ", ""))
                    except ValueError: continue
            return None
        def _fint(*keys):
            n = _fnum(*keys)
            return int(n) if n is not None else None

        return {
            "source": "bestsellers",
            "sold_sum": float(row["sold_sum"]) if row["sold_sum"] is not None else None,
            "sold_units": row["sold_units"],
            "avg_price": float(row["avg_price"]) if row["avg_price"] is not None else None,
            "min_price": float(row["min_price"]) if row["min_price"] is not None else None,
            "session_count": row["session_count"],
            "conv_to_cart": float(row["conv_to_cart"]) if row["conv_to_cart"] is not None else None,
            "buyout_rate": float(row["buyout_rate"]) if row["buyout_rate"] is not None else None,
            "lost_sales": float(row["lost_sales"]) if row["lost_sales"] is not None else None,
            "days_without_stock": row["days_without_stock"],
            "daily_sales": float(row["daily_sales"]) if row["daily_sales"] is not None else None,
            "search_position": row["search_position"],
            "dynamic_pct": float(row["dynamic_pct"]) if row["dynamic_pct"] is not None else None,
            "captured_at": row["captured_at"].isoformat() if row["captured_at"] else None,
            # Дополнительные показатели из raw_data
            "views": _fint("views"),                                 # Показы всего
            "session_count_search": _fint("sessionCountSearch"),     # Показы в поиске и каталоге
            "qty_view_pdp": _fint("qtyViewPdp"),                     # Посещения карточки
            "conv_view_to_order": _fnum("convViewToOrder"),          # Конв. показ→заказ (доля)
            "conv_to_cart_search": _fnum("convToCartSearch"),        # Конв. поиск→корзина (доля)
            "conv_to_cart_pdp": _fnum("convToCartPdp", "pdpToCartConversion"),  # В корзину из карточки (%)
            "discount": _fnum("discount"),                           # Скидка от вашей цены, %
            "promo_revenue_share": _fnum("promoRevenueShare"),       # Доля оборота в акциях, %
            "days_in_promo": _fint("daysInPromo"),                   # Дней в акциях
            "days_with_trafarets": _fint("daysWithTrafarets"),       # Дней с продвижением
            "drr": _fnum("drr"),                                     # Общая ДРР, %
            "stock": _fint("stock"),                                 # Остаток на конец периода
            "volume_l": _fnum("volume"),                             # Объём, л
            "sales_schema": rd.get("salesSchema"),                   # Схема работы
            "create_date": rd.get("nullableCreateDate"),             # Дата создания карточки
            "avg_delivery_days": _fnum("avgDeliveryDays"),           # Средняя доставка, дн
        }

    # Сначала ищем по sku (для конкурента — это его sku; для своего — тоже sku, если задан)
    lookup_sku = sku
    if lookup_sku:
        async with pool.acquire() as conn:
            row = await _bestsellers_row(conn, lookup_sku)
        if row:
            out.update(_pack_bestsellers(row))
            return web.json_response(out)

    # Если sku не дал результата, но есть offer_id — ищем bestsellers по article=offer_id из raw_data
    if offer_id:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT sold_sum, sold_units, avg_price, min_price,
                       session_count, conv_to_cart, buyout_rate, lost_sales,
                       days_without_stock, daily_sales, search_position, dynamic_pct,
                       captured_at, raw_data
                FROM competitor_snapshots
                WHERE source = 'bestsellers'
                  AND raw_data->>'article' = $1
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                offer_id,
            )
            if row:
                out.update(_pack_bestsellers(row))
                return web.json_response(out)

            # Резолв offer_id → sku из products.raw_data (product_info_v3.sku)
            sku_row = await conn.fetchrow(
                """
                SELECT (raw_data::jsonb)->'product_info_v3'->>'sku' AS sku
                FROM products
                WHERE offer_id = $1
                LIMIT 1
                """,
                offer_id,
            )
            resolved_sku = sku_row["sku"] if sku_row and sku_row["sku"] else None
            if resolved_sku:
                row2 = await _bestsellers_row(conn, str(resolved_sku))
                if row2:
                    out.update(_pack_bestsellers(row2))
                    out["resolved_sku"] = resolved_sku
                    return web.json_response(out)
                # sku нашли, но snapshot-а нет — вернём, чтобы фронт мог авто-подтянуть
                out["resolved_sku"] = resolved_sku

    # 2. Fallback: для нашего offer_id — из fact_order_items за период
    if offer_id:
        from datetime import date, datetime, timedelta, timezone
        today_msk = datetime.now(MSK).date()
        date_from = today_msk - timedelta(days=days - 1)

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  coalesce(sum(foi.quantity), 0)::int AS units,
                  coalesce(sum(foi.buyer_paid * foi.quantity), 0)::float AS sum_buyer,
                  coalesce(sum(foi.price * foi.quantity), 0)::float AS sum_seller,
                  coalesce(min(foi.buyer_paid), 0)::float AS min_price,
                  coalesce(sum(foi.buyer_paid * foi.quantity) / nullif(sum(foi.quantity),0), 0)::float AS avg_price
                FROM fact_order_items foi
                JOIN fact_orders fo ON fo.order_id = foi.order_id
                WHERE foi.offer_id = $1
                  AND (fo.created_at AT TIME ZONE 'UTC')::date >= $2
                  AND coalesce(foi.quantity, 0) > 0
                """,
                offer_id, date_from,
            )
        if row and row["units"]:
            out.update({
                "source": "fact_orders",
                "sold_units": row["units"],
                "sold_sum": row["sum_buyer"],
                "avg_price": row["avg_price"],
                "min_price": row["min_price"] or None,
                "daily_sales": row["units"] / days,
            })
            return web.json_response(out)

    return web.json_response(out)


# ====================================================================
# /api/unitka/competitors/recent — список последних snapshots из БД
# ====================================================================

async def get_unitka_competitors_recent(request: web.Request) -> web.Response:
    """Последние сохранённые snapshots (calculator + bestsellers) для UI Юнитки."""
    try:
        limit = max(1, min(100, int(request.query.get("limit") or "30")))
    except ValueError:
        limit = 30
    source_filter = (request.query.get("source") or "").strip()

    pool: asyncpg.Pool = request.app["pool"]
    sql = """
        SELECT source, sku, name, brand, price_buyer, avg_price, min_price,
               weight_kg, length_cm, width_cm, height_cm, volume_l,
               fbo_commission_rate, fbs_commission_rate,
               sold_units, buyout_rate, photo_url, captured_at
        FROM competitor_snapshots
    """
    args = []
    if source_filter in ("calculator", "bestsellers"):
        sql += " WHERE source = $1"
        args.append(source_filter)
    sql += " ORDER BY captured_at DESC LIMIT " + str(limit)

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = []
    for r in rows:
        items.append({
            "source": r["source"],
            "sku": r["sku"],
            "name": r["name"] or "",
            "brand": r["brand"],
            "price": float(r["price_buyer"] or r["avg_price"] or 0),
            "avg_price": float(r["avg_price"]) if r["avg_price"] is not None else None,
            "min_price": float(r["min_price"]) if r["min_price"] is not None else None,
            "weight_kg": float(r["weight_kg"]) if r["weight_kg"] is not None else None,
            "length_cm": float(r["length_cm"]) if r["length_cm"] is not None else None,
            "width_cm": float(r["width_cm"]) if r["width_cm"] is not None else None,
            "height_cm": float(r["height_cm"]) if r["height_cm"] is not None else None,
            "volume_l": float(r["volume_l"]) if r["volume_l"] is not None else None,
            "fbo_commission_rate": float(r["fbo_commission_rate"]) if r["fbo_commission_rate"] is not None else None,
            "fbs_commission_rate": float(r["fbs_commission_rate"]) if r["fbs_commission_rate"] is not None else None,
            "sold_units": r["sold_units"],
            "buyout_rate": float(r["buyout_rate"]) if r["buyout_rate"] is not None else None,
            "photo_url": r["photo_url"],
            "captured_at": r["captured_at"].isoformat() if r["captured_at"] else None,
        })
    return web.json_response({"items": items, "count": len(items)})


# ====================================================================
# /api/unitka/import/* — endpoints для Chrome-расширения
# ====================================================================

def _parse_pct(val) -> Optional[float]:
    """Ozon возвращает % как 0..1 или 0..100 — нормализуем к [0..1]."""
    try:
        f = float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
    if f is None:
        return None
    if f > 1.5:
        f /= 100.0
    return round(f, 4)


def _extract_bestseller_row(item: Dict[str, Any]) -> Dict[str, Any]:
    """Извлекает поля из одной строки ответа what_to_sell/data/v3."""
    def _num(*keys):
        for k in keys:
            v = item.get(k)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                try:
                    return float(str(v).replace(",", ".").replace(" ", ""))
                except ValueError:
                    continue
        return None

    def _int(*keys):
        n = _num(*keys)
        return int(n) if n is not None else None

    def _str(*keys):
        for k in keys:
            v = item.get(k)
            if v:
                return str(v)
        return None

    # «Дней без остатка» = период (accessibilityByDays) - daysInStock.
    # daysInStock в API строкой ("0"), accessibilityByDays числом (28 для monthly).
    period_days = _int("accessibilityByDays")
    days_in_stock = _int("daysInStock")
    days_without_stock = None
    if period_days is not None and days_in_stock is not None:
        days_without_stock = max(0, period_days - days_in_stock)

    return {
        "sku": _str("sku", "variantId", "variant_id") or "",
        "name": _str("name", "skuName", "title"),
        "brand": _str("brand"),
        "category1": _str("category1", "category_level_1"),
        "category3": _str("category3", "category_level_3"),
        "sold_sum": _num("soldSum", "sold_sum", "gmvSum", "sumGmv", "sum_gmv"),
        "sold_units": _int("soldCount", "sold_count", "orderedUnits", "ordered_units"),
        "avg_price": _num("avgPrice", "avg_price", "avgGmv"),
        "min_price": _num("minSellerPrice", "minPrice", "min_price"),
        "session_count": _int("sessionCount", "session_count", "qtyViewPdp"),
        "conv_to_cart": _parse_pct(item.get("convToCart") or item.get("conv_to_cart")),
        # Ozon отдаёт redemption уже в процентах (86.6), _parse_pct нормализует к 0..1.
        "buyout_rate": _parse_pct(item.get("nullableRedemptionRate")
                                  or item.get("buyoutRate") or item.get("buyout_rate")),
        "lost_sales": _num("sumMissedGmv", "lostSales", "lost_sales"),
        "days_without_stock": days_without_stock if days_without_stock is not None
                              else _int("daysWithoutStock", "days_without_stock"),
        "daily_sales": _num("avgOrdersOnAccDays", "dailySales", "daily_sales", "avgDailySales"),
        "search_position": _int("localIndex", "searchPosition", "search_position", "position"),
        "dynamic_pct": _num("salesDynamics", "dynamic", "dynamicPct"),
        "photo_url": _str("photo", "photoUrl", "image"),
        "product_url": _str("link", "productUrl", "url"),
    }


async def post_unitka_import_bestsellers(request: web.Request) -> web.Response:
    """Приёмник данных из расширения: список товаров из what_to_sell/data/v3."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    items = body.get("items") or []
    period = body.get("period") or "monthly"
    if not isinstance(items, list):
        return web.json_response({"error": "items must be a list"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    inserted = 0
    async with pool.acquire() as conn:
        for item in items:
            if not isinstance(item, dict):
                continue
            extracted = _extract_bestseller_row(item)
            if not extracted["sku"]:
                continue
            import json as _json
            await conn.execute(
                """
                INSERT INTO competitor_snapshots
                  (source, sku, name, brand, category1, category3, period,
                   sold_sum, sold_units, avg_price, min_price, session_count,
                   conv_to_cart, buyout_rate, lost_sales, days_without_stock,
                   daily_sales, search_position, dynamic_pct,
                   photo_url, product_url, raw_data, captured_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7,
                        $8, $9, $10, $11, $12,
                        $13, $14, $15, $16,
                        $17, $18, $19,
                        $20, $21, $22::jsonb, now())
                """,
                "bestsellers", extracted["sku"], extracted["name"], extracted["brand"],
                extracted["category1"], extracted["category3"], period,
                extracted["sold_sum"], extracted["sold_units"], extracted["avg_price"],
                extracted["min_price"], extracted["session_count"],
                extracted["conv_to_cart"], extracted["buyout_rate"],
                extracted["lost_sales"], extracted["days_without_stock"],
                extracted["daily_sales"], extracted["search_position"],
                extracted["dynamic_pct"],
                extracted["photo_url"], extracted["product_url"],
                _json.dumps(item, ensure_ascii=False),
            )
            inserted += 1

    return web.json_response({"inserted": inserted, "period": period})


async def post_unitka_import_competitor(request: web.Request) -> web.Response:
    """Приёмник данных из расширения: один товар с calculator.ozon.ru."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    items_raw: List[Dict[str, Any]] = []
    if isinstance(body, dict):
        if isinstance(body.get("items"), list):
            items_raw = [x for x in body["items"] if isinstance(x, dict)]
        elif body.get("sku"):
            items_raw = [body]

    if not items_raw:
        return web.json_response({"error": "Нет товаров для сохранения"}, status=400)

    items = [_map_competitor_item(x) for x in items_raw]

    pool: asyncpg.Pool = request.app["pool"]
    import json as _json
    async with pool.acquire() as conn:
        for it, raw in zip(items, items_raw):
            if not it["sku"]:
                continue
            await conn.execute(
                """
                INSERT INTO competitor_snapshots
                  (source, sku, name, price_buyer, weight_kg,
                   length_cm, width_cm, height_cm, volume_l,
                   fbo_commission_rate, fbs_commission_rate,
                   photo_url, raw_data, captured_at)
                VALUES ('calculator', $1, $2, $3, $4,
                        $5, $6, $7, $8,
                        $9, $10,
                        $11, $12::jsonb, now())
                """,
                it["sku"], it["name"], it["price_buyer"], it["weight_kg"],
                it["dimensions"]["length"], it["dimensions"]["width"],
                it["dimensions"]["height"], it["volume_l"],
                it["fbo_commission_rate"], it["fbs_commission_rate"],
                it["thumbnail_url"],
                _json.dumps(raw, ensure_ascii=False),
            )

    return web.json_response({"items": items, "count": len(items), "saved": True})


# ====================================================================
# /api/unitka/competitor-lookup
# ====================================================================

def _map_competitor_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Нормализует ответ calculator.ozon.ru/p-api/.../item-search в плоскую структуру.

    Ozon отдаёт camelCase. Единицы по результатам реальных ответов:
      - dimensions.length/width/height — в СМ
      - weight — в КГ
      - price — цена для покупателя (₽)
      - fboCommissionRate / fbsCommissionRate — доля [0..1]
    """
    def g(*keys, default=None):
        for k in keys:
            if k in raw and raw[k] is not None:
                return raw[k]
        return default

    sku = str(g("sku", "id", default="") or "")
    name = str(g("name", "title", default="") or "")
    subtitle = str(g("subtitle", "brand", default="") or "")
    thumbnail = g("thumbnailUrl", "thumbnail_url", "image", "picture", default="") or ""
    price_buyer = float(g("price", "price_buyer", default=0) or 0)

    weight_raw = g("weight", default=None)
    try:
        weight_kg = float(weight_raw) if weight_raw is not None else None
    except (TypeError, ValueError):
        weight_kg = None

    dims = g("dimensions", default={}) or {}
    if not isinstance(dims, dict):
        dims = {}
    def _num(x):
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None

    length_cm = _num(dims.get("length"))
    width_cm = _num(dims.get("width"))
    height_cm = _num(dims.get("height"))
    volume_l = None
    if length_cm and width_cm and height_cm:
        volume_l = round(length_cm * width_cm * height_cm / 1000.0, 3)

    fbo_rate = float(g("fboCommissionRate", "fbo_commission_rate", default=0) or 0)
    fbs_rate = float(g("fbsCommissionRate", "fbs_commission_rate", default=0) or 0)
    if fbo_rate > 1.5:
        fbo_rate /= 100.0
    if fbs_rate > 1.5:
        fbs_rate /= 100.0

    price_seller_estimated = round(price_buyer / 0.6, 2) if price_buyer else 0.0

    return {
        "sku": sku,
        "name": name,
        "subtitle": subtitle,
        "thumbnail_url": thumbnail,
        "price_buyer": price_buyer,
        "price_seller_estimated": price_seller_estimated,
        "weight_kg": round(weight_kg, 3) if weight_kg is not None else None,
        "dimensions": {"length": length_cm, "width": width_cm, "height": height_cm},
        "volume_l": volume_l,
        "fbo_commission_rate": fbo_rate,
        "fbs_commission_rate": fbs_rate,
        "raw_keys": sorted(list(raw.keys())) if isinstance(raw, dict) else [],
    }


async def post_unitka_competitor_lookup(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    query = str(body.get("query") or "").strip()
    if not query:
        return web.json_response({"error": "query обязателен"}, status=400)

    # Calculator.ozon.ru закрыт антиботом для прямых HTTP-запросов,
    # поэтому используем Chrome-сессию (те же инструменты что и для supply):
    # браузер сам проходит JS-челлендж, мы делаем fetch из контекста страницы.
    try:
        from src.chrome_browser import calc_item_search
        raw = await calc_item_search(query)
    except Exception as e:
        logger.warning("competitor-lookup via chrome failed: %s", e)
        return web.json_response(
            {"error": f"Калькулятор Ozon недоступен: {e}"},
            status=502,
        )

    # Нормализация: API может вернуть list или {items: [...]} или {products: [...]}
    items_raw: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        items_raw = [x for x in raw if isinstance(x, dict)]
    elif isinstance(raw, dict):
        for key in ("items", "products", "results", "data"):
            val = raw.get(key)
            if isinstance(val, list):
                items_raw = [x for x in val if isinstance(x, dict)]
                break
        if not items_raw and "sku" in raw:
            items_raw = [raw]

    items = [_map_competitor_item(x) for x in items_raw]
    return web.json_response({"items": items, "count": len(items)})
