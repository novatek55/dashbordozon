from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiohttp

DEFAULT_RELAY_HTTP = "http://127.0.0.1:19000"
DEFAULT_RELAY_TOKEN = "codex-browser-relay-dev-token"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Capture Network requests from a browser tab via CDP and save request/response payloads."
    )
    p.add_argument("--relay-http", default=DEFAULT_RELAY_HTTP)
    p.add_argument("--relay-token", default=DEFAULT_RELAY_TOKEN)
    p.add_argument(
        "--url-contains",
        default="seller.ozon.ru",
        help="Attach to first tab whose URL contains this substring",
    )
    p.add_argument(
        "--api-contains",
        default="",
        help="Comma-separated substrings to filter URLs (e.g. upsert-items,supplier-drafts)",
    )
    p.add_argument("--seconds", type=int, default=30, help="Capture duration")
    p.add_argument("--max-post-bytes", type=int, default=1024 * 1024)
    p.add_argument("--output", default="exports/cdp_network_capture.json")
    return p.parse_args()


async def find_target_id(
    session: aiohttp.ClientSession, relay_http: str, relay_token: str, url_contains: str
) -> tuple[str, str]:
    async with session.get(f"{relay_http}/json/list?token={relay_token}") as resp:
        tabs = await resp.json()
    for t in tabs:
        url = str(t.get("url", ""))
        if url_contains in url:
            return str(t.get("id")), url
    raise RuntimeError(f"No tab found with url containing: {url_contains}")


async def run_capture(args: argparse.Namespace) -> dict[str, Any]:
    filters = [x.strip() for x in args.api_contains.split(",") if x.strip()]
    started_at = time.time()

    async with aiohttp.ClientSession() as session:
        target_id, target_url = await find_target_id(
            session, args.relay_http, args.relay_token, args.url_contains
        )
        ws_url = (
            f"{args.relay_http.replace('http', 'ws', 1)}"
            f"/cdp?token={args.relay_token}&targetId={target_id}"
        )

        requests: dict[str, dict[str, Any]] = {}
        events: list[dict[str, Any]] = []

        async with session.ws_connect(ws_url) as ws:
            cmd_id = 1

            async def send(method: str, params: dict[str, Any] | None = None) -> None:
                nonlocal cmd_id
                await ws.send_json(
                    {"id": cmd_id, "method": method, "params": params or {}}
                )
                cmd_id += 1

            await send("Runtime.enable")
            await send("Network.enable", {"maxPostDataSize": args.max_post_bytes})

            deadline = time.time() + args.seconds
            while time.time() < deadline:
                try:
                    msg = await ws.receive_json(timeout=1.0)
                except Exception:
                    continue

                method = msg.get("method", "")
                params = msg.get("params", {})

                if method == "Network.requestWillBeSent":
                    request = params.get("request") or {}
                    url = str(request.get("url", ""))
                    if filters and not any(f in url for f in filters):
                        continue
                    rid = str(params.get("requestId"))
                    requests[rid] = {
                        "requestId": rid,
                        "url": url,
                        "method": request.get("method"),
                        "requestHeaders": request.get("headers"),
                        "requestBody": request.get("postData"),
                        "resourceType": params.get("type"),
                        "timestamp": params.get("timestamp"),
                    }

                elif method == "Network.responseReceived":
                    rid = str(params.get("requestId"))
                    if rid not in requests:
                        continue
                    response = params.get("response") or {}
                    requests[rid]["status"] = response.get("status")
                    requests[rid]["statusText"] = response.get("statusText")
                    requests[rid]["responseHeaders"] = response.get("headers")
                    requests[rid]["mimeType"] = response.get("mimeType")

                elif method == "Network.loadingFinished":
                    rid = str(params.get("requestId"))
                    if rid not in requests:
                        continue
                    # Pull response body for tracked request.
                    body_cmd_id = cmd_id
                    await send("Network.getResponseBody", {"requestId": rid})
                    # Wait specifically for this response command.
                    while True:
                        try:
                            body_msg = await ws.receive_json(timeout=2.0)
                        except Exception:
                            break
                        if body_msg.get("id") != body_cmd_id:
                            continue
                        if "result" in body_msg:
                            body_result = body_msg.get("result") or {}
                            body = body_result.get("body", "")
                            if body_result.get("base64Encoded"):
                                requests[rid]["responseBodyBase64"] = body
                            else:
                                requests[rid]["responseBody"] = body
                        break
                    events.append(requests[rid])
                    del requests[rid]

        return {
            "targetId": target_id,
            "targetUrl": target_url,
            "startedAt": started_at,
            "endedAt": time.time(),
            "durationSec": args.seconds,
            "filters": filters,
            "events": events,
        }


def main() -> None:
    args = parse_args()
    data = asyncio.run(run_capture(args))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Captured: {len(data['events'])} requests")
    print(f"Target:   {data['targetUrl']}")
    print(f"Saved:    {out}")


if __name__ == "__main__":
    main()

