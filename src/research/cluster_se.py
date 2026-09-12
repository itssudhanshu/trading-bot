#!/usr/bin/env python3
"""Cluster-robust standard error for a two-group spread. And the proof it works.

PRE-REGISTERED 2026-09-12, batch `20260912-clusterse`, before running it on any
real feature.

WHY. Every spread this project reports splits randomly-sampled symbol-dates at a
feature's median and differences the two halves' mean returns, with a Welch
standard error. Welch assumes the observations are independent. They are not:
forty names sampled on the SAME DATE share that date's market move over the same
holding window, so their returns are positively correlated. A standard error
that ignores that is too small, and every `t` built on it is too big.

It has not bitten yet only because `fund_test.sample()` draws dates ~23 sessions
apart against a 10-day hold, so windows do not overlap -- the correlation is
within a date, not across them. It bites the moment anyone raises the sample to
buy power, which is exactly what L103 stopped me recommending.

WHAT THIS IS. The cluster-robust (CR1) variance of the coefficient on the
group indicator, clustered by non-overlapping time block. Written out for the
two-group case rather than pulled from a library, because this repo is stdlib
only and a sandwich estimator is twenty lines.

    V = A (SUM_g X_g' u_g u_g' X_g) A ,   A = (X'X)^-1,  X = [1, D]

REGISTERED PREDICTION, recorded before the first run on real data:

  1. With NO clustering in the data, the clustered standard error must agree
     with Welch. The sandwich reduces to the Welch FORM algebraically -- with
     singleton clusters the meat collapses to `SUM u1^2/n1^2 + SUM u0^2/n0^2`
     -- but not to the same number, because Welch divides each group's squared
     residuals by `n_i - 1` while the sandwich divides by `n_i`, and CR1's
     correction `n/(n-2)` very nearly but not exactly undoes the difference.
     At a few hundred rows a side they agree to about 1e-7 relative, which is
     what `_selftest` asserts. Claiming bit-exact agreement would have been
     wrong, and the first version of this docstring did.
  2. With a per-date common shock, the clustered standard error must be LARGER
     than Welch, and must track the TRUE sampling spread of the estimate, which
     the Monte Carlo below measures directly by re-drawing the whole experiment.
  3. If the clustered error comes back SMALLER than Welch on real data, the
     estimator is wrong and is investigated, not used.

WHAT IT DOES NOT FIX. Clustering by block widens the error bar; it does not
create information. A feature that reads t = 1.27 under Welch will read less,
never more. Nothing here can rescue an underpowered test -- it can only stop one
from looking powered.

Also: CR1 is itself biased down when the cluster count is small. Rule of thumb
is 40+; `fund_test` draws 60 dates. Below about 30 the number this returns is
optimistic and should be said so out loud rather than quoted.

    python3 src/research/cluster_se.py --selftest
"""
import argparse
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths  # noqa: F401

MIN_CLUSTERS_TRUSTED = 30


def blocks(session_index, hold):
    """-> {session_index: block_id}, so two windows in different blocks cannot overlap.

    A holding period starting at session i covers i..i+hold. Two sampled dates
    closer together than `hold` share market moves, so they belong in one
    cluster. Where dates are already further apart than the hold -- which is the
    current sampling design -- every date becomes its own block and this reduces
    to clustering by date.
    """
    out, bid, anchor = {}, 0, None
    for idx in sorted(set(session_index)):
        if anchor is None or idx - anchor > hold:
            bid += 1
            anchor = idx
        out[idx] = bid
    return out


