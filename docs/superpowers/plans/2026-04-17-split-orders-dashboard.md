# Split orders_dashboard.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 12.5K-line monolith `orders_dashboard.py` into focused modules by domain, keeping all behaviour identical.

**Architecture:** Extract route handlers into `src/dashboard/routes/<domain>.py` modules, shared helpers into `src/dashboard/helpers.py`, module-level constants/state into `src/dashboard/constants.py` and `src/dashboard/state.py`. The original `orders_dashboard.py` becomes a thin shell: imports `create_app` from `src/dashboard/app.py`. Each module imports helpers from `src/dashboard/helpers.py` and constants from `src/dashboard/constants.py`.

**Tech Stack:** Python 3.14, aiohttp, asyncpg, pandas, openpyxl

---

## Important constraints

1. **Zero behaviour change.** Every route must return identical JSON. No logic changes.
2. **Резервная копия есть:** `orders_dashboard.py.bak`
3. **Нет тестов.** Верификация — запуск сервера + ручной smoke-тест endpoints.
4. **Circular imports:** helpers не импортируют routes, routes не импортируют друг друга. Общие зависимости — через `helpers`, `constants`, `state`.
5. **Порядок extract:** сначала helpers/constants (от которых всё зависит), потом routes (от крупных к мелким), в конце — `app.py` + оригинальный файл.

## File Structure

```
orders_dashboard.py              ← тонкая обёртка: from src.dashboard.app import create_app (+ __main__)
src/dashboard/
├── __init__.py                  ← пустой
├── app.py                       ← create_app(), create_pool/close_pool, route registration
├── constants.py                 ← MSK, BASE_DIR, HTML_PATH, FINANCE_*, ACCRUAL_*, AD_*, DELIVERED_STATUSES, PLAN_*, PROMO_EVENT_*, PALLETIZATION_IMPORT_*
├── state.py                     ← мутабельное глобальное состояние: sync_status, SYNC_LOCK_PATH, _CHROME_STATE, _CHROME_TASK, SUPPLY_PLAN_UPLOADED_*, SUPPLY_ACCEPTANCE_CACHE, _CROSSDOCK_TARIFFS_CACHE_*, _LEGACY_PALLETIZATION_CACHE
├── helpers.py                   ← clean_nan_values, normalize_offer_id, _calc_ad_kpis, _get_ozon_credentials, _get_env_from_dotenv, as_float, safe_divide, month_bounds, parse_date_utc, month_start_msk, normalize_article_key, normalize_sku_value, article_tags_from_offer_id, build_where, build_cost_maps, _to_int, _is_ad_description, _build_cost_description_whitelist, to_asyncpg_dsn, extract_item_article, _ozon_post_json, _ozon_supply_post, _normalize_text_key, _normalize_cluster_name, _normalize_column_name, _pick_df_column, month_timeline, scale_plan_value
├── routes/
│   ├── __init__.py              ← пустой
│   ├── pages.py                 ← index, finance_costs_page, palletization_page, palletization_asset (~15 строк)
│   ├── system.py                ← health, restart_server, sync_ozon_data, get_sync_status, reset_sync_status, run_sync_step, update_sync_status, _pid_is_running, _read_sync_lock, _acquire_sync_lock, _release_sync_lock, create_pool, close_pool (~200 строк)
│   ├── orders.py                ← get_orders, get_sales, get_articles (~160 строк)
│   ├── returns.py               ← get_returns (~120 строк)
│   ├── finance.py               ← get_cash_flow, build_rows_map_for_month, get_finance_report, get_finance_report_v2, get_accruals_comp_by_article, ensure_finance_report_tables, get_finance_costs, upload_finance_costs, save_finance_plan, load_posting_context, lookup_unit_cost, init_row, recalculate_row_total, set_row_from_formula, append_finance_posting, finance_row_key_for_compensation_article, build_kpi_summary, analyze_finance_data, get_realization_v2 (~1800 строк)
│   ├── stocks.py                ← get_warehouse_stock, get_analytics_stocks, get_stock_balances, get_analytics_turnover, get_average_delivery_time (~1100 строк)
│   ├── analytics.py             ← get_analytics_product_queries, get_article_query_matrix, get_article_analytics, get_article_characteristics, refresh_article_characteristics (~1500 строк)
│   ├── actions.py               ← get_actions, get_action_products, get_actions_report, activate_action_products, deactivate_action_products, _fetch_accruals_for_period, _norm_offer, _index_accrual_items_by_offer (~650 строк)
│   ├── advertising.py           ← get_advertising_summary, get_advertising_report (~600 строк)
│   ├── supply.py                ← get_supply_plan, save_supply_plan_state, reset_hidden_supply_plan_items, fill_supply_plan_from_availability_report, calculate_supply_plan_pallets, export_supply_plan_pallets, build_supply_plan_acceptance, filter_supply_plan_pallets, repack_supply_plan_cluster, request_supply_plan_timeslots, upload_supply_file, _extract_supply_clusters, _resolve_availability_report_path, _load_supply_targets_from_availability_report, _resolve_crossdock_tariff_file_path, _load_crossdock_tariffs_sc_rows, _resolve_crossdock_pickup_points_from_env, _load_cluster_markup_tariffs, _select_crossdock_tariff_for_cluster, _estimate_crossdock_costs_for_pallet_clusters, _load_supply_targets_from_availability_bytes, _extract_supply_targets_from_availability_workbook, sync_cluster_warehouses_to_db, build_supply_acceptance_report, _load_crossdock_dropoff_candidates_from_db, _load_warehouse_names_from_db (~3000 строк)
│   ├── supply_chrome.py         ← supply_stage2_set_warehouses, supply_multi_cluster_api, supply_mixed_flow, supply_scan_warehouses_ui, supply_collect_timeslots, supply_filter_timeslots, supply_fill_draft, supply_reconcile_draft_quantities, supply_check_drafts, supply_set_vehicle_pass, _chrome_auth_background, chrome_auth_init, chrome_auth_status (~1200 строк)
│   └── palletization_routes.py  ← palletization_products_get/create/update/delete/import, palletization_shipment_get/create/bulk/clear/missing, palletization_pallets_calculate, _fetch_palletization_product_rows, _load_legacy_palletization_products, _build_palletization_products_map (~350 строк)
```

