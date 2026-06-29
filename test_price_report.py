from decimal import Decimal
import asyncio

from src.dashboard.routes.prices import build_price_report_item
from src.ozon_client import OzonClient
from src.sync_manager import SyncManager


def test_build_price_report_item_marks_beneficial_when_current_price_is_not_above_recommended():
    row = {
        "offer_id": "ART-1",
        "product_name": "Table",
        "ozon_product_id": 1001,
        "fbo_sku_id": 2001,
        "fbs_sku_id": None,
        "price_current": Decimal("990.00"),
        "price_base": Decimal("1290.00"),
        "customer_price": Decimal("880.00"),
        "price_recommended": Decimal("1000.00"),
        "recommended_price_link": "https://example.test/p/1",
        "price_details_synced_at": None,
        "last_synced_at": None,
    }

    item = build_price_report_item(row)

    assert item["offer_id"] == "ART-1"
    assert item["price_current"] == 990.0
    assert item["customer_price"] == 880.0
    assert item["customer_price_status"] == "ok"
    assert item["price_recommended"] == 1000.0
    assert item["recommended_price_link"] == "https://example.test/p/1"
    assert item["is_beneficial_price"] is True
    assert item["price_index"] == 0.99
    assert item["ozon_competitor_prices"]["status"] == "missing"
    assert item["own_other_marketplace_prices"]["index"] == 0.99
    assert item["own_other_marketplace_prices"]["price"] == 1000.0
    assert item["own_other_marketplace_prices"]["source"] == "example.test"
    assert item["other_marketplace_competitor_prices"]["index"] == 0.99
    assert item["other_marketplace_competitor_prices"]["price"] == 1000.0
    assert item["other_marketplace_competitor_prices"]["source"] == "example.test"
    assert item["beneficial_price_status"] == "Да"


def test_build_price_report_item_uses_v5_price_indexes_for_market_cards():
    row = {
        "offer_id": "ART-INDEX",
        "product_name": "Indexed",
        "ozon_product_id": 1004,
        "fbo_sku_id": 2004,
        "fbs_sku_id": None,
        "price_current": Decimal("1000.00"),
        "price_base": Decimal("1300.00"),
        "customer_price": None,
        "price_indexes": {
            "ozon_index_data": {
                "minimal_price": "724",
                "price_index_value": "1.38",
            },
            "self_marketplaces_index_data": {
                "minimal_price": "788",
                "price_index_value": "1.22",
                "minimal_price_link": "https://wildberries.ru/catalog/1/detail.aspx",
            },
            "external_index_data": {
                "minimal_price": "799",
                "price_index_value": "1.25",
                "minimal_price_link": "https://market.yandex.ru/product/1",
            },
        },
        "price_recommended": None,
        "recommended_price_link": "",
        "price_details_synced_at": None,
        "last_synced_at": None,
    }

    item = build_price_report_item(row)

    assert item["ozon_competitor_prices"]["price"] == 724.0
    assert item["ozon_competitor_prices"]["index"] == 1.38
    assert item["own_other_marketplace_prices"]["price"] == 788.0
    assert item["own_other_marketplace_prices"]["index"] == 1.22
    assert item["own_other_marketplace_prices"]["source"] == "wildberries.ru"
    assert item["other_marketplace_competitor_prices"]["price"] == 799.0
    assert item["other_marketplace_competitor_prices"]["index"] == 1.25
    assert item["other_marketplace_competitor_prices"]["source"] == "market.yandex.ru"


def test_build_price_report_item_marks_not_beneficial_when_current_price_is_above_recommended():
    row = {
        "offer_id": "ART-2",
        "product_name": "Chair",
        "ozon_product_id": 1002,
        "fbo_sku_id": None,
        "fbs_sku_id": 3002,
        "price_current": Decimal("1200.00"),
        "price_base": Decimal("1500.00"),
        "price_recommended": Decimal("1000.00"),
        "recommended_price_link": "",
        "last_synced_at": None,
    }

    item = build_price_report_item(row)

    assert item["is_beneficial_price"] is False
    assert item["beneficial_price_status"] == "Нет"


def test_build_price_report_item_leaves_beneficial_status_empty_without_comparable_prices():
    row = {
        "offer_id": "ART-3",
        "product_name": "Shelf",
        "ozon_product_id": 1003,
        "fbo_sku_id": 2003,
        "fbs_sku_id": 3003,
        "price_current": Decimal("700.00"),
        "price_base": Decimal("900.00"),
        "price_recommended": None,
        "recommended_price_link": "",
        "last_synced_at": None,
    }

    item = build_price_report_item(row)

    assert item["is_beneficial_price"] is None
    assert item["beneficial_price_status"] == ""


