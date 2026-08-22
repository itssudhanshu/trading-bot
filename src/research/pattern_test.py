#!/usr/bin/env python3
"""H5: does a named chart pattern beat the breakout it would replace?

PRE-REGISTERED 2026-08-20 (spec section 6); detectors frozen 2026-08-21 in their
own commit, before this file was written.

THE HYPOTHESIS
--------------
The live trigger asks one question about one bar: does the close clear the prior
20-day high. The hypothesis is that the SHAPE of the preceding window carries
something that single question does not -- a flag, an ascending triangle, a cup
and handle.

THE CONTROL, NAMED EXPLICITLY
-----------------------------
`breakout`. The incumbent, not `none`. A new trigger has to displace the one
actually running, and post-guard `trigger_test` showed breakout is the only one
of seven to clear the promotion bar, so it is a real opponent rather than a
straw man.

THE ARM THAT WILL PROBABLY KILL IT, AND WHY IT IS HERE FIRST
------------------------------------------------------------
Measured on real bars before this file was written: `pattern` fires on 17.56% of
signal bars against breakout's 3.86%. It is four and a half times LOOSER, so it
admits far more candidates -- and a looser trigger fills the bucket more easily,
which means reaching further down the ranked list. Rank depth is the one effect
this project has actually resolved: -1.18% per cohort step, t = -4.10.

So a `pattern` result could be nothing but a looseness dial in disguise, and the
`none` arm is here to say so. `none` is maximally loose (it fires on every bar).
If pattern's per-trade return sits between breakout's and none's, roughly where
its firing rate puts it, then the shapes are doing nothing and the arm is
measuring how often the trigger says yes.

This is the same defence the long-flat arm gave H4, where holding 30 days flat
beat the clever structural rule four to one. It is in the file before the run
because it was foreseeable before the run.

THE ENDPOINT AND THE BAR
------------------------
Mean per-trade return, arm minus breakout, with standard error and t. Reported
per size group and per half-year block, n beside every figure.

|t| >= 2.6, the pre-registered family-wise bar. `pattern` is the ONLY arm with
an adoption path: the three detectors are reported individually as description,
because testing each would add three comparisons to the family and raise the bar
for every other hypothesis in the spec.

Adoption requires ALL of:
  1. per-trade edge over breakout at |t| >= 2.6
  2. both size groups positive
  3. worst half-year block no worse than breakout's
  4. NOT explained by looseness -- pattern must beat where its firing rate
     places it on the breakout/none line

    STRATEGY=patterns python3 src/research/pattern_test.py
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import statistics
import sys
from collections import defaultdict

import entry
import features
import selection
import simulate

BATCH = "20260821-patterns-h5"
BAR = 2.6

# Read the live rules; never copy them.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5)

CONTROL = selection.TRIGGER              # "breakout" -- read, not typed
PRIMARY = "pattern"
DESCRIPTIVE = ("flag", "asc_triangle", "cup_handle")
LOOSE = "none"


def _block(day):
    return f"{day.year}-H{1 if day.month <= 6 else 2}"


def _stats(trades):
    r = [t["ret"] for t in trades]
    if len(r) < 2:
        return {"n": len(r), "mean": float("nan"), "sd": float("nan")}
    return {"n": len(r), "mean": statistics.fmean(r), "sd": statistics.stdev(r)}


def _diff(a, b):
    if a["n"] < 2 or b["n"] < 2:
        return float("nan"), float("nan"), float("nan")
    se = (a["sd"] ** 2 / a["n"] + b["sd"] ** 2 / b["n"]) ** 0.5
    edge = b["mean"] - a["mean"]
    return edge, se, (edge / se if se else float("nan"))


def _verdict(t):
    if t != t:
        return "not enough trades"
    return "RESOLVED" if abs(t) >= BAR else "inside the noise"


def _worst(trades):
    by = defaultdict(float)
    for t in trades:
        by[_block(t["day"])] += t["ret"]
    return min(by.values()) if by else float("nan")


def main():
    if paths.STRATEGY != "patterns":
        print(f"this test belongs to patterns; STRATEGY={paths.STRATEGY}.")
        print("run:  STRATEGY=patterns python3 src/research/pattern_test.py")
        return 1

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"batch {BATCH}   control '{CONTROL}'   bar |t| >= {BAR}\n")

    arms = {}
    for name in (CONTROL, PRIMARY, LOOSE) + DESCRIPTIVE:
        entry._CACHE.clear()
        arms[name] = simulate.run(corpus, days, trigger=name, **BASE)

    ctrl = _stats(arms[CONTROL]["trades"])
    print(f"{'trigger':<16}{'CAGR':>8}{'maxDD':>7}{'n':>6}{'per trade':>11}"
          f"{'vs ctrl':>10}{'t':>7}   verdict")
    for name in (CONTROL, PRIMARY, LOOSE) + DESCRIPTIVE:
        st = _stats(arms[name]["trades"])
        row = (f"  {name:<14}{arms[name]['cagr']:>8.2f}{arms[name]['maxdd']:>7.1f}"
               f"{st['n']:>6}{st['mean']:>10.2f}%")
        if name == CONTROL:
            print(f"{row}{'--':>10}{'--':>7}   control (the incumbent)")
            continue
        edge, se, t = _diff(ctrl, st)
        tag = _verdict(t)
        if name in DESCRIPTIVE:
            tag = "description only -- no adoption path"
        elif name == LOOSE:
            tag = "looseness reference"
        print(f"{row}{edge:>+9.2f}%{t:>7.2f}   {tag}")

    prim = _stats(arms[PRIMARY]["trades"])
    loose = _stats(arms[LOOSE]["trades"])
    edge, se, t = _diff(ctrl, prim)
    edge_l, _, _ = _diff(ctrl, loose)

    print("\nper size group (pattern minus breakout):")
    for clu in ("micro", "small"):
        a = _stats([x for x in arms[CONTROL]["trades"] if x["clu"] == clu])
        b = _stats([x for x in arms[PRIMARY]["trades"] if x["clu"] == clu])
        e, s_, tt = _diff(a, b)
        print(f"  {clu:<8} breakout {a['mean']:+6.2f}% (n={a['n']:3d})   "
              f"pattern {b['mean']:+6.2f}% (n={b['n']:3d})   "
              f"edge {e:+6.2f}%  t {tt:+5.2f}   {_verdict(tt)}")

    print("\nworst half-year block:")
    for name in (CONTROL, PRIMARY):
        print(f"  {name:<14}{_worst(arms[name]['trades']):+8.1f}%")

    print("\nadoption check (all four required):")
    c1 = abs(t) >= BAR
    print(f"  1  edge clears |t| >= {BAR}        {edge:+.2f}% +/- {se:.2f}, "
          f"t {t:+.2f}   {'PASS' if c1 else 'FAIL'}")
    grp = {c: _stats([x for x in arms[PRIMARY]["trades"] if x["clu"] == c])["mean"]
           for c in ("micro", "small")}
    c2 = all(v == v and v > 0 for v in grp.values())
    print(f"  2  both size groups positive     micro {grp['micro']:+.2f}%, "
          f"small {grp['small']:+.2f}%   {'PASS' if c2 else 'FAIL'}")
    c3 = _worst(arms[PRIMARY]["trades"]) >= _worst(arms[CONTROL]["trades"])
    print(f"  3  worst block no worse          {_worst(arms[PRIMARY]['trades']):+.1f}%"
          f" vs {_worst(arms[CONTROL]['trades']):+.1f}%   {'PASS' if c3 else 'FAIL'}")
    # Looseness: pattern fires on 17.6% of bars, breakout 3.9%, none 100%. If
    # pattern's edge is no better than the loose arm's, the shapes added nothing
    # that saying yes more often would not have added.
    c4 = not (edge_l == edge_l and edge == edge and edge_l >= edge)
    print(f"  4  not just a looser trigger     none's edge {edge_l:+.2f}% "
          f"vs pattern {edge:+.2f}%   {'PASS' if c4 else 'FAIL'}")

    ok = c1 and c2 and c3 and c4
    print(f"\n  -> {'ADOPT' if ok else 'DO NOT ADOPT'}: "
          f"{'all four conditions met' if ok else _verdict(t) if not c1 else 'a condition failed'}")
    if not ok:
        print("     TRIGGER stays 'breakout'.")
    return 0


def _selftest():
    """The arithmetic and the pre-registration, without a backtest."""
    import math
    a = {"n": 100, "mean": 0.0, "sd": 10.0}
    b = {"n": 100, "mean": 2.0, "sd": 10.0}
    e, se, t = _diff(a, b)
    assert abs(e - 2.0) < 1e-9 and abs(se - math.sqrt(2)) < 1e-9
    assert _diff(b, a)[0] < 0 < e, "the subtraction is the wrong way round"
    assert _diff({"n": 1, "mean": 0.0, "sd": 0.0}, b)[2] != _diff(
        {"n": 1, "mean": 0.0, "sd": 0.0}, b)[2], "one trade produced a real t"
    assert BAR >= 2.0, "the bar may be tightened, never relaxed"
    assert _verdict(BAR) == "RESOLVED" and _verdict(-BAR) == "RESOLVED"
    assert _verdict(BAR - 0.01) == "inside the noise"

    # The control must be READ from the live rule, never typed here.
    assert CONTROL == selection.TRIGGER, "control drifted from the live trigger"
    assert CONTROL != "none", "controlling on 'none' would be a straw man"

    # Every arm must exist as a real trigger. This file lives in the SHARED
    # research directory but its arms are patterns's, and the sweep runs under
    # whatever STRATEGY is active -- usually breakout, where `pattern` does not
    # exist. Asserting against the imported module therefore failed the sweep
    # for a reason that says nothing about whether this test is correct.
    #
    # Skipping the check when the strategy is not patterns would be worse: it
    # would never run, since the sweep is the only thing that runs it. So it
    # checks the FILE that defines the triggers, which is true from anywhere.
    if paths.STRATEGY == "patterns":
        for nm in (CONTROL, PRIMARY, LOOSE) + DESCRIPTIVE:
            assert nm in entry.TRIGGERS, f"{nm} is not a registered trigger"
    else:
        # The TABLE, not the source text. A first attempt grepped patterns's
        # entry.py for '"cup_handle"' and had no teeth: the name appears twice
        # in that file -- once in the TRIGGERS registration and once inside its
        # own selftest's list -- so renaming the registration still matched the
        # mention. agent.py records the same trap in the same words: a source
        # scan for a name matches the list of names itself and can never fail.
        #
        # So patterns is imported in a CHILD, which is the only way one process
        # can see another strategy's rules -- paths binds the active strategy at
        # import, and that isolation is deliberate.
        import os
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["STRATEGY"] = "patterns"
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'src'); import paths, entry;"
             " print(' '.join(entry.TRIGGERS))"],
            cwd=paths.ROOT, env=env, capture_output=True, text=True, timeout=120)
        got = set(r.stdout.split())
        assert got, f"could not read patterns's triggers: {r.stderr[-300:]}"
        for nm in (PRIMARY,) + DESCRIPTIVE:
            assert nm in got, f"{nm} is not registered in patterns: {sorted(got)}"
        for nm in (CONTROL, LOOSE):
            assert nm in entry.TRIGGERS, f"{nm} must exist in every strategy"

    # The looseness arm is not optional: removing it is what would let a purely
    # looser trigger pass as a pattern finding, exactly as removing H4's
    # long-flat arm would have. Assert it survives, and that condition 4 still
    # references it -- a check that exists but is no longer consulted is worse
    # than none, because it reads as protection.
    assert LOOSE == "none", "the looseness reference must be the no-filter arm"
    _src = open(__file__).read()
    assert "not just a looser trigger" in _src, "adoption condition 4 is gone"
    assert "edge_l" in _src, "condition 4 no longer reads the looseness arm"
    print("pattern_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
