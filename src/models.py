"""Модели базы данных для Ozon API."""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, BigInteger, String, Numeric, DateTime, Date,
    Boolean, Text, ForeignKey, Index, JSON, Float, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class SyncLog(Base):
    """Лог синхронизации данных."""
    __tablename__ = "sync_logs"
    
    id = Column(Integer, primary_key=True)
    entity_type = Column(String(50), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False)  # running, success, error
    records_processed = Column(Integer, default=0)
    records_inserted = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    """Товары Ozon."""
    __tablename__ = "products"
    
    id = Column(Integer, primary_key=True)
    product_id = Column(BigInteger, unique=True, nullable=False, index=True)
    offer_id = Column(String(255), index=True)
    name = Column(String(1000))
    barcode = Column(String(100))
    category_id = Column(BigInteger)
    type_id = Column(BigInteger)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
    
    # Цены
    price = Column(Numeric(15, 2))
    old_price = Column(Numeric(15, 2))
    retail_price = Column(Numeric(15, 2))
    min_ozon_price = Column(Numeric(15, 2))
    
    # Остатки
    stock_fbo = Column(Integer, default=0)
    stock_fbs = Column(Integer, default=0)
    
    # Статус
    is_visible = Column(Boolean, default=True)
    status = Column(String(50))
    
    # JSON поля для доп. данных
    raw_data = Column(JSON)
    
    # Метаданные
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Отношения
    postings = relationship("PostingItem", back_populates="product")
    stocks_history = relationship("StockHistory", back_populates="product")
    
    __table_args__ = (
        Index('idx_product_name', 'name'),
        Index('idx_product_category', 'category_id'),
    )


class Posting(Base):
    """Отправления (заказы)."""
    __tablename__ = "postings"
    
    id = Column(Integer, primary_key=True)
    posting_number = Column(String(50), unique=True, nullable=False, index=True)
    order_id = Column(BigInteger, index=True)
    order_number = Column(String(50))
    status = Column(String(50), index=True)
    
    # Даты
    created_at = Column(DateTime(timezone=True), index=True)
    in_process_at = Column(DateTime(timezone=True))
    shipment_date = Column(DateTime(timezone=True))
    delivered_at = Column(DateTime(timezone=True))
    
    # Схема доставки
    delivery_schema = Column(String(10), index=True)  # FBO, FBS, rFBS
    
    # Финансы
    total_price = Column(Numeric(15, 2))
    total_discount = Column(Numeric(15, 2))
    
    # Доставка
    tracking_number = Column(String(100))
    delivery_method_name = Column(String(255))
    
    # Адрес доставки
    customer_name = Column(String(255))
    customer_phone = Column(String(50))
    address = Column(Text)
    city = Column(String(255))
    region = Column(String(255))
    
    # JSON данные
    raw_data = Column(JSON)
    
    # Метаданные
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Отношения
    items = relationship("PostingItem", back_populates="posting")
    financial_data = relationship("PostingFinancial", back_populates="posting", uselist=False)


class PostingItem(Base):
    """Товары в отправлении."""
    __tablename__ = "posting_items"
    
    id = Column(Integer, primary_key=True)
    posting_id = Column(Integer, ForeignKey("postings.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), index=True)
    sku = Column(BigInteger, index=True)
    offer_id = Column(String(255))
    
    quantity = Column(Integer, default=1)
    price = Column(Numeric(15, 2))
    discount = Column(Numeric(15, 2))
    
    # Отношения
    posting = relationship("Posting", back_populates="items")
    product = relationship("Product", back_populates="postings")


class PostingFinancial(Base):
    """Финансовые данные по отправлению."""
    __tablename__ = "posting_financials"
    
    id = Column(Integer, primary_key=True)
    posting_id = Column(Integer, ForeignKey("postings.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Комиссии и услуги
    commission_amount = Column(Numeric(15, 2))
    commission_percent = Column(Numeric(5, 2))
    service_fee = Column(Numeric(15, 2))
    delivery_fee = Column(Numeric(15, 2))
    
    # Вознаграждения
    reward = Column(Numeric(15, 2))
    
    # Итоги
    total_payout = Column(Numeric(15, 2))
    
    # Отношения
    posting = relationship("Posting", back_populates="financial_data")


class Transaction(Base):
    """Финансовые транзакции."""
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True)
    transaction_id = Column(BigInteger, unique=True, nullable=False, index=True)
    operation_id = Column(BigInteger, index=True)
    operation_type = Column(String(255), index=True)
    operation_date = Column(DateTime(timezone=True), index=True)
    
    # Связь с отправлением
    posting_number = Column(String(50), index=True)
    
    # Суммы
    amount = Column(Numeric(15, 2))
    currency = Column(String(3))
    
    # Детали
    type = Column(String(100))
    description = Column(Text)
    
    # JSON данные
    raw_data = Column(JSON)
    
    # Метаданные
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_transaction_date_type', 'operation_date', 'operation_type'),
    )


class PostingTransactionSnapshot(Base):
    """Syrie otvety Finance API po konkretnomu posting_number."""
    __tablename__ = "posting_transaction_snapshots"

    id = Column(Integer, primary_key=True)
    posting_number = Column(String(100), nullable=False, index=True)
    date_from = Column(DateTime(timezone=True), nullable=False)
    date_to = Column(DateTime(timezone=True), nullable=False)
    requested_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    row_count = Column(Integer)
    response_json = Column(JSON, nullable=False)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("posting_number", "date_from", "date_to", name="uq_posting_txn_snapshot_window"),
        Index("idx_posting_txn_snapshot_requested", "requested_at"),
    )


class TransactionItem(Base):
    """Normalizovannye tovary iz transactions.raw_data.items."""
    __tablename__ = "transaction_items"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(BigInteger, nullable=False, index=True)
    line_no = Column(Integer, nullable=False)

    posting_number = Column(String(100), index=True)
    operation_date = Column(DateTime(timezone=True), index=True)
    sku = Column(BigInteger, index=True)
    name = Column(String(1000))
    quantity = Column(Integer, default=1)  # Kolvo shtuk
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("transaction_id", "line_no", name="uq_transaction_items_tx_line"),
        Index("idx_transaction_items_posting_sku", "posting_number", "sku"),
    )


