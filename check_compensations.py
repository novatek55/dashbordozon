"""
Proverka summu kompensaciy za fevral 2026
"""
import asyncio
from datetime import datetime, timezone, timedelta
from src.database import init_database, close_database, db_manager
from sqlalchemy import text

MSK = timezone(timedelta(hours=3))

async def check():
    await init_database()
    
    async with db_manager.session() as session:
        # Februal 2026
        start = datetime(2026, 2, 1, 0, 0, 0, tzinfo=MSK).astimezone(timezone.utc)
        end = datetime(2026, 3, 1, 0, 0, 0, tzinfo=MSK).astimezone(timezone.utc)
        
        print("Proverka kompensaciy za fevral 2026")
        print(f"Period: {start} - {end}")
        print()
        
        # 1. Iz transactions (po description)
        result1 = await session.execute(text("""
            SELECT 
                description,
                amount,
                operation_date
            FROM transactions
            WHERE operation_date >= :start AND operation_date < :end
              AND (LOWER(description) LIKE '%потеря по вине ozon%' 
                   OR LOWER(description) LIKE '%компенсац%')
            ORDER BY operation_date
        """), {"start": start, "end": end})
        
        print("1. Kompensacii iz transactions:")
        total_trans = 0
        for row in result1.fetchall():
            print(f"   {row.operation_date.date()} | {row.amount:>12.2f} | {row.description[:50]}")
            total_trans += float(row.amount)
        print(f"   SUMMA: {total_trans:.2f}")
        print()
        
        # 2. Iz report_compensation_items
        result2 = await session.execute(text("""
            SELECT 
                article_name,
                amount,
                effective_date
            FROM report_compensation_items
            WHERE effective_date >= :start AND effective_date < :end
            ORDER BY effective_date
        """), {"start": start, "end": end})
        
        print("2. Kompensacii iz report_compensation_items:")
        total_comp = 0
        for row in result2.fetchall():
            print(f"   {row.effective_date.date()} | {row.amount:>12.2f} | {row.article_name[:50]}")
            total_comp += float(row.amount)
        print(f"   SUMMA: {total_comp:.2f}")
        print()
        
        print(f"ITOGO (trans + comp): {total_trans + total_comp:.2f}")
    
    await close_database()

if __name__ == "__main__":
    asyncio.run(check())
