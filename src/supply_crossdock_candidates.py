from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from src.palletization import supply_bff


ALLOWED_TYPES = [
    "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER",
    "DROP_OFF_POINT_TYPE_V2_ORDERS_RECEIVING_POINT",
    "DROP_OFF_POINT_TYPE_V2_DELIVERY_POINT",
    "DROP_OFF_POINT_TYPE_V2_EXTERNAL_ORDERS_RECEIVING_POINT",
    "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK",
]


def is_moscow_cluster(cluster: dict[str, Any]) -> bool:
    cid = str(cluster.get("macrolocalClusterId") or "")
    name = str(cluster.get("name") or "").lower()
    return cid == "4039" or "москва" in name


async def get_alt_dropoff_points(
    target_id: str,
    draft_id: str,
    cluster_id: str,
    current_drop_off_point_id: str,
) -> list[dict[str, Any]]:
    body = {
        "draftId": draft_id,
        "companyId": supply_bff.COMPANY_ID,
        "cargoType": "CARGO_TYPE_BOX_ONLY",
        "currentDropOffPointId": current_drop_off_point_id,
        "withoutCalculation": {
            "allowedDropOffPointTypes": ALLOWED_TYPES,
            "macrolocalClusterIds": [cluster_id],
        },
    }
    resp = await supply_bff._fetch(  # noqa: SLF001
        target_id, "/api/supplier-drafts/bff/v1/get-alternative-drop-off-points", body
    )
    return resp.get("alternativeDropOffPoint") or []


async def check_availability(
    target_id: str,
    draft_id: str,
    cluster_id: str,
    point_id: str,
    point_type: str,
) -> dict[str, Any]:
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
    return await supply_bff._fetch(  # noqa: SLF001
        target_id, "/api/supplier-drafts/bff/v1/drop-off-point-availability-for-multi-cluster-draft", body
    )


async def save_selected_warehouse(
    target_id: str,
    draft_id: str,
    cluster_id: str,
    point_id: str,
    point_type: str,
) -> dict[str, Any]:
    body = {
        "draftId": draft_id,
        "companyId": supply_bff.COMPANY_ID,
        "shipmentInfo": {
            "crossDock": {
                "dropOffFlow": {"self": {}},
                "macrolocalClusterId": cluster_id,
                "dropOffWarehouseInfo": {
                    "dropOffWarehouseId": int(point_id),
                    "dropOffWarehouseType": point_type,
                },
            }
        },
    }
    return await supply_bff._fetch(  # noqa: SLF001
        target_id, "/api/supplier-drafts/api/v1/update-shipment-info", body
    )


def pick_best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    accepted = [c for c in candidates if bool(c.get("accept"))]
    if not accepted:
        return None

    def key(c: dict[str, Any]) -> tuple[str, float]:
        src = str(c.get("source") or "")
        ptype = str(c.get("dropOffPointType") or "")
        source_rank = 0 if src.startswith("file:") else 1
        type_rank = 0 if ptype == "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER" else 1
        ts = str(c.get("firstAvailableFrom") or "9999-12-31T23:59:59Z")
        dist_raw = c.get("distanceKilometers")
        try:
            dist = float(dist_raw) if dist_raw is not None else 1e18
        except (TypeError, ValueError):
            dist = 1e18
        return f"{source_rank}:{type_rank}:{ts}", dist

    return sorted(accepted, key=key)[0]


async def search_xdock_warehouses(target_id: str, search_text: str) -> list[dict[str, Any]]:
    expr = f"""
(async () => {{
  const url = "https://seller.ozon.ru/api/supplier/warehouses/xdock/supply-warehouses?searchString=" + encodeURIComponent({json.dumps(search_text)});
  const r = await fetch(url, {{
    method: "GET",
    headers: {{
      "x-o3-app-name": "seller-ui",
      "x-o3-company-id": "{supply_bff.COMPANY_ID}",
      "x-o3-language": "ru",
      "x-o3-page-type": "supply-other"
    }}
  }});
  let data = null;
  try {{ data = await r.json(); }} catch {{}}
  return {{ status: r.status, data }};
}})()
"""
    result = await supply_bff._eval(target_id, expr)  # noqa: SLF001
    if not result or int(result.get("status") or 0) != 200:
        return []
    payload = result.get("data") or {}
    return payload.get("supplyWarehouses") or []


