from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.supply_warehouse_scanner import CdpRelayClient, build_headers

BFF_BASE = "https://seller.ozon.ru/api/supplier-drafts/bff"
DEFAULT_RELAY_HTTP = "http://127.0.0.1:19000"
DEFAULT_RELAY_TOKEN = "codex-browser-relay-dev-token"


async def target_eval(
    relay_http: str, relay_token: str, expression: str, target_id: str
) -> Any:
    ws_url = f"{relay_http.replace('http', 'ws', 1)}/cdp?token={relay_token}&targetId={target_id}"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws_url) as ws:
            seq = 1

            async def cmd(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
                nonlocal seq
                my = seq
                seq += 1
                await ws.send_json({"id": my, "method": method, "params": params or {}})
                while True:
                    msg = await ws.receive_json()
                    if msg.get("id") == my:
                        if "error" in msg:
                            raise RuntimeError(f"CDP {method} error: {msg['error']}")
                        return msg.get("result", {})

            await cmd("Runtime.enable")
            result = await cmd("Runtime.evaluate", {"expression": expression, "returnByValue": True})
            return result.get("result", {}).get("value")


async def get_contexts_from_page(relay_http: str, relay_token: str) -> tuple[str, int, dict[str, dict[str, Any]], dict[str, str]]:
    async with CdpRelayClient(relay_http, relay_token) as cdp:
        tabs = await cdp.list_tabs()
        seller = next((t for t in tabs if "seller.ozon.ru" in str(t.get("url", ""))), None)
        if not seller:
            raise RuntimeError("Не найдена вкладка seller.ozon.ru")
        target_id = str(seller["id"])

    events_json = await target_eval(
        relay_http,
        relay_token,
        "JSON.stringify(window.__ozonNetEvents || [])",
        target_id,
    )
    events = json.loads(events_json or "[]")

    # cluster context from get-alternative-drop-off-points request bodies
    contexts: dict[str, dict[str, Any]] = {}
    draft_id = ""
    company_id = 0
    for e in events:
        url = str(e.get("url", ""))
        if "/api/supplier-drafts/bff/v1/get-alternative-drop-off-points" not in url:
            continue
        rb = e.get("requestBody") or {}
        if isinstance(rb, str):
            try:
                rb = json.loads(rb)
            except json.JSONDecodeError:
                continue
        sv = (((rb.get("withCalculation") or {}).get("storageVariants")) or [{}])[0]
        cid = str(sv.get("macrolocalClusterId") or "")
        if not cid:
            continue
        draft_id = str(rb.get("draftId") or draft_id)
        company_id = int(rb.get("companyId") or company_id)
        contexts[cid] = {
            "cluster_id": cid,
            "storageWarehouseId": str(sv.get("storageWarehouseId") or ""),
            "currentDropOffPointId": str(rb.get("currentDropOffPointId") or ""),
            "cargoType": str(rb.get("cargoType") or "CARGO_TYPE_BOX_ONLY"),
        }

    # cookies
    ws_url = f"{relay_http.replace('http', 'ws', 1)}/cdp?token={relay_token}&targetId={target_id}"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws_url) as ws:
            seq = 1
            await ws.send_json({"id": seq, "method": "Network.enable", "params": {}})
            while True:
                msg = await ws.receive_json()
                if msg.get("id") == seq:
                    break
            seq += 1
            await ws.send_json({"id": seq, "method": "Network.getCookies", "params": {"urls": ["https://seller.ozon.ru"]}})
            cookies_raw = {}
            while True:
                msg = await ws.receive_json()
                if msg.get("id") == seq:
                    cookies_raw = msg.get("result", {})
                    break
    cookies = {c["name"]: c["value"] for c in cookies_raw.get("cookies", []) if "name" in c and "value" in c}
    if not company_id:
        company_id = int(cookies.get("x-o3-company-id") or 0) or int(cookies.get("sc_company_id") or 0)

    if not draft_id:
        # fallback from location
        href = await target_eval(relay_http, relay_token, "location.href", target_id)
        draft_id = str(href).rstrip("/").split("/")[-1]

    return draft_id, company_id, contexts, cookies


async def post_json(session: aiohttp.ClientSession, url: str, body: dict[str, Any]) -> dict[str, Any]:
    async with session.post(url, json=body) as resp:
        text = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {text[:350]}")
        return json.loads(text)


async def run(args: argparse.Namespace) -> None:
    draft_id, company_id, contexts, cookies = await get_contexts_from_page(args.relay_http, args.relay_token)
    clusters = [c.strip() for c in args.clusters.split(",") if c.strip()]
    missing = [c for c in clusters if c not in contexts]
    if missing:
        raise RuntimeError(f"Нет контекста в window.__ozonNetEvents для кластеров: {missing}")

    headers = build_headers(company_id, draft_id)
    out: dict[str, Any] = {"draft_id": draft_id, "company_id": company_id, "clusters": {}}
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=aiohttp.ClientTimeout(total=45)) as s:
        for cid in clusters:
            ctx = contexts[cid]
            body = {
                "draftId": draft_id,
                "companyId": company_id,
                "cargoType": ctx["cargoType"],
                "currentDropOffPointId": ctx["currentDropOffPointId"],
                "withCalculation": {
                    "allowedDropOffPointTypes": {
                        "dropOffPointTypes": [
                            "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER",
                            "DROP_OFF_POINT_TYPE_V2_ORDERS_RECEIVING_POINT",
                            "DROP_OFF_POINT_TYPE_V2_EXTERNAL_ORDERS_RECEIVING_POINT",
                            "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK",
                        ]
                    },
                    "storageVariants": [
                        {
                            "macrolocalClusterId": cid,
                            "storageWarehouseId": ctx["storageWarehouseId"],
                        }
                    ],
                },
            }
            alt = await post_json(s, f"{BFF_BASE}/v1/get-alternative-drop-off-points", body)
            points = alt.get("alternativeDropOffPoint") or []

            rows = []
            for p in points:
                pid = str(p.get("dropOffPointId"))
                ptype = str(p.get("dropOffPointType"))
                avail_body = {
                    "draftId": draft_id,
                    "shipmentInfo": {
                        "crossDock": {
                            "macrolocalClusterId": cid,
                            "dropOffWarehouseInfo": {"dropOffWarehouseId": pid, "dropOffWarehouseType": ptype},
                            "dropOffFlow": {"self": {}},
                        }
                    },
                }
                av = await post_json(s, f"{BFF_BASE}/v1/drop-off-point-availability-for-multi-cluster-draft", avail_body)
                first = av.get("firstAvailableTimeslot") or {}
                rows.append(
                    {
                        "id": pid,
                        "name": p.get("name"),
                        "type": ptype,
                        "distanceKilometers": p.get("distanceKilometers"),
                        "nearestTimeslotLocal": p.get("nearestTimeslotLocal"),
                        "firstAvailableFrom": first.get("fromLocal"),
                        "firstAvailableTo": first.get("toLocal"),
                    }
                )

            out["clusters"][cid] = {"context": ctx, "points": rows}
            print(f"\nКластер {cid}: {len(rows)} точек")
            for r in rows[:5]:
                print(f"- {r['id']} | {r['name']} | nearest={r['nearestTimeslotLocal']} | first={r['firstAvailableFrom']}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено: {out_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scan 2 clusters using live contexts from page events")
    p.add_argument("--clusters", default="4007,4066")
    p.add_argument("--relay-http", default=DEFAULT_RELAY_HTTP)
    p.add_argument("--relay-token", default=DEFAULT_RELAY_TOKEN)
    p.add_argument("--output", default="exports/two_clusters_dropoff_scan.json")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
