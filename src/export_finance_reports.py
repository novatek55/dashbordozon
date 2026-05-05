"""Export finance reports from Ozon Seller API for selected months."""

import argparse
import asyncio
import json
import logging
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from src.config import settings
from src.ozon_client import OzonAPIError, OzonClient


logger = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )


def parse_month(value: str) -> Tuple[int, int]:
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid month '{value}', expected YYYY-MM") from exc
    return parsed.year, parsed.month


def month_bounds(year: int, month: int) -> Tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end_day = monthrange(year, month)[1]
    end = datetime(year, month, end_day, 23, 59, 59, tzinfo=timezone.utc)
    return start, end


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def guess_suffix(file_url: Optional[str], fallback: str = ".bin") -> str:
    if not file_url:
        return fallback
    suffix = Path(urlparse(file_url).path).suffix
    return suffix or fallback


async def wait_report_ready(
    client: OzonClient,
    report_code: str,
    timeout_seconds: int = 600,
    poll_seconds: int = 10,
) -> Dict[str, Any]:
    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    last_payload: Dict[str, Any] = {}

    while datetime.now(timezone.utc) < deadline:
        info = await client.get_report_info(report_code)
        result = info.get("result", {}) if isinstance(info, dict) else {}
        last_payload = result
        status = str(result.get("status") or "").lower()
        if status == "success" and result.get("file"):
            return result
        if status in {"failed", "error", "cancelled"}:
            raise OzonAPIError(
                f"Async report {report_code} finished with status '{status}'",
                response_data=result,
            )
        await asyncio.sleep(poll_seconds)

    raise OzonAPIError(
        f"Timed out waiting for async report {report_code}",
        response_data=last_payload,
    )


async def export_async_report(
    client: OzonClient,
    target_dir: Path,
    report_kind: str,
    report_month: str,
) -> Dict[str, Any]:
    if report_kind == "compensation":
        created = await client.create_report_compensation(report_month, language="RU")
    elif report_kind == "decompensation":
        created = await client.create_report_decompensation(report_month, language="RU")
    else:
        raise ValueError(f"Unsupported async report kind: {report_kind}")

    report_code = str((created.get("result") or {}).get("code") or "").strip()
    if not report_code:
        raise OzonAPIError(f"{report_kind} report creation returned no code", response_data=created)

    ready = await wait_report_ready(client, report_code)
    file_url = ready.get("file")
    if not file_url:
        raise OzonAPIError(f"{report_kind} report {report_code} has no file URL", response_data=ready)

    suffix = guess_suffix(file_url, ".xlsx")
    file_name = f"{report_kind}_{report_month}{suffix}"
    file_path = target_dir / file_name
    file_bytes = await client.download_file(file_url)
    file_path.write_bytes(file_bytes)

    meta = {
        "report_kind": report_kind,
        "report_month": report_month,
        "report_code": report_code,
        "status": ready.get("status"),
        "file_name": file_name,
    }
    write_json(target_dir / f"{report_kind}_{report_month}_meta.json", meta)
    return meta


