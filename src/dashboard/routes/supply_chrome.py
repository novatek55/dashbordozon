"""Dashboard routes/supply_chrome.py handlers."""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import aiohttp
from aiohttp import web

from src.config import settings
from src.dashboard.constants import BASE_DIR, MSK, SUPPLY_MACROLOCAL_CLUSTER_FALLBACKS
from src.dashboard import state
from src.dashboard.helpers import (
    _to_int, _ozon_supply_post, _get_env_from_dotenv, _get_ozon_credentials,
    _normalize_cluster_name, _extract_supply_clusters,
)


async def _chrome_auth_background() -> None:
    """Фоновая задача: запускает Chrome и ждёт авторизации пользователя."""
    try:
        from src.chrome_browser import (
            ensure_chrome,
            connect_cdp,
            wait_for_auth,
            _is_logged_in_url,
            SELLER_ORIGIN,
        )

        state._CHROME_STATE = {"status": "starting", "message": "Запускаю Chrome..."}
        await ensure_chrome()

        state._CHROME_STATE = {"status": "connecting", "message": "Подключаюсь к Chrome..."}
        pw, browser = await connect_cdp()
        try:
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await ctx.new_page()
            await page.goto(
                f"{SELLER_ORIGIN}/app/supply/orders", wait_until="domcontentloaded"
            )

            if _is_logged_in_url(page.url):
                state._CHROME_STATE = {"status": "ready", "message": "Уже авторизован. Готово!"}
                return

            state._CHROME_STATE = {
                "status": "waiting_auth",
                "message": "Войдите в аккаунт в открывшемся Chrome",
            }
            await wait_for_auth(page, auth_timeout_sec=180)
            state._CHROME_STATE = {"status": "ready", "message": "Авторизация успешна! Chrome готов к работе."}
        finally:
            await browser.close()
            await pw.stop()

    except Exception as e:
        state._CHROME_STATE = {"status": "error", "message": str(e)}


async def chrome_auth_init(request: web.Request) -> web.Response:
    """POST /api/chrome/init — запустить Chrome и начать авторизацию."""
    # Если уже готов — сразу возвращаем
    if state._CHROME_STATE.get("status") == "ready":
        return web.json_response({"started": True, "state": state._CHROME_STATE})
    # Если задача уже выполняется — не запускаем повторно
    if state._CHROME_TASK and not state._CHROME_TASK.done():
        return web.json_response({"started": False, "state": state._CHROME_STATE})
    state._CHROME_STATE = {"status": "idle", "message": ""}
    state._CHROME_TASK = asyncio.create_task(_chrome_auth_background())
    return web.json_response({"started": True, "state": state._CHROME_STATE})


async def chrome_auth_status(request: web.Request) -> web.Response:
    """GET /api/chrome/status — текущий статус Chrome."""
    return web.json_response(state._CHROME_STATE)


async def supply_stage2_set_warehouses(request: web.Request) -> web.Response:
    """Этап 2 (Playwright CDP): установить способ доставки и склад для выбранных кластеров черновика."""
    import traceback
    try:
        from src.supply_stage2_warehouses import (
            process_all_clusters as _stage2_process_all,
            get_drop_off_points_viewport as _stage2_get_points,
        )
        from src.chrome_browser import OzonBrowser, bff_fetch, COMPANY_ID as _COMPANY_ID

        body = await request.json() if request.body_exists else {}
        draft_id = str(body.get("draft_id") or "").strip()
        dry_run = bool(body.get("dry_run"))

        async with OzonBrowser("seller.ozon.ru/app/supply") as page:
            # Если draft_id не указан — создаём новый через UI
            if not draft_id:
                from src.supply_stage2_warehouses import _create_draft_via_ui
                draft_id = await _create_draft_via_ui(page)

            draft = await bff_fetch(
                page,
                "/api/supplier-drafts/api/v4/get",
                {"companyId": _COMPANY_ID, "draftId": draft_id},
            )
            clusters = (
                (draft.get("draft") or {}).get("multiCluster") or {}
            ).get("clusterInfos") or []
            selected = [c for c in clusters if bool(c.get("isSelected"))]

            if not selected:
                selected = clusters

            if not selected:
                return web.json_response(
                    {
                        "success": False,
                        "error": "Черновик не содержит кластеров. Проверьте draft_id.",
                    },
                    status=400,
                )

            try:
                all_points = await _stage2_get_points(page, draft_id)
            except Exception as pts_err:
                import traceback as _tb
                print(f"WARNING: get_drop_off_points_viewport failed: {pts_err}\n{_tb.format_exc()}")
                all_points = []

            results = await _stage2_process_all(
                page, draft_id, selected, dry_run, all_points,
            )

        ok_count = sum(1 for r in results if r["status"] == "ok")
        warn_count = sum(1 for r in results if r["status"] == "warning")
        err_count = sum(1 for r in results if r["status"] == "error")
        return web.json_response(
            {
                "success": True,
                "draftId": draft_id,
                "draftUrl": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}",
                "dryRun": dry_run,
                "totalClusters": len(selected),
                "totalDropOffPoints": len(all_points),
                "ok": ok_count,
                "warnings": warn_count,
                "errors": err_count,
                "results": results,
            }
        )
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"ERROR in supply_stage2_set_warehouses: {error_details}")
        return web.json_response(
            {"success": False, "error": str(e), "details": error_details}, status=500
        )


