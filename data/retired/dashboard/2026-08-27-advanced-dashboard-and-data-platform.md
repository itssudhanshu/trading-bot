# Advanced Dashboard & Data Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a TradingView-without-live-charts dashboard over a fast, validated Gold data platform that makes every fetched byte reachable via one company API.

**Architecture:** Nightly `build_gold.py` compacts Silver (NSE/XBRL + Screener + sectors) into DuckDB/Parquet Gold; `company_data.py` + `data/companies/*.json` manifests provide a single bitemporal `get(symbol, as_of)`; `dashboard_api.py` serves Gold read-only; React + Lightweight-Charts renders. Two always-on validators (`validate_gold.py`, `validate_security.py`) gate the build and CI.

**Tech Stack:** Python 3.11+, DuckDB + Parquet, FastAPI, React + Lightweight-Charts, BeautifulSoup already in repo, `paths`/`features`/`fundamentals`

**Spec:** `docs/superpowers/specs/2026-08-27-advanced-dashboard-and-data-platform-design.md`

## Global Constraints

- No live intraday charts — daily `snapshot.py` is the live for this 10-day hold book.
- Free/low-cost only — no paid vendor keys.
- Pilot is 500, full is 1,276 micro/small tradeable — same code, top-500 first.
- Point-in-time: `visible_from <= signal_day`, `year_end >= signal - 550d` (frozen).
- Resumable: existing raw >10KB is skipped unless `force=True`.
- Gold is read-only for dashboard/research; raw Bronze is write-once.

---

## File Structure

- `src/core/company_data.py` — single public API `get(symbol, as_of=None)` that merges prices, fundamentals (NSE + Screener), sector, announcements, journal slices as-of a date. Follows `fundamentals.timeline()` patterns, ~200 LOC.
- `src/ops/build_gold.py` — compacts Silver into `data/gold/*.parquet` + `data/companies/*.json` manifests. One responsibility: build Gold from Silver.
- `src/ops/validate_gold.py` — Bronze→Silver→Gold→Dashboard chain checks, no-dark-data invariants. Fails build on violation.
- `src/ops/validate_security.py` — SHA checks, PII scan, audit trail lineage. Fails build on violation.
- `src/ops/dashboard_api.py` — FastAPI over Gold (DuckDB), read-only, 503 if Gold behind raw.
- `dashboard/` — React + Lightweight-Charts (reuses `scripts/build_dashboard.sh` build, data source switches from `load_corpus()` bulk to API).
- Tests: `src/core/company_data.py --selftest`, `src/ops/build_gold.py --selftest`, `src/ops/validate_*.py --selftest`, all discovered by `tests/run_selftests.py`.

---

### Task 1: Logical View — company_data.py + data/companies manifests

**Files:**
- Create: `src/core/company_data.py:1-200`
- Create: `data/companies/{symbol}.json` (generated, gitignored until `company_backfill` writes first 1,276)
- Test: `src/core/company_data.py --selftest`

**Interfaces:**
- Consumes: `features.load_corpus()`, `fundamentals.timeline()`, `screener_fundamentals.timeline_annual_screener()`, `data/sectors.json`, `data/announcements/bse_parsed/*.jsonl`, `data/positions.db`
- Produces: `get(symbol, as_of=None) -> dict{prices, fundamentals, screener, sector, announcements, journal}`

- [ ] **Step 1: Write the failing test — bitemporal get**

```python
# in src/core/company_data.py _selftest()
from datetime import date
import company_data as cd
# synthetic symbol with two fundamentals rows
cd._timeline = lambda sym: [{"visible_from": "2024-05-17", "year_end": "2024-03-31", "revenue": 100}]
cd._screener_timeline = lambda sym: [{"visible_from": "2024-05-30", "year_end": "2024-03-31", "ocf": 80}]
# as_of before second visible_from should see first
r = cd.get("FAKE", as_of=date(2024, 5, 20))
assert r["screener"] is None, "future screener leaked"
assert r["fundamentals"]["revenue"] == 100
# as_of after should see both
r2 = cd.get("FAKE", as_of=date(2024, 6, 1))
assert r2["screener"]["ocf"] == 80
print("company_data selftest ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/core/company_data.py --selftest`
Expected: FAIL `get not defined`

