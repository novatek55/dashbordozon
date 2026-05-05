"""
Создание черновика поставки через Ozon Seller API напрямую.

Эндпоинт: POST /v1/draft/multi-cluster/create
Лимиты: 2 раза в минуту, 50 раз в час, 500 раз в день.

Скрипт:
1. Получает план поставки с дашборда (localhost:8088)
2. Получает список кластеров /v1/cluster/list
3. Создаёт мульти-кластерный черновик /v1/draft/multi-cluster/create
4. Опрашивает статус /v2/draft/create/info — получает доступные склады
"""

import asyncio
import json
import os
import sys
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

# ── Конфигурация ──────────────────────────────────────────────
DASHBOARD_URL = "http://127.0.0.1:8088"
OZON_API_BASE = "https://api-seller.ozon.ru"

MACROLOCAL_FALLBACKS: Dict[str, int] = {
    "москва, мо и дальние регионы": 4039,
    "москва и мо": 4039,
    "санкт петербург и сзо": 4007,
    "санкт петербург": 4007,
    "казань": 4041,
    "краснодар": 4065,
    "красноярск": 4043,
    "новосибирск": 4067,
    "екатеринбург": 4066,
    "самара": 4042,
    "саратов": 4049,
    "уфа": 4040,
    "ярославль": 4051,
    "тверь": 4072,
    "воронеж": 4036,
    "пермь": 4070,
    "омск": 4068,
    "оренбург": 4069,
    "ростов": 4071,
    "тюмень": 4046,
}


def _load_env() -> Tuple[str, str, int, int]:
    """Загрузить Client-Id, Api-Key и warehouse IDs из .env."""
    env_path = Path(__file__).parent / ".env"
    env_vars: Dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"').strip("'")

    client_id = (
        os.getenv("OZON_CLIENT_ID")
        or env_vars.get("OZON_CLIENT_ID", "")
    )
    api_key = (
        os.getenv("OZON_SUPPLY_API_KEY")
        or os.getenv("OZON_API_KEY")
        or env_vars.get("OZON_SUPPLY_API_KEY", "")
        or env_vars.get("OZON_API_KEY", "")
    )
    seller_wh = int(
        os.getenv("OZON_CROSSDOCK_SELLER_WAREHOUSE_ID")
        or env_vars.get("OZON_CROSSDOCK_SELLER_WAREHOUSE_ID", "23785825652000")
    )
    dropoff_wh = int(
        os.getenv("OZON_CROSSDOCK_DROPOFF_WAREHOUSE_ID")
        or env_vars.get("OZON_CROSSDOCK_DROPOFF_WAREHOUSE_ID", "23969023230000")
    )
    return client_id, api_key, seller_wh, dropoff_wh


def _normalize(name: str) -> str:
    n = name.strip().lower().replace("ё", "е")
    n = n.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", n)


async def ozon_post(
    session: aiohttp.ClientSession,
    endpoint: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
) -> Tuple[int, Dict[str, Any]]:
    """POST к api-seller.ozon.ru с retry при 429."""
    url = f"{OZON_API_BASE}{endpoint}"
    for attempt in range(3):
        async with session.post(url, headers=headers, json=body) as resp:
            status = resp.status
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                data = {"raw": text[:500]}
            if status == 429:
                retry_after = int(resp.headers.get("Retry-After", "35"))
                print(f"  ⏳ Rate limit (429), ждём {retry_after}с...")
                await asyncio.sleep(retry_after)
                continue
            return status, data
    return status, data


async def get_supply_plan_from_dashboard(
    session: aiohttp.ClientSession,
) -> List[Dict[str, Any]]:
    """Получить план поставки с дашборда."""
    async with session.get(f"{DASHBOARD_URL}/api/supply-plan") as resp:
        data = await resp.json()
    items = data.get("items", [])
    print(f"📦 Получено {len(items)} артикулов из плана поставки")
    return items


