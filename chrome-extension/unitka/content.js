"use strict";
// Isolated-world bridge: слушает window-события страницы и пересылает в фон.
// Также отвечает на "ping" напрямую (быстрая проверка наличия расширения).

window.addEventListener("ozon-unitka:request", (e) => {
  const { requestId, action, payload } = e.detail || {};
  if (!requestId || !action) return;

  // Быстрый отклик для ping — не нужно дёргать background
  if (action === "ping") {
    window.dispatchEvent(new CustomEvent("ozon-unitka:response", {
      detail: { requestId, response: { ok: true, version: "0.1.0" } },
    }));
    return;
  }

  try {
    chrome.runtime.sendMessage({ action, ...(payload || {}) }, (resp) => {
      const err = chrome.runtime.lastError;
      const response = err ? { ok: false, error: err.message } : (resp || { ok: false, error: "no response" });
      window.dispatchEvent(new CustomEvent("ozon-unitka:response", {
        detail: { requestId, response },
      }));
    });
  } catch (err) {
    window.dispatchEvent(new CustomEvent("ozon-unitka:response", {
      detail: { requestId, response: { ok: false, error: String(err) } },
    }));
  }
});
