#!/usr/bin/env python3
"""Does the learning loop's feature ranking survive taking the market out?

HYPOTHESIS, stated before the run. `learning.analyse()` scores each feature by
the RAW return spread between its top and bottom third, and `propose()` moves
weights toward the largest spread. Raw return contains the market's move over
the holding window, so a feature that loads on high-beta names -- or that simply
fires more often in rising regimes -- collects that move and is credited with
information it does not carry.

The obvious form of this argument is WRONG and must not be used. A move common
to every trade on one date hits both halves of a median split equally and
CANCELS out of a difference of means; `cluster_se.py --demo` measured that for
this repo, and it is why Welch is honest to within 1% at zero imbalance. Within
a date, raw return is not contaminated.

The argument that survives is about POOLING. `analyse()` pools trades across
2019-2026 and the holding windows differ trade by trade (exits are stop, target
or time: 82 / 39 / 75 on the live book). Across dates nothing cancels, because
each trade's market move is its own. So the confound is real and it is a
between-date effect, not a within-date one.

ENDPOINT, fixed before running, and nothing is adopted on anything else:

  For each scored feature (rs, deliv, liq, near_high), the top-minus-bottom
  tercile spread measured on RAW return and on EXCESS return, where excess is
  the trade's return less an equal-weight buy-and-hold of its own cluster over
  the trade's OWN holding window (entry_day -> exit_day, not a fixed 10 days).

  The loop's ranking is disturbed if EITHER holds:
    1. any feature changes the SIGN of its spread, or
    2. the ORDER of the four features by spread differs between raw and excess.

  A difference is reported as real only at |t| >= 2.6 on the paired
  raw-minus-excess difference -- the same family bar fund_test uses for four
  features tested together. Standard errors are reported iid AND cluster-robust
  by non-overlapping time block, with the measured imbalance, because that is
  now this project's instrument (L104/L105).

This measures. It adopts nothing and changes no weight: a spread that moves is
a reason to re-derive the weights deliberately, not to let this file do it.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import statistics
import sys

import cluster_se
import features as feat
import learning
import market
import selection
import simulate

# The features the live score actually weights. `off_high` and `score` sit in
# the historical ledger and are NOT weighted; `rsi` is in learning.FEATURES but
# carries no weight either. Measuring what is not scored would answer a question
# nobody asked.
SCORED = ("rs", "deliv", "liq", "near_high")
FAMILY_BAR = 2.6
MIN_PER_FEATURE = 30        # matches learning.analyse's own floor


def benchmark_for(corpus, trade, universe_by_cluster):
    """-> equal-weight buy-and-hold of the trade's OWN cluster over its window.

    Its own cluster, not the whole corpus: a micro-cap held ten days is not
    usefully compared against a book that includes the names this strategy
    refuses to trade (L69 is the same distinction). And its own window, because
    a stop can close a position on day 2 while a time exit runs the full ten --
    charging both the same ten days of market would be a different error.
    """
    syms = universe_by_cluster.get(trade["clu"])
    if not syms:
        return None
    r = market.equal_weight_return(corpus, syms, trade["entry_day"], trade["day"])
    return r.get("ret") if r else None


def tercile_spread(rows, key, value_key):
    """Top-minus-bottom tercile difference, the same shape learning.analyse uses."""
    vals = [(r[key], r[value_key]) for r in rows
            if r.get(key) is not None and r.get(value_key) is not None]
    if len(vals) < MIN_PER_FEATURE:
        return None
    xs = [v for v, _ in vals]
    if len(set(xs)) < 3 or statistics.pstdev(xs) == 0:
        return None
    vals.sort(key=lambda vr: vr[0])
    k = len(vals) // 3
    lo = statistics.fmean(r for _, r in vals[:k])
    hi = statistics.fmean(r for _, r in vals[-k:])
    return {"spread": hi - lo, "n": len(vals), "k": k}


def paired_shift(rows, key, di_of):
    """-> the raw-minus-excess difference in tercile spread, with both SEs.

    Paired on the SAME trades and the SAME tercile membership, so the only thing
    that changes between the two numbers is which return is averaged. Comparing
    two independently-sorted terciles would confound the shift with a change in
    who is in which third.
    """
    vals = [r for r in rows if r.get(key) is not None
            and r.get("ret") is not None and r.get("excess") is not None]
    if len(vals) < MIN_PER_FEATURE:
        return None
    xs = [r[key] for r in vals]
    if len(set(xs)) < 3:
        return None
    vals.sort(key=lambda r: r[key])
    k = len(vals) // 3
    top, bot = vals[-k:], vals[:k]
    # per-trade contribution to (raw spread - excess spread); for a trade in the
    # top third that is +(raw - excess) = +benchmark, in the bottom third it is
    # -(raw - excess). The difference of spreads IS a difference of means over
    # these signed values, so diff_means applies directly.
    obs = [(+1, r) for r in top] + [(-1, r) for r in bot]
    values = [r["ret"] - r["excess"] for _, r in obs]
    group = [1 if sgn > 0 else 0 for sgn, _ in obs]
    clus = [di_of(r) for _, r in obs]
    return cluster_se.diff_means(values, group, clus)


def run(n_dates=None):
    corpus = feat.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    res = simulate.run(corpus, days)
    # "trades", not "closed". Checked against simulate.run's return rather than
    # written from memory -- the first draft of this line guessed and the
    # selftest could not catch it, because the selftest never calls run().
    closed = res["trades"]
    print(f"{len(closed)} closed trades from the live configuration "
          f"({selection.HOLD_DAYS}d hold, {selection.STOP_PCT}% stop, "
          f"{selection.TARGET_PCT}% target)")

    # the cluster memberships to benchmark against, taken once at the end of the
    # window; a point-in-time membership per trade would be better and is a
    # bigger job -- said out loud rather than left for a reader to assume.
    import clusters
    by_cluster = {k: list(v) for k, v in
                  clusters.size_clusters(corpus, as_of=days[-1]).items()}
    print(f"benchmark = equal-weight buy-and-hold of the trade's own cluster "
          f"({', '.join(f'{k}:{len(v)}' for k, v in sorted(by_cluster.items()))})")

    di = {d: i for i, d in enumerate(days)}
    rows, no_bench = [], 0
    for t in closed:
        s = corpus.get(t["sym"])
        i = s.index_of(t["entry_day"]) if s else None
        if i is None:
            continue
        f = learning.entry_features(s, i)
        if not f:
            continue
        b = benchmark_for(corpus, t, by_cluster)
        if b is None:
            no_bench += 1
            continue
        rows.append({**f, "ret": t["ret"], "excess": market.alpha(t["ret"], b),
                     "bench": b, "clu": t["clu"],
                     "_di": di.get(t["entry_day"], 0)})
    print(f"{len(rows)} trades carry both entry features and a benchmark"
          + (f" ({no_bench} had no measurable benchmark)" if no_bench else ""))
    if not rows:
        print("nothing to measure")
        return {}

    blocks = cluster_se.blocks([r["_di"] for r in rows], selection.HOLD_DAYS)
    mean_b = statistics.fmean(r["bench"] for r in rows)
    print(f"mean benchmark over the held windows: {mean_b:+.2f}%\n")

    print(f"  {'feature':<12}{'raw':>9}{'excess':>9}{'shift':>9}"
          f"{'se(iid)':>9}{'se(clu)':>9}{'t':>7}{'n':>6}  reading")
    out = {}
    for f in SCORED:
        raw = tercile_spread(rows, f, "ret")
        exc = tercile_spread(rows, f, "excess")
        if raw is None or exc is None:
            print(f"  {f:<12}{'--':>9}{'--':>9}{'--':>9}{'--':>9}{'--':>9}"
                  f"{'--':>7}{0:>6}  too few observations")
            out[f] = None
            continue
        sh = paired_shift(rows, f, lambda r: blocks[r["_di"]])
        t = sh["t_cluster"] if sh else 0.0
        note = ("the market explains part of it" if abs(t) >= FAMILY_BAR
                else "shift inside the noise")
        if (raw["spread"] > 0) != (exc["spread"] > 0):
            note = "SIGN CHANGES -- endpoint 1 met"
        print(f"  {f:<12}{raw['spread']:>+8.2f}%{exc['spread']:>+8.2f}%"
              f"{raw['spread'] - exc['spread']:>+8.2f}%"
              f"{(sh['se_welch'] if sh else 0):>8.2f}%"
              f"{(sh['se_cluster'] if sh else 0):>8.2f}%"
              f"{t:>+7.2f}{raw['n']:>6}  {note}")
        out[f] = {"raw": raw["spread"], "excess": exc["spread"], "shift": sh}

    ok = {f: v for f, v in out.items() if v}
    if ok:
        r_order = [f for f, _ in sorted(ok.items(), key=lambda kv: -kv[1]["raw"])]
        e_order = [f for f, _ in sorted(ok.items(), key=lambda kv: -kv[1]["excess"])]
        print(f"\n  ranking by raw spread:    {' > '.join(r_order)}")
        print(f"  ranking by excess spread: {' > '.join(e_order)}")
        flipped = [f for f, v in ok.items()
                   if (v["raw"] > 0) != (v["excess"] > 0)]
        print(f"\n  ENDPOINT 1 (any sign change): "
              f"{'MET -- ' + ', '.join(flipped) if flipped else 'not met'}")
        print(f"  ENDPOINT 2 (ranking differs): "
              f"{'MET' if r_order != e_order else 'not met'}")
        big = [f for f, v in ok.items()
               if v["shift"] and abs(v["shift"]["t_cluster"]) >= FAMILY_BAR]
        print(f"  family bar |t| >= {FAMILY_BAR} over {len(ok)} features: "
              f"{', '.join(big) if big else 'nothing clears it'}")
        print(f"\n  {len(blocks and set(blocks.values()))} non-overlapping blocks. "
              f"A shift that does not clear the bar is not a reason to move a "
              f"weight, and this file moves none regardless.")
    return out


def clusters_mod():
    import clusters
    return clusters


def _selftest():
    # tercile_spread must reproduce learning.analyse's shape on the same input,
    # or the comparison is against a different statistic than the loop uses.
    rows = [{"x": i, "ret": float(i), "excess": float(i) - 1.0}
            for i in range(90)]
    ts = tercile_spread(rows, "x", "ret")
    assert ts["n"] == 90 and ts["k"] == 30, ts
    assert abs(ts["spread"] - 60.0) < 1e-9, ts
    la = learning.analyse([{"rs": r["x"], "ret": r["ret"]} for r in rows])
    assert abs(la["rs"]["spread"] - ts["spread"]) < 1e-9, (la["rs"], ts)

    # a constant benchmark must shift every tercile equally and cancel
    flat = [{"x": i, "ret": float(i), "excess": float(i) - 5.0} for i in range(90)]
    a = tercile_spread(flat, "x", "ret")["spread"]
    b = tercile_spread(flat, "x", "excess")["spread"]
    assert abs(a - b) < 1e-9, ("a benchmark identical on every trade must not "
                               f"change a difference of means: {a} vs {b}")

    # ...and a benchmark correlated with the feature must NOT cancel; this is
    # the whole hypothesis, so it is asserted rather than assumed.
    tilt = [{"x": i, "ret": float(i), "excess": float(i) - i * 0.5}
            for i in range(90)]
    a2 = tercile_spread(tilt, "x", "ret")["spread"]
    b2 = tercile_spread(tilt, "x", "excess")["spread"]
    assert a2 - b2 > 25.0, (a2, b2)

    # too few rows is None, never a number computed from nothing
    assert tercile_spread(rows[:10], "x", "ret") is None
    # a constant feature carries no information and must not borrow neighbouring
    # order -- the guard learning.analyse documents
    assert tercile_spread([{"x": 1, "ret": float(i)} for i in range(90)],
                          "x", "ret") is None

    # diff_means names the difference "spread", not "diff" -- checked against
    # cluster_se rather than assumed, after this line was written from memory
    # and raised KeyError on its first run.
    sh = paired_shift(flat, "x", lambda r: 0)
    assert sh is not None and abs(sh["spread"]) < 1e-9, sh
    assert {"se_welch", "se_cluster", "t_cluster"} <= set(sh), sorted(sh)
    # run() is not exercised here -- it needs the corpus and a full backtest --
    # so the names it reaches into are asserted instead. Both of this file's
    # first-run failures were exactly this: a dict key and a function name
    # written from memory, in the one code path the selftest does not execute.
    assert hasattr(clusters_mod(), "size_clusters"), "clusters.size_clusters gone"
    for m, name in ((market, "equal_weight_return"), (market, "alpha"),
                    (learning, "entry_features"), (cluster_se, "diff_means"),
                    (cluster_se, "blocks"), (simulate, "run"),
                    (feat, "load_corpus")):
        assert hasattr(m, name), f"{m.__name__}.{name} is gone"
    import inspect
    src = inspect.getsource(simulate.run)
    assert '"trades": closed' in src, \
        "simulate.run no longer returns its trades under 'trades'"
    for k in ("ret", "entry_day", "sym", "clu"):
        assert f'"{k}"' in src, f"simulate.run stopped recording {k}"
    print("excess_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        bad = [a for a in sys.argv[1:] if a.startswith("-")]
        if bad:
            raise SystemExit(f"unknown flag {bad[0]!r}; use --selftest")
        run()
