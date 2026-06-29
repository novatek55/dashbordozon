"""SERP-роуты: сбор и хранение выдачи ozon.ru по поисковому запросу."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, Optional

import asyncpg
from aiohttp import web

from src.dashboard.bestsellers import normalize_bestsellers_percent
from src.services.serp_service import (
    save_snapshot,
    get_latest_snapshot,
    build_serp_report_rows,
    mark_competitor,
    get_competitors,
    get_primary_query,
    set_primary_query,
    recalculate_primary_queries,
    get_top_queries_for_sku,
    get_article_serp_report,
)

logger = logging.getLogger(__name__)

# Таймаут ожидания ответа от плагина
PLUGIN_TIMEOUT = 300.0
SERP_SNAPSHOT_LIMIT = 30
SERP_REPORT_TOP_N = 30


class _PluginError(Exception):
    """Ошибка плагина — возвращается как JSON {error: ...}."""


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _to_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _json_dumps(obj) -> str:
    import decimal, datetime

    def _default(o):
        if isinstance(o, decimal.Decimal):
            return str(o)
        if isinstance(o, (datetime.datetime, datetime.date)):
            return o.isoformat()
        raise TypeError(f"Not serializable: {type(o)}")

    return json.dumps(obj, default=_default)


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", str(value or "").lower(), flags=re.U)).strip()


def _map_bestseller_item(item: Dict[str, Any]) -> Dict[str, Any]:
    def to_num(value):
        if value in (None, "", False):
            return None
        try:
            return float(str(value).replace(" ", "").replace(",", "."))
        except Exception:
            return None

    def to_int(value):
        n = to_num(value)
        return int(round(n)) if n is not None else None

    sold_sum = item.get("soldSum", item.get("sold_sum", item.get("gmvSum")))
    daily_sales = item.get("avgOrdersOnAccDays", item.get("dailySales", item.get("daily_sales")))
    normalized = {
        "sku": str(item.get("sku") or item.get("id") or item.get("item_id") or ""),
        "name": item.get("name") or item.get("skuName") or item.get("title"),
        "brand": item.get("brand"),
        "category1": item.get("category1") or item.get("category_level_1"),
        "category3": item.get("category3") or item.get("category_level_3"),
        "sold_sum": to_num(sold_sum),
        "sold_units": to_int(item.get("soldCount", item.get("sold_count", item.get("orderedUnits")))),
        "avg_price": to_num(item.get("avgPrice", item.get("avg_price", item.get("avgGmv")))),
        "min_price": to_num(item.get("minSellerPrice", item.get("minPrice", item.get("min_price")))),
        "session_count": to_int(item.get("sessionCount", item.get("session_count", item.get("qtyViewPdp")))),
        "views": to_int(item.get("views", item.get("viewCount", item.get("qtyViewAll")))),
        "session_count_search": to_int(item.get("sessionCountSearch", item.get("session_count_search"))),
        "qty_view_pdp": to_int(item.get("qtyViewPdp", item.get("qty_view_pdp"))),
        "conv_to_cart": normalize_bestsellers_percent(item.get("convToCart", item.get("conv_to_cart"))),
        "conv_to_cart_search": normalize_bestsellers_percent(item.get("convToCartSearch", item.get("conv_to_cart_search"))),
        "conv_to_cart_pdp": normalize_bestsellers_percent(item.get("convToCartPdp", item.get("conv_to_cart_pdp"))),
        "conv_view_to_order": normalize_bestsellers_percent(item.get("convViewToOrder", item.get("conv_view_to_order"))),
        "buyout_rate": normalize_bestsellers_percent(item.get("nullableRedemptionRate", item.get("buyoutRate", item.get("buyout_rate")))),
        "lost_sales": to_num(item.get("sumMissedGmv", item.get("lostSales", item.get("lost_sales")))),
        "days_without_stock": to_int(item.get("daysWithoutStock", item.get("days_without_stock"))),
        "daily_sales": to_num(daily_sales),
        "search_position": to_int(item.get("localIndex", item.get("searchPosition", item.get("search_position")))),
        "dynamic_pct": to_num(item.get("salesDynamics", item.get("dynamic", item.get("dynamicPct")))),
        "photo_url": item.get("photo") or item.get("photoUrl") or item.get("image"),
        "product_url": item.get("link") or item.get("productUrl") or item.get("url"),
        "stock_end": to_int(item.get("stock", item.get("stockOnEnd", item.get("balance")))),
        "promo_revenue_share": to_num(item.get("promoRevenueShare", item.get("promo_revenue_share"))),
        "days_in_promo": to_int(item.get("daysInPromo", item.get("days_in_promo"))),
        "days_with_trafarets": to_int(item.get("daysWithTrafarets", item.get("days_with_trafarets"))),
        "drr": to_num(item.get("drr", item.get("DRR"))),
        "avg_delivery_days": to_num(item.get("avgDeliveryDays", item.get("avg_delivery_days"))),
        "volume_l": to_num(item.get("volumeL", item.get("volume_l", item.get("volume")))),
        "raw": item,
    }
    return {
        "revenue_30d": to_num(sold_sum),
        "sales_per_day": to_num(daily_sales),
        "bestsellers_data": normalized,
    }


def _pick_bestseller_item_py(items: list[Dict[str, Any]], sku: str, title: str) -> Optional[Dict[str, Any]]:
    target_sku = str(sku or "").strip()
    for item in items or []:
        raw_sku = str(item.get("sku") or item.get("id") or item.get("item_id") or "").strip()
        if raw_sku and raw_sku == target_sku:
            return item
    title_norm = _normalize_text(title)
    if title_norm:
        for item in items or []:
            item_name = _normalize_text(item.get("name") or item.get("skuName") or item.get("title"))
            if item_name and (item_name in title_norm or title_norm in item_name):
                return item
    return items[0] if len(items or []) == 1 else None


async def _enrich_via_fetch_bestsellers(
    request: web.Request,
    competitor_targets: list[Dict[str, Any]],
) -> Dict[str, Any]:
    matches: Dict[str, Any] = {}
    stats: Dict[str, Any] = {
        "requested_count": len(competitor_targets),
        "mass_pages_scanned": 0,
        "mass_matches": 0,
        "fallback_attempts": 0,
        "fallback_matches": 0,
        "unresolved_skus": [],
        "seller_open_requested": False,
        "seller_open_confirmed": False,
        "seller_final_url": "",
        "input_ready_confirmed": False,
        "period_28_confirmed": False,
        "category_reset_confirmed": False,
        "category_reset_clicked": False,
        "last_error": "",
        "debug_samples": [],
    }
    seller_flags_set = False

    for entry in competitor_targets:
        sku = str(entry.get("sku") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not sku:
            continue
        search_variants = [sku]
        if title and title not in search_variants:
            search_variants.append(title)
        matched = None
        for search_key in search_variants:
            stats["fallback_attempts"] += 1
            resp = await _call_plugin(
                request,
                "fetch_bestsellers",
                {"options": {"period": "monthly", "limit": 10, "search": search_key, "autoOpen": True}},
            )
            debug = resp.get("debug", {}) if isinstance(resp, dict) else {}
            if not seller_flags_set:
                stats["seller_open_requested"] = True
                stats["seller_open_confirmed"] = bool(debug.get("ready_confirmed")) or bool(debug.get("tab_found_before_open"))
                stats["seller_final_url"] = debug.get("final_url", "")
                stats["input_ready_confirmed"] = bool(debug.get("input_ready_confirmed"))
                stats["period_28_confirmed"] = bool(debug.get("period_28_confirmed"))
                stats["category_reset_confirmed"] = bool(debug.get("category_reset_confirmed"))
                stats["category_reset_clicked"] = bool(debug.get("category_reset_clicked"))
                seller_flags_set = True
            items = resp.get("items", []) if isinstance(resp, dict) else []
            if len(stats["debug_samples"]) < 12:
                stats["debug_samples"].append({
                    "phase": "fetch_by_sku",
                    "sku": sku,
                    "searchKey": search_key[:120],
                    "items_count": len(items),
                })
            matched = _pick_bestseller_item_py(items, sku, title)
            if matched:
                matches[sku] = _map_bestseller_item(matched)
                stats["fallback_matches"] += 1
                break
        if not matched:
            stats["unresolved_skus"].append(sku)

    return {"matches": matches, "stats": stats}


def _apply_serp_enrichment(
    positions: list[dict[str, Any]],
    enrichment_map: Dict[str, Any],
    fallback_map: Optional[Dict[str, Any]] = None,
) -> int:
    """Merge plugin enrichment with optional DB fallback into snapshot positions."""
    merged_map = dict(fallback_map or {})
    merged_map.update(enrichment_map or {})

    enriched_count = 0
    for pos in positions:
        extra = merged_map.get(str(pos.get("sku")), {})
        pos["revenue_30d"] = extra.get("revenue_30d")
        pos["sales_per_day"] = extra.get("sales_per_day")
        pos["bestsellers_data"] = extra.get("bestsellers_data")
        if extra.get("bestsellers_data"):
            enriched_count += 1
    return enriched_count


async def _load_bestsellers_fallback_map(
    pool: asyncpg.Pool,
    skus: list[int],
) -> Dict[str, Dict[str, Any]]:
    """Load latest local bestsellers snapshots for unresolved SKUs."""
    normalized_skus = [int(sku) for sku in skus if sku]
    if not normalized_skus:
        return {}

    query = """
        WITH ranked AS (
            SELECT DISTINCT ON (sku)
                   sku, sold_sum, daily_sales, raw_data
            FROM competitor_snapshots
            WHERE source = 'bestsellers'
              AND sku ~ '^[0-9]+$'
              AND sku::bigint = ANY($1::bigint[])
            ORDER BY sku, captured_at DESC
        )
        SELECT sku, sold_sum, daily_sales, raw_data
        FROM ranked
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, normalized_skus)

    fallback_map: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        raw_data = row["raw_data"]
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except Exception:
                raw_data = {}
        if not isinstance(raw_data, dict):
            raw_data = {}
        fallback_map[str(row["sku"])] = {
            "revenue_30d": float(row["sold_sum"]) if row["sold_sum"] is not None else None,
            "sales_per_day": float(row["daily_sales"]) if row["daily_sales"] is not None else None,
            "bestsellers_data": (_normalize_bestsellers_item(raw_data).get("bestsellers_data") if raw_data else None),
        }
    return fallback_map


