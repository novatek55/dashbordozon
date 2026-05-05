"""
Тест: создать отдельные crossdock-черновики для каждого кластера,
получить детальные склады и таймслоты.
"""
import asyncio
import aiohttp
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

env_path = Path('.env')
env_vars = {}
for line in env_path.read_text(encoding='utf-8-sig').splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    if '=' in line:
        k, v = line.split('=', 1)
        env_vars[k.strip()] = v.strip().strip('"')

CLIENT_ID = env_vars.get('OZON_CLIENT_ID', '')
API_KEY = env_vars.get('OZON_SUPPLY_API_KEY', '') or env_vars.get('OZON_API_KEY', '')
SELLER_WH = int(env_vars.get('OZON_CROSSDOCK_SELLER_WAREHOUSE_ID', '23785825652000'))
DROPOFF_WH = 22190776129000  # МО_ЩЕРБИНКА_ХАБ
HEADERS = {'Client-Id': CLIENT_ID, 'Api-Key': API_KEY, 'Content-Type': 'application/json'}

CLUSTERS = [
    {"name": "Оренбург", "id": 4069, "sku": 1866257461, "qty": 1},
    {"name": "Новосибирск", "id": 4067, "sku": 1866252620, "qty": 1},
    {"name": "СПб и СЗО", "id": 4007, "sku": 1866252620, "qty": 1},
]


async def ozon_post(session, ep, body, label=''):
    url = f'https://api-seller.ozon.ru{ep}'
    for attempt in range(7):
        async with session.post(url, headers=HEADERS, json=body) as r:
            s = r.status
            t = await r.text()
            try:
                d = json.loads(t)
            except Exception:
                d = {'raw': t[:500]}
            if s == 429:
                wait = min(8 * (1.7 ** attempt), 60)
                print(f'    [{label}] 429, wait {wait:.0f}s (attempt {attempt+1})')
                await asyncio.sleep(wait)
                continue
            return s, d
    return s, d


