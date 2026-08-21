#!/usr/bin/env python3
"""Should the size bands hold seats at all, or should merit take all five?

THE PROPOSAL, in the operator's words: drop the 3/2 quota and let the split fall
where it lands -- 5/0 if small looks stronger than micro this week, 0/5 if it
does not. The bucket then takes the best five outright and the size clusters
only decide who is ELIGIBLE, not how the seats divide.

THIS ALREADY EXISTS AND WAS ALREADY REVERTED. `selection.RANKING = "pooled"`
does exactly that via `clusters.pick_pooled`, which recomputes the percentile
score across the COMBINED pool rather than merging per-band percentiles -- so
the comparison it makes is legitimate, not apples to oranges. Measured on
2026-08-16 it won 2 of 7: CAGR +16.61% vs +13.57% and CAGR-per-drawdown 0.553
vs 0.471, against worst half-year -119.4% vs -83.6%, best single symbol 15.4%
of all gains vs 7.6%, 119 symbols traded vs 136, 2.54 held vs 3.09, and -1,588
vs +5,349 replaying the last 30 sessions. The recorded verdict: two wins, both
of them the measure a best-of-N search inflates by construction.

SO WHY RE-RUN IT. Because every one of those numbers is PRE-GUARD. They date
from 2026-08-16; the circuit-lock guard (L58) landed on the 19th and CLAUDE.md's
rule is explicit -- any figure without a post-guard batch tag is the old,
phantom-filled one. The guard removed about half the CAGR, more than most of the
gaps it was used to judge, and it FLIPPED one verdict outright: the breakout
trigger went from costing a point of CAGR to winning by nearly ten. Seven
decisions were re-measured in batch 20260819-postlock. This was not one of them.
Re-running a decision whose evidence was invalidated is not a knob search; it is
the same repair the other seven already had.

WHAT POOLING ACTUALLY DOES, which is not what the proposal assumes. It does not
make the bucket adaptive. Ranking within a band neutralises `liq` by comparing a
stock only against others of similar turnover; pooling stops doing that, so
turnover becomes a live differentiator and the more liquid band wins seats
STRUCTURALLY rather than because it is having a good week. Checked on
2026-08-20: the pooled top ten was 9 small / 1 micro against 8 / 2 per-cluster.
That is a tilt, not a response.

HYPOTHESIS, written before the run. Pooling reproduces its pre-guard SHAPE:
higher CAGR, worse worst-block, higher concentration, fewer names held, and a
per-trade edge that does NOT resolve. If instead pooling now shows lower
concentration and a better tail, the guard changed the character of the
comparison and not merely its level, which would be a finding in itself.

ENDPOINT. Per-trade return with std err and t against the live per-cluster 3/2,
plus the six measures the original decision turned on, so this is like-for-like
rather than a fresh choice of yardstick: worst six-month block return,
concentration (top-1 share of gains, distinct symbols), occupancy, CAGR, maxDD,
and paired per-block drawdown.

THE PROMOTION BAR, fixed here before a single run:

  Adopt pooled ONLY if ALL of:
    a. per-trade edge vs live has |t| > 2.0 IN POOLED'S FAVOUR
    b. the worst six-month block does NOT worsen
    c. concentration does NOT worsen -- top-1 share no higher AND distinct
       symbols no fewer
    d. paired per-block drawdown does not worsen at |t| > 2

  (b) and (c) are conditions rather than footnotes precisely because they are
  where pooled lost last time. Tightening a test that previously let something
  through is allowed; loosening one is not.

  Anything else is reported as "inside the noise" IN THOSE WORDS and nothing
  changes.

MULTIPLICITY. ONE comparison, pooled against live, so no correction is needed
and none is applied. Stated because the immediately preceding experiment
(20260820-drawdown) tested two arms against one reference WITHOUT registering a
correction, and its headline cleared the bar as written while missing the
Bonferroni threshold. That defect is not repeated here.

WHAT IS NOT VARIED. Five seats, hold, stop, target, trigger, weights and the
impact constant all stay live. Risk invariants untouched.

ONE MECHANICAL WRINKLE, now FIXED, recorded because the fix is what makes the
post-hoc arms possible. Under `RANKING = "pooled"`, `allocate()` took
`rows[:MAX_POSITIONS]` -- the MODULE constant -- where the per-cluster path took
an injectable quota. So a pooled bucket could not be sized at all: asked for
eight seats it allocated five, and the arm would have been mislabelled rather
than wrong-looking. Same defect as `position_size` carried (bucket_size_test):
a count injected by the caller and ignored by the callee. `allocate()` takes
`max_pos` now, defaulting to the live constant, and `selection._selftest`
asserts the pooled path honours 3/5/8/12.

The PRE-REGISTERED comparison above is still pooled-at-5 against live-at-5, one
comparison, no multiplicity correction. The 8- and 12-seat pooled arms were
asked for after that result was seen; they are POST_HOC, reported below the bar
and unable to promote through it.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import statistics

import analysis          # concentration(), already the project's definition
import drawdown_test     # blocks() and paired(), already written and tested
import entry, features, selection, simulate

BATCH = "20260820-pooled"

BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, refresh=5, trigger=selection.TRIGGER)

# (label, RANKING value, take_per_cluster). Pooled REQUIRES take=None: with a
# quota it is neither one thing nor the other, and allocate() says so.
# (label, RANKING, take_per_cluster, seats). Pooled at 8 and 12 was proposed
# AFTER the 5-seat result was seen and is marked post-hoc for that reason: it
# cannot promote under the bar above, which compares one variant to live. It is
# measurable at all only because allocate() now honours an injected seat count;
# it read the module constant before, so every pooled arm allocated five however
# many it was asked for.
ARMS = [
    ("per_cluster 3/2", "per_cluster", dict(selection.TAKE_PER_CLUSTER), 5),
    ("pooled",          "pooled",      None, 5),
]
POST_HOC = [
    ("pooled@8",  "pooled", None, 8),
    ("pooled@12", "pooled", None, 12),
]
LIVE = "per_cluster 3/2"


def _worst_block(bl):
    """-> the worst six-month block RETURN. The tail measure that has
    generalised in this project, and the one pooled lost on before."""
    return min(v[1] for v in bl.values()) if bl else 0.0


def measure(corpus, days, arms=None):
    out = {}
    original = selection.RANKING
    try:
        for label, ranking, take, seats in (arms or ARMS):
            # Set the variant INSIDE the fork and restore it, so a variant
            # cannot leak into its sibling or into the live module.
            selection.RANKING = ranking
            entry._CACHE.clear()
            r = simulate.run(corpus, days, max_pos=seats,
                             take_per_cluster=take, **BASE)
            if not r["trades"]:
                raise SystemExit(f"{label}: no trades; nothing to compare")
            bl = drawdown_test.blocks(r["curve"])
            out[label] = {
                "cagr": r["cagr"], "maxdd": r["maxdd"],
                "occ": r.get("occupancy"), "blocks": bl,
                "worst_block": _worst_block(bl),
                "conc": analysis.concentration(r["trades"]),
                "rets": [t["ret"] for t in r["trades"]],
                "clusters": {c: sum(1 for t in r["trades"] if t["clu"] == c)
                             for c in ("micro", "small")},
            }
    finally:
        selection.RANKING = original
    # The live module must be exactly as it was found. A leak here would make
    # every later run in this process describe a bucket nobody chose.
    assert selection.RANKING == original, "RANKING leaked out of the fork"
    return out


def report(res):
    live = res[LIVE]
    lm, lse, ln = drawdown_test._stats(live["rets"])
    lbl = {k: v[0] for k, v in live["blocks"].items()}
    print(f"batch {BATCH} | hold={BASE['hold']}d trigger={BASE['trigger']} "
          f"impact_c={simulate.engine.IMPACT_C} seats={selection.MAX_POSITIONS}")
    print(f"\n{'measure':<28}{'per_cluster 3/2':>18}{'pooled':>18}   better")
    p = res["pooled"]
    pm, pse, pn = drawdown_test._stats(p["rets"])
    rows = [
        ("CAGR", f"{live['cagr']:+.2f}%", f"{p['cagr']:+.2f}%",
         "pooled" if p["cagr"] > live["cagr"] else "per_cluster"),
        ("maxDD (whole path)", f"{live['maxdd']:.1f}%", f"{p['maxdd']:.1f}%",
         "pooled" if p["maxdd"] < live["maxdd"] else "per_cluster"),
        ("per trade", f"{lm:+.2f}% +/-{lse:.2f}", f"{pm:+.2f}% +/-{pse:.2f}",
         "pooled" if pm > lm else "per_cluster"),
        ("trades", f"{ln}", f"{pn}", "-"),
        ("worst 6-month block", f"{live['worst_block']:+.1f}%",
         f"{p['worst_block']:+.1f}%",
         "pooled" if p["worst_block"] > live["worst_block"] else "per_cluster"),
        ("top-1 share of gains", f"{live['conc']['top1']:.1f}%",
         f"{p['conc']['top1']:.1f}%",
         "pooled" if p["conc"]["top1"] < live["conc"]["top1"] else "per_cluster"),
        ("distinct symbols", f"{live['conc']['n_symbols']}",
         f"{p['conc']['n_symbols']}",
         "pooled" if p["conc"]["n_symbols"] > live["conc"]["n_symbols"]
         else "per_cluster"),
        ("stocks held (occupancy)", f"{live['occ']:.2f}", f"{p['occ']:.2f}",
         "pooled" if p["occ"] > live["occ"] else "per_cluster"),
        ("trades micro / small",
         f"{live['clusters']['micro']} / {live['clusters']['small']}",
         f"{p['clusters']['micro']} / {p['clusters']['small']}", "-"),
    ]
    for name, a, b, better in rows:
        print(f"{name:<28}{a:>18}{b:>18}   {better}")

    t = drawdown_test._t2(pm, pse, lm, lse)
    print(f"\nper-trade edge, pooled minus live: {pm - lm:+.2f}%  t={t:+.2f}  "
          f"{'RESOLVED' if abs(t) > 2 else 'inside the noise'}")
    pbl = {k: v[0] for k, v in p["blocks"].items()}
    m, se, n, bt, _ = drawdown_test.paired(pbl, lbl)
    print(f"paired block drawdown, pooled minus live: {m:+.2f}%  "
          f"std err {se:.2f}  t={bt:+.2f}  n={n} blocks  "
          f"{'RESOLVED' if abs(bt) > 2 else 'inside the noise'}")
    wins = sum(1 for _, _, _, b in rows if b == "pooled")
    print(f"\npooled wins {wins} of {sum(1 for r in rows if r[3] != '-')} "
          f"compared measures")
    return t, bt


def promote(res, t, bt):
    """-> (adopt, why). Bar fixed in the docstring before any run."""
    live, p = res[LIVE], res["pooled"]
    fail = []
    if not (t > 2.0):
        fail.append(f"per-trade edge is not resolved in pooled's favour "
                    f"(t={t:+.2f})")
    if p["worst_block"] < live["worst_block"]:
        fail.append(f"worst block worsens ({p['worst_block']:+.1f}% vs "
                    f"{live['worst_block']:+.1f}%)")
    if p["conc"]["top1"] > live["conc"]["top1"]:
        fail.append(f"top-1 share of gains worsens ({p['conc']['top1']:.1f}% "
                    f"vs {live['conc']['top1']:.1f}%)")
    if p["conc"]["n_symbols"] < live["conc"]["n_symbols"]:
        fail.append(f"fewer distinct symbols ({p['conc']['n_symbols']} vs "
                    f"{live['conc']['n_symbols']})")
    if bt > 2.0:
        fail.append(f"block drawdown worsens (t={bt:+.2f})")
    if fail:
        return False, "; ".join(fail)
    return True, ("pooled clears every condition of the pre-set bar -- a "
                  "RECOMMENDATION to put to the operator, not a change")


def _selftest():
    import datetime as _dt
    d = _dt.date
    # worst block is the worst RETURN, not the worst drawdown
    bl = {"2024H1": (10.0, -5.0), "2024H2": (30.0, +2.0)}
    assert _worst_block(bl) == -5.0, _worst_block(bl)
    assert _worst_block({}) == 0.0
    # the bar must veto on each condition independently
    base = {"worst_block": -10.0, "conc": {"top1": 8.0, "n_symbols": 130},
            "cagr": 7.0, "maxdd": 30.0, "rets": [1.0] * 50, "occ": 3.0,
            "blocks": {}, "clusters": {"micro": 1, "small": 1}}
    good = dict(base)
    res = {LIVE: base, "pooled": good}
    ok, why = promote(res, t=3.0, bt=0.0)
    assert ok, why
    for tweak, word in (({"worst_block": -20.0}, "worst block"),
                        ({"conc": {"top1": 15.0, "n_symbols": 130}}, "top-1"),
                        ({"conc": {"top1": 8.0, "n_symbols": 100}}, "distinct")):
        bad = dict(base, **tweak)
        ok2, why2 = promote({LIVE: base, "pooled": bad}, t=3.0, bt=0.0)
        assert not ok2 and word in why2, (tweak, why2)
    # an unresolved edge vetoes even when everything else is fine
    ok3, why3 = promote(res, t=1.5, bt=0.0)
    assert not ok3 and "not resolved" in why3, why3
    # and so does worsening block drawdown
    ok4, why4 = promote(res, t=3.0, bt=2.5)
    assert not ok4 and "drawdown worsens" in why4, why4
    # RANKING must be restorable; measure() asserts this too
    was = selection.RANKING
    selection.RANKING = "pooled"
    selection.RANKING = was
    assert selection.RANKING == "per_cluster", selection.RANKING
    print("pooled_test selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        corpus = features.load_corpus()
        days = sorted({d for x in corpus.values() for d in x.days})
        res = measure(corpus, days)
        t, bt = report(res)
        extra = measure(corpus, days, arms=POST_HOC)
        lm, lse, _ = drawdown_test._stats(res[LIVE]["rets"])
        lbl = {k: v[0] for k, v in res[LIVE]["blocks"].items()}
        print("\n\nPOST-HOC: pooled at other seat counts. Proposed after the "
              "5-seat result;\nreported, and excluded from the bar above.")
        print(f"\n{'arm':<12}{'CAGR':>9}{'maxDD':>8}{'worstBlk':>10}{'top1':>8}"
              f"{'syms':>6}{'occ':>6}{'n':>6}{'per trade':>16}{'t':>7}")
        for label, _, _, seats in POST_HOC:
            d = extra[label]
            m, se, n = drawdown_test._stats(d["rets"])
            tt = drawdown_test._t2(m, se, lm, lse)
            print(f"{label:<12}{d['cagr']:>+8.2f}%{d['maxdd']:>7.1f}%"
                  f"{d['worst_block']:>9.1f}%{d['conc']['top1']:>7.1f}%"
                  f"{d['conc']['n_symbols']:>6}{d['occ']:>6.2f}{n:>6}"
                  f"{m:>+11.2f}% +/-{se:.2f}{tt:>+7.2f}")
            pb = {k: v[0] for k, v in d["blocks"].items()}
            bm, bse, bn, bbt, _ = drawdown_test.paired(pb, lbl)
            print(f"{'':12}paired block drawdown vs live {bm:+.2f}% "
                  f"t={bbt:+.2f} (n={bn})  "
                  f"{'RESOLVED' if abs(bbt) > 2 else 'inside the noise'}")
        ok, why = promote(res, t, bt)
        print(f"\nPROMOTION BAR: {'ADOPT -- ' if ok else 'no change. '}{why}")
        print("\nrecord:", json.dumps(
            {"at": BATCH, "kind": "pooled_vs_per_cluster", "adopt": ok,
             "why": why,
             "arms": {k: {"cagr": v["cagr"], "maxdd": v["maxdd"],
                          "worst_block": v["worst_block"], "occ": v["occ"],
                          "top1": v["conc"]["top1"],
                          "n_symbols": v["conc"]["n_symbols"],
                          "n": len(v["rets"]),
                          "per_trade": drawdown_test._stats(v["rets"])[0]}
                      for k, v in res.items()}}))
