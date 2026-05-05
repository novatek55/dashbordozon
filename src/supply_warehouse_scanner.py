from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp

DEFAULT_RELAY_HTTP = "http://127.0.0.1:19000"
DEFAULT_RELAY_TOKEN = "codex-browser-relay-dev-token"
DEFAULT_OUTPUT = "exports/supply_warehouse_scan_autonomous.json"

MSK = timezone(timedelta(hours=3))


class CdpRelayClient:
    def __init__(self, relay_http: str, relay_token: str) -> None:
        self.relay_http = relay_http.rstrip("/")
        self.relay_token = relay_token
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "CdpRelayClient":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def list_tabs(self) -> list[dict[str, Any]]:
        if not self._session:
            raise RuntimeError("CDP session is not opened")
        url = f"{self.relay_http}/json/list?token={self.relay_token}"
        async with self._session.get(url) as resp:
            return await resp.json()

    async def eval_in_tab(self, target_id: str, expression: str) -> Any:
        result = await self._cdp_command(
            target_id,
            [
                ("Runtime.enable", {}),
                ("Runtime.evaluate", {"expression": expression, "returnByValue": True}),
            ],
        )
        return (result.get("result") or {}).get("value")

    async def get_cookies(self, target_id: str, url: str) -> dict[str, str]:
        result = await self._cdp_command(
            target_id,
            [
                ("Network.enable", {}),
                ("Network.getCookies", {"urls": [url]}),
            ],
        )
        return {
            c["name"]: c["value"]
            for c in result.get("cookies", [])
            if isinstance(c, dict) and "name" in c and "value" in c
        }

    async def _cdp_command(
        self, target_id: str, calls: list[tuple[str, dict[str, Any]]]
    ) -> dict[str, Any]:
        if not self._session:
            raise RuntimeError("CDP session is not opened")
        ws_url = (
            f"{self.relay_http.replace('http', 'ws', 1)}"
            f"/cdp?token={self.relay_token}&targetId={target_id}"
        )
        async with self._session.ws_connect(ws_url) as ws:
            seq = 1
            last: dict[str, Any] = {}
            for method, params in calls:
                cid = seq
                seq += 1
                await ws.send_json({"id": cid, "method": method, "params": params})
                while True:
                    msg = await ws.receive_json()
                    if msg.get("id") != cid:
                        continue
                    if "error" in msg:
                        raise RuntimeError(f"CDP {method} error: {msg['error']}")
                    last = msg.get("result", {})
                    break
            return last


def build_headers(company_id: int, draft_id: str) -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://seller.ozon.ru",
        "referer": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}",
        "x-o3-app-name": "seller-ui",
        "x-o3-company-id": str(company_id),
        "x-o3-language": "ru",
        "x-o3-page-type": "supply-other",
    }


@dataclass
class RuntimeContext:
    target_id: str
    draft_id: str
    company_id: int
    cookies: dict[str, str]


def parse_draft_id_from_url(url: str) -> str | None:
    m = re.search(r"/multi-cluster/(\d+)", url or "")
    return m.group(1) if m else None


def parse_company_id(cookies: dict[str, str], arg_company_id: int | None) -> int:
    if arg_company_id:
        return arg_company_id
    for key in ("x-o3-company-id", "sc_company_id"):
        value = cookies.get(key)
        if value and value.isdigit():
            return int(value)
    raise RuntimeError("Cannot determine company id. Pass --company-id explicitly.")


async def discover_runtime_context(
    relay_http: str,
    relay_token: str,
    draft_id: str | None,
    company_id: int | None,
) -> RuntimeContext:
    async with CdpRelayClient(relay_http, relay_token) as cdp:
        tabs = await cdp.list_tabs()
        seller = next((t for t in tabs if "seller.ozon.ru" in str(t.get("url", ""))), None)
        if not seller:
            raise RuntimeError("Open seller.ozon.ru tab first.")
        target_id = str(seller["id"])
        current_url = str(seller.get("url", ""))

        cookies = await cdp.get_cookies(target_id, "https://seller.ozon.ru")
        effective_draft = draft_id or parse_draft_id_from_url(current_url)
        if not effective_draft:
            href = await cdp.eval_in_tab(target_id, "location.href")
            effective_draft = parse_draft_id_from_url(str(href))
        if not effective_draft:
            raise RuntimeError("Cannot determine draft id. Pass --draft-id.")

        effective_company = parse_company_id(cookies, company_id)
        return RuntimeContext(
            target_id=target_id,
            draft_id=effective_draft,
            company_id=effective_company,
            cookies=cookies,
        )