async def supply_multi_cluster_api(request: web.Request) -> web.Response:
    """POST /api/supply-plan/multi-cluster-api — создать мультикластерный черновик через Ozon Seller API.

    Принимает данные паллетизации (кластеры + SKU + количества),
    создаёт черновик, получает склады и таймслоты за 3 запроса.

    Body:
        clusters: [{cluster_name, items: [{sku, quantity}]}]
        dropoff_warehouse_id: int (optional, default from .env)
        date_from: str YYYY-MM-DD (optional)
        date_to: str YYYY-MM-DD (optional)
        create_supply: bool (optional, false) — создать заявку
        timeslot_from: str (optional) — слот для создания заявки
        timeslot_to: str (optional)
    """
    import traceback

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    clusters_input = body.get("clusters") or []
    create_supply = bool(body.get("create_supply"))
    timeslot_from = (body.get("timeslot_from") or "").strip()
    timeslot_to = (body.get("timeslot_to") or "").strip()

    # Credentials
    client_id = (
        os.getenv("OZON_CLIENT_ID")
        or getattr(settings, "ozon_client_id", "")
        or _get_env_from_dotenv("OZON_CLIENT_ID")
        or ""
    ).strip()
    api_key = (
        os.getenv("OZON_SUPPLY_API_KEY")
        or os.getenv("OZON_API_KEY")
        or _get_env_from_dotenv("OZON_SUPPLY_API_KEY")
        or _get_env_from_dotenv("OZON_API_KEY")
        or getattr(settings, "ozon_api_key", "")
        or ""
    ).strip()
    if not client_id or not api_key:
        return web.json_response(
            {"success": False, "error": "Missing OZON_CLIENT_ID/OZON_SUPPLY_API_KEY"},
            status=400,
        )

    seller_wh_str = (
        os.getenv("OZON_CROSSDOCK_SELLER_WAREHOUSE_ID")
        or _get_env_from_dotenv("OZON_CROSSDOCK_SELLER_WAREHOUSE_ID")
        or "23785825652000"
    ).strip()
    seller_wh = int(seller_wh_str)

    dropoff_wh = int(body.get("dropoff_warehouse_id") or (
        os.getenv("OZON_CROSSDOCK_DROPOFF_WAREHOUSE_ID")
        or _get_env_from_dotenv("OZON_CROSSDOCK_DROPOFF_WAREHOUSE_ID")
        or "22190776129000"
    ))

    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }

    if not clusters_input:
        return web.json_response(
            {"success": False, "error": "No clusters data provided"},
            status=400,
        )

    # Resolve cluster names to macrolocal_cluster_id
    clusters_info = []
    resolve_log = []
    for c in clusters_input:
        cname = str(c.get("cluster_name") or "").strip()
        items_raw = c.get("items") or []
        items = [
            {"sku": int(it["sku"]), "quantity": int(it["quantity"])}
            for it in items_raw
            if int(it.get("quantity") or 0) > 0
        ]
        if not cname or not items:
            continue

        # Resolve name -> macrolocal_cluster_id
        cname_lower = re.sub(r"[^а-яёa-z0-9 ]", "", cname.lower()).strip()
        cid = SUPPLY_MACROLOCAL_CLUSTER_FALLBACKS.get(cname_lower)
        if not cid:
            for fb_name, fb_id in SUPPLY_MACROLOCAL_CLUSTER_FALLBACKS.items():
                if fb_name in cname_lower or cname_lower in fb_name:
                    cid = fb_id
                    break
        if not cid:
            resolve_log.append({"cluster": cname, "status": "NOT_FOUND"})
            continue

        resolve_log.append({"cluster": cname, "macrolocal_cluster_id": cid, "items_count": len(items)})
        clusters_info.append({
            "macrolocal_cluster_id": cid,
            "items": items,
        })

    if not clusters_info:
        return web.json_response(
            {"success": False, "error": "No valid clusters resolved", "resolve_log": resolve_log},
            status=400,
        )

    result: Dict[str, Any] = {"resolve_log": resolve_log}
    timeout = aiohttp.ClientTimeout(total=300)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # ── Шаг 1: Создать черновик ──
            create_body = {
                "clusters_info": clusters_info,
                "deletion_sku_mode": "PARTIAL",
                "delivery_info": {
                    "type": "DROPOFF",
                    "seller_warehouse_id": seller_wh,
                    "drop_off_warehouse": {
                        "warehouse_id": dropoff_wh,
                        "warehouse_type": "DELIVERY_POINT",
                    },
                },
            }
            s1, d1 = await _ozon_supply_post(session, "/v1/draft/multi-cluster/create", headers, create_body)
            draft_id = int(d1.get("draft_id") or 0)
            create_errors = d1.get("errors") or []
            result["step1_create"] = {
                "status": s1,
                "draft_id": draft_id,
                "errors": create_errors,
                "draft_url": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}" if draft_id else None,
            }
            if s1 != 200 or draft_id <= 0:
                result["success"] = False
                result["error"] = f"Failed to create draft: {d1}"
                return web.json_response(result, status=502)

            # ── Шаг 2: Получить склады (create/info) ──
            info_data: Dict[str, Any] = {}
            for _ in range(15):
                await asyncio.sleep(2)
                s2, info_data = await _ozon_supply_post(
                    session, "/v2/draft/create/info", headers, {"draft_id": draft_id}
                )
                if s2 == 200 and info_data.get("status") != "IN_PROGRESS":
                    break

            clusters_result = []
            selected_warehouses = []
            for ic in info_data.get("clusters") or []:
                cname = ic.get("cluster_name", "?")
                cid = ic.get("macrolocal_cluster_id")
                whs = ic.get("warehouses") or []
                cluster_whs = []
                for wh in whs:
                    avail = wh.get("availability_status") or {}
                    state = avail.get("state", "?")
                    storage = wh.get("storage_warehouse") or {}
                    ok = state in ("FULL_AVAILABLE", "AVAILABLE")
                    bid = wh.get("bundle_id", "")
                    wh_info = {
                        "name": storage.get("name") if storage else "(auto)",
                        "warehouse_id": storage.get("warehouse_id") if storage else None,
                        "bundle_id": bid,
                        "state": state,
                        "score": wh.get("total_score"),
                        "rank": wh.get("total_rank"),
                        "address": storage.get("address") if storage else None,
                        "available": ok,
                    }
                    cluster_whs.append(wh_info)
                    if ok and bid:
                        selected_warehouses.append({
                            "macrolocal_cluster_id": cid,
                            "storage_warehouse_id": 0,
                        })
                clusters_result.append({
                    "cluster_name": cname,
                    "macrolocal_cluster_id": cid,
                    "warehouses": cluster_whs,
                })

            result["step2_info"] = {
                "status": info_data.get("status"),
                "errors": info_data.get("errors") or [],
                "clusters": clusters_result,
            }

            # ── Шаг 3: Получить таймслоты ──
            timeslots_result: Dict[str, Any] = {}
            if selected_warehouses:
                date_from = body.get("date_from") or datetime.now().strftime("%Y-%m-%d")
                date_to = body.get("date_to") or (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
                ts_body = {
                    "draft_id": draft_id,
                    "date_from": date_from,
                    "date_to": date_to,
                    "supply_type": "MULTI_CLUSTER",
                    "selected_cluster_warehouses": selected_warehouses,
                }
                await asyncio.sleep(3)
                s3, d3 = await _ozon_supply_post(session, "/v2/draft/timeslot/info", headers, ts_body)
                ts_data = d3.get("result") or {}
                days_raw = (ts_data.get("drop_off_warehouse_timeslots") or {}).get("days") or []
                timeslots_flat = []
                for day in days_raw:
                    dt = day.get("date_in_timezone", "")
                    for slot in day.get("timeslots") or []:
                        timeslots_flat.append({
                            "date": dt,
                            "from": slot.get("from_in_timezone", ""),
                            "to": slot.get("to_in_timezone", ""),
                        })
                timeslots_result = {
                    "status": s3,
                    "error_reason": d3.get("error_reason"),
                    "timezone": (ts_data.get("drop_off_warehouse_timeslots") or {}).get("warehouse_timezone"),
                    "total_slots": len(timeslots_flat),
                    "days_count": len(days_raw),
                    "slots": timeslots_flat,
                }

            result["step3_timeslots"] = timeslots_result

            # ── Шаг 4 (опционально): Создать заявку ──
            if create_supply and timeslot_from and timeslot_to:
                supply_body = {
                    "draft_id": draft_id,
                    "supply_type": "MULTI_CLUSTER",
                    "timeslot": {
                        "from_in_timezone": timeslot_from,
                        "to_in_timezone": timeslot_to,
                    },
                    "selected_cluster_warehouses": selected_warehouses,
                }
                s4, d4 = await _ozon_supply_post(session, "/v2/draft/supply/create", headers, supply_body)
                supply_result: Dict[str, Any] = {"status": s4, "error_reasons": d4.get("error_reasons") or []}

                if s4 == 200 and not d4.get("error_reasons"):
                    for _ in range(15):
                        await asyncio.sleep(3)
                        s5, d5 = await _ozon_supply_post(
                            session, "/v2/draft/supply/create/status", headers, {"draft_id": draft_id}
                        )
                        status = d5.get("status", "")
                        if status != "IN_PROGRESS":
                            supply_result["order_id"] = d5.get("order_id")
                            supply_result["create_status"] = status
                            supply_result["create_error_reasons"] = d5.get("error_reasons") or []
                            break

                result["step4_supply"] = supply_result

            result["success"] = True
            result["draft_id"] = draft_id
            result["draft_url"] = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"

    except Exception as e:
        error_details = traceback.format_exc()
        result["success"] = False
        result["error"] = str(e)
        result["details"] = error_details

    return web.json_response(result)


async def supply_mixed_flow(request: web.Request) -> web.Response:
    """POST /api/supply-plan/mixed-flow — создать черновик через API + установить склады через Playwright.

    Принимает данные паллетизации (кластеры + SKU + количества из latestPalletClusters).
    Шаг 1: Создаёт мультикластерный черновик через Ozon Seller API (прямой HTTP)
    Шаг 2: Ожидает готовности черновика
    Шаг 3: Подключается к Chrome через Playwright, устанавливает склады (bff_fetch)

    Body:
        clusters: [{cluster_name, items: [{sku, quantity}]}]
        dry_run: bool (optional, default false)
    """
    import traceback

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    clusters_input = body.get("clusters") or []
    dry_run = bool(body.get("dry_run"))

    if not clusters_input:
        return web.json_response(
            {"success": False, "error": "No clusters data provided"}, status=400
        )

    # ── Credentials ──
    client_id = (
        os.getenv("OZON_CLIENT_ID")
        or getattr(settings, "ozon_client_id", "")
        or _get_env_from_dotenv("OZON_CLIENT_ID")
        or ""
    ).strip()
    api_key = (
        os.getenv("OZON_SUPPLY_API_KEY")
        or os.getenv("OZON_API_KEY")
        or _get_env_from_dotenv("OZON_SUPPLY_API_KEY")
        or _get_env_from_dotenv("OZON_API_KEY")
        or getattr(settings, "ozon_api_key", "")
        or ""
    ).strip()
    if not client_id or not api_key:
        return web.json_response(
            {"success": False, "error": "Missing OZON_CLIENT_ID/OZON_SUPPLY_API_KEY"},
            status=400,
        )

    seller_wh = int((
        os.getenv("OZON_CROSSDOCK_SELLER_WAREHOUSE_ID")
        or _get_env_from_dotenv("OZON_CROSSDOCK_SELLER_WAREHOUSE_ID")
        or "23785825652000"
    ).strip())
    dropoff_wh = int((
        os.getenv("OZON_CROSSDOCK_DROPOFF_WAREHOUSE_ID")
        or _get_env_from_dotenv("OZON_CROSSDOCK_DROPOFF_WAREHOUSE_ID")
        or "22190776129000"
    ).strip())

    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }

    # ── Resolve cluster names → macrolocal_cluster_id ──
    clusters_info = []
    resolve_log = []
    for c in clusters_input:
        cname = str(c.get("cluster_name") or "").strip()
        items_raw = c.get("items") or []
        items = [
            {"sku": int(it["sku"]), "quantity": int(it["quantity"])}
            for it in items_raw
            if int(it.get("quantity") or 0) > 0
        ]
        if not cname or not items:
            continue

        cname_lower = re.sub(r"[^а-яёa-z0-9 ]", "", cname.lower()).strip()
        cid = SUPPLY_MACROLOCAL_CLUSTER_FALLBACKS.get(cname_lower)
        if not cid:
            for fb_name, fb_id in SUPPLY_MACROLOCAL_CLUSTER_FALLBACKS.items():
                if fb_name in cname_lower or cname_lower in fb_name:
                    cid = fb_id
                    break
        if not cid:
            resolve_log.append({"cluster": cname, "status": "NOT_FOUND"})
            continue

        resolve_log.append({"cluster": cname, "macrolocal_cluster_id": cid, "items_count": len(items)})
        clusters_info.append({"macrolocal_cluster_id": cid, "items": items})

    if not clusters_info:
        return web.json_response(
            {"success": False, "error": "No valid clusters resolved", "resolve_log": resolve_log},
            status=400,
        )

    result: Dict[str, Any] = {"resolve_log": resolve_log}
    timeout = aiohttp.ClientTimeout(total=300)

    try:
        # ── Шаг 1: Создать черновик через Ozon Seller API ──
        draft_id = 0
        async with aiohttp.ClientSession(timeout=timeout) as session:
            create_body = {
                "clusters_info": clusters_info,
                "deletion_sku_mode": "PARTIAL",
                "delivery_info": {
                    "type": "DROPOFF",
                    "seller_warehouse_id": seller_wh,
                    "drop_off_warehouse": {
                        "warehouse_id": dropoff_wh,
                        "warehouse_type": "DELIVERY_POINT",
                    },
                },
            }

            if dry_run:
                result["success"] = True
                result["dry_run"] = True
                result["step1_create"] = {"status": "dry_run", "clusters_info": clusters_info}
                return web.json_response(result)

            s1, d1 = await _ozon_supply_post(session, "/v1/draft/multi-cluster/create", headers, create_body)
            draft_id = int(d1.get("draft_id") or 0)
            result["step1_create"] = {
                "status": s1,
                "draft_id": draft_id,
                "errors": d1.get("errors") or [],
                "draft_url": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}" if draft_id else None,
            }
            if s1 != 200 or draft_id <= 0:
                result["success"] = False
                result["error"] = f"Failed to create draft: {d1}"
                return web.json_response(result, status=502)

            # ── Шаг 2: Ожидание готовности ──
            info_data: Dict[str, Any] = {}
            for _ in range(15):
                await asyncio.sleep(2)
                s2, info_data = await _ozon_supply_post(
                    session, "/v2/draft/create/info", headers, {"draft_id": draft_id}
                )
                if s2 == 200 and info_data.get("status") != "IN_PROGRESS":
                    break

            result["step2_info"] = {
                "status": info_data.get("status"),
                "errors": info_data.get("errors") or [],
            }

        # ── Шаг 3: Сканирование складов через Playwright UI ──
        from src.supply_scan_warehouses_ui import scan_warehouses_for_draft
        from src.chrome_browser import OzonBrowser

        async with OzonBrowser() as page:
            scan_result = await scan_warehouses_for_draft(page, str(draft_id))

        result["success"] = True
        result["draft_id"] = draft_id
        result["draft_url"] = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"
        result["step3_warehouses"] = scan_result

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"ERROR in supply_mixed_flow: {error_details}")
        result["success"] = False
        result["error"] = str(e)
        result["details"] = error_details

    return web.json_response(result)


