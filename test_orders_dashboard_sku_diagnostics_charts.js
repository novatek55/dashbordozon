const fs = require("fs");
const path = require("path");
const vm = require("vm");
const test = require("node:test");
const assert = require("node:assert/strict");

function extractFunction(source, name) {
  const needle = `function ${name}(`;
  const start = source.indexOf(needle);
  if (start === -1) throw new Error(`${name} not found`);
  const bodyStart = source.indexOf("{", start);
  let depth = 0;
  for (let i = bodyStart; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === "{") depth += 1;
    if (ch === "}") depth -= 1;
    if (depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`${name} end not found`);
}

function extractConstArrow(source, name) {
  const needle = `const ${name} =`;
  const start = source.indexOf(needle);
  if (start === -1) throw new Error(`${name} not found`);
  const bodyStart = source.indexOf("{", start);
  let depth = 0;
  for (let i = bodyStart; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === "{") depth += 1;
    if (ch === "}") depth -= 1;
    if (depth === 0) {
      const end = source.indexOf(";", i);
      return source.slice(start, end + 1);
    }
  }
  throw new Error(`${name} end not found`);
}

function loadSkuChartHelpers() {
  const file = path.resolve(__dirname, "web", "orders_dashboard.html");
  const source = fs.readFileSync(file, "utf8");
  const snippets = [
    extractConstArrow(source, "buildPortfolioItem"),
    extractFunction(source, "buildPdpVisitorsChart"),
    extractFunction(source, "buildPositionChart"),
    extractFunction(source, "buildCartToOrderChart"),
    extractFunction(source, "buildStockHistoryChart"),
  ].join("\n");
  const context = {
    module: { exports: {} },
    formatMetric(value, decimals = 0) {
      return Number(value || 0).toFixed(decimals);
    },
    buildHtmlXTicks(points) {
      return points.map((point) => String(point.day || "").slice(8, 10)).join("|");
    },
    globalTsStatsLine(series) {
      return `<stats data-count="${series.length}" data-series="${series.join(",")}"></stats>`;
    },
    globalTsBollingerSvg() {
      return "";
    },
    globalTsBollingerDots() {
      return "";
    },
    globalPromoEventsSvg() {
      return "";
    },
    deltaPct(cur, prev) {
      return Number(prev || 0) ? ((Number(cur || 0) - Number(prev || 0)) / Number(prev || 0)) * 100 : 0;
    },
    summary: {},
  };
  vm.runInNewContext(`${snippets}; module.exports = { buildPortfolioItem, buildPdpVisitorsChart, buildPositionChart, buildCartToOrderChart, buildStockHistoryChart };`, context, {
    filename: "orders_dashboard.html",
  });
  return context.module.exports;
}

test("pdp visitors chart clamps ad split to total visitors so bars stay visible", () => {
  const { buildPdpVisitorsChart } = loadSkuChartHelpers();

  const html = buildPdpVisitorsChart({
    daily: [
      { day: "2026-06-01", session_view_pdp: 10, pdp_ad_clicks: 15, pdp_seo_visitors: 0 },
      { day: "2026-06-02", session_view_pdp: 20, pdp_ad_clicks: 5, pdp_seo_visitors: 0 },
    ],
    promos: [],
  }, "chart");

  assert.match(html, /Всего: 30/);
  assert.match(html, /SEO: 15/);
  assert.match(html, /Реклама: 15/);
  assert.equal(html.includes('y="-'), false);
});

test("portfolio item sums pdp visitors by SEO and ad channels across all articles", () => {
  const { buildPortfolioItem, buildPdpVisitorsChart } = loadSkuChartHelpers();

  const portfolio = buildPortfolioItem([
    {
      daily: [
        { day: "2026-06-01", session_view_pdp: 10, pdp_ad_clicks: 3, pdp_seo_visitors: 7 },
        { day: "2026-06-02", session_view_pdp: 20, pdp_ad_clicks: 5, pdp_seo_visitors: 15 },
      ],
    },
    {
      daily: [
        { day: "2026-06-01", session_view_pdp: 4, pdp_ad_clicks: 1, pdp_seo_visitors: 3 },
        { day: "2026-06-02", session_view_pdp: 6, pdp_ad_clicks: 0, pdp_seo_visitors: 6 },
      ],
    },
  ]);

  assert.deepEqual(JSON.parse(JSON.stringify(
    portfolio.daily.map((point) => ({
      day: point.day,
      total: point.session_view_pdp,
      ads: point.pdp_ad_clicks,
      seo: point.pdp_seo_visitors,
    })),
  )),
    [
      { day: "2026-06-01", total: 14, ads: 4, seo: 10 },
      { day: "2026-06-02", total: 26, ads: 5, seo: 21 },
    ],
  );

  const html = buildPdpVisitorsChart(portfolio, "chart");
  assert.match(html, /Всего: 40/);
  assert.match(html, /SEO: 31/);
  assert.match(html, /Реклама: 9/);
});

