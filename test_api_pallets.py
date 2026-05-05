#!/usr/bin/env python3
import requests
import json

print("Получаю данные supply-plan...")
resp = requests.get('http://127.0.0.1:8088/api/supply-plan?limit=5', timeout=10)
data = resp.json()

print(f"Товаров: {len(data['items'])}")
print(f"Сводка: {data['summary']}")
print()

# Берем товары с allocated_supply > 0
items_with_alloc = [item for item in data['items'] 
                   if any(d.get('allocated_supply', 0) > 0 for d in item.get('details', []))]

print(f"Товаров с распределением: {len(items_with_alloc)}")

if items_with_alloc:
    test_items = items_with_alloc[:3]
    
    print("Отправляю на расчет паллетов...")
    response = requests.post(
        'http://127.0.0.1:8088/api/supply-plan/pallets',
        json={'items': test_items},
        timeout=10
    )
    
    result = response.json()
    print(f"Status: {response.status_code}")
    print(f"Success: {result.get('success')}")
    
    if result.get('success'):
        print()
        print("РЕЗУЛЬТАТ РАСЧЕТА ПАЛЛЕТОВ:")
        print("=" * 50)
        for c in result['clusters']:
            print(f"Кластер: {c['cluster']}")
            print(f"  Паллет: {len(c['pallets'])}")
            for p in c['pallets']:
                print(f"    Паллета {p['pallet_number']}: {p['total_height']}м, {p['total_weight']}кг")
                for item in p['items']:
                    print(f"      - {item['name']}: {item['quantity']}шт ({item['layers']} слоев)")
            print()
    else:
        print(f"Ошибка: {result.get('error')}")
else:
    print("Нет товаров с распределением. Создаю тестовые данные...")
    # Создаем тестовые данные
    test_items = [
        {
            'offer_id': '401 держатель',
            'details': [
                {'cluster_name': 'Москва', 'allocated_supply': 60}
            ]
        },
        {
            'offer_id': '106 носорог',
            'details': [
                {'cluster_name': 'Москва', 'allocated_supply': 12}
            ]
        }
    ]
    
    response = requests.post(
        'http://127.0.0.1:8088/api/supply-plan/pallets',
        json={'items': test_items},
        timeout=10
    )
    
    result = response.json()
    print(f"Status: {response.status_code}")
    print(f"Success: {result.get('success')}")
    
    if result.get('success'):
        print()
        print("РЕЗУЛЬТАТ:")
        for c in result['clusters']:
            print(f"  {c['cluster']}: {len(c['pallets'])} паллет")