async def supply_scan_warehouses_ui(request: web.Request) -> web.Response:
    """POST /api/supply-plan/scan-warehouses-ui — полный флоу через UI seller.ozon.ru (Playwright).

    Сверяет чекбоксы, сканирует склады, выбирает оптимальный, устанавливает, жмёт "Далее".

    Body:
        draft_id: str (обязательно)
        expected_clusters: list[str] (опционально — имена кластеров из отчёта)
    """
    import traceback
    try:
        body = await request.json() if request.body_exists else {}
        draft_id = str(body.get("draft_id") or "").strip()
        if not draft_id:
            return web.json_response(
                {"success": False, "error": "draft_id is required"}, status=400
            )

        expected_clusters = body.get("expected_clusters") or None
        transit_warehouses = body.get("transit_warehouses") or None
        requested_clusters = body.get("clusters") or None
        collect_timeslots = body.get("collect_timeslots", True)

        from src.supply_scan_warehouses_ui import scan_and_set_warehouses
        from src.chrome_browser import OzonBrowser

        async with OzonBrowser() as page:
            result = await scan_and_set_warehouses(
                page, draft_id,
                warehouses=transit_warehouses,
                expected_clusters=expected_clusters,
                requested_clusters=requested_clusters,
                collect_timeslots=bool(collect_timeslots),
            )

        result["success"] = True
        return web.json_response(result)
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"ERROR in supply_scan_warehouses_ui: {error_details}")
        return web.json_response(
            {"success": False, "error": str(e), "details": error_details}, status=500
        )