class TransactionService(Base):
    """Normalizovannye uslugi iz transactions.raw_data.services."""
    __tablename__ = "transaction_services"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(BigInteger, nullable=False, index=True)
    line_no = Column(Integer, nullable=False)

    posting_number = Column(String(100), index=True)
    operation_date = Column(DateTime(timezone=True), index=True)
    service_name = Column(String(255), index=True)
    price = Column(Numeric(15, 2))
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("transaction_id", "line_no", name="uq_transaction_services_tx_line"),
        Index("idx_transaction_services_name_date", "service_name", "operation_date"),
    )


class StockHistory(Base):
    """История изменения остатков."""
    __tablename__ = "stock_history"
    
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    
    stock_fbo = Column(Integer, default=0)
    stock_fbs = Column(Integer, default=0)
    stock_reserved = Column(Integer, default=0)
    
    # Отношения
    product = relationship("Product", back_populates="stocks_history")
    
    __table_args__ = (
        Index('idx_stock_history_product_date', 'product_id', 'date'),
    )


class Campaign(Base):
    """Рекламные кампании (Performance API)."""
    __tablename__ = "campaigns"
    
    id = Column(Integer, primary_key=True)
    campaign_id = Column(BigInteger, unique=True, nullable=False, index=True)
    title = Column(String(500))
    state = Column(String(50), index=True)
    
    # Тип кампании
    adv_object_type = Column(String(50), index=True)  # SKU, SEARCH_PROMO, etc.
    
    # Бюджет
    daily_budget = Column(Numeric(15, 2))
    total_budget = Column(Numeric(15, 2))
    
    # Даты
    created_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))
    
    # JSON данные
    raw_data = Column(JSON)
    
    # Метаданные
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Отношения
    statistics = relationship("CampaignStatistic", back_populates="campaign")


class CampaignStatistic(Base):
    """Статистика рекламных кампаний."""
    __tablename__ = "campaign_statistics"
    
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    
    # Показы и клики
    views = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    ctr = Column(Numeric(5, 4))
    
    # Расходы
    spent = Column(Numeric(15, 2))
    avg_bid = Column(Numeric(15, 2))
    cpc = Column(Numeric(15, 2))  # Cost per click
    
    # Корзина
    adds_to_cart = Column(Integer, default=0)

    # Конверсия
    orders = Column(Integer, default=0)
    revenue = Column(Numeric(15, 2))
    
    # ROI метрики
    roas = Column(Numeric(10, 2))  # Return on Ad Spend
    acos = Column(Numeric(5, 4))   # Advertising Cost of Sales
    
    # SKU статистика (для кампаний типа SKU)
    sku = Column(BigInteger, index=True)
    
    # JSON данные
    raw_data = Column(JSON)
    
    # Отношения
    campaign = relationship("Campaign", back_populates="statistics")
    
    __table_args__ = (
        Index('idx_campaign_stat_campaign_date', 'campaign_id', 'date'),
    )


class Return(Base):
    """Возвраты товаров."""
    __tablename__ = "returns"
    
    id = Column(Integer, primary_key=True)
    return_id = Column(BigInteger, unique=True, nullable=False, index=True)
    posting_number = Column(String(50), index=True)
    
    # Товар
    sku = Column(BigInteger, index=True)
    offer_id = Column(String(255))
    product_name = Column(String(1000))
    
    # Детали возврата
    quantity = Column(Integer)
    return_reason = Column(Text)
    
    # Статус и даты
    status = Column(String(50), index=True)
    returned_at = Column(DateTime(timezone=True), index=True)
    
    # Финансы
    refund_amount = Column(Numeric(15, 2))
    
    # JSON данные
    raw_data = Column(JSON)
    
    # Метаданные
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class AnalyticsData(Base):
    """Данные аналитики Ozon."""
    __tablename__ = "analytics_data"

    id = Column(Integer, primary_key=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    sku = Column(BigInteger, nullable=False, index=True)

    # Продажи и выручка
    ordered_units = Column(Integer, default=0)
    delivered_units = Column(Integer, default=0)
    returned_units = Column(Integer, default=0)
    revenue = Column(Numeric(15, 2))

    # Трафик и позиции
    impressions = Column(Integer)
    clicks = Column(Integer)
    ctr = Column(Numeric(8, 4))
    position = Column(Float)
    position_category = Column(Float)
    position_promo = Column(Float)

    # Гибкие поля для неизвестных метрик/измерений
    metric_values = Column(JSON)
    dimensions = Column(JSON)
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("date", "sku", name="uq_analytics_data_date_sku"),
        Index('idx_analytics_date_sku', 'date', 'sku'),
    )


class AnalyticsProductQuerySummary(Base):
    """Агрегированная аналитика поисковых запросов по SKU за период."""
    __tablename__ = "analytics_product_query_summary"

    id = Column(Integer, primary_key=True)
    period_start = Column(DateTime(timezone=True), nullable=False, index=True)
    period_end = Column(DateTime(timezone=True), nullable=False, index=True)
    granularity = Column(String(16), nullable=False, default="day", index=True)

    sku = Column(BigInteger, nullable=False, index=True)
    offer_id = Column(String(255), index=True)
    product_name = Column(String(1000))

    searches = Column(Integer)
    views = Column(Integer)
    avg_position = Column(Float)
    conversion = Column(Numeric(10, 4))
    gmv = Column(Numeric(15, 2))

    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "period_start",
            "period_end",
            "granularity",
            "sku",
            name="uq_apq_summary_period_granularity_sku",
        ),
        Index("idx_apq_summary_sku_period", "sku", "period_start"),
    )


class AnalyticsProductQueryDetail(Base):
    """Детализация поисковых запросов по SKU за период."""
    __tablename__ = "analytics_product_query_details"

    id = Column(Integer, primary_key=True)
    period_start = Column(DateTime(timezone=True), nullable=False, index=True)
    period_end = Column(DateTime(timezone=True), nullable=False, index=True)
    granularity = Column(String(16), nullable=False, default="day", index=True)

    sku = Column(BigInteger, nullable=False, index=True)
    offer_id = Column(String(255), index=True)
    product_name = Column(String(1000))
    query_text = Column(String(1000), nullable=False)

    searches = Column(Integer)
    views = Column(Integer)
    avg_position = Column(Float)
    conversion = Column(Numeric(10, 4))
    gmv = Column(Numeric(15, 2))

    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "period_start",
            "period_end",
            "granularity",
            "sku",
            "query_text",
            name="uq_apq_details_period_granularity_sku_query",
        ),
        Index("idx_apq_details_sku_period", "sku", "period_start"),
        Index("idx_apq_details_query", "query_text"),
    )


