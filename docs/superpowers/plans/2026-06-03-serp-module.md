# SERP-модуль Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить сбор топ-20 выдачи ozon.ru по запросу через Chrome-плагин, хранение снимков в БД, управление главным запросом SKU и секцию «Поиск» в отчёте по артикулу.

**Architecture:** Chrome-плагин (content.js → background.js) собирает SERP через `chrome.scripting.executeScript` в существующей вкладке браузера текущего профиля. Дашборд триггерит скрейп через POST-эндпоинт, который отправляет CustomEvent в плагин и ждёт ответа. Данные хранятся в 4 новых таблицах PostgreSQL.

**Tech Stack:** Python/aiohttp (backend), asyncpg (DB), JavaScript MV3 Chrome Extension, PostgreSQL, HTML/CSS/JS (frontend)

---

## File Map

| Файл | Действие | Ответственность |
|------|----------|----------------|
| `migrations/add_serp_tables.sql` | Create | DDL для 4 таблиц |
| `src/services/serp_service.py` | Create | Бизнес-логика: сохранение снимков, главный запрос, отчёт |
| `src/dashboard/routes/serp.py` | Create | HTTP-эндпоинты SERP-модуля |
| `src/dashboard/app.py` | Modify | Регистрация новых роутов |
| `chrome-extension/unitka/background.js` | Modify | Новые actions: scrape_serp, enrich_with_bestsellers |
| `chrome-extension/unitka/content.js` | Modify | Проксирование новых actions (уже готово — структура универсальная) |
| `chrome-extension/unitka/ozon-overlay.js` | Modify | Богатый скрейп карточки (позиция, рейтинг, старая цена, промо) |
| `web/orders_dashboard.html` | Modify | Вкладка «Выдача» + секция «Поиск» в отчёте по артикулу |

---

## Task 1: Миграция БД — 4 новые таблицы

**Files:**
- Create: `migrations/add_serp_tables.sql`

- [ ] **Шаг 1: Создать файл миграции**

```sql
-- migrations/add_serp_tables.sql

-- 1. Снимки выдачи
CREATE TABLE IF NOT EXISTS serp_snapshots (
    id          SERIAL PRIMARY KEY,
    query_text  VARCHAR(500) NOT NULL,
    scraped_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    position_count INTEGER,
    raw_data    JSONB
);
CREATE INDEX IF NOT EXISTS idx_serp_snapshots_query ON serp_snapshots (query_text);
CREATE INDEX IF NOT EXISTS idx_serp_snapshots_scraped ON serp_snapshots (scraped_at DESC);

-- 2. Позиции в снимке
CREATE TABLE IF NOT EXISTS serp_positions (
    id              SERIAL PRIMARY KEY,
    snapshot_id     INTEGER NOT NULL REFERENCES serp_snapshots(id) ON DELETE CASCADE,
    position        SMALLINT NOT NULL,
    sku             BIGINT,
    title           VARCHAR(500),
    brand           VARCHAR(255),
    price           NUMERIC(15,2),
    price_before    NUMERIC(15,2),
    rating          FLOAT,
    review_count    INTEGER,
    stock           INTEGER,
    promo_label     VARCHAR(100),
    thumbnail_url   TEXT,
    revenue_30d     NUMERIC(15,2),
    sales_per_day   FLOAT,
    is_our_product  BOOLEAN NOT NULL DEFAULT false,
    is_competitor   BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (snapshot_id, position)
);
CREATE INDEX IF NOT EXISTS idx_serp_pos_snapshot ON serp_positions (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_serp_pos_sku ON serp_positions (sku);

-- 3. Справочник конкурентов
CREATE TABLE IF NOT EXISTS serp_competitors (
    sku         BIGINT PRIMARY KEY,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. Главный запрос артикула
CREATE TABLE IF NOT EXISTS sku_primary_query (
    sku          BIGINT PRIMARY KEY,
    offer_id     VARCHAR(255),
    query_text   VARCHAR(500) NOT NULL,
    set_manually BOOLEAN NOT NULL DEFAULT false,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_spq_offer_id ON sku_primary_query (offer_id);
```

- [ ] **Шаг 2: Применить миграцию к БД**

```bash
PYTHONIOENCODING=utf-8 python -c "
import asyncio, asyncpg, sys
sys.stdout.reconfigure(encoding='utf-8')

async def main():
    conn = await asyncpg.connect('postgresql://localhost/ozon_analytics')
    sql = open('migrations/add_serp_tables.sql', encoding='utf-8').read()
    await conn.execute(sql)
    await conn.close()
    print('OK')

asyncio.run(main())
"
```

Ожидаемый вывод: `OK`

> Если строка подключения другая — возьми из `.env` (переменная `DATABASE_URL` или `POSTGRES_DSN`).

- [ ] **Шаг 3: Проверить таблицы**

```bash
PYTHONIOENCODING=utf-8 python -c "
import asyncio, asyncpg, sys
sys.stdout.reconfigure(encoding='utf-8')

async def main():
    conn = await asyncpg.connect('postgresql://localhost/ozon_analytics')
    rows = await conn.fetch(\"SELECT tablename FROM pg_tables WHERE tablename LIKE 'serp%' OR tablename = 'sku_primary_query'\")
    for r in rows: print(r['tablename'])
    await conn.close()

asyncio.run(main())
"
```

Ожидаемый вывод (4 строки):
```
serp_competitors
serp_positions
serp_snapshots
sku_primary_query
```

- [ ] **Шаг 4: Коммит**

```bash
git add migrations/add_serp_tables.sql
git commit -m "feat(serp): add DB tables for SERP snapshots, positions, competitors, primary query"
```

---

## Task 2: serp_service.py — бизнес-логика

**Files:**
- Create: `src/services/serp_service.py`

- [ ] **Шаг 1: Создать файл сервиса**

