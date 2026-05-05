"""
supply_mixed_flow.py — Создание поставки: API (черновик + товары) + Playwright (склады)

Шаг 1: Читает latestPalletClusters из открытого дашборда в Chrome (Playwright)
        — это именно те данные, которые видны после "Рассчитать паллеты" с учётом правок
Шаг 2: Создаёт мультикластерный черновик через Ozon Seller API (прямой HTTP)
Шаг 3: Ожидает готовности черновика (poll /v2/draft/create/info)
Шаг 4: Переключается на seller.ozon.ru → выбирает склады через Playwright (bff_fetch)

Требуется:
  - Открытый Chrome с дашбордом (run_dashboard.cmd)
  - Рассчитанные паллеты (кнопка "Рассчитать паллеты" на вкладке Поставка)
  - Залогиненный seller.ozon.ru в том же Chrome

Usage:
  python supply_mixed_flow.py                          # полный флоу
  python supply_mixed_flow.py --dry-run                # без записи в Ozon
  python supply_mixed_flow.py --draft-id 96601234      # пропустить создание, только склады
  python supply_mixed_flow.py --skip-warehouses        # только создание, без складов
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import aiohttp

sys.stdout.reconfigure(encoding="utf-8")

# ─── пути ────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
EXPORTS_DIR = _HERE / "exports"

# ─── .env ────────────────────────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    env_path = _HERE / ".env"
    env_vars: dict[str, str] = {}
    if not env_path.exists():
        return env_vars
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"')
    return env_vars


_ENV = _load_env()
CLIENT_ID = _ENV.get("OZON_CLIENT_ID", "")
API_KEY = _ENV.get("OZON_SUPPLY_API_KEY", "") or _ENV.get("OZON_API_KEY", "")
SELLER_WH = int(_ENV.get("OZON_CROSSDOCK_SELLER_WAREHOUSE_ID", "23785825652000"))
DROPOFF_WH = int(_ENV.get("OZON_CROSSDOCK_DROPOFF_WAREHOUSE_ID", "23969023230000"))

API_HEADERS = {
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
}

# Маппинг имён кластеров → macrolocal_cluster_id (из orders_dashboard.py)
CLUSTER_NAME_TO_ID: dict[str, int] = {
    "москва мо и дальние регионы": 4039,
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
    "дальний восток": 4002,
    "калининград": 4004,
    "махачкала": 4077,
    "невинномысск": 4076,
}


def _normalize_cluster_name(name: str) -> str:
    """Нормализация имени кластера: убрать спецсимволы, lowercase."""
    return re.sub(r"[^а-яёa-z0-9 ]", "", name.lower()).strip()


def _resolve_cluster_id(cluster_name: str) -> int | None:
    """Найти macrolocal_cluster_id по имени кластера."""
    normalized = _normalize_cluster_name(cluster_name)
    # Точное совпадение
    if normalized in CLUSTER_NAME_TO_ID:
        return CLUSTER_NAME_TO_ID[normalized]
    # Частичное совпадение
    for name, cid in CLUSTER_NAME_TO_ID.items():
        if name in normalized or normalized in name:
            return cid
    # Специальные fallback
    if "моск" in normalized:
        return 4039
    if ("санкт" in normalized and "петербург" in normalized) or "спб" in normalized:
        return 4007
    return None


# ─── Шаг 1: Загрузка данных из отчёта "Поставка" ─────────────────────────────

async def load_supply_plan_from_dashboard(
    dashboard_url: str = DASHBOARD_URL,
) -> dict[str, list[dict[str, Any]]]:
    """
    GET /api/supply-plan с дашборда → актуальные данные с allocated_supply.
    Возвращает: {cluster_name: [{sku: int, quantity: int}, ...]}
    """
    url = f"{dashboard_url}/api/supply-plan"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"Дашборд вернул {resp.status}. Убедитесь что он запущен (run_dashboard.cmd)"
                )
            data = await resp.json()

    items = data.get("items") or []
    if not items:
        raise RuntimeError("Отчёт 'Поставка' пуст. Нет данных для создания черновика.")

    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in items:
        details = item.get("details") or []
        for d in details:
            cluster_name = str(d.get("cluster_name") or "").strip()
            sku = d.get("sku")
            qty = int(d.get("allocated_supply") or 0)
            if not cluster_name or not sku or qty <= 0:
                continue
            # Агрегируем если один SKU встречается дважды в кластере
            existing = next(
                (x for x in by_cluster[cluster_name] if x["sku"] == int(sku)),
                None,
            )
            if existing:
                existing["quantity"] += qty
            else:
                by_cluster[cluster_name].append({"sku": int(sku), "quantity": qty})

    if not by_cluster:
        raise RuntimeError(
            "Нет данных с allocated_supply > 0. "
            "Проверьте что в отчёте 'Поставка' заполнены количества."
        )

    return dict(by_cluster)


# ─── Шаг 2: Создание черновика через Seller API ──────────────────────────────

async def _api_post(
    session: aiohttp.ClientSession,
    endpoint: str,
    body: dict[str, Any],
    label: str = "",
) -> tuple[int, dict[str, Any]]:
    """POST к Ozon Seller API с retry на 429."""
    url = f"https://api-seller.ozon.ru{endpoint}"
    status = 0
    data: dict[str, Any] = {}
    for attempt in range(5):
        async with session.post(url, headers=API_HEADERS, json=body) as resp:
            status = resp.status
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                data = {"raw": text[:500]}
            if status == 429:
                wait = 35
                print(f"  [{label}] 429 Too Many Requests, жду {wait}с (попытка {attempt + 1})")
                await asyncio.sleep(wait)
                continue
            return status, data
    return status, data


async def create_draft_via_api(
    session: aiohttp.ClientSession,
    cluster_items: dict[str, list[dict[str, Any]]],
) -> tuple[int, dict[str, Any]]:
    """
    Создать мультикластерный черновик через Ozon Seller API.
    Возвращает (draft_id, full_response).
    """
    clusters_info = []
    skipped = []
    for cluster_name, items in cluster_items.items():
        cluster_id = _resolve_cluster_id(cluster_name)
        if cluster_id is None:
            skipped.append(cluster_name)
            print(f"  [!] Кластер '{cluster_name}' не найден в маппинге, пропускаю")
            continue
        api_items = [{"sku": it["sku"], "quantity": it["quantity"]} for it in items]
        clusters_info.append({
            "macrolocal_cluster_id": cluster_id,
            "items": api_items,
        })

    if not clusters_info:
        raise RuntimeError(
            f"Нет кластеров для создания черновика. "
            f"Пропущены: {skipped}. Проверьте маппинг CLUSTER_NAME_TO_ID."
        )

    body = {
        "clusters_info": clusters_info,
        "deletion_sku_mode": "PARTIAL",
        "delivery_info": {
            "type": "DROPOFF",
            "seller_warehouse_id": SELLER_WH,
            "drop_off_warehouse": {
                "warehouse_id": DROPOFF_WH,
                "warehouse_type": "DELIVERY_POINT",
            },
        },
    }

    print("  Кластеры:")
    for ci in clusters_info:
        total_qty = sum(it["quantity"] for it in ci["items"])
        total_sku = len(ci["items"])
        print(f"    cluster={ci['macrolocal_cluster_id']}: {total_sku} SKU, {total_qty} шт")

    status, data = await _api_post(session, "/v1/draft/multi-cluster/create", body, "create")
    draft_id = int(data.get("draft_id") or 0)
    return draft_id, data


# ─── Шаг 3: Ожидание готовности черновика ─────────────────────────────────────

async def wait_draft_ready(
    session: aiohttp.ClientSession,
    draft_id: int,
    max_polls: int = 15,
    interval: float = 3.0,
) -> dict[str, Any]:
    """Ждать пока черновик перейдёт из IN_PROGRESS."""
    info_data: dict[str, Any] = {}
    for i in range(max_polls):
        await asyncio.sleep(interval)
        status, info_data = await _api_post(
            session, "/v2/draft/create/info", {"draft_id": draft_id}, "info"
        )
        draft_status = info_data.get("status", "")
        print(f"    #{i + 1}: status={draft_status}")
        if status == 200 and draft_status != "IN_PROGRESS":
            break

    # Вывести ошибки если есть
    for err in info_data.get("errors") or []:
        print(f"    ERROR: {err.get('error_message', '?')}")

    # Вывести склады
    for ic in info_data.get("clusters") or []:
        cname = ic.get("cluster_name", "?")
        warehouses = ic.get("warehouses") or []
        available = [
            w for w in warehouses
            if (w.get("availability_status") or {}).get("state") in ("FULL_AVAILABLE", "AVAILABLE")
        ]
        print(f"    {cname}: {len(available)}/{len(warehouses)} складов доступно")

    return info_data


# ─── Шаг 4: Выбор складов через Playwright ───────────────────────────────────

async def select_warehouses_playwright(
    draft_id: int,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """
    Подключиться к Chrome через Playwright и выбрать склады для каждого кластера.
    Использует bff_fetch (JS в контексте браузера) — выглядит как обычный пользователь.
    """
    from src.chrome_browser import OzonBrowser, bff_fetch, COMPANY_ID
    from src.supply_stage2_warehouses import (
        get_drop_off_points_viewport,
        process_all_clusters,
    )

    async with OzonBrowser("seller.ozon.ru/app/supply") as page:
        # Переходим на страницу черновика чтобы контекст был правильный
        draft_url = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"
        print(f"  Открываю черновик: {draft_url}")
        await page.goto(draft_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Получаем кластеры из черновика
        draft_data = await bff_fetch(
            page,
            "/api/supplier-drafts/api/v4/get",
            {"companyId": COMPANY_ID, "draftId": str(draft_id)},
        )
        clusters = (
            (draft_data.get("draft") or {}).get("multiCluster", {}).get("clusterInfos") or []
        )
        selected = [c for c in clusters if bool(c.get("isSelected"))]
        if not selected:
            selected = clusters  # fallback: все

        if not selected:
            print("  Черновик не содержит кластеров.")
            return []

        print(f"  Кластеров в черновике: {len(selected)}")
        for c in selected:
            cname = c.get("name", "?")
            cid = c.get("macrolocalClusterId", "?")
            items_count = len(c.get("items") or [])
            print(f"    {cname} (id={cid}), товаров: {items_count}")

        # Загружаем точки отгрузки
        try:
            all_points = await get_drop_off_points_viewport(page, str(draft_id))
            print(f"  Точек отгрузки: {len(all_points)}")
        except Exception as e:
            all_points = []
            print(f"  Не удалось загрузить точки отгрузки: {e}")

        if dry_run:
            print("  [DRY-RUN] Изменения не сохраняются")

        # Выбираем склады
        results = await process_all_clusters(
            page, str(draft_id), selected, dry_run, all_points,
        )

    return results


# ─── main ─────────────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> None:
    draft_id = int(args.draft_id) if args.draft_id else 0
    cluster_items: dict[str, list[dict[str, Any]]] = {}

    # ── Шаг 1: Данные из отчёта "Поставка" ──
    if not draft_id:
        print("=" * 60)
        print("  ШАГ 1: Загрузка данных из отчёта 'Поставка'")
        print("=" * 60)
        cluster_items = await load_supply_plan_from_dashboard(args.dashboard_url)
        total_sku = sum(len(items) for items in cluster_items.values())
        total_qty = sum(sum(it["quantity"] for it in items) for items in cluster_items.values())
        print(f"  Кластеров: {len(cluster_items)}, SKU: {total_sku}, шт: {total_qty}")
        for cluster, items in cluster_items.items():
            cid = _resolve_cluster_id(cluster)
            qty = sum(it["quantity"] for it in items)
            mark = "+" if cid else "?"
            print(f"    [{mark}] {cluster} (id={cid}): {len(items)} SKU, {qty} шт")

    # ── Шаг 2: Создание черновика через API ──
    if not draft_id:
        print()
        print("=" * 60)
        print("  ШАГ 2: Создание черновика через Ozon Seller API")
        print("=" * 60)

        if args.dry_run:
            print("  [DRY-RUN] Черновик не создаётся")
            print("  Для реального создания запустите без --dry-run")
            return

        timeout = aiohttp.ClientTimeout(total=600)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            draft_id, create_resp = await create_draft_via_api(session, cluster_items)
            if draft_id <= 0:
                print(f"  ОШИБКА создания: {json.dumps(create_resp, ensure_ascii=False)[:500]}")
                return
            print(f"  draft_id: {draft_id}")
            print(f"  URL: https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}")

            # ── Шаг 3: Ожидание готовности ──
            print()
            print("=" * 60)
            print("  ШАГ 3: Ожидание готовности черновика")
            print("=" * 60)
            info_data = await wait_draft_ready(session, draft_id)

            # Сохраняем промежуточный результат
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            interim_path = EXPORTS_DIR / f"supply_mixed_draft_{draft_id}.json"
            interim_path.write_text(
                json.dumps(
                    {"draft_id": draft_id, "create_response": create_resp, "info": info_data},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
            print(f"  Промежуточный файл: {interim_path}")
    else:
        print(f"\n  Используем существующий черновик: {draft_id}")

    # ── Шаг 4: Выбор складов через Playwright ──
    if args.skip_warehouses:
        print("\n  --skip-warehouses: пропускаем выбор складов")
        return

    print()
    print("=" * 60)
    print("  ШАГ 4: Выбор складов через Playwright")
    print("=" * 60)

    results = await select_warehouses_playwright(draft_id, args.dry_run)

    # ── Итоги ──
    ok = sum(1 for r in results if r.get("status") == "ok")
    warn = sum(1 for r in results if r.get("status") == "warning")
    err = sum(1 for r in results if r.get("status") == "error")
    print()
    print("=" * 60)
    print(f"  ИТОГ: {ok} OK / {warn} предупреждений / {err} ошибок")
    print(f"  URL: https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}")
    print("=" * 60)

    # Сохраняем результат
    output_path = Path(args.output)
    out: dict[str, Any] = {
        "draftId": draft_id,
        "draftUrl": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}",
        "dryRun": args.dry_run,
        "source": "supply_plan_dashboard",
        "inputClusters": {
            name: {"skuCount": len(items), "totalQty": sum(it["quantity"] for it in items)}
            for name, items in cluster_items.items()
        } if cluster_items else {},
        "warehouseResults": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Результат: {output_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Создание поставки: API (черновик + товары) + Playwright (склады)"
    )
    p.add_argument("--draft-id", default="", help="Существующий draft_id (пропустить создание)")
    p.add_argument("--dry-run", action="store_true", help="Не записывать в Ozon")
    p.add_argument("--skip-warehouses", action="store_true", help="Только создать черновик, без выбора складов")
    p.add_argument("--dashboard-url", default=DASHBOARD_URL, help="URL дашборда (по умолчанию localhost:8088)")
    p.add_argument("--output", default="exports/supply_mixed_flow_result.json")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
