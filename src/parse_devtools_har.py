from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Parse exported DevTools HAR and extract API requests/responses."
    )
    p.add_argument("--har", required=True, help="Path to .har file exported from DevTools")
    p.add_argument(
        "--contains",
        default="",
        help="Comma-separated URL substrings to keep (e.g. upsert-items,supplier-drafts)",
    )
    p.add_argument("--output", default="exports/devtools_har_extracted.json")
    return p.parse_args()


def maybe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def decode_har_content(text: str | None, encoding: str | None) -> str | None:
    if text is None:
        return None
    if encoding == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except Exception:
            return text
    return text


def main() -> None:
    args = parse_args()
    filters = [x.strip() for x in args.contains.split(",") if x.strip()]

    har_path = Path(args.har)
    raw = json.loads(har_path.read_text(encoding="utf-8-sig"))
    entries = ((raw.get("log") or {}).get("entries")) or []

    result: list[dict[str, Any]] = []
    for e in entries:
        req = e.get("request") or {}
        res = e.get("response") or {}
        url = str(req.get("url") or "")
        if filters and not any(f in url for f in filters):
            continue

        post = (req.get("postData") or {}).get("text")
        content = res.get("content") or {}
        resp_text = decode_har_content(content.get("text"), content.get("encoding"))

        item = {
            "startedDateTime": e.get("startedDateTime"),
            "timeMs": e.get("time"),
            "method": req.get("method"),
            "url": url,
            "status": res.get("status"),
            "statusText": res.get("statusText"),
            "requestHeaders": req.get("headers"),
            "responseHeaders": res.get("headers"),
            "requestBodyRaw": post,
            "requestBody": maybe_json(post),
            "responseBodyRaw": resp_text,
            "responseBody": maybe_json(resp_text),
        }
        result.append(item)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Entries parsed: {len(entries)}")
    print(f"Entries kept:   {len(result)}")
    print(f"Saved:          {out}")


if __name__ == "__main__":
    main()

