from src.dashboard.routes.system import build_finance_sync_steps, build_wb_finance_sync_steps


def test_analytics_product_queries_sync_step_is_critical() -> None:
    steps = build_finance_sync_steps(days_back=30)

    matching = [
        step for step in steps
        if "--mode" in step[0]
        and step[0][step[0].index("--mode") + 1] == "analytics_product_queries"
    ]

    assert matching, "analytics_product_queries step must be present"
    assert matching[0][2] is False


def test_wb_finance_sync_steps_load_normalize_and_rebuild_daily() -> None:
    steps = build_wb_finance_sync_steps(days_back=30)

    modes = [
        step[0][step[0].index("--mode") + 1]
        for step in steps
        if "--mode" in step[0]
    ]

    assert modes == ["wb_finance_raw", "wb_finance_normalize", "wb_finance_daily"]
    assert steps[0][0][-2:] == ["--days-back", "30"]
    assert all(step[2] is False for step in steps)
