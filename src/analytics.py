"""Модуль для аналитики и построения отчетов на основе данных Ozon."""
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, and_, or_, desc, asc
from sqlalchemy.orm import joinedload
import logging

from src.database import db_manager
from src.models import (
    Product, Posting, PostingItem, Transaction, 
    Campaign, CampaignStatistic, Return, StockHistory
)

logger = logging.getLogger(__name__)


class OzonAnalytics:
    """Класс для аналитики данных Ozon."""
    
    def __init__(self):
        pass
    
    # ==================== PRODUCT ANALYTICS ====================
    
    async def get_products_summary(self) -> Dict[str, Any]:
        """Сводка по товарам."""
        async with db_manager.session() as session:
            # Общее количество товаров
            total_products = await session.scalar(select(func.count(Product.id)))
            
            # Количество активных товаров
            active_products = await session.scalar(
                select(func.count(Product.id)).where(Product.is_visible == True)
            )
            
            # Товары с нулевыми остатками
            out_of_stock = await session.scalar(
                select(func.count(Product.id)).where(
                    and_(Product.stock_fbo == 0, Product.stock_fbs == 0)
                )
            )
            
            # Средняя цена
            avg_price = await session.scalar(select(func.avg(Product.price)))
            
            return {
                "total_products": total_products,
                "active_products": active_products,
                "out_of_stock": out_of_stock,
                "average_price": round(float(avg_price or 0), 2)
            }
    
    async def get_low_stock_products(self, threshold: int = 10) -> List[Dict]:
        """Получить товары с низкими остатками."""
        async with db_manager.session() as session:
            result = await session.execute(
                select(Product)
                .where(
                    and_(
                        Product.is_visible == True,
                        (Product.stock_fbo + Product.stock_fbs) < threshold
                    )
                )
                .order_by((Product.stock_fbo + Product.stock_fbs).asc())
            )
            
            products = result.scalars().all()
            return [
                {
                    "product_id": p.product_id,
                    "offer_id": p.offer_id,
                    "name": p.name,
                    "stock_fbo": p.stock_fbo,
                    "stock_fbs": p.stock_fbs,
                    "total_stock": p.stock_fbo + p.stock_fbs,
                    "price": float(p.price) if p.price else 0
                }
                for p in products
            ]
    
    async def get_top_products_by_revenue(
        self, 
        days: int = 30, 
        limit: int = 20
    ) -> List[Dict]:
        """Топ товаров по выручке."""
        from_date = datetime.now() - timedelta(days=days)
        
        async with db_manager.session() as session:
            result = await session.execute(
                select(
                    Product.id,
                    Product.product_id,
                    Product.offer_id,
                    Product.name,
                    func.sum(PostingItem.quantity).label("total_quantity"),
                    func.sum(PostingItem.quantity * PostingItem.price).label("total_revenue")
                )
                .join(PostingItem, PostingItem.product_id == Product.id)
                .join(Posting, Posting.id == PostingItem.posting_id)
                .where(
                    and_(
                        Posting.created_at >= from_date,
                        Posting.status.notin_(["cancelled"])
                    )
                )
                .group_by(Product.id, Product.product_id, Product.offer_id, Product.name)
                .order_by(desc("total_revenue"))
                .limit(limit)
            )
            
            rows = result.all()
            return [
                {
                    "product_id": row.product_id,
                    "offer_id": row.offer_id,
                    "name": row.name,
                    "total_quantity": row.total_quantity or 0,
                    "total_revenue": float(row.total_revenue or 0)
                }
                for row in rows
            ]
    
    # ==================== SALES ANALYTICS ====================
    
    async def get_sales_summary(self, days: int = 30) -> Dict[str, Any]:
        """Сводка по продажам."""
        from_date = datetime.now() - timedelta(days=days)
        
        async with db_manager.session() as session:
            # Общее количество заказов
            total_orders = await session.scalar(
                select(func.count(Posting.id))
                .where(
                    and_(
                        Posting.created_at >= from_date,
                        Posting.status.notin_(["cancelled"])
                    )
                )
            )
            
            # Общая выручка
            total_revenue = await session.scalar(
                select(func.sum(Posting.total_price))
                .where(
                    and_(
                        Posting.created_at >= from_date,
                        Posting.status.notin_(["cancelled"])
                    )
                )
            )
            
            # Средний чек
            avg_order_value = await session.scalar(
                select(func.avg(Posting.total_price))
                .where(
                    and_(
                        Posting.created_at >= from_date,
                        Posting.status.notin_(["cancelled"])
                    )
                )
            )
            
            # Количество отмененных заказов
            cancelled_orders = await session.scalar(
                select(func.count(Posting.id))
                .where(
                    and_(
                        Posting.created_at >= from_date,
                        Posting.status == "cancelled"
                    )
                )
            )
            
            return {
                "period_days": days,
                "total_orders": total_orders,
                "total_revenue": float(total_revenue or 0),
                "average_order_value": float(avg_order_value or 0),
                "cancelled_orders": cancelled_orders,
                "cancellation_rate": round(
                    (cancelled_orders / (total_orders + cancelled_orders) * 100), 2
                ) if (total_orders + cancelled_orders) > 0 else 0
            }
    
    async def get_sales_by_day(self, days: int = 30) -> List[Dict]:
        """Продажи по дням."""
        from_date = datetime.now() - timedelta(days=days)
        
        async with db_manager.session() as session:
            result = await session.execute(
                select(
                    func.date(Posting.created_at).label("date"),
                    func.count(Posting.id).label("orders_count"),
                    func.sum(Posting.total_price).label("revenue"),
                    func.avg(Posting.total_price).label("avg_order_value")
                )
                .where(
                    and_(
                        Posting.created_at >= from_date,
                        Posting.status.notin_(["cancelled"])
                    )
                )
                .group_by(func.date(Posting.created_at))
                .order_by(asc("date"))
            )
            
            rows = result.all()
            return [
                {
                    "date": row.date.isoformat() if row.date else None,
                    "orders_count": row.orders_count or 0,
                    "revenue": float(row.revenue or 0),
                    "avg_order_value": float(row.avg_order_value or 0)
                }
                for row in rows
            ]
    
    async def get_sales_by_delivery_schema(self, days: int = 30) -> List[Dict]:
        """Продажи по схемам доставки (FBO/FBS)."""
        from_date = datetime.now() - timedelta(days=days)
        
        async with db_manager.session() as session:
            result = await session.execute(
                select(
                    Posting.delivery_schema,
                    func.count(Posting.id).label("orders_count"),
                    func.sum(Posting.total_price).label("revenue")
                )
                .where(
                    and_(
                        Posting.created_at >= from_date,
                        Posting.status.notin_(["cancelled"])
                    )
                )
                .group_by(Posting.delivery_schema)
            )
            
            rows = result.all()
            return [
                {
                    "delivery_schema": row.delivery_schema or "unknown",
                    "orders_count": row.orders_count or 0,
                    "revenue": float(row.revenue or 0)
                }
                for row in rows
            ]
    
    # ==================== FINANCIAL ANALYTICS ====================
    
    async def get_financial_summary(self, days: int = 30) -> Dict[str, Any]:
        """Финансовая сводка."""
        from_date = datetime.now() - timedelta(days=days)
        
        async with db_manager.session() as session:
            # Доходы
            income = await session.scalar(
                select(func.sum(Transaction.amount))
                .where(
                    and_(
                        Transaction.operation_date >= from_date,
                        Transaction.amount > 0
                    )
                )
            )
            
            # Расходы
            expenses = await session.scalar(
                select(func.sum(Transaction.amount))
                .where(
                    and_(
                        Transaction.operation_date >= from_date,
                        Transaction.amount < 0
                    )
                )
            )
            
            # По типам операций
            result = await session.execute(
                select(
                    Transaction.operation_type,
                    func.sum(Transaction.amount).label("total_amount"),
                    func.count(Transaction.id).label("count")
                )
                .where(Transaction.operation_date >= from_date)
                .group_by(Transaction.operation_type)
            )
            
            by_operation_type = [
                {
                    "operation_type": row.operation_type,
                    "total_amount": float(row.total_amount or 0),
                    "count": row.count
                }
                for row in result.all()
            ]
            
            return {
                "period_days": days,
                "total_income": float(income or 0),
                "total_expenses": float(abs(expenses) if expenses else 0),
                "net_profit": float((income or 0) + (expenses or 0)),
                "by_operation_type": by_operation_type
            }
    
    async def get_commissions_analysis(self, days: int = 30) -> List[Dict]:
        """Анализ комиссий."""
        from_date = datetime.now() - timedelta(days=days)
        
        async with db_manager.session() as session:
            from src.models import PostingFinancial
            
            result = await session.execute(
                select(
                    func.sum(PostingFinancial.commission_amount).label("total_commission"),
                    func.avg(PostingFinancial.commission_percent).label("avg_commission_percent"),
                    func.sum(PostingFinancial.delivery_fee).label("total_delivery_fee"),
                    func.sum(PostingFinancial.service_fee).label("total_service_fee")
                )
                .join(Posting, Posting.id == PostingFinancial.posting_id)
                .where(Posting.created_at >= from_date)
            )
            
            row = result.one()
            return [
                {
                    "metric": "Комиссия Ozon",
                    "amount": float(row.total_commission or 0)
                },
                {
                    "metric": "Средний % комиссии",
                    "amount": float(row.avg_commission_percent or 0)
                },
                {
                    "metric": "Доставка",
                    "amount": float(row.total_delivery_fee or 0)
                },
                {
                    "metric": "Услуги",
                    "amount": float(row.total_service_fee or 0)
                }
            ]
    
    # ==================== RETURNS ANALYTICS ====================
    
    async def get_returns_summary(self, days: int = 30) -> Dict[str, Any]:
        """Сводка по возвратам."""
        from_date = datetime.now() - timedelta(days=days)
        
        async with db_manager.session() as session:
            # Количество возвратов
            total_returns = await session.scalar(
                select(func.count(Return.id))
                .where(Return.returned_at >= from_date)
            )
            
            # Сумма возвратов
            total_refund = await session.scalar(
                select(func.sum(Return.refund_amount))
                .where(Return.returned_at >= from_date)
            )
            
            # По причинам
            result = await session.execute(
                select(
                    Return.return_reason,
                    func.count(Return.id).label("count"),
                    func.sum(Return.refund_amount).label("total_amount")
                )
                .where(Return.returned_at >= from_date)
                .group_by(Return.return_reason)
                .order_by(desc("count"))
            )
            
            by_reason = [
                {
                    "reason": row.return_reason or "Не указана",
                    "count": row.count,
                    "total_amount": float(row.total_amount or 0)
                }
                for row in result.all()
            ]
            
            return {
                "period_days": days,
                "total_returns": total_returns,
                "total_refund_amount": float(total_refund or 0),
                "by_reason": by_reason
            }
    
    # ==================== ADVERTISING ANALYTICS ====================
    
    async def get_advertising_summary(self, days: int = 30) -> Dict[str, Any]:
        """Сводка по рекламе."""
        from_date = datetime.now() - timedelta(days=days)
        
        async with db_manager.session() as session:
            # Общий расход на рекламу
            total_spent = await session.scalar(
                select(func.sum(CampaignStatistic.spent))
                .where(CampaignStatistic.date >= from_date)
            )
            
            # Показы и клики
            result = await session.execute(
                select(
                    func.sum(CampaignStatistic.views).label("total_views"),
                    func.sum(CampaignStatistic.clicks).label("total_clicks"),
                    func.sum(CampaignStatistic.orders).label("total_orders"),
                    func.sum(CampaignStatistic.revenue).label("total_revenue")
                )
                .where(CampaignStatistic.date >= from_date)
            )
            
            row = result.one()
            
            # CTR
            ctr = (row.total_clicks / row.total_views * 100) if row.total_views else 0
            
            # ROAS
            roas = (row.total_revenue / total_spent) if total_spent else 0
            
            return {
                "period_days": days,
                "total_spent": float(total_spent or 0),
                "total_views": row.total_views or 0,
                "total_clicks": row.total_clicks or 0,
                "ctr": round(ctr, 2),
                "total_orders": row.total_orders or 0,
                "total_revenue": float(row.total_revenue or 0),
                "roas": round(roas, 2)
            }
    
    async def get_top_campaigns(self, days: int = 30, limit: int = 10) -> List[Dict]:
        """Топ кампаний по расходу."""
        from_date = datetime.now() - timedelta(days=days)
        
        async with db_manager.session() as session:
            result = await session.execute(
                select(
                    Campaign.campaign_id,
                    Campaign.title,
                    Campaign.adv_object_type,
                    func.sum(CampaignStatistic.spent).label("total_spent"),
                    func.sum(CampaignStatistic.views).label("total_views"),
                    func.sum(CampaignStatistic.clicks).label("total_clicks"),
                    func.sum(CampaignStatistic.orders).label("total_orders"),
                    func.sum(CampaignStatistic.revenue).label("total_revenue")
                )
                .join(CampaignStatistic, CampaignStatistic.campaign_id == Campaign.id)
                .where(CampaignStatistic.date >= from_date)
                .group_by(Campaign.id, Campaign.campaign_id, Campaign.title, Campaign.adv_object_type)
                .order_by(desc("total_spent"))
                .limit(limit)
            )
            
            rows = result.all()
            return [
                {
                    "campaign_id": row.campaign_id,
                    "title": row.title,
                    "type": row.adv_object_type,
                    "spent": float(row.total_spent or 0),
                    "views": row.total_views or 0,
                    "clicks": row.total_clicks or 0,
                    "orders": row.total_orders or 0,
                    "revenue": float(row.total_revenue or 0),
                    "roas": round(
                        (row.total_revenue / row.total_spent), 2
                    ) if row.total_spent else 0
                }
                for row in rows
            ]
    
    # ==================== COMPREHENSIVE REPORT ====================
    
    async def generate_full_report(self, days: int = 30) -> Dict[str, Any]:
        """Генерация полного отчета."""
        logger.info(f"Generating full report for last {days} days...")
        
        report = {
            "generated_at": datetime.now().isoformat(),
            "period_days": days,
            "products": await self.get_products_summary(),
            "sales": await self.get_sales_summary(days),
            "sales_by_day": await self.get_sales_by_day(days),
            "sales_by_schema": await self.get_sales_by_delivery_schema(days),
            "top_products": await self.get_top_products_by_revenue(days),
            "low_stock": await self.get_low_stock_products(10),
            "financial": await self.get_financial_summary(days),
            "returns": await self.get_returns_summary(days),
        }
        
        # Добавляем данные по рекламе если есть
        try:
            report["advertising"] = await self.get_advertising_summary(days)
            report["top_campaigns"] = await self.get_top_campaigns(days)
        except Exception as e:
            logger.warning(f"Could not include advertising data: {e}")
        
        return report