def extract_clusters(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Извлечь кластеры и SKU из плана поставки (как _extract_supply_clusters)."""
    by_cluster: Dict[str, Dict[str, Any]] = {}

    for item in items:
        item_sku = item.get("sku")
        if item_sku is not None:
            item_sku = int(item_sku)
        details = item.get("details") or []
        if not isinstance(details, list):
            continue

        for detail in details:
            if not isinstance(detail, dict):
                continue
            allocated = int(detail.get("allocated_supply") or 0)
            if allocated <= 0:
                continue

            cluster_name = (
                detail.get("cluster_name") or detail.get("warehouse_name") or ""
            )
            cluster_name = str(cluster_name).strip()
            if not cluster_name:
                continue

            sku = int(detail.get("sku") or 0) or item_sku
            if not sku:
                continue

            bucket = by_cluster.setdefault(
                cluster_name,
                {"cluster_name": cluster_name, "allocated_total": 0, "skus": set(), "sku_qty": {}},
            )
            bucket["allocated_total"] += allocated
            bucket["skus"].add(sku)
            bucket["sku_qty"][sku] = bucket["sku_qty"].get(sku, 0) + allocated

    result = []
    for name, raw in by_cluster.items():
        sku_list = sorted(raw["skus"])
        if not sku_list:
            continue
        result.append({
            "cluster_name": name,
            "allocated_total": raw["allocated_total"],
            "skus": sku_list,
            "sku_qty": raw["sku_qty"],
        })
    return result


def resolve_macrolocal(cluster_name: str, api_clusters: Dict[str, int]) -> Optional[int]:
    """Resolve macrolocal_cluster_id по имени."""
    if cluster_name in api_clusters:
        return api_clusters[cluster_name]
    norm = _normalize(cluster_name)
    for api_name, mid in api_clusters.items():
        if _normalize(api_name) == norm:
            return mid
    # fallback по таблице
    return MACROLOCAL_FALLBACKS.get(norm)


async def main():
    client_id, api_key, seller_wh, dropoff_wh = _load_env()
    if not client_id or not api_key:
        print("❌ Не найдены OZON_CLIENT_ID / OZON_API_KEY в .env")
        sys.exit(1)

    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:

        # 1. Получить план поставки
        items = await get_supply_plan_from_dashboard(session)
        clusters = extract_clusters(items)
        if not clusters:
            print("⚠️  Нет товаров с allocated_supply > 0")
            return

        print(f"\n📋 Кластеры для поставки ({len(clusters)}):")
        for c in clusters:
            print(f"   {c['cluster_name']}: {len(c['skus'])} SKU, итого {c['allocated_total']} шт")
            for sku, qty in sorted(c["sku_qty"].items()):
                print(f"      SKU {sku}: {qty} шт")

        # 2. Получить список кластеров Ozon
        print("\n🔍 Запрашиваю /v1/cluster/list...")
        cl_status, cl_data = await ozon_post(
            session, "/v1/cluster/list", headers, {"cluster_type": 1}
        )
        if cl_status != 200:
            print(f"❌ /v1/cluster/list вернул {cl_status}: {cl_data}")
            return

        api_clusters: Dict[str, int] = {}
        for cl in cl_data.get("clusters", []):
            name = str(cl.get("name", "")).strip()
            mid = int(cl.get("macrolocal_cluster_id", 0))
            if name and mid:
                api_clusters[name] = mid
        print(f"   Доступно кластеров: {len(api_clusters)}")

        # 3. Подготовить данные для multi-cluster/create
        clusters_info = []
        has_crossdock = False
        for c in clusters:
            mid = resolve_macrolocal(c["cluster_name"], api_clusters)
            if not mid:
                print(f"⚠️  Не нашёл macrolocal для '{c['cluster_name']}', пропускаю")
                continue

            norm = _normalize(c["cluster_name"])
            is_direct = "моск" in norm
            if not is_direct:
                has_crossdock = True

            # Все SKU с количествами
            items_list = [
                {"sku": sku, "quantity": qty}
                for sku, qty in sorted(c["sku_qty"].items())
            ]
            clusters_info.append({
                "macrolocal_cluster_id": mid,
                "items": items_list,
            })
            supply_type = "DIRECT" if is_direct else "CROSS_DOCK"
            print(f"   ✓ {c['cluster_name']} → macrolocal={mid}, {supply_type}, {len(items_list)} SKU")

        if not clusters_info:
            print("❌ Ни один кластер не удалось сопоставить")
            return

        body: Dict[str, Any] = {
            "clusters_info": clusters_info,
            "deletion_sku_mode": "PARTIAL",
        }

        # Для CROSS_DOCK обязательна delivery_info
        if has_crossdock:
            body["delivery_info"] = {
                "type": "DROPOFF",
                "seller_warehouse_id": seller_wh,
                "drop_off_warehouse": {
                    "warehouse_id": dropoff_wh,
                    "warehouse_type": "DELIVERY_POINT",
                },
            }
            print(f"\n📍 delivery_info: seller_wh={seller_wh}, dropoff_wh={dropoff_wh}")

        # 4. Создать черновик
        print("\n🚀 POST /v1/draft/multi-cluster/create ...")
        print(f"   Body: {json.dumps(body, ensure_ascii=False, indent=2)}")

        confirm = input("\n▶ Создать черновик? (y/n): ").strip().lower()
        if confirm != "y":
            print("Отменено.")
            return

        status, data = await ozon_post(
            session, "/v1/draft/multi-cluster/create", headers, body
        )
        print(f"   Статус: {status}")
        print(f"   Ответ: {json.dumps(data, ensure_ascii=False, indent=2)}")

        draft_id = int(data.get("draft_id") or 0)
        if status != 200 or draft_id <= 0:
            print(f"❌ Не удалось создать черновик")
            return

        draft_url = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"
        print(f"\n✅ Черновик создан: {draft_id}")
        print(f"   🔗 {draft_url}")

        # 5. Опросить статус и доступные склады
        print("\n⏳ Запрашиваю /v2/draft/create/info (ждём готовности)...")
        for attempt in range(10):
            info_status, info_data = await ozon_post(
                session, "/v2/draft/create/info", headers, {"draft_id": draft_id}
            )
            draft_status = info_data.get("status", "")
            if info_status == 200 and draft_status != "IN_PROGRESS":
                break
            print(f"   Попытка {attempt + 1}: status={draft_status}, ждём 2с...")
            await asyncio.sleep(2.0)

        print(f"\n📊 Статус черновика: {draft_status}")

        # 6. Вывести доступные склады по кластерам
        info_clusters = info_data.get("clusters", []) or []
        for ic in info_clusters:
            cid = ic.get("macrolocal_cluster_id")
            print(f"\n   🏭 Кластер macrolocal={cid}:")
            warehouses = ic.get("warehouses", []) or []
            for wh in warehouses:
                avail = wh.get("availability_status", {})
                state = avail.get("state", "?")
                storage = wh.get("storage_warehouse", {})
                wh_name = storage.get("name", "?")
                wh_id = storage.get("warehouse_id", "?")
                wh_addr = storage.get("address", "")
                rank = wh.get("total_rank", "?")
                invalid = avail.get("invalid_reason", "")
                marker = "✅" if state in ("FULL_AVAILABLE", "AVAILABLE") else "❌"
                print(f"      {marker} {wh_name} (id={wh_id}, rank={rank}) — {state}")
                if wh_addr:
                    print(f"         📍 {wh_addr}")
                if invalid:
                    print(f"         ⚠ {invalid}")

        # Сохранить полный ответ
        out_path = Path(__file__).parent / "exports" / f"draft_{draft_id}_create_info.json"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(
            json.dumps(info_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n💾 Полный ответ сохранён: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
