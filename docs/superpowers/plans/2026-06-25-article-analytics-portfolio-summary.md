# Article Analytics Portfolio Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a collapsible "All articles" diagnostics block above the article analytics summary cards.

**Architecture:** Reuse the existing article analytics row and chart builders by creating one synthetic portfolio item from the currently loaded rows. Sum additive metrics and compute ratios from summed numerators/denominators so the portfolio block behaves like a real SKU card.

**Tech Stack:** Single-file HTML/CSS/JavaScript dashboard in `web/orders_dashboard.html`.

---

### Task 1: Build Portfolio Item

**Files:**
- Modify: `web/orders_dashboard.html`

- [ ] **Step 1: Add an aggregation helper inside `renderArticleAnalytics`**

Create `buildPortfolioItem(sourceRows)` near the existing helper functions. It should aggregate daily arrays by day, sum 30d metrics, sum stock and traffic source metrics, compute CTR as `clicks_30d / impressions_30d * 100`, compute CR as `orders_30d / clicks_30d * 100`, and average only non-additive values like score, prices, rating, and position.

- [ ] **Step 2: Render the synthetic item before summary cards**

Use the existing row renderer path by extracting the row-template logic into `renderArticleAnalyticsCard(item, idx, options)`, then call it once for the portfolio item and once per real SKU.

- [ ] **Step 3: Avoid SKU-only lazy loads for the portfolio card**

For the portfolio card, hide SERP/query-matrix sections and skip lazy query loading when no real SKU exists.

- [ ] **Step 4: Verify**

Run `node --check` on the extracted script or an equivalent syntax check, then run the existing relevant JS tests if available.
