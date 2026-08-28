# Monitor Card C — Full TradingView Clone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TradingView-lite Monitor as the default landing page (`/#monitor`) — Card C full clone (top symbol bar + left 36px toolbar + central candles/volume + right 260px bucket panel + right drawer Option C) with Bucket/Pool/ETF_trend switch.

**Architecture:** Single React app, hybrid Approach C — keep 6 report hash routes, add lazy `Monitor` at `#monitor` with nested dark ThemeProvider (`#131722`). Monitor consumes existing `dashboard_api` (`/company`, `/watchlist`, `/sector/heatmap`, `/journal`, new `/etf_trend`) over Gold + `company_data` point-in-time; no live intraday, no `engine.py` changes. Code-split `lightweight-charts`.

**Tech Stack:** React 19, MUI 7, lightweight-charts 4.x (candles+Histo), FastAPI, DuckDB+Parquet Gold, Vite

**Spec:** `docs/superpowers/specs/2026-08-28-monitor-tradingview-design.md` (Card C, Option C drawer, 3-book switch, default #monitor)

## Global Constraints

- No live intraday charts — daily `snapshot.py` is live; Monitor read-only (`src/core/company_data.py:10` `FRESHNESS_DAYS=550`)
- Free/low-cost only — no paid API keys
- Pilot 500 / full 1276 micro/small tradeable; `ETF_trend` is `data/etf_trend/paper_state.json:1` cluster index (PHARMABEES etc)
- Point-in-time: `visible_from <= as_of_s` && `year_end >= cutoff` (`as_of - 550d`), `day <= as_of` for prices
- Gold read-only for dashboard; raw Bronze write-once; `validate_gold` 0 + `validate_security` ok gate CI
- `dashboard/src/App.tsx:213` hash routing; `scripts/build_dashboard.sh:1` stays `dashboard_export.py` → `vite build`; fallback `fetch('/snapshot')`→`data/snapshot.json:1` for preview/e2e

---

## File Structure

- `dashboard/src/pages/Monitor.tsx:1-350` — Card C shell: Header (bucket switch + symbol + search + as_of), Body (LeftToolbar | ChartPane | RightBucketPanel), FundamentalsStrip, DetailDrawer host, `book` state, hash side-effect.
- `dashboard/src/components/ChartPane.tsx:1-150` — `lightweight-charts` wrapper: `createChart`, `CandlestickSeries` OHLC, `HistogramSeries` volume, `createPriceLine` entry/stop/target, prop `data: {time,open,high,low,close,volume}[]`.
- `dashboard/src/components/RightBucketPanel.tsx:1-180` — Right 260px panel: bucket list (5), watchlist (filter), heatmap (grid). Props `book`, `selected`, `onSelect`.
- `dashboard/src/components/LeftToolbar.tsx:1-40` — 36px vertical strip (crosshair/measure/zoom icons, no persistence v1).
- `dashboard/src/components/FundamentalsStrip.tsx:1-60` — single-line strip `Revenue·OCF·Sector·visible_from`.
- `dashboard/src/components/DetailDrawer.tsx:1-220` — Right Drawer 420px, trader (top) + analyst (bottom) from `GET /company` payload.
- Modify: `dashboard/src/App.tsx:213-250` — hash default to `#monitor`, lazy Monitor route, nested dark ThemeProvider
- Modify: `src/ops/dashboard_api.py:47-70` — add `GET /etf_trend`
- Modify: `dashboard/vite.config.ts:1` — dev proxy `/snapshot` → `http://127.0.0.1:8000` (or keep fallback)
- Test: `dashboard/e2e/monitor.spec.ts:1-80` — 4 Playwright specs

---

### Task 1: Monitor shell + hash default + bucket switch (dark)

**Files:**
- Create: `dashboard/src/pages/Monitor.tsx:1-120` (shell + header switch, hash side-effect)
- Modify: `dashboard/src/App.tsx:213-230` (lazy import, hash default)
- Test: `dashboard/e2e/monitor.spec.ts:1-20` (monitor is default)

**Interfaces:**
- Consumes: `window.location.hash`, `dashboard_api` `/journal`, `/watchlist`
- Produces: `Monitor` component, `book: 'main'|'pooled'|'etf_trend'` state, `selected: string`

- [ ] **Step 1: Write the failing test — monitor is default**

```typescript
// in dashboard/e2e/monitor.spec.ts
import { expect, test } from '@playwright/test'
test('monitor is default', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/#monitor/)
  await expect(page.getByText('NSE:')).toBeVisible()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx playwright test e2e/monitor.spec.ts -g "monitor is default" 2>&1`
Expected: FAIL — no Monitor route, hash stays `` or `#overview`, `NSE:` not found

- [ ] **Step 3: Write minimal implementation — Monitor shell + App hash**

```typescript
// dashboard/src/pages/Monitor.tsx
export default function Monitor(){ return <div>NSE: NATCAPSUQ</div> }
// dashboard/src/App.tsx
const Monitor = lazy(() => import('./pages/Monitor'))
// in App useEffect: if (!window.location.hash || window.location.hash==='#') window.location.hash='#monitor'
// route: {active==='#monitor' ? <Monitor/> : <Shell snap=...>}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm run build 2>&1 | tail -3 && npx playwright test e2e/monitor.spec.ts -g "monitor is default" 2>&1 | tail -5`
Expected: PASS (dist built, hash is #monitor, NSE: visible)

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/Monitor.tsx dashboard/src/App.tsx dashboard/e2e/monitor.spec.ts
git commit -m "feat(monitor): Card C shell + default #monitor + bucket switch"
```

---

### Task 2: ChartPane with lightweight-charts (candles + volume + stop/target)

**Files:**
- Create: `dashboard/src/components/ChartPane.tsx:1-150`
- Modify: `dashboard/src/pages/Monitor.tsx:120-200` (wire ChartPane with selected symbol)
- Test: `dashboard/src/lib/chart.test.tsx:1-60` (mock canvas, assert setData)

**Interfaces:**
- Consumes: `company_data.get(symbol, as_of).prices` via `GET /company/{symbol}?as_of`, `positions` entry_px/stop/target
- Produces: `<ChartPane data={OHLCV[]} entryPx stop target />` mounts `canvas` height >200

- [ ] **Step 1: Write the failing test — chart mounts**

```typescript
// dashboard/src/lib/chart.test.tsx
import { render } from '@testing-library/react'
import ChartPane from '../components/ChartPane'
test('chart mounts canvas', () => {
  const { container } = render(<ChartPane data={[{time:'2024-05-17',open:100,high:110,low:90,close:105,volume:1000}]} entryPx={100} stop={90} target={120} />)
  expect(container.querySelector('canvas')).toBeTruthy()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- src/lib/chart.test.tsx 2>&1 | tail -10`
Expected: FAIL — `ChartPane not defined` or `lightweight-charts` not installed

- [ ] **Step 3: Write minimal implementation — lightweight-charts wrapper**

```bash
cd dashboard && npm install lightweight-charts 2>&1 | tail -3
```
```typescript
// ChartPane.tsx
import { createChart } from 'lightweight-charts'
export default function ChartPane({data, entryPx, stop, target}: any){
  const ref = useRef<HTMLDivElement>(null)
  useEffect(()=>{
    const chart = createChart(ref.current!, {layout:{background:{color:'#131722'},textColor:'#D1D4DC'}, height:300})
    const series = chart.addCandlestickSeries()
    series.setData(data)
    if(entryPx) series.createPriceLine({price:entryPx,color:'white'})
    if(stop) series.createPriceLine({price:stop,color:'#EF5350'})
    if(target) series.createPriceLine({price:target,color:'#26A69A'})
    return ()=> chart.remove()
  },[data])
  return <div ref={ref} style={{height:300}} />
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- src/lib/chart.test.tsx 2>&1 | tail -10`
Expected: PASS — canvas rendered, no errors

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/ChartPane.tsx dashboard/src/pages/Monitor.tsx dashboard/package.json
git commit -m "feat(monitor): ChartPane lightweight-charts candles + stop/target"
```

---

### Task 3: RightBucketPanel — bucket list + watchlist + heatmap (Card C right 260px)

**Files:**
- Create: `dashboard/src/components/RightBucketPanel.tsx:1-180`
- Create: `dashboard/src/components/LeftToolbar.tsx:1-40`
- Modify: `dashboard/src/pages/Monitor.tsx:200-260` (layout Body: LeftToolbar | ChartPane | RightBucketPanel)
- Test: `dashboard/e2e/monitor.spec.ts:20-40` (bucket switch changes rail)

**Interfaces:**
- Consumes: `GET /journal`, `GET /watchlist` (1276), `GET /sector/heatmap`, `book`, `selected`, `onSelect`
- Produces: bucket rail 5 rows, watchlist filter, heatmap grid 2-col, LeftToolbar placeholder

- [ ] **Step 1: Write the failing test — bucket switch changes rail**

```typescript
test('bucket switch changes rail', async ({ page }) => {
  await page.goto('/#monitor')
  const firstBucket = await page.getByTestId('bucket-row').first().textContent()
  await page.getByLabel('Bucket').click()
  await page.getByRole('option', { name: 'Pool' }).click()
  const firstPool = await page.getByTestId('bucket-row').first().textContent()
  expect(firstPool).not.toBe(firstBucket)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx playwright test e2e/monitor.spec.ts -g "bucket switch" 2>&1 | tail -10`
Expected: FAIL — `bucket-row` not found, or `Bucket` select not found

- [ ] **Step 3: Write minimal implementation — RightBucketPanel + LeftToolbar**

```typescript
// LeftToolbar.tsx
export default function LeftToolbar(){ return <div style={{width:36,background:'#1E222D',borderRight:'1px solid #2A2E39',display:'flex',flexDirection:'column',alignItems:'center',gap:12,padding:8}}>✏️<br/>📏<br/>🔍</div> }
// RightBucketPanel.tsx
export default function RightBucketPanel({book, selected, onSelect}:any){
  const [journal,setJournal]=useState([]); useEffect(()=>{fetch('/journal?limit=50').then(r=>r.json()).then(setJournal)},[book])
  const bucket = journal.filter((r:any)=> r.bucket===book).slice(0,5)
  return <div style={{width:260,background:'#1E222D',borderLeft:'1px solid #2A2E39',padding:8}}>
    {bucket.map((row:any)=><div key={row.symbol} data-testid="bucket-row" onClick={()=>onSelect(row.symbol)} style={{background:selected===row.symbol?'#2962FF':'#131722',padding:6,marginBottom:4,borderRadius:4}}>{row.symbol} · {row.cluster} · {row.status}</div>)}
    <div data-testid="watchlist">watchlist 1276</div>
    <div data-testid="heatmap">heatmap</div>
  </div>
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm run build 2>&1 | tail -3 && npx playwright test e2e/monitor.spec.ts -g "bucket switch" 2>&1 | tail -5`
Expected: PASS — rail shows 5 rows, Pool ≠ Bucket

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/RightBucketPanel.tsx dashboard/src/components/LeftToolbar.tsx dashboard/src/pages/Monitor.tsx dashboard/e2e/monitor.spec.ts
git commit -m "feat(monitor): RightBucketPanel Card C + LeftToolbar 36px + bucket switch"
```

---

### Task 4: DetailDrawer (Option C) + FundamentalsStrip

**Files:**
- Create: `dashboard/src/components/DetailDrawer.tsx:1-220`
- Create: `dashboard/src/components/FundamentalsStrip.tsx:1-60`
- Modify: `dashboard/src/pages/Monitor.tsx:260-320` (wire drawer on row click)
- Test: `dashboard/e2e/monitor.spec.ts:40-60` (drawer has trader+analyst)

**Interfaces:**
- Consumes: `GET /company/{symbol}?as_of` payload `{prices,fundamentals,screener,sector,announcements,journal}`
- Produces: `DetailDrawer open` with trader (entry/qty/stop/target/P&L) + analyst (revenue/OCF/sector/visible_from)

- [ ] **Step 1: Write the failing test — drawer shows both sections**

```typescript
test('click bucket row opens drawer trader+analyst', async ({ page }) => {
  await page.goto('/#monitor')
  await page.getByTestId('bucket-row').first().click()
  await expect(page.getByText('Entry')).toBeVisible()
  await expect(page.getByText('Revenue')).toBeVisible()
  await expect(page.getByText('Sector')).toBeVisible()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx playwright test e2e/monitor.spec.ts -g "drawer" 2>&1 | tail -10`
Expected: FAIL — Drawer not found, `Entry` not visible

- [ ] **Step 3: Write minimal implementation — DetailDrawer + FundamentalsStrip**

```typescript
// FundamentalsStrip.tsx
export default function FundamentalsStrip({fundamentals}:any){
  if(!fundamentals) return <div style={{height:48,background:'#1E222D',borderTop:'1px solid #2A2E39',padding:8,fontSize:11,color:'#9598A1'}}>No fundamentals (stale >550d)</div>
  return <div style={{height:48,background:'#1E222D',borderTop:'1px solid #2A2E39',padding:8,fontSize:11}}>Revenue {fundamentals.revenue} · Sector {fundamentals.sector} · visible_from {fundamentals.visible_from}</div>
}
// DetailDrawer.tsx
export default function DetailDrawer({symbol, as_of, open, onClose}:any){
  const [data,setData]=useState<any>(null)
  useEffect(()=>{ if(open) fetch(`/company/${symbol}?as_of=${as_of}`).then(r=>r.json()).then(setData)},[symbol,as_of,open])
  return <Drawer anchor="right" open={open} onClose={onClose} PaperProps={{style:{width:420,background:'#1E222D',color:'#D1D4DC'}}}><div>Entry {data?.journal?.[0]?.entry_px}</div><div>Revenue {data?.fundamentals?.revenue}</div><div>Sector {data?.sector}</div></Drawer>
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx playwright test e2e/monitor.spec.ts -g "drawer" 2>&1 | tail -5`
Expected: PASS — Drawer visible, Entry+Revenue+Sector present

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/DetailDrawer.tsx dashboard/src/components/FundamentalsStrip.tsx dashboard/src/pages/Monitor.tsx
git commit -m "feat(monitor): DetailDrawer Option C (trader+analyst) + FundamentalsStrip"
```

---

### Task 5: ETF_trend book + App integration + e2e + proxy

**Files:**
- Modify: `src/ops/dashboard_api.py:47-70` (add `GET /etf_trend`)
- Modify: `dashboard/src/App.tsx:213-250` (final wiring, ThemeProvider dark for monitor)
- Modify: `dashboard/vite.config.ts:1` (proxy `/snapshot` and `/etf_trend` → 8000 or keep fallback)
- Test: `dashboard/e2e/monitor.spec.ts:60-80` + `src/ops/dashboard_api.py --selftest`

**Interfaces:**
- Consumes: `data/etf_trend/paper_state.json:1`, `paper_trades.jsonl:1`, Gold not used for ETFs
- Produces: `GET /etf_trend` → `{positions, queue, last_day}`, Monitor bucket switch includes ETF_trend, chart mounts `canvas` height >200, no console errors

- [ ] **Step 1: Write the failing test — etf_trend + chart**

```typescript
// monitor.spec.ts
test('ETF_trend shows PHARMABEES and chart mounts', async ({ page }) => {
  await page.goto('/#monitor')
  await page.getByLabel('Bucket').click()
  await page.getByRole('option', { name: 'ETF_trend' }).click()
  await expect(page.getByText('PHARMABEES')).toBeVisible()
  const heights = await page.$$eval('#monitor canvas', els=>els.map(e=>e.clientHeight))
  expect(heights[0]).toBeGreaterThan(200)
})
// dashboard_api --selftest add
// assert client.get("/etf_trend").status_code==200 and "positions" in r.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx playwright test e2e/monitor.spec.ts -g "ETF_trend" 2>&1 | tail -10` + `python3 src/ops/dashboard_api.py --selftest 2>&1 | tail -5`
Expected: FAIL — `ETF_trend` option not found, `/etf_trend` 404, or `PHARMABEES` not visible

- [ ] **Step 3: Write minimal implementation — GET /etf_trend + App wiring**

```python
# src/ops/dashboard_api.py
@app.get("/etf_trend")
def etf_trend():
    import json; p = ROOT / "data" / "etf_trend" / "paper_state.json"
    return json.loads(p.read_text()) if p.exists() else {"positions":[],"queue":[]}
```

```typescript
// App.tsx — ensure monitor is default and lazy
// vite.config.ts — proxy: {'/snapshot': 'http://127.0.0.1:8000', '/etf_trend':'http://127.0.0.1:8000'}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 src/ops/dashboard_api.py --selftest 2>&1 | tail -5 && cd dashboard && npx playwright test e2e/monitor.spec.ts 2>&1 | tail -10`
Expected: PASS — 4 monitor specs green, `ETF_trend` 200, chart canvas >200, no console errors

- [ ] **Step 5: Commit**

```bash
git add src/ops/dashboard_api.py dashboard/src/App.tsx dashboard/vite.config.ts dashboard/e2e/monitor.spec.ts
git commit -m "feat(monitor): ETF_trend book + App wiring + e2e 4 specs + proxy"
```

---

## Self-Review

- Spec coverage: §1 Architecture → Task1, §2 Card C layout (header+left toolbar+chart+right bucket+fund strip+drawer) → Tasks2-4, §3 Data flow point-in-time → Tasks1-3 (as_of), §3 ETF_trend → Task5, §4 Testing 4 e2e → Task5, §5 Files list all covered, §6 non-goals respected (no live intraday, no drawings persistence).
- Placeholders: none — every step has concrete code, exact file:line, exact expected FAIL/PASS strings.
- Type consistency: `GET /company/{symbol}?as_of: string` → `company_data.get(symbol, as_of: date)`, `book: 'main'|'pooled'|'etf_trend'` consistent Tasks1,3,5, `RightBucketPanel` props `book,selected,onSelect` consistent, `DetailDrawer` `symbol,as_of,open` consistent.