```python
# src/services/serp_service.py
"""SERP-сервис: сохранение снимков выдачи, управление конкурентами и главным запросом."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Снимки выдачи
# ────────────────────────────────────────────────────────────────────────────

async def save_snapshot(
    pool: asyncpg.Pool,
    query_text: str,
    positions: List[Dict[str, Any]],
    raw_data: Optional[Dict] = None,
) -> int:
    """Сохраняет снимок выдачи в БД. Возвращает snapshot_id."""
    # Получаем набор SKU наших товаров из таблицы products
    async with pool.acquire() as conn:
        our_skus_rows = await conn.fetch("SELECT product_id FROM products WHERE is_visible = true")
        our_skus = {r["product_id"] for r in our_skus_rows}

        # Получаем набор sku конкурентов из справочника
        comp_rows = await conn.fetch("SELECT sku FROM serp_competitors")
        competitor_skus = {r["sku"] for r in comp_rows}

        # Создаём снимок
        snapshot_id = await conn.fetchval(
            """
            INSERT INTO serp_snapshots (query_text, scraped_at, position_count, raw_data)
            VALUES ($1, now(), $2, $3::jsonb)
            RETURNING id
            """,
            query_text,
            len(positions),
            __import__("json").dumps(raw_data) if raw_data else None,
        )

        # Вставляем позиции
        for pos in positions:
            sku = _to_bigint(pos.get("sku"))
            await conn.execute(
                """
                INSERT INTO serp_positions
                    (snapshot_id, position, sku, title, brand, price, price_before,
                     rating, review_count, stock, promo_label, thumbnail_url,
                     revenue_30d, sales_per_day, is_our_product, is_competitor)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                ON CONFLICT (snapshot_id, position) DO NOTHING
                """,
                snapshot_id,
                int(pos.get("position", 0)),
                sku,
                (pos.get("title") or "")[:500] or None,
                (pos.get("brand") or "")[:255] or None,
                _to_decimal(pos.get("price")),
                _to_decimal(pos.get("price_before")),
                _to_float(pos.get("rating")),
                _to_int(pos.get("review_count")),
                _to_int(pos.get("stock")),
                (pos.get("promo_label") or "")[:100] or None,
                pos.get("thumbnail_url") or None,
                _to_decimal(pos.get("revenue_30d")),
                _to_float(pos.get("sales_per_day")),
                bool(sku and sku in our_skus),
                bool(sku and sku in competitor_skus),
            )

    logger.info("Saved SERP snapshot id=%s for query=%r positions=%d", snapshot_id, query_text, len(positions))
    return snapshot_id


async def get_latest_snapshot(pool: asyncpg.Pool, query_text: str) -> Optional[Dict]:
    """Возвращает последний снимок выдачи по запросу со всеми позициями."""
    async with pool.acquire() as conn:
        snap = await conn.fetchrow(
            "SELECT id, query_text, scraped_at, position_count FROM serp_snapshots "
            "WHERE query_text = $1 ORDER BY scraped_at DESC LIMIT 1",
            query_text,
        )
        if not snap:
            return None

        positions = await conn.fetch(
            """
            SELECT position, sku, title, brand, price, price_before,
                   rating, review_count, stock, promo_label, thumbnail_url,
                   revenue_30d, sales_per_day, is_our_product, is_competitor
            FROM serp_positions
            WHERE snapshot_id = $1
            ORDER BY position
            """,
            snap["id"],
        )

    return {
        "snapshot_id": snap["id"],
        "query_text": snap["query_text"],
        "scraped_at": snap["scraped_at"].isoformat(),
        "position_count": snap["position_count"],
        "positions": [dict(p) for p in positions],
    }


# ────────────────────────────────────────────────────────────────────────────
# Конкуренты
# ────────────────────────────────────────────────────────────────────────────

async def mark_competitor(pool: asyncpg.Pool, sku: int, is_competitor: bool, note: str = "") -> None:
    """Добавляет или удаляет SKU из справочника конкурентов."""
    async with pool.acquire() as conn:
        if is_competitor:
            await conn.execute(
                """
                INSERT INTO serp_competitors (sku, note, created_at)
                VALUES ($1, $2, now())
                ON CONFLICT (sku) DO UPDATE SET note = EXCLUDED.note
                """,
                sku, note or None,
            )
            # Обновляем флаг во всех снимках
            await conn.execute(
                "UPDATE serp_positions SET is_competitor = true WHERE sku = $1", sku
            )
        else:
            await conn.execute("DELETE FROM serp_competitors WHERE sku = $1", sku)
            await conn.execute(
                "UPDATE serp_positions SET is_competitor = false WHERE sku = $1", sku
            )


async def get_competitors(pool: asyncpg.Pool) -> List[Dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT sku, note, created_at FROM serp_competitors ORDER BY created_at DESC"
        )
    return [dict(r) for r in rows]


# ────────────────────────────────────────────────────────────────────────────
# Главный запрос
# ────────────────────────────────────────────────────────────────────────────

async def get_primary_query(pool: asyncpg.Pool, sku: int) -> Optional[Dict]:
    """Возвращает главный запрос для SKU (или None)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sku, offer_id, query_text, set_manually, updated_at FROM sku_primary_query WHERE sku = $1",
            sku,
        )
    return dict(row) if row else None


async def set_primary_query(pool: asyncpg.Pool, sku: int, query_text: str, manual: bool = True) -> None:
    """Устанавливает главный запрос для SKU."""
    async with pool.acquire() as conn:
        offer_id = await conn.fetchval(
            "SELECT offer_id FROM products WHERE product_id = $1", sku
        )
        await conn.execute(
            """
            INSERT INTO sku_primary_query (sku, offer_id, query_text, set_manually, updated_at)
            VALUES ($1, $2, $3, $4, now())
            ON CONFLICT (sku) DO UPDATE
                SET query_text = EXCLUDED.query_text,
                    set_manually = EXCLUDED.set_manually,
                    updated_at = now()
            """,
            sku, offer_id, query_text, manual,
        )


async def recalculate_primary_queries(pool: asyncpg.Pool) -> int:
    """
    Пересчитывает главный запрос для всех SKU где set_manually = false.
    Правило: MAX(searches * conversion) за последние 30 дней.
    Возвращает кол-во обновлённых строк.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (sku)
                sku, offer_id, query_text,
                (COALESCE(searches, 0) * COALESCE(conversion, 0)) AS score
            FROM analytics_product_query_details
            WHERE period_start >= now() - INTERVAL '30 days'
              AND query_text IS NOT NULL AND query_text <> ''
            ORDER BY sku, score DESC
            """
        )
        count = 0
        for row in rows:
            # Пропускаем если уже установлен вручную
            existing = await conn.fetchrow(
                "SELECT set_manually FROM sku_primary_query WHERE sku = $1", row["sku"]
            )
            if existing and existing["set_manually"]:
                continue
            await conn.execute(
                """
                INSERT INTO sku_primary_query (sku, offer_id, query_text, set_manually, updated_at)
                VALUES ($1, $2, $3, false, now())
                ON CONFLICT (sku) DO UPDATE
                    SET query_text = EXCLUDED.query_text,
                        offer_id = EXCLUDED.offer_id,
                        updated_at = now()
                WHERE sku_primary_query.set_manually = false
                """,
                row["sku"], row["offer_id"], row["query_text"],
            )
            count += 1
    logger.info("Recalculated primary queries: %d updated", count)
    return count


async def get_top_queries_for_sku(pool: asyncpg.Pool, sku: int, limit: int = 20) -> List[Dict]:
    """Топ запросов для SKU по убыванию searches*conversion — для dropdown смены главного запроса."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT query_text,
                   SUM(searches) AS total_searches,
                   AVG(conversion) AS avg_conversion,
                   SUM(COALESCE(searches,0) * COALESCE(conversion,0)) AS score
            FROM analytics_product_query_details
            WHERE sku = $1
              AND period_start >= now() - INTERVAL '30 days'
              AND query_text IS NOT NULL AND query_text <> ''
            GROUP BY query_text
            ORDER BY score DESC
            LIMIT $2
            """,
            sku, limit,
        )
    return [dict(r) for r in rows]


# ────────────────────────────────────────────────────────────────────────────
# Отчёт по артикулу
# ────────────────────────────────────────────────────────────────────────────

async def get_article_serp_report(pool: asyncpg.Pool, sku: int) -> Dict:
    """
    Возвращает снапшот для секции «Поиск» в отчёте по артикулу:
    - главный запрос
    - наша позиция
    - список конкурентов из последнего снимка
    """
    primary = await get_primary_query(pool, sku)
    if not primary:
        return {"primary_query": None, "our_position": None, "positions": [], "scraped_at": None}

    snapshot = await get_latest_snapshot(pool, primary["query_text"])
    if not snapshot:
        return {
            "primary_query": primary["query_text"],
            "set_manually": primary["set_manually"],
            "our_position": None,
            "positions": [],
            "scraped_at": None,
        }

    our_pos = next((p for p in snapshot["positions"] if p.get("sku") == sku), None)
    return {
        "primary_query": primary["query_text"],
        "set_manually": primary["set_manually"],
        "our_position": our_pos["position"] if our_pos else None,
        "our_price": str(our_pos["price"]) if our_pos and our_pos["price"] else None,
        "positions": snapshot["positions"],
        "scraped_at": snapshot["scraped_at"],
        "snapshot_id": snapshot["snapshot_id"],
    }


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _to_bigint(v) -> Optional[int]:
    try: return int(v)
    except (TypeError, ValueError): return None

def _to_int(v) -> Optional[int]:
    try: return int(v)
    except (TypeError, ValueError): return None

def _to_float(v) -> Optional[float]:
    try: return float(v)
    except (TypeError, ValueError): return None

def _to_decimal(v):
    if v is None: return None
    try:
        from decimal import Decimal
        return Decimal(str(v))
    except Exception:
        return None
```

