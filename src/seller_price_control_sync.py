"""Automated Ozon Seller price-control sync through an authenticated browser profile."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from src.database import db_manager
from src.seller_price_control import (
    extract_customer_prices_from_capture,
    find_known_price_contexts,
    upsert_customer_prices,
)

DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9224
DEFAULT_PROFILE_DIR = "/var/lib/ozon-dashboard/ozon-seller-price-profile"
DEFAULT_PRICE_URL = "https://seller.ozon.ru/app/prices/control"


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_chromium_executable(explicit: str = "") -> str:
    candidates = [
        explicit,
        os.getenv("OZON_CHROMIUM_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("Chromium/Chrome executable not found")


async def ensure_chromium(
    *,
    host: str,
    port: int,
    profile_dir: str,
    executable: str = "",
    headless: bool = True,
    wait_sec: float = 20.0,
) -> bool:
    if is_port_open(host, port):
        return False

    chrome = find_chromium_executable(executable)
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome,
        f"--remote-debugging-address={host}",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ]
    if headless:
        cmd.extend(["--headless=new", "--no-sandbox"])
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if is_port_open(host, port):
            await asyncio.sleep(0.5)
            return True
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Chromium did not open CDP port {host}:{port}")


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json()


async def find_or_create_target(
    session: aiohttp.ClientSession,
    cdp_http: str,
    url_contains: str,
    navigate_url: str,
) -> tuple[str, str]:
    payload = await _get_json(session, f"{cdp_http}/json/list")
    tabs = payload if isinstance(payload, list) else payload.get("value", [])
    for tab in tabs:
        url = str(tab.get("url", ""))
        if url_contains in url:
            return str(tab["webSocketDebuggerUrl"]), url

    new_url = f"{cdp_http}/json/new?{quote(navigate_url, safe='')}"
    try:
        created = await _get_json(session, new_url)
    except Exception:
        async with session.put(new_url) as resp:
            resp.raise_for_status()
            created = await resp.json()
    return str(created["webSocketDebuggerUrl"]), str(created.get("url", navigate_url))


async def send_cdp(ws: aiohttp.ClientWebSocketResponse, command_id: int, method: str, params: dict[str, Any] | None = None) -> None:
    await ws.send_json({"id": command_id, "method": method, "params": params or {}})


async def wait_cdp_result(ws: aiohttp.ClientWebSocketResponse, command_id: int, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = await ws.receive_json(timeout=max(0.1, deadline - time.time()))
        if msg.get("id") == command_id:
            if "error" in msg:
                raise RuntimeError(f"CDP command failed: {msg['error']}")
            return msg.get("result", {})
    raise TimeoutError(f"Timed out waiting for CDP command {command_id}")


async def capture_price_control(cdp_http: str, navigate_url: str, seconds: int, max_body_bytes: int) -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        ws_url, target_url = await find_or_create_target(session, cdp_http, "seller.ozon.ru", navigate_url)
        requests: dict[str, dict[str, Any]] = {}
        events: list[dict[str, Any]] = []
        auth_url = target_url

        async with session.ws_connect(ws_url) as ws:
            command_id = 1
            for method, params in [
                ("Runtime.enable", {}),
                ("Page.enable", {}),
                ("Network.enable", {"maxPostDataSize": max_body_bytes}),
                ("Page.navigate", {"url": navigate_url}),
            ]:
                await send_cdp(ws, command_id, method, params)
                command_id += 1

            deadline = time.time() + seconds
            while time.time() < deadline:
                try:
                    msg = await ws.receive_json(timeout=1.0)
                except Exception:
                    continue
                method = msg.get("method", "")
                params = msg.get("params", {})
                if method == "Page.frameNavigated":
                    frame = params.get("frame") or {}
                    if not frame.get("parentId"):
                        auth_url = str(frame.get("url") or auth_url)
                elif method == "Network.requestWillBeSent":
                    request = params.get("request") or {}
                    url = str(request.get("url", ""))
                    if "seller.ozon.ru" not in url:
                        continue
                    rid = str(params.get("requestId"))
                    requests[rid] = {
                        "requestId": rid,
                        "url": url,
                        "method": request.get("method"),
                        "requestHeaders": request.get("headers"),
                        "requestBody": request.get("postData"),
                        "resourceType": params.get("type"),
                    }
                elif method == "Network.responseReceived":
                    rid = str(params.get("requestId"))
                    if rid not in requests:
                        continue
                    response = params.get("response") or {}
                    mime = str(response.get("mimeType") or "")
                    if "json" not in mime and "javascript" not in mime and "text" not in mime:
                        requests.pop(rid, None)
                        continue
                    requests[rid]["status"] = response.get("status")
                    requests[rid]["mimeType"] = mime
                    requests[rid]["responseHeaders"] = response.get("headers")
                elif method == "Network.loadingFinished":
                    rid = str(params.get("requestId"))
                    if rid not in requests:
                        continue
                    body_command_id = command_id
                    await send_cdp(ws, body_command_id, "Network.getResponseBody", {"requestId": rid})
                    command_id += 1
                    try:
                        body_result = await wait_cdp_result(ws, body_command_id, timeout=3.0)
                    except Exception:
                        events.append(requests.pop(rid))
                        continue
                    body = str(body_result.get("body") or "")
                    if len(body) > max_body_bytes:
                        body = body[:max_body_bytes]
                    if body_result.get("base64Encoded"):
                        requests[rid]["responseBodyBase64"] = body
                    else:
                        requests[rid]["responseBody"] = body
                    events.append(requests.pop(rid))

        return {
            "targetUrl": target_url,
            "finalUrl": auth_url,
            "durationSec": seconds,
            "events": events,
        }


async def run_sync(args: argparse.Namespace) -> dict[str, Any]:
    cdp_http = f"http://{args.cdp_host}:{args.cdp_port}"
    await ensure_chromium(
        host=args.cdp_host,
        port=args.cdp_port,
        profile_dir=args.profile_dir,
        executable=args.chromium,
        headless=not args.headed,
    )
    capture = await capture_price_control(cdp_http, args.url, args.seconds, args.max_body_bytes)
    final_url = str(capture.get("finalUrl") or "")
    records = extract_customer_prices_from_capture(capture)
    known_contexts = find_known_price_contexts(capture, set(args.known_price))
    status = "ok"
    if "signin" in final_url or "login" in final_url or "registration" in final_url:
        status = "auth_required"
    elif not records:
        status = "no_prices_found"

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "status": status,
                "records": [record.__dict__ for record in records],
                "knownPriceContexts": known_contexts,
                "capture": capture if args.save_capture else {"eventsCount": len(capture.get("events", [])), "finalUrl": final_url},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    upserted = 0
    if args.apply and records:
        await db_manager.initialize()
        try:
            upserted = await upsert_customer_prices(records)
        finally:
            await db_manager.close()
    return {
        "status": status,
        "records": len(records),
        "known_price_contexts": len(known_contexts),
        "upserted": upserted,
        "output": str(out_path),
        "final_url": final_url,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Ozon Seller price-control customer prices via browser profile.")
    parser.add_argument("--profile-dir", default=os.getenv("OZON_SELLER_BROWSER_PROFILE_DIR", DEFAULT_PROFILE_DIR))
    parser.add_argument("--chromium", default=os.getenv("OZON_CHROMIUM_PATH", ""))
    parser.add_argument("--cdp-host", default=os.getenv("OZON_SELLER_CDP_HOST", DEFAULT_CDP_HOST))
    parser.add_argument("--cdp-port", type=int, default=int(os.getenv("OZON_SELLER_CDP_PORT", str(DEFAULT_CDP_PORT))))
    parser.add_argument("--url", default=DEFAULT_PRICE_URL)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--max-body-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--output", default="exports/price_control_sync.json")
    parser.add_argument("--known-price", action="append", type=int, default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--save-capture", action="store_true")
    parser.add_argument("--headed", action="store_true")
    return parser.parse_args()


def main() -> None:
    result = asyncio.run(run_sync(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
