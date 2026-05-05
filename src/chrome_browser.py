"""
chrome_browser.py — управление Chrome через CDP (порт 9223) + Playwright

Функции:
  ensure_chrome()         — запустить Chrome если не запущен
  connect_cdp()           — подключиться через Playwright.connect_over_cdp
  get_or_create_page()    — найти вкладку seller.ozon.ru или открыть новую
  wait_for_ozon_ready()   — ждать готовности страницы + защита от antibot
  bff_fetch()             — POST-запрос к BFF API в контексте страницы (куки автоматически)

Первый запуск: Chrome открывается, пользователь логинится на seller.ozon.ru вручную.
Следующие запуски: куки из профиля используются автоматически.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

# ─── константы ────────────────────────────────────────────────────────────────

CDP_PORT: int = 9223
CDP_URL: str = f"http://127.0.0.1:{CDP_PORT}"
SELLER_ORIGIN: str = "https://seller.ozon.ru"
COMPANY_ID: int = int(os.getenv("OZON_COMPANY_ID", "146478"))

# Профиль Chrome с куками (создаётся при первом запуске)
_HERE = Path(__file__).resolve().parent.parent  # корень проекта
CHROME_PROFILE_DIR: Path = _HERE / "exports" / "chrome_profile_2"

# ─── поиск Chrome ──────────────────────────────────────────────────────────────

_CHROME_CANDIDATES_WIN = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
]


def _find_chrome_exe() -> str:
    """Найти chrome.exe на Windows."""
    for path in _CHROME_CANDIDATES_WIN:
        if path and Path(path).exists():
            return path
    # fallback: искать в PATH
    import shutil
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if found:
        return found
    raise RuntimeError(
        "Chrome не найден. Установите Google Chrome или укажите путь вручную."
    )


def _is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """Проверить, слушает ли кто-то на порту."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ─── запуск Chrome ─────────────────────────────────────────────────────────────

async def ensure_chrome(
    wait_sec: float = 15.0,
    profile_dir: Path | None = None,
) -> None:
    """
    Запустить Chrome с CDP на порту 9223, если он ещё не запущен.

    После первого запуска откроется окно браузера — нужно залогиниться
    на seller.ozon.ru. Куки сохраняются в профиле и используются автоматически.
    """
    if _is_port_open(CDP_PORT):
        return  # уже запущен

    chrome_exe = _find_chrome_exe()
    user_data = str(profile_dir or CHROME_PROFILE_DIR)
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        # ── anti-detection: скрыть автоматизацию от WAF/antibot ──
        "--disable-blink-features=AutomationControlled",
        "--disable-features=AutomationControlled",
        "--disable-infobars",
        "--disable-component-update",
    ]

    print(f"[chrome] Запускаю Chrome: {chrome_exe}")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Ждём пока порт откроется
    deadline = asyncio.get_event_loop().time() + wait_sec
    while asyncio.get_event_loop().time() < deadline:
        if _is_port_open(CDP_PORT):
            await asyncio.sleep(0.5)  # дать Chrome инициализироваться
            return
        await asyncio.sleep(0.3)

    raise RuntimeError(
        f"Chrome не запустился на порту {CDP_PORT} за {wait_sec:.0f} сек. "
        "Проверьте что Chrome не открыт с другим профилем."
    )


# ─── подключение через Playwright ─────────────────────────────────────────────

async def connect_cdp():
    """
    Подключиться к запущенному Chrome через Playwright CDP.

    Returns:
        (playwright, browser) — оба нужно закрыть после использования.

    Usage:
        pw, browser = await connect_cdp()
        try:
            ...
        finally:
            await browser.close()
            await pw.stop()
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(CDP_URL)

    # Инъекция anti-detection: скрыть navigator.webdriver на всех страницах
    for ctx in browser.contexts:
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            // Скрыть Playwright/CDP-сигнатуры
            delete window.__playwright;
            delete window.__pw_manual;
        """)

    return pw, browser


# ─── управление страницей ──────────────────────────────────────────────────────

async def get_or_create_page(browser, url_hint: str = "seller.ozon.ru"):
    """
    Найти уже открытую вкладку с url_hint или открыть новую.

    Returns:
        playwright Page object
    """
    # Ищем среди всех контекстов и страниц
    for ctx in browser.contexts:
        for page in ctx.pages:
            if url_hint in page.url and "signin" not in page.url:
                return page

    # Нет подходящей вкладки — открываем новую
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    page = await ctx.new_page()
    await page.goto(f"https://{url_hint}/app/supply/orders", wait_until="domcontentloaded")
    return page