- [ ] **Шаг 2: Проверить импорт**

```bash
PYTHONIOENCODING=utf-8 python -c "from src.services.serp_service import save_snapshot, get_latest_snapshot, mark_competitor, get_primary_query, set_primary_query, recalculate_primary_queries, get_article_serp_report; print('OK')"
```

Ожидаемый вывод: `OK`

- [ ] **Шаг 3: Коммит**

```bash
git add src/services/serp_service.py
git commit -m "feat(serp): add serp_service with snapshot save, primary query, competitor management"
```

---

## Task 3: Новые actions в background.js плагина

**Files:**
- Modify: `chrome-extension/unitka/background.js`

- [ ] **Шаг 1: Добавить функцию `scrapeSerpPage` перед `chrome.runtime.onMessage.addListener`**

Открыть `chrome-extension/unitka/background.js`, найти строку `chrome.runtime.onMessage.addListener(` и вставить перед ней:

```javascript
// ─── SERP scraping ──────────────────────────────────────────────────────────

async function scrapeSerpPage({ query_text, limit = 20 }) {
  if (!query_text || !query_text.trim()) throw new Error("query_text is required");
  const url = "https://www.ozon.ru/search/?text=" + encodeURIComponent(query_text.trim()) + "&sorting=score";

  // Используем или открываем вкладку в текущем профиле (НЕ новый профиль)
  const tab = await _ensureTab("https://www.ozon.ru/search/");

  // Навигируем к нужному запросу
  await chrome.tabs.update(tab.id, { url });

  // Ждём загрузки страницы + появления карточек (SPA)
  await new Promise(r => setTimeout(r, 2500));
  for (let i = 0; i < 20; i++) {
    await new Promise(r => setTimeout(r, 500));
    const [check] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: () => document.querySelectorAll('a[href*="/product/"]').length,
    });
    if ((check && check.result) >= 3) break;
  }

  // Скрейпим карточки
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: (limitArg) => {
      function extractSku(href) {
        const m = (href || "").match(/\/product\/[^\/]*?-(\d{6,})\/?/);
        return m ? m[1] : null;
      }
      function parsePrice(text) {
        if (!text) return null;
        const m = text.replace(/\s/g, "").match(/(\d+)/);
        return m ? Number(m[1]) : null;
      }

      const cards = [];
      const seen = new Set();
      const anchors = document.querySelectorAll('a[href*="/product/"]');

      for (const a of anchors) {
        const sku = extractSku(a.getAttribute("href") || "");
        if (!sku || seen.has(sku)) continue;

        const card = a.closest('[class*="tile"], [class*="product-card"], [class*="widget"], article, li');
        if (!card) continue;

        // Позиция = порядковый номер
        const position = cards.length + 1;

        // Название
        const nameEl = card.querySelector('h3, h2, [class*="title"], [class*="name"], span');
        const title = (nameEl?.innerText || "").trim().slice(0, 200);

        // Бренд
        const brandEl = card.querySelector('[class*="brand"]');
        const brand = (brandEl?.innerText || "").trim() || null;

        // Цены: ищем все числа с ₽
        const priceEls = [...card.querySelectorAll('*')].filter(el => {
          const t = el.childElementCount === 0 ? el.innerText?.trim() : "";
          return t && t.includes("₽") && t.length < 20;
        });
        let price = null, price_before = null;
        for (const el of priceEls) {
          const t = el.innerText.trim();
          const isStrike = el.style.textDecoration === "line-through"
            || getComputedStyle(el).textDecoration.includes("line-through")
            || el.closest("[class*='old'], [class*='cross'], [class*='before']");
          if (isStrike) { price_before = parsePrice(t); }
          else if (!price) { price = parsePrice(t); }
        }

        // Рейтинг
        const ratingEl = card.querySelector('[class*="rating"] span, [class*="star"] span');
        const rating = ratingEl ? parseFloat(ratingEl.innerText.replace(",", ".")) || null : null;

        // Отзывы
        const reviewEl = card.querySelector('[class*="review"], [class*="comment"]');
        const review_count = reviewEl ? parseInt(reviewEl.innerText.replace(/\D/g, "")) || null : null;

        // Промо-лейбл
        const promoEl = card.querySelector('[class*="badge"], [class*="label"], [class*="tag"], [class*="promo"]');
        const promo_label = promoEl ? promoEl.innerText.trim().slice(0, 100) || null : null;

        // Фото
        const imgEl = card.querySelector('img');
        const thumbnail_url = imgEl?.src || imgEl?.dataset?.src || null;

        seen.add(sku);
        cards.push({ position, sku, title, brand, price, price_before, rating, review_count, promo_label, thumbnail_url });
        if (cards.length >= limitArg) break;
      }
      return cards;
    },
    args: [limit],
  });

  const positions = (result && result.result) || [];
  if (!positions.length) throw new Error("Не удалось собрать карточки из выдачи ozon.ru");
  return { positions, query_text };
}


async function enrichWithBestsellers(skus) {
  // Обогащает массив SKU данными из bestsellers (выручка, продажи в день).
  // Возвращает Map: sku (string) → { revenue_30d, sales_per_day }
  if (!skus || !skus.length) return {};
  const result = {};
  for (const sku of skus) {
    try {
      const resp = await fetchBestsellers({ search: String(sku), limit: 5 });
      const item = (resp.items || []).find(i =>
        String(i.sku || i.id || i.item_id) === String(sku)
      );
      if (item) {
        result[String(sku)] = {
          revenue_30d: item.sum_gmv || item.revenue || item.gmv || null,
          sales_per_day: item.orders_per_day || item.sales_per_day || null,
        };
      }
    } catch (e) {
      // Конкретный SKU не найден — не критично
    }
  }
  return result;
}
```

- [ ] **Шаг 2: Зарегистрировать новые actions в `onMessage.addListener`**

Найти блок внутри `chrome.runtime.onMessage.addListener` (в конце background.js) и добавить две ветки рядом с `fetch_bestsellers`:

```javascript
      } else if (msg.action === "scrape_serp") {
        const data = await scrapeSerpPage(msg.options || {});
        sendResponse({ ok: true, data });
      } else if (msg.action === "enrich_with_bestsellers") {
        const data = await enrichWithBestsellers(msg.skus || []);
        sendResponse({ ok: true, data });
```

Итоговый блок listener должен выглядеть так:
```javascript
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.action === "lookup_calculator") {
        const data = await lookupCalculator(msg.query);
        sendResponse({ ok: true, data });
      } else if (msg.action === "fetch_bestsellers") {
        const data = await fetchBestsellers(msg.options || {});
        sendResponse({ ok: true, data });
      } else if (msg.action === "scrape_serp") {
        const data = await scrapeSerpPage(msg.options || {});
        sendResponse({ ok: true, data });
      } else if (msg.action === "enrich_with_bestsellers") {
        const data = await enrichWithBestsellers(msg.skus || []);
        sendResponse({ ok: true, data });
      } else if (msg.action === "ping") {
        sendResponse({ ok: true, version: chrome.runtime.getManifest().version });
      } else {
        sendResponse({ ok: false, error: "unknown action" });
      }
    } catch (e) {
      sendResponse({ ok: false, error: e.message || String(e) });
    }
  })();
  return true;
});
```

- [ ] **Шаг 3: Проверить синтаксис**

```bash
node --check chrome-extension/unitka/background.js && echo "syntax OK"
```

Ожидаемый вывод: `syntax OK`

- [ ] **Шаг 4: Перезагрузить расширение в Chrome**

Открыть `chrome://extensions/` → найти «Ozon Unitka Helper» → нажать кнопку перезагрузки (🔄).

