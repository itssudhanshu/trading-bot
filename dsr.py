#!/usr/bin/env python3
"""Deflated Sharpe Ratio -- quantifies the multiple-testing inflation that this
project has so far only warned about in prose (lessons L7).

Searching ~1,000 specs and reporting the best one's Sharpe is the textbook way
to be fooled: the maximum over N trials is inflated even when every candidate is
pure noise. The DSR asks the right question -- is this Sharpe larger than the
best you would expect from N coin flips?

Formulas from Bailey & Lopez de Prado, "The Deflated Sharpe Ratio: Correcting
for Selection Bias, Backtest Overfitting and Non-Normality" (2014), verified
against two independent sources rather than written from memory:

    E[max SR] ~= sqrt(V[SR]) * ( (1-g)*Z^-1[1 - 1/N] + g*Z^-1[1 - 1/(N*e)] )
    PSR(SR*)   = Z[ (SR - SR*) * sqrt(T-1) / sqrt(1 - g3*SR + (g4-1)/4 * SR^2) ]
    DSR        = PSR evaluated at SR* = E[max SR]

g  = Euler-Mascheroni ~= 0.5772
g3 = skewness of RETURNS (not of the trial Sharpes)
g4 = kurtosis of RETURNS (non-excess: 3.0 for a normal distribution)
N  = number of independent trials, T = number of return observations
"""
import math
import statistics
import sys

EULER_MASCHERONI = 0.5772156649015329
_N = statistics.NormalDist()


def expected_max_sr(n_trials: int, sr_variance: float, sr_mean: float = 0.0) -> float:
    """Best Sharpe expected from `n_trials` draws of pure noise.

    Undefined for n_trials < 2: Z^-1[1 - 1/1] = Z^-1[0] = -inf.
    """
    if n_trials < 2:
        raise ValueError("expected_max_sr needs at least 2 trials")
    sd = math.sqrt(max(sr_variance, 0.0))
    a = _N.inv_cdf(1.0 - 1.0 / n_trials)
    b = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return sr_mean + sd * ((1.0 - EULER_MASCHERONI) * a + EULER_MASCHERONI * b)


def psr(sr: float, benchmark: float, n_obs: int, skew: float, kurt: float) -> float:
    """Probabilistic Sharpe Ratio: P(true SR > benchmark), given the sample.

    `kurt` is NON-excess (3.0 for a normal). Fat tails and negative skew both
    widen the denominator, which is the point -- a Sharpe earned by selling
    tail risk should not read as confidently as a symmetric one.
    """
    if n_obs < 2:
        return float("nan")
    denom_sq = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom_sq <= 0:
        return float("nan")
    return _N.cdf((sr - benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq))


def sharpe(returns) -> float:
    """Per-period Sharpe, not annualised. Annualising here would silently
    inflate the DSR, which compares like with like."""
    if len(returns) < 2:
        return 0.0
    sd = statistics.pstdev(returns)
    return statistics.fmean(returns) / sd if sd > 0 else 0.0


def moments(returns) -> tuple:
    """-> (skewness, non-excess kurtosis) of the return series."""
    n = len(returns)
    if n < 4:
        return 0.0, 3.0
    m = statistics.fmean(returns)
    sd = statistics.pstdev(returns)
    if sd == 0:
        return 0.0, 3.0
    s3 = sum((r - m) ** 3 for r in returns) / n / sd ** 3
    s4 = sum((r - m) ** 4 for r in returns) / n / sd ** 4
    return s3, s4


def deflated_sharpe(returns, trial_sharpes) -> dict:
    """-> {sr, expected_max_sr, dsr, n_trials, verdict}.

    `trial_sharpes` must be the Sharpes of EVERY candidate tested, not only the
    survivors. Passing the survivors understates the variance across trials and
    therefore understates E[max SR] -- flattering exactly the number this is
    meant to deflate.
    """
    n = len(trial_sharpes)
    sr = sharpe(returns)
    g3, g4 = moments(returns)
    emax = expected_max_sr(n, statistics.pvariance(trial_sharpes)) if n >= 2 else 0.0
    d = psr(sr, emax, len(returns), g3, g4)
    return {
        "sr": sr, "expected_max_sr": emax, "dsr": d, "n_trials": n,
        "n_obs": len(returns), "skew": g3, "kurt": g4,
        # 0.95 is the conventional threshold; below it, the result is not
        # distinguishable from the best of N noise draws.
        "verdict": "SIGNIFICANT" if d > 0.95 else "NOT DISTINGUISHABLE FROM NOISE",
    }


def _selftest():
    # E[max SR] must grow with the number of trials -- more draws, luckier max
    v = 1.0
    vals = [expected_max_sr(n, v) for n in (2, 10, 100, 1000, 10000)]
    assert vals == sorted(vals), vals
    assert all(b > a for a, b in zip(vals, vals[1:])), vals

    # verified numeric: unit variance across 1,000 trials
    #   (1-0.5772)*Z^-1(0.999) + 0.5772*Z^-1(1 - 1/(1000e))
    #   = 0.4228*3.0902 + 0.5772*3.3782 ~= 3.256
    e1000 = expected_max_sr(1000, 1.0)
    assert 3.20 < e1000 < 3.31, e1000
    # and it sits near the sqrt(2 ln N) rule of thumb (3.72 for N=1000)
    assert abs(e1000 - math.sqrt(2 * math.log(1000))) < 0.6, e1000

    # zero variance across trials => no selection inflation possible
    assert expected_max_sr(1000, 0.0) == 0.0

    try:
        expected_max_sr(1, 1.0)
        raise AssertionError("N=1 must raise, not return -inf")
    except ValueError:
        pass

    # PSR is 0.5 when the observed Sharpe equals the benchmark
    assert abs(psr(0.1, 0.1, 250, 0.0, 3.0) - 0.5) < 1e-9

    # negative skew and fat tails must REDUCE confidence at equal Sharpe
    base = psr(0.15, 0.0, 500, 0.0, 3.0)
    skewed = psr(0.15, 0.0, 500, -1.5, 3.0)
    fat = psr(0.15, 0.0, 500, 0.0, 9.0)
    assert skewed < base and fat < base, (base, skewed, fat)

    # a good-looking Sharpe from many trials must not survive deflation
    import random
    rng = random.Random(0)
    noise = [rng.gauss(0.001, 0.02) for _ in range(500)]
    trials = [rng.gauss(0.0, 0.05) for _ in range(1000)]
    out = deflated_sharpe(noise, trials)
    assert out["expected_max_sr"] > 0, out
    assert out["dsr"] < 0.95, f"noise passed deflation: {out}"
    assert out["verdict"].startswith("NOT"), out

    # a genuinely strong series, few trials, must pass
    strong = [rng.gauss(0.004, 0.01) for _ in range(500)]
    out2 = deflated_sharpe(strong, [rng.gauss(0.0, 0.02) for _ in range(5)])
    assert out2["dsr"] > 0.95, out2
    print("dsr selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        for n in (10, 100, 1000, 10000):
            print(f"  N={n:>6}  E[max SR] = {expected_max_sr(n, 1.0):.3f}  (unit variance)")
