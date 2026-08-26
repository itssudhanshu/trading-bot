#!/usr/bin/env python3
"""Do earnings-quality signals visible BEFORE entry separate per-trade returns?

Second application of the L78 protocol (loss taxonomy), this time to the
accounting-quality family that the fundamental-analysis plugin checklist is
built around. A feasibility scan on 400 random filings fixed what is even
askable from quarterly NSE XBRL:

  - cash-flow statements: 0% of filings. The accruals ratio, OCF-vs-NI and
    FCF families are IMPOSSIBLE here -- recorded as impossible, not skipped.
  - OtherIncome / ExceptionalItems / FinanceCosts: 100% of filings.
  - Inventories instants: 98%; TradeReceivables only 38%.
  (parser extended the same day: fundamentals.parse_instants + four flow
  fields; recording only, no rule touched)

TWO pre-named hypotheses, directional, decided before any run:

  H1 EARNINGS QUALITY: per-trade return FALLS as other_income share rises.
     oi_share = other_income / revenue * 100 at the latest filing visible on
     the signal day. Mechanism: a breakout whose profits are propped by
     non-operating income is lower-quality momentum; repricing lands on the
     holders.
  H2 INVENTORY DIVERGENCE: per-trade return FALLS as inventory growth outruns
     revenue growth. inv_div = (inventory YoY %) - (revenue YoY %), both from
     the visible timeline (current quarter vs four back). Mechanism: stock
     building faster than sales is demand weakness or channel stuffing --
     receivables would be the classic twin but sits in only 38% of filings,
     too thin to power a test.

SAMPLE AND CONTROL -- identical to loss_taxonomy_test.py: control is the live
book itself (offset 0, raw returns); power harvest = offsets 0..5, six
disjoint rank cohorts, every harvest statistic computed on returns DEMEANED
WITHIN COHORT so rank depth cannot masquerade as a feature effect.

AS-OF RULE, frozen: rows with visible_from <= signal day (the bar before the
fill); latest visible row used as-is -- no freshness filter, staleness is part
of what live entries actually see. Growth compares that row against four
visible quarters earlier, which may cross a consolidated/standalone flip;
accepted up front rather than tuned after seeing spreads.

POWER FLOOR, pre-registered: a hypothesis needs >=300 joined rows for RESOLVED
to count at all. Below that it is reported as underpowered whatever the t --
the harvest's own arithmetic says ~2%/trade SEs make anything resolvable at
n<300 untrustworthy.

DECISION RULES: |t| > 2 WITH the mechanism's sign earns ONE follow-up rule-
shape test at the observed tercile boundary (new file, registered there
before running, judged by simulate.keep). Otherwise null -> docs/lessons.md,
and nearby definitions (other denominators, windows, floors) must NOT be
tried. Exactly these two hypotheses; any other cut is exploratory and carries
no decision. This file adopts nothing either way.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import statistics
import sys

import analysis
import entry
import features
import fundamentals
import remeasure
import selection
import simulate

# reuse the tested machinery from the L78 study rather than re-typing it
from loss_taxonomy_test import (block, demean_within_cohort, mean_se,
                                terciles, verdict, welch_gap)

BATCH = "20260826-quality"
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)
COHORTS = 6
MIN_ROWS = 300      # pre-registered power floor for any RESOLVED


def quality_at(sym, corpus, entry_day):
    """-> (oi_share, inv_div) visible on the signal day; None where absent."""
    s = corpus[sym]
    ie = s.index_of(entry_day)
    if ie is None or ie < 1:
        return None
    sig_iso = s.days[ie - 1].isoformat()
    rows = [r for r in fundamentals.timeline(sym)
            if r.get("visible_from") and r["visible_from"] <= sig_iso]
    if not rows:
        return None
    cur = rows[-1]
    rev = cur.get("revenue")
    oi_share = (cur["other_income"] / rev * 100
                if cur.get("other_income") is not None and rev else None)
    inv_div = None
    yr = rows[-5] if len(rows) >= 5 else None
    if (yr is not None
            and cur.get("inventories") and yr.get("inventories")
            and cur.get("revenue") and yr.get("revenue")):
        ig = (cur["inventories"] / yr["inventories"] - 1) * 100
        rg = (cur["revenue"] / yr["revenue"] - 1) * 100
        inv_div = ig - rg
    return oi_share, inv_div


def join(trades, cohort, corpus):
    """Attach H1/H2 values -> (rows_h1, rows_h2, n_missing_any)."""
    r1, r2, miss = [], [], 0
    for t in trades:
        q = quality_at(t["sym"], corpus, t.get("entry_day"))
        if q is None:
            miss += 1
            continue
        oi, div = q
        base = {"cohort": cohort, "ret": t["ret"], "clu": t["clu"],
                "why": t["why"], "day": t["day"], "sym": t["sym"]}
        if oi is not None:
            r1.append({**base, "feat": oi})
        if div is not None:
            r2.append({**base, "feat": div})
    return r1, r2, miss


def report_one(rows, label, expect):
    print(f"\n  {label}  (n={len(rows)})")
    if len(rows) < MIN_ROWS:
        print(f"    UNDERPOWERED: below the pre-registered floor of "
              f"{MIN_ROWS}; whatever t says, it does not count.")
    b, se, t = remeasure.slope([r["feat"] for r in rows],
                               [r["ret0"] for r in rows])
    ok_n = len(rows) >= MIN_ROWS
    v = verdict(t) if ok_n else "underpowered"
    print(f"    slope {b:+.4f}%/pt +/-{se:.4f}  t={t:+.2f}  [expect {expect}]  {v}")
    lo, mid, hi = terciles(rows, "feat")
    d, gse, gt = welch_gap([r["ret0"] for r in hi], [r["ret0"] for r in lo])
    fm = [statistics.fmean([r["feat"] for r in x]) for x in (lo, mid, hi)]
    v = verdict(gt) if ok_n else "underpowered"
    print(f"    terciles feat {fm[0]:+.2f}/{fm[1]:+.2f}/{fm[2]:+.2f}  "
          f"gap top-bottom {d:+.2f}% +/-{gse:.2f}  t={gt:+.2f}  {v}")
    return t, gt, ok_n


_C = _D = None


def _one(off):
    entry._CACHE.clear()
    r = simulate.run(_C, _D, offset=off, **BASE)
    return off, r


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"EARNINGS-QUALITY SPREADS  batch {BATCH}  {len(_C)} symbols x "
          f"{len(_D)} sessions\n")
    with mp.get_context("fork").Pool(min(COHORTS, mp.cpu_count())) as p:
        res = sorted(p.map(_one, range(COHORTS)))

    h1_all, h2_all = [], []
    for off, r in res:
        r1, r2, miss = join(r["trades"], off, _C)
        tag = "live book" if off == 0 else f"harvest cohort {off}"
        print(f"  cohort {off} ({tag}): n={len(r['trades'])}  H1 rows={len(r1)}"
              f"  H2 rows={len(r2)}  no-filing={miss}")
        h1_all += r1
        h2_all += r2

    dm1 = demean_within_cohort(h1_all)
    dm2 = demean_within_cohort(h2_all)

    z1 = [dict(r, ret0=r["ret"]) for r in h1_all if r["cohort"] == 0]
    report_one(z1, "H1 PRIMARY -- offset 0, raw", "NEGATIVE")
    t1s, t1g, ok1h = report_one(dm1, "H1 HARVEST -- demeaned within cohort",
                                "NEGATIVE")

    z2 = [dict(r, ret0=r["ret"]) for r in h2_all if r["cohort"] == 0]
    report_one(z2, "H2 PRIMARY -- offset 0, raw", "NEGATIVE")
    t2s, t2g, ok2h = report_one(dm2, "H2 HARVEST -- demeaned within cohort",
                                "NEGATIVE")

    for label, rows in (("micro", [r for r in dm1 if r["clu"] == "micro"]),
                        ("small", [r for r in dm1 if r["clu"] == "small"])):
        report_one(rows, f"H1 cluster {label}", "NEGATIVE")
    blocks = sorted({block(r["day"]) for r in dm1})
    for blk in blocks:
        report_one([r for r in dm1 if block(r["day"]) == blk],
                   f"H1 block {blk}", "NEGATIVE")

    resolved = ((ok1h and min(abs(t1s), abs(t1g)) > 2)
                or (ok2h and min(abs(t2s), abs(t2g)) > 2))
    print(f"\n  resolving a {analysis.BACKTEST_EDGE:.1f}%/trade edge needs "
          f"{analysis.trades_needed(analysis.BACKTEST_EDGE)} trades.")
    print("  ENDPOINT (fixed above): a powered |t|>2 with the mechanism's sign")
    print("  earns ONE follow-up rule-shape test at the observed tercile")
    print("  boundary; otherwise NULL -> lessons.md, nothing adopted.")


def _selftest():
    from datetime import date, timedelta

    def series(closes):
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))]
        s = features.Series("Q", days)
        for px in closes:
            s.open.append(float(px))
            s.high.append(px * 1.001)
            s.low.append(px * 0.999)
            s.close.append(float(px))
            s.volume.append(1000)
            s.turnover.append(1e6)
            s.deliv_pct.append(40.0)
            s.surveillance_known.append(True)
            s.restricted.append(False)
        return s

    corp = {"Q": series([10.0] * 800)}
    tl_rows = [
        {"visible_from": "2024-01-05", "quarter_end": "2023-12-31",
         "revenue": 100.0, "other_income": 5.0, "inventories": 40.0},
        {"visible_from": "2024-04-05", "quarter_end": "2024-03-31",
         "revenue": 110.0, "other_income": 11.0},
        {"visible_from": "2024-07-05", "quarter_end": "2024-06-30",
         "revenue": 120.0, "other_income": 6.0, "inventories": 60.0},
        {"visible_from": "2024-10-05", "quarter_end": "2024-09-30",
         "revenue": 130.0, "other_income": 13.0, "inventories": 66.0},
        {"visible_from": "2025-01-05", "quarter_end": "2024-12-31",
         "revenue": 140.0, "other_income": 7.0, "inventories": 99.0},
    ]
    saved = fundamentals.timeline
    fundamentals.timeline = lambda sym: tl_rows          # injected, restored below
    try:
        # early entry: only the Jan-05 filing is visible at the Jan-19 signal
        got = quality_at("Q", corp, date(2024, 1, 20))
        assert got is not None
        oi, div = got
        assert abs(oi - 5.0) < 1e-9, oi                  # 5/100
        assert div is None, "growth needs five visible quarters"

        # late entry: all five rows visible at the 2025-02-09 signal
        got = quality_at("Q", corp, date(2025, 2, 10))
        oi, div = got
        assert abs(oi - 7.0 / 140 * 100) < 1e-9, oi      # latest = Dec-24 row
        # inventories 99 vs 40 (+147.5%) vs revenue 140 vs 100 (+40%)
        assert abs(div - 107.5) < 1e-9, div

        # a filing not yet BROADCAST is invisible, always
        fundamentals.timeline = lambda sym: [
            dict(tl_rows[-1], visible_from="2024-02-01")]
        got = quality_at("Q", corp, date(2024, 1, 20))
        assert got is None, "a future filing leaked into signal time"
    finally:
        fundamentals.timeline = saved

    # join(): missing quality simply routes to fewer rows, never fabricates
    trades = [{"sym": "Q", "entry_day": date(2025, 2, 10), "ret": 2.0,
               "clu": "micro", "why": "stop", "day": date(2025, 2, 20),
               "cohort": 0}]
    fundamentals.timeline = lambda sym: tl_rows
    try:
        r1, r2, miss = join(trades, 0, corp)
        assert len(r1) == 1 and len(r2) == 1 and miss == 0, (r1, r2, miss)
        fundamentals.timeline = lambda sym: []
        r1, r2, miss = join(trades, 0, corp)
        assert r1 == [] and r2 == [] and miss == 1
    finally:
        fundamentals.timeline = saved

    print("quality_spread_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
