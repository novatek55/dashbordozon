from src.wb_advertising_sync import _extract_fullstats_daily_metrics, _extract_fullstats_nm_metrics


def test_extract_fullstats_daily_metrics_from_days():
    rows = [
        {
            "advertId": 123,
            "days": [
                {
                    "date": "2026-06-01T00:00:00+03:00",
                    "views": 100,
                    "clicks": 12,
                    "atbs": 4,
                    "orders": 3,
                    "shks": 5,
                    "canceled": 1,
                    "sum_price": 1500,
                    "sum": 90,
                },
                {
                    "date": "2026-06-02",
                    "views": "50",
                    "clicks": "5",
                    "atbs": "2",
                    "orders": "1",
                    "sum_price": "700.5",
                },
            ],
        }
    ]

    metrics = _extract_fullstats_daily_metrics(rows)

    assert metrics[(123, "2026-06-01")] == {
        "views": 100,
        "clicks": 12,
        "carts": 4,
        "orders": 3,
        "shks": 5,
        "canceled": 1,
        "revenue": 1500.0,
        "stats_spend": 90.0,
        "avg_position": 0.0,
        "raw": [rows[0]["days"][0]],
    }
    assert metrics[(123, "2026-06-02")]["views"] == 50
    assert metrics[(123, "2026-06-02")]["revenue"] == 700.5


def test_extract_fullstats_daily_metrics_aggregates_duplicate_days():
    rows = [
        {
            "advert_id": 777,
            "days": [
                {"date": "2026-06-01", "views": 10, "clicks": 1, "atbs": 1, "orders": 0, "sum_price": 0},
                {"date": "2026-06-01", "views": 15, "clicks": 2, "atbs": 0, "orders": 1, "sum_price": 500},
            ],
        }
    ]

    metrics = _extract_fullstats_daily_metrics(rows)

    assert metrics[(777, "2026-06-01")]["views"] == 25
    assert metrics[(777, "2026-06-01")]["clicks"] == 3
    assert metrics[(777, "2026-06-01")]["orders"] == 1
    assert metrics[(777, "2026-06-01")]["revenue"] == 500.0


def test_extract_fullstats_daily_metrics_averages_booster_position():
    rows = [
        {
            "advertId": 321,
            "boosterStats": [
                {"date": "2026-06-01", "nm": 111, "avg_position": 20},
                {"date": "2026-06-01", "nm": 222, "avg_position": 30},
            ],
            "days": [{"date": "2026-06-01", "views": 10}],
        }
    ]

    metrics = _extract_fullstats_daily_metrics(rows)

    assert metrics[(321, "2026-06-01")]["avg_position"] == 25.0


def test_extract_fullstats_nm_metrics_from_apps_nms():
    rows = [
        {
            "advertId": 123,
            "days": [
                {
                    "date": "2026-06-01T00:00:00Z",
                    "apps": [
                        {
                            "appType": 32,
                            "nms": [
                                {
                                    "nmId": 555,
                                    "name": "Table leg",
                                    "views": 100,
                                    "clicks": 10,
                                    "atbs": 2,
                                    "orders": 1,
                                    "shks": 1,
                                    "canceled": 0,
                                    "sum": 33.3,
                                    "sum_price": 1000,
                                }
                            ],
                        },
                        {
                            "appType": 64,
                            "nms": [
                                {
                                    "nmId": 555,
                                    "name": "Table leg",
                                    "views": 50,
                                    "clicks": 5,
                                    "atbs": 1,
                                    "orders": 0,
                                    "shks": 0,
                                    "canceled": 1,
                                    "sum": 10,
                                    "sum_price": 0,
                                }
                            ],
                        },
                    ],
                }
            ],
        }
    ]

    metrics = _extract_fullstats_nm_metrics(rows)
    row = metrics[(123, "2026-06-01", 555)]

    assert row["name"] == "Table leg"
    assert row["views"] == 150
    assert row["clicks"] == 15
    assert row["carts"] == 3
    assert row["orders"] == 1
    assert row["shks"] == 1
    assert row["canceled"] == 1
    assert row["stats_spend"] == 43.3
    assert row["revenue"] == 1000.0
