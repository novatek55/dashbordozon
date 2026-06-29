"use strict";

// Сервис-воркер: принимает сообщения от content.js, делает HTTP-запросы
// (минуя CORS — есть host_permissions), возвращает результат.

async function _ensureTab(url) {
  const pattern = url + "*";
  const tabs = await chrome.tabs.query({ url: pattern });
  if (tabs.length) return { tab: tabs[0], created: false, requested_url: url, final_url: tabs[0].url || "", status: tabs[0].status || "" };
  // Открываем в фоне
  const tab = await chrome.tabs.create({ url, active: false });
  // Ждём загрузки
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 500));
    const t = await chrome.tabs.get(tab.id);
    if (t.status === "complete") return { tab: t, created: true, requested_url: url, final_url: t.url || "", status: t.status || "" };
  }
  return { tab, created: true, requested_url: url, final_url: tab.url || "", status: tab.status || "" };
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

async function _waitForSellerBestsellersReady(tabId, maxMs = 20000) {
  const deadline = Date.now() + maxMs;
  const expectedPath = "/app/analytics/what-to-sell/ozon-bestsellers";
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 500));
    let t;
    try { t = await chrome.tabs.get(tabId); } catch (e) { return null; }
    const url = t.url || "";
    if (t.status === "complete" && url.startsWith("https://seller.ozon.ru/") && url.includes(expectedPath)) {
      await new Promise((r) => setTimeout(r, 2500));
      return t;
    }
  }
  return null;
}

async function _activateTab(tabId) {
  let tab = null;
  try { tab = await chrome.tabs.get(tabId); } catch (e) { return null; }
  if (!tab?.id) return null;
  const prev = await chrome.tabs.query({ active: true, currentWindow: true }).catch(() => []);
  const prevActiveTabId = prev[0]?.id || null;
  try {
    if (tab.windowId) {
      await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
    }
    await chrome.tabs.update(tab.id, { active: true });
    await new Promise((r) => setTimeout(r, 1200));
  } catch (e) {
    return { prevActiveTabId };
  }
  return { prevActiveTabId };
}

async function _restoreActiveTab(prevActiveTabId) {
  if (!prevActiveTabId) return;
  try {
    await chrome.tabs.update(prevActiveTabId, { active: true });
  } catch (e) {
    // ignore restore errors
  }
}

// SELLER_UI_HELPERS_START
function _normalizeSellerUiText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function _sellerPlaceholderType(value) {
  const normalized = _normalizeSellerUiText(value);
  if (!normalized) return "unknown";
  if (normalized === "поиск" || normalized === "рџрѕрёсѓрє") return "global-search";
  if (normalized === "название товара" || normalized === "рќр°р·рір°рѕрёрµ с‚рѕрір°сђр°") return "product-name";
  return "unknown";
}

function _pickSellerProductInput(inputs) {
  const candidates = (inputs || []).filter((entry) => entry && entry.visible !== false);
  let best = null;
  let bestScore = -Infinity;

  for (const entry of candidates) {
    const rect = entry.rect || {};
    const type = _sellerPlaceholderType(entry.placeholder);
    let score = 0;
    if (type === "product-name") score += 100;
    if (type === "global-search") score -= 100;
    if ((rect.y || 0) >= 120) score += 15;
    if ((rect.width || 0) >= 180) score += 5;
    if (score > bestScore) {
      best = entry;
      bestScore = score;
    }
  }

  return best;
}
// SELLER_UI_HELPERS_END