## Execution strategy

Извлекаем **снизу вверх по зависимостям**: constants → state → helpers → routes (по одному) → app → обёртка. После каждого таска сервер должен запускаться.

---

### Task 1: Создать каркас пакета `src/dashboard/`

**Files:**
- Create: `src/dashboard/__init__.py`
- Create: `src/dashboard/routes/__init__.py`

- [ ] **Step 1: Создать пустые `__init__.py`**

```python
# src/dashboard/__init__.py
# src/dashboard/routes/__init__.py
# оба файла пустые
```

- [ ] **Step 2: Проверить что сервер запускается**

Run: `cd "e:/скрипты OZ/ozonapi" && PYTHONIOENCODING=utf-8 python -c "from orders_dashboard import create_app; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/dashboard/__init__.py src/dashboard/routes/__init__.py
git commit -m "chore: scaffold src/dashboard/ package structure"
```

---

### Task 2: Извлечь `constants.py`

**Files:**
- Create: `src/dashboard/constants.py`
- Modify: `orders_dashboard.py`

- [ ] **Step 1: Создать `src/dashboard/constants.py`**

Вырезать из `orders_dashboard.py` все неизменяемые module-level переменные (строки ~48-369, ~390-391, ~414-428, ~716-717). Скопировать нужные импорты в начало файла.

Содержимое `constants.py`:
```python
from datetime import timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # → корень проекта (e:\скрипты OZ\ozonapi)
HTML_PATH = BASE_DIR / "web" / "orders_dashboard.html"
COSTS_HTML_PATH = BASE_DIR / "web" / "finance_costs.html"
PALLETIZATION_WEB_DIR = BASE_DIR / "web" / "palletization"
MSK = timezone(timedelta(hours=3))

# ... все SUPPLY_*, PLAN_*, FINANCE_*, ACCRUAL_*, AD_*, DELIVERED_STATUSES, PROMO_EVENT_*, PALLETIZATION_IMPORT_* дословно как сейчас
```