async def fetch_page_events(
    relay_http: str, relay_token: str, target_id: str
) -> list[dict[str, Any]]:
    async with CdpRelayClient(relay_http, relay_token) as cdp:
        raw = await cdp.eval_in_tab(target_id, "JSON.stringify(window.__ozonNetEvents || [])")
    try:
        return json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []


async def post_json(
    session: aiohttp.ClientSession, url: str, body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    async with session.post(url, json=body) as resp:
        status = resp.status
        text = await resp.text()
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {"raw": text[:1000]}
        return status, data


def to_msk(iso_value: str | None) -> str:
    if not iso_value:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00")).astimezone(MSK)
    except ValueError:
        return iso_value
    return dt.strftime("%d.%m.%Y %H:%M")


def to_msk_dt(iso_value: str | None) -> tuple[str, str]:
    if not iso_value:
        return "-", "-"
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00")).astimezone(MSK)
    except ValueError:
        return iso_value, "-"
    return dt.strftime("%d.%m.%Y"), dt.strftime("%H:%M")


def extract_dropoff_rows_from_events(
    events: list[dict[str, Any]], draft_id: str
) -> list[dict[str, Any]]:
    avail_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}

    for e in events:
        url = str(e.get("url", ""))
        rb = e.get("requestBody")
        rs = e.get("response")
        if isinstance(rb, str):
            try:
                rb = json.loads(rb)
            except json.JSONDecodeError:
                rb = {}
        if isinstance(rs, str):
            try:
                rs = json.loads(rs)
            except json.JSONDecodeError:
                rs = {}
        if not isinstance(rb, dict):
            rb = {}
        if not isinstance(rs, dict):
            rs = {}

        if rb.get("draftId") and str(rb.get("draftId")) != draft_id:
            continue

        if "drop-off-point-availability-for-multi-cluster-draft" in url:
            cross = (((rb.get("shipmentInfo") or {}).get("crossDock")) or {})
            cid = str(cross.get("macrolocalClusterId") or "")
            info = cross.get("dropOffWarehouseInfo") or {}
            pid = str(info.get("dropOffWarehouseId") or "")
            ptype = str(info.get("dropOffWarehouseType") or "")
            first = rs.get("firstAvailableTimeslot") or {}
            if cid and pid:
                avail_map[(cid, pid, ptype)] = {
                    "firstAvailableFrom": first.get("fromLocal"),
                    "firstAvailableTo": first.get("toLocal"),
                }

    for e in events:
        url = str(e.get("url", ""))
        if "get-alternative-drop-off-points" not in url:
            continue
        rb = e.get("requestBody")
        rs = e.get("response")
        if isinstance(rb, str):
            try:
                rb = json.loads(rb)
            except json.JSONDecodeError:
                rb = {}
        if isinstance(rs, str):
            try:
                rs = json.loads(rs)
            except json.JSONDecodeError:
                rs = {}
        if not isinstance(rb, dict) or not isinstance(rs, dict):
            continue
        if str(rb.get("draftId") or "") != draft_id:
            continue

        cid = ""
        with_calc = rb.get("withCalculation") or {}
        storage_variants = with_calc.get("storageVariants") or []
        if storage_variants:
            cid = str((storage_variants[0] or {}).get("macrolocalClusterId") or "")
        if not cid:
            without_calc = rb.get("withoutCalculation") or {}
            cids = without_calc.get("macrolocalClusterIds") or []
            if cids:
                cid = str(cids[0])
        if not cid:
            continue

        points = rs.get("alternativeDropOffPoint") or []
        for p in points:
            pid = str(p.get("dropOffPointId") or "")
            ptype = str(p.get("dropOffPointType") or "")
            key = (cid, pid, ptype)
            first = avail_map.get(key, {})
            rows[key] = {
                "clusterId": cid,
                "dropOffPointId": pid,
                "dropOffPointType": ptype,
                "name": p.get("name"),
                "distanceKilometers": p.get("distanceKilometers"),
                "nearestTimeslotLocal": p.get("nearestTimeslotLocal"),
                "firstAvailableFrom": first.get("firstAvailableFrom"),
                "firstAvailableTo": first.get("firstAvailableTo"),
            }

    result = list(rows.values())
    result.sort(key=lambda x: (x.get("clusterId", ""), x.get("name") or ""))
    return result


