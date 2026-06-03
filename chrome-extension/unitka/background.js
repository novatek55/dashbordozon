"use strict";

// Сервис-воркер: принимает сообщения от content.js, делает HTTP-запросы
// (минуя CORS — есть host_permissions), возвращает результат.

async function _ensureTab(url) {
  const pattern = url + "*";
  const tabs = await chrome.tabs.query({ url: pattern });
  if (tabs.length) return tabs[0];
  // Открываем в фоне
  const tab = await chrome.tabs.create({ url, active: false });
  // Ждём загрузки
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 500));
    const t = await chrome.tabs.get(tab.id);
    if (t.status === "complete") return t;
  }
  return tab;
}

// Ждём, пока вкладка seller.ozon.ru дойдёт до НЕ-signin URL (после редиректов авторизации)
// и status === "complete". Возвращает финальное состояние tab или null если timeout.
async function _waitForSellerReady(tabId, maxMs = 15000) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 400));
    let t;
    try { t = await chrome.tabs.get(tabId); } catch (e) { return null; }
    const url = t.url || "";
    if (t.status === "complete" && url.startsWith("https://seller.ozon.ru/")
        && !/\/(signin|registration)/.test(url)) {
      return t;
    }
  }
  return null;
}

async function lookupCalculator(query) {
  const tab = await _ensureTab("https://calculator.ozon.ru/");
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: async (q) => {
      try {
        const r = await fetch(
          "/p-api/the-calculator-ozon-ru/api/item-search",
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-O3-App-Name": "calculator-ui",
              "Accept": "application/json",
            },
            credentials: "include",
            body: JSON.stringify({ query: q }),
          }
        );
        const text = await r.text();
        let data = null;
        try { data = JSON.parse(text); } catch (e) {}
        return { status: r.status, data, text_preview: data ? null : text.slice(0, 200) };
      } catch (e) {
        return { status: 0, error: String(e) };
      }
    },
    args: [query],
  });
  const res = result && result.result;
  if (!res) throw new Error("Нет ответа от вкладки calculator.ozon.ru");
  if (res.status !== 200) {
    throw new Error(`calculator HTTP ${res.status}: ${res.text_preview || res.error || ""}`);
  }
  const raw = res.data;
  const items = Array.isArray(raw) ? raw : (raw.items || raw.products || []);
  return { items };
}

// Авто-извлечение company_id из cookie sc_company_id (seller.ozon.ru),
// чтобы пользователю не нужно было вбивать его в popup настройках.
// Пробуем несколько URL — кука может быть на seller.ozon.ru или на родительском .ozon.ru.
async function _resolveCompanyId(explicit) {
  if (explicit && String(explicit).trim()) return String(explicit).trim();
  const urls = ["https://seller.ozon.ru/", "https://www.ozon.ru/", "https://ozon.ru/"];
  for (const url of urls) {
    try {
      const c = await chrome.cookies.get({ url, name: "sc_company_id" });
      if (c && c.value) return c.value;
    } catch (e) { /* try next */ }
  }
  // Fallback: getAll по домену
  try {
    const all = await chrome.cookies.getAll({ name: "sc_company_id" });
    for (const c of all || []) {
      if (c.value && /(^|\.)ozon\.ru$/.test(c.domain || "")) return c.value;
    }
  } catch (e) { /* ignore */ }
  return "";
}

// Ждём появления cookie sc_company_id (после загрузки/редиректов авторизации Ozon).
async function _waitForCompanyIdCookie(maxMs = 10000) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    const cid = await _resolveCompanyId("");
    if (cid) return cid;
    await new Promise((r) => setTimeout(r, 400));
  }
  return "";
}

