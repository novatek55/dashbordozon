"""Отчёт «Возвраты и отмены» с подсветкой повторных клиентов.

Клиент = префикс posting_number до первого дефиса (например,
'55049297-0180-4' → '55049297'). Это стабильный order prefix, по которому
можно находить повторяющиеся отмены/возвраты от одного покупателя.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import asyncpg


async def get_returns_analytics_data(
    conn: asyncpg.Connection,
    date_from: datetime,
    date_to_exclusive: datetime,
) -> Dict[str, Any]:
    """События отмен и возвратов за период + сводка по кластерам доставки."""
    rows = await conn.fetch(
        """
        WITH order_clusters AS (
            -- Кластер на уровне order_number (общий для всех posting -N того же заказа).
            SELECT
                split_part(posting_number, '-', 1) || '-' || split_part(posting_number, '-', 2) AS order_key,
                max(delivery_cluster_to) FILTER (WHERE delivery_cluster_to IS NOT NULL) AS cluster,
                max(shipping_warehouse_name) FILTER (WHERE shipping_warehouse_name IS NOT NULL) AS warehouse
            FROM fact_orders
            GROUP BY 1
        ),
        events AS (
            -- Отмены заказов (cancelled_at у нас часто NULL → используем created_at)
            SELECT
                coalesce(o.cancelled_at, o.created_at) AS event_at,
                'cancel' AS event_type,
                upper(coalesce(o.delivery_schema, '')) AS schema,
                o.posting_number,
                coalesce(it->>'offer_id', it->>'sku') AS offer_id,
                (it->>'sku')::text AS sku,
                coalesce((it->>'quantity')::float, 1.0) AS quantity,
                coalesce((it->>'price')::float, 0.0) AS price,
                NULL::text AS reason,
                coalesce(o.delivery_cluster_to, oc.cluster) AS cluster,
                coalesce(o.shipping_warehouse_name, oc.warehouse) AS warehouse
            FROM fact_orders o
            LEFT JOIN order_clusters oc ON oc.order_key = split_part(o.posting_number, '-', 1) || '-' || split_part(o.posting_number, '-', 2)
            LEFT JOIN LATERAL jsonb_array_elements(coalesce(o.items::jsonb, '[]'::jsonb)) it ON TRUE
            WHERE lower(coalesce(o.status, '')) IN ('отменён', 'отменен')
              AND coalesce(o.cancelled_at, o.created_at) >= $1
              AND coalesce(o.cancelled_at, o.created_at) < $2

            UNION ALL
            -- Возвраты: UNION двух таблиц с приоритетом корректной схемы.
            -- Один posting_number встречается в обеих таблицах (sync их дублирует),
            -- оставляем запись где src совпадает со схемой fact_orders.
            SELECT
                event_at, event_type, schema, posting_number, offer_id, sku,
                quantity, price, reason, cluster, warehouse
            FROM (
                SELECT
                    r.returned_at AS event_at,
                    CASE WHEN upper(coalesce(o.delivery_schema,''))='FBO' THEN 'return_fbo' ELSE 'return_fbs' END AS event_type,
                    CASE WHEN upper(coalesce(o.delivery_schema,''))='FBO' THEN 'FBO' ELSE 'FBS' END AS schema,
                    r.posting_number,
                    r.offer_id,
                    r.sku::text AS sku,
                    coalesce(r.quantity, 1)::float AS quantity,
                    coalesce(r.refund_amount, 0)::float AS price,
                    r.return_reason AS reason,
                    coalesce(o.delivery_cluster_to, oc.cluster) AS cluster,
                    coalesce(o.shipping_warehouse_name, oc.warehouse) AS warehouse,
                    -- priority: 1 = совпадает схема источника и fact_orders; 2 = не совпадает
                    CASE
                        WHEN upper(coalesce(o.delivery_schema,''))='FBS' THEN 1
                        WHEN o.delivery_schema IS NULL THEN 2
                        ELSE 3
                    END AS prio,
                    'fbs_src' AS src
                FROM returns r
                LEFT JOIN fact_orders o ON o.posting_number = r.posting_number
                LEFT JOIN order_clusters oc ON oc.order_key = split_part(r.posting_number, '-', 1) || '-' || split_part(r.posting_number, '-', 2)
                WHERE r.returned_at >= $1 AND r.returned_at < $2
                  AND lower(coalesce(o.status, '')) NOT IN ('отменён', 'отменен')
                UNION ALL
                SELECT
                    r.returned_at AS event_at,
                    'return_fbo' AS event_type,
                    'FBO' AS schema,
                    r.posting_number,
                    r.offer_id,
                    r.sku::text AS sku,
                    coalesce(r.quantity, 1)::float AS quantity,
                    coalesce(r.refund_amount, 0)::float AS price,
                    r.return_reason AS reason,
                    coalesce(o.delivery_cluster_to, oc.cluster) AS cluster,
                    coalesce(o.shipping_warehouse_name, oc.warehouse) AS warehouse,
                    CASE
                        WHEN upper(coalesce(o.delivery_schema,''))='FBO' THEN 1
                        WHEN o.delivery_schema IS NULL THEN 2
                        ELSE 3
                    END AS prio,
                    'fbo_src' AS src
                FROM returns_fbo r
                LEFT JOIN fact_orders o ON o.posting_number = r.posting_number
                LEFT JOIN order_clusters oc ON oc.order_key = split_part(r.posting_number, '-', 1) || '-' || split_part(r.posting_number, '-', 2)
                WHERE r.returned_at >= $1 AND r.returned_at < $2
                  AND lower(coalesce(o.status, '')) NOT IN ('отменён', 'отменен')
            ) combined
            WHERE (posting_number, src) IN (
                SELECT posting_number, src
                FROM (
                    SELECT posting_number, src, prio,
                           row_number() OVER (PARTITION BY posting_number ORDER BY prio, src) AS rn
                    FROM (
                        SELECT r.posting_number, 'fbs_src' AS src,
                            CASE WHEN upper(coalesce(o.delivery_schema,''))='FBS' THEN 1
                                 WHEN o.delivery_schema IS NULL THEN 2 ELSE 3 END AS prio
                        FROM returns r LEFT JOIN fact_orders o ON o.posting_number = r.posting_number
                        WHERE r.returned_at >= $1 AND r.returned_at < $2
                        UNION ALL
                        SELECT r.posting_number, 'fbo_src' AS src,
                            CASE WHEN upper(coalesce(o.delivery_schema,''))='FBO' THEN 1
                                 WHEN o.delivery_schema IS NULL THEN 2 ELSE 3 END AS prio
                        FROM returns_fbo r LEFT JOIN fact_orders o ON o.posting_number = r.posting_number
                        WHERE r.returned_at >= $1 AND r.returned_at < $2
                    ) all_src
                ) ranked
                WHERE rn = 1
            )
        )
        SELECT
            event_at, event_type, schema, posting_number,
            split_part(posting_number, '-', 1) AS client_id,
            offer_id, sku, quantity, price, reason, cluster, warehouse
        FROM events
        WHERE posting_number IS NOT NULL
        ORDER BY event_at DESC
        """,
        date_from, date_to_exclusive,
    )

    events: List[Dict[str, Any]] = []
    client_repeats: Dict[str, int] = {}
    cluster_totals: Dict[str, Dict[str, float]] = {}

    for r in rows:
        client_id = r["client_id"] or ""
        client_repeats[client_id] = client_repeats.get(client_id, 0) + 1

        amount = float(r["quantity"] or 0) * float(r["price"] or 0)
        cluster = r["cluster"] or "—"
        bucket = cluster_totals.setdefault(cluster, {
            "cluster": cluster,
            "events_total": 0,
            "cancels": 0,
            "returns": 0,
            "quantity": 0.0,
            "amount": 0.0,
        })
        bucket["events_total"] += 1
        if r["event_type"] == "cancel":
            bucket["cancels"] += 1
        else:
            bucket["returns"] += 1
        bucket["quantity"] += float(r["quantity"] or 0)
        bucket["amount"] += amount

        events.append({
            "event_at": r["event_at"].isoformat() if r["event_at"] else None,
            "event_type": r["event_type"],
            "schema": r["schema"],
            "posting_number": r["posting_number"],
            "client_id": client_id,
            "offer_id": r["offer_id"],
            "sku": r["sku"],
            "quantity": float(r["quantity"] or 0),
            "price": float(r["price"] or 0),
            "amount": amount,
            "reason": r["reason"],
            "cluster": cluster,
            "warehouse": r["warehouse"],
        })

    # Добавляем client_repeats в события
    for ev in events:
        ev["client_repeats"] = client_repeats.get(ev["client_id"], 0)

    # Сортируем кластеры по числу событий
    clusters = sorted(cluster_totals.values(), key=lambda x: x["events_total"], reverse=True)

    # Топ клиентов с повторами
    repeat_clients = sorted(
        [(cid, c) for cid, c in client_repeats.items() if c >= 2],
        key=lambda x: x[1], reverse=True
    )
    repeat_clients_list = [
        {"client_id": cid, "repeats": c} for cid, c in repeat_clients[:50]
    ]

    return {
        "count": len(events),
        "events": events,
        "clusters": clusters,
        "repeat_clients": repeat_clients_list,
        "date_from": date_from.date().isoformat(),
        "date_to": (date_to_exclusive).date().isoformat(),
    }
