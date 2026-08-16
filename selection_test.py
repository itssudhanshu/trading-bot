#!/usr/bin/env python3
"""Which 5 stocks should the bucket hold? Test the composition rules.

The bucket has always been "top 2 micro, top 2 small, top 1 mid" -- assumed,
never tested. This runs the alternatives as independent books over the full
history: different cluster mixes, and a correlation cap that refuses a
candidate moving too closely with one already taken.

Correlation only ever REMOVES a name; it never reaches deeper down the ranking
to backfill. Reaching deeper is what cost 4 points of CAGR when the entry
trigger was applied before ranking instead of after.
"""
import multiprocessing as mp
import statistics
from collections import defaultdict

import entry, features, portfolio, simulate

BASE = dict(stop_pct=10.0, target_pct=20.0, hold=15, max_pos=5, refresh=5,
            trigger="breakout")
CASES = [
    ("baseline 2/2/1",     dict()),
    ("corr cap 0.7",       dict(max_corr=0.7)),
    ("corr cap 0.5",       dict(max_corr=0.5)),
    ("corr cap 0.3",       dict(max_corr=0.3)),
    ("mix 3/1/1 micro",    dict(take_per_cluster={"micro": 3, "small": 1, "mid": 1})),
    ("mix 1/2/2 larger",   dict(take_per_cluster={"micro": 1, "small": 2, "mid": 2})),
    ("mix 2/1/2",          dict(take_per_cluster={"micro": 2, "small": 1, "mid": 2})),
    ("mix 1/1/3 mid",      dict(take_per_cluster={"micro": 1, "small": 1, "mid": 3})),
]
_C = _D = None


def _one(idx):
    name, kw = CASES[idx]
    entry._CACHE.clear()
    r = simulate.run(_C, _D, **{**BASE, **kw})
    t = r["trades"]
    by = defaultdict(float)
    mix = defaultdict(int)
    for x in t:
        by[f"{x['day'].year}-H{1 if x['day'].month <= 6 else 2}"] += x["ret"]
        mix[x["clu"]] += 1
    n = len(t)
    return {"name": name, "cagr": r["cagr"], "dd": r["maxdd"], "n": n,
            "win": sum(1 for x in t if x["ret"] > 0) / max(n, 1) * 100,
            "worst": min(by.values()) if by else float("nan"),
            "mix": dict(mix), "_r": r, "kw": kw}


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"{len(CASES)} composition rules x {len(_D)} sessions, capital "
          f"Rs {portfolio.CAPITAL:,}\n")
    with mp.get_context("fork").Pool(min(len(CASES), mp.cpu_count())) as p:
        res = p.map(_one, range(len(CASES)))

    print(f"  {'rule':<18}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'worst blk':>11}   micro/small/mid")
    for x in sorted(res, key=lambda y: -y["cagr"]):
        m = x["mix"]
        print(f"  {x['name']:<18}{x['cagr']:>+8.2f}%{x['dd']:>7.1f}%{x['win']:>5.0f}%"
              f"{x['n']:>6}{x['worst']:>+10.1f}%   "
              f"{m.get('micro',0)}/{m.get('small',0)}/{m.get('mid',0)}")

    print("\n  ranked by WORST block (the ranking that generalises here):")
    for x in sorted(res, key=lambda y: -y["worst"])[:4]:
        print(f"    {x['name']:<18} worst {x['worst']:>+8.1f}%   CAGR {x['cagr']:>+7.2f}%")

    base = next(x for x in res if x["name"].startswith("baseline"))
    better = [x for x in res if x["cagr"] > base["cagr"] and x["worst"] > base["worst"]]
    print(f"\n  rules beating baseline on BOTH CAGR and worst block: "
          f"{', '.join(x['name'] for x in better) if better else 'none'}")
    for x in res:
        simulate.keep(f"selection: {x['name']}", x["_r"], {**BASE, **x["kw"]},
                      batch="selection", track="cluster",
                      note="bucket composition test")
    return res


if __name__ == "__main__":
    main()
