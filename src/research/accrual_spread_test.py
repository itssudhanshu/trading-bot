#!/usr/bin/env python3
"""Do accruals separate per-trade returns on this bucket?

Third application of the L78/L79 protocol. Enabled by new DATA, not by a
re-test of anything rejected: quarterly NSE XBRL carries no cash-flow
statement (0% of filings, L79), but the ANNUAL feed carries full Ind-AS cash
flows, now harvested broadcast-dated into xbrl_annual/ and parsed into
annual as-of timelines (fundamentals.build_parsed_annual -- deliberately
separate from the quarterly timeline, whose row arithmetic assumes quarters).

ONE pre-named hypothesis, directional, decided before any run:

  H ACCRUALS: per-trade return FALLS as (net_profit - ocf) / revenue rises.
    accr = (net_profit - ocf) / revenue * 100 at the latest annual filing
    visible on the signal day. Mechanism (Sloan's, scaled by revenue because
    total assets are absent from these filings -- a sibling construct, stated
    here rather than discovered after): earnings that outrun operating cash
    are lower-quality; the market eventually reprices them.

FROZEN DEFINITIONS:
  - visibility: rows with visible_from <= signal day (bar before the fill);
  - freshness: year_end must be within 550 days of the signal day. Stated up
    front with its reason -- accrual mispricing decays within about a year,
    so a three-year-old balance is not the construct being tested. This is a
    definitional boundary fixed BEFORE spreads were seen, not a filter tuned
    after them;
  - revenue > 0 required; missing pieces mean the trade is simply not in the
    arm's sample (reported as counts, never imputed).

SAMPLE AND CONTROL -- identical to loss_taxonomy_test.py / quality_spread_test.py:
control is the live book itself (offset 0, raw returns); power harvest =
offsets 0..5, six disjoint rank cohorts, every harvest statistic computed on
returns DEMEANED WITHIN COHORT so rank depth cannot masquerade as a feature.

POWER FLOOR, pre-registered: >=300 joined rows for RESOLVED to count.

DECISION RULES: powered |t| > 2 on BOTH the slope and the top-bottom tercile
gap, with the NEGATIVE sign the mechanism predicts, earns ONE follow-up
rule-shape test at the observed tercile boundary (a NEW file, registered
there before running, judged by simulate.keep). Anything else is a NULL ->
docs/lessons.md, nothing adopted, and nearby definitions (other scalings,
windows, winsorizations) must NOT be tried. Exactly one hypothesis; any other
cut printed here is exploratory and carries no decision.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

from datetime import timedelta

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
from loss_taxonomy_test import (block, demean_within_cohort, terciles,
                                verdict, welch_gap)

BATCH = "20260826-accrual"
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)
COHORTS = 6
MIN_ROWS = 300        # pre-registered power floor
WINDOW_DAYS = 550     # frozen freshness bound for the visible annual


def accrual_at(sym, corpus, entry_day):
    """-> accruals % for the latest visible-and-fresh annual, or None."""
    s = corpus[sym]
    ie = s.index_of(entry_day)
    if ie is None or ie < 1:
        return None
    sig = s.days[ie - 1]
    earliest = sig - timedelta(days=WINDOW_DAYS)
    best = None
    for r in fundamentals.timeline_annual(sym):
        vf = r.get("visible_from")
        ye = r.get("year_end")
        if not vf or not ye or vf > sig.isoformat() or ye < earliest.isoformat():
            continue
        if best is None or vf > best["visible_from"]:
            best = r
    if not best:
        return None
    rev = best.get("revenue")
    np_, ocf = best.get("net_profit"), best.get("ocf")
    if not rev or np_ is None or ocf is None:
        return None
    return (np_ - ocf) / rev * 100


def join(trades, cohort, corpus):
    rows, miss = [], 0
    for t in trades:
        a = accrual_at(t["sym"], corpus, t.get("entry_day"))
        if a is None:
            miss += 1
            continue
        rows.append({"cohort": cohort, "ret": t["ret"], "clu": t["clu"],
                     "why": t["why"], "day": t["day"], "sym": t["sym"],
                     "feat": a})
    return rows, miss


def report(rows, label):
    print(f"\n  {label}  (n={len(rows)})")
    ok_n = len(rows) >= MIN_ROWS
    if not ok_n:
        print(f"    UNDERPOWERED: below the pre-registered floor of {MIN_ROWS}.")
    b, se, t = remeasure.slope([r["feat"] for r in rows],
                               [r["ret0"] for r in rows])
    v = verdict(t) if ok_n else "underpowered"
    print(f"    slope {b:+.4f}%/pt +/-{se:.4f}  t={t:+.2f}  [expect NEGATIVE]  {v}")
    lo, mid, hi = terciles(rows, "feat")
    d, gse, gt = welch_gap([r["ret0"] for r in hi], [r["ret0"] for r in lo])
    fm = [statistics.fmean([r["feat"] for r in x]) for x in (lo, mid, hi)]
    v = verdict(gt) if ok_n else "underpowered"
    print(f"    terciles accr {fm[0]:+.2f}/{fm[1]:+.2f}/{fm[2]:+.2f}  "
          f"gap {d:+.2f}% +/-{gse:.2f}  t={gt:+.2f}  {v}")
    return b, t, gt, ok_n


_C = _D = None


def source_usable(directory, k=30, need=0.5, seed=19):
    """-> True if >=`need` of `k` random harvested annual files carry OCF in a
    FULL-YEAR-DATED span (>=300 days).

    Measured 2026-08-26 on the live harvest: 0 of 250 files qualified -- values
    exist but sit in quarter-dated contexts, sometimes holding full-year
    magnitudes beside true-quarter P&L facts (RELIANCE FY24), which no
    mechanical rule can separate. This gate keeps the study OFF corrupt inputs
    until a source that dates its own cash flows exists.
    """
    import random as _r
    from datetime import datetime as _dt2
    from pathlib import Path as _P
    files = list(_P(directory).rglob("*.xml"))
    if len(files) < k:
        return False
    good = 0
    for pth in _r.Random(seed).sample(files, k):
        try:
            parsed = fundamentals.parse_xbrl(pth.read_bytes())
        except Exception:
            continue
        ok = False
        for (sd, ed), fl in parsed.items():
            if "ocf" not in fl:
                continue
            try:
                if (_dt2.strptime(ed, "%Y-%m-%d").date()
                        - _dt2.strptime(sd, "%Y-%m-%d").date()).days >= 300:
                    ok = True
                    break
            except ValueError:
                continue
        good += 1 if ok else 0
    return good / k >= need


def _one(off):
    entry._CACHE.clear()
    r = simulate.run(_C, _D, offset=off, **BASE)
    return off, r


def main():
    global _C, _D
    gate_dir = fundamentals.RAW / "xbrl_annual"
    if not source_usable(gate_dir):
        raise SystemExit(
            "SOURCE UNUSABLE (L80): annual-feed OCF is not full-year dated "
            "(0/250 files). The accrual construct cannot be measured here; "
            "see docs/lessons.md. This gate is deliberate.")
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    n_annual = sum(1 for sym in _C if fundamentals.timeline_annual(sym))
    print(f"ACCRUAL SPREADS  batch {BATCH}  {len(_C)} symbols "
          f"({n_annual} with annual timelines) x {len(_D)} sessions\n")
    with mp.get_context("fork").Pool(min(COHORTS, mp.cpu_count())) as p:
        res = sorted(p.map(_one, range(COHORTS)))

    all_rows = []
    for off, r in res:
        rows, miss = join(r["trades"], off, _C)
        tag = "live book" if off == 0 else f"harvest cohort {off}"
        print(f"  cohort {off} ({tag}): n={len(r['trades'])}  "
              f"joined={len(rows)}  no-visible-annual={miss}")
        all_rows += rows

    dm = demean_within_cohort(all_rows)
    z = [dict(r, ret0=r["ret"]) for r in all_rows if r["cohort"] == 0]
    report(z, "PRIMARY -- offset 0, raw")
    _, t_s, t_g, ok = report(dm, "HARVEST -- demeaned within cohort")

    for clu in ("micro", "small"):
        sub = [r for r in dm if r["clu"] == clu]
        report(sub, f"cluster {clu}")
    for blk in sorted({block(r["day"]) for r in dm}):
        report([r for r in dm if block(r["day"]) == blk], f"block {blk}")

    feats = [r["feat"] for r in all_rows]
    qs = statistics.quantiles(feats, n=4)
    print(f"\n  accrual distribution across joined entries: median "
          f"{statistics.median(feats):+.1f}%  IQR {qs[0]:+.1f}-{qs[2]:+.1f}%")
    resolved = (ok and abs(t_s) > 2 and abs(t_g) > 2
                and t_s < 0 and t_g < 0)
    print(f"\n  ENDPOINT: {'RESOLVED with mechanism sign -> ONE follow-up rule'
          '-shape test at the tercile boundary above' if resolved else
          'NULL -> lessons.md, nothing adopted'}")


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
        {"visible_from": "2024-05-10", "year_end": "2024-03-31",
         "ocf": 80.0, "revenue": 200.0, "net_profit": 20.0},   # accr -30%
        {"visible_from": "2025-05-10", "year_end": "2025-03-31",
         "ocf": 50.0, "revenue": 300.0, "net_profit": 65.0},   # accr +5%
        {"visible_from": "2026-06-15", "year_end": "2026-03-31",
         "ocf": 10.0, "revenue": 400.0, "net_profit": 90.0},   # fresh but far future
    ]
    saved = fundamentals.timeline_annual
    fundamentals.timeline_annual = lambda sym: tl
    try:
        a = accrual_at("A", corp, date(2024, 6, 1))
        assert abs(a - (-30.0)) < 1e-9, a                    # only FY24 visible
        a = accrual_at("A", corp, date(2025, 6, 1))
        assert abs(a - 5.0) < 1e-9, a                        # FY25 supersedes
        # freshness: with ONLY FY24 visible, signals past 2024-03-31+550d must yield nothing
        fundamentals.timeline_annual = lambda sym: [tl[0]]
        a = accrual_at("A", corp, date(2025, 11, 1))
        assert a is None, f"stale annual used despite the frozen window: {a}"
        a = accrual_at("A", corp, date(2024, 9, 1))
        assert abs(a - (-30.0)) < 1e-9, "inside the window it must read"
        fundamentals.timeline_annual = lambda sym: tl
        # a future filing is invisible even inside the window
        fundamentals.timeline_annual = \
            lambda sym: [dict(tl[-1], visible_from="2099-01-01")]
        assert accrual_at("A", corp, date(2025, 6, 1)) is None
        # non-positive revenue excluded
        fundamentals.timeline_annual = \
            lambda sym: [dict(tl[0], revenue=0.0)]
        assert accrual_at("A", corp, date(2024, 6, 1)) is None
    finally:
        fundamentals.timeline_annual = saved

    # join(): routing without fabrication
    trades = [{"sym": "A", "entry_day": date(2025, 6, 1), "ret": 1.0,
               "clu": "small", "why": "time", "day": date(2025, 6, 11),
               "cohort": 0}]
    fundamentals.timeline_annual = lambda sym: tl
    try:
        rows, miss = join(trades, 0, corp)
        assert len(rows) == 1 and miss == 0 and abs(rows[0]["feat"] - 5.0) < 1e-9
        fundamentals.timeline_annual = lambda sym: []
        rows, miss = join(trades, 0, corp)
        assert rows == [] and miss == 1
    finally:
        fundamentals.timeline_annual = saved

    print("accrual_spread_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
