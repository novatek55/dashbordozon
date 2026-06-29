from src.dashboard.routes.system import build_finance_sync_steps, get_ozon_sync_report_specs


def _mode_from_cmd(cmd: list[str]) -> str | None:
    if "--mode" not in cmd:
        return None
    idx = cmd.index("--mode")
    return cmd[idx + 1] if idx + 1 < len(cmd) else None


def test_all_dashboard_sync_modes_have_report_freshness_specs() -> None:
    spec_modes = {spec.mode for spec in get_ozon_sync_report_specs()}
    step_modes = {
        mode
        for cmd, _stage, _continue_on_error in build_finance_sync_steps(days_back=30)
        for mode in [_mode_from_cmd(cmd)]
        if mode and mode != "normalize_finance"
    }

    assert step_modes <= spec_modes


def test_product_queries_freshness_uses_business_period() -> None:
    specs = {spec.mode: spec for spec in get_ozon_sync_report_specs()}

    spec = specs["analytics_product_queries"]

    assert spec.tables[0].table == "analytics_product_query_details"
    assert spec.tables[0].business_date_column == "period_start"
    assert spec.max_lag_days == 2


def test_monthly_reports_are_marked_as_monthly() -> None:
    specs = {spec.mode: spec for spec in get_ozon_sync_report_specs()}

    assert specs["realization_v2"].periodicity == "monthly"
    assert specs["report_compensation"].periodicity == "monthly"
    assert specs["analytics_product_queries"].periodicity == "daily"
