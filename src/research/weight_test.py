#!/usr/bin/env python3
"""The last unmeasured knob: is deliv worth 1.5?

`data/sprout/weights.json` carries deliv at 1.5 with this justification:

    "unconditioned spread +1.22% on 954 randomly-sampled trades; confirmed by
     simulation (+24.10% CAGR / 27.0% DD vs +12.66% / 38.9% neutral)"

Both halves of that are now suspect.

  - The spread came from 954 samples. A later 2,337-sample run of the same test
    read deliv +0.93% (t=2.05) and put `rs` highest instead -- and standard error
    at that size is ~0.46%, so +1.22% and +0.93% are the same measurement twice.
  - The simulation was pre-guard. Every fill in it could include a circuit-locked
    bar that no buyer could have got (L58), and the guard removed 6.5 CAGR points
    from the live configuration -- more than most gaps this project has called a
    finding.

So the number that set the weight and the number that confirmed it were both
measured on a book that has since stopped existing. This re-runs the decision at
the live settings against the corrected engine.

The control is NEUTRAL (every weight 1.0), because that is what raising deliv
was a decision AGAINST. Nothing here writes a weight; `clusters.W` is set inside
each fork so a variant cannot leak into the live file or into its siblings.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
from collections import defaultdict

import analysis, clusters, entry, features, learning, remeasure, selection, simulate

BATCH = "20260819-postlock"

BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)

_F = ("rs", "deliv", "liq", "near_high")
NEUTRAL = {f: 1.0 for f in _F}
LIVE = learning.load_weights()
assert set(LIVE) == set(_F), f"the live weight file does not cover {_F}: {LIVE}"


def _w(**over):
    return {**NEUTRAL, **over}


# Pre-registered before running. Five variants, so a single |t| near 2 across
# them is worth about half what it looks like -- stated again at the bottom.
VARIANTS = [
    ("neutral 1/1/1/1 (control)", NEUTRAL),
    ("live: deliv 1.5",           dict(LIVE)),
    ("deliv 2.0",                 _w(deliv=2.0)),
    ("rs 1.5",                    _w(rs=1.5)),
    ("near_high 1.5",             _w(near_high=1.5)),
]

_C = _D = None


def _one(item):
    label, w = item
    # Module global, set inside the worker. A fork pool gives each child its own
    # copy, so this cannot leak across variants -- and setting clusters.W stops
    # _weights() reading the file at all, which is the whole point: these were
    # function-locals once, and four different weight sets returned byte-identical
    # buckets because every call silently re-read the file (clusters.py:88).
    clusters.W = dict(w)
    entry._CACHE.clear()
    r = simulate.run(_C, _D, **BASE)
    t = r["trades"]
    by = defaultdict(float)
    for x in t:
        by[f"{x['day'].year}-H{1 if x['day'].month <= 6 else 2}"] += x["ret"]
    return {"label": label, "w": w, "cagr": r["cagr"], "dd": r["maxdd"],
            "n": len(t), "win": sum(1 for x in t if x["ret"] > 0) / max(len(t), 1) * 100,
            "worst": min(by.values()) if by else float("nan"),
            "cluster": analysis.per_cluster(t), "_r": r}


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"selection weights — {len(VARIANTS)} variants x {len(_D)} sessions, "
          f"at the live exit rules ({BASE['stop_pct']:g}/{BASE['target_pct']:g}/"
          f"{BASE['hold']}d), post-guard, batch {BATCH}\n")
    with mp.get_context("fork").Pool(min(len(VARIANTS), mp.cpu_count())) as p:
        res = p.map(_one, VARIANTS)

    print(f"  {'variant':<28}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'per-trade':>12}{'std err':>9}{'worst blk':>11}")
    for x in res:
        m, se, n = remeasure.edge(x["_r"])
        print(f"  {x['label']:<28}{x['cagr']:>+8.2f}%{x['dd']:>7.1f}%"
              f"{x['win']:>5.0f}%{x['n']:>6}{m:>+11.2f}%{se:>8.2f}%"
              f"{x['worst']:>+10.1f}%")

    ctl = res[0]
    print(f"\n  against NEUTRAL ({ctl['cagr']:+.2f}% CAGR, n={ctl['n']}) — the "
          f"control raising deliv was a decision against:")
    for x in res[1:]:
        d, se, t = remeasure.gap(x["_r"], ctl["_r"])
        print(f"    {x['label']:<28}{x['cagr'] - ctl['cagr']:>+7.2f} CAGR pts"
              f"{d:>+8.2f}% / trade  +/-{se:>5.2f}  t{t:>+6.2f}  "
              f"{'RESOLVED' if abs(t) > 2 else 'inside the noise'}")

    print("\n  per cluster (micro / small), average per trade:")
    for x in res:
        c = x["cluster"]
        print(f"    {x['label']:<28}"
              f"micro {c.get('micro', {}).get('avg', 0):+.2f}% "
              f"(n={c.get('micro', {}).get('n', 0)})   "
              f"small {c.get('small', {}).get('avg', 0):+.2f}% "
              f"(n={c.get('small', {}).get('n', 0)})")

    print(f"\n  {analysis.trades_needed(analysis.BACKTEST_EDGE)} trades are "
          f"needed to resolve a {analysis.BACKTEST_EDGE:.1f}%/trade edge. "
          f"{len(VARIANTS)} variants were run, so a single |t| near 2 is worth "
          f"about half what it looks like.")
    # batch= is a FIELD, not part of the name. overview.py dedupes stored
    # candidates by variant name and prints the batch beside it, so a batch baked
    # into the name creates a second candidate per re-run -- the duplicate-row bug
    # L59 fixed. The weights go in params so a stored row can be replayed.
    kept = sum(1 for x in res if simulate.keep(
        f"weights {x['label']}", x["_r"], {**BASE, "weights": x["w"]},
        batch=BATCH, note="selection weights, post-guard"))
    print(f"\n  {kept} of {len(res)} cleared the promotion bar")


def _selftest():
    # The knob has to actually reach the score, or this test measures nothing
    # five times. clusters.py:88 records four weight sets that returned
    # byte-identical buckets for exactly this reason.
    assert LIVE["deliv"] != 1.0, "the live file no longer raises deliv; re-read the note"
    assert VARIANTS[0][1] == NEUTRAL, "the control is not the neutral weight set"
    assert dict(VARIANTS[1][1]) == dict(LIVE), "the live row is not the live file"
    # W=None means "read the file"; any dict means "use me". Both directions.
    clusters.W = None
    from_file, _ = clusters._weights()
    clusters.W = {"deliv": 9.0}
    override, _ = clusters._weights()
    clusters.W = None
    assert from_file == LIVE, f"_weights() ignored the file: {from_file}"
    assert override == {"deliv": 9.0}, f"_weights() ignored the override: {override}"
    print("weight_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        main()