# ────────────────────────────────────────────────────────────────────────────
# Plugin bridge
# ────────────────────────────────────────────────────────────────────────────

async def _call_plugin(request: web.Request, action: str, payload: Dict[str, Any]) -> Dict:
    """
    Отправляет задачу в очередь плагина и ждёт ответа.
    JS-страница дашборда поллит /api/plugin/poll, выполняет вызов
    и возвращает результат на /api/plugin/result.
    """
    app = request.app
    pending: dict = app.setdefault("plugin_pending", {})
    queue: asyncio.Queue = app.setdefault("plugin_queue", asyncio.Queue())

    request_id = str(uuid.uuid4())
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    pending[request_id] = fut

    await queue.put({"requestId": request_id, "action": action, "payload": payload})

    try:
        result = await asyncio.wait_for(fut, timeout=PLUGIN_TIMEOUT)
    except asyncio.TimeoutError:
        pending.pop(request_id, None)
        raise _PluginError(
            f"Timeout: расширение не ответило за {int(PLUGIN_TIMEOUT)} сек. "
            "Убедитесь что дашборд открыт в Chrome с установленным плагином «Ozon Unitka Helper»."
        )
    finally:
        pending.pop(request_id, None)

    if not result.get("ok"):
        raise _PluginError(result.get("error", "Plugin error"))

    return result.get("data", {})


