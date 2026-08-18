#!/usr/bin/env python3
"""Run N candidate 5-stock portfolios from the 3 clusters, in parallel.

Each portfolio is a distinct rank cohort: offset 0 takes the top 2 micro /
2 small / 1 mid, offset 1 takes the next five, and so on. Same rules, same
costs, same trigger -- the ONLY difference is how far down the ranking the
five names were drawn from.

This is the test the score has never faced. Every result so far comes from
offset 0, the top of the list, and a single book cannot distinguish "the
ranking works" from "those five names happened to do well". If rank carries
information, returns must decay with depth. If cohort 5 matches cohort 0, the
score is decoration.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import statistics
import sys
from collections import defaultdict

import entry, features, simulate

BASE = dict(stop_pct=10.0, target_pct=20.0, hold=15, max_pos=5, refresh=5,
            trigger="breakout")
N = 6
_C = _D = None


def _one(off):
    entry._CACHE.clear()
    r = simulate.run(_C, _D, offset=off, **BASE)
    t = r["trades"]
    by = defaultdict(float)
    for x in t:
        by[f"{x['day'].year}-H{1 if x['day'].month <= 6 else 2}"] += x["ret"]
    n = len(t)
    return {"offset": off, "cagr": r["cagr"], "maxdd": r["maxdd"], "n": n,
            "win": sum(1 for x in t if x["ret"] > 0) / max(n, 1) * 100,
            "avg": statistics.fmean([x["ret"] for x in t]) if n else 0.0,
            "worst": min(by.values()) if by else float("nan"), "_r": r}


def main(n=N):
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"{n} rank cohorts x {len(_D)} sessions, in parallel")
    print(f"cohort k = ranks {{2k..2k+1}} micro, {{2k..2k+1}} small, {{k}} mid\n")
    with mp.get_context("fork").Pool(min(n, mp.cpu_count())) as p:
        res = p.map(_one, range(n))

    print(f"  {'cohort':<9}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'avg/trade':>11}{'worst blk':>11}")
    for x in res:
        tag = "top 5" if x["offset"] == 0 else f"ranks {x['offset']*2+1}-{x['offset']*2+2}"
        print(f"  {x['offset']} ({tag:<7}){x['cagr']:>+8.2f}%{x['maxdd']:>7.1f}%"
              f"{x['win']:>5.0f}%{x['n']:>6}{x['avg']:>+10.2f}%{x['worst']:>+10.1f}%")

    cg = [x["cagr"] for x in res]
    print(f"\n  spread: best {max(cg):+.2f}%  worst {min(cg):+.2f}%  "
          f"median {statistics.median(cg):+.2f}%  stdev {statistics.pstdev(cg):.2f}")
    top = res[0]["cagr"]
    beat = sum(1 for x in res[1:] if x["cagr"] >= top)
    print(f"  cohorts matching or beating the top-ranked book: {beat} of {len(res)-1}")
    # Does rank predict return? Compare first half of cohorts against second.
    half = len(res) // 2
    a = statistics.fmean([x["cagr"] for x in res[:half]])
    b = statistics.fmean([x["cagr"] for x in res[half:]])
    print(f"  shallow cohorts {a:+.2f}% vs deep cohorts {b:+.2f}%  "
          f"-> rank {'PREDICTS' if a > b else 'does NOT predict'} return")

    kept = sum(1 for x in res if simulate.keep(
        f"cohort {x['offset']}", x["_r"], {**BASE, "offset": x["offset"]},
        batch="cohorts", track="cluster", note="rank-cohort portfolio test"))
    print(f"\n  {kept} of {len(res)} cleared the promotion bar")
    return res


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else N)