async def supply_collect_timeslots(request: web.Request) -> web.Response:
    """POST /api/supply-plan/collect-timeslots — сбор таймслотов для всех валидных складов.

    Для каждого кластера перебирает все валидные склады:
    установить склад → сохранить → раскрыть кластер → кликнуть "Выбрать" → перехватить слоты.

    Body:
        draft_id: str (обязательно)
    """
    import traceback
    try:
        body = await request.json() if request.body_exists else {}
        draft_id = str(body.get("draft_id") or "").strip()
        if not draft_id:
            return web.json_response(
                {"success": False, "error": "draft_id is required"}, status=400
            )

        from src.supply_scan_warehouses_ui import collect_timeslots_for_draft
        from src.chrome_browser import OzonBrowser

        async with OzonBrowser() as page:
            # Открываем страницу черновика
            url = f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}"
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            # scan_result=None → функция сначала сделает скан
            result = await collect_timeslots_for_draft(page, draft_id)

        result["success"] = True
        return web.json_response(result)
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"ERROR in supply_collect_timeslots: {error_details}")
        return web.json_response(
            {"success": False, "error": str(e), "details": error_details}, status=500
        )


async def supply_filter_timeslots(request: web.Request) -> web.Response:
    """POST /api/supply-plan/filter-timeslots — фильтрация таймслотов по дате отгрузки.

    Body:
        draft_id: str (обязательно)
        target_date: str "YYYY-MM-DD" (опционально, пусто = сегодня +2 дня)
    """
    import traceback
    try:
        body = await request.json() if request.body_exists else {}
        draft_id = str(body.get("draft_id") or "").strip()
        target_date = str(body.get("target_date") or "").strip() or None

        if not draft_id:
            return web.json_response(
                {"success": False, "error": "draft_id is required"}, status=400
            )

        # Читаем собранные таймслоты из файла
        import json as _json
        from pathlib import Path as _Path
        ts_path = _Path("exports") / f"supply_timeslots_{draft_id}.json"
        if not ts_path.exists():
            return web.json_response(
                {"success": False, "error": f"Файл {ts_path} не найден. Сначала соберите таймслоты."}, status=404
            )

        ts_data = _json.loads(ts_path.read_text(encoding="utf-8"))

        from src.supply_scan_warehouses_ui import filter_timeslots, _slot_date_msk
        from datetime import timedelta, timezone as _tz
        _MSK = _tz(timedelta(hours=3))

        result = filter_timeslots(ts_data, target_date)

        # Добавляем группированные слоты по датам для каждого склада
        warehouses_by_cluster: dict = {}
        for entry in (ts_data.get("timeslots") or []):
            if entry.get("error") or not entry.get("slots"):
                continue
            cluster = entry.get("cluster", "")
            wh_name = entry.get("warehouse", "")
            by_date: dict = {}
            for s in entry["slots"]:
                dt = _slot_date_msk(s)
                date_key = dt.strftime("%Y-%m-%d")
                h_from = dt.strftime("%H:%M")
                dt_to = __import__("datetime").datetime.fromisoformat(
                    s["toUtc"].replace("Z", "+00:00")
                ).astimezone(_MSK)
                h_to = dt_to.strftime("%H:%M")
                by_date.setdefault(date_key, []).append({"from": h_from, "to": h_to})
            warehouses_by_cluster.setdefault(cluster, []).append({
                "warehouse": wh_name,
                "transitWarehouse": entry.get("transitWarehouse", ""),
                "itemsCount": entry.get("itemsCount", 0),
                "volumeLiters": entry.get("volumeLiters", 0),
                "acceptsAll": entry.get("acceptsAll", True),
                "dates": by_date,
                "totalSlots": entry.get("slotsCount", 0),
            })
        result["warehouses"] = warehouses_by_cluster
        result["success"] = True
        result["draft_id"] = draft_id
        return web.json_response(result)
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"ERROR in supply_filter_timeslots: {error_details}")
        return web.json_response(
            {"success": False, "error": str(e), "details": error_details}, status=500
        )


