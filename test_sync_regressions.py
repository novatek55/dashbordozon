import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from src.ozon_client import OzonAPIError, OzonClient, RateLimitError
from src import sync_manager as sync_manager_module
from src.config import settings
from src.sync_manager import SyncManager


def test_full_sync_includes_transactions_and_campaigns():
    async def _run():
        manager = SyncManager(client=None)
        calls = []

        async def _step(name, result=None):
            calls.append(name)
            return result or {"ok": True}

        manager.sync_products = lambda: _step("products")
        manager.sync_analytics_data = lambda days_back=None: _step("analytics_data")
        manager.sync_analytics_stocks = lambda: _step("analytics_stocks")
        manager.sync_fbs_warehouse_stocks = lambda: _step("fbs_warehouse_stocks")
        manager.sync_analytics_turnover = lambda days_back=None: _step("analytics_turnover")
        manager.sync_analytics_average_delivery_time = lambda: _step("average_delivery_time")
        manager.sync_realization_v2 = lambda days_back=None: _step("realization_v2")
        manager.sync_transactions = lambda days_back=None: _step("transactions")
        manager.sync_campaigns = lambda: _step("campaigns")
        manager.sync_returns = lambda: _step("returns")
        manager.sync_returns_fbo = lambda: _step("returns_fbo")
        manager.sync_cash_flow_statements = lambda days_back=None: _step("cash_flow")
        manager.sync_promo = lambda: _step("promo")
        manager.sync_postings_report = lambda: _step("report_postings")
        manager.sync_products_report = lambda: _step("report_products")
        manager.sync_returns_report = lambda: _step("report_returns")
        manager.sync_compensation_reports = lambda: _step("report_compensation")
        manager.sync_warehouse_stock_report = lambda: _step("report_warehouse_stock")

        await manager.full_sync(days_back=30)
        return calls

    calls = asyncio.run(_run())

    assert "transactions" in calls
    assert "campaigns" in calls
    assert calls.index("transactions") < calls.index("cash_flow")
    assert calls.index("report_warehouse_stock") < calls.index("campaigns")


def test_async_report_freshness_allows_next_calendar_day():
    async def _run():
        manager = SyncManager(client=None)
        sync_log = SimpleNamespace(completed_at=None)
        updates = []

        async def _get_last_successful_sync(_entity_type):
            return SimpleNamespace(
                completed_at=datetime(2026, 6, 13, 23, 7, 29, tzinfo=timezone.utc)
            )

        async def _update_sync_log(_sync_log, status, records_processed=0, records_inserted=0, records_updated=0, error_message=None):
            updates.append(
                {
                    "status": status,
                    "error_message": error_message,
                }
            )

        original_hours = settings.async_report_refresh_hours
        original_datetime = sync_manager_module.datetime

        class _FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                current = datetime(2026, 6, 14, 21, 26, 13, tzinfo=timezone.utc)
                if tz is None:
                    return current.replace(tzinfo=None)
                return current.astimezone(tz)

        try:
            settings.async_report_refresh_hours = 24
            sync_manager_module.datetime = _FakeDateTime
            manager._get_last_successful_sync = _get_last_successful_sync
            manager._update_sync_log = _update_sync_log

            result = await manager._skip_recent_async_report_sync_if_fresh(sync_log, "report_products")
        finally:
            settings.async_report_refresh_hours = original_hours
            sync_manager_module.datetime = original_datetime

        assert result is None
        assert updates == []

    asyncio.run(_run())


def test_cpo_orders_report_uses_timestamp_window():
    async def _run():
        client = OzonClient("client", "key")
        captured = {}

        async def _fake_make_request(method, endpoint, data=None, use_performance=False):
            captured.update(
                {
                    "method": method,
                    "endpoint": endpoint,
                    "data": data,
                    "use_performance": use_performance,
                }
            )
            return {"UUID": "report-uuid"}

        client._make_request = _fake_make_request

        uuid = await client.request_cpo_orders_report_json(
            date_from=datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc),
            date_to=datetime(2026, 6, 16, 9, 30, 15, tzinfo=timezone.utc),
        )
        return uuid, captured

    uuid, captured = asyncio.run(_run())

    assert uuid == "report-uuid"
    assert captured["endpoint"] == "/api/client/statistic/orders/generate/json"
    assert captured["use_performance"] is True
    assert captured["data"]["from"] == "2026-05-16T00:00:00Z"
    assert captured["data"]["to"] == "2026-06-16T09:30:15Z"


def test_analytics_product_queries_starts_probe_with_two_day_lag():
    async def _run():
        probed_dates = []

        class _Client:
            async def get_analytics_product_queries(self, date_from, date_to, skus, page, page_size):
                probed_dates.append(date_from.date().isoformat())
                raise sync_manager_module.OzonAPIError(
                    "HTTP Error 400: Bad Request. Response: There is no data for the specified period",
                    status_code=400,
                )

        manager = SyncManager(_Client())
        manager._create_sync_log = lambda _entity_type: asyncio.sleep(0, result=SimpleNamespace())
        manager._ensure_analytics_product_queries_schema = lambda: asyncio.sleep(0)
        manager._load_product_query_sku_reference = lambda: asyncio.sleep(
            0,
            result={123: {"offer_id": "A-123", "product_name": "Product"}},
        )
        manager._update_sync_log = lambda *args, **kwargs: asyncio.sleep(0)

        async def _with_rate_limit_retry(factory, attempts=1, base_delay=0):
            return await factory()

        manager._with_rate_limit_retry = _with_rate_limit_retry

        original_datetime = sync_manager_module.datetime

        class _FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                current = datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc)
                if tz is None:
                    return current.replace(tzinfo=None)
                return current.astimezone(tz)

        try:
            sync_manager_module.datetime = _FakeDateTime
            result = await manager.sync_analytics_product_queries(
                days_back=30,
                max_availability_probe_days=1,
            )
        finally:
            sync_manager_module.datetime = original_datetime

        return result, probed_dates

    result, probed_dates = asyncio.run(_run())

    assert probed_dates == ["2026-06-15"]
    assert result["requested_last_complete_date"] == "2026-06-15"


def test_campaign_active_request_limit_detection():
    manager = SyncManager(client=None)

    errors = [
        RateLimitError(
            'Rate limit exceeded: {"error":"Превышен лимит активных запросов (максимум 1)"}',
            status_code=429,
        ),
        OzonAPIError(
            "HTTP Error 429: Too Many Requests",
            status_code=429,
            response_data={"text": '{"error":"active requests limit exceeded (maximum 1)"}'},
        ),
    ]

    for error in errors:
        assert manager._is_active_campaign_request_limit_error(error)

    assert not manager._is_active_campaign_request_limit_error(
        OzonAPIError("HTTP Error 429: regular per-second limit", status_code=429)
    )