# ==================== NOVYE MODELLI ====================

class CampaignDetail(Base):
    """Detali reklamnyh kampanij."""
    __tablename__ = "campaign_details"
    
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    
    # Parametry kampanii
    budget = Column(Numeric(15, 2))
    daily_budget = Column(Numeric(15, 2))
    start_date = Column(DateTime(timezone=True))
    end_date = Column(DateTime(timezone=True))
    schedule = Column(JSON)  # Raspisanie pokaza
    targeting = Column(JSON)  # Celеваja audiencija
    
    # Ob#ekty kampanii (SKU, kategorii, poiskovye frazy)
    objects = Column(JSON)
    
    # JSON dannye
    raw_data = Column(JSON)
    
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class CampaignObject(Base):
    """Ob#ekty reklamnyh kampanij (SKU v kampanii)."""
    __tablename__ = "campaign_objects"
    
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    
    sku = Column(BigInteger, nullable=False, index=True)
    bid = Column(Numeric(15, 2))  # Stavka
    status = Column(String(50))  # ACTIVE, PAUSED, etc.
    
    # Statistika po ob#ektu
    views = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    spent = Column(Numeric(15, 2))
    orders = Column(Integer, default=0)
    revenue = Column(Numeric(15, 2))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_campaign_object_sku', 'campaign_id', 'sku'),
    )


class PostingFBO(Base):
    """Otpravlenija FBO (Fulfillment by Ozon)."""
    __tablename__ = "postings_fbo"
    
    id = Column(Integer, primary_key=True)
    posting_number = Column(String(50), unique=True, nullable=False, index=True)
    order_id = Column(BigInteger, index=True)
    status = Column(String(50), index=True)
    
    # Daty
    created_at = Column(DateTime(timezone=True), index=True)
    in_process_at = Column(DateTime(timezone=True))
    shipment_date = Column(DateTime(timezone=True))
    delivered_at = Column(DateTime(timezone=True))
    
    # Finansy
    total_price = Column(Numeric(15, 2))
    total_discount = Column(Numeric(15, 2))
    
    # Dostavka
    warehouse_id = Column(BigInteger, index=True)
    warehouse_name = Column(String(255))
    
    # Tovary (JSON dlja prosty)
    items = Column(JSON)
    raw_data = Column(JSON)
    
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class ReturnFBO(Base):
    """Vozvraty FBO."""
    __tablename__ = "returns_fbo"
    
    id = Column(Integer, primary_key=True)
    return_id = Column(BigInteger, unique=True, nullable=False, index=True)
    posting_number = Column(String(50), index=True)
    
    sku = Column(BigInteger, index=True)
    offer_id = Column(String(255))
    product_name = Column(String(1000))
    quantity = Column(Integer)
    
    status = Column(String(50), index=True)
    return_reason = Column(Text)
    returned_at = Column(DateTime(timezone=True), index=True)
    
    refund_amount = Column(Numeric(15, 2))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class ReturnRFBS(Base):
    """Vozvraty rFBS (real FBS)."""
    __tablename__ = "returns_rfbs"
    
    id = Column(Integer, primary_key=True)
    return_id = Column(BigInteger, unique=True, nullable=False, index=True)
    posting_number = Column(String(50), index=True)
    
    sku = Column(BigInteger, index=True)
    offer_id = Column(String(255))
    product_name = Column(String(1000))
    quantity = Column(Integer)
    
    # Statusy vozvrata
    status = Column(String(50), index=True)  # CREATED, APPROVED, etc.
    substatus = Column(String(50))
    
    # Prichiny
    return_reason = Column(Text)
    return_type = Column(String(50))  # CLIENT_RETURN, etc.
    
    # Finansy
    refund_amount = Column(Numeric(15, 2))
    compensation_amount = Column(Numeric(15, 2))
    
    # Daty
    created_at = Column(DateTime(timezone=True))
    approved_at = Column(DateTime(timezone=True))
    received_at = Column(DateTime(timezone=True))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class FinanceTransactionTotal(Base):
    """Itogi po finansovym transakcijam."""
    __tablename__ = "finance_transaction_totals"
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    
    # Tipy operacij
    operation_type = Column(String(50), nullable=False, index=True)
    
    # Summy
    total_amount = Column(Numeric(15, 2))
    count = Column(Integer, default=0)
    
    # Valjuta
    currency = Column(String(3))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_finance_total_date_type', 'date', 'operation_type'),
    )


class FinanceBalance(Base):
    """Balans prodavca za period."""
    __tablename__ = "finance_balances"

    id = Column(Integer, primary_key=True)
    period_from = Column(DateTime(timezone=True), nullable=False, index=True)
    period_to = Column(DateTime(timezone=True), nullable=False, index=True)

    opening_balance = Column(Numeric(15, 2))
    closing_balance = Column(Numeric(15, 2))
    accrued_amount = Column(Numeric(15, 2))
    payment_amount = Column(Numeric(15, 2))
    currency = Column(String(3))

    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("period_from", "period_to", name="uq_finance_balances_period"),
        Index("idx_finance_balances_period", "period_from", "period_to"),
    )


class CashFlowStatement(Base):
    """Finansovyj otchet (Cash Flow)."""
    __tablename__ = "cash_flow_statements"
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    
    # Dohody i rashody
    revenue = Column(Numeric(15, 2))
    commission = Column(Numeric(15, 2))
    delivery_cost = Column(Numeric(15, 2))
    return_cost = Column(Numeric(15, 2))
    other_costs = Column(Numeric(15, 2))
    
    # Itog
    net_amount = Column(Numeric(15, 2))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class MutualSettlement(Base):
    """Vzaimoraschety s Ozon."""
    __tablename__ = "mutual_settlements"
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    
    # Nachislenija i vyplaty
    accrual_amount = Column(Numeric(15, 2))
    payment_amount = Column(Numeric(15, 2))
    
    # Saldo
    opening_balance = Column(Numeric(15, 2))
    closing_balance = Column(Numeric(15, 2))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class B2BSale(Base):
    """Prodazhi jurlicam (B2B)."""
    __tablename__ = "b2b_sales"
    
    id = Column(Integer, primary_key=True)
    posting_number = Column(String(50), index=True)
    order_date = Column(DateTime(timezone=True), index=True)
    
    # Pokupatel'
    company_name = Column(String(500))
    inn = Column(String(20))
    
    # Tovary
    sku = Column(BigInteger, index=True)
    quantity = Column(Integer)
    price = Column(Numeric(15, 2))
    total_amount = Column(Numeric(15, 2))
    
    # Dokumenty
    invoice_number = Column(String(50))
    invoice_date = Column(DateTime(timezone=True))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class AnalyticsTurnover(Base):
    """Oborachivaemost' zapasov."""
    __tablename__ = "analytics_turnover"
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    sku = Column(BigInteger, nullable=False, index=True)
    
    # Ostatki
    stock = Column(Integer, default=0)
    
    # Prodazhi
    sales_speed = Column(Numeric(10, 4))  # Skorost' prodazh v den'
    days_in_stock = Column(Integer)  # Dni zapasa
    
    # Rekomendacii
    recommended_stock = Column(Integer)
    recommended_supply = Column(Integer)
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_turnover_date_sku', 'date', 'sku'),
    )