**Критически важно:** скопировать содержимое ВСЕ констант (FINANCE_REPORT_ROWS, FINANCE_ROW_META, FINANCE_ZERO_ROWS, FINANCE_DESCRIPTION_FILTERS, ACCRUAL_COST_ROW_KEYS, AD_FINANCE_DESCRIPTIONS, DELIVERED_STATUSES, PLAN_BASELINE_REVENUE, PLAN_BASE_VALUES, PLAN_BASE_PCTS, SUPPLY_CLUSTER_MARKUP_DEFAULTS, SUPPLY_MACROLOCAL_CLUSTER_FALLBACKS, SUPPLY_ACCEPTANCE_CACHE_MAX_ENTRIES).

- [ ] **Step 2: В `orders_dashboard.py` заменить определения на импорт**

```python
from src.dashboard.constants import (
    BASE_DIR, HTML_PATH, COSTS_HTML_PATH, PALLETIZATION_WEB_DIR, MSK,
    SUPPLY_CLUSTER_MARKUP_DEFAULTS, SUPPLY_MACROLOCAL_CLUSTER_FALLBACKS,
    SUPPLY_ACCEPTANCE_CACHE_MAX_ENTRIES,
    PLAN_BASELINE_REVENUE, PLAN_BASE_VALUES, PLAN_BASE_PCTS,
    FINANCE_REPORT_ROWS, FINANCE_ROW_META, FINANCE_ZERO_ROWS,
    FINANCE_DESCRIPTION_FILTERS, ACCRUAL_COST_ROW_KEYS, AD_FINANCE_DESCRIPTIONS,
    DELIVERED_STATUSES, PROMO_EVENT_ADDED, PROMO_EVENT_REMOVED,
    PALLETIZATION_IMPORT_ARTICLE_COLUMNS, PALLETIZATION_IMPORT_ITEMS_PER_LAYER_COLUMNS,
    ACCRUAL_COST_DESCRIPTION_WHITELIST,
)
```

Удалить оригинальные определения этих переменных из `orders_dashboard.py`.

- [ ] **Step 3: Проверить что сервер запускается**

Run: `cd "e:/скрипты OZ/ozonapi" && PYTHONIOENCODING=utf-8 python -c "from orders_dashboard import create_app; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/dashboard/constants.py orders_dashboard.py
git commit -m "refactor: extract constants to src/dashboard/constants.py"
```

---

### Task 3: Извлечь `state.py`

**Files:**
- Create: `src/dashboard/state.py`
- Modify: `orders_dashboard.py`

- [ ] **Step 1: Создать `src/dashboard/state.py`**

Вырезать из `orders_dashboard.py` все мутабельные глобальные переменные:

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SUPPLY_ACCEPTANCE_CACHE: dict = {}
_CHROME_STATE: dict = {"ready": False, "cookies": None, "error": None}
_CHROME_TASK = None
SUPPLY_PLAN_UPLOADED_TARGETS: dict = {}
SUPPLY_PLAN_UPLOADED_META: dict = {}
_CROSSDOCK_TARIFFS_CACHE_PATH = None
_CROSSDOCK_TARIFFS_CACHE_MTIME = None
_CROSSDOCK_TARIFFS_CACHE_ROWS = None
_LEGACY_PALLETIZATION_CACHE: dict = {}