async def fetch_draft_structure(
    session: aiohttp.ClientSession, draft_id: str, company_id: int
) -> dict[str, Any]:
    status, data = await post_json(
        session,
        "https://seller.ozon.ru/api/supplier-drafts/api/v4/get",
        {"companyId": company_id, "draftId": draft_id},
    )
    if status != 200:
        raise RuntimeError(f"/api/supplier-drafts/api/v4/get failed: HTTP {status}: {data}")
    draft = data.get("draft") or {}
    multi = draft.get("multiCluster") or {}
    clusters = multi.get("clusterInfos") or []
    if not clusters:
        raise RuntimeError("No clusterInfos in draft response.")
    return data


async def fetch_orchestrator_result(
    session: aiohttp.ClientSession,
    draft_id: str,
    company_id: int,
    timeout_sec: int,
) -> dict[str, Any]:
    start_status, start_data = await post_json(
        session,
        "https://seller.ozon.ru/api/supplier-drafts/api/v2/start-orchestrator-calculation",
        {"draftId": draft_id, "companyId": company_id},
    )
    if start_status != 200:
        raise RuntimeError(
            f"/api/supplier-drafts/api/v2/start-orchestrator-calculation failed: "
            f"HTTP {start_status}: {start_data}"
        )

    deadline = asyncio.get_running_loop().time() + timeout_sec
    payload = {"draftId": draft_id, "companyId": company_id, "successTaskIds": []}
    url = "https://seller.ozon.ru/api/supplier-drafts/api/v2/get-orchestrator-calculation-result"
    last_data: dict[str, Any] = {}

    while asyncio.get_running_loop().time() < deadline:
        status, data = await post_json(session, url, payload)
        last_data = data
        if status == 200:
            task_results = (((data.get("success") or {}).get("taskResults")) or [])
            if task_results:
                return data
        await asyncio.sleep(1.0)

    raise RuntimeError(
        f"No taskResults in orchestrator result before timeout ({timeout_sec}s). "
        f"Last response: {last_data}"
    )


async def fetch_timeslots_for_warehouse(
    session: aiohttp.ClientSession,
    draft_id: str,
    company_id: int,
    calculation_task_id: str,
    storage_warehouse_id: str,
) -> dict[str, Any]:
    status, data = await post_json(
        session,
        "https://seller.ozon.ru/api/supplier-drafts/bff/v3/get-timeslots",
        {
            "companyId": company_id,
            "draftId": draft_id,
            "storageWarehouses": [{"storageWarehouseId": storage_warehouse_id}],
            "calculationTaskId": calculation_task_id,
        },
    )
    if status != 200:
        return {
            "status": status,
            "error": data,
            "timeslots": [],
            "timezone": None,
        }
    return {
        "status": 200,
        "error": None,
        "timeslots": data.get("timeslots") or [],
        "timezone": data.get("timezone"),
    }


def extract_current_dropoff_map_from_events(
    events: list[dict[str, Any]], draft_id: str
) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in events:
        url = str(e.get("url", ""))
        if "get-alternative-drop-off-points" not in url or int(e.get("status") or 0) != 200:
            continue
        rb = e.get("requestBody")
        if isinstance(rb, str):
            try:
                rb = json.loads(rb)
            except json.JSONDecodeError:
                continue
        if not isinstance(rb, dict):
            continue
        if str(rb.get("draftId") or "") != draft_id:
            continue
        cid = ""
        sv = ((rb.get("withCalculation") or {}).get("storageVariants") or [])
        if sv:
            cid = str((sv[0] or {}).get("macrolocalClusterId") or "")
        if not cid:
            cids = ((rb.get("withoutCalculation") or {}).get("macrolocalClusterIds") or [])
            if cids:
                cid = str(cids[0])
        cur = str(rb.get("currentDropOffPointId") or "")
        if cid and cur:
            out[cid] = cur
    return out


