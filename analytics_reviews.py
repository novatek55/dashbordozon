"""Analiz otzyvov."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from src.database import db_manager
from sqlalchemy import select, func
from src.models import Review, Product


async def analyze():
    await db_manager.initialize()
    
    print("=" * 70)
    print("ANALITIKA OTZVov")
    print("=" * 70)
    
    async with db_manager.session() as session:
        # Obshhaja statistika
        result = await session.execute(select(func.count()).select_from(Review))
        total = result.scalar()
        print(f"\n[STATISTIKA] Vsego otzyvov: {total}")
        
        # Raspredeelenie po ocenkam
        result = await session.execute(
            select(Review.rating, func.count())
            .group_by(Review.rating)
            .order_by(Review.rating.desc())
        )
        print("\n[RASPREDELENIE] Po ocenkam:")
        for rating, count in result.all():
            pct = count / total * 100
            bar = "#" * int(pct / 2)
            print(f"   {rating}: {count:5d} ({pct:5.1f}%) {bar}")
        
        # Srednij rejtng
        result = await session.execute(select(func.avg(Review.rating)))
        avg_rating = result.scalar()
        print(f"\n[METRIKA] Srednij rejtng: {avg_rating:.2f}")
        
        # Top tovarov po kolichestvu otzyvov
        result = await session.execute(
            select(Review.sku, func.count().label("cnt"))
            .group_by(Review.sku)
            .order_by(func.count().desc())
            .limit(10)
        )
        print("\n[TOP 10] Tovary po kolichestvu otzyvov:")
        for sku, count in result.all():
            prod_result = await session.execute(
                select(Product.name).where(Product.product_id == sku)
            )
            name = prod_result.scalar() or f"SKU:{sku}"
            name = name[:40] + "..." if len(str(name)) > 40 else name
            print(f"   {count:3d} otzyvov - {name}")
        
        # Statusy otzyvov
        result = await session.execute(
            select(Review.status, func.count())
            .group_by(Review.status)
        )
        print("\n[STATUSY] Otzyvov:")
        for status, count in result.all():
            print(f"   {status}: {count}")
        
        # Negativnye otzyvy (1-2)
        result = await session.execute(
            select(func.count()).where(Review.rating <= 2)
        )
        negative = result.scalar()
        print(f"\n[NEGATIV] Otzyvov (1-2): {negative} ({negative/total*100:.1f}%)")
        
        # Pozitivnye (4-5)
        result = await session.execute(
            select(func.count()).where(Review.rating >= 4)
        )
        positive = result.scalar()
        print(f"[POZITIV] Otzyvov (4-5): {positive} ({positive/total*100:.1f}%)")
        
        # Pokupateli
        result = await session.execute(
            select(Review.is_buyer, func.count())
            .group_by(Review.is_buyer)
        )
        print("\n[ISTOChNIK] Otzyvov:")
        for is_buyer, count in result.all():
            tip = "Ot pokupatelej" if is_buyer else "Ot ne-pokupatelej"
            print(f"   {tip}: {count}")
    
    await db_manager.close()
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(analyze())
