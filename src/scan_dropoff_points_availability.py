from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.supply_warehouse_scanner import CdpRelayClient, build_headers


DEFAULT_RELAY_HTTP = "http://127.0.0.1:19000"
DEFAULT_RELAY_TOKEN = "codex-browser-relay-dev-token"
BFF_BASE_URL = "https://seller.ozon.ru/api/supplier-drafts/bff"


def msk_fmt(iso_value: str | None) -> str:
    if not iso_value:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        dt = dt.astimezone(timezone(timedelta(hours=3)))
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso_value


async def get_context_from_page(relay_http: str, relay_token: str) -> dict[str, Any]:
    async with CdpRelayClient(relay_http, relay_token) as cdp:
        tabs = await cdp.list_tabs()
        seller = next((t for t in tabs if "seller.ozon.ru" in str(t.get("url", ""))), None)
        if not seller:
            raise RuntimeError("Не найдена вкладка seller.ozon.ru")

        # Open direct target session for stable Runtime context.
        ws = f"{relay_http.replace('http', 'ws', 1)}/cdp?token={relay_token}&targetId={seller['id']}"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws) as target_ws:
                cid = 1

                async def tcmd(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
                    nonlocal cid
                    my = cid
                    cid += 1
                    await target_ws.send_json({"id": my, "method": method, "params": params or {}})
                    while True:
                        msg = await target_ws.receive_json()
                        if msg.get("id") == my:
                            if "error" in msg:
                                raise RuntimeError(f"CDP {method} error: {msg['error']}")
                            return msg.get("result", {})

                await tcmd("Runtime.enable")
                await tcmd("Network.enable")
                cookies_raw = await tcmd("Network.getCookies", {"urls": ["https://seller.ozon.ru"]})
                cookies = {c["name"]: c["value"] for c in cookies_raw.get("cookies", []) if "name" in c}

                expr = r"""
(() => {
  const events = window.__ozonNetEvents || [];
  const hits = events.filter(e => String(e.url || '').includes('/api/supplier-drafts/bff/v1/get-alternative-drop-off-points'));
  if (!hits.length) return JSON.stringify({ ok: false, reason: 'no_get_alternative_events' });
  const last = hits[hits.length - 1];
  let req = last.requestBody;
  if (typeof req === 'string') {
    try { req = JSON.parse(req); } catch {}
  }
  return JSON.stringify({ ok: true, last });
})()
"""
                ev = await tcmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
                value = ev.get("result", {}).get("value") or "{}"
                payload = json.loads(value)
                if not payload.get("ok"):
                    raise RuntimeError("Не найдено событие get-alternative-drop-off-points в window.__ozonNetEvents")
                return {"cookies": cookies, "event": payload["last"]}


async def post_json(session: aiohttp.ClientSession, url: str, body: dict[str, Any]) -> dict[str, Any]:
    async with session.post(url, json=body) as resp:
        text = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {text[:400]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON decode error: {text[:400]}") from exc


async def main_async(args: argparse.Namespace) -> None:
    ctx = await get_context_from_page(args.relay_http, args.relay_token)
    last = ctx["event"]
    req = last.get("requestBody") or {}
    if isinstance(req, str):
        req = json.loads(req)
    resp = last.get("response") or {}
    points = resp.get("alternativeDropOffPoint") or []
    if not points:
        raise RuntimeError("В последнем get-alternative-drop-off-points нет списка точек")

    draft_id = str(req.get("draftId"))
    company_id = int(req.get("companyId"))
    storage_variants = (((req.get("withCalculation") or {}).get("storageVariants")) or [])
    if not storage_variants:
        raise RuntimeError("В requestBody нет withCalculation.storageVariants")
    cluster_id = str(storage_variants[0].get("macrolocalClusterId"))

    headers = build_headers(company_id, draft_id)
    results: list[dict[str, Any]] = []

    async with aiohttp.ClientSession(headers=headers, cookies=ctx["cookies"], timeout=aiohttp.ClientTimeout(total=45)) as session:
        for p in points:
            point_id = str(p.get("dropOffPointId"))
            point_type = str(p.get("dropOffPointType"))
            body = {
                "draftId": draft_id,
                "shipmentInfo": {
                    "crossDock": {
                        "macrolocalClusterId": cluster_id,
                        "dropOffWarehouseInfo": {
                            "dropOffWarehouseId": point_id,
                            "dropOffWarehouseType": point_type,
                        },
                        "dropOffFlow": {"self": {}},
                    }
                },
            }
            try:
                avail = await post_json(
                    session,
                    f"{BFF_BASE_URL}/v1/drop-off-point-availability-for-multi-cluster-draft",
                    body,
                )
                first = (avail or {}).get("firstAvailableTimeslot") or {}
                results.append(
                    {
                        "id": point_id,
                        "name": p.get("name"),
                        "type": point_type,
                        "distanceKilometers": p.get("distanceKilometers"),
                        "nearestFromAlternative": p.get("nearestTimeslotLocal"),
                        "firstAvailableFrom": first.get("fromLocal"),
                        "firstAvailableTo": first.get("toLocal"),
                        "rawAvailability": avail,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "id": point_id,
                        "name": p.get("name"),
                        "type": point_type,
                        "distanceKilometers": p.get("distanceKilometers"),
                        "nearestFromAlternative": p.get("nearestTimeslotLocal"),
                        "error": str(exc),
                    }
                )

    print(
        f"{'ID':<18} | {'Название':<34} | {'Тип':<42} | {'Км':>7} | "
        f"{'Ближайший из list':<18} | {'1й слот availability':<18}"
    )
    print("-" * 165)
    for r in results:
        km = r.get("distanceKilometers")
        km_s = f"{km:.3f}" if isinstance(km, (int, float)) else "-"
        nearest = msk_fmt(r.get("nearestFromAlternative"))
        first = msk_fmt(r.get("firstAvailableFrom")) if r.get("firstAvailableFrom") else (f"ERROR: {r.get('error','')[:24]}" if r.get("error") else "-")
        print(
            f"{str(r.get('id','')):<18} | {str(r.get('name',''))[:34]:<34} | {str(r.get('type',''))[:42]:<42} | "
            f"{km_s:>7} | {nearest:<18} | {first:<18}"
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "sourceEvent": last,
                "derivedContext": {
                    "draftId": draft_id,
                    "companyId": company_id,
                    "clusterId": cluster_id,
                    "pointsCount": len(points),
                },
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nСохранено: {out}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scan availability for each alternative drop-off point from live page event")
    p.add_argument("--relay-http", default=DEFAULT_RELAY_HTTP)
    p.add_argument("--relay-token", default=DEFAULT_RELAY_TOKEN)
    p.add_argument("--output", default="exports/dropoff_points_availability_latest.json")
    return p.parse_args()


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
