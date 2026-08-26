#!/usr/bin/env python3
"""FOLLOW-UP, pre-registered consequence of batch 20260826-lossclass.

There, H2 (fill premium) RESOLVED with its mechanism's sign: per-trade return
FALLS as the fill open sits further above the signal close. Harvest gap
top-bottom tercile -3.71% +/- 1.17 (t=-3.18, n=1060); resolved inside micro
(slope t=-2.02, gap t=-2.79) and in the two outer regime blocks (2019-2021
t=-2.58, 2024-2026 t=-3.45); null in small caps. H1 (market breadth at entry)
did NOT survive its own power harvest and earns nothing.

THE RULE SHAPE UNDER TEST -- one variant, no sweep:

    refuse an entry whose fill open is more than +2.29% above the signal close.

+2.29% is the count-based mid|top tercile boundary of fill premium measured on
those 1,060 harvested entries (batch 20260826-lossclass); it was printed by
that study and pasted here UNCHANGED. The mechanism: a trigger marks demand;
paying a large overnight extension buys exhaustion, and the -10% stop hangs
off a price that has already spent part of its move. The rule is implementable
live -- signal close is known the night before, the open is known at the open.

CONTROL: the live configuration, byte for byte. Nothing else varies -- not the
stop, not the hold, not the weights, not engine.py.

DECISION, fixed before running: this becomes a stored candidate ONLY if
(a) simulate.keep's promotion bar clears (CAGR > 5%, maxDD < 55%, n >= 150,
win >= 30%) AND (b) the per-trade gap against the control resolves at t > 2,
positive. Anything else is a null result recorded in docs/lessons.md, and the
rule is dropped -- including if it wins CAGR alone without the error bar.

KNOWN COST, accepted in advance: the rule bound on roughly the top third of
historical fills, so the variant book holds materially fewer positions and its
occupancy profile differs. That is part of what is measured, not a defect in
the measurement.
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
import remeasure
import selection
import simulate

BATCH = "20260826-premskip"

# Pasted from loss_taxonomy_test.py output, batch 20260826-lossclass. Do not
# recompute here: a threshold recomputed on the variant's own run would make
# the rule endogenous to the test that judges it.
PREM_SKIP = 2.29     # percent over signal close; strictly ABOVE is refused

BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)


def premium_guard(bound):
    """-> tradable(s, i, purpose). Entries whose fill open exceeds `bound`%
    over the prior close are refused; exits are never blocked (an upper lock
    has no sellers, but this rule has nothing to say about leaving -- blocking
    exits would be a risk change, and risk invariants are not searchable).
    Unknowable cases (first bar, dead prior close) pass rather than silently
    become a second, unstated rule."""
    def f(s, i, purpose):
        if purpose != "entry":
            return True
        if i < 1 or not s.close[i - 1]:
            return True
        # Percent space, normalised through round(..., 6): the raw ratio puts
        # an exact-boundary fill at 2.289999...96 versus the 2.29 literal, and
        # a rule that flips on binary dust is not a rule.
        prem = (s.open[i] / s.close[i - 1] - 1) * 100
        return round(prem, 6) <= bound
    return f


ARMS = [
    ("baseline (live rules)", None),
    (f"skip fill premium > +{PREM_SKIP}%", PREM_SKIP),
]

_C = _D = None


def _one(item):
    label, bound = item
    entry._CACHE.clear()
    kw = dict(BASE)
    if bound is not None:
        # Built INSIDE the worker: a fork pool pickles its task args, and a
        # closure is not picklable.
        kw["tradable"] = premium_guard(bound)
    r = simulate.run(_C, _D, **kw)
    return label, bound, r


def subset_gap(a_trades, b_trades, filt):
    """-> (gap, se, t) of mean per-trade return, arm A minus arm B, within one
    subset (cluster or regime block). remeasure.gap arithmetic on filtered
    rows; independent samples, so this can miss a real difference and cannot
    manufacture one."""
    fa = [t["ret"] for t in a_trades if filt(t)]
    fb = [t["ret"] for t in b_trades if filt(t)]
    if len(fa) < 2 or len(fb) < 2:
        return float("nan"), float("nan"), float("nan")
    ma, sa = statistics.fmean(fa), statistics.stdev(fa) / max(len(fa), 1) ** .5
    mb, sb = statistics.fmean(fb), statistics.stdev(fb) / max(len(fb), 1) ** .5
    se = (sa ** 2 + sb ** 2) ** 0.5
    d = ma - mb
    return d, se, (d / se if se else float("nan"))


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"FILL-PREMIUM SKIP  batch {BATCH}  threshold +{PREM_SKIP}%  "
          f"{len(_C)} symbols x {len(_D)} sessions\n")
    with mp.get_context("fork").Pool(len(ARMS)) as p:
        res = p.map(_one, ARMS)

    print(f"  {'arm':<28}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'per-trade':>12}{'std err':>9}")
    for label, guard, r in res:
        m, se, _n = remeasure.edge(r)
        win = sum(1 for x in r["trades"] if x["ret"] > 0) / max(len(r["trades"]), 1) * 100
        print(f"  {label:<28}{r['cagr']:>+8.2f}%{r['maxdd']:>7.1f}%"
              f"{win:>5.0f}%{len(r['trades']):>6}{m:>+11.2f}%{se:>8.2f}%")

    (l_label, _, lr), (v_label, _, vr) = res[0], res[1]
    d, se, t = remeasure.gap(vr, lr)
    print(f"\n  variant - control: {vr['cagr'] - lr['cagr']:+.2f} CAGR pts"
          f"  {d:+.2f}%/trade  +/-{se:.2f}  t={t:+.2f}  "
          f"{'RESOLVED' if abs(t) > 2 else 'inside the noise'}")
    print(f"  occupancy: control {lr['occupancy']:.2f} vs variant "
          f"{vr['occupancy']:.2f} average seats; full-book sessions "
          f"{lr['occ_full']:.0f}% vs {vr['occ_full']:.0f}%")

    print("\n  per cluster (variant - control, per trade):")
    for clu in ("micro", "small"):
        d, se, t = subset_gap(vr["trades"], lr["trades"],
                              lambda x, c=clu: x["clu"] == c)
        nv = sum(1 for x in vr["trades"] if x["clu"] == clu)
        nc = sum(1 for x in lr["trades"] if x["clu"] == clu)
        print(f"    {clu:<6}{d:>+7.2f}%  +/-{se:.2f}  t={t:+.2f}"
              f"   (n {nv} vs {nc})")

    def blk(day):
        y = int(str(day)[:4])
        return "2019-2021" if y <= 2021 else ("2022-2023" if y <= 2023
                                              else "2024-2026")
    print("\n  per regime block (variant - control, per trade):")
    blocks = sorted({blk(x["day"]) for x in lr["trades"] + vr["trades"]})
    for b in blocks:
        d, se, t = subset_gap(vr["trades"], lr["trades"],
                              lambda x, bb=b: blk(x["day"]) == bb)
        print(f"    {b:<10}{d:>+7.2f}%  +/-{se:.2f}  t={t:+.2f}")

    print("\n  exit mix, variant (does the rule just trade differently?):")
    why = defaultdict(int)
    for x in vr["trades"]:
        why[x["why"]] += 1
    print("    " + ", ".join(f"{k} {v}" for k, v in sorted(why.items())))

    kept = simulate.keep(v_label, vr, {**BASE, "max_fill_premium_pct": PREM_SKIP},
                         batch=BATCH, track="cluster",
                         note="H2 follow-up, threshold from 20260826-lossclass")
    bar_ok = kept is not None
    verdict = (bar_ok and abs(t) > 2 and d > 0)
    print(f"\n  promotion bar (keep): {'CLEARED' if bar_ok else 'NOT cleared'}; "
          f"error bar: {'RESOLVED' if abs(t) > 2 else 'inside the noise'}")
    print(f"  ENDPOINT: {'candidate stored -- forward paper trades decide next'
          if verdict else 'NULL RESULT -- rule dropped, nothing adopted'}")


def _selftest():
    from datetime import date, timedelta

    def series(closes):
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))]
        s = features.Series("G", days)
        for px in closes:
            s.open.append(float(px))
            s.high.append(px * 1.001)
            s.low.append(px * 0.999)
            s.close.append(float(px))
            s.volume.append(1000)
            s.turnover.append(1e6)
            s.deliv_pct.append(40.0)
            s.surveillance_known.append(True)
            s.restricted.append(False)
        return s

    g = premium_guard(PREM_SKIP)
    # prior close 100 -> ceiling 102.29; open exactly AT the bound passes
    assert g(series([100.0, 102.29]), 1, "entry") is True
    # strictly above is refused
    assert g(series([100.0, 102.30]), 1, "entry") is False
    assert g(series([100.0, 110.0]), 1, "entry") is False
    # ordinary fills pass
    assert g(series([100.0, 101.0]), 1, "entry") is True
    # exits are NEVER blocked by this rule
    assert g(series([100.0, 110.0]), 1, "exit") is True
    # unknowable cases pass rather than become a second unstated rule
    assert g(series([50.0]), 0, "entry") is True
    s_bad = series([100.0, 101.0])
    s_bad.close[0] = 0.0
    assert g(s_bad, 1, "entry") is True

    # the threshold in this file is the one the docstring freezes: no drift
    assert PREM_SKIP == 2.29, PREM_SKIP
    assert ARMS[0][1] is None, "the control carries an override"
    assert ARMS[1][1] == PREM_SKIP, "variant threshold drifted from the freeze"
    assert premium_guard(PREM_SKIP)(series([100.0, 103.0]), 1, "entry") is False

    # subset_gap: zero gap on identical lists; sign and magnitude on shifted
    import random as _rnd
    _rnd.seed(3)
    xs = [_rnd.gauss(1.0, 16) for _ in range(300)]
    ta = [{"ret": x, "clu": "micro"} for x in xs[:150]]
    tb = [{"ret": x, "clu": "micro"} for x in xs[150:]]
    d, _, t = subset_gap(tb, ta, lambda x: x["clu"] == "micro")
    assert abs(t) < 2, (d, t)          # noise must not resolve, whatever d is
    tc = [{"ret": x + 4, "clu": "micro"} for x in xs[:150]]
    d, _, t = subset_gap(tc, ta, lambda x: x["clu"] == "micro")
    assert t > 2 and d > 3, (d, t)

    print("premium_skip_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
