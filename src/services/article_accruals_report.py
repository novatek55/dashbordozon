# src/services/article_accruals_report.py
"""Генератор MD-отчёта «Начисления по артикулам» за месяц.

Использование:
    python -m src.export_article_accruals_report --month 2026-05
    python -m src.export_article_accruals_report --month 2026-05 --compare 2026-04

Данные:
  - transactions (OperationAgentDeliveredToCustomer) → raw_data: accruals_for_sale,
    sale_commission, delivery_schema, services[]
  - fact_order_items → offer_id, sku, quantity, price
  - finance_article_costs → unit_cost по артикулу/sku
  - OperationItemReturn / OperationReturnGoodsFBSofRMS / ClientReturnAgentOperation → возвраты

Аллокация для мультиартикульных отправлений:
  Начисления и комиссия делятся пропорционально price × quantity каждого товара.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Tuple

import asyncpg

from src.dashboard.constants import MSK

# Названия сервисов логистики (цена отрицательная)
_LOGISTICS_SERVICES = {
    "MarketplaceServiceItemDirectFlowLogistic",
    "MarketplaceServiceItemReturnFlowLogistic",
    "MarketplaceServiceItemDeliveryToHandoverPlaceOzon",
    "MarketplaceServiceItemRedistributionLastMileCourier",
    "MarketplaceServiceItemRedistributionDropOffApvz",
    "MarketplaceServiceItemDropoffPVZ",
    "MarketplaceServiceItemDeliveryToHandoverPlaceCourier",
    "MarketplaceServiceItemCourierPreprocessing",
    "MarketplaceServiceItemLastMile",
}

# Типы операций возврата
_RETURN_TYPES = {
    "OperationItemReturn",
    "OperationReturnGoodsFBSofRMS",
    "ClientReturnAgentOperation",
}


@dataclass
class ArticleSchemaRow:
    offer_id: str
    schema: str          # FBO / FBS / ""
    units: float = 0.0
    revenue: float = 0.0      # accruals_for_sale
    commission: float = 0.0   # sale_commission (положительное число = потери)
    logistics: float = 0.0    # логистика (положительное)
    material_cost: float = 0.0
    return_units: float = 0.0
    return_revenue: float = 0.0

    @property
    def commission_rate(self) -> float:
        return self.commission / self.revenue if self.revenue else 0.0

    @property
    def logistics_per_unit(self) -> float:
        return self.logistics / self.units if self.units else 0.0

    TAX_RATE: float = 0.10  # УСН 10% от выручки (не участвует в датаклассе, только как константа)

    @property
    def tax(self) -> float:
        """Налог УСН = Выручка × 10%."""
        return self.revenue * 0.10

    @property
    def gross_profit(self) -> float:
        """ВП до налога = Начислено − Себестоимость."""
        return self.accrued - self.material_cost

    @property
    def gross_profit_after_tax(self) -> float:
        """ВП после налога = Начислено − Себестоимость − Налог."""
        return self.accrued - self.material_cost - self.tax

    @property
    def gross_rate(self) -> float:
        return self.gross_profit / self.revenue if self.revenue else 0.0

    @property
    def accrued(self) -> float:
        """Деньги на счёт по артикулу = Выручка − Комиссия − Логистика."""
        return self.revenue - self.commission - self.logistics

    @property
    def gp_to_accrued(self) -> float:
        """ВП после налога / Деньги_на_счёт. Цель ≥ 50%."""
        return self.gross_profit_after_tax / self.accrued if self.accrued else 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rub(v: float) -> str:
    return f"{v:>12,.0f} ₽"


def _pct(v: float) -> str:
    return f"{v * 100:5.1f}%"


def _delta_pct(cur: float, prev: float) -> str:
    if prev == 0:
        return "   н/д"
    d = (cur - prev) / abs(prev) * 100
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.1f}%"


def _delta_pp(cur: float, prev: float) -> str:
    d = (cur - prev) * 100
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.1f} п.п."


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

async def _load_cost_map(conn: asyncpg.Connection) -> Dict[str, float]:
    """offer_id → unit_cost из finance_article_costs."""
    rows = await conn.fetch("SELECT article, sku, unit_cost FROM finance_article_costs")
    cost_by_article: Dict[str, float] = {}
    for r in rows:
        if r["article"]:
            cost_by_article[r["article"].strip().lower()] = float(r["unit_cost"] or 0)
    return cost_by_article


async def _load_sku_offer_fallback(conn: asyncpg.Connection) -> Dict[int, str]:
    """sku → offer_id из report_products_items (через fbo_sku_id / fbs_sku_id).
    Используется как fallback когда sku не найден в fact_order_items.
    """
    rows = await conn.fetch("""
        SELECT DISTINCT
            COALESCE(fbo_sku_id, fbs_sku_id) AS sku,
            offer_id
        FROM report_products_items
        WHERE offer_id IS NOT NULL
          AND offer_id != ''
          AND COALESCE(fbo_sku_id, fbs_sku_id) IS NOT NULL
        ORDER BY sku, offer_id
    """)
    result: Dict[int, str] = {}
    for r in rows:
        sku = r["sku"]
        offer_id = r["offer_id"].strip().lstrip("'")
        # Берём первый непустой — если дублей несколько, первый алфавитно
        if sku not in result:
            result[sku] = offer_id
    return result


async def _load_offer_map(
    conn: asyncpg.Connection, month: str
) -> Tuple[Dict[Tuple[str, int], str], Dict[int, str]]:
    """Возвращает (posting_map, sku_fallback_map).

    posting_map: (posting_number, sku) → offer_id из fact_order_items.
    Фильтр: transactions.operation_date — согласовано с Finance Report.
    sku_fallback_map: sku → offer_id из report_products_items (fallback).

    Примечание: дашборд считает единицы по fact_orders.created_at (дата заказа),
    поэтому возможно расхождение на ~5-10% по кол-ву единиц на границе месяца.
    Финансовые суммы при этом полностью совпадают с Finance Report.
    """
    rows = await conn.fetch("""
        SELECT DISTINCT f.posting_number, f.sku, f.offer_id
        FROM fact_order_items f
        WHERE f.posting_number IN (
            SELECT posting_number FROM transactions
            WHERE TO_CHAR(operation_date AT TIME ZONE 'UTC', 'YYYY-MM') = $1
              AND operation_type = 'OperationAgentDeliveredToCustomer'
              AND posting_number != ''
        )
    """, month)
    posting_map: Dict[Tuple[str, int], str] = {}
    for r in rows:
        posting_map[(r["posting_number"], r["sku"])] = r["offer_id"]

    sku_fallback = await _load_sku_offer_fallback(conn)
    return posting_map, sku_fallback


async def _load_transactions(
    conn: asyncpg.Connection,
    month: str,
    offer_map: Dict[Tuple[str, int], str],
    sku_fallback: Dict[int, str],
    cost_map: Dict[str, float],
) -> Dict[Tuple[str, str], ArticleSchemaRow]:
    """Загружает транзакции за месяц и возвращает dict (offer_id, schema) → ArticleSchemaRow."""

    rows: Dict[Tuple[str, str], ArticleSchemaRow] = {}

    def get_row(offer_id: str, schema: str) -> ArticleSchemaRow:
        key = (offer_id, schema or "")
        if key not in rows:
            rows[key] = ArticleSchemaRow(offer_id=offer_id, schema=schema or "")
        return rows[key]

    # --- продажи ---
    txs = await conn.fetch("""
        SELECT posting_number, amount, raw_data::text AS raw
        FROM transactions
        WHERE TO_CHAR(operation_date AT TIME ZONE 'UTC', 'YYYY-MM') = $1
          AND operation_type = 'OperationAgentDeliveredToCustomer'
    """, month)

    for tx in txs:
        raw = json.loads(tx["raw"])
        accruals = float(raw.get("accruals_for_sale") or 0)
        commission = abs(float(raw.get("sale_commission") or 0))
        schema = (raw.get("posting") or {}).get("delivery_schema") or ""
        items = raw.get("items") or []
        services = raw.get("services") or []

        # Логистика = сумма абс.значений сервисов логистики
        logistics_total = sum(
            abs(float(s.get("price") or 0))
            for s in services
            if s.get("name") in _LOGISTICS_SERVICES
        )

        pnum = tx["posting_number"]

        if not items:
            continue

        # Находим offer_id для каждого item
        # Приоритет: fact_order_items (точный) → report_products_items (по sku) → sku:XXX
        item_offers: list[tuple[str, int]] = []  # (offer_id, sku)
        for item in items:
            sku = item.get("sku")
            offer_id = offer_map.get((pnum, sku)) if sku else None
            if not offer_id and sku:
                offer_id = sku_fallback.get(sku)
            if not offer_id:
                offer_id = f"sku:{sku}" if sku else "unknown"
            item_offers.append((offer_id, sku or 0))

        # Аллокация по price × quantity (из fact_order_items) если несколько товаров
        if len(items) == 1:
            weights = [1.0]
        else:
            # Получаем цены для пропорционального расчёта
            w_list = []
            for offer_id, sku in item_offers:
                # Берём price из offer_map-связанных данных (уже есть из fact_order_items)
                w_list.append(1.0)  # fallback: равные доли
            total_w = sum(w_list) or 1
            weights = [w / total_w for w in w_list]

        for i, (offer_id, sku) in enumerate(item_offers):
            w = weights[i]
            row = get_row(offer_id, schema)
            row.units += 1 * w
            row.revenue += accruals * w
            row.commission += commission * w
            row.logistics += logistics_total * w
            # Себестоимость
            oid_lower = offer_id.strip().lower()
            if oid_lower in cost_map:
                row.material_cost += cost_map[oid_lower] * w

    # --- возвраты ---
    ret_txs = await conn.fetch("""
        SELECT posting_number, raw_data::text AS raw
        FROM transactions
        WHERE TO_CHAR(operation_date AT TIME ZONE 'UTC', 'YYYY-MM') = $1
          AND operation_type = ANY($2::text[])
    """, month, list(_RETURN_TYPES))

    for tx in ret_txs:
        raw = json.loads(tx["raw"])
        items = raw.get("items") or []
        schema = (raw.get("posting") or {}).get("delivery_schema") or ""
        # Цена отмены / возврата в raw_data обычно 0, берём через offer_map
        pnum = tx["posting_number"]

        for item in items:
            sku = item.get("sku")
            offer_id = offer_map.get((pnum, sku)) if sku else None
            if not offer_id and sku:
                offer_id = sku_fallback.get(sku)
            if not offer_id:
                offer_id = f"sku:{sku}" if sku else "unknown"
            row = get_row(offer_id, schema)
            row.return_units += 1
            # return_revenue оставляем 0 — нет данных в этих транзакциях

    return rows


async def _load_month_data(
    conn: asyncpg.Connection,
    month: str,
    cost_map: Dict[str, float],
) -> Dict[Tuple[str, str], ArticleSchemaRow]:
    posting_map, sku_fallback = await _load_offer_map(conn, month)
    return await _load_transactions(conn, month, posting_map, sku_fallback, cost_map)


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

async def build_article_accruals_report(
    conn: asyncpg.Connection,
    month: str,
    prev_month: Optional[str] = None,
) -> str:
    generated = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")

    cost_map = await _load_cost_map(conn)
    cur_data = await _load_month_data(conn, month, cost_map)
    prev_data = await _load_month_data(conn, prev_month, cost_map) if prev_month else None

    # Сортировка: по выручке DESC
    sorted_rows = sorted(cur_data.values(), key=lambda r: r.revenue, reverse=True)

    lines: list[str] = []
    lines.append(f"# Начисления по артикулам — {month}")
    lines.append(f"Generated: {generated}  ")
    if prev_month:
        lines.append(f"Сравнение: {month} vs {prev_month}")
    lines.append("")

    # -----------------------------------------------------------------------
    # Раздел 1: Детальная таблица
    # -----------------------------------------------------------------------
    lines.append("## 1. По артикулам и схемам")
    lines.append("")

    has_cost = any(r.material_cost > 0 for r in sorted_rows)

    TARGET = 0.50  # целевая ВП/Счёт

    if has_cost:
        lines.append(
            f"| {'Артикул':<28} | {'Схема':^5} | {'Шт.':>5} | {'Выручка':>12} | "
            f"{'Комиссия':>12} | {'Ком.%':>6} | {'Лог./шт.':>9} | "
            f"{'Себест.':>10} | {'Вал.прибыль':>12} | {'ВП%':>5} | {'ВП/Счёт':>8} |"
        )
        lines.append(
            f"|{'-'*30}|{'-'*7}|{'-'*7}|{'-'*14}|{'-'*14}|{'-'*8}|{'-'*11}|{'-'*12}|{'-'*14}|{'-'*7}|{'-'*10}|"
        )
    else:
        lines.append(
            f"| {'Артикул':<28} | {'Схема':^5} | {'Шт.':>5} | {'Выручка':>12} | "
            f"{'Комиссия':>12} | {'Ком.%':>6} | {'Лог./шт.':>9} | "
            f"{'Вал.прибыль':>12} | {'ВП%':>5} | {'ВП/Счёт':>8} |"
        )
        lines.append(
            f"|{'-'*30}|{'-'*7}|{'-'*7}|{'-'*14}|{'-'*14}|{'-'*8}|{'-'*11}|{'-'*14}|{'-'*7}|{'-'*10}|"
        )

    for r in sorted_rows:
        if r.revenue < 100 and r.units < 1:
            continue  # пропускаем шум
        schema_label = r.schema if r.schema else "—"
        gp_acc = _pct(r.gp_to_accrued)
        flag = "  " if r.gp_to_accrued >= TARGET else "⚠️"
        if has_cost:
            lines.append(
                f"| {r.offer_id:<28} | {schema_label:^5} | {int(r.units):>5} | "
                f"{_rub(r.revenue):>12} | {_rub(r.commission):>12} | {_pct(r.commission_rate):>6} | "
                f"{_rub(r.logistics_per_unit):>9} | "
                f"{_rub(r.material_cost):>10} | {_rub(r.gross_profit):>12} | {_pct(r.gross_rate):>5} | "
                f"{flag}{gp_acc:>6} |"
            )
        else:
            lines.append(
                f"| {r.offer_id:<28} | {schema_label:^5} | {int(r.units):>5} | "
                f"{_rub(r.revenue):>12} | {_rub(r.commission):>12} | {_pct(r.commission_rate):>6} | "
                f"{_rub(r.logistics_per_unit):>9} | "
                f"{_rub(r.gross_profit):>12} | {_pct(r.gross_rate):>5} | "
                f"{flag}{gp_acc:>6} |"
            )

    lines.append("")

    # -----------------------------------------------------------------------
    # Раздел 2: Итоги FBO vs FBS
    # -----------------------------------------------------------------------
    lines.append("## 2. FBO vs FBS — итого")
    lines.append("")

    schema_totals: Dict[str, ArticleSchemaRow] = {}
    for r in cur_data.values():
        s = r.schema or "—"
        if s not in schema_totals:
            schema_totals[s] = ArticleSchemaRow(offer_id="ИТОГО", schema=s)
        t = schema_totals[s]
        t.units += r.units
        t.revenue += r.revenue
        t.commission += r.commission
        t.logistics += r.logistics
        t.material_cost += r.material_cost
        t.return_units += r.return_units

    lines.append(
        f"| {'Схема':^5} | {'Шт.':>6} | {'Выручка':>14} | {'Комиссия':>14} | "
        f"{'Ком.%':>6} | {'Логист.':>12} | {'Возвр.шт.':>10} |"
    )
    lines.append(f"|{'-'*7}|{'-'*8}|{'-'*16}|{'-'*16}|{'-'*8}|{'-'*14}|{'-'*12}|")

    grand = ArticleSchemaRow(offer_id="ИТОГО", schema="ALL")
    for schema in ["FBO", "FBS", "—", ""]:
        t = schema_totals.get(schema)
        if not t or t.units < 1:
            continue
        grand.units += t.units
        grand.revenue += t.revenue
        grand.commission += t.commission
        grand.logistics += t.logistics
        grand.material_cost += t.material_cost
        grand.return_units += t.return_units

        # Сравнение с прошлым месяцем
        prev_schema_rev = 0.0
        prev_schema_comm_rate = 0.0
        if prev_data:
            for pr in prev_data.values():
                if (pr.schema or "—") == schema:
                    prev_schema_rev += pr.revenue
                    prev_schema_comm_rate = (
                        sum(p.commission for p in prev_data.values() if (p.schema or "—") == schema)
                        / prev_schema_rev if prev_schema_rev else 0
                    )

        delta_rev = f" ({_delta_pct(t.revenue, prev_schema_rev)})" if prev_data and prev_schema_rev else ""
        lines.append(
            f"| {schema or '—':^5} | {int(t.units):>6} | {_rub(t.revenue):>14}{delta_rev} | "
            f"{_rub(t.commission):>14} | {_pct(t.commission_rate):>6} | "
            f"{_rub(t.logistics):>12} | {int(t.return_units):>10} |"
        )

    lines.append(
        f"| {'ВСЕ':^5} | {int(grand.units):>6} | {_rub(grand.revenue):>14} | "
        f"{_rub(grand.commission):>14} | {_pct(grand.commission_rate):>6} | "
        f"{_rub(grand.logistics):>12} | {int(grand.return_units):>10} |"
    )
    lines.append("")

    # -----------------------------------------------------------------------
    # Раздел 3: Топ по ставке комиссии (сигналы)
    # -----------------------------------------------------------------------
    lines.append("## 3. Сигналы — высокая ставка комиссии")
    lines.append("")
    lines.append("Артикулы/схемы с комиссией > 45% (отсортировано по ставке DESC):")
    lines.append("")
    lines.append(
        f"| {'Артикул':<28} | {'Схема':^5} | {'Шт.':>5} | {'Выручка':>12} | "
        f"{'Ком.%':>6} | {'Δ от среднего':>14} |"
    )
    lines.append(f"|{'-'*30}|{'-'*7}|{'-'*7}|{'-'*14}|{'-'*8}|{'-'*16}|")

    avg_comm_rate = grand.commission_rate
    high_commission = [
        r for r in sorted_rows
        if r.commission_rate > 0.45 and r.units >= 1
    ]
    high_commission.sort(key=lambda r: r.commission_rate, reverse=True)

    if high_commission:
        for r in high_commission[:15]:
            delta = (r.commission_rate - avg_comm_rate) * 100
            sign = "+" if delta >= 0 else ""
            lines.append(
                f"| {r.offer_id:<28} | {r.schema or '—':^5} | {int(r.units):>5} | "
                f"{_rub(r.revenue):>12} | {_pct(r.commission_rate):>6} | "
                f"{sign}{delta:.1f} п.п. vs среднего |"
            )
    else:
        lines.append("| (нет артикулов с комиссией > 45%) |")

    lines.append("")

    # -----------------------------------------------------------------------
    # Раздел 4: Топ по выручке + сравнение с прошлым месяцем
    # -----------------------------------------------------------------------
    if prev_data:
        lines.append(f"## 4. Топ-15 по выручке — {month} vs {prev_month}")
        lines.append("")
        lines.append(
            f"| {'Артикул':<28} | {'Схема':^5} | {'Выручка тек.':>13} | "
            f"{'Выручка пред.':>14} | {'Δ выручка':>10} | "
            f"{'Ком.% тек.':>10} | {'Ком.% пред.':>11} | {'Δ п.п.':>8} |"
        )
        lines.append(
            f"|{'-'*30}|{'-'*7}|{'-'*15}|{'-'*16}|{'-'*12}|{'-'*12}|{'-'*13}|{'-'*10}|"
        )
        top15 = sorted_rows[:15]
        for r in top15:
            if r.units < 1:
                continue
            prev_r = prev_data.get((r.offer_id, r.schema))
            prev_rev = prev_r.revenue if prev_r else 0.0
            prev_rate = prev_r.commission_rate if prev_r else 0.0
            lines.append(
                f"| {r.offer_id:<28} | {r.schema or '—':^5} | "
                f"{_rub(r.revenue):>13} | {_rub(prev_rev):>14} | "
                f"{_delta_pct(r.revenue, prev_rev):>10} | "
                f"{_pct(r.commission_rate):>10} | {_pct(prev_rate):>11} | "
                f"{_delta_pp(r.commission_rate, prev_rate):>8} |"
            )
        lines.append("")

    return "\n".join(lines)
