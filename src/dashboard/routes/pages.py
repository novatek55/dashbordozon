"""Dashboard routes/pages.py handlers."""
import os
from aiohttp import web
from src.dashboard.constants import HTML_PATH, COSTS_HTML_PATH, PALLETIZATION_WEB_DIR


async def index(_: web.Request) -> web.Response:
    response = web.FileResponse(path=HTML_PATH)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


async def finance_costs_page(_: web.Request) -> web.Response:
    return web.FileResponse(path=COSTS_HTML_PATH)


async def palletization_page(_: web.Request) -> web.Response:
    return web.FileResponse(path=PALLETIZATION_WEB_DIR / "index.html")


async def shared_theme_css(_: web.Request) -> web.Response:
    return web.FileResponse(path=HTML_PATH.parent / "shared-report-theme.css")


async def palletization_asset(request: web.Request) -> web.Response:
    filename = str(request.match_info.get("filename") or "").strip()
    safe_name = os.path.basename(filename)
    asset_path = PALLETIZATION_WEB_DIR / safe_name
    if not asset_path.exists() or not asset_path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path=asset_path)

