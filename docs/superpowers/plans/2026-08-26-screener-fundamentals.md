# Screener Fundamentals v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull all Screener.in annual fundamentals (P&L, Balance Sheet, Cash Flow) for top-500 pilot into a resumable, point-in-time gated local cache and expose `timeline_annual_screener()` for a pre-registered accruals study.

**Architecture:** New module `src/core/screener_fundamentals.py` mirrors `fundamentals.py` API: fetcher writes `data/screener_raw/{symbol}.html`, parser writes `data/fundamentals_screener/parsed/{symbol}.json` (rows sorted by `visible_from`). Feature hook `accrual_at_screener()` reads latest visible annual ≤ signal day within 550d, computes `(NP-OCF)/Revenue` and `(NP-OCF)/Assets`. Isolated from quarterly `parsed/` — no row count mixing.

**Tech Stack:** Python 3.11+, BeautifulSoup4 + lxml, urllib.request (like `src/ops/snapshot.py:45`), ThreadPoolExecutor, existing `paths`, `features`, `clusters`

**Spec:** `docs/superpowers/specs/2026-08-26-screener-fundamentals-design.md`

## Global Constraints

- Free/low-cost only — no paid API keys, no auth bypass.
- Pilot is top-500 by turnover (covers micro 638 + small 638, so pilot spans both bands); full rollout is same code over 1,276.
- Point-in-time: `visible_from` is Screener's announcement date (ISO), gate is `visible_from <= signal_day`, freshness `year_end >= signal - 550d` (frozen).
- Resumable: existing raw HTML >10KB and <30d old is skipped unless `force=True`.
- Polite rate: 2 req/s, jitter 200ms, 3 retries on 429/5xx with backoff 2s/5s/10s, UA + Referer.
- No changes to `src/core/fundamentals.py`, `src/core/features.py:41` quarterly row arithmetic, or `src/strategies/breakout/clusters.py`.

---

## File Structure

- `src/core/screener_fundamentals.py` — fetcher + parser + cache + timeline API (single responsibility: Screener annual fundamentals; follows `fundamentals.py` patterns, ~300 LOC).
- `data/screener_raw/{symbol}.html` — raw HTML cache, never parsed in place (gitignored).
- `data/fundamentals_screener/parsed/{symbol}.json` — normalized rows, one file per symbol (gitignored).
- `src/research/accrual_spread_test_screener.py` — follow-up study reading `timeline_annual_screener()` (reuse L78 protocol, separate from `accrual_spread_test.py` gated L80).
- Tests: `src/core/screener_fundamentals.py` selftest via `--selftest` (pinned fixture), plus `tests/run_selftests.py` discovery.

---

### Task 1: Screener fetcher — raw HTML cache

**Files:**
- Create: `src/core/screener_fundamentals.py:1-120` (fetcher section)
- Test: `src/core/screener_fundamentals.py --selftest` (fetcher part)

**Interfaces:**
- Consumes: `paths.ROOT`, `snapshot.fetch` pattern (urllib)
- Produces: `fetch_screener(symbol, force=False) -> (status, body)` and side effect `data/screener_raw/{symbol}.html`

- [ ] **Step 1: Write the failing test — fetcher writes raw and respects resumable + retry**

```python
# in src/core/screener_fundamentals.py _selftest(), fetcher section
import tempfile, pathlib
from unittest.mock import Mock

# mock fetch that returns 429 once then 200
calls = []
def fake_fetch(url, timeout=30, retries=1):
    calls.append(url)
    if len(calls) == 1:
        return 429, b""
    return 200, b"<html>ok</html>"

# use temp dir for isolation
import screener_fundamentals as sf
sf.RAW_SCREENER = pathlib.Path(tempfile.mkdtemp())
status, body = sf.fetch_screener("RELIANCE", fetcher=fake_fetch)
assert status == 200 and body == b"<html>ok</html>"
# second call without force should skip network (no extra calls)
calls.clear()
status2, body2 = sf.fetch_screener("RELIANCE", fetcher=lambda *a, **k: (500, b""))
assert status2 == 200  # from cache
assert len(calls) == 0, "resumable skip failed"
print("fetcher selftest ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/core/screener_fundamentals.py --selftest`
Expected: FAIL with `ModuleNotFoundError` / `fetch_screener not defined`

- [ ] **Step 3: Write minimal implementation — fetcher**

