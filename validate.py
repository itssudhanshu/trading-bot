#!/usr/bin/env python3
"""Walk-forward promotion gate: the last step before a holdout consultation.

PROMOTION CRITERIA ARE PRE-REGISTERED. They are fixed here, in code, before any
candidate is examined against them. Choosing thresholds after seeing results is
fitting with extra steps, and it is the failure mode this whole harness exists
to prevent. If a criterion below turns out to be wrong, change it and re-run
EVERYTHING -- never tune it against a candidate you have already seen.

Folds partition the TRAIN span only. The holdout is untouched here; a promoted
spec earns one consultation, and that costs budget.
"""
import argparse
import json
import statistics
import sys
from datetime import timedelta
from pathlib import Path

import backtest
import features
import generator
import judge
import spec as specmod
import split

ROOT = Path(__file__).resolve().parent
CANDIDATES = ROOT / "data" / "candidates.jsonl"
PROMOTED = ROOT / "data" / "promoted.jsonl"

# --- pre-registered promotion criteria ------------------------------------
# REVISION 2026-08-15, after a pass promoted 0/20. Two changes, both TIGHTENING
# or correcting an error -- recorded here because revising criteria after seeing
# results is the classic way this discipline dies, and the audit trail is the
# only defence.
#
#   (a) MIN_TRADES_PER_FOLD now counts TAKEN trades, not generated instances.
#       This was an error: a spec generating 8,991 signals and taking 78 has 78
#       trades of evidence. Counting instances overstated statistical power by
#       two orders of magnitude. Strictly more rigorous.
#
#   (b) MAX_CAPACITY_RATIO now applies only when rank.by == "none". Its original
#       justification was that an over-subscribed spec is under-specified --
#       which trades you get is chronological accident. The rank rule (added
#       after this criterion was written) removes that accident: the spec now
#       states its own preference. With (a) in force this is largely redundant
#       anyway, since heavy selection fails the taken-trade floor on its own.
N_FOLDS = 4
MIN_TRADES_PER_FOLD = judge.MIN_TRADES      # 30; below this a fold is not evidence
MIN_POSITIVE_FOLDS = 3                      # of 4; an edge should not flip sign
MAX_DD = judge.MAX_DD                       # 0.25
# A spec offering far more signals than the portfolio can hold is UNDER-SPECIFIED:
# which trades you get depends on chronological accident, not on the strategy.
# It needs a selection rule (rank by R:R? delivery z?) before it means anything.
MAX_CAPACITY_RATIO = 3.0
SHORTLIST = 20                              # walk-forward only the train-ranked top N


def fold_stats(spec, corpus, bd, ctx_cache, folds, allowed=None):
    sigs = generator.signals_for(spec, corpus, bd, ctx_cache, allowed=allowed)
    hold = spec.get("hold", {}).get("max_bars", backtest.MAX_HOLD)
    trades = [t for t in (backtest.simulate(sig, s, i, q, hold)
                          for i, s, sig, q in sigs) if t]
    out = []
    for train, test in folds:
        lo, hi = test[0], test[-1]
        sub = [t for t in trades if lo <= t.signal_day <= hi]
        if not sub:
            out.append({"n": 0, "n_taken": 0, "exp": 0.0, "dd": 1.0})
            continue
        _, dd, adm = backtest.portfolio_path(sub)
        out.append({
            "n": len(sub), "n_taken": len(adm), "dd": dd,
            "exp": statistics.fmean(t.net for t in adm) if adm else 0.0,
            "capacity": len(sub) / len(adm) if adm else float("inf"),
        })
    return out


def verdict(folds_stats, rank="none"):
    """-> (promoted: bool, reasons: list[str]). Every failure is named."""
    reasons = []
    thin = [f for f in folds_stats if f["n_taken"] < MIN_TRADES_PER_FOLD]
    if thin:
        reasons.append(f"{len(thin)}/{len(folds_stats)} folds under "
                       f"{MIN_TRADES_PER_FOLD} taken trades")
    positive = sum(1 for f in folds_stats if f["exp"] > 0)
    if positive < MIN_POSITIVE_FOLDS:
        reasons.append(f"only {positive}/{len(folds_stats)} folds positive "
                       f"(need {MIN_POSITIVE_FOLDS})")
    worst_dd = max(f["dd"] for f in folds_stats)
    if worst_dd > MAX_DD:
        reasons.append(f"worst fold drawdown {worst_dd*100:.0f}% > {MAX_DD*100:.0f}%")
    cap = max(f.get("capacity", 1.0) for f in folds_stats)
    if rank == "none" and cap > MAX_CAPACITY_RATIO:
        reasons.append(f"capacity ratio {cap:.1f}x > {MAX_CAPACITY_RATIO}x "
                       f"(under-specified: needs a signal selection rule)")
    return (not reasons), reasons