class AnalyticsAverageDeliveryTime(Base):
    """Legacy table for average delivery time; source endpoints are obsolete for many accounts."""
    __tablename__ = "analytics_average_delivery_time"

    id = Column(Integer, primary_key=True)
    delivery_cluster_id = Column(BigInteger, nullable=False, unique=True, index=True)

    average_delivery_time = Column(Numeric(10, 2))
    average_delivery_time_status = Column(String(50))
    lost_profit = Column(Numeric(15, 2))
    exact_impact_share = Column(Numeric(10, 4))
    attention_level = Column(String(50))
    recommended_supply = Column(Integer)

    orders_total = Column(Integer)
    orders_fast = Column(Integer)
    orders_fast_percent = Column(Numeric(10, 4))
    orders_medium = Column(Integer)
    orders_medium_percent = Column(Numeric(10, 4))
    orders_long = Column(Integer)
    orders_long_percent = Column(Numeric(10, 4))

    clusters_data = Column(JSON)
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class AnalyticsStockManagement(Base):
    """Upravlenie ostatkami."""
    __tablename__ = "analytics_stock_management"
    
    id = Column(Integer, primary_key=True)
    sku = Column(BigInteger, nullable=False, index=True)
    warehouse_id = Column(BigInteger, nullable=False, index=True)
    
    # Tekushhie ostatki
    stock = Column(Integer, default=0)
    reserved = Column(Integer, default=0)
    available = Column(Integer, default=0)
    
    # Planirovanie
    min_stock = Column(Integer)
    max_stock = Column(Integer)
    reorder_point = Column(Integer)
    
    # Status
    stock_status = Column(String(50))  # IN_STOCK, OUT_OF_STOCK, etc.
    
    updated_at = Column(DateTime(timezone=True))
    raw_data = Column(JSON)
    
    __table_args__ = (
        Index('idx_stock_mgmt_sku_warehouse', 'sku', 'warehouse_id'),
    )


class AnalyticsStock(Base):
    """Srez po ostatkam i oborachivaemosti iz /v1/analytics/stocks."""
    __tablename__ = "analytics_stocks"

    id = Column(Integer, primary_key=True)
    sku = Column(BigInteger, nullable=False, index=True)
    offer_id = Column(String(255), index=True)
    name = Column(String(1000))

    warehouse_id = Column(BigInteger, nullable=False, index=True)
    warehouse_name = Column(String(255))
    cluster_id = Column(BigInteger, index=True)
    cluster_name = Column(String(255))
    macrolocal_cluster_id = Column(BigInteger, index=True)

    ads = Column(Numeric(10, 4))
    idc = Column(Numeric(10, 4))
    days_without_sales = Column(Integer)
    turnover_grade = Column(String(50))

    available_stock_count = Column(Integer)
    valid_stock_count = Column(Integer)
    waiting_docs_stock_count = Column(Integer)
    expiring_stock_count = Column(Integer)
    transit_defect_stock_count = Column(Integer)
    stock_defect_stock_count = Column(Integer)
    excess_stock_count = Column(Integer)
    other_stock_count = Column(Integer)
    requested_stock_count = Column(Integer)
    transit_stock_count = Column(Integer)
    return_from_customer_stock_count = Column(Integer)
    return_to_seller_stock_count = Column(Integer)

    ads_cluster = Column(Numeric(10, 4))
    idc_cluster = Column(Numeric(10, 4))
    days_without_sales_cluster = Column(Integer)
    turnover_grade_cluster = Column(String(50))

    item_tags = Column(JSON)
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint("sku", "warehouse_id", "cluster_id", name="uq_analytics_stocks_sku_wh_cluster"),
        Index("idx_analytics_stocks_offer_wh", "offer_id", "warehouse_id"),
    )


class StockDailySnapshot(Base):
    """Ежедневный снапшот остатков (FBO + FBS) для накопления истории по дням."""
    __tablename__ = "stock_daily_snapshots"

    id = Column(Integer, primary_key=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    snapshot_at = Column(DateTime(timezone=True), server_default=func.now())

    sku = Column(BigInteger, nullable=False, index=True)
    offer_id = Column(String(255), index=True)
    stock_type = Column(String(8), nullable=False)  # 'FBO' | 'FBS'

    warehouse_id = Column(BigInteger, nullable=False)
    warehouse_name = Column(String(255))
    cluster_id = Column(BigInteger, index=True)
    cluster_name = Column(String(255))

    stock_total = Column(Integer, default=0)
    stock_available = Column(Integer, default=0)
    stock_supply = Column(Integer, default=0)         # requested (на поставке) — только FBO
    stock_transit = Column(Integer, default=0)        # transit — только FBO
    stock_acceptance = Column(Integer, default=0)     # waiting_docs — только FBO
    stock_reserved = Column(Integer, default=0)       # reserved — только FBS

    source_table = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "snapshot_date", "stock_type", "sku", "warehouse_id", "cluster_id",
            name="uq_stock_daily_snapshots_key",
        ),
        Index("idx_stock_daily_snapshots_sku_date", "sku", "snapshot_date"),
    )


class DeliveryTimeDailySnapshot(Base):
    """Ежедневный снапшот среднего времени доставки по кластерам."""
    __tablename__ = "delivery_time_daily_snapshots"

    id = Column(Integer, primary_key=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    snapshot_at = Column(DateTime(timezone=True), server_default=func.now())

    delivery_cluster_id = Column(BigInteger, nullable=False, index=True)
    average_delivery_time = Column(Numeric(10, 2))
    average_delivery_time_status = Column(String(50))
    orders_total = Column(Integer)
    orders_fast = Column(Integer)
    orders_medium = Column(Integer)
    orders_long = Column(Integer)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "snapshot_date", "delivery_cluster_id",
            name="uq_delivery_time_daily_snapshots_key",
        ),
    )


