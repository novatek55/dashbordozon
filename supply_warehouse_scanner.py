"""
Сканер доступности складов для поставки (cross-dock).

Работает через Chrome CDP — запросы идут от имени браузера с реальными куками,
что исключает блокировку антибот-системой Ozon.

Запуск:
    python supply_warehouse_scanner.py --draft-id 95786734
    python supply_warehouse_scanner.py --draft-id 95786734 --verbose
"""

import asyncio
import argparse
import json
import random
import sys

# Windows: гарантировать UTF-8 вывод (данные API могут содержать Unicode-символы)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from datetime import datetime
from typing import Optional

from src.chrome_browser import OzonBrowser, bff_fetch, COMPANY_ID

BFF_BASE = "/api/supplier-drafts/bff"

WAREHOUSE_TYPE_LABELS = {
    "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER": "XAБ/CЦ",
    "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK": "КД",
    "DROP_OFF_POINT_TYPE_V2_ORDERS_RECEIVING_POINT": "ПРЗ",
    "DROP_OFF_POINT_TYPE_V2_EXTERNAL_ORDERS_RECEIVING_POINT": "вн.ПРЗ",
    "DROP_OFF_POINT_TYPE_V2_DELIVERY_POINT": "ПВЗ",
    "DROP_OFF_POINT_TYPE_V2_SELLER_WAREHOUSE": "СкладПродавца",
}

CLUSTER_MOSCOW = "4007"


# ─── API через браузер ────────────────────────────────────────────────────────

async def get_alternative_drop_off_points(
    page,
    draft_id: str,
    company_id: int,
    cluster_ids: list = None,
) -> list:
    """Получает список всех альтернативных точек сдачи (через Chrome)."""
    if cluster_ids is None:
        cluster_ids = [CLUSTER_MOSCOW]

    body = {
        "draftId": draft_id,
        "companyId": company_id,
        "cargoType": "CARGO_TYPE_BOX_AND_PALETTE",
        "withoutCalculation": {
            "allowedDropOffPointTypes": [
                "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER",
                "DROP_OFF_POINT_TYPE_V2_ORDERS_RECEIVING_POINT",
                "DROP_OFF_POINT_TYPE_V2_EXTERNAL_ORDERS_RECEIVING_POINT",
                "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK",
            ],
            "macrolocalClusterIds": cluster_ids,
        },
    }
    data = await bff_fetch(page, f"{BFF_BASE}/v1/get-alternative-drop-off-points", body)
    return data.get("alternativeDropOffPoint", [])


async def get_warehouse_availability(
    page,
    draft_id: str,
    warehouse_id: str,
    warehouse_type: str,
    cluster_id: str = CLUSTER_MOSCOW,
) -> dict:
    """Получает слоты для конкретного склада (через Chrome)."""
    body = {
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
    }
    try:
        return await bff_fetch(page, f"{BFF_BASE}/v1/drop-off-point-availability-for-multi-cluster-draft", body)
    except RuntimeError as e:
        return {"error": str(e)}


# ─── форматирование ──────────────────────────────────────────────────────────