async def supply_fill_draft(request: web.Request) -> web.Response:
    """POST /api/supply-plan/fill-draft — заполнить кластеры черновика товарами из паллетизации."""
    import traceback
    try:
        from src.chrome_browser import OzonBrowser, bff_fetch, COMPANY_ID as _COMPANY_ID

        body = await request.json() if request.body_exists else {}
        draft_id = str(body.get("draft_id") or "").strip()
        had_existing_draft = bool(draft_id)
        clusters_input = body.get("clusters") or []
        dry_run = bool(body.get("dry_run"))

        if not clusters_input:
            return web.json_response({"success": False, "error": "clusters required"}, status=400)

        async def _get_cluster_assortment_item(
            page,
            local_draft_id: str,
            local_cluster_id: str,
            local_bundle_id: str,
            sku_value: Any,
        ) -> Dict[str, Any]:
            return await bff_fetch(
                page,
                "/api/supplier-drafts/api/v1/get-assortment-for-multi-cluster-draft",
                {
                    "editingBundleId": local_bundle_id,
                    "companyId": _COMPANY_ID,
                    "macrolocalClusterId": local_cluster_id,
                    "draftId": local_draft_id,
                    "searchString": str(sku_value or ""),
                },
            )

        async with OzonBrowser("seller.ozon.ru/app/supply") as page:
            # Если draft_id не указан — создаём новый через UI
            if not draft_id:
                from src.supply_stage2_warehouses import _create_draft_via_ui
                draft_id = await _create_draft_via_ui(page)

            draft = await bff_fetch(
                page,
                "/api/supplier-drafts/api/v4/get",
                {"companyId": _COMPANY_ID, "draftId": draft_id},
            )
            draft_clusters = (
                (draft.get("draft") or {}).get("multiCluster") or {}
            ).get("clusterInfos") or []

            by_name = {str(c.get("name") or "").strip().lower(): c for c in draft_clusters}

            if had_existing_draft and not dry_run:
                reset_errors: List[Dict[str, Any]] = []
                for draft_cluster in draft_clusters:
                    reset_cluster_id = str(draft_cluster.get("macrolocalClusterId") or "")
                    reset_cluster_name = str(draft_cluster.get("name") or "")
                    reset_bundle_id = str(draft_cluster.get("bundleId") or "")
                    try:
                        if reset_cluster_id:
                            await bff_fetch(page, "/api/supplier-drafts/bff/v1/update-is-cluster-selected", {
                                "companyId": _COMPANY_ID,
                                "draftId": draft_id,
                                "isSelected": False,
                                "macrolocalClusterId": reset_cluster_id,
                            })
                        if reset_bundle_id:
                            await bff_fetch(
                                page,
                                "/api/supplier-product-bundles-bff/v1/draft/clear",
                                {
                                    "marketplaceCompanyId": _COMPANY_ID,
                                    "supplierProductBundleId": reset_bundle_id,
                                },
                            )
                    except Exception as reset_err:
                        reset_errors.append({
                            "cluster_name": reset_cluster_name or reset_cluster_id,
                            "error": str(reset_err),
                        })
                if reset_errors:
                    print(f"WARNING supply_fill_draft reset errors for draft {draft_id}: {reset_errors}")

            # Собираем id кластеров из черновика, которые были затронуты паллетизацией
            matched_cluster_ids: set = set()

            results: List[Dict[str, Any]] = []
            for cluster_input in clusters_input:
                cluster_name = str(cluster_input.get("cluster_name") or "").strip()
                items_raw = cluster_input.get("items") or []

                # Матчим кластер по имени (точное, затем частичное)
                cluster = by_name.get(cluster_name.lower())
                if not cluster:
                    for key, val in by_name.items():
                        if cluster_name.lower() in key or key in cluster_name.lower():
                            cluster = val
                            break

                if not cluster:
                    results.append({
                        "cluster_name": cluster_name,
                        "status": "not_found",
                        "error": f"Кластер '{cluster_name}' не найден в черновике. Доступны: {list(by_name.keys())}",
                    })
                    continue

                cluster_id = str(cluster.get("macrolocalClusterId") or "")
                matched_name = str(cluster.get("name") or "")
                matched_cluster_ids.add(cluster_id)

                try:
                    if dry_run:
                        results.append({
                            "cluster_name": cluster_name,
                            "matched_name": matched_name,
                            "cluster_id": cluster_id,
                            "status": "dry_run",
                            "items_count": len(items_raw),
                            "items": items_raw,
                        })
                        continue

                    # 1. Отметить кластер выбранным
                    await bff_fetch(page, "/api/supplier-drafts/bff/v1/update-is-cluster-selected", {
                        "companyId": _COMPANY_ID,
                        "draftId": draft_id,
                        "isSelected": True,
                        "macrolocalClusterId": cluster_id,
                    })

                    # 2. Открыть редактирование ассортимента → получить editingBundleId
                    edit_resp = await bff_fetch(
                        page,
                        "/api/supplier-drafts/bff/v1/edit-cluster-assortment",
                        {"draftId": draft_id, "macrolocalClusterId": cluster_id, "companyId": _COMPANY_ID},
                    )
                    editing_bundle_id = str((edit_resp or {}).get("editingBundleId") or "")
                    bundle_id = editing_bundle_id or str(cluster.get("bundleId") or "")

                    # 3. Очистить существующие товары бандла (рекомендации Ozon)
                    await bff_fetch(
                        page,
                        "/api/supplier-product-bundles-bff/v1/draft/clear",
                        {
                            "marketplaceCompanyId": _COMPANY_ID,
                            "supplierProductBundleId": bundle_id,
                        },
                    )

                    # 4. Загрузить товары
                    upsert_items = []
                    requested_rows = []
                    for it in items_raw:
                        sku = it.get("sku")
                        qty = int(it.get("quantity") or 0)
                        if sku and qty > 0:
                            upsert_items.append({"sku": int(sku), "quant": 1, "quantity": qty})
                            requested_rows.append(
                                {
                                    "sku": str(sku),
                                    "requested_quantity": qty,
                                }
                            )

                    if upsert_items:
                        await bff_fetch(
                            page,
                            "/api/supplier-product-bundles-bff/v1/draft/upsert-items",
                            {
                                "marketplaceCompanyId": _COMPANY_ID,
                                "supplierProductBundleId": bundle_id,
                                "items": upsert_items,
                            },
                        )

                    # 5. Сохранить ассортимент
                    save_resp = await bff_fetch(
                        page,
                        "/api/supplier-drafts/bff/v1/save-cluster-assortment",
                        {
                            "macrolocalClusterId": cluster_id,
                            "draftId": draft_id,
                            "companyId": _COMPANY_ID,
                            "editingBundleId": editing_bundle_id,
                        },
                    )
                    final_bundle_id = str(
                        ((save_resp or {}).get("success") or {}).get("bundleId")
                        or str(cluster.get("bundleId") or "")
                        or bundle_id
                    )

                    accepted_items = []
                    removed_items = []
                    for row in requested_rows:
                        sku_value = str(row["sku"])
                        requested_qty = int(row["requested_quantity"] or 0)
                        actual_item = await _get_cluster_assortment_item(
                            page,
                            draft_id,
                            cluster_id,
                            final_bundle_id,
                            sku_value,
                        )
                        matched_item = None
                        for candidate in actual_item.get("items") or []:
                            if str(candidate.get("sku") or "").strip() == sku_value:
                                matched_item = candidate
                                break

                        rejected_reasons = []
                        restriction_reasons = []
                        is_contained = False
                        if isinstance(matched_item, dict):
                            rejected_reasons = [str(x) for x in (matched_item.get("rejectedReasons") or []) if x]
                            restriction_reasons = [str(x) for x in (matched_item.get("restrictionReasons") or []) if x]
                            is_contained = bool(matched_item.get("isContainedInDestinationBundle"))

                        if matched_item and is_contained and not rejected_reasons and not restriction_reasons:
                            accepted_items.append(
                                {
                                    "sku": sku_value,
                                    "requested_quantity": requested_qty,
                                    "accepted_quantity": requested_qty,
                                }
                            )
                        else:
                            removed_items.append(
                                {
                                    "sku": sku_value,
                                    "requested_quantity": requested_qty,
                                    "accepted_quantity": 0,
                                    "removed_quantity": requested_qty,
                                    "rejected_reasons": rejected_reasons,
                                    "restriction_reasons": restriction_reasons,
                                }
                            )

                    if requested_rows and not accepted_items:
                        try:
                            await bff_fetch(page, "/api/supplier-drafts/bff/v1/update-is-cluster-selected", {
                                "companyId": _COMPANY_ID,
                                "draftId": draft_id,
                                "isSelected": False,
                                "macrolocalClusterId": cluster_id,
                            })
                        except Exception:
                            pass

                    results.append({
                        "cluster_name": cluster_name,
                        "matched_name": matched_name,
                        "cluster_id": cluster_id,
                        "bundle_id": final_bundle_id,
                        "status": "ok",
                        "items_count": len(upsert_items),
                        "accepted_items": accepted_items,
                        "removed_items": removed_items,
                    })

                except Exception as cluster_err:
                    results.append({
                        "cluster_name": cluster_name,
                        "cluster_id": cluster_id,
                        "status": "error",
                        "error": str(cluster_err),
                    })

            # Снять выбор с кластеров черновика, которых нет в паллетизации
            if not dry_run:
                deselected = []
                for dc in draft_clusters:
                    dc_id = str(dc.get("macrolocalClusterId") or "")
                    dc_name = str(dc.get("name") or "")
                    if dc_id and dc_id not in matched_cluster_ids:
                        try:
                            await bff_fetch(page, "/api/supplier-drafts/bff/v1/update-is-cluster-selected", {
                                "companyId": _COMPANY_ID,
                                "draftId": draft_id,
                                "isSelected": False,
                                "macrolocalClusterId": dc_id,
                            })
                            deselected.append(dc_name or dc_id)
                        except Exception as desel_err:
                            results.append({
                                "cluster_name": dc_name,
                                "cluster_id": dc_id,
                                "status": "deselect_error",
                                "error": str(desel_err),
                            })
            else:
                deselected = [
                    str(dc.get("name") or dc.get("macrolocalClusterId") or "")
                    for dc in draft_clusters
                    if str(dc.get("macrolocalClusterId") or "") not in matched_cluster_ids
                    and str(dc.get("macrolocalClusterId") or "")
                ]

        ok = sum(1 for r in results if r["status"] == "ok")
        err = sum(1 for r in results if r["status"] in ("error", "deselect_error"))
        removed_total = sum(
            sum(int(item.get("removed_quantity") or 0) for item in (result.get("removed_items") or []))
            for result in results
        )
        return web.json_response({
            "success": True,
            "draft_id": draft_id,
            "dry_run": dry_run,
            "draft_url": f"https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}",
            "ok": ok,
            "errors": err,
            "removed_total": removed_total,
            "deselected_clusters": deselected,
            "results": results,
        })

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"ERROR in supply_fill_draft: {error_details}")
        return web.json_response(
            {"success": False, "error": str(e), "details": error_details}, status=500
        )