class Warehouse(Base):
    """Sklady Ozon."""
    __tablename__ = "warehouses"
    
    id = Column(Integer, primary_key=True)
    warehouse_id = Column(BigInteger, unique=True, nullable=False, index=True)
    name = Column(String(255))
    
    # Tip sklada
    warehouse_type = Column(String(50))  # FBO, FBS, etc.
    
    # Adres
    region = Column(String(255))
    city = Column(String(255))
    address = Column(Text)
    
    # Status
    is_active = Column(Boolean, default=True)
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class Cluster(Base):
    """Klastery dostavki."""
    __tablename__ = "clusters"
    
    id = Column(Integer, primary_key=True)
    cluster_id = Column(BigInteger, unique=True, nullable=False, index=True)
    name = Column(String(255))
    
    # Regiony pokrytija
    regions = Column(JSON)
    
    # Sklady v klastere
    warehouses = Column(JSON)
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class DeliveryMethod(Base):
    """Metody dostavki."""
    __tablename__ = "delivery_methods"
    
    id = Column(Integer, primary_key=True)
    delivery_method_id = Column(BigInteger, unique=True, nullable=False, index=True)
    name = Column(String(255))
    
    # Tipy dostavki
    delivery_type = Column(String(50))  # COURIER, PICKUP, etc.
    warehouse_id = Column(BigInteger, index=True)
    
    # Status
    is_active = Column(Boolean, default=True)
    
    # Stoimost'
    base_price = Column(Numeric(15, 2))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class SellerRating(Base):
    """Rejting prodavca."""
    __tablename__ = "seller_ratings"
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime(timezone=True), nullable=False, unique=True, index=True)
    
    # Obshhij reiting
    overall_rating = Column(Numeric(3, 2))
    position_in_category = Column(Integer)
    
    # Pokazateli
    price_quality_rating = Column(Numeric(3, 2))
    delivery_rating = Column(Numeric(3, 2))
    service_rating = Column(Numeric(3, 2))
    
    # Metriki
    cancellation_rate = Column(Numeric(5, 4))
    late_shipment_rate = Column(Numeric(5, 4))
    return_rate = Column(Numeric(5, 4))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class SellerRatingHistory(Base):
    """Istorija izmenenija reitinga."""
    __tablename__ = "seller_rating_history"
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    
    rating = Column(Numeric(3, 2))
    change = Column(Numeric(3, 2))  # Izmenenie za den'
    
    # Prichiny izmenenija
    reason = Column(Text)
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class Review(Base):
    """Otzyvy pokupatelej."""
    __tablename__ = "reviews"
    
    id = Column(Integer, primary_key=True)
    review_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Tovar
    sku = Column(BigInteger, nullable=False, index=True)
    offer_id = Column(String(255))
    
    # Ocenka
    rating = Column(Integer)  # 1-5
    text = Column(Text)
    
    # Status
    status = Column(String(50))  # PUBLISHED, UNPROCESSED, etc.
    is_buyer = Column(Boolean, default=False)  # Pokupal li tovar
    
    # Daty
    published_at = Column(DateTime(timezone=True), index=True)
    created_at = Column(DateTime(timezone=True))
    
    # Statistika
    helpful_count = Column(Integer, default=0)
    unhelpful_count = Column(Integer, default=0)
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    # Otnohenija
    comments = relationship("ReviewComment", back_populates="review")


class ReviewRatingSnapshot(Base):
    """Snimok srednej ocenki po sku — odin v sutki."""
    __tablename__ = "review_rating_snapshots"

    id = Column(Integer, primary_key=True)
    sku = Column(BigInteger, nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    avg_rating = Column(Numeric(4, 3))
    reviews_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("sku", "snapshot_date", name="uq_rating_snapshot_sku_date"),
    )


class ReviewComment(Base):
    """Kommentarii k otzyvam (otvety prodavca)."""
    __tablename__ = "review_comments"
    
    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False)
    comment_id = Column(BigInteger, unique=True, nullable=False)
    
    # Soderzhanie
    text = Column(Text)
    
    # Avtor (prodavec)
    author_name = Column(String(255))
    
    # Daty
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Otnohenija
    review = relationship("Review", back_populates="comments")


class Question(Base):
    """Voprosy pokupatelej."""
    __tablename__ = "questions"
    
    id = Column(Integer, primary_key=True)
    question_id = Column(BigInteger, unique=True, nullable=False, index=True)
    
    # Tovar
    sku = Column(BigInteger, nullable=False, index=True)
    offer_id = Column(String(255))
    
    # Vopros
    text = Column(Text)
    
    # Status
    status = Column(String(50))  # ANSWERED, NOT_ANSWERED, etc.
    
    # Daty
    created_at = Column(DateTime(timezone=True), index=True)
    answer_deadline = Column(DateTime(timezone=True))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Otnohenija
    answers = relationship("QuestionAnswer", back_populates="question")


class QuestionAnswer(Base):
    """Otvety na voprosy."""
    __tablename__ = "question_answers"
    
    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    answer_id = Column(BigInteger, unique=True, nullable=False)
    
    # Soderzhanie
    text = Column(Text)
    
    # Avtor
    is_seller_answer = Column(Boolean, default=False)
    author_name = Column(String(255))
    
    # Daty
    created_at = Column(DateTime(timezone=True))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Otnohenija
    question = relationship("Question", back_populates="answers")


class ChatThread(Base):
    """Chaty s pokupateljami."""
    __tablename__ = "chat_threads"
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(String(100), unique=True, nullable=False, index=True)
    
    # Zakaz
    posting_number = Column(String(50), index=True)
    
    # Status
    status = Column(String(50))  # OPEN, CLOSED, etc.
    unread_count = Column(Integer, default=0)
    
    # Poslednee soobshhenie
    last_message_at = Column(DateTime(timezone=True))
    last_message_text = Column(Text)
    
    # SLA
    first_response_sla = Column(DateTime(timezone=True))  # Deadline pervogo otveta
    response_sla = Column(DateTime(timezone=True))  # Deadline otveta
    
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Otnohenija
    messages = relationship("ChatMessage", back_populates="thread")


