const fs = require("fs");
const path = require("path");
const vm = require("vm");
const test = require("node:test");
const assert = require("node:assert/strict");

function loadOfferFilterHelpers() {
  const file = path.resolve(__dirname, "web", "orders_dashboard.html");
  const source = fs.readFileSync(file, "utf8");
  const start = source.indexOf("// OFFER_FILTER_HELPERS_START");
  const end = source.indexOf("// OFFER_FILTER_HELPERS_END");
  if (start === -1 || end === -1 || end <= start) {
    throw new Error("Offer filter helper block not found in orders_dashboard.html");
  }

  const snippet = source.slice(start, end);
  const context = { module: { exports: {} }, exports: {} };
  vm.runInNewContext(
    `${snippet}
module.exports = {
  dashboardOfferFilterShouldReloadOnInput,
  dashboardArticleListSource,
};`,
    context,
    { filename: "orders_dashboard.html" }
  );
  return context.module.exports;
}

test("Offer ID clear reloads all reports that apply the global article filter", () => {
  const { dashboardOfferFilterShouldReloadOnInput } = loadOfferFilterHelpers();

  assert.equal(dashboardOfferFilterShouldReloadOnInput("stock_balances", ""), true);
  assert.equal(dashboardOfferFilterShouldReloadOnInput("accruals_comp_by_article", "   "), true);
  assert.equal(dashboardOfferFilterShouldReloadOnInput("advertising_report", ""), true);
});

test("Offer ID typing only live-reloads advertising report", () => {
  const { dashboardOfferFilterShouldReloadOnInput } = loadOfferFilterHelpers();

  assert.equal(dashboardOfferFilterShouldReloadOnInput("stock_balances", "124"), false);
  assert.equal(dashboardOfferFilterShouldReloadOnInput("advertising_report", "124"), true);
});

test("article datalist uses current products for article-filtered dashboard reports", () => {
  const { dashboardArticleListSource } = loadOfferFilterHelpers();

  assert.equal(dashboardArticleListSource("stock_balances"), "current_products");
  assert.equal(dashboardArticleListSource("article_analytics"), "current_products");
  assert.equal(dashboardArticleListSource("sales"), "sales");
  assert.equal(dashboardArticleListSource("returns"), "returns");
});
