from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from src.palletization import supply_bff


def is_moscow_cluster(cluster: dict[str, Any]) -> bool:
    cid = str(cluster.get("macrolocalClusterId") or "")
    name = str(cluster.get("name") or "").lower()
    return cid == "4039" or "москва" in name


async def set_delivery_type(
    target_id: str, draft_id: str, cluster_id: str, mode: str
) -> dict[str, Any]:
    if mode == "direct":
        body = {
            "companyId": supply_bff.COMPANY_ID,
            "draftId": draft_id,
            "shipmentInfo": {"direct": {"macrolocalClusterId": cluster_id}},
        }
    elif mode == "crossdock":
        body = {
            "companyId": supply_bff.COMPANY_ID,
            "draftId": draft_id,
            "shipmentInfo": {
                "crossDock": {
                    "macrolocalClusterId": cluster_id,
                    "dropOffFlow": {"self": {}},
                }
            },
        }
    else:
        raise ValueError(f"Unsupported mode: {mode}")
    return await supply_bff._fetch(  # noqa: SLF001
        target_id, "/api/supplier-drafts/api/v1/update-shipment-info", body
    )


async def run(args: argparse.Namespace) -> None:
    target_id = await supply_bff.find_seller_tab()
    draft = await supply_bff.get_draft(target_id, args.draft_id)
    clusters = ((draft.get("draft") or {}).get("multiCluster") or {}).get("clusterInfos") or []
    selected = [c for c in clusters if bool(c.get("isSelected"))]

    results: list[dict[str, Any]] = []
    for c in selected:
        cid = str(c.get("macrolocalClusterId") or "")
        cname = str(c.get("name") or "")
        mode = "direct" if is_moscow_cluster(c) else "crossdock"
        if args.dry_run:
            resp = {"status": "dry_run"}
        else:
            resp = await set_delivery_type(target_id, args.draft_id, cid, mode)
        results.append(
            {
                "clusterId": cid,
                "clusterName": cname,
                "mode": mode,
                "response": resp,
            }
        )

    out = {
        "draftId": args.draft_id,
        "totalSelectedClusters": len(selected),
        "results": results,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Draft: {args.draft_id}")
    print(f"Selected clusters: {len(selected)}")
    print(f"Saved: {out_path}")
    for r in results:
        print(f"- {r['clusterName']} ({r['clusterId']}): {r['mode']}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Set delivery type per selected cluster: Moscow=direct, others=crossdock."
    )
    p.add_argument("--draft-id", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--output", default="exports/supply_set_delivery_types_result.json")
    return p.parse_args()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()

