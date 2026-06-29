import asyncio, asyncpg, os, re, sys
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv()
dsn = re.sub(r"^postgresql\+asyncpg", "postgresql", os.environ.get("DATABASE_URL", ""))

async def main():
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch("""
        SELECT rpi.offer_id, sum(cs.spent::float8) AS spent
        FROM campaign_statistics cs
        JOIN report_products_items rpi ON (rpi.fbo_sku_id = cs.sku OR rpi.fbs_sku_id = cs.sku)
        WHERE cs.date >= now() - interval '60 days'
        GROUP BY rpi.offer_id
        ORDER BY spent DESC LIMIT 3
    """)
    for r in rows:
        print(r["offer_id"], round(float(r["spent"]), 2))
    await conn.close()

asyncio.run(main())
