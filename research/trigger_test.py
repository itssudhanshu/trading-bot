#!/usr/bin/env python3
"""Test every entry trigger in parallel, against the no-trigger control."""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
from collections import defaultdict

import entry, features, simulate

BASE = dict(stop_pct=10.0, target_pct=20.0, hold=15, max_pos=5, refresh=5)
_C = _D = None


def _one(name):
    entry._CACHE.clear()
    r = simulate.run(_C, _D, trigger=name, **BASE)
    t = r["trades"]
    by = defaultdict(float)
    for x in t:
        by[f"{x['day'].year}-H{1 if x['day'].month <= 6 else 2}"] += x["ret"]
    n = len(t)
    return {"trigger": name, "cagr": r["cagr"], "maxdd": r["maxdd"], "n": n,
            "win": sum(1 for x in t if x["ret"] > 0) / max(n, 1) * 100,
            "worst": min(by.values()) if by else float("nan"), "_r": r}


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    names = list(entry.TRIGGERS)
    print(f"{len(names)} triggers x {len(_D)} sessions, in parallel\n")
    with mp.get_context("fork").Pool(min(len(names), mp.cpu_count())) as p:
        res = p.map(_one, names)
    print(f"  {'trigger':<15}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}{'worst blk':>11}")
    for x in sorted(res, key=lambda y: -y["cagr"]):
        flag = "  <- control" if x["trigger"] == "none" else ""
        print(f"  {x['trigger']:<15}{x['cagr']:>+8.2f}%{x['maxdd']:>7.1f}%"
              f"{x['win']:>5.0f}%{x['n']:>6}{x['worst']:>+10.1f}%{flag}")
    kept = sum(1 for x in res if simulate.keep(
        f"trigger: {x['trigger']}", x["_r"], {**BASE, "trigger": x["trigger"]},
        batch="triggers", track="cluster", note="entry trigger test"))
    print(f"\n  {kept} of {len(res)} cleared the promotion bar")
    return res


if __name__ == "__main__":
    main()
