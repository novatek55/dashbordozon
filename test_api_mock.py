import requests
import json

# Тестовые данные с allocated_supply
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

print('Testing API with mock data...')
response = requests.post(
    'http://127.0.0.1:8088/api/supply-plan/pallets',
    json={'items': test_items},
    timeout=10
)

result = response.json()
print(f'Status: {response.status_code}')
print(f'Success: {result.get("success")}')

if result.get('success'):
    clusters = result.get('clusters', [])
    print(f'Clusters: {len(clusters)}')
    for c in clusters:
        print(f'  {c["cluster"]}: {len(c["pallets"])} pallets')
        for p in c['pallets']:
            print(f'    Pallet {p["pallet_number"]}: {p["total_height"]}m, {p["total_weight"]}kg')
            for item in p['items']:
                print(f'      - {item["name"]}: {item["quantity"]} pcs')
else:
    print(f'Error: {result.get("error")}')
