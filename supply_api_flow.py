"""
Полный флоу создания мультикластерной поставки через Ozon Seller API.
Шаг 0: Получить cluster_id
Шаг 1: Создать черновик
Шаг 2: Получить склады
Шаг 3: Получить таймслоты
"""
import asyncio
import aiohttp
import json
import sys
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


async def ozon_post(session, ep, body, label=''):
    url = f'https://api-seller.ozon.ru{ep}'
    for attempt in range(5):
        async with session.post(url, headers=HEADERS, json=body) as r:
            s = r.status
            t = await r.text()
            try:
                d = json.loads(t)
            except Exception:
                d = {'raw': t[:500]}
            if s == 429:
                wait = 35
                print(f'  [{label}] 429, wait {wait}s (attempt {attempt+1})')
                await asyncio.sleep(wait)
                continue
            return s, d
    return s, d


async def main():
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:

        # === ШАГ 0: Cluster IDs ===
        print('=== Шаг 0: Cluster IDs ===')

        # Известные cluster_id (из предыдущих сканов)
        KNOWN_CLUSTERS = {
            'Москва': 4039,
            'Оренбург': 4069,
            'Санкт-Петербург': 4007,
            'Новосибирск': 4067,
        }

        orb_id = KNOWN_CLUSTERS['Оренбург']
        nsk_id = KNOWN_CLUSTERS['Новосибирск']
        spb_id = KNOWN_CLUSTERS['Санкт-Петербург']

        print(f'  Оренбург = {orb_id}')
        print(f'  Новосибирск = {nsk_id}')
        print(f'  СПб = {spb_id}')

        # === ШАГ 1: Создать мультикластерный черновик ===
        print()
        print('=== Шаг 1: /v1/draft/multi-cluster/create ===')
        print('  Оренбург: SKU 1866257461 x1')
        print('  Новосибирск: SKU 1866252620 x20')
        print('  СПб: SKU 1866252620 x30')

        body = {
            'clusters_info': [
                {'macrolocal_cluster_id': orb_id, 'items': [{'sku': 1866257461, 'quantity': 1}]},
                {'macrolocal_cluster_id': nsk_id, 'items': [{'sku': 1866252620, 'quantity': 20}]},
                {'macrolocal_cluster_id': spb_id, 'items': [{'sku': 1866252620, 'quantity': 30}]},
            ],
            'deletion_sku_mode': 'PARTIAL',
            'delivery_info': {
                'type': 'DROPOFF',
                'seller_warehouse_id': SELLER_WH,
                'drop_off_warehouse': {
                    'warehouse_id': DROPOFF_WH,
                    'warehouse_type': 'DELIVERY_POINT',
                },
            }
        }
        s1, d1 = await ozon_post(session, '/v1/draft/multi-cluster/create', body, 'create')
        draft_id = int(d1.get('draft_id') or 0)
        print(f'  Status: {s1}, draft_id: {draft_id}')
        if s1 != 200 or draft_id <= 0:
            print(f'  FAIL: {json.dumps(d1, ensure_ascii=False)}')
            return
        print(f'  URL: https://seller.ozon.ru/app/supply/orders/multi-cluster/{draft_id}')

        # === ШАГ 2: Получить склады ===
        print()
        print('=== Шаг 2: /v2/draft/create/info (склады) ===')
        info_data = {}
        for i in range(15):
            await asyncio.sleep(3)
            si, info_data = await ozon_post(session, '/v2/draft/create/info', {'draft_id': draft_id}, 'info')
            ds = info_data.get('status', '')
            print(f'  #{i+1}: {ds}')
            if si == 200 and ds != 'IN_PROGRESS':
                break

        for err in (info_data.get('errors') or []):
            print(f'  ERROR: {err.get("error_message", "?")}')

        # Собираем bundle_id для timeslot запроса
        selected_warehouses = []
        for ic in (info_data.get('clusters') or []):
            cname = ic.get('cluster_name', '?')
            cid = ic.get('macrolocal_cluster_id')
            print(f'')
            print(f'  --- {cname} (macrolocal={cid}) ---')
            for wh in (ic.get('warehouses') or []):
                avail = wh.get('availability_status') or {}
                state = avail.get('state', '?')
                storage = wh.get('storage_warehouse') or {}
                name = storage.get('name', '(auto)') if storage else '(auto)'
                bid = wh.get('bundle_id', '')
                rank = wh.get('total_rank')
                score = wh.get('total_score')
                inv = avail.get('invalid_reason', '')
                ok = state in ('FULL_AVAILABLE', 'AVAILABLE')
                m = 'OK' if ok else 'X'
                addr = (storage.get('address', '') if storage else '')[:80]
                print(f'    [{m}] {name} rank={rank} score={score} - {state}')
                if addr:
                    print(f'        {addr}')
                if inv and inv != 'UNSPECIFIED':
                    print(f'        reason: {inv}')
                if ok and bid:
                    selected_warehouses.append({
                        'macrolocal_cluster_id': cid,
                        'bundle_id': bid
                    })

        print(f'')
        print(f'  Доступных складов для timeslot: {len(selected_warehouses)}')

        if not selected_warehouses:
            print('  Нет доступных складов, пропускаем timeslot')
            out = Path('exports') / f'draft_{draft_id}_full.json'
            out.write_text(json.dumps({
                'draft_id': draft_id, 'create_info': info_data
            }, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'  Saved: {out}')
            return

        # === ШАГ 3: Таймслоты ===
        print()
        print('=== Шаг 3: /v2/draft/timeslot/info (слоты) ===')
        await asyncio.sleep(5)
        ts_body = {
            'draft_id': draft_id,
            'date_from': '2026-03-31',
            'date_to': '2026-04-14',
            'supply_type': 'CREATE_TYPE_CROSSDOCK',
            'selected_cluster_warehouses': selected_warehouses
        }
        ts_s, ts_d = await ozon_post(session, '/v2/draft/timeslot/info', ts_body, 'timeslot')
        print(f'  Status: {ts_s}')
        ts_text = json.dumps(ts_d, ensure_ascii=False, indent=2)
        print(ts_text[:4000])

        # === Сохраняем всё ===
        out = Path('exports') / f'draft_{draft_id}_full.json'
        all_data = {
            'draft_id': draft_id,
            'create_info': info_data,
            'timeslot_info': ts_d,
            'selected_warehouses': selected_warehouses
        }
        out.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'')
        print(f'Saved: {out}')


if __name__ == '__main__':
    asyncio.run(main())