async function _prepareSellerBestsellersPage(tabId, period = "monthly", searchValue = "", maxMs = 25000) {
  const deadline = Date.now() + maxMs;
  const result = {
    input_ready_confirmed: false,
    period_28_confirmed: false,
    category_reset_confirmed: false,
    category_reset_clicked: false,
    ui_trace: [],
    final_url: "",
    final_title: "",
  };
  const expectedPath = "/app/analytics/what-to-sell/ozon-bestsellers";
  const activation = await _activateTab(tabId);

  try {
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 700));
      const [check] = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: async (periodArg, searchArg) => {
          const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
          const isVisible = (el) => {
            if (!el) return false;
            const rect = typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : null;
            if (!rect || rect.width <= 0 || rect.height <= 0) return false;
            const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            return !style || (style.display !== "none" && style.visibility !== "hidden");
          };
          const normalize = (value) => String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
          const uiTrace = [];
          const placeholderType = (value) => {
            const normalized = normalize(value);
            if (!normalized) return "unknown";
            if (normalized === "поиск" || normalized === "рџрѕрёсѓрє") return "global-search";
            if (normalized === "название товара" || normalized === "рќр°р·рір°рѕрёрµ с‚рѕрір°сђр°") return "product-name";
            return "unknown";
          };
          const activeMeta = () => {
            const el = document.activeElement;
            const rect = typeof el?.getBoundingClientRect === "function" ? el.getBoundingClientRect() : null;
            return {
              tag: el?.tagName || null,
              placeholder: el?.getAttribute?.("placeholder") || "",
              value: typeof el?.value === "string" ? el.value.slice(0, 80) : "",
              className: String(el?.className || "").slice(0, 120),
              rect: rect ? {
                x: Math.round(rect.x || 0),
                y: Math.round(rect.y || 0),
                width: Math.round(rect.width || 0),
                height: Math.round(rect.height || 0),
              } : null,
            };
          };
          const pushTrace = (step, extra = {}) => {
            if (uiTrace.length >= 50) return;
            uiTrace.push({
              step,
              active: activeMeta(),
              ...extra,
            });
          };
          const inputMeta = (el) => {
            const rect = typeof el?.getBoundingClientRect === "function" ? el.getBoundingClientRect() : null;
            return {
              el,
              placeholder: el?.getAttribute("placeholder") || "",
              type: placeholderType(el?.getAttribute("placeholder")),
              visible: isVisible(el),
              rect: {
                x: Math.round(rect?.x || 0),
                y: Math.round(rect?.y || 0),
                width: Math.round(rect?.width || 0),
                height: Math.round(rect?.height || 0),
              },
            };
          };
          const pickProductInput = (entries) => {
            let best = null;
            let bestScore = -Infinity;
            for (const entry of entries || []) {
              if (!entry?.visible) continue;
              const type = placeholderType(entry.placeholder);
              let score = 0;
              if (type === "product-name") score += 100;
              if (type === "global-search") score -= 100;
              if ((entry.rect?.y || 0) >= 120) score += 15;
              if ((entry.rect?.width || 0) >= 180) score += 5;
              if (score > bestScore) {
                best = entry;
                bestScore = score;
              }
            }
            return best?.el || null;
          };
          const clickLikeUser = (el) => {
            if (!el) return false;
            const target = el.closest?.("button,[role='button'],div") || el;
            for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
              target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
            }
            return true;
          };
          const setNativeInputValue = (el, value) => {
            if (!el) return false;
            const proto = window.HTMLInputElement?.prototype;
            const setter = proto ? Object.getOwnPropertyDescriptor(proto, "value")?.set : null;
            if (typeof setter === "function") setter.call(el, value);
            else el.value = value;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            return true;
          };
          const closeGlobalSearchIfOpen = () => {
            const globalSearch = [...document.querySelectorAll("input")].find((el) => {
              return placeholderType(el.getAttribute("placeholder")) === "global-search" && isVisible(el);
            });
            const activeEl = document.activeElement;
            const activeRect = typeof activeEl?.getBoundingClientRect === "function" ? activeEl.getBoundingClientRect() : null;
            const activeOverlayLike = !!activeEl
              && activeEl.tagName === "INPUT"
              && isVisible(activeEl)
              && placeholderType(activeEl.getAttribute("placeholder")) !== "product-name"
              && (activeRect?.y || 0) < 120
              && (activeRect?.width || 0) >= 500;
            pushTrace("global-search-scan", {
              overlayFound: !!globalSearch || activeOverlayLike,
              globalSearch: globalSearch ? inputMeta(globalSearch) : null,
              activeOverlayLike: activeOverlayLike ? inputMeta(activeEl) : null,
            });
            if (!globalSearch && !activeOverlayLike) return false;
            document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
            document.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape", code: "Escape", bubbles: true }));
            pushTrace("global-search-escape-sent");
            return true;
          };
          pushTrace("prepare-start", {
            allInputs: [...document.querySelectorAll("input")]
              .map((el) => inputMeta(el))
              .filter((entry) => entry.visible && entry.placeholder),
          });
          closeGlobalSearchIfOpen();
          await sleep(150);
          pushTrace("after-global-search-close", {
            visibleInputs: [...document.querySelectorAll("input")]
              .map((el) => inputMeta(el))
              .filter((entry) => entry.visible && entry.placeholder),
          });
          const periodLabel = periodArg === "monthly" ? "28" : "";
          let periodBtn = null;
          if (periodLabel) {
            periodBtn = [...document.querySelectorAll("button")].find((btn) => /28/.test(textOf(btn)) && /дн/i.test(textOf(btn)));
          }
          const periodActive = !!periodBtn && (
            periodBtn.getAttribute("data-active") === "true"
            || /active/i.test(periodBtn.className || "")
          );
          if (periodBtn && !periodActive && typeof periodBtn.click === "function") {
            periodBtn.click();
            pushTrace("period-clicked", { buttonText: textOf(periodBtn) });
            await sleep(700);
          }
          const periodBtnAfter = periodLabel
            ? [...document.querySelectorAll("button")].find((btn) => /28/.test(textOf(btn)) && /дн/i.test(textOf(btn)))
            : null;
          const periodActiveAfter = !!periodBtnAfter && (
            periodBtnAfter.getAttribute("data-active") === "true"
            || /active/i.test(periodBtnAfter.className || "")
          );

          const findCategoryChip = () => [...document.querySelectorAll("button, div")].find((el) => {
            const text = textOf(el);
            return /категори/i.test(text) && /:\s*\d+/.test(text) && !!el.querySelector("svg") && isVisible(el);
          });
          const findFilterScope = (chip) => {
            if (!chip) return document;
            let node = chip.parentElement;
            while (node && node !== document.body) {
              const namedInputs = [...node.querySelectorAll("input")].filter((el) => placeholderType(el.getAttribute("placeholder")) === "product-name" && isVisible(el));
              if (namedInputs.length) return node;
              node = node.parentElement;
            }
            return document;
          };
          let categoryChip = findCategoryChip();
          const filterScope = findFilterScope(categoryChip);
          const scopeInputs = [...filterScope.querySelectorAll("input")].map(inputMeta);
          const allInputs = [...document.querySelectorAll("input")].map(inputMeta);
          const pickedFromScope = pickProductInput(scopeInputs);
          const input = pickedFromScope || pickProductInput(allInputs);
          pushTrace("input-picked", {
            pickedFromScope: pickedFromScope ? inputMeta(pickedFromScope) : null,
            pickedFinal: input ? inputMeta(input) : null,
            scopeInputs: scopeInputs.filter((entry) => entry.visible && entry.placeholder),
            allInputs: allInputs.filter((entry) => entry.visible && entry.placeholder),
          });
          if (input && typeof input.focus === "function") {
            input.focus();
            pushTrace("input-focus-called", { pickedFinal: inputMeta(input) });
            await sleep(80);
            pushTrace("after-input-focus", { pickedFinal: inputMeta(input) });
            const activeAfterFocus = document.activeElement;
            if (activeAfterFocus !== input) {
              document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
              document.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape", code: "Escape", bubbles: true }));
              pushTrace("escape-after-focus-mismatch", {
                pickedFinal: inputMeta(input),
                actualActive: activeMeta(),
              });
              await sleep(120);
              input.focus();
              pushTrace("refocus-after-escape", { pickedFinal: inputMeta(input) });
              await sleep(80);
              pushTrace("after-refocus", { pickedFinal: inputMeta(input) });
            }
          }
          if (input && searchArg) {
            setNativeInputValue(input, String(searchArg));
            pushTrace("input-value-set", {
              pickedFinal: inputMeta(input),
              searchValue: String(searchArg).slice(0, 120),
            });
            await sleep(120);
            pushTrace("after-input-events", { pickedFinal: inputMeta(input) });
            input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
            input.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", bubbles: true }));
            pushTrace("input-enter-sent", { pickedFinal: inputMeta(input) });
            await sleep(150);
            pushTrace("after-input-enter", { pickedFinal: inputMeta(input) });
          }
          let categoryReset = categoryChip
            ? categoryChip.querySelector("svg")?.parentElement || categoryChip.querySelector("svg") || null
            : null;

          const hasSelectedCategoryChip = () => [...document.querySelectorAll("button, div")].some((el) => {
            const text = textOf(el);
            if (!/категор/i.test(text)) return false;
            if (!/\d/.test(text)) return false;
            if (!el.querySelector("svg")) return false;
            const rect = typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : null;
            return !!rect && rect.width > 0 && rect.height > 0;
          });

          let categoryResetClicked = false;
          if (categoryReset) {
            clickLikeUser(categoryReset);
            categoryResetClicked = true;
            pushTrace("category-reset-clicked");
            await sleep(900);
            categoryChip = findCategoryChip();
            categoryReset = categoryChip
              ? categoryChip.querySelector("svg")?.parentElement || categoryChip.querySelector("svg") || null
              : null;
          }

          return {
            url: location.href,
            title: document.title || "",
            input_ready_confirmed: !!input,
            period_28_confirmed: periodLabel ? !!periodActiveAfter : true,
            category_reset_confirmed: !hasSelectedCategoryChip(),
            category_reset_clicked: categoryResetClicked,
            ui_trace: uiTrace,
          };
        },
        args: [period, searchValue],
      }).catch(() => [{ result: null }]);

      const state = (check && check.result) || {};
      result.input_ready_confirmed = !!state.input_ready_confirmed;
      result.period_28_confirmed = !!state.period_28_confirmed;
      result.category_reset_confirmed = !!state.category_reset_confirmed;
      result.category_reset_clicked = result.category_reset_clicked || !!state.category_reset_clicked;
      result.ui_trace = Array.isArray(state.ui_trace) ? state.ui_trace : result.ui_trace;
      result.final_url = state.url || result.final_url;
      result.final_title = state.title || result.final_title;

      if (
        result.final_url.includes(expectedPath)
        && result.input_ready_confirmed
        && result.period_28_confirmed
        && result.category_reset_confirmed
      ) {
        return result;
      }
    }
    return result;
  } finally {
    await _restoreActiveTab(activation?.prevActiveTabId || null);
  }
}

