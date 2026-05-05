"""Планировщик для автоматической синхронизации."""
import asyncio
import logging
import schedule
import time
from datetime import datetime

from src.config import settings
from src.database import init_database, close_database
from src.ozon_client import OzonClient
from src.sync_manager import SyncManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def sync_job(mode: str = "full"):
    """Задача синхронизации."""
    logger.info(f"[{datetime.now()}] Starting scheduled sync: {mode}")
    
    try:
        async with OzonClient(
            client_id=settings.ozon_client_id,
            api_key=settings.ozon_api_key,
            performance_client_id=settings.ozon_performance_client_id,
            performance_client_secret=settings.ozon_performance_client_secret,
            max_concurrent_requests=settings.max_concurrent_requests
        ) as client:
            
            sync_manager = SyncManager(client)
            
            if mode == "full":
                results = await sync_manager.full_sync()
                logger.info(f"Full sync completed: {results}")
            elif mode == "products":
                result = await sync_manager.sync_products()
                logger.info(f"Products sync completed: {result}")
            elif mode == "stocks":
                result = await sync_manager.sync_product_stocks()
                logger.info(f"Stocks sync completed: {result}")
            elif mode == "postings":
                result = await sync_manager.sync_postings()
                logger.info(f"Postings sync completed: {result}")
            elif mode == "transactions":
                result = await sync_manager.sync_transactions()
                logger.info(f"Transactions sync completed: {result}")
            elif mode == "returns":
                result = await sync_manager.sync_returns()
                logger.info(f"Returns sync completed: {result}")
            elif mode == "campaigns":
                result = await sync_manager.sync_campaigns()
                logger.info(f"Campaigns sync completed: {result}")
                
    except Exception as e:
        logger.exception(f"Scheduled sync failed: {e}")


def run_async_job(mode: str):
    """Запуск асинхронной задачи."""
    asyncio.run(sync_job(mode))


def setup_schedule():
    """Настройка расписания."""
    # Полная синхронизация каждые 6 часов
    schedule.every(6).hours.do(run_async_job, mode="full")
    
    # Синхронизация остатков каждый час
    schedule.every(1).hours.do(run_async_job, mode="stocks")
    
    # Синхронизация отправлений каждые 2 часа
    schedule.every(2).hours.do(run_async_job, mode="postings")
    
    # Синхронизация транзакций раз в день в 3:00
    schedule.every().day.at("03:00").do(run_async_job, mode="transactions")
    
    logger.info("Scheduler started. Press Ctrl+C to stop.")
    logger.info("Schedule:")
    logger.info("  - Full sync: every 6 hours")
    logger.info("  - Stocks sync: every hour")
    logger.info("  - Postings sync: every 2 hours")
    logger.info("  - Transactions sync: daily at 03:00")


def main():
    """Главная функция планировщика."""
    # Инициализация БД
    asyncio.run(init_database())
    
    # Настройка расписания
    setup_schedule()
    
    # Бесконечный цикл
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # Проверка каждую минуту
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
    finally:
        asyncio.run(close_database())


if __name__ == "__main__":
    main()
