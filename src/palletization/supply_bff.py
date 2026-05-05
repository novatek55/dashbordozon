"""
CDP-based Ozon Supply BFF API module.

Calls Ozon BFF endpoints through the browser session (cookies auto-attached).
Requires seller.ozon.ru tab open in browser with codex-browser-relay running.

Key APIs:
  - create_draft()              → create new multi-cluster draft
  - get_clusters()              → list all clusters with IDs
  - get_sc_warehouses()         → get sorting centers for a cluster
  - set_warehouse()             → set delivery method + drop-off point
  - select_cluster()            → mark cluster as selected
  - get_timeslots()             → get available timeslots for a warehouse
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import aiohttp

RELAY_HTTP = "http://127.0.0.1:19000"
RELAY_TOKEN = "codex-browser-relay-dev-token"
SELLER_ORIGIN = "https://seller.ozon.ru"
COMPANY_ID = 146478

API_BASE = "/api/supplier-drafts/api"
BFF_BASE = "/api/supplier-drafts/bff"

# Drop-off point types
SC_TYPE  = "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER"
ALL_TYPES = [
    "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER",
    "DROP_OFF_POINT_TYPE_V2_ORDERS_RECEIVING_POINT",
    "DROP_OFF_POINT_TYPE_V2_DELIVERY_POINT",
    "DROP_OFF_POINT_TYPE_V2_EXTERNAL_ORDERS_RECEIVING_POINT",
    "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK",
]

# ─── low-level CDP helpers ────────────────────────────────────────────────────

async def _cdp_ws(target_id: str, calls: list[tuple[str, dict]]) -> dict:
    ws_url = (
        f"{RELAY_HTTP.replace('http', 'ws', 1)}"
        f"/cdp?token={RELAY_TOKEN}&targetId={target_id}"
    )
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url) as ws:
            seq, last = 1, {}
            for method, params in calls:
                cid = seq
                seq += 1
                await ws.send_json({"id": cid, "method": method, "params": params})
                while True:
                    msg = await ws.receive_json()
                    if msg.get("id") != cid:
                        continue
                    if "error" in msg:
                        raise RuntimeError(f"CDP {method}: {msg['error']}")
                    last = msg.get("result", {})
                    break
            return last


async def _eval(target_id: str, expr: str) -> Any:
    """Evaluate JS in browser tab; returns Python value."""
    result = await _cdp_ws(target_id, [
        ("Runtime.enable", {}),
        ("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": True,
            "timeout": 30000,
        }),
    ])
    if result.get("exceptionDetails"):
        raise RuntimeError(result["exceptionDetails"]["exception"]["description"])
    return (result.get("result") or {}).get("value")


async def _fetch(target_id: str, path: str, body: dict, draft_id: str = "") -> dict:
    """POST JSON to seller.ozon.ru in browser context (cookies attached automatically)."""
    headers_js = json.dumps({
        "content-type": "application/json",
        "x-o3-app-name": "seller-ui",
        "x-o3-company-id": str(COMPANY_ID),
        "x-o3-language": "ru",
        "x-o3-page-type": "supply-other",
    })
    body_js = json.dumps(body)
    expr = f"""
(async () => {{
  const r = await fetch('{SELLER_ORIGIN}{path}', {{
    method: 'POST',
    headers: {headers_js},
    body: JSON.stringify({body_js})
  }});
  let data;
  try {{ data = await r.json(); }} catch {{ data = null; }}
  return {{ status: r.status, data }};
}})()
"""
    result = await _eval(target_id, expr)
    if result is None:
        raise RuntimeError(f"No response from {path}")
    if result["status"] not in (200, 201):
        raise RuntimeError(f"HTTP {result['status']} from {path}: {result['data']}")
    return result["data"]


# ─── tab discovery ────────────────────────────────────────────────────────────

async def find_seller_tab() -> str:
    """Find seller.ozon.ru tab via relay HTTP API. Returns targetId."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{RELAY_HTTP}/json/list?token={RELAY_TOKEN}"
        ) as resp:
            tabs = await resp.json()
    seller = next((t for t in tabs if "seller.ozon.ru" in str(t.get("url", ""))), None)
    if not seller:
        raise RuntimeError("Open seller.ozon.ru tab first.")
    return str(seller["id"])


# ─── draft API ───────────────────────────────────────────────────────────────

async def create_draft(target_id: str) -> str:
    """Create new multi-cluster draft. Returns draftId string."""
    resp = await _fetch(target_id, f"{API_BASE}/v3/create", {
        "companyId": COMPANY_ID,
        "origin": "web:seller",
        "multiCluster": {"allClusters": {}},
    })
    return str(resp["draftId"])