async function lookupCalculator(query) {
  const ensured = await _ensureTab("https://calculator.ozon.ru/");
  const tab = ensured.tab;
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

async function fetchBestsellers({ period = "monthly", limit = 50, offset = 0, search = "", companyId = "", autoOpen = false, prepareUi = true }) {
  // Делаем fetch из контекста вкладки seller.ozon.ru (через chrome.scripting.executeScript),
  // чтобы куки гарантированно ушли как first-party.
  // Выбираем вкладку seller.ozon.ru, которая НЕ на странице авторизации.
  const expectedSellerUrl = "https://seller.ozon.ru/app/analytics/what-to-sell/ozon-bestsellers";
  const expectedSellerPath = "/app/analytics/what-to-sell/ozon-bestsellers";
  const allTabs = await chrome.tabs.query({ url: "https://seller.ozon.ru/*" });
  let validTabs = allTabs.filter(t => !/\/(signin|registration)/.test(t.url || "") && (t.url || "").includes(expectedSellerPath));
  const debug = {
    requested_url: expectedSellerUrl,
    auto_open: !!autoOpen,
    tab_found_before_open: validTabs.length > 0,
    tab_opened: false,
    ready_confirmed: false,
    input_ready_confirmed: false,
    period_28_confirmed: false,
    category_reset_confirmed: false,
    category_reset_clicked: false,
    ui_trace: [],
    final_url: validTabs[0]?.url || "",
    final_status: validTabs[0]?.status || "",
  };
  if (!validTabs.length) {
    if (!autoOpen) {
      // Машиночитаемый код — дашборд распознаёт и спрашивает пользователя
      throw new Error("NO_SELLER_TAB");
    }
    // Автооткрытие вкладки в фоне (по аналогии с _ensureTab для calculator.ozon.ru)
    const opened = await _ensureTab(expectedSellerUrl);
    debug.tab_opened = true;
    debug.final_url = opened.final_url || "";
    debug.final_status = opened.status || "";
    // Ждём окончания всех редиректов и появления валидной (НЕ-signin) страницы.
    const ready = await _waitForSellerReady(opened.tab.id, 30000);
    if (!ready) {
      const cur = await chrome.tabs.get(opened.tab.id).catch(() => null);
      debug.final_url = cur?.url || debug.final_url;
      debug.final_status = cur?.status || debug.final_status;
      if (cur && /\/(signin|registration)/.test(cur.url || "")) {
        throw new Error("Вкладка seller.ozon.ru открыта, но требуется вход — авторизуйтесь");
      }
      throw new Error("Не удалось дождаться загрузки seller.ozon.ru (timeout)");
    }
    debug.ready_confirmed = true;
    debug.final_url = ready.url || debug.final_url;
    debug.final_status = ready.status || debug.final_status;
    const bestsellersReady = await _waitForSellerBestsellersReady(opened.tab.id, 20000);
    if (bestsellersReady) {
      debug.final_url = bestsellersReady.url || debug.final_url;
      debug.final_status = bestsellersReady.status || debug.final_status;
      validTabs = [bestsellersReady];
    } else {
      validTabs = [ready];
    }
  }
  const tab = validTabs[0];
  debug.final_url = tab.url || debug.final_url;
  debug.final_status = tab.status || debug.final_status;
  const body = {
    limit: String(limit), offset: String(Math.max(0, Number(offset) || 0)),
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
        const cookieCompanyId = (document.cookie.match(/(?:^|; )sc_company_id=([^;]+)/) || [])[1] || "";
        const resolvedCompanyId = companyId || cookieCompanyId || "";
        const headers = {
          "Accept": "application/json, text/plain, */*",
          "Content-Type": "application/json",
          "x-o3-app-name": "seller-ui",
          "x-o3-language": "ru",
          "x-o3-page-type": "analytics_platform",
        };
        if (resolvedCompanyId) headers["x-o3-company-id"] = resolvedCompanyId;
        const r = await fetch(
          "/api/site/seller-analytics/what_to_sell/data/v3",
          { method: "POST", headers, credentials: "include", body: bodyJson }
        );
        const text = await r.text();
        let data = null;
        try { data = JSON.parse(text); } catch (e) {}
        return { status: r.status, data, text_preview: data ? null : text.slice(0, 200), cid_used: resolvedCompanyId || "(empty)" };
      },
      args: [cid || "", JSON.stringify(body)],
    });
    return result && result.result;
  };

  // Полинг до 45 сек: каждые 1.5 сек заново резолвим cid и пробуем fetch.
  // Останавливаемся на любом ответе кроме 401/403/0. Это гораздо надёжнее, чем
  // угадывать момент готовности — Ozon SPA может ставить куки/CSRF несколькими волнами.
  const finalizeSuccess = (res, mode) => {
    const raw = res.data || {};
    debug.request_mode = mode;
    return { items: raw.items || raw.data || [], debug };
  };
  let res = await _doFetch();
  if (res && res.status === 200) {
    return finalizeSuccess(res, "direct-fetch");
  }
  if (prepareUi) {
    const prepared = await _prepareSellerBestsellersPage(tab.id, period, search, 25000);
    debug.input_ready_confirmed = !!prepared.input_ready_confirmed;
    debug.period_28_confirmed = !!prepared.period_28_confirmed;
    debug.category_reset_confirmed = !!prepared.category_reset_confirmed;
    debug.category_reset_clicked = !!prepared.category_reset_clicked;
    debug.ui_trace = Array.isArray(prepared.ui_trace) ? prepared.ui_trace : [];
    debug.final_url = prepared.final_url || debug.final_url;
    if (!debug.input_ready_confirmed || !debug.period_28_confirmed || !debug.category_reset_confirmed) {
      throw new Error(
        `SELLER_UI_NOT_READY: input=${debug.input_ready_confirmed ? "yes" : "no"}, period28=${debug.period_28_confirmed ? "yes" : "no"}, categoryReset=${debug.category_reset_confirmed ? "yes" : "no"}`
      );
    }
    res = await _doFetch();
    if (res && res.status === 200) {
      return finalizeSuccess(res, "after-ui-prepare");
    }
  }
  const overallDeadline = Date.now() + 45000;
  let attempts = 1;
  while (Date.now() < overallDeadline && res && (res.status === 400 || res.status === 401 || res.status === 403 || res.status === 0)) {
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
  return finalizeSuccess(res, prepareUi ? "after-ui-poll" : "direct-fetch-poll");
}