async def plugin_poll(request: web.Request) -> web.Response:
    """GET /api/plugin/poll — JS дашборда забирает задачи для плагина."""
    queue: asyncio.Queue = request.app.setdefault("plugin_queue", asyncio.Queue())
    try:
        task = await asyncio.wait_for(queue.get(), timeout=20.0)
        return web.json_response(task)
    except asyncio.TimeoutError:
        return web.json_response({"requestId": None})


async def plugin_result(request: web.Request) -> web.Response:
    """POST /api/plugin/result — JS дашборда возвращает результат вызова плагина."""
    body = await request.json()
    request_id = body.get("requestId")
    pending: dict = request.app.get("plugin_pending", {})
    fut = pending.get(request_id)
    if fut and not fut.done():
        fut.set_result(body.get("response", {}))
    return web.json_response({"ok": True})


# ────────────────────────────────────────────────────────────────────────────
# SERP endpoints
# ────────────────────────────────────────────────────────────────────────────

async def post_serp_scrape(request: web.Request) -> web.Response:
    """POST /api/serp/scrape — запустить скрейп выдачи по запросу."""
    body = await request.json()
    query_text = (body.get("query_text") or "").strip()
    if not query_text:
        return web.json_response({"error": "query_text required"}, status=400)

    limit = max(SERP_REPORT_TOP_N, int(body.get("limit", SERP_SNAPSHOT_LIMIT)))
    pool: asyncpg.Pool = request.app["pool"]

    try:
        # 1. Скрейп через плагин
        plugin_data = await _call_plugin(
            request, "scrape_serp", {"options": {"query_text": query_text, "limit": limit}}
        )
    except _PluginError as e:
        return web.json_response({"error": str(e)}, status=502)
    positions = plugin_data.get("positions", [])
    scrape_debug = plugin_data.get("debug") or {}

    # 2. Обогащение конкурентов данными bestsellers
    competitor_targets = [
        {"sku": p["sku"], "title": p.get("title") or ""}
        for p in positions if p.get("sku")
    ]
    enriched_count = 0
    enrichment_stats: Dict[str, Any] = {}
    if competitor_targets:
        try:
            enriched = await _enrich_via_fetch_bestsellers(request, competitor_targets)
            enrichment_map = enriched.get("matches", {}) if isinstance(enriched, dict) else {}
            enrichment_stats = enriched.get("stats", {}) if isinstance(enriched, dict) else {}
            unresolved_skus = [
                int(p["sku"])
                for p in positions
                if p.get("sku") and not enrichment_map.get(str(p.get("sku")), {}).get("bestsellers_data")
            ]
            fallback_map = await _load_bestsellers_fallback_map(pool, unresolved_skus)
            enriched_count = _apply_serp_enrichment(positions, enrichment_map, fallback_map)
        except _PluginError as e:
            return web.json_response({"error": f"Не удалось открыть/прочитать Ozon bestsellers: {e}"}, status=502)
        except Exception as e:
            logger.exception("Bestsellers enrichment failed")
            return web.json_response({"error": f"Не удалось загрузить статистику по карточкам: {e}"}, status=502)

    # 3. Сохраняем в БД
    snapshot_id = await save_snapshot(
        pool, query_text, positions, raw_data={"source": "plugin"}
    )

    stage_results = {
        "query": {"ok": bool(query_text), "query_text": query_text},
        "ozon": {
            "ok": bool(scrape_debug.get("url_open_confirmed")) and bool(scrape_debug.get("cards_ready_confirmed")),
            "pages": len(scrape_debug.get("pages") or []),
            "tab_opened": bool(scrape_debug.get("tab_opened")),
            "url_open_confirmed": bool(scrape_debug.get("url_open_confirmed")),
            "cards_ready_confirmed": bool(scrape_debug.get("cards_ready_confirmed")),
            "initial_url": scrape_debug.get("initial_url"),
        },
        "cards": {"ok": bool(positions), "position_count": len(positions)},
        "metrics": {
            "ok": bool(enrichment_stats.get("seller_open_confirmed")) and bool(
                enrichment_stats.get("mass_pages_scanned") or enrichment_stats.get("fallback_attempts") or enriched_count
            ),
            "requested_count": len(competitor_targets),
            "enriched_count": enriched_count,
            "seller_open_requested": bool(enrichment_stats.get("seller_open_requested")),
            "seller_open_confirmed": bool(enrichment_stats.get("seller_open_confirmed")),
            "seller_final_url": enrichment_stats.get("seller_final_url"),
            "input_ready_confirmed": bool(enrichment_stats.get("input_ready_confirmed")),
            "period_28_confirmed": bool(enrichment_stats.get("period_28_confirmed")),
            "category_reset_confirmed": bool(enrichment_stats.get("category_reset_confirmed")),
            "category_reset_clicked": bool(enrichment_stats.get("category_reset_clicked")),
            "mass_pages_scanned": enrichment_stats.get("mass_pages_scanned", 0),
            "mass_matches": enrichment_stats.get("mass_matches", 0),
            "fallback_attempts": enrichment_stats.get("fallback_attempts", 0),
            "fallback_matches": enrichment_stats.get("fallback_matches", 0),
            "unresolved_skus": enrichment_stats.get("unresolved_skus", []),
        },
        "snapshot": {"ok": bool(snapshot_id), "snapshot_id": snapshot_id},
    }

    return web.json_response({
        "snapshot_id": snapshot_id,
        "position_count": len(positions),
        "report_top_n": SERP_REPORT_TOP_N,
        "metrics_requested_count": len(competitor_targets),
        "metrics_enriched_count": enriched_count,
        "stage_results": stage_results,
    })


