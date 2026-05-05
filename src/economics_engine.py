"""
Универсальный движок расчёта экономики товара.

Используется в отчётах:
- Акции — расчёт что будет, если зайти в акцию по action_price
- Реклама (планируется) — расчёт маржи при изменении объёма заказов

Принципы:
- Движок не знает про БД, aiohttp, фронт. Принимает dataclass на вход — возвращает dataclass на выход.
- "База" — фактические показатели товара за прошлый период (из accruals_comp_by_article).
- "Сценарий" — что меняем (цена / units / ставки).
- Все расходы делятся на 3 типа:
  * percent_of_revenue — комиссия Ozon, эквайринг, налог. Меняются от цены.
  * per_unit            — логистика, FBO, упаковка, себестоимость, реклама.
                          Не зависят от цены, растут пропорционально количеству.
  * fixed_per_period    — подписки, штрафы. (пока не разделяем — относим к per_unit)
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


@dataclass
class ProductEconomicsBase:
    """Базовая экономика товара за период (фактические данные)."""

    offer_id: str
    sku: Optional[int] = None
    period_days: int = 30

    # Объём
    ordered_units: float = 0.0
    revenue: float = 0.0  # выручка-нетто (после возвратов)

    # Расходы — % от цены
    sale_commission: float = 0.0  # вознаграждение Ozon за продажу
    acquiring: float = 0.0        # эквайринг

    # Расходы — фикс на единицу (не меняются от цены)
    delivery_services: float = 0.0   # логистика, обработка, последняя миля
    fbo_services: float = 0.0        # приёмка, размещение, кросс-докинг
    agent_services_other: float = 0.0  # партнёрские услуги БЕЗ эквайринга
    other_services: float = 0.0      # упаковка, штрафы, прочее
    ad_spend: float = 0.0            # реклама
    material_cost: float = 0.0       # себестоимость

    # Налог уже посчитан в базе (не нужен — пересчитываем в сценарии)
    # Сохраняем коэффициенты для прозрачности
    raw_values: Dict[str, float] = field(default_factory=dict)

    # ----- Производные коэффициенты -----

    @property
    def avg_price(self) -> float:
        return _safe_div(self.revenue, self.ordered_units)

    @property
    def commission_pct(self) -> float:
        """% комиссии Ozon от выручки."""
        return _safe_div(self.sale_commission, self.revenue)

    @property
    def acquiring_pct(self) -> float:
        """% эквайринга от выручки."""
        return _safe_div(self.acquiring, self.revenue)

    @property
    def fixed_costs_total(self) -> float:
        """Сумма всех расходов фикс на единицу за период."""
        return (
            self.delivery_services
            + self.fbo_services
            + self.agent_services_other
            + self.other_services
            + self.ad_spend
            + self.material_cost
        )

    @property
    def fixed_cost_per_unit(self) -> float:
        """Фикс-расход на 1 единицу (без себестоимости и без рекламы)."""
        non_material = (
            self.delivery_services
            + self.fbo_services
            + self.agent_services_other
            + self.other_services
        )
        return _safe_div(non_material, self.ordered_units)

    @property
    def material_cost_per_unit(self) -> float:
        return _safe_div(self.material_cost, self.ordered_units)

    @property
    def ad_per_unit(self) -> float:
        return _safe_div(self.ad_spend, self.ordered_units)


@dataclass
class EconomicsScenario:
    """Параметры сценария — что меняем относительно базы.

    Все None = берём из базы. Все числа = override.
    """

    price: Optional[float] = None              # новая цена за единицу
    units: Optional[float] = None              # новое количество заказов
    commission_pct: Optional[float] = None     # override % комиссии
    acquiring_pct: Optional[float] = None      # override % эквайринга
    tax_rate_pct: float = 0.0                  # ставка налога (%)
    fixed_cost_per_unit: Optional[float] = None    # override фикс-расхода на ед.
    material_cost_per_unit: Optional[float] = None # override себестоимости на ед.
    ad_spend_total: Optional[float] = None     # override бюджета рекламы за период


@dataclass
class EconomicsResult:
    """Результат расчёта по сценарию."""

    # Вход
    units: float
    price: float

    # Доходы
    revenue: float

    # Расходы (% от цены)
    sale_commission: float
    acquiring: float
    tax: float

    # Расходы (фикс на единицу)
    fixed_costs: float       # delivery + fbo + agent_other + other (× units)
    material_cost: float     # себестоимость (× units)
    ad_spend: float          # реклама за период

    # Итог
    total_expenses: float
    gross_profit: float
    gross_profit_pct: float  # % от revenue

    # Δ относительно базы
    revenue_delta: Optional[float] = None
    gross_profit_delta: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate(
    base: ProductEconomicsBase,
    scenario: Optional[EconomicsScenario] = None,
) -> EconomicsResult:
    """Считает экономику товара по сценарию.

    Если сценарий None — возвращает фактическую базу как результат
    (для режима "В акциях" — без пересчёта).
    """
    if scenario is None:
        scenario = EconomicsScenario()

    units = scenario.units if scenario.units is not None else base.ordered_units
    price = scenario.price if scenario.price is not None else base.avg_price

    commission_pct = (
        scenario.commission_pct
        if scenario.commission_pct is not None
        else base.commission_pct
    )
    acquiring_pct = (
        scenario.acquiring_pct
        if scenario.acquiring_pct is not None
        else base.acquiring_pct
    )

    fixed_per_unit = (
        scenario.fixed_cost_per_unit
        if scenario.fixed_cost_per_unit is not None
        else base.fixed_cost_per_unit
    )
    material_per_unit = (
        scenario.material_cost_per_unit
        if scenario.material_cost_per_unit is not None
        else base.material_cost_per_unit
    )
    ad_total = (
        scenario.ad_spend_total
        if scenario.ad_spend_total is not None
        else base.ad_spend
    )

    revenue = price * units
    sale_commission = revenue * commission_pct
    acquiring = revenue * acquiring_pct
    tax = revenue * (scenario.tax_rate_pct / 100.0)
    fixed_costs = fixed_per_unit * units
    material_cost = material_per_unit * units

    total_expenses = sale_commission + acquiring + tax + fixed_costs + material_cost + ad_total
    gross_profit = revenue - total_expenses
    gross_pct = _safe_div(gross_profit, revenue)

    # Сравнение с базой (факт без сценария, с тем же налогом)
    base_revenue = base.revenue
    base_tax = base_revenue * (scenario.tax_rate_pct / 100.0)
    base_gross = (
        base_revenue
        - base.sale_commission
        - base.acquiring
        - base_tax
        - base.delivery_services
        - base.fbo_services
        - base.agent_services_other
        - base.other_services
        - base.ad_spend
        - base.material_cost
    )

    return EconomicsResult(
        units=units,
        price=price,
        revenue=revenue,
        sale_commission=sale_commission,
        acquiring=acquiring,
        tax=tax,
        fixed_costs=fixed_costs,
        material_cost=material_cost,
        ad_spend=ad_total,
        total_expenses=total_expenses,
        gross_profit=gross_profit,
        gross_profit_pct=gross_pct,
        revenue_delta=revenue - base_revenue,
        gross_profit_delta=gross_profit - base_gross,
    )


def base_from_accrual_values(
    offer_id: str,
    values: Dict[str, Any],
    period_days: int = 30,
    sku: Optional[int] = None,
) -> ProductEconomicsBase:
    """Преобразует словарь values из ответа /api/accruals-comp-by-article в ProductEconomicsBase.

    Маппинг полей опирается на структуру compute_derived() в orders_dashboard.py.
    """
    def f(k: str) -> float:
        try:
            return float(values.get(k, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    sale_commission = f("ozon_fee_total")  # уже sale_commission - return_commission
    acquiring = f("acquiring")
    agent_total = f("agent_services_total")
    agent_other = max(0.0, agent_total - acquiring)

    return ProductEconomicsBase(
        offer_id=offer_id,
        sku=sku,
        period_days=period_days,
        ordered_units=f("ordered_units"),
        revenue=f("revenue_sales"),
        sale_commission=sale_commission,
        acquiring=acquiring,
        delivery_services=f("delivery_services_total"),
        fbo_services=f("fbo_services_total"),
        agent_services_other=agent_other,
        other_services=f("other_services") + f("promotion_total") - f("ad_spend"),  # промо/штрафы/прочее без рекламы
        ad_spend=f("ad_spend"),
        material_cost=f("material_cost"),
        raw_values=dict(values),
    )