def main(shortlist=SHORTLIST):
    rows = [json.loads(l) for l in CANDIDATES.read_text().splitlines() if l.strip()]
    if not rows:
        print("no candidates; run generator.py first")
        return
    key = "portfolio_expectancy" if "portfolio_expectancy" in rows[0] else "expectancy_after_costs"
    if key != "portfolio_expectancy":
        print("WARNING: candidates predate portfolio metrics (lessons L11/L12). "
              "Re-run generator.py before trusting this.", file=sys.stderr)
    rows.sort(key=lambda r: r[key], reverse=True)
    rows = rows[:shortlist]
    print(f"walk-forward on top {len(rows)} of the train ranking "
          f"(shortlisting is itself selection -- the holdout is the real test)\n")

    corpus = features.load_corpus()
    all_days = sorted({d for s in corpus.values() for d in s.days})
    train_days, _ = split.split_days(all_days)
    allowed = set(train_days)
    assert not any(split.is_holdout(d) for d in allowed), "holdout leaked"
    bd = features.breadth(corpus)
    folds = backtest.walk_forward_folds(train_days, N_FOLDS)
    ctx_cache = {}

    promoted = []
    print(f"  {'hash':16} {'setup':16} {'fold n/exp':>34} {'verdict'}")
    for r in rows:
        fs = fold_stats(r["spec"], corpus, bd, ctx_cache, folds, allowed)
        ok, reasons = verdict(fs, r["spec"].get("rank", {}).get("by", "none"))
        cells = "  ".join(f"{f['n_taken']:>3}/{f['exp']:>+6.0f}" for f in fs)
        print(f"  {r['spec_hash']:16} {r['spec']['setup']:16} {cells}  "
              f"{'PROMOTED' if ok else reasons[0]}")
        if ok:
            promoted.append({**r, "folds": fs})

    PROMOTED.write_text("".join(json.dumps(p, default=str) + "\n" for p in promoted))
    print(f"\npromoted: {len(promoted)}/{len(rows)}")
    if promoted:
        print("each promoted spec may now be consulted against the sealed holdout, "
              f"at one budget unit each ({judge._load()['spent']}/{judge.BUDGET} spent)")
    else:
        print("nothing survived walk-forward. No holdout consultation is warranted, "
              "and no budget is spent -- which is the system working, not failing.")


def _selftest():
    good = [{"n": 50, "n_taken": 40, "exp": 100.0, "dd": 0.1, "capacity": 1.2}] * 4
    ok, why = verdict(good)
    assert ok, why

    thin = [{"n": 5, "n_taken": 5, "exp": 900.0, "dd": 0.01, "capacity": 1.0}] * 4
    ok, why = verdict(thin)
    assert not ok and "under 30 taken trades" in why[0], why

    # many instances but few taken: evidence is the TAKEN count, not the instances
    selective = [{"n": 9000, "n_taken": 12, "exp": 500.0, "dd": 0.05, "capacity": 750.0}] * 4
    ok, why = verdict(selective, rank="rr")
    assert not ok and "taken trades" in why[0], why

    flippy = [{"n": 50, "n_taken": 40, "exp": e, "dd": 0.1, "capacity": 1.0}
              for e in (500.0, -400.0, 300.0, -200.0)]
    ok, why = verdict(flippy)
    assert not ok and "2/4 folds positive" in why[0], why

    deep = [{"n": 50, "n_taken": 40, "exp": 100.0, "dd": 0.6, "capacity": 1.0}] * 4
    assert not verdict(deep)[0]

    over = [{"n": 5000, "n_taken": 50, "exp": 100.0, "dd": 0.1, "capacity": 100.0}] * 4
    ok, why = verdict(over, rank="none")
    assert not ok and "under-specified" in why[-1], why
    # with an explicit rank rule the selection is defined, so capacity is allowed
    ok2, why2 = verdict(over, rank="rr")
    assert ok2, why2

    # a spec failing several criteria names all of them, not just the first
    bad = [{"n": 5, "n_taken": 2, "exp": -10.0, "dd": 0.9, "capacity": 50.0}] * 4
    assert len(verdict(bad, rank="none")[1]) == 4, verdict(bad, rank="none")[1]
    print("validate selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shortlist", type=int, default=SHORTLIST)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest()
    else:
        main(a.shortlist)