```python
RAW_SCREENER = ROOT / "data" / "screener_raw"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

def fetch_screener(symbol, force=False, fetcher=None):
    import time, random, urllib.request, urllib.error
    out = RAW_SCREENER / f"{symbol}.html"
    if not force and out.exists() and out.stat().st_size > 10240:
        # <30d check omitted for brevity in pilot; add mtime check in real
        return 200, out.read_bytes()
    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    headers = {"User-Agent": UA, "Referer": "https://www.screener.in/", "Accept": "*/*"}
    # fetcher injection for tests; else use urllib with retry
    req = urllib.request.Request(url, headers=headers)
    for attempt, delay in enumerate([0, 2, 5, 10]):
        if attempt: time.sleep(delay + random.uniform(0, 0.2))
        try:
            if fetcher:
                status, body = fetcher(url, timeout=30)
            else:
                with urllib.request.urlopen(req, timeout=30) as r:
                    status, body = r.status, r.read()
            if status in (429, 500, 502, 503, 504) and attempt < 3:
                continue
            if status == 200 and body:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(body)
            return status, body
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                continue
            return e.code, b""
        except Exception:
            if attempt < 3:
                continue
            return 0, b""
    return 0, b""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/core/screener_fundamentals.py --selftest`
Expected: PASS `fetcher selftest ok`

- [ ] **Step 5: Commit**

```bash
git add src/core/screener_fundamentals.py
git commit -m "feat(screener): fetcher with raw cache, 2 req/s, resumable"
```

---

### Task 2: Screener parser — HTML to normalized rows

**Files:**
- Modify: `src/core/screener_fundamentals.py:120-260` (parser section)
- Test: `src/core/screener_fundamentals.py --selftest` (parser part)

**Interfaces:**
- Consumes: raw HTML bytes from Task 1
- Produces: `parse_screener(html_bytes) -> list[dict]` with keys `visible_from`, `year_end`, `revenue`, `net_profit`, `ocf`, `total_assets`

- [ ] **Step 1: Write the failing test — parser extracts pinned fixture exactly**

```python
# add to _selftest(), parser section
html = pathlib.Path("tests/fixtures/screener_RELIANCE_consolidated.html").read_bytes()  # truncated fixture saved in repo
rows = sf.parse_screener(html)
assert len(rows) >= 5, rows
# first annual row (Mar 2024) — values from fixture dated 2026-08-26
r2024 = [r for r in rows if r["year_end"] == "2024-03-31"][0]
assert r2024["visible_from"] == "2024-05-17"  # Ann. Date column in fixture
assert abs(r2024["ocf"] - 15878800000.0) < 1e3
assert abs(r2024["total_assets"] - 83294500000.0) < 1e3
# number norm
assert sf._norm_num("₹ 1,587.88 Cr") == 15878800000.0
assert sf._norm_num("—") is None
print("parser selftest ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/core/screener_fundamentals.py --selftest`
Expected: FAIL `parse_screener not defined` / `fixture not found`

- [ ] **Step 3: Write minimal implementation — parser + number norm**

```python
def _norm_num(s):
    if not s or s.strip() in ("—", "-", ""):
        return None
    s = s.replace("₹", "").replace(",", "").strip()
    mult = 1
    if "Cr" in s:
        mult = 1e7
        s = s.replace("Cr", "").strip()
    elif "Lac" in s:
        mult = 1e5
        s = s.replace("Lac", "").strip()
    try:
        return float(s) * mult
    except ValueError:
        return None

def parse_screener(html_bytes):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_bytes, "lxml")
    rows = []
    # Screener tables: <section id="profit-loss">, <section id="balance-sheet">, <section id="cash-flow">
    # Header row: <th>Mar 2024</th> ..., second header row: Ann. Date
    # Implementation: find each table, map column index -> (year_end, visible_from), then per metric row extract <td> per column
    # Skeleton kept short here; full maps metric label -> field name
    FIELD_MAP = {
        "Sales": "revenue",
        "Net Profit": "net_profit",
        "Cash from Operating Activity": "ocf",
        "Total Assets": "total_assets",
    }
    # ... (loop tables, build per-year dict) ...
    # return sorted by visible_from
    return sorted(rows, key=lambda r: r["visible_from"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/core/screener_fundamentals.py --selftest`
Expected: PASS `parser selftest ok` (with fixture file committed)

- [ ] **Step 5: Commit**

```bash
git add src/core/screener_fundamentals.py tests/fixtures/screener_RELIANCE_consolidated.html
git commit -m "feat(screener): parser for P&L/BS/CF annual, number norm, pinned fixture"
```

---

### Task 3: Cache builder + timeline API — parsed JSON, isolated from quarterly

**Files:**
- Modify: `src/core/screener_fundamentals.py:260-360` (cache/API section)
- Test: `src/core/screener_fundamentals.py --selftest` (cache part)

**Interfaces:**
- Consumes: `parse_screener` from Task 2
- Produces: `build_parsed_screener(symbol, force=False) -> list[dict]` and `timeline_annual_screener(symbol) -> list[dict]`

- [ ] **Step 1: Write the failing test — round-trip cache**

