# Диагностика SKU — график «CR корзина → заказ» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в блок «Диагностика SKU» карточку-график «CR корзина → заказ по дням» сразу после существующего «Конверсия в корзину по дням», в том же стиле и размере.

**Architecture:** Чисто фронтенд-изменение в [web/orders_dashboard.html](web/orders_dashboard.html). Одна новая функция `buildCartToOrderChart(item, chartId)` — копия `buildCartConversionChart` с изменённой формулой (`ordered_units / hits_tocart × 100`) и null-обработкой дней без корзин. Плюс одна строка вызова в рендер-цепочке карточек.

**Tech Stack:** Vanilla JS (IIFE-код внутри HTML), SVG-рендер, существующие хелперы `globalTsStatsLine`, `globalTsBollingerSvg`, `globalTsBollingerDots`, `globalPromoEventsSvg`, `buildHtmlXTicks`.

**Spec:** [docs/superpowers/specs/2026-04-17-sku-diagnostics-cart-to-order-cr-design.md](docs/superpowers/specs/2026-04-17-sku-diagnostics-cart-to-order-cr-design.md)

---

## Карта файлов

- **Modify:** [web/orders_dashboard.html](web/orders_dashboard.html)
  - Вставка новой функции `buildCartToOrderChart` сразу после `buildCartConversionChart` (после строки 5605, перед `buildPositionChart`).
  - Объявление ID графика рядом со строкой 6574.
  - Вызов новой функции в рендер-цепочке сразу после строки 6685.

Backend/БД/CSS не трогаем.

---

## Task 1: Добавить функцию `buildCartToOrderChart`

**Files:**
- Modify: [web/orders_dashboard.html](web/orders_dashboard.html) — вставка после строки 5605 (т.е. после закрытия `buildCartConversionChart`, перед `function buildPositionChart`).

- [ ] **Step 1: Вставить функцию-близнец сразу после `buildCartConversionChart`**

Использовать Edit с `old_string` = текущий заголовок `function buildPositionChart(item, chartId) {` (чтобы привязаться к уникальному якорю) и `new_string` = ниже приведённая функция + пустая строка + исходный заголовок.

Точный новый код, который надо вставить **перед** `function buildPositionChart(item, chartId) {`:

