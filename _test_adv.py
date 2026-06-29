import urllib.request, json, time, sys
sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://localhost:8088"
offers = ["204 пешка", "123 V центр"]

for offer in offers:
    url = f"{BASE}/api/advertising-report?offer_id={urllib.request.quote(offer)}&date_from=2026-05-01&date_to=2026-06-03"
    t0 = time.perf_counter()
    try:
        r = urllib.request.urlopen(url, timeout=30)
        data = json.loads(r.read())
        elapsed = time.perf_counter() - t0
        daily = data.get("daily", [])
        campaigns = data.get("campaigns", [])
        summary = data.get("summary", {})
        skus = data.get("skus", [])
        print(f"\n=== {offer} ({elapsed:.2f}s) ===")
        print(f"  SKUs: {skus}")
        print(f"  product_name: {data.get('product_name')}")
        print(f"  daily points: {len(daily)}")
        print(f"  campaigns: {len(campaigns)}")
        print(f"  summary.spent: {summary.get('spent')}")
        print(f"  summary.views: {summary.get('views')}")
        print(f"  summary.total_qty: {summary.get('total_qty')}")
        print(f"  promo_markers: {len(data.get('promo_markers', []))}")
        # Проверяем что daily не пустые
        non_zero = [d for d in daily if d.get("spent", 0) > 0 or d.get("total_qty", 0) > 0]
        print(f"  daily non-zero days: {len(non_zero)}")
    except Exception as e:
        print(f"\n=== {offer} ERROR: {e} ===")

# Тест summary endpoint
print("\n=== /api/advertising-summary ===")
url = f"{BASE}/api/advertising-summary?date_from=2026-05-01&date_to=2026-06-03"
t0 = time.perf_counter()
try:
    r = urllib.request.urlopen(url, timeout=30)
    data = json.loads(r.read())
    elapsed = time.perf_counter() - t0
    items = data.get("items", [])
    print(f"  Time: {elapsed:.2f}s, items: {len(items)}")
    if items:
        top = items[0]
        print(f"  Top: {top.get('offer_id')} spent={top.get('spent')} views={top.get('views')}")
except Exception as e:
    print(f"  ERROR: {e}")
