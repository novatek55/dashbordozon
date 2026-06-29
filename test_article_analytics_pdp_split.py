from src.dashboard.routes.analytics import choose_position_category, split_pdp_visitors_by_source


def test_split_pdp_visitors_uses_campaign_clicks_when_available():
    ad, seo = split_pdp_visitors_by_source(
        pdp_visitors=100,
        campaign_clicks=35,
        ad_impressions=80,
        total_impressions=200,
    )

    assert ad == 35
    assert seo == 65


def test_split_pdp_visitors_falls_back_to_ad_impression_share():
    ad, seo = split_pdp_visitors_by_source(
        pdp_visitors=100,
        campaign_clicks=0,
        ad_impressions=80,
        total_impressions=200,
    )

    assert ad == 40
    assert seo == 60


def test_split_pdp_visitors_clamps_ad_clicks_to_total():
    ad, seo = split_pdp_visitors_by_source(
        pdp_visitors=100,
        campaign_clicks=130,
        ad_impressions=80,
        total_impressions=200,
    )

    assert ad == 100
    assert seo == 0


def test_choose_position_category_prefers_analytics_position():
    assert choose_position_category(12.5, 30.0) == 12.5


def test_choose_position_category_falls_back_to_query_position():
    assert choose_position_category(0, 30.0) == 30.0
    assert choose_position_category(None, 18.25) == 18.25