```javascript
      function buildCartToOrderChart(item, chartId) {
        const points = Array.isArray(item.daily) ? item.daily : [];
        if (!points.length) {
          return `<div class="analytics-chart-empty">Нет динамики за период</div>`;
        }
        const width = 920;
        const height = 140;
        const left = 42;
        const top = 16;
        const right = 16;
        const bottom = 24;
        const usableW = width - left - right;
        const usableH = height - top - bottom;
        const barGap = 5;
        const barWidth = Math.max(4, ((usableW / Math.max(points.length, 1)) - barGap) * 0.46);
        const barBottom = top + usableH;
        const barColor = "#a78bdb";
        const labelColor = "#5b3a99";

        // cr = orders / cart * 100, null если корзины не было — чтобы день не попал в статистику
        const convSeries = points.map((p) => {
          const cart = Number(p.hits_tocart || 0);
          const orders = Number(p.ordered_units || 0);
          return cart > 0 ? (orders / cart) * 100 : null;
        });
        const validValues = convSeries.filter((v) => v != null && v > 0);
        const avgConv = validValues.length ? validValues.reduce((s, v) => s + v, 0) / validValues.length : 0;

        // Если за весь период не было ни одной корзины — показываем заглушку
        const anyCart = convSeries.some((v) => v != null);
        if (!anyCart) {
          return `<div class="analytics-chart-empty">Нет корзин за период</div>`;
        }

        const nonNullForRange = convSeries.filter((v) => v != null);
        const maxValue = Math.max(0.5, ...nonNullForRange);
        const yForValue = (v) => barBottom - (Math.max(0, v) / maxValue) * usableH;

        const gridValues = Array.from({ length: 3 }, (_, i) => +((maxValue / 2) * i).toFixed(1)).reverse();
        const gridLines = gridValues.map((v) => {
          const y = yForValue(v);
          return `<line x1="${left}" y1="${y.toFixed(2)}" x2="${(width-right).toFixed(2)}" y2="${y.toFixed(2)}" stroke="#ede4f7" stroke-width="1" />
            <text x="${(left-8).toFixed(2)}" y="${(y+4).toFixed(2)}" text-anchor="end" font-size="10" fill="#a78bdb">${v.toFixed(1)}%</text>`;
        }).join("");

        const bars = points.map((p, i) => {
          const x = left + i * (usableW / Math.max(points.length, 1)) + barGap / 2;
          const v = convSeries[i];
          if (v == null) {
            // День без корзин — позиция по X сохранена, но столбец не рисуем
            return "";
          }
          const h = (v / maxValue) * usableH;
          const y = barBottom - h;
          const labelY = Math.max(12, y - 4);
          const label = v > 0 ? v.toFixed(1) : "";
          return `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${Math.max(h, 1).toFixed(2)}" rx="2" fill="${barColor}" />
            <text x="${(x + barWidth / 2).toFixed(2)}" y="${labelY.toFixed(2)}" text-anchor="middle" font-size="11" font-weight="700" fill="${labelColor}">${label}</text>`;
        }).join("");

        // Для статистики и Bollinger — только валидные значения (исключая null и нули)
        const statsSeries = convSeries.filter((v) => v != null);
        const statsXFor = (idx) => {
          // Индекс в statsSeries мапится обратно на индекс в исходной серии
          let found = -1, counter = -1;
          for (let j = 0; j < convSeries.length; j += 1) {
            if (convSeries[j] != null) counter += 1;
            if (counter === idx) { found = j; break; }
          }
          const realIdx = found >= 0 ? found : idx;
          return left + realIdx * (usableW / Math.max(points.length, 1)) + barWidth / 2;
        };

        const htmlXTicks = buildHtmlXTicks(points, width, left, right, barWidth);
        return `
          <div class="analytics-chart sku-impressions-chart" id="${chartId}">
            <div class="sku-impressions-meta">
              <div>
                <div class="sku-impressions-title"><button class="sku-collapse-btn" onclick="this.classList.toggle('collapsed');this.closest('.sku-impressions-chart').querySelector('.sku-collapsible').classList.toggle('collapsed')"><span class="arrow">&#9660;</span></button> CR корзина → заказ по дням</div>
                <div class="sku-impressions-summary">Средняя за период: ${avgConv.toFixed(2)}%</div>
              </div>
              <div class="sku-chart-legend">
                <span class="sku-chart-legend-item"><i class="sku-chart-legend-swatch" style="background:${barColor};"></i>CR корзина → заказ %</span>
              </div>
              ${globalTsStatsLine(statsSeries)}
            </div>
            <div class="sku-collapsible sku-chart-wrap">
              <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
                ${gridLines}
                <line x1="${left}" y1="${barBottom}" x2="${width-right}" y2="${barBottom}" stroke="#ccd6e3" stroke-width="1" />
                ${globalTsBollingerSvg(statsSeries, statsXFor, yForValue)}
                ${bars}
                ${globalTsBollingerDots(statsSeries, statsXFor, yForValue)}
                ${globalPromoEventsSvg(Array.isArray(item.promos) ? item.promos : [], points, (idx) => left + idx * (usableW / Math.max(points.length, 1)) + barWidth / 2, top, barBottom)}
              </svg>
              <div class="sku-chart-xticks">${htmlXTicks}</div>
            </div>
          </div>
        `;
      }

```

- [ ] **Step 2: Проверить, что JS-синтаксис валиден**

Запуск (в bash):

```bash
PYTHONIOENCODING=utf-8 python -c "
import re
html = open('web/orders_dashboard.html', encoding='utf-8').read()
m = re.search(r'function buildCartToOrderChart\(item, chartId\)', html)
print('found function:', bool(m))
# убедимся что функция буквально вставлена один раз
print('occurrences:', len(re.findall(r'function buildCartToOrderChart\(', html)))
"
```

Expected output:
```
found function: True
occurrences: 1
```

Если `occurrences: 0` — Edit не применился, исправить `old_string`.
Если `occurrences: >1` — функция вставлена дважды, откатить и повторить.

- [ ] **Step 3: Коммит**

```bash
git add web/orders_dashboard.html
git commit -m "feat(sku): функция buildCartToOrderChart для графика CR корзина→заказ"
```

---

## Task 2: Подключить функцию в рендер-цепочку