class ChatMessage(Base):
    """Soobshhenija v chat."""
    __tablename__ = "chat_messages"
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(String(100), unique=True, nullable=False)
    
    # Soderzhanie
    text = Column(Text)
    message_type = Column(String(50))  # TEXT, IMAGE, etc.
    
    # Otpravitel'
    is_from_seller = Column(Boolean, default=False)
    sender_name = Column(String(255))
    
    # Status
    is_read = Column(Boolean, default=False)
    
    # Daty
    created_at = Column(DateTime(timezone=True), index=True)
    read_at = Column(DateTime(timezone=True))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Otnohenija
    thread = relationship("ChatThread", back_populates="messages")


class PromoAction(Base):
    """Akcii i promokampanii Ozon."""
    __tablename__ = "promo_actions"
    
    id = Column(Integer, primary_key=True)
    action_id = Column(BigInteger, unique=True, nullable=False, index=True)
    title = Column(String(500))
    
    # Tip akcii
    action_type = Column(String(50))  # DISCOUNT, SPECIAL, etc.
    
    # Daty provedenija
    date_start = Column(DateTime(timezone=True))
    date_end = Column(DateTime(timezone=True))
    
    # Status
    status = Column(String(50))  # RUNNING, COMPLETED, etc.
    is_participating = Column(Boolean, default=False)
    
    # Uslovija
    discount_percent = Column(Numeric(5, 2))
    max_quantity = Column(Integer)

    # Dopolnitelnye polja iz API /v1/actions
    discount_type = Column(String(50))            # PERCENT / CURRENCY
    discount_value = Column(Numeric(15, 2))       # Raw znachenie skidki (% ili rubli)
    potential_products_count = Column(Integer)
    participating_products_count = Column(Integer)
    banned_products_count = Column(Integer)
    description = Column(Text)
    with_targeting = Column(Boolean, default=False)
    is_voucher_action = Column(Boolean, default=False)
    order_amount = Column(Numeric(15, 2))
    freeze_date = Column(String(50))

    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    # Otnohenija
    products = relationship("PromoProduct", back_populates="action")


class PromoProduct(Base):
    """Tovary v akcijah."""
    __tablename__ = "promo_products"
    
    id = Column(Integer, primary_key=True)
    action_id = Column(Integer, ForeignKey("promo_actions.id", ondelete="CASCADE"), nullable=False)
    
    sku = Column(BigInteger, nullable=False, index=True)
    offer_id = Column(String(255))
    
    # Ceny v akcii
    regular_price = Column(Numeric(15, 2))
    action_price = Column(Numeric(15, 2))
    discount_percent = Column(Numeric(5, 2))
    
    # Status uchastija
    is_participating = Column(Boolean, default=False)
    is_candidate = Column(Boolean, default=False)
    
    # Metriki akcii
    orders_count = Column(Integer, default=0)
    revenue = Column(Numeric(15, 2))

    # Dopolnitelnye polja iz API /v1/actions/products i /v1/actions/candidates
    max_action_price = Column(Numeric(15, 2))
    add_mode = Column(String(50))                # NOT_SET / MANUAL / AUTO
    stock = Column(Integer)                      # ostatok na moment proverki
    min_stock = Column(Integer)                  # min ostatok dlja uchastija
    current_boost = Column(Numeric(10, 2))
    min_boost = Column(Numeric(10, 2))
    max_boost = Column(Numeric(10, 2))
    price_min_elastic = Column(Numeric(15, 2))
    price_max_elastic = Column(Numeric(15, 2))

    raw_data = Column(JSON)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    # Otnohenija
    action = relationship("PromoAction", back_populates="products")


class PromoProductEvent(Base):
    """Istorija dobavlenija/udalenija tovarov v akcijah."""
    __tablename__ = "promo_product_events"

    id = Column(Integer, primary_key=True)
    action_id = Column(Integer, nullable=False, index=True)       # ozon action_id (external)
    sku = Column(BigInteger, nullable=False, index=True)           # ozon product_id / sku
    event_type = Column(String(20), nullable=False)                # ADDED | REMOVED
    source = Column(String(20), nullable=False, default="sync")    # manual | sync
    detected_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_promo_events_action_sku", "action_id", "sku"),
    )


class AsyncReport(Base):
    """Asinhronnye otchety Ozon."""
    __tablename__ = "async_reports"
    
    id = Column(Integer, primary_key=True)
    report_id = Column(BigInteger, unique=True, nullable=False, index=True)
    
    # Tip otcheta
    report_type = Column(String(50))  # PRODUCTS, POSTINGS, RETURNS, etc.
    
    # Status
    status = Column(String(50))  # PENDING, PROCESSING, SUCCESS, ERROR
    
    # Parametry
    date_from = Column(DateTime(timezone=True))
    date_to = Column(DateTime(timezone=True))
    filters = Column(JSON)
    
    # Rezul'tat
    file_url = Column(String(1000))
    file_size = Column(BigInteger)
    row_count = Column(Integer)
    
    # Daty
    created_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class RealizationReport(Base):
    """Otchet o realizacii (vypiska)."""
    __tablename__ = "realization_reports"
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    
    # Tovar
    sku = Column(BigInteger, nullable=False, index=True)
    offer_id = Column(String(255))
    name = Column(String(1000))
    
    # Prodazhi
    quantity = Column(Integer, default=0)
    price = Column(Numeric(15, 2))
    total_amount = Column(Numeric(15, 2))
    
    # Komissii i vyplaty
    commission_percent = Column(Numeric(5, 2))
    commission_amount = Column(Numeric(15, 2))
    payout_amount = Column(Numeric(15, 2))
    
    # Dostavka
    delivery_cost = Column(Numeric(15, 2))
    
    # Itog
    total_payout = Column(Numeric(15, 2))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_realization_date_sku', 'date', 'sku'),
    )


