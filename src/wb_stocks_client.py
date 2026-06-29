"""Client for Wildberries stocks API."""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from src.wb_finance_client import WBAPIError

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, WBAPIError) and exc.status_code in {429, 500, 502, 503, 504}:
        return True
    return False


class WBStocksClient:
    """Minimal WB statistics API client for stock balances."""

    BASE_URL = "https://statistics-api.wildberries.ru"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=180, connect=20)
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
    async def get_supplier_stocks(self, date_from: str) -> List[Dict[str, Any]]:
        if not self.session:
            raise RuntimeError("WB stocks client is not initialized")

        url = f"{self.BASE_URL}/api/v1/supplier/stocks"
        async with self.session.get(url, headers=self.headers, params={"dateFrom": date_from}) as response:
            body_text = await response.text()
            if response.status >= 400:
                raise WBAPIError(
                    f"WB stocks request failed: {response.status}",
                    status_code=response.status,
                    response_data=body_text,
                )
            if not body_text.strip():
                return []
            data = await response.json(content_type=None)
            if isinstance(data, list):
                return data
            raise WBAPIError("Unexpected WB stocks response format", response_data=data)
