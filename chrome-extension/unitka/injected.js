"use strict";
// Этот скрипт выполняется в контексте страницы (world: MAIN),
// поэтому устанавливает флаг прямо в window дашборда.
window.__ozonUnitkaExt = { ready: true, version: "0.1.0" };
window.dispatchEvent(new CustomEvent("ozon-unitka:ready"));
