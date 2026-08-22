#!/usr/bin/env python3
"""H7: is a trigger's drawdown advantage real, or is it just trading less?

PRE-REGISTERED 2026-08-22, before any trigger has been measured on this endpoint.

THE UNCOMFORTABLE PART, STATED FIRST
------------------------------------
`asc_triangle` is being tested BECAUSE its drawdown looked good. H5 registered
it as description-only with no adoption path, it came back at 10.4% max drawdown
against breakout's 31.0%, and that number is why this file exists. Choosing what
to test after seeing which arm won is the exact habit this project's lessons
file exists to prevent, and pretending otherwise would be worse than the bias.

Two things make it a legal test anyway, and both are structural rather than
rhetorical:

  1. EVERY trigger is measured here, not the one that looked good, and the bar
     is Bonferroni-corrected across all of them. That correction is precisely
     the tool for "I looked at ten and picked the best" -- it prices the search
     in rather than pretending it did not happen.
  2. Drawdown has never had a criterion in this project. L62 records the same
     hole for bucket size: the pre-set bar had no adoption path for drawdown
     IMPROVING, only a veto on it worsening. A first criterion for a dimension
     is a new test, not a re-run of an old one.

What this CANNOT do is license adopting asc_triangle on this data alone. It can
establish whether the effect is resolvable, which is what would justify a
forward or out-of-sample test.

THE PROBLEM WITH 10.4 vs 31.0
-----------------------------
It is one number off one path. maxDD is a single realisation with no error bar
and cannot be compared between configs any more than a single trade can.

The fix is not new: `drawdown_test.py` already solved it for bucket size, and
this imports that method rather than growing a second one. Split the equity
curve into disjoint six-month blocks, compute drawdown INSIDE each, and compare
arms over the same calendar blocks -- which makes it paired, removing regime
from the difference.

THE ALTERNATIVE THIS IS BUILT TO CATCH
--------------------------------------
**A trigger that fires less holds less, and a book that holds less draws down
less.** That is arithmetic, not skill. The H5 numbers line up exactly that way:

    breakout      n=196   DD 31.0%
    asc_triangle  n=145   DD 10.4%
    cup_handle    n= 30   DD 10.0%

Fewer trades, less drawdown, in order. If that is all this is, the ranking of
arms by drawdown will track their occupancy, and buying comfort by not trading
is available for free at any time by trading less -- it needs no pattern
detector.

So the exposure control is the point of the test, the way the long-flat arm was
the point of H4 and the `none` arm the point of H5. Both of those killed their
hypothesis. This one is built to do the same.

ENDPOINT
--------
Paired mean difference in per-block maxDD against the incumbent `breakout`,
with std err and t, over disjoint six-month blocks. Reported with block count,
median, win rate, and a leave-one-out check. Plus, across arms, the correlation
between mean block drawdown and mean occupancy.

THE BAR, fixed here before a single run
---------------------------------------
Ten arms are compared against the control, so Bonferroni gives 0.05/10 = 0.005
and |t| >= 2.81. Tighter than every other bar in this project, deliberately,
because the candidate was chosen after seeing it.

Recommend a trigger on drawdown ONLY if ALL of:

  a. paired mean block-drawdown reduction vs breakout has |t| >= 2.81
  b. NOT one episode: the median difference shares the mean's sign, AND
     dropping the largest-magnitude block still leaves |t| > 1.5
  c. NOT bought by trading less: across arms, the correlation between mean block
     drawdown and mean occupancy must be below 0.70 -- otherwise drawdown is
     tracking exposure and any arm could buy the same comfort by firing less
  d. the per-trade return cost is NOT a resolved loss (|t| < 2 against breakout)

Anything else is reported as "inside the noise" IN THOSE WORDS, and the live
trigger does not move.

    STRATEGY=trellis python3 src/research/trigger_dd_test.py
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import statistics
import sys

import drawdown_test as DD          # the block method, not a second copy of it
import entry
import features
import selection
import simulate

BATCH = "20260822-trigger-dd"
BAR = 2.81                 # Bonferroni over ten arms; see the docstring
MAX_EXPOSURE_R = 0.70      # above this, drawdown is exposure not skill
LOO_MIN = 1.5

CONTROL = selection.TRIGGER          # read, never typed

# Read the live rules.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5)


def measure(corpus, days):
    """-> {trigger: {blocks, occupancy, trades, cagr, maxdd, per_trade}}."""
    out = {}
    for name in entry.TRIGGERS:
        entry._CACHE.clear()
        if hasattr(selection, "_ATR"):
            selection._ATR.clear()
        r = simulate.run(corpus, days, trigger=name, **BASE)
        rets = [t["ret"] for t in r["trades"]]
        out[name] = {
            "blocks": {k: v[0] for k, v in DD.blocks(r["curve"]).items()},
            "occupancy": r["occupancy"], "n": len(rets),
            "cagr": r["cagr"], "maxdd": r["maxdd"],
            "rets": rets,
            "per_trade": statistics.fmean(rets) if rets else float("nan"),
        }
    return out


def _ret_t(a, b):
    """-> t for per-trade return, arm minus control. Independent samples."""
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = (statistics.stdev(a) ** 2 / len(a)
          + statistics.stdev(b) ** 2 / len(b)) ** 0.5
    return (statistics.fmean(a) - statistics.fmean(b)) / se if se else float("nan")


def _loo(diffs):
    """-> |t| after dropping the single largest-magnitude block."""
    if len(diffs) < 3:
        return float("nan")
    worst = max(diffs, key=lambda k: abs(diffs[k]))
    rest = [v for k, v in diffs.items() if k != worst]
    m, se, n = DD._stats(rest)
    return abs(m / se) if se else float("nan")


def _corr(xs, ys):
    if len(xs) < 3:
        return float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def _verdict(t):
    if t != t:
        return "not enough blocks"
    return "RESOLVED" if abs(t) >= BAR else "inside the noise"


def main():
    if paths.STRATEGY != "trellis":
        print(f"this test belongs to trellis; STRATEGY={paths.STRATEGY}.")
        print("run:  STRATEGY=trellis python3 src/research/trigger_dd_test.py")
        return 1

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"batch {BATCH}   control '{CONTROL}'   bar |t| >= {BAR} "
          f"(Bonferroni, ten arms)\n")
    res = measure(corpus, days)
    ctrl = res[CONTROL]

    print(f"{'trigger':<15}{'n':>5}{'occ':>6}{'CAGR':>8}{'maxDD':>7}"
          f"{'blockDD':>9}{'vs ctrl':>9}{'t':>7}   verdict")
    rows = {}
    for name, r in sorted(res.items(), key=lambda kv: kv[1]["maxdd"]):
        mean_bdd = statistics.fmean(r["blocks"].values()) if r["blocks"] else float("nan")
        line = (f"  {name:<13}{r['n']:>5}{r['occupancy']:>6.2f}"
                f"{r['cagr']:>8.2f}{r['maxdd']:>7.1f}{mean_bdd:>9.2f}")
        if name == CONTROL:
            print(f"{line}{'--':>9}{'--':>7}   control")
            rows[name] = (0.0, 0.0, 0, {})
            continue
        m, se, n, t, diffs = DD.paired(r["blocks"], ctrl["blocks"])
        rows[name] = (m, se, n, diffs)
        print(f"{line}{m:>+8.2f}%{t:>7.2f}   {_verdict(t)}")

    # --- the control that decides it ---------------------------------------
    names = [k for k in res if res[k]["blocks"]]
    occ = [res[k]["occupancy"] for k in names]
    bdd = [statistics.fmean(res[k]["blocks"].values()) for k in names]
    r_exp = _corr(occ, bdd)
    print(f"\nis drawdown just exposure?")
    print(f"  correlation(occupancy, mean block drawdown) across "
          f"{len(names)} arms = {r_exp:+.3f}")
    print(f"  {'EXPOSURE -- comfort bought by trading less' if abs(r_exp) >= MAX_EXPOSURE_R else 'not explained by exposure'}"
          f"  (threshold {MAX_EXPOSURE_R})")

    # --- the arm this file was written for ---------------------------------
    tgt = "asc_triangle"
    if tgt not in res:
        print(f"\n{tgt} is not a registered trigger.")
        return 1
    m, se, n, diffs = rows[tgt]
    t = m / se if se else float("nan")
    med = statistics.median(diffs.values()) if diffs else float("nan")
    win = (sum(1 for v in diffs.values() if v < 0) / len(diffs) * 100
           if diffs else float("nan"))
    loo = _loo(diffs)
    rt = _ret_t(res[tgt]["rets"], ctrl["rets"])

    print(f"\n{tgt} against {CONTROL}, block by block ({n} blocks)")
    print(f"  mean difference   {m:+.2f}%  +/- {se:.2f}   t {t:+.2f}")
    print(f"  median difference {med:+.2f}%")
    print(f"  blocks improved   {win:.0f}%")
    print(f"  leave-one-out |t| {loo:.2f}")
    print(f"  per-trade return  {res[tgt]['per_trade']:+.2f}% vs "
          f"{ctrl['per_trade']:+.2f}%   t {rt:+.2f}")

    print("\nadoption check (all four required):")
    a = t == t and abs(t) >= BAR and m < 0
    print(f"  a  drawdown cut clears |t| >= {BAR}   {m:+.2f}% t {t:+.2f}"
          f"   {'PASS' if a else 'FAIL'}")
    b = (med == med and m == m and (med < 0) == (m < 0)
         and loo == loo and loo > LOO_MIN)
    print(f"  b  not one episode                  median {med:+.2f}, "
         f"LOO |t| {loo:.2f}   {'PASS' if b else 'FAIL'}")
    c = r_exp == r_exp and abs(r_exp) < MAX_EXPOSURE_R
    print(f"  c  not bought by trading less       |r| {abs(r_exp):.3f} < "
          f"{MAX_EXPOSURE_R}   {'PASS' if c else 'FAIL'}")
    d = not (rt == rt and abs(rt) >= 2.0 and rt < 0)
    print(f"  d  return cost not a resolved loss  t {rt:+.2f}"
          f"   {'PASS' if d else 'FAIL'}")

    ok = a and b and c and d
    print(f"\n  -> {'RECOMMEND for an out-of-sample test' if ok else 'DO NOT ADOPT'}: "
          f"{'all four met' if ok else _verdict(t) if not a else 'a condition failed'}")
    if not ok:
        print(f"     TRIGGER stays '{CONTROL}'. And note this arm was chosen")
        print("     after its number was seen, so even passing would license a")
        print("     fresh test, never a change on this data.")
    return 0


def _selftest():
    # the block method is imported, not reimplemented
    assert hasattr(DD, "blocks") and hasattr(DD, "paired"), \
        "the drawdown method moved; this file must not grow its own copy"

    from datetime import date
    curve = [(date(2024, 1, 1 + i), 100 - i) for i in range(25)]
    b = DD.blocks(curve)
    assert b and all(v[0] >= 0 for v in b.values()), b

    # paired difference: identical inputs cancel exactly
    a = {"2024H1": 10.0, "2024H2": 20.0}
    m, se, n, t, d = DD.paired(a, a)
    assert m == 0.0 and n == 2, (m, n)

    # Leave-one-out drops the LARGEST-magnitude block, not the first: changing
    # only that block must leave the statistic identical. The remaining values
    # need real variance, or _stats returns NaN and the check compares NaN to
    # NaN -- which is False, and was how this assertion first failed.
    assert abs(_loo({"a": 1.0, "b": 2.0, "c": 50.0})
               - _loo({"a": 1.0, "b": 2.0, "c": 99.0})) < 1e-9
    # ...and a negative outlier is "largest" too; magnitude, not value.
    assert abs(_loo({"a": 1.0, "b": 2.0, "c": -99.0})
               - _loo({"a": 1.0, "b": 2.0, "c": 50.0})) < 1e-9
    # too few blocks must give NaN, never a confident number
    assert _loo({"a": 1.0}) != _loo({"a": 1.0})

    # correlation, against known cases
    assert abs(_corr([1, 2, 3], [1, 2, 3]) - 1.0) < 1e-9
    assert abs(_corr([1, 2, 3], [3, 2, 1]) + 1.0) < 1e-9

    assert BAR > 2.64, "this bar must be TIGHTER than the six-test family's"
    assert CONTROL == selection.TRIGGER, "control drifted from the live trigger"
    assert CONTROL != "none", "controlling on none would be a straw man"

    # The exposure control is the point of the file. Losing it turns a
    # trades-less-so-draws-down-less arm into a finding.
    src = open(__file__).read()
    assert "MAX_EXPOSURE_R" in src and "just exposure" in src, \
        "the exposure control is gone"
    assert "chosen after its number was seen" in src, \
        "the post-hoc caveat is gone from the output"
    print("trigger_dd_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