function _toNumberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const normalized = String(value).replace(/\s+/g, "").replace(",", ".");
  const n = Number(normalized);
  return Number.isFinite(n) ? n : null;
}

function _toIntOrNull(value) {
  const n = _toNumberOrNull(value);
  return n === null ? null : Math.round(n);
}

function _toPercentFraction(value) {
  const n = _toNumberOrNull(value);
  if (n === null) return null;
  return n > 1 ? n / 100 : n;
}

function normalizeBestsellerItem(item) {
  const periodDays = _toIntOrNull(item.accessibilityByDays);
  const daysInStock = _toIntOrNull(item.daysInStock);
  const daysWithoutStock = periodDays !== null && daysInStock !== null
    ? Math.max(0, periodDays - daysInStock)
    : _toIntOrNull(item.daysWithoutStock || item.days_without_stock);
  return {
    sku: String(item.sku || item.variantId || item.variant_id || ""),
    name: item.name || item.skuName || item.title || null,
    brand: item.brand || null,
    category1: item.category1 || item.category_level_1 || null,
    category3: item.category3 || item.category_level_3 || null,
    sold_sum: _toNumberOrNull(item.soldSum ?? item.sold_sum ?? item.gmvSum ?? item.sumGmv ?? item.sum_gmv),
    sold_units: _toIntOrNull(item.soldCount ?? item.sold_count ?? item.orderedUnits ?? item.ordered_units),
    avg_price: _toNumberOrNull(item.avgPrice ?? item.avg_price ?? item.avgGmv),
    min_price: _toNumberOrNull(item.minSellerPrice ?? item.minPrice ?? item.min_price),
    session_count: _toIntOrNull(item.sessionCount ?? item.session_count ?? item.qtyViewPdp),
    conv_to_cart: _toPercentFraction(item.convToCart ?? item.conv_to_cart),
    buyout_rate: _toPercentFraction(item.nullableRedemptionRate ?? item.buyoutRate ?? item.buyout_rate),
    lost_sales: _toNumberOrNull(item.sumMissedGmv ?? item.lostSales ?? item.lost_sales),
    days_without_stock: daysWithoutStock,
    daily_sales: _toNumberOrNull(item.avgOrdersOnAccDays ?? item.dailySales ?? item.daily_sales ?? item.avgDailySales),
    search_position: _toIntOrNull(item.localIndex ?? item.searchPosition ?? item.search_position ?? item.position),
    dynamic_pct: _toNumberOrNull(item.salesDynamics ?? item.dynamic ?? item.dynamicPct),
    photo_url: item.photo || item.photoUrl || item.image || null,
    product_url: item.link || item.productUrl || item.url || null,
    stock_end: _toIntOrNull(item.stock ?? item.stockOnEnd ?? item.balance ?? item.availableStock),
    views: _toIntOrNull(item.views ?? item.viewCount ?? item.qtyViewAll),
    session_count_search: _toIntOrNull(item.sessionCountSearch ?? item.session_count_search ?? item.qtyViewSearch),
    qty_view_pdp: _toIntOrNull(item.qtyViewPdp ?? item.qty_view_pdp),
    conv_view_to_order: _toPercentFraction(item.convViewToOrder ?? item.conv_view_to_order),
    conv_to_cart_search: _toPercentFraction(item.convToCartSearch ?? item.conv_to_cart_search),
    conv_to_cart_pdp: _toPercentFraction(item.convToCartPdp ?? item.conv_to_cart_pdp),
    promo_revenue_share: _toNumberOrNull(item.promoRevenueShare ?? item.promo_revenue_share),
    days_in_promo: _toIntOrNull(item.daysInPromo ?? item.days_in_promo),
    days_with_trafarets: _toIntOrNull(item.daysWithTrafarets ?? item.days_with_trafarets),
    drr: _toNumberOrNull(item.drr ?? item.DRR),
    avg_delivery_days: _toNumberOrNull(item.avgDeliveryDays ?? item.avg_delivery_days),
    volume_l: _toNumberOrNull(item.volumeL ?? item.volume_l),
    raw: item || null,
  };
}