async def get_draft(target_id: str, draft_id: str) -> dict:
    """Get full draft info."""
    return await _fetch(target_id, f"{API_BASE}/v4/get", {
        "companyId": COMPANY_ID,
        "draftId": draft_id,
    })


async def get_clusters(target_id: str, draft_id: str) -> list[dict]:
    """
    Get all clusters from draft.
    Returns list of:
      {macrolocalClusterId, name, shipmentInfoId, bundleId, storageWarehouseCount, ...}
    """
    resp = await get_draft(target_id, draft_id)
    return resp["draft"]["multiCluster"]["clusterInfos"]


# ─── warehouse / drop-off point API ─────────────────────────────────────────

async def get_allowed_types(
    target_id: str,
    draft_id: str,
    cluster_id: str,
) -> list[str]:
    """
    Проверяет какие типы точек отгрузки разрешены для кластера.
    Returns list of allowed DROP_OFF_POINT_TYPE_V2_* strings.
    """
    resp = await _fetch(target_id, f"{BFF_BASE}/v1/allowed-drop-off-point-types-for-multi-cluster-draft", {
        "draftId": draft_id,
        "companyId": COMPANY_ID,
        "macrolocalClusterId": cluster_id,
    })
    allowed = []
    for item in resp.get("allowedDropOffPointTypes") or []:
        if item.get("isAllowed"):
            t = str(item.get("dropOffPointType") or "")
            if t:
                allowed.append(t)
    return allowed


async def get_drop_off_points_viewport(
    target_id: str,
    draft_id: str,
    types: list[str] | None = None,
) -> list[dict]:
    """
    Получает ВСЕ точки отгрузки через viewport-запрос (вся Россия/СНГ).
    POST /api/supplier-drafts/bff/v4/get-drop-off-points
    """
    resp = await _fetch(target_id, "/api/supplier-drafts/bff/v4/get-drop-off-points", {
        "companyId": COMPANY_ID,
        "draftId": draft_id,
        "dropOffPointTypes": types or [SC_TYPE, "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK"],
        "byViewport": {
            "viewport": {
                "bottomLeftPoint": {"latitude": 40.0, "longitude": 20.0},
                "topRightPoint": {"latitude": 75.0, "longitude": 180.0},
            }
        },
    })
    return resp.get("dropOffPoints") or []


async def get_warehouse_info(
    target_id: str,
    draft_id: str,
    warehouse_id: int,
    warehouse_type: str = SC_TYPE,
) -> dict:
    """
    Получает детали склада: ближайший таймслот, лимиты, расписание.
    POST /api/supplier-drafts/bff/v2/warehouse-info
    """
    return await _fetch(target_id, "/api/supplier-drafts/bff/v2/warehouse-info", {
        "supplierDraftId": draft_id,
        "clearingWarehouseId": warehouse_id,
        "dropOffPointTypeV2": warehouse_type,
    })


async def get_sc_warehouses(
    target_id: str,
    draft_id: str,
    cluster_id: str,
    types: list[str] | None = None,
) -> list[dict]:
    """
    Get available drop-off warehouses for a cluster.

    types: list of DROP_OFF_POINT_TYPE_V2_* constants (default: SC only).
    Returns list of:
      {dropOffPointId, name, dropOffPointType, nearestTimeslotLocal, distanceKilometers, priority}
    """
    resp = await _fetch(target_id, f"{BFF_BASE}/v1/get-alternative-drop-off-points", {
        "draftId": draft_id,
        "companyId": COMPANY_ID,
        "cargoType": "CARGO_TYPE_BOX_ONLY",
        "withoutCalculation": {
            "allowedDropOffPointTypes": types or [SC_TYPE],
            "macrolocalClusterIds": [cluster_id],
        },
    })
    return resp.get("alternativeDropOffPoint", [])


async def check_warehouse_availability(
    target_id: str,
    draft_id: str,
    cluster_id: str,
    warehouse_id: str,
    warehouse_type: str = SC_TYPE,
) -> dict:
    """
    Check if a warehouse is available for drop-off on this draft.
    Returns {available: bool, reasons: list[str]}.
    """
    resp = await _fetch(target_id, f"{BFF_BASE}/v1/drop-off-point-availability-for-multi-cluster-draft", {
        "draftId": draft_id,
        "shipmentInfo": {
            "crossDock": {
                "macrolocalClusterId": cluster_id,
                "dropOffWarehouseInfo": {
                    "dropOffWarehouseId": warehouse_id,
                    "dropOffWarehouseType": warehouse_type,
                },
                "dropOffFlow": {"self": {}},
            }
        },
    })
    reasons = resp.get("notAvailableResponse", {}).get("reasonsV2", [])
    return {"available": not bool(reasons), "reasons": reasons}


