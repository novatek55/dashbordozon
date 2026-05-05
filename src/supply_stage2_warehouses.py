"""
supply_stage2_warehouses.py — Этап 2: склады и окна

Для каждого выбранного кластера в черновике устанавливает способ доставки
и подбирает склад отгрузки:
  - Москва (clusterId=4039 или "москва" в названии) → прямая поставка (direct)
  - Остальные → кросс-докинг, автоматический подбор ближайшего СЦ

Транспорт: Chrome CDP через Playwright (порт 9223).
Chrome запускается автоматически, куки хранятся в exports/chrome_profile.
Relay не нужен. Черновики видны на seller.ozon.ru.

Usage:
  python -m src.supply_stage2_warehouses --draft-id 96594991 --dry-run
  python -m src.supply_stage2_warehouses --draft-id 96594991
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from src.chrome_browser import (
    OzonBrowser,
    bff_fetch,
    COMPANY_ID,
)

SC_TYPE = "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER"
ALL_TYPES = [
    "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER",
    "DROP_OFF_POINT_TYPE_V2_ORDERS_RECEIVING_POINT",
    "DROP_OFF_POINT_TYPE_V2_DELIVERY_POINT",
    "DROP_OFF_POINT_TYPE_V2_EXTERNAL_ORDERS_RECEIVING_POINT",
    "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK",
]

BFF_BASE = "/api/supplier-drafts/bff/v1"
API_BASE = "/api/supplier-drafts/api/v1"

# Типы точек отгрузки, которые интересуют (СЦ + кросс-доки)
PREFERRED_TYPES = [
    "DROP_OFF_POINT_TYPE_V2_SORTING_CENTER",
    "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK",
]

# Файл со списком СЦ Москвы/МО — используем только эти склады
_HERE = Path(__file__).resolve().parent.parent  # корень проекта
MSK_SC_FILE = _HERE / "exports" / "sorting_centers_supply_msk_mo.json"

# Склады МСК/МО для кросс-докинга (имена из UI Ozon, частичное совпадение)
# Проблемные — только в крайних случаях (низкий приоритет)
_MSK_WAREHOUSES_PREFERRED = [
    "домодедово_рфц_кросс",
    "мо_щербинка_хаб",
    "мо_внуково_2_хаб",
    "сц_рябиновая_кроссдок",
    "мск_кавказский_2_хаб",
    "мо_замоскворечье_xd",
    "мск_волгоградский_3_х",
    "мо_давыдовское_фбс",
    "мо_тсц_новая_рига",
    "петровское_рфц_кросс",
    "мск_чермянская_фбс",
    "мо_осташковский_хаб",
    "мо_осташковский_3_х",
    "мск_молжаниново_3_ха",
    "мо_тсц_никольское",
    "хоругвино_рфц_кроссдок",
    # Из viewport-сканирования:
    "жуковский_рфц_кроссдок",
    "хоругвино_кроссдок",
    "павло_слободское_кроссдок",
    "софьино_рфц_кроссдок",
    "пушкино_1_рфц_кроссдок",
    "ногинск_рфц_негабарит_кроссдок",
    "радумля_рфц_негабарит_кроссдок",
]
_MSK_WAREHOUSES_FALLBACK = [
    "мск_строгино_2_хаб",     # проблемный
    "пушкино_1_рфц_кроссдок", # проблемный
]


def _load_msk_sc_ids() -> set[str]:
    """Загружает ID складов МСК/МО из JSON-файла."""
    if not MSK_SC_FILE.exists():
        return set()
    data = json.loads(MSK_SC_FILE.read_text(encoding="utf-8"))
    rows = data.get("sorting_msk_mo") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return set()
    return {str(r.get("warehouse_id") or "") for r in rows if r.get("warehouse_id")}


def _msk_warehouse_priority(name: str) -> int:
    """
    Приоритет склада: 0 = preferred, 1 = fallback, 2 = не в списке.
    Склады с одинаковым таймслотом сортируются по приоритету.
    """
    name_lower = name.lower()
    for pat in _MSK_WAREHOUSES_PREFERRED:
        if pat in name_lower:
            return 0
    for pat in _MSK_WAREHOUSES_FALLBACK:
        if pat in name_lower:
            return 1
    return 2


def _is_msk_warehouse(name: str, address: str = "") -> bool:
    """Проверяет по имени — склад в допустимом списке МСК/МО."""
    return _msk_warehouse_priority(name) < 2


# ─── helpers ──────────────────────────────────────────────────────────────────

def is_moscow_cluster(cluster: dict[str, Any]) -> bool:
    cid = str(cluster.get("macrolocalClusterId") or "")
    name = str(cluster.get("name") or "").lower()
    return cid == "4039" or "москва" in name or "moscow" in name


def _fmt_slot(ts: Any) -> str:
    s = str(ts or "")
    return s[:16].replace("T", " ") if s else ""


# ─── BFF API ──────────────────────────────────────────────────────────────────

async def get_draft_clusters(page, draft_id: str) -> list[dict[str, Any]]:
    resp = await bff_fetch(page, f"{API_BASE.replace('v1','v4')}/get".replace("bff/v1", "api/v4"),
                           {"companyId": COMPANY_ID, "draftId": draft_id})
    return (
        (resp.get("draft") or {}).get("multiCluster", {}).get("clusterInfos") or []
    )


async def _get_draft(page, draft_id: str) -> dict[str, Any]:
    return await bff_fetch(
        page,
        "/api/supplier-drafts/api/v4/get",
        {"companyId": COMPANY_ID, "draftId": draft_id},
    )


async def get_allowed_types(
    page, draft_id: str, cluster_id: str,
) -> list[str]:
    """
    Проверяет какие типы точек разрешены для кластера.
    POST /api/supplier-drafts/bff/v1/allowed-drop-off-point-types-for-multi-cluster-draft
    Возвращает список разрешённых типов, например:
      ["DROP_OFF_POINT_TYPE_V2_SORTING_CENTER", "DROP_OFF_POINT_TYPE_V2_CROSS_DOCK"]
    """
    resp = await bff_fetch(
        page,
        f"{BFF_BASE}/allowed-drop-off-point-types-for-multi-cluster-draft",
        {
            "draftId": draft_id,
            "companyId": COMPANY_ID,
            "macrolocalClusterId": cluster_id,
        },
    )
    # Ответ: {allowedDropOffPointTypes: [{dropOffPointType, isAllowed}, ...]}
    allowed = []
    for item in resp.get("allowedDropOffPointTypes") or []:
        if item.get("isAllowed"):
            allowed.append(str(item.get("dropOffPointType") or ""))
    return [t for t in allowed if t]


async def get_drop_off_points_viewport(
    page, draft_id: str, types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Получает ВСЕ склады (СЦ + кросс-доки) через viewport-запрос.
    POST /api/supplier-drafts/bff/v4/get-drop-off-points
    Один запрос с широким viewport покрывает всю Россию/СНГ.
    """
    resp = await bff_fetch(
        page,
        "/api/supplier-drafts/bff/v4/get-drop-off-points",
        {
            "companyId": COMPANY_ID,
            "draftId": draft_id,
            "dropOffPointTypes": types or PREFERRED_TYPES,
            "byViewport": {
                "viewport": {
                    "bottomLeftPoint": {"latitude": 40.0, "longitude": 20.0},
                    "topRightPoint": {"latitude": 75.0, "longitude": 180.0},
                }
            },
        },
    )
    return resp.get("dropOffPoints") or []


