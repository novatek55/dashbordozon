"""SERP-сервис: сохранение снимков выдачи, управление конкурентами и главным запросом."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Снимки выдачи
# ────────────────────────────────────────────────────────────────────────────

async def save_snapshot(
    pool: asyncpg.Pool,
    query_text: str,
    positions: List[Dict[str, Any]],
    raw_data: Optional[Dict] = None,
) -> int:
    """Сохраняет снимок выдачи в БД. Возвращает snapshot_id."""
    async with pool.acquire() as conn:
        # SKU наших товаров
        our_skus_rows = await conn.fetch("SELECT product_id FROM products WHERE is_visible = true")
        our_skus = {r["product_id"] for r in our_skus_rows}

        # SKU конкурентов из справочника
        comp_rows = await conn.fetch("SELECT sku FROM serp_competitors")
        competitor_skus = {r["sku"] for r in comp_rows}

        # Создаём снимок
        snapshot_id = await conn.fetchval(
            """
            INSERT INTO serp_snapshots (query_text, scraped_at, position_count, raw_data)
            VALUES ($1, now(), $2, $3::jsonb)
            RETURNING id
            """,
            query_text,
            len(positions),
            json.dumps(raw_data) if raw_data else None,
        )

        # Вставляем позиции
        for pos in positions:
            sku = _to_bigint(pos.get("sku"))
            await conn.execute(
                """
                INSERT INTO serp_positions
                    (snapshot_id, position, sku, title, brand, price, price_before,
                     rating, review_count, stock, promo_label, thumbnail_url,
                     revenue_30d, sales_per_day, is_our_product, is_competitor)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                ON CONFLICT (snapshot_id, position) DO NOTHING
                """,
                snapshot_id,
                int(pos.get("position", 0)),
                sku,
                (pos.get("title") or "")[:500] or None,
                (pos.get("brand") or "")[:255] or None,
                _to_decimal(pos.get("price")),
                _to_decimal(pos.get("price_before")),
                _to_float(pos.get("rating")),
                _to_int(pos.get("review_count")),
                _to_int(pos.get("stock")),
                (pos.get("promo_label") or "")[:100] or None,
                pos.get("thumbnail_url") or None,
                _to_decimal(pos.get("revenue_30d")),
                _to_float(pos.get("sales_per_day")),
                bool(sku and sku in our_skus),
                bool(sku and sku in competitor_skus),
            )

    logger.info(
        "Saved SERP snapshot id=%s for query=%r positions=%d",
        snapshot_id, query_text, len(positions),
    )
    return snapshot_id


async def get_latest_snapshot(pool: asyncpg.Pool, query_text: str) -> Optional[Dict]:
    """Возвращает последний снимок выдачи по запросу со всеми позициями."""
    async with pool.acquire() as conn:
        snap = await conn.fetchrow(
            "SELECT id, query_text, scraped_at, position_count FROM serp_snapshots "
            "WHERE query_text = $1 ORDER BY scraped_at DESC LIMIT 1",
            query_text,
        )
        if not snap:
            return None

        positions = await conn.fetch(
            """
            SELECT position, sku, title, brand, price, price_before,
                   rating, review_count, stock, promo_label, thumbnail_url,
                   revenue_30d, sales_per_day, is_our_product, is_competitor
            FROM serp_positions
            WHERE snapshot_id = $1
            ORDER BY position
            """,
            snap["id"],
        )

    return {
        "snapshot_id": snap["id"],
        "query_text": snap["query_text"],
        "scraped_at": snap["scraped_at"].isoformat(),
        "position_count": snap["position_count"],
        "positions": [_serialize_row(p) for p in positions],
    }


# ────────────────────────────────────────────────────────────────────────────
# Конкуренты
# ────────────────────────────────────────────────────────────────────────────

async def mark_competitor(
    pool: asyncpg.Pool, sku: int, is_competitor: bool, note: str = ""
) -> None:
    """Добавляет или удаляет SKU из справочника конкурентов."""
    async with pool.acquire() as conn:
        if is_competitor:
            await conn.execute(
                """
                INSERT INTO serp_competitors (sku, note, created_at)
                VALUES ($1, $2, now())
                ON CONFLICT (sku) DO UPDATE SET note = EXCLUDED.note
                """,
                sku, note or None,
            )
            await conn.execute(
                "UPDATE serp_positions SET is_competitor = true WHERE sku = $1", sku
            )
        else:
            await conn.execute("DELETE FROM serp_competitors WHERE sku = $1", sku)
            await conn.execute(
                "UPDATE serp_positions SET is_competitor = false WHERE sku = $1", sku
            )


async def get_competitors(pool: asyncpg.Pool) -> List[Dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT sku, note, created_at FROM serp_competitors ORDER BY created_at DESC"
        )
    return [{"sku": r["sku"], "note": r["note"], "created_at": r["created_at"].isoformat()} for r in rows]


# ────────────────────────────────────────────────────────────────────────────
# Главный запрос
# ────────────────────────────────────────────────────────────────────────────

async def get_primary_query(pool: asyncpg.Pool, sku: int) -> Optional[Dict]:
    """Возвращает главный запрос для SKU или None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sku, offer_id, query_text, set_manually, updated_at "
            "FROM sku_primary_query WHERE sku = $1",
            sku,
        )
    if not row:
        return None
    return {
        "sku": row["sku"],
        "offer_id": row["offer_id"],
        "query_text": row["query_text"],
        "set_manually": row["set_manually"],
        "updated_at": row["updated_at"].isoformat(),
    }