async def supply_reconcile_draft_quantities(request: web.Request) -> web.Response:
    """POST /api/supply-plan/reconcile-draft-quantities — сверка количеств в черновике с паллетизацией.

    Вызывается перед/после сохранения слотов (этап 2), чтобы обнаружить урезания складами.

    Body:
        draft_id: str
        clusters: [{cluster_name, items: [{sku, quantity}]}]

    Для каждого кластера/SKU вызывает get-assortment-for-multi-cluster-draft
    и сравнивает фактическое количество с запрошенным.
    """
    import traceback
    try:
        from src.chrome_browser import OzonBrowser, bff_fetch, COMPANY_ID as _COMPANY_ID

        body = await request.json() if request.body_exists else {}
        draft_id = str(body.get("draft_id") or "").strip()
        clusters_input = body.get("clusters") or []

        if not draft_id:
            return web.json_response({"success": False, "error": "draft_id is required"}, status=400)
        if not clusters_input:
            return web.json_response({"success": False, "error": "clusters is required"}, status=400)

        async with OzonBrowser("seller.ozon.ru/app/supply") as page:
            # Получаем данные черновика
            draft = await bff_fetch(
                page,
                "/api/supplier-drafts/api/v4/get",
                {"companyId": _COMPANY_ID, "draftId": draft_id},
            )
            draft_clusters = (
                (draft.get("draft") or {}).get("multiCluster") or {}
            ).get("clusterInfos") or []

            by_name = {str(c.get("name") or "").strip().lower(): c for c in draft_clusters}

            results: List[Dict[str, Any]] = []
            changed_clusters: List[str] = []
            removed_total = 0

            for cluster_input in clusters_input:
                cluster_name = str(cluster_input.get("cluster_name") or "").strip()
                items_raw = cluster_input.get("items") or []
                if not cluster_name or not items_raw:
                    continue

                # Матчим кластер
                cluster = by_name.get(cluster_name.lower())
                if not cluster:
                    for key, val in by_name.items():
                        if cluster_name.lower() in key or key in cluster_name.lower():
                            cluster = val
                            break

                if not cluster:
                    results.append({
                        "cluster_name": cluster_name,
                        "status": "not_found",
                        "error": f"Кластер '{cluster_name}' не найден в черновике",
                        "items": [],
                    })
                    continue

                cluster_id = str(cluster.get("macrolocalClusterId") or "")
                matched_name = str(cluster.get("name") or "")
                bundle_id = str(cluster.get("bundleId") or "")

                cluster_result: Dict[str, Any] = {
                    "cluster_name": cluster_name,
                    "matched_name": matched_name,
                    "cluster_id": cluster_id,
                    "status": "ok",
                    "items": [],
                }
                cluster_changed = False

                for it in items_raw:
                    sku = str(it.get("sku") or "").strip()
                    requested_qty = int(it.get("quantity") or 0)
                    if not sku or requested_qty <= 0:
                        continue

                    # Запрос ассортимента по SKU
                    try:
                        assortment_resp = await bff_fetch(
                            page,
                            "/api/supplier-drafts/api/v1/get-assortment-for-multi-cluster-draft",
                            {
                                "editingBundleId": bundle_id,
                                "companyId": _COMPANY_ID,
                                "macrolocalClusterId": cluster_id,
                                "draftId": draft_id,
                                "searchString": sku,
                            },
                        )
                    except Exception as e:
                        cluster_result["items"].append({
                            "sku": sku,
                            "requested_quantity": requested_qty,
                            "accepted_quantity": requested_qty,
                            "removed_quantity": 0,
                            "error": str(e),
                        })
                        continue

                    # Ищем совпадение по SKU
                    matched_item = None
                    for candidate in assortment_resp.get("items") or []:
                        if str(candidate.get("sku") or "").strip() == sku:
                            matched_item = candidate
                            break

                    if matched_item:
                        is_contained = bool(matched_item.get("isContainedInDestinationBundle"))
                        rejected_reasons = [str(x) for x in (matched_item.get("rejectedReasons") or []) if x]
                        restriction_reasons = [str(x) for x in (matched_item.get("restrictionReasons") or []) if x]
                        # Фактическое количество из бандла
                        actual_qty = int(matched_item.get("quantity") or 0)
                        if actual_qty <= 0 and is_contained:
                            actual_qty = requested_qty  # если quantity не указано, считаем принятым

                        removed_qty = max(0, requested_qty - actual_qty) if actual_qty > 0 else (requested_qty if not is_contained else 0)
                        if removed_qty > 0 or rejected_reasons or restriction_reasons:
                            cluster_changed = True
                            removed_total += removed_qty

                        cluster_result["items"].append({
                            "sku": sku,
                            "requested_quantity": requested_qty,
                            "accepted_quantity": max(0, actual_qty) if actual_qty > 0 else (requested_qty if is_contained else 0),
                            "removed_quantity": removed_qty,
                            "is_contained": is_contained,
                            "rejected_reasons": rejected_reasons,
                            "restriction_reasons": restriction_reasons,
                        })
                    else:
                        # SKU не найден в ассортименте — полностью снят
                        cluster_changed = True
                        removed_total += requested_qty
                        cluster_result["items"].append({
                            "sku": sku,
                            "requested_quantity": requested_qty,
                            "accepted_quantity": 0,
                            "removed_quantity": requested_qty,
                            "is_contained": False,
                            "rejected_reasons": ["SKU не найден в ассортименте кластера"],
                            "restriction_reasons": [],
                        })

                if cluster_changed:
                    changed_clusters.append(matched_name)
                results.append(cluster_result)

        return web.json_response({
            "success": True,
            "draft_id": draft_id,
            "clusters": results,
            "changed_clusters": changed_clusters,
            "removed_total": removed_total,
        })

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"ERROR in supply_reconcile_draft_quantities: {error_details}")
        return web.json_response(
            {"success": False, "error": str(e), "details": error_details}, status=500
        )


