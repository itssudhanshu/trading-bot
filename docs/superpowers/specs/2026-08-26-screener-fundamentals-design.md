# Screener Fundamentals v2 — Free Vendor Cash-Flow Source

**Date:** 2026-08-26
**Status:** Draft — pending writing-plans
**Context:** L79/L80 closed the NSE XBRL track: quarterly 0% cash-flow, annual 0/250 FY-dated OCF (RELIANCE FY24 exemplar: FY value in Q4 context). `accrual_spread_test.py` gated `SOURCE UNUSABLE (L80)`. Operator confirmed free/low-cost preference, top-500 pilot then full 1,276 micro/small.

## 1. Goals

- Pull **all fields** Screener exposes (P&L, Balance Sheet, Cash Flow, annual + quarterly where trivial) for top-500 by turnover, point-in-time dated via result announcement date, into a local resumable cache.
- Enable a pre-registered accruals study ` (NP-OCF)/Revenue` and ` (NP-OCF)/Assets` on the same L78 protocol (offsets 0..5, demeaned within cohort, floor 300, t>2) without touching `data/fundamentals/parsed/` row arithmetic.
- Pilot proves coverage and dating; full rollout is same code over 1,276 symbols.

## 2. Non-Goals

- Not replacing NSE quarterly pipeline (96%/91% coverage, broadcast-dated) — quarterly stays authoritative for revenue/profit.
- Not a paid vendor integration (Tijori/CMIE) — free scrape only for this pilot.
- Not real-time intraday — batch harvest, same cadence as `fundamentals.backfill`.

## 3. Architecture

```
top-500 by turnover ──► fetcher ──► screener_raw/{symbol}.html ──► parser ──► parsed_annual_screener/{symbol}.json ──► timeline_annual_screener() ──► accrual_at_screener() ──► research
       │                    │                    │                              │
  features.load_corpus  2 req/s, UA+Ref    BeautifulSoup, pinned fixture   sorted by visible_from, 550d window
  clusters.size_clusters resumable, 3 retries  FY columns only              isolated from quarterly parsed/
```

Existing `fundamentals.py:43` WANTED and `parse_instants()` untouched. New code lives in `src/core/screener_fundamentals.py` mirroring `fundamentals.py` API: `RAW_SCREENER`, `PARSED_SCREENER`, `fetch_screener()`, `parse_screener()`, `build_parsed_screener()`, `timeline_annual_screener()`.

## 4. Components

### 4.1 Fetcher — `fetch_screener(symbol, force=False)`
- URL: `https://www.screener.in/company/{SYMBOL}/consolidated/` (fallback to `/` if consolidated 404)
- Headers: UA `Mozilla/5.0 Chrome/120`, Referer `https://www.screener.in/`, Accept `*/*`
- Rate: 2 req/s token bucket, jitter 200ms, 3 retries on 429/5xx with exponential backoff (2s, 5s, 10s), honors `Retry-After`
- Resumable: skip if `screener_raw/{symbol}.html` exists and >10KB and <30 days old unless `force`
- Never raises: returns `(status, body)` like `src/ops/snapshot.py:45` `fetch()`

### 4.2 Parser — `parse_screener(html_bytes) -> list[dict]`
- BeautifulSoup4 on `lxml`, selects `#profit-loss`, `#balance-sheet`, `#cash-flow` tables; annual columns only (header `Mar 2024`, `Mar 2023`...)
- Result date: header `Ann. Date` column or `data-announcement` attribute per row — normalized to ISO `YYYY-MM-DD`, becomes `visible_from`
- `year_end`: `Mar 31 YYYY` of column header → ISO
- Number norm: `₹ 1,587.88 Cr` → `15878800000.0` (strip `₹`, commas, `Cr` *1e7, `Lac` *1e5, `—` → None)
- Fields: `revenue` (Sales), `net_profit` (Net Profit), `ocf` (Cash from Operating Activity), `total_assets` (Total Assets), plus `expenses`, `other_income`, `finance_cost` where present — all optional
- First-value-wins per `(year_end, field)` like `fundamentals.parse_xbrl()`

### 4.3 Cache — `data/screener_raw/` + `data/fundamentals_screener/parsed/`
- Raw: `data/screener_raw/{symbol}.html` (verbatim, never parsed in place)
- Parsed: `data/fundamentals_screener/parsed/{symbol}.json` → `[{visible_from, year_end, revenue, net_profit, ocf, total_assets, ...}]` sorted by `visible_from`
- Separate from `data/fundamentals/parsed/` and `data/fundamentals/parsed_annual/` — quarterly `features_asof()` row counts never see these rows