- [ ] **Шаг 5: Коммит**

```bash
git add chrome-extension/unitka/background.js
git commit -m "feat(serp): add scrape_serp and enrich_with_bestsellers actions to background.js"
```

---

## Task 4: HTTP-роуты serp.py + регистрация в app.py

**Files:**
- Create: `src/dashboard/routes/serp.py`
- Modify: `src/dashboard/app.py`

- [ ] **Шаг 1: Создать `src/dashboard/routes/serp.py`**

```python
# src/dashboard/routes/serp.py
"""SERP-роуты: сбор и хранение выдачи ozon.ru по поисковому запросу."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

import asyncpg
from aiohttp import web

from src.services.serp_service import (
    save_snapshot,
    get_latest_snapshot,
    mark_competitor,
    get_competitors,
    get_primary_query,
    set_primary_query,
    recalculate_primary_queries,
    get_top_queries_for_sku,
    get_article_serp_report,
)

logger = logging.getLogger(__name__)

# Таймаут ожидания ответа от плагина (скрейп может занять ~15 сек)
PLUGIN_TIMEOUT = 30.0


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _to_int(v) -> Optional[int]:
    try: return int(v)
    except (TypeError, ValueError): return None


async def _call_plugin(request: web.Request, action: str, payload: Dict[str, Any]) -> Dict:
    """
    Отправляет сообщение Chrome-плагину через SSE-шину дашборда.
    Дашборд имеет endpoint /api/plugin/call, который проксирует вызов
    через window.dispatchEvent → content.js → background.js.
    
    Механизм: дашборд хранит очередь pending запросов к плагину в app["plugin_pending"].
    JS-страница слушает /api/plugin/poll, выполняет вызов и отвечает на /api/plugin/result.
    """
    app = request.app
    pending: dict = app.setdefault("plugin_pending", {})

    import uuid
    request_id = str(uuid.uuid4())
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    pending[request_id] = fut

    # Помещаем задачу в очередь для JS-клиента
    queue: asyncio.Queue = app.setdefault("plugin_queue", asyncio.Queue())
    await queue.put({"requestId": request_id, "action": action, "payload": payload})

    try:
        result = await asyncio.wait_for(fut, timeout=PLUGIN_TIMEOUT)
    except asyncio.TimeoutError:
        pending.pop(request_id, None)
        raise web.HTTPGatewayTimeout(reason="Plugin timeout — расширение не ответило за 30 сек")
    finally:
        pending.pop(request_id, None)

    if not result.get("ok"):
        raise web.HTTPBadGateway(reason=result.get("error", "Plugin error"))

    return result.get("data", {})


# ────────────────────────────────────────────────────────────────────────────
# Plugin bridge endpoints (вызываются JS-страницей дашборда)
# ────────────────────────────────────────────────────────────────────────────

async def plugin_poll(request: web.Request) -> web.Response:
    """JS дашборда поллит этот endpoint, чтобы забрать задачи для плагина."""
    queue: asyncio.Queue = request.app.setdefault("plugin_queue", asyncio.Queue())
    try:
        task = await asyncio.wait_for(queue.get(), timeout=20.0)
        return web.json_response(task)
    except asyncio.TimeoutError:
        return web.json_response({"requestId": None})  # нет задач


async def plugin_result(request: web.Request) -> web.Response:
    """JS дашборда POST-ит сюда результат выполненного вызова плагина."""
    body = await request.json()
    request_id = body.get("requestId")
    pending: dict = request.app.get("plugin_pending", {})
    fut = pending.get(request_id)
    if fut and not fut.done():
        fut.set_result(body.get("response", {}))
    return web.json_response({"ok": True})


# ────────────────────────────────────────────────────────────────────────────
# SERP endpoints
# ────────────────────────────────────────────────────────────────────────────

async def post_serp_scrape(request: web.Request) -> web.Response:
    """POST /api/serp/scrape — запустить скрейп выдачи по запросу."""
    body = await request.json()
    query_text = (body.get("query_text") or "").strip()
    if not query_text:
        return web.json_response({"error": "query_text required"}, status=400)

    limit = int(body.get("limit", 20))
    pool: asyncpg.Pool = request.app["pool"]

    # 1. Запрашиваем скрейп через плагин
    plugin_data = await _call_plugin(request, "scrape_serp", {"query_text": query_text, "limit": limit})
    positions = plugin_data.get("positions", [])

    # 2. Обогащаем конкурентов данными bestsellers
    competitor_skus = [p["sku"] for p in positions if p.get("sku")]
    if competitor_skus:
        try:
            enriched = await _call_plugin(request, "enrich_with_bestsellers", {"skus": competitor_skus})
            for p in positions:
                extra = enriched.get(str(p.get("sku")), {})
                p["revenue_30d"] = extra.get("revenue_30d")
                p["sales_per_day"] = extra.get("sales_per_day")
        except Exception as e:
            logger.warning("Bestsellers enrichment failed: %s", e)

    # 3. Сохраняем в БД
    snapshot_id = await save_snapshot(pool, query_text, positions, raw_data={"source": "plugin"})

    return web.json_response({"snapshot_id": snapshot_id, "position_count": len(positions)})


async def post_serp_scrape_by_sku(request: web.Request) -> web.Response:
    """POST /api/serp/scrape-by-sku — скрейп по главному запросу артикула."""
    body = await request.json()
    sku = _to_int(body.get("sku"))
    if not sku:
        return web.json_response({"error": "sku required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    primary = await get_primary_query(pool, sku)
    if not primary:
        return web.json_response({"error": "Главный запрос не задан для этого SKU"}, status=404)

    # Делегируем в post_serp_scrape через синтетический запрос
    request._json_body = {"query_text": primary["query_text"]}
    request._read_bytes = json.dumps(request._json_body).encode()
    return await post_serp_scrape(request)


async def get_serp_snapshot(request: web.Request) -> web.Response:
    """GET /api/serp/snapshot?query=... — последний снимок выдачи."""
    query_text = (request.query.get("query") or "").strip()
    if not query_text:
        return web.json_response({"error": "query required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    snapshot = await get_latest_snapshot(pool, query_text)
    if not snapshot:
        return web.json_response({"snapshot": None})
    return web.json_response({"snapshot": snapshot})


async def post_serp_competitor(request: web.Request) -> web.Response:
    """POST /api/serp/competitor — пометить/снять метку конкурента."""
    body = await request.json()
    sku = _to_int(body.get("sku"))
    if not sku:
        return web.json_response({"error": "sku required"}, status=400)

    is_competitor = bool(body.get("is_competitor", True))
    note = (body.get("note") or "").strip()
    pool: asyncpg.Pool = request.app["pool"]
    await mark_competitor(pool, sku, is_competitor, note)
    return web.json_response({"ok": True})


async def get_serp_competitors(request: web.Request) -> web.Response:
    """GET /api/serp/competitors — список конкурентов."""
    pool: asyncpg.Pool = request.app["pool"]
    items = await get_competitors(pool)
    return web.json_response({"competitors": [
        {**i, "created_at": i["created_at"].isoformat() if i.get("created_at") else None}
        for i in items
    ]})


async def get_serp_primary_query(request: web.Request) -> web.Response:
    """GET /api/serp/primary-query?sku=... — главный запрос артикула."""
    sku = _to_int(request.query.get("sku"))
    if not sku:
        return web.json_response({"error": "sku required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    primary = await get_primary_query(pool, sku)
    top_queries = await get_top_queries_for_sku(pool, sku)

    return web.json_response({
        "primary": {
            **primary,
            "updated_at": primary["updated_at"].isoformat() if primary and primary.get("updated_at") else None,
        } if primary else None,
        "top_queries": [
            {**q, "score": float(q["score"] or 0), "avg_conversion": float(q["avg_conversion"] or 0)}
            for q in top_queries
        ],
    })


async def put_serp_primary_query(request: web.Request) -> web.Response:
    """PUT /api/serp/primary-query — установить главный запрос вручную."""
    body = await request.json()
    sku = _to_int(body.get("sku"))
    query_text = (body.get("query_text") or "").strip()
    if not sku or not query_text:
        return web.json_response({"error": "sku and query_text required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    await set_primary_query(pool, sku, query_text, manual=True)
    return web.json_response({"ok": True})


async def get_serp_article_report(request: web.Request) -> web.Response:
    """GET /api/serp/article-report?sku=... — снапшот для секции «Поиск» в артикуле."""
    sku = _to_int(request.query.get("sku"))
    if not sku:
        return web.json_response({"error": "sku required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    report = await get_article_serp_report(pool, sku)

    # Сериализуем Decimal → str, datetime → str
    def _serialize(obj):
        import decimal, datetime
        if isinstance(obj, decimal.Decimal): return str(obj)
        if isinstance(obj, (datetime.datetime, datetime.date)): return obj.isoformat()
        raise TypeError(f"Not serializable: {type(obj)}")

    return web.Response(
        text=json.dumps(report, default=_serialize),
        content_type="application/json",
    )


async def post_serp_recalculate_primary(request: web.Request) -> web.Response:
    """POST /api/serp/recalculate-primary — пересчитать главные запросы авто-правилом."""
    pool: asyncpg.Pool = request.app["pool"]
    count = await recalculate_primary_queries(pool)
    return web.json_response({"updated": count})
```