class RealizationReportDetail(Base):
    """Normalizovannye detali row/header iz realization_reports.raw_data."""
    __tablename__ = "realization_report_details"

    id = Column(Integer, primary_key=True)
    realization_report_id = Column(Integer, ForeignKey("realization_reports.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    document_number = Column(String(50), index=True)
    document_date = Column(DateTime(timezone=True))
    period_start = Column(DateTime(timezone=True))
    period_end = Column(DateTime(timezone=True))
    contract_number = Column(String(100))

    row_number = Column(Integer)
    barcode = Column(String(255))
    delivery_quantity = Column(Integer)
    delivery_amount = Column(Numeric(15, 2))
    delivery_bonus = Column(Numeric(15, 2))
    delivery_standard_fee = Column(Numeric(15, 2))
    delivery_total = Column(Numeric(15, 2))
    bank_coinvestment = Column(Numeric(15, 2))
    pick_up_point_coinvestment = Column(Numeric(15, 2))
    stars = Column(Numeric(15, 2))
    return_commission_amount = Column(Numeric(15, 2))
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())


class FactOrderItem(Base):
    """Normalizovannye tovary iz fact_orders.items."""
    __tablename__ = "fact_order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(String(50), nullable=False, index=True)
    posting_number = Column(String(50), index=True)
    line_no = Column(Integer, nullable=False)

    offer_id = Column(String(255), index=True)
    sku = Column(BigInteger, index=True)
    product_name = Column(String(1000))
    quantity = Column(Numeric(15, 2))
    price = Column(Numeric(15, 2))
    buyer_paid = Column(Numeric(15, 2))
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("order_id", "line_no", name="uq_fact_order_items_order_line"),
        Index("idx_fact_order_items_offer_sku", "offer_id", "sku"),
    )


class ReportProductItem(Base):
    """Stroki CSV otcheta po tovaram (/v1/report/products/create)."""
    __tablename__ = "report_products_items"

    id = Column(Integer, primary_key=True)
    report_id = Column(BigInteger, nullable=False, index=True)
    line_no = Column(Integer, nullable=False)

    # Bazovye identifikatory i opisanie
    offer_id = Column(String(255), index=True)
    product_name = Column(String(1000))
    ozon_product_id = Column(BigInteger, index=True)
    fbo_sku_id = Column(BigInteger, index=True)
    fbs_sku_id = Column(BigInteger, index=True)
    crossborder_sku = Column(String(255))
    barcode = Column(String(255))

    # Status/vidimost'
    product_status = Column(String(255), index=True)

    # Ostatki i ceny
    stock_fbo_available = Column(Integer)
    stock_reserved = Column(Integer)
    price_current = Column(Numeric(15, 2))
    price_base = Column(Numeric(15, 2))
    price_premium = Column(Numeric(15, 2))
    price_recommended = Column(Numeric(15, 2))
    recommended_price_link = Column(String(1000))

    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("report_id", "line_no", name="uq_report_products_items_report_line"),
        Index("idx_report_products_offer_status", "offer_id", "product_status"),
    )


class ProductPriceDetail(Base):
    """Detailed Ozon site prices from /v1/product/prices/details."""
    __tablename__ = "product_price_details"

    id = Column(Integer, primary_key=True)
    sku = Column(BigInteger, nullable=False, unique=True, index=True)
    offer_id = Column(String(255), index=True)
    customer_price = Column(Numeric(15, 2))
    price = Column(Numeric(15, 2))
    price_indexes = Column(JSON)
    details_status = Column(String(50), nullable=False, default="ok")
    error_message = Column(Text)
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_product_price_details_offer", "offer_id"),
        Index("idx_product_price_details_synced", "last_synced_at"),
    )


class ReportReturnItem(Base):
    """Stroki CSV otcheta po vozvratam (/v2/report/returns/create)."""
    __tablename__ = "report_returns_items"

    id = Column(Integer, primary_key=True)
    report_id = Column(BigInteger, nullable=False, index=True)
    line_no = Column(Integer, nullable=False)

    return_id = Column(BigInteger, index=True)
    posting_number = Column(String(100), index=True)
    order_id = Column(String(50), index=True)
    delivery_schema = Column(String(10), index=True)  # FBO/FBS
    status = Column(String(100), index=True)

    offer_id = Column(String(255), index=True)
    sku = Column(BigInteger, index=True)
    product_name = Column(String(1000))
    quantity = Column(Integer)
    refund_amount = Column(Numeric(15, 2))

    returned_at = Column(DateTime(timezone=True), index=True)

    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("report_id", "line_no", name="uq_report_returns_items_report_line"),
        Index("idx_report_returns_offer_schema", "offer_id", "delivery_schema"),
    )


class ReportWarehouseStockItem(Base):
    """Stroki XLSX otcheta po skladskim ostatkam (/v1/report/warehouse/stock)."""
    __tablename__ = "report_warehouse_stock_items"

    id = Column(Integer, primary_key=True)
    report_id = Column(BigInteger, nullable=False, index=True)
    line_no = Column(Integer, nullable=False)

    warehouse_id = Column(BigInteger, index=True)
    warehouse_name = Column(String(255))

    offer_id = Column(String(255), index=True)
    sku = Column(BigInteger, index=True)
    product_name = Column(String(1000))

    stock_total = Column(Integer)

    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("report_id", "line_no", name="uq_report_warehouse_stock_items_report_line"),
        Index("idx_report_warehouse_stock_offer_wh", "offer_id", "warehouse_id"),
    )


class FBSWarehouseStock(Base):
    """Zhivoj srez FBS-ostatkov po skladam iz /v2/product/info/stocks-by-warehouse/fbs."""
    __tablename__ = "fbs_warehouse_stocks"

    id = Column(Integer, primary_key=True)
    sku = Column(BigInteger, nullable=False, index=True)
    offer_id = Column(String(255), index=True)
    product_id = Column(BigInteger, index=True)

    warehouse_id = Column(BigInteger, nullable=False, index=True)
    warehouse_name = Column(String(255))

    present = Column(Integer)
    reserved = Column(Integer)

    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint("sku", "warehouse_id", name="uq_fbs_warehouse_stocks_sku_wh"),
        Index("idx_fbs_warehouse_stocks_offer_wh", "offer_id", "warehouse_id"),
    )


class ReportCompensationItem(Base):
    """Stroki async-otchetov /v1/finance/compensation i /v1/finance/decompensation."""
    __tablename__ = "report_compensation_items"

    id = Column(Integer, primary_key=True)
    report_id = Column(BigInteger, nullable=False, index=True)
    line_no = Column(Integer, nullable=False)

    report_kind = Column(String(32), nullable=False, index=True)  # compensation/decompensation
    report_month = Column(DateTime(timezone=True), nullable=False, index=True)
    effective_date = Column(DateTime(timezone=True), nullable=False, index=True)

    article_name = Column(String(1000), index=True)
    raw_amount = Column(Numeric(15, 2))
    amount = Column(Numeric(15, 2))  # signed amount for finance report

    posting_number = Column(String(100), index=True)
    order_id = Column(String(100), index=True)
    offer_id = Column(String(255), index=True)
    sku = Column(BigInteger, index=True)
    product_name = Column(String(1000))

    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("report_id", "line_no", name="uq_report_compensation_items_report_line"),
        Index("idx_report_compensation_kind_date", "report_kind", "effective_date"),
    )