sync_status: dict = {
    "running": False, "current_step": None, "progress": None,
    "error": None, "last_completed": None, "pid": None,
}
SYNC_LOCK_PATH = BASE_DIR / ".sync_lock"
```

- [ ] **Step 2: В `orders_dashboard.py` заменить на импорт**

```python
from src.dashboard import state
```

И заменить прямые обращения на `state.sync_status`, `state._CHROME_STATE` и т.д.
**Либо** (проще для начала): импорт конкретных имён + замена мутаций на `state.xxx = ...`:

```python
from src.dashboard.state import (
    SUPPLY_ACCEPTANCE_CACHE, _CHROME_STATE, _CHROME_TASK,
    SUPPLY_PLAN_UPLOADED_TARGETS, SUPPLY_PLAN_UPLOADED_META,
    _CROSSDOCK_TARIFFS_CACHE_PATH, _CROSSDOCK_TARIFFS_CACHE_MTIME, _CROSSDOCK_TARIFFS_CACHE_ROWS,
    _LEGACY_PALLETIZATION_CACHE, sync_status, SYNC_LOCK_PATH,
)
```

**ВАЖНО:** Для мутабельных переменных (dict, list) `from module import X` работает — мутации в месте (dict update, list append) видны всем. Но для переприсваивания скаляров (`_CHROME_TASK = asyncio.create_task(...)`) нужно `import state` и `state._CHROME_TASK = ...`. Проверить каждое использование.

- [ ] **Step 3: Проверить что сервер запускается**

Run: `cd "e:/скрипты OZ/ozonapi" && PYTHONIOENCODING=utf-8 python -c "from orders_dashboard import create_app; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/dashboard/state.py orders_dashboard.py
git commit -m "refactor: extract mutable state to src/dashboard/state.py"
```

---

### Task 4: Извлечь `helpers.py`

**Files:**
- Create: `src/dashboard/helpers.py`
- Modify: `orders_dashboard.py`

- [ ] **Step 1: Создать `src/dashboard/helpers.py`**

Вырезать из `orders_dashboard.py` все helper-функции (не route handlers). Полный список (по порядку определения):

```
clean_nan_values, month_bounds, safe_divide, _calc_ad_kpis,
normalize_offer_id, _normalize_column_name, _pick_df_column,
init_row, recalculate_row_total, set_row_from_formula, append_finance_posting,
finance_row_key_for_compensation_article, to_asyncpg_dsn, parse_date_utc,
month_start_msk, as_float, extract_item_article, _build_cost_description_whitelist,
_is_ad_description, normalize_article_key, normalize_sku_value,
article_tags_from_offer_id, build_cost_maps, load_posting_context,
lookup_unit_cost, month_timeline, scale_plan_value, build_kpi_summary,
build_where, _get_env_from_dotenv, _get_ozon_credentials,
_to_int, _ozon_supply_post, _ozon_post_json,
_normalize_cluster_name, _normalize_text_key
```

Добавить нужные импорты в начало `helpers.py`:
```python
import math
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.dashboard.constants import (
    MSK, BASE_DIR, FINANCE_REPORT_ROWS, FINANCE_ROW_META,
    FINANCE_ZERO_ROWS, ACCRUAL_COST_ROW_KEYS, AD_FINANCE_DESCRIPTIONS,
    ACCRUAL_COST_DESCRIPTION_WHITELIST, PLAN_BASE_VALUES, PLAN_BASE_PCTS,
    PLAN_BASELINE_REVENUE,
)
from src.config import settings
```

- [ ] **Step 2: В `orders_dashboard.py` заменить определения на импорт**

```python
from src.dashboard.helpers import (
    clean_nan_values, month_bounds, safe_divide, _calc_ad_kpis,
    normalize_offer_id, _normalize_column_name, _pick_df_column,
    # ... все функции
)
```

Удалить тела функций из `orders_dashboard.py`.

- [ ] **Step 3: Проверить что сервер запускается**

Run: `cd "e:/скрипты OZ/ozonapi" && PYTHONIOENCODING=utf-8 python -c "from orders_dashboard import create_app; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/dashboard/helpers.py orders_dashboard.py
git commit -m "refactor: extract helper functions to src/dashboard/helpers.py"
```

---

### Task 5: Извлечь `routes/pages.py`

**Files:**
- Create: `src/dashboard/routes/pages.py`
- Modify: `orders_dashboard.py`

- [ ] **Step 1: Создать файл**

Вырезать: `index`, `finance_costs_page`, `palletization_page`, `palletization_asset` (~15 строк).

```python
from aiohttp import web
from src.dashboard.constants import HTML_PATH, COSTS_HTML_PATH, PALLETIZATION_WEB_DIR


async def index(request: web.Request) -> web.Response:
    return web.FileResponse(HTML_PATH)


async def finance_costs_page(request: web.Request) -> web.Response:
    return web.FileResponse(COSTS_HTML_PATH)


async def palletization_page(request: web.Request) -> web.Response:
    return web.FileResponse(PALLETIZATION_WEB_DIR / "index.html")


async def palletization_asset(request: web.Request) -> web.Response:
    filename = request.match_info["filename"]
    path = PALLETIZATION_WEB_DIR / filename
    if not path.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(path)
