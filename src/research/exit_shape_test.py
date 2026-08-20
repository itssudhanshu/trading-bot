#!/usr/bin/env python3
"""H4: does an exit that reads the chart beat a flat day count?

PRE-REGISTERED 2026-08-20, before the structural exit was ever run end to end.
Spec: docs/superpowers/specs/2026-08-20-thicket-trellis-design.md, section 6.

THE QUESTION
------------
The live exit is -10% stop / +20% target / 10 trading days, flat, no trailing.
The 10-day hold was chosen over 15 and the difference measured at t = 0.28 --
inside the noise. Every subsequent pass over that dial produced a different
winner, which is what a knob inside the noise looks like.

So the hypothesis is not "a different number of days". It is that the right
answer is not a number at all: hold past day 10 while the up-structure is
intact, exit when it breaks.

THE CONTROL, NAMED EXPLICITLY
-----------------------------
The live FLAT exit -- stop/target/HOLD_DAYS with no trailing. That is what the
structural exit must displace, and it is the thing the current design was a
decision in favour of. Not "the live setting" vaguely: the specific flat rule.

A second arm runs the flat exit at the structural rule's MAXIMUM hold
(STRUCT_MAX_HOLD). Without it, a structural win could be nothing more than
"holding longer is better", which is a dial and already known to be inside the
noise. If the long-flat arm captures most of the gain, the structure is doing
nothing and the result is a rediscovery of the hold knob.

THE ENDPOINT
------------
Mean per-trade return, structural minus flat, with its standard error and t.
Reported per size group and per half-year block, with n beside every figure.
CAGR is reported too and is NOT the endpoint: it moves with trade count and
sequencing, which a per-trade mean cannot see, and it is arithmetic on one path.

THE BAR, FIXED IN ADVANCE
-------------------------
|t| >= 2.6. That is the usual |t| > 2 tightened by Bonferroni across the five
pre-registered tests in this spec (0.05 / 5 = 0.01): test five things at the
usual bar and roughly one wins by luck, which is exactly the search that
produced "two of five weight variants beat live at t < 0.5".

Adopting requires ALL of:
  1. per-trade edge over the flat control at |t| >= 2.6
  2. both size groups still positive
  3. worst half-year block no worse than the control's
  4. the long-flat arm does NOT explain most of the gain

Anything short of that is reported in these words: inside the noise.

    STRATEGY=trellis python3 src/research/exit_shape_test.py
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import statistics
import sys
from collections import defaultdict

import features
import selection
import simulate

BATCH = "20260820-trellis"
BAR = 2.6                      # fixed before the run; may be tightened, never relaxed

# Read the live constants. NEVER copy them: impact_test.py carried a copy saying
# hold=15 for three months after the live value moved to 10, and every number it
# printed in that time described a bucket nobody was running.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            trigger=selection.TRIGGER, refresh=5)


def _block(day):
    return f"{day.year}-H{1 if day.month <= 6 else 2}"


def _stats(trades):
    r = [t["ret"] for t in trades]
    n = len(r)
    if n < 2:
        return {"n": n, "mean": float("nan"), "sd": float("nan")}
    return {"n": n, "mean": statistics.fmean(r), "sd": statistics.stdev(r)}


def _diff(a, b):
    """-> (edge, std err, t) for b minus a. Two independent samples: the arms
    select different trades, so these are not paired and must not be treated as
    if they were."""
    if a["n"] < 2 or b["n"] < 2:
        return float("nan"), float("nan"), float("nan")
    se = (a["sd"] ** 2 / a["n"] + b["sd"] ** 2 / b["n"]) ** 0.5
    edge = b["mean"] - a["mean"]
    return edge, se, (edge / se if se else float("nan"))


def _verdict(t):
    if t != t:                                   # NaN
        return "not enough trades"
    return "RESOLVED" if abs(t) >= BAR else "inside the noise"


def main():
    if paths.STRATEGY != "trellis":
        print(f"this test belongs to trellis; STRATEGY={paths.STRATEGY}.")
        print("run:  STRATEGY=trellis python3 src/research/exit_shape_test.py")
        return 1

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"batch {BATCH}   {len(days)} sessions   bar |t| >= {BAR}\n")

    arms = {}

    # Set variant constants INSIDE each fork, so one arm cannot leak into the
    # next or into the live file.
    selection.STRUCTURAL_EXIT = False
    selection._ATR.clear()
    arms["flat (control)"] = simulate.run(corpus, days, **BASE)

    long_base = dict(BASE, hold=selection.STRUCT_MAX_HOLD)
    selection.STRUCTURAL_EXIT = False
    selection._ATR.clear()
    arms[f"flat, {selection.STRUCT_MAX_HOLD}d"] = simulate.run(corpus, days, **long_base)

    selection.STRUCTURAL_EXIT = True
    selection._ATR.clear()
    arms["structural"] = simulate.run(corpus, days,
                                      time_exit=selection.time_exit, **BASE)
    selection.STRUCTURAL_EXIT = False        # never leave the knob on

    ctrl = _stats(arms["flat (control)"]["trades"])

    print(f"{'arm':<18}{'CAGR':>8}{'maxDD':>8}{'n':>6}"
          f"{'per trade':>12}{'vs control':>12}{'t':>7}   verdict")
    for name, r in arms.items():
        st = _stats(r["trades"])
        head = (f"  {name:<16}{r['cagr']:>8.2f}{r['maxdd']:>8.1f}"
                f"{st['n']:>6}{st['mean']:>11.2f}%")
        if name.startswith("flat ("):
            print(f"{head}{'--':>12}{'--':>7}   control")
            continue
        edge, se, t = _diff(ctrl, st)
        print(f"{head}{edge:>+11.2f}%{t:>7.2f}   {_verdict(t)}")

    # --- per size group: a total is not a finding if one group supplied it ---
    print("\nper size group (structural minus flat):")
    for clu in ("micro", "small"):
        a = _stats([t for t in arms["flat (control)"]["trades"] if t["clu"] == clu])
        b = _stats([t for t in arms["structural"]["trades"] if t["clu"] == clu])
        edge, se, t = _diff(a, b)
        print(f"  {clu:<8} flat {a['mean']:+6.2f}% (n={a['n']:3d})   "
              f"structural {b['mean']:+6.2f}% (n={b['n']:3d})   "
              f"edge {edge:+6.2f}%  t {t:+5.2f}   {_verdict(t)}")

    # --- per regime block ---------------------------------------------------
    print("\nworst half-year block (sum of per-trade returns):")
    for name in ("flat (control)", "structural"):
        by = defaultdict(float)
        for t in arms[name]["trades"]:
            by[_block(t["day"])] += t["ret"]
        worst = min(by.items(), key=lambda kv: kv[1])
        print(f"  {name:<18} {worst[1]:+8.1f}%  in {worst[0]}")

    # --- the four adoption conditions, checked rather than eyeballed --------
    st = _stats(arms["structural"]["trades"])
    lng = _stats(arms[f"flat, {selection.STRUCT_MAX_HOLD}d"]["trades"])
    edge, se, t = _diff(ctrl, st)
    edge_l, _, _ = _diff(ctrl, lng)
    print(f"\nadoption check (all four required):")
    c1 = abs(t) >= BAR
    print(f"  1  edge clears |t| >= {BAR}          {edge:+.2f}% +/- {se:.2f}, "
          f"t {t:+.2f}   {'PASS' if c1 else 'FAIL'}")
    grp = {}
    for clu in ("micro", "small"):
        grp[clu] = _stats([x for x in arms["structural"]["trades"]
                           if x["clu"] == clu])["mean"]
    c2 = all(v > 0 for v in grp.values())
    print(f"  2  both size groups positive       micro {grp['micro']:+.2f}%, "
          f"small {grp['small']:+.2f}%   {'PASS' if c2 else 'FAIL'}")
    w = {}
    for name in ("flat (control)", "structural"):
        by = defaultdict(float)
        for x in arms[name]["trades"]:
            by[_block(x["day"])] += x["ret"]
        w[name] = min(by.values())
    c3 = w["structural"] >= w["flat (control)"]
    print(f"  3  worst block no worse            {w['structural']:+.1f}% vs "
          f"{w['flat (control)']:+.1f}%   {'PASS' if c3 else 'FAIL'}")
    c4 = not (edge_l == edge_l and edge == edge and edge > 0
              and edge_l >= 0.5 * edge)
    print(f"  4  not explained by holding longer  long-flat edge {edge_l:+.2f}% "
          f"vs structural {edge:+.2f}%   {'PASS' if c4 else 'FAIL'}")

    ok = c1 and c2 and c3 and c4
    print(f"\n  -> {'ADOPT' if ok else 'DO NOT ADOPT'}: "
          f"{'all four conditions met' if ok else _verdict(t) if not c1 else 'a condition failed'}")
    if not ok:
        print("     The knob stays off. A candidate that wins by less than its")
        print("     margin of error is a finding about this price history.")
    return 0


def _selftest():
    """The arithmetic every verdict rests on, checked without a backtest.

    This module could have been excluded from the sweep by name, the way
    impact_test and trigger_test are -- it runs three full backtests and has no
    business doing that in a selftest. But the exclusion list is for modules
    with nothing to assert offline, and this one has the most load-bearing
    arithmetic in the project: if _diff computes t wrongly, every verdict it
    prints is wrong in a way that looks completely normal.
    """
    # --- _stats ------------------------------------------------------------
    s = _stats([{"ret": 1.0}, {"ret": 3.0}])
    assert s["n"] == 2 and abs(s["mean"] - 2.0) < 1e-9, s
    assert _stats([{"ret": 1.0}])["n"] == 1, "a single trade must not claim an sd"
    assert _stats([])["n"] == 0

    # --- _diff: two independent samples, hand-computable --------------------
    # a: n=100 mean 0 sd 10 -> se^2 = 1;  b: n=100 mean 2 sd 10 -> se^2 = 1
    # edge = 2, se = sqrt(2) = 1.4142, t = 1.4142
    import math
    a = {"n": 100, "mean": 0.0, "sd": 10.0}
    b = {"n": 100, "mean": 2.0, "sd": 10.0}
    edge, se, t = _diff(a, b)
    assert abs(edge - 2.0) < 1e-9, edge
    assert abs(se - math.sqrt(2)) < 1e-9, se
    assert abs(t - math.sqrt(2)) < 1e-9, t

    # Direction: _diff(a, b) is b MINUS a. Getting this backwards would flip
    # every verdict's sign while leaving |t| identical, so it is asserted
    # rather than assumed.
    edge_r, _, t_r = _diff(b, a)
    assert edge_r < 0 < edge and t_r < 0 < t, (edge, edge_r)

    # A bigger sample must NARROW the error bar, not widen it.
    _, se_small, _ = _diff({"n": 25, "mean": 0.0, "sd": 10.0}, b)
    assert se_small > se, (se_small, se)

    # Too few trades must return NaN, never a confident zero.
    e2, s2, t2 = _diff({"n": 1, "mean": 0.0, "sd": 0.0}, b)
    assert e2 != e2 and t2 != t2, "a one-trade arm produced a real t"

    # --- the bar ------------------------------------------------------------
    assert BAR >= 2.0, "the bar may be tightened, never relaxed below |t| > 2"
    assert _verdict(BAR) == "RESOLVED"
    assert _verdict(-BAR) == "RESOLVED", "the bar is two-sided"
    assert _verdict(BAR - 0.01) == "inside the noise"
    assert _verdict(0.22) == "inside the noise", "the measured H4 t must not pass"
    assert _verdict(float("nan")) == "not enough trades"

    # --- the control arms are named, and the long-flat one is not optional --
    # Dropping it is what would turn H4 back into a false finding.
    assert "STRUCT_MAX_HOLD" in open(__file__).read(), \
        "the long-flat control arm is gone; a structural win could then be " \
        "nothing more than holding longer"
    print("exit_shape_test selftest ok (H4 verdict: inside the noise, t=0.22)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
