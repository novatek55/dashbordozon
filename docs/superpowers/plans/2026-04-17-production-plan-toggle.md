# План производства — тумблер в отчёте «Остатки» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в отчёт `stock_balances` тумблер `📦 Остатки` ↔ `🏭 План производства`, показать по каждому артикулу скорость продаж, дорожку остатка и кол-во к производству (по настраиваемому горизонту).

**Architecture:** Весь функционал во фронте, в [web/orders_dashboard.html](web/orders_dashboard.html). При загрузке отчёта параллельно с `/api/stock-balances` запрашивается `/api/supply-plan`, данные мэтчатся по `offer_id`. Одна функция `renderProductionPlan(data, supplyMap, horizon)` плюс реактивное поле «Горизонт». Бэкенд не трогаем.

**Tech Stack:** vanilla JS, inline CSS. Верификация — ручная в браузере (у проекта нет JS-тестового рантайма; существующие JS-правки тоже идут без юнит-тестов, этот паттерн сохраняем).

---

## Spec reference

См. [docs/superpowers/specs/2026-04-17-production-plan-toggle-design.md](../specs/2026-04-17-production-plan-toggle-design.md).

## Файлы

- Modify: [web/orders_dashboard.html](web/orders_dashboard.html)
  - CSS-блок `.analytics-summary` area (~строка 655) — добавить стили для тумблера и групп.
  - Функция `renderStockBalances` ([web/orders_dashboard.html:9057](web/orders_dashboard.html#L9057)) — вставить тумблер в шапку, сохранить данные в замыкании.
  - Новая функция `renderProductionPlan` рядом с `renderStockBalances`.
  - Pipeline fetchReport (~строка 11908) — параллельный запрос `/api/supply-plan`.

Backend, DB — без изменений.

---

## Task 1: CSS для тумблера и групп приоритетов

**Files:**
- Modify: `web/orders_dashboard.html` — добавить CSS-правила в существующий `<style>`-блок после секции `.analytics-summary-card .subvalue` (~строка 647).

- [ ] **Step 1: Добавить CSS-блок**

Найти в файле строку `.analytics-summary-card .subvalue { margin-top: 6px; ...` и вставить **после** неё:

```css
    /* Production plan toggle & groups */
    .pp-toggle { display: inline-flex; gap: 0; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; margin-bottom: 12px; }
    .pp-toggle button { width: auto; padding: 8px 14px; font-size: 13px; background: var(--panel); color: var(--muted); border: 0; border-radius: 0; box-shadow: none; cursor: pointer; }
    .pp-toggle button.active { background: var(--accent); color: #fff; }
    .pp-horizon { display: inline-flex; align-items: center; gap: 8px; margin-left: 16px; font-size: 13px; color: var(--muted); }
    .pp-horizon input { width: 70px; padding: 4px 8px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px; }
    .pp-group-head { font-weight: 700; font-size: 13px; padding: 8px 10px; cursor: pointer; border-radius: 8px; margin: 12px 0 4px; user-select: none; display: flex; align-items: center; gap: 8px; }
    .pp-group-head .pp-arrow { display: inline-block; transition: transform .15s; }
    .pp-group-head.collapsed .pp-arrow { transform: rotate(-90deg); }
    .pp-row-crit { background: #fff0f0; }
    .pp-row-urg { background: #fff5e6; }
    .pp-row-plan { background: #fffae0; }
    .pp-row-ok { background: #f2fbf2; }
    .pp-row-nosale { background: #f5f5f5; color: #999; }
    .pp-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
```

- [ ] **Step 2: Проверить что ничего не сломалось**

Открыть `http://127.0.0.1:8088/` в браузере, зайти в отчёт «Остатки», убедиться что существующий вид отрисовывается без визуальных артефактов.

- [ ] **Step 3: Commit**

```bash
git add web/orders_dashboard.html
git commit -m "feat(stocks): CSS для тумблера «План производства»"
```

---

## Task 2: Кэш данных и тумблер-кнопка

Переработать `renderStockBalances` так, чтобы:
- Сохранять входные данные в замыкании (module-level переменных внутри IIFE/функции).
- В шапке отрисовывать тумблер `📦 Остатки` / `🏭 План производства`.
- При клике переключать `stockBalancesWrap.dataset.view` и вызывать либо существующий рендер, либо новую функцию плана (пока заглушка).

**Files:**
- Modify: [web/orders_dashboard.html:9057-9217](web/orders_dashboard.html#L9057) — функция `renderStockBalances`.

- [ ] **Step 1: Ввести state-переменные и обёртку рендера**

Заменить сигнатуру и начало `renderStockBalances`. Найти строку:

```js
    function renderStockBalances(data) {
      const items = data.items || [];
```

Заменить на:

```js
    // State для переключения Остатки ↔ План производства
    let stockBalancesState = {
      data: null,
      supplyMap: null, // Map<offer_id, supplyItem>
      horizon: 60,
    };

    function renderStockBalances(data) {
      stockBalancesState.data = data;
      stockBalancesWrap.dataset.view = stockBalancesWrap.dataset.view || "stocks";
      _renderStockBalancesByView();
    }

    function _renderStockBalancesByView() {
      const view = stockBalancesWrap.dataset.view || "stocks";
      if (view === "production") {
        renderProductionPlan(stockBalancesState.data, stockBalancesState.supplyMap, stockBalancesState.horizon);
      } else {
        _renderStockBalancesStocksView(stockBalancesState.data);
      }
    }

    function _renderStockBalancesStocksView(data) {
      const items = data.items || [];
```

- [ ] **Step 2: Переименовать дальнейший код функции**

Дальше всё тело функции до `"""}` и event-listeners остаётся как есть. Нужно только закрыть `_renderStockBalancesStocksView` вместо старой `renderStockBalances`.

Найти в конце функции (строка ~9217):

```js
          if (arrow) arrow.style.transform = isOpen ? "" : "rotate(90deg)";
        });
      });
    }
```

Это всё ещё закрывает `_renderStockBalancesStocksView`. Никаких правок не нужно — скобка уже есть. Но теперь ПОСЛЕ этой скобки вставить тумблер-враппер. Сразу после `}` функции добавить:

```js

    function renderProductionPlan(data, supplyMap, horizonDays) {
      // Placeholder — будет реализовано в Task 4.
      stockBalancesWrap.innerHTML = `
        ${_renderStockBalancesToggle()}
        <div style="padding:30px;text-align:center;color:#888;">Подгружаем план производства…</div>`;
      _bindStockBalancesToggle();
    }

    function _renderStockBalancesToggle() {
      const view = stockBalancesWrap.dataset.view || "stocks";
      return `
        <div class="pp-toggle">
          <button data-pp-view="stocks" class="${view === "stocks" ? "active" : ""}">📦 Остатки</button>
          <button data-pp-view="production" class="${view === "production" ? "active" : ""}">🏭 План производства</button>
        </div>
      `;
    }

    function _bindStockBalancesToggle() {
      stockBalancesWrap.querySelectorAll(".pp-toggle button").forEach((btn) => {
        btn.addEventListener("click", () => {
          const newView = btn.dataset.ppView;
          if (stockBalancesWrap.dataset.view === newView) return;
          stockBalancesWrap.dataset.view = newView;
          _renderStockBalancesByView();
        });
      });
    }
```

- [ ] **Step 3: Вставить тумблер в начало существующего stocks-view**

В `_renderStockBalancesStocksView` найти строку с `stockBalancesWrap.innerHTML = \`` (~9182) и вставить вызов тумблера. Было:

```js
      stockBalancesWrap.innerHTML = `
        ${summaryHtml}
        <div style="overflow-x:auto;display:flex;justify-content:center;">
```

Стало:

```js
      stockBalancesWrap.innerHTML = `
        ${_renderStockBalancesToggle()}
        ${summaryHtml}
        <div style="overflow-x:auto;display:flex;justify-content:center;">
```

Так же в самом конце `_renderStockBalancesStocksView`, сразу после `stockBalancesWrap.querySelectorAll(".sb-parent").forEach(...)` блока, но перед закрывающей `}`, добавить:

```js
      _bindStockBalancesToggle();
```

- [ ] **Step 4: Ручная проверка**

Перезапустить сервер (`run_dashboard.cmd`), открыть отчёт «Остатки», убедиться:
- В шапке над карточками виден тумблер.
- «📦 Остатки» подсвечен, таблица рендерится как раньше.
- Клик на «🏭 План производства» — в контейнере появляется текст «Подгружаем план производства…», тумблер переключён.
- Обратно на «📦 Остатки» — таблица восстанавливается.

- [ ] **Step 5: Commit**

```bash
git add web/orders_dashboard.html
git commit -m "feat(stocks): тумблер «Остатки» ↔ «План производства» (stub)"
```

---

## Task 3: Параллельный запрос /api/supply-plan

При загрузке отчёта «Остатки» подтягивать supply-plan параллельно с accruals и класть в `stockBalancesState.supplyMap`.

**Files:**
- Modify: [web/orders_dashboard.html:11908-11930](web/orders_dashboard.html#L11908) — блок `else if (report === "stock_balances")` в обработчике submit.

- [ ] **Step 1: Добавить загрузку supply-plan**

Найти блок:

```js
        } else if (report === "stock_balances") {
          // Fetch accruals for last 60 days to enrich stock table
          const d60 = new Date(); d60.setDate(d60.getDate() - 60);
          ...
          } catch(e) { data._accruals = {}; }
          renderStockBalances(data);
```

Между `data._accruals = {}` catch-блоком и вызовом `renderStockBalances(data)` добавить:

```js
          // Подгружаем supply-plan параллельно — нужен для вкладки «План производства»
          try {
            const supplyData = await fetchJson(`/api/supply-plan`);
            const supplyMap = new Map();
            for (const it of (supplyData.items || [])) {
              if (it && it.offer_id) supplyMap.set(String(it.offer_id), it);
            }
            stockBalancesState.supplyMap = supplyMap;
          } catch(e) {
            stockBalancesState.supplyMap = new Map();
          }
```

- [ ] **Step 2: Ручная проверка**

В браузере в отчёте «Остатки» → DevTools → Network: видеть два запроса на `/api/supply-plan` и `/api/stock-balances`. Оба 200. Переключение на тумблер «🏭 План производства» — пока плейсхолдер, но в DevTools Console выполнить `stockBalancesState.supplyMap.size` → ожидается число > 0.

- [ ] **Step 3: Commit**

```bash
git add web/orders_dashboard.html
git commit -m "feat(stocks): параллельный fetch /api/supply-plan для плана производства"
```

---

## Task 4: Функция renderProductionPlan

Основная функция: группировка артикулов по приоритету, таблица с 7 колонками, сводная панель.

**Files:**
- Modify: [web/orders_dashboard.html](web/orders_dashboard.html) — заменить stub `renderProductionPlan` (из Task 2) на полную реализацию.

- [ ] **Step 1: Реализовать группировку и рендер**

Найти placeholder `function renderProductionPlan(data, supplyMap, horizonDays)` (добавленный в Task 2) и заменить целиком на:

```js
    function renderProductionPlan(data, supplyMap, horizonDays) {
      const stockItems = (data && data.items) || [];
      const horizon = Math.max(7, Math.min(365, Number(horizonDays) || 60));
      if (!(supplyMap instanceof Map)) supplyMap = new Map();

      // Слить stock-balances items с supply-plan по offer_id.
      const merged = stockItems.map((item) => {
        const offer = String(item.offer_id || "");
        const sp = supplyMap.get(offer) || null;
        const v = sp ? Number(sp.avg_daily_sales || 0) : 0;
        const stock = sp ? (
          Number(sp.stock_fbo_available || 0) +
          Number(sp.stock_fbo_supply || 0) +
          Number(sp.stock_fbo_transit || 0) +
          Number(sp.stock_fbo_acceptance || 0) +
          Number(sp.stock_fbs || 0)
        ) : Number(item.total || 0);
        const daysLeft = v > 0 ? stock / v : Infinity;
        const productionQty = v > 0 ? Math.max(0, Math.ceil(horizon * v) - stock) : 0;
        return {
          offer_id: offer,
          name: item.name || "",
          v: v,
          stock: stock,
          daysLeft: daysLeft,
          productionQty: productionQty,
          grade: sp ? (sp.turnover_grade || "") : "",
        };
      });

      // Классификация по приоритету
      const buckets = { crit: [], urg: [], plan: [], ok: [], nosale: [] };
      for (const r of merged) {
        if (r.v <= 0) buckets.nosale.push(r);
        else if (r.daysLeft <= 1) buckets.crit.push(r);
        else if (r.daysLeft <= 14) buckets.urg.push(r);
        else if (r.daysLeft <= 35) buckets.plan.push(r);
        else buckets.ok.push(r);
      }

      // Сортировка внутри групп: по нарастанию daysLeft (худшее сверху)
      for (const b of Object.values(buckets)) b.sort((a, b2) => a.daysLeft - b2.daysLeft);

      const sumQty = (arr) => arr.reduce((s, r) => s + r.productionQty, 0);
      const summary = {
        crit: { n: buckets.crit.length, qty: sumQty(buckets.crit) },
        urg: { n: buckets.urg.length, qty: sumQty(buckets.urg) },
        plan: { n: buckets.plan.length, qty: sumQty(buckets.plan) },
        total: sumQty(buckets.crit) + sumQty(buckets.urg) + sumQty(buckets.plan),
      };

      const groupDefs = [
        { key: "crit", title: "🔴 Критично (заканчивается за сутки)", cls: "pp-row-crit", collapsed: false },
        { key: "urg", title: "🟠 Срочно (до 14 дней)", cls: "pp-row-urg", collapsed: false },
        { key: "plan", title: "🟡 В плане (15–35 дней)", cls: "pp-row-plan", collapsed: false },
        { key: "ok", title: "🟢 Запас OK (>35 дней)", cls: "pp-row-ok", collapsed: true },
        { key: "nosale", title: "⚪ Без продаж", cls: "pp-row-nosale", collapsed: true },
      ];

      const renderRow = (r, cls) => {
        const daysTxt = r.daysLeft === Infinity ? "∞" : r.daysLeft.toFixed(1);
        const prodTxt = r.v > 0 ? formatMetric(r.productionQty, 0) : "—";
        return `<tr class="${cls}">
          <td style="font-weight:600;padding:4px 8px;white-space:nowrap;">${fmt(r.offer_id)}</td>
          <td style="padding:4px 8px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${fmt(r.name)}">${fmt(r.name.length > 50 ? r.name.slice(0, 48) + "…" : r.name)}</td>
          <td style="text-align:right;padding:4px 8px;">${r.v > 0 ? r.v.toFixed(2) : "—"}</td>
          <td style="text-align:right;padding:4px 8px;">${formatMetric(r.stock, 0)}</td>
          <td style="text-align:right;padding:4px 8px;">${daysTxt}</td>
          <td style="text-align:right;padding:4px 8px;font-weight:700;">${prodTxt}</td>
          <td style="padding:4px 8px;font-size:11px;color:#888;">${fmt(r.grade)}</td>
        </tr>`;
      };

      const renderGroup = (def) => {
        const rows = buckets[def.key];
        if (!rows.length) return "";
        const colCls = def.collapsed ? "collapsed" : "";
        const display = def.collapsed ? "none" : "";
        return `
          <div class="pp-group" data-pp-group="${def.key}">
            <div class="pp-group-head ${colCls}" data-pp-group-toggle="${def.key}">
              <span class="pp-arrow">&#9660;</span>
              <span>${def.title}</span>
              <span style="margin-left:auto;color:#888;font-weight:400;">${rows.length} арт. / ${formatMetric(sumQty(rows), 0)} ед.</span>
            </div>
            <table class="data-table report-table pp-group-body" data-pp-group-body="${def.key}" style="font-size:13px;width:100%;display:${display === "none" ? "none" : "table"};">
              <thead><tr>
                <th>Артикул</th>
                <th>Наименование</th>
                <th style="text-align:right;">v/день</th>
                <th style="text-align:right;">Остаток</th>
                <th style="text-align:right;">≈дней</th>
                <th style="text-align:right;">К произв.</th>
                <th>Статус Ozon</th>
              </tr></thead>
              <tbody>${rows.map((r) => renderRow(r, def.cls)).join("")}</tbody>
            </table>
          </div>
        `;
      };

      const summaryHtml = `
        <div class="pp-summary">
          <div class="analytics-summary-card"><div class="label">🔴 Критично</div><div class="value">${summary.crit.n}</div><div class="subvalue">${formatMetric(summary.crit.qty, 0)} ед. к производству</div></div>
          <div class="analytics-summary-card"><div class="label">🟠 Срочно</div><div class="value">${summary.urg.n}</div><div class="subvalue">${formatMetric(summary.urg.qty, 0)} ед. к производству</div></div>
          <div class="analytics-summary-card"><div class="label">🟡 В плане</div><div class="value">${summary.plan.n}</div><div class="subvalue">${formatMetric(summary.plan.qty, 0)} ед. к производству</div></div>
          <div class="analytics-summary-card"><div class="label">Итого к производству</div><div class="value">${formatMetric(summary.total, 0)}</div><div class="subvalue">за горизонт ${horizon} дн. (без 🟢/⚪)</div></div>
        </div>
      `;

      const horizonHtml = `
        <label class="pp-horizon">Горизонт производства, дней:
          <input type="number" id="pp-horizon-input" min="7" max="365" step="1" value="${horizon}">
        </label>
      `;

      stockBalancesWrap.innerHTML = `
        ${_renderStockBalancesToggle()}
        ${horizonHtml}
        ${summaryHtml}
        <div style="max-width: 1200px; margin: 0 auto;">
          ${groupDefs.map(renderGroup).join("")}
        </div>
      `;

      _bindStockBalancesToggle();
      _bindProductionPlanHandlers();
    }

    function _bindProductionPlanHandlers() {
      // Реализовано в Task 5 и Task 6.
    }
```

- [ ] **Step 2: Ручная проверка — корректность цифр**

Перезапустить сервер, открыть «Остатки» → «🏭 План производства». Сверить с моим ответом в чате (горизонт 60):

```bash
# Сравнительный расчёт 1 артикула через curl (терминал)
curl -s http://127.0.0.1:8088/api/supply-plan | python -c "
import json,sys; d=json.load(sys.stdin)
for it in d['items']:
    if it['offer_id']=='202 сетка':
        v=it['avg_daily_sales']
        st=it['stock_fbo_available']+it['stock_fbo_supply']+it['stock_fbo_transit']+it['stock_fbo_acceptance']+it['stock_fbs']
        import math
        print('v=',v,'stock=',st,'prod60=',max(0, math.ceil(60*v)-st))
"
```

В UI значение в колонке «К произв.» для «202 сетка» должно совпадать с `prod60`.

- [ ] **Step 3: Commit**

```bash
git add web/orders_dashboard.html
git commit -m "feat(stocks): реализация «План производства» — группировка, сводка, таблица"
```

---

## Task 5: Реактивность поля «Горизонт»

Поле `#pp-horizon-input` должно при изменении пересчитывать «К произв.» и сводку без повторных запросов.

**Files:**
- Modify: `web/orders_dashboard.html` — функция `_bindProductionPlanHandlers` из Task 4.

- [ ] **Step 1: Реализовать слушатель поля**

Заменить текущее тело `_bindProductionPlanHandlers` на:

```js
    function _bindProductionPlanHandlers() {
      const horizonInput = stockBalancesWrap.querySelector("#pp-horizon-input");
      if (horizonInput) {
        const onChange = () => {
          const v = Math.max(7, Math.min(365, Number(horizonInput.value) || 60));
          if (v !== stockBalancesState.horizon) {
            stockBalancesState.horizon = v;
            renderProductionPlan(stockBalancesState.data, stockBalancesState.supplyMap, stockBalancesState.horizon);
          }
        };
        horizonInput.addEventListener("change", onChange);
        horizonInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); onChange(); } });
      }
    }
```

- [ ] **Step 2: Ручная проверка**

На вкладке «🏭 План производства»:
- Ввести в поле «Горизонт» `30` → Enter. «Итого к производству» и «К произв.» должны уменьшиться.
- Ввести `120` → Enter. Цифры вырастут.
- Ввести `5` → клампится до `7` (минимум).
- Ввести `9999` → клампится до `365`.
- Группировка (кол-во артикулов в 🔴/🟠/🟡/🟢/⚪) при этом НЕ меняется — она завязана на `stock / v`, а не на горизонт.

- [ ] **Step 3: Commit**

```bash
git add web/orders_dashboard.html
git commit -m "feat(stocks): реактивное поле «Горизонт производства»"
```

---

## Task 6: Сворачиваемые группы

Клик по заголовку группы скрывает/показывает её таблицу.

**Files:**
- Modify: `web/orders_dashboard.html` — функция `_bindProductionPlanHandlers`.

- [ ] **Step 1: Добавить обработчик клика по заголовку**

В конец функции `_bindProductionPlanHandlers` (перед закрывающей `}`) добавить:

```js
      stockBalancesWrap.querySelectorAll("[data-pp-group-toggle]").forEach((head) => {
        head.addEventListener("click", () => {
          const key = head.dataset.ppGroupToggle;
          const body = stockBalancesWrap.querySelector(`[data-pp-group-body="${key}"]`);
          if (!body) return;
          const hidden = body.style.display === "none";
          body.style.display = hidden ? "table" : "none";
          head.classList.toggle("collapsed", !hidden);
        });
      });
```

- [ ] **Step 2: Ручная проверка**

- Группы 🟢 и ⚪ при открытии вкладки свёрнуты (таблицы скрыты, стрелка повёрнута).
- Клик по заголовку 🟢 — разворачивает таблицу, стрелка обратно вниз.
- Клик ещё раз — сворачивает.
- То же для 🔴/🟠/🟡 (по умолчанию развёрнуты).

- [ ] **Step 3: Commit**

```bash
git add web/orders_dashboard.html
git commit -m "feat(stocks): сворачиваемые группы приоритета в плане производства"
```

---

## Task 7: Финальная end-to-end проверка

Сквозной smoke-test на живом сервере, чтобы убедиться что всё работает вместе.

- [ ] **Step 1: Перезапустить сервер**

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File restart_dashboard.ps1
```

- [ ] **Step 2: Пройти чек-лист в браузере**

Открыть `http://127.0.0.1:8088/`, отчёт «Остатки», нажать «Показать».

| # | Действие | Ожидаемый результат |
|---|----------|---------------------|
| 1 | Отчёт отрисовался | Тумблер виден, активно «📦 Остатки», таблица как раньше |
| 2 | Клик «🏭 План производства» | Подгрузка supply-plan не идёт повторно (кэш), вкладка открывается за <100мс |
| 3 | Видны 5 групп с иконками | 🔴/🟠/🟡/🟢/⚪, 🟢 и ⚪ свёрнуты |
| 4 | Сводка: 4 карточки | Числа соответствуют суммам строк в группах |
| 5 | Поле «Горизонт» = 60 | «К произв.» у «202 сетка»: совпадает с ручным расчётом (см. Task 4) |
| 6 | Ввести горизонт 30, Enter | Цифры уменьшились, приоритизация та же |
| 7 | Развернуть 🟢 | Показываются артикулы с >35 дней запаса, «К произв.» = 0 или маленькое |
| 8 | Вернуться на «📦 Остатки» | Таблица восстановлена, нет лишних запросов |
| 9 | DevTools Console без ошибок | Нет `Uncaught …` в консоли |

- [ ] **Step 3: Проверить сервер-логи**

```bash
python -c "print(open('logs/dashboard_stderr.log', encoding='utf-8').read()[-500:])"
```

Не должно быть новых traceback после перезапуска.

- [ ] **Step 4: Финальный коммит (пустой tag-коммит или просто мердж в master)**

Если работа шла в ветке — слить в master. Если работа была прямо на master — ничего не нужно, все коммиты уже в истории.

---

## Self-review (выполнено при написании плана)

**Spec coverage:**
- ✅ Тумблер — Task 2.
- ✅ Поле «Горизонт» по умолчанию 60, min 7, max 365 — Task 4 (рендер), Task 5 (клэмп).
- ✅ Сводная панель 4 карточки — Task 4.
- ✅ Таблица 7 колонок, группировка — Task 4.
- ✅ Приоритизация по `stock/v` — Task 4.
- ✅ Формула `ceil(horizon × v) − stock` — Task 4.
- ✅ Определение `stock` (fbo_available + fbo_supply + fbo_transit + fbo_acceptance + fbs) — Task 4, совпадает со спекой.
- ✅ Параллельный fetch supply-plan — Task 3.
- ✅ Кэш в замыкании, нет повторных запросов при переключении — Task 2 (state), Task 3 (заполнение).
- ✅ Обработка артикулов без supply-plan — Task 4 (fallback: v=0, falls into `nosale`).
- ✅ Сворачиваемые группы, 🟢/⚪ по умолчанию свёрнуты — Task 4 (начальное состояние), Task 6 (взаимодействие).

**Placeholder scan:** stub в Task 2 — осознанный (распаковывается в Task 4). В финальном коде плейсхолдеров не осталось.

**Type consistency:** `stockBalancesState.supplyMap` — всегда `Map<string, supplyItem>`. `horizonDays` — number. Названия функций `_renderStockBalancesByView`, `_renderStockBalancesStocksView`, `renderProductionPlan`, `_renderStockBalancesToggle`, `_bindStockBalancesToggle`, `_bindProductionPlanHandlers` — использованы одинаково во всех тасках.