```

- [ ] **Step 2: В `orders_dashboard.py` заменить на импорт и удалить тела**

```python
from src.dashboard.routes.pages import index, finance_costs_page, palletization_page, palletization_asset
```

- [ ] **Step 3: Проверить**

Run: `cd "e:/скрипты OZ/ozonapi" && PYTHONIOENCODING=utf-8 python -c "from orders_dashboard import create_app; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add src/dashboard/routes/pages.py orders_dashboard.py
git commit -m "refactor: extract page routes to src/dashboard/routes/pages.py"
```

---

### Task 6: Извлечь `routes/system.py`

**Files:**
- Create: `src/dashboard/routes/system.py`
- Modify: `orders_dashboard.py`

- [ ] **Step 1: Создать файл**

Вырезать: `health`, `restart_server`, `create_pool`, `close_pool`, `sync_ozon_data`, `get_sync_status`, `reset_sync_status`, `run_sync_step`, `update_sync_status`, `_pid_is_running`, `_read_sync_lock`, `_acquire_sync_lock`, `_release_sync_lock`.

Импорты:
```python
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime

import asyncpg
from aiohttp import web

from src.dashboard.constants import MSK
from src.dashboard import state
from src.dashboard.helpers import to_asyncpg_dsn
from src.config import settings
```

**ВАЖНО:** `sync_status` мутируется через присваивание ключей (`sync_status["running"] = True`), это безопасно при импорте dict. Но `state.SYNC_LOCK_PATH` — скаляр, поэтому в коде `_acquire_sync_lock`/`_release_sync_lock` обращаться через `state.SYNC_LOCK_PATH`.

- [ ] **Step 2: Заменить в `orders_dashboard.py` на импорт**

- [ ] **Step 3: Проверить и commit**

```bash
git add src/dashboard/routes/system.py orders_dashboard.py
git commit -m "refactor: extract system routes to src/dashboard/routes/system.py"
```

---

### Task 7: Извлечь `routes/orders.py`

**Files:**
- Create: `src/dashboard/routes/orders.py`
- Modify: `orders_dashboard.py`

- [ ] **Step 1: Создать файл**

Вырезать: `get_orders`, `get_sales`, `get_articles`.

- [ ] **Step 2: Заменить в `orders_dashboard.py` на импорт, проверить, commit**

```bash
git commit -m "refactor: extract order routes to src/dashboard/routes/orders.py"
```

---

### Task 8: Извлечь `routes/returns.py`

- [ ] Вырезать `get_returns`, заменить, проверить, commit.

```bash
git commit -m "refactor: extract returns routes"
```

---

### Task 9: Извлечь `routes/finance.py`

**Files:**
- Create: `src/dashboard/routes/finance.py`

- [ ] **Step 1: Создать файл**

Самый большой блок после supply. Вырезать:
`ensure_finance_report_tables`, `get_finance_costs`, `upload_finance_costs`, `save_finance_plan`,
`get_cash_flow`, `build_rows_map_for_month`, `get_finance_report`, `get_finance_report_v2`,
`get_accruals_comp_by_article`, `load_posting_context`, `lookup_unit_cost`,
`init_row`, `recalculate_row_total`, `set_row_from_formula`, `append_finance_posting`,
`finance_row_key_for_compensation_article`, `build_kpi_summary`, `analyze_finance_data`,
`get_realization_v2`.

**Примечание:** `init_row`, `recalculate_row_total` и другие finance-хелперы уже в `helpers.py` (Task 4). Здесь просто импортируем из `helpers`.

- [ ] **Step 2: Заменить, проверить, commit**

```bash
git commit -m "refactor: extract finance routes to src/dashboard/routes/finance.py"
```

---

### Task 10: Извлечь `routes/stocks.py`

- [ ] Вырезать `get_warehouse_stock`, `get_analytics_stocks`, `get_stock_balances`, `get_analytics_turnover`, `get_average_delivery_time`. Заменить, проверить, commit.

```bash
git commit -m "refactor: extract stock routes to src/dashboard/routes/stocks.py"
```

---

### Task 11: Извлечь `routes/analytics.py`

- [ ] Вырезать `get_analytics_product_queries`, `get_article_query_matrix`, `get_article_analytics`, `get_article_characteristics`, `refresh_article_characteristics`. Заменить, проверить, commit.

```bash
git commit -m "refactor: extract analytics routes to src/dashboard/routes/analytics.py"
```

---

### Task 12: Извлечь `routes/actions.py`

- [ ] Вырезать `get_actions`, `get_action_products`, `get_actions_report`, `activate_action_products`, `deactivate_action_products`, `_fetch_accruals_for_period`, `_norm_offer`, `_index_accrual_items_by_offer`. Заменить, проверить, commit.

```bash
git commit -m "refactor: extract actions routes to src/dashboard/routes/actions.py"
```

---

### Task 13: Извлечь `routes/advertising.py`

- [ ] Вырезать `get_advertising_summary`, `get_advertising_report`. Заменить, проверить, commit.

```bash
git commit -m "refactor: extract advertising routes to src/dashboard/routes/advertising.py"
```

---

### Task 14: Извлечь `routes/supply.py`

**Files:**
- Create: `src/dashboard/routes/supply.py`

- [ ] **Step 1: Создать файл**

Самый крупный модуль (~3000 строк). Вырезать ВСЕ supply_* функции кроме chrome-зависимых:
`get_supply_plan`, `save_supply_plan_state`, `reset_hidden_supply_plan_items`,
`fill_supply_plan_from_availability_report`, `calculate_supply_plan_pallets`,
`export_supply_plan_pallets`, `build_supply_plan_acceptance`, `filter_supply_plan_pallets`,
`repack_supply_plan_cluster`, `request_supply_plan_timeslots`, `upload_supply_file`,
`sync_cluster_warehouses_to_db`, `build_supply_acceptance_report`,
и все private helpers: `_extract_supply_clusters`, `_resolve_*`, `_load_*`, `_select_*`, `_estimate_*`.

State-зависимости: `SUPPLY_PLAN_UPLOADED_TARGETS`, `SUPPLY_PLAN_UPLOADED_META`, `SUPPLY_ACCEPTANCE_CACHE`, `_CROSSDOCK_TARIFFS_CACHE_*`, `_LEGACY_PALLETIZATION_CACHE` — обращаться через `from src.dashboard import state`.

- [ ] **Step 2: Заменить, проверить, commit**

```bash
git commit -m "refactor: extract supply routes to src/dashboard/routes/supply.py"
```

---

### Task 15: Извлечь `routes/supply_chrome.py`

- [ ] Вырезать все chrome/UI-зависимые supply функции:
`supply_stage2_set_warehouses`, `supply_multi_cluster_api`, `supply_mixed_flow`,
`supply_scan_warehouses_ui`, `supply_collect_timeslots`, `supply_filter_timeslots`,
`supply_fill_draft`, `supply_reconcile_draft_quantities`, `supply_check_drafts`,
`supply_set_vehicle_pass`, `_chrome_auth_background`, `chrome_auth_init`, `chrome_auth_status`.

State: `_CHROME_STATE`, `_CHROME_TASK` — обращаться через `state._CHROME_STATE` / `state._CHROME_TASK`.

Commit:
```bash
git commit -m "refactor: extract supply chrome routes to src/dashboard/routes/supply_chrome.py"
```

---

### Task 16: Извлечь `routes/palletization_routes.py`

- [ ] Вырезать: `palletization_products_get/create/update/delete/import`, `palletization_shipment_*`, `palletization_pallets_calculate`, `_fetch_palletization_product_rows`, `_load_legacy_palletization_products`, `_build_palletization_products_map`. Заменить, проверить, commit.

```bash
git commit -m "refactor: extract palletization routes"
```

---

### Task 17: Создать `src/dashboard/app.py` и упростить `orders_dashboard.py`

**Files:**
- Create: `src/dashboard/app.py`
- Modify: `orders_dashboard.py`

- [ ] **Step 1: Создать `src/dashboard/app.py`**

Перенести `create_app()` сюда. Импортировать все route handlers из соответствующих модулей:

```python
from aiohttp import web