// ─── SERP scraping ──────────────────────────────────────────────────────────

async function scrapeSerpPageOld({ query_text, limit = 20 }) {
  if (!query_text || !query_text.trim()) throw new Error("query_text is required");
  const url = "https://www.ozon.ru/search/?text=" + encodeURIComponent(query_text.trim()) + "&sorting=score";

  // Используем или открываем вкладку в текущем профиле (НЕ новый профиль)
  const ensured = await _ensureTab("https://www.ozon.ru/search/");
  const tab = ensured.tab;

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


async function scrapeSerpPage({ query_text, limit = 30 }) {
  if (!query_text || !query_text.trim()) throw new Error("query_text is required");
  const query = query_text.trim();
  const safeLimit = Math.max(1, Math.min(Number(limit) || 30, 300));
  const maxPages = Math.max(1, Math.ceil(safeLimit / 36) + 1);
  const ensured = await _ensureTab("https://www.ozon.ru/search/");
  const tab = ensured.tab;
  const positions = [];
  const seen = new Set();
  const debug = {
    requested_base_url: "https://www.ozon.ru/search/",
    requested_query: query,
    tab_found_before_open: !ensured.created,
    tab_opened: true,
    initial_url: ensured.final_url || "",
    initial_status: ensured.status || "",
    url_open_confirmed: false,
    cards_ready_confirmed: false,
    pages: [],
    selector: ".tile-root / .tile-clickable-element[data-prerender]",
  };

  for (let page = 1; page <= maxPages && positions.length < safeLimit; page++) {
    const url = new URL("https://www.ozon.ru/search/");
    url.searchParams.set("text", query);
    url.searchParams.set("sorting", "score");
    if (page > 1) url.searchParams.set("page", String(page));

    await chrome.tabs.update(tab.id, { url: url.toString(), active: false });
    await new Promise((r) => setTimeout(r, 1800));
    const afterNav = await chrome.tabs.get(tab.id).catch(() => null);
    if ((afterNav?.url || "").includes("ozon.ru/search")) {
      debug.url_open_confirmed = true;
    }

    const ready = await _waitForSerpCards(tab.id, 18000);
    if ((ready.card_count || 0) > 0 || (ready.link_count || 0) > 0) {
      debug.cards_ready_confirmed = true;
    }
    if (!ready.card_count && !ready.link_count) {
      debug.pages.push({ page, url: url.toString(), ...ready, extracted: 0, added: 0 });
      if (page === 1) throw new Error("Не удалось дождаться карточек выдачи Ozon");
      break;
    }

    await _scrollSerpPage(tab.id, safeLimit - positions.length);
    const pageItems = await _extractSerpCards(tab.id);
    let added = 0;
    for (const item of pageItems) {
      if (!item.sku || seen.has(String(item.sku))) continue;
      seen.add(String(item.sku));
      positions.push({
        ...item,
        position: positions.length + 1,
        page,
        query_text: query,
      });
      added++;
      if (positions.length >= safeLimit) break;
    }

    debug.pages.push({ page, url: url.toString(), ...ready, extracted: pageItems.length, added });
    if (!pageItems.length || added === 0) break;
  }

  if (!positions.length) throw new Error("Не удалось собрать карточки из выдачи ozon.ru");
  return { positions, query_text: query, debug };
}

async function _waitForSerpCards(tabId, maxMs = 15000) {
  const deadline = Date.now() + maxMs;
  let last = { card_count: 0, link_count: 0, title: "", url: "" };
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 600));
    const [check] = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: () => ({
        card_count: document.querySelectorAll(".tile-root, .tile-clickable-element[data-prerender]").length,
        link_count: document.querySelectorAll('a[href*="/product/"]').length,
        title: document.title || "",
        url: location.href,
      }),
    });
    last = (check && check.result) || last;
    if (last.card_count >= 4 || last.link_count >= 4) break;
  }
  return last;
}

