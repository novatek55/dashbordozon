const fs = require("fs");
const path = require("path");
const vm = require("vm");
const test = require("node:test");
const assert = require("node:assert/strict");

function loadUiHelpers() {
  const file = path.resolve(__dirname, "background.js");
  const source = fs.readFileSync(file, "utf8");
  const start = source.indexOf("// SELLER_UI_HELPERS_START");
  const end = source.indexOf("// SELLER_UI_HELPERS_END");
  if (start === -1 || end === -1 || end <= start) {
    throw new Error("Seller UI helper block not found in background.js");
  }

  const snippet = source.slice(start, end);
  const context = { module: { exports: {} }, exports: {} };
  vm.runInNewContext(
    `${snippet}
module.exports = {
  _normalizeSellerUiText,
  _sellerPlaceholderType,
  _pickSellerProductInput,
};`,
    context,
    { filename: "background.js" }
  );
  return context.module.exports;
}

test("bestsellers helpers recognize the real seller placeholders", () => {
  const { _sellerPlaceholderType } = loadUiHelpers();

  assert.equal(_sellerPlaceholderType("Поиск"), "global-search");
  assert.equal(_sellerPlaceholderType("Название товара"), "product-name");
});

test("bestsellers helpers prefer the product-name filter over the global search", () => {
  const { _pickSellerProductInput } = loadUiHelpers();

  const picked = _pickSellerProductInput([
    { placeholder: "Поиск", visible: true, rect: { x: 686, y: 18, width: 204, height: 20 } },
    { placeholder: "Название товара", visible: true, rect: { x: 520, y: 358, width: 250, height: 20 } },
  ]);

  assert.equal(picked?.placeholder, "Название товара");
});