- [ ] **Шаг 2: Зарегистрировать роуты в `src/dashboard/app.py`**

Добавить импорт после блока `from src.dashboard.routes.report import get_monthly_report`:

```python
from src.dashboard.routes.serp import (
    plugin_poll, plugin_result,
    post_serp_scrape, post_serp_scrape_by_sku,
    get_serp_snapshot, post_serp_competitor, get_serp_competitors,
    get_serp_primary_query, put_serp_primary_query,
    get_serp_article_report, post_serp_recalculate_primary,
)
```

Добавить роуты в `create_app()` перед `return app`:

```python
    # SERP module
    app.router.add_get("/api/plugin/poll", plugin_poll)
    app.router.add_post("/api/plugin/result", plugin_result)
    app.router.add_post("/api/serp/scrape", post_serp_scrape)
    app.router.add_post("/api/serp/scrape-by-sku", post_serp_scrape_by_sku)
    app.router.add_get("/api/serp/snapshot", get_serp_snapshot)
    app.router.add_post("/api/serp/competitor", post_serp_competitor)
    app.router.add_get("/api/serp/competitors", get_serp_competitors)
    app.router.add_get("/api/serp/primary-query", get_serp_primary_query)
    app.router.add_put("/api/serp/primary-query", put_serp_primary_query)
    app.router.add_get("/api/serp/article-report", get_serp_article_report)
    app.router.add_post("/api/serp/recalculate-primary", post_serp_recalculate_primary)
```

- [ ] **Шаг 3: Проверить импорт**

```bash
PYTHONIOENCODING=utf-8 python -c "from src.dashboard.routes.serp import post_serp_scrape; print('OK')"
```

Ожидаемый вывод: `OK`

- [ ] **Шаг 4: Проверить запуск дашборда**

```bash
PYTHONIOENCODING=utf-8 python -m src.main --check-imports 2>&1 | head -5
```

Если команды `--check-imports` нет — просто запусти дашборд и убедись что стартует без ошибок:
```
run_dashboard.cmd
```
Дашборд должен стартовать на `http://127.0.0.1:8088`.

- [ ] **Шаг 5: Коммит**

```bash
git add src/dashboard/routes/serp.py src/dashboard/app.py
git commit -m "feat(serp): add SERP routes and plugin bridge (poll/result)"
```

---

## Task 5: Plugin bridge — JS-сторона в дашборде

Дашборд должен поллить `/api/plugin/poll`, вызывать плагин и возвращать результат на `/api/plugin/result`. Это делается JS-кодом на странице дашборда, который работает в контексте, где доступен `content.js` плагина.

**Files:**
- Modify: `web/orders_dashboard.html`

- [ ] **Шаг 1: Добавить plugin bridge в `<script>` секцию `orders_dashboard.html`**

Найти закрывающий тег `</script>` (или место, где заканчиваются основные JS-инициализации) и добавить перед ним:

```javascript
// ═══════════════════════════════════════════════════════════
// Plugin Bridge — проксирует вызовы backend → Chrome-плагин
// ═══════════════════════════════════════════════════════════
(function startPluginBridge() {
  let bridgeActive = true;

  async function callPlugin(action, payload) {
    return new Promise((resolve, reject) => {
      const requestId = crypto.randomUUID();
      const timeout = setTimeout(() => {
        window.removeEventListener("ozon-unitka:response", handler);
        reject(new Error("Plugin timeout"));
      }, 28000);

      function handler(e) {
        if (e.detail?.requestId === requestId) {
          clearTimeout(timeout);
          window.removeEventListener("ozon-unitka:response", handler);
          resolve(e.detail.response);
        }
      }
      window.addEventListener("ozon-unitka:response", handler);
      window.dispatchEvent(new CustomEvent("ozon-unitka:request", {
        detail: { requestId, action, payload }
      }));
    });
  }

  async function pollLoop() {
    while (bridgeActive) {
      try {
        const task = await fetch("/api/plugin/poll").then(r => r.json());
        if (!task.requestId) continue; // нет задач — сразу следующий запрос

        const { requestId, action, payload } = task;
        let response;
        try {
          response = await callPlugin(action, payload);
        } catch (e) {
          response = { ok: false, error: e.message };
        }

        await fetch("/api/plugin/result", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ requestId, response }),
        });
      } catch (e) {
        // Сетевая ошибка — пауза перед повтором
        await new Promise(r => setTimeout(r, 2000));
      }
    }
  }

  pollLoop();
})();
```

- [ ] **Шаг 2: Проверить bridge вручную**

1. Открыть дашборд `http://127.0.0.1:8088`
2. Открыть DevTools → Console
3. Выполнить:
```javascript
window.dispatchEvent(new CustomEvent("ozon-unitka:request", {
  detail: { requestId: "test-1", action: "ping", payload: {} }
}));
window.addEventListener("ozon-unitka:response", e => console.log("RESPONSE:", e.detail));
```
Ожидаемый вывод в консоли: `RESPONSE: {requestId: "test-1", response: {ok: true, version: "0.1.0"}}`

- [ ] **Шаг 3: Коммит**

```bash
git add web/orders_dashboard.html
git commit -m "feat(serp): add plugin bridge polling loop to dashboard JS"
```

---

## Task 6: Вкладка «Выдача» в дашборде

**Files:**
- Modify: `web/orders_dashboard.html`

- [ ] **Шаг 1: Добавить вкладку в навигацию**

Найти в `orders_dashboard.html` блок с вкладками навигации (ищи `<button` или `<a` с tab-переключателями) и добавить кнопку «Выдача» рядом с остальными вкладками:

```html
<button class="tab-btn" data-tab="serp" onclick="showTab('serp')">🔍 Выдача</button>
```

- [ ] **Шаг 2: Добавить HTML-контент вкладки**

Найти место где объявляются блоки вкладок (ищи `<div id="tab-` или аналогичный паттерн) и добавить:

