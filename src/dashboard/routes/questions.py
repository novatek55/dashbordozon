"""Questions report — spisok voprosov k tovaram + otvety cherez Ozon Seller API.

Trebuet podpiski Premium Plus. Bez svoej BD: tjanem napryamuyu cherez API.
Patern reuse iz reviews.py: _ozon_request helper + lookup product_name iz
report_products_items.
"""
import json
from typing import Any, Dict, List, Optional

import aiohttp
import asyncpg
from aiohttp import web

from src.dashboard.helpers import _get_ozon_credentials, load_sku_identity_map


async def _ozon_request(endpoint: str, body: Dict[str, Any]) -> tuple:
    """Vyzov endpoint'a Ozon Seller API. Vozvrashhaet (status, payload)."""
    client_id, api_key = _get_ozon_credentials()
    if not client_id or not api_key:
        return 401, {"message": "Ozon credentials not configured"}
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    url = f"https://api-seller.ozon.ru{endpoint}"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        async with sess.post(url, headers=headers, json=body) as resp:
            text = await resp.text()
            try:
                payload = json.loads(text) if text else {}
            except json.JSONDecodeError:
                payload = {"text": text[:1000]}
            return resp.status, payload


async def _lookup_products_by_sku(pool: asyncpg.Pool, skus: List[int]) -> Dict[int, Dict[str, Any]]:
    """sku → {offer_id, product_name} через единый резолвер каталога."""
    if not skus:
        return {}
    async with pool.acquire() as conn:
        identity_map = await load_sku_identity_map(conn, skus)
    return {
        int(sku): {"offer_id": data.get("offer_id"), "product_name": data.get("product_name")}
        for sku, data in identity_map.items()
    }


async def get_questions_service_status(request: web.Request) -> web.Response:
    """Proverit, podkljuchen li Premium Plus. /v1/question/count → 200/403."""
    status, payload = await _ozon_request("/v1/question/count", {})
    if status == 200:
        return web.json_response({"enabled": True, "counts": payload})
    if status == 403:
        return web.json_response({
            "enabled": False,
            "reason": "Premium Plus ne podkljuchen — endpoint /v1/question/* nedostupen",
        })
    return web.json_response({
        "enabled": False,
        "reason": payload.get("message") or f"HTTP {status}",
    })


async def get_questions_report(request: web.Request) -> web.Response:
    """Spisok voprosov + scyotchiki po statusam.

    Query params:
        status: ALL/NEW/VIEWED/PROCESSED/UNPROCESSED (default: ALL)
        date_from, date_to: ISO-format daty (opcional'no)
        limit: 1..100 (default: 100)
        last_id: pagination cursor
        sort_dir: ASC/DESC (default: DESC)
    """
    pool: asyncpg.Pool = request.app["pool"]

    status_filter = (request.query.get("status") or "ALL").upper()
    date_from = request.query.get("date_from")
    date_to = request.query.get("date_to")
    try:
        limit = max(1, min(100, int(request.query.get("limit") or 100)))
    except ValueError:
        limit = 100
    last_id = request.query.get("last_id") or ""
    sort_dir = (request.query.get("sort_dir") or "DESC").upper()
    if sort_dir not in ("ASC", "DESC"):
        sort_dir = "DESC"

    filter_obj: Dict[str, Any] = {"status": status_filter}
    if date_from:
        filter_obj["date_from"] = date_from
    if date_to:
        filter_obj["date_to"] = date_to

    body: Dict[str, Any] = {
        "filter": filter_obj,
        "limit": limit,
        "last_id": last_id,
        "sort_dir": sort_dir,
    }

    list_status, list_payload = await _ozon_request("/v1/question/list", body)
    if list_status != 200:
        return web.json_response(
            {
                "error": list_payload.get("message") or f"HTTP {list_status}",
                "code": list_status,
            },
            status=list_status if list_status >= 400 else 500,
        )

    questions = list_payload.get("questions") or []
    skus = list({int(q["sku"]) for q in questions if q.get("sku") is not None})
    product_map = await _lookup_products_by_sku(pool, skus)

    items: List[Dict[str, Any]] = []
    for q in questions:
        sku = int(q["sku"]) if q.get("sku") is not None else None
        product = product_map.get(sku, {}) if sku is not None else {}
        items.append({
            "id": q.get("id"),
            "sku": sku,
            "offer_id": product.get("offer_id"),
            "product_name": product.get("product_name"),
            "product_url": q.get("product_url"),
            "question_link": q.get("question_link"),
            "author_name": q.get("author_name"),
            "text": q.get("text"),
            "published_at": q.get("published_at"),
            "status": q.get("status"),
            "answers_count": int(q.get("answers_count") or 0),
        })

    # Schyotchiki po statusam — otdel'no, nezavisimo ot filtra.
    _, count_payload = await _ozon_request("/v1/question/count", {})

    return web.json_response({
        "items": items,
        "last_id": list_payload.get("last_id") or "",
        "has_next": bool(list_payload.get("has_next")),
        "counts": count_payload if isinstance(count_payload, dict) else {},
    })