async def post_serp_scrape_by_sku(request: web.Request) -> web.Response:
    """POST /api/serp/scrape-by-sku — скрейп по главному запросу артикула."""
    body = await request.json()
    sku = _to_int(body.get("sku"))
    if not sku:
        return web.json_response({"error": "sku required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    primary = await get_primary_query(pool, sku)
    if not primary:
        return web.json_response(
            {"error": "Главный запрос не задан для этого SKU"}, status=404
        )

    # Синтетически вызываем post_serp_scrape с query_text главного запроса
    # Делаем это через прямой вызов логики, без HTTP-редиректа
    query_text = primary["query_text"]
    limit = max(SERP_REPORT_TOP_N, int(body.get("limit", SERP_SNAPSHOT_LIMIT)))

    try:
        plugin_data = await _call_plugin(
            request, "scrape_serp", {"options": {"query_text": query_text, "limit": limit}}
        )
    except _PluginError as e:
        return web.json_response({"error": str(e)}, status=502)
    positions = plugin_data.get("positions", [])
    scrape_debug = plugin_data.get("debug") or {}

    competitor_targets = [
        {"sku": p["sku"], "title": p.get("title") or ""}
        for p in positions if p.get("sku")
    ]
    enriched_count = 0
    enrichment_stats: Dict[str, Any] = {}
    if competitor_targets:
        try:
            enriched = await _enrich_via_fetch_bestsellers(request, competitor_targets)
            enrichment_map = enriched.get("matches", {}) if isinstance(enriched, dict) else {}
            enrichment_stats = enriched.get("stats", {}) if isinstance(enriched, dict) else {}
            unresolved_skus = [
                int(p["sku"])
                for p in positions
                if p.get("sku") and not enrichment_map.get(str(p.get("sku")), {}).get("bestsellers_data")
            ]
            fallback_map = await _load_bestsellers_fallback_map(pool, unresolved_skus)
            enriched_count = _apply_serp_enrichment(positions, enrichment_map, fallback_map)
        except _PluginError as e:
            return web.json_response({"error": f"Не удалось открыть/прочитать Ozon bestsellers: {e}"}, status=502)
        except Exception as e:
            logger.exception("Bestsellers enrichment failed")
            return web.json_response({"error": f"Не удалось загрузить статистику по карточкам: {e}"}, status=502)

    snapshot_id = await save_snapshot(
        pool, query_text, positions, raw_data={"source": "plugin", "triggered_by_sku": sku}
    )

    stage_results = {
        "query": {"ok": bool(query_text), "query_text": query_text, "sku": sku},
        "ozon": {
            "ok": bool(scrape_debug.get("url_open_confirmed")) and bool(scrape_debug.get("cards_ready_confirmed")),
            "pages": len(scrape_debug.get("pages") or []),
            "tab_opened": bool(scrape_debug.get("tab_opened")),
            "url_open_confirmed": bool(scrape_debug.get("url_open_confirmed")),
            "cards_ready_confirmed": bool(scrape_debug.get("cards_ready_confirmed")),
            "initial_url": scrape_debug.get("initial_url"),
        },
        "cards": {"ok": bool(positions), "position_count": len(positions)},
        "metrics": {
            "ok": bool(enrichment_stats.get("seller_open_confirmed")) and bool(
                enrichment_stats.get("mass_pages_scanned") or enrichment_stats.get("fallback_attempts") or enriched_count
            ),
            "requested_count": len(competitor_targets),
            "enriched_count": enriched_count,
            "seller_open_requested": bool(enrichment_stats.get("seller_open_requested")),
            "seller_open_confirmed": bool(enrichment_stats.get("seller_open_confirmed")),
            "seller_final_url": enrichment_stats.get("seller_final_url"),
            "input_ready_confirmed": bool(enrichment_stats.get("input_ready_confirmed")),
            "period_28_confirmed": bool(enrichment_stats.get("period_28_confirmed")),
            "category_reset_confirmed": bool(enrichment_stats.get("category_reset_confirmed")),
            "category_reset_clicked": bool(enrichment_stats.get("category_reset_clicked")),
            "mass_pages_scanned": enrichment_stats.get("mass_pages_scanned", 0),
            "mass_matches": enrichment_stats.get("mass_matches", 0),
            "fallback_attempts": enrichment_stats.get("fallback_attempts", 0),
            "fallback_matches": enrichment_stats.get("fallback_matches", 0),
            "unresolved_skus": enrichment_stats.get("unresolved_skus", []),
        },
        "snapshot": {"ok": bool(snapshot_id), "snapshot_id": snapshot_id},
    }

    return web.json_response({
        "snapshot_id": snapshot_id,
        "position_count": len(positions),
        "report_top_n": SERP_REPORT_TOP_N,
        "metrics_requested_count": len(competitor_targets),
        "metrics_enriched_count": enriched_count,
        "stage_results": stage_results,
    })


async def get_serp_snapshot(request: web.Request) -> web.Response:
    """GET /api/serp/snapshot?query=... — последний снимок выдачи."""
    query_text = (request.query.get("query") or "").strip()
    if not query_text:
        return web.json_response({"error": "query required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    snapshot = await get_latest_snapshot(pool, query_text)
    if snapshot:
        our_sku = _to_int(request.query.get("our_sku"))
        snapshot["report_positions"] = build_serp_report_rows(
            snapshot.get("positions", []),
            our_sku=our_sku,
            top_n=SERP_REPORT_TOP_N,
        )
    return web.Response(
        text=_json_dumps({"snapshot": snapshot}),
        content_type="application/json",
    )


async def post_serp_competitor(request: web.Request) -> web.Response:
    """POST /api/serp/competitor — пометить/снять метку конкурента."""
    body = await request.json()
    sku = _to_int(body.get("sku"))
    if not sku:
        return web.json_response({"error": "sku required"}, status=400)

    is_competitor = bool(body.get("is_competitor", True))
    note = (body.get("note") or "").strip()
    pool: asyncpg.Pool = request.app["pool"]
    await mark_competitor(pool, sku, is_competitor, note)
    return web.json_response({"ok": True})


async def get_serp_competitors(request: web.Request) -> web.Response:
    """GET /api/serp/competitors — список конкурентов."""
    pool: asyncpg.Pool = request.app["pool"]
    items = await get_competitors(pool)
    return web.json_response({"competitors": items})


async def get_serp_primary_query(request: web.Request) -> web.Response:
    """GET /api/serp/primary-query?sku=... — главный запрос + топ запросов для dropdown."""
    sku = _to_int(request.query.get("sku"))
    if not sku:
        return web.json_response({"error": "sku required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    primary = await get_primary_query(pool, sku)
    top_queries = await get_top_queries_for_sku(pool, sku)

    return web.json_response({
        "primary": primary,
        "top_queries": top_queries,
    })


async def put_serp_primary_query(request: web.Request) -> web.Response:
    """PUT /api/serp/primary-query — установить главный запрос вручную."""
    body = await request.json()
    sku = _to_int(body.get("sku"))
    query_text = (body.get("query_text") or "").strip()
    if not sku or not query_text:
        return web.json_response({"error": "sku and query_text required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    await set_primary_query(pool, sku, query_text, manual=True)
    return web.json_response({"ok": True})


async def get_serp_article_report(request: web.Request) -> web.Response:
    """GET /api/serp/article-report?sku=... — снапшот для секции «Поиск» в артикуле."""
    sku = _to_int(request.query.get("sku"))
    if not sku:
        return web.json_response({"error": "sku required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    report = await get_article_serp_report(pool, sku)
    return web.Response(
        text=_json_dumps(report),
        content_type="application/json",
    )


async def post_serp_recalculate_primary(request: web.Request) -> web.Response:
    """POST /api/serp/recalculate-primary — пересчитать главные запросы авто-правилом."""
    pool: asyncpg.Pool = request.app["pool"]
    count = await recalculate_primary_queries(pool)
    return web.json_response({"updated": count})


async def get_serp_all_primary_queries(request: web.Request) -> web.Response:
    """GET /api/serp/all-primary-queries — все артикулы с их главными запросами."""
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.sku, s.offer_id, s.query_text, s.set_manually
            FROM sku_primary_query s
            ORDER BY s.updated_at DESC
            LIMIT 200
            """
        )
    return web.json_response({
        "items": [
            {
                "sku": r["sku"],
                "offer_id": r["offer_id"],
                "query_text": r["query_text"],
                "set_manually": r["set_manually"],
            }
            for r in rows
        ]
    })


async def post_serp_save_from_overlay(request: web.Request) -> web.Response:
    """POST /api/serp/save-from-overlay — сохранить данные собранные overlay."""
    body = await request.json()
    items = body.get("items", [])
    query_text = (body.get("query_text") or "неизвестный запрос").strip()[:500]
    if not items:
        return web.json_response({"error": "items required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    snapshot_id = await save_snapshot(pool, query_text, items, raw_data={"source": "overlay"})
    return web.json_response({"ok": True, "snapshot_id": snapshot_id, "count": len(items)})
