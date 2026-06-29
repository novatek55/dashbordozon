from src.dashboard.routes.serp import _apply_serp_enrichment


def test_apply_serp_enrichment_uses_fallback_when_plugin_has_no_match():
    positions = [{"sku": 111, "title": "Item A"}]

    enriched_count = _apply_serp_enrichment(
        positions,
        enrichment_map={},
        fallback_map={
            "111": {
                "revenue_30d": 12345,
                "sales_per_day": 4.5,
                "bestsellers_data": {
                    "session_count": 321,
                    "views": 654,
                },
            }
        },
    )

    assert enriched_count == 1
    assert positions[0]["revenue_30d"] == 12345
    assert positions[0]["sales_per_day"] == 4.5
    assert positions[0]["bestsellers_data"]["session_count"] == 321
