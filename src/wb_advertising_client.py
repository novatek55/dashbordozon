"""Client for Wildberries Promotion API."""
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


class WBAdvertisingClient:
    """Minimal WB Promotion API client for advertising spend ingestion."""

    BASE_URL = "https://advert-api.wildberries.ru"

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

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if not self.session:
            raise RuntimeError("WB advertising client is not initialized")
        url = f"{self.BASE_URL}{path}"
        async with self.session.request(method, url, headers=self.headers, **kwargs) as response:
            if response.status == 204:
                return None
            body_text = await response.text()
            if response.status >= 400:
                raise WBAPIError(
                    f"WB advertising request failed: {response.status}",
                    status_code=response.status,
                    response_data=body_text,
                )
            if not body_text.strip():
                return None
            try:
                return await response.json(content_type=None)
            except Exception as exc:
                raise WBAPIError("Unexpected WB advertising response format", response_data=body_text) from exc

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(_is_retryable),
    )
    async def get_campaign_count(self) -> Dict[str, Any]:
        data = await self._request("GET", "/adv/v1/promotion/count")
        return data if isinstance(data, dict) else {}

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(_is_retryable),
    )
    async def get_campaign_details(self, campaign_ids: List[int]) -> List[Dict[str, Any]]:
        if not campaign_ids:
            return []
        params = [("id", str(int(campaign_id))) for campaign_id in campaign_ids[:50]]
        data = await self._request("GET", "/api/advert/v2/adverts", params=params)
        return data if isinstance(data, list) else []

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(_is_retryable),
    )
    async def get_fullstats(self, campaign_ids: List[int], date_from: str, date_to: str) -> List[Dict[str, Any]]:
        if not campaign_ids:
            return []
        params = {
            "ids": ",".join(str(int(campaign_id)) for campaign_id in campaign_ids[:100]),
            "beginDate": date_from,
            "endDate": date_to,
        }
        data = await self._request("GET", "/adv/v3/fullstats", params=params)
        return data if isinstance(data, list) else []

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(_is_retryable),
    )
    async def get_expense_history(self, date_from: str, date_to: str) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/adv/v1/upd", params={"from": date_from, "to": date_to})
        return data if isinstance(data, list) else []