def _is_logged_in_url(url: str) -> bool:
    """Проверить по URL что пользователь залогинен на seller.ozon.ru."""
    return (
        "seller.ozon.ru" in url
        and "signin" not in url
        and "login" not in url
        and "/app/" in url
    )


async def wait_for_auth(
    page,
    auth_timeout_sec: float = 180.0,
    poll_interval: float = 2.0,
) -> None:
    """
    Если пользователь не залогинен — открыть страницу входа и ждать авторизации.

    Параметры:
        auth_timeout_sec: сколько секунд ждать логина (по умолчанию 3 минуты)
        poll_interval:    интервал проверки URL

    Вызывается автоматически из OzonBrowser.__aenter__.
    """
    current_url = page.url

    # Уже залогинен — ничего делать не надо
    if _is_logged_in_url(current_url):
        return

    # Открываем страницу логина если ещё не там
    if "seller.ozon.ru" not in current_url:
        await page.goto(f"{SELLER_ORIGIN}/app/supply/orders", wait_until="domcontentloaded")
        current_url = page.url

    if _is_logged_in_url(current_url):
        return

    # Нужна авторизация — печатаем инструкцию и ждём
    print()
    print("=" * 60)
    print("  ТРЕБУЕТСЯ АВТОРИЗАЦИЯ В OZON")
    print("=" * 60)
    print("  1. В открывшемся Chrome войдите на seller.ozon.ru")
    print("  2. После входа скрипт продолжится автоматически")
    print(f"  Ожидание: {auth_timeout_sec:.0f} секунд")
    print("=" * 60)

    deadline = asyncio.get_event_loop().time() + auth_timeout_sec
    dots = 0
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(poll_interval)
        try:
            url = page.url
        except Exception:
            await asyncio.sleep(poll_interval)
            continue

        if _is_logged_in_url(url):
            print("\n[chrome] Авторизация успешна!")
            return

        dots = (dots + 1) % 4
        remaining = max(0, deadline - asyncio.get_event_loop().time())
        print(f"\r  Жду авторизацию{'.' * (dots + 1)}{' ' * (3 - dots)}  ({remaining:.0f} сек)", end="", flush=True)

    print()
    raise RuntimeError(
        f"Авторизация на seller.ozon.ru не выполнена за {auth_timeout_sec:.0f} сек. "
        "Войдите в Chrome и попробуйте снова."
    )


async def wait_for_ozon_ready(page, timeout_ms: int = 30_000) -> None:
    """
    Дождаться готовности seller.ozon.ru:
      - страница загружена
      - пользователь залогинен (или дождаться логина)
      - нет оверлея antibot/капчи
    """
    await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

    # Проверяем авторизацию и ждём если нужно
    await wait_for_auth(page)

    # Если есть antibot-оверлей — ждём его исчезновения
    antibot_selectors = [
        "[class*='antibot']",
        "[id*='antibot']",
        "[class*='captcha']",
        "[class*='challenge']",
    ]
    for sel in antibot_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                print(f"[chrome] Обнаружен antibot ({sel}), жду исчезновения...")
                await loc.wait_for(state="hidden", timeout=timeout_ms)
        except Exception:
            pass


# ─── BFF API запросы ───────────────────────────────────────────────────────────

_BFF_HEADERS = json.dumps(
    {
        "content-type": "application/json",
        "x-o3-app-name": "seller-ui",
        "x-o3-company-id": str(COMPANY_ID),
        "x-o3-language": "ru",
        "x-o3-page-type": "supply-other",
    }
)


async def bff_fetch(page, path: str, body: dict[str, Any]) -> dict[str, Any]:
    """
    Выполнить POST-запрос к BFF API seller.ozon.ru в контексте страницы.
    Куки прикрепляются автоматически браузером.

    Args:
        page:   Playwright Page (открытый на seller.ozon.ru)
        path:   путь вида "/api/supplier-drafts/..."
        body:   тело запроса

    Returns:
        dict с ответом API
    """
    body_js = json.dumps(body)
    result = await page.evaluate(
        f"""
        async () => {{
          const r = await fetch('{SELLER_ORIGIN}{path}', {{
            method: 'POST',
            headers: {_BFF_HEADERS},
            body: JSON.stringify({body_js})
          }});
          let data = null;
          try {{ data = await r.json(); }} catch {{}}
          return {{ status: r.status, data }};
        }}
        """
    )
    if result is None:
        raise RuntimeError(f"bff_fetch: нет ответа от {path}")
    status = result.get("status", 0)
    if status not in (200, 201):
        raise RuntimeError(f"bff_fetch HTTP {status} от {path}: {result.get('data')}")
    return result.get("data") or {}