### 4.4 Feature Hook — `accrual_at_screener(sym, corpus, entry_day)`
- Mirrors `accrual_spread_test.py:1` `accrual_at()` but reads `timeline_annual_screener(sym)`
- Latest `visible_from <= signal_day` (signal = `s.days[ie-1]`), `year_end >= signal - 550d` (frozen window from L80)
- Requires `revenue>0`, `net_profit` and `ocf` present → else `None` (trade not in sample, reported as `no-visible-annual` counts)
- Returns both scalings: `accr_rev = (NP-OCF)/Revenue*100`, `accr_assets = (NP-OCF)/Assets*100` if assets present

## 5. Data Flow (pilot)

1. Symbol list: `features.load_corpus()` → `clusters.size_clusters(as_of=latest, names=(micro,small))` → rank by turnover → top-500
2. `screener_fundamentals.backfill_screener(symbols, workers=4)` fetches 500 HTML (≈4 min at 2 req/s)
3. `build_parsed_screener()` parses each HTML → JSON per symbol (offline, <30s for 500)
4. Research reads `timeline_annual_screener()` — no network
5. Harvest experiment: offsets 0..5, 6 cohorts, `demeaned within cohort`, floor 300, slopes + tercile gaps, per-cluster/per-block

## 6. Data Contract

```json
{
  "visible_from": "2024-05-17",
  "year_end": "2024-03-31",
  "revenue": 2407150000000.0,
  "net_profit": 212430000000.0,
  "ocf": 1587880000000.0,
  "total_assets": 8329450000000.0
}
```
- `visible_from`: result announcement date from Screener, ISO, point-in-time gate `visible_from <= signal_day`
- `year_end`: column header date, ISO, freshness check `year_end >= signal - 550d`
- All money in INR (float), `None` where Screener shows `—`
- Sorted by `visible_from` ascending

## 7. Error Handling

- **Rate limit 429 / 5xx:** retry with backoff, log `fail` count, resume on next run (existing raw kept)
- **404 / delisted:** count as `no_screener_page`, trade simply not in accrual sample
- **HTML structure change:** parser returns `[]` for that symbol, `build` logs `parse_fail`; pinned fixture in `_selftest` catches drift within one sweep run — `tests/run_selftests.py` will fail that module, not silently produce empty timelines
- **Missing fields per year:** row written only if `ocf` present (accruals gate), other fields optional; research reports `joined` vs `no-visible-annual` per cohort like `quality_spread_test.py`
- **Anti-bot block (Cloudflare):** detect `cf-challenge` / `Just a moment` in body → treat as 429, backoff, log `blocked`

## 8. Testing

- `_selftest` offline, no network: (a) pinned HTML fixture from 2026-08-26 (RELIANCE consolidated page, truncated) → assert exact `visible_from`, `year_end`, `ocf`, `total_assets` values; (b) number norm `₹ 1,587.88 Cr` → `15878800000`; (c) `fetch` retry on 429 via injected fetcher; (d) `timeline_annual_screener` round-trip sort order; (e) `accrual_at_screener` visibility/freshness/zero-revenue cases (mirrors `accrual_spread_test.py:180`)
- Coverage check in `main()`: prints `n with screener timeline / top-500` and per-field hit rates (OCF, assets) before any spread
- Audit: `src/ops/audit.py` baseline drift still 0 (this cache is not read by `overview.py` until research opts in)

## 9. Risks & Mitigations

- **TOS / IP block:** read-only, low rate, UA+Referer, no auth bypass; pilot 500 not 2,404; raw cache avoids re-hits
- **HTML fragility:** parser pinned to table IDs + header text, fixture guards; if Screener redesigns, selftest fails fast before any backtest
- **Point-in-time fidelity:** Screener's announcement date is the best available `visible_from`; we log its lag vs `year_end` (median) to document the same 42-day lag honesty as quarterly

## 10. Rollout

- **Pilot:** 500 symbols, `workers=4`, ~4 min fetch + <1 min parse, verify `timeline_annual_screener` hit rate >70% with OCF, then run `accrual_spread_test_screener.py` (same protocol as `quality_spread_test.py`) — no adoption, just description
- **Full:** same code over 1,276 micro/small tradeable, `src/research/backfill_annual.py` pattern reused, `data/fundamentals_screener/` gitignored (~50 MB raw + ~2 MB parsed)

## 11. Alternatives Considered

- **Yahoo Finance via yfinance:** rejected — ~60% Indian micro cover, no `visible_from`, lookahead bias
- **Hybrid NSE quarterly + Screener OCF:** rejected for pilot — two clocks to reconcile; kept as fallback if Screener revenue proves spotty

## 12. Open Questions — None for pilot

Revenue-scaled accruals primary, assets-scaled secondary if assets present — both reported, no extra decision.

---

*Isolation:* quarterly `parsed/` untouched, `features_asof()` unchanged, `pick_fy_span()` and `parse_instants()` from L79 remain. Screener is a parallel, gated source until its own spreads earn a rule-shape follow-up.
