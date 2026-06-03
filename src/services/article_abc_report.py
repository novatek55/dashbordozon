# src/services/article_abc_report.py
"""АВС-анализ артикулов по валовой прибыли (70/20/10).

Использование:
    python -m src.export_article_abc_report --month 2026-05
    python -m src.export_article_abc_report --month 2026-05 --compare 2026-04
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import asyncpg

from src.dashboard.constants import MSK
from src.services.article_accruals_report import (
    ArticleSchemaRow,
    _load_cost_map,
    _load_month_data,
)


# ---------------------------------------------------------------------------
# Агрегат по артикулу (FBO + FBS вместе)
# ---------------------------------------------------------------------------

@dataclass
class ArticleTotals:
    offer_id: str
    units_fbo: float = 0.0
    units_fbs: float = 0.0
    revenue: float = 0.0
    commission: float = 0.0
    logistics: float = 0.0
    material_cost: float = 0.0
    return_units: float = 0.0
    schemas: Dict[str, ArticleSchemaRow] = field(default_factory=dict)

    @property
    def units(self) -> float:
        return self.units_fbo + self.units_fbs

    @property
    def tax(self) -> float:
        """Налог УСН = Выручка × 10%."""
        return self.revenue * 0.10

    @property
    def accrued(self) -> float:
        """Деньги на счёт = Выручка − Комиссия − Логистика."""
        return self.revenue - self.commission - self.logistics

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
    def commission_rate(self) -> float:
        return self.commission / self.revenue if self.revenue else 0.0

    @property
    def gp_to_accrued(self) -> float:
        """ВП после налога / Деньги_на_счёт. Цель ≥ 50%."""
        return self.gross_profit_after_tax / self.accrued if self.accrued else 0.0

    @property
    def logistics_per_unit(self) -> float:
        return self.logistics / self.units if self.units else 0.0


def _aggregate(data: Dict[Tuple[str, str], ArticleSchemaRow]) -> List[ArticleTotals]:
    """Схлопываем FBO+FBS → один ArticleTotals на offer_id."""
    totals: Dict[str, ArticleTotals] = {}
    for (offer_id, schema), row in data.items():
        if offer_id not in totals:
            totals[offer_id] = ArticleTotals(offer_id=offer_id)
        t = totals[offer_id]
        if schema == "FBO":
            t.units_fbo += row.units
        else:
            t.units_fbs += row.units
        t.revenue += row.revenue
        t.commission += row.commission
        t.logistics += row.logistics
        t.material_cost += row.material_cost
        t.return_units += row.return_units
        t.schemas[schema or "—"] = row
    return list(totals.values())


def _classify_abc(articles: List[ArticleTotals]) -> Tuple[
    List[ArticleTotals], List[ArticleTotals], List[ArticleTotals]
]:
    """Делим на A/B/C по 70/20/10 от суммарной валовой прибыли (только >0)."""
    # Сортируем по GP убывающей (убыточные — в конец C)
    positives = sorted([a for a in articles if a.gross_profit > 0],
                       key=lambda a: a.gross_profit, reverse=True)
    negatives = sorted([a for a in articles if a.gross_profit <= 0],
                       key=lambda a: a.gross_profit)

    total_gp = sum(a.gross_profit for a in positives)
    if total_gp == 0:
        return [], [], articles

    cumulative = 0.0
    group_a, group_b = [], []
    for art in positives:
        cumulative += art.gross_profit
        share = cumulative / total_gp
        if share <= 0.70:
            group_a.append(art)
        elif share <= 0.90:
            group_b.append(art)
        else:
            break  # остальные positives идут в C
    group_c_positives = positives[len(group_a) + len(group_b):]
    group_c = group_c_positives + negatives

    return group_a, group_b, group_c


# ---------------------------------------------------------------------------
# Форматирование
# ---------------------------------------------------------------------------

def _rub(v: float) -> str:
    return f"{v:>12,.0f} ₽"


def _pct(v: float) -> str:
    return f"{v * 100:5.1f}%"


def _delta_pp(cur: float, prev: float) -> str:
    d = (cur - prev) * 100
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.1f}"


def _delta_pct(cur: float, prev: float) -> str:
    if prev == 0:
        return "н/д"
    d = (cur - prev) / abs(prev) * 100
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.0f}%"


_TARGET_GP_ACC = 0.50  # целевая ВП/Деньги_на_счёт


def _article_row(
    art: ArticleTotals,
    prev_art: Optional[ArticleTotals],
    show_cost: bool,
) -> str:
    neg_flag = "⚠️ " if art.gross_profit < 0 else "   "
    fbo_str = f"{int(art.units_fbo):>4}" if art.units_fbo > 0 else "   —"
    fbs_str = f"{int(art.units_fbs):>4}" if art.units_fbs > 0 else "   —"

    delta_rate_str = ""
    if prev_art:
        delta_rate_str = _delta_pp(art.gross_rate, prev_art.gross_rate)

    # ВП/Счёт — предупреждение если ниже цели
    gp_acc_str = _pct(art.gp_to_accrued)
    acc_flag = "  " if art.gp_to_accrued >= _TARGET_GP_ACC or art.gross_profit <= 0 else "⚠️"

    if show_cost:
        return (
            f"| {neg_flag}{art.offer_id:<26} | {fbo_str} | {fbs_str} | "
            f"{_rub(art.revenue):>12} | {_pct(art.commission_rate):>6} | "
            f"{_rub(art.logistics_per_unit):>9} | {_rub(art.material_cost):>10} | "
            f"{_rub(art.tax):>10} | {_rub(art.gross_profit_after_tax):>12} | {_pct(art.gross_rate):>5} | "
            f"{acc_flag}{gp_acc_str:>6} | {delta_rate_str:>8} |"
        )
    else:
        return (
            f"| {neg_flag}{art.offer_id:<26} | {fbo_str} | {fbs_str} | "
            f"{_rub(art.revenue):>12} | {_pct(art.commission_rate):>6} | "
            f"{_rub(art.logistics_per_unit):>9} | "
            f"{_rub(art.tax):>10} | {_rub(art.gross_profit_after_tax):>12} | {_pct(art.gross_rate):>5} | "
            f"{acc_flag}{gp_acc_str:>6} | {delta_rate_str:>8} |"
        )


def _group_header(show_cost: bool) -> str:
    if show_cost:
        return (
            f"| {'Артикул':<29} | {'FBO':>4} | {'FBS':>4} | "
            f"{'Выручка':>12} | {'Ком.%':>6} | {'Лог./шт.':>9} | "
            f"{'Себест.':>10} | {'Налог':>10} | {'ВП':>12} | {'ВП%':>5} | {'ВП/Счёт':>8} | {'Δ ВП%':>7} |"
        )
    else:
        return (
            f"| {'Артикул':<29} | {'FBO':>4} | {'FBS':>4} | "
            f"{'Выручка':>12} | {'Ком.%':>6} | {'Лог./шт.':>9} | "
            f"{'Налог':>10} | {'ВП':>12} | {'ВП%':>5} | {'ВП/Счёт':>8} | {'Δ ВП%':>7} |"
        )


def _group_sep(show_cost: bool) -> str:
    if show_cost:
        return f"|{'-'*31}|{'-'*6}|{'-'*6}|{'-'*14}|{'-'*8}|{'-'*11}|{'-'*12}|{'-'*12}|{'-'*14}|{'-'*7}|{'-'*10}|{'-'*9}|"
    else:
        return f"|{'-'*31}|{'-'*6}|{'-'*6}|{'-'*14}|{'-'*8}|{'-'*11}|{'-'*12}|{'-'*14}|{'-'*7}|{'-'*10}|{'-'*9}|"


def _auto_comment(
    label: str,
    group: List[ArticleTotals],
    total_gp: float,
    prev_map: Optional[Dict[str, ArticleTotals]],
) -> str:
    if not group:
        return f"> {label}: нет артикулов."

    gp_sum = sum(a.gross_profit for a in group)
    gp_share = gp_sum / total_gp * 100 if total_gp else 0
    avg_rate = (
        sum(a.gross_rate * a.revenue for a in group) /
        sum(a.revenue for a in group)
        if sum(a.revenue for a in group) else 0
    )
    n = len(group)

    lines = [
        f"> **{label}:** {n} арт. — ВП {gp_sum:,.0f} ₽ ({gp_share:.0f}% от итога), "
        f"средняя маржа {avg_rate * 100:.1f}%"
    ]

    # Артикул с наибольшим падением маржи
    if prev_map:
        drops = []
        for a in group:
            p = prev_map.get(a.offer_id)
            if p and p.gross_rate > 0:
                drops.append((a.offer_id, a.gross_rate - p.gross_rate))
        drops.sort(key=lambda x: x[1])
        if drops and drops[0][1] < -0.02:
            art_id, dd = drops[0]
            lines.append(
                f"> ⚠️ Падение маржи: **{art_id}** {dd * 100:+.1f} п.п. vs прошлый месяц"
            )

    # Убыточные в C
    neg = [a for a in group if a.gross_profit < 0]
    if neg:
        neg_total = sum(a.gross_profit for a in neg)
        lines.append(
            f"> 🔴 Убыточных: {len(neg)} арт. / потери {abs(neg_total):,.0f} ₽ — "
            f"кандидаты на отключение: {', '.join(a.offer_id for a in neg[:5])}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

async def build_article_abc_report(
    conn: asyncpg.Connection,
    month: str,
    prev_month: Optional[str] = None,
) -> str:
    generated = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")

    cost_map = await _load_cost_map(conn)
    cur_raw = await _load_month_data(conn, month, cost_map)
    prev_raw = await _load_month_data(conn, prev_month, cost_map) if prev_month else None

    cur_articles = _aggregate(cur_raw)
    prev_map: Optional[Dict[str, ArticleTotals]] = None
    if prev_raw:
        prev_map = {a.offer_id: a for a in _aggregate(prev_raw)}

    group_a, group_b, group_c = _classify_abc(cur_articles)

    total_gp = sum(a.gross_profit for a in cur_articles if a.gross_profit > 0)
    total_rev = sum(a.revenue for a in cur_articles)
    total_gp_all = sum(a.gross_profit for a in cur_articles)
    show_cost = any(a.material_cost > 0 for a in cur_articles)

    lines: list[str] = []
    lines.append(f"# АВС-анализ артикулов — {month}")
    lines.append(f"Generated: {generated}  ")
    if prev_month:
        lines.append(f"Сравнение: {month} vs {prev_month}")
    lines.append("")
    lines.append(
        "_Метод: 70/20/10 по валовой прибыли. "
        "Агрегация FBO+FBS. Убыточные — в группу C._"
    )
    lines.append("")

    # -------------------------------------------------------------------
    # Раздел 0: Сводная таблица
    # -------------------------------------------------------------------
    lines.append("## 0. Сводка по группам")
    lines.append("")
    lines.append(
        f"| {'Группа':<8} | {'Арт.':>5} | {'Шт.':>6} | {'Выручка':>14} | "
        f"{'Вал.прибыль':>13} | {'ВП%':>5} | {'ВП/Счёт':>8} | {'Ком.%':>6} | {'Доля ВП':>8} |"
    )
    lines.append(f"|{'-'*10}|{'-'*7}|{'-'*8}|{'-'*16}|{'-'*15}|{'-'*7}|{'-'*10}|{'-'*8}|{'-'*10}|")

    def _summary_row(label: str, group: List[ArticleTotals]) -> str:
        if not group:
            return f"| {label:<8} |     — |      — |             — |             — |    — |       — |    — |       — |"
        n = len(group)
        units = sum(a.units for a in group)
        rev = sum(a.revenue for a in group)
        gp = sum(a.gross_profit for a in group)
        gp_after_tax = sum(a.gross_profit_after_tax for a in group)
        accrued_total = sum(a.accrued for a in group)
        gp_rate = gp / rev if rev else 0
        gp_acc = gp_after_tax / accrued_total if accrued_total else 0
        comm_rate = sum(a.commission for a in group) / rev if rev else 0
        gp_share = gp / total_gp_all * 100 if total_gp_all else 0
        prev_gp = sum(prev_map[a.offer_id].gross_profit for a in group
                      if prev_map and a.offer_id in prev_map)
        delta = f" ({_delta_pct(gp, prev_gp)})" if prev_map and prev_gp else ""
        acc_flag = "  " if gp_acc >= _TARGET_GP_ACC else "⚠️"
        return (
            f"| {label:<8} | {n:>5} | {int(units):>6} | {_rub(rev):>14} | "
            f"{_rub(gp):>13}{delta} | {_pct(gp_rate):>5} | "
            f"{acc_flag}{_pct(gp_acc):>6} | {_pct(comm_rate):>6} | "
            f"{gp_share:>7.0f}% |"
        )

    lines.append(_summary_row("A  (70%)", group_a))
    lines.append(_summary_row("B  (20%)", group_b))
    lines.append(_summary_row("C  (10%)", group_c))
    lines.append(f"|{'-'*10}|{'-'*7}|{'-'*8}|{'-'*16}|{'-'*15}|{'-'*7}|{'-'*10}|{'-'*8}|{'-'*10}|")
    lines.append(_summary_row("ИТОГО   ", cur_articles))
    lines.append("")

    # -------------------------------------------------------------------
    # Разделы 1–3: по группам
    # -------------------------------------------------------------------
    groups = [
        ("1", "A", "🏆 Группа A — 70% валовой прибыли", group_a),
        ("2", "B", "📊 Группа B — 20% валовой прибыли", group_b),
        ("3", "C", "⚠️  Группа C — хвост и убыточные", group_c),
    ]

    for num, letter, title, group in groups:
        lines.append(f"## {num}. {title}")
        lines.append("")
        lines.append(_auto_comment(f"Группа {letter}", group, total_gp_all, prev_map))
        lines.append("")

        if not group:
            lines.append("_(нет артикулов)_")
            lines.append("")
            continue

        lines.append(_group_header(show_cost))
        lines.append(_group_sep(show_cost))

        for art in group:
            prev_art = prev_map.get(art.offer_id) if prev_map else None
            lines.append(_article_row(art, prev_art, show_cost))

        # Итого по группе
        g_rev = sum(a.revenue for a in group)
        g_comm = sum(a.commission for a in group)
        g_log = sum(a.logistics for a in group)
        g_mc = sum(a.material_cost for a in group)
        g_gp = sum(a.gross_profit for a in group)
        g_tax = g_rev * 0.10
        g_gp_after_tax = g_gp - g_tax
        g_units_fbo = sum(a.units_fbo for a in group)
        g_units_fbs = sum(a.units_fbs for a in group)
        g_rate = g_gp / g_rev if g_rev else 0
        g_comm_rate = g_comm / g_rev if g_rev else 0
        g_log_u = g_log / (g_units_fbo + g_units_fbs) if (g_units_fbo + g_units_fbs) else 0

        g_accrued = g_rev - g_comm - g_log
        g_gp_acc = g_gp_after_tax / g_accrued if g_accrued else 0
        acc_flag = "  " if g_gp_acc >= _TARGET_GP_ACC else "⚠️"

        lines.append(_group_sep(show_cost))
        if show_cost:
            lines.append(
                f"| {'   ИТОГО группы ' + letter:<29} | {int(g_units_fbo):>4} | {int(g_units_fbs):>4} | "
                f"{_rub(g_rev):>12} | {_pct(g_comm_rate):>6} | {_rub(g_log_u):>9} | "
                f"{_rub(g_mc):>10} | {_rub(g_tax):>10} | {_rub(g_gp_after_tax):>12} | {_pct(g_rate):>5} | "
                f"{acc_flag}{_pct(g_gp_acc):>6} | {'':>7} |"
            )
        else:
            lines.append(
                f"| {'   ИТОГО группы ' + letter:<29} | {int(g_units_fbo):>4} | {int(g_units_fbs):>4} | "
                f"{_rub(g_rev):>12} | {_pct(g_comm_rate):>6} | {_rub(g_log_u):>9} | "
                f"{_rub(g_tax):>10} | {_rub(g_gp_after_tax):>12} | {_pct(g_rate):>5} | "
                f"{acc_flag}{_pct(g_gp_acc):>6} | {'':>7} |"
            )
        lines.append("")

        # FBO vs FBS внутри группы (если есть оба)
        fbo_arts = [a for a in group if a.units_fbo > 0]
        fbs_arts = [a for a in group if a.units_fbs > 0]
        if fbo_arts and fbs_arts:
            fbo_rev = sum(a.revenue * a.units_fbo / a.units for a in fbo_arts if a.units)
            fbs_rev = sum(a.revenue * a.units_fbs / a.units for a in fbs_arts if a.units)

            def _schema_comm(arts, schema):
                total_rev = 0.0
                total_comm = 0.0
                for a in arts:
                    sr = a.schemas.get(schema)
                    if sr:
                        total_rev += sr.revenue
                        total_comm += sr.commission
                return total_comm / total_rev * 100 if total_rev else 0

            fbo_rate = _schema_comm(fbo_arts, "FBO")
            fbs_rate = _schema_comm(fbs_arts, "FBS")
            lines.append(
                f"> FBO: {len(fbo_arts)} арт., ком. {fbo_rate:.1f}% | "
                f"FBS: {len(fbs_arts)} арт., ком. {fbs_rate:.1f}%"
            )
            lines.append("")

    # -------------------------------------------------------------------
    # Раздел 4: Аномалии расходов + ценовые рекомендации
    # -------------------------------------------------------------------
    lines.append("## 4. Аномалии расходов и ценовые рекомендации")
    lines.append("")
    lines.append(
        f"_Целевой ВП/Счёт ≥ {_TARGET_GP_ACC*100:.0f}%. Налог УСН 10% учтён. "
        f"«Δ→50%» — необходимое изменение цены до ВП/Счёт=50%, «Δ→55%» — до 55%._"
    )
    lines.append("")

    # Константы для логистики-аномалий
    LOGISTICS_SVC = {
        "MarketplaceServiceItemDirectFlowLogistic",
        "MarketplaceServiceItemReturnFlowLogistic",
        "MarketplaceServiceItemDeliveryToHandoverPlaceOzon",
        "MarketplaceServiceItemRedistributionLastMileCourier",
        "MarketplaceServiceItemRedistributionDropOffApvz",
        "MarketplaceServiceItemDropoffPVZ",
        "MarketplaceServiceItemDeliveryToHandoverPlaceCourier",
        "MarketplaceServiceItemLastMile",
    }

    TARGET_50 = 0.50
    TARGET_55 = 0.55

    def _price_for_target(rev: float, mat: float, log: float, c_rate: float, t: float) -> Optional[float]:
        """Минимальная выручка для достижения ВП/Счёт = t."""
        denom = (0.9 - c_rate) - t * (1 - c_rate)
        if denom <= 0:
            return None
        return (mat + log * (1 - t)) / denom

    # Собираем данные по артикулам из уже посчитанных
    all_articles = group_a + group_b + group_c

    # Средняя логистика/шт по магазину
    total_units_all = sum(a.units for a in all_articles if a.units > 0)
    avg_log_u = (
        sum(a.logistics for a in all_articles) / total_units_all if total_units_all else 0
    )

    # Таблица аномалий
    lines.append(
        f"| {'Артикул':<26} | {'Шт.':>5} | {'Тек.цена':>9} | {'Ком.%':>6} | "
        f"{'МС%':>5} | {'Лог./шт.':>9} | {'ВП/Счёт':>8} | {'Δ→50%':>7} | "
        f"{'Цена 50%':>9} | {'Δ→55%':>7} | {'Цена 55%':>9} | Аномалия |"
    )
    lines.append(
        f"|{'-'*28}|{'-'*7}|{'-'*11}|{'-'*8}|{'-'*7}|{'-'*11}|{'-'*10}|"
        f"{'-'*9}|{'-'*11}|{'-'*9}|{'-'*11}|{'-'*10}|"
    )

    for art in sorted(all_articles, key=lambda a: a.gp_to_accrued):
        if art.gp_to_accrued >= 0.60 and art.revenue > 0:
            continue  # норма, не показываем
        if art.revenue < 3000:
            continue  # шум

        rev = art.revenue
        units = art.units
        c_rate = art.commission_rate
        log = art.logistics
        mat = art.material_cost
        cur_price = rev / units if units else 0

        r50 = _price_for_target(rev, mat, log, c_rate, TARGET_50)
        r55 = _price_for_target(rev, mat, log, c_rate, TARGET_55)

        def _fmt_delta(r_target: Optional[float]) -> str:
            if r_target is None:
                return "   н/д"
            d = (r_target - rev) / rev * 100
            sign = "+" if d >= 0 else ""
            return f"{sign}{d:.0f}%"

        def _fmt_price(r_target: Optional[float]) -> str:
            if r_target is None or units == 0:
                return "      н/д"
            return f"{r_target/units:>8,.0f}₽"

        # Аномалии
        anomalies = []
        if art.gp_to_accrued < TARGET_50:
            flag = "🔴"
        elif art.gp_to_accrued < TARGET_55:
            flag = "⚠️ "
        else:
            flag = "   "

        mat_pct = mat / rev * 100 if rev else 0
        log_u = log / units if units else 0
        if mat_pct > 20:
            anomalies.append(f"МС {mat_pct:.0f}%")
        if log_u > avg_log_u * 2:
            anomalies.append(f"Лог.×{log_u/avg_log_u:.1f}")
        if c_rate > 0.505:
            anomalies.append(f"Ком.{c_rate*100:.0f}%")
        anom_str = ", ".join(anomalies) if anomalies else "—"

        lines.append(
            f"| {flag}{art.offer_id:<24} | {int(units):>5} | {cur_price:>8,.0f}₽ | "
            f"{_pct(c_rate):>6} | {mat_pct:>4.0f}% | {log_u:>8,.0f}₽ | "
            f"{_pct(art.gp_to_accrued):>8} | {_fmt_delta(r50):>7} | "
            f"{_fmt_price(r50):>9} | {_fmt_delta(r55):>7} | "
            f"{_fmt_price(r55):>9} | {anom_str} |"
        )

    lines.append("")

    # Итоговые выводы-блоки
    below_50 = [a for a in all_articles if 0 < a.gp_to_accrued < TARGET_50 and a.revenue > 3000]
    below_55 = [a for a in all_articles if TARGET_50 <= a.gp_to_accrued < TARGET_55 and a.revenue > 3000]

    lines.append("### Выводы по аномалиям")
    lines.append("")

    if below_50:
        lines.append("**🔴 Ниже 50% — требуют действий:**")
        for art in sorted(below_50, key=lambda a: a.revenue, reverse=True):
            rev = art.revenue
            mat = art.material_cost
            log = art.logistics
            c_rate = art.commission_rate
            units = art.units
            mat_pct = mat / rev * 100 if rev else 0
            cur_price = rev / units if units else 0
            r55 = _price_for_target(rev, mat, log, c_rate, TARGET_55)
            if r55 and r55 > rev:
                action = f"↑ цену с {cur_price:,.0f}₽ до {r55/units:,.0f}₽ (+{(r55-rev)/rev*100:.0f}%)"
            elif mat_pct > 20:
                action = f"↓ себестоимость или ↑ цену (МС {mat_pct:.0f}% — слишком высокая)"
            else:
                action = f"↑ цену минимум +{((_price_for_target(rev,mat,log,c_rate,TARGET_50) or rev)-rev)/rev*100:.0f}%"
            lines.append(f"- **{art.offer_id}** ({int(units)} шт., ВП/Счёт {art.gp_to_accrued*100:.0f}%) — {action}")
        lines.append("")

    if below_55:
        lines.append("**⚠️ 50–55% — мониторить, пересмотреть цену при следующем обновлении:**")
        for art in sorted(below_55, key=lambda a: a.revenue, reverse=True):
            rev = art.revenue
            units = art.units
            mat = art.material_cost
            log = art.logistics
            c_rate = art.commission_rate
            cur_price = rev / units if units else 0
            r55 = _price_for_target(rev, mat, log, c_rate, TARGET_55)
            delta = f"+{(r55-rev)/rev*100:.0f}%" if r55 and r55 > rev else f"{(r55-rev)/rev*100:.0f}% (есть запас)"
            lines.append(f"- **{art.offer_id}** ({int(units)} шт., ВП/Счёт {art.gp_to_accrued*100:.0f}%) — для 55%: {delta}")
        lines.append("")

    lines.append(
        f"> _Средние по магазину: ком. {sum(a.commission for a in all_articles)/sum(a.revenue for a in all_articles)*100:.1f}%, "
        f"лог./шт. {avg_log_u:,.0f}₽, ВП/Счёт {sum(a.gp_to_accrued*a.revenue for a in all_articles)/sum(a.revenue for a in all_articles)*100:.1f}%_"
    )
    lines.append("")

    return "\n".join(lines)