def format_slot(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        msk_offset = 3 * 3600
        ts = dt.timestamp() + msk_offset
        local = datetime.utcfromtimestamp(ts)
        hour = local.hour
        flag = " (HЧ)" if hour >= 20 or hour < 7 else ""
        return local.strftime("%d.%m %H:%M") + flag
    except Exception:
        return iso_str


def slot_is_night(iso_str: Optional[str]) -> bool:
    if not iso_str:
        return False
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        ts = dt.timestamp() + 3 * 3600
        local = datetime.utcfromtimestamp(ts)
        return local.hour >= 20 or local.hour < 7
    except Exception:
        return False


def extract_all_slots(availability_response: dict) -> list:
    slots = []
    for key in ("timeslots", "slots", "availableTimeslots", "dropOffTimeslots"):
        if key in availability_response:
            raw_slots = availability_response[key]
            if isinstance(raw_slots, list):
                for s in raw_slots:
                    start = s.get("start") or s.get("from") or s.get("startTime")
                    end = s.get("end") or s.get("to") or s.get("endTime")
                    if start:
                        slots.append({"start": start, "end": end})
                return slots
    return [{"raw": availability_response}]


def print_overview_table(warehouses: list, availabilities: dict):
    print("\n" + "=" * 100)
    print(f"{'N':>3}  {'Склад':<35} {'Тип':<8} {'Км':>5}  {'Ближайший слот':>14}  {'Все доступные слоты'}")
    print("=" * 100)

    day_count = 0
    night_count = 0
    unavail_count = 0

    for i, wh in enumerate(warehouses, 1):
        name = wh.get("name", "-")
        wh_type = WAREHOUSE_TYPE_LABELS.get(wh.get("dropOffPointType", ""), wh.get("dropOffPointType", ""))
        distance = wh.get("distanceKilometers", 0)
        nearest = wh.get("nearestTimeslotLocal")
        nearest_str = format_slot(nearest)
        is_night = slot_is_night(nearest)

        wh_id = wh.get("dropOffPointId")
        avail = availabilities.get(wh_id, {})
        slots = extract_all_slots(avail)

        if slots and "raw" not in slots[0]:
            all_slots_str = "  |  ".join(format_slot(s.get("start")) for s in slots[:5])
            if len(slots) > 5:
                all_slots_str += f"  (+{len(slots)-5})"
        elif "error" in avail:
            all_slots_str = f"ОШИБКА: {avail['error'][:40]}"
        elif not nearest:
            all_slots_str = "недоступен"
            unavail_count += 1
        else:
            all_slots_str = "(нет детализации)"

        night_marker = " (!)НЧ" if is_night else ""
        if nearest:
            if is_night:
                night_count += 1
            else:
                day_count += 1

        print(f"{i:>3}. {name:<35} {wh_type:<8} {distance:>5.1f}  {nearest_str:>14}{night_marker}  {all_slots_str}")

    print("=" * 100)
    print(f"Итого: {len(warehouses)} складов  |  дневных: {day_count}  |  ночных(!): {night_count}  |  недоступных: {unavail_count}")
    print()


# ─── основной скан ────────────────────────────────────────────────────────────

async def scan(draft_id: str, company_id: int, verbose: bool = False):
    async with OzonBrowser() as page:
        # Навигация на страницу черновика (как настоящий пользователь)
        draft_url = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"
        print(f"[0/2] Открываю черновик {draft_id}...")
        await page.goto(draft_url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(1.5, 3.0))

        # Шаг 1: получаем все склады региона
        print(f"[1/2] Получаю список складов...")
        warehouses = await get_alternative_drop_off_points(page, draft_id, company_id)
        print(f"      Найдено складов: {len(warehouses)}")

        if not warehouses:
            print("Склады не найдены. Проверьте draft_id.")
            return

        # Шаг 2: детализация по каждому складу (с человеческими задержками)
        print(f"[2/2] Проверяю доступность по каждому складу...")
        availabilities = {}
        for idx, wh in enumerate(warehouses):
            wh_id = wh.get("dropOffPointId")
            wh_type = wh.get("dropOffPointType", "")
            wh_name = wh.get("name", wh_id)
            print(f"      -> {wh_name} ...", end="", flush=True)

            avail = await get_warehouse_availability(page, draft_id, wh_id, wh_type)
            availabilities[wh_id] = avail

            if verbose:
                print(f"\n         raw: {json.dumps(avail, ensure_ascii=False)[:200]}")
            else:
                slots = extract_all_slots(avail)
                if "error" in avail:
                    print(f" ОШИБКА")
                elif slots and "raw" not in slots[0]:
                    print(f" {len(slots)} слотов")
                else:
                    print(f" (ответ получен)")

            # Случайная задержка 1.5-3.5 сек между запросами
            if idx < len(warehouses) - 1:
                delay = random.uniform(1.5, 3.5)
                await asyncio.sleep(delay)

        # Вывод таблицы
        print_overview_table(warehouses, availabilities)

        # Сохранить raw ответы
        output_file = f"supply_scan_{draft_id}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(
                {"warehouses": warehouses, "availabilities": availabilities},
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"Raw данные сохранены: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Сканер доступности складов Ozon (через Chrome)")
    parser.add_argument("--draft-id", required=True, help="ID черновика поставки (из URL)")
    parser.add_argument("--company-id", type=int, default=COMPANY_ID, help="ID компании")
    parser.add_argument("--verbose", action="store_true", help="Показывать raw ответы API")
    args = parser.parse_args()

    print(f"Ozon Supply Warehouse Scanner (Chrome CDP)")
    print(f"Draft ID: {args.draft_id}  |  Company ID: {args.company_id}")
    print()

    asyncio.run(scan(args.draft_id, args.company_id, verbose=args.verbose))


if __name__ == "__main__":
    main()
