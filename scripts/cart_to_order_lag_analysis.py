"""
Анализ лага "корзина -> заказ" для выбранных артикулов.

Проверяет:
1. Cross-correlation Pearson между hits_tocart(t) и ordered_units(t+k) для k=0..10.
   Пик корреляции = типичный лаг выкупа.
2. Коэффициент вариации (CV = std/mean) для CR(корзина->заказ), посчитанного
   на скользящем окне разной длины (1, 3, 7, 14, 30 дней). CV показывает шумность:
   CV < 0.3 = читаемый сигнал, > 0.5 = шум.

Запуск:
    PYTHONIOENCODING=utf-8 python -m scripts.cart_to_order_lag_analysis 215 123
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from statistics import mean, pstdev

import asyncpg

from src.config import settings
from src.dashboard.helpers import to_asyncpg_dsn


ROLLING_WINDOWS = [1, 3, 7, 14, 30]
MAX_LAG = 10
PERIOD_DAYS = 60


async def fetch_daily(pool: asyncpg.Pool, offer_id: str, days: int) -> list[dict]:
    start_day = date.today() - timedelta(days=days)
    rows = await pool.fetch(
        """
        WITH sku_product AS (
            SELECT DISTINCT ON (sku) sku, ozon_product_id FROM (
                SELECT fbo_sku_id::bigint AS sku, ozon_product_id FROM report_products_items WHERE fbo_sku_id IS NOT NULL AND ozon_product_id IS NOT NULL
                UNION ALL
                SELECT fbs_sku_id::bigint AS sku, ozon_product_id FROM report_products_items WHERE fbs_sku_id IS NOT NULL AND ozon_product_id IS NOT NULL
            ) x ORDER BY sku
        ),
        sku_map AS (
            SELECT sp.sku, coalesce(p.offer_id, concat('sku_', sp.sku::text)) AS offer_id
            FROM sku_product sp LEFT JOIN products p ON p.product_id = sp.ozon_product_id
        )
        SELECT
            ad.date::date AS day,
            coalesce((ad.metric_values ->> 'hits_tocart')::numeric, ad.clicks, 0) AS hits_tocart,
            coalesce((ad.metric_values ->> 'ordered_units')::numeric, ad.ordered_units, 0) AS ordered_units
        FROM analytics_data ad
        LEFT JOIN sku_map sm ON sm.sku = ad.sku
        WHERE ad.date::date >= $1::date
          AND lower(coalesce(sm.offer_id, '')) = lower($2)
        ORDER BY day
        """,
        start_day,
        offer_id,
    )
    # Дни могут дублироваться если у offer_id несколько sku — сгруппируем по дню
    by_day: dict[date, dict] = {}
    for r in rows:
        d = r["day"]
        bucket = by_day.setdefault(d, {"day": d, "carts": 0.0, "orders": 0.0})
        bucket["carts"] += float(r["hits_tocart"] or 0)
        bucket["orders"] += float(r["ordered_units"] or 0)
    return sorted(by_day.values(), key=lambda x: x["day"])


def pearson(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(a) != len(b):
        return 0.0
    ma, mb = mean(a), mean(b)
    da = [x - ma for x in a]
    db = [x - mb for x in b]
    num = sum(x * y for x, y in zip(da, db))
    den_a = sum(x * x for x in da) ** 0.5
    den_b = sum(x * x for x in db) ** 0.5
    if den_a == 0 or den_b == 0:
        return 0.0
    return num / (den_a * den_b)


def cross_correlation(carts: list[float], orders: list[float], max_lag: int) -> list[tuple[int, float, int]]:
    """Возвращает [(lag, corr, n_points)] для lag = 0..max_lag."""
    result = []
    n = len(carts)
    for k in range(max_lag + 1):
        if n - k < 3:
            result.append((k, 0.0, 0))
            continue
        c = carts[: n - k]
        o = orders[k:]
        result.append((k, pearson(c, o), len(c)))
    return result


def rolling_cr_cv(carts: list[float], orders: list[float], window: int) -> tuple[float, float, int]:
    """Возвращает (mean_CR, CV, num_points) для скользящего окна длины window."""
    n = len(carts)
    if n < window:
        return (0.0, 0.0, 0)
    crs: list[float] = []
    for i in range(n - window + 1):
        c_sum = sum(carts[i : i + window])
        o_sum = sum(orders[i : i + window])
        if c_sum > 0:
            crs.append(o_sum / c_sum * 100)
    if len(crs) < 2:
        return (mean(crs) if crs else 0.0, 0.0, len(crs))
    mu = mean(crs)
    sigma = pstdev(crs)
    cv = sigma / mu if mu > 0 else 0.0
    return (mu, cv, len(crs))


async def analyze(offer_id: str, pool: asyncpg.Pool) -> None:
    rows = await fetch_daily(pool, offer_id, PERIOD_DAYS)
    if not rows:
        print(f"\n=== offer_id={offer_id} === НЕТ ДАННЫХ за {PERIOD_DAYS} дней")
        return

    carts = [r["carts"] for r in rows]
    orders = [r["orders"] for r in rows]
    total_carts = sum(carts)
    total_orders = sum(orders)
    overall_cr = (total_orders / total_carts * 100) if total_carts > 0 else 0.0

    print(f"\n=== offer_id={offer_id} ===")
    print(f"Дней с данными: {len(rows)}  Период: {rows[0]['day']} .. {rows[-1]['day']}")
    print(f"Sum carts: {total_carts:.0f}  Sum orders: {total_orders:.0f}  Overall CR: {overall_cr:.2f}%")

    # 1. Cross-correlation
    print("\n-- Cross-correlation carts(t) vs orders(t+k) --")
    print(f"  {'lag':>4} {'corr':>8} {'N':>5}")
    xcorr = cross_correlation(carts, orders, MAX_LAG)
    for lag, corr, n in xcorr:
        marker = " <-- peak" if corr == max(c for _, c, _ in xcorr) and corr > 0 else ""
        print(f"  {lag:>4} {corr:>8.3f} {n:>5}{marker}")
    peak_lag = max(xcorr, key=lambda x: x[1])
    print(f"Пик лаг: {peak_lag[0]}д (corr={peak_lag[1]:.3f})")

    # 2. Rolling CR CV
    print("\n-- Rolling CR(корзина->заказ) stability --")
    print(f"  {'window':>7} {'mean_CR%':>10} {'CV':>8} {'N_wind':>7} {'читаемо?':>10}")
    for w in ROLLING_WINDOWS:
        mu, cv, n = rolling_cr_cv(carts, orders, w)
        readable = "да" if cv > 0 and cv < 0.30 else ("шум" if cv >= 0.50 else "серая" if cv >= 0.30 else "-")
        print(f"  {w:>7} {mu:>10.2f} {cv:>8.3f} {n:>7} {readable:>10}")


async def main(offer_ids: list[str]) -> None:
    dsn = to_asyncpg_dsn(settings.database_url)
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        for oid in offer_ids:
            await analyze(oid, pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    args = sys.argv[1:] or ["215", "123"]
    asyncio.run(main(args))
