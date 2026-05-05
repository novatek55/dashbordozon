import requests

resp = requests.get('http://127.0.0.1:8088/api/supply-plan?limit=3', timeout=10)
data = resp.json()

items_with_alloc = [item for item in data['items'] 
                   if any(d.get('allocated_supply', 0) > 0 for d in item.get('details', []))]

print(f'Total items: {len(data["items"])}')
print(f'With allocated_supply: {len(items_with_alloc)}')

if items_with_alloc:
    print('Testing pallet API...')
    response = requests.post(
        'http://127.0.0.1:8088/api/supply-plan/pallets',
        json={'items': items_with_alloc},
        timeout=10
    )
    
    result = response.json()
    print(f'Status: {response.status_code}')
    print(f'success: {result.get("success")}')
    
    if result.get('success'):
        clusters = result.get('clusters', [])
        print(f'Clusters: {len(clusters)}')
        for c in clusters:
            print(f'  {c["cluster"]}: {len(c["pallets"])} pallets')
    else:
        print(f'Error: {result.get("error")}')