async def get_warehouse_info(
    page, draft_id: str, warehouse_id: int, warehouse_type: str,
) -> dict[str, Any]:
    """
    Получает детали склада: ближайший таймслот, лимиты, расписание.
    POST /api/supplier-drafts/bff/v2/warehouse-info
    """
    return await bff_fetch(
        page,
        "/api/supplier-drafts/bff/v2/warehouse-info",
        {
            "supplierDraftId": draft_id,
            "clearingWarehouseId": warehouse_id,
            "dropOffPointTypeV2": warehouse_type,
        },
    )


async def get_sc_warehouses(page, draft_id: str, cluster_id: str) -> list[dict[str, Any]]:
    resp = await bff_fetch(
        page,
        f"{BFF_BASE}/get-alternative-drop-off-points",
        {
            "draftId": draft_id,
            "companyId": COMPANY_ID,
            "cargoType": "CARGO_TYPE_BOX_ONLY",
            "withoutCalculation": {
                "allowedDropOffPointTypes": ALL_TYPES,
                "macrolocalClusterIds": [cluster_id],
            },
        },
    )
    return resp.get("alternativeDropOffPoint") or []


async def check_warehouse_availability(
    page, draft_id: str, cluster_id: str, wh_id: str, wh_type: str
) -> dict[str, Any]:
    resp = await bff_fetch(
        page,
        f"{BFF_BASE}/drop-off-point-availability-for-multi-cluster-draft",
        {
            "draftId": draft_id,
            "shipmentInfo": {
                "crossDock": {
                    "macrolocalClusterId": cluster_id,
                    "dropOffWarehouseInfo": {
                        "dropOffWarehouseId": wh_id,
                        "dropOffWarehouseType": wh_type,
                    },
                    "dropOffFlow": {"self": {}},
                }
            },
        },
    )
    reasons = (resp.get("notAvailableResponse") or {}).get("reasonsV2") or []
    first_ts = resp.get("firstAvailableTimeslot") or {}
    return {
        "available": not bool(reasons),
        "reasons": reasons,
        "firstAvailableFrom": first_ts.get("fromLocal"),
        "firstAvailableTo": first_ts.get("toLocal"),
    }