async def fetch_dropoff_points_for_cluster_storage(
    session: aiohttp.ClientSession,
    draft_id: str,
    company_id: int,
    cluster_id: str,
    storage_warehouse_id: str,
    current_dropoff_id: str,
) -> tuple[int, dict[str, Any]]:
    body = {
        "draftId": draft_id,
        "companyId": company_id,
        "cargoType": "CARGO_TYPE_BOX_AND_PALETTE",
        "currentDropOffPointId": current_dropoff_id,
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
                    "macrolocalClusterId": cluster_id,
                    "storageWarehouseId": storage_warehouse_id,
                }
            ],
        },
    }
    return await post_json(
        session,
        "https://seller.ozon.ru/api/supplier-drafts/bff/v1/get-alternative-drop-off-points",
        body,
    )


async def fetch_dropoff_availability(
    session: aiohttp.ClientSession,
    draft_id: str,
    cluster_id: str,
    dropoff_id: str,
    dropoff_type: str,
) -> tuple[int, dict[str, Any]]:
    body = {
        "draftId": draft_id,
        "shipmentInfo": {
            "crossDock": {
                "macrolocalClusterId": cluster_id,
                "dropOffWarehouseInfo": {
                    "dropOffWarehouseId": dropoff_id,
                    "dropOffWarehouseType": dropoff_type,
                },
                "dropOffFlow": {"self": {}},
            }
        },
    }
    return await post_json(
        session,
        "https://seller.ozon.ru/api/supplier-drafts/bff/v1/drop-off-point-availability-for-multi-cluster-draft",
        body,
    )


async def fetch_dropoff_points_v4(
    session: aiohttp.ClientSession,
    draft_id: str,
    company_id: int,
) -> tuple[int, dict[str, Any]]:
    # Works for direct-delivery flow where v1/get-alternative-drop-off-points may be unavailable.
    body = {
        "all": True,
        "draftId": draft_id,
        "companyId": company_id,
        "dropOffPointTypes": [
            "DROP_OFF_POINT_TYPE_V2_SELLER_WAREHOUSE",
            "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER",
            "DROP_OFF_POINT_TYPE_V2_ORDERS_RECEIVING_POINT",
            "DROP_OFF_POINT_TYPE_V2_EXTERNAL_ORDERS_RECEIVING_POINT",
            "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK",
            "DROP_OFF_POINT_TYPE_V2_DELIVERY_POINT",
        ],
    }
    return await post_json(
        session,
        "https://seller.ozon.ru/api/supplier-drafts/bff/v4/get-drop-off-points",
        body,
    )


def print_table(rows: list[dict[str, Any]]) -> None:
    headers = [
        "Cluster",
        "Warehouse",
        "StorageId",
        "Accept",
        "First slot",
        "Slots",
        "Day/Night",
    ]
    widths = [16, 34, 16, 6, 16, 6, 9]

    def fmt(values: list[str]) -> str:
        return "|" + "|".join(f" {v[:w]:<{w}} " for v, w in zip(values, widths)) + "|"

    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    print(sep)
    print(fmt(headers))
    print(sep)
    for row in rows:
        print(
            fmt(
                [
                    row.get("clusterName", ""),
                    row.get("warehouseName", ""),
                    row.get("storageWarehouseId", ""),
                    "yes" if row.get("accepts") else "no",
                    row.get("firstSlotMsk", "-"),
                    str(row.get("slotsCount", 0)),
                    row.get("dayNight", "-"),
                ]
            )
        )
    print(sep)


