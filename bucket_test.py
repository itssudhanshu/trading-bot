#!/usr/bin/env python3
"""Run one isolated book per size bucket, in parallel.

The mixed book reports a single number for six different populations. If the
edge lives only in the smallest names -- which the earlier 'small+mid only'
run at -10.18% against +12.66% for the full mix already hinted at -- a blended
CAGR hides that completely. Each bucket gets its own Rs 5L book, its own
positions and its own drawdown, so the per-bucket result stands alone.

fork, not spawn: the corpus is ~1,700 sessions and loading it six times costs
more than the simulations. Workers inherit it copy-on-write and never write to
it.
"""
import multiprocessing as mp
import statistics
import sys
from collections import defaultdict

import clusters
import features
import simulate

NAMES = ("nano", "micro", "small", "smid", "mid", "large")
BASE = dict(stop_pct=10.0, target_pct=20.0, hold=15, max_pos=5, refresh=5)
_CORPUS = _DAYS = None


def _blk(d):
    return f"{d.year}-H{1 if d.month <= 6 else 2}"


def _one(bucket):
    """Run a book that may only ever hold names from `bucket`."""
    clusters.BUCKET_NAMES = NAMES
    r = simulate.run(_CORPUS, _DAYS, per_bucket={bucket: BASE["max_pos"]}, **BASE)
    by = defaultdict(float)
    for t in r["trades"]:
        by[_blk(t["day"])] += t["ret"]
    n = len(r["trades"])
    return {
        "bucket": bucket, "cagr": r["cagr"], "maxdd": r["maxdd"], "n": n,
        "win": sum(1 for t in r["trades"] if t["ret"] > 0) / max(n, 1) * 100,
        "worst_block": min(by.values()) if by else float("nan"),
        "neg_blocks": sum(1 for v in by.values() if v < 0), "blocks": len(by),
        "avg_ret": statistics.fmean([t["ret"] for t in r["trades"]]) if n else 0.0,
        "_r": r,
    }


def main(names=NAMES):
    global _CORPUS, _DAYS
    _CORPUS = features.load_corpus()
    _DAYS = sorted({d for s in _CORPUS.values() for d in s.days})
    clusters.BUCKET_NAMES = names
    print(f"{len(_CORPUS)} symbols, {len(_DAYS)} sessions -> {len(names)} buckets "
          f"(~{len(_CORPUS) // len(names)} each), running in parallel\n")

    ctx = mp.get_context("fork")
    with ctx.Pool(min(len(names), mp.cpu_count())) as pool:
        res = pool.map(_one, list(names))

    print(f"  {'bucket':<8}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'avg/trade':>11}{'worst blk':>11}{'neg':>7}")
    for x in sorted(res, key=lambda y: -y["cagr"]):
        print(f"  {x['bucket']:<8}{x['cagr']:>+8.2f}%{x['maxdd']:>7.1f}%"
              f"{x['win']:>5.0f}%{x['n']:>6}{x['avg_ret']:>+10.2f}%"
              f"{x['worst_block']:>+10.1f}%{x['neg_blocks']:>4}/{x['blocks']}")

    print("\n  ranked by WORST block (the ranking that generalises here):")
    for x in sorted(res, key=lambda y: -y["worst_block"]):
        print(f"    {x['bucket']:<8} worst {x['worst_block']:>+8.1f}%   "
              f"CAGR {x['cagr']:>+7.2f}%")

    kept = 0
    for x in res:
        if simulate.keep(f"bucket-only: {x['bucket']}", x["_r"],
                         {**BASE, "buckets": list(names), "only": x["bucket"]},
                         batch="bucket-split", track="cluster",
                         note=f"isolated {x['bucket']} book, {len(names)}-way split"):
            kept += 1
    print(f"\n  {kept} of {len(res)} cleared the promotion bar and were stored")
    return res


if __name__ == "__main__":
    main(tuple(sys.argv[1:]) or NAMES)