```html
<!-- ═══════════════════════ ВКЛАДКА: ВЫДАЧА ═══════════════════════ -->
<div id="tab-serp" class="tab-content" style="display:none;">
  <div style="padding:16px;">
    <h2 style="margin-bottom:12px;">🔍 Поисковая выдача Ozon</h2>

    <!-- Форма запроса -->
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
      <input id="serp-query-input" type="text" placeholder="Введите поисковый запрос..."
             style="flex:1;padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:14px;">
      <button id="serp-scrape-btn" onclick="serpScrape()"
              style="padding:8px 16px;background:#005bff;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:14px;">
        🔄 Обновить выдачу
      </button>
    </div>

    <!-- Статус -->
    <div id="serp-status" style="font-size:13px;color:#888;margin-bottom:12px;"></div>

    <!-- Фильтры -->
    <div style="display:flex;gap:8px;margin-bottom:12px;">
      <button class="serp-filter-btn active" onclick="serpFilter('all')" data-filter="all"
              style="padding:4px 12px;border:1px solid #ddd;border-radius:4px;cursor:pointer;background:#f0f4ff;font-size:13px;">Все</button>
      <button class="serp-filter-btn" onclick="serpFilter('our')" data-filter="our"
              style="padding:4px 12px;border:1px solid #ddd;border-radius:4px;cursor:pointer;font-size:13px;">Наши</button>
      <button class="serp-filter-btn" onclick="serpFilter('competitors')" data-filter="competitors"
              style="padding:4px 12px;border:1px solid #ddd;border-radius:4px;cursor:pointer;font-size:13px;">Конкуренты</button>
    </div>

    <!-- Таблица -->
    <div style="overflow-x:auto;">
      <table id="serp-table" style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#f5f7fa;border-bottom:2px solid #e0e4eb;">
            <th style="padding:8px;text-align:center;width:40px;">#</th>
            <th style="padding:8px;width:48px;">Фото</th>
            <th style="padding:8px;text-align:left;">Название / SKU</th>
            <th style="padding:8px;">Бренд</th>
            <th style="padding:8px;text-align:right;">Цена</th>
            <th style="padding:8px;text-align:right;">До скидки</th>
            <th style="padding:8px;text-align:right;">Выручка 30д</th>
            <th style="padding:8px;text-align:right;">Прод/день</th>
            <th style="padding:8px;text-align:center;">Рейтинг</th>
            <th style="padding:8px;text-align:right;">Отзывы</th>
            <th style="padding:8px;">Акция</th>
            <th style="padding:8px;text-align:center;">Метка</th>
          </tr>
        </thead>
        <tbody id="serp-tbody"></tbody>
      </table>
    </div>
  </div>
</div>
```

- [ ] **Шаг 3: Добавить JS-логику вкладки «Выдача»**

В `<script>` секцию добавить:

```javascript
// ═══════════════════════════════════════════════════════════
// Вкладка «Выдача» (SERP)
// ═══════════════════════════════════════════════════════════
let _serpCurrentFilter = 'all';
let _serpPositions = [];

async function serpScrape() {
  const query = document.getElementById('serp-query-input').value.trim();
  if (!query) { alert('Введите поисковый запрос'); return; }

  const btn = document.getElementById('serp-scrape-btn');
  const status = document.getElementById('serp-status');
  btn.disabled = true;
  status.textContent = '⏳ Собираем выдачу...';

  try {
    const res = await fetch('/api/serp/scrape', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query_text: query, limit: 20 }),
    }).then(r => r.json());

    if (res.error) throw new Error(res.error);
    status.textContent = `✓ Снимок #${res.snapshot_id} — ${res.position_count} позиций`;
    await serpLoadSnapshot(query);
  } catch (e) {
    status.textContent = '❌ ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function serpLoadSnapshot(query) {
  const status = document.getElementById('serp-status');
  const res = await fetch('/api/serp/snapshot?query=' + encodeURIComponent(query)).then(r => r.json());
  if (!res.snapshot || !res.snapshot.positions) { serpRenderTable([]); return; }
  _serpPositions = res.snapshot.positions;
  const ts = new Date(res.snapshot.scraped_at).toLocaleString('ru-RU');
  status.textContent = `Снимок от ${ts} — ${_serpPositions.length} позиций`;
  serpRenderTable(_serpPositions);
}

function serpFilter(filter) {
  _serpCurrentFilter = filter;
  document.querySelectorAll('.serp-filter-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.filter === filter);
    b.style.background = b.dataset.filter === filter ? '#e8f0fe' : '';
  });
  serpRenderTable(_serpPositions);
}

function serpRenderTable(positions) {
  const tbody = document.getElementById('serp-tbody');
  let filtered = positions;
  if (_serpCurrentFilter === 'our') filtered = positions.filter(p => p.is_our_product);
  if (_serpCurrentFilter === 'competitors') filtered = positions.filter(p => p.is_competitor);

  tbody.innerHTML = filtered.map(p => {
    const rowStyle = p.is_our_product
      ? 'background:#f0fff4;'
      : p.is_competitor ? 'background:#fff8f0;' : '';
    const priceStr = p.price ? Number(p.price).toLocaleString('ru-RU') + ' ₽' : '—';
    const priceBeforeStr = p.price_before ? Number(p.price_before).toLocaleString('ru-RU') + ' ₽' : '—';
    const revenueStr = p.revenue_30d ? Number(p.revenue_30d).toLocaleString('ru-RU') + ' ₽' : '—';
    const salesStr = p.sales_per_day ? Number(p.sales_per_day).toFixed(1) : '—';
    const ratingStr = p.rating ? Number(p.rating).toFixed(1) : '—';
    const reviewStr = p.review_count ? p.review_count.toLocaleString('ru-RU') : '—';
    const promoStr = p.promo_label || '—';
    const thumb = p.thumbnail_url
      ? `<img src="${p.thumbnail_url}" style="width:40px;height:40px;object-fit:cover;border-radius:4px;">`
      : '<div style="width:40px;height:40px;background:#eee;border-radius:4px;"></div>';
    const compLabel = p.is_competitor
      ? `<button onclick="serpToggleCompetitor(${p.sku}, false)" style="padding:2px 8px;background:#ff9500;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;">★ Конкурент</button>`
      : `<button onclick="serpToggleCompetitor(${p.sku}, true)" style="padding:2px 8px;background:#eee;border:none;border-radius:4px;cursor:pointer;font-size:11px;">Пометить</button>`;
    const ourBadge = p.is_our_product ? ' <span style="font-size:10px;color:#1a8c3c;">●НАШ</span>' : '';

    return `<tr style="${rowStyle}border-bottom:1px solid #f0f0f0;">
      <td style="padding:8px;text-align:center;font-weight:600;">${p.position}</td>
      <td style="padding:4px;">${thumb}</td>
      <td style="padding:8px;">
        <div style="font-weight:500;font-size:12px;">${(p.title || '').slice(0,60)}${ourBadge}</div>
        <div style="color:#888;font-size:11px;">SKU: ${p.sku || '—'}</div>
      </td>
      <td style="padding:8px;font-size:12px;">${p.brand || '—'}</td>
      <td style="padding:8px;text-align:right;font-weight:600;">${priceStr}</td>
      <td style="padding:8px;text-align:right;color:#999;text-decoration:line-through;">${priceBeforeStr}</td>
      <td style="padding:8px;text-align:right;">${revenueStr}</td>
      <td style="padding:8px;text-align:right;">${salesStr}</td>
      <td style="padding:8px;text-align:center;">${ratingStr}</td>
      <td style="padding:8px;text-align:right;">${reviewStr}</td>
      <td style="padding:8px;font-size:12px;">${promoStr}</td>
      <td style="padding:8px;text-align:center;">${compLabel}</td>
    </tr>`;
  }).join('');
}

async function serpToggleCompetitor(sku, isCompetitor) {
  await fetch('/api/serp/competitor', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sku, is_competitor: isCompetitor }),
  });
  // Обновляем флаг локально
  _serpPositions = _serpPositions.map(p =>
    p.sku == sku ? { ...p, is_competitor: isCompetitor } : p
  );
  serpRenderTable(_serpPositions);
}
```

- [ ] **Шаг 4: Проверить визуально**

1. Открыть `http://127.0.0.1:8088`
2. Кликнуть вкладку «Выдача»
3. Ввести запрос, нажать «Обновить выдачу»
4. Убедиться что таблица заполняется