**Files:**
- Modify: [web/orders_dashboard.html:6574](web/orders_dashboard.html#L6574) — объявление ID.
- Modify: [web/orders_dashboard.html:6685](web/orders_dashboard.html#L6685) — вызов в рендере.

- [ ] **Step 1: Объявить ID нового графика**

Найти строку:
```
const cartConvChartId = `article_analytics_cart_conv_chart_${idx}`;
```

И **после** неё вставить:
```
const cartToOrderChartId = `article_analytics_cart_to_order_chart_${idx}`;
```

- [ ] **Step 2: Добавить вызов функции в рендере**

Найти строку:
```
              ${buildCartConversionChart(item, cartConvChartId)}
```

И **после** неё вставить (с тем же отступом):
```
              ${buildCartToOrderChart(item, cartToOrderChartId)}
```

- [ ] **Step 3: Проверить, что и объявление ID, и вызов добавлены**

Запуск (в bash):

```bash
PYTHONIOENCODING=utf-8 python -c "
html = open('web/orders_dashboard.html', encoding='utf-8').read()
print('id declared:', html.count('cartToOrderChartId ='))
print('id used:', html.count('cartToOrderChartId)'))
print('call present:', html.count('buildCartToOrderChart(item'))
"
```

Expected output:
```
id declared: 1
id used: 1
call present: 1
```

- [ ] **Step 4: Коммит**

```bash
git add web/orders_dashboard.html
git commit -m "feat(sku): подключить график CR корзина→заказ в рендер диагностики"
```

---

## Task 3: Ручная визуальная проверка в браузере

Автоматических тестов для фронта в проекте нет (см. `package.json` — нет jest/vitest и т.п.). Проверка — через локальный запуск дашборда и открытие реального SKU.

**Files:** нет изменений кода.

- [ ] **Step 1: Запустить/перезапустить дашборд**

Сервер сам перезапускается при изменении `.py`/`.html` (см. `run_dashboard.cmd` → `run_dashboard.ps1`). Если сервер не запущен — запустить через `run_dashboard.cmd` и убедиться, что процесс жив (открыть http://localhost:5000).

- [ ] **Step 2: Пройти тест-кейсы из спеки**

Открыть в браузере отчёт «Диагностика SKU» для любого активно продающегося артикула за последние 30 дней. Проверить поочерёдно:

1. Под блоком «Конверсия в корзину по дням» появился блок «CR корзина → заказ по дням» такого же размера.
2. Количество и позиция столбцов совпадают с соседним графиком — края выровнены.
3. Найти SKU, где в середине периода был день с 0 корзин (или искусственно проверить: выбрать период, охватывающий «тихий» день). В новом графике этот день — пустая позиция, не 0%. Строка `Z=... MAD=... IQR=... pctl...` не содержит этот день.
4. Плашка НОРМА/ВНИМАНИЕ и стат-строка заполнены (не пустые).
5. Промо-маркеры (вертикальные линии) отрисованы так же, как у соседних графиков.
6. SKU без корзин за весь период → «Нет корзин за период».
7. SKU с CR > 100% (заказов больше, чем корзин за день) — столбец и label корректно уходят выше средней сетки.

- [ ] **Step 3: Если всё ок — финальный коммит с меткой**

Если предыдущие два коммита уже покрыли все изменения — дополнительный коммит не нужен, просто отметить шаг выполненным. Если в процессе визуальной проверки пришлось что-то править — отдельный коммит `fix(sku): визуальные правки графика CR корзина→заказ` с описанием фикса.

---

## Self-Review

Проверка плана против спеки:

1. **Размещение после «Конверсия в корзину по дням»** → Task 2, вставка вызова после строки 6685. ✓
2. **Одинаковая размерность графиков** → Task 1, Step 1: `width=920, height=140` идентично `buildCartConversionChart`; `bars.map` проходит по полной `points`-серии, просто для null-дней возвращает пустую строку (позиция на оси сохранена). ✓
3. **Фиолетовый цвет как у CR в корзину** → Task 1, Step 1: `barColor = "#a78bdb"`, `labelColor = "#5b3a99"` — те же значения, что в исходной функции. ✓
4. **Нули (cart==0) не попадают в статистику** → Task 1, Step 1: `statsSeries = convSeries.filter(v => v != null)` подаётся в `globalTsStatsLine`, `globalTsBollingerSvg`, `globalTsBollingerDots`. `avgConv` считается по `validValues` без null. ✓
5. **Бэкенд без изменений** → план не трогает Python-файлы. ✓
6. **Заглушка при полном отсутствии корзин** → Task 1, Step 1: проверка `anyCart` + `<div class="analytics-chart-empty">Нет корзин за период</div>`. ✓
7. **Промо-маркеры** → Task 1, Step 1: вызов `globalPromoEventsSvg` по полной серии `points`. ✓
8. **Сворачивание по кнопке** → Task 1, Step 1: `sku-collapse-btn` + `sku-collapsible` — классы те же, что у соседей. ✓

Плейсхолдеров `TBD`/`TODO`/«add error handling» в плане нет. Имена функций и переменных консистентны между тасками (`buildCartToOrderChart`, `cartToOrderChartId`).
