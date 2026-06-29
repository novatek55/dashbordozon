const fs = require("fs");
const path = require("path");
const vm = require("vm");
const test = require("node:test");
const assert = require("node:assert/strict");

function loadWbFinanceHelpers() {
  const file = path.resolve(__dirname, "web", "orders_dashboard.html");
  const source = fs.readFileSync(file, "utf8");
  const start = source.indexOf("// WB_FINANCE_HELPERS_START");
  const end = source.indexOf("    function buildFinanceRowsMap");
  if (start === -1 || end === -1 || end <= start) {
    throw new Error("WB finance helper block not found in orders_dashboard.html");
  }

  const snippet = source.slice(start, end);
  const context = { module: { exports: {} }, exports: {} };
  vm.runInNewContext(
    `function toNum(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}
${snippet}
module.exports = {
  classifyWbFinanceOperation,
  normalizeWbExpenseAmount,
  signedWbRevenueAmount,
  buildWbFinanceReportView,
};`,
    context,
    { filename: "orders_dashboard.html" }
  );
  return context.module.exports;
}

test("WB finance operation names map into Ozon finance groups", () => {
  const { classifyWbFinanceOperation } = loadWbFinanceHelpers();
  const classify = (name) => JSON.parse(JSON.stringify(classifyWbFinanceOperation(name)));

  assert.deepEqual(classify("Логистика"), {
    groupKey: "delivery_services_total",
    rowKey: "wb_logistics",
    label: "Логистика",
  });
  assert.deepEqual(classify("Хранение"), {
    groupKey: "fbo_storage_services",
    rowKey: "wb_storage",
    label: "Хранение",
  });
  assert.deepEqual(classify("Обработка товара"), {
    groupKey: "fbo_acceptance_services",
    rowKey: "wb_acceptance",
    label: "Обработка товара",
  });
  assert.deepEqual(classify("Удержание"), {
    groupKey: "other_services_misc",
    rowKey: "wb_deduction",
    label: "Удержание",
  });
});

test("WB finance accrued is calculated and WB forPay remains available for reconciliation", () => {
  const { buildWbFinanceReportView } = loadWbFinanceHelpers();
  const report = buildWbFinanceReportView({
    plan: { month: "2026-05", month_days: 31 },
    items: [
      {
        report_date: "2026-05-04",
        seller_oper_name: "Продажа",
        gross_revenue: 1000,
        marketplace_commission: 200,
        logistics_direct: 100,
        logistics_reverse: 0,
        acquiring: 0,
        penalties: 0,
        other_deductions: 0,
        material_cost: 150,
        to_pay: 850,
      },
    ],
    advertising_daily: [{ report_date: "2026-05-04", spend: 100 }],
  });
  const row = (key) => report.rows.find((item) => item.key === key);

  assert.equal(row("accrued").total, 600);
  assert.equal(row("accrued").label, "Начислено расчетное");
  assert.equal(row("wb_for_pay").total, 850);
  assert.equal(row("wb_for_pay").label, "К выплате WB (forPay)");
  assert.equal(row("wb_for_pay_delta").total, 250);
  assert.equal(row("gross_profit").total, 400);
  assert.equal(row("gross_profit_pct_accrued").total, 400 / 600);
});

test("WB finance normalizes expense signs and returns reduce revenue", () => {
  const { normalizeWbExpenseAmount, signedWbRevenueAmount } = loadWbFinanceHelpers();

  assert.equal(normalizeWbExpenseAmount(-34105), 34105);
  assert.equal(normalizeWbExpenseAmount(44036.03), 44036.03);
  assert.equal(signedWbRevenueAmount("Продажа", 229844.34), 229844.34);
  assert.equal(signedWbRevenueAmount("Возврат", 9395), -9395);
});
