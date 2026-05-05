"""Удалить из products записи, которых больше нет в Ozon.

Шаги:
1) Стянуть актуальный список product_id из Ozon (/v3/product/list, постранично).
2) Найти в БД лишние products (product_id NOT IN актуальных).
3) Показать сколько найдено + сколько связанных posting_items / stocks_history.
4) Дождаться подтверждения (--yes для авто).
5) Удалить связанные строки и сами products.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import DatabaseManager
from src.models import Product, PostingItem, StockHistory
from src.ozon_client import OzonClient
from src.config import settings


async def fetch_actual_product_ids() -> set[int]:
    actual: set[int] = set()
    async with OzonClient(
        client_id=settings.ozon_client_id,
        api_key=settings.ozon_api_key,
    ) as client:
        async for batch in client.get_all_products():
            for item in batch:
                pid = item.get("product_id")
                if pid is not None:
                    actual.add(int(pid))
    return actual


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, ничего не удалять")
    args = parser.parse_args()

    print("Тяну актуальный список product_id из Ozon...")
    actual = await fetch_actual_product_ids()
    print(f"  Получено: {len(actual)} product_id")

    if not actual:
        print("ОШИБКА: Ozon вернул пустой список. Прерываю — иначе снесу всё.")
        return 1

    db = DatabaseManager(settings.database_url)
    await db.initialize()

    async with db.session() as session:  # type: AsyncSession
        # Найти лишние
        stale = (
            await session.execute(
                select(Product.id, Product.product_id, Product.offer_id, Product.name)
                .where(Product.product_id.notin_(actual))
            )
        ).all()

        print(f"\nЛишних товаров в БД: {len(stale)}")
        if not stale:
            print("Нечего удалять.")
            return 0

        for row in stale[:20]:
            print(f"  id={row.id} product_id={row.product_id} offer_id={row.offer_id!r} name={row.name!r}")
        if len(stale) > 20:
            print(f"  ... и ещё {len(stale) - 20}")

        stale_pk_ids = [r.id for r in stale]

        # Узнать какие таблицы физически существуют
        existing_tables = {
            r[0]
            for r in (
                await session.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            ).all()
        }

        pi_count = sh_count = 0
        if "posting_items" in existing_tables:
            pi_count = (
                await session.execute(
                    text("SELECT count(*) FROM posting_items WHERE product_id = ANY(:ids)"),
                    {"ids": stale_pk_ids},
                )
            ).scalar_one()
        if "stocks_history" in existing_tables:
            sh_count = (
                await session.execute(
                    text("SELECT count(*) FROM stocks_history WHERE product_id = ANY(:ids)"),
                    {"ids": stale_pk_ids},
                )
            ).scalar_one()

        print(f"\nСвязанные строки которые тоже будут удалены:")
        print(f"  posting_items:  {pi_count} (таблица {'есть' if 'posting_items' in existing_tables else 'НЕТ'})")
        print(f"  stocks_history: {sh_count} (таблица {'есть' if 'stocks_history' in existing_tables else 'НЕТ'})")

        if args.dry_run:
            print("\n[dry-run] Удаление пропущено.")
            return 0

        if not args.yes:
            answer = input("\nУдалить? [yes/NO]: ").strip().lower()
            if answer != "yes":
                print("Отмена.")
                return 0

        if "posting_items" in existing_tables:
            print("\nУдаляю posting_items...")
            await session.execute(
                text("DELETE FROM posting_items WHERE product_id = ANY(:ids)"),
                {"ids": stale_pk_ids},
            )
        if "stocks_history" in existing_tables:
            print("Удаляю stocks_history...")
            await session.execute(
                text("DELETE FROM stocks_history WHERE product_id = ANY(:ids)"),
                {"ids": stale_pk_ids},
            )
        print("Удаляю products...")
        await session.execute(delete(Product).where(Product.id.in_(stale_pk_ids)))
        await session.commit()
        print("Готово.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