def test_sync_products_report_helpers_read_russian_ozon_price_headers():
    manager = SyncManager(client=None)
    row = {
        "Артикул": "ART-4",
        "Название товара": "Bench",
        "Текущая цена с учётом скидки, руб.": "1 190,50",
        "Базовая цена (цена до скидок), руб.": "1 490",
        "Рекомендованная цена, руб.": "1 200",
        "Актуальная ссылка на рекомендованную цену": "https://example.test/bench",
    }

    assert manager._pick_value(row, ["Артикул"]) == "ART-4"
    assert manager._parse_decimal_flexible(
        manager._pick_value(row, ["Текущая цена с учётом скидки, руб."])
    ) == 1190.5
    assert manager._parse_decimal_flexible(
        manager._pick_by_contains(row, ["рекомендованная цена"])
    ) == 1200.0
    assert manager._pick_by_contains(row, ["ссылка"]) == "https://example.test/bench"


def test_sync_products_report_row_builder_reads_russian_price_columns():
    manager = SyncManager(client=None)
    row = {
        "Артикул": "ART-5",
        "Название товара": "Rack",
        "Ozon Product ID": "555",
        "FBO Ozon SKU ID": "666",
        "FBS Ozon SKU ID": "777",
        "Текущая цена с учётом скидки, руб.": "1 190,50",
        "Базовая цена (цена до скидок), руб.": "1 490",
        "Рекомендованная цена, руб.": "1 200",
        "Актуальная ссылка на рекомендованную цену": "https://example.test/rack",
    }

    data = manager._build_report_product_item_row_data(row, report_id=10, line_no=2)

    assert data["offer_id"] == "ART-5"
    assert data["product_name"] == "Rack"
    assert data["ozon_product_id"] == 555
    assert data["fbo_sku_id"] == 666
    assert data["fbs_sku_id"] == 777
    assert data["price_current"] == 1190.5
    assert data["price_base"] == 1490.0
    assert data["price_recommended"] == 1200.0
    assert data["recommended_price_link"] == "https://example.test/rack"


def test_sync_products_report_row_builder_reads_current_ozon_price_headers():
    manager = SyncManager(client=None)
    row = {
        "Артикул": "ART-6",
        "Название товара": "Stand",
        "Текущая цена с учетом скидки, ₽": "1 669,00",
        "Цена до скидки (перечеркнутая цена), ₽": "4 400,00",
        "Цена Premium, ₽": "",
    }

    data = manager._build_report_product_item_row_data(row, report_id=11, line_no=3)

    assert data["offer_id"] == "ART-6"
    assert data["product_name"] == "Stand"
    assert data["price_current"] == 1669.0
    assert data["price_base"] == 4400.0


def test_product_price_details_row_builder_reads_customer_price_object():
    manager = SyncManager(client=None)
    row = {
        "sku": 123456789,
        "offer_id": "ART-7",
        "customer_price": {"price": "984.00"},
        "price": {"price": "1600.00"},
        "price_indexes": [{"type": "external_marketplace", "index": "1.22"}],
    }

    data = manager._build_product_price_detail_row_data(row)

    assert data["sku"] == 123456789
    assert data["offer_id"] == "ART-7"
    assert data["customer_price"] == 984.0
    assert data["price"] == 1600.0
    assert data["details_status"] == "ok"
    assert data["price_indexes"] == [{"type": "external_marketplace", "index": "1.22"}]


def test_product_price_details_client_filters_zero_skus_before_request():
    class DummyClient(OzonClient):
        def __init__(self):
            super().__init__(client_id="dummy", api_key="dummy")
            self.payload = None

        async def _make_request(self, method, endpoint, data=None, **kwargs):
            self.payload = data
            return {"prices": []}

    client = DummyClient()

    asyncio.run(client.get_product_price_details([0, 123, None, -5, 456]))

    assert client.payload == {"skus": ["123", "456"]}


def test_product_prices_client_uses_v5_endpoint():
    class DummyClient(OzonClient):
        def __init__(self):
            super().__init__(client_id="dummy", api_key="dummy")
            self.endpoint = None
            self.payload = None

        async def _make_request(self, method, endpoint, data=None, **kwargs):
            self.endpoint = endpoint
            self.payload = data
            return {"items": []}

    client = DummyClient()

    asyncio.run(client.get_product_prices())

    assert client.endpoint == "/v5/product/info/prices"
    assert client.payload == {"filter": {"visibility": "ALL"}, "limit": 1000, "last_id": ""}
