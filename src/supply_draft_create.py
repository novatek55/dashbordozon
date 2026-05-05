from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from src.supply_create_from_plan import create_draft_flexible, find_supply_orders_tab


async def run(args: argparse.Namespace) -> None:
    target_id = await find_supply_orders_tab()
    draft_id = await create_draft_flexible(target_id)
    payload = {
        "draftId": draft_id,
        "draftUrl": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Draft: {draft_id}")
    print(f"URL:   {payload['draftUrl']}")
    print(f"Saved: {out}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create Ozon multi-cluster supply draft only.")
    p.add_argument("--output", default="exports/supply_draft_created.json")
    return p.parse_args()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()