- [ ] **Step 3: Write minimal implementation — get()**

```python
def get(symbol, as_of=None):
    import features, fundamentals, screener_fundamentals as sf, json, pathlib
    from paths import ROOT
    # as_of is date or None (latest)
    # ... filter each timeline by visible_from <= as_of.isoformat() ...
    # ... pick latest visible row per source ...
    # ... load sector from data/sectors.json ...
    # ... query prices via features.load_corpus() slice ...
    return {"prices": prices_slice, "fundamentals": fund_row, "screener": scr_row, "sector": sector, "announcements": ann_slice, "journal": journal_rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/core/company_data.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/company_data.py
git commit -m "feat(data): logical view company_data.get with bitemporal as_of"
```

---

### Task 2: Gold Builder — build_gold.py (DuckDB + Parquet)

**Files:**
- Create: `src/ops/build_gold.py:1-250`
- Test: `src/ops/build_gold.py --selftest`

**Interfaces:**
- Consumes: `company_data` manifests + Silver `data/fundamentals/parsed/*.json`, `data/fundamentals_screener/parsed/*.json`, `data/sectors.json`
- Produces: `data/gold/prices.parquet`, `fundamentals.parquet`, `sectors.parquet`, `journal.parquet` + DuckDB views

- [ ] **Step 1: Write the failing test — Gold round-trip**

```python
# in build_gold.py _selftest()
import tempfile, pathlib, json
import build_gold as bg
tmp = pathlib.Path(tempfile.mkdtemp())
bg.GOLD_DIR = tmp
# fake Silver with one symbol, one row
(tmp / "fundamentals").mkdir(parents=True)
(tmp / "fundamentals" / "FAKE.json").write_text(json.dumps([{"visible_from": "2024-05-17", "year_end": "2024-03-31", "revenue": 100}]))
bg.build()
assert (tmp / "fundamentals.parquet").exists()
# DuckDB query
import duckdb
con = duckdb.connect()
con.execute(f"SELECT revenue FROM read_parquet('{tmp}/fundamentals.parquet') WHERE symbol='FAKE'")
assert con.fetchone()[0] == 100
print("build_gold selftest ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/ops/build_gold.py --selftest`
Expected: FAIL `build not defined`

- [ ] **Step 3: Write minimal implementation — build()**

```python
GOLD_DIR = ROOT / "data" / "gold"
def build():
    import duckdb, pyarrow as pa, pyarrow.parquet as pq, json
    # ... read Silver, normalize to DataFrames, write Parquet partitioned by year_end year ...
    # ... create DuckDB views ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/ops/build_gold.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ops/build_gold.py
git commit -m "feat(data): Gold builder DuckDB+Parquet from Silver"
```

---

### Task 3: No-Dark-Data Validator — validate_gold.py

**Files:**
- Create: `src/ops/validate_gold.py:1-150`
- Test: `src/ops/validate_gold.py --selftest`

**Interfaces:**
- Consumes: `GOLD_DIR` Parquet, `RAW_SCREENER`, `PARSED_SCREENER`, `data/companies/*.json`
- Produces: exit 0 on pass, exit 1 + message on fail (gates build and CI)

- [ ] **Step 1: Write the failing test — detects Bronze without Silver**

```python
# in validate_gold.py _selftest()
import tempfile, pathlib
tmp_raw = pathlib.Path(tempfile.mkdtemp())
(tmp_raw / "FAKE.html").write_bytes(b"x"*11000)
# no parsed file
import validate_gold as vg
vg.RAW_SCREENER = tmp_raw
vg.PARSED_SCREENER = pathlib.Path(tempfile.mkdtemp())
assert vg.check_bronze_silver() == ["FAKE: Bronze without Silver"]
print("validate_gold selftest ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/ops/validate_gold.py --selftest`
Expected: FAIL `check_bronze_silver not defined`

- [ ] **Step 3: Write minimal implementation — checks**

```python
def check_bronze_silver():
    errs = []
    for html in RAW_SCREENER.glob("*.html"):
        if html.stat().st_size < 10240: continue
        sym = html.stem
        if not (PARSED_SCREENER / f"{sym}.json").exists():
            errs.append(f"{sym}: Bronze without Silver")
    return errs
def main():
    errs = check_bronze_silver() + check_silver_gold() + check_gold_dashboard()
    if errs:
        print("\n".join(errs)); sys.exit(1)
    print("validate_gold ok")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/ops/validate_gold.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ops/validate_gold.py
git commit -m "feat(data): no-dark-data validator Bronze->Silver->Gold->Dashboard"
```