- [ ] **Шаг 5: Коммит**

```bash
git add web/orders_dashboard.html
git commit -m "feat(serp): add SERP tab with position table, filters, competitor marking"
```

---

## Task 7: Секция «Поиск» в отчёте по артикулу

**Files:**
- Modify: `web/orders_dashboard.html`

- [ ] **Шаг 1: Найти место вставки**

В `orders_dashboard.html` найти функцию `renderArticleAnalytics` или блок `article-analytics-detail` (секция с детальным отчётом по артикулу). Добавить вызов `renderSerpSection(sku)` в конце рендера артикула.

- [ ] **Шаг 2: Добавить HTML-контейнер секции**

В шаблон/место отрисовки детального отчёта по артикулу добавить:

```html
<!-- Секция Поиск -->
<div id="article-serp-section" style="margin-top:20px;padding:16px;border:1px solid #e0e4eb;border-radius:8px;background:#fafbfc;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;font-size:15px;">🔍 Поиск</h3>
    <button id="article-serp-refresh-btn" onclick="articleSerpRefresh()"
            style="padding:4px 12px;background:#005bff;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;">
      🔄 Обновить
    </button>
  </div>

  <!-- Главный запрос -->
  <div style="margin-bottom:12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
    <span style="font-size:13px;color:#555;">Главный запрос:</span>
    <strong id="article-serp-query" style="font-size:13px;">—</strong>
    <span id="article-serp-manual-badge" style="display:none;font-size:11px;background:#e8f0fe;color:#1a73e8;padding:2px 6px;border-radius:4px;">вручную</span>
    <select id="article-serp-query-select" style="font-size:12px;padding:4px;border:1px solid #ddd;border-radius:4px;display:none;">
    </select>
    <button onclick="articleSerpShowQuerySelect()" style="font-size:12px;padding:2px 8px;border:1px solid #ddd;border-radius:4px;cursor:pointer;background:#fff;">Изменить</button>
    <button id="article-serp-save-query-btn" onclick="articleSerpSaveQuery()" style="font-size:12px;padding:2px 8px;border:none;border-radius:4px;cursor:pointer;background:#005bff;color:#fff;display:none;">Сохранить</button>
  </div>

  <!-- Наша позиция -->
  <div style="margin-bottom:12px;">
    <span style="font-size:13px;color:#555;">Наша позиция:</span>
    <strong id="article-serp-our-pos" style="font-size:15px;margin-left:8px;color:#1a8c3c;">—</strong>
    <span style="font-size:12px;color:#888;margin-left:8px;">Цена:</span>
    <strong id="article-serp-our-price" style="font-size:13px;margin-left:4px;">—</strong>
  </div>

  <!-- Дата снимка -->
  <div id="article-serp-date" style="font-size:11px;color:#aaa;margin-bottom:12px;"></div>

  <!-- Мини-таблица позиций -->
  <div style="overflow-x:auto;">
    <table id="article-serp-table" style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead>
        <tr style="background:#f5f7fa;border-bottom:2px solid #e0e4eb;">
          <th style="padding:6px;text-align:center;">#</th>
          <th style="padding:6px;text-align:left;">Название / SKU</th>
          <th style="padding:6px;">Бренд</th>
          <th style="padding:6px;text-align:right;">Цена</th>
          <th style="padding:6px;text-align:center;">Рейтинг</th>
          <th style="padding:6px;text-align:right;">Отзывы</th>
        </tr>
      </thead>
      <tbody id="article-serp-tbody"></tbody>
    </table>
  </div>

  <div id="article-serp-status" style="font-size:12px;color:#888;margin-top:8px;"></div>
</div>
```

- [ ] **Шаг 3: Добавить JS-логику секции**

```javascript
// ═══════════════════════════════════════════════════════════
// Секция «Поиск» в отчёте по артикулу
// ═══════════════════════════════════════════════════════════
let _articleSerpSku = null;
let _articleSerpTopQueries = [];

async function renderSerpSection(sku) {
  _articleSerpSku = sku;
  document.getElementById('article-serp-section').style.display = 'block';
  document.getElementById('article-serp-status').textContent = 'Загрузка...';

  try {
    const res = await fetch('/api/serp/article-report?sku=' + sku).then(r => r.json());
    _articleSerpRender(res);
  } catch (e) {
    document.getElementById('article-serp-status').textContent = '❌ ' + e.message;
  }

  // Загружаем топ запросов для dropdown
  try {
    const pqRes = await fetch('/api/serp/primary-query?sku=' + sku).then(r => r.json());
    _articleSerpTopQueries = pqRes.top_queries || [];
  } catch (e) { /* нет данных — не критично */ }
}

function _articleSerpRender(data) {
  document.getElementById('article-serp-query').textContent = data.primary_query || '—';
  const manualBadge = document.getElementById('article-serp-manual-badge');
  manualBadge.style.display = data.set_manually ? 'inline' : 'none';

  document.getElementById('article-serp-our-pos').textContent = data.our_position ? `#${data.our_position}` : '—';
  document.getElementById('article-serp-our-price').textContent = data.our_price ? Number(data.our_price).toLocaleString('ru-RU') + ' ₽' : '—';
  document.getElementById('article-serp-date').textContent = data.scraped_at
    ? 'Снимок от ' + new Date(data.scraped_at).toLocaleString('ru-RU')
    : '';
  document.getElementById('article-serp-status').textContent = '';

  const tbody = document.getElementById('article-serp-tbody');
  tbody.innerHTML = (data.positions || []).map(p => {
    const rowStyle = p.is_our_product ? 'background:#f0fff4;font-weight:600;' : p.is_competitor ? 'background:#fff8f0;' : '';
    const priceStr = p.price ? Number(p.price).toLocaleString('ru-RU') + ' ₽' : '—';
    const badge = p.is_our_product ? ' <span style="color:#1a8c3c;font-size:10px;">●НАШ</span>' : '';
    return `<tr style="${rowStyle}border-bottom:1px solid #f0f0f0;">
      <td style="padding:6px;text-align:center;">${p.position}</td>
      <td style="padding:6px;">${(p.title||'').slice(0,50)}${badge}<br><span style="color:#aaa;font-size:10px;">SKU: ${p.sku||'—'}</span></td>
      <td style="padding:6px;">${p.brand||'—'}</td>
      <td style="padding:6px;text-align:right;">${priceStr}</td>
      <td style="padding:6px;text-align:center;">${p.rating ? Number(p.rating).toFixed(1) : '—'}</td>
      <td style="padding:6px;text-align:right;">${p.review_count ? p.review_count.toLocaleString('ru-RU') : '—'}</td>
    </tr>`;
  }).join('');
}

