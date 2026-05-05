"""Модуль для работы с базой данных SQLite (для быстрого старта без PostgreSQL)."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import logging
import os

from src.models import Base

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Менеджер базы данных SQLite."""
    
    def __init__(self, database_url: str = None):
        # По умолчанию используем локальный SQLite файл
        if database_url is None:
            db_path = os.path.join(os.path.dirname(__file__), '..', 'ozon_analytics.db')
            database_url = f"sqlite+aiosqlite:///{os.path.abspath(db_path)}"
        self.database_url = database_url
        self.engine = None
        self.async_session_maker = None
    
    async def initialize(self):
        """Инициализация подключения к БД."""
        self.engine = create_async_engine(
            self.database_url,
            echo=False,
            future=True,
        )
        
        self.async_session_maker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        
        logger.info(f"Database engine initialized: {self.database_url}")
    
    async def create_tables(self):
        """Создание всех таблиц."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
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
db_manager = DatabaseManager()


async def init_database():
    """Инициализация базы данных."""
    await db_manager.initialize()
    await db_manager.create_tables()


async def close_database():
    """Закрытие соединения с БД."""
    await db_manager.close()
