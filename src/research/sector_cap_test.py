#!/usr/bin/env python3
"""H18 — a sector concentration cap, the one sector rule L85 leaves admissible.

L85 closed sector as a SCORE INPUT: `data/sectors.json` is a 2026 scrape, it
covers 99.8% of today's tradeable universe and 64.8% of 2021's, and the names
missing from the old universe are 66% delisted and 32% GROWN OUT of the
micro/small band. Requiring a sector label historically drops the failures and
the biggest winners at once, so anything that DROPS or REQUIRES a mapped name is
measuring "still small in 2026" and not sectors.

A CAP is the exception, and it is the reason this test exists. A cap never has
to drop an unmapped name -- it just never counts one. Unmapped is UNCONSTRAINED
here, which makes the historical rule strictly WEAKER than the live rule would
be: 55% of the 195 trades carry a sector, so roughly half the book is invisible
to the cap in this test and would not be live. The bias has one direction and it
is stated: this UNDERSTATES the effect. A null here is therefore weak evidence
and an effect here is strong evidence, which is the right way round.

WHY THIS IS A LEGAL EXPERIMENT (CLAUDE.md's four kinds): a new rule SHAPE. The
bucket has no sector rule of any kind; `simulate.run`'s buy loop says so in a
comment ("There is no sector rule in this system"). This is not a new value for
an existing dial.

THE BINDING GATE WAS RUN FIRST, which is the lesson H17 paid for. H17 wrote a
whole test module for a diversification rule that turned out to remove ONE trade
in seven years, because the book holds 3.1 names on a five-session refresh and
correlated names rarely compete for a seat (L86). Measured before writing this:

    max 1 per broad sector   blocks 18 of 195 entries  (9.2%)
    max 2 per broad sector   blocks  1 of 195 entries  (0.5%)

and the book holds two names from one sector on 12.6% of sessions, three on
1.0%. So `max 2` is predicted INERT here, in writing, before the run -- it is
kept as the dose-response arm and its job is to come back empty. `max 1` clears
the gate H17 failed.

TWO SHAPES, because a cap can refuse a name in two different ways and they are
different bets:

  substitute  the blocked seat is filled by the next name down the ranking.
              Rank depth costs -1.12% per step (CLAUDE.md), so substitution is
              NOT free -- this arm pays that and buys diversification with it.
  hold cash   the blocked seat stays empty. This is what selection.build
              already does when its best names have not triggered ("hold cash
              instead"), so it is the shape consistent with the rest of the book.

THE CONTROL is no sector rule: what the live book runs.

THE ENDPOINT, declared before the run. A cap is a RISK rule and is judged the
way L58 and L64 judged theirs, NOT on CAGR:

  1. MOVED ENOUGH. An arm must change at least MIN_CHANGED trades, counted as
     the SYMMETRIC DIFFERENCE of trade sets against the control -- not |n
     difference|, which is what H17's bar used and which cannot see a
     substitution that swaps one trade for another and leaves the count alone.
  2. To ADOPT: max drawdown must improve, THE WORST REGIME BLOCK MUST IMPROVE
     TOO, per-trade return must not fall by more than one standard error, and
     the two thresholds must not contradict each other. `max 2` is predicted
     inert, so a LARGE effect from `max 2` would mean the cap is not doing what
     this docstring claims and is disqualifying, not confirming.

     THE WORST-BLOCK CLAUSE WAS ADDED AFTER THE FIRST RUN, as a TIGHTENING, and
     the first run's verdict is recorded rather than overwritten (L89): the bar
     as first written returned ADOPT for both `max 1` arms on a +1.42 CAGR gain,
     a 0.7-point drawdown gain and t = +0.26. It should not have. Max drawdown
     on ONE path is a single-extremum statistic that moves on noise alone, and
     asking it to "improve" with no floor and no error bar is not a risk test.
     The worst block is the honest one, and it is brutal here: 2022-H1 reads
     -166.4% in EVERY arm, and 0 of the 19 trades the cap removed fall inside
     it. The rule does nothing in the worst stretch the book has had.
  3. Anything else is reported as `inside the noise` in those words.

Nothing is adopted by this file and no baseline is re-recorded; that is the
operator's separate step.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import multiprocessing as mp
from collections import defaultdict

import analysis, entry, features, remeasure, selection, simulate
from paths import ROOT

BATCH = "20260829-sectorcap"

# Read the live constants, never copy them (L60).
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)

# The symmetric difference an arm must reach before its result describes the
# RULE rather than one price path. H17's bar passed a rule that changed a single
# trade in seven years because it compared trade COUNTS (L86).
MIN_CHANGED = 20

SECTORS = json.loads((ROOT / "data" / "sectors.json").read_text())

# Pre-registered before running. `max 2` is predicted inert; it is here for the
# dose-response and is expected to come back empty.
VARIANTS = [
    ("no cap (control, = live)", None, False),
    ("max 1/sector, substitute", 1, False),
    ("max 1/sector, hold cash",  1, True),
    ("max 2/sector, substitute", 2, False),
    ("max 2/sector, hold cash",  2, True),
]

_C = _D = None


def _key(r):
    return {(t["sym"], t["entry_day"]) for t in r["trades"]}


def _one(item):
    label, cap, cash = item
    entry._CACHE.clear()
    r = simulate.run(_C, _D, sector_cap=cap, sector_cash=cash,
                     sector_map=(SECTORS if cap else None), **BASE)
    t = r["trades"]
    by = defaultdict(float)
    for x in t:
        by[f"{x['day'].year}-H{1 if x['day'].month <= 6 else 2}"] += x["ret"]
    return {"label": label, "cap": cap, "cash": cash, "cagr": r["cagr"],
            "dd": r["maxdd"], "n": len(t),
            "win": sum(1 for x in t if x["ret"] > 0) / max(len(t), 1) * 100,
            "worst": min(by.values()) if by else float("nan"),
            "occ": r.get("occupancy", 0.0),
            "keys": _key(r), "cluster": analysis.per_cluster(t), "_r": r}


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    covered = sum(1 for s in _C if s in SECTORS)
    print(f"H18 sector cap — {len(VARIANTS)} arms x {len(_D)} sessions, at the "
          f"live exit rules ({BASE['stop_pct']:g}/{BASE['target_pct']:g}/"
          f"{BASE['hold']}d), batch {BATCH}")
    print(f"  sector map covers {covered} of {len(_C)} corpus symbols; unmapped "
          f"is UNCONSTRAINED, so this understates the live rule (L85)\n")

    with mp.get_context("fork").Pool(min(len(VARIANTS), mp.cpu_count())) as p:
        res = p.map(_one, VARIANTS)

    ctl = res[0]
    print("  DID IT MOVE?  (symmetric difference of trade sets vs the control)")
    for x in res[1:]:
        d = len(x["keys"] ^ ctl["keys"])
        x["moved"] = d
        print(f"    {x['label']:<26}{d:>4} trades differ "
              f"({len(ctl['keys'] - x['keys']):>3} dropped, "
              f"{len(x['keys'] - ctl['keys']):>3} added)")

    print(f"\n  {'arm':<26}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}{'occ':>6}"
          f"{'per-trade':>12}{'std err':>9}{'worst blk':>11}")
    for x in res:
        m, se, _n = remeasure.edge(x["_r"])
        print(f"  {x['label']:<26}{x['cagr']:>+8.2f}%{x['dd']:>7.1f}%"
              f"{x['win']:>5.0f}%{x['n']:>6}{x['occ']:>6.2f}{m:>+11.2f}%"
              f"{se:>8.2f}%{x['worst']:>+10.1f}%")

    print(f"\n  against NO CAP ({ctl['cagr']:+.2f}% CAGR, {ctl['dd']:.1f}% DD, "
          f"n={ctl['n']}) — what the live book runs:")
    for x in res[1:]:
        d, se, t = remeasure.gap(x["_r"], ctl["_r"])
        print(f"    {x['label']:<26}{x['dd'] - ctl['dd']:>+6.1f} DD pts"
              f"{x['cagr'] - ctl['cagr']:>+8.2f} CAGR pts"
              f"{d:>+8.2f}%/trade +/-{se:>5.2f}  t{t:>+6.2f}  "
              f"{'RESOLVED' if abs(t) > 2 else 'inside the noise'}")

    print("\n  per cluster (micro / small), average per trade:")
    for x in res:
        c = x["cluster"]
        print(f"    {x['label']:<26}"
              f"micro {c.get('micro', {}).get('avg', 0):+.2f}% "
              f"(n={c.get('micro', {}).get('n', 0)})   "
              f"small {c.get('small', {}).get('avg', 0):+.2f}% "
              f"(n={c.get('small', {}).get('n', 0)})")

    print(f"\n  the pre-registered bar (moves >= {MIN_CHANGED} trades AND "
          f"drawdown improves AND the WORST BLOCK improves AND per-trade holds "
          f"within 1 se):")
    for x in res[1:]:
        d, se, _t = remeasure.gap(x["_r"], ctl["_r"])
        checks = (("moves too few trades", x["moved"] >= MIN_CHANGED),
                  ("drawdown did not improve", x["dd"] < ctl["dd"]),
                  # A risk rule that leaves the worst stretch untouched has not
                  # been shown to reduce risk, whatever the average did.
                  ("worst block unchanged", x["worst"] > ctl["worst"] + 0.05),
                  ("per-trade fell by more than 1 se", d >= -se))
        ok = all(c[1] for c in checks)
        print(f"    {x['label']:<26}"
              f"{'ADOPT' if ok else 'no — ' + ', '.join(w for w, c in checks if not c)}")

    # Written down BEFORE the run: max 2 blocks 1 of 195 entries, so it must
    # come back inert. If it does not, the cap is not doing what it claims.
    big2 = [x for x in res[1:] if x["cap"] == 2 and x["moved"] >= MIN_CHANGED]
    if big2:
        print("\n  WARNING: 'max 2' was predicted inert (it blocks 1 of 195 "
              "entries) and moved more than that. The cap is not doing what "
              "this test says it does; do not read the 'max 1' arms until that "
              "is explained.")

    kept = sum(1 for x in res if simulate.keep(
        f"sector cap {x['label']}", x["_r"],
        {**BASE, "sector_cap": x["cap"], "sector_cash": x["cash"]},
        batch=BATCH, note="H18 sector concentration cap, unmapped unconstrained"))
    print(f"\n  {kept} of {len(res)} cleared the promotion bar")
    print(f"  {analysis.trades_needed(analysis.BACKTEST_EDGE)} trades are needed "
          f"to resolve a {analysis.BACKTEST_EDGE:.1f}%/trade edge. "
          f"{len(VARIANTS) - 1} arms were run against one control, so a single "
          f"|t| near 2 is worth about a quarter of what it looks like.")


def _selftest():
    # The cap has to REACH the simulation, or this measures one book five times
    # -- the failure clusters.py:88 records and the one L58 is made of.
    import inspect
    src = inspect.getsource(simulate.run)
    assert "sector_cap" in src and "held_sectors" in src, \
        "simulate.run does not carry the sector cap"

    # An unmapped name must be UNCONSTRAINED, never dropped: that is the whole
    # of what makes this admissible on a 2026 scrape (L85). Asserted on a
    # synthetic map so it is about the rule, not about today's sectors.
    from datetime import date, timedelta
    days = [date(2024, 1, 1) + timedelta(days=k) for k in range(400)]
    corpus = {}
    for j in range(8):
        s = features.Series(f"S{j}", list(days))
        px = 100.0
        for k in range(len(days)):
            px *= 1.002 if (k + j) % 3 else 0.999
            s.close.append(px); s.open.append(px)
            s.high.append(px * 1.01); s.low.append(px * 0.99)
            s.turnover.append(1e6 * (j + 1)); s.volume.append(1000)
            s.deliv_pct.append(50.0)
        corpus[f"S{j}"] = s
    # every name in one sector: a cap of 1 must leave at most one holding
    one = {f"S{j}": "OnlySector" for j in range(8)}
    r = simulate.run(corpus, days, max_pos=5, start_idx=300, refresh=5,
                     sector_cap=1, sector_map=one, hold=5)
    for t in r["trades"]:
        overlap = [u for u in r["trades"]
                   if u is not t and u["entry_day"] <= t["entry_day"] < u["day"]]
        assert not overlap, f"a cap of 1 held two names from one sector: {t}, {overlap}"
    # with NO map entry, the same cap must change nothing at all
    a = simulate.run(corpus, days, max_pos=5, start_idx=300, refresh=5,
                     sector_cap=1, sector_map={}, hold=5)
    b = simulate.run(corpus, days, max_pos=5, start_idx=300, refresh=5, hold=5)
    assert _key(a) == _key(b), "an unmapped name was constrained by the cap"

    assert VARIANTS[0][1] is None, "the control is not the no-cap arm"
    assert MIN_CHANGED >= 20, "the bar was loosened; criteria may only tighten"
    print("sector_cap_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        main()
