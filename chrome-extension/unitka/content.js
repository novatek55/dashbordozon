"use strict";
// Isolated-world bridge: listens for page events and forwards them to the
// extension background worker.

window.addEventListener("ozon-unitka:request", (e) => {
  const { requestId, action, payload } = e.detail || {};
  if (!requestId || !action) return;

  if (!chrome?.runtime?.sendMessage) {
    window.dispatchEvent(new CustomEvent("ozon-unitka:response", {
      detail: {
        requestId,
        response: {
          ok: false,
          error: "Extension bridge is unavailable: chrome.runtime.sendMessage is missing",
        },
      },
    }));
    return;
  }

  try {
    chrome.runtime.sendMessage({ action, ...(payload || {}) }, (resp) => {
      const err = chrome.runtime.lastError;
      const response = err
        ? { ok: false, error: err.message }
        : (resp || { ok: false, error: "no response" });
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
