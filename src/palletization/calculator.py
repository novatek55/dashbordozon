from dataclasses import dataclass
import re
from typing import Dict, List, Tuple

MAX_PALLET_HEIGHT = 1.8  # meters


@dataclass
class PalletItem:
    product_id: int | None
    sku: str
    offer_id: str
    name: str
    quantity: int
    item_height: float
    item_weight: float
    layers: int


@dataclass
class Pallet:
    pallet_number: int
    items: List[PalletItem]
    total_height: float
    total_weight: float

    def to_dict(self) -> Dict:
        return {
            "pallet_number": self.pallet_number,
            "total_height": round(self.total_height, 3),
            "total_weight": round(self.total_weight, 2),
            "items": [
                {
                    "product_id": item.product_id,
                    "sku": item.sku,
                    "offer_id": item.offer_id,
                    "name": item.name,
                    "quantity": item.quantity,
                    "height": round(item.item_height, 3),
                    "weight": round(item.item_weight, 2),
                    "layers": item.layers,
                }
                for item in self.items
            ],
        }


def calculate_item_height(quantity: int, items_per_layer: int, layer_height: float) -> Tuple[float, int]:
    if items_per_layer <= 0:
        return 0.0, 0
    layers = (int(quantity) + int(items_per_layer) - 1) // int(items_per_layer)
    return float(layers * float(layer_height)), int(layers)