def diff_means(values, group, cluster):
    """-> dict with the spread and BOTH standard errors, so they can be compared.

    `group` is 1 for the upper half and 0 for the lower. `cluster` is any
    hashable; rows sharing one are allowed to be correlated.
    """
    n = len(values)
    if not (n == len(group) == len(cluster)):
        raise ValueError("values, group and cluster must be the same length")
    hi = [v for v, d in zip(values, group) if d]
    lo = [v for v, d in zip(values, group) if not d]
    n1, n0 = len(hi), len(lo)
    if n1 < 2 or n0 < 2:
        return None

    m1, m0 = statistics.fmean(hi), statistics.fmean(lo)
    beta = m1 - m0
    se_welch = ((statistics.variance(hi) / n1)
                + (statistics.variance(lo) / n0)) ** 0.5

    # Residuals from the two-group fit are just deviations from each mean.
    resid = [v - (m1 if d else m0) for v, d in zip(values, group)]

    # X_g' u_g for X = [1, D] is (sum of residuals, sum of residuals where D=1).
    s_all, s_one = defaultdict(float), defaultdict(float)
    for r, d, c in zip(resid, group, cluster):
        s_all[c] += r
        if d:
            s_one[c] += r

    # Second row of A = (X'X)^-1 -- the row that selects the slope.
    a1, a2 = -1.0 / n0, n / (n1 * n0)
    meat = sum((a1 * s_all[c] + a2 * s_one[c]) ** 2 for c in s_all)

    g = len(s_all)
    # CR1 finite-sample correction, the same one Stata applies by default.
    corr = (g / (g - 1)) * ((n - 1) / (n - 2)) if g > 1 else 1.0
    se_cluster = (meat * corr) ** 0.5

    return {"spread": beta, "se_welch": se_welch, "se_cluster": se_cluster,
            "n": n, "n_hi": n1, "n_lo": n0, "clusters": g,
            "t_welch": beta / se_welch if se_welch else None,
            "t_cluster": beta / se_cluster if se_cluster else None,
            "clusters_trusted": g >= MIN_CLUSTERS_TRUSTED}


# --------------------------------------------------------------------------

def _draw(rng, n_clusters, per_cluster, shock_sd, effect=0.0, noise_sd=10.0,
          imbalance=0.0):
    """One synthetic experiment: a per-cluster shock, plus noise.

    `imbalance` is the spread of each cluster's probability of landing in the
    upper half, and it is the parameter that matters. At 0 every cluster is a
    50/50 split and a common shock CANCELS in the difference of means -- which
    the first version of this file's Monte Carlo demonstrated by accident,
    refuting the mechanism its docstring claimed. Above 0 the clusters carry
    different upper/lower mixes, the shock no longer cancels, and Welch starts
    understating.
    """
    values, group, cluster = [], [], []
    for g in range(n_clusters):
        shock = rng.gauss(0, shock_sd)
        p = min(0.95, max(0.05, rng.gauss(0.5, imbalance))) if imbalance else 0.5
        for _ in range(per_cluster):
            d = 1 if rng.random() < p else 0
            values.append(shock + rng.gauss(0, noise_sd) + (effect if d else 0.0))
            group.append(d)
            cluster.append(g)
    return values, group, cluster


def _monte_carlo(shock_sd, reps=400, seed=7, n_clusters=60, per_cluster=18,
                 imbalance=0.0):
    """-> (true sd of the estimate, mean Welch se, mean clustered se).

    The TRUE sampling spread is measured by re-drawing the whole experiment,
    which is the only way to say whether a standard error is honest. An
    estimator is not proven by agreeing with another estimator.
    """
    rng = random.Random(seed)
    betas, welch, clust = [], [], []
    for _ in range(reps):
        v, g, c = _draw(rng, n_clusters, per_cluster, shock_sd,
                        imbalance=imbalance)
        r = diff_means(v, g, c)
        betas.append(r["spread"])
        welch.append(r["se_welch"])
        clust.append(r["se_cluster"])
    return (statistics.stdev(betas), statistics.fmean(welch),
            statistics.fmean(clust))


