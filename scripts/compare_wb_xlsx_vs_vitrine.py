"""Compare WB cabinet XLSX totals with local WB vitrine totals."""
from __future__ import annotations

import asyncio
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
from typing import Dict, List, Tuple

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import settings

NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass
class XlsxSummary:
    name: str
    date_from: str
    date_to: str
    totals: Dict[str, Decimal]


def _cell_col(ref: str) -> str:
    out = []
    for ch in ref:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out)


def _cell_text(cell: ET.Element) -> str:
    is_node = cell.find("a:is", NS)
    if is_node is not None:
        t = is_node.find("a:t", NS)
        return "" if t is None or t.text is None else t.text
    v = cell.find("a:v", NS)
    return "" if v is None or v.text is None else v.text


def _dec(value: str) -> Decimal:
    s = (value or "").strip().replace(" ", "").replace(",", ".")
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def parse_wb_xlsx(path: Path) -> XlsxSummary:
    with zipfile.ZipFile(path, "r") as zf:
        root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    rows = root.find("a:sheetData", NS).findall("a:row", NS)

    data_rows: List[Dict[str, str]] = []
    for r in rows[1:]:
        row: Dict[str, str] = {}
        for c in r.findall("a:c", NS):
            col = _cell_col(c.attrib.get("r", ""))
            row[col] = _cell_text(c)
        data_rows.append(row)

    sales_dates = []
    for row in data_rows:
        sale_dt = (row.get("M") or "").strip()
        if sale_dt:
            try:
                sales_dates.append(datetime.fromisoformat(sale_dt.replace("Z", "+00:00")))
            except ValueError:
                pass
    if sales_dates:
        date_from = min(sales_dates).date().isoformat()
        date_to = max(sales_dates).date().isoformat()
    else:
        date_from = ""
        date_to = ""

    totals = {
        "Валовая выручка": sum(_dec(r.get("P", "")) for r in data_rows),
        "Комиссия маркетплейса": sum(_dec(r.get("AF", "")) for r in data_rows),
        "Логистика прямая": sum(_dec(r.get("AK", "")) for r in data_rows),
        "Эквайринг / платёжные услуги": sum(_dec(r.get("AC", "")) for r in data_rows),
        "Штрафы": sum(_dec(r.get("AO", "")) for r in data_rows),
        "Прочие удержания": sum(_dec(r.get("BI", "")) + _dec(r.get("BH", "")) + _dec(r.get("BJ", "")) for r in data_rows),
        "К выплате": sum(_dec(r.get("AH", "")) for r in data_rows),
    }
    return XlsxSummary(path.name, date_from, date_to, totals)


async def load_vitrine_totals(date_from: str, date_to: str) -> Dict[str, Decimal]:
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    date_from_obj = date.fromisoformat(date_from)
    date_to_obj = date.fromisoformat(date_to)
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(gross_revenue), 0) AS gross_revenue,
                COALESCE(SUM(marketplace_commission), 0) AS marketplace_commission,
                COALESCE(SUM(logistics_direct), 0) AS logistics_direct,
                COALESCE(SUM(acquiring), 0) AS acquiring,
                COALESCE(SUM(penalties), 0) AS penalties,
                COALESCE(SUM(other_deductions), 0) AS other_deductions,
                COALESCE(SUM(to_pay), 0) AS to_pay
            FROM wb_finance_daily
            WHERE report_date BETWEEN $1::date AND $2::date
            """,
            date_from_obj,
            date_to_obj,
        )
        return {
            "Валовая выручка": Decimal(str(row["gross_revenue"])),
            "Комиссия маркетплейса": Decimal(str(row["marketplace_commission"])),
            "Логистика прямая": Decimal(str(row["logistics_direct"])),
            "Эквайринг / платёжные услуги": Decimal(str(row["acquiring"])),
            "Штрафы": Decimal(str(row["penalties"])),
            "Прочие удержания": Decimal(str(row["other_deductions"])),
            "К выплате": Decimal(str(row["to_pay"])),
        }
    finally:
        await conn.close()


def fmt(v: Decimal) -> str:
    return f"{v:.2f}"


async def main() -> None:
    files = sorted(Path("tmp_wb_reports").rglob("*.xlsx"))
    if not files:
        print("No XLSX files in tmp_wb_reports")
        return

    for p in files:
        summary = parse_wb_xlsx(p)
        if not summary.date_from or not summary.date_to:
            print(f"\n{summary.name}: no sale dates found, skip")
            continue
        vitrine = await load_vitrine_totals(summary.date_from, summary.date_to)

        print(f"\n=== {summary.name}")
        print(f"Period: {summary.date_from} .. {summary.date_to}")
        print("Статья | XLSX | Vitrine | Delta")
        for key in [
            "Валовая выручка",
            "Комиссия маркетплейса",
            "Логистика прямая",
            "Эквайринг / платёжные услуги",
            "Штрафы",
            "Прочие удержания",
            "К выплате",
        ]:
            x = summary.totals.get(key, Decimal("0"))
            v = vitrine.get(key, Decimal("0"))
            d = v - x
            print(f"{key} | {fmt(x)} | {fmt(v)} | {fmt(d)}")


if __name__ == "__main__":
    asyncio.run(main())
