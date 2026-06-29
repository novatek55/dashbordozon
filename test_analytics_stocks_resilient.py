import asyncio

from tenacity import RetryError

from src.ozon_client import OzonAPIError
from src.sync_manager import SyncManager


class _Attempt:
    def __init__(self, exc):
        self._exc = exc

    def exception(self):
        return self._exc


class _ClientAlwaysRetryError:
    async def get_analytics_stocks(self, skus):
        if len(skus) == 1:
            return {"items": [{"sku": skus[0]}]}
        raise RetryError(_Attempt(OzonAPIError("rate limited", status_code=429)))


def test_fetch_analytics_stocks_chunk_resilient_splits_retryerror():
    async def _run():
        manager = SyncManager(_ClientAlwaysRetryError())
        return await manager._fetch_analytics_stocks_chunk_resilient([11, 22, 33, 44])

    items, skipped = asyncio.run(_run())

    assert sorted(item["sku"] for item in items) == [11, 22, 33, 44]
    assert skipped == []
