"""Reviews report — taablica artikulov so srednej ocenkoj, alertami i razvernutym spiskom otzyvov."""
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import aiohttp
import asyncpg
from aiohttp import web

from src.dashboard.helpers import _get_ozon_credentials, load_sku_identity_map


async def ensure_reviews_report_tables(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reviews (
                id SERIAL PRIMARY KEY,
                review_id VARCHAR(100) UNIQUE NOT NULL,
                sku BIGINT NOT NULL,
                offer_id VARCHAR(255),
                rating INTEGER,
                text TEXT,
                status VARCHAR(50),
                is_buyer BOOLEAN DEFAULT FALSE,
                published_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ,
                helpful_count INTEGER DEFAULT 0,
                unhelpful_count INTEGER DEFAULT 0,
                raw_data JSON,
                last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_sku ON reviews (sku)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_published_at ON reviews (published_at)")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_comments (
                id SERIAL PRIMARY KEY,
                review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                comment_id BIGINT UNIQUE NOT NULL,
                text TEXT,
                author_name VARCHAR(255),
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ,
                raw_data JSON,
                last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_rating_snapshots (
                id SERIAL PRIMARY KEY,
                sku BIGINT NOT NULL,
                snapshot_date DATE NOT NULL,
                avg_rating NUMERIC(4, 3),
                reviews_count INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_rating_snapshot_sku_date UNIQUE (sku, snapshot_date)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rating_snapshot_sku
            ON review_rating_snapshots (sku)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rating_snapshot_date
            ON review_rating_snapshots (snapshot_date)
            """
        )


def _parse_raw(raw: Any) -> Dict[str, Any]:
    """Razobrat' raw_data v dict (mozhet prijti str, dict ili None)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _photo_urls_from_raw(raw: Any) -> List[str]:
    """Vytaschit url-y foto iz raw_data otzyva. /v1/review/list dajot tol'ko
    photos_amount, real'nye url-y prihodjat iz /v1/review/info i kjeshirujutsja
    pod kljuchom 'info_photos'."""
    payload = _parse_raw(raw)
    if not payload:
        return []
    out: List[str] = []
    # Sperva probuem keshirovannye iz /v1/review/info, potom standartnye polja.
    for key in ("info_photos", "photos", "images", "photo_urls"):
        val = payload.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    url = item.get("url") or item.get("link") or item.get("src")
                    if url:
                        out.append(url)
    # Dedupe sohranjaja porjadok.
    seen = set()
    deduped = []
    for u in out:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


async def get_reviews_report(request: web.Request) -> web.Response:
    """Spisok artikulov so srednej ocenkoj, deltoj k vcheraschnemu i alertom."""
    pool: asyncpg.Pool = request.app["pool"]

    today = date.today()
    yesterday = today - timedelta(days=1)
    week_ago = datetime.utcnow() - timedelta(days=7)

    sql = """
        WITH agg AS (
            SELECT
                r.sku,
                r.offer_id,
                AVG(r.rating)::numeric(4,3) AS avg_rating,
                COUNT(*)                    AS reviews_count,
                COUNT(*) FILTER (
                    WHERE r.rating <= 4 AND r.published_at >= $1
                ) AS negative_7d
            FROM reviews r
            WHERE r.sku IS NOT NULL
            GROUP BY r.sku, r.offer_id
        ),
        prev AS (
            SELECT DISTINCT ON (sku) sku, avg_rating, snapshot_date
            FROM review_rating_snapshots
            WHERE snapshot_date < $2
            ORDER BY sku, snapshot_date DESC
        )
        ,stock AS (
            -- Suммarnyj ostatok ANALOGICHNO otchjotu "Ostatki" (/api/stock-balances):
            -- FBO total = available + waiting_docs + requested + transit po analytics_stocks
            -- FBS total = present po fbs_warehouse_stocks
            -- Obedinjaem po sku (analytics_stocks.sku == fbs_warehouse_stocks.sku == reviews.sku).
            SELECT sku, SUM(qty)::int AS available_stock FROM (
                SELECT sku, (
                    coalesce(available_stock_count, 0)
                    + coalesce(waiting_docs_stock_count, 0)
                    + coalesce(requested_stock_count, 0)
                    + coalesce(transit_stock_count, 0)
                ) AS qty
                FROM analytics_stocks
                WHERE sku IS NOT NULL
                UNION ALL
                SELECT sku, coalesce(present, 0) AS qty
                FROM fbs_warehouse_stocks
                WHERE sku IS NOT NULL
            ) src
            GROUP BY sku
        )
        ,sku_map AS (
            -- reviews.offer_id pochti vsegda NULL (Ozon API ne vozvrashhaet),
            -- a reviews.sku — eto fbo_sku/fbs_sku iz Ozon. Mapping ishhem cherez
            -- report_products_items (osnovnoj istochnik), s fallbackom na
            -- fact_order_items dlja redko prodayushhihsja sku.
            -- product_status: "Prodaetsja" / "Gotov k prodazhe" = aktivnyj,
            -- "Ne prodaetsja" = arhiv → otseivaem.
            SELECT DISTINCT ON (sku) sku, offer_id, product_name, product_status
            FROM (
                SELECT fbo_sku_id AS sku, offer_id, product_name, product_status, 1 AS prio
                FROM report_products_items
                WHERE fbo_sku_id IS NOT NULL AND offer_id IS NOT NULL
                UNION ALL
                SELECT fbs_sku_id AS sku, offer_id, product_name, product_status, 1 AS prio
                FROM report_products_items
                WHERE fbs_sku_id IS NOT NULL AND offer_id IS NOT NULL
                UNION ALL
                SELECT foi.sku, foi.offer_id, p.name AS product_name, NULL AS product_status, 2 AS prio
                FROM fact_order_items foi
                LEFT JOIN products p ON p.offer_id = foi.offer_id
                WHERE foi.sku IS NOT NULL
            ) src
            ORDER BY sku, prio
        )
        SELECT
            agg.sku,
            COALESCE(agg.offer_id, sm.offer_id) AS offer_id,
            sm.product_name                    AS product_name,
            agg.avg_rating,
            agg.reviews_count,
            agg.negative_7d,
            prev.avg_rating                    AS prev_avg_rating,
            prev.snapshot_date                 AS prev_snapshot_date,
            COALESCE(stock.available_stock, 0) AS available_stock
        FROM agg
        LEFT JOIN prev    ON prev.sku = agg.sku
        -- INNER JOIN: artikuly bez mappinga — eto, kak pravilo, snjatye s prodazhi
        -- starye karty, ih ne pokazyvaem (trebovanie 2026-04-30).
        INNER JOIN sku_map sm ON sm.sku = agg.sku
        LEFT JOIN stock   ON stock.sku = agg.sku
        WHERE sm.product_status IS DISTINCT FROM 'Не продается'
        ORDER BY agg.reviews_count DESC
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, week_ago, today)
        sku_list = [int(r["sku"]) for r in rows if r["sku"] is not None]
        identity_map = await load_sku_identity_map(conn, sku_list)

    items: List[Dict[str, Any]] = []
    for r in rows:
        sku = int(r["sku"])
        identity = identity_map.get(sku) or {}
        resolved_offer_id = str(identity.get("offer_id") or r["offer_id"] or "").strip()
        if not resolved_offer_id:
            continue
        avg = float(r["avg_rating"]) if r["avg_rating"] is not None else None
        prev = float(r["prev_avg_rating"]) if r["prev_avg_rating"] is not None else None
        delta = (avg - prev) if (avg is not None and prev is not None) else None
        negative_7d = int(r["negative_7d"] or 0)
        has_alert = (delta is not None and delta < 0) or negative_7d > 0
        items.append({
            "sku": sku,
            "offer_id": resolved_offer_id,
            "product_name": identity.get("product_name") or r["product_name"],
            "available_stock": int(r["available_stock"] or 0),
            "avg_rating": avg,
            "prev_avg_rating": prev,
            "delta": delta,
            "reviews_count": int(r["reviews_count"] or 0),
            "negative_7d": negative_7d,
            "has_alert": has_alert,
        })

    items.sort(key=lambda x: (not x["has_alert"], -(x["reviews_count"] or 0)))

    return web.json_response({
        "items": items,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    })


async def get_reviews_report_detail(request: web.Request) -> web.Response:
    """Spisok otzyvov po konkretnomu sku."""
    sku_raw = request.match_info.get("sku", "")
    try:
        sku = int(sku_raw)
    except ValueError:
        return web.json_response({"error": "Invalid sku"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    sql = """
        SELECT
            r.review_id,
            r.rating,
            r.text,
            r.status,
            r.is_buyer,
            r.published_at,
            r.helpful_count,
            r.unhelpful_count,
            r.raw_data,
            r.offer_id,
            (
                SELECT json_agg(json_build_object(
                    'text', c.text,
                    'author', c.author_name,
                    'created_at', c.created_at
                ) ORDER BY c.created_at)
                FROM review_comments c
                WHERE c.review_id = r.id
            ) AS comments
        FROM reviews r
        WHERE r.sku = $1
        ORDER BY r.published_at DESC NULLS LAST
        LIMIT 500
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, sku)
        identity_map = await load_sku_identity_map(conn, [sku])
        offer_id_lookup = (identity_map.get(sku) or {}).get("offer_id")

    # Lazy-pidqruzka foto: dlja otzyvov s photos_amount > 0 i bez keshirovannyh
    # info_photos — parallelno (semaphore=4) dergaem /v1/review/info i obnovljaem
    # raw_data.info_photos. Pri sledujushhih otkrytijah poluchim mgnovenno iz BD.
    import asyncio as _asyncio

    needs_fetch: List[tuple] = []  # (review_id, raw_dict)
    for r in rows:
        raw = _parse_raw(r["raw_data"])
        photos_amount = int(raw.get("photos_amount") or 0)
        already_cached = isinstance(raw.get("info_photos"), list)
        if photos_amount > 0 and not already_cached and r["review_id"]:
            needs_fetch.append((r["review_id"], raw))

    fetched_photos: Dict[str, List[Dict[str, Any]]] = {}
    if needs_fetch:
        sem = _asyncio.Semaphore(4)

        async def _fetch_one(review_id: str) -> None:
            async with sem:
                status, payload = await _ozon_review_request(
                    "/v1/review/info", {"review_id": review_id}
                )
                if status == 200:
                    photos = payload.get("photos") or []
                    if isinstance(photos, list):
                        fetched_photos[review_id] = photos

        await _asyncio.gather(*[_fetch_one(rid) for rid, _ in needs_fetch])

        # Persistim v BD ottdelnym tranzaktom (vse uspeshnye srazu).
        if fetched_photos:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for review_id, photos in fetched_photos.items():
                        await conn.execute(
                            """
                            UPDATE reviews
                            SET raw_data = COALESCE(raw_data::jsonb, '{}'::jsonb)
                                            || jsonb_build_object('info_photos', $2::jsonb)
                            WHERE review_id = $1
                            """,
                            review_id,
                            json.dumps(photos),
                        )

    offer_id: Optional[str] = offer_id_lookup
    items: List[Dict[str, Any]] = []
    for r in rows:
        offer_id = offer_id or r["offer_id"]
        comments_raw = r["comments"]
        if isinstance(comments_raw, str):
            try:
                comments_raw = json.loads(comments_raw)
            except json.JSONDecodeError:
                comments_raw = None
        # Esli foto pripodgruzili sejchas — vstavim ih v raw vremenno dlja extracta.
        raw_for_photos = _parse_raw(r["raw_data"])
        if r["review_id"] in fetched_photos:
            raw_for_photos["info_photos"] = fetched_photos[r["review_id"]]
        items.append({
            "review_id": r["review_id"],
            "rating": r["rating"],
            "text": r["text"],
            "status": r["status"],
            "is_buyer": r["is_buyer"],
            "published_at": r["published_at"].isoformat() if r["published_at"] else None,
            "helpful_count": r["helpful_count"],
            "unhelpful_count": r["unhelpful_count"],
            "photos": _photo_urls_from_raw(raw_for_photos),
            "photos_amount": int(raw_for_photos.get("photos_amount") or 0),
            "comments": comments_raw or [],
        })

    seller_link = (
        f"https://seller.ozon.ru/app/reviews?offer_id={quote(offer_id, safe='')}"
        if offer_id else "https://seller.ozon.ru/app/reviews"
    )
    public_link = f"https://www.ozon.ru/product/{sku}/reviews/"

    return web.json_response({
        "sku": sku,
        "offer_id": offer_id,
        "seller_link": seller_link,
        "public_link": public_link,
        "items": items,
    })


async def _ozon_review_request(endpoint: str, body: Dict[str, Any]) -> tuple:
    """Mini-helper: vyzov endpoint'a Ozon Seller API. Vozvrashhaet (status, payload)."""
    client_id, api_key = _get_ozon_credentials()
    if not client_id or not api_key:
        return 401, {"message": "Ozon credentials not configured"}
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    url = f"https://api-seller.ozon.ru{endpoint}"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        async with sess.post(url, headers=headers, json=body) as resp:
            text = await resp.text()
            try:
                payload = json.loads(text) if text else {}
            except json.JSONDecodeError:
                payload = {"text": text[:1000]}
            return resp.status, payload


async def get_reviews_service_status(request: web.Request) -> web.Response:
    """Proverit, podkljuchen li servis 'Upravlenie otzyvami' / Premium Pro.

    /v1/review/count trebuet podpisku. 200 OK → est', 403 → net, drugoe → ne znaem.
    """
    status, payload = await _ozon_review_request("/v1/review/count", {})
    if status == 200:
        return web.json_response({"enabled": True, "counts": payload})
    if status == 403:
        return web.json_response({
            "enabled": False,
            "reason": "Servis 'Upravlenie otzyvami' ili Premium Pro ne podkljuchen",
        })
    return web.json_response({
        "enabled": False,
        "reason": payload.get("message") or f"HTTP {status}",
    })


async def post_review_reply(request: web.Request) -> web.Response:
    """Otvet na otzyv(y). Body: {review_ids: [str], text: str, mark_as_processed: bool}.

    Odin endpoint dlja single i bulk: ozon ne podderzhivaet bulk natively, my prosto
    posylaem zaprosy posledovatelno i sobiraem rezultat.
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    review_ids_raw = data.get("review_ids") or ([data["review_id"]] if data.get("review_id") else [])
    text = (data.get("text") or "").strip()
    mark_as_processed = bool(data.get("mark_as_processed", True))
    parent_comment_id = data.get("parent_comment_id")

    if not review_ids_raw:
        return web.json_response({"error": "review_ids required"}, status=400)
    if not text:
        return web.json_response({"error": "text required"}, status=400)

    review_ids: List[str] = [str(rid) for rid in review_ids_raw if rid]
    results: List[Dict[str, Any]] = []
    pool: asyncpg.Pool = request.app["pool"]

    for rid in review_ids:
        body = {
            "review_id": rid,
            "text": text,
            "mark_review_as_processed": mark_as_processed,
        }
        if parent_comment_id and len(review_ids) == 1:
            body["parent_comment_id"] = parent_comment_id

        status, payload = await _ozon_review_request("/v1/review/comment/create", body)
        ok = status == 200
        comment_id = payload.get("comment_id") if ok else None

        # Sinhronno proapdejtim status v lokal'noj BD chtoby UI pokazyval aktual'nyj.
        if ok and mark_as_processed:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE reviews SET status = 'PROCESSED' WHERE review_id = $1",
                    rid,
                )

        results.append({
            "review_id": rid,
            "ok": ok,
            "comment_id": comment_id,
            "error": None if ok else (payload.get("message") or f"HTTP {status}"),
        })

    success = sum(1 for r in results if r["ok"])
    return web.json_response({
        "total": len(results),
        "success": success,
        "failed": len(results) - success,
        "results": results,
    })