class ReportDownloadRetry(Base):
    """Ochered' povtornogo skachivanija problemnyh async-otchetov."""
    __tablename__ = "report_download_retries"

    id = Column(Integer, primary_key=True)
    report_code = Column(String(255), nullable=False, unique=True, index=True)
    report_type = Column(String(100), nullable=False, index=True)
    file_url = Column(String(2000))
    status = Column(String(50), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text)
    raw_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FactOrder(Base):
    """Edinnaja vitrina zakazov (FBS + FBO + rFBS)."""
    __tablename__ = "fact_orders"
    
    id = Column(Integer, primary_key=True)
    order_id = Column(String(50), unique=True, nullable=False, index=True)
    posting_number = Column(String(50), index=True)
    
    # Schema ispolnenija
    delivery_schema = Column(String(10), nullable=False, index=True)  # FBO, FBS, rFBS
    
    # Statusy
    status = Column(String(50), index=True)
    substatus = Column(String(50))
    
    # Daty
    created_at = Column(DateTime(timezone=True), index=True)
    in_process_at = Column(DateTime(timezone=True))
    shipment_date = Column(DateTime(timezone=True))
    delivered_at = Column(DateTime(timezone=True))
    cancelled_at = Column(DateTime(timezone=True))
    
    # Finansy
    items_total = Column(Numeric(15, 2))
    discount_total = Column(Numeric(15, 2))
    delivery_cost = Column(Numeric(15, 2))
    commission_total = Column(Numeric(15, 2))
    payout_total = Column(Numeric(15, 2))
    
    # SLA
    is_on_time = Column(Boolean)
    sla_hours = Column(Integer)
    
    # Vozvraty
    is_returned = Column(Boolean, default=False)
    return_amount = Column(Numeric(15, 2))
    
    # Tovary (JSON dlja prostoty)
    items = Column(JSON)
    
    # Klient
    customer_name = Column(String(255))
    region = Column(String(255))
    city = Column(String(255))
    delivery_cluster_from = Column(String(255), index=True)
    delivery_cluster_to = Column(String(255), index=True)
    shipping_warehouse_name = Column(String(255))
    
    raw_data = Column(JSON)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_fact_order_schema_date', 'delivery_schema', 'created_at'),
        Index('idx_fact_order_status', 'status'),
    )


class LogisticsTariff(Base):
    """Прайс логистики Ozon FBO/FBS. Ключ (cluster_from, cluster_to, объёмный бакет)."""
    __tablename__ = "logistics_tariffs"

    id = Column(Integer, primary_key=True)
    cluster_from = Column(String(128), nullable=False, index=True)
    cluster_to = Column(String(128), nullable=False, index=True)
    volume_min_l = Column(Numeric(8, 3), nullable=False)
    volume_max_l = Column(Numeric(8, 3), nullable=False)
    price_under_300 = Column(Numeric(10, 2), nullable=False)
    price_over_300 = Column(Numeric(10, 2), nullable=False)
    source_file = Column(String(128))
    imported_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_lt_lookup', 'cluster_from', 'cluster_to', 'volume_min_l'),
    )


class CompetitorSnapshot(Base):
    """Снимки данных по товарам конкурентов (или своим) из Ozon-кабинета.

    Источники:
      - 'bestsellers' — seller.ozon.ru/app/analytics/what-to-sell/ozon-bestsellers (28д)
      - 'calculator'  — calculator.ozon.ru/.../item-search
    """
    __tablename__ = "competitor_snapshots"

    id = Column(Integer, primary_key=True)
    source = Column(String(32), nullable=False, index=True)  # 'bestsellers' | 'calculator'
    sku = Column(String(64), nullable=False, index=True)
    name = Column(String(1000))
    brand = Column(String(255))
    category1 = Column(String(255))
    category3 = Column(String(255))
    period = Column(String(32))  # 'monthly' | 'weekly' | null
    sold_sum = Column(Numeric(15, 2))       # заказано на сумму
    sold_units = Column(Integer)            # заказано товаров
    avg_price = Column(Numeric(15, 2))      # средняя цена покупки
    min_price = Column(Numeric(15, 2))      # самая низкая цена
    session_count = Column(Integer)         # просмотры/сессии
    conv_to_cart = Column(Numeric(8, 4))    # конверсия в корзину
    buyout_rate = Column(Numeric(8, 4))     # доля выкупа
    lost_sales = Column(Numeric(15, 2))     # упущенные продажи
    days_without_stock = Column(Integer)
    daily_sales = Column(Numeric(15, 2))    # среднесуточные продажи
    search_position = Column(Integer)       # позиция в поиске
    dynamic_pct = Column(Numeric(8, 2))     # динамика %
    price_buyer = Column(Numeric(15, 2))    # для калькулятора
    weight_kg = Column(Numeric(10, 3))
    length_cm = Column(Numeric(10, 2))
    width_cm = Column(Numeric(10, 2))
    height_cm = Column(Numeric(10, 2))
    volume_l = Column(Numeric(10, 3))
    fbo_commission_rate = Column(Numeric(6, 4))
    fbs_commission_rate = Column(Numeric(6, 4))
    photo_url = Column(Text)
    product_url = Column(Text)
    raw_data = Column(JSON)
    captured_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index('idx_competitor_sku_captured', 'sku', 'captured_at'),
    )


class ProductDimension(Base):
    """Габариты и вес товара (для расчёта объёма и выбора логистического тарифа)."""
    __tablename__ = "product_dimensions"

    id = Column(Integer, primary_key=True)
    offer_id = Column(String(128), unique=True, nullable=False, index=True)
    sku = Column(BigInteger)
    length_cm = Column(Numeric(8, 2))
    width_cm = Column(Numeric(8, 2))
    height_cm = Column(Numeric(8, 2))
    weight_kg = Column(Numeric(8, 3))
    volume_l = Column(Numeric(8, 3))
    source = Column(String(32))  # 'ozon_api_v4' | 'manual' | 'calculator_ozon'
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
