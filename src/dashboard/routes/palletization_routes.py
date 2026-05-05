"""Dashboard routes/palletization_routes.py handlers."""
import io
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import pandas as pd
from aiohttp import web

from src.dashboard.constants import (
    BASE_DIR, PALLETIZATION_IMPORT_ARTICLE_COLUMNS, PALLETIZATION_IMPORT_ITEMS_PER_LAYER_COLUMNS,
)
from src.dashboard import state
from src.dashboard.helpers import (
    normalize_offer_id, as_float, _normalize_column_name, _pick_df_column, _to_int,
)
from src.palletization.calculator import calculate_pallets_from_supply_plan, calculate_pallets_for_cluster, filter_small_pallets
from src.palletization.database import (
    add_shipment_item as pallet_add_shipment_item,
    clear_shipment as pallet_clear_shipment,
    get_shipment_by_cluster as pallet_get_shipment_by_cluster,
    get_shipment_items as pallet_get_shipment_items,
)


async def _fetch_palletization_product_rows(
    conn: asyncpg.Connection,
    product_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    params: List[Any] = []
    where_sql = ""
    if product_ids:
        where_sql = "WHERE p.product_id = any($1::bigint[])"
        params.append(product_ids)
    rows = await conn.fetch(
        f"""
        SELECT
            p.product_id,
            p.offer_id,
            coalesce(ac.article_name, p.name, p.offer_id) AS article_name,
            ac.sku,
            ac.is_kgt,
            ac.shipment_type,
            ac.height_mm,
            ac.weight_g,
            pp.items_per_layer,
            pp.updated_at AS params_updated_at,
            ac.updated_at AS characteristics_updated_at
        FROM products p
        LEFT JOIN LATERAL (
            SELECT fbo_sku_id
            FROM report_products_items rpi
            WHERE rpi.ozon_product_id = p.product_id AND rpi.fbo_sku_id IS NOT NULL
            ORDER BY rpi.id DESC
            LIMIT 1
        ) sku_map ON true
        LEFT JOIN article_characteristics ac
          ON ac.sku = sku_map.fbo_sku_id
        LEFT JOIN palletization_product_params pp
          ON pp.product_id = p.product_id
        {where_sql}
        ORDER BY p.offer_id NULLS LAST, p.product_id
        """,
        *params,
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        height_mm = _to_int(row["height_mm"])
        weight_g = _to_int(row["weight_g"])
        items.append(
            {
                "product_id": _to_int(row["product_id"]),
                "offer_id": row["offer_id"],
                "sku": _to_int(row["sku"]),
                "name": row["article_name"],
                "article_name": row["article_name"],
                "is_kgt": row["is_kgt"],
                "shipment_type": row["shipment_type"],
                "height_mm": height_mm,
                "weight_g": weight_g,
                "layer_height": round(height_mm / 1000.0, 3) if height_mm else None,
                "weight_per_item": round(weight_g / 1000.0, 3) if weight_g else None,
                "items_per_layer": _to_int(row["items_per_layer"]),
                "params_updated_at": row["params_updated_at"].isoformat() if row["params_updated_at"] else None,
                "characteristics_updated_at": (
                    row["characteristics_updated_at"].isoformat() if row["characteristics_updated_at"] else None
                ),
            }
        )
    return items


def _load_legacy_palletization_products() -> Dict[str, Dict[str, Dict[str, Any]]]:
    if state._LEGACY_PALLETIZATION_CACHE is not None:
        return state._LEGACY_PALLETIZATION_CACHE

    legacy_path = BASE_DIR / "palletization.db"
    lookup: Dict[str, Dict[str, Dict[str, Any]]] = {"by_offer": {}, "by_sku": {}}
    if not legacy_path.exists():
        state._LEGACY_PALLETIZATION_CACHE = lookup
        return lookup

    try:
        conn = sqlite3.connect(str(legacy_path))
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT sku, name, layer_height, items_per_layer, weight_per_item FROM products")
            for sku, name, layer_height, items_per_layer, weight_per_item in cursor.fetchall():
                raw_sku = str(sku or "").strip()
                offer_norm = normalize_offer_id(raw_sku)
                legacy_payload = {
                    "offer_id": sku,
                    "name": name,
                    "layer_height": as_float(layer_height, default=0.0),
                    "items_per_layer": _to_int(items_per_layer) or 0,
                    "weight_per_item": as_float(weight_per_item, default=0.0),
                }
                if offer_norm:
                    lookup["by_offer"][offer_norm] = legacy_payload
                legacy_sku = _to_int(raw_sku)
                if not legacy_sku:
                    continue
                lookup["by_sku"][str(legacy_sku)] = legacy_payload
        finally:
            conn.close()
    except Exception:
        lookup = {"by_offer": {}, "by_sku": {}}

    state._LEGACY_PALLETIZATION_CACHE = lookup
    return lookup


async def _build_palletization_products_map(pool: asyncpg.Pool) -> Dict[str, Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await _fetch_palletization_product_rows(conn)
    legacy_lookup = _load_legacy_palletization_products()
    products_db: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        product_id = _to_int(row.get("product_id"))
        sku = _to_int(row.get("sku"))
        offer_norm = normalize_offer_id(row.get("offer_id"))
        legacy_row = None
        if offer_norm:
            legacy_row = legacy_lookup.get("by_offer", {}).get(offer_norm)
        if legacy_row is None and sku:
            legacy_row = legacy_lookup.get("by_sku", {}).get(str(sku))
        layer_height = as_float(row.get("layer_height"), default=0.0)
        items_per_layer = _to_int(row.get("items_per_layer")) or 0
        weight_per_item = as_float(row.get("weight_per_item"), default=0.0)
        if legacy_row:
            if layer_height <= 0:
                layer_height = as_float(legacy_row.get("layer_height"), default=0.0)
            if items_per_layer <= 0:
                items_per_layer = _to_int(legacy_row.get("items_per_layer")) or 0
            if weight_per_item <= 0:
                weight_per_item = as_float(legacy_row.get("weight_per_item"), default=0.0)
        payload = {
            "product_id": product_id,
            "offer_id": row.get("offer_id"),
            "name": row.get("name") or row.get("offer_id") or (str(sku) if sku else ""),
            "layer_height": layer_height if layer_height > 0 else None,
            "items_per_layer": items_per_layer if items_per_layer > 0 else None,
            "weight_per_item": weight_per_item if weight_per_item > 0 else None,
        }
        if product_id:
            products_db[f"product:{product_id}"] = payload
        if sku:
            products_db[f"sku:{sku}"] = payload
        if offer_norm:
            products_db[f"offer:{offer_norm}"] = payload
    return products_db


async def palletization_products_get(_: web.Request) -> web.Response:
    pool = _.app["pool"]
    async with pool.acquire() as conn:
        products = await _fetch_palletization_product_rows(conn)
    return web.json_response({"success": True, "products": products})


async def palletization_products_create(request: web.Request) -> web.Response:
    data = await request.json()
    product_id = _to_int(data.get("product_id"))
    items_per_layer = max(0, _to_int(data.get("items_per_layer")) or 0)
    if not product_id:
        return web.json_response({"success": False, "error": "product_id is required"}, status=400)
    async with request.app["pool"].acquire() as conn:
        exists = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM products WHERE product_id = $1)", product_id)
        if not exists:
            return web.json_response({"success": False, "error": "Product not found"}, status=404)
        await conn.execute(
            """
            INSERT INTO palletization_product_params (product_id, items_per_layer, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (product_id) DO UPDATE
            SET items_per_layer = EXCLUDED.items_per_layer,
                updated_at = now()
            """,
            product_id,
            items_per_layer,
        )
    return web.json_response({"success": True})


async def palletization_products_update(request: web.Request) -> web.Response:
    product_id = _to_int(request.match_info.get("sku"))
    data = await request.json()
    items_per_layer = max(0, _to_int(data.get("items_per_layer")) or 0)
    if not product_id:
        return web.json_response({"success": False, "error": "product_id is required"}, status=400)
    async with request.app["pool"].acquire() as conn:
        await conn.execute(
            """
            INSERT INTO palletization_product_params (product_id, items_per_layer, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (product_id) DO UPDATE
            SET items_per_layer = EXCLUDED.items_per_layer,
                updated_at = now()
            """,
            product_id,
            items_per_layer,
        )
    return web.json_response({"success": True})


async def palletization_products_delete(request: web.Request) -> web.Response:
    product_id = _to_int(request.match_info.get("sku"))
    if not product_id:
        return web.json_response({"success": False, "error": "product_id is required"}, status=400)
    async with request.app["pool"].acquire() as conn:
        result = await conn.execute(
            "DELETE FROM palletization_product_params WHERE product_id = $1",
            product_id,
        )
    return web.json_response({"success": result.endswith("1")})


async def palletization_products_import(request: web.Request) -> web.Response:
    try:
        form = await request.post()
        file_field = form.get("file")
        if file_field is None:
            return web.json_response({"success": False, "error": "Файл не найден"}, status=400)
        filename = str(getattr(file_field, "filename", "") or "")
        if not filename.lower().endswith(".xlsx"):
            return web.json_response({"success": False, "error": "Поддерживаются только .xlsx"}, status=400)
        content = file_field.file.read()
        df = pd.read_excel(io.BytesIO(content))
        article_col = _pick_df_column(list(df.columns), PALLETIZATION_IMPORT_ARTICLE_COLUMNS)
        items_col = _pick_df_column(list(df.columns), PALLETIZATION_IMPORT_ITEMS_PER_LAYER_COLUMNS)
        if not article_col or not items_col:
            return web.json_response(
                {
                    "success": False,
                    "error": "В файле нужны колонки артикул и количество в слое",
                },
                status=400,
            )
        imported = 0
        errors: List[str] = []
        async with request.app["pool"].acquire() as conn:
            product_rows = await _fetch_palletization_product_rows(conn)
            by_offer = {
                normalize_offer_id(row.get("offer_id")): row
                for row in product_rows
                if normalize_offer_id(row.get("offer_id"))
            }
            by_sku = {
                str(_to_int(row.get("sku"))): row
                for row in product_rows
                if _to_int(row.get("sku"))
            }
            for idx, row in df.iterrows():
                article_raw = str(row.get(article_col) or "").strip()
                if not article_raw or article_raw.lower() == "nan":
                    continue
                items_val = _to_int(row.get(items_col))
                if items_val is None:
                    errors.append(f"{article_raw}: не заполнено количество в слое")
                    continue
                product = by_offer.get(normalize_offer_id(article_raw)) or by_sku.get(article_raw)
                if not product:
                    errors.append(f"{article_raw}: товар не найден в products")
                    continue
                await conn.execute(
                    """
                    INSERT INTO palletization_product_params (product_id, items_per_layer, updated_at)
                    VALUES ($1, $2, now())
                    ON CONFLICT (product_id) DO UPDATE
                    SET items_per_layer = EXCLUDED.items_per_layer,
                        updated_at = now()
                    """,
                    _to_int(product.get("product_id")),
                    max(0, int(items_val)),
                )
                imported += 1
        return web.json_response({"success": True, "imported": int(imported), "errors": errors})
    except Exception as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=500)


async def palletization_shipment_get(_: web.Request) -> web.Response:
    return web.json_response({"success": True, "items": pallet_get_shipment_items()})


async def palletization_shipment_create(request: web.Request) -> web.Response:
    data = await request.json()
    success = pallet_add_shipment_item(
        sku=str(data.get("sku") or "").strip(),
        cluster=str(data.get("cluster") or "").strip(),
        quantity=int(data.get("quantity") or 0),
        shipment_date=data.get("shipment_date"),
    )
    return web.json_response({"success": bool(success)})


async def palletization_shipment_bulk(request: web.Request) -> web.Response:
    data = await request.json()
    items = data.get("items") or []
    success_count = 0
    errors: List[str] = []
    for item in items:
        try:
            ok = pallet_add_shipment_item(
                sku=str(item.get("sku") or "").strip(),
                cluster=str(item.get("cluster") or "").strip(),
                quantity=int(item.get("quantity") or 0),
                shipment_date=item.get("shipment_date"),
            )
            if ok:
                success_count += 1
            else:
                errors.append(f"{item.get('sku')}: ошибка сохранения")
        except Exception as exc:
            errors.append(f"{item.get('sku')}: {exc}")
    return web.json_response({"success": True, "imported": success_count, "errors": errors})


async def palletization_shipment_clear(_: web.Request) -> web.Response:
    pallet_clear_shipment()
    return web.json_response({"success": True})


async def palletization_shipment_missing(_: web.Request) -> web.Response:
    shipment_items = pallet_get_shipment_items()
    pool = _.app["pool"]
    products_db = await _build_palletization_products_map(pool)
    missing: List[str] = []
    seen: set[str] = set()
    for item in shipment_items:
        sku_raw = str(item.get("sku") or "").strip()
        offer_norm = normalize_offer_id(sku_raw)
        if (
            (sku_raw and products_db.get(f"sku:{sku_raw}"))
            or (offer_norm and products_db.get(f"offer:{offer_norm}"))
        ):
            continue
        if sku_raw and sku_raw not in seen:
            seen.add(sku_raw)
            missing.append(sku_raw)
    return web.json_response({"success": True, "missing_products": missing})


async def palletization_pallets_calculate(_: web.Request) -> web.Response:
    products_db = await _build_palletization_products_map(_.app["pool"])
    shipment_by_cluster = pallet_get_shipment_by_cluster()
    clusters: List[Dict[str, Any]] = []
    for cluster_name, rows in shipment_by_cluster.items():
        cluster_items = [
            {
                "sku": str(r.get("sku") or "").strip(),
                "offer_id": str(r.get("sku") or "").strip(),
                "quantity": int(r.get("quantity") or 0),
                "cluster": cluster_name,
            }
            for r in rows
            if r.get("sku") and int(r.get("quantity") or 0) > 0
        ]
        clusters.append(calculate_pallets_for_cluster(cluster_items, products_db))
    return web.json_response({"success": True, "clusters": clusters})

