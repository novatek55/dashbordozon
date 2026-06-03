import argparse
import copy
import datetime as dt
import json
import os
from typing import Any, Dict, List, Tuple

import requests


HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}


def download_spec(url: str) -> Dict[str, Any]:
    """Download OpenAPI/Swagger spec from URL and return parsed JSON."""
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to download spec from {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Response is not valid JSON: {exc}") from exc


def load_spec(file_path: str) -> Dict[str, Any]:
    """Load spec JSON from local disk."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to load spec from {file_path}: {exc}") from exc


def save_spec(spec: Dict[str, Any], file_path: str) -> None:
    """Save spec JSON to local disk."""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=2, sort_keys=True)
    except OSError as exc:
        raise RuntimeError(f"Failed to save spec to {file_path}: {exc}") from exc


def normalize_parameters(parameters: Any) -> List[Dict[str, Any]]:
    if not isinstance(parameters, list):
        return []
    result: List[Dict[str, Any]] = []
    for p in parameters:
        if not isinstance(p, dict):
            continue
        result.append(
            {
                "name": p.get("name"),
                "in": p.get("in"),
                "required": p.get("required"),
                "schema": p.get("schema"),
                "type": p.get("type"),
                "description": p.get("description"),
            }
        )
    result.sort(key=lambda x: (str(x.get("in")), str(x.get("name"))))
    return result


def normalize_responses(responses: Any) -> Dict[str, Any]:
    if not isinstance(responses, dict):
        return {}
    normalized: Dict[str, Any] = {}
    for code, body in responses.items():
        if not isinstance(body, dict):
            normalized[str(code)] = body
            continue
        normalized[str(code)] = {
            "description": body.get("description"),
            "content": body.get("content"),
            "schema": body.get("schema"),
        }
    return dict(sorted(normalized.items(), key=lambda x: x[0]))


def parse_methods(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Parse methods from OpenAPI/Swagger spec.
    Returns mapping key -> method metadata, where key is "METHOD path".
    """
    methods: Dict[str, Dict[str, Any]] = {}
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return methods

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            method_l = str(method).lower()
            if method_l not in HTTP_METHODS or not isinstance(op, dict):
                continue

            method_u = method_l.upper()
            key = f"{method_u} {path}"
            methods[key] = {
                "path": path,
                "method": method_u,
                "operationId": op.get("operationId"),
                "summary": op.get("summary"),
                "description": op.get("description"),
                "parameters": normalize_parameters(op.get("parameters", [])),
                "responses": normalize_responses(op.get("responses", {})),
            }
    return dict(sorted(methods.items(), key=lambda x: x[0]))


def compare_methods(
    old_methods: Dict[str, Dict[str, Any]],
    new_methods: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare old and new method maps and return structured diff."""
    old_keys = set(old_methods.keys())
    new_keys = set(new_methods.keys())

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    common = sorted(old_keys & new_keys)

    changed: List[Dict[str, Any]] = []
    for key in common:
        old_item = old_methods[key]
        new_item = new_methods[key]
        field_changes: Dict[str, Dict[str, Any]] = {}
        for field in ("operationId", "summary", "description", "parameters", "responses"):
            if old_item.get(field) != new_item.get(field):
                field_changes[field] = {
                    "old": old_item.get(field),
                    "new": new_item.get(field),
                }
        if field_changes:
            changed.append({"method_key": key, "changes": field_changes})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "stats": {
            "old_total": len(old_methods),
            "new_total": len(new_methods),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
    }


def apply_change_marks(
    new_methods: Dict[str, Dict[str, Any]],
    diff: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Annotate methods with change status for easy reading."""
    result = copy.deepcopy(new_methods)
    added_set = set(diff.get("added", []))
    changed_map = {x["method_key"]: x for x in diff.get("changed", [])}

    for key, item in result.items():
        if key in added_set:
            item["_status"] = "added"
        elif key in changed_map:
            item["_status"] = "changed"
            item["_changed_fields"] = sorted(changed_map[key]["changes"].keys())
        else:
            item["_status"] = "unchanged"
    return result


def save_methods(methods: Dict[str, Dict[str, Any]], file_path: str) -> None:
    """Save methods as readable JSON."""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(methods, f, ensure_ascii=False, indent=2, sort_keys=True)
    except OSError as exc:
        raise RuntimeError(f"Failed to save methods to {file_path}: {exc}") from exc


def format_report(diff: Dict[str, Any], url: str) -> str:
    now = dt.datetime.now().isoformat(timespec="seconds")
    lines = [
        f"# API Methods Update Report",
        f"",
        f"- Timestamp: {now}",
        f"- Source URL: {url}",
        f"- Old methods: {diff['stats']['old_total']}",
        f"- New methods: {diff['stats']['new_total']}",
        f"- Added: {diff['stats']['added']}",
        f"- Removed: {diff['stats']['removed']}",
        f"- Changed: {diff['stats']['changed']}",
        "",
    ]

    if diff["added"]:
        lines.append("## Added")
        lines.extend([f"- {k}" for k in diff["added"]])
        lines.append("")

    if diff["removed"]:
        lines.append("## Removed")
        lines.extend([f"- {k}" for k in diff["removed"]])
        lines.append("")

    if diff["changed"]:
        lines.append("## Changed")
        for item in diff["changed"]:
            fields = ", ".join(sorted(item["changes"].keys()))
            lines.append(f"- {item['method_key']} (fields: {fields})")
        lines.append("")

    if not diff["added"] and not diff["removed"] and not diff["changed"]:
        lines.append("No changes detected.")

    return "\n".join(lines).strip() + "\n"


def save_report(report_text: str, file_path: str) -> None:
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(report_text)
    except OSError as exc:
        raise RuntimeError(f"Failed to save report to {file_path}: {exc}") from exc


def ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Failed to create directory {path}: {exc}") from exc


def run(url: str, data_dir: str) -> Tuple[str, str, str]:
    ensure_dir(data_dir)
    spec_path = os.path.join(data_dir, "openapi_spec.json")
    methods_path = os.path.join(data_dir, "api_methods.json")
    report_path = os.path.join(data_dir, "api_update_report.md")

    old_methods: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(spec_path):
        old_spec = load_spec(spec_path)
        old_methods = parse_methods(old_spec)

    new_spec = download_spec(url)
    save_spec(new_spec, spec_path)
    new_methods = parse_methods(new_spec)

    diff = compare_methods(old_methods, new_methods)
    marked_methods = apply_change_marks(new_methods, diff)
    save_methods(marked_methods, methods_path)

    report_text = format_report(diff, url)
    save_report(report_text, report_path)

    return methods_path, report_path, spec_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Track OpenAPI/Swagger methods and local change history."
    )
    parser.add_argument("url", help="URL to OpenAPI/Swagger JSON (e.g. swagger.json)")
    parser.add_argument("data_dir", help="Local directory to store spec/methods/report")
    args = parser.parse_args()

    try:
        methods_path, report_path, spec_path = run(args.url, args.data_dir)
        print("Done.")
        print(f"Spec file: {spec_path}")
        print(f"Methods file: {methods_path}")
        print(f"Report file: {report_path}")
        print("")
        print(
            "Note: For YAML support in future, parse response with PyYAML "
            "(yaml.safe_load) when URL/file ends with .yaml/.yml."
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