async function articleSerpRefresh() {
  if (!_articleSerpSku) return;
  const btn = document.getElementById('article-serp-refresh-btn');
  const status = document.getElementById('article-serp-status');
  btn.disabled = true;
  status.textContent = '⏳ Обновляем...';
  try {
    await fetch('/api/serp/scrape-by-sku', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku: _articleSerpSku }),
    }).then(r => r.json());
    await renderSerpSection(_articleSerpSku);
  } catch (e) {
    status.textContent = '❌ ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

function articleSerpShowQuerySelect() {
  const select = document.getElementById('article-serp-query-select');
  const saveBtn = document.getElementById('article-serp-save-query-btn');
  select.innerHTML = _articleSerpTopQueries.map(q =>
    `<option value="${q.query_text}">${q.query_text} (score: ${q.score.toFixed(0)})</option>`
  ).join('');
  select.style.display = 'inline';
  saveBtn.style.display = 'inline';
}

async function articleSerpSaveQuery() {
  const select = document.getElementById('article-serp-query-select');
  const queryText = select.value;
  if (!queryText || !_articleSerpSku) return;
  await fetch('/api/serp/primary-query', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sku: _articleSerpSku, query_text: queryText }),
  });
  select.style.display = 'none';
  document.getElementById('article-serp-save-query-btn').style.display = 'none';
  document.getElementById('article-serp-query').textContent = queryText;
  document.getElementById('article-serp-manual-badge').style.display = 'inline';
}
```

- [ ] **Шаг 4: Вызвать `renderSerpSection` при открытии артикула**

Найти в существующем коде место, где рендерится детальный отчёт по артикулу (обычно функция типа `showArticleDetail(sku)` или `loadArticleAnalytics(sku)`). В конце этой функции добавить:

```javascript
renderSerpSection(sku);
```

- [ ] **Шаг 5: Проверить визуально**

1. Открыть отчёт по любому артикулу
2. Убедиться, что секция «🔍 Поиск» отображается
3. Нажать «Обновить» — должен запуститься скрейп

- [ ] **Шаг 6: Коммит**

```bash
git add web/orders_dashboard.html
git commit -m "feat(serp): add Search section to article report with primary query management"
```

---

## Task 8: Обогатить скрейпер overlay — богатые данные карточки

Текущий `scrapeSearchResults()` в `ozon-overlay.js` отдаёт только SKU+название+цена. Обновить чтобы собирал те же поля что `scrapeSerpPage` в background.js.

**Files:**
- Modify: `chrome-extension/unitka/ozon-overlay.js`

- [ ] **Шаг 1: Заменить функцию `scrapeSearchResults` в ozon-overlay.js**

Найти и заменить всю функцию `scrapeSearchResults()`:

```javascript
function scrapeSearchResults() {
  const cards = [];
  const seen = new Set();

  function parsePrice(text) {
    if (!text) return null;
    const m = text.replace(/\s/g, "").match(/(\d+)/);
    return m ? Number(m[1]) : null;
  }

  for (const a of document.querySelectorAll('a[href*="/product/"]')) {
    const sku = extractSkuFromUrl(a.getAttribute("href") || "");
    if (!sku || seen.has(sku)) continue;

    const card = a.closest('[class*="tile"], [class*="product-card"], [class*="widget"], article, li, div');
    if (!card) continue;

    const position = cards.length + 1;

    const nameEl = card.querySelector('h3, h2, [class*="title"], [class*="name"], span');
    const title = (nameEl?.innerText || "").trim().slice(0, 200);

    const brandEl = card.querySelector('[class*="brand"]');
    const brand = (brandEl?.innerText || "").trim() || null;

    // Цены
    const priceEls = [...card.querySelectorAll("*")].filter(el => {
      const t = el.childElementCount === 0 ? el.innerText?.trim() : "";
      return t && t.includes("₽") && t.length < 20;
    });
    let price = null, price_before = null;
    for (const el of priceEls) {
      const t = el.innerText.trim();
      const isStrike = getComputedStyle(el).textDecoration.includes("line-through")
        || el.closest("[class*='old'],[class*='cross'],[class*='before']");
      if (isStrike) { price_before = parsePrice(t); }
      else if (!price) { price = parsePrice(t); }
    }

    const ratingEl = card.querySelector('[class*="rating"] span, [class*="star"] span');
    const rating = ratingEl ? parseFloat(ratingEl.innerText.replace(",", ".")) || null : null;

    const reviewEl = card.querySelector('[class*="review"], [class*="comment"]');
    const review_count = reviewEl ? parseInt(reviewEl.innerText.replace(/\D/g, "")) || null : null;

    const promoEl = card.querySelector('[class*="badge"], [class*="label"], [class*="tag"], [class*="promo"]');
    const promo_label = promoEl ? promoEl.innerText.trim().slice(0, 100) || null : null;

    const imgEl = card.querySelector("img");
    const thumbnail_url = imgEl?.src || imgEl?.dataset?.src || null;

    seen.add(sku);
    cards.push({ position, sku, title, brand, price, price_before, rating, review_count, promo_label, thumbnail_url });
    if (cards.length >= 50) break;
  }
  return cards;
}
```

- [ ] **Шаг 2: Обновить `scrapeAndSend` — отправлять на правильный endpoint**

Найти в `scrapeAndSend` строку с `/api/unitka/import/competitor` и заменить на `/api/serp/save-from-overlay`:

```javascript
    const save = await fetch(`${base}/api/serp/save-from-overlay`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: cards, query_text: document.title }),
    }).then(r => r.json());
```

- [ ] **Шаг 3: Добавить endpoint `save-from-overlay` в `serp.py`**

В `src/dashboard/routes/serp.py` добавить функцию и зарегистрировать в `app.py`:

```python
async def post_serp_save_from_overlay(request: web.Request) -> web.Response:
    """POST /api/serp/save-from-overlay — сохранить данные собранные overlay с поисковой страницы."""
    body = await request.json()
    items = body.get("items", [])
    query_text = (body.get("query_text") or "неизвестный запрос").strip()[:500]
    if not items:
        return web.json_response({"error": "items required"}, status=400)

    pool: asyncpg.Pool = request.app["pool"]
    snapshot_id = await save_snapshot(pool, query_text, items, raw_data={"source": "overlay"})
    return web.json_response({"ok": True, "snapshot_id": snapshot_id, "count": len(items)})
```

В `app.py` добавить импорт `post_serp_save_from_overlay` и роут:
```python
app.router.add_post("/api/serp/save-from-overlay", post_serp_save_from_overlay)
```

- [ ] **Шаг 4: Проверить синтаксис overlay**

```bash
node --check chrome-extension/unitka/ozon-overlay.js && echo "syntax OK"
```

- [ ] **Шаг 5: Перезагрузить расширение и проверить вручную**

1. `chrome://extensions/` → перезагрузить плагин
2. Открыть `ozon.ru/search/?text=тест`
3. В оверлее нажать «Собрать со страницы»
4. Убедиться что статус показывает кол-во карточек без ошибок

- [ ] **Шаг 6: Коммит**

```bash
git add chrome-extension/unitka/ozon-overlay.js src/dashboard/routes/serp.py src/dashboard/app.py
git commit -m "feat(serp): enrich overlay scraper with position/rating/reviews/promo, add save-from-overlay endpoint"
```

---

## Self-Review

### Покрытие спеки
- ✅ 4 таблицы БД (Task 1)
- ✅ `serp_service.py` со всеми методами (Task 2)
- ✅ `background.js`: `scrape_serp` + `enrich_with_bestsellers` (Task 3)
- ✅ Все 11 HTTP-эндпоинтов (Task 4)
- ✅ Plugin bridge JS (Task 5)
- ✅ Вкладка «Выдача» (Task 6)
- ✅ Секция «Поиск» в артикуле (Task 7)
- ✅ Богатый скрейпер overlay (Task 8)
- ✅ Работает в текущем профиле Chrome — `_ensureTab` без новых профилей
- ✅ Фильтры Все/Наши/Конкуренты
- ✅ Смена главного запроса через dropdown
- ✅ Авто-правило: `MAX(searches × conversion)`

### Типы и сигнатуры
- `save_snapshot(pool, query_text, positions, raw_data)` → используется в Task 2, Task 4, Task 8 ✅
- `get_latest_snapshot(pool, query_text)` → Task 4 ✅
- `mark_competitor(pool, sku, is_competitor, note)` → Task 4 ✅
- `get_article_serp_report(pool, sku)` → Task 4 ✅
- `scrape_serp` action → Task 3 background.js, Task 4 serp.py ✅
