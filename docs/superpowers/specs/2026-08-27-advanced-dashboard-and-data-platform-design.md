# Advanced Trading Dashboard & Data Platform — TradingView without Live Charts

**Date:** 2026-08-27
**Status:** Draft — pending writing-plans
**Context:** User wants a TradingView-class dashboard (every pane except live intraday charts) with fast access and zero dark data. Builds on L79/L80 (NSE XBRL quarterly 0% CF, annual undatable), Screener pilot 447/500 → full 1276 micro/small at 1 req/s (now 1268 raw / 1236 parsed), and the logical-view request (all company info in one place). Previous spec `2026-08-26-screener-fundamentals-design.md` covered Screener fetch; this spec covers the unified data platform and dashboard that makes it usable.

## 1. Goals

- One logical view per company: `data/companies/{symbol}.json` manifest + `src/core/company_data.py:get(symbol, as_of)` returns prices, fundamentals (NSE + Screener), sector, announcements, and journal slices as-of a date — human `ls` and code share one call.
- TradingView without live charts: watchlist, price history + indicators (RSI, SMA, volume), fundamentals pane (P&L/BS/CF YoY), sector heatmap, trade journal + bucket P&L, alerts — all from Gold, not raw.
- Fast: dashboard queries <100ms via DuckDB over Parquet, not 1.6 GB bulk `load_corpus()`.
- No dark data: every raw file has a Silver row and every Silver row is reachable from Gold and from the dashboard — verified every night and in CI.

## 2. Non-Goals

- No live intraday charts / websockets — daily `snapshot.py` is the "live" for this 10-day hold book.
- No paid vendor (Screener free pilot stays free, top-500 → 1,276).
- No physical per-company folder copy (duplicates corpus, breaks `load_corpus()`).

## 3. Architecture

```
Bronze (raw, immutable, never parsed in place)
  data/raw/{date}/ (bhavcopy, delivery, ASM/GSM) + manifest.json
  data/fundamentals/xbrl*/ + data/screener_raw/ + data/announcements/bse/raw/
      │
      ▼
Silver (normalized, as-of, visible_from + year_end)
  data/fundamentals/parsed/ + parsed_annual/ + data/fundamentals_screener/parsed/
  data/sectors.json (12 broad) + sectors_detailed.json
      │
      ▼  nightly build_gold.py — compacts Silver → Gold, validates
Gold (query-optimized, bitemporal)
  data/gold/prices.parquet (partitioned by year_end) + fundamentals.parquet + sectors.parquet + journal.parquet
  DuckDB: SELECT * FROM fundamentals WHERE symbol='RELIANCE' AND visible_from <= '2024-05-17'
      │
      ├─► src/core/company_data.py:get(symbol, as_of) ──► human ls + research + dashboard (one API)
      └─► src/ops/dashboard_api.py (FastAPI) ──► dashboard/ React + Lightweight-Charts (TradingView's lib)
```

Existing `fundamentals.py:43` WANTED, `parse_instants()`, `pick_fy_span()`, `screener_fundamentals.py` untouched.

## 4. Components

### 4.1 Logical View — `src/core/company_data.py` + `data/companies/{symbol}.json`
- Index is a manifest, not a copy: `{"prices": "data/raw/...", "fundamentals": "data/fundamentals/parsed/RELIANCE.json", "screener": "data/fundamentals_screener/parsed/RELIANCE.json", "sector": "Commodities", "announcements": "data/announcements/bse_parsed/RELIANCE.jsonl", "journal": "positions.db:RELIANCE"}`
- `get(symbol, as_of=None)` — if `as_of` is None returns latest; else filters each slice by `visible_from <= as_of` (bitemporal) and `year_end >= as_of - 550d` for annual. Missing slice → `None`, never imputed.
- One-time indexer `src/ops/company_backfill.py` walks `features.load_corpus()` + `sectors.json` + both parsed trees and writes 1,276 manifests.

### 4.2 Gold Builder — `src/ops/build_gold.py`
- Reads Silver (all parsed JSON + `sectors.json` + `positions.db` journal), writes Parquet partitioned by `year_end` year, plus DuckDB views.
- Price gold: `prices.parquet` from `features.load_corpus()` (adjusted closes, corporate actions already in `features.py`).
- Fundamentals gold: `fundamentals.parquet` from `fundamentals.timeline()` + `timeline_annual_screener()` merged, with `visible_from`, `year_end`, `revenue`, `net_profit`, `ocf`, `total_assets`, `sector`.

### 4.3 Dashboard API — `src/ops/dashboard_api.py`
- FastAPI, read-only over Gold (DuckDB) + journal. Endpoints: `/company/{symbol}?as_of=`, `/watchlist`, `/sector/heatmap`, `/journal`, `/bucket`.
- No direct raw reads — if Gold is stale, API returns `503 Gold behind raw` (from manifest lag).

### 4.4 Dashboard — `dashboard/` React + Lightweight-Charts
- Reuses `scripts/build_dashboard.sh` + `src/ops/dashboard_export.py` build, but data source switches from `load_corpus()` bulk to `dashboard_api.py`.

## 5. Data Flow

