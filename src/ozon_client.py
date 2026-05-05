"""Klijent dlja raboty s Ozon API."""
import aiohttp
import asyncio
from typing import Optional, Dict, Any, List, AsyncGenerator
from datetime import datetime, timedelta, timezone
import logging
import os
import tempfile
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
import json

logger = logging.getLogger(__name__)


class OzonAPIError(Exception):
    """Oshibka API Ozon."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class RateLimitError(OzonAPIError):
    """Prevyashen limit zaprosov."""
    pass


def _is_retryable_request_error(exc: BaseException) -> bool:
    """Retry transport errors and transient 5xx responses from Ozon."""
    if isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, OzonAPIError) and exc.status_code in {429, 500, 502, 503, 504}:
        return True
    return False


class OzonClient:
    """Klijent dlja Ozon Seller API."""
    
    BASE_URL = "https://api-seller.ozon.ru"
    PERFORMANCE_URL = "https://api-performance.ozon.ru"
    
    def __init__(
        self, 
        client_id: str, 
        api_key: str,
        performance_client_id: Optional[str] = None,
        performance_client_secret: Optional[str] = None,
        max_concurrent_requests: int = 5
    ):
        self.client_id = client_id
        self.api_key = api_key
        self.performance_client_id = performance_client_id
        self.performance_client_secret = performance_client_secret
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.performance_token: Optional[str] = None
        self.performance_token_expires: Optional[datetime] = None
        
        # Semafhor dlja ogranichenija parallel'nyh zaprosov
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        # Performance API often allows only one active request at a time.
        self.performance_semaphore = asyncio.Semaphore(1)
        self._performance_token_lock = asyncio.Lock()
        self._last_performance_request_ts: float = 0.0
        self._performance_min_interval_seconds: float = 2.5

        # Hard-throttle dlja /v1/analytics/data: dokumentirovannyj limit Ozon — 1 RPS,
        # no na praktike (29.04.2026) Ozon vozvrashhal 429 dazhe pri 0.25 RPS — pohozhe
        # na burst-quota / akkaunt-uroven', kotoryj ne otpuskaet posle predydushhih
        # zavisov. Posle ispytanij na 1.2s (ne pomoglo) stavim 6.0s s zapasom.
        # Tenacity backoff dlja 429 takzhe podnjat (sm. _is_retryable_request_error /
        # @retry decorator na _make_request).
        self._analytics_data_lock = asyncio.Lock()
        self._last_analytics_data_request_ts: float = 0.0
        self._analytics_data_min_interval_seconds: float = 6.0
        
        # Zagolovki dlja Seller API
        self.headers = {
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json"
        }
    
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def initialize(self):
        """Inicializacija sessii."""
        timeout = aiohttp.ClientTimeout(total=60, connect=10)
        self.session = aiohttp.ClientSession(timeout=timeout)
        
        # Poluchaem token dlja Performance API esli est' credentials
        if self.performance_client_id and self.performance_client_secret:
            try:
                await self._get_performance_token()
            except Exception as e:
                logger.warning(f"Performance API not available: {e}")
                logger.warning("Continuing without Performance API (campaigns sync will be skipped)")
                self.performance_client_id = None
                self.performance_client_secret = None
        
        logger.info("Ozon client initialized")
    
    async def close(self):
        """Zakrytie sessii."""
        if self.session:
            await self.session.close()
            logger.info("Ozon client closed")
    
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=3, max=30),
        retry=retry_if_exception(_is_retryable_request_error)
    )
    async def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict] = None,
        use_performance: bool = False,
        is_performance_auth: bool = False
    ) -> Dict[str, Any]:
        """Vypolnenie HTTP zaprosa."""
        if not self.session:
            raise OzonAPIError("Client not initialized. Use 'async with' or call initialize()")

        # Refresh performance token OUTSIDE semaphores to avoid deadlock:
        # _get_performance_token -> _make_request -> semaphore (already held)
        if use_performance and not is_performance_auth:
            if not self.performance_token or datetime.now() >= self.performance_token_expires:
                logger.debug("Performance token needs refresh for %s", endpoint)
                async with self._performance_token_lock:
                    # Double-check after acquiring the lock
                    if not self.performance_token or datetime.now() >= self.performance_token_expires:
                        await self._get_performance_token()

        logger.debug("Acquiring semaphore for %s (performance=%s)", endpoint, use_performance)
        async with self.semaphore:
            logger.debug("Semaphore acquired for %s", endpoint)
            # Throttle /v1/analytics/data — Ozon ogranichivaet endpoint na 1 RPS.
            # Lock + sleep garantirujut interval mezhdu uspeshnymi vyzovami nezavisimo
            # ot kolichestva metrik/stranic v sync_analytics_data.
            if endpoint == "/v1/analytics/data":
                async with self._analytics_data_lock:
                    loop = asyncio.get_running_loop()
                    wait_seconds = self._analytics_data_min_interval_seconds - (
                        loop.time() - self._last_analytics_data_request_ts
                    )
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)
                    self._last_analytics_data_request_ts = loop.time()
            if use_performance:

                async with self.performance_semaphore:
                    loop = asyncio.get_running_loop()
                    now_ts = loop.time()
                    wait_seconds = self._performance_min_interval_seconds - (now_ts - self._last_performance_request_ts)
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)
                    self._last_performance_request_ts = loop.time()

                    url = f"{self.PERFORMANCE_URL}{endpoint}"
                    if is_performance_auth:
                        headers = {"Content-Type": "application/json"}
                    else:
                        headers = {"Authorization": f"Bearer {self.performance_token}"}
                    return await self._perform_http_request(method, url, headers, data, endpoint)
            else:
                url = f"{self.BASE_URL}{endpoint}"
                headers = self.headers
                return await self._perform_http_request(method, url, headers, data, endpoint)

    async def _perform_http_request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Dict],
        endpoint: str,
    ) -> Dict[str, Any]:
        """Nizkourovnevyj HTTP-vyzov s edinym razborom otveta."""
        try:
            async with self.session.request(
                method=method,
                url=url,
                headers=headers,
                json=data
            ) as response:
                response_text = await response.text()

                if response.status == 429:
                    raise RateLimitError(
                        f"Rate limit exceeded: {response_text[:500]}".strip(),
                        status_code=429,
                    )
                
                if response.status == 204:
                    return {}

                if response.status >= 400:
                    message = response.reason or "HTTP error"
                    if response_text.strip():
                        message = f"{message}. Response: {response_text[:1000]}"
                    logger.error(f"HTTP Error {response.status} for {endpoint}: {message}")
                    raise OzonAPIError(
                        f"HTTP Error {response.status}: {message}",
                        status_code=response.status,
                        response_data={"text": response_text[:4000]},
                    )

                if not response_text.strip():
                    return {}

                try:
                    return json.loads(response_text)
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON response for {endpoint}: {response_text[:1000]}")
                    raise OzonAPIError(
                        f"Invalid JSON response from {endpoint}",
                        status_code=response.status,
                        response_data={"text": response_text[:4000]},
                    )
        except Exception as e:
            logger.error(f"Request error: {e}")
            raise
    
    async def _get_performance_token(self):
        """Poluchenie tokena dlja Performance API."""
        if not self.performance_client_id or not self.performance_client_secret:
            raise OzonAPIError("Performance API credentials not provided")
        
        auth_data = {
            "client_id": self.performance_client_id,
            "client_secret": self.performance_client_secret,
            "grant_type": "client_credentials"
        }
        
        try:
            result = await self._make_request(
                "POST", 
                "/api/client/token",
                auth_data,
                use_performance=True,
                is_performance_auth=True
            )
            
            self.performance_token = result.get("access_token")
            expires_in = result.get("expires_in", 3600)
            self.performance_token_expires = datetime.now() + timedelta(seconds=expires_in - 60)
            
            logger.info("Performance API token obtained")
        except Exception as e:
            logger.error(f"Failed to get Performance API token: {e}")
            raise
    
    # ==================== PRODUCT API ====================
    
    async def get_product_list(
        self, 
        limit: int = 1000, 
        last_id: Optional[str] = None,
        offer_id: Optional[str] = None,
        sku: Optional[int] = None
    ) -> Dict[str, Any]:
        """Poluchenie spiska tovarov."""
        data = {
            "filter": {},
            "limit": limit,
            "last_id": last_id or ""
        }
        if offer_id:
            data["filter"]["offer_id"] = [offer_id]
        if sku:
            data["filter"]["sku"] = [sku]
        
        return await self._make_request("POST", "/v3/product/list", data)
    
    async def get_all_products(self) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh tovarov s paginaciej."""
        last_id = ""
        while True:
            result = await self.get_product_list(limit=1000, last_id=last_id)
            # API v3 vozvrashhaet items vnutri result
            result_data = result.get("result", {})
            items = result_data.get("items", [])
            
            if not items:
                break
            
            yield items
            
            last_id = result_data.get("last_id", "")
            if not last_id:
                break
    
    async def get_product_info(
        self, 
        offer_id: Optional[str] = None,
        sku: Optional[int] = None,
        product_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Poluchenie informacii o tovare."""
        data = {}
        if offer_id:
            data["offer_id"] = offer_id
        if sku:
            data["sku"] = sku
        if product_id:
            data["product_id"] = product_id
        
        return await self._make_request("POST", "/v2/product/info", data)
    
    async def get_product_info_list(self, sku_list: List[int]) -> Dict[str, Any]:
        """Poluchenie informacii o neskol'kih tovarah."""
        data = {"sku": sku_list}
        return await self._make_request("POST", "/v1/product/info/list", data)

    async def get_product_info_list_v3(self, sku_list: List[int]) -> Dict[str, Any]:
        """Poluchenie rasshirennoj informacii o neskol'kih tovarah (v3)."""
        data = {"sku": sku_list}
        return await self._make_request("POST", "/v3/product/info/list", data)

    async def get_product_info_attributes(
        self,
        sku_list: List[int],
        limit: int = 1000,
        last_id: str = "",
    ) -> Dict[str, Any]:
        """Poluchenie atributov tovarov (v4/product/info/attributes)."""
        data = {
            "filter": {"sku": sku_list},
            "limit": limit,
            "last_id": last_id or "",
        }
        return await self._make_request("POST", "/v4/product/info/attributes", data)
    
    async def get_product_prices(self, limit: int = 1000, last_id: Optional[str] = None) -> Dict[str, Any]:
        """Poluchenie cen tovarov."""
        data = {"limit": limit, "last_id": last_id or ""}
        return await self._make_request("POST", "/v4/product/info/prices", data)
    
    async def get_all_product_prices(self) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh cen s paginaciej."""
        last_id = ""
        while True:
            result = await self.get_product_prices(limit=1000, last_id=last_id)
            items = result.get("items", [])
            
            if not items:
                break
            
            yield items
            
            last_id = result.get("last_id", "")
            if not last_id:
                break
    
    async def get_product_stocks(self, limit: int = 1000, last_id: Optional[str] = None) -> Dict[str, Any]:
        """Poluchenie ostatkov tovarov."""
        data = {"limit": limit, "last_id": last_id or ""}
        return await self._make_request("POST", "/v4/product/info/stocks", data)

    async def get_fbs_stocks_by_warehouse(
        self,
        sku: Optional[List[str]] = None,
        offer_id: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Poluchenie FBS-ostatkov po skladam prodavca."""
        data = {
            "sku": sku or [],
            "offer_id": offer_id or [],
        }
        if limit is not None:
            data["limit"] = int(limit)
        if not data["sku"] and not data["offer_id"]:
            raise OzonAPIError("Either sku or offer_id must not be empty")
        return await self._make_request("POST", "/v2/product/info/stocks-by-warehouse/fbs", data)

    async def get_warehouse_stocks(self, warehouse_id: int, cursor: str = "") -> Dict[str, Any]:
        """Get stocks for a specific warehouse via /v1/product/info/warehouse/stocks."""
        data: Dict[str, Any] = {"warehouse_id": warehouse_id, "limit": 1000}
        if cursor:
            data["cursor"] = cursor
        return await self._make_request("POST", "/v1/product/info/warehouse/stocks", data)

    async def get_all_warehouse_stocks(self, warehouse_id: int) -> List[Dict[str, Any]]:
        """Get all stocks for a warehouse with pagination."""
        all_items: List[Dict[str, Any]] = []
        cursor = ""
        while True:
            result = await self.get_warehouse_stocks(warehouse_id, cursor)
            items = result.get("stocks", [])
            if not items:
                break
            all_items.extend(items)
            if not result.get("has_next"):
                break
            cursor = result.get("cursor", "")
            if not cursor:
                break
        return all_items

    async def get_all_product_stocks(self) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh ostatkov s paginaciej."""
        last_id = ""
        while True:
            result = await self.get_product_stocks(limit=1000, last_id=last_id)
            items = result.get("items", [])
            
            if not items:
                break
            
            yield items
            
            last_id = result.get("last_id", "")
            if not last_id:
                break
    
    # ==================== POSTINGS API (FBS) ====================
    
    async def get_postings_list(
        self,
        since: datetime,
        to: datetime,
        status: Optional[str] = None,
        delivery_schema: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Poluchenie spiska otpravlenij FBS."""
        data = {
            "filter": {
                "since": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "to": to.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            },
            "limit": limit,
            "offset": offset
        }
        
        if status:
            data["filter"]["status"] = status
        if delivery_schema:
            data["filter"]["delivery_schema"] = [delivery_schema]
        
        return await self._make_request("POST", "/v3/posting/fbs/list", data)
    
    async def get_all_postings(
        self,
        since: datetime,
        to: datetime,
        status: Optional[str] = None
    ) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh otpravlenij s paginaciej."""
        offset = 0
        while True:
            result = await self.get_postings_list(
                since=since,
                to=to,
                status=status,
                limit=1000,
                offset=offset
            )
            postings = result.get("result", [])
            
            if not postings:
                break
            
            yield postings
            
            if len(postings) < 1000:
                break
            
            offset += 1000
    
    async def get_posting_details(self, posting_number: str) -> Dict[str, Any]:
        """Poluchenie detalej otpravlenija."""
        data = {"posting_number": posting_number}
        return await self._make_request("POST", "/v3/posting/fbs/get", data)
    
    # ==================== POSTINGS API (FBO) ====================
    
    async def get_postings_fbo_list(
        self,
        since: datetime,
        to: datetime,
        status: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Poluchenie spiska otpravlenij FBO."""
        data = {
            "filter": {
                "since": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "to": to.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            },
            "limit": limit,
            "offset": offset
        }
        
        if status:
            data["filter"]["status"] = status
        
        return await self._make_request("POST", "/v2/posting/fbo/list", data)
    
    async def get_all_postings_fbo(
        self,
        since: datetime,
        to: datetime,
        status: Optional[str] = None
    ) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh otpravlenij FBO s paginaciej."""
        offset = 0
        while True:
            result = await self.get_postings_fbo_list(
                since=since,
                to=to,
                status=status,
                limit=1000,
                offset=offset
            )
            postings = result.get("postings", [])
            
            if not postings:
                break
            
            yield postings
            
            if len(postings) < 1000:
                break
            
            offset += 1000
    
    # ==================== FINANCE API ====================
    
    async def get_transaction_list(
        self,
        from_date: datetime,
        to_date: datetime,
        operation_types: Optional[List[str]] = None,
        posting_number: str = "",
        transaction_type: str = "all",
        page: int = 1,
        page_size: int = 1000
    ) -> Dict[str, Any]:
        """Poluchenie spiska transakcij."""
        data = {
            "filter": {
                "date": {
                    "from": from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "to": to_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                },
                "operation_type": operation_types or [],
                "posting_number": posting_number,
                "transaction_type": transaction_type,
            },
            "page": page,
            "page_size": page_size
        }

        return await self._make_request("POST", "/v3/finance/transaction/list", data)
    
    async def get_all_transactions(
        self,
        from_date: datetime,
        to_date: datetime,
        operation_types: Optional[List[str]] = None,
        posting_number: str = "",
        transaction_type: str = "all",
        page_size: int = 1000,
    ) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh transakcij s paginaciej."""
        page = 1
        while True:
            result = await self.get_transaction_list(
                from_date=from_date,
                to_date=to_date,
                operation_types=operation_types,
                posting_number=posting_number,
                transaction_type=transaction_type,
                page=page,
                page_size=page_size
            )
            result_block = result.get("result", {}) if isinstance(result, dict) else {}
            operations = result_block.get("operations", [])
            
            if not operations:
                break
            
            yield operations
            
            page_count = int(result_block.get("page_count") or 0)
            if page_count and page >= page_count:
                break
            if len(operations) < page_size:
                break
            
            page += 1
    
    async def get_transaction_totals(
        self,
        from_date: datetime,
        to_date: datetime
    ) -> Dict[str, Any]:
        """Poluchenie itogov po transakcijam."""
        data = {
            "filter": {
                "date": {
                    "from": from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "to": to_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                }
            }
        }
        return await self._make_request("POST", "/v3/finance/transaction/totals", data)
    
    async def get_realization_report(self, year: int, month: int) -> Dict[str, Any]:
        """Poluchenie otcheta o realizacii za mesjac."""
        data = {"year": int(year), "month": int(month)}
        return await self._make_request("POST", "/v2/finance/realization", data)

    async def get_analytics_average_delivery_time(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Poluchenie analitiki srednego vremeni dostavki."""
        return await self._make_request("POST", "/v1/analytics/average-delivery-time", payload or {})

    async def get_analytics_average_delivery_time_details(
        self,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Poluchenie detalizacii po srednemu vremeni dostavki."""
        return await self._make_request(
            "POST",
            "/v1/analytics/average-delivery-time/details",
            payload or {},
        )

    async def get_analytics_average_delivery_time_summary(
        self,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Poluchenie svodki po srednemu vremeni dostavki."""
        return await self._make_request(
            "POST",
            "/v1/analytics/average-delivery-time/summary",
            payload or {},
        )
    
    async def get_cash_flow_statement(
        self,
        from_date: datetime,
        to_date: datetime,
        page: int = 1,
        page_size: int = 1000,
    ) -> Dict[str, Any]:
        """Poluchenie finansovogo otcheta (Cash Flow)."""
        data = {
            "date": {
                "from": from_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": to_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            },
            "page": page,
            "page_size": page_size,
        }
        return await self._make_request("POST", "/v1/finance/cash-flow-statement/list", data)

    async def get_all_cash_flow_statements(
        self,
        from_date: datetime,
        to_date: datetime,
        page_size: int = 1000,
    ) -> AsyncGenerator[List[Dict[str, Any]], None]:
        """Poluchenie vsego cash-flow s paginaciej."""
        page = 1
        while True:
            result = await self.get_cash_flow_statement(
                from_date=from_date,
                to_date=to_date,
                page=page,
                page_size=page_size,
            )
            result_data = result.get("result", {}) if isinstance(result, dict) else {}
            items = result_data.get("cash_flows", [])
            if not items:
                break
            yield items
            page_count = int(result_data.get("page_count") or 0)
            if page_count and page >= page_count:
                break
            if not page_count and len(items) < page_size:
                break
            page += 1
    
    async def get_mutual_settlement(
        self,
        date: datetime
    ) -> Dict[str, Any]:
        """Poluchenie otcheta o vzaimoraschetah."""
        data = {"date": date.strftime("%Y-%m-%d")}
        return await self._make_request("POST", "/v1/finance/mutual-settlement", data)
    
    async def get_b2b_sales(
        self,
        from_date: datetime,
        to_date: datetime
    ) -> Dict[str, Any]:
        """Poluchenie reestra prodazh jurlicam."""
        data = {
            "date": {
                "from": from_date.strftime("%Y-%m-%d"),
                "to": to_date.strftime("%Y-%m-%d")
            }
        }
        return await self._make_request("POST", "/v1/finance/document-b2b-sales", data)

    async def get_finance_balance(
        self,
        from_date: datetime,
        to_date: datetime,
    ) -> Dict[str, Any]:
        """Poluchenie balansa prodavca za period."""
        data = {
            "date_from": from_date.strftime("%Y-%m-%d"),
            "date_to": to_date.strftime("%Y-%m-%d"),
        }
        return await self._make_request("POST", "/v1/finance/balance", data)
    
    # ==================== RETURNS API ====================
    
    async def get_returns_list(
        self,
        limit: int = 100,
        last_id: Optional[int] = None,
        status: Optional[str] = None,
        filter_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Poluchenie spiska vozvratov.

        Aktual'nyj endpoint: /v1/returns/list.
        """
        if limit > 500:
            limit = 500
        if limit < 1:
            limit = 1

        data: Dict[str, Any] = {"limit": limit}
        if last_id is not None:
            data["last_id"] = last_id
        if filter_payload:
            self._validate_returns_filter(filter_payload)
            data["filter"] = filter_payload
        elif status:
            # Backward compatibility for old callers.
            data["filter"] = {"visual_status_name": status}

        return await self._make_request("POST", "/v1/returns/list", data)

    def _validate_returns_filter(self, filter_payload: Dict[str, Any]) -> None:
        """Proverka ogranichenija API: tol'ko odin date-filter za raz."""
        date_filters = [
            "logistic_return_date",
            "storage_tariffication_start_date",
            "visual_status_change_moment",
        ]
        set_count = 0
        for key in date_filters:
            value = filter_payload.get(key)
            if isinstance(value, dict) and (value.get("time_from") or value.get("time_to")):
                set_count += 1
        if set_count > 1:
            raise OzonAPIError(
                "Returns filter must contain only one of "
                "logistic_return_date/storage_tariffication_start_date/visual_status_change_moment"
            )
    
    async def get_all_returns(
        self,
        status: Optional[str] = None,
        filter_payload: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh vozvratov s paginaciej.
        
        Limit ne mozhet byt' bolee 500 (ogranichenie API).
        """
        last_id: Optional[int] = None
        while True:
            result = await self.get_returns_list(
                limit=limit,
                last_id=last_id,
                status=status,
                filter_payload=filter_payload,
            )
            returns = result.get("returns", [])
            has_next = result.get("has_next", False)
            
            if not returns:
                break
            
            yield returns
            
            if not has_next:
                break

            last_id = returns[-1].get("id")
            if last_id is None:
                break
    
    async def get_returns_fbo_list(
        self,
        limit: int = 100,
        last_id: Optional[int] = None,
        status: Optional[str] = None,
        filter_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Sovmestimost' dlja FBO vozvratov cherez aktual'nyj endpoint."""
        payload = dict(filter_payload) if filter_payload else {}
        payload["return_schema"] = "FBO"
        return await self.get_returns_list(
            limit=limit,
            last_id=last_id,
            status=status,
            filter_payload=payload,
        )
    
    async def get_all_returns_fbo(
        self,
        status: Optional[str] = None,
        filter_payload: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh vozvratov FBO."""
        last_id: Optional[int] = None
        while True:
            result = await self.get_returns_fbo_list(
                limit=limit,
                last_id=last_id,
                status=status,
                filter_payload=filter_payload,
            )
            returns = [r for r in result.get("returns", []) if (r.get("schema") or "").lower() == "fbo"]
            has_next = result.get("has_next", False)
            
            if not returns:
                if not has_next:
                    break
                # Id for next page should be from original page, even if filtered page is empty.
                raw_returns = result.get("returns", [])
                if not raw_returns:
                    break
                last_id = raw_returns[-1].get("id")
                if last_id is None:
                    break
                continue
            
            yield returns
            
            if not has_next:
                break

            raw_returns = result.get("returns", [])
            last_id = raw_returns[-1].get("id") if raw_returns else None
            if last_id is None:
                break
    
    async def get_returns_rfbs_list(
        self,
        limit: int = 1000,
        offset: int = 0,
        status: Optional[str] = None
    ) -> Dict[str, Any]:
        """Poluchenie spiska vozvratov rFBS."""
        data = {"limit": limit, "offset": offset}
        if status:
            data["status"] = status
        
        return await self._make_request("POST", "/v2/returns/rfbs/list", data)
    
    async def get_return_rfbs_details(self, return_id: int) -> Dict[str, Any]:
        """Poluchenie detalej vozvrata rFBS."""
        data = {"return_id": return_id}
        return await self._make_request("POST", "/v2/returns/rfbs/get", data)
    
    # ==================== ANALYTICS API ====================
    
    async def get_analytics_data(
        self,
        date_from: datetime,
        date_to: datetime,
        metrics: List[str],
        dimension: List[str],
        filters: Optional[Dict] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Poluchenie dannyh analitiki."""
        data = {
            "date_from": date_from.strftime("%Y-%m-%d"),
            "date_to": date_to.strftime("%Y-%m-%d"),
            "metrics": metrics,
            "dimension": dimension,
            "limit": limit,
            "offset": offset
        }
        
        if filters:
            data["filters"] = filters
        
        return await self._make_request("POST", "/v1/analytics/data", data)
    
    async def get_stock_on_warehouses(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Poluchenie ostatkov na skladah."""
        return await self._make_request("POST", "/v2/analytics/stock_on_warehouses", payload or {})
    
    async def get_analytics_turnover(
        self,
        date_from: datetime,
        date_to: datetime,
        limit: int = 1000,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Poluchenie oborachivaemosti zapasov."""
        data = {
            "date_from": date_from.strftime("%Y-%m-%d"),
            "date_to": date_to.strftime("%Y-%m-%d"),
            "limit": limit,
            "offset": offset
        }
        return await self._make_request("POST", "/v1/analytics/turnover/stocks", data)
    
    async def get_analytics_stock_management(
        self,
        limit: int = 1000,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Poluchenie upravlenija ostatkami."""
        data = {"limit": limit, "offset": offset}
        return await self._make_request("POST", "/v1/analytics/manage/stocks", data)

    async def get_analytics_product_queries(
        self,
        date_from: datetime,
        date_to: Optional[datetime] = None,
        skus: Optional[List[int]] = None,
        page: int = 0,
        page_size: int = 100,
        sort_by: str = "BY_SEARCHES",
        sort_dir: str = "DESCENDING",
    ) -> Dict[str, Any]:
        """Получение агрегированной аналитики по запросам товара."""
        payload: Dict[str, Any] = {
            "date_from": date_from.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "page": page,
            "page_size": page_size,
            "skus": [str(int(s)) for s in (skus or []) if s is not None],
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }
        if date_to is not None:
            payload["date_to"] = date_to.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return await self._make_request("POST", "/v1/analytics/product-queries", payload)

    async def get_analytics_product_queries_details(
        self,
        date_from: datetime,
        date_to: Optional[datetime] = None,
        skus: Optional[List[int]] = None,
        page: int = 0,
        page_size: int = 100,
        limit_by_sku: int = 15,
        sort_by: str = "BY_SEARCHES",
        sort_dir: str = "DESCENDING",
    ) -> Dict[str, Any]:
        """Получение детализации поисковых запросов по товару."""
        payload: Dict[str, Any] = {
            "date_from": date_from.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "page": page,
            "page_size": page_size,
            "limit_by_sku": limit_by_sku,
            "skus": [str(int(s)) for s in (skus or []) if s is not None],
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }
        if date_to is not None:
            payload["date_to"] = date_to.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return await self._make_request("POST", "/v1/analytics/product-queries/details", payload)

    async def get_analytics_stocks(self, skus: List[int]) -> Dict[str, Any]:
        """Poluchenie analytics stocks po SKU (1..100 za zapros)."""
        if not skus:
            raise OzonAPIError("skus must not be empty")
        if len(skus) > 100:
            raise OzonAPIError("skus must contain at most 100 items")
        data = {"skus": skus}
        return await self._make_request("POST", "/v1/analytics/stocks", data)
    
    # ==================== WAREHOUSES API ====================
    
    async def get_warehouses_list(self) -> Dict[str, Any]:
        """Poluchenie spiska skladov prodavca (FBO/FBS)."""
        return await self._make_request("POST", "/v1/warehouse/fbo/seller/list", {})
    
    async def get_clusters_list(self) -> Dict[str, Any]:
        """Poluchenie spiska klastero."""
        return await self._make_request("POST", "/v1/cluster/list", {})
    
    async def get_delivery_methods_list(
        self,
        limit: int = 1000,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Poluchenie metodov dostavki."""
        data = {"limit": limit, "offset": offset}
        return await self._make_request("POST", "/v1/delivery-method/list", data)
    
    # ==================== SELLER RATING API ====================
    
    async def get_rating_summary(self) -> Dict[str, Any]:
        """Poluchenie svodki po reitingu prodavca."""
        return await self._make_request("POST", "/v1/rating/summary", {})
    
    async def get_rating_history(
        self,
        date_from: datetime,
        date_to: datetime
    ) -> Dict[str, Any]:
        """Poluchenie istorii reitinga."""
        data = {
            "date_from": date_from.strftime("%Y-%m-%d"),
            "date_to": date_to.strftime("%Y-%m-%d")
        }
        return await self._make_request("POST", "/v1/rating/history", data)
    
    # ==================== REVIEWS API ====================
    
    async def get_reviews_list(
        self,
        limit: int = 100,
        last_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> Dict[str, Any]:
        """Poluchenie spiska otzyvov."""
        data = {"limit": limit}
        if last_id:
            data["last_id"] = last_id
        if status:
            data["status"] = status
        
        return await self._make_request("POST", "/v1/review/list", data)
    
    async def get_all_reviews(
        self,
        status: Optional[str] = None
    ) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh otzyvov s paginaciej."""
        last_id = None
        while True:
            result = await self.get_reviews_list(
                limit=100,
                last_id=last_id,
                status=status
            )
            reviews = result.get("reviews", [])
            
            if not reviews:
                break
            
            yield reviews
            
            last_id = result.get("last_id")
            if not last_id:
                break
    
    async def get_review_info(self, review_id) -> Dict[str, Any]:
        """Poluchenie informacii ob otzyve. review_id — string (UUID)."""
        data = {"review_id": str(review_id)}
        return await self._make_request("POST", "/v1/review/info", data)
    
    async def get_review_comments(self, review_id: int) -> Dict[str, Any]:
        """Poluchenie kommentariev k otzyvu."""
        data = {"review_id": review_id}
        return await self._make_request("POST", "/v1/review/comment/list", data)
    
    async def get_reviews_count(self) -> Dict[str, Any]:
        """Poluchenie kolichestva otzyvov."""
        return await self._make_request("POST", "/v1/review/count", {})

    async def create_review_comment(
        self,
        review_id: str,
        text: str,
        mark_review_as_processed: bool = True,
        parent_comment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Otvet na otzyv. Trebuet podpisku 'Upravlenie otzyvami' ili Premium Pro."""
        data: Dict[str, Any] = {
            "review_id": review_id,
            "text": text,
            "mark_review_as_processed": mark_review_as_processed,
        }
        if parent_comment_id:
            data["parent_comment_id"] = parent_comment_id
        return await self._make_request("POST", "/v1/review/comment/create", data)
    
    # ==================== QUESTIONS API ====================
    
    async def get_questions_list(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None
    ) -> Dict[str, Any]:
        """Poluchenie spiska voprosov."""
        data = {"limit": limit, "offset": offset}
        if status:
            data["status"] = status
        
        return await self._make_request("POST", "/v1/question/list", data)
    
    async def get_all_questions(
        self,
        status: Optional[str] = None
    ) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh voprosov s paginaciej."""
        offset = 0
        while True:
            result = await self.get_questions_list(
                limit=100,
                offset=offset,
                status=status
            )
            questions = result.get("questions", [])
            
            if not questions:
                break
            
            yield questions
            
            if len(questions) < 100:
                break
            
            offset += 100
    
    async def get_question_info(self, question_id: int) -> Dict[str, Any]:
        """Poluchenie informacii o voprose."""
        data = {"question_id": question_id}
        return await self._make_request("POST", "/v1/question/info", data)
    
    async def get_question_answers(self, question_id: int) -> Dict[str, Any]:
        """Poluchenie otvetov na vopros."""
        data = {"question_id": question_id}
        return await self._make_request("POST", "/v1/question/answer/list", data)
    
    async def get_questions_count(self) -> Dict[str, Any]:
        """Poluchenie kolichestva voprosov."""
        return await self._make_request("POST", "/v1/question/count", {})
    
    async def get_top_sku_with_questions(self) -> Dict[str, Any]:
        """Poluchenie tovarov s voprosami."""
        return await self._make_request("POST", "/v1/question/top_sku", {})
    
    # ==================== CHAT API ====================
    
    async def get_chat_list(
        self,
        limit: int = 100,
        offset: int = 0,
        chat_status: Optional[str] = None
    ) -> Dict[str, Any]:
        """Poluchenie spiska chatov."""
        data = {"limit": limit, "offset": offset}
        if chat_status:
            data["chat_status"] = chat_status
        
        return await self._make_request("POST", "/v1/chat/list", data)
    
    async def get_all_chats(
        self,
        chat_status: Optional[str] = None
    ) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh chatov s paginaciej."""
        offset = 0
        while True:
            result = await self.get_chat_list(
                limit=100,
                offset=offset,
                chat_status=chat_status
            )
            chats = result.get("chats", [])
            
            if not chats:
                break
            
            yield chats
            
            if len(chats) < 100:
                break
            
            offset += 100
    
    async def get_chat_history(self, chat_id: str, limit: int = 100) -> Dict[str, Any]:
        """Poluchenie istorii soobshhenij chata."""
        data = {"chat_id": chat_id, "limit": limit}
        return await self._make_request("POST", "/v1/chat/history", data)
    
    async def get_chat_updates(self, from_date: datetime) -> Dict[str, Any]:
        """Poluchenie obnovlenij chatov."""
        data = {"from_date": from_date.isoformat()}
        return await self._make_request("POST", "/v1/chat/updates", data)
    
    # ==================== PROMO API ====================
    
    async def get_actions_list(self) -> Dict[str, Any]:
        """Poluchenie spiska akcij."""
        return await self._make_request("GET", "/v1/actions")
    
    async def get_action_products(
        self,
        action_id: int,
        limit: int = 100,
        last_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Poluchenie tovarov v akcii."""
        data = {
            "action_id": action_id,
            "limit": limit,
        }
        if last_id:
            data["last_id"] = last_id
        return await self._make_request("POST", "/v1/actions/products", data)

    async def get_all_action_products(
        self,
        action_id: int,
        limit: int = 100
    ) -> AsyncGenerator[List[Dict], None]:
        """Poluchenie vseh tovarov, uchastvujushhih v akcii."""
        last_id: Optional[str] = None
        while True:
            result = await self.get_action_products(action_id=action_id, limit=limit, last_id=last_id)
            result_data = result.get("result", {})
            items = result_data.get("products", [])
            if not items:
                break
            yield items
            next_last_id = result_data.get("last_id")
            if not next_last_id or next_last_id == last_id:
                break
            last_id = str(next_last_id)
    
    async def get_action_candidates(
        self,
        action_id: int,
        limit: int = 1000,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Poluchenie tovarov-kandidatov dlja akcii."""
        data = {
            "action_id": action_id,
            "limit": limit,
            "offset": offset
        }
        return await self._make_request("POST", "/v1/actions/candidates", data)
    
    async def activate_action_products(
        self,
        action_id: int,
        products: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Dobavit tovary v akciju.

        products: [{"product_id": 123, "action_price": 100.0}, ...]
        """
        data = {
            "action_id": action_id,
            "products": products,
        }
        return await self._make_request("POST", "/v1/actions/products/activate", data)

    async def deactivate_action_products(
        self,
        action_id: int,
        product_ids: List[int],
    ) -> Dict[str, Any]:
        """Udalit tovary iz akcii."""
        data = {
            "action_id": action_id,
            "product_ids": product_ids,
        }
        return await self._make_request("POST", "/v1/actions/products/deactivate", data)

    # ==================== REPORTS API ====================
    
    async def get_reports_list(
        self,
        page: int = 1,
        page_size: int = 100,
        report_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Poluchenie spiska asinhronnyh otchetov."""
        data: Dict[str, Any] = {"page": page, "page_size": page_size}
        if report_type:
            data["report_type"] = report_type
        return await self._make_request("POST", "/v1/report/list", data)
    
    async def get_report_info(self, report_id: str) -> Dict[str, Any]:
        """Poluchenie informacii ob otchete."""
        data = {"code": report_id}
        return await self._make_request("POST", "/v1/report/info", data)

    async def download_file(self, file_url: str) -> bytes:
        """Skachivanie fajla otcheta po prjamoj ssylke."""
        # Report files are served from Ozon CDN and can take noticeably longer
        # than API JSON responses, especially for xlsx/csv report generation.
        timeout = aiohttp.ClientTimeout(total=600, connect=60, sock_read=300)
        last_error: Optional[Exception] = None
        for attempt in range(4):
            try:
                async with self.semaphore:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(file_url) as response:
                            response.raise_for_status()
                            return await response.read()
            except Exception as exc:  # pragma: no cover - network instability
                last_error = exc
                await asyncio.sleep(min(2 ** attempt, 10))
        raise OzonAPIError(f"Failed to download report file: {last_error}")

    async def download_file_to_tempfile(self, file_url: str, suffix: str = "") -> str:
        """Skachivanie fajla otcheta vo vremennyj fajl potokom."""
        timeout = aiohttp.ClientTimeout(total=900, connect=60, sock_read=300)
        last_error: Optional[Exception] = None

        for attempt in range(4):
            tmp_path = ""
            try:
                fd, tmp_path = tempfile.mkstemp(prefix="ozon_report_", suffix=suffix)
                os.close(fd)

                async with self.semaphore:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(file_url) as response:
                            response.raise_for_status()
                            with open(tmp_path, "wb") as out:
                                async for chunk in response.content.iter_chunked(1024 * 1024):
                                    if chunk:
                                        out.write(chunk)
                return tmp_path
            except Exception as exc:  # pragma: no cover - network instability
                last_error = exc
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                await asyncio.sleep(min(2 ** attempt, 10))

        raise OzonAPIError(f"Failed to download report file: {last_error}")
    
    async def create_report_products(
        self,
        language: str = "DEFAULT",
        visibility: str = "ALL",
        offer_id: Optional[str] = None,
        search: Optional[str] = None,
        sku: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """Sozdanie otcheta po tovaram."""
        data: Dict[str, Any] = {
            "language": language,
            "visibility": visibility,
            "offer_id": [],
            "search": search or "",
            "sku": [],
        }
        if offer_id:
            data["offer_id"] = [offer_id]
        if sku:
            data["sku"] = sku
        
        return await self._make_request("POST", "/v1/report/products/create", data)
    
    async def create_report_postings(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        posting_type: Optional[str] = None,
        filter_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Sozdanie otcheta ob otpravlenijah.
        
        Dokumentacija: https://docs.ozon.ru/api/seller/#operation/ReportAPI_CreateCompanyPostingsReport
        """
        if filter_payload is not None:
            data: Dict[str, Any] = filter_payload
        else:
            if not date_from or not date_to:
                raise OzonAPIError("Either filter_payload or date_from/date_to must be provided")
            schema = (posting_type or "fbo").lower()
            data = {
                "filter": {
                    "processed_at_from": date_from.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "processed_at_to": date_to.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "delivery_schema": [schema],
                    "is_express": False,
                    "sku": [],
                    "cancel_reason_id": [],
                    "offer_id": "",
                    "status_alias": [],
                    "statuses": [],
                    "title": "",
                },
                "language": "DEFAULT",
                "with": {
                    "additional_data": False,
                    "analytics_data": False,
                    "customer_data": False,
                    "jewelry_codes": False,
                },
            }
        
        return await self._make_request("POST", "/v1/report/postings/create", data)
    
    async def create_report_returns(
        self,
        date_from: datetime,
        date_to: datetime,
        delivery_schema: Optional[str] = None,
        status: Optional[str] = None,
        language: str = "DEFAULT",
        filter_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Sozdanie otcheta o vozvratah."""
        if filter_payload is not None:
            data = {"filter": filter_payload, "language": language}
        else:
            filt: Dict[str, Any] = {
                "date_from": date_from.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "date_to": date_to.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
            }
            if delivery_schema:
                filt["delivery_schema"] = delivery_schema.lower()
            if status:
                filt["status"] = status
            data = {"filter": filt, "language": language}
        return await self._make_request("POST", "/v2/report/returns/create", data)

    async def create_report_warehouse_stock(
        self,
        warehouse_ids: List[int],
        language: str = "DEFAULT",
    ) -> Dict[str, Any]:
        """Sozdanie otcheta po ostatkam na skladah."""
        if not warehouse_ids:
            raise OzonAPIError("warehouse_ids must not be empty")
        data = {
            "warehouse_id": warehouse_ids,
            "language": language,
        }
        return await self._make_request("POST", "/v1/report/warehouse/stock", data)

    async def create_report_compensation(
        self,
        report_month: str,
        language: str = "RU",
    ) -> Dict[str, Any]:
        """Sozdanie async-otcheta o kompensacijah."""
        data = {
            "date": report_month,
            "language": language,
        }
        return await self._make_request("POST", "/v1/finance/compensation", data)

    async def create_report_decompensation(
        self,
        report_month: str,
        language: str = "RU",
    ) -> Dict[str, Any]:
        """Sozdanie async-otcheta o dekompensacijah."""
        data = {
            "date": report_month,
            "language": language,
        }
        return await self._make_request("POST", "/v1/finance/decompensation", data)

    # ==================== PERFORMANCE API ====================
    
    async def get_campaigns(self) -> Dict[str, Any]:
        """Poluchenie spiska reklamnyh kampanij."""
        return await self._make_request("GET", "/api/client/campaign", use_performance=True)
    
    async def get_campaign_details(self, campaign_id: int) -> Dict[str, Any]:
        """Poluchenie detalej kampanii."""
        return await self._make_request(
            "GET", 
            f"/api/client/campaign/{campaign_id}",
            use_performance=True
        )
    
    async def get_campaign_objects(self, campaign_id: int) -> Dict[str, Any]:
        """Poluchenie ob#ektov kampanii."""
        return await self._make_request(
            "GET",
            f"/api/client/campaign/{campaign_id}/objects",
            use_performance=True
        )

    async def activate_campaign(self, campaign_id: int) -> Dict[str, Any]:
        """Vkljuchit' reklamnuju kampaniju (Performance API)."""
        return await self._make_request(
            "POST",
            f"/api/client/campaign/{campaign_id}/activate",
            data={},
            use_performance=True,
        )

    async def deactivate_campaign(self, campaign_id: int) -> Dict[str, Any]:
        """Vykljuchit' reklamnuju kampaniju (Performance API)."""
        return await self._make_request(
            "POST",
            f"/api/client/campaign/{campaign_id}/deactivate",
            data={},
            use_performance=True,
        )
    
    async def request_campaign_report(
        self,
        campaign_ids: List[int],
        date_from: datetime,
        date_to: datetime,
        group_by: str = "DATE"
    ) -> str:
        """Zapros otcheta po kampanii. Vozvrashhaet UUID otcheta."""
        data = {
            "campaigns": [str(cid) for cid in campaign_ids],
            "dateFrom": date_from.strftime("%Y-%m-%d"),
            "dateTo": date_to.strftime("%Y-%m-%d"),
            "groupBy": group_by
        }

        result = await self._make_request(
            "POST",
            "/api/client/statistics/json",
            data,
            use_performance=True
        )
        return result.get("UUID")

    async def get_report_status(self, uuid: str) -> Dict[str, Any]:
        """Poluchenie statusa otcheta (state, link, request)."""
        return await self._make_request(
            "GET",
            f"/api/client/statistics/{uuid}",
            use_performance=True
        )

    async def download_campaign_report(self, uuid: str) -> Dict[str, Any]:
        """Skachivanie gotovogo JSON-otcheta po UUID."""
        return await self._make_request(
            "GET",
            f"/api/client/statistics/report?UUID={uuid}",
            use_performance=True
        )

    async def download_report(self, uuid: str) -> bytes:
        """Skachivanie CSV/ZIP otcheta."""
        async with self.semaphore:
            url = f"{self.PERFORMANCE_URL}/api/client/statistics/report/download?UUID={uuid}"
            headers = {"Authorization": f"Bearer {self.performance_token}"}

            async with self.session.get(url, headers=headers) as response:
                response.raise_for_status()
                return await response.read()
