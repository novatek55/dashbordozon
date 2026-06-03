# src/services/finance_md_report.py
"""Генератор MD-отчёта по финансам магазина за месяц.

Использование:
    python -m src.export_finance_report --month 2026-05
    python -m src.export_finance_report --month 2026-05 --compare 2026-04
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import asyncpg

from src.dashboard.constants import MSK
from src.dashboard.helpers import safe_divide
from src.dashboard.routes.finance import build_rows_map_for_month


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rub(v: Any) -> str:
    try:
        return f"{float(v or 0):>12,.0f} ₽"
    except (TypeError, ValueError):
        return "           0 ₽"


def _pct(v: Any) -> str:
    try:
        return f"{float(v or 0)*100:5.1f}%"
    except (TypeError, ValueError):
        return "  0.0%"


def _delta(cur: float, prev: float) -> str:
    """Возвращает строку вида '+12.3%' или '-5.1%'."""
    if prev == 0:
        return "    н/д"
    d = (cur - prev) / abs(prev) * 100
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:5.1f}%"


def _row(label: str, cur: float, prev: Optional[float] = None, unit: str = "rub") -> str:
    if unit == "rub":
        val = _rub(cur)
        prev_val = f"  {_rub(prev)}" if prev is not None else ""
        delta = f"  {_delta(cur, prev)}" if prev is not None else ""
    elif unit == "pct":
        val = f"{cur*100:8.1f}%"
        prev_val = f"  {prev*100:8.1f}%" if prev is not None else ""
        delta = f"  {_delta(cur, prev)}" if prev is not None else ""
    elif unit == "int":
        val = f"{int(cur):>12}"
        prev_val = f"  {int(prev):>12}" if prev is not None else ""
        delta = f"  {_delta(cur, prev)}" if prev is not None else ""
    else:
        val = str(cur)
        prev_val = ""
        delta = ""
    return f"| {label:<38} | {val} |{prev_val}{delta} |"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

async def _load_month(conn: asyncpg.Connection, month: str) -> Dict[str, float]:
    rm, _ = await build_rows_map_for_month(conn, month)

    def t(key: str) -> float:
        return float(rm.get(key, {}).get("total") or 0)

    rev = t("revenue_sales")
    units = t("ordered_units")

    return {
        "month": month,
        "rev": rev,
        "rev_gross": t("revenue"),
        "returns": t("returns_revenue"),
        "units": units,
        "commission": t("ozon_fee_total"),
        "sale_commission": t("sale_commission"),
        "return_commission": t("return_commission"),
        "logistics": t("delivery_services_total"),
        "logistics_fbo": t("logistics"),
        "logistics_reverse": t("reverse_logistics"),
        "logistics_courier": t("courier_departure"),
        "logistics_pickup": t("pickup_courier_delivery"),
        "logistics_dropoff": t("dropoff_processing"),
        "agent": t("agent_services_total"),
        "acquiring": t("acquiring"),
        "delivery_to_pickup": t("delivery_to_pickup"),
        "partner_returns": t("partner_returns_processing"),
        "partner_dropoff": t("partner_dropoff_processing"),
        "storage": t("temporary_partner_storage"),
        "ads_total": t("promotion_total"),
        "ads_ppc": t("pay_per_click"),
        "ads_review_pin": t("review_pin"),
        "ads_accel_reviews": t("accelerated_reviews"),
        "ads_premium": t("premium_plus_subscription"),
        "mp_expenses": t("marketplace_expenses"),
        "accrued": t("accrued"),
        "material_cost": t("material_cost"),
        "gross_profit": t("gross_profit"),
        "penalties": t("penalties_total"),
        "penalty_slot": t("penalty_non_recommended_slot"),
        "compensations": t("compensations"),
        # Производные
        "commission_rate": safe_divide(t("ozon_fee_total"), rev),
        "logistics_rate": safe_divide(t("delivery_services_total"), rev),
        "ads_rate": safe_divide(t("promotion_total"), rev),
        "mp_rate": safe_divide(t("marketplace_expenses"), rev),
        "accrued_rate": safe_divide(t("accrued"), rev),
        "material_rate": safe_divide(t("material_cost"), rev),
        "gross_rate": safe_divide(t("gross_profit"), rev),
        "return_rate": safe_divide(t("returns_revenue"), rev),
        "avg_check": safe_divide(rev, units),
        "log_per_order": safe_divide(t("delivery_services_total"), units),
        "comm_per_order": safe_divide(t("ozon_fee_total"), units),
        "ads_per_order": safe_divide(t("pay_per_click"), units),
        "gross_per_order": safe_divide(t("gross_profit"), units),
        "material_per_order": safe_divide(t("material_cost"), units),
    }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

async def build_finance_report(
    conn: asyncpg.Connection,
    month: str,
    prev_month: Optional[str] = None,
) -> str:
    cur = await _load_month(conn, month)
    prev = await _load_month(conn, prev_month) if prev_month else None

    generated = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")
    has_prev = prev is not None
    compare_label = f" vs {prev_month}" if has_prev else ""

    lines: list[str] = []

    def h(text: str) -> None:
        lines.append(f"\n{text}\n")

    def sep() -> None:
        lines.append("")

    # -----------------------------------------------------------------------
    # Заголовок
    # -----------------------------------------------------------------------
    lines.append(f"# Finance Report — {month}")
    lines.append(f"Generated: {generated}  ")
    if has_prev:
        lines.append(f"Сравнение: {month} vs {prev_month}")
    sep()

    # -----------------------------------------------------------------------
    # Раздел 1: P&L
    # -----------------------------------------------------------------------
    h("## 1. P&L магазина")

    if has_prev:
        lines.append(f"| {'Метрика':<38} | {'Текущий':>14} | {'Прошлый':>14} | {'Δ':>8} |")
        lines.append(f"|{'-'*40}|{'-'*16}|{'-'*16}|{'-'*10}|")
    else:
        lines.append(f"| {'Метрика':<38} | {'Значение':>14} |")
        lines.append(f"|{'-'*40}|{'-'*16}|")

    def add(label: str, key: str, unit: str = "rub") -> None:
        c = cur[key]
        p = prev[key] if has_prev else None
        lines.append(_row(label, c, p, unit))

    add("Выручка gross (до возвратов)", "rev_gross")
    add("Возвраты", "returns")
    add("Чистая выручка", "rev")
    lines.append(f"|{'-'*40}|{'-'*16}|" + (f"{'-'*16}|{'-'*10}|" if has_prev else ""))
    add("Комиссия Ozon", "commission")
    add("  в т.ч. с продаж", "sale_commission")
    add("  в т.ч. возврат комиссии (−)", "return_commission")
    add("Логистика (итого)", "logistics")
    add("Реклама (итого)", "ads_total")
    add("Прочие сервисы MP", "agent")
    lines.append(f"|{'-'*40}|{'-'*16}|" + (f"{'-'*16}|{'-'*10}|" if has_prev else ""))
    add("ИТОГО расходы MP", "mp_expenses")
    lines.append(f"|{'-'*40}|{'-'*16}|" + (f"{'-'*16}|{'-'*10}|" if has_prev else ""))
    add("Деньги на счёт (accrued)", "accrued")
    add("Себестоимость товаров", "material_cost")
    add("ВАЛОВАЯ ПРИБЫЛЬ", "gross_profit")
    sep()

    # -----------------------------------------------------------------------
    # Раздел 2: % от чистой выручки
    # -----------------------------------------------------------------------
    h("## 2. Структура (% от чистой выручки)")

    if has_prev:
        lines.append(f"| {'Статья':<38} | {'Текущий':>8} | {'Прошлый':>8} | {'Δ п.п.':>8} |")
        lines.append(f"|{'-'*40}|{'-'*10}|{'-'*10}|{'-'*10}|")
    else:
        lines.append(f"| {'Статья':<38} | {'%':>8} |")
        lines.append(f"|{'-'*40}|{'-'*10}|")

    def add_pct(label: str, key: str) -> None:
        c = cur[key]
        p = prev[key] if has_prev else None
        if has_prev:
            pp_delta = (c - p) * 100
            sign = "+" if pp_delta >= 0 else ""
            lines.append(
                f"| {label:<38} | {c*100:>7.1f}% | {p*100:>7.1f}% | {sign}{pp_delta:>6.1f} п.п. |"
            )
        else:
            lines.append(f"| {label:<38} | {c*100:>7.1f}% |")

    add_pct("Комиссия Ozon", "commission_rate")
    add_pct("Логистика", "logistics_rate")
    add_pct("Реклама", "ads_rate")
    add_pct("Прочие сервисы MP", "mp_rate")
    add_pct("─── Итого расходы MP", "mp_rate")
    add_pct("Деньги на счёт", "accrued_rate")
    add_pct("Себестоимость", "material_rate")
    add_pct("Валовая прибыль", "gross_rate")
    sep()

    # -----------------------------------------------------------------------
    # Раздел 3: Юнит-экономика
    # -----------------------------------------------------------------------
    h("## 3. Юнит-экономика")

    if has_prev:
        lines.append(f"| {'Метрика':<38} | {'Текущий':>14} | {'Прошлый':>14} | {'Δ':>8} |")
        lines.append(f"|{'-'*40}|{'-'*16}|{'-'*16}|{'-'*10}|")
    else:
        lines.append(f"| {'Метрика':<38} | {'Значение':>14} |")
        lines.append(f"|{'-'*40}|{'-'*16}|")

    add("Заказов (шт.)", "units", "int")
    add("Средний чек", "avg_check")
    add("Логистика / заказ", "log_per_order")
    add("Комиссия / заказ", "comm_per_order")
    add("Реклама PPC / заказ", "ads_per_order")
    add("Себестоимость / заказ", "material_per_order")
    add("Валовая прибыль / заказ", "gross_per_order")
    sep()

    # -----------------------------------------------------------------------
    # Раздел 4: Детализация расходов
    # -----------------------------------------------------------------------
    h("## 4. Детализация расходов")

    h("### 4.1 Логистика")
    if has_prev:
        lines.append(f"| {'Статья':<38} | {'Текущий':>14} | {'Прошлый':>14} | {'Δ':>8} |")
        lines.append(f"|{'-'*40}|{'-'*16}|{'-'*16}|{'-'*10}|")
    else:
        lines.append(f"| {'Статья':<38} | {'Значение':>14} |")
        lines.append(f"|{'-'*40}|{'-'*16}|")
    add("FBO (прямая логистика)", "logistics_fbo")
    add("Обратная логистика (возвраты)", "logistics_reverse")
    add("Курьерская забор", "logistics_courier")
    add("Доставка до ПВЗ", "logistics_pickup")
    add("Обработка дропофф", "logistics_dropoff")
    sep()

    h("### 4.2 Реклама")
    if has_prev:
        lines.append(f"| {'Статья':<38} | {'Текущий':>14} | {'Прошлый':>14} | {'Δ':>8} |")
        lines.append(f"|{'-'*40}|{'-'*16}|{'-'*16}|{'-'*10}|")
    else:
        lines.append(f"| {'Статья':<38} | {'Значение':>14} |")
        lines.append(f"|{'-'*40}|{'-'*16}|")
    add("Трафиковые кампании (PPC)", "ads_ppc")
    add("Закрепление отзывов", "ads_review_pin")
    add("Ускоренные отзывы", "ads_accel_reviews")
    add("Premium+ подписка", "ads_premium")
    sep()

    h("### 4.3 Прочие сервисы MP")
    if has_prev:
        lines.append(f"| {'Статья':<38} | {'Текущий':>14} | {'Прошлый':>14} | {'Δ':>8} |")
        lines.append(f"|{'-'*40}|{'-'*16}|{'-'*16}|{'-'*10}|")
    else:
        lines.append(f"| {'Статья':<38} | {'Значение':>14} |")
        lines.append(f"|{'-'*40}|{'-'*16}|")
    add("Эквайринг", "acquiring")
    add("Доставка до ПВЗ (агентская)", "delivery_to_pickup")
    add("Обработка возвратов партнёр", "partner_returns")
    add("Дропофф обработка партнёр", "partner_dropoff")
    add("Временное хранение партнёр", "storage")
    sep()

    # -----------------------------------------------------------------------
    # Раздел 5: Возвраты
    # -----------------------------------------------------------------------
    h("## 5. Возвраты")
    if has_prev:
        lines.append(f"| {'Метрика':<38} | {'Текущий':>14} | {'Прошлый':>14} | {'Δ':>8} |")
        lines.append(f"|{'-'*40}|{'-'*16}|{'-'*16}|{'-'*10}|")
    else:
        lines.append(f"| {'Метрика':<38} | {'Значение':>14} |")
        lines.append(f"|{'-'*40}|{'-'*16}|")
    add("Выручка возвратов", "returns")
    add_pct("Доля возвратов от выручки", "return_rate") if not has_prev else add("Доля возвратов (% выручки)", "return_rate", "pct")
    add("Комиссия с возвратов (потери)", "return_commission")
    sep()

    # -----------------------------------------------------------------------
    # Раздел 6: Штрафы и компенсации
    # -----------------------------------------------------------------------
    h("## 6. Штрафы и компенсации")
    if has_prev:
        lines.append(f"| {'Метрика':<38} | {'Текущий':>14} | {'Прошлый':>14} | {'Δ':>8} |")
        lines.append(f"|{'-'*40}|{'-'*16}|{'-'*16}|{'-'*10}|")
    else:
        lines.append(f"| {'Метрика':<38} | {'Значение':>14} |")
        lines.append(f"|{'-'*40}|{'-'*16}|")
    add("Штрафы итого", "penalties")
    add("  нерекомендованный слот", "penalty_slot")
    add("Компенсации от Ozon", "compensations")
    sep()

    # -----------------------------------------------------------------------
    # Раздел 7: Шаблон выводов (заполняется вручную)
    # -----------------------------------------------------------------------
    h("## 7. Выводы")
    lines.append("<!-- Заполнить после анализа по инструкции finance_report_monthly_guide.md -->")
    sep()
    lines.append("**✅ Хорошо:**")
    lines.append("- ")
    sep()
    lines.append("**⚠️ Внимание:**")
    lines.append("- ")
    sep()
    lines.append("**🔴 Действие:**")
    lines.append("- ")
    sep()
    lines.append("**📌 Следующие отчёты:**")
    lines.append("- ")
    sep()

    return "\n".join(lines)