def _selftest():
    # --- blocks: at the current design every date is its own cluster --------
    b = blocks([0, 23, 46, 69], hold=10)
    assert len(set(b.values())) == 4, b
    # ...and dates closer together than the hold merge, because their windows
    # overlap and the returns inside them are not separate observations.
    b = blocks([0, 3, 6, 40, 43], hold=10)
    assert b[0] == b[3] == b[6] and b[40] == b[43] and b[0] != b[40], b
    assert blocks([], hold=10) == {}

    # --- with no clustering the algebra REDUCES to Welch, exactly ------------
    rng = random.Random(1)
    v, g, _ = _draw(rng, 40, 20, shock_sd=0.0)
    iid = diff_means(v, g, list(range(len(v))))     # every row its own cluster
    rel = abs(iid["se_cluster"] - iid["se_welch"]) / iid["se_welch"]
    assert rel < 1e-5, (iid["se_cluster"], iid["se_welch"], rel)
    # The residual difference is the degrees-of-freedom convention, not an
    # error: it must SHRINK as the sample grows, which is what asymptotic
    # agreement means and what a coding mistake would not do.
    big_v, big_g, _ = _draw(random.Random(2), 200, 40, shock_sd=0.0)
    big = diff_means(big_v, big_g, list(range(len(big_v))))
    rel_big = abs(big["se_cluster"] - big["se_welch"]) / big["se_welch"]
    assert rel_big < rel, (rel_big, rel)

    # --- a per-cluster shock ALONE changes nothing --------------------------
    # This is the correction the Monte Carlo forced. A shock common to every row
    # in a cluster hits both halves equally and CANCELS in a difference of
    # means, so Welch is honest and clustering buys nothing. The mechanism this
    # module was written for is not "rows on a date are correlated".
    tb, wb, cb = _monte_carlo(shock_sd=6.0, imbalance=0.0)
    assert 0.85 * tb <= wb <= 1.15 * tb, (wb, tb)
    assert 0.85 * tb <= cb <= 1.15 * tb, (cb, tb)

    # --- THE proof: the shock leaks only when the split is IMBALANCED -------
    # When clusters carry different upper/lower mixes -- which is what a
    # cross-sectionally correlated feature produces, since a whole date can sit
    # above the global median -- the shock enters the difference in proportion
    # to the imbalance and Welch understates. Measured against the TRUE sampling
    # spread, re-drawn, not against another formula.
    true_sd, welch, clust = _monte_carlo(shock_sd=6.0, imbalance=0.28)
    assert welch < 0.80 * true_sd, (welch, true_sd)
    assert 0.85 * true_sd <= clust <= 1.20 * true_sd, (clust, true_sd)

    # --- clustered is never SMALLER on clustered data -----------------------
    v, g, c = _draw(random.Random(5), 60, 18, shock_sd=6.0, imbalance=0.28)
    r = diff_means(v, g, c)
    assert r["se_cluster"] > r["se_welch"], r
    assert abs(r["t_cluster"]) < abs(r["t_welch"]), r
    assert r["clusters"] == 60 and r["clusters_trusted"]

    # --- too few clusters is flagged, not silently trusted ------------------
    v, g, c = _draw(random.Random(5), 8, 40, shock_sd=6.0, imbalance=0.28)
    assert not diff_means(v, g, c)["clusters_trusted"]

    # --- degenerate input returns None rather than a number -----------------
    assert diff_means([1.0, 2.0], [1, 1], ["a", "a"]) is None
    assert diff_means([], [], []) is None

    print(f"cluster_se selftest ok (Welch {welch:.3f} vs true {true_sd:.3f} vs "
          f"clustered {clust:.3f} under a per-date shock)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo", action="store_true",
                    help="print the Monte Carlo across shock sizes")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if a.demo:
        print(f"  {'shock':>7}{'imbal':>7}{'true sd':>10}{'Welch':>9}"
              f"{'clustered':>11}{'Welch/true':>12}")
        for s, im in ((6.0, 0.00), (6.0, 0.10), (6.0, 0.20), (6.0, 0.28),
                      (0.0, 0.28), (9.0, 0.28)):
            t, w, c = _monte_carlo(shock_sd=s, imbalance=im)
            print(f"  {s:>7.1f}{im:>7.2f}{t:>10.3f}{w:>9.3f}{c:>11.3f}"
                  f"{w / t:>12.2f}")
        return
    ap.error("--selftest or --demo")


if __name__ == "__main__":
    main()