from src.dashboard.routes.pages import index, finance_costs_page, palletization_page, palletization_asset
from src.dashboard.routes.system import health, restart_server, create_pool, close_pool, sync_ozon_data, get_sync_status
from src.dashboard.routes.orders import get_orders, get_sales, get_articles
from src.dashboard.routes.returns import get_returns
from src.dashboard.routes.finance import (
    get_cash_flow, get_finance_report, get_finance_report_v2,
    get_accruals_comp_by_article, ensure_finance_report_tables,
    get_finance_costs, upload_finance_costs, save_finance_plan,
    analyze_finance_data, get_realization_v2,
)
from src.dashboard.routes.stocks import (
    get_warehouse_stock, get_analytics_stocks, get_stock_balances,
    get_analytics_turnover, get_average_delivery_time,
)
from src.dashboard.routes.analytics import (
    get_analytics_product_queries, get_article_query_matrix,
    get_article_analytics, get_article_characteristics, refresh_article_characteristics,
)
from src.dashboard.routes.actions import (
    get_actions, get_action_products, get_actions_report,
    activate_action_products, deactivate_action_products,
)
from src.dashboard.routes.advertising import get_advertising_summary, get_advertising_report
from src.dashboard.routes.supply import (
    get_supply_plan, save_supply_plan_state, reset_hidden_supply_plan_items,
    fill_supply_plan_from_availability_report, calculate_supply_plan_pallets,
    export_supply_plan_pallets, build_supply_plan_acceptance, filter_supply_plan_pallets,
    repack_supply_plan_cluster, request_supply_plan_timeslots, upload_supply_file,
    sync_cluster_warehouses_to_db,
)
from src.dashboard.routes.supply_chrome import (
    supply_stage2_set_warehouses, supply_multi_cluster_api, supply_mixed_flow,
    supply_scan_warehouses_ui, supply_collect_timeslots, supply_filter_timeslots,
    supply_fill_draft, supply_reconcile_draft_quantities, supply_check_drafts,
    supply_set_vehicle_pass, chrome_auth_init, chrome_auth_status,
)
from src.dashboard.routes.palletization_routes import (
    palletization_products_get, palletization_products_create,
    palletization_products_update, palletization_products_delete,
    palletization_products_import, palletization_shipment_get,
    palletization_shipment_create, palletization_shipment_bulk,
    palletization_shipment_clear, palletization_shipment_missing,
    palletization_pallets_calculate,
)


