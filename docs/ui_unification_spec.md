# UI Unification Spec (Reports)

Date: 2026-05-30  
Scope: visual unification for report pages (no functional changes)

## 1) Pages In Scope

1. `web/orders_dashboard.html`
2. `web/finance_costs.html`
3. `web/palletization/index.html`

## 2) Baseline Audit Artifacts

1. `logs/visual_audit_2026-05-30/audit_results.json`
2. `logs/visual_audit_2026-05-30/orders_dashboard_*.png`
3. `logs/visual_audit_2026-05-30/finance_costs_*.png`
4. `logs/visual_audit_2026-05-30/palletization_*.png`

## 3) Unified Design Standard (Target)

### 3.1 Typography

1. Base font-family: `"Onest", Arial, sans-serif`
2. Body text: `14px`
3. Secondary/meta text: `12px`
4. Table text: `12px`
5. Numeric columns: tabular numbers (`font-variant-numeric: tabular-nums`)

### 3.2 Color Tokens

Use a shared token set (single source of truth):

1. `--bg: #f3f5f7`
2. `--panel: #ffffff`
3. `--line: #e3e8ef`
4. `--text: #24324a`
5. `--muted: #7e8ca3`
6. `--accent: #005bff`
7. `--accent-soft: #e9f0ff`
8. `--success: #1fa971`
9. `--warning: #ffb800`
10. `--danger: #ff5a36`

### 3.3 Buttons

1. Primary/secondary only (remove ad-hoc variants where possible)
2. Height: `40px`
3. Padding: `10px 12px`
4. Radius: `12px`
5. Font-size: `14px`
6. Disabled style must be consistent across pages

### 3.4 Tables

1. Header/body typography: `12px`
2. Unified cell paddings
3. Unified border and hover styling
4. Horizontal scroll must be explicit via wrapper container
5. Sticky header behavior should be consistent where used

### 3.5 Layout & Spacing

1. Spacing scale: `8/12/16/24`
2. Card radius: `12-16px`
3. Shared border/shadow style for cards and table wrappers

## 4) Current -> Target Matrix

## 4.1 Buttons

1. Current:
- `orders_dashboard`: multiple heights (`21/28/30/39/40`), mixed radius values (`999/12/6/...`).
- `finance_costs`: `32-35px`, radius `8px`.
- `palletization`: `39-43px`, mobile outlier `74px`.
2. Target:
- Unified `40px` height, `12px` radius, 2 semantic variants (primary/secondary).
3. Where to change:
- `web/orders_dashboard.html`
- `web/finance_costs.html`
- `web/palletization/index.html`

## 4.2 Tables

1. Current:
- `orders_dashboard`: rich tables, `12px`.
- `finance_costs`: simple table, `13px`.
- `palletization`: table styles differ, with page-specific palette and spacing.
2. Target:
- One table foundation style, `12px`, shared paddings, wrapper behavior.
3. Where to change:
- `web/orders_dashboard.html`
- `web/finance_costs.html`
- `web/palletization/index.html`

## 4.3 Visibility/Overlaps

1. Current:
- `orders_dashboard` has many overlay/sticky candidates (higher overlap risk).
- Horizontal overflow observed on `orders_dashboard` (1024x768) and `finance_costs` (390x844).
2. Target:
- No accidental clipping/overlap in desktop/tablet/mobile.
- Explicit overflow wrappers for wide content.
3. Where to change:
- `web/orders_dashboard.html` (overflow-heavy sections)
- `web/finance_costs.html` (mobile overflow)

## 4.4 Fonts & Colors

1. Current:
- `orders_dashboard`: `Onest`, cool neutral palette.
- `finance_costs`: `Arial`, warm palette.
- `palletization`: `Segoe UI`, separate palette.
2. Target:
- One typography stack and token palette across all report pages.
3. Where to change:
- `web/finance_costs.html`
- `web/palletization/index.html`
- (minor cleanup) `web/orders_dashboard.html`

## 5) Implementation Plan (No Behavior Change)

1. Create `web/shared-report-theme.css`:
- tokens
- typography
- buttons
- forms
- tables
- card/container primitives

2. Connect shared stylesheet to all 3 pages.

3. Refactor `finance_costs.html`:
- remove page-local palette and typography
- adopt shared button/table/card classes
- fix mobile horizontal overflow

4. Refactor `palletization/index.html`:
- map `.btn`, `.card`, `.form-control`, `table` to shared standards
- keep functional class names, unify visual tokens/sizes
- normalize mobile button height

5. Refactor `orders_dashboard.html`:
- reduce local button variants to shared semantic classes
- replace critical inline button styles in JS-rendered fragments with classes
- fix 1024x768 overflow hotspots

6. Re-run visual regression and compare against baseline screenshots.

## 6) QA Acceptance Checklist

Mark each as Pass/Fail with screenshot evidence.

1. Buttons:
- same height, radius, typography, hover, disabled across pages

2. Tables:
- same header/body typography and spacing
- no accidental style drift between pages

3. Visibility:
- no overlapping actionable controls
- no clipped text or hidden controls

4. Responsive:
- no horizontal overflow on key layouts at `390x844`, `1024x768`, `1440x900`

5. Typography/Colors:
- same base font-family and token palette in all pages

## 7) Risks

1. `orders_dashboard.html` contains many inline styles generated in JS; visual standardization may require class extraction first.
2. `palletization` currently uses a distinct visual language; full convergence may need phased rollout to reduce UI shock.
3. Existing ad-hoc button styles may encode semantic meaning by color; mapping must preserve meaning while unifying visuals.

## 8) Rollout Strategy

1. Phase 1: shared tokens + typography only (lowest risk)
2. Phase 2: buttons and forms
3. Phase 3: tables and overflow clean-up
4. Phase 4: final visual regression and sign-off

