"""Chats report: spisok chatov + istorija + otpravka soobshhenij cherez Ozon API."""
import json
from typing import Any, Dict, List

import aiohttp
from aiohttp import web

from src.dashboard.helpers import _get_ozon_credentials


def _extract_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


async def _ozon_request(endpoint: str, body: Dict[str, Any]) -> tuple:
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


async def get_chats_service_status(request: web.Request) -> web.Response:
    status, payload = await _ozon_request("/v1/chat/list", {"limit": 1, "offset": 0})
    if status == 200:
        return web.json_response({"enabled": True})
    if status == 403:
        return web.json_response({"enabled": False, "reason": "Chat API nedostupen (403)"})
    return web.json_response({
        "enabled": False,
        "reason": payload.get("message") or f"HTTP {status}",
    })


async def get_chats_report(request: web.Request) -> web.Response:
    chat_status = (request.query.get("chat_status") or "").strip()
    try:
        limit = max(1, min(100, int(request.query.get("limit") or 100)))
    except ValueError:
        limit = 100
    try:
        offset = max(0, int(request.query.get("offset") or 0))
    except ValueError:
        offset = 0

    body: Dict[str, Any] = {"limit": limit, "offset": offset}
    if chat_status and chat_status.upper() != "ALL":
        body["chat_status"] = chat_status

    code, payload = await _ozon_request("/v1/chat/list", body)
    if code != 200:
        return web.json_response(
            {"error": payload.get("message") or f"HTTP {code}"},
            status=code if code >= 400 else 500,
        )

    result_payload = _extract_result(payload)
    chats = payload.get("chats") or result_payload.get("chats") or []
    items: List[Dict[str, Any]] = []
    for c in chats:
        last_msg = c.get("last_message") if isinstance(c.get("last_message"), dict) else {}
        items.append({
            "chat_id": c.get("chat_id") or c.get("id"),
            "chat_status": c.get("chat_status") or c.get("status"),
            "unread_count": int(c.get("unread_count") or 0),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "last_message_at": c.get("last_message_created_at") or last_msg.get("created_at"),
            "last_message_text": c.get("last_message_text") or last_msg.get("text") or "",
            "raw": c,
        })

    return web.json_response({
        "items": items,
        "offset": offset,
        "limit": limit,
        "has_next": len(items) >= limit,
    })


async def get_chat_history(request: web.Request) -> web.Response:
    chat_id = request.match_info.get("chat_id", "")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
    try:
        limit = max(1, min(100, int(request.query.get("limit") or 100)))
    except ValueError:
        limit = 100

    code, payload = await _ozon_request("/v1/chat/history", {"chat_id": chat_id, "limit": limit})
    if code != 200:
        return web.json_response(
            {"error": payload.get("message") or f"HTTP {code}"},
            status=code if code >= 400 else 500,
        )
    result_payload = _extract_result(payload)
    messages = payload.get("messages") or result_payload.get("messages") or []
    return web.json_response({"messages": messages})


async def post_chat_send_message(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    chat_id = str(data.get("chat_id") or "").strip()
    text = str(data.get("text") or "").strip()
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
    if len(text) < 1 or len(text) > 3000:
        return web.json_response({"error": "text dolzhen byt' 1..3000 simvolov"}, status=400)

    body = {"chat_id": chat_id, "text": text, "message": text}
    code, payload = await _ozon_request("/v1/chat/send/message", body)
    if code != 200:
        return web.json_response(
            {"error": payload.get("message") or f"HTTP {code}"},
            status=code if code >= 400 else 500,
        )
    return web.json_response({"ok": True, "result": payload})