async function fetchBestsellers({ period = "monthly", limit = 50, search = "", companyId = "", autoOpen = false }) {
  // Делаем fetch из контекста вкладки seller.ozon.ru (через chrome.scripting.executeScript),
  // чтобы куки гарантированно ушли как first-party.
  // Выбираем вкладку seller.ozon.ru, которая НЕ на странице авторизации.
  const allTabs = await chrome.tabs.query({ url: "https://seller.ozon.ru/*" });
  let validTabs = allTabs.filter(t => !/\/(signin|registration)/.test(t.url || ""));
  if (!validTabs.length) {
    if (!autoOpen) {
      // Машиночитаемый код — дашборд распознаёт и спрашивает пользователя
      throw new Error("NO_SELLER_TAB");
    }
    // Автооткрытие вкладки в фоне (по аналогии с _ensureTab для calculator.ozon.ru)
    const opened = await _ensureTab("https://seller.ozon.ru/app/analytics/what-to-sell/ozon-bestsellers");
    // Ждём окончания всех редиректов и появления валидной (НЕ-signin) страницы.
    const ready = await _waitForSellerReady(opened.id, 30000);
    if (!ready) {
      const cur = await chrome.tabs.get(opened.id).catch(() => null);
      if (cur && /\/(signin|registration)/.test(cur.url || "")) {
        throw new Error("Вкладка seller.ozon.ru открыта, но требуется вход — авторизуйтесь");
      }
      throw new Error("Не удалось дождаться загрузки seller.ozon.ru (timeout)");
    }
    validTabs = [ready];
  }
  const tab = validTabs[0];

  const body = {
    limit: String(limit), offset: "0",
    filter: { stock: "any_stock", period },
    sort: { key: "sum_gmv_desc" },
  };
  if (search) body.filter.name = search;

  const _doFetch = async () => {
    // Резолвим cid внутри попытки — после автооткрытия cookie может появиться позже.
    const cid = await _resolveCompanyId(companyId);
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: async (companyId, bodyJson) => {
        const headers = {
          "Accept": "application/json, text/plain, */*",
          "Content-Type": "application/json",
          "x-o3-app-name": "seller-ui",
          "x-o3-language": "ru",
          "x-o3-page-type": "analytics_platform",
        };
        if (companyId) headers["x-o3-company-id"] = companyId;
        const r = await fetch(
          "/api/site/seller-analytics/what_to_sell/data/v3",
          { method: "POST", headers, credentials: "include", body: bodyJson }
        );
        const text = await r.text();
        let data = null;
        try { data = JSON.parse(text); } catch (e) {}
        return { status: r.status, data, text_preview: data ? null : text.slice(0, 200), cid_used: companyId || "(empty)" };
      },
      args: [cid || "", JSON.stringify(body)],
    });
    return result && result.result;
  };

  // Полинг до 45 сек: каждые 1.5 сек заново резолвим cid и пробуем fetch.
  // Останавливаемся на любом ответе кроме 401/403/0. Это гораздо надёжнее, чем
  // угадывать момент готовности — Ozon SPA может ставить куки/CSRF несколькими волнами.
  const overallDeadline = Date.now() + 45000;
  let res = await _doFetch();
  let attempts = 1;
  while (Date.now() < overallDeadline && res && (res.status === 401 || res.status === 403 || res.status === 0)) {
    await new Promise((r) => setTimeout(r, 1500));
    res = await _doFetch();
    attempts++;
  }
  if (!res) throw new Error("Нет ответа от вкладки seller.ozon.ru");
  if (res.status !== 200) {
    if (res.status === 401 || res.status === 403) {
      throw new Error(`Не авторизованы (HTTP ${res.status}) — войдите в seller.ozon.ru`);
    }
    throw new Error(`seller HTTP ${res.status}: ${res.text_preview || ""}`);
  }
  const raw = res.data || {};
  return { items: raw.items || raw.data || [] };
}

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
          const isStrike = el.style.textDecoration === "line-through"
            || getComputedStyle(el).textDecoration.includes("line-through")
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
  // Возвращает объект: sku (string) → { revenue_30d, sales_per_day }
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

// ─── Message listener ───────────────────────────────────────────────────────

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
  return true;  // async response
});
