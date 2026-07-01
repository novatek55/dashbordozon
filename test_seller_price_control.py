import json

from src.seller_price_control import (
    extract_customer_prices_from_capture,
    extract_customer_prices_from_payload,
    find_known_price_contexts,
    parse_money_value,
)


def test_parse_money_value_reads_russian_money_strings():
    assert parse_money_value("1 013 ₽") == 1013.0
    assert parse_money_value("5\u00a0515 ₽") == 5515.0
    assert parse_money_value({"price": "984 ₽"}) == 984.0
    assert parse_money_value(0) is None


def test_extract_customer_prices_from_payload_reads_explicit_customer_price_fields():
    payload = {
        "items": [
            {"offer_id": "405-30", "customerPrice": "984 ₽"},
            {"offerId": "405-40", "priceForBuyer": {"value": 1013}},
            {"offer_id": "NO-PRICE", "price": 1600},
        ]
    }

    records = extract_customer_prices_from_payload(payload, "https://seller.ozon.ru/api/test")

    assert [(r.offer_id, r.customer_price, r.source_key) for r in records] == [
        ("405-30", 984.0, "customerPrice"),
        ("405-40", 1013.0, "priceForBuyer"),
    ]


def test_extract_customer_prices_from_payload_reads_seller_price_control_products():
    payload = {
        "products": [
            {
                "part_item": {"offer_id": "405-30"},
                "part_marketing_price": {
                    "price": {"currencyCode": "RUB", "units": "1093", "nanos": 0},
                    "seller_price": {"currencyCode": "RUB", "units": "1600", "nanos": 0},
                    "oa_price": {"currencyCode": "RUB", "units": "984", "nanos": 0},
                },
            }
        ]
    }

    records = extract_customer_prices_from_payload(payload, "https://seller.ozon.ru/api/v1/products/list-by-filter")

    assert len(records) == 1
    assert records[0].offer_id == "405-30"
    assert records[0].customer_price == 984.0
    assert records[0].source_key == "part_marketing_price.oa_price"


def test_extract_customer_prices_from_capture_reads_json_response_bodies():
    capture = {
        "events": [
            {
                "url": "https://seller.ozon.ru/api/prices",
                "responseBody": json.dumps({"rows": [{"offer_id": "114", "buyer_price": 5515}]}),
            }
        ]
    }

    records = extract_customer_prices_from_capture(capture)

    assert len(records) == 1
    assert records[0].offer_id == "114"
    assert records[0].customer_price == 5515.0
    assert records[0].source_url == "https://seller.ozon.ru/api/prices"


def test_find_known_price_contexts_reports_responses_containing_reference_prices():
    capture = {
        "events": [
            {"url": "u1", "status": 200, "mimeType": "application/json", "responseBody": '{"price":1013}'},
            {"url": "u2", "status": 200, "mimeType": "application/json", "responseBody": '{"price":1600}'},
        ]
    }

    matches = find_known_price_contexts(capture, {984, 1013, 5515})

    assert len(matches) == 1
    assert matches[0]["url"] == "u1"
    assert matches[0]["matchedPrices"] == [1013]
