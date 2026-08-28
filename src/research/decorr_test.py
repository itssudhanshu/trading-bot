#!/usr/bin/env python3
"""H17 — the diversification rule that was written and never called.

`selection.decorrelate()` drops a candidate that moves too closely with one
already taken. Its docstring records why it exists:

    "The bucket had no diversification rule beyond cluster counts, and it
     showed: two of five positions were hospital chains."

**Nothing calls it.** `selection.build()` -- the live path that queues real
orders -- ranks, sorts and returns; it never decorrelates. `simulate.run()`
calls it only when `max_corr` is passed, and `max_corr` defaults to None and is
passed by no caller, no research module and no batch in this repo. So the live
bucket has no diversification rule beyond the 3/2 cluster quota, and the rule
that was supposed to supply one has never been measured.

This is the L58 shape exactly: `engine.gate()` had always rejected `high == low`
and nothing called `engine.gate()`, and the correction removed 6.5 CAGR points.
`build()` name-checks that failure twice in its own comments while the dead
`decorrelate` sits ninety lines below it in the same file.

WHY THIS IS A LEGAL EXPERIMENT (CLAUDE.md's four kinds): a new rule SHAPE. The
book has no diversification rule at all today, so this is not a new value for an
existing dial -- `max_corr` has never held a value in any measurement.

THE CONTROL is `max_corr=None`: no diversification rule. That is what the live
book runs, and -- accidentally rather than deliberately -- what the current
setting is a decision against.

TWO ARMS, BECAUSE THE HOOK CANNOT DO THE JOB ITS DOCSTRING CLAIMS.

  within-day   the hook AS WRITTEN. `simulate.run` calls it on one day's
               candidate rows, so it compares candidates only against each
               other. Two hospital chains entered on DIFFERENT days -- the case
               the docstring cites -- never meet inside it. It also runs AFTER
               `allocate()` has cut to five, so a removal leaves the seat empty
               rather than reaching further down the ranking. That is the right
               behaviour for this book (`build()`: "hold cash instead"), and it
               means this arm measures "drop a correlated candidate and hold
               cash", not "diversify the book".

  vs open book the rule the docstring describes: a candidate is also compared
               against every position ALREADY OPEN. Implemented by seeding
               `decorrelate` with synthetic rows for the held names rather than
               by writing the rule a second time (rules.md R1). Known and stated
               conservative bias: if two HELD names are themselves correlated
               above the cap, the second seeds nothing, so a candidate that
               moves with it survives. That makes this arm weaker than the rule
               it describes, never stronger.

THRESHOLDS, declared here before the run: 0.70, 0.60, 0.50 for each arm. Three
values, fixed, not re-sliced afterwards. Six variants plus a control, so a
single |t| near 2 is worth about a third of what it looks like -- stated again
in the output.

THE ENDPOINT, declared before the run. This is a RISK rule, so it is judged the
way the circuit-lock guard and the drawdown/concentration work were (L58, L64),
NOT on CAGR:

  1. If it never binds, it is dead code and the finding is to DELETE it. L81's
     RSI gate produced 0 trades and was dropped; a rule that removes nothing is
     not a conservative rule, it is an absent one. The removal count is printed
     for every arm and is the first thing read.
  2. To ADOPT: max drawdown must improve, per-trade return must not fall by more
     than one standard error, and the direction must be MONOTONE across the
     three thresholds. Non-monotone is noise -- that is exactly how the
     participation cap was rejected (2% best, 1% and 5% worse), and that
     precedent is binding here.
  3. Anything else is reported as `inside the noise` in those words, and
     nothing is adopted.

No adoption path runs automatically. `--rebaseline` stays the operator's step.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
from collections import defaultdict

import analysis, entry, features, remeasure, selection, simulate

BATCH = "20260828-decorr"

# Read the live constants, never copy them. impact_test.py carried a copy that
# said hold=15 for three months after the live value moved to 10 (L60).
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)

THRESHOLDS = (0.70, 0.60, 0.50)

# The minimum number of trades an arm must move before its result is about the
# RULE rather than about one price path. Added after v1 of the bar passed a rule
# that changed a single trade in seven years (L86).
MIN_CHANGED = 20

# Pre-registered before running: control first, then the hook as written, then
# the rule its docstring describes.
VARIANTS = [("no rule (control, = live)", None, False)]
VARIANTS += [(f"within-day {c:.2f}", c, False) for c in THRESHOLDS]
VARIANTS += [(f"vs open book {c:.2f}", c, True) for c in THRESHOLDS]

_C = _D = None


def _one(item):
    """One arm, in its own fork so a counter cannot leak between variants."""
    label, max_corr, vs_open = item
    entry._CACHE.clear()

    # Count what the rule actually REMOVES. A rule that removes nothing is the
    # first outcome this test checks for, and a status message is not evidence:
    # the count comes from the function itself, not from the flag being set.
    seen = {"in": 0, "out": 0, "days": 0}
    _orig = selection.decorrelate

    def _counting(rows, corpus, as_of, cap):
        kept = _orig(rows, corpus, as_of, cap)
        if cap:
            seen["days"] += 1
            seen["in"] += len(rows)
            seen["out"] += len(rows) - len(kept)
        return kept

    selection.decorrelate = _counting
    try:
        r = simulate.run(_C, _D, max_corr=max_corr, decorr_open=vs_open, **BASE)
    finally:
        selection.decorrelate = _orig

    t = r["trades"]
    by = defaultdict(float)
    for x in t:
        by[f"{x['day'].year}-H{1 if x['day'].month <= 6 else 2}"] += x["ret"]
    return {"label": label, "cap": max_corr, "vs_open": vs_open,
            "cagr": r["cagr"], "dd": r["maxdd"], "n": len(t),
            "win": sum(1 for x in t if x["ret"] > 0) / max(len(t), 1) * 100,
            "worst": min(by.values()) if by else float("nan"),
            "removed": seen["out"], "considered": seen["in"],
            "cluster": analysis.per_cluster(t), "_r": r}


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"H17 decorrelation — {len(VARIANTS)} arms x {len(_D)} sessions, at "
          f"the live exit rules ({BASE['stop_pct']:g}/{BASE['target_pct']:g}/"
          f"{BASE['hold']}d), batch {BATCH}\n")
    with mp.get_context("fork").Pool(min(len(VARIANTS), mp.cpu_count())) as p:
        res = p.map(_one, VARIANTS)

    # Gate 1, read first: does the rule bind at all?
    print("  DOES IT BIND?  (a rule that removes nothing is absent, not conservative)")
    for x in res[1:]:
        pct = x["removed"] / x["considered"] * 100 if x["considered"] else 0.0
        print(f"    {x['label']:<24}removed {x['removed']:>5} of "
              f"{x['considered']:>6} candidate rows ({pct:>4.1f}%)")
    if all(x["removed"] == 0 for x in res[1:]):
        print("\n  NULL: no arm removed a single row. The rule is inert at every "
              "threshold tested; the finding is to delete it, not to tune it.")

    print(f"\n  {'arm':<24}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'per-trade':>12}{'std err':>9}{'worst blk':>11}")
    for x in res:
        m, se, _n = remeasure.edge(x["_r"])
        print(f"  {x['label']:<24}{x['cagr']:>+8.2f}%{x['dd']:>7.1f}%"
              f"{x['win']:>5.0f}%{x['n']:>6}{m:>+11.2f}%{se:>8.2f}%"
              f"{x['worst']:>+10.1f}%")

    ctl = res[0]
    print(f"\n  against NO RULE ({ctl['cagr']:+.2f}% CAGR, {ctl['dd']:.1f}% DD, "
          f"n={ctl['n']}) — what the live book runs:")
    for x in res[1:]:
        d, se, t = remeasure.gap(x["_r"], ctl["_r"])
        print(f"    {x['label']:<24}{x['dd'] - ctl['dd']:>+6.1f} DD pts"
              f"{x['cagr'] - ctl['cagr']:>+8.2f} CAGR pts"
              f"{d:>+8.2f}%/trade +/-{se:>5.2f}  t{t:>+6.2f}  "
              f"{'RESOLVED' if abs(t) > 2 else 'inside the noise'}")

    print("\n  per cluster (micro / small), average per trade:")
    for x in res:
        c = x["cluster"]
        print(f"    {x['label']:<24}"
              f"micro {c.get('micro', {}).get('avg', 0):+.2f}% "
              f"(n={c.get('micro', {}).get('n', 0)})   "
              f"small {c.get('small', {}).get('avg', 0):+.2f}% "
              f"(n={c.get('small', {}).get('n', 0)})")

    # The pre-registered adoption test, evaluated in code so it cannot be
    # re-read leniently after the numbers are on screen.
    #
    # THE FIRST VERSION OF THIS BAR SAID "ADOPT" AND WAS WRONG, which is
    # recorded here rather than quietly replaced (L86). It asked for drawdown
    # to improve, per-trade to hold within one standard error, and the three
    # thresholds to be monotone -- and every one of those is satisfied by a
    # rule that does NOTHING. Drawdown "improved" by 0.2 points, per-trade
    # "held" because it fell by less than its own error bar, and six IDENTICAL
    # numbers are vacuously monotone in both directions. The arms differed from
    # the control by exactly one trade in seven years.
    #
    # MIN_CHANGED is the tightening (criteria may be tightened, never
    # loosened). A rule whose whole effect is a handful of trades is not
    # measurable here at all: per-trade sd is ~16%, so one lucky trade moves
    # CAGR by more than any rule effect this book can resolve.
    print("\n  the pre-registered bar (drawdown improves AND per-trade holds "
          "AND monotone across 0.70/0.60/0.50):")
    for arm, vs_open in (("within-day", False), ("vs open book", True)):
        xs = [x for x in res[1:] if x["vs_open"] is vs_open]
        dds = [x["dd"] - ctl["dd"] for x in xs]
        gaps = [remeasure.gap(x["_r"], ctl["_r"]) for x in xs]
        better = all(d < 0 for d in dds)
        # Monotone AND non-degenerate: six identical numbers are monotone in
        # both directions and mean the threshold did not matter at all.
        mono = (all(a >= b for a, b in zip(dds, dds[1:])) or
                all(a <= b for a, b in zip(dds, dds[1:]))) and len(set(dds)) > 1
        holds = all(d >= -se for d, se, _t in gaps)
        moved = min(abs(ctl["n"] - x["n"]) for x in xs) >= MIN_CHANGED
        verdict = ("ADOPT" if (better and mono and holds and moved)
                   else "no — " + ", ".join(
                       w for w, ok in (
                           (f"changes fewer than {MIN_CHANGED} trades", moved),
                           ("drawdown did not improve", better),
                           ("threshold made no difference", mono),
                           ("per-trade fell by more than 1 se", holds))
                       if not ok))
        print(f"    {arm:<14}{verdict}")

    kept = sum(1 for x in res if simulate.keep(
        f"decorr {x['label']}", x["_r"],
        {**BASE, "max_corr": x["cap"], "decorr_open": x["vs_open"]},
        batch=BATCH, note="H17 diversification rule, never previously called"))
    print(f"\n  {kept} of {len(res)} cleared the promotion bar")
    print(f"  {analysis.trades_needed(analysis.BACKTEST_EDGE)} trades are needed "
          f"to resolve a {analysis.BACKTEST_EDGE:.1f}%/trade edge. "
          f"{len(VARIANTS) - 1} arms were run against one control, so a single "
          f"|t| near 2 is worth about a third of what it looks like.")


def _selftest():
    # The knob has to actually REACH the simulation, or this measures the same
    # book seven times. Four weight sets once returned byte-identical buckets
    # because the value never reached the score (clusters.py:88), and
    # engine.gate() rejected locked bars for months while nothing called it
    # (L58). Assert the wiring, not the flag.
    import inspect
    src = inspect.getsource(simulate.run)
    assert "decorr_open" in src, "simulate.run does not carry the open-book arm"
    assert "decorrelate" in src, "simulate.run no longer calls decorrelate"

    # decorrelate must actually remove a perfectly-correlated pair, and must
    # keep an uncorrelated one. Built from a synthetic corpus so the assertion
    # is about the rule, not about today's prices.
    from datetime import date, timedelta
    days = [date(2024, 1, 1) + timedelta(days=k) for k in range(120)]
    corpus = {}
    for name, mult in (("A", 1.0), ("B", 1.0), ("C", -1.0)):
        s = features.Series(name, list(days))
        px = 100.0
        for k in range(len(days)):
            step = (1 if k % 2 else -1) * mult
            px += step
            s.close.append(px); s.open.append(px)
            s.high.append(px + 1); s.low.append(px - 1)
            s.turnover.append(1e6); s.volume.append(1000)
            s.deliv_pct.append(50.0)
        corpus[name] = s
    rows = [{"symbol": "A", "cluster": "micro"},
            {"symbol": "B", "cluster": "micro"},
            {"symbol": "C", "cluster": "micro"}]
    kept = [r["symbol"] for r in
            selection.decorrelate(rows, corpus, days[-1], 0.9)]
    assert "A" in kept, kept
    assert "B" not in kept, f"a perfectly correlated pair survived a 0.9 cap: {kept}"
    # C moves exactly opposite A: |corr| is 1.0, so a cap on ABSOLUTE
    # correlation must drop it too. This asserts the sign convention the rule
    # actually uses rather than the one it is easy to assume.
    assert "C" not in kept, f"abs() is not being applied to the correlation: {kept}"

    passthrough = [r["symbol"] for r in
                   selection.decorrelate(rows, corpus, days[-1], None)]
    assert passthrough == ["A", "B", "C"], "cap=None must be inert"

    assert VARIANTS[0][1] is None, "the control is not the no-rule arm"
    assert len(VARIANTS) == 1 + 2 * len(THRESHOLDS), VARIANTS
    print("decorr_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        main()