# ─── save delivery method ─────────────────────────────────────────────────────

async def set_warehouse_crossdock(
    target_id: str,
    draft_id: str,
    cluster_id: str,
    warehouse_id: int,
    warehouse_type: str = SC_TYPE,
) -> None:
    """
    Set cross-dock delivery method + drop-off warehouse for a cluster.
    warehouse_id must be int (as returned in dropOffPointId from get_sc_warehouses).
    After this, call select_cluster() to mark it as selected.
    """
    await _fetch(target_id, f"{API_BASE}/v1/update-shipment-info", {
        "companyId": COMPANY_ID,
        "draftId": draft_id,
        "shipmentInfo": {
            "crossDock": {
                "macrolocalClusterId": cluster_id,
                "dropOffWarehouseInfo": {
                    "dropOffWarehouseId": warehouse_id,
                    "dropOffWarehouseType": warehouse_type,
                },
                "dropOffFlow": {"self": {}},
            }
        },
    }, draft_id=draft_id)


async def select_cluster(
    target_id: str,
    draft_id: str,
    cluster_id: str,
    selected: bool = True,
) -> None:
    """Mark (or unmark) a cluster as selected in the draft."""
    await _fetch(target_id, f"{BFF_BASE}/v1/update-is-cluster-selected", {
        "companyId": COMPANY_ID,
        "draftId": draft_id,
        "isSelected": selected,
        "macrolocalClusterId": cluster_id,
    })


# ─── timeslots (TODO: needs calculationTaskId investigation) ──────────────────

async def get_timeslots(
    target_id: str,
    draft_id: str,
    warehouse_id: str,
    calculation_task_id: str,
) -> list[dict]:
    """
    Get available timeslots for a warehouse.
    calculationTaskId is obtained from draft state after warehouse is set.
    """
    resp = await _fetch(target_id, f"{BFF_BASE}/v3/get-timeslots", {
        "companyId": COMPANY_ID,
        "draftId": draft_id,
        "storageWarehouses": [{"storageWarehouseId": warehouse_id}],
        "calculationTaskId": calculation_task_id,
    })
    return resp.get("timeslots", [])


# ─── high-level scan ─────────────────────────────────────────────────────────

async def scan_clusters_for_sc(
    draft_id: str | None = None,
    target_id: str | None = None,
    types: list[str] | None = None,
) -> dict:
    """
    High-level: for each cluster get top SC warehouses + nearest timeslot.

    Returns {
      "draftId": str,
      "clusters": [{
        "clusterId": str,
        "clusterName": str,
        "shipmentInfoId": str,
        "bundleId": str,
        "warehouses": [{dropOffPointId, name, nearestTimeslotLocal, ...}]
      }]
    }
    """
    tid = target_id or await find_seller_tab()

    if not draft_id:
        draft_id = await create_draft(tid)

    clusters = await get_clusters(tid, draft_id)
    results = []

    for cluster in clusters:
        cid = cluster["macrolocalClusterId"]
        try:
            warehouses = await get_sc_warehouses(tid, draft_id, cid, types)
        except Exception as e:
            warehouses = []
            error = str(e)
        else:
            error = None

        results.append({
            "clusterId": cid,
            "clusterName": cluster.get("name", ""),
            "shipmentInfoId": cluster.get("shipmentInfoId", ""),
            "bundleId": cluster.get("bundleId", ""),
            "storageWarehouseCount": cluster.get("storageWarehouseCount", 0),
            "warehouses": warehouses,
            "error": error,
        })

    return {"draftId": draft_id, "clusters": results}


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    async def main():
        print("Scanning clusters for SC warehouses...")
        result = await scan_clusters_for_sc()
        draft_id = result["draftId"]
        print(f"Draft: {draft_id}\n")

        fmt = "{:<35} {:<25} {:<12}"
        print(fmt.format("Кластер", "Склад (СЦ)", "Ближайший слот"))
        print("-" * 75)

        for c in result["clusters"]:
            if c["error"]:
                print(fmt.format(c["clusterName"][:34], "ОШИБКА", c["error"][:20]))
                continue
            if not c["warehouses"]:
                print(fmt.format(c["clusterName"][:34], "нет СЦ", "—"))
                continue
            top = c["warehouses"][0]
            slot = top.get("nearestTimeslotLocal", "")[:16].replace("T", " ")
            print(fmt.format(c["clusterName"][:34], top.get("name", "?")[:24], slot))

    asyncio.run(main())
