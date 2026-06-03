"""Client for Wildberries Finance API."""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class WBAPIError(Exception):
    """Wildberries API error."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Any] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, WBAPIError) and exc.status_code in {429, 500, 502, 503, 504}:
        return True
    return False


class WBFinanceClient:
    """Minimal WB finance API client for raw sales report ingestion."""

    BASE_URL = "https://finance-api.wildberries.ru"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=120, connect=20)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(_is_retryable),
    )
    async def get_sales_report_detailed(
        self,
        date_from: str,
        date_to: str,
        rrd_id: int = 0,
        limit: int = 100000,
    ) -> List[Dict[str, Any]]:
        if not self.session:
            raise RuntimeError("WB client is not initialized")

        url = f"{self.BASE_URL}/api/finance/v1/sales-reports/detailed"
        payload = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "rrdId": rrd_id,
            "limit": limit,
        }
        async with self.session.post(url, headers=self.headers, json=payload) as response:
            if response.status == 204:
                return []
            if response.status >= 400:
                body = await response.text()
                raise WBAPIError(
                    f"WB request failed: {response.status}",
                    status_code=response.status,
                    response_data=body,
                )

            data = await response.json(content_type=None)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # Defensive parsing for possible envelope responses.
                for key in ("data", "rows", "result"):
                    maybe_rows = data.get(key)
                    if isinstance(maybe_rows, list):
                        return maybe_rows
            raise WBAPIError("Unexpected WB response format", response_data=data)
