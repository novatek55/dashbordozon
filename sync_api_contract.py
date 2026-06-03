import argparse
import json
import pathlib
import re
from typing import Any, Dict, List, Set, Tuple


HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"}


def load_json(path: pathlib.Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def extract_used_methods_from_client(client_path: pathlib.Path) -> List[Dict[str, str]]:
    text = client_path.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(
        r'_make_request\(\s*"(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|TRACE)"\s*,\s*"(?P<path>/[^"]+)"'
    )
    methods: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for m in pattern.finditer(text):
        method = m.group("method").upper()
        path = m.group("path")
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        methods.append({"method": method, "path": path})
    methods.sort(key=lambda x: (x["path"], x["method"]))
    return methods


def swagger_index(swagger: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    paths = swagger.get("paths", {})
    if not isinstance(paths, dict):
        return out
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            method_u = str(method).upper()
            if method_u not in HTTP_METHODS:
                continue
            if not isinstance(op, dict):
                continue
            out[(method_u, str(path))] = op
    return out


def build_contract_report(used: List[Dict[str, str]], sw: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
    matched = []
    missing = []
    deprecated = []

    for item in used:
        key = (item["method"], item["path"])
        op = sw.get(key)
        if not op:
            missing.append(item)
            continue
        rec = {
            "method": item["method"],
            "path": item["path"],
            "operationId": op.get("operationId"),
            "deprecated": bool(op.get("deprecated", False)),
            "summary": op.get("summary"),
        }
        matched.append(rec)
        if rec["deprecated"]:
            deprecated.append(rec)

    return {
        "used_total": len(used),
        "matched_total": len(matched),
        "missing_total": len(missing),
        "deprecated_total": len(deprecated),
        "matched": matched,
        "missing_in_swagger": missing,
        "deprecated_in_swagger": deprecated,
    }


def save_json(path: pathlib.Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_markdown(path: pathlib.Path, report: Dict[str, Any], info: Dict[str, Any], swagger_path: pathlib.Path) -> None:
    lines = []
    lines.append("# API Contract Check")
    lines.append("")
    lines.append(f"- Source swagger: `{swagger_path.as_posix()}`")
    lines.append(f"- API title: `{info.get('title', '')}`")
    lines.append(f"- API version: `{info.get('version', '')}`")
    lines.append(f"- Used methods: **{report['used_total']}**")
    lines.append(f"- Matched in swagger: **{report['matched_total']}**")
    lines.append(f"- Missing in swagger: **{report['missing_total']}**")
    lines.append(f"- Deprecated in swagger: **{report['deprecated_total']}**")
    lines.append("")
    lines.append("## Missing In Swagger")
    if report["missing_in_swagger"]:
        for x in report["missing_in_swagger"]:
            lines.append(f"- {x['method']} `{x['path']}`")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Deprecated In Swagger")
    if report["deprecated_in_swagger"]:
        for x in report["deprecated_in_swagger"]:
            lines.append(f"- {x['method']} `{x['path']}` (`{x.get('operationId') or ''}`)")
    else:
        lines.append("- none")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare project API calls with local Ozon swagger.json")
    parser.add_argument("--swagger", default="swagger.json", help="Path to local swagger.json")
    parser.add_argument("--client", default="src/ozon_client.py", help="Path to client file with _make_request calls")
    parser.add_argument("--out-dir", default="api_contract", help="Output directory")
    args = parser.parse_args()

    swagger_path = pathlib.Path(args.swagger)
    client_path = pathlib.Path(args.client)
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    swagger = load_json(swagger_path)
    used = extract_used_methods_from_client(client_path)
    sw_index = swagger_index(swagger)
    report = build_contract_report(used, sw_index)

    save_json(out_dir / "used_methods.json", {"count": len(used), "methods": used})
    save_json(out_dir / "contract_report.json", report)
    save_markdown(out_dir / "contract_report.md", report, swagger.get("info", {}), swagger_path)

    print(f"Done. used={report['used_total']} matched={report['matched_total']} missing={report['missing_total']} deprecated={report['deprecated_total']}")
    print(f"Output: {(out_dir / 'contract_report.md').as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

