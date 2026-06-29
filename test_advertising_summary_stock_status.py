import asyncio
import json
from types import SimpleNamespace

from src.dashboard.routes.advertising import get_advertising_summary


class _FakeConnection:
    async def fetch(self, sql, *params):
        sql_norm = " ".join(sql.lower().split())
        if "from campaign_statistics cs" in sql_norm and "group by cs.sku" in sql_norm:
            return []
        if "with stock as" in sql_norm:
            return [{"offer_key": "art-1", "offer_id": "ART-1", "sku": 1001, "product_name": "Item 1", "total_stock": 10}]
        if "from promo_products pp" in sql_norm:
            return []
        return []


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_advertising_summary_includes_stocked_article_without_enabled_ad():
    request = SimpleNamespace(
        query={"date_from": "2026-06-01", "date_to": "2026-06-02"},
        app={"pool": _FakePool(_FakeConnection())},
    )

    response = asyncio.run(get_advertising_summary(request))

    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["items"] == [
        {
            "offer_id": "ART-1",
            "product_name": "Item 1",
            "stock_total": 10,
            "ad_enabled": False,
            "promo_enabled": False,
            "views": 0,
            "clicks": 0,
            "adds_to_cart": 0,
            "spent": 0,
            "ad_orders": 0,
            "ad_revenue": 0,
            "ad_orders_cpo": 0,
            "ad_revenue_cpo": 0,
            "ad_orders_total": 0,
            "ad_revenue_total": 0,
            "total_qty": 0,
            "total_revenue": 0,
            "organic_qty": 0,
            "organic_revenue": 0,
            "ad_share_pct": 0,
            "ctr": 0,
            "cpc": 0,
            "cpo": 0,
            "drr_ad": 0,
            "drr_total": 0,
            "unit_cost": 0,
        }
    ]


def test_advertising_summary_headers_place_status_lamps_after_article():
    html = open("web/orders_dashboard.html", encoding="utf-8").read()

    article_idx = html.index('data-sort="offer_id"')
    ad_idx = html.index('data-sort="${c.key}"><span>${c.label}</span>', html.index("for (const c of advStatusCols)"))
    metric_idx = html.index("for (const c of cols)")

    assert article_idx < ad_idx < metric_idx
    assert "Рекламная кампания по артикулу" in html
    assert "Участие артикула в акции" in html
