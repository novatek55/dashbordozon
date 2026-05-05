"""Import exported Ozon finance reports into PostgreSQL."""

import argparse
import asyncio
import json
import logging
from calendar import monthrange
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from sqlalchemy import delete, insert, select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database import close_database, db_manager, init_database
from src.models import (
    AsyncReport,
    CashFlowStatement,
    FinanceBalance,
    RealizationReport,
    ReportCompensationItem,
    Transaction,
)
from src.sync_manager import SyncManager


logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )


def parse_month(value: str) -> Tuple[int, int]:
    parsed = datetime.strptime(value, "%Y-%m")
    return parsed.year, parsed.month


def month_bounds(year: int, month: int) -> Tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year, month, monthrange(year, month)[1], 23, 59, 59, tzinfo=timezone.utc)
    return start, end


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def decimal_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


class FinanceExportImporter:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.sync_manager = SyncManager(client=None)  # parser helpers only

    async def import_month(self, year: int, month: int) -> Dict[str, int]:
        month_dir = self.base_dir / f"{year:04d}-{month:02d}"
        summary: Dict[str, int] = {
            "balances": 0,
            "transactions": 0,
            "cash_flow": 0,
            "realization": 0,
            "compensation": 0,
        }
        if not month_dir.exists():
            raise FileNotFoundError(month_dir)

        start, end = month_bounds(year, month)
        month_start = datetime(year, month, 1, tzinfo=timezone.utc)
        month_end = datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1, tzinfo=timezone.utc)

        async with db_manager.session() as session:
            balance_path = month_dir / "finance_balance.json"
            if balance_path.exists():
                payload = load_json(balance_path)
                total = payload.get("total", {}) if isinstance(payload.get("total"), dict) else {}
                payments = total.get("payments") or []
                payment_amount = None
                payment_currency = None
                if payments and isinstance(payments[0], dict):
                    payment_amount = decimal_value(payments[0].get("value"))
                    payment_currency = payments[0].get("currency_code")

                stmt = pg_insert(FinanceBalance).values(
                    period_from=start,
                    period_to=end,
                    opening_balance=decimal_value(((total.get("opening_balance") or {}) if isinstance(total.get("opening_balance"), dict) else {}).get("value")),
                    closing_balance=decimal_value(((total.get("closing_balance") or {}) if isinstance(total.get("closing_balance"), dict) else {}).get("value")),
                    accrued_amount=decimal_value(((total.get("accrued") or {}) if isinstance(total.get("accrued"), dict) else {}).get("value")),
                    payment_amount=payment_amount,
                    currency=(
                        ((total.get("opening_balance") or {}) if isinstance(total.get("opening_balance"), dict) else {}).get("currency_code")
                        or ((total.get("closing_balance") or {}) if isinstance(total.get("closing_balance"), dict) else {}).get("currency_code")
                        or payment_currency
                    ),
                    raw_data=payload,
                    last_synced_at=datetime.now(timezone.utc),
                )
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_finance_balances_period",
                    set_={
                        "opening_balance": stmt.excluded.opening_balance,
                        "closing_balance": stmt.excluded.closing_balance,
                        "accrued_amount": stmt.excluded.accrued_amount,
                        "payment_amount": stmt.excluded.payment_amount,
                        "currency": stmt.excluded.currency,
                        "raw_data": stmt.excluded.raw_data,
                        "last_synced_at": stmt.excluded.last_synced_at,
                    },
                )
                await session.execute(stmt)
                summary["balances"] = 1

            tx_path = month_dir / "transaction_list.json"
            if tx_path.exists():
                payload = load_json(tx_path)
                operations = ((payload.get("result") or {}) if isinstance(payload.get("result"), dict) else {}).get("operations") or []
                await session.execute(
                    delete(Transaction).where(
                        Transaction.operation_date >= start,
                        Transaction.operation_date <= end,
                    )
                )
                for operation in operations:
                    operation_id = operation.get("operation_id")
                    if not operation_id:
                        continue
                    posting = operation.get("posting", {}) if isinstance(operation.get("posting"), dict) else {}
                    transaction_dict = {
                        "transaction_id": int(operation_id),
                        "operation_id": int(operation_id),
                        "operation_type": operation.get("operation_type"),
                        "operation_date": self.sync_manager._parse_operation_datetime(operation.get("operation_date")),
                        "posting_number": posting.get("posting_number"),
                        "amount": self.sync_manager._parse_decimal_flexible(operation.get("amount")),
                        "currency": operation.get("currency_code") or operation.get("currency"),
                        "type": operation.get("type"),
                        "description": operation.get("operation_type_name") or operation.get("description"),
                        "raw_data": operation,
                        "last_synced_at": datetime.now(timezone.utc),
                    }
                    stmt = pg_insert(Transaction).values(**transaction_dict)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["transaction_id"],
                        set_={k: v for k, v in transaction_dict.items() if k != "transaction_id"},
                    )
                    await session.execute(stmt)
                    await self.sync_manager._replace_transaction_children(
                        session=session,
                        transaction_id=int(operation_id),
                        posting_number=posting.get("posting_number"),
                        operation_date=transaction_dict["operation_date"],
                        raw_data=operation,
                    )
                    summary["transactions"] += 1

            cash_flow_path = month_dir / "cash_flow_statement.json"
            if cash_flow_path.exists():
                payload = load_json(cash_flow_path)
                rows = ((payload.get("result") or {}) if isinstance(payload.get("result"), dict) else {}).get("cash_flows") or []
                await session.execute(
                    delete(CashFlowStatement).where(
                        CashFlowStatement.date >= start,
                        CashFlowStatement.date <= end,
                    )
                )
                for item in rows:
                    period = item.get("period", {}) if isinstance(item.get("period"), dict) else {}
                    begin = self.sync_manager._parse_datetime_flexible(period.get("begin"))
                    if begin is None:
                        continue
                    orders_amount = decimal_value(item.get("orders_amount")) or Decimal("0")
                    returns_amount = decimal_value(item.get("returns_amount")) or Decimal("0")
                    commission_amount = decimal_value(item.get("commission_amount")) or Decimal("0")
                    services_amount = decimal_value(item.get("services_amount")) or Decimal("0")
                    item_delivery = decimal_value(item.get("item_delivery_and_return_amount")) or Decimal("0")
                    row = {
                        "date": begin,
                        "revenue": orders_amount,
                        "commission": commission_amount,
                        "delivery_cost": item_delivery,
                        "return_cost": returns_amount,
                        "other_costs": services_amount,
                        "net_amount": orders_amount + returns_amount + commission_amount + services_amount + item_delivery,
                        "raw_data": item,
                        "last_synced_at": datetime.now(timezone.utc),
                    }
                    await session.execute(insert(CashFlowStatement).values(**row))
                    summary["cash_flow"] += 1

            realization_path = month_dir / "realization.json"
            if realization_path.exists():
                payload = load_json(realization_path)
                result = payload.get("result", {}) if isinstance(payload.get("result"), dict) else {}
                header = result.get("header", {}) if isinstance(result.get("header"), dict) else {}
                rows = result.get("rows", []) if isinstance(result.get("rows"), list) else []

                await session.execute(
                    delete(RealizationReport).where(
                        RealizationReport.date >= month_start,
                        RealizationReport.date < month_end,
                    )
                )

                doc_date = self.sync_manager._parse_datetime_flexible(header.get("doc_date")) or month_start
                for row in rows:
                    item = row.get("item", {}) if isinstance(row.get("item"), dict) else {}
                    delivery_commission = row.get("delivery_commission", {}) if isinstance(row.get("delivery_commission"), dict) else {}
                    return_commission = row.get("return_commission", {}) if isinstance(row.get("return_commission"), dict) else {}
                    quantity = self.sync_manager._parse_int_flexible(delivery_commission.get("quantity")) or 0
                    price_per_instance = self.sync_manager._parse_decimal_flexible(row.get("seller_price_per_instance")) or Decimal("0")
                    total_amount = price_per_instance * quantity if quantity else price_per_instance
                    sku = self.sync_manager._parse_int_flexible(item.get("sku")) or 0
                    if not sku:
                        continue

                    row_data = {
                        "date": doc_date,
                        "sku": sku,
                        "offer_id": item.get("offer_id"),
                        "name": item.get("name"),
                        "quantity": quantity,
                        "price": price_per_instance,
                        "total_amount": total_amount,
                        "commission_percent": self.sync_manager._parse_decimal_flexible(row.get("commission_ratio")) * 100
                        if self.sync_manager._parse_decimal_flexible(row.get("commission_ratio")) is not None
                        else None,
                        "commission_amount": self.sync_manager._parse_decimal_flexible(delivery_commission.get("commission")),
                        "payout_amount": self.sync_manager._parse_decimal_flexible(delivery_commission.get("bonus")),
                        "delivery_cost": self.sync_manager._parse_decimal_flexible(delivery_commission.get("amount")),
                        "total_payout": self.sync_manager._parse_decimal_flexible(delivery_commission.get("total")),
                        "raw_data": {
                            "header": header,
                            "row": row,
                            "return_commission": return_commission,
                        },
                        "last_synced_at": datetime.now(timezone.utc),
                    }
                    inserted = await session.execute(insert(RealizationReport).returning(RealizationReport.id).values(**row_data))
                    realization_report_id = inserted.scalar_one()
                    await self.sync_manager._upsert_realization_detail(
                        session=session,
                        realization_report_id=realization_report_id,
                        raw_data=row_data["raw_data"],
                    )
                    summary["realization"] += 1

            compensation_path = month_dir / f"compensation_{year:04d}-{month:02d}.xlsx"
            if compensation_path.exists():
                file_bytes = compensation_path.read_bytes()
                rows = self.sync_manager._parse_tabular_report_file(
                    file_bytes,
                    [
                        "дата",
                        "сум",
                        "компенса",
                        "декомпенса",
                        "артикул",
                        "sku",
                        "товар",
                        "posting",
                        "отправлен",
                        "основание",
                    ],
                )
                report_code = f"manual_compensation_{year:04d}-{month:02d}"
                report_id = self.sync_manager._report_code_to_id(report_code)
                await session.execute(delete(ReportCompensationItem).where(ReportCompensationItem.report_id == report_id))
                await session.execute(
                    delete(ReportCompensationItem).where(
                        ReportCompensationItem.report_kind == "compensation",
                        ReportCompensationItem.report_month == month_start,
                    )
                )

                report_data = {
                    "report_id": report_id,
                    "report_type": "compensation",
                    "status": "success",
                    "date_from": month_start,
                    "date_to": end,
                    "filters": {"date": f"{year:04d}-{month:02d}", "source": "manual_file_import"},
                    "file_url": str(compensation_path),
                    "file_size": len(file_bytes),
                    "row_count": len(rows),
                    "created_at": datetime.now(timezone.utc),
                    "completed_at": datetime.now(timezone.utc),
                    "raw_data": {
                        "source_file": str(compensation_path),
                        "source": "manual_file_import",
                    },
                    "last_synced_at": datetime.now(timezone.utc),
                }
                stmt_report = pg_insert(AsyncReport).values(**report_data)
                stmt_report = stmt_report.on_conflict_do_update(
                    index_elements=["report_id"],
                    set_={k: v for k, v in report_data.items() if k != "report_id"},
                )
                await session.execute(stmt_report)

                for line_no, row in enumerate(rows, start=1):
                    normalized = self.sync_manager._normalize_compensation_report_row(row, "compensation", month_start)
                    if not normalized:
                        continue
                    row_data = {
                        "report_id": report_id,
                        "line_no": line_no,
                        "report_kind": "compensation",
                        **normalized,
                        "last_synced_at": datetime.now(timezone.utc),
                    }
                    stmt_item = pg_insert(ReportCompensationItem).values(**row_data)
                    stmt_item = stmt_item.on_conflict_do_update(
                        constraint="uq_report_compensation_items_report_line",
                        set_={k: v for k, v in row_data.items() if k not in {"report_id", "line_no"}},
                    )
                    await session.execute(stmt_item)
                    summary["compensation"] += 1

        return summary


async def async_main(args: argparse.Namespace) -> int:
    setup_logging()
    await init_database()
    try:
        if not await db_manager.health_check():
            raise RuntimeError("Database connection failed")
        importer = FinanceExportImporter(Path(args.input_dir).resolve())
        total = {"balances": 0, "transactions": 0, "cash_flow": 0, "realization": 0, "compensation": 0}
        for year, month in args.months:
            month_result = await importer.import_month(year, month)
            logger.info("Imported %s-%s: %s", year, f"{month:02d}", month_result)
            for key, value in month_result.items():
                total[key] += value
        logger.info("Import completed: %s", total)
    finally:
        await close_database()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import exported Ozon finance reports into PostgreSQL")
    parser.add_argument("--months", nargs="+", type=parse_month, required=True, help="Months in YYYY-MM format")
    parser.add_argument("--input-dir", default="exports/finance_reports", help="Directory with exported finance reports")
    return parser


def main() -> int:
    return asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