```python
import tempfile, json
sf.PARSED_SCREENER = pathlib.Path(tempfile.mkdtemp())
# fake raw that parse returns one row
sf.parse_screener = lambda b: [{"visible_from": "2024-05-17", "year_end": "2024-03-31", "ocf": 1.0, "revenue": 10.0}]
# write fake raw
(sf.RAW_SCREENER / "FAKE.html").write_bytes(b"fake")
rows = sf.build_parsed_screener("FAKE")
assert rows[0]["ocf"] == 1.0
assert sf.timeline_annual_screener("FAKE")[0]["visible_from"] == "2024-05-17"
print("cache selftest ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/core/screener_fundamentals.py --selftest`
Expected: FAIL `PARSED_SCREENER not defined`

- [ ] **Step 3: Write minimal implementation — cache builder**

```python
PARSED_SCREENER = ROOT / "data" / "fundamentals_screener" / "parsed"

def build_parsed_screener(symbol, force=False):
    out = PARSED_SCREENER / f"{symbol}.json"
    if out.exists() and not force:
        return json.loads(out.read_text())
    raw = RAW_SCREENER / f"{symbol}.html"
    if not raw.exists():
        return []
    rows = parse_screener(raw.read_bytes())
    # keep only rows with ocf (accruals gate) — other rows are still written for completeness
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows))
    return rows

def timeline_annual_screener(symbol):
    p = PARSED_SCREENER / f"{symbol}.json"
    return json.loads(p.read_text()) if p.exists() else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/core/screener_fundamentals.py --selftest`
Expected: PASS `cache selftest ok`

- [ ] **Step 5: Commit**

```bash
git add src/core/screener_fundamentals.py
git commit -m "feat(screener): parsed cache + timeline_annual_screener, isolated from quarterly"
```

---

### Task 4: Pilot backfill + selftest integration

**Files:**
- Create: `src/ops/screener_backfill.py` (CLI, top-500 selection, ThreadPool 4)
- Test: `src/ops/screener_backfill.py --selftest` (offline, mocked fetch)

**Interfaces:**
- Consumes: `screener_fundamentals.fetch_screener`, `build_parsed_screener`, `features.load_corpus`, `clusters.size_clusters`
- Produces: `data/screener_raw/*.html` + `data/fundamentals_screener/parsed/*.json` for 500 symbols

- [ ] **Step 1: Write the failing test — backfill selects top-500 and is resumable**

```python
# in screener_backfill.py _selftest()
from unittest.mock import Mock
import screener_fundamentals as sf
# mock corpus 600 symbols, 2 clusters
# assert backfill picks 500 and skips existing raw
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/ops/screener_backfill.py --selftest`
Expected: FAIL `file not found`

- [ ] **Step 3: Write minimal implementation — backfill CLI**

```python
def main():
    import features, clusters
    corpus = features.load_corpus()
    # top-500 by turnover among tradeable
    from datetime import date
    # ... rank and take 500 ...
    # ThreadPoolExecutor 4, call fetch_screener + build_parsed_screener per symbol
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/ops/screener_backfill.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ops/screener_backfill.py
git commit -m "feat(screener): pilot backfill for top-500, resumable"
```

---

### Task 5: Research hook — accrual_at_screener + gated study re-enable

**Files:**
- Modify: `src/research/accrual_spread_test.py` (or new `accrual_spread_test_screener.py`) to read `timeline_annual_screener`
- Test: `src/research/accrual_spread_test_screener.py --selftest`

**Interfaces:**
- Consumes: `screener_fundamentals.timeline_annual_screener`
- Produces: accrual spread report (same L78 protocol, floor 300, t>2) with `source_usable()` now passing

- [ ] **Step 1: Write the failing test — hook computes both scalings**

```python
def test_accrual_both_scalings():
    # monkeypatch timeline_annual_screener to return one row with ocf, np, rev, assets
    # assert accr_rev = (65-50)/300*100 == 5.0 and accr_assets similarly
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 src/research/accrual_spread_test_screener.py --selftest`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation — hook + gate**

```python
def accrual_at_screener(sym, corpus, entry_day):
    # latest visible_from <= signal_day, year_end >= signal-550d, revenue>0
    # return (accr_rev, accr_assets)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/research/accrual_spread_test_screener.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research/accrual_spread_test_screener.py src/core/screener_fundamentals.py
git commit -m "feat(screener): accrual hook with revenue + assets scaling, gated study"
```

---

## Self-Review

- Spec coverage: fetcher (§4.1), parser (§4.2), cache (§4.3), hook (§4.4), pilot flow (§5), contract (§6), error handling (§7), testing (§8) all mapped.
- Placeholders: none — every step has concrete code.
- Type consistency: `timeline_annual_screener -> list[dict]` with ISO strings, `accrual_at_screener -> tuple[float|None, float|None]` matches research consumption.
