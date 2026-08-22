#!/usr/bin/env python3
"""What did the ETFs in the historical universe pay for?

HYPOTHESIS. `universe.non_equity_symbols()` built its denylist from ONE
snapshot, so a fund had to be trading on the newest date to be eligible for it.
Every fund that had already delisted stayed in the universe for every
historical date, inside the micro and small clusters this book trades. If that
is true, the recorded baseline was measured on a universe containing
instruments the strategy was never meant to hold, and removing them moves it.

CONTROL. The universe as it stood -- funds included. This is not a knob being
re-chosen: it is category 4 in CLAUDE.md, removing something that was never
evidence, and the circuit-lock guard (L58) is the precedent for how to read it.
Error bars decide which RULE to prefer. They do not decide whether a silver ETF
is a small-cap company; it is not one at any t-statistic, and the arm with the
better mean is not the arm that gets adopted here.

ENDPOINT. Per-trade return of the trades that disappear, with its standard
error, plus CAGR / maxDD / n before and after, per cluster and per regime
block. What would change the DECISION: nothing. What would change the STORY:
if the removed trades were a random sample of the book -- mean indistinguishable
from the rest, spread over the whole period -- then the funds were noise the
baseline could absorb, and the correction is bookkeeping. If instead they are
concentrated and profitable, part of the recorded edge was a commodity rally
reached through instruments this strategy does not trade.

    python3 src/research/universe_test.py
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import statistics
import sys

import features
import selection
import simulate
import universe

# -nonequity3 = all THREE classifier tiers. The plain "20260820-nonequity" rows
# in simulations.jsonl are a superseded two-tier build; append-only means they
# stay, so the tag has to carry the difference.
BATCH = "20260820-nonequity3"

# Read the live constants, never copy them. impact_test.py carried a copy that
# said 15 for three months after the live hold moved to 10.
LIVE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            take_per_cluster=dict(selection.TAKE_PER_CLUSTER),
            trigger=selection.TRIGGER)


def edge(trades):
    """-> (mean per-trade return, std err, n)."""
    r = [t["ret"] for t in trades]
    if len(r) < 2:
        return float("nan"), float("nan"), len(r)
    return statistics.fmean(r), statistics.stdev(r) / len(r) ** 0.5, len(r)


def gap(a, b):
    """Welch on two trade samples -> (difference, std err, t)."""
    ma, sa, _ = edge(a)
    mb, sb, _ = edge(b)
    se = (sa ** 2 + sb ** 2) ** 0.5
    return ma - mb, se, ((ma - mb) / se if se else float("nan"))


def verdict(t):
    return "RESOLVED" if abs(t) > 2 else "inside the noise"


def blocks(days, n=4):
    """-> [(label, first, last)] over n equal regime blocks."""
    out, step = [], len(days) // n
    for i in range(n):
        lo = i * step
        hi = (i + 1) * step - 1 if i < n - 1 else len(days) - 1
        out.append((f"{days[lo]}..{days[hi]}", days[lo], days[hi]))
    return out


def run(corpus, days):
    return simulate.run(corpus, days, **LIVE)


def main():
    # The two universes. `with_funds` restores the pre-fix behaviour by taking
    # the historical artifact back out -- the snapshot-derived half stays,
    # because that half was never in dispute.
    hist = set(universe.historical_non_equity())
    print(f"POINT-IN-TIME NON-EQUITY GAP   batch {BATCH}")
    print(f"{len(hist)} funds delisted before any snapshot held a company master\n")

    features._CORPUS = None
    universe._NON_EQUITY = None
    after = features.load_corpus()
    days = sorted({d for s in after.values() for d in s.days})
    r_after = run(after, days)

    features._CORPUS = None
    universe._NON_EQUITY = universe._seen_non_equity()      # artifact removed
    before = features.load_corpus()
    r_before = run(before, days)
    universe._NON_EQUITY = None

    extra = sorted(set(before) - set(after))
    print(f"corpus: {len(before)} symbols before, {len(after)} after "
          f"({len(extra)} funds removed)\n")

    print(f"{'universe':<22} {'CAGR':>8} {'maxDD':>8} {'n':>5} {'per trade':>18}")
    for name, r in (("control: funds in", r_before), ("funds removed", r_after)):
        m, se, n = edge(r["trades"])
        print(f"  {name:<20} {r['cagr']:>7.2f}% {r['maxdd']:>7.1f}% {n:>5} "
              f"{m:>+9.2f}% +/- {se:.2f}%")

    # The trades that disappear, and whether they look like the rest of the book.
    gone = [t for t in r_before["trades"] if t["sym"] in extra]
    rest = [t for t in r_before["trades"] if t["sym"] not in extra]
    mg, sg, ng = edge(gone)
    mr, sr, nr = edge(rest)
    d, se, t = gap(gone, rest)
    print(f"\nTHE REMOVED TRADES, inside the control book")
    print(f"  fund trades        n={ng:>4}  {mg:>+7.2f}% +/- {sg:.2f}%")
    print(f"  everything else    n={nr:>4}  {mr:>+7.2f}% +/- {sr:.2f}%")
    print(f"  difference         {d:>+7.2f}% +/- {se:.2f}%  t={t:+.2f}  {verdict(t)}")
    print(f"  share of the control book: {ng / max(ng + nr, 1) * 100:.1f}% of trades")

    print(f"\nPER CLUSTER (control book)")
    for clu in sorted({t["clu"] for t in r_before["trades"]}):
        g = [x for x in gone if x["clu"] == clu]
        e = [x for x in rest if x["clu"] == clu]
        mg2, sg2, ng2 = edge(g)
        me2, se2, ne2 = edge(e)
        print(f"  {clu:<8} funds n={ng2:>3} {mg2:>+7.2f}% +/- {sg2:5.2f}%   "
              f"rest n={ne2:>3} {me2:>+7.2f}% +/- {se2:5.2f}%")

    print(f"\nPER REGIME BLOCK -- where the fund trades actually sat")
    for label, lo, hi in blocks(days):
        g = [x for x in gone if lo <= x["day"] <= hi]
        a = [x for x in r_after["trades"] if lo <= x["day"] <= hi]
        b = [x for x in r_before["trades"] if lo <= x["day"] <= hi]
        mg3, sg3, ng3 = edge(g)
        share = ng3 / max(len(b), 1) * 100
        print(f"  {label}  control n={len(b):>3}  fixed n={len(a):>3}  "
              f"funds n={ng3:>3} ({share:4.1f}%) "
              + (f"{mg3:+.2f}% +/- {sg3:.2f}%" if ng3 > 1 else ""))

    print(f"\nWorst removed trade {min((x['ret'] for x in gone), default=0):+.2f}%, "
          f"best {max((x['ret'] for x in gone), default=0):+.2f}%")
    print("\nThe fix is not adopted on these numbers. An ETF is not a company at "
          "any t-statistic;\nwhat the numbers say is how much of the recorded "
          "baseline was never this strategy's.")

    if "--store" in sys.argv:
        simulate.store("universe: funds in (control)", r_before, batch=BATCH)
        simulate.store("universe: funds removed (live)", r_after, batch=BATCH)
        print(f"\nstored both arms under batch {BATCH}")


def _selftest():
    assert verdict(2.5) == "RESOLVED" and verdict(1.9) == "inside the noise"
    d, se, t = gap([{"ret": 10.0}, {"ret": 10.0}], [{"ret": 0.0}, {"ret": 0.0}])
    assert d == 10.0 and se == 0.0
    m, se2, n = edge([{"ret": 1.0}, {"ret": 3.0}])
    assert m == 2.0 and n == 2 and abs(se2 - 1.0) < 1e-9
    b = blocks([f"d{i}" for i in range(10)], 4)
    assert len(b) == 4 and b[0][1] == "d0" and b[-1][2] == "d9", b
    # The control must be reachable: the artifact is what this test removes, so
    # an empty one would silently compare the live book against itself.
    assert universe.historical_non_equity(), \
        "no historical artifact -- the control arm would equal the live one"
    print("universe_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
