"""Application configuration."""
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ozon Seller API
    ozon_client_id: str = Field(alias="OZON_CLIENT_ID")
    ozon_api_key: str = Field(alias="OZON_API_KEY")

    # Ozon Performance API (optional)
    ozon_performance_client_id: Optional[str] = Field(default=None, alias="OZON_PERFORMANCE_CLIENT_ID")
    ozon_performance_client_secret: Optional[str] = Field(default=None, alias="OZON_PERFORMANCE_CLIENT_SECRET")

    # Wildberries Finance API (optional, required for WB sync mode)
    wb_api_key: Optional[str] = Field(default=None, alias="WB_API_KEY")

    # Database
    database_url: str = Field(alias="DATABASE_URL")

    # Sync settings
    sync_days_back: int = Field(default=30, alias="SYNC_DAYS_BACK")
    report_postings_days_back: int = Field(default=60, alias="REPORT_POSTINGS_DAYS_BACK")
    batch_size: int = Field(default=1000, alias="BATCH_SIZE")
    max_concurrent_requests: int = Field(default=5, alias="MAX_CONCURRENT_REQUESTS")
    ozon_http_timeout_total: int = Field(default=180, alias="OZON_HTTP_TIMEOUT_TOTAL")
    ozon_http_timeout_connect: int = Field(default=30, alias="OZON_HTTP_TIMEOUT_CONNECT")
    ozon_http_timeout_sock_read: int = Field(default=120, alias="OZON_HTTP_TIMEOUT_SOCK_READ")
    ozon_trust_env_proxy: bool = Field(default=True, alias="OZON_TRUST_ENV_PROXY")
    ozon_force_ipv4: bool = Field(default=False, alias="OZON_FORCE_IPV4")
    async_report_refresh_hours: int = Field(default=24, alias="ASYNC_REPORT_REFRESH_HOURS")
    campaigns_refresh_hours: int = Field(default=6, alias="CAMPAIGNS_REFRESH_HOURS")

    # FBS warehouse IDs for stock sync (comma-separated)
    # Format: id1,id2 or id1:Name1,id2:Name2
    fbs_warehouse_ids: str = Field(default="", alias="FBS_WAREHOUSE_IDS")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


settings = Settings()
