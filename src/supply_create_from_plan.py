from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import aiohttp

from src.palletization import supply_bff


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip().replace(",", ".")
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def load_cluster_items_from_plan(plan_path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """
    Build mapping:
      cluster_name -> sku_str -> {"quantity": int, "offer_id": str}

    Supported input:
      exports/api_supply_plan_latest.json
    """
    data = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    items = data.get("items") or []
    cluster_map: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for item in items:
        offer_id = str(item.get("offer_id") or "").strip()
        details = item.get("details") or []
        for d in details:
            cluster_name = str(d.get("cluster_name") or "").strip()
            sku = str(d.get("sku") or "").strip()
            qty = _to_int(d.get("allocated_supply"), 0)
            if not cluster_name or not sku or qty <= 0:
                continue
            entry = cluster_map[cluster_name].get(sku)
            if entry:
                entry["quantity"] += qty
            else:
                cluster_map[cluster_name][sku] = {"quantity": qty, "offer_id": offer_id}

    return cluster_map


async def find_supply_orders_tab() -> str:
    relay = supply_bff.RELAY_HTTP
    token = supply_bff.RELAY_TOKEN
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{relay}/json/list?token={token}") as resp:
            tabs = await resp.json()
    for t in tabs:
        url = str(t.get("url", ""))
        if "seller.ozon.ru/app/supply/orders" in url and "signin" not in url:
            return str(t["id"])
    return await supply_bff.find_seller_tab()


async def get_quant_for_sku(
    target_id: str,
    draft_id: str,
    cluster_id: str,
    bundle_id: str,
    sku: str,
) -> int:
    body = {
        "editingBundleId": bundle_id,
        "companyId": supply_bff.COMPANY_ID,
        "macrolocalClusterId": cluster_id,
        "draftId": draft_id,
        "searchString": sku,
    }
    try:
        resp = await supply_bff._fetch(  # noqa: SLF001
            target_id, "/api/supplier-drafts/api/v1/get-assortment-for-multi-cluster-draft", body
        )
    except Exception:
        return 1
    for it in resp.get("items") or []:
        if str(it.get("sku") or "") == sku:
            return max(_to_int(it.get("quant"), 1), 1)
    return 1


def normalize_quantity(qty: int, quant: int) -> int:
    if quant <= 1:
        return qty
    return int(math.ceil(qty / quant) * quant)


async def upsert_items_for_cluster(
    target_id: str,
    bundle_id: str,
    items: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    body = {
        "marketplaceCompanyId": supply_bff.COMPANY_ID,
        "supplierProductBundleId": bundle_id,
        "items": items,
    }
    if dry_run:
        return {"status": "dry_run", "items_count": len(items)}
    resp = await supply_bff._fetch(  # noqa: SLF001
        target_id, "/api/supplier-product-bundles-bff/v1/draft/upsert-items", body
    )
    return {"status": "ok", "response": resp}


async def edit_cluster_assortment(target_id: str, draft_id: str, cluster_id: str) -> dict[str, Any]:
    return await supply_bff._fetch(  # noqa: SLF001
        target_id,
        "/api/supplier-drafts/bff/v1/edit-cluster-assortment",
        {"draftId": draft_id, "macrolocalClusterId": cluster_id, "companyId": supply_bff.COMPANY_ID},
    )


async def save_cluster_assortment(
    target_id: str, draft_id: str, cluster_id: str, editing_bundle_id: str
) -> dict[str, Any]:
    return await supply_bff._fetch(  # noqa: SLF001
        target_id,
        "/api/supplier-drafts/bff/v1/save-cluster-assortment",
        {
            "macrolocalClusterId": cluster_id,
            "draftId": draft_id,
            "companyId": supply_bff.COMPANY_ID,
            "editingBundleId": editing_bundle_id,
        },
    )


async def create_draft_flexible(target_id: str) -> str:
    resp = await supply_bff._fetch(  # noqa: SLF001
        target_id,
        "/api/supplier-drafts/api/v3/create",
        {
            "companyId": supply_bff.COMPANY_ID,
            "origin": "web:seller",
            "multiCluster": {"allClusters": {}},
        },
    )
    if isinstance(resp, dict):
        for key in ("draftId", "draft_id"):
            if key in resp and resp.get(key):
                return str(resp[key])
        success = resp.get("success") if isinstance(resp.get("success"), dict) else {}
        for key in ("draftId", "draft_id"):
            if key in success and success.get(key):
                return str(success[key])
        result = resp.get("result") if isinstance(resp.get("result"), dict) else {}
        for key in ("draftId", "draft_id"):
            if key in result and result.get(key):
                return str(result[key])
    raise RuntimeError(f"Unexpected create draft response: {resp}")


async def run(args: argparse.Namespace) -> None:
    cluster_items = load_cluster_items_from_plan(Path(args.plan_json))
    if not cluster_items:
        raise RuntimeError("No allocated_supply > 0 found in plan.")

    target_id = await find_supply_orders_tab()
    draft_id = args.draft_id or await create_draft_flexible(target_id)
    clusters = await supply_bff.get_clusters(target_id, draft_id)

    by_name = {str(c.get("name") or ""): c for c in clusters}
    by_id = {str(c.get("macrolocalClusterId") or ""): c for c in clusters}

    summary: dict[str, Any] = {
        "draftId": draft_id,
        "draftUrl": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}",
        "dryRun": args.dry_run,
        "clusters": [],
    }

    for input_cluster, sku_map in cluster_items.items():
        cluster = by_name.get(input_cluster) or by_id.get(input_cluster)
        if not cluster:
            summary["clusters"].append(
                {"inputCluster": input_cluster, "status": "cluster_not_found", "items": len(sku_map)}
            )
            continue

        cluster_id = str(cluster.get("macrolocalClusterId"))
        cluster_name = str(cluster.get("name") or "")
        bundle_id = str(cluster.get("bundleId") or "")
        if not bundle_id:
            summary["clusters"].append(
                {
                    "inputCluster": input_cluster,
                    "clusterId": cluster_id,
                    "clusterName": cluster_name,
                    "status": "missing_bundle_id",
                }
            )
            continue

        working_bundle_id = bundle_id
        if not args.dry_run:
            await supply_bff.select_cluster(target_id, draft_id, cluster_id, True)
            edit_resp = await edit_cluster_assortment(target_id, draft_id, cluster_id)
            editing_bundle_id = str((edit_resp or {}).get("editingBundleId") or "")
            if editing_bundle_id:
                working_bundle_id = editing_bundle_id

        upsert_items: list[dict[str, Any]] = []
        adjusted_rows: list[dict[str, Any]] = []
        for sku, row in sku_map.items():
            qty = _to_int(row.get("quantity"), 0)
            if qty <= 0:
                continue
            quant = await get_quant_for_sku(target_id, draft_id, cluster_id, working_bundle_id, sku)
            adj_qty = normalize_quantity(qty, quant)
            upsert_items.append({"sku": sku, "quant": quant, "quantity": adj_qty})
            adjusted_rows.append(
                {
                    "sku": sku,
                    "offer_id": row.get("offer_id"),
                    "qtyInput": qty,
                    "quant": quant,
                    "qtySent": adj_qty,
                }
            )

        result = await upsert_items_for_cluster(target_id, working_bundle_id, upsert_items, args.dry_run)
        if not args.dry_run and result.get("status") == "ok":
            await save_cluster_assortment(target_id, draft_id, cluster_id, working_bundle_id)
        summary["clusters"].append(
            {
                "inputCluster": input_cluster,
                "clusterId": cluster_id,
                "clusterName": cluster_name,
                "bundleId": bundle_id,
                "workingBundleId": working_bundle_id,
                "status": result.get("status"),
                "itemsCount": len(upsert_items),
                "items": adjusted_rows,
            }
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Draft: {summary['draftId']}")
    print(f"URL:   {summary['draftUrl']}")
    print(f"Saved: {out}")
    ok = [c for c in summary["clusters"] if c.get("status") in {"ok", "dry_run"}]
    print(f"Clusters processed: {len(ok)}/{len(summary['clusters'])}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Create Ozon multi-cluster draft and fill items from supply plan."
    )
    p.add_argument("--plan-json", default="exports/api_supply_plan_latest.json")
    p.add_argument("--draft-id", default="", help="Use existing draft id; otherwise create new")
    p.add_argument("--dry-run", action="store_true", help="Do not write to Ozon")
    p.add_argument("--output", default="exports/supply_create_from_plan_result.json")
    return p.parse_args()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
