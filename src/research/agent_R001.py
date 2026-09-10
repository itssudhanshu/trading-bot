#!/usr/bin/env python3
"""R-001: At the fill bar, after ranking and after the entry trigger, compute fill_premium_pct = (fill open - signal close) / signal close * 100. If fill_premium_pct > 5.0, do not open the position. The vacancy is NOT refilled: no lower-ranked candidate is promoted into it, and the bucket holds cash for that stock until the next selection date.

Written by Agent 5 from the template, BEFORE the run. A docstring written
afterwards is a rationalisation, so `pipeline.py --new-research` creates this
file at proposal time and the gate refuses a validation whose research file has
no hypothesis in it.

The leading underscore keeps the TEMPLATE out of the selftest sweep. A generated
`agent_RNNN.py` has no underscore and IS swept, which is why `--selftest` below
must stay cheap -- it asserts the pre-registration, never runs the backtest.

hypothesis: If the next open prints more than 5.0% above the signal close, then the position's per-trade return is materially worse than the bucket mean and skipping it lifts the bucket's per-trade return, because the gap has already consumed a large share of the 20% target while the 10% stop is measured from the higher fill, so the same nominal reward-to-risk is bought at a worse price and the stop sits inside a wider post-gap noise band. Measured direction: top premium tercile -3.71 percentage points per trade, std err 1.17, t = -3.18, n = 1060 (L78 H2).

control: the live rules, unchanged
    Whatever the live setting was a decision AGAINST, not "the live setting".
    weight_test.py controls on neutral 1/1/1/1 because raising deliv was a
    decision against neutral.

adoption bar (pre-registered, and it may be tightened, never loosened):
    primary metric:     mean per-trade return of the affected trades (fill_premium_pct > 5.0) versus the mean per-trade return of the unaffected trades, on the same corrected-universe corpus at c=1.0, plus the whole-bucket per-trade return of the with-rule arm against the recorded baseline of +1.07% per trade at n=193
    minimum effect:     affected-trade mean must sit at least 3.71 percentage points BELOW the unaffected-trade mean (the L78 H2 tercile magnitude), AND the whole-bucket per-trade return must improve by more than +1.12 percentage points, which is one standard error at n=193
    minimum sample:     n >= 30 affected trades (fill open more than 5.0% above the signal close) in the backtest set; below 30 the verdict is INCONCLUSIVE and the rule is not adopted, whatever the sign
    secondary check:    the rank-depth slope re-measured on the with-rule arm over the same six disjoint cohorts (n = 1062) must not flatten by more than 0.3 percentage points per cohort step, i.e. it must stay steeper than -0.82% per cohort step against the recorded -1.12% (std err 0.28%). Agent 5 must also report the rank-cohort distribution of the affected trades.
    impact sensitivity: the with-rule arm must beat the no-rule arm at c=0.5, c=1.0, c=2.0 and c=3.0, and the per-trade effect at c=2.0 must carry the same sign as at c=1.0; a rule that only pays at c=1.0 is a friction artefact

failure mode: Four ways this shows itself wrong. (1) Fewer than 30 affected trades: the verdict is INCONCLUSIVE, which is the pre-registered expectation here, not a near-miss to be argued up. (2) The affected trades' mean return is at or above the unaffected mean: the premium is not harmful at this threshold and the tercile result does not survive a hard cut. (3) The rank-depth slope flattens by more than 0.3 points per cohort step: fill premium correlates with momentum, so the gate may be removing top-cohort trades preferentially, and a rule that lifts a return while flattening the one slope that survived both corrections has broken the thing that was working. (4) The timing asymmetry, which is the deepest one: fill_premium_pct can only be known AFTER the next-open fill has printed. A backtest can simply not take the trade and pays nothing. Forward, the open has already been bought, so the position must be voided the same session and pays entry impact AND exit impact plus the spread - roughly 0.6% of order value at the median modelled 0.31% per side and over 2.2% at the p90 1.12% per side. The backtest therefore measures an UPPER BOUND on the benefit that the forward bucket can realise. If Agent 5's measured benefit per affected trade is smaller than twice the modelled per-side impact for those names, the rule is NEGATIVE in the live bucket even while positive in the backtest, and must be reported as such rather than adopted.

what would change the decision: an effect that clears the bar above AND leaves
the rank-depth slope intact. Read the slope from
data/breakout/rank_slope_baseline.json; do not quote one from a document.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths                                                    # noqa: F401

BATCH = "20260911-agent5-R001"
RULE_ID = "R-001"

# Read the live constants, never copy them. impact_test.py carried a copy that
# said 15 for three months after selection.HOLD_DAYS moved to 10.
import selection                                                # noqa: E402
import simulate                                                 # noqa: E402

BASE = {"hold": selection.HOLD_DAYS}

# Impact is not calibrated, so it is reported as a sensitivity and never as one
# number. These are the five points the gate requires.
C_GRID = (0.0, 0.5, 1.0, 2.0, 3.0)


# The corpus and the session list, loaded once in the parent and inherited by
# every fork. Same shape as premium_skip_test.py and rank_test.py.
_C = _D = None


def _premium_pct(s, i):
    """-> fill premium in percent for a signal at bar `i`, or None when it is
    unknowable.

    THE fill this bucket takes is `s.open[i + 1]` against a signal close of
    `s.close[i]` -- simulate.run reads exactly those two cells, so this reads
    them too rather than restating the arithmetic in different terms. Unknowable
    cases (no next bar, dead signal close) return None and are NEVER gated: a
    silent fallback would make the gate a second, unstated rule.
    """
    if s is None or i is None or i + 1 >= len(s):
        return None
    c, o = s.close[i], s.open[i + 1]
    if not c or not o:
        return None
    return (o / c - 1.0) * 100


def variant(cfg):
    """The proposed rule, applied. Set variant constants INSIDE this fork so a
    variant cannot leak into the live weights file or into its siblings, and
    never vary anything in engine.py -- risk invariants are not searchable.

    R-001 gates the ENTRY and does not refill the vacancy, so it is installed in
    exactly the place selection.build already installs the circuit-lock guard:
    the candidate is MARKED untriggered, not filtered out. That distinction is
    the load-bearing half of the rule text. allocate() slices its 3 micro / 2
    small quota from the score order FIRST and drops untriggered names LAST, so
    marking removes the name and leaves every other name at the rank it already
    held -- the bucket holds cash. Filtering inside allocate() instead would let
    a lower-ranked candidate be promoted, which is the change that once moved
    the result by 4 CAGR points and is the mechanism that would flatten the
    rank-depth slope.

    `cfg["gated"] is False` runs the control through the identical code path, so
    the two arms differ by the gate and by nothing else.
    """
    # 5.0% is the threshold in the registered rule text, frozen there before the
    # run. It is not recomputed here: a threshold derived from the same run that
    # judges it makes the rule endogenous to its own test.
    bound = cfg.get("max_fill_premium_pct", 5.0)
    # The live constants, READ not copied. BASE carries selection.HOLD_DAYS and
    # is layered on top, so the hold can only ever be the live one.
    # simulate.run's own defaults would silently give trigger="none" -- a
    # different bucket entirely -- which is the impact_test.py failure in a new
    # costume. refresh=5 is what audit.py passes when it records the baseline.
    kw = {"stop_pct": selection.STOP_PCT, "target_pct": selection.TARGET_PCT,
          "max_pos": selection.MAX_POSITIONS, "refresh": 5,
          "trigger": selection.TRIGGER, **BASE,
          **{k: v for k, v in cfg.items() if k in ("impact_c", "offset")}}
    if not cfg.get("gated"):
        return simulate.run(_C, _D, **kw)

    orig_build = selection.build

    def _gated_build(corpus, as_of, capital=selection.CAPITAL, trigger=None):
        rows = orig_build(corpus, as_of, capital=capital, trigger=trigger)
        for r in rows:
            if not r.get("triggered"):
                continue
            s = corpus.get(r["symbol"])
            prem = _premium_pct(s, s.index_of(as_of) if s else None)
            # round(..., 6) before comparing: the raw ratio puts an exact 5.0%
            # fill at 4.99999...6 against the literal, and a rule that flips on
            # binary dust is not a rule (premium_skip_test.py made the same
            # point about its own boundary).
            if prem is not None and round(prem, 6) > bound:
                r["triggered"] = False
        return rows

    selection.build = _gated_build
    try:
        return simulate.run(_C, _D, **kw)
    finally:
        selection.build = orig_build


def _one(cfg):
    """Pool worker. One arm, one impact constant, one rank cohort."""
    import entry
    entry._CACHE.clear()
    r = variant(cfg)
    # The curve is one tuple per session and is not read by anything here;
    # dropping it keeps the pickle back to the parent small.
    r.pop("curve", None)
    return cfg, r


def _affected(trades, bound=5.0):
    """-> the subset of `trades` whose FILL bar printed more than `bound`% above
    the signal close, each row annotated with that premium.

    simulate records entry_day = days[di + 1], so the signal bar is days[di] and
    the fill bar is the symbol's own next bar after it -- the same two cells the
    gate reads. Recovered from the session list rather than guessed, because a
    symbol that did not trade on days[di + 1] fills later than its label says.
    """
    pos = {d: k for k, d in enumerate(_D)}
    out = []
    for x in trades:
        k = pos.get(x["entry_day"])
        if not k:
            continue
        s = _C.get(x["sym"])
        i = s.index_of(_D[k - 1]) if s else None
        prem = _premium_pct(s, i)
        if prem is not None and round(prem, 6) > bound:
            y = dict(x)
            y["prem"] = prem
            y["fill_day"] = s.days[i + 1]
            out.append(y)
    return out


def _mean_se(xs):
    import statistics
    if len(xs) < 2:
        return (statistics.fmean(xs) if xs else float("nan")), float("nan"), len(xs)
    return (statistics.fmean(xs), statistics.stdev(xs) / len(xs) ** 0.5, len(xs))


def _welch(a, b):
    """-> (difference of means, std err, t) for two independent samples."""
    ma, sa, na = _mean_se(a)
    mb, sb, nb = _mean_se(b)
    if na < 2 or nb < 2:
        return ma - mb, float("nan"), float("nan")
    se = (sa ** 2 + sb ** 2) ** 0.5
    return ma - mb, se, ((ma - mb) / se if se else float("nan"))


def _blk(day):
    y = day.year
    return "2019-2021" if y <= 2021 else ("2022-2023" if y <= 2023 else "2024-2026")


def main():
    import json
    import multiprocessing as mp
    import statistics
    from collections import Counter

    global _C, _D
    import analysis
    import features
    import remeasure

    out_path = None
    if "--json" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--json") + 1])

    # ---------------------------------------------------------------- sealed
    # Read from disk, never from a document. baseline.json said +7.59% for three
    # days after it stopped being true.
    bl = json.loads((paths.SDATA / "baseline.json").read_text())
    sl = analysis.load_rank_slope()
    print(f"{RULE_ID}  batch {BATCH}")
    print(f"  baseline.json            {bl}")
    print(f"  rank_slope_baseline.json {sl['slope_pct_per_step']:+.4f}%/step "
          f"+/-{sl['std_err']:.4f} t={sl['t']:+.3f} n={sl['n']} "
          f"batch {sl['batch']}")
    print(f"  live constants read: hold={selection.HOLD_DAYS} "
          f"stop={selection.STOP_PCT} target={selection.TARGET_PCT} "
          f"max_pos={selection.MAX_POSITIONS} trigger={selection.TRIGGER!r}")

    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"  corpus {len(_C)} symbols x {len(_D)} sessions "
          f"({_D[0]} .. {_D[-1]})\n", flush=True)

    ctx = mp.get_context("fork")

    # ------------------------------------------------- arms + impact grid
    # Both arms on the same corpus, the same guard and the same impact model.
    # The whole c grid in one pool: the constant is not calibrated, so one
    # number is not a result.
    tasks = [{"arm": arm, "gated": arm == "rule", "impact_c": c}
             for c in C_GRID for arm in ("control", "rule")]
    with ctx.Pool(6) as p:
        grid = p.map(_one, tasks)
    R = {(cfg["arm"], cfg["impact_c"]): r for cfg, r in grid}
    print("PHASE control+variant arms and impact grid done", flush=True)

    print(f"  {'arm':<26}{'c':>5}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'per-trade':>12}{'std err':>9}{'occ':>6}")
    rows_c = []
    for c in C_GRID:
        for arm in ("control", "rule"):
            r = R[(arm, c)]
            m, se, n = remeasure.edge(r)
            win = sum(1 for x in r["trades"] if x["ret"] > 0) / max(n, 1) * 100
            label = ("live rules (control)" if arm == "control"
                     else "R-001 skip prem > 5.0%")
            print(f"  {label:<26}{c:>5.1f}{r['cagr']:>+8.2f}%{r['maxdd']:>7.1f}%"
                  f"{win:>5.0f}%{n:>6}{m:>+11.2f}%{se:>8.2f}%"
                  f"{r['occupancy']:>6.2f}")
        rows_c.append({"c": c,
                       "cagr": round(R[("rule", c)]["cagr"], 2),
                       "per_trade": round(remeasure.edge(R[("rule", c)])[0], 2),
                       "n": len(R[("rule", c)]["trades"]),
                       "control_cagr": round(R[("control", c)]["cagr"], 2),
                       "control_per_trade": round(remeasure.edge(R[("control", c)])[0], 2),
                       "control_n": len(R[("control", c)]["trades"])})

    lr, vr = R[("control", 1.0)], R[("rule", 1.0)]
    d, se, t = remeasure.gap(vr, lr)
    print(f"\n  variant - control at c=1.0: {vr['cagr'] - lr['cagr']:+.2f} CAGR "
          f"pts  {d:+.2f}%/trade  +/-{se:.2f}  t={t:+.2f}  "
          f"{'RESOLVED' if abs(t) > 2 else 'inside the noise'}")

    # --------------------------------------------- the affected trades
    aff = _affected(lr["trades"])
    keep = {(x["sym"], x["entry_day"], x["why"]) for x in aff}
    unaff = [x["ret"] for x in lr["trades"]
             if (x["sym"], x["entry_day"], x["why"]) not in keep]
    ad, ase, at_ = _welch([x["ret"] for x in aff], unaff)
    ma, mse, na = _mean_se([x["ret"] for x in aff])
    mu, muse, nu = _mean_se(unaff)
    print(f"\n  PRIMARY: affected (fill premium > 5.0%) vs unaffected, control arm")
    print(f"    affected     n={na:<5} {ma:+.2f}% +/-{mse:.2f}")
    print(f"    unaffected   n={nu:<5} {mu:+.2f}% +/-{muse:.2f}")
    print(f"    difference   {ad:+.2f}% +/-{ase:.2f}  t={at_:+.2f}")
    print(f"\n  affected trades in the control arm ({len(aff)}):")
    print(f"    {'symbol':<14}{'cluster':<8}{'fill day':<12}{'exit day':<12}"
          f"{'prem%':>8}{'ret%':>9}{'net Rs':>10}{'why':>9}")
    for x in sorted(aff, key=lambda y: y["fill_day"]):
        print(f"    {x['sym']:<14}{x['clu']:<8}{str(x['fill_day']):<12}"
              f"{str(x['day']):<12}{x['prem']:>+8.2f}{x['ret']:>+9.2f}"
              f"{x['net']:>+10.0f}{x['why']:>9}")

    # NO-REFILL, verified rather than asserted. The vacancy must stay empty:
    # on a day the gate fired, the arm may hold FEWER names than the control and
    # must never hold a name the control did not also take that day. A name that
    # appears only in the arm on that date would be a lower-ranked candidate
    # promoted into the vacancy, which is the half of the rule that protects the
    # rank-depth slope.
    refill = []
    for dd in sorted({x["entry_day"] for x in aff}):   # the label trades carry
        cset = {x["sym"] for x in lr["trades"] if x["entry_day"] == dd}
        vset = {x["sym"] for x in vr["trades"] if x["entry_day"] == dd}
        extra = vset - cset
        print(f"    no-refill {dd}: control entered {sorted(cset) or '-'}, "
              f"rule entered {sorted(vset) or '-'}"
              + (f"  PROMOTED {sorted(extra)}" if extra else ""))
        if extra:
            refill.append((str(dd), sorted(extra)))

    # Full fill-premium distribution: how far out in the tail 5.0% actually sits.
    prems = []
    pos = {dd: k for k, dd in enumerate(_D)}
    for x in lr["trades"]:
        k = pos.get(x["entry_day"])
        s = _C.get(x["sym"])
        pr = _premium_pct(s, s.index_of(_D[k - 1])) if (k and s) else None
        if pr is not None:
            prems.append(pr)
    prems.sort()
    def _q(f):
        return prems[min(int(f * len(prems)), len(prems) - 1)] if prems else float("nan")
    print(f"\n  fill premium distribution over {len(prems)} control fills: "
          f"p50 {_q(.50):+.2f}%  p75 {_q(.75):+.2f}%  p90 {_q(.90):+.2f}%  "
          f"p95 {_q(.95):+.2f}%  p99 {_q(.99):+.2f}%  max {prems[-1]:+.2f}%")
    print(f"  share above the 5.0% gate: "
          f"{len(aff) / max(len(prems), 1) * 100:.1f}%")

    # --------------------------------------------- per cluster, per regime
    print("\n  per cluster (variant - control, per trade):")
    for clu in ("micro", "small"):
        dd, sse, tt = _welch([x["ret"] for x in vr["trades"] if x["clu"] == clu],
                             [x["ret"] for x in lr["trades"] if x["clu"] == clu])
        nv = sum(1 for x in vr["trades"] if x["clu"] == clu)
        nc = sum(1 for x in lr["trades"] if x["clu"] == clu)
        na_ = sum(1 for x in aff if x["clu"] == clu)
        print(f"    {clu:<6}{dd:>+7.2f}%  +/-{sse:.2f}  t={tt:+.2f}"
              f"   (n {nv} vs {nc}, affected {na_})")
    print("\n  per regime block (variant - control, per trade):")
    for b in sorted({_blk(x["day"]) for x in lr["trades"] + vr["trades"]}):
        dd, sse, tt = _welch([x["ret"] for x in vr["trades"] if _blk(x["day"]) == b],
                             [x["ret"] for x in lr["trades"] if _blk(x["day"]) == b])
        na_ = sum(1 for x in aff if _blk(x["day"]) == b)
        print(f"    {b:<10}{dd:>+7.2f}%  +/-{sse:.2f}  t={tt:+.2f}"
              f"   (affected {na_})")

    print("\n  exit mix: control " + str(dict(Counter(x["why"] for x in lr["trades"])))
          + " variant " + str(dict(Counter(x["why"] for x in vr["trades"]))))
    print("PHASE affected trades and subsets done", flush=True)

    # --------------------------------------------- rank-depth slope
    # Six disjoint cohorts, both arms, so the delta is measured on today's
    # corpus as well as against the recorded figure the bar names.
    coh_tasks = [{"arm": arm, "gated": arm == "rule", "impact_c": 1.0,
                  "offset": k} for k in range(6) for arm in ("control", "rule")]
    with ctx.Pool(6) as p:
        coh = p.map(_one, coh_tasks)
    CO = {(cfg["arm"], cfg["offset"]): r for cfg, r in coh}
    print("PHASE rank cohorts done", flush=True)

    slopes = {}
    for arm in ("control", "rule"):
        xs = [k for k in range(6) for _ in CO[(arm, k)]["trades"]]
        ys = [x["ret"] for k in range(6) for x in CO[(arm, k)]["trades"]]
        b, bse, bt = remeasure.slope(xs, ys)
        g, gse, gt = remeasure.gap(CO[(arm, 0)], CO[(arm, 5)])
        slopes[arm] = {"slope": b, "se": bse, "t": bt, "n": len(ys),
                       "gap": g, "gap_se": gse, "gap_t": gt}
    print(f"\n  rank-depth slope, six disjoint cohorts:")
    print(f"    {'cohort':<9}{'control n':>11}{'control avg':>13}"
          f"{'rule n':>9}{'rule avg':>11}{'affected':>10}")
    for k in range(6):
        ct = [x["ret"] for x in CO[("control", k)]["trades"]]
        vt = [x["ret"] for x in CO[("rule", k)]["trades"]]
        nk = len(_affected(CO[("control", k)]["trades"]))
        print(f"    {k:<9}{len(ct):>11}{statistics.fmean(ct) if ct else 0:>+12.2f}%"
              f"{len(vt):>9}{statistics.fmean(vt) if vt else 0:>+10.2f}%{nk:>10}")
    for arm in ("control", "rule"):
        s_ = slopes[arm]
        print(f"    {arm:<8} {s_['slope']:+.4f}%/step +/-{s_['se']:.4f} "
              f"t={s_['t']:+.2f} n={s_['n']}   top-deepest {s_['gap']:+.2f}% "
              f"+/-{s_['gap_se']:.2f} t={s_['gap_t']:+.2f}")

    # The bar names the RECORDED slope, not the one measured alongside. Degraded
    # means FLATTER, i.e. less negative: delta = rule - recorded, and a positive
    # delta above the tolerance fails.
    rec = sl["slope_pct_per_step"]
    delta = slopes["rule"]["slope"] - rec
    slope_pass = delta <= 0.3
    print(f"    recorded {rec:+.4f} (batch {sl['batch']}) -> rule "
          f"{slopes['rule']['slope']:+.4f}   delta {delta:+.4f} pts/step   "
          f"{'PASS' if slope_pass else 'FAIL rank_slope_degraded'} "
          f"(tolerance 0.3)")

    payload = {
        "batch_tag": BATCH, "rule_id": RULE_ID,
        "baseline_read_from": "data/breakout/baseline.json",
        "baseline_value": bl,
        "corpus": {"symbols": len(_C), "sessions": len(_D),
                   "first": str(_D[0]), "last": str(_D[-1])},
        "control": {"cagr": round(lr["cagr"], 2), "maxdd": round(lr["maxdd"], 1),
                    "n": len(lr["trades"]),
                    "per_trade": round(remeasure.edge(lr)[0], 2),
                    "std_err": round(remeasure.edge(lr)[1], 2),
                    "win": round(sum(1 for x in lr["trades"] if x["ret"] > 0)
                                 / max(len(lr["trades"]), 1) * 100)},
        "with_rule": {"cagr": round(vr["cagr"], 2), "maxdd": round(vr["maxdd"], 1),
                      "n": len(vr["trades"]),
                      "per_trade": round(remeasure.edge(vr)[0], 2),
                      "std_err": round(remeasure.edge(vr)[1], 2),
                      "win": round(sum(1 for x in vr["trades"] if x["ret"] > 0)
                                   / max(len(vr["trades"]), 1) * 100),
                      "t": round(t, 2)},
        "effect": {"size": round(d, 2), "std_err": round(se, 2), "t": round(t, 2)},
        "primary_affected_vs_unaffected": {
            "affected_n": na,
            "affected_mean": (round(ma, 2) if ma == ma else None),
            "unaffected_n": nu,
            "unaffected_mean": (round(mu, 2) if mu == mu else None),
            "difference": (round(ad, 2) if ad == ad else None),
            "std_err": (round(ase, 2) if ase == ase else None),
            "t": (round(at_, 2) if at_ == at_ else None)},
        "impact_sensitivity": rows_c,
        "rank_slope": {"baseline": rec, "with_rule": round(slopes["rule"]["slope"], 4),
                       "control_remeasured": round(slopes["control"]["slope"], 4),
                       "delta": round(delta, 4), "pass": bool(slope_pass),
                       "n": slopes["rule"]["n"],
                       "affected_by_cohort": {k: len(_affected(CO[("control", k)]["trades"]))
                                              for k in range(6)}},
        "fill_premium_pct": {"p50": round(_q(.50), 2), "p75": round(_q(.75), 2),
                             "p90": round(_q(.90), 2), "p95": round(_q(.95), 2),
                             "p99": round(_q(.99), 2),
                             "max": round(prems[-1], 2) if prems else None,
                             "over_gate": len(aff), "of": len(prems)},
        "no_refill_violations": refill,
        "affected_trades": [
            {"symbol": x["sym"], "cluster": x["clu"],
             "fill_day": str(x["fill_day"]), "exit_day": str(x["day"]),
             "fill_premium_pct": round(x["prem"], 2), "ret_pct": round(x["ret"], 2),
             "net_rs": round(x["net"]), "exit": x["why"]}
            for x in sorted(aff, key=lambda y: y["fill_day"])],
    }
    if out_path:
        out_path.write_text(json.dumps(payload, indent=1) + "\n")
        print(f"\n  wrote {out_path}")
    print("PHASE done", flush=True)
    return payload


def _selftest():
    """Cheap on purpose -- this runs in every sweep. It asserts the
    PRE-REGISTRATION, which is the thing that must exist before the run."""
    doc = (__doc__ or "")
    assert doc.strip(), "no docstring: the hypothesis was never registered"
    assert "hypothesis:" in doc, "the docstring states no hypothesis"
    assert "@@" not in doc, "the template placeholders were never filled in"
    assert "adoption bar" in doc, "the docstring states no adoption bar"
    assert "control:" in doc, "the docstring names no control"
    assert BATCH and "@@" not in BATCH, "no batch tag: the result cannot be compared"
    assert tuple(C_GRID) == (0.0, 0.5, 1.0, 2.0, 3.0), \
        "the impact sensitivity grid was narrowed"
    print(f"{Path(__file__).stem} selftest ok (pre-registered, batch {BATCH})")


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else main()