async def set_direct_delivery(page, draft_id: str, cluster_id: str) -> dict[str, Any]:
    return await bff_fetch(
        page,
        f"{API_BASE}/update-shipment-info",
        {
            "companyId": COMPANY_ID,
            "draftId": draft_id,
            "shipmentInfo": {"direct": {"macrolocalClusterId": cluster_id}},
        },
    )


async def set_crossdock_warehouse(
    page, draft_id: str, cluster_id: str, wh_id: int, wh_type: str
) -> dict[str, Any]:
    return await bff_fetch(
        page,
        f"{API_BASE}/update-shipment-info",
        {
            "companyId": COMPANY_ID,
            "draftId": draft_id,
            "shipmentInfo": {
                "crossDock": {
                    "macrolocalClusterId": cluster_id,
                    "dropOffWarehouseInfo": {
                        "dropOffWarehouseId": wh_id,
                        "dropOffWarehouseType": wh_type,
                    },
                    "dropOffFlow": {"self": {}},
                }
            },
        },
    )


# ─── кэш warehouse-info ──────────────────────────────────────────────────────

async def _load_warehouse_infos(
    page, draft_id: str, all_points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Загружает warehouse-info для всех точек ОДИН РАЗ.
    Возвращает список обогащённых записей (id, name, type, isActive, ...).
    """
    entries: list[dict[str, Any]] = []
    for i, p in enumerate(all_points):
        wh_id_raw = p.get("dropOffPointId")
        wh_type = str(p.get("dropOffPointType") or SC_TYPE)
        try:
            wh_id = int(wh_id_raw)
        except (TypeError, ValueError):
            continue

        entry: dict[str, Any] = {
            "id": wh_id_raw,
            "type": wh_type,
            "coordinates": p.get("coordinates"),
        }
        try:
            info = await get_warehouse_info(page, draft_id, wh_id, wh_type)
            entry["name"] = info.get("name") or str(wh_id)
            entry["address"] = info.get("address")
            entry["isActive"] = info.get("isActive", False)
            entry["maxPallets"] = info.get("maxPalletCount")
            entry["maxBoxes"] = info.get("maxBoxCount")
        except Exception as e:
            entry["name"] = str(wh_id)
            entry["warehouseInfoError"] = str(e)
            entry["isActive"] = False

        # Помечаем МСК/МО приоритет
        name = str(entry.get("name") or "")
        prio = _msk_warehouse_priority(name)
        entry["mskPriority"] = prio
        entry["isMskMo"] = prio < 2

        entries.append(entry)
        if (i + 1) % 20 == 0:
            active_count = sum(1 for e in entries if e.get("isActive"))
            print(f"    ... warehouse-info: {i + 1}/{len(all_points)}, активных: {active_count}")

    active_count = sum(1 for e in entries if e.get("isActive"))
    msk_count = sum(1 for e in entries if e.get("isMskMo") and e.get("isActive"))
    print(f"    warehouse-info: {len(entries)} проверено, активных: {active_count}, МСК/МО: {msk_count}")
    return entries


async def _check_availability_for_cluster(
    page, draft_id: str, cluster_id: str,
    warehouse_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Проверяет availability каждого активного склада для конкретного кластера.
    Возвращает список записей с полями available, timeslotFrom, timeslotTo.
    """
    results: list[dict[str, Any]] = []
    active = [e for e in warehouse_entries if e.get("isActive")]

    for i, entry in enumerate(active):
        # Копируем чтобы не мутировать общий кэш
        e = dict(entry)
        wh_id_str = str(e["id"])
        wh_type = str(e.get("type") or SC_TYPE)

        try:
            av = await check_warehouse_availability(
                page, draft_id, cluster_id, wh_id_str, wh_type
            )
            e["available"] = av.get("available", False)
            e["notAvailableReasons"] = av.get("reasons", [])
            e["timeslotFrom"] = av.get("firstAvailableFrom")
            e["timeslotTo"] = av.get("firstAvailableTo")
            e["timeslotDate"] = _fmt_slot(e["timeslotFrom"])
        except Exception as ex:
            e["available"] = False
            e["availabilityError"] = str(ex)

        results.append(e)
        if (i + 1) % 20 == 0:
            avail = sum(1 for r in results if r.get("available"))
            print(f"    ... availability: {i + 1}/{len(active)}, доступных: {avail}")

    avail_count = sum(1 for r in results if r.get("available"))
    msk_avail = sum(1 for r in results if r.get("available") and r.get("isMskMo"))
    print(f"    availability: {len(active)} проверено, доступных: {avail_count}, МСК/МО: {msk_avail}")
    return results


# ─── обработка кластеров (общий склад) ───────────────────────────────────────

async def process_all_clusters(
    page, draft_id: str, clusters: list[dict[str, Any]],
    dry_run: bool, all_points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Обрабатывает все кластеры с логикой выбора общего склада.

    Алгоритм:
    1) warehouse-info один раз для всех 123 точек (кэш)
    2) Для каждого crossdock-кластера: availability → список доступных
    3) Находим общий склад МСК/МО (доступен для максимума кластеров)
    4) Если общий есть — используем его для всех
    5) Если нет — per-cluster best
    """
    # Разделяем кластеры
    direct_clusters = [c for c in clusters if is_moscow_cluster(c)]
    crossdock_clusters = [c for c in clusters if not is_moscow_cluster(c)]

    results: list[dict[str, Any]] = []

    # 1) warehouse-info один раз
    print(f"  Загрузка warehouse-info для {len(all_points)} точек...")
    wh_entries = await _load_warehouse_infos(page, draft_id, all_points)

    # 2) Availability для каждого crossdock-кластера
    # cluster_id → list[dict] (доступные склады с таймслотами)
    cluster_available: dict[str, list[dict[str, Any]]] = {}
    cluster_all: dict[str, list[dict[str, Any]]] = {}

    for c in crossdock_clusters:
        cid = str(c.get("macrolocalClusterId") or "")
        cname = str(c.get("name") or "")
        print(f"  [{cname}] проверяем availability...")
        checked = await _check_availability_for_cluster(page, draft_id, cid, wh_entries)
        cluster_all[cid] = checked
        cluster_available[cid] = [e for e in checked if e.get("available")]

    # 3) Находим общий склад МСК/МО
    common_warehouse = None
    if len(crossdock_clusters) > 1:
        common_warehouse = _find_common_warehouse(crossdock_clusters, cluster_available)
        if common_warehouse:
            print(f"  >>> Общий склад: {common_warehouse['name']} "
                  f"(доступен для {common_warehouse['_common_count']}/{len(crossdock_clusters)} кластеров)")

    # 4) Обрабатываем direct кластеры
    for c in direct_clusters:
        cid = str(c.get("macrolocalClusterId") or "")
        cname = str(c.get("name") or "")
        result: dict[str, Any] = {
            "clusterId": cid,
            "clusterName": cname,
            "deliveryType": "direct",
            "status": "pending",
        }
        if dry_run:
            result["status"] = "dry_run"
        else:
            try:
                await set_direct_delivery(page, draft_id, cid)
                result["status"] = "ok"
            except Exception as e:
                result["status"] = "error"
                result["error"] = str(e)
        results.append(result)
        icon = {"ok": "+", "dry_run": "o", "error": "x"}.get(result["status"], "?")
        print(f"  {icon} {cname} [direct]")

    # 5) Обрабатываем crossdock кластеры
    for c in crossdock_clusters:
        cid = str(c.get("macrolocalClusterId") or "")
        cname = str(c.get("name") or "")
        available = cluster_available.get(cid, [])
        all_checked = cluster_all.get(cid, [])

        result = {
            "clusterId": cid,
            "clusterName": cname,
            "deliveryType": "crossdock",
            "status": "pending",
            "candidatesChecked": len(all_checked),
            "candidates": [{k: v for k, v in e.items() if k != "infoRaw"} for e in all_checked],
        }

        # Выбираем склад: общий или лучший per-cluster
        best = None
        if common_warehouse:
            # Проверяем что общий склад доступен для этого кластера
            wh_id = str(common_warehouse["id"])
            best = next((e for e in available if str(e["id"]) == wh_id), None)

        if not best:
            # Per-cluster: лучший МСК/МО по таймслоту
            msk_available = [e for e in available if e.get("isMskMo")]
            if msk_available:
                msk_available.sort(key=lambda e: (
                    str(e.get("timeslotFrom") or "9999"),
                    e.get("mskPriority", 2),
                ))
                best = msk_available[0]
            elif available:
                available.sort(key=lambda e: str(e.get("timeslotFrom") or "9999"))
                best = available[0]

        if not available:
            result["status"] = "error"
            result["error"] = "Нет доступных складов для этого кластера"
        elif not best:
            result["status"] = "warning"
            result["warning"] = "Нет складов МСК/МО"
            result["warehouse"] = {k: v for k, v in available[0].items() if k != "infoRaw"}
        else:
            result["warehouse"] = {k: v for k, v in best.items() if k != "infoRaw"}
            if common_warehouse and str(best["id"]) == str(common_warehouse["id"]):
                result["commonWarehouse"] = True

            if dry_run:
                result["status"] = "dry_run"
            else:
                try:
                    wh_id = int(best["id"])
                    wh_type = str(best.get("type") or SC_TYPE)
                    await set_crossdock_warehouse(page, draft_id, cid, wh_id, wh_type)
                    result["status"] = "ok"
                except Exception as e:
                    result["status"] = "error"
                    result["error"] = str(e)

        results.append(result)
        wh = result.get("warehouse") or {}
        icon = {"ok": "+", "dry_run": "o", "warning": "!", "error": "x"}.get(result["status"], "?")
        common_mark = " [ОБЩИЙ]" if result.get("commonWarehouse") else ""
        ts = wh.get("timeslotDate") or wh.get("timeslotFrom", "")[:16] if wh else ""
        extra = f" | {wh.get('name', '—')}" if wh.get("name") else ""
        if ts:
            extra += f" | {ts}"
        extra += common_mark
        if result.get("error"):
            extra += f" | {result['error']}"
        print(f"  {icon} {cname} [crossdock]{extra}")

    # Добавляем доступные склады в каждый result (для следующего этапа)
    for r in results:
        cid = r.get("clusterId", "")
        available = cluster_available.get(cid, [])
        if available:
            r["availableWarehouses"] = [
                {
                    "id": e.get("id"),
                    "name": e.get("name"),
                    "type": e.get("type"),
                    "timeslotFrom": e.get("timeslotFrom"),
                    "timeslotTo": e.get("timeslotTo"),
                    "timeslotDate": e.get("timeslotDate"),
                    "maxPallets": e.get("maxPallets"),
                    "maxBoxes": e.get("maxBoxes"),
                    "isMskMo": e.get("isMskMo"),
                    "mskPriority": e.get("mskPriority"),
                }
                for e in sorted(available, key=lambda e: (
                    e.get("mskPriority", 2),
                    str(e.get("timeslotFrom") or "9999"),
                ))
            ]

    return results


def _find_common_warehouse(
    crossdock_clusters: list[dict[str, Any]],
    cluster_available: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """
    Находит склад МСК/МО, доступный для наибольшего числа кластеров.
    Если несколько — берём с ближайшим таймслотом (средним по кластерам).
    Возвращает None если нет общего хотя бы для 2 кластеров.
    """
    msk_ids = _load_msk_sc_ids()
    # wh_id → {count, latest_timeslot, entry}
    wh_coverage: dict[str, dict[str, Any]] = {}

    for c in crossdock_clusters:
        cid = str(c.get("macrolocalClusterId") or "")
        for e in cluster_available.get(cid, []):
            wh_id = str(e.get("id") or "")
            # Только МСК/МО
            if not e.get("isMskMo") and wh_id not in msk_ids:
                continue
            if wh_id not in wh_coverage:
                wh_coverage[wh_id] = {
                    "count": 0,
                    "max_timeslot": "",
                    "entry": e,
                    "priority": e.get("mskPriority", 2),
                }
            wh_coverage[wh_id]["count"] += 1
            ts = str(e.get("timeslotFrom") or "")
            if ts > wh_coverage[wh_id]["max_timeslot"]:
                wh_coverage[wh_id]["max_timeslot"] = ts

    if not wh_coverage:
        return None

    # Сортируем: больше кластеров → preferred → раньше максимальный таймслот
    ranked = sorted(
        wh_coverage.values(),
        key=lambda w: (-w["count"], w["priority"], w["max_timeslot"]),
    )

    best = ranked[0]
    if best["count"] < 2:
        return None

    result = dict(best["entry"])
    result["_common_count"] = best["count"]
    return result


def _format_warehouse(w: dict[str, Any]) -> dict[str, Any]:
    ts = str(w.get("nearestTimeslotLocal") or "")
    return {
        "id": w.get("dropOffPointId"),
        "name": w.get("name"),
        "type": w.get("dropOffPointType"),
        "nearestTimeslot": ts,
        "nearestTimeslotShort": _fmt_slot(ts),
        "distanceKm": w.get("distanceKilometers"),
    }


# ─── main ──────────────────────────────────────────────────────────────────────

def _status_icon(status: str) -> str:
    return {"ok": "+", "dry_run": "o", "warning": "!", "error": "x"}.get(status, "?")


async def _create_draft_via_ui(page) -> str:
    """
    Создать новый multi-cluster черновик через UI (кнопка 'Создать заявку').
    Черновик, созданный через API, не виден на сайте — поэтому кликаем кнопку.
    """
    from playwright.async_api import expect

    # Переходим на страницу поставок
    await page.goto("https://seller.ozon.ru/app/supply/orders", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # Кликаем "Создать заявку"
    btn = page.get_by_test_id("CreateMultiClusterDraftButton")
    await btn.click()

    # Ожидаем редирект на страницу черновика: /multi-cluster/<draft_id>
    await page.wait_for_url("**/multi-cluster/**", timeout=15000)
    url = page.url
    # Извлекаем draft_id из URL
    # https://seller.ozon.ru/app/supply/orders/multi-cluster/96601234
    parts = url.rstrip("/").split("/")
    draft_id = parts[-1]
    if not draft_id.isdigit():
        raise RuntimeError(f"Не удалось извлечь draft_id из URL: {url}")
    return draft_id


async def run(args: argparse.Namespace) -> None:
    async with OzonBrowser("seller.ozon.ru/app/supply") as page:
        draft_id = args.draft_id

        if not draft_id:
            draft_id = await _create_draft_via_ui(page)
            print(f"Создан новый черновик: {draft_id}")

        draft = await _get_draft(page, draft_id)
        clusters = (
            (draft.get("draft") or {}).get("multiCluster") or {}
        ).get("clusterInfos") or []
        selected = [c for c in clusters if bool(c.get("isSelected"))]

        if not selected:
            selected = clusters  # fallback: все кластеры

        if not selected:
            print("Черновик не содержит кластеров.")
            return

        print(f"Черновик: {draft_id}")
        print(f"Кластеров: {len(selected)}")
        try:
            all_points = await get_drop_off_points_viewport(page, draft_id)
            print(f"Точек отгрузки загружено: {len(all_points)}")
        except Exception as e:
            all_points = []
            print(f"Не удалось загрузить точки отгрузки: {e}")
        if args.dry_run:
            print("[DRY-RUN] Изменения не сохраняются")
        print()

        results = await process_all_clusters(
            page, draft_id, selected, args.dry_run, all_points,
        )

    ok = sum(1 for r in results if r["status"] == "ok")
    warn = sum(1 for r in results if r["status"] == "warning")
    err = sum(1 for r in results if r["status"] == "error")
    print(f"\nИтог: {ok} OK / {warn} предупреждений / {err} ошибок")
    print(f"URL:  https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}")

    out: dict[str, Any] = {
        "draftId": draft_id,
        "draftUrl": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}",
        "dryRun": args.dry_run,
        "totalClusters": len(selected),
        "ok": ok,
        "warnings": warn,
        "errors": err,
        "results": results,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сохранено: {out_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Этап 2: установить способ доставки и склад для кластеров черновика."
    )
    p.add_argument("--draft-id", default="", help="ID черновика (если не указан — создаётся новый)")
    p.add_argument("--dry-run", action="store_true", help="Не сохранять в Ozon")
    p.add_argument("--output", default="exports/supply_stage2_warehouses.json")
    return p.parse_args()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