def _normalize_offer_lookup(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _resolve_product(products_db: Dict, item: Dict) -> Dict | None:
    product_id = item.get("product_id")
    if product_id not in (None, ""):
        product = products_db.get(f"product:{int(product_id)}")
        if product:
            return product

    sku = str(item.get("sku") or "").strip()
    if sku:
        product = products_db.get(f"sku:{sku}")
        if product:
            return product

    offer_norm = _normalize_offer_lookup(item.get("offer_id"))
    if offer_norm:
        return products_db.get(f"offer:{offer_norm}")
    return None


def _split_item_to_height_limited_chunks(item: Dict) -> Tuple[List[Dict], bool]:
    """
    Split SKU only if this SKU itself exceeds pallet height limit.
    """
    layers_total = int(item.get("layers") or 0)
    layer_height = float(item.get("layer_height") or 0.0)
    items_per_layer = int(item.get("items_per_layer") or 0)
    quantity_total = int(item.get("quantity") or 0)
    weight_per_item = float(item.get("weight_per_item") or 0.0)

    if layers_total <= 0 or layer_height <= 0 or items_per_layer <= 0:
        return [item], False

    max_layers_per_pallet = int(MAX_PALLET_HEIGHT // layer_height)
    if max_layers_per_pallet <= 0:
        return [item], False

    if layers_total <= max_layers_per_pallet:
        return [item], False

    chunks_count = (layers_total + max_layers_per_pallet - 1) // max_layers_per_pallet
    base_layers = layers_total // chunks_count
    rem_layers = layers_total % chunks_count

    chunks: List[Dict] = []
    qty_left = quantity_total

    for idx in range(chunks_count):
        chunk_layers = base_layers + (1 if idx < rem_layers else 0)
        if chunk_layers <= 0:
            continue

        if idx < chunks_count - 1:
            chunk_qty = min(qty_left, chunk_layers * items_per_layer)
        else:
            chunk_qty = qty_left
        qty_left -= chunk_qty

        chunk_height, real_layers = calculate_item_height(
            quantity=chunk_qty,
            items_per_layer=items_per_layer,
            layer_height=layer_height,
        )
        chunks.append(
            {
                **item,
                "quantity": int(chunk_qty),
                "height": float(chunk_height),
                "layers": int(real_layers),
                "weight": float(chunk_qty * weight_per_item),
                "split_part": idx + 1,
                "split_total": chunks_count,
            }
        )

    return chunks, True


def _merge_same_sku_chunks(chunks: List[Dict]) -> List[PalletItem]:
    merged: Dict[str, PalletItem] = {}
    order: List[str] = []

    for item in chunks:
        sku = str(item["sku"])
        if sku not in merged:
            merged[sku] = PalletItem(
                product_id=item.get("product_id"),
                sku=sku,
                offer_id=item.get("offer_id") or sku,
                name=item.get("name") or sku,
                quantity=int(item.get("quantity") or 0),
                item_height=float(item.get("height") or 0.0),
                item_weight=float(item.get("weight") or 0.0),
                layers=int(item.get("layers") or 0),
            )
            order.append(sku)
            continue

        existing = merged[sku]
        existing.quantity += int(item.get("quantity") or 0)
        existing.item_height += float(item.get("height") or 0.0)
        existing.item_weight += float(item.get("weight") or 0.0)
        existing.layers += int(item.get("layers") or 0)

    return [merged[sku] for sku in order]


def _rebalance_two_pallets(pallet_chunks: List[List[Dict]]) -> None:
    if len(pallet_chunks) != 2:
        return

    def _height(idx: int) -> float:
        return sum(float(x.get("height") or 0.0) for x in pallet_chunks[idx])

    for _ in range(30):
        h0, h1 = _height(0), _height(1)
        hi = 0 if h0 >= h1 else 1
        lo = 1 - hi
        best_idx = None
        best_delta = abs(h0 - h1)

        for idx, item in enumerate(pallet_chunks[hi]):
            ih = float(item.get("height") or 0.0)
            new_hi = _height(hi) - ih
            new_lo = _height(lo) + ih
            if new_lo > MAX_PALLET_HEIGHT + 1e-9:
                continue
            delta = abs(new_hi - new_lo)
            if delta + 1e-9 < best_delta:
                best_delta = delta
                best_idx = idx

        if best_idx is None:
            break

        moved = pallet_chunks[hi].pop(best_idx)
        pallet_chunks[lo].append(moved)


def distribute_items_to_pallets(items: List[Dict]) -> Tuple[List[Pallet], int]:
    prepared: List[Dict] = []
    split_sku_count = 0

    for item in items:
        chunks, split_applied = _split_item_to_height_limited_chunks(item)
        prepared.extend(chunks)
        if split_applied:
            split_sku_count += 1

    prepared.sort(key=lambda x: float(x.get("height") or 0.0), reverse=True)

    pallet_chunks: List[List[Dict]] = []
    pallet_heights: List[float] = []

    for item in prepared:
        h = float(item.get("height") or 0.0)
        placed = False
        for i in range(len(pallet_chunks)):
            if pallet_heights[i] + h <= MAX_PALLET_HEIGHT + 1e-9:
                pallet_chunks[i].append(item)
                pallet_heights[i] += h
                placed = True
                break
        if not placed:
            pallet_chunks.append([item])
            pallet_heights.append(h)

    if len(pallet_chunks) == 2:
        _rebalance_two_pallets(pallet_chunks)

    pallets: List[Pallet] = []
    for idx, chunk_list in enumerate(pallet_chunks, start=1):
        merged_items = _merge_same_sku_chunks(chunk_list)
        pallets.append(
            Pallet(
                pallet_number=idx,
                items=merged_items,
                total_height=sum(item.item_height for item in merged_items),
                total_weight=sum(item.item_weight for item in merged_items),
            )
        )

    return pallets, split_sku_count


def calculate_pallets_for_cluster(cluster_items: List[Dict], products_db: Dict) -> Dict:
    cluster_name = cluster_items[0].get("cluster", "Unknown") if cluster_items else "Unknown"

    items_with_data: List[Dict] = []
    errors: List[str] = []
    missing_products: List[str] = []

    for item in cluster_items:
        sku = str(item.get("sku") or "").strip()
        quantity = int(item.get("quantity") or 0)
        if not sku or quantity <= 0:
            continue

        product = _resolve_product(products_db, item)
        if not product:
            display_article = str(item.get("offer_id") or "").strip() or sku
            missing_products.append(display_article)
            errors.append(f"Артикул '{display_article}' отсутствует в справочнике")
            continue

        items_per_layer = int(product.get("items_per_layer") or 0)
        layer_height = float(product.get("layer_height") or 0.0)
        if items_per_layer <= 0 or layer_height <= 0:
            display_article = str(product.get("offer_id") or item.get("offer_id") or sku).strip() or sku
            errors.append(f"Артикул '{display_article}': некорректные параметры слоя")
            missing_products.append(display_article)
            continue

        height, layers = calculate_item_height(
            quantity=quantity,
            items_per_layer=items_per_layer,
            layer_height=layer_height,
        )

        weight_per_item = float(product.get("weight_per_item") or 0.0)
        items_with_data.append(
            {
                "product_id": product.get("product_id"),
                "sku": sku,
                "offer_id": product.get("offer_id") or item.get("offer_id") or sku,
                "name": product.get("name") or product.get("offer_id") or sku,
                "quantity": quantity,
                "height": height,
                "weight": quantity * weight_per_item,
                "layers": layers,
                "items_per_layer": items_per_layer,
                "layer_height": layer_height,
                "weight_per_item": weight_per_item,
            }
        )

    if not items_with_data:
        return {
            "cluster": cluster_name,
            "pallets": [],
            "errors": errors,
            "missing_products": missing_products,
            "split_sku_count": 0,
        }

    pallets, split_sku_count = distribute_items_to_pallets(items_with_data)

    return {
        "cluster": cluster_name,
        "pallets": [p.to_dict() for p in pallets],
        "errors": errors,
        "missing_products": missing_products,
        "split_sku_count": split_sku_count,
    }


def calculate_pallets_from_supply_plan(supply_items: List[Dict], products_db: Dict) -> List[Dict]:
    by_cluster: Dict[str, List[Dict]] = {}

    for item in supply_items:
        default_sku = item.get("sku") or item.get("offer_id")
        default_offer_id = item.get("offer_id")
        default_product_id = item.get("product_id")

        for detail in item.get("details", []):
            cluster = detail.get("cluster_name") or detail.get("warehouse_name") or "Unknown"
            quantity = int(detail.get("allocated_supply") or 0)
            if quantity <= 0:
                continue

            sku_value = detail.get("sku") or default_sku
            if not sku_value:
                continue
            sku = str(sku_value).strip()
            if not sku:
                continue

            by_cluster.setdefault(cluster, []).append(
                {
                    "product_id": detail.get("product_id") or default_product_id,
                    "sku": sku,
                    "offer_id": detail.get("offer_id") or default_offer_id or sku,
                    "quantity": quantity,
                    "cluster": cluster,
                }
            )

    results: List[Dict] = []
    for cluster, items in by_cluster.items():
        result = calculate_pallets_for_cluster(items, products_db)
        results.append(result)

    return results


def filter_small_pallets(
    clusters: List[Dict],
    cluster_markup_map: Dict[str, float] | None = None,
    min_height_any: float = 0.4,
) -> List[Dict]:
    """
    Убрать паллеты с высотой < min_height_any (0.4м).
    Если у кластера не осталось паллет — оставить с пустыми паллетами и filtered_out.
    """
    filtered: List[Dict] = []

    for cluster in clusters:
        cluster_name = cluster.get("cluster", "")
        pallets = cluster.get("pallets") or []
        removed: List[str] = []
        kept_pallets: List[Dict] = []

        for p in pallets:
            h = float(p.get("total_height") or 0)
            if h < min_height_any:
                removed.append(f"паллета #{p.get('pallet_number')}: высота {h:.2f}м < {min_height_any}м")
                continue
            kept_pallets.append(p)
        if removed:
            print(f"  [filter] {cluster_name}: убрано {len(removed)}, осталось {len(kept_pallets)}", flush=True)

        if not kept_pallets and pallets:
            cluster_copy = dict(cluster)
            cluster_copy["pallets"] = []
            cluster_copy["filtered_out"] = removed
            filtered.append(cluster_copy)
        else:
            cluster_copy = dict(cluster)
            cluster_copy["pallets"] = kept_pallets
            if removed:
                cluster_copy["filtered_out"] = removed
            filtered.append(cluster_copy)

    return filtered


def calculate_all_pallets() -> List[Dict]:
    """
    Backward-compatible entrypoint for palletization Flask app.
    Reads shipment items and product catalog from local SQLite database.
    """
    from database import get_all_products, get_shipment_by_cluster

    products = get_all_products()
    products_db = {
        f"sku:{p.get('sku')}": {
            "product_id": p.get("id"),
            "offer_id": p.get("sku"),
            "name": p.get("name"),
            "layer_height": p.get("layer_height"),
            "items_per_layer": p.get("items_per_layer"),
            "weight_per_item": p.get("weight_per_item"),
        }
        for p in products
        if p.get("sku") is not None
    }

    shipment_by_cluster = get_shipment_by_cluster()
    results: List[Dict] = []
    for cluster, rows in shipment_by_cluster.items():
        cluster_items = [
            {
                "sku": str(r.get("sku") or "").strip(),
                "offer_id": str(r.get("sku") or "").strip(),
                "quantity": int(r.get("quantity") or 0),
                "cluster": cluster,
            }
            for r in rows
            if r.get("sku") and int(r.get("quantity") or 0) > 0
        ]
        results.append(calculate_pallets_for_cluster(cluster_items, products_db))
    return results
