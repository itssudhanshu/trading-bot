#!/usr/bin/env python3
"""Does a loss carry structure an ENTRY-TIME rule could have seen?

RakshaQuant (a LangGraph paper-trading project) classifies every closed trade
into "lessons" and injects them into LLM prompts. The injection is unmeasurable
by construction and is not what is tested here. The deterministic core of the
idea is: if losses cluster in conditions observable BEFORE the fill, then a
rule shape exists that this score cannot currently see. That is a legal
experiment under CLAUDE.md category 1/2 -- a new input, or a new rule shape --
and it has never been measured on this bucket:

  - Nothing in the selection path looks at the MARKET. The 200-DMA gate is
    per-stock; the score is a within-cluster percentile. And L55 measured that
    the score goes UP as markets weaken (rank-1 score 94.5 in the weakest
    quartile vs 89.3 in the strongest), so the book reaches for names whose
    percentile is high precisely when the absolute picture is worst. If
    relative scoring smuggles market weakness into entries, breadth at entry
    should predict per-trade return.

  - Every fill pays the NEXT OPEN after a trigger. Nothing measures what that
    open cost relative to the signal close. A trigger marks demand; paying a
    large overnight extension buys someone else's exit liquidity and hangs the
    -10% stop off an already-extended price.

TWO pre-named hypotheses, both directional, decided before any run:

  H1 (regime mismatch): per-trade return RISES with market breadth at entry.
      Breadth = % of corpus series with >=200 sessions that day whose close is
      above its own trailing 200-session mean, point-in-time, corpus-only --
      no index data, no new downloads, nothing survivorship-corrected beyond
      what the corpus already is.
  H2 (paying the extension): per-trade return FALLS as the fill premium rises.
      Premium = open[entry_day]/close[signal_day] - 1 on the filled symbol's
      own bars, in percent.

Exit REASON taxonomy (stop vs time vs target mix) is Stage A context only:
exits were already tested extensively (exit_test.py), so counts are printed,
never decided on.

CONTROL AND SAMPLE. There is no variant here; the control is the live book
itself (offset 0), and its trades are the primary sample -- but n~193 cannot
resolve anything (per-trade sd ~16%, so ~2.3% SE even before splitting). So a
pre-registered POWER HARVEST reuses the rank_test mechanism: offsets 0..5, six
DISJOINT rank cohorts, same rules/costs/trigger. Depth has its own known slope
(-1.12%/step, t=-3.95, batch 20260820-nonequity3) which would masquerade as a
feature effect, so every harvest statistic uses returns DEMEANED WITHIN
COHORT before any conditioning. Offset-0 rows are also shown undemeaned, for
continuity with the live book.

DECISION RULES, fixed now:
  - A statistic speaks only at |t| > 2 WITH the sign its mechanism predicts.
    Anything else is reported as inside the noise, in those words.
  - Exactly these two hypotheses. Any other cut that happens to look resolved
    in the output is exploratory and carries no decision.
  - RESOLVED or not, THIS file adopts nothing -- not a weight, not a rule, not
    a threshold. A resolved hypothesis earns one follow-up: a NEW pre-registered
    backtest of the rule SHAPE ("breadth floor" / "premium skip") at the single
    tercile boundary observed here, written down there before it runs, judged
    by simulate.keep's promotion bar. Description proposes; only that test
    decides.
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

BATCH = "20260826-lossclass"

# Read, never copied (the impact_test.py lesson: a copied hold said 15 for
# three months after the live value moved to 10).
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)

COHORTS = 6          # offsets 0..5, disjoint, the rank_test mechanism
WINDOW = 200         # breadth lookback, frozen


# --------------------------------------------------------------- breadth (H1)

def _above_ma_flags(s, window=WINDOW):
    """-> {day: close > mean(last `window` closes incl today)} per symbol.

    Strictly greater: a flat series is NOT above its own mean. Symbols with
    fewer than `window` sessions contribute nothing, which keeps the first
    history years honest instead of counting warm-up names as 'no'.
    """
    flags = {}
    run = 0.0
    for i, d in enumerate(s.days):
        c = s.close[i]
        run += c
        if i >= window:
            run -= s.close[i - window]
        if i >= window - 1:
            flags[d] = c > run / window
    return flags


def breadth_map(corpus, days):
    """-> {day: % of eligible series above their own MA} -- point-in-time."""
    per = [(s.days, _above_ma_flags(s)) for s in corpus.values()]
    out = {}
    for day in days:
        above = tot = 0
        for ds, fl in per:
            f = fl.get(day)
            if f is None:
                continue
            tot += 1
            above += 1 if f else 0
        out[day] = above / tot * 100 if tot else None
    return out


# ------------------------------------------------------------ join + demean

def join(trades, cohort, corpus, br):
    """Attach entry-time observables to closed trades -> (rows, dropped).

    A missing key is NOT a null result (bucket_size_test ran four empty arms
    and called it 'inside the noise' because the record keys had been guessed
    wrong). Loud failure, always.
    """
    rows, dropped = [], 0
    for t in trades:
        ed = t.get("entry_day")
        if ed is None:
            raise SystemExit(
                f"cohort {cohort}: trade has no entry_day -- the record keys "
                f"moved: {sorted(t)}")
        s = corpus[t["sym"]]
        ie = s.index_of(ed)
        if ie is None or ie < 1 or br.get(ed) is None:
            dropped += 1
            continue
        rows.append({"cohort": cohort, "ret": t["ret"],
                     "clu": t["clu"], "why": t["why"], "day": t["day"],
                     "sym": t["sym"], "held": t.get("held"),
                     "prem": (s.open[ie] / s.close[ie - 1] - 1) * 100,
                     "br": br[ed]})
    return rows, dropped


def demean_within_cohort(rows):
    """Remove each cohort's mean return, so the rank-depth slope (-1.12% per
    step) cannot reappear dressed up as a feature effect."""
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


def terciles(rows, key):
    xs = sorted(rows, key=lambda r: r[key])
    k = len(xs) // 3
    return xs[:k], xs[k:2 * k], xs[2 * k:]


def mean_se(vals):
    if len(vals) < 2:
        return float("nan"), float("nan"), len(vals)
    return (statistics.fmean(vals), statistics.stdev(vals) / len(vals) ** 0.5,
            len(vals))


def welch_gap(a, b):
    """Top-bin minus bottom-bin; error bars in quadrature (the remeasure.gap
    arithmetic applied to arbitrary subsets rather than whole runs)."""
    ma, sa, na = mean_se(a)
    mb, sb, nb = mean_se(b)
    se = (sa ** 2 + sb ** 2) ** 0.5
    d = ma - mb
    return d, se, (d / se if se else float("nan"))


def block(day):
    y = int(str(day)[:4])
    if y <= 2021:
        return "2019-2021"
    return "2022-2023" if y <= 2023 else "2024-2026"


def verdict(t):
    return "RESOLVED" if abs(t) > 2 else "inside the noise"


# ------------------------------------------------------------------- report

def report_h(rows, label):
    """Both hypotheses on one set of rows, with n beside everything."""
    print(f"\n  {label}  (n={len(rows)})")
    if len(rows) < 30:
        print("    too few rows to speak; reported for continuity only")
    b, se, t = remeasure.slope([r["prem"] for r in rows],
                               [r["ret0"] for r in rows])
    print(f"    H2 premium   slope {b:+.3f}%/pt +/-{se:.3f}  t={t:+.2f}  "
          f"[expect NEGATIVE]  {verdict(t)}")
    lo, mid, hi = terciles(rows, "prem")
    d, gse, gt = welch_gap([r["ret0"] for r in hi], [r["ret0"] for r in lo])
    pm = [statistics.fmean([r["prem"] for r in x]) for x in (lo, mid, hi)]
    print(f"    H2 terciles  prem {pm[0]:+.2f}/{pm[1]:+.2f}/{pm[2]:+.2f}%  "
          f"gap top-bottom {d:+.2f}% +/-{gse:.2f}  t={gt:+.2f}  {verdict(gt)}")
    b, se, t = remeasure.slope([r["br"] for r in rows],
                               [r["ret0"] for r in rows])
    print(f"    H1 breadth   slope {b:+.3f}%/pt +/-{se:.3f}  t={t:+.2f}  "
          f"[expect POSITIVE]  {verdict(t)}")
    lo, mid, hi = terciles(rows, "br")
    d, gse, gt = welch_gap([r["ret0"] for r in hi], [r["ret0"] for r in lo])
    bm = [statistics.fmean([r["br"] for r in x]) for x in (lo, mid, hi)]
    print(f"    H1 terciles  brd {bm[0]:.1f}/{bm[1]:.1f}/{bm[2]:.1f}%  "
          f"gap top-bottom {d:+.2f}% +/-{gse:.2f}  t={gt:+.2f}  {verdict(gt)}")


# --------------------------------------------------------------------- main

_C = _D = _BR = None


def _one(off):
    entry._CACHE.clear()
    r = simulate.run(_C, _D, offset=off, **BASE)
    return off, r


def main():
    global _C, _D, _BR
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    _BR = breadth_map(_C, _D)
    print(f"LOSS TAXONOMY  batch {BATCH}  {len(_C)} symbols x {len(_D)} sessions")
    print(f"live rules {BASE['stop_pct']:g}/{BASE['target_pct']:g}/"
          f"{BASE['hold']}d trig={BASE['trigger']}  cohorts 0..{COHORTS - 1}\n")

    with mp.get_context("fork").Pool(min(COHORTS, mp.cpu_count())) as p:
        res = sorted(p.map(_one, range(COHORTS)))

    all_rows = []
    for off, r in res:
        rows, dropped = join(r["trades"], off, _C, _BR)
        tag = "live book" if off == 0 else f"harvest cohort {off}"
        print(f"  cohort {off} ({tag}): CAGR {r['cagr']:+6.2f}%  "
              f"n={len(r['trades'])}  joined={len(rows)}  "
              f"dropped(no bar/breadth)={dropped}")
        if r["trades"] and not rows:
            raise SystemExit(f"cohort {off}: {len(r['trades'])} trades, 0 joined")
        all_rows += rows

    dm = demean_within_cohort(all_rows)

    # PRIMARY: the live book itself, undemeaned (its own cohort mean IS the
    # live edge; demeaning would hide it).
    zero = [dict(r) for r in all_rows if r["cohort"] == 0]
    for r in zero:
        r["ret0"] = r["ret"]
    report_h(zero, "PRIMARY -- offset 0, the live book, raw returns")

    # POWER HARVEST: six cohorts, within-cohort demeaned.
    report_h(dm, "POWER HARVEST -- cohorts 0..5, demeaned within cohort")

    print("\n  per cluster (harvest, demeaned):")
    for clu in ("micro", "small"):
        sub = [r for r in dm if r["clu"] == clu]
        report_h(sub, f"cluster {clu}")

    print("\n  per regime block (harvest, demeaned):")
    blocks = sorted({block(r["day"]) for r in dm})
    for blk in blocks:
        sub = [r for r in dm if block(r["day"]) == blk]
        report_h(sub, f"block {blk}")

    # Stage A context ONLY -- exits belong to exit_test.py; these counts decide
    # nothing and are here so the loss anatomy behind H1/H2 is visible.
    print("\n  Stage A -- exit mix, live book (context only, NOT a decision):")
    mix = defaultdict(lambda: defaultdict(int))
    why_all = defaultdict(int)
    for r in zero:
        mix[block(r["day"])][r["why"]] += 1
        why_all[r["why"]] += 1
    for blk in sorted(mix):
        line = ", ".join(f"{k} {v}" for k, v in sorted(mix[blk].items()))
        print(f"    {blk:<10}{line}")
    print(f"    {'all':<10}" + ", ".join(f"{k} {v}"
                                         for k, v in sorted(why_all.items())))

    brs = [r["br"] for r in all_rows]
    qs = statistics.quantiles(brs, n=4)
    print(f"\n  breadth across ALL harvested entry days: median "
          f"{statistics.median(brs):.1f}%  IQR {qs[0]:.1f}-{qs[2]:.1f}%")
    prems = [r["prem"] for r in all_rows]
    qp = statistics.quantiles(prems, n=4)
    print(f"  fill premium across harvested entries: median "
          f"{statistics.median(prems):+.2f}%  IQR {qp[0]:+.2f}-{qp[2]:+.2f}%")
    sp = sorted(prems)
    sb = sorted(brs)
    k = len(sp) // 3
    print(f"  EXACT tercile boundaries (count-based, n={len(sp)}): premium "
          f"mid|top split between {sp[2 * k - 1]:+.2f}% and {sp[2 * k]:+.2f}% "
          f"-- the follow-up rule freezes its threshold at the latter")
    print(f"  breadth low|mid split between {sb[k - 1]:.1f}% and "
          f"{sb[k]:.1f}%; mid|top between {sb[2 * k - 1]:.1f}% and "
          f"{sb[2 * k]:.1f}%")

    print(f"\n  resolving a {analysis.BACKTEST_EDGE:.1f}%/trade edge needs "
          f"{analysis.trades_needed(analysis.BACKTEST_EDGE)} trades; the "
          f"harvest holds {len(all_rows)}, split three ways for terciles.")
    print("  Endpoints (fixed in the docstring before running): |t|>2 with the\n"
          "  mechanism's sign earns ONE follow-up pre-registered rule-shape test\n"
          "  at the tercile boundary above. This file adopts nothing either way.")


# ----------------------------------------------------------------- selftest

def _selftest():
    from datetime import date, timedelta

    def series(sym, closes):
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))]
        s = features.Series(sym, days)
        for c in closes:
            px = float(c)
            s.open.append(px)
            s.high.append(px * 1.001)
            s.low.append(px * 0.999)
            s.close.append(px)
            s.volume.append(1000)
            s.turnover.append(1e6)
            s.deliv_pct.append(40.0)
            s.surveillance_known.append(True)
            s.restricted.append(False)
        return s

    # --- _above_ma_flags matches brute force, strict inequality, warmup gate
    closes = [100 + (i % 7) * 0.5 for i in range(260)]
    s = series("T", closes)
    fl = _above_ma_flags(s, window=200)
    assert len(fl) == 61, len(fl)                     # only i>=199 flagged
    brute = {}
    for i in range(199, 260):
        w = closes[i - 199:i + 1]
        assert len(w) == 200
        brute[s.days[i]] = closes[i] > sum(w) / 200
    assert fl == brute, "flag disagrees with brute force"
    flat = series("F", [100.0] * 210)
    assert not any(_above_ma_flags(flat, 200).values()), "flat counted as above"

    # --- breadth_map: rising=above, falling=below, flat=not -> exactly 1/3
    corp = {"U": series("U", [100 + i for i in range(210)]),
            "D": series("D", [300 - i for i in range(210)]),
            "S": series("S", [100.0] * 210)}
    days = corp["U"].days
    bm = breadth_map(corp, days)
    assert abs(bm[days[205]] - 100 / 3) < 1e-9, bm[days[205]]
    assert bm[days[150]] is None                      # warmup: nobody eligible
    short = {"A": series("A", [1.0] * 50)}
    assert all(v is None for v in breadth_map(short, short["A"].days).values())

    # --- join: premium math exact; missing key raises; unjoinable day drops
    s2 = series("J", [10.0] * 6 + [12.0] * 5)
    ie = s2.index_of(s2.days[6])
    trade = {"sym": "J", "entry_day": s2.days[6], "ret": 1.0, "clu": "micro",
             "why": "stop", "day": s2.days[8], "held": 2}
    br = {s2.days[6]: 55.0}
    rows, dropped = join([trade], 0, {"J": s2}, br)
    assert dropped == 0 and len(rows) == 1
    assert abs(rows[0]["prem"] - (s2.open[ie] / s2.close[ie - 1] - 1) * 100) < 1e-9
    assert abs(rows[0]["prem"] - 20.0) < 1e-9, rows[0]["prem"]  # 12 over 10
    bad = dict(trade)
    del bad["entry_day"]
    try:
        join([bad], 0, {"J": s2}, br)
        raise AssertionError("missing entry_day did not raise")
    except SystemExit:
        pass
    ghost = dict(trade, entry_day=s2.days[0])          # before any breadth
    rows2, dropped2 = join([ghost], 0, {"J": s2}, br)
    assert rows2 == [] and dropped2 == 1

    # --- demeaning removes each cohort's level exactly
    rs = [{"cohort": 0, "ret": 5.0}, {"cohort": 0, "ret": 7.0},
          {"cohort": 1, "ret": -9.0}]
    dm = demean_within_cohort(rs)
    assert abs(statistics.fmean([r["ret0"] for r in dm if r["cohort"] == 0])) < 1e-12
    assert abs(statistics.fmean([r["ret0"] for r in dm if r["cohort"] == 1])) < 1e-12

    # --- terciles: disjoint, exhaustive, ordered by the key
    ts = [{"x": i} for i in range(10)]
    lo, mid, hi = terciles(ts, "x")
    assert [t["x"] for t in lo] == [0, 1, 2]
    assert [t["x"] for t in mid] == [3, 4, 5]
    assert [t["x"] for t in hi] == [6, 7, 8, 9]       # last bin takes remainder
    seen = [t["x"] for t in lo + mid + hi]
    assert sorted(seen) == list(range(10)) and len(seen) == len(set(seen))

    # --- welch_gap sanity: identical samples gap ~0; shifted samples resolve
    import random as _rnd
    _rnd.seed(7)
    a = [_rnd.gauss(0, 16) for _ in range(400)]
    d0, _, t0 = welch_gap(a[:200], a[200:])
    assert abs(d0) < 1.0 and abs(t0) < 1, (d0, t0)
    b = [x + 4 for x in a[:200]]                       # +4 shift, sd 16, n 200
    _, _, t1 = welch_gap(b, a[200:])
    assert t1 > 2, t1

    print("loss_taxonomy_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
