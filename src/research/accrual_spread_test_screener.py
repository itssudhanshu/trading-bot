#!/usr/bin/env python3
"""Accruals via Screener — same L78 protocol, but reads timeline_annual_screener().

Isolated from quarterly parsed/ and NSE annual parsed_annual/. Both scalings
reported: (NP-OCF)/Revenue and (NP-OCF)/Assets (if assets present).
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401

from datetime import timedelta
import sys

import features
import screener_fundamentals as sf

WINDOW_DAYS = 550


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


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        print("usage: python3 accrual_spread_test_screener.py --selftest")