async def calc_item_search(query: str, timeout_ms: int = 20000) -> dict[str, Any]:
    """
    POST к calculator.ozon.ru/p-api/.../item-search через Chrome — обходит антибот.

    1. Запускает/находит Chrome (reuse ensure_chrome).
    2. Открывает calculator.ozon.ru (антибот-челлендж проходит сам).
    3. Из JS-контекста страницы делает fetch — куки и session token сохранены.

    Returns:
        dict с ответом API (raw). Может быть list или dict.
    """
    await ensure_chrome()
    import playwright.async_api as pw_async
    async with pw_async.async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = None
        for p in ctx.pages:
            if "calculator.ozon.ru" in p.url:
                page = p
                break
        if page is None:
            page = await ctx.new_page()
            await page.goto("https://calculator.ozon.ru/", wait_until="domcontentloaded", timeout=timeout_ms)

        # Дождаться прохождения антибота: title не "Antibot Challenge"
        for _ in range(30):
            try:
                title = await page.title()
            except Exception:
                title = ""
            if "Antibot" not in title and "Challenge" not in title:
                break
            await asyncio.sleep(1)

        body_js = json.dumps({"query": query})
        result = await page.evaluate(
            f"""
            async () => {{
              const r = await fetch('https://calculator.ozon.ru/p-api/the-calculator-ozon-ru/api/item-search', {{
                method: 'POST',
                headers: {{
                  'Content-Type': 'application/json',
                  'X-O3-App-Name': 'calculator-ui',
                  'Accept': 'application/json',
                }},
                credentials: 'include',
                body: JSON.stringify({body_js})
              }});
              let data = null;
              let text = null;
              try {{ data = await r.json(); }} catch {{
                try {{ text = await r.text(); }} catch {{}}
              }}
              return {{ status: r.status, data, text_preview: text ? text.slice(0, 200) : null }};
            }}
            """
        )
        await browser.close()

    if result is None:
        raise RuntimeError("calc_item_search: нет ответа от страницы")
    status = result.get("status", 0)
    if status != 200:
        raise RuntimeError(f"calc_item_search HTTP {status}: {result.get('text_preview') or result.get('data')}")
    return result.get("data")


async def bff_get(page, path: str) -> dict[str, Any]:
    """GET-запрос к BFF API в контексте страницы."""
    result = await page.evaluate(
        f"""
        async () => {{
          const r = await fetch('{SELLER_ORIGIN}{path}', {{
            method: 'GET',
            headers: {_BFF_HEADERS}
          }});
          let data = null;
          try {{ data = await r.json(); }} catch {{}}
          return {{ status: r.status, data }};
        }}
        """
    )
    if result is None:
        raise RuntimeError(f"bff_get: нет ответа от {path}")
    status = result.get("status", 0)
    if status not in (200, 201):
        raise RuntimeError(f"bff_get HTTP {status} от {path}: {result.get('data')}")
    return result.get("data") or {}


# ─── Context manager для удобного использования ───────────────────────────────

class OzonBrowser:
    """
    Контекстный менеджер: запускает Chrome, подключается, отдаёт страницу.

    Usage:
        async with OzonBrowser() as page:
            data = await bff_fetch(page, "/api/...", {...})
    """

    def __init__(self, url_hint: str = "seller.ozon.ru"):
        self.url_hint = url_hint
        self._pw = None
        self._browser = None
        self.page = None

    async def __aenter__(self):
        await ensure_chrome()
        self._pw, self._browser = await connect_cdp()
        self.page = await get_or_create_page(self._browser, self.url_hint)
        await wait_for_ozon_ready(self.page)
        return self.page

    async def __aexit__(self, *_):
        # Не закрываем браузер — Chrome продолжает работать
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass


# ─── CLI: первый запуск / проверка ────────────────────────────────────────────

if __name__ == "__main__":
    async def _main() -> None:
        print(f"CDP URL: {CDP_URL}")
        print(f"Профиль: {CHROME_PROFILE_DIR}")

        await ensure_chrome()
        print("Chrome запущен.")

        pw, browser = await connect_cdp()
        try:
            print(f"Подключено. Контекстов: {len(browser.contexts)}")
            page = await get_or_create_page(browser)
            print(f"Страница: {page.url}")
            await wait_for_ozon_ready(page)
            print("Страница готова.")

            # Тест BFF: получить список черновиков
            result = await bff_get(page, "/api/supplier-drafts/api/v4/get?draftId=0")
            print(f"BFF тест: {list(result.keys()) if isinstance(result, dict) else result}")
        finally:
            await browser.close()
            await pw.stop()

    asyncio.run(_main())
