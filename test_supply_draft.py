"""Тестовый скрипт: черновик поставки #93492572 - склад кросс-докинга и таймслоты."""
import asyncio
import aiohttp
import json
import sys
import io
from datetime import datetime, timedelta

# Фиксим кодировку Windows-консоли
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

CLIENT_ID = "146478"
API_KEY = "04c033fd-7b08-4d31-a2f3-9fedce82da88"
BASE_URL = "https://api-seller.ozon.ru"
DRAFT_ID = 93492572

HEADERS = {
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
}


async def post(session: aiohttp.ClientSession, endpoint: str, body: dict, label: str = "") -> dict:
    await asyncio.sleep(1)  # плавно, без 429
    url = f"{BASE_URL}{endpoint}"
    async with session.post(url, headers=HEADERS, json=body) as resp:
        text = await resp.text()
        print(f"\n{'='*60}")
        print(f"{'[' + label + '] ' if label else ''}POST {endpoint}  ->  HTTP {resp.status}")
        print(f"{'='*60}")
        try:
            data = json.loads(text)
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return data
        except Exception:
            print(text[:500])
            return {}


async def main():
    # Период для таймслотов: следующие 14 дней
    date_from = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\nЧерновик поставки: #{DRAFT_ID}")
    print(f"Период таймслотов: {date_from} -- {date_to}")

    async with aiohttp.ClientSession() as session:

        # 1. Склады FBO с типом КРОСС-ДОКИНГ
        await post(
            session,
            "/v1/warehouse/fbo/list",
            {"filter_by_supply_type": "CREATE_TYPE_CROSSDOCK"},
            "Склады кросс-докинга"
        )

        # 2. Доступные таймслоты для черновика (v1)
        await post(
            session,
            "/v1/draft/timeslot/info",
            {
                "draft_id": DRAFT_ID,
                "date_from": date_from,
                "date_to": date_to,
            },
            "Таймслоты v1"
        )

        # 3. То же самое через v2 (v1 помечен как устаревший)
        await post(
            session,
            "/v2/draft/timeslot/info",
            {
                "draft_id": DRAFT_ID,
                "date_from": date_from,
                "date_to": date_to,
            },
            "Таймслоты v2"
        )


if __name__ == "__main__":
    asyncio.run(main())
