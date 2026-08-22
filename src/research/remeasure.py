"""Re-measure the knobs that were decided on phantom fills (L58).

Every CAGR in CLAUDE.md's error-bar table was measured before the circuit-lock
guard, so 8.7% of the fills behind those numbers could not have been got. The
guard cost the live configuration 6.5 CAGR points -- MORE than the gaps two of
those verdicts were decided on, which is the whole reason this file exists.

Reports the CAGR gap AND the per-trade edge with its standard error, because a
CAGR gap on one path is arithmetic, not evidence: per-trade returns here have a
standard deviation near 16%, so at ~200 trades nothing under about 3 points per
trade is resolvable. A verdict is only re-decided if the edge clears its own
error bar -- which, on this project's record, it almost never does.

    python3 src/research/remeasure.py
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import statistics
import sys

import features, selection, simulate

BATCH = "20260819-postlock"

# The live bucket, exactly as src/strategies/breakout/selection.py runs it. Not simulate.run's
# defaults, which still carry the old 15-day hold.
LIVE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            take_per_cluster=dict(selection.TAKE_PER_CLUSTER),
            trigger=selection.TRIGGER)

VARIANTS = [
    ("live 3/2 10d breakout", {}),
    ("hold 15d (the old rule)", dict(hold=15)),
    ("mix 2 micro / 3 small", dict(take_per_cluster={"micro": 2, "small": 3})),
    ("no trigger", dict(trigger="none")),
]


def edge(r):
    """-> (mean per-trade return, its standard error, n)."""
    t = [x["ret"] for x in r["trades"]]
    if len(t) < 2:
        return float("nan"), float("nan"), len(t)
    sd = statistics.stdev(t)
    return statistics.fmean(t), sd / len(t) ** 0.5, len(t)


def gap(a, b):
    """Welch on two independent trade samples -> (difference, std err, t).

    Independent is an approximation: both books trade the same corpus over the
    same dates, so a name held by both contributes to each. That correlation
    makes the true error bar SMALLER than this one, so the test is conservative
    -- it can fail to resolve a real difference, and cannot manufacture one.
    """
    ma, sa, na = edge(a)
    mb, sb, nb = edge(b)
    se = (sa ** 2 + sb ** 2) ** 0.5
    return ma - mb, se, (ma - mb) / se if se else float("nan")


def slope(xs, ys):
    """OLS of ys on xs -> (slope, std err, t).

    This is the arithmetic behind the one claim in this project that clears its
    error bar: per-trade return against rank-cohort depth. Regressed over the
    individual TRADES, not the six cohort means -- six points would give an
    error bar built from four degrees of freedom and would resolve almost
    anything.
    """
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    mse = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys)) / (n - 2)
    se = (mse / sxx) ** 0.5
    return b, se, (b / se if se else float("nan"))


def main():
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"POST-LOCK RE-MEASUREMENT  {days[300]} .. {days[-1]}  "
          f"Rs {selection.CAPITAL:,}  batch {BATCH}\n")

    out = {}
    for name, over in VARIANTS:
        r = simulate.run(corpus, days, **{**LIVE, **over})
        out[name] = r
        m, se, n = edge(r)
        win = sum(1 for x in r["trades"] if x["ret"] > 0) / max(n, 1) * 100
        print(f"  {name:<26} CAGR {r['cagr']:>+6.2f}%  DD {r['maxdd']:>5.1f}%  "
              f"n={n:>4}  win {win:>3.0f}%  "
              f"per-trade {m:>+6.2f}% +/- {se:.2f}%")
        simulate.store(name, r, batch=BATCH)
        sys.stdout.flush()

    base = out["live 3/2 10d breakout"]
    print("\n  each variant against the live bucket, per trade:")
    for name in out:
        if name == "live 3/2 10d breakout":
            continue
        d, se, t = gap(base, out[name])
        verdict = "RESOLVED" if abs(t) > 2 else "inside the noise"
        print(f"  live - {name:<26} {d:>+6.2f}% +/- {se:.2f}%  "
              f"t={t:>+5.2f}  {verdict}")
    return out


def _selftest():
    """gap() must not call a difference resolved that its own error bar covers.

    This is the arithmetic every verdict in CLAUDE.md rests on, and the one
    thing that would quietly invalidate all of them is a sign or a sqrt in the
    wrong place.
    """
    same = {"trades": [{"ret": x} for x in (-10, 0, 10, 5, -5) * 20]}
    wide = {"trades": [{"ret": x + 1.0} for x in (-10, 0, 10, 5, -5) * 20]}
    huge = {"trades": [{"ret": x + 40.0} for x in (-10, 0, 10, 5, -5) * 20]}
    d, se, t = gap(same, same)
    assert d == 0 and abs(t) < 1e-9, (d, se, t)
    d, se, t = gap(same, wide)
    assert abs(d - -1.0) < 1e-9 and abs(t) < 2, \
        f"a 1% shift inside a 16%-sd sample must not resolve: t={t}"
    d, se, t = gap(same, huge)
    assert abs(t) > 2, f"a 40% shift must resolve: t={t}"
    # and the error bar must shrink as the sample grows, or n is being misused
    small = {"trades": same["trades"][:10]}
    assert edge(small)[1] > edge(same)[1]

    # slope() reads a clean line exactly, and must NOT resolve a slope that is
    # small against the scatter around it -- the failure that would turn every
    # noisy cohort ordering into a finding.
    xs = list(range(60))
    b, se, t = slope(xs, [2.0 * x + 1 for x in xs])
    assert abs(b - 2.0) < 1e-9 and se < 1e-9, (b, se)
    noisy = [(-1.0) ** x * 16.0 + 0.05 * x for x in xs]   # sd ~16, slope 0.05
    b, se, t = slope(xs, noisy)
    assert abs(t) < 2, f"a 0.05 slope under 16% scatter must not resolve: t={t}"
    b, se, t = slope(xs, [(-1.0) ** x * 16.0 + 2.0 * x for x in xs])
    assert abs(t) > 2, f"a 2.0 slope under the same scatter must resolve: t={t}"
    print("remeasure selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