def write_html_report(path: Path, payload: dict[str, Any]) -> None:
    rows = payload.get("rows") or []
    dropoff_rows = payload.get("dropoffRowsFromEvents") or []
    cluster_name_by_id = {
        str(c.get("macrolocalClusterId") or ""): str(c.get("name") or "")
        for c in (payload.get("draftClusters") or [])
    }
    accepts = sum(1 for r in rows if r.get("accepts"))
    clusters = sorted({str(r.get("clusterName") or "") for r in rows})
    html_rows = []
    for r in rows:
        accept_text = "yes" if r.get("accepts") else "no"
        cls = "ok" if r.get("accepts") else "bad"
        html_rows.append(
            "<tr>"
            f"<td>{r.get('clusterName','')}</td>"
            f"<td>{r.get('warehouseName','')}</td>"
            f"<td>{r.get('storageWarehouseId','')}</td>"
            f"<td class='{cls}'>{accept_text}</td>"
            f"<td>{r.get('firstSlotMsk','-')}</td>"
            f"<td>{r.get('slotsCount',0)}</td>"
            f"<td>{r.get('dayNight','-')}</td>"
            "</tr>"
        )

    html_dropoff_rows = []
    for r in dropoff_rows:
        cid = str(r.get("clusterId") or "")
        cname = cluster_name_by_id.get(cid) or cid
        first_detailed = to_msk(r.get("firstAvailableFrom"))
        if first_detailed == "-":
            first_detailed = to_msk(r.get("nearestTimeslotLocal"))
        html_dropoff_rows.append(
            "<tr>"
            f"<td>{cname}</td>"
            f"<td>{r.get('name','')}</td>"
            f"<td>{r.get('dropOffPointId','')}</td>"
            f"<td>{r.get('dropOffPointType','')}</td>"
            f"<td>{to_msk(r.get('nearestTimeslotLocal'))}</td>"
            f"<td>{first_detailed}</td>"
            "</tr>"
        )

    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ozon Supply Warehouses Report</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 20px; color: #111; }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    .meta {{ margin: 4px 0; color: #444; }}
    .chips {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0 18px; }}
    .chip {{ background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 999px; padding: 6px 10px; font-size: 13px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d1d5db; padding: 8px; text-align: left; }}
    th {{ background: #f8fafc; }}
    .ok {{ color: #0f766e; font-weight: 600; }}
    .bad {{ color: #b91c1c; font-weight: 600; }}
  </style>
</head>
<body>
  <h1>Точки отгрузки и окна Ozon</h1>
  <div class="meta">Draft: {payload.get('draftId','')} | Company: {payload.get('companyId','')}</div>
  <div class="meta">Calculation task: {payload.get('calculationTaskId','')}</div>
  <div class="chips">
    <div class="chip">Clusters: {len(clusters)}</div>
    <div class="chip">Warehouses: {len(rows)}</div>
    <div class="chip">Drop-off points: {len(dropoff_rows)}</div>
    <div class="chip">Accepts: {accepts}</div>
    <div class="chip">Not accepts: {len(rows)-accepts}</div>
  </div>
  <h2>Основной блок: точки отгрузки</h2>
  <div class="meta">Если нет строк, нужно открыть в UI выбор точки отгрузки/таймслота и запустить снова.</div>
  <table>
    <thead>
      <tr>
        <th>Cluster</th>
        <th>Drop-off point</th>
        <th>Drop-off ID</th>
        <th>Type</th>
        <th>Nearest window</th>
        <th>Detailed/First window</th>
      </tr>
    </thead>
    <tbody>
      {''.join(html_dropoff_rows) if html_dropoff_rows else '<tr><td colspan=\"6\">Нет данных по точкам отгрузки</td></tr>'}
    </tbody>
  </table>
  <h2 style="margin-top:24px;">Технический блок: склады назначения (оркестратор)</h2>
  <table>
    <thead>
      <tr>
        <th>Cluster</th>
        <th>Warehouse</th>
        <th>Storage ID</th>
        <th>Accept</th>
        <th>First slot (MSK)</th>
        <th>Slots</th>
        <th>Day/Night</th>
      </tr>
    </thead>
    <tbody>
      {''.join(html_rows)}
    </tbody>
  </table>
  <h2 style="margin-top:24px;">РЎР»РѕС‚С‹ РїРѕ РїСЂРёРЅРёРјР°СЋС‰РёРј С‚РѕС‡РєР°Рј (Accept = yes)</h2>
  <div class="meta">РџСЂРѕРїСѓСЃРєР°РµРј Accept=no, РїРѕ Accept=yes СЃРѕР±РёСЂР°РµРј РІСЃРµ РґР°С‚С‹/РІСЂРµРјСЏ СЃР»РѕС‚РѕРІ.</div>
  {build_accept_slots_html(payload)}
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def build_accept_slots_html(payload: dict[str, Any]) -> str:
    rows = payload.get("rows") or []
    accepted = [r for r in rows if r.get("accepts")]
    if not accepted:
        return "<div class='meta'>РќРµС‚ РїСЂРёРЅРёРјР°СЋС‰РёС… С‚РѕС‡РµРє.</div>"

    accepted.sort(key=lambda x: (x.get("clusterName", ""), x.get("warehouseName", "")))
    blocks: list[str] = []
    for r in accepted:
        points = r.get("dropOffPoints") or []
        blocks.append(
            "<details style='margin:8px 0; border:1px solid #d1d5db; border-radius:8px; padding:8px;'>"
            f"<summary><b>{r.get('clusterName','')}</b> | {r.get('warehouseName','')} | "
            f"{r.get('storageWarehouseId','')} | points: {len(points)}</summary>"
        )
        if points:
            for p in points:
                pslots = p.get("slots") or []
                blocks.append(
                    "<details style='margin:6px 0; border:1px dashed #cbd5e1; border-radius:6px; padding:6px;'>"
                    f"<summary>{p.get('name','')} | {p.get('dropOffPointId','')} | slots: {len(pslots)}</summary>"
                )
                if pslots:
                    blocks.append(
                        "<table style='margin-top:8px;'><thead><tr>"
                        "<th>#</th><th>Date</th><th>From</th><th>To</th>"
                        "</tr></thead><tbody>"
                    )
                    for i, s in enumerate(pslots, start=1):
                        blocks.append(
                            "<tr>"
                            f"<td>{i}</td>"
                            f"<td>{s.get('dateMsk','-')}</td>"
                            f"<td>{s.get('fromMsk','-')}</td>"
                            f"<td>{s.get('toMsk','-')}</td>"
                            "</tr>"
                        )
                    blocks.append("</tbody></table>")
                else:
                    blocks.append("<div class='meta'>РЎР»РѕС‚С‹ РЅРµ РїРѕР»СѓС‡РµРЅС‹.</div>")
                blocks.append("</details>")
        else:
            blocks.append("<div class='meta'>РўРѕС‡РєРё РѕС‚РіСЂСѓР·РєРё РЅРµ РїРѕР»СѓС‡РµРЅС‹.</div>")
        blocks.append("</details>")
    return "".join(blocks)


async def run(args: argparse.Namespace) -> None:
    ctx = await discover_runtime_context(
        relay_http=args.relay_http,
        relay_token=args.relay_token,
        draft_id=args.draft_id,
        company_id=args.company_id,
    )
    headers = build_headers(ctx.company_id, ctx.draft_id)
    timeout = aiohttp.ClientTimeout(total=90)

    events = await fetch_page_events(args.relay_http, args.relay_token, ctx.target_id)
    dropoff_rows = extract_dropoff_rows_from_events(events, ctx.draft_id)
    current_dropoff_map = extract_current_dropoff_map_from_events(events, ctx.draft_id)

    async with aiohttp.ClientSession(
        headers=headers, cookies=ctx.cookies, timeout=timeout
    ) as session:
        draft_data = await fetch_draft_structure(session, ctx.draft_id, ctx.company_id)
        cluster_infos = ((draft_data.get("draft") or {}).get("multiCluster") or {}).get("clusterInfos") or []
        selected_cluster_ids = {str(c.get("macrolocalClusterId")) for c in cluster_infos}
        if args.cluster_ids:
            selected_cluster_ids = {x.strip() for x in args.cluster_ids.split(",") if x.strip()}

        calc_data = await fetch_orchestrator_result(
            session, ctx.draft_id, ctx.company_id, args.calc_timeout
        )
        task_results = (((calc_data.get("success") or {}).get("taskResults")) or [])
        if not task_results:
            raise RuntimeError(f"No taskResults in orchestrator response: {calc_data}")

        calculation_task_id = str(task_results[-1].get("calculationTaskId") or "")
        if not calculation_task_id:
            raise RuntimeError(f"No calculationTaskId in taskResults: {task_results}")

        # Ozon may split clusters across multiple taskResults; merge them all.
        clusters_by_id: dict[str, dict[str, Any]] = {}
        calc_by_storage: dict[str, str] = {}
        for tr in task_results:
            tr_calc_id = str(tr.get("calculationTaskId") or "")
            for c in (((tr.get("success") or {}).get("clusters")) or []):
                cid = str(c.get("macrolocalClusterId") or "")
                if cid:
                    clusters_by_id[cid] = c
                for w in (c.get("warehouses") or []):
                    storage = w.get("storageWarehouse") or {}
                    sid = str(storage.get("clearingWarehouseId") or "")
                    if sid and tr_calc_id:
                        calc_by_storage[sid] = tr_calc_id
        clusters = list(clusters_by_id.values())
        rows: list[dict[str, Any]] = []
        raw_clusters: list[dict[str, Any]] = []

        sem = asyncio.Semaphore(args.parallel)

        async def process_warehouse(cluster: dict[str, Any], wh: dict[str, Any]) -> dict[str, Any]:
            storage = wh.get("storageWarehouse") or {}
            status = wh.get("status") or {}
            nearest = wh.get("nearestWarehouseTimeslot") or {}
            storage_id = str(storage.get("clearingWarehouseId") or "")
            if not storage_id:
                return {}
            accepts = status.get("state") in {
                "WAREHOUSE_SCORING_STATE_FULL_AVAILABLE",
                "WAREHOUSE_SCORING_STATE_PARTIALLY_AVAILABLE",
            }
            calc_id = calc_by_storage.get(storage_id) or calculation_task_id
            ts_data: dict[str, Any]
            slots: list[dict[str, Any]] = []

            if accepts:
                async with sem:
                    ts_data = await fetch_timeslots_for_warehouse(
                        session,
                        ctx.draft_id,
                        ctx.company_id,
                        calc_id,
                        storage_id,
                    )
                for s in (ts_data.get("timeslots") or []):
                    d_from, t_from = to_msk_dt(s.get("fromUtc"))
                    _, t_to = to_msk_dt(s.get("toUtc"))
                    slots.append(
                        {
                            "fromUtc": s.get("fromUtc"),
                            "toUtc": s.get("toUtc"),
                            "dateMsk": d_from,
                            "fromMsk": t_from,
                            "toMsk": t_to,
                        }
                    )
            else:
                ts_data = {
                    "status": None,
                    "error": "SKIPPED_NOT_ACCEPTING",
                    "timeslots": [],
                    "timezone": None,
                }

            first_slot = slots[0] if slots else {}
            first_from = first_slot.get("fromUtc") or nearest.get("fromLocal")
            first_msk = to_msk(first_from)
            hour = -1
            if first_from:
                try:
                    hour = (
                        datetime.fromisoformat(first_from.replace("Z", "+00:00"))
                        .astimezone(MSK)
                        .hour
                    )
                except ValueError:
                    hour = -1
            day_night = "-"
            if hour >= 0:
                day_night = "night" if (hour >= 22 or hour < 8) else "day"

            return {
                "clusterId": str(cluster.get("macrolocalClusterId") or ""),
                "clusterName": str(cluster.get("clusterName") or ""),
                "warehouseName": str(storage.get("name") or ""),
                "storageWarehouseId": storage_id,
                "warehouseStatus": status,
                "accepts": accepts,
                "nearestWarehouseTimeslot": nearest,
                "timeslotsStatus": ts_data.get("status"),
                "timeslotsError": ts_data.get("error"),
                "timeslotsCalculationTaskId": calc_id,
                "slotsCount": len(slots),
                "firstSlot": first_slot,
                "firstSlotMsk": first_msk,
                "dayNight": day_night,
                "allSlots": slots,
            }

        tasks: list[asyncio.Task] = []
        for cluster in clusters:
            cluster_id = str(cluster.get("macrolocalClusterId") or "")
            if cluster_id not in selected_cluster_ids:
                continue
            raw_clusters.append(cluster)
            for wh in (cluster.get("warehouses") or []):
                tasks.append(asyncio.create_task(process_warehouse(cluster, wh)))

        for task in tasks:
            item = await task
            if item:
                rows.append(item)

        # Required hierarchy: cluster -> cluster warehouse -> drop-off points -> slots.
        fallback_event_points: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for d in dropoff_rows:
            cid = str(d.get("clusterId") or "")
            if not cid:
                continue
            # Event-derived points are cluster-scoped; reuse for each accepting warehouse in cluster.
            fallback_event_points.setdefault((cid, "*"), []).append(d)

        for r in rows:
            r["dropOffPoints"] = []
            if not r.get("accepts"):
                continue
            cid = str(r.get("clusterId") or "")
            sid = str(r.get("storageWarehouseId") or "")
            current_dropoff_id = current_dropoff_map.get(cid) or args.default_current_dropoff_id
            warehouse_slots = r.get("allSlots") or []

            status, alt = await fetch_dropoff_points_for_cluster_storage(
                session,
                ctx.draft_id,
                ctx.company_id,
                cid,
                sid,
                current_dropoff_id,
            )
            points = []
            if status == 200:
                points = alt.get("alternativeDropOffPoint") or []
            else:
                # Fallback to points from page events.
                points = fallback_event_points.get((cid, "*"), [])
                if not points:
                    v4_status, v4 = await fetch_dropoff_points_v4(
                        session, ctx.draft_id, ctx.company_id
                    )
                    if v4_status == 200:
                        points = [
                            p
                            for p in (v4.get("dropOffPoints") or [])
                            if bool(p.get("isAvailableForPickup"))
                        ]

            enriched_points: list[dict[str, Any]] = []
            for p in points:
                pid = str(p.get("dropOffPointId") or "")
                ptype = str(p.get("dropOffPointType") or "")
                if not pid or not ptype:
                    continue
                av_status, av = await fetch_dropoff_availability(
                    session,
                    ctx.draft_id,
                    cid,
                    pid,
                    ptype,
                )

                slots_raw = []
                if av_status == 200:
                    # Keep future-proof: if API returns full list, use it; otherwise wrap first slot.
                    slots_raw = (
                        av.get("timeslots")
                        or av.get("availableTimeslots")
                        or ([] if not av.get("firstAvailableTimeslot") else [av.get("firstAvailableTimeslot")])
                    )
                # In direct flow UI full calendar comes from v3/get-timeslots by storageWarehouseId.
                # Prefer full warehouse calendar whenever point-specific response is only nearest slot.
                if warehouse_slots and len(slots_raw) < len(warehouse_slots):
                    slots_raw = [
                        {"fromUtc": s.get("fromUtc"), "toUtc": s.get("toUtc")}
                        for s in warehouse_slots
                    ]
                slots = []
                for s in slots_raw:
                    f = s.get("fromLocal") or s.get("fromUtc")
                    t = s.get("toLocal") or s.get("toUtc")
                    d_from, t_from = to_msk_dt(f)
                    _, t_to = to_msk_dt(t)
                    slots.append(
                        {
                            "from": f,
                            "to": t,
                            "dateMsk": d_from,
                            "fromMsk": t_from,
                            "toMsk": t_to,
                        }
                    )

                enriched_points.append(
                    {
                        "dropOffPointId": pid,
                        "dropOffPointType": ptype,
                        "name": p.get("name"),
                        "nearestTimeslotLocal": p.get("nearestTimeslotLocal"),
                        "availabilityStatus": av_status,
                        "slotsSource": (
                            "warehouse_timeslots_v3"
                            if warehouse_slots and len(slots) == len(warehouse_slots)
                            else "point_availability"
                        ),
                        "slots": slots,
                    }
                )

            r["dropOffPoints"] = enriched_points

    rows.sort(key=lambda x: (x.get("clusterName", ""), x.get("warehouseName", "")))
    print(
        f"Draft {ctx.draft_id} | company {ctx.company_id} | "
        f"clusters {len({r['clusterId'] for r in rows})} | warehouses {len(rows)}"
    )
    print_table(rows)

    output = {
        "draftId": ctx.draft_id,
        "companyId": ctx.company_id,
        "calculationTaskId": calculation_task_id,
        "calculationTaskByStorage": calc_by_storage,
        "currentDropOffByCluster": current_dropoff_map,
        "rows": rows,
        "dropoffRowsFromEvents": dropoff_rows,
        "draftClusters": cluster_infos,
        "calcClustersRaw": raw_clusters,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {out_path}")
    html_path = Path(args.html_output)
    write_html_report(html_path, output)
    print(f"Saved HTML: {html_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Autonomous Ozon multi-cluster warehouse scanner (clusters + timeslots)."
    )
    p.add_argument("--relay-http", default=DEFAULT_RELAY_HTTP)
    p.add_argument("--relay-token", default=DEFAULT_RELAY_TOKEN)
    p.add_argument("--draft-id", default=None)
    p.add_argument("--company-id", type=int, default=None)
    p.add_argument("--cluster-ids", default="", help="Optional CSV cluster ids")
    p.add_argument("--calc-timeout", type=int, default=30, help="Seconds for calc poll")
    p.add_argument("--parallel", type=int, default=6)
    p.add_argument("--default-current-dropoff-id", default="22190776129000")
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--html-output", default="exports/supply_warehouse_scan_autonomous.html")
    return p.parse_args()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
