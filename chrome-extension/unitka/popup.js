"use strict";

const els = {
  calcQuery: document.getElementById("calcQuery"),
  calcBtn: document.getElementById("calcBtn"),
  bestPeriod: document.getElementById("bestPeriod"),
  bestLimit: document.getElementById("bestLimit"),
  bestSearch: document.getElementById("bestSearch"),
  bestBtn: document.getElementById("bestBtn"),
  status: document.getElementById("status"),
  dashboardUrl: document.getElementById("dashboardUrl"),
  companyId: document.getElementById("companyId"),
};

// ─── storage ───────────────────────────────────────────
(async function loadSettings() {
  const { dashboardUrl, companyId } = await chrome.storage.sync.get(["dashboardUrl", "companyId"]);
  if (dashboardUrl) els.dashboardUrl.value = dashboardUrl;
  if (companyId) els.companyId.value = companyId;
})();

[els.dashboardUrl, els.companyId].forEach((el) => {
  el.addEventListener("change", () => {
    chrome.storage.sync.set({
      dashboardUrl: els.dashboardUrl.value.trim(),
      companyId: els.companyId.value.trim(),
    });
  });
});

// ─── helpers ───────────────────────────────────────────
function setStatus(text, kind = "info") {
  els.status.textContent = text;
  els.status.className = kind;
}

function postDashboard(path, body) {
  const base = (els.dashboardUrl.value || "http://127.0.0.1:8088").replace(/\/+$/, "");
  return fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(async (r) => {
    const text = await r.text();
    let data = null;
    try { data = JSON.parse(text); } catch (e) {}
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  });
}

// ─── Calculator button ─────────────────────────────────
els.calcBtn.addEventListener("click", async () => {
  const query = els.calcQuery.value.trim();
  if (!query) { setStatus("Введите URL или SKU", "err"); return; }

  els.calcBtn.disabled = true;
  setStatus("Запрос к calculator.ozon.ru через фон-воркер…", "info");

  try {
    const resp = await chrome.runtime.sendMessage({ action: "lookup_calculator", query });
    if (!resp || !resp.ok) throw new Error((resp && resp.error) || "нет ответа от фона");
    const items = resp.data.items || [];
    if (!items.length) throw new Error("Ничего не найдено");

    const save = await postDashboard("/api/unitka/import/competitor", { items });
    setStatus(`✓ Сохранено: ${save.count} шт. → БД + Юнитка`, "ok");
  } catch (e) {
    setStatus(`Ошибка: ${e.message}`, "err");
  } finally {
    els.calcBtn.disabled = false;
  }
});

// ─── Bestsellers button ────────────────────────────────
els.bestBtn.addEventListener("click", async () => {
  const period = els.bestPeriod.value;
  const limit = parseInt(els.bestLimit.value) || 50;
  const search = els.bestSearch.value.trim();
  const companyId = els.companyId.value.trim();

  els.bestBtn.disabled = true;
  setStatus("Запрос к seller.ozon.ru через фон-воркер…", "info");

  try {
    // Используем background для fetch через вкладку seller.ozon.ru (first-party cookies)
    const resp = await chrome.runtime.sendMessage({
      action: "fetch_bestsellers",
      options: { period, limit, search, companyId },
    });
    if (!resp || !resp.ok) throw new Error((resp && resp.error) || "нет ответа от фона");
    const items = resp.data.items || [];
    if (!items.length) { setStatus("Список пуст", "err"); return; }

    const save = await postDashboard("/api/unitka/import/bestsellers", { items, period });
    setStatus(`✓ Сохранено: ${save.inserted} строк (${period}) → БД`, "ok");
  } catch (e) {
    setStatus(`Ошибка: ${e.message}`, "err");
  } finally {
    els.bestBtn.disabled = false;
  }
});