async def get_question_answers(request: web.Request) -> web.Response:
    """Spisok otvetov na konkretnyj vopros. Trebuet sku v query."""
    question_id = request.match_info.get("question_id", "")
    sku_raw = request.query.get("sku", "")
    try:
        sku = int(sku_raw)
    except ValueError:
        return web.json_response({"error": "sku required"}, status=400)
    if not question_id:
        return web.json_response({"error": "question_id required"}, status=400)

    last_id = request.query.get("last_id") or ""
    body = {"question_id": question_id, "sku": sku, "last_id": last_id}

    status, payload = await _ozon_request("/v1/question/answer/list", body)
    if status != 200:
        return web.json_response(
            {"error": payload.get("message") or f"HTTP {status}"},
            status=status if status >= 400 else 500,
        )

    return web.json_response({
        "answers": payload.get("answers") or [],
        "last_id": payload.get("last_id") or "",
    })


async def post_question_answer(request: web.Request) -> web.Response:
    """Otvet na vopros + avto-perevod statusa v PROCESSED.

    Body: {question_id: str, sku: int, text: str, mark_as_processed: bool=true}
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    question_id = (data.get("question_id") or "").strip()
    sku = data.get("sku")
    text = (data.get("text") or "").strip()
    mark_as_processed = bool(data.get("mark_as_processed", True))

    if not question_id:
        return web.json_response({"error": "question_id required"}, status=400)
    if not sku:
        return web.json_response({"error": "sku required"}, status=400)
    if not text or len(text) < 2 or len(text) > 3000:
        return web.json_response(
            {"error": "text dolzhen byt' 2..3000 simvolov"}, status=400
        )

    try:
        sku_int = int(sku)
    except (TypeError, ValueError):
        return web.json_response({"error": "sku dolzhen byt' chislom"}, status=400)

    create_status, create_payload = await _ozon_request(
        "/v1/question/answer/create",
        {"question_id": question_id, "sku": sku_int, "text": text},
    )
    if create_status != 200:
        return web.json_response(
            {"error": create_payload.get("message") or f"HTTP {create_status}"},
            status=create_status if create_status >= 400 else 500,
        )

    answer_id = create_payload.get("answer_id")

    status_change_ok: Optional[bool] = None
    status_change_error: Optional[str] = None
    if mark_as_processed:
        st_code, st_payload = await _ozon_request(
            "/v1/question/change-status",
            {"question_ids": [question_id], "status": "PROCESSED"},
        )
        status_change_ok = st_code == 200
        if not status_change_ok:
            status_change_error = st_payload.get("message") or f"HTTP {st_code}"

    return web.json_response({
        "ok": True,
        "answer_id": answer_id,
        "status_changed": status_change_ok,
        "status_change_error": status_change_error,
    })


async def post_questions_change_status(request: web.Request) -> web.Response:
    """Smenit' status u odnogo ili neskol'kih voprosov.

    Body: {question_ids: [str], status: 'NEW'|'VIEWED'|'PROCESSED'}
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    question_ids = data.get("question_ids") or []
    new_status = (data.get("status") or "").upper()

    if not question_ids:
        return web.json_response({"error": "question_ids required"}, status=400)
    if new_status not in ("NEW", "VIEWED", "PROCESSED"):
        return web.json_response(
            {"error": "status dolzhen byt' NEW/VIEWED/PROCESSED"}, status=400
        )

    code, payload = await _ozon_request(
        "/v1/question/change-status",
        {"question_ids": [str(qid) for qid in question_ids], "status": new_status},
    )
    if code != 200:
        return web.json_response(
            {"error": payload.get("message") or f"HTTP {code}"},
            status=code if code >= 400 else 500,
        )

    return web.json_response({"ok": True, "status": new_status, "count": len(question_ids)})
