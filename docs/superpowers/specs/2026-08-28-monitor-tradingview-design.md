# Monitor — TradingView-lite Bucket Monitor Design

**Date:** 2026-08-28  
**Status:** Approved (4 sections, Approach C hybrid, Monitor as default)  
**Layout:** Card A — Top Bucket Strip + Central Chart (dark #131722)  
**Detail:** Option C (trader + analyst) in Right Drawer (420px)  
**Bucket Switch:** Bucket (`main` 3/2) / Pool (`pooled`) / ETF_trend (`data/etf_trend/paper_state.json`)

---

## 1. Architecture & Routing

Single React app (`dashboard/src/App.tsx:213`), no new repo. Keep 6 report sections (`#overview`, `#approach`, `#evidence`, `#lessons`, `#book`, `#gates`) unchanged for `src/ops/overview.py:1` and `src/ops/audit.py:1` audit (38 passed). Add new hash route `#monitor` rendering a dark TradingView shell isolated via nested `ThemeProvider` (`background #131722`, `text #D1D4DC`, `border #2A2E39`). On load with empty hash or `#`, `useEffect` redirects `window.location.hash = '#monitor'` — Monitor is the landing page, report pages remain reachable via Drawer. No new build entry: `scripts/build_dashboard.sh:1` stays `dashboard_export.py` → `vite build` → `dashboard/dist/index.html:1`. Monitor code-splits: `const Monitor = lazy(() => import('./pages/Monitor'))` so report pages never load `lightweight-charts` (~120k gzip). Bucket switch is React state `book: 'main'|'pooled'|'etf_trend'` driven by header `<Select>`; it swaps which 5 rows feed the left rail and which detail source is used.

**Constraints carried from `CLAUDE.md`:**
- No live intraday — `daily.py` snapshot is live; Monitor is read-only, point-in-time `visible_from <= as_of`, `year_end >= as_of-550d` (`src/core/company_data.py:10` `FRESHNESS_DAYS`).
- Free/low-cost only — no paid API keys; data via `src/ops/dashboard_api.py:16` over Gold (`data/gold/*.parquet` 28991 fundamentals, 1276 sectors, 2.8M prices).
- Risk invariants in `src/strategies/breakout/*` never searched; Monitor does not touch `engine.py:1`.

---

## 2. Components & Layout (Card A)

```
Header (56px): [Bucket Switch v]  [NSE: NATCAPSUQ · micro · breakout · 1D  212.5 +1.2%]  [Watchlist toggle] [Heatmap toggle] [as_of date]
Body (flex:1):
  BucketRail (220px) | ChartPane (flex:1) | RightPanel (280px)
FundamentalsStrip (48px): Revenue 2407Cr (FY24) · OCF 80Cr · Margin 12% · Sector Commodities · visible_from 2024-05-17
DetailDrawer (420px, anchor="right", on bucket row click)
```

**BucketRail:** Vertical 5 rows for active book. Row: symbol, cluster chip (`micro`/`small`/`index`), `P&L%` vs `entry_px`, `status` (open/pending/closed), `queued_on`. Selected `background #2962FF`. Data: `GET /journal?limit=50` filtered `WHERE bucket=:book` (or `GET /etf_trend` for `etf_trend`), plus `GET /watchlist` for count badge. Click → `setSelected(symbol)`.

**ChartPane:** `lightweight-charts` `CandlestickSeries` with OHLCV from `company_data.get(symbol, as_of).prices` (`day <= as_of`, daily). Overlays: `PriceLine` entry (solid white), stop (red dashed `98.74`), target (green dashed `256.99`), hold window shading. Bottom `HistogramSeries` volume. Left toolbar placeholder (crosshair, zoom) — no drawings in v1.

**RightPanel:** Stacked `WatchlistPanel` (scroll 1276 symbols from `GET /watchlist`, filter input, highlight bucket sector) + `HeatmapPanel` (grid `sectors: {Commodities 164,...}` from `GET /sector/heatmap`, click filters watchlist by `by_sector[sector]`).

**FundamentalsStrip:** Single line below chart: `Revenue · OCF · Margin · Sector · visible_from · year_end` from `company_data.get(...).fundamentals` + `screener` + `sector`. Shows `No fundamentals (stale >550d)` if null.

**DetailDrawer — Right Drawer, Option C:**

- *Trader's* (top half): entry_px/qty/stop/target/hold days left, net/P&L, `exit_reason`, hold countdown, journal table (all `journal` rows for symbol).
- *Analyst's* (bottom half): fundamentals table (all `timeline` fields for that `as_of`), `screener` OCF, `sector`, `announcements` last 5 from `bse_announcements.timeline`, `score/rank` if available.

**Theme:** Dark `#131722` shell, `#1E222D` panels, `#2962FF` selection, `#26A69A`/`#EF5350` for target/stop. Report pages keep MUI light theme — themes are nested, not global.

---

## 3. Data Flow & API (no future leak)

**Mount on `book` change:**
1. `Promise.all([GET /watchlist, GET /sector/heatmap, GET /journal?limit=50, GET /etf_trend? if etf_trend])` — warm `<100ms` (19ms loop at `src/core/company_data.py:77` after first `load_corpus`; heatmap/watchlist hit `sectors.json` directly).
2. Derive bucket rows: `journal` filtered `bucket=:book` OR `paper_state.json:1` `positions+queue` for `etf_trend` → 5 symbols. Set `selected = bucket[0].symbol` if none.
3. Fetch `GET /company/{selected}?as_of={as_of}` where `as_of = selectedRow.entry_day ?? today` (ISO). `dashboard_api.py:21` `company()` → `company_data.get(symbol, as_of=date.fromisoformat)` → `src/core/company_data.py:49` `_latest_visible` (`visible_from <= as_of_s` + `year_end >= cutoff`) and `_prices` (`day <= as_of`). Guarantees point-in-time, 550d freshness, same as backtest.

**Interactions:**
- BucketRail click → `setSelected(sym)` → refetch `GET /company/{sym}?as_of={row.entry_day}` + open `DetailDrawer` (drawer reuses same `GET /company` payload — `{prices,fundamentals,screener,sector,announcements,journal}`).
- Bucket Switch → re-derive bucket rows, reset `selected` to new book's top, refetch.
- Watchlist row click → same as bucket click (any of 1276).
- Heatmap cell → filter watchlist locally via `by_sector[sector]`, no fetch.

**New endpoint:** `GET /etf_trend` in `dashboard_api.py:47` — reads `data/etf_trend/paper_state.json:1` + `paper_trades.jsonl:1`, returns `{positions, queue, last_day}`. No Gold table; ETFs excluded from Gold at `src/core/universe.py:153` deliberately.

**Error/empty states:** `fundamentals: null` → strip shows stale message; `prices: null` → chart shows `No price history`; `journal []` → `Bucket empty`; fetch fail → `ErrorBoundary` + retry. No polling — daily snapshot is live.

---

## 4. Testing, Rollout & Guardrails

**Unit:**
- `src/core/company_data.py --selftest` already covers `visible_from`/`FRESHNESS_DAYS` + new `get_universe`/`get_sector_heatmap`/`get_journal` (1276). Add `dashboard_api.py --selftest` case for `GET /etf_trend` shape.
- `lightweight-charts` wrapper: `dashboard/src/lib/chart.test.tsx` mock canvas, assert `setData` with OHLCV.

**Integration:**
- `validate_gold` 0, `validate_security` ok, `build_gold` 28991 rows — Monitor consumes Gold only; no pipeline change. `DetailDrawer` property test: `fundamentals.visible_from <= as_of` (not hardcoded date, avoids `newswatch:579` stale bug).

**e2e (Playwright, 9 passed at 24947216):** Add `dashboard/e2e/monitor.spec.ts` 4 specs:
1. `monitor is default` — `goto('/')` → `hash == '#monitor'`
2. `bucket switch changes rail` — select `Pool` → rail 5 pooled ≠ Bucket
3. `click bucket row opens drawer trader+analyst` — click `NATCAPSUQ` → drawer has `Entry`, `Revenue`, `Sector`
4. `chart mounts height >200` — `canvas` in `ChartPane` visible, no `consoleErrors` filter. `ETF_trend` shows `PHARMABEES`.

**Perf:** `GET /company` warm 19ms (`<100ms`); `GET /snapshot` 20s cold is **not** on Monitor path — Monitor never calls it. Snapshot stays for report pages.

**Rollout:**
- Code-split `Monitor` lazy, dark theme nested — report pages unaffected. `scripts/build_dashboard.sh:1` unchanged.
- Default: `App.tsx:217` `if (!hash) hash='#monitor'`; deep-links `#overview` etc still work.
- Guardrail: `validate_gold` + `validate_security` gate CI; Monitor read-only, no `engine.py:1` change per `CLAUDE.md`.

---

## 5. Files & Interfaces

- Create: `dashboard/src/pages/Monitor.tsx:1-350`, `dashboard/src/components/ChartPane.tsx:1-120` (`lightweight-charts`), `dashboard/src/components/BucketRail.tsx`, `RightPanel.tsx`, `FundamentalsStrip.tsx`, `DetailDrawer.tsx`
- Modify: `dashboard/src/App.tsx:213` (hash default + Monitor route + ThemeProvider swap + Bucket Switch header), `src/ops/dashboard_api.py:47` (add `GET /etf_trend`), `dashboard/vite.config.ts:1` (add `/snapshot` proxy for dev `→ http://127.0.0.1:8000` or keep fallback)
- Consumes: Gold `data/gold/*.parquet` via `dashboard_api` + `company_data`, `data/positions.db:1` (`pos` 13 rows), `data/etf_trend/paper_state.json:1`, `data/sectors.json:1` (1276), `data/screener_raw:1` via `company_data`
- Produces: Monitor at `/#monitor` (default), `dashboard/dist/index.html:1` + `dashboard/dist/assets/*`

---

## 6. Non-Goals (v1)

Live intraday candles, drawings persistence, symbol search beyond 1276 watchlist, order entry, indicator studio (RSI/MA), drawings/annotations save, mobile drawer polish — all deferred; `lightweight-charts` with daily OHLCV + stop/target is the v1 chart.

---

## 7. Visual Reference

Card A mockup at `http://localhost:52265/?key=0c00e9cd0a27f36cdb4c2753af89d2ed02e8ad81db7293e7b0c706749e603c4c` `bucket-monitor-layout.html:1` — dark header, left bucket rail (5), center candles+volume+stop/target, right watchlist+heatmap, bottom fundamentals, right drawer on click.