async def set_primary_query(
    pool: asyncpg.Pool, sku: int, query_text: str, manual: bool = True
) -> None:
    """Устанавливает главный запрос для SKU."""
    async with pool.acquire() as conn:
        offer_id = await conn.fetchval(
            "SELECT offer_id FROM products WHERE product_id = $1", sku
        )
        await conn.execute(
            """
            INSERT INTO sku_primary_query (sku, offer_id, query_text, set_manually, updated_at)
            VALUES ($1, $2, $3, $4, now())
            ON CONFLICT (sku) DO UPDATE
                SET query_text   = EXCLUDED.query_text,
                    set_manually = EXCLUDED.set_manually,
                    updated_at   = now()
            """,
            sku, offer_id, query_text, manual,
        )


async def recalculate_primary_queries(pool: asyncpg.Pool) -> int:
    """
    Пересчитывает главный запрос для всех SKU где set_manually = false.
    Правило: MAX(searches * conversion) за последние 30 дней.
    Возвращает кол-во обновлённых строк.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (sku)
                sku, offer_id, query_text,
                (COALESCE(searches, 0) * COALESCE(conversion, 0)) AS score
            FROM analytics_product_query_details
            WHERE period_start >= now() - INTERVAL '30 days'
              AND query_text IS NOT NULL AND query_text <> ''
            ORDER BY sku, score DESC
            """
        )
        count = 0
        for row in rows:
            existing = await conn.fetchrow(
                "SELECT set_manually FROM sku_primary_query WHERE sku = $1", row["sku"]
            )
            if existing and existing["set_manually"]:
                continue
            await conn.execute(
                """
                INSERT INTO sku_primary_query (sku, offer_id, query_text, set_manually, updated_at)
                VALUES ($1, $2, $3, false, now())
                ON CONFLICT (sku) DO UPDATE
                    SET query_text = EXCLUDED.query_text,
                        offer_id   = EXCLUDED.offer_id,
                        updated_at = now()
                WHERE sku_primary_query.set_manually = false
                """,
                row["sku"], row["offer_id"], row["query_text"],
            )
            count += 1
    logger.info("Recalculated primary queries: %d updated", count)
    return count


async def get_top_queries_for_sku(
    pool: asyncpg.Pool, sku: int, limit: int = 20
) -> List[Dict]:
    """Топ запросов для SKU по убыванию searches*conversion — для dropdown."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT query_text,
                   SUM(searches)  AS total_searches,
                   AVG(conversion) AS avg_conversion,
                   SUM(COALESCE(searches,0) * COALESCE(conversion,0)) AS score
            FROM analytics_product_query_details
            WHERE sku = $1
              AND period_start >= now() - INTERVAL '30 days'
              AND query_text IS NOT NULL AND query_text <> ''
            GROUP BY query_text
            ORDER BY score DESC
            LIMIT $2
            """,
            sku, limit,
        )
    return [
        {
            "query_text": r["query_text"],
            "total_searches": r["total_searches"],
            "avg_conversion": float(r["avg_conversion"] or 0),
            "score": float(r["score"] or 0),
        }
        for r in rows
    ]


# ────────────────────────────────────────────────────────────────────────────
# Отчёт по артикулу
# ────────────────────────────────────────────────────────────────────────────

async def get_article_serp_report(pool: asyncpg.Pool, sku: int) -> Dict:
    """Снапшот для секции «Поиск» в отчёте по артикулу."""
    primary = await get_primary_query(pool, sku)
    if not primary:
        return {
            "primary_query": None,
            "set_manually": False,
            "our_position": None,
            "our_price": None,
            "positions": [],
            "scraped_at": None,
            "snapshot_id": None,
        }

    snapshot = await get_latest_snapshot(pool, primary["query_text"])
    if not snapshot:
        return {
            "primary_query": primary["query_text"],
            "set_manually": primary["set_manually"],
            "our_position": None,
            "our_price": None,
            "positions": [],
            "scraped_at": None,
            "snapshot_id": None,
        }

    our_pos = next((p for p in snapshot["positions"] if p.get("sku") == sku), None)
    return {
        "primary_query": primary["query_text"],
        "set_manually": primary["set_manually"],
        "our_position": our_pos["position"] if our_pos else None,
        "our_price": our_pos["price"] if our_pos else None,
        "positions": snapshot["positions"],
        "scraped_at": snapshot["scraped_at"],
        "snapshot_id": snapshot["snapshot_id"],
    }


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _to_bigint(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_decimal(v):
    if v is None:
        return None
    try:
        from decimal import Decimal
        return Decimal(str(v))
    except Exception:
        return None


def _serialize_row(row) -> Dict:
    """Конвертирует asyncpg Record в dict, Decimal → str."""
    import decimal
    result = {}
    for key in row.keys():
        val = row[key]
        if isinstance(val, decimal.Decimal):
            result[key] = str(val)
        else:
            result[key] = val
    return result