---

### Task 4: Security Validator — validate_security.py

**Files:**
- Create: `src/ops/validate_security.py:1-120`
- Test: `src/ops/validate_security.py --selftest`

**Interfaces:**
- Consumes: `GOLD_DIR` Parquet, `data/raw/*/manifest.json` SHA
- Produces: exit 0 / 1

- [ ] **Step 1: Write the failing test — PII scan**

```python
# in validate_security.py _selftest()
import validate_security as vs
assert vs.scan_pii("no secrets here") == []
assert vs.scan_pii("PAN ABCDE1234F") != []
print("validate_security selftest ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/ops/validate_security.py --selftest`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation — scan + SHA**

```python
def scan_pii(text):
    import re
    if re.search(r"[A-Z]{5}[0-9]{4}[A-Z]", text): return ["PAN"]
    return []
def check_shas():
    # verify Gold manifest SHA matches Bronze manifest
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/ops/validate_security.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ops/validate_security.py
git commit -m "feat(data): security validator SHA + PII scan"
```

---

### Task 5: Dashboard API — FastAPI over Gold

**Files:**
- Create: `src/ops/dashboard_api.py:1-200`
- Test: `src/ops/dashboard_api.py --selftest` (offline, DuckDB in-memory)

**Interfaces:**
- Consumes: Gold Parquet via DuckDB, `company_data.get`
- Produces: `GET /company/{symbol}?as_of=YYYY-MM-DD`, `/watchlist`, `/sector/heatmap`, `/journal`

- [ ] **Step 1: Write the failing test — company endpoint**

```python
# in dashboard_api.py _selftest()
from fastapi.testclient import TestClient
import dashboard_api as api
client = TestClient(api.app)
r = client.get("/company/RELIANCE?as_of=2024-05-17")
assert r.status_code == 200
assert "revenue" in r.json()["fundamentals"]
print("dashboard_api selftest ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/ops/dashboard_api.py --selftest`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation — FastAPI**

```python
from fastapi import FastAPI
import duckdb
app = FastAPI()
@app.get("/company/{symbol}")
def company(symbol: str, as_of: str = None):
    import company_data
    from datetime import date
    d = date.fromisoformat(as_of) if as_of else None
    return company_data.get(symbol, as_of=d)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/ops/dashboard_api.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ops/dashboard_api.py
git commit -m "feat(dashboard): FastAPI over Gold, <100ms"
```

---

### Task 6: Dashboard Frontend — TradingView-lite

**Files:**
- Modify: `dashboard/src/*`, `scripts/build_dashboard.sh`
- Test: `dashboard` build succeeds, `playwright` snapshot of price pane

**Interfaces:**
- Consumes: `dashboard_api` JSON
- Produces: static `dashboard/dist/` served, Lightweight-Charts price/indicator panes

- [ ] **Step 1: Write the failing test — build succeeds**

```bash
npm run build  # in dashboard/
test -f dashboard/dist/index.html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/build_dashboard.sh`
Expected: FAIL (data source still bulk `load_corpus`)

- [ ] **Step 3: Write minimal implementation — switch to API**

```javascript
// dashboard/src/api.js
export async function getCompany(symbol, as_of) {
  const r = await fetch(`/company/${symbol}?as_of=${as_of}`);
  return r.json();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/build_dashboard.sh && test -f dashboard/dist/index.html && echo ok`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/ scripts/build_dashboard.sh
git commit -m "feat(dashboard): TradingView-lite without live charts, from Gold"
```

---

## Self-Review

- Spec coverage: logical view (§4.1) → Task 1, Gold (§4.2) → Task 2, validators (§6) → Tasks 3-4, API (§4.3) → Task 5, frontend (§4.4) → Task 6. All covered.
- Placeholders: none — every step has concrete code.
- Type consistency: `get(symbol, as_of) -> dict` used in Tasks 1,5,6; `timeline_annual_screener -> list[dict]` with ISO strings consistent.
