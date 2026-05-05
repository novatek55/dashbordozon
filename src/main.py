"""Glavnyj modul' dlja zapuska sinhronizacii Ozon API."""
import asyncio
import logging
import sys
from datetime import datetime
from typing import Optional
import argparse

from src.config import settings
from src.database import init_database, close_database, db_manager
from src.ozon_client import OzonClient
from src.sync_manager import SyncManager


# Nastrojka logirovanija
def setup_logging(log_level: str = "INFO"):
    """Nastrojka logirovanija."""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Konsol'nyj obrabotchik
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format))
    
    # Fajlovyj obrabotchik
    import os
    os.makedirs("logs", exist_ok=True)
    file_handler = logging.FileHandler(f"logs/ozon_sync_{datetime.now().strftime('%Y%m%d')}.log")
    file_handler.setFormatter(logging.Formatter(log_format))
    
    # Kornevoj logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Logger dlja sqlalchemy (umenshaem shum)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


async def sync_products(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija tovarov."""
    logging.info("=== Syncing Products ===")
    result = await sync_manager.sync_products()
    logging.info(f"Products sync result: {result}")


async def sync_stocks(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija ostatkov."""
    logging.info("=== Syncing Stocks ===")
    result = await sync_manager.sync_stocks()
    logging.info(f"Stocks sync result: {result}")


async def sync_analytics_stocks(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija analytics stocks."""
    logging.info("=== Syncing Analytics Stocks ===")
    result = await sync_manager.sync_analytics_stocks()
    logging.info(f"Analytics stocks sync result: {result}")


async def sync_analytics_turnover(client: OzonClient, sync_manager: SyncManager, days_back: int = None):
    """Sinhronizacija analytics turnover."""
    logging.info("=== Syncing Analytics Turnover ===")
    effective_days = days_back if days_back is not None else 30
    result = await sync_manager.sync_analytics_turnover(days_back=effective_days)
    logging.info(f"Analytics turnover sync result: {result}")


async def sync_analytics_data(
    client: OzonClient,
    sync_manager: SyncManager,
    days_back: int = None,
    metrics: Optional[str] = None,
    dimensions: Optional[str] = None,
):
    """Sinhronizacija analytics data (/v1/analytics/data)."""
    logging.info("=== Syncing Analytics Data ===")
    effective_days = days_back if days_back is not None else 7
    metrics_list = [m.strip() for m in (metrics or "").split(",") if m.strip()]
    dimensions_list = [d.strip() for d in (dimensions or "").split(",") if d.strip()]
    result = await sync_manager.sync_analytics_data(
        days_back=effective_days,
        metrics=metrics_list or None,
        dimensions=dimensions_list or None,
    )
    logging.info(f"Analytics data sync result: {result}")


async def sync_analytics_product_queries(client: OzonClient, sync_manager: SyncManager, days_back: int = None):
    """Синхронизация аналитики поисковых запросов по SKU."""
    logging.info("=== Syncing Analytics Product Queries ===")
    effective_days = days_back if days_back is not None else 30
    result = await sync_manager.sync_analytics_product_queries(days_back=effective_days)
    logging.info(f"Analytics product queries sync result: {result}")


async def sync_average_delivery_time(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija average delivery time."""
    logging.info("=== Syncing Average Delivery Time ===")
    result = await sync_manager.sync_analytics_average_delivery_time()
    logging.info(f"Average delivery time sync result: {result}")


async def sync_realization_v2(client: OzonClient, sync_manager: SyncManager, days_back: int = None):
    """Sinhronizacija realization v2."""
    logging.info("=== Syncing Realization V2 ===")
    effective_days = days_back if days_back is not None else 365
    result = await sync_manager.sync_realization_v2(days_back=effective_days)
    logging.info(f"Realization v2 sync result: {result}")


async def sync_postings(client: OzonClient, sync_manager: SyncManager, days_back: int = None):
    """Sinhronizacija otpravlenij."""
    logging.info("=== Syncing Postings ===")
    result = await sync_manager.sync_postings(days_back=days_back)
    logging.info(f"Postings sync result: {result}")


async def sync_transactions(client: OzonClient, sync_manager: SyncManager, days_back: int = None):
    """Sinhronizacija transakcij."""
    logging.info("=== Syncing Transactions ===")
    result = await sync_manager.sync_transactions(days_back=days_back)
    logging.info(f"Transactions sync result: {result}")


async def sync_cash_flow(client: OzonClient, sync_manager: SyncManager, days_back: int = None):
    """Sinhronizacija cash flow statements."""
    logging.info("=== Syncing Cash Flow Statements ===")
    effective_days = days_back if days_back is not None else 365
    result = await sync_manager.sync_cash_flow_statements(days_back=effective_days)
    logging.info(f"Cash flow sync result: {result}")


async def normalize_finance(client: OzonClient, sync_manager: SyncManager):
    """Backfill normalizovannyh finance-tablic iz uzhe zagruzhennyh dannyh."""
    logging.info("=== Normalizing Finance Data ===")
    result = await sync_manager.backfill_normalized_finance_data()
    logging.info(f"Normalize finance result: {result}")


async def sync_returns(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija vozvratov."""
    logging.info("=== Syncing Returns ===")
    result = await sync_manager.sync_returns()
    logging.info(f"Returns sync result: {result}")


async def sync_returns_fbo(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija vozvratov FBO."""
    logging.info("=== Syncing Returns FBO ===")
    result = await sync_manager.sync_returns_fbo()
    logging.info(f"Returns FBO sync result: {result}")


async def sync_campaigns(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija reklamnyh kampanij."""
    logging.info("=== Syncing Campaigns ===")
    result = await sync_manager.sync_campaigns()
    logging.info(f"Campaigns sync result: {result}")


async def sync_reviews(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija otzyvov."""
    logging.info("=== Syncing Reviews ===")
    result = await sync_manager.sync_reviews()
    logging.info(f"Reviews sync result: {result}")


async def sync_seller_rating(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija reitinga prodavca."""
    logging.info("=== Syncing Seller Rating ===")
    result = await sync_manager.sync_seller_rating()
    logging.info(f"Seller rating sync result: {result}")


async def sync_promo(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija akcij Ozon."""
    logging.info("=== Syncing Promo Actions ===")
    result = await sync_manager.sync_promo()
    logging.info(f"Promo sync result: {result}")


async def sync_report_postings(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija async otchetov po otpravlenijam v BD."""
    logging.info("=== Syncing Report Postings ===")
    result = await sync_manager.sync_postings_report()
    logging.info(f"Report postings sync result: {result}")


async def sync_report_products(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija async otcheta po tovaram v BD."""
    logging.info("=== Syncing Report Products ===")
    result = await sync_manager.sync_products_report()
    logging.info(f"Report products sync result: {result}")


async def sync_report_returns(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija async otcheta po vozvratam v BD."""
    logging.info("=== Syncing Report Returns ===")
    result = await sync_manager.sync_returns_report()
    logging.info(f"Report returns sync result: {result}")


async def sync_report_compensation(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija async otchetov po kompensacijam i dekompensacijam v BD."""
    logging.info("=== Syncing Report Compensation ===")
    result = await sync_manager.sync_compensation_reports()
    logging.info(f"Report compensation sync result: {result}")


async def sync_report_warehouse_stock(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija async otcheta po skladskim ostatkam v BD."""
    logging.info("=== Syncing Report Warehouse Stock ===")
    result = await sync_manager.sync_warehouse_stock_report()
    logging.info(f"Report warehouse stock sync result: {result}")


async def sync_fbs_warehouse_stocks(client: OzonClient, sync_manager: SyncManager):
    """Sinhronizacija zhivyh FBS-ostatkov po skladam v BD."""
    logging.info("=== Syncing FBS Warehouse Stocks ===")
    result = await sync_manager.sync_fbs_warehouse_stocks()
    logging.info(f"FBS warehouse stocks sync result: {result}")


async def full_sync(client: OzonClient, sync_manager: SyncManager):
    """Polnaja sinhronizacija vseh dannyh."""
    logging.info("=== Starting Full Sync ===")
    results = await sync_manager.full_sync()
    
    logging.info("\n=== Sync Results Summary ===")
    for entity, result in results.items():
        if "error" in result:
            logging.error(f"{entity}: ERROR - {result['error']}")
        else:
            logging.info(f"{entity}: {result}")


async def main():
    """Glavnaja funkcija."""
    parser = argparse.ArgumentParser(description="Ozon API Data Sync Tool")
    parser.add_argument(
        "--mode",
        choices=["full", "products", "transactions", "normalize_finance",
                 "cash_flow", "returns", "returns_fbo", "promo", "reviews", "campaigns",
                 "report_postings", "report_products", "report_returns", "report_compensation", "report_warehouse_stock",
                 "fbs_warehouse_stocks",
                 "analytics_data", "analytics_product_queries", "analytics_stocks", "analytics_turnover", "average_delivery_time", "realization_v2",
                 "dimensions"],
        default="full",
        help="Sync mode (default: full)"
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=None,
        help=f"Number of days back to sync (default: {settings.sync_days_back})"
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database (create tables)"
    )
    parser.add_argument(
        "--metrics",
        type=str,
        default=None,
        help="Comma-separated metrics for --mode analytics_data"
    )
    parser.add_argument(
        "--dimensions",
        type=str,
        default=None,
        help="Comma-separated dimensions for --mode analytics_data"
    )
    
    args = parser.parse_args()
    
    # Nastrojka logirovanija
    setup_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 50)
    logger.info("Ozon API Sync Tool Started")
    logger.info("=" * 50)
    
    try:
        # Inicializacija bazy dannyh
        logger.info("Initializing database...")
        await init_database()
        
        # Proverka soedinenija s BD
        if not await db_manager.health_check():
            logger.error("Database connection failed!")
            return 1
        
        logger.info("Database connection established")
        
        # Sozdanie klijenta Ozon API
        async with OzonClient(
            client_id=settings.ozon_client_id,
            api_key=settings.ozon_api_key,
            performance_client_id=settings.ozon_performance_client_id,
            performance_client_secret=settings.ozon_performance_client_secret,
            max_concurrent_requests=settings.max_concurrent_requests
        ) as client:
            
            # Sozdanie menedzhera sinhronizacii
            sync_manager = SyncManager(client)
            
            # Vypolnenie sinhronizacii v zavisimosti ot rezhima
            if args.mode == "full":
                await full_sync(client, sync_manager)
            elif args.mode == "products":
                await sync_products(client, sync_manager)
            elif args.mode == "transactions":
                await sync_transactions(client, sync_manager, args.days_back)
            elif args.mode == "normalize_finance":
                await normalize_finance(client, sync_manager)
            elif args.mode == "analytics_stocks":
                await sync_analytics_stocks(client, sync_manager)
            elif args.mode == "analytics_data":
                await sync_analytics_data(
                    client,
                    sync_manager,
                    args.days_back,
                    args.metrics,
                    args.dimensions,
                )
            elif args.mode == "analytics_product_queries":
                await sync_analytics_product_queries(client, sync_manager, args.days_back)
            elif args.mode == "analytics_turnover":
                await sync_analytics_turnover(client, sync_manager, args.days_back)
            elif args.mode == "average_delivery_time":
                await sync_average_delivery_time(client, sync_manager)
            elif args.mode == "realization_v2":
                await sync_realization_v2(client, sync_manager, args.days_back)
            elif args.mode == "cash_flow":
                await sync_cash_flow(client, sync_manager, args.days_back)
            elif args.mode == "returns":
                await sync_returns(client, sync_manager)
            elif args.mode == "returns_fbo":
                await sync_returns_fbo(client, sync_manager)
            elif args.mode == "promo":
                await sync_promo(client, sync_manager)
            elif args.mode == "reviews":
                await sync_reviews(client, sync_manager)
            elif args.mode == "campaigns":
                await sync_campaigns(client, sync_manager)
            elif args.mode == "report_postings":
                await sync_report_postings(client, sync_manager)
            elif args.mode == "report_products":
                await sync_report_products(client, sync_manager)
            elif args.mode == "report_returns":
                await sync_report_returns(client, sync_manager)
            elif args.mode == "report_compensation":
                await sync_report_compensation(client, sync_manager)
            elif args.mode == "report_warehouse_stock":
                await sync_report_warehouse_stock(client, sync_manager)
            elif args.mode == "fbs_warehouse_stocks":
                await sync_fbs_warehouse_stocks(client, sync_manager)
            elif args.mode == "dimensions":
                logger.info("Starting product dimensions sync...")
                result = await sync_manager.sync_product_dimensions()
                logger.info(f"Product dimensions sync completed: {result}")
        
        logger.info("=" * 50)
        logger.info("Sync completed successfully!")
        logger.info("=" * 50)
        
    except Exception as e:
        logger.exception(f"Sync failed with error: {e}")
        return 1
    finally:
        await close_database()
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
