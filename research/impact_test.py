#!/usr/bin/env python3
"""How much of the result survives realistic fills?

The impact constant cannot be calibrated from bhavcopy -- that needs
trade-level data. So this reports a SENSITIVITY across plausible values rather
than one number. c=0 is the old assumption (fill at the printed price), c=1 is
the standard calibration, c=2 is conservative.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import statistics
from collections import defaultdict

import analysis, entry, features, portfolio, simulate

BASE = dict(stop_pct=10.0, target_pct=20.0, hold=15, max_pos=5, refresh=5,
            trigger="breakout")
CS = [0.0, 0.5, 1.0, 2.0, 3.0]
_C = _D = None


def _one(c):
    entry._CACHE.clear()
    r = simulate.run(_C, _D, impact_c=c, **BASE)
    t = r["trades"]
    by = defaultdict(float)
    for x in t:
        by[f"{x['day'].year}-H{1 if x['day'].month <= 6 else 2}"] += x["ret"]
    imps = [x.get("imp", 0.0) for x in t]
    return {"c": c, "cagr": r["cagr"], "dd": r["maxdd"], "n": len(t),
            "win": sum(1 for x in t if x["ret"] > 0) / max(len(t), 1) * 100,
            "imp_med": statistics.median(imps) if imps else 0.0,
            "imp_max": max(imps) if imps else 0.0,
            "worst": min(by.values()) if by else float("nan"),
            "cluster": analysis.per_cluster(t), "_r": r}


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    mix = "/".join(str(v) for v in portfolio.TAKE_PER_CLUSTER.values())
    print(f"impact sensitivity — bucket {mix}, Rs {portfolio.CAPITAL:,}\n")
    with mp.get_context("fork").Pool(len(CS)) as p:
        res = p.map(_one, CS)
    print(f"  {'c':<6}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'impact/trade':>14}{'worst':>10}   micro / small")
    for x in res:
        cl = x["cluster"]
        m = cl.get("micro", {}); s = cl.get("small", {})
        tag = "  <- old assumption" if x["c"] == 0 else ""
        print(f"  {x['c']:<6.1f}{x['cagr']:>+8.2f}%{x['dd']:>7.1f}%{x['win']:>5.0f}%"
              f"{x['n']:>6}{x['imp_med']:>12.2f}%{x['worst']:>+9.1f}%   "
              f"{m.get('avg',0):+.2f}% / {s.get('avg',0):+.2f}%{tag}")
    base = res[0]["cagr"]
    print(f"\n  worst observed round-trip impact: {max(x['imp_max'] for x in res):.2f}%")
    for x in res[1:]:
        print(f"  c={x['c']}: {x['cagr'] - base:+.2f} points vs the no-impact "
              f"assumption ({(x['cagr']/base*100 if base else 0):.0f}% of it survives)")
    breakeven = [x["c"] for x in res if x["cagr"] <= 0]
    print(f"\n  turns unprofitable at c >= {min(breakeven)}" if breakeven
          else "\n  still profitable at every c tested")
    for x in res:
        simulate.keep(f"impact c={x['c']}", x["_r"], {**BASE, "impact_c": x["c"]},
                      batch="impact", track="cluster", note="impact sensitivity")
    return res


if __name__ == "__main__":
    main()