async function _scrollSerpPage(tabId, remaining) {
  const steps = Math.max(3, Math.min(12, Math.ceil((Number(remaining) || 36) / 10)));
  for (let i = 0; i < steps; i++) {
    await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: (step, total) => {
        const height = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
        window.scrollTo(0, Math.floor(height * ((step + 1) / total)));
      },
      args: [i, steps],
    });
    await new Promise((r) => setTimeout(r, 450));
  }
}

async function _extractSerpCards(tabId) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: () => {
      function textOf(el) {
        return (el?.innerText || el?.textContent || "").trim();
      }
      function parseSku(href) {
        const raw = href || "";
        const m = raw.match(/\/product\/[^/?#]*?-(\d{6,12})(?:[/?#]|$)/) || raw.match(/\/product\/(\d{6,12})(?:[/?#]|$)/);
        return m ? m[1] : null;
      }
      function parseNumber(text) {
        if (!text) return null;
        const cleaned = String(text).replace(/\s+/g, "").replace(",", ".");
        const m = cleaned.match(/\d+(?:\.\d+)?/);
        return m ? Number(m[0]) : null;
      }
      function parseIntText(text) {
        if (!text) return null;
        const m = String(text).replace(/\s+/g, "").match(/\d+/);
        return m ? Number(m[0]) : null;
      }
      function absoluteUrl(href) {
        try { return new URL(href, location.origin).toString().split("?")[0]; }
        catch (e) { return href || ""; }
      }
      function getProductLink(card) {
        return card.querySelector(".tile-clickable-element[data-prerender][href*='/product/']")
          || card.querySelector("a.tile-clickable-element[href*='/product/']")
          || card.querySelector("a[href*='/product/']");
      }
      function getTitle(card) {
        return textOf(card.querySelector("span.tsBody500Medium"))
          || textOf(card.querySelector("a[href*='/product/'] span"))
          || textOf(card.querySelector("h3, h2"))
          || "";
      }
      function hasCurrency(text) {
        return /[\u20bd]|(?:\u0440\u0443\u0431)/i.test(text);
      }
      function getCurrentPrice(card) {
        const exact = textOf(card.querySelector("span.tsHeadline500Medium"));
        if (exact) return exact;
        return [...card.querySelectorAll("span")].map(textOf).find((t) => hasCurrency(t) && /\d/.test(t)) || "";
      }
      function getOldPrice(card, currentPriceText) {
        const spans = [...card.querySelectorAll("span")];
        for (const sp of spans) {
          const t = textOf(sp);
          const cls = sp.className || "";
          const style = getComputedStyle(sp);
          const isCurrency = hasCurrency(t) && /\d/.test(t);
          const isOld = style.textDecoration.includes("line-through")
            || (cls.includes("tsBodyControl400Small") && cls.includes("-b") && !cls.includes("b4"))
            || sp.closest("[class*='old'],[class*='cross'],[class*='before']");
          if (isCurrency && isOld && t !== currentPriceText) return t;
        }
        return "";
      }
      function getRatingAndReviews(card) {
        const small = [...card.querySelectorAll("span.tsBodyControl300XSmall, .tsBodyControl300XSmall")].map(textOf).filter(Boolean);
        let rating = "";
        let reviews = "";
        for (const t of small) {
          if (!rating && /^\d(?:[.,]\d)?$/.test(t)) rating = t;
          if (!reviews && /\d/.test(t) && !/^\d(?:[.,]\d)?$/.test(t)) reviews = t;
        }
        const reviewExact = textOf(card.querySelector("span.c7w1_6_1-a0.tsBodyControl300XSmall"));
        if (reviewExact && /\d/.test(reviewExact)) reviews = reviewExact;
        return { rating, reviews };
      }
      function getBrand(card) {
        const exact = textOf(card.querySelector("span.c7w1_6_1-a0.tsBodyControl400Small"));
        if (exact && !/\d|\u043e\u0442\u0437\u044b\u0432|[\u20bd]|\u0440\u0443\u0431|\u043e\u0441\u0442\u0430\u043b/i.test(exact)) return exact;
        const brand = textOf(card.querySelector("[class*='brand']"));
        return brand || "";
      }
      function getPromo(card) {
        const values = [];
        for (const el of card.querySelectorAll(".b5_6_5-a4, [class*='badge'], [class*='promo'], [class*='label']")) {
          const t = textOf(el);
          if (t && t.length <= 120 && !values.includes(t)) values.push(t);
        }
        for (const el of card.querySelectorAll("div[title], span[title]")) {
          const t = (el.getAttribute("title") || "").trim();
          if (t && t.length <= 120 && !values.includes(t)) values.push(t);
        }
        return values.join(" | ");
      }
      function getStock(card) {
        const t = [...card.querySelectorAll("span, div")].map(textOf).find((x) => /\d+\s*\u0448\u0442\.?\s*\u043e\u0441\u0442\u0430\u043b/i.test(x));
        return t || "";
      }
      function getDeliveryText(card) {
        const deliveryPattern = /(\u0434\u043e\u0441\u0442\u0430\u0432|\u043f\u043e\u043b\u0443\u0447|\u0441\u0435\u0433\u043e\u0434\u043d\u044f|\u0437\u0430\u0432\u0442\u0440\u0430|\u043f\u043e\u0441\u043b\u0435\u0437\u0430\u0432\u0442\u0440\u0430|\d{1,2}\s*(?:\u044f\u043d\u0432|\u0444\u0435\u0432|\u043c\u0430\u0440|\u0430\u043f\u0440|\u043c\u0430\u044f|\u0438\u044e\u043d|\u0438\u044e\u043b|\u0430\u0432\u0433|\u0441\u0435\u043d|\u043e\u043a\u0442|\u043d\u043e\u044f|\u0434\u0435\u043a))/i;
        const rejectPattern = /(\u043e\u0442\u0437\u044b\u0432|[\u20bd]|\u0440\u0443\u0431|\u0448\u0442\.?\s*\u043e\u0441\u0442\u0430\u043b|\u0441\u043a\u0438\u0434\u043a|\u0440\u0435\u0439\u0442\u0438\u043d\u0433)/i;
        const values = [...card.querySelectorAll("span, div")]
          .map(textOf)
          .map((t) => t.replace(/\s+/g, " ").trim())
          .filter((t) => t && t.length <= 120 && deliveryPattern.test(t) && !rejectPattern.test(t));
        values.sort((a, b) => {
          const aDelivery = /\u0434\u043e\u0441\u0442\u0430\u0432/i.test(a) ? 0 : 1;
          const bDelivery = /\u0434\u043e\u0441\u0442\u0430\u0432/i.test(b) ? 0 : 1;
          return aDelivery - bDelivery || a.length - b.length;
        });
        return values[0] || "";
      }

      const rootCards = [...document.querySelectorAll(".tile-root")];
      const cards = rootCards.length
        ? rootCards
        : [...document.querySelectorAll(".tile-clickable-element[data-prerender]")]
            .map((a) => a.closest("[class*='tile'], article, li, div"))
            .filter(Boolean);
      const result = [];
      const seen = new Set();

      for (const card of cards) {
        const link = getProductLink(card);
        const href = link?.href || link?.getAttribute("href") || "";
        const sku = parseSku(href);
        if (!sku || seen.has(sku)) continue;
        seen.add(sku);

        const priceText = getCurrentPrice(card);
        const oldPriceText = getOldPrice(card, priceText);
        const { rating, reviews } = getRatingAndReviews(card);
        const stockText = getStock(card);
        const deliveryText = getDeliveryText(card);
        const img = card.querySelector("img");

        result.push({
          sku,
          title: getTitle(card).slice(0, 500),
          brand: getBrand(card).slice(0, 255) || null,
          price: parseNumber(priceText),
          price_before: parseNumber(oldPriceText),
          rating: parseNumber(rating),
          review_count: parseIntText(reviews),
          stock: parseIntText(stockText),
          promo_label: getPromo(card).slice(0, 100) || null,
          delivery_text: deliveryText.slice(0, 120) || null,
          thumbnail_url: img?.currentSrc || img?.src || img?.dataset?.src || null,
          product_url: absoluteUrl(href),
          is_advert: /advert/i.test(href) || /\u0440\u0435\u043a\u043b\u0430\u043c\u0430/i.test(textOf(card)),
          raw: {
            price_text: priceText,
            old_price_text: oldPriceText,
            rating_text: rating,
            reviews_text: reviews,
            stock_text: stockText,
            delivery_text: deliveryText,
          },
        });
      }
      return result;
    },
  });
  return (result && result.result) || [];
}