async def supply_check_drafts(request: web.Request) -> web.Response:
    """POST /api/supply-plan/check-drafts — проверка статуса поставок.

    Body (все поля опциональны):
        limit: int          — макс. кол-во заявок (default 20, max 100)
        states: list[str]   — фильтр по статусам (default: все актуальные)
        with_items: bool    — загружать товары из грузомест (default true)

    Использует Seller API:
        /v3/supply-order/list  — список заявок
        /v3/supply-order/get   — детали заявок (supplies, bundle_id, склады)
        /v1/supply-order/bundle — товары в грузоместах
    """
    import traceback
    try:
        body = await request.json() if request.body_exists else {}
        limit = max(1, min(100, int(body.get("limit") or 20)))
        with_items = body.get("with_items", True)
        states = body.get("states") or [
            "DATA_FILLING", "CREATED", "CONFIRMED",
            "IN_PROGRESS", "ACCEPTED_AT_SUPPLY_WAREHOUSE",
        ]

        client_id = (
            os.getenv("OZON_CLIENT_ID")
            or getattr(settings, "ozon_client_id", "")
            or _get_env_from_dotenv("OZON_CLIENT_ID")
            or ""
        ).strip()
        api_key = (
            os.getenv("OZON_SUPPLY_API_KEY")
            or os.getenv("OZON_API_KEY")
            or _get_env_from_dotenv("OZON_SUPPLY_API_KEY")
            or _get_env_from_dotenv("OZON_API_KEY")
            or getattr(settings, "ozon_api_key", "")
            or ""
        ).strip()
        if not client_id or not api_key:
            return web.json_response(
                {"success": False, "error": "Missing OZON_CLIENT_ID/OZON_SUPPLY_API_KEY"}, status=400
            )

        headers = {
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=90)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Получаем список order_id
            list_status, list_data = await _ozon_supply_post(
                session, "/v3/supply-order/list", headers, {
                    "filter": {"states": states},
                    "last_id": "",
                    "limit": limit,
                    "sort_by": "ORDER_CREATION",
                    "sort_dir": "DESC",
                },
            )
            if list_status != 200:
                return web.json_response({
                    "success": False,
                    "error": f"supply-order/list HTTP {list_status}: {json.dumps(list_data, ensure_ascii=False)[:400]}",
                }, status=502)

            order_ids = list_data.get("order_ids") or []
            if not order_ids:
                return web.json_response({
                    "success": True,
                    "orders": [],
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "note": "Нет заявок на поставку",
                })

            # 2. Получаем детали заявок (батчами по 50)
            all_orders: List[Dict[str, Any]] = []
            for i in range(0, len(order_ids), 50):
                batch = order_ids[i:i + 50]
                get_status, get_data = await _ozon_supply_post(
                    session, "/v3/supply-order/get", headers, {"order_ids": batch},
                )
                if get_status == 200:
                    all_orders.extend(get_data.get("orders") or [])

            # 3. Собираем все bundle_id и запрашиваем товары
            bundle_items_map: Dict[str, List[Dict[str, Any]]] = {}
            if with_items:
                all_bundle_ids: List[str] = []
                for order in all_orders:
                    for supply in order.get("supplies") or []:
                        bid = str(supply.get("bundle_id") or "").strip()
                        if bid and bid not in all_bundle_ids:
                            all_bundle_ids.append(bid)

                # Запрашиваем товары батчами по 50 bundle_id
                for i in range(0, len(all_bundle_ids), 50):
                    batch_bids = all_bundle_ids[i:i + 50]
                    b_status, b_data = await _ozon_supply_post(
                        session, "/v1/supply-order/bundle", headers, {
                            "bundle_ids": batch_bids,
                            "limit": 100,
                        },
                    )
                    if b_status == 200:
                        for item in b_data.get("items") or []:
                            # API не возвращает bundle_id в items — маппим по порядку
                            pass
                        # bundle endpoint возвращает items без привязки к bundle_id
                        # нужно запрашивать по одному
                        pass

                # Запрашиваем по одному bundle_id для точного маппинга
                for bid in all_bundle_ids:
                    try:
                        b_status, b_data = await _ozon_supply_post(
                            session, "/v1/supply-order/bundle", headers, {
                                "bundle_ids": [bid],
                                "limit": 100,
                            },
                        )
                        if b_status == 200:
                            bundle_items_map[bid] = b_data.get("items") or []
                    except Exception:
                        pass

            # 4. Формируем результат
            results: List[Dict[str, Any]] = []
            for order in all_orders:
                order_id = order.get("order_id")
                order_number = order.get("order_number", "")
                state = order.get("state", "")
                created = order.get("created_date", "")
                state_updated = order.get("state_updated_date", "")

                drop_off = order.get("drop_off_warehouse") or {}
                timeslot_info = order.get("timeslot") or {}
                ts = timeslot_info.get("timeslot") or {}

                supplies_raw = order.get("supplies") or []
                supplies_out: List[Dict[str, Any]] = []
                total_items_count = 0

                for supply in supplies_raw:
                    bid = str(supply.get("bundle_id") or "").strip()
                    storage_wh = supply.get("storage_warehouse") or {}
                    items = bundle_items_map.get(bid, [])
                    items_count = len(items)
                    items_qty = sum(int(it.get("quantity") or 0) for it in items)
                    total_items_count += items_count

                    supplies_out.append({
                        "supply_id": supply.get("supply_id"),
                        "state": supply.get("state", ""),
                        "bundle_id": bid or None,
                        "has_items": items_count > 0,
                        "items_count": items_count,
                        "items_total_qty": items_qty,
                        "is_crossdock": supply.get("is_crossdock", False),
                        "warehouse_name": storage_wh.get("name", ""),
                        "warehouse_address": (storage_wh.get("address") or "")[:100],
                        "items": [
                            {
                                "sku": it.get("sku"),
                                "offer_id": it.get("offer_id", ""),
                                "name": (it.get("name") or "")[:60],
                                "quantity": it.get("quantity", 0),
                            }
                            for it in items
                        ] if with_items else [],
                    })

                results.append({
                    "order_id": order_id,
                    "order_number": order_number,
                    "state": state,
                    "created_date": created,
                    "state_updated_date": state_updated,
                    "data_filling_deadline": order.get("data_filling_deadline", ""),
                    "drop_off_warehouse": drop_off.get("name", ""),
                    "drop_off_address": (drop_off.get("address") or "")[:120],
                    "timeslot_from": ts.get("from", ""),
                    "timeslot_to": ts.get("to", ""),
                    "supplies_count": len(supplies_out),
                    "total_items_count": total_items_count,
                    "supplies": supplies_out,
                })

        return web.json_response({
            "success": True,
            "orders": results,
            "total_orders": len(results),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"ERROR in supply_check_drafts: {error_details}")
        return web.json_response(
            {"success": False, "error": str(e), "details": error_details}, status=500
        )


async def supply_set_vehicle_pass(request: web.Request) -> web.Response:
    """POST /api/supply-plan/set-vehicle-pass — заполнить данные автомобиля и водителя.

    Body:
        order_ids: list[int]       — ID заявок для заполнения
        vehicle_model: str         — марка/модель авто
        vehicle_number: str        — госномер
        driver_name: str           — ФИО водителя
        driver_phone: str          — телефон водителя
    """
    import traceback
    try:
        body = await request.json() if request.body_exists else {}
        order_ids_raw = body.get("order_ids") or []
        vehicle_model = str(body.get("vehicle_model") or "").strip()
        vehicle_number = str(body.get("vehicle_number") or "").strip()
        driver_name = str(body.get("driver_name") or "").strip()
        driver_phone = str(body.get("driver_phone") or "").strip()

        if not order_ids_raw:
            return web.json_response({"success": False, "error": "order_ids is required"}, status=400)
        if not vehicle_model or not vehicle_number:
            return web.json_response({"success": False, "error": "vehicle_model and vehicle_number are required"}, status=400)
        if not driver_name or not driver_phone:
            return web.json_response({"success": False, "error": "driver_name and driver_phone are required"}, status=400)

        order_ids = [_to_int(x) for x in order_ids_raw if _to_int(x) and _to_int(x) > 0]
        if not order_ids:
            return web.json_response({"success": False, "error": "No valid order_ids"}, status=400)

        client_id = (
            os.getenv("OZON_CLIENT_ID")
            or getattr(settings, "ozon_client_id", "")
            or _get_env_from_dotenv("OZON_CLIENT_ID")
            or ""
        ).strip()
        api_key = (
            os.getenv("OZON_SUPPLY_API_KEY")
            or os.getenv("OZON_API_KEY")
            or _get_env_from_dotenv("OZON_SUPPLY_API_KEY")
            or _get_env_from_dotenv("OZON_API_KEY")
            or getattr(settings, "ozon_api_key", "")
            or ""
        ).strip()
        if not client_id or not api_key:
            return web.json_response(
                {"success": False, "error": "Missing OZON_CLIENT_ID/OZON_SUPPLY_API_KEY"}, status=400
            )

        headers = {
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=60)

        results: List[Dict[str, Any]] = []
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for oid in order_ids:
                pass_body = {
                    "supply_order_id": oid,
                    "vehicle": {
                        "vehicle_model": vehicle_model,
                        "vehicle_number": vehicle_number,
                        "driver_name": driver_name,
                        "driver_phone": driver_phone,
                    },
                }
                try:
                    status, data = await _ozon_supply_post(
                        session, "/v1/supply-order/pass/create", headers, pass_body
                    )
                    results.append({
                        "order_id": oid,
                        "status": status,
                        "success": status == 200 and not (data.get("error_reasons") or []),
                        "operation_id": data.get("operation_id"),
                        "error_reasons": data.get("error_reasons") or [],
                        "error": None if status == 200 else json.dumps(data, ensure_ascii=False)[:300],
                    })
                except Exception as e:
                    results.append({
                        "order_id": oid,
                        "status": 0,
                        "success": False,
                        "error": str(e),
                    })

        ok_count = sum(1 for r in results if r.get("success"))
        return web.json_response({
            "success": True,
            "results": results,
            "total": len(results),
            "ok_count": ok_count,
            "fail_count": len(results) - ok_count,
        })

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"ERROR in supply_set_vehicle_pass: {error_details}")
        return web.json_response(
            {"success": False, "error": str(e), "details": error_details}, status=500
        )

