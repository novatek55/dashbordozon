"""Модуль для работы с базой данных."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
import logging

from src.models import Base
from src.config import settings

logger = logging.getLogger(__name__)

USED_TABLES = {
    "sync_logs",
    "products",
    "fact_orders",
    "fact_order_items",
    "transactions",
    "postings",
    "postings_fbo",
    "posting_transaction_snapshots",
    "transaction_items",
    "transaction_services",
    "finance_balances",
    "finance_transaction_totals",
    "returns",
    "returns_fbo",
    "campaigns",
    "campaign_statistics",
    "campaign_details",
    "campaign_objects",
    "cash_flow_statements",
    "mutual_settlements",
    "b2b_sales",
    "promo_actions",
    "promo_products",
    "async_reports",
    "report_products_items",
    "report_returns_items",
    "report_warehouse_stock_items",
    "fbs_warehouse_stocks",
    "report_compensation_items",
    "report_download_retries",
    "analytics_data",
    "analytics_product_query_summary",
    "analytics_product_query_details",
    "analytics_stocks",
    "analytics_turnover",
    "analytics_average_delivery_time",
    "stock_daily_snapshots",
    "delivery_time_daily_snapshots",
    "realization_reports",
    "realization_report_details",
    "logistics_tariffs",
    "product_dimensions",
    "competitor_snapshots",
    "reviews",
    "review_comments",
    "review_rating_snapshots",
    "seller_ratings",
    "seller_rating_history",
}


class DatabaseManager:
    """Менеджер базы данных."""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = None
        self.async_session_maker = None
    
    async def initialize(self):
        """Инициализация подключения к БД."""
        self.engine = create_async_engine(
            self.database_url,
            echo=False,
            future=True,
            pool_size=10,
            max_overflow=20,
        )
        
        self.async_session_maker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        
        logger.info("Database engine initialized")
    
    async def create_tables(self):
        """Создание всех таблиц."""
        async with self.engine.begin() as conn:
            tables_to_create = [
                table for name, table in Base.metadata.tables.items()
                if name in USED_TABLES
            ]
            await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables_to_create))
        logger.info("Database tables created")
    
    async def drop_tables(self):
        """Удаление всех таблиц (осторожно!)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        logger.info("Database tables dropped")
    
    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Контекстный менеджер для сессии БД."""
        async with self.async_session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            finally:
                await session.close()
    
    async def close(self):
        """Закрытие соединения."""
        if self.engine:
            await self.engine.dispose()
            logger.info("Database connection closed")
    
    async def health_check(self) -> bool:
        """Проверка соединения с БД."""
        try:
            async with self.session() as session:
                result = await session.execute(text("SELECT 1"))
                return result.scalar() == 1
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False


# Глобальный экземпляр менеджера БД
db_manager = DatabaseManager(settings.database_url)


async def init_database():
    """Инициализация базы данных."""
    await db_manager.initialize()
    await db_manager.create_tables()


async def close_database():
    """Закрытие соединения с БД."""
    await db_manager.close()
