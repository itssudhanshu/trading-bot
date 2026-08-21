#!/usr/bin/env python3
"""Does a wider bucket actually cut drawdown, or did one bad episode do it?

WHY THIS EXISTS. bucket_size_test (batch 20260820-bucketsize) found per-trade
return decaying monotonically with seats and NOTHING resolving -- largest |t| was
0.85. But drawdown moved hard the other way: 31.0% at five seats against 19.7% at
eight. That bar had no adoption path for drawdown IMPROVING, only a veto on it
worsening, so the question was left open rather than re-decided after the fact.
This is that question, asked properly.

THE PROBLEM WITH THE 31.0 vs 19.7 NUMBER. It is one number off one path. maxDD
is a single realisation with no error bar, so it cannot be compared between two
configs any more than a single trade can. Eleven points looks decisive and is
not evidence of anything on its own.

THE FIX, and it is the whole design. Split the equity curve into DISJOINT
six-month blocks -- the project already calls that a "block" (rules.md: worst
block = worst six months) -- and compute drawdown inside each. That turns one
realisation into a distribution, and because every arm is run over the same
calendar blocks the comparison is PAIRED, which removes regime from the
difference. simulate.run now returns the curve; it did not before, and nothing
in the loop reads it.

HYPOTHESIS. More concurrent positions diversify idiosyncratic risk, so a wider
bucket should show lower drawdown in MOST blocks, not just on aggregate. Written
before the run: the paired mean difference (8 seats minus 5) should be NEGATIVE,
and the effect should be monotone -- 12 <= 8 <= 5 in mean block drawdown.

THE ALTERNATIVE THIS IS BUILT TO CATCH, and the reason for condition (b) below.
The global gap could come from a SINGLE episode -- one block where the five-seat
book happened to hold one bad name -- with every other block showing nothing. On
aggregate that is indistinguishable from a real diversification effect. If that
is what happened, the per-block differences will cluster near zero with one large
outlier, the median will disagree with the mean, and dropping the worst block
will collapse the statistic. That is a different finding and must not be
reported as this one.

ENDPOINT. Paired mean difference in per-block maxDD, with std err and t, across
disjoint six-month blocks, for 8 and 12 seats against the live 5. Reported with
the block count beside it, plus the median, the win rate (blocks where the wider
bucket drew down less), and the leave-one-out check.

THE PROMOTION BAR, fixed here before a single run:

  Recommend a wider bucket ONLY if ALL of:
    a. paired mean drawdown reduction vs 5 seats has |t| > 2.0
    b. NOT driven by one block: the median difference has the same sign as the
       mean, AND removing the single largest-magnitude block still leaves
       |t| > 1.5
    c. monotone in seats: mean block drawdown 12 <= 8 <= 5
    d. the per-trade return cost does NOT reach |t| > 2 against live -- a
       resolved loss of return is not worth an unresolved gain in comfort

  Anything else is reported as "inside the noise" IN THOSE WORDS and nothing
  changes.

AND EVEN IF IT CLEARS. This is a RECOMMENDATION, not a change. How much
drawdown the book should accept is the operator's design decision, not an
output of a backtest -- CLAUDE.md is explicit that the approach is the user's
design and not a parameter to be tuned away. Clearing the bar means the question
is worth putting to them with evidence attached; it does not mean editing
MAX_POSITIONS.

WHAT IS NOT VARIED. Mix stays at the live 3:2 ratio, scaled per arm. Measured at
8 seats it moved nothing: 4/4 against 5/3 was +0.05% per trade at t = +0.05 on
n=311 vs n=318, the tightest mix test this project has run. Risk invariants are
untouched, as always.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import statistics

import entry, features, selection, simulate

BATCH = "20260820-drawdown"

# READ the live constants, never copy them.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, refresh=5, trigger=selection.TRIGGER)

# Size axis only, mix held at the live 3:2 -- the same ladder arms as
# bucket_size_test so the two results are directly comparable.
ARMS = [
    (5,  dict(selection.TAKE_PER_CLUSTER)),      # live
    (8,  {"micro": 5, "small": 3}),
    (12, {"micro": 7, "small": 5}),
]
LIVE = 5
BLOCK_MONTHS = 6


def block_key(day):
    """-> the disjoint six-month block a date falls in, e.g. 2024H1."""
    return f"{day.year}H{1 if day.month <= 6 else 2}"


def blocks(curve):
    """-> {block: (maxdd_pct, ret_pct)} computed INSIDE each block.

    The peak resets at each block start, which is the point: this measures the
    drawdown an operator would have lived through during that period, not the
    tail of one that began years earlier.
    """
    by = {}
    for day, eq in curve:
        by.setdefault(block_key(day), []).append(eq)
    out = {}
    for k, eqs in by.items():
        if len(eqs) < 20:                 # a stub block is not a period
            continue
        peak = dd = 0.0
        for e in eqs:
            peak = max(peak, e)
            if peak > 0:
                dd = max(dd, (peak - e) / peak)
        out[k] = (dd * 100, (eqs[-1] / eqs[0] - 1) * 100 if eqs[0] else 0.0)
    return out


def _stats(vals):
    n = len(vals)
    if n < 2:
        return (0.0, 0.0, n)
    return (statistics.fmean(vals),
            statistics.stdev(vals) / (n ** 0.5), n)


def _t2(m1, se1, m2, se2):
    """-> t for the difference of two independent means, without dividing by
    zero. Identical-variance-zero inputs only arise in the selftest, but a
    crash there is a crash in the thing that guards the bar."""
    denom = (se1 ** 2 + se2 ** 2) ** 0.5
    if denom:
        return (m1 - m2) / denom
    return 0.0 if m1 == m2 else (float("inf") if m1 > m2 else float("-inf"))


def paired(a, b):
    """-> (mean_diff, std_err, n, t, diffs) for a minus b over shared blocks."""
    keys = sorted(set(a) & set(b))
    d = [a[k] - b[k] for k in keys]
    m, se, n = _stats(d)
    return m, se, n, (m / se if se else 0.0), dict(zip(keys, d))


def measure(corpus, days):
    out = {}
    for seats, take in ARMS:
        entry._CACHE.clear()
        r = simulate.run(corpus, days, max_pos=seats, take_per_cluster=take,
                         **BASE)
        if not r.get("curve"):
            raise SystemExit(f"{seats} seats: simulate.run returned no curve; "
                             f"the drawdown split has nothing to work on")
        bl = blocks(r["curve"])
        if not bl:
            raise SystemExit(f"{seats} seats: the curve produced no usable "
                             f"blocks ({len(r['curve'])} points)")
        out[seats] = {"cagr": r["cagr"], "maxdd": r["maxdd"], "blocks": bl,
                      "rets": [t["ret"] for t in r["trades"]], "take": take}
    return out


def report(res):
    live = res[LIVE]
    lbl = {k: v[0] for k, v in live["blocks"].items()}
    lm, lse, ln = _stats([p for p in live["rets"]])
    print(f"batch {BATCH} | hold={BASE['hold']}d trigger={BASE['trigger']} "
          f"impact_c={simulate.engine.IMPACT_C} | {BLOCK_MONTHS}-month blocks")
    print(f"\nlive {LIVE} seats: whole-path maxDD {live['maxdd']:.1f}%, "
          f"mean block drawdown {statistics.fmean(lbl.values()):.2f}%, "
          f"{len(lbl)} blocks, per trade {lm:+.2f}% +/- {lse:.2f}% (n={ln})\n")
    print(f"{'seats':>5} {'pathDD':>7} {'meanBlkDD':>10} {'vs live':>9} "
          f"{'stderr':>7} {'t':>7} {'blocks':>7} {'medDiff':>8} {'win%':>6} "
          f"{'LOO t':>7}   verdict")
    verdicts = {}
    for seats, _ in ARMS:
        d = res[seats]
        bd = {k: v[0] for k, v in d["blocks"].items()}
        mean_blk = statistics.fmean(bd.values())
        if seats == LIVE:
            print(f"{seats:>5} {d['maxdd']:>6.1f}% {mean_blk:>9.2f}% "
                  f"{'--':>9} {'--':>7} {'--':>7} {len(bd):>7} {'--':>8} "
                  f"{'--':>6} {'--':>7}   reference")
            verdicts[seats] = (0.0, 0.0, {})
            continue
        m, se, n, t, diffs = paired(bd, lbl)
        med = statistics.median(diffs.values())
        win = 100 * sum(1 for v in diffs.values() if v < 0) / max(len(diffs), 1)
        # leave-one-out: drop the single most influential block
        worst = max(diffs, key=lambda k: abs(diffs[k]))
        loo = [v for k, v in diffs.items() if k != worst]
        lm2, lse2, _ = _stats(loo)
        loo_t = lm2 / lse2 if lse2 else 0.0
        verdicts[seats] = (m, t, diffs)
        print(f"{seats:>5} {d['maxdd']:>6.1f}% {mean_blk:>9.2f}% {m:>+8.2f}% "
              f"{se:>7.2f} {t:>+7.2f} {n:>7} {med:>+7.2f}% {win:>5.0f}% "
              f"{loo_t:>+7.2f}   "
              f"{'RESOLVED' if abs(t) > 2 else 'inside the noise'}")

    print("\nPER-BLOCK DRAWDOWN (%), and the difference each arm made")
    keys = sorted(lbl)
    print(f"{'block':>8} " + "".join(f"{s:>9} seats" for s, _ in ARMS))
    for k in keys:
        row = "".join(f"{res[s]['blocks'].get(k, (float('nan'),))[0]:>14.1f}"
                      for s, _ in ARMS)
        print(f"{k:>8} {row}")

    print("\nRETURN COST -- condition (d): a resolved loss of return vetoes it")
    for seats, _ in ARMS:
        m, se, n = _stats(res[seats]["rets"])
        if seats == LIVE:
            print(f"  {seats:>2} seats  {m:+.2f}% +/- {se:.2f}%  n={n:<4} reference")
        else:
            t = _t2(m, se, lm, lse)
            print(f"  {seats:>2} seats  {m:+.2f}% +/- {se:.2f}%  n={n:<4} "
                  f"vs live {m - lm:+.2f}%  t={t:+.2f}  "
                  f"{'RESOLVED COST' if abs(t) > 2 else 'inside the noise'}")
    return verdicts


def promote(res, verdicts):
    """-> (recommend, why). The bar was fixed in the docstring before any run."""
    lbl = {k: v[0] for k, v in res[LIVE]["blocks"].items()}
    lm, lse, _ = _stats(res[LIVE]["rets"])
    means = {s: statistics.fmean(v[0] for v in res[s]["blocks"].values())
             for s, _ in ARMS}
    reasons = []
    for seats, _ in ARMS:
        if seats == LIVE:
            continue
        m, t, diffs = verdicts[seats]
        if abs(t) <= 2.0:
            continue
        med = statistics.median(diffs.values())
        if (med < 0) != (m < 0):
            reasons.append(f"{seats}: median and mean disagree in sign")
            continue
        worst = max(diffs, key=lambda k: abs(diffs[k]))
        loo = [v for k, v in diffs.items() if k != worst]
        m2, se2, _ = _stats(loo)
        # se2 == 0 is a perfectly CONSISTENT remainder, which is the strongest
        # possible pass, not a failure. Treating it as one was backwards.
        loo_ok = (abs(m2) > 0 if se2 == 0 else abs(m2 / se2) > 1.5)
        if not loo_ok:
            reasons.append(f"{seats}: dropping {worst} collapses it -- one "
                           f"episode, not a diversification effect")
            continue
        if not (means[12] <= means[8] <= means[LIVE]):
            reasons.append(f"{seats}: not monotone in seats "
                           f"({means[LIVE]:.2f} / {means[8]:.2f} / {means[12]:.2f})")
            continue
        rm, rse, _ = _stats(res[seats]["rets"])
        rt = _t2(rm, rse, lm, lse)
        if abs(rt) > 2:
            reasons.append(f"{seats}: return cost is RESOLVED (t={rt:+.2f})")
            continue
        return True, (f"{seats} seats clears every condition. This is a "
                      f"RECOMMENDATION to put to the operator, not a change: "
                      f"how much drawdown the book accepts is their design "
                      f"decision, not a backtest output.")
    return False, ("nothing clears the bar; the live 5 seats stand. "
                   + ("; ".join(reasons) if reasons
                      else "no arm reached |t| > 2 on paired block drawdown"))


def _selftest():
    import datetime as _dt
    d = _dt.date
    assert block_key(d(2024, 1, 5)) == "2024H1"
    assert block_key(d(2024, 6, 30)) == "2024H1"
    assert block_key(d(2024, 7, 1)) == "2024H2"
    # a curve that rises then halves inside one block: 50% drawdown, and the
    # peak must RESET at the next block rather than carry over
    c = ([(d(2024, 2, 1 + i), 100.0) for i in range(10)]
         + [(d(2024, 3, 1 + i), 200.0) for i in range(10)]
         + [(d(2024, 4, 1 + i), 100.0) for i in range(10)]
         + [(d(2024, 8, 1 + i), 100.0) for i in range(25)])
    b = blocks(c)
    assert abs(b["2024H1"][0] - 50.0) < 1e-9, b
    assert abs(b["2024H2"][0] - 0.0) < 1e-9, ("the peak carried across a block "
                                              "boundary", b)
    # a block with too few sessions is not a period
    assert "2023H1" not in blocks([(d(2023, 1, 1), 100.0)])
    # paired arithmetic, and the sign convention: negative = wider drew down less
    m, se, n, t, diffs = paired({"a": 10.0, "b": 20.0}, {"a": 12.0, "b": 26.0})
    assert n == 2 and abs(m - (-4.0)) < 1e-9, (m, n)
    assert diffs["b"] == -6.0 and t < 0, diffs
    def _fake(diffs):
        """-> a res/verdicts pair where 8 and 12 beat 5 by `diffs` per block."""
        base = {k: (10.0, 0.0) for k in diffs}
        wide = {k: (10.0 + v, 0.0) for k, v in diffs.items()}
        res = {5: {"blocks": base, "rets": [1.0] * 60, "maxdd": 30.0, "cagr": 7.0},
               8: {"blocks": wide, "rets": [1.0] * 60, "maxdd": 20.0, "cagr": 5.0},
               12: {"blocks": dict(wide), "rets": [1.0] * 60, "maxdd": 20.0,
                    "cagr": 4.0}}
        m, se, n, t, d = paired({k: v[0] for k, v in wide.items()},
                                {k: v[0] for k, v in base.items()})
        return res, {5: (0.0, 0.0, {}), 8: (m, t, d), 12: (m, t, d)}, t

    # ONE EPISODE must not be adopted. Note WHERE it gets rejected: a lone
    # outlier inflates the variance it is measured against, so it fails
    # condition (a) on its own and never reaches the leave-one-out guard. That
    # is the t-statistic doing the work, and it is worth knowing that (b) is
    # therefore a backstop rather than the primary defence.
    one = {f"b{i}": -0.2 for i in range(9)}
    one["blowup"] = -40.0
    res1, v1, t1 = _fake(one)
    assert abs(t1) < 2.0, f"a single-episode fixture reached t={t1:+.2f}"
    ok, why = promote(res1, v1)
    assert not ok, why

    # A CONSISTENT effect with one extra-bad block must SURVIVE the leave-one-out
    # check -- the guard must not reject a real effect that happens to have a
    # worst block, which every real series does.
    many = {f"b{i}": v for i, v in enumerate(
        [-1.2, -1.4, -1.6, -1.8, -1.3, -1.7, -1.5, -1.5, -1.6, -8.0])}
    res2, v2, t2 = _fake(many)
    assert abs(t2) > 2.0, t2
    ok2, why2 = promote(res2, v2)
    assert ok2, f"a consistent effect was rejected: {why2}"
    assert "RECOMMENDATION" in why2, why2

    # and a resolved return cost must veto it even when drawdown is convincing
    res3, v3, _ = _fake(many)
    # BOTH wide arms must carry the cost: the veto is per-arm, so leaving 12
    # untouched simply promotes 12 instead -- which is the bar working, and a
    # fixture that did not say what it meant to say.
    res3[8]["rets"] = res3[12]["rets"] = [-40.0] * 60
    ok3, why3 = promote(res3, v3)
    assert not ok3 and "return cost is RESOLVED" in why3, why3
    print("drawdown_test selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        corpus = features.load_corpus()
        days = sorted({d for x in corpus.values() for d in x.days})
        res = measure(corpus, days)
        verdicts = report(res)
        ok, why = promote(res, verdicts)
        print(f"\nPROMOTION BAR: {'RECOMMEND -- ' if ok else 'no change. '}{why}")
        print("\nrecord:", json.dumps(
            {"at": BATCH, "kind": "drawdown_vs_concentration",
             "block_months": BLOCK_MONTHS, "recommend": ok, "why": why,
             "arms": {str(s): {"cagr": res[s]["cagr"], "maxdd": res[s]["maxdd"],
                               "mean_block_dd": statistics.fmean(
                                   v[0] for v in res[s]["blocks"].values()),
                               "blocks": len(res[s]["blocks"]),
                               "n": len(res[s]["rets"])}
                      for s, _ in ARMS}}))
