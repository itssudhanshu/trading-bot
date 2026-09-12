#!/usr/bin/env python3
"""Do fundamentals carry information the price features do not?

Measured the ONLY honest way: on trades sampled at RANDOM from the universe,
never on trades a fundamental score helped choose. Measuring a feature on its
own selections is what made `deliv` look backwards and cost 26 CAGR points.

For each feature the universe is split at its median on the entry date, and the
question is whether the top half outperforms the bottom half over the holding
period. A spread near zero means the feature is decoration.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import random
import statistics

import cluster_se
import features as F
import fundamentals
import selection

# READ from the strategy, never a copy. This file carried HOLD = 15 while the
# bucket has held 10 since L52, so every fundamentals figure this repo has
# published -- including the four-feature null -- was measured over a horizon
# the book does not trade. impact_test.py had the identical defect, fixed it,
# and left the assertion below as the pattern.
HOLD, STOP, TARGET = selection.HOLD_DAYS, selection.STOP_PCT, selection.TARGET_PCT
assert HOLD == selection.HOLD_DAYS, "fund_test measured a hold the bucket does not run"
FEATS = ("rev_growth", "profit_growth", "margin", "margin_change")

# Four features are tested together, so the bar is family-wise, not per-feature.
# 2.6 is this repo's own precedent -- the bar `ann_tone` was held to in
# tone_test, where it read t = 1.71 and was not adopted -- and Bonferroni at
# four tests gives 2.50, so the precedent is the stricter of the two. Stated
# here, before any number is read, because picking a bar afterwards is the move
# this project forbids.
FAMILY_BAR = 2.6

# What this design can actually resolve, computed from the published per-trade
# spread (0.65% std err at n = 1,049 implies a per-trade sd near 10.5%):
#
#     n =  1,000   std err 0.66%   resolvable at the family bar: 1.73%
#     n =  2,000   std err 0.47%   resolvable:                   1.22%
#     n =  4,000   std err 0.33%   resolvable:                   0.87%
#
# So a real 1.2% spread is INVISIBLE at n = 1,000. The 2026-09-12 run measured
# std err 0.57% at n = 1,038, so the smallest resolvable spread here is 1.48%.
#
# RAISING n DOES NOT CLEANLY FIX THIS, and an earlier note in this file said it
# did. `sample()` draws 60 dates ~23 sessions apart, which at a 10-day hold
# barely overlap. Reaching the ~4,300 trades that would resolve +0.73% needs
# either dates closer together than the holding period -- overlapping windows
# sharing the same market moves -- or more names per date, which share that
# date's move. Either way the extra observations are correlated, the Welch
# standard error above assumes they are not, and `t` would rise without the
# evidence rising with it.
#
# The honest upgrade is a date-clustered or block-bootstrapped standard error.
# That is a methodology change and needs its own pre-registration, not a knob
# turned mid-result.
#
# What this design CAN say is bounded, and worth stating plainly: at std err
# 0.57% not one of the price features quoted below would clear the family bar
# either -- deliv +1.22% reads t = +2.14. A test that cannot resolve the feature
# carrying the raised weight in the live score has not shown anything is flat.


def sample(corpus, days, n_dates=60, per_date=40, seed=11):
    """-> [{feature values, ret}] over randomly chosen symbol-dates."""
    rng = random.Random(seed)
    out = []
    step = max(1, (len(days) - 320) // n_dates)
    for di in range(300, len(days) - HOLD - 1, step):
        day = days[di]
        day_iso = day.isoformat()
        syms = [s for s in corpus if corpus[s].index_of(day) is not None]
        for sym in rng.sample(syms, min(per_date, len(syms))):
            s = corpus[sym]
            i = s.index_of(day)
            if i is None or i < 200 or i + 1 >= len(s):
                continue
            e = s.open[i + 1]
            if not e:
                continue
            f = fundamentals.features_asof(getattr(s, "fund", []) or [], day_iso)
            if not f:
                continue
            stop, tgt = e * (1 - STOP / 100), e * (1 + TARGET / 100)
            px = s.close[min(i + HOLD, len(s) - 1)]
            for k in range(i + 1, min(i + 1 + HOLD, len(s))):
                if s.low[k] <= stop:
                    px = min(stop, s.open[k]); break
                if s.high[k] >= tgt:
                    px = max(tgt, s.open[k]); break
            f["ret"] = (px / e - 1) * 100
            # The session index travels with the row. Without it the clustered
            # standard error has nothing to cluster ON, and the returns of forty
            # names drawn on one date are not forty independent observations.
            f["_di"] = di
            out.append(f)
    return out


def spread(rows, feat):
    """-> the full result dict from `cluster_se.diff_means`, or None.

    Returned a point estimate alone until 2026-09-12, and `main` then labelled
    anything past a hardcoded 0.5% as "does better" -- about 0.8 standard
    errors, well inside the noise.

    Both standard errors are carried: Welch, which assumes every row is an
    independent observation, and cluster-robust by non-overlapping time block,
    which does not. `imbalance` is reported beside them because it is the whole
    mechanism -- a date's market move only leaks into a difference of means when
    that date's upper/lower split is lopsided. Where the splits are even, the
    two standard errors agree and the clustering has bought nothing, which is a
    result worth seeing rather than assuming either way.
    """
    vals = [r[feat] for r in rows if feat in r]
    if len(vals) < 100:
        return None
    med = statistics.median(vals)
    use = [r for r in rows if r.get(feat) is not None]
    group = [1 if r[feat] > med else 0 for r in use]
    if sum(group) < 50 or len(group) - sum(group) < 50:
        return None
    blk = cluster_se.blocks([r["_di"] for r in use], HOLD)
    out = cluster_se.diff_means([r["ret"] for r in use], group,
                                [blk[r["_di"]] for r in use])
    if out is None:
        return None
    out["imbalance"] = _imbalance(use, group)
    return out


def _imbalance(use, group):
    """-> the sd of each date's share of upper-half rows, across dates.

    0.0 means every date split evenly and a common market move cancels out of
    the difference; the Monte Carlo in cluster_se puts Welch within 1% of the
    truth there. It rises as dates become lopsided, and at 0.28 Welch understates
    the true sampling spread by 31%.
    """
    per = {}
    for r, d in zip(use, group):
        hi, n = per.get(r["_di"], (0, 0))
        per[r["_di"]] = (hi + d, n + 1)
    shares = [hi / n for hi, n in per.values() if n >= 5]
    return statistics.pstdev(shares) if len(shares) > 1 else 0.0


def main():
    corpus = F.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    rows = sample(corpus, days)
    print(f"{len(rows)} randomly-sampled trades with fundamentals visible")
    print(f"  hold {HOLD}d, stop {STOP}%, target {TARGET}% "
          f"(read from selection.py)\n")
    print(f"  {'feature':<15}{'spread':>9}{'se(iid)':>9}{'se(clust)':>11}"
          f"{'t':>7}{'n':>6}{'blk':>5}   reading")
    res = {}
    for f in FEATS:
        r = spread(rows, f)
        res[f] = r
        if r is None:
            print(f"  {f:<15}{'--':>9}{'--':>9}{'--':>11}{'--':>7}"
                  f"{0:>6}{0:>5}   too few observations")
            continue
        t = r["t_cluster"]
        verdict = ("CLEARS the family bar" if abs(t) >= FAMILY_BAR else
                   "inside the noise")
        if not r["clusters_trusted"]:
            verdict += f" (only {r['clusters']} blocks -- se is optimistic)"
        print(f"  {f:<15}{r['spread']:>+8.2f}%{r['se_welch']:>8.2f}%"
              f"{r['se_cluster']:>10.2f}%{t:>+7.2f}{r['n']:>6}"
              f"{r['clusters']:>5}   {verdict}")
    print()
    imb = [r["imbalance"] for r in res.values() if r]
    if imb:
        widen = [r["se_cluster"] / r["se_welch"] for r in res.values()
                 if r and r["se_welch"]]
        print(f"  Per-date upper/lower imbalance {statistics.fmean(imb):.3f} "
              f"(sd of each date's upper-half share).")
        print(f"  Clustering widened the error bar by "
              f"x{statistics.fmean(widen):.2f} on average.")
        print("  A date's market move cancels out of a difference of means when")
        print("  that date splits evenly; it leaks in proportion to how lopsided")
        print("  the split is. At imbalance 0.00 Welch is honest to within 1%;")
        print("  at 0.28 it understates the true spread by 31% (cluster_se).")
    print()
    print("  For scale, the price features measured the same way:")
    print("    deliv +1.22%   liq -1.09%   off_high +0.30%   rs -0.03%")
    print(f"\n  Family bar |t| >= {FAMILY_BAR} over {len(FEATS)} features tested")
    print("  together. The largest of four point estimates is not a t-test, and")
    print("  reading the max as the finding is how a noise search produces one.")
    passed = [(k, v) for k, v in res.items()
              if v and v["t_cluster"] is not None
              and abs(v["t_cluster"]) >= FAMILY_BAR]
    if not passed:
        print("\n  VERDICT: no fundamental feature clears the bar. That is not the")
        print("  same as proving they carry nothing -- it is the sample being too")
        print("  small to resolve an effect this size.")
    else:
        for k, v in passed:
            print(f"\n  VERDICT: {k} clears at {v['spread']:+.2f}% +/- "
                  f"{v['se_cluster']:.2f}% clustered "
                  f"(t {v['t_cluster']:+.2f}, n {v['n']}, "
                  f"{v['clusters']} blocks).")
        print("  Univariate significance is NOT marginal value to the bucket:")
        print("  rs had the highest t of any feature measured here and weighting")
        print("  it up produced the worst of five books. A weight needs a")
        print("  pre-registered simulation against the live book, not this table.")
    return res


if __name__ == "__main__":
    main()