def collect_search_terms(args: argparse.Namespace) -> list[str]:
    terms: list[str] = []
    if args.search:
        terms.extend([t.strip() for t in args.search if t and t.strip()])
    if args.search_file:
        p = Path(args.search_file)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    terms.append(line)
    # keep order, dedupe
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def load_moscow_sc_from_file(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []

    # Expected format:
    # {"sorting_msk_mo":[{"warehouse_id":..., "name":"...", "type":"SORTING_CENTER"}, ...]}
    rows = data.get("sorting_msk_mo") if isinstance(data, dict) else None
    if isinstance(rows, list):
        for r in rows:
            wid = str(r.get("warehouse_id") or "")
            if not wid:
                continue
            out.append(
                {
                    "dropOffPointId": wid,
                    "dropOffPointType": "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER",
                    "name": r.get("name"),
                    "source": f"file:{p.name}",
                }
            )
    return out


async def run(args: argparse.Namespace) -> None:
    target_id = await supply_bff.find_seller_tab()
    draft = await supply_bff.get_draft(target_id, args.draft_id)
    clusters = ((draft.get("draft") or {}).get("multiCluster") or {}).get("clusterInfos") or []
    selected = [c for c in clusters if bool(c.get("isSelected"))]
    search_terms = collect_search_terms(args)
    file_warehouses = load_moscow_sc_from_file(args.moscow_sc_file)

    searched_warehouses: list[dict[str, Any]] = []
    if search_terms:
        seen_wh: set[str] = set()
        for term in search_terms:
            found = await search_xdock_warehouses(target_id, term)
            for w in found:
                wid = str(w.get("clearingWarehouseId") or "")
                if not wid or wid in seen_wh:
                    continue
                seen_wh.add(wid)
                searched_warehouses.append(
                    {
                        "dropOffPointId": wid,
                        "dropOffPointType": "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER",
                        "name": w.get("name"),
                        "address": w.get("address"),
                        "source": f"search:{term}",
                    }
                )

    results: list[dict[str, Any]] = []
    for c in selected:
        if is_moscow_cluster(c):
            continue
        cid = str(c.get("macrolocalClusterId") or "")
        cname = str(c.get("name") or "")
        current_id = args.default_current_dropoff_id
        points = await get_alt_dropoff_points(target_id, args.draft_id, cid, current_id)
        pool: dict[str, dict[str, Any]] = {}
        for p in points:
            pid = str(p.get("dropOffPointId") or "")
            if not pid:
                continue
            pool[pid] = dict(p)
            pool[pid]["source"] = "alternative"
        for s in searched_warehouses:
            sid = str(s.get("dropOffPointId") or "")
            if not sid:
                continue
            pool[sid] = {**pool.get(sid, {}), **s}
        for w in file_warehouses:
            wid = str(w.get("dropOffPointId") or "")
            if not wid:
                continue
            pool[wid] = {**pool.get(wid, {}), **w}

        enriched: list[dict[str, Any]] = []
        for p in pool.values():
            pid = str(p.get("dropOffPointId") or "")
            ptype = str(p.get("dropOffPointType") or "")
            av = await check_availability(target_id, args.draft_id, cid, pid, ptype)
            first = (av or {}).get("firstAvailableTimeslot") or {}
            reasons = ((av or {}).get("notAvailableResponse") or {}).get("reasonsV2") or []
            enriched.append(
                {
                    "dropOffPointId": pid,
                    "dropOffPointType": ptype,
                    "name": p.get("name"),
                    "address": p.get("address"),
                    "distanceKilometers": p.get("distanceKilometers"),
                    "nearestTimeslotLocal": p.get("nearestTimeslotLocal"),
                    "firstAvailableFrom": first.get("fromLocal"),
                    "firstAvailableTo": first.get("toLocal"),
                    "accept": not bool(reasons),
                    "notAvailableReasons": reasons,
                    "source": p.get("source"),
                }
            )
        results.append(
            {
                "clusterId": cid,
                "clusterName": cname,
                "currentDropOffPointId": current_id,
                "searchTerms": search_terms,
                "candidates": enriched,
            }
        )

    saved_count = 0
    if args.save_best:
        for r in results:
            best = pick_best_candidate(r.get("candidates") or [])
            if not best:
                r["savedSelection"] = {"saved": False, "reason": "no_accept_candidates"}
                continue
            save_resp = await save_selected_warehouse(
                target_id=target_id,
                draft_id=args.draft_id,
                cluster_id=str(r.get("clusterId") or ""),
                point_id=str(best.get("dropOffPointId") or ""),
                point_type=str(best.get("dropOffPointType") or ""),
            )
            ok = isinstance(save_resp, dict) and "success" in save_resp
            r["savedSelection"] = {
                "saved": ok,
                "dropOffPointId": best.get("dropOffPointId"),
                "dropOffPointType": best.get("dropOffPointType"),
                "name": best.get("name"),
                "firstAvailableFrom": best.get("firstAvailableFrom"),
                "firstAvailableTo": best.get("firstAvailableTo"),
                "source": best.get("source"),
            }
            if ok:
                saved_count += 1

    out = {"draftId": args.draft_id, "clusters": results}
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Draft: {args.draft_id}")
    print(f"Crossdock clusters scanned: {len(results)}")
    for r in results:
        print(f"- {r['clusterName']} ({r['clusterId']}): {len(r['candidates'])} candidates")
    if args.save_best:
        print(f"Saved selections: {saved_count}/{len(results)}")
    print(f"Saved: {out_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Get crossdock drop-off candidates per selected non-Moscow cluster.")
    p.add_argument("--draft-id", required=True)
    p.add_argument("--default-current-dropoff-id", default="22190776129000")
    p.add_argument("--search", action="append", help="Warehouse search string (repeatable), e.g. --search кавказ")
    p.add_argument("--search-file", help="Text file with warehouse search strings, one per line")
    p.add_argument(
        "--moscow-sc-file",
        default="exports/sorting_centers_supply_msk_mo.json",
        help="JSON file with Moscow sorting centers list (expects sorting_msk_mo[].warehouse_id,name)",
    )
    p.add_argument("--save-best", action="store_true", help="Save one best accepted warehouse per crossdock cluster")
    p.add_argument("--output", default="exports/supply_crossdock_candidates.json")
    return p.parse_args()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()

