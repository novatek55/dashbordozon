"use strict";

// Overlay-виджет на страницах www.ozon.ru
//   - на товаре: кнопка «Отправить в Юнитку» (определяет SKU из URL)
//   - в поиске/категории: кнопка «Собрать со страницы» (скрейпит DOM)

const DASHBOARD_DEFAULT = "http://80.87.203.161";

async function getDashboardUrl() {
  const { dashboardUrl } = await chrome.storage.sync.get(["dashboardUrl"]);
  return (dashboardUrl || DASHBOARD_DEFAULT).replace(/\/+$/, "");
}

function extractSkuFromUrl(url) {
  // https://www.ozon.ru/product/xyz-1860180682/ → 1860180682
  const m = url.match(/\/product\/[^\/]*?-(\d{6,})\/?/);
  return m ? m[1] : null;
}

function isProductPage() { return /\/product\//.test(location.pathname); }
function isSearchPage() {
  return /\/search|\/category|\/highlight\/|\/seller\/.+\/products\//.test(location.pathname)
    || location.pathname === "/";
}

// ─── scraping search results ────────────────────────────────
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

    const card = a.closest('[class*="tile"], [class*="product-card"], [class*="widget"], article, li');
    if (!card) continue;

    const position = cards.length + 1;

    const nameEl = card.querySelector('h3, h2, [class*="title"], [class*="name"], span');
    const title = (nameEl?.innerText || "").trim().slice(0, 200);

    const brandEl = card.querySelector('[class*="brand"]');
    const brand = (brandEl?.innerText || "").trim() || null;

    // Цены: ищем элементы с ₽
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

// ─── panel ──────────────────────────────────────────────────
function buildPanel() {
  if (document.getElementById("ou-panel")) return;

  const panel = document.createElement("div");
  panel.id = "ou-panel";
  panel.innerHTML = `
    <div class="ou-head">
      <span>🧩 Юнитка Helper</span>
      <span class="ou-toggle" data-role="toggle">—</span>
    </div>
    <div class="ou-body"></div>
  `;
  document.body.appendChild(panel);

  panel.querySelector('[data-role="toggle"]').addEventListener("click", () => {
    panel.classList.toggle("collapsed");
    panel.querySelector(".ou-toggle").textContent = panel.classList.contains("collapsed") ? "+" : "—";
  });

  // Drag by header
  const head = panel.querySelector(".ou-head");
  let dragging = false, offsetX = 0, offsetY = 0;
  head.addEventListener("mousedown", (e) => {
    if (e.target.closest(".ou-toggle")) return;
    dragging = true;
    const rect = panel.getBoundingClientRect();
    offsetX = e.clientX - rect.left;
    offsetY = e.clientY - rect.top;
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    panel.style.left = (e.clientX - offsetX) + "px";
    panel.style.top = (e.clientY - offsetY) + "px";
    panel.style.right = "auto";
  });
  window.addEventListener("mouseup", () => { dragging = false; });

  renderBody();
}

function setStatus(text, kind) {
  const st = document.querySelector("#ou-panel .ou-status");
  if (!st) return;
  st.textContent = text;
  st.className = "ou-status " + (kind || "info");
}

function renderBody() {
  const body = document.querySelector("#ou-panel .ou-body");
  if (!body) return;
  const url = location.href;
  const sku = extractSkuFromUrl(url);

  let productHtml = "";
  if (isProductPage() && sku) {
    productHtml = `
      <div class="ou-section">
        <div><b>Товар на странице</b></div>
        <div class="ou-sku">SKU: ${sku}</div>
        <button class="primary" data-role="send-product">📐 Отправить в Юнитку</button>
        <div class="ou-hint">Вызывает калькулятор Ozon и сохраняет в БД.</div>
      </div>`;
  }

  let searchHtml = "";
  if (isSearchPage()) {
    searchHtml = `
      <div class="ou-section">
        <div><b>Страница поиска / категории</b></div>
        <button class="secondary" data-role="scrape-search">📋 Собрать со страницы</button>
        <div class="ou-hint">Скрейпит видимые карточки (до 50 шт) → сохраняет в БД.</div>
        <div class="ou-preview-wrap" data-role="preview-wrap" style="display:none;"></div>
      </div>`;
  }

  body.innerHTML = `
    ${productHtml}
    ${searchHtml}
    <div class="ou-status" style="display:none;"></div>
  `;

  const sendBtn = body.querySelector('[data-role="send-product"]');
  if (sendBtn) sendBtn.addEventListener("click", () => sendProductToUnitka(sku, sendBtn));

  const scrapeBtn = body.querySelector('[data-role="scrape-search"]');
  if (scrapeBtn) scrapeBtn.addEventListener("click", () => scrapeAndSend(scrapeBtn));
}

// ─── actions ────────────────────────────────────────────────

async function sendProductToUnitka(sku, btn) {
  btn.disabled = true;
  setStatus("Запрос к калькулятору Ozon…", "info");
  try {
    const resp = await chrome.runtime.sendMessage({
      action: "lookup_calculator",
      query: location.href,
    });
    if (!resp || !resp.ok) throw new Error((resp && resp.error) || "no response");
    const items = resp.data.items || [];
    if (!items.length) throw new Error("Калькулятор ничего не вернул");

    const base = await getDashboardUrl();
    const save = await fetch(`${base}/api/unitka/import/competitor`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    }).then(r => r.json());

    if (save.error) throw new Error(save.error);
    setStatus(`✓ Сохранено: ${save.count} шт. → БД + Юнитка`, "ok");
  } catch (e) {
    setStatus("Ошибка: " + e.message, "err");
  } finally {
    btn.disabled = false;
  }
}

async function scrapeAndSend(btn) {
  btn.disabled = true;
  setStatus("Скрейпим страницу…", "info");
  try {
    const cards = scrapeSearchResults();
    if (!cards.length) throw new Error("Не нашли карточек");

    // Превью
    const previewWrap = document.querySelector('[data-role="preview-wrap"]');
    if (previewWrap) {
      previewWrap.style.display = "block";
      previewWrap.innerHTML = `<table class="ou-preview">${cards.slice(0, 15).map(c => `
        <tr><td>${(c.name || c.sku).slice(0, 30)}</td>
            <td class="ou-price">${c.price_buyer ? c.price_buyer.toLocaleString("ru-RU") + " ₽" : "—"}</td></tr>
      `).join("")}</table>`;
    }

    const base = await getDashboardUrl();
    // Определяем запрос из URL страницы
    const urlParams = new URLSearchParams(location.search);
    const queryText = urlParams.get("text") || urlParams.get("search") || document.title || "поиск";

    const save = await fetch(`${base}/api/serp/save-from-overlay`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: cards, query_text: queryText }),
    }).then(r => r.json());

    if (save.error) throw new Error(save.error);
    setStatus(`✓ Отправлено: ${cards.length} карточек`, "ok");
  } catch (e) {
    setStatus("Ошибка: " + e.message, "err");
  } finally {
    btn.disabled = false;
  }
}

// ─── init + SPA-reactivity ──────────────────────────────────
buildPanel();

// Ozon — SPA, URL меняется без перезагрузки. Следим за изменениями.
let lastUrl = location.href;
new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    renderBody();
  }
}).observe(document.body, { childList: true, subtree: true });