async def export_month(client: OzonClient, year: int, month: int, base_dir: Path) -> Dict[str, Any]:
    start, end = month_bounds(year, month)
    report_month = f"{year:04d}-{month:02d}-01"
    out_dir = ensure_dir(base_dir / month_key(year, month))
    summary: Dict[str, Any] = {
        "month": month_key(year, month),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "reports": {},
    }

    logger.info("Exporting finance reports for %s", summary["month"])

    try:
        balance = await client.get_finance_balance(start, end)
        write_json(out_dir / "finance_balance.json", balance)
        summary["reports"]["finance_balance"] = "finance_balance.json"
    except OzonAPIError as exc:
        summary["reports"]["finance_balance"] = {"error": str(exc), "status_code": exc.status_code}

    try:
        transactions = {"result": {"operations": []}}
        async for batch in client.get_all_transactions(start, end):
            transactions["result"]["operations"].extend(batch)
        write_json(out_dir / "transaction_list.json", transactions)
        summary["reports"]["transaction_list"] = len(transactions["result"]["operations"])
    except OzonAPIError as exc:
        summary["reports"]["transaction_list"] = {"error": str(exc), "status_code": exc.status_code}

    try:
        transaction_totals = await client.get_transaction_totals(start, end)
        write_json(out_dir / "transaction_totals.json", transaction_totals)
        summary["reports"]["transaction_totals"] = "transaction_totals.json"
    except OzonAPIError as exc:
        summary["reports"]["transaction_totals"] = {"error": str(exc), "status_code": exc.status_code}

    try:
        cash_flow = {"result": {"cash_flows": []}}
        async for batch in client.get_all_cash_flow_statements(start, end):
            cash_flow["result"]["cash_flows"].extend(batch)
        write_json(out_dir / "cash_flow_statement.json", cash_flow)
        summary["reports"]["cash_flow_statement"] = len(cash_flow["result"]["cash_flows"])
    except OzonAPIError as exc:
        summary["reports"]["cash_flow_statement"] = {"error": str(exc), "status_code": exc.status_code}

    try:
        realization = await client.get_realization_report(year, month)
        write_json(out_dir / "realization.json", realization)
        summary["reports"]["realization"] = "realization.json"
    except OzonAPIError as exc:
        summary["reports"]["realization"] = {"error": str(exc), "status_code": exc.status_code}

    try:
        mutual_settlement = await client.get_mutual_settlement(end)
        write_json(out_dir / "mutual_settlement.json", mutual_settlement)
        summary["reports"]["mutual_settlement"] = "mutual_settlement.json"
    except OzonAPIError as exc:
        summary["reports"]["mutual_settlement"] = {"error": str(exc), "status_code": exc.status_code}

    try:
        b2b_sales = await client.get_b2b_sales(start, end)
        write_json(out_dir / "document_b2b_sales.json", b2b_sales)
        summary["reports"]["document_b2b_sales"] = "document_b2b_sales.json"
    except OzonAPIError as exc:
        summary["reports"]["document_b2b_sales"] = {"error": str(exc), "status_code": exc.status_code}

    async_reports: Dict[str, Any] = {}
    for report_kind in ("compensation", "decompensation"):
        try:
            async_reports[report_kind] = await export_async_report(client, out_dir, report_kind, report_month)
        except OzonAPIError as exc:
            async_reports[report_kind] = {"error": str(exc), "status_code": exc.status_code}
            logger.warning("Skipping %s for %s: %s", report_kind, summary["month"], exc)
    summary["reports"]["async_reports"] = async_reports

    write_json(out_dir / "summary.json", summary)
    return summary


async def async_main(args: argparse.Namespace) -> int:
    setup_logging(settings.log_level)
    output_dir = ensure_dir(Path(args.output_dir).resolve())
    summaries: List[Dict[str, Any]] = []

    async with OzonClient(
        client_id=settings.ozon_client_id,
        api_key=settings.ozon_api_key,
        performance_client_id=settings.ozon_performance_client_id,
        performance_client_secret=settings.ozon_performance_client_secret,
        max_concurrent_requests=settings.max_concurrent_requests,
    ) as client:
        for year, month in args.months:
            summaries.append(await export_month(client, year, month, output_dir))

    write_json(output_dir / "summary.json", {"generated_at": datetime.now(timezone.utc).isoformat(), "months": summaries})
    logger.info("Finance export completed: %s", output_dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Ozon finance reports for selected months")
    parser.add_argument(
        "--months",
        nargs="+",
        type=parse_month,
        required=True,
        help="Months in YYYY-MM format, for example 2026-02 2026-03",
    )
    parser.add_argument(
        "--output-dir",
        default="exports/finance_reports",
        help="Directory for exported JSON/XLSX files",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
