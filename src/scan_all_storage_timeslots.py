from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp

MSK = timezone(timedelta(hours=3))
DEFAULT_RELAY_HTTP = "http://127.0.0.1:19000"
DEFAULT_RELAY_TOKEN = "codex-browser-relay-dev-token"


def to_msk(iso_value: str | None) -> str:
    if not iso_value:
        return "-"
    dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00")).astimezone(MSK)
    return dt.strftime("%d.%m.%Y %H:%M")


async def cdp_eval_and_cookies(relay_http: str, relay_token: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    async with aiohttp.ClientSession() as s:
        tabs = await (await s.get(f"{relay_http}/json/list?token={relay_token}")).json()
        seller = next((t for t in tabs if "seller.ozon.ru" in str(t.get("url", ""))), None)
        if not seller:
            raise RuntimeError("Не найдена вкладка seller.ozon.ru")

        ws_url = f"{relay_http.replace('http', 'ws', 1)}/cdp?token={relay_token}&targetId={seller['id']}"
        async with s.ws_connect(ws_url) as ws:
            async def cmd(cid: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
                await ws.send_json({"id": cid, "method": method, "params": params or {}})
                while True:
                    msg = await ws.receive_json()
                    if msg.get("id") == cid:
                        if "error" in msg:
                            raise RuntimeError(f"CDP {method} error: {msg['error']}")
                        return msg.get("result", {})

            await cmd(1, "Runtime.enable")
            await cmd(2, "Network.enable")
            ev = await cmd(
                3,
                "Runtime.evaluate",
                {"expression": "JSON.stringify(window.__ozonNetEvents || [])", "returnByValue": True},
            )
            ck = await cmd(4, "Network.getCookies", {"urls": ["https://seller.ozon.ru"]})

    events = json.loads(ev.get("result", {}).get("value") or "[]")
    cookies = {c["name"]: c["value"] for c in ck.get("cookies", []) if "name" in c and "value" in c}
    return events, cookies


def derive_context(events: list[dict[str, Any]]) -> tuple[str, int, list[str], dict[str, str], str | None]:
    alt_reqs: list[dict[str, Any]] = []
    for e in events:
        if "/api/supplier-drafts/bff/v1/get-alternative-drop-off-points" not in str(e.get("url", "")):
            continue
        rb = e.get("requestBody")
        if isinstance(rb, str):
            try:
                rb = json.loads(rb)
            except json.JSONDecodeError:
                continue
        if isinstance(rb, dict):
            alt_reqs.append(rb)
    if not alt_reqs:
        raise RuntimeError("Нет событий get-alternative-drop-off-points в window.__ozonNetEvents")

    last = alt_reqs[-1]
    draft_id = str(last.get("draftId"))
    company_id = int(last.get("companyId"))

    storage_ids: list[str] = []
    for rb in alt_reqs:
        for sv in ((rb.get("withCalculation") or {}).get("storageVariants") or []):
            sid = str(sv.get("storageWarehouseId") or "")
            if sid and sid not in storage_ids:
                storage_ids.append(sid)

    calc_by_storage: dict[str, str] = {}
    last_calc: str | None = None
    for e in events:
        if "/api/supplier-drafts/bff/v3/get-timeslots" not in str(e.get("url", "")):
            continue
        rb = e.get("requestBody")
        if isinstance(rb, str):
            try:
                rb = json.loads(rb)
            except json.JSONDecodeError:
                continue
        if not isinstance(rb, dict):
            continue
        calc = rb.get("calculationTaskId")
        if calc:
            last_calc = str(calc)
        for sw in (rb.get("storageWarehouses") or []):
            sid = str(sw.get("storageWarehouseId") or "")
            if sid and calc:
                calc_by_storage[sid] = str(calc)

    return draft_id, company_id, storage_ids, calc_by_storage, last_calc


async def fetch_timeslots(
    cookies: dict[str, str],
    draft_id: str,
    company_id: int,
    storage_ids: list[str],
    calc_by_storage: dict[str, str],
    fallback_calc: str | None,
) -> list[dict[str, Any]]:
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "x-o3-app-name": "seller-ui",
        "x-o3-company-id": str(company_id),
        "x-o3-language": "ru",
        "x-o3-page-type": "supply-other",
        "origin": "https://seller.ozon.ru",
        "referer": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}",
    }
    results: list[dict[str, Any]] = []

    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=aiohttp.ClientTimeout(total=60)) as sess:
        for sid in storage_ids:
            calc_id = calc_by_storage.get(sid) or fallback_calc
            if not calc_id:
                results.append({"storageWarehouseId": sid, "status": None, "error": "NO_CALCULATION_TASK_ID"})
                continue
            body = {
                "companyId": company_id,
                "draftId": draft_id,
                "storageWarehouses": [{"storageWarehouseId": sid}],
                "calculationTaskId": calc_id,
            }
            url = "https://seller.ozon.ru/api/supplier-drafts/bff/v3/get-timeslots"
            async with sess.post(url, json=body) as resp:
                text = await resp.text()
                if resp.status != 200:
                    results.append(
                        {
                            "storageWarehouseId": sid,
                            "status": resp.status,
                            "calculationTaskId": calc_id,
                            "error": text[:500],
                        }
                    )
                    continue
                data = json.loads(text)
                slots = data.get("timeslots") or []
                results.append(
                    {
                        "storageWarehouseId": sid,
                        "status": 200,
                        "calculationTaskId": calc_id,
                        "timezone": data.get("timezone"),
                        "slotsCount": len(slots),
                        "first": slots[0] if slots else None,
                        "last": slots[-1] if slots else None,
                    }
                )
    return results


async def main_async(args: argparse.Namespace) -> None:
    events, cookies = await cdp_eval_and_cookies(args.relay_http, args.relay_token)
    draft_id, company_id, storage_ids, calc_by_storage, fallback_calc = derive_context(events)

    if args.storage_ids:
        requested = [x.strip() for x in args.storage_ids.split(",") if x.strip()]
        storage_ids = [x for x in storage_ids if x in requested]

    results = await fetch_timeslots(cookies, draft_id, company_id, storage_ids, calc_by_storage, fallback_calc)

    print(f"Draft: {draft_id} | Company: {company_id}")
    print(f"{'StorageWarehouseId':<18} | {'Slots':>6} | {'First slot':<16} | {'Last slot':<16} | Status")
    print("-" * 90)
    for r in results:
        if r.get("status") == 200:
            first = to_msk((r.get("first") or {}).get("fromUtc"))
            last = to_msk((r.get("last") or {}).get("fromUtc"))
            print(f"{r['storageWarehouseId']:<18} | {r['slotsCount']:>6} | {first:<16} | {last:<16} | 200")
        else:
            print(f"{r['storageWarehouseId']:<18} | {'-':>6} | {'-':<16} | {'-':<16} | {r.get('status')} ERROR")

    out = {
        "draftId": draft_id,
        "companyId": company_id,
        "storage_ids": storage_ids,
        "calc_by_storage": calc_by_storage,
        "fallback_calc": fallback_calc,
        "results": results,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено: {out_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Get v3 timeslots for all storage warehouses from current live draft context")
    p.add_argument("--relay-http", default=DEFAULT_RELAY_HTTP)
    p.add_argument("--relay-token", default=DEFAULT_RELAY_TOKEN)
    p.add_argument("--storage-ids", default="", help="Optional comma-separated subset")
    p.add_argument("--output", default="exports/all_storage_timeslots_latest.json")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