1. Daily `snapshot.py` 18:00 → `data/raw/{date}/` + `manifest.json`
2. `fundamentals` backfills + `screener_backfill` (1 req/s) → Bronze
3. Parsers → Silver (`parsed/`, `parsed_annual_screener/`)
4. `sector_backfill` → `data/sectors.json`
5. Nightly `build_gold.py` → Gold Parquet + DuckDB + `data/companies/*.json` manifests
6. `dashboard_api.py` serves Gold; `company_data.py` is the shared library for research and dashboard

## 6. Data Security & No-Dark-Data Validation — Always-On Step

This step runs **every night after `build_gold.py` and on every CI push**, and fails the build on violation. It is not an afterthought; it is the gate that makes "no data untouched" and "secure" real.

### 6.1 Security Validation (`src/ops/validate_security.py`)

- **Access control:** Gold Parquet and `data/companies/*.json` are read-only for dashboard/research; raw Bronze is write-once (no overwrite without `force`). No secrets in Gold.
- **Audit trail:** every Gold row carries `source_file` + `source_visible_from` + `built_at` (from Silver manifest) — lineage from Bronze bytes to dashboard tile is one query.
- **PII / secrets scan:** no PAN, email, or Upstox tokens in Gold (regex scan; Upstox master stays in `data/upstox_instruments.json` which is already gitignored).
- **Integrity:** SHA256 of each Bronze file stored in `manifest.json` (like `snapshot.py`); Gold build verifies SHA before compacting, fails on mismatch.

### 6.2 No-Dark-Data Validation (`src/ops/validate_gold.py`)

Invariants, all point-in-time:

- **Bronze → Silver:** every `screener_raw/*.html` >10KB has a `parsed_annual_screener/*.json` with ≥1 row containing `ocf` or `revenue`; every `xbrl/*.xml` has a `parsed/*.json` row for its `visible_from`. Missing → fail.
- **Silver → Gold:** every Silver row is reachable from `fundamentals.parquet` or `sectors.parquet` and from `data/companies/{symbol}.json` manifest. Orphan → fail.
- **Gold → Dashboard:** every Gold table has a dashboard tile querying it (watchlist, price, fundamentals pane, sector heatmap, journal). Unqueried table → fail (dark data).
- **Coverage:** `screener_raw` 1278 → `parsed` 1236 → Gold 1236; `sectors` 1276/1276; `announcements` 770k rows reachable via `company_data.get()`.

Both validators run as `python3 src/ops/validate_security.py && python3 src/ops/validate_gold.py` in CI and as the last step of `build_gold.py`. A failed validation blocks the dashboard deploy and the next research run.

## 7. Error Handling

- **Missing slice:** `company_data.get()` returns `None` for that pane, never a stale fallback.
- **Stale Gold:** `dashboard_api` returns 503 with `lag_hours` from manifest; dashboard shows "Gold behind raw" banner.
- **Screener HTML change / IP ban:** `screener_fundamentals` 429/5xx retry + 1 req/s (as piloted 447 → 1278), `validate_gold` fails fast with "Bronze without Silver" before any backtest reads it.
- **Sector missing:** `sector_backfill` 5/5 live uncached proved 0 missing Broad Sector on 1,238 measured; missing → `sector=None`, not imputed.

## 8. Testing

- `src/core/company_data.py --selftest`: synthetic symbol, `as_of` before/after `visible_from`, freshness window 550d, missing slice → `None`.
- `src/ops/build_gold.py --selftest`: fixture Silver → Gold round-trip, manifest SHA check.
- `src/ops/validate_gold.py --selftest`: synthetic Bronze without Silver → fail, orphan Silver → fail.
- `tests/run_selftests.py` discovers all three; `validate_*` also runs in CI.

## 9. Risks & Mitigations

- **Screener TOS / IP ban:** read-only, 1 req/s single-worker, resumable raw cache; full 1,276 already proven 1278 raw.
- **HTML fragility:** parser pinned to `profit-loss`/`balance-sheet`/`cash-flow` IDs + `data-date-key`, fixture from 2026-08-26; selftest fails on redesign before Gold builds.
- **Gold size:** Parquet partitioned, DuckDB, not bulk JSON — <100ms dashboard queries vs 1.6 GB bulk.

## 10. Rollout

1. `company_backfill` indexer → 1,276 manifests (seconds, offline).
2. `build_gold.py` → Gold Parquet + DuckDB (minutes, offline) + `validate_gold` + `validate_security` gates.
3. `dashboard_api.py` + dashboard build → TradingView-lite (no live charts) reading Gold only.
4. Backfill Screener remaining 55 + full 1,276 already done (1278 raw); no further fetch.

## 11. Alternatives Considered

- **Physical per-company folders** (`data/companies/RELIANCE/{prices.csv, ...}`): rejected — duplicates corpus, breaks `load_corpus()`.
- **Yahoo Finance via yfinance:** rejected — ~60% micro/small cover, no `visible_from`, lookahead bias.

---

*Isolation:* quarterly `parsed/` untouched, `features_asof()` unchanged, `pick_fy_span()` from L79 stays. New Gold and `company_data` are additive and gated.
