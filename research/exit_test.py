#!/usr/bin/env python3
"""Can the book hold for 6-8 days with a 5% stop, and does moving the stop help?

Three PRE-REGISTERED experiments. They are written down here before running so
the result cannot be chosen after the fact -- this project's own record (L47)
is that parameter tuning on this book anti-predicts, and a grid searched for
its winner would inflate exactly the number the operator would then trade.

  1. FACTORIAL. Stop and hold are changed one at a time and then together, so
     a bad result at 5%/7d can be attributed. Four runs, no free parameters.

  2. TARGET AT THE TIGHT STOP. A 20% target on a 5% stop is 4R, and L1/L8 say
     large R multiples need TIME -- which is the thing being cut. So the
     target is swept only in the regime where the mechanism predicts trouble.

  3. MOVING STOP / MULTIPLE TARGETS. Book part of the position at a first
     target, then move the stop up under the rest. Tested at BOTH the current
     baseline and the proposed one, because portfolio.py's finding that every
     trail lowered expectancy was measured at 10%/15d and may not transfer.

Every comparison carries a per-trade error bar. A CAGR gap is arithmetic on
one path; only the per-trade edge says whether the rule picks better trades.
Positions are compared per POSITION, not per leg: a scaled exit books two
rows and counting them as two trades would flatter nothing and confuse
everything.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import statistics
import sys
from collections import defaultdict

import analysis
import entry
import features
import portfolio
import simulate

# The live book, exactly as it stands. Every variant is a delta from this.
BASE = dict(stop_pct=10.0, target_pct=20.0, hold=15, max_pos=5, refresh=5,
            trigger="breakout")

GOAL_HOLD = 7           # the operator's target: 6-8 sessions
GOAL_STOP = 5.0         # the operator's target: 5% stop

# (label, overrides) -- pre-registered, in the order they are argued for above.
VARIANTS = [
    ("baseline 10% / 20% / 15d", {}),
    ("stop 5% only",             dict(stop_pct=GOAL_STOP)),
    ("hold 7d only",             dict(hold=GOAL_HOLD)),
    ("GOAL 5% / 20% / 7d",       dict(stop_pct=GOAL_STOP, hold=GOAL_HOLD)),

    ("goal, target 10%",         dict(stop_pct=GOAL_STOP, hold=GOAL_HOLD, target_pct=10.0)),
    ("goal, target 15%",         dict(stop_pct=GOAL_STOP, hold=GOAL_HOLD, target_pct=15.0)),
    ("goal, hold 6d",            dict(stop_pct=GOAL_STOP, hold=6)),
    ("goal, hold 8d",            dict(stop_pct=GOAL_STOP, hold=8)),

    # both rules at once, kept for continuity with the first run
    ("base + sell half & move",  dict(targets=[(10.0, 0.5)], stop_to=(10.0, 0.0))),
    ("goal + sell half & move",  dict(stop_pct=GOAL_STOP, hold=GOAL_HOLD,
                                      targets=[(10.0, 0.5)], stop_to=(10.0, 0.0))),
]

# ---------------------------------------------------------------- --hold
# "Reducing trade time-period impact", isolated: the stop stays at 10%, only
# the clock moves. The earlier 6d/8d runs were at a 5% stop and so measured the
# stop, not the hold. The diagnostic that decides this is not the CAGR column
# but WHEN winners actually pay -- a target reached on day 12 cannot be
# collected by a book that leaves on day 8.
HOLD_VARIANTS = [
    ("baseline, hold 15d", {}),
    ("hold 5d",  dict(hold=5)),
    ("hold 6d",  dict(hold=6)),
    ("hold 7d",  dict(hold=7)),
    ("hold 8d",  dict(hold=8)),
    ("hold 10d", dict(hold=10)),
    ("hold 12d", dict(hold=12)),
    ("hold 20d", dict(hold=20)),
]

# ------------------------------------------------------------- --stopmove
# "Move the stop to entry once half the target is reached." Target is +20%, so
# half is +10%. NOTHING IS SOLD -- this is the stop move on its own, which the
# first run never measured because it always sold a fraction at the same time.
# The variants either side of +10% ask whether the trigger point matters and
# whether locking a small gain beats locking nothing.
STOPMOVE_VARIANTS = [
    ("baseline, stop never moves", {}),
    ("at +10% -> stop to entry",   dict(stop_to=(10.0, 0.0))),
    ("at +10% -> stop to +5%",     dict(stop_to=(10.0, 5.0))),
    ("at +5%  -> stop to entry",   dict(stop_to=(5.0, 0.0))),
    ("at +15% -> stop to entry",   dict(stop_to=(15.0, 0.0))),
    ("at +10% -> stop to -5%",     dict(stop_to=(10.0, -5.0))),
    ("at +10% -> entry, hold 8d",  dict(stop_to=(10.0, 0.0), hold=8)),
]

# --------------------------------------------------------------- --ladder
# "Multiple targets", on its own: sell in pieces on the way up and leave the
# stop where it is. Every rung is a separate order paying its own brokerage,
# STT and DP charge, which is a real cost on a Rs 45,000 position and is the
# reason a ladder can lose to a single exit even when it sells at better prices.
LADDER_VARIANTS = [
    ("baseline, one exit at +20%", {}),
    ("half at +10%, rest +20%",    dict(targets=[(10.0, 0.5)])),
    ("third at +7/+14, rest +20%", dict(targets=[(7.0, 1 / 3), (14.0, 1 / 3)])),
    ("quarter at +5/+10/+15",      dict(targets=[(5.0, 0.25), (10.0, 0.25),
                                                 (15.0, 0.25)])),
    ("half at +15%, rest +20%",    dict(targets=[(15.0, 0.5)])),
    ("half at +10%, hold 8d",      dict(targets=[(10.0, 0.5)], hold=8)),
    ("half at +10% + stop moves",  dict(targets=[(10.0, 0.5)], stop_to=(10.0, 0.0))),
]

# Second pre-registered set, run with --atr. The fixed 5% stop asks a 6%-vol
# microcap and a 2%-vol name to survive the same distance. If tighter stops are
# to work at all here, the distance has to scale with each name's own noise --
# so this asks what stop distance the book can actually carry, rather than
# assuming 5% is available.
ATR_VARIANTS = [
    ("baseline 10% / 20% / 15d", {}),
    ("atr 1.5x, hold 7d",        dict(atr_stop=1.5, hold=GOAL_HOLD)),
    ("atr 2.0x, hold 7d",        dict(atr_stop=2.0, hold=GOAL_HOLD)),
    ("atr 2.5x, hold 7d",        dict(atr_stop=2.5, hold=GOAL_HOLD)),
    ("atr 3.0x, hold 7d",        dict(atr_stop=3.0, hold=GOAL_HOLD)),
    ("atr 2.5x, hold 15d",       dict(atr_stop=2.5)),
    ("atr 3.0x, hold 15d",       dict(atr_stop=3.0)),
]

# ------------------------------------------------------------ --factorial
# The pair, measured rather than assumed. Both earlier mistakes in this file
# came from testing combinations instead of components; this is the reverse
# error and needs its own guard -- two changes that are each defensible alone
# can interact, and the ladder's tail benefit is exactly the kind of thing a
# shorter clock could already be collecting.
#
# 3 holds x 3 ladders. The corners are the two single changes and the control,
# so any interaction shows up as the grid failing to be additive.
FACTORIAL = [
    (f"hold {h}d, {lname}", {**({} if h == 15 else {"hold": h}),
                             **({} if rungs is None else {"targets": rungs})})
    for h in (8, 10, 15)
    for lname, rungs in (("no ladder", None),
                         ("half @+10", [(10.0, 0.5)]),
                         ("third @+7/+14", [(7.0, 1 / 3), (14.0, 1 / 3)]))
]
# The control must come first: main() reads res[0] as the baseline.
FACTORIAL = ([x for x in FACTORIAL if x[0] == "hold 15d, no ladder"]
             + [x for x in FACTORIAL if x[0] != "hold 15d, no ladder"])

_C = _D = None


def per_position(trades):
    """-> one row per POSITION, merging the legs of a scaled exit.

    Return is rupees netted over rupees deployed, so a half-sized second leg
    cannot count the same as a full one. Without this a scaled book shows more
    trades at a lower average and looks worse than it is (or better, when the
    partials are the winners) purely from how the rows were split.
    """
    by = defaultdict(list)
    for t in trades:
        by[t.get("pid", id(t))].append(t)
    out = []
    for legs in by.values():
        buy = sum(x.get("buy", 0.0) for x in legs)
        net = sum(x.get("net", 0.0) for x in legs)
        last = max(legs, key=lambda x: x["day"])
        out.append({"ret": (net / buy * 100) if buy else 0.0,
                    "why": "+".join(x["why"] for x in legs),
                    "clu": last["clu"], "day": last["day"], "sym": last["sym"],
                    "net": net, "legs": len(legs)})
    return out


def _one(item):
    label, over = item
    entry._CACHE.clear()
    kw = {**BASE, **over}
    r = simulate.run(_C, _D, **kw)
    pos = per_position(r["trades"])
    by = defaultdict(float)
    for x in pos:
        by[f"{x['day'].year}-H{1 if x['day'].month <= 6 else 2}"] += x["ret"]
    st = analysis.stats(pos)
    dist = [t["stop_dist"] for t in r["trades"] if t.get("stop_dist")]
    why = defaultdict(int)
    for x in r["trades"]:
        why[x["why"]] += 1
    return {"label": label, "over": over, "cagr": r["cagr"], "dd": r["maxdd"],
            "n": len(pos), "legs": len(r["trades"]),
            "win": sum(1 for x in pos if x["ret"] > 0) / max(len(pos), 1) * 100,
            "avg": st["mean"], "se": st["se"], "t": st["t"],
            "worst": min(by.values()) if by else float("nan"),
            "why": dict(why), "cluster": analysis.per_cluster(pos),
            "hold": kw["hold"], "stop": kw["stop_pct"], "tgt": kw["target_pct"],
            "dist": statistics.median(dist) if dist else None,
            "dist_lo": sorted(dist)[len(dist) // 10] if dist else None,
            "dist_hi": sorted(dist)[len(dist) * 9 // 10] if dist else None,
            "_r": r}


def diff(a, b):
    """-> (edge per position, its standard error, t) for a against b.

    Independent samples, so the standard errors add in quadrature. These are
    NOT paired trades: the two books hold different names on different days.
    """
    if not (a["se"] and b["se"]):
        return None, None, None
    d = a["avg"] - b["avg"]
    se = (a["se"] ** 2 + b["se"] ** 2) ** 0.5
    return d, se, (d / se if se else 0.0)


def main(variants=None):
    global _C, _D
    variants = variants or VARIANTS
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"exit rules — {len(variants)} pre-registered variants x {len(_D)} "
          f"sessions, Rs {portfolio.CAPITAL:,}\n")
    with mp.get_context("fork").Pool(min(len(variants), mp.cpu_count())) as p:
        res = p.map(_one, variants)

    print(f"  {'variant':<26}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>5}"
          f"{'per-trade':>12}{'std err':>9}{'worst blk':>11}")
    for x in res:
        print(f"  {x['label']:<26}{x['cagr']:>+8.2f}%{x['dd']:>7.1f}%"
              f"{x['win']:>5.0f}%{x['n']:>5}{x['avg']:>+11.2f}%"
              f"{x['se'] or 0:>8.2f}%{x['worst']:>+10.1f}%")

    ctl = res[0]
    print(f"\n  against the baseline ({ctl['avg']:+.2f}% per trade, n={ctl['n']}):")
    for x in res[1:]:
        d, se, t = diff(x, ctl)
        if d is None:
            continue
        verdict = "RESOLVED" if abs(t) > 1.96 else "inside the noise"
        print(f"    {x['label']:<26}{x['cagr'] - ctl['cagr']:>+7.2f} CAGR pts"
              f"{d:>+8.2f}% / trade  +/-{1.96 * se:>5.2f}  t{t:>+6.2f}  {verdict}")

    if any(x["dist"] for x in res):
        print("\n  stop distance actually placed (p10 / median / p90 of entries):")
        for x in res:
            if x["dist"]:
                print(f"    {x['label']:<26}{x['dist_lo']:>5.1f}% /"
                      f"{x['dist']:>5.1f}% /{x['dist_hi']:>5.1f}%")

    # The hold question is decided by WHEN winners pay, not by the CAGR
    # column: a target reached on day 12 is simply not collectable by a book
    # that leaves on day 8, and the cumulative curve says how much is forfeited
    # at each cutoff. Taken from the control, which is the only book that ran
    # long enough to observe it.
    ctl_t = [t for t in ctl["_r"]["trades"] if t.get("held") is not None]
    wins = sorted(t["held"] for t in ctl_t if t["why"] == "target")
    if wins:
        print(f"\n  when the baseline's {len(wins)} target hits actually landed:")
        for cut in (5, 6, 7, 8, 10, 12, 15):
            got = sum(1 for h in wins if h <= cut)
            bar = "█" * round(got / len(wins) * 30)
            print(f"    by day {cut:>2}  {got:>3} of {len(wins)}"
                  f" ({got / len(wins) * 100:>3.0f}%)  {bar}")
        print(f"    median target lands on day {statistics.median(wins):.0f}; "
              f"a book that leaves earlier forfeits the rest.")

    print("\n  exit mix (how each book actually ended its positions):")
    for x in res:
        mix = "  ".join(f"{k} {v}" for k, v in sorted(x["why"].items()))
        print(f"    {x['label']:<26}{mix}")

    print(f"\n  {analysis.trades_needed(analysis.BACKTEST_EDGE)} trades are "
          f"needed to resolve a {analysis.BACKTEST_EDGE:.1f}%/trade edge; "
          f"the gaps above are read against that, not against the CAGR column.")
    for x in res:
        simulate.keep(x["label"], x["_r"], {**BASE, **x["over"]},
                      batch="exits", track="cluster", note="exit rule test")
    return res


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # per_position is the only logic here that is not a call into simulate.
        legs = [{"pid": 1, "ret": 10.0, "buy": 1000.0, "net": 100.0, "why": "target1",
                 "clu": "micro", "day": __import__("datetime").date(2024, 1, 1),
                 "sym": "A"},
                {"pid": 1, "ret": -1.0, "buy": 1000.0, "net": -10.0, "why": "stop",
                 "clu": "micro", "day": __import__("datetime").date(2024, 1, 9),
                 "sym": "A"},
                {"pid": 2, "ret": -8.0, "buy": 2000.0, "net": -160.0, "why": "stop",
                 "clu": "small", "day": __import__("datetime").date(2024, 1, 5),
                 "sym": "B"}]
        got = per_position(legs)
        assert len(got) == 2, got
        merged = [g for g in got if g["sym"] == "A"][0]
        # 90 net on 2000 deployed = 4.5%, NOT the 4.5 average of 10 and -1
        assert abs(merged["ret"] - 4.5) < 1e-9, merged
        assert merged["legs"] == 2 and merged["why"] == "target1+stop", merged
        assert merged["day"] == __import__("datetime").date(2024, 1, 9), merged
        solo = [g for g in got if g["sym"] == "B"][0]
        assert abs(solo["ret"] - (-8.0)) < 1e-9, solo
        print("exit_test selftest ok")
    elif "--atr" in sys.argv:
        main(ATR_VARIANTS)
    elif "--hold" in sys.argv:
        main(HOLD_VARIANTS)
    elif "--stopmove" in sys.argv:
        main(STOPMOVE_VARIANTS)
    elif "--ladder" in sys.argv:
        main(LADDER_VARIANTS)
    elif "--factorial" in sys.argv:
        main(FACTORIAL)
    else:
        main()
