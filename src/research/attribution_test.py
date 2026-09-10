#!/usr/bin/env python3
"""What actually MOVED a closed trade -- selection, the market, or the exit grid?

Every attribution question this project has asked was a PREDICTOR question:
"does feature X forecast return?" (a tercile spread with an error bar). Four
channels have been answered that way and the answers are on file:

  breakout / score   rank depth  -1.12%/cohort step  t=-3.95   RESOLVED  (L69 tbl)
  fundamentals       4 features  |t| <= 0.89, n=1,049          null      (CLAUDE.md)
  sentiment          category    +1.24%  t=+1.71,  n=1,792     null      (L66/L68)
  chart pattern      4 families  |t| <= 0.51,  4 pre-reg tests null      (L74-L77)
  execution          fill premium -3.71% t=-3.18, n=1,060      RESOLVED  (L78 H2)
  regime             breadth      +1.51% t=+1.33, n=1,060      null      (L78 H1)

Those lines are CLOSED. Re-running them is forbidden -- L66 says so in terms,
and the pattern review closed at L77. Nothing here re-opens one.

What has NEVER been asked is the DECOMPOSITION: of the return dispersion in the
trades this book actually closed, how much was the stock, how much was the
market carrying it, and how much was the -10/+20/10d grid deciding where the
trade stopped? A predictor test cannot answer that -- it conditions on entry
and says nothing about where the realised number came from.

TWO pre-named hypotheses, mechanisms and signs fixed before any run:

  H19a (it was mostly the market): per-trade return rises with the EQUAL-WEIGHT
      corpus return over that trade's own holding window. A long-only micro/
      small book holding ~3.1 names for 10 sessions has nowhere to hide from
      the market. Expect beta > 0.
      The honest sample is TIME EXITS ONLY. Stop and target returns are pinned
      to -10%/+20% by construction, so including them measures the grid, not
      co-movement, and would ATTENUATE the very thing being estimated. Both are
      reported; the time-exit row is the one that decides.
      Written down now: if concurrent market moves explain more than 25% of the
      variance of time-exit returns, the correct description of this book's
      results is "market-driven with a selection residual", and every per-trade
      edge quoted anywhere in this repo is a residual on top of that.

  H19b (the grid decides, not the pick): the between-exit-class share of total
      return variance is LARGE. Mechanism: three of four trades leave through a
      hard boundary (-10% or +20%), so upstream selection cannot set the
      magnitude of those returns -- only the PROBABILITY of which boundary is
      reached. Expect between-class share > 50%.
      If it holds, "which exit fired" is the return, and a per-trade mean is a
      statement about the exit MIX as much as about stock picking.

  Reported alongside, descriptive only, no hypothesis: the friction share
      (costs + modelled impact) of gross return, since both are recorded per
      trade and neither has ever been shown as a share of the result.

SAMPLE. Offset 0 is the live book (n~193) and resolves almost nothing on its
own -- per-trade sd ~16%. So the L78 power harvest is reused unchanged: offsets
0..5, six DISJOINT rank cohorts, same rules, same costs, same trigger. Rank
depth has its own known slope, so every pooled statistic uses returns DEMEANED
WITHIN COHORT. Offset 0 is also shown raw, for continuity with the live book.

DECISION RULES, fixed now:
  - A statistic speaks only at |t| > 2 WITH its predicted sign. Otherwise it is
    reported as inside the noise, in those words.
  - Exactly these two hypotheses. Any other cut that looks resolved in the
    output is exploratory and carries no decision.
  - THIS FILE ADOPTS NOTHING. It is a decomposition, not a rule search: there
    is no tradeable action in "the market carried it", and a variance share is
    not a signal. A resolved H19a earns one thing only -- a sentence in
    lessons.md about how every other per-trade number in this repo should be
    read. No weight, no gate, no threshold, now or later.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import statistics
import sys
from collections import defaultdict

import entry
import features
import remeasure
import selection
import simulate

BATCH = "20260910-h19-attrib"

# Read, never copied (the impact_test.py lesson: a copied hold said 15 for
# three months after the live value moved to 10).
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)

COHORTS = 6            # offsets 0..5, disjoint, the rank_test mechanism
VAR_SHARE_BAR = 25.0   # H19a, frozen above
BETWEEN_BAR = 50.0     # H19b, frozen above


# ------------------------------------------------------- the market, per day

def ew_index(corpus, days):
    """Equal-weight corpus index level per session -> {day: level}.

    The mean of every symbol's own close-to-close return that day, cumulated.
    Point-in-time by construction: a symbol contributes only on days it has a
    bar AND a prior bar, so listings and delistings enter and leave without
    ever back-filling a price. No index download -- the corpus IS the universe
    this book chooses from, which is the comparison that matters. NIFTY would
    answer a different question (large caps this book never buys).
    """
    num, den = defaultdict(float), defaultdict(int)
    for s in corpus.values():
        for i in range(1, len(s.days)):
            p = s.close[i - 1]
            if p:
                num[s.days[i]] += s.close[i] / p - 1
                den[s.days[i]] += 1
    lvl, x = {}, 100.0
    for d in days:
        if den.get(d):
            x *= 1 + num[d] / den[d]
        lvl[d] = x
    return lvl


def join(trades, cohort, lvl):
    """Attach the concurrent market move to closed trades -> (rows, dropped).

    A missing key is NOT a null result (bucket_size_test ran four empty arms
    and called it 'inside the noise' because the record keys had been guessed
    wrong). Loud failure, always.
    """
    rows, dropped = [], 0
    for t in trades:
        ed, xd = t.get("entry_day"), t.get("day")
        if ed is None or xd is None:
            raise SystemExit(
                f"cohort {cohort}: trade has no entry_day/day -- the record "
                f"keys moved: {sorted(t)}")
        a, b = lvl.get(ed), lvl.get(xd)
        if a is None or b is None or not a:
            dropped += 1
            continue
        rows.append({"cohort": cohort, "ret": t["ret"], "why": t["why"],
                     "clu": t["clu"], "sym": t["sym"], "day": xd,
                     "held": t.get("held"),
                     "cost": t.get("cost_pct", 0.0), "imp": t.get("imp", 0.0),
                     "mkt": (b / a - 1) * 100})
    return rows, dropped


def demean_within_cohort(rows):
    """Remove each cohort's mean return, so the rank-depth slope (-1.12% per
    step) cannot reappear dressed up as a market effect."""
    by = defaultdict(list)
    for r in rows:
        by[r["cohort"]].append(r["ret"])
    means = {c: statistics.fmean(v) for c, v in by.items()}
    out = []
    for r in rows:
        q = dict(r)
        q["ret0"] = r["ret"] - means[r["cohort"]]
        out.append(q)
    return out


# --------------------------------------------------------------- statistics

def mean_se(vals):
    if len(vals) < 2:
        return float("nan"), float("nan"), len(vals)
    return (statistics.fmean(vals), statistics.stdev(vals) / len(vals) ** 0.5,
            len(vals))


def var_share(rows, key="why"):
    """Between-group share of total variance, as a percentage (H19b).

    The one-way ANOVA decomposition: total = between + within, where between
    is what knowing the group alone would tell you.
    """
    if len(rows) < 3:
        return float("nan"), {}, float("nan")
    xs = [r["ret"] for r in rows]
    grand = statistics.fmean(xs)
    tot = sum((x - grand) ** 2 for x in xs)
    by = defaultdict(list)
    for r in rows:
        by[r[key]].append(r["ret"])
    between = sum(len(v) * (statistics.fmean(v) - grand) ** 2
                  for v in by.values())
    stats = {g: (statistics.fmean(v),
                 statistics.stdev(v) if len(v) > 1 else 0.0, len(v))
             for g, v in by.items()}
    return (between / tot * 100 if tot else float("nan"), stats,
            (tot / len(xs)) ** 0.5)


def r2(xs, ys):
    """Fraction of variance in ys explained by a straight line in xs, percent."""
    b, se, t = remeasure.slope(xs, ys)
    my = statistics.fmean(ys)
    a = my - b * statistics.fmean(xs)
    tot = sum((y - my) ** 2 for y in ys)
    res = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    return b, se, t, (1 - res / tot) * 100 if tot else float("nan")


def verdict(t):
    return "RESOLVED" if abs(t) > 2 else "inside the noise"


def block(day):
    y = int(str(day)[:4])
    if y <= 2021:
        return "2019-2021"
    return "2022-2023" if y <= 2023 else "2024-2026"


# ------------------------------------------------------------------- report

def report_market(rows, label, retkey):
    """H19a on one set of rows. The time-exit row is the one that decides."""
    print(f"\n  {label}  (n={len(rows)})")
    if len(rows) < 30:
        print("    too few rows to speak; reported for continuity only")
    for why in ("ALL", "time", "stop", "target"):
        sub = rows if why == "ALL" else [r for r in rows if r["why"] == why]
        if len(sub) < 3:
            continue
        b, se, t, rr = r2([r["mkt"] for r in sub], [r[retkey] for r in sub])
        note = "  <- the honest read (untruncated)" if why == "time" else ""
        flag = ""
        if why == "time":
            flag = ("  ABOVE the 25% bar" if rr > VAR_SHARE_BAR
                    else "  below the 25% bar")
        print(f"    {why:<7} n={len(sub):<5} beta {b:+.3f} +/-{se:.3f} "
              f"t={t:+6.2f}  R2 {rr:5.1f}%  {verdict(t)}{flag}{note}")


def report_exits(rows, label):
    """H19b on one set of rows."""
    share, stats, sd = var_share(rows)
    print(f"\n  {label}  (n={len(rows)})  return sd {sd:.2f}%")
    for g in sorted(stats, key=lambda g: -stats[g][2]):
        m, s, n = stats[g]
        print(f"    {g:<12} n={n:<5} mean {m:+7.2f}%  sd {s:5.2f}%  "
              f"{n / len(rows) * 100:4.1f}% of trades")
    bar = ("ABOVE the 50% bar -- the grid decides"
           if share > BETWEEN_BAR else "below the 50% bar")
    print(f"    between-exit-class share of variance: {share:.1f}%   {bar}")


def report_friction(rows, label):
    print(f"\n  {label}  (n={len(rows)})")
    cost = [r["cost"] for r in rows]
    imp = [r["imp"] for r in rows]
    tot = [r["cost"] + r["imp"] for r in rows]
    net = statistics.fmean([r["ret"] for r in rows])
    mc, sc, _ = mean_se(cost)
    mi, si, _ = mean_se(imp)
    mt, st, _ = mean_se(tot)
    print(f"    costs   {mc:.3f}% +/-{sc:.3f}   impact {mi:.3f}% +/-{si:.3f}"
          f"   together {mt:.3f}% +/-{st:.3f}")
    gross = net + mt
    # A share of a NEGATIVE gross is not a share of anything -- the harvest's
    # deep cohorts lose money before costs, and printing "-280% of gross" there
    # reads as a friction figure when it is a sign artefact.
    share = (f"friction takes {mt / gross * 100:.0f}% of gross"
             if gross > 0 else
             f"gross is NEGATIVE, so no share is meaningful; friction adds "
             f"{mt:.3f}% to a loss that was already there")
    print(f"    net per trade {net:+.3f}%  ->  gross {gross:+.3f}%  ({share})")


# --------------------------------------------------------------------- main

_C = _D = None


def _one(off):
    entry._CACHE.clear()
    r = simulate.run(_C, _D, offset=off, **BASE)
    return off, r


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    lvl = ew_index(_C, _D)
    print(f"ATTRIBUTION  batch {BATCH}  {len(_C)} symbols x {len(_D)} sessions")
    print(f"live rules {BASE['stop_pct']:g}/{BASE['target_pct']:g}/"
          f"{BASE['hold']}d trig={BASE['trigger']}  cohorts 0..{COHORTS - 1}")
    print(f"equal-weight corpus index {lvl[_D[0]]:.1f} -> {lvl[_D[-1]]:.1f} "
          f"over {len(_D)} sessions\n")

    with mp.get_context("fork").Pool(min(COHORTS, mp.cpu_count())) as p:
        res = sorted(p.map(_one, range(COHORTS)))

    all_rows, live = [], []
    for off, r in res:
        rows, dropped = join(r["trades"], off, lvl)
        tag = "live book" if off == 0 else f"harvest cohort {off}"
        print(f"  cohort {off} ({tag}): CAGR {r['cagr']:+6.2f}%  "
              f"n={len(r['trades'])}  joined={len(rows)}  dropped={dropped}")
        if off == 0:
            live = rows
        all_rows += rows
    if not all_rows:
        raise SystemExit("no joined rows -- the harvest produced nothing")
    pooled = demean_within_cohort(all_rows)

    print("\n" + "=" * 72)
    print("H19a  WAS IT THE MARKET?   [expect beta POSITIVE]")
    print("=" * 72)
    report_market(live, "offset 0 -- the live book, raw returns", "ret")
    report_market(pooled, f"harvest 0..{COHORTS - 1} -- demeaned within cohort",
                  "ret0")
    for blk in ("2019-2021", "2022-2023", "2024-2026"):
        sub = [r for r in pooled if block(r["day"]) == blk]
        if sub:
            report_market(sub, f"harvest, block {blk}", "ret0")

    print("\n" + "=" * 72)
    print("H19b  OR WAS IT THE EXIT GRID?   [expect between-class share > 50%]")
    print("=" * 72)
    report_exits(live, "offset 0 -- the live book")
    report_exits(all_rows, f"harvest 0..{COHORTS - 1} -- raw returns")
    for clu in ("micro", "small"):
        sub = [r for r in all_rows if r["clu"] == clu]
        if sub:
            report_exits(sub, f"harvest, cluster {clu}")

    print("\n" + "=" * 72)
    print("FRICTION   (descriptive, no hypothesis)")
    print("=" * 72)
    report_friction(live, "offset 0 -- the live book")
    report_friction(all_rows, f"harvest 0..{COHORTS - 1}")

    print("\nADOPTS NOTHING -- decomposition, not a rule search (see docstring).")


# ---------------------------------------------------------------- selftest

def _selftest():
    from datetime import date as _date

    class S:
        def __init__(self, days, closes):
            self.days, self.close = days, closes

    d = [_date(2020, 1, i) for i in range(1, 6)]
    # Two symbols, both +10% on day 2 and flat after -> index +10% once.
    c = {"A": S(d, [100, 110, 110, 110, 110]),
         "B": S(d, [50, 55, 55, 55, 55])}
    lvl = ew_index(c, d)
    assert abs(lvl[d[0]] - 100.0) < 1e-9, lvl
    assert abs(lvl[d[1]] - 110.0) < 1e-9, lvl
    assert abs(lvl[d[-1]] - 110.0) < 1e-9, lvl

    # join reads entry_day/day and computes the span return off the index.
    tr = [{"ret": 5.0, "why": "time", "clu": "micro", "sym": "A",
           "entry_day": d[0], "day": d[2], "cost_pct": 0.4, "imp": 0.2,
           "held": 2}]
    rows, dropped = join(tr, 0, lvl)
    assert dropped == 0 and len(rows) == 1, (rows, dropped)
    assert abs(rows[0]["mkt"] - 10.0) < 1e-9, rows

    # A moved record key must CRASH, not silently yield an empty null result.
    try:
        join([{"ret": 1.0, "why": "time", "clu": "micro", "sym": "A"}], 0, lvl)
    except SystemExit:
        pass
    else:
        raise AssertionError("join accepted a trade with no entry_day")

    # var_share: identical groups -> 0% between; separated groups -> most of it.
    same = [{"ret": r, "why": w} for w, rs in
            (("stop", [-1, 1]), ("time", [-1, 1])) for r in rs]
    s0, _, _ = var_share(same)
    assert abs(s0) < 1e-9, s0
    split = [{"ret": r, "why": w} for w, rs in
             (("stop", [-10, -10]), ("target", [20, 20])) for r in rs]
    s1, _, _ = var_share(split)
    assert abs(s1 - 100.0) < 1e-9, s1

    # demeaning removes the cohort mean and leaves the spread untouched.
    rr = demean_within_cohort([{"cohort": 0, "ret": 10.0},
                               {"cohort": 0, "ret": 20.0},
                               {"cohort": 1, "ret": 0.0},
                               {"cohort": 1, "ret": 10.0}])
    assert [round(x["ret0"], 9) for x in rr] == [-5.0, 5.0, -5.0, 5.0], rr

    # r2 is exact on a perfect line and ~0 on a flat one.
    _, _, _, rr2 = r2([1, 2, 3, 4], [2, 4, 6, 8])
    assert abs(rr2 - 100.0) < 1e-6, rr2
    _, _, _, rr3 = r2([1, 2, 3, 4], [5, 5, 5, 5.0001])
    assert rr3 < 101.0, rr3
    print("attribution_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