def create_app() -> web.Application:
    app = web.Application()
    # ... дословно вся регистрация маршрутов из текущего create_app()
    app.on_startup.append(create_pool)
    app.on_cleanup.append(close_pool)
    return app
```

- [ ] **Step 2: Упростить `orders_dashboard.py`**

```python
"""Ozon Dashboard — thin entry point."""
from src.dashboard.app import create_app

__all__ = ["create_app"]
```

Это ~3 строки вместо 12.5K.

- [ ] **Step 3: Проверить что сервер запускается**

Run: `cd "e:/скрипты OZ/ozonapi" && PYTHONIOENCODING=utf-8 python -c "from orders_dashboard import create_app; app = create_app(); print(f'Routes: {len(app.router.routes())}')"`
Expected: `Routes: <N>` (то же число что и до рефакторинга)

- [ ] **Step 4: Проверить что `run_dashboard.cmd` работает**

Запустить сервер через обычный способ, открыть дашборд в браузере, проверить что страница загружается.

- [ ] **Step 5: Commit**

```bash
git add src/dashboard/app.py orders_dashboard.py
git commit -m "refactor: create app.py entry point, simplify orders_dashboard.py to thin wrapper"
```

---

### Task 18: Финальная проверка

- [ ] **Step 1: Подсчитать маршруты до и после**

```bash
cd "e:/скрипты OZ/ozonapi"
PYTHONIOENCODING=utf-8 python -c "
from orders_dashboard import create_app
app = create_app()
for route in sorted(app.router.routes(), key=lambda r: r.resource.canonical if r.resource else ''):
    print(f'{route.method:6s} {route.resource.canonical if route.resource else \"?\"}')" 
```

Сравнить с выводом из бэкапа:
```bash
PYTHONIOENCODING=utf-8 python -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('bak', 'orders_dashboard.py.bak')
mod = importlib.util.module_from_spec(spec)
sys.modules['bak'] = mod
spec.loader.exec_module(mod)
app = mod.create_app()
print(len(list(app.router.routes())))
"
```

- [ ] **Step 2: Smoke-тест API endpoints**

Запустить сервер, проверить в браузере:
- `GET /` — главная страница
- `GET /api/health` — `{"status": "ok"}`
- `GET /api/articles?source=all` — список артикулов
- `GET /api/sync-status` — статус синхронизации

- [ ] **Step 3: Удалить бэкап**

```bash
rm orders_dashboard.py.bak
```

- [ ] **Step 4: Финальный commit**

```bash
git add -A
git commit -m "refactor: complete split of orders_dashboard.py into src/dashboard/ modules"
```

---

## Обновить MEMORY.md

После завершения рефакторинга обновить таблицу ключевых файлов в memory, заменив `orders_dashboard.py` на модули `src/dashboard/`.
