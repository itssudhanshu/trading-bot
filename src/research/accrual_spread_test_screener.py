#!/usr/bin/env python3
"""Accruals via Screener — same L78 protocol, but reads timeline_annual_screener().

Isolated from quarterly parsed/ and NSE annual parsed_annual/. Both scalings
reported: (NP-OCF)/Revenue and (NP-OCF)/Assets (if assets present).
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401

from datetime import timedelta
import multiprocessing as mp
import statistics
import sys

import analysis
import entry
import features
import remeasure
import screener_fundamentals as sf
import selection
import simulate
from loss_taxonomy_test import block, demean_within_cohort, terciles, verdict, welch_gap

WINDOW_DAYS = 550
BATCH = "20260827-accrual-screener"
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)
COHORTS = 6
MIN_ROWS = 300


def accrual_at_screener(sym, corpus, entry_day):
    """-> (accr_rev, accr_assets) or (None, None) if missing.

    Latest visible annual <= signal_day, year_end >= signal-550d, revenue>0.
    Returns tuple of two scalings; second is None if total_assets missing.
    """
    s = corpus[sym]
    ie = s.index_of(entry_day)
    if ie is None or ie < 1:
        return None, None
    sig = s.days[ie - 1]
    earliest = sig - timedelta(days=WINDOW_DAYS)
    best = None
    for r in sf.timeline_annual_screener(sym):
        vf = r.get("visible_from")
        ye = r.get("year_end")
        if not vf or not ye or vf > sig.isoformat() or ye < earliest.isoformat():
            continue
        if best is None or vf > best["visible_from"]:
            best = r
    if not best:
        return None, None
    rev = best.get("revenue")
    np_ = best.get("net_profit")
    ocf = best.get("ocf")
    if not rev or np_ is None or ocf is None:
        return None, None
    accr_rev = (np_ - ocf) / rev * 100
    assets = best.get("total_assets")
    accr_assets = (np_ - ocf) / assets * 100 if assets else None
    return accr_rev, accr_assets


def _selftest():
    from datetime import date, timedelta
    def series(closes):
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))]
        s = features.Series("A", days)
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

    corp = {"A": series([10.0] * 900)}
    tl = [
        {"visible_from": "2024-05-17", "year_end": "2024-03-31", "ocf": 80.0, "revenue": 200.0, "net_profit": 20.0, "total_assets": 1000.0},
        {"visible_from": "2025-05-17", "year_end": "2025-03-31", "ocf": 50.0, "revenue": 300.0, "net_profit": 65.0, "total_assets": 1200.0},
    ]
    saved = sf.timeline_annual_screener
    sf.timeline_annual_screener = lambda sym: tl
    try:
        rev, assets = accrual_at_screener("A", corp, date(2024, 6, 1))
        assert abs(rev - ((20-80)/200*100)) < 1e-9, rev  # -30
        assert abs(assets - ((20-80)/1000*100)) < 1e-9, assets  # -6
        rev2, assets2 = accrual_at_screener("A", corp, date(2025, 6, 1))
        assert abs(rev2 - 5.0) < 1e-9, rev2  # (65-50)/300*100
        print("accrual both scalings ok")
    finally:
        sf.timeline_annual_screener = saved

    # visibility cutoff
    sf.timeline_annual_screener = lambda sym: [{"visible_from": "2099-01-01", "year_end": "2025-03-31", "ocf": 1, "revenue": 10, "net_profit": 2}]
    try:
        assert accrual_at_screener("A", corp, date(2025, 6, 1)) == (None, None)
    finally:
        sf.timeline_annual_screener = saved

    print("accrual_spread_test_screener selftest ok")


_C = _D = None

def _one(off):
    entry._CACHE.clear()
    r = simulate.run(_C, _D, offset=off, **BASE)
    return off, r

def join_screener(trades, cohort, corpus):
    rows_rev, rows_assets, miss = [], [], 0
    for t in trades:
        rev, assets = accrual_at_screener(t["sym"], corpus, t.get("entry_day"))
        if rev is None:
            miss += 1
            continue
        base = {"cohort": cohort, "ret": t["ret"], "clu": t["clu"], "day": t["day"], "sym": t["sym"]}
        rows_rev.append({**base, "feat": rev})
        if assets is not None:
            rows_assets.append({**base, "feat": assets})
    return rows_rev, rows_assets, miss

def report_one(rows, label, expect):
    print(f"\n  {label}  (n={len(rows)})")
    ok_n = len(rows) >= MIN_ROWS
    if not ok_n:
        print(f"    UNDERPOWERED: below floor {MIN_ROWS}")
    b, se, t = remeasure.slope([r["feat"] for r in rows], [r["ret0"] for r in rows])
    v = verdict(t) if ok_n else "underpowered"
    print(f"    slope {b:+.4f}%/pt +/-{se:.4f}  t={t:+.2f}  [expect {expect}]  {v}")
    lo, mid, hi = terciles(rows, "feat")
    d, gse, gt = welch_gap([r["ret0"] for r in hi], [r["ret0"] for r in lo])
    fm = [statistics.fmean([r["feat"] for r in x]) for x in (lo, mid, hi)]
    v2 = verdict(gt) if ok_n else "underpowered"
    print(f"    terciles {fm[0]:+.2f}/{fm[1]:+.2f}/{fm[2]:+.2f}  gap {d:+.2f}% +/-{gse:.2f} t={gt:+.2f} {v2}")
    return t, gt, ok_n

def main():
    global _C, _D
    # gate: need at least 300 of top-500 with screener OCF
    from pathlib import Path
    import json as _js
    # quick coverage check
    have = sum(1 for p in Path(sf.PARSED_SCREENER).glob("*.json") if any(r.get("ocf") for r in _js.loads(p.read_text())))
    if have < 300:
        raise SystemExit(f"SOURCE NOT READY: only {have} symbols with screener OCF (need 300) — run screener_backfill for top-500 first")
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"ACCRUAL SCREENER  batch {BATCH}  {len(_C)} symbols x {len(_D)} sessions  screener_ocf={have}")
    with mp.get_context("fork").Pool(min(COHORTS, mp.cpu_count())) as p:
        res = sorted(p.map(_one, range(COHORTS)))
    all_rev, all_assets = [], []
    for off, r in res:
        rev_rows, assets_rows, miss = join_screener(r["trades"], off, _C)
        print(f"  cohort {off}: n={len(r['trades'])}  rev_joined={len(rev_rows)} assets_joined={len(assets_rows)} miss={miss}")
        all_rev += rev_rows
        all_assets += assets_rows
    dm_rev = demean_within_cohort(all_rev)
    dm_assets = demean_within_cohort(all_assets)
    z_rev = [dict(r, ret0=r["ret"]) for r in all_rev if r["cohort"]==0]
    report_one(z_rev, "REV PRIMARY — offset 0 raw", "NEGATIVE")
    t_rev_s, t_rev_g, ok_rev = report_one(dm_rev, "REV HARVEST — demeaned", "NEGATIVE")
    # assets scaling if powered
    if len(dm_assets) >= MIN_ROWS:
        z_a = [dict(r, ret0=r["ret"]) for r in all_assets if r["cohort"]==0]
        report_one(z_a, "ASSETS PRIMARY — offset 0 raw", "NEGATIVE")
        report_one(dm_assets, "ASSETS HARVEST — demeaned", "NEGATIVE")
    else:
        print(f"\n  ASSETS HARVEST underpowered (n={len(dm_assets)}) — revenue scaling is primary")
    print(f"\n  resolving {analysis.BACKTEST_EDGE:.1f}% edge needs {analysis.trades_needed(analysis.BACKTEST_EDGE)} trades")
    print("  ENDPOINT: powered |t|>2 with NEGATIVE sign earns ONE follow-up rule at tercile boundary; else NULL")

if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        main()
