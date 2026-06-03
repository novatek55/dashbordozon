"""SERP-роуты: сбор и хранение выдачи ozon.ru по поисковому запросу."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

import asyncpg
from aiohttp import web

from src.services.serp_service import (
    save_snapshot,
    get_latest_snapshot,
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
PLUGIN_TIMEOUT = 60.0


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
        raise web.HTTPGatewayTimeout(reason="Plugin timeout — расширение не ответило")
    finally:
        pending.pop(request_id, None)

    if not result.get("ok"):
        raise web.HTTPBadGateway(reason=result.get("error", "Plugin error"))

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

    limit = int(body.get("limit", 20))
    pool: asyncpg.Pool = request.app["pool"]

    # 1. Скрейп через плагин
    plugin_data = await _call_plugin(
        request, "scrape_serp", {"query_text": query_text, "limit": limit}
    )
    positions = plugin_data.get("positions", [])

    # 2. Обогащение конкурентов данными bestsellers
    competitor_skus = [p["sku"] for p in positions if p.get("sku")]
    if competitor_skus:
        try:
            enriched = await _call_plugin(
                request, "enrich_with_bestsellers", {"skus": competitor_skus}
            )
            for p in positions:
                extra = enriched.get(str(p.get("sku")), {})
                p["revenue_30d"] = extra.get("revenue_30d")
                p["sales_per_day"] = extra.get("sales_per_day")
        except Exception as e:
            logger.warning("Bestsellers enrichment failed: %s", e)

    # 3. Сохраняем в БД
    snapshot_id = await save_snapshot(
        pool, query_text, positions, raw_data={"source": "plugin"}
    )

    return web.json_response({"snapshot_id": snapshot_id, "position_count": len(positions)})


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
    limit = 20

    plugin_data = await _call_plugin(
        request, "scrape_serp", {"query_text": query_text, "limit": limit}
    )
    positions = plugin_data.get("positions", [])

    competitor_skus = [p["sku"] for p in positions if p.get("sku")]
    if competitor_skus:
        try:
            enriched = await _call_plugin(
                request, "enrich_with_bestsellers", {"skus": competitor_skus}
            )
            for p in positions:
                extra = enriched.get(str(p.get("sku")), {})
                p["revenue_30d"] = extra.get("revenue_30d")
                p["sales_per_day"] = extra.get("sales_per_day")
        except Exception as e:
            logger.warning("Bestsellers enrichment failed: %s", e)

    snapshot_id = await save_snapshot(
        pool, query_text, positions, raw_data={"source": "plugin", "triggered_by_sku": sku}
    )

    return web.json_response({"snapshot_id": snapshot_id, "position_count": len(positions)})


async def get_serp_snapshot(request: web.Request) -> web.Response:
    """GET /api/serp/snapshot?query=... — последний снимок выдачи."""
    query_text = (request.query.get("query") or "").strip()
    if not query_text:
        return web.json_response({"error": "query required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    snapshot = await get_latest_snapshot(pool, query_text)
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