async def process_cluster(session, cluster, all_results):
    name = cluster["name"]
    cid = cluster["id"]
    sku = cluster["sku"]
    qty = cluster["qty"]

    print(f'\n{"="*60}')
    print(f'  {name} (macrolocal={cid}): SKU {sku} x{qty}')
    print(f'{"="*60}')

    result = {"cluster": name, "macrolocal_cluster_id": cid}

    # ── Шаг 1: Создать crossdock черновик ──
    print(f'  [1] POST /v1/draft/crossdock/create')
    body = {
        "cluster_info": {
            "macrolocal_cluster_id": cid,
            "items": [{"sku": sku, "quantity": qty}],
        },
        "deletion_sku_mode": "PARTIAL",
        "delivery_info": {
            "type": "DROPOFF",
            "seller_warehouse_id": SELLER_WH,
            "drop_off_warehouse": {
                "warehouse_id": DROPOFF_WH,
                "warehouse_type": "DELIVERY_POINT",
            },
        },
    }
    s1, d1 = await ozon_post(session, '/v1/draft/crossdock/create', body, f'{name}/create')
    draft_id = int(d1.get('draft_id') or 0)
    errors = d1.get('errors') or []
    result["draft_id"] = draft_id
    result["create_status"] = s1
    result["create_errors"] = errors
    print(f'      Status: {s1}, draft_id: {draft_id}')
    if errors:
        for e in errors:
            print(f'      ERROR: {e.get("error_message","?")} — {e.get("message","")}')
    if s1 != 200 or draft_id <= 0:
        result["error"] = f"Failed to create: {d1}"
        all_results.append(result)
        return

    # ── Шаг 2: Получить склады ──
    print(f'  [2] POST /v2/draft/create/info')
    info_data = {}
    for i in range(15):
        await asyncio.sleep(3)
        s2, info_data = await ozon_post(session, '/v2/draft/create/info', {'draft_id': draft_id}, f'{name}/info')
        ds = info_data.get('status', '')
        if s2 == 200 and ds != 'IN_PROGRESS':
            break

    print(f'      Status: {info_data.get("status","?")}')
    warehouses = []
    for ic in info_data.get('clusters') or []:
        for wh in ic.get('warehouses') or []:
            avail = wh.get('availability_status') or {}
            state = avail.get('state', '?')
            storage = wh.get('storage_warehouse') or {}
            ok = state in ('FULL_AVAILABLE', 'AVAILABLE')
            wh_info = {
                "name": storage.get('name', '(?)') if storage else '(?)',
                "warehouse_id": storage.get('warehouse_id') if storage else None,
                "state": state,
                "score": wh.get('total_score'),
                "rank": wh.get('total_rank'),
                "address": storage.get('address', '') if storage else '',
                "available": ok,
                "invalid_reason": avail.get('invalid_reason', ''),
            }
            warehouses.append(wh_info)
            m = 'OK' if ok else 'X '
            print(f'      [{m}] {wh_info["name"]} rank={wh_info["rank"]} score={wh_info["score"]} — {state}')
            if wh_info["address"]:
                print(f'           {wh_info["address"][:80]}')
            if wh_info["invalid_reason"] and wh_info["invalid_reason"] != 'UNSPECIFIED':
                print(f'           reason: {wh_info["invalid_reason"]}')

    result["warehouses"] = warehouses
    result["info_status"] = info_data.get("status")
    result["available_count"] = sum(1 for w in warehouses if w["available"])

    # ── Шаг 3: Таймслоты ──
    available_whs = [w for w in warehouses if w["available"] and w.get("warehouse_id")]
    if not available_whs:
        print(f'      Нет доступных складов — таймслоты не запрашиваем')
        all_results.append(result)
        return

    print(f'  [3] POST /v2/draft/timeslot/info ({len(available_whs)} складов)')
    await asyncio.sleep(3)
    date_from = datetime.now().strftime('%Y-%m-%d')
    date_to = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')

    # Для crossdock: передаём storage_warehouse_id конкретного склада
    selected = [
        {"macrolocal_cluster_id": cid, "storage_warehouse_id": w["warehouse_id"]}
        for w in available_whs[:1]  # Берём первый доступный (rank=1)
    ]
    ts_body = {
        "draft_id": draft_id,
        "date_from": date_from,
        "date_to": date_to,
        "supply_type": "CROSSDOCK",
        "selected_cluster_warehouses": selected,
    }
    s3, d3 = await ozon_post(session, '/v2/draft/timeslot/info', ts_body, f'{name}/timeslot')
    print(f'      Status: {s3}, error_reason: {d3.get("error_reason","?")}')
    ts_data = d3.get('result') or {}
    days = (ts_data.get('drop_off_warehouse_timeslots') or {}).get('days') or []
    total_slots = sum(len(day.get('timeslots') or []) for day in days)
    tz = (ts_data.get('drop_off_warehouse_timeslots') or {}).get('warehouse_timezone', '')
    print(f'      Timezone: {tz}, days: {len(days)}, total_slots: {total_slots}')
    for day in days[:3]:
        dt = day.get('date_in_timezone', '')
        slots = day.get('timeslots') or []
        times = ', '.join(s.get('from_in_timezone', '').split('T')[1][:5] for s in slots[:5])
        more = f' +{len(slots)-5}' if len(slots) > 5 else ''
        print(f'      {dt}: {times}{more}')

    result["timeslots"] = {
        "status": s3,
        "error_reason": d3.get("error_reason"),
        "timezone": tz,
        "days_count": len(days),
        "total_slots": total_slots,
        "selected_warehouse": available_whs[0]["name"] if available_whs else None,
    }
    all_results.append(result)


async def main():
    timeout = aiohttp.ClientTimeout(total=600)
    all_results = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for cluster in CLUSTERS:
            await process_cluster(session, cluster, all_results)
            await asyncio.sleep(5)  # Пауза между кластерами

    # Сохраняем
    out = Path('exports') / 'crossdock_3clusters_test.json'
    out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nSaved: {out}')

    # Итог
    print(f'\n{"="*60}')
    print(f'  ИТОГ:')
    for r in all_results:
        wh_count = len(r.get("warehouses") or [])
        avail = r.get("available_count", 0)
        slots = (r.get("timeslots") or {}).get("total_slots", 0)
        print(f'  {r["cluster"]}: draft={r.get("draft_id")}, складов={wh_count} (доступно {avail}), слотов={slots}')


if __name__ == '__main__':
    asyncio.run(main())
