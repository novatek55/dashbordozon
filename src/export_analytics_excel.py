"""Convert raw analytics endpoint exports to Excel workbooks for quick review."""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_records(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {"value": item} for item in value]
    if isinstance(value, dict):
        return [value]
    return [{"value": value}]


def flatten_records(records: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    rows = list(records)
    if not rows:
        return pd.DataFrame()
    return pd.json_normalize(rows, sep=".")


def write_sheet(writer: pd.ExcelWriter, sheet_name: str, value: Any) -> None:
    df = flatten_records(ensure_records(value))
    if df.empty:
        df = pd.DataFrame([{"info": "no rows"}])
    df.to_excel(writer, sheet_name=sheet_name[:31], index=False)


def export_endpoint_file(json_path: Path) -> Path:
    payload = load_json(json_path)
    xlsx_path = json_path.with_suffix(".xlsx")

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        write_sheet(
            writer,
            "meta",
            [
                {
                    "endpoint": payload.get("endpoint"),
                    "status": payload.get("status"),
                    "request_payload": json.dumps(payload.get("request_payload"), ensure_ascii=False),
                }
            ],
        )

        response = payload.get("response")
        if isinstance(response, dict):
            if "result" in response and isinstance(response["result"], dict):
                write_sheet(writer, "result_rows", response["result"].get("rows", []))
            if "items" in response:
                write_sheet(writer, "items", response.get("items", []))
            if "data" in response:
                write_sheet(writer, "data", response.get("data", []))
            if "total" in response:
                write_sheet(writer, "total", response.get("total"))
            if "current_tariff" in response:
                write_sheet(writer, "current_tariff", response.get("current_tariff"))
            if "data" not in response and "items" not in response and "result" not in response:
                write_sheet(writer, "response", response)
        else:
            write_sheet(writer, "response", response)

        attempts = payload.get("attempts")
        if attempts:
            write_sheet(writer, "attempts", attempts)

    return xlsx_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert analytics JSON exports to Excel")
    parser.add_argument(
        "export_dir",
        nargs="?",
        default=None,
        help="Directory with analytics endpoint JSON files",
    )
    return parser.parse_args()


def find_latest_export_dir(base_dir: Path) -> Path:
    candidates = sorted(
        [path for path in base_dir.glob("analytics_endpoints_*") if path.is_dir()],
        key=lambda path: path.name,
    )
    if not candidates:
        raise FileNotFoundError("No analytics_endpoints_* export directories found")
    return candidates[-1]


def main() -> int:
    args = parse_args()
    base_dir = Path("exports").resolve()
    export_dir = Path(args.export_dir).resolve() if args.export_dir else find_latest_export_dir(base_dir)

    json_files = sorted(
        path
        for path in export_dir.glob("*.json")
        if path.name != "summary.json"
    )
    if not json_files:
        raise FileNotFoundError(f"No endpoint JSON files found in {export_dir}")

    created_files = [export_endpoint_file(path) for path in json_files]

    summary_rows = [{"file": path.name} for path in created_files]
    summary_xlsx = export_dir / "analytics_endpoints_overview.xlsx"
    with pd.ExcelWriter(summary_xlsx, engine="openpyxl") as writer:
        write_sheet(writer, "files", summary_rows)

    print(str(export_dir))
    for file_path in created_files:
        print(str(file_path))
    print(str(summary_xlsx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
