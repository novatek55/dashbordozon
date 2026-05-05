"""
Proverka dannyh za predydushchiy mesyac (fevral) dlya rascheta plana mart
"""
import asyncio
from datetime import datetime, timezone, timedelta
from src.database import init_database, close_database, db_manager
from sqlalchemy import text

MSK = timezone(timedelta(hours=3))

async def check():
    await init_database()
    
    async with db_manager.session() as session:
        # Dlya marta 2026 predydushchiy mesyac - fevral 2026
        month_value = "2026-03"
        prev_month_start = datetime(2026, 2, 1, tzinfo=MSK).astimezone(timezone.utc)
        prev_month_end = datetime(2026, 3, 1, tzinfo=MSK).astimezone(timezone.utc)
        
        print(f"Proverka dannyh za predydushchiy mesyac")
        print(f"Period: {prev_month_start} - {prev_month_end}")
        print()
        
        # Zapros kak v osnovnom kode
        prev_stats = await session.execute(text("""
            SELECT 
                COALESCE(SUM(CASE WHEN description = 'Доставка покупателю' AND type = 'orders' 
                    AND (raw_data->>'accruals_for_sale')::numeric > 0 THEN amount ELSE 0 END), 0) as revenue,
                COALESCE(SUM(CASE WHEN (raw_data->>'sale_commission')::numeric < 0 
                    THEN (raw_data->>'sale_commission')::numeric ELSE 0 END), 0) as commission,
                COALESCE(SUM(CASE WHEN description IN ('Оплата за клик', 'Закрепление отзыва', 'Вывод в топ', 'Реклама в сети интернет на сайте')
                    THEN amount ELSE 0 END), 0) as ads
            FROM transactions
            WHERE operation_date >= :start AND operation_date < :end
        """), {"start": prev_month_start, "end": prev_month_end})
        
        row = prev_stats.fetchone()
        print(f"Revenue (vyruchka): {row.revenue}")
        print(f"Commission: {row.commission}")
        print(f"Ads (marketing): {row.ads}")
        
        if row.revenue and float(row.revenue) > 0:
            rev = float(row.revenue)
            comm = abs(float(row.commission)) if row.commission else 0
            ads = abs(float(row.ads)) if row.ads else 0
            
            print()
            print("Raschet procentov:")
            print(f"  Commission %: {comm/rev*100:.2f}%")
            print(f"  Ads %: {ads/rev*100:.2f}%")
            print(f"  Total expenses %: {(comm+ads)/rev*100:.2f}%")
        else:
            print("Vyruchka = 0 ili net dannyh!")
        
        # Proverim chto voobsche est transakcii za etot period
        print()
        print("Proverka nalichiya transakciy za fevral 2026:")
        check_result = await session.execute(text("""
            SELECT COUNT(*) as cnt,
                   MIN(operation_date) as min_date,
                   MAX(operation_date) as max_date
            FROM transactions
            WHERE operation_date >= :start AND operation_date < :end
        """), {"start": prev_month_start, "end": prev_month_end})
        
        check_row = check_result.fetchone()
        print(f"  Kolichestvo transakciy: {check_row.cnt}")
        print(f"  Min date: {check_row.min_date}")
        print(f"  Max date: {check_row.max_date}")
    
    await close_database()

if __name__ == "__main__":
    asyncio.run(check())
