"""Export raw Ozon analytics endpoint responses for inspection."""

import argparse
import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Tuple

from src.config import settings
from src.ozon_client import OzonAPIError, OzonClient


logger = logging.getLogger(__name__)

EndpointCall = Callable[[OzonClient, Dict[str, Any]], Awaitable[Dict[str, Any]]]


def setup_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_default_dates(days_back: int) -> Tuple[str, str]:
    # Use complete closed days so the sample is stable and easier to compare.
    today = datetime.now(timezone.utc).date()
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=days_back - 1)
    return date_from.isoformat(), date_to.isoformat()


def normalize_preview(response: Any) -> Dict[str, Any]:
    if not isinstance(response, dict):
        return {"response_type": type(response).__name__}

    preview: Dict[str, Any] = {
        "top_level_keys": sorted(response.keys()),
    }
    for list_key in ("items", "data", "rows", "result"):
        value = response.get(list_key)
        if isinstance(value, list):
            preview[f"{list_key}_count"] = len(value)
            if value and isinstance(value[0], dict):
                preview[f"{list_key}_first_keys"] = sorted(value[0].keys())
        elif isinstance(value, dict):
            preview[f"{list_key}_keys"] = sorted(value.keys())
    return preview


async def call_stock_on_warehouses(client: OzonClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await client.get_stock_on_warehouses(payload)


async def call_turnover_stocks(client: OzonClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await client.get_analytics_turnover(
        date_from=datetime.strptime(payload["date_from"], "%Y-%m-%d").replace(tzinfo=timezone.utc),
        date_to=datetime.strptime(payload["date_to"], "%Y-%m-%d").replace(tzinfo=timezone.utc),
        limit=int(payload.get("limit", 1000)),
        offset=int(payload.get("offset", 0)),
    )


async def call_average_delivery_time(client: OzonClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await client.get_analytics_average_delivery_time(payload)


async def call_average_delivery_time_details(client: OzonClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await client.get_analytics_average_delivery_time_details(payload)


async def call_average_delivery_time_summary(client: OzonClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await client.get_analytics_average_delivery_time_summary(payload)


def build_endpoint_specs(date_from: str, date_to: str) -> List[Dict[str, Any]]:
    common_window = {"date_from": date_from, "date_to": date_to}
    return [
        {
            "name": "stock_on_warehouses",
            "endpoint": "/v2/analytics/stock_on_warehouses",
            "caller": call_stock_on_warehouses,
            "payload_candidates": [
                {},
                {"limit": 1000, "offset": 0},
                {"limit": 1000, "offset": 0, "warehouse_type": "ALL"},
            ],
        },
        {
            "name": "turnover_stocks",
            "endpoint": "/v1/analytics/turnover/stocks",
            "caller": call_turnover_stocks,
            "payload_candidates": [
                {**common_window, "limit": 1000, "offset": 0},
            ],
        },
        {
            "name": "average_delivery_time",
            "endpoint": "/v1/analytics/average-delivery-time",
            "caller": call_average_delivery_time,
            "payload_candidates": [
                {},
                common_window,
            ],
        },
        {
            "name": "average_delivery_time_details",
            "endpoint": "/v1/analytics/average-delivery-time/details",
            "caller": call_average_delivery_time_details,
            "payload_candidates": [
                {
                    "cluster_id": 154,
                    "filters": {
                        "delivery_schema": "ALL",
                        "supply_period": "EIGHT_WEEKS",
                    },
                    "limit": 1000,
                    "offset": 0,
                },
                {
                    "cluster_id": 2,
                    "filters": {
                        "delivery_schema": "ALL",
                        "supply_period": "FOUR_WEEKS",
                    },
                    "limit": 1000,
                    "offset": 0,
                },
            ],
        },
        {
            "name": "average_delivery_time_summary",
            "endpoint": "/v1/analytics/average-delivery-time/summary",
            "caller": call_average_delivery_time_summary,
            "payload_candidates": [
                {},
                common_window,
            ],
        },
    ]


async def try_payloads(
    client: OzonClient,
    endpoint_name: str,
    endpoint_path: str,
    caller: EndpointCall,
    payload_candidates: List[Dict[str, Any]],
    output_dir: Path,
) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []

    for index, payload in enumerate(payload_candidates, start=1):
        logger.info("Requesting %s with candidate #%s: %s", endpoint_name, index, payload)
        try:
            response = await caller(client, payload)
            result = {
                "endpoint": endpoint_path,
                "status": "success",
                "request_payload": payload,
                "response_preview": normalize_preview(response),
                "response": response,
            }
            write_json(output_dir / f"{endpoint_name}.json", result)
            return {
                "endpoint": endpoint_name,
                "status": "success",
                "used_payload": payload,
                "preview": result["response_preview"],
                "attempts": attempts,
            }
        except OzonAPIError as exc:
            attempt = {
                "payload": payload,
                "error": str(exc),
                "status_code": exc.status_code,
                "response_data": exc.response_data,
            }
            attempts.append(attempt)
            logger.warning("%s failed with candidate #%s: %s", endpoint_name, index, exc)
        except Exception as exc:
            attempt = {
                "payload": payload,
                "error": str(exc),
                "status_code": None,
            }
            attempts.append(attempt)
            logger.warning("%s failed with unexpected error on candidate #%s: %s", endpoint_name, index, exc)

    failure = {
        "endpoint": endpoint_path,
        "status": "error",
        "attempts": attempts,
    }
    write_json(output_dir / f"{endpoint_name}.json", failure)
    return {
        "endpoint": endpoint_name,
        "status": "error",
        "attempts": attempts,
    }


async def async_main(args: argparse.Namespace) -> int:
    setup_logging(settings.log_level)
    date_from, date_to = pick_default_dates(args.days_back)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = ensure_dir(Path(args.output_dir).resolve() / f"analytics_endpoints_{timestamp}")
    summary: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_from": date_from,
        "date_to": date_to,
        "output_dir": str(output_dir),
        "results": [],
    }

    async with OzonClient(
        client_id=settings.ozon_client_id,
        api_key=settings.ozon_api_key,
        performance_client_id=settings.ozon_performance_client_id,
        performance_client_secret=settings.ozon_performance_client_secret,
        max_concurrent_requests=settings.max_concurrent_requests,
    ) as client:
        for spec in build_endpoint_specs(date_from, date_to):
            result = await try_payloads(
                client=client,
                endpoint_name=spec["name"],
                endpoint_path=spec["endpoint"],
                caller=spec["caller"],
                payload_candidates=spec["payload_candidates"],
                output_dir=output_dir,
            )
            summary["results"].append(result)

    write_json(output_dir / "summary.json", summary)
    logger.info("Analytics endpoint export finished: %s", output_dir)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export raw Ozon analytics endpoint responses")
    parser.add_argument(
        "--days-back",
        type=int,
        default=28,
        help="Closed-day date window for endpoints that need date_from/date_to (default: 28)",
    )
    parser.add_argument(
        "--output-dir",
        default="exports",
        help="Base directory for JSON exports (default: exports)",
    )
    return parser.parse_args()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