async function enrichWithBestsellers(skus) {
  // Обогащает SKU полным набором метрик из seller.ozon.ru/ozon-bestsellers.
  // Возвращает объект: sku (string) → { revenue_30d, sales_per_day, bestsellers_data }
  const entries = (skus || []).map((entry) => {
    if (typeof entry === "object" && entry) {
      return {
        sku: String(entry.sku || "").trim(),
        title: String(entry.title || "").trim(),
      };
    }
    return { sku: String(entry || "").trim(), title: "" };
  }).filter((entry) => entry.sku);
  if (!entries.length) return {};
  const pending = new Set(entries.map((entry) => entry.sku));
  const entryBySku = new Map(entries.map((entry) => [entry.sku, entry]));
  const result = {};
  const stats = {
    requested_count: entries.length,
    mass_pages_scanned: 0,
    mass_matches: 0,
    fallback_attempts: 0,
    fallback_matches: 0,
    unresolved_skus: [],
    seller_open_requested: false,
    seller_open_confirmed: false,
    seller_final_url: "",
    input_ready_confirmed: false,
    period_28_confirmed: false,
    category_reset_confirmed: false,
    category_reset_clicked: false,
    last_error: "",
    debug_samples: [],
  };
  if (!pending.size) return { matches: result, stats };

  // 100 элементов дают слишком тяжелый ответ для bridge/dashboard и вызов
  // начинает зависать еще до сопоставления SKU. 50 проходят стабильно.
  const pageLimit = 50;
  const maxPages = 3;
  let bootstrapResp = null;

  // Контрольная точка: без готовой seller-вкладки сбор bestsellers не должен
  // продолжаться "успешно". Здесь допускаем автооткрытие и пробрасываем ошибку
  // наверх, чтобы дашборд остановил прогресс с понятным сообщением.
  try {
    bootstrapResp = await fetchBestsellers({
      limit: pageLimit,
      offset: 0,
      autoOpen: true,
      prepareUi: true,
    });
    stats.seller_open_requested = true;
    stats.seller_open_confirmed = !!bootstrapResp?.debug?.ready_confirmed || !!bootstrapResp?.debug?.tab_found_before_open;
    stats.seller_final_url = bootstrapResp?.debug?.final_url || "";
    stats.input_ready_confirmed = !!bootstrapResp?.debug?.input_ready_confirmed;
    stats.period_28_confirmed = !!bootstrapResp?.debug?.period_28_confirmed;
    stats.category_reset_confirmed = !!bootstrapResp?.debug?.category_reset_confirmed;
    stats.category_reset_clicked = !!bootstrapResp?.debug?.category_reset_clicked;
  } catch (e) {
    const message = String(e && e.message || e || "");
    stats.seller_open_requested = true;
    stats.last_error = message;
    if (stats.debug_samples.length < 8) {
      stats.debug_samples.push({
        phase: "bootstrap_error",
        error: message.slice(0, 240),
      });
    }
    if (/NO_SELLER_TAB|signin|авториз|timeout|открыть|загрузки seller/i.test(message)) {
      throw e;
    }
  }

  for (let page = 0; page < maxPages && pending.size && bootstrapResp; page++) {
    let resp;
    try {
      resp = page === 0
        ? bootstrapResp
        : await fetchBestsellers({
            limit: pageLimit,
            offset: page * pageLimit,
            prepareUi: false,
          });
    } catch (e) {
      stats.last_error = String(e && e.message || e || "");
      if (stats.debug_samples.length < 8) {
        stats.debug_samples.push({
          phase: "mass_error",
          page,
          error: stats.last_error.slice(0, 240),
        });
      }
      break;
    }
    const items = resp.items || [];
    stats.mass_pages_scanned += 1;
    if (stats.debug_samples.length < 8) {
      stats.debug_samples.push({
        phase: "mass",
        page,
        items_count: items.length,
        seller_final_url: resp?.debug?.final_url || "",
      });
    }
    if (!items.length) break;
    for (const item of items) {
      const sku = String(item.sku || item.id || item.item_id || "").trim();
      if (!sku || !pending.has(sku)) continue;
      const normalized = normalizeBestsellerItem(item);
      result[sku] = {
        revenue_30d: normalized.sold_sum,
        sales_per_day: normalized.daily_sales,
        bestsellers_data: normalized,
      };
      pending.delete(sku);
      stats.mass_matches += 1;
    }
  }

  function normalizeKey(v) {
    return String(v || "")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function keyTokens(v) {
    const stop = new Set([
      "для", "стола", "подстолье", "ножки", "опора", "опоры", "рама",
      "металлические", "металлическое", "металлическая", "черный", "черные",
      "белый", "белые", "цвет", "комплекте", "штуки", "штук", "шт", "лофт",
      "style", "loft", "в", "и", "из", "по", "на", "с", "под"
    ]);
    return normalizeKey(v)
      .split(" ")
      .filter((token) => token && token.length >= 3 && !stop.has(token));
  }

  function pickBestsellerItem(items, target) {
    const list = Array.isArray(items) ? items : [];
    const targetSku = String(target && target.sku || "").trim();
    if (targetSku) {
      const bySku = list.find((item) => {
        const rawSku = item && (item.sku || item.variantId || item.variant_id || item.id || item.item_id);
        return String(rawSku || "").trim() === targetSku;
      });
      if (bySku) return bySku;
    }
    const titleNorm = normalizeKey(target && target.title);
    const byText = list.find((item) => {
      const itemName = normalizeKey(item && (item.name || item.skuName || item.title));
      return titleNorm && itemName && itemName.includes(titleNorm);
    });
    if (byText) return byText;
    const targetTokens = keyTokens(target && target.title);
    if (targetTokens.length) {
      let best = null;
      let bestScore = 0;
      let secondScore = 0;
      for (const item of list) {
        const itemName = item && (item.name || item.skuName || item.title);
        const itemTokens = new Set(keyTokens(itemName));
        if (!itemTokens.size) continue;
        let overlap = 0;
        for (const token of targetTokens) {
          if (itemTokens.has(token)) overlap += 1;
        }
        const score = overlap / targetTokens.length;
        if (score > bestScore) {
          secondScore = bestScore;
          bestScore = score;
          best = item;
        } else if (score > secondScore) {
          secondScore = score;
        }
      }
      if (best && (bestScore >= 0.45 || (bestScore >= 0.34 && bestScore - secondScore >= 0.15))) {
        return best;
      }
    }
    return list.length === 1 ? list[0] : null;
  }

  const fallbackEntries = [...pending].map((sku) => entryBySku.get(sku) || { sku, title: "" });
  for (const entry of fallbackEntries) {
    const sku = entry.sku;
    try {
      const searchVariants = [String(sku || "").trim(), String(entry.title || "").trim()].filter(Boolean);
      let matched = null;
      for (const searchKey of searchVariants) {
        stats.fallback_attempts += 1;
        const resp = await fetchBestsellers({ search: searchKey, limit: 50, autoOpen: true, prepareUi: false });
        if (stats.debug_samples.length < 8) {
          stats.debug_samples.push({
            phase: "fallback",
            sku,
            searchKey: searchKey.slice(0, 120),
            items_count: (resp.items || []).length,
            seller_final_url: resp?.debug?.final_url || "",
          });
        }
        matched = pickBestsellerItem(resp.items || [], entry);
        if (matched) break;
      }
      if (!matched) continue;
      const normalized = normalizeBestsellerItem(matched);
      result[sku] = {
        revenue_30d: normalized.sold_sum,
        sales_per_day: normalized.daily_sales,
        bestsellers_data: normalized,
      };
      pending.delete(sku);
      stats.fallback_matches += 1;
    } catch (e) {
      stats.last_error = String(e && e.message || e || "");
      if (stats.debug_samples.length < 8) {
        stats.debug_samples.push({
          phase: "fallback_error",
          sku,
          error: stats.last_error.slice(0, 240),
        });
      }
      // fallback по названию не удался
    }
  }
  stats.unresolved_skus = [...pending];
  return { matches: result, stats };
}

// ─── Message listener ───────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.action === "lookup_calculator") {
        const data = await lookupCalculator(msg.query);
        sendResponse({ ok: true, data });
      } else if (msg.action === "fetch_bestsellers") {
        const data = await fetchBestsellers(msg.options || msg.payload || msg || {});
        sendResponse({ ok: true, data });
      } else if (msg.action === "scrape_serp") {
        const data = await scrapeSerpPage(msg.options || msg.payload || msg || {});
        sendResponse({ ok: true, data });
      } else if (msg.action === "enrich_with_bestsellers") {
        const data = await enrichWithBestsellers(msg.skus || msg.payload?.skus || []);
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