test("portfolio item averages daily position across articles with valid positions", () => {
  const { buildPortfolioItem, buildPositionChart } = loadSkuChartHelpers();

  const portfolio = buildPortfolioItem([
    {
      position_category_30d: 10,
      stock_fbo: 3,
      stock_fbs: 0,
      daily: [
        { day: "2026-06-01", position_category: 10 },
        { day: "2026-06-02", position_category: 5, stock_fbo: 0, stock_fbs: 2 },
      ],
    },
    {
      position_category_30d: 20,
      stock_fbo: 0,
      stock_fbs: 0,
      daily: [
        { day: "2026-06-01", position_category: 20 },
        { day: "2026-06-02", position_category: 7 },
      ],
    },
  ]);

  assert.deepEqual(JSON.parse(JSON.stringify(
    portfolio.daily.map((point) => ({
      day: point.day,
      position: point.position_category,
    })),
  )),
    [
      { day: "2026-06-01", position: 10 },
      { day: "2026-06-02", position: 5 },
    ],
  );

  const html = buildPositionChart(portfolio, "chart");
  assert.match(html, /data-series="10,5"/);
  assert.match(html, /7\.50/);
});

test("cart to order chart preserves day slots and keeps zero CR days in stats", () => {
  const { buildCartToOrderChart } = loadSkuChartHelpers();

  const html = buildCartToOrderChart({
    daily: [
      { day: "2026-06-01", hits_tocart: 4, ordered_units: 2 },
      { day: "2026-06-02", hits_tocart: 0, ordered_units: 1 },
      { day: "2026-06-03", hits_tocart: 5, ordered_units: 0 },
      { day: "2026-06-04", hits_tocart: 2, ordered_units: 3 },
    ],
    promos: [],
  }, "chart");

  assert.match(html, /CR корзина → заказ по дням/);
  assert.match(html, /Средняя за период: 66\.67%/);
  assert.match(html, /data-count="3"/);
  assert.match(html, /data-series="50,0,150"/);
  assert.match(html, />01\|02\|03\|04</);
  assert.equal((html.match(/<rect /g) || []).length, 3);
});

test("cart to order chart renders an empty state when the period has no carts", () => {
  const { buildCartToOrderChart } = loadSkuChartHelpers();

  const html = buildCartToOrderChart({
    daily: [
      { day: "2026-06-01", hits_tocart: 0, ordered_units: 0 },
      { day: "2026-06-02", hits_tocart: 0, ordered_units: 2 },
    ],
  }, "chart");

  assert.match(html, /Нет корзин за период/);
});

test("stock history chart uses daily stock snapshots instead of flat current stock", () => {
  const { buildStockHistoryChart } = loadSkuChartHelpers();

  const html = buildStockHistoryChart({
    stock_fbo: 91,
    stock_fbs: 241,
    daily: [
      { day: "2026-06-01", stock_fbo: 10, stock_fbs: 20 },
      { day: "2026-06-02", stock_fbo: 8, stock_fbs: 18 },
      { day: "2026-06-03", stock_fbo: 7, stock_fbs: 16 },
    ],
  }, "chart");

  assert.match(html, /Дневная история FBO \+ FBS/);
  assert.match(html, /data-series="30,26,23"/);
  assert.equal((html.match(/<rect /g) || []).length, 6);
});

test("stock history chart skips days without stock snapshots", () => {
  const { buildStockHistoryChart } = loadSkuChartHelpers();

  const html = buildStockHistoryChart({
    stock_fbo: 5,
    stock_fbs: 9,
    daily: [
      { day: "2026-06-24", stock_fbo: 0, stock_fbs: 0 },
      { day: "2026-06-25", stock_fbo: 0, stock_fbs: 0 },
      { day: "2026-06-26", stock_fbo: 5, stock_fbs: 9 },
    ],
  }, "chart");

  assert.match(html, /data-series="14"/);
  assert.equal((html.match(/<rect /g) || []).length, 2);
  assert.match(html, />26</);
  assert.equal(html.includes(">24|25|26<"), false);
});

test("stock history chart does not fake a flat chart when daily stock is missing", () => {
  const { buildStockHistoryChart } = loadSkuChartHelpers();

  const html = buildStockHistoryChart({
    stock_fbo: 91,
    stock_fbs: 241,
    daily: [
      { day: "2026-06-01", ordered_units_fbo: 1, ordered_units_fbs: 2 },
      { day: "2026-06-02", ordered_units_fbo: 0, ordered_units_fbs: 3 },
    ],
  }, "chart");

  assert.match(html, /Нет дневной истории остатков/);
  assert.equal((html.match(/<rect /g) || []).length, 0);
});

test("sku diagnostics details do not render the average price chart", () => {
  const file = path.resolve(__dirname, "web", "orders_dashboard.html");
  const source = fs.readFileSync(file, "utf8");

  assert.equal(source.includes("function buildPriceChart("), false);
  assert.equal(source.includes("Средняя цена по дням"), false);
  assert.equal(source.includes("const priceChartId = `article_analytics_price_chart_${idx}`;"), false);
  assert.equal(source.includes("${buildPriceChart(item, priceChartId)}"), false);
});
