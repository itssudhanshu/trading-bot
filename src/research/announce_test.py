#!/usr/bin/env python3
"""H1-H3: do corporate announcements carry information the price features do not?

PRE-REGISTERED 2026-08-20, before the announcement backfill finished.
Spec: docs/superpowers/specs/2026-08-20-sentiment-patterns-design.md, section 6.

THE THREE HYPOTHESES
--------------------
  H1  ann_burst  a company filing far more than its OWN usual rate earns a
                 different forward return. Sign-free: no judgement about what
                 the filings say, only that there are unusually many.
  H2  ann_tone   entries whose most recent visible announcement carries a
                 positive sign in the frozen table outperform negative ones.
  H3  ann_flag   names where NSE DEMANDED an explanation for unusual trading
                 UNDERPERFORM. Direction stated in advance, not read off.

MEASURED ON RANDOM TRADES, WHICH IS THE ONLY HONEST WAY
-------------------------------------------------------
Sampled at random from the universe, never on trades an announcement score
helped choose. Measuring a feature on its own selections is what made `deliv`
look backwards and cost 26 CAGR points (fund_test.py says the same thing for
the same reason).

It is also why this measures the FEATURE and not the bucket. The bucket has
~195 trades, where per-trade standard deviation near 16% means nothing under
about 3 points is resolvable. A random sample reaches ~1,000+ and can see
effects four times smaller.

THE BAR, FIXED IN ADVANCE
-------------------------
|t| >= 2.6 -- the usual |t| > 2 tightened by Bonferroni across the five
pre-registered tests in this spec, because testing five things at the usual bar
means roughly one wins by luck.

AND THE SECOND GATE, WHICH THIS TEST DOES NOT DECIDE
----------------------------------------------------
Clearing the bar here does NOT mean a weight moves. `rs` had the highest t of
any feature ever measured in this project, and weighting it up produced the
worst of five books, because the 200-DMA gate and the breakout trigger already
capture it. Univariate significance is not marginal value to the bucket. A
feature that passes here goes on to a bucket test; a feature that fails here
never gets one.

A NOTE ON A CONSTANT THIS FILE DELIBERATELY DOES NOT COPY
---------------------------------------------------------
fund_test.py opens `HOLD, STOP, TARGET = 15, 10.0, 20.0`. The live hold has
been 10 since L51/L52. That is the stale-copy shape L60 documents and
impact_test.py already suffered for three months. This file reads
selection.HOLD_DAYS instead. fund_test.py is left alone -- it belongs to breakout,
and the operator's condition for this work is that breakout does not move -- but
its published fundamentals result was measured at a hold the bucket stopped
using.

    STRATEGY=sentiment python3 src/research/announce_test.py
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import random
import statistics
import sys

import announcements
import clusters
import features as F
import selection

BATCH = "20260820-sentiment"
BAR = 2.6

# Read the live rules. Never copy them; see the docstring.
HOLD = selection.HOLD_DAYS
STOP = selection.STOP_PCT
TARGET = selection.TARGET_PCT

FEATS = ("ann_burst", "ann_tone", "ann_flag")

# Stated BEFORE the run. A hypothesis with no predicted direction cannot be
# wrong, and a test that cannot be wrong is not a test.
PREDICTED = {"ann_burst": None,        # two-sided: no direction claimed
             "ann_tone": +1,           # positive tone should outperform
             "ann_flag": -1}           # NSE-flagged names should underperform

_TL = {}


def _timeline(sym):
    tl = _TL.get(sym)
    if tl is None:
        tl = _TL[sym] = announcements.timeline(sym)
    return tl


def sample(corpus, days, n_dates=90, per_date=45, seed=11):
    """-> [{feature values, ret, clu}] over randomly chosen symbol-dates.

    Entry is the NEXT session's open, exactly as the bucket fills, and the exit
    applies the live stop, target and hold. A feature measured against a
    frictionless close-to-close return would be answering a question nobody
    trades.
    """
    rng = random.Random(seed)
    out = []
    step = max(1, (len(days) - 320) // n_dates)
    for di in range(300, len(days) - HOLD - 1, step):
        day = days[di]
        day_iso = day.isoformat()
        bands = clusters.size_clusters(corpus, day)
        where = {s: b for b, v in bands.items() for s in v}
        syms = [s for s in where if corpus[s].index_of(day) is not None]
        if not syms:
            continue
        for sym in rng.sample(syms, min(per_date, len(syms))):
            s = corpus[sym]
            i = s.index_of(day)
            if i is None or i < 200 or i + 1 >= len(s):
                continue
            e = s.open[i + 1]
            if not e:
                continue
            tl = _timeline(sym)
            if not tl:
                continue                    # no announcement history: excluded
            f = announcements.features_asof(tl, day_iso)
            if not f:
                continue
            stop, tgt = e * (1 - STOP / 100), e * (1 + TARGET / 100)
            px = s.close[min(i + HOLD, len(s) - 1)]
            for k in range(i + 1, min(i + 1 + HOLD, len(s))):
                if s.low[k] <= stop:
                    px = min(stop, s.open[k]); break
                if s.high[k] >= tgt:
                    px = max(tgt, s.open[k]); break
            row = dict(f)
            row["ret"] = (px / e - 1.0) * 100
            row["clu"] = where[sym]
            out.append(row)
    return out


def split(rows, feat):
    """-> (high group, low group). Split where the feature actually varies.

    A median split on ann_tone or ann_flag would be nonsense: both are mostly
    zero, so the median IS zero and 'above median' would be empty or would
    swallow the neutral majority. Signed features split on their sign, and the
    neutral majority is excluded from BOTH sides rather than being silently
    counted as one of them.
    """
    if feat in ("ann_tone", "ann_flag"):
        hi = [r for r in rows if r.get(feat, 0) > 0]
        lo = [r for r in rows if r.get(feat, 0) < 0]
        if feat == "ann_flag":              # 0/1: the comparison is flagged vs not
            lo = [r for r in rows if r.get(feat, 0) == 0]
        return hi, lo
    vals = sorted(r[feat] for r in rows if r.get(feat) is not None)
    if not vals:
        return [], []
    med = statistics.median(vals)
    return ([r for r in rows if r.get(feat) is not None and r[feat] > med],
            [r for r in rows if r.get(feat) is not None and r[feat] <= med])


def measure(hi, lo):
    """-> (spread, std err, t) for hi minus lo, two independent samples."""
    if len(hi) < 2 or len(lo) < 2:
        return float("nan"), float("nan"), float("nan")
    mh, ml = statistics.fmean(r["ret"] for r in hi), statistics.fmean(r["ret"] for r in lo)
    sh, sl = statistics.stdev(r["ret"] for r in hi), statistics.stdev(r["ret"] for r in lo)
    se = (sh ** 2 / len(hi) + sl ** 2 / len(lo)) ** 0.5
    sp = mh - ml
    return sp, se, (sp / se if se else float("nan"))


def report(rows):
    print(f"  {'feature':<12}{'high n':>8}{'low n':>8}{'spread':>10}"
          f"{'std err':>9}{'t':>7}   verdict")
    verdicts = {}
    for f in FEATS:
        hi, lo = split(rows, f)
        sp, se, t = measure(hi, lo)
        if t != t:
            print(f"  {f:<12}{len(hi):>8}{len(lo):>8}{'--':>10}{'--':>9}{'--':>7}"
                  f"   too few to say")
            verdicts[f] = None
            continue
        resolved = abs(t) >= BAR
        want = PREDICTED[f]
        note = "RESOLVED" if resolved else "inside the noise"
        if resolved and want is not None and (sp > 0) != (want > 0):
            note = "RESOLVED but the WRONG WAY"
        print(f"  {f:<12}{len(hi):>8}{len(lo):>8}{sp:>+9.2f}%{se:>8.2f}%"
              f"{t:>+7.2f}   {note}")
        verdicts[f] = (sp, se, t, note)
    return verdicts


def main():
    if paths.STRATEGY != "sentiment":
        print(f"this test belongs to sentiment; STRATEGY={paths.STRATEGY}.")
        print("run:  STRATEGY=sentiment python3 src/research/announce_test.py")
        return 1

    corpus = F.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"batch {BATCH}   hold {HOLD}d / stop {STOP}% / target {TARGET}%   "
          f"bar |t| >= {BAR}\n")

    rows = sample(corpus, days)
    print(f"{len(rows)} randomly-sampled trades with an announcement history "
          f"visible\n")
    if len(rows) < 500:
        print("  WARNING: fewer than 500 samples. Widen n_dates/per_date before")
        print("  reading anything below as a result.\n")

    verdicts = report(rows)

    # Per size group. A total is not a finding when one group supplied it.
    print("\nper size group:")
    for clu in ("micro", "small"):
        sub = [r for r in rows if r["clu"] == clu]
        print(f"\n  {clu} (n={len(sub)}):")
        for f in FEATS:
            hi, lo = split(sub, f)
            sp, se, t = measure(hi, lo)
            if t != t:
                print(f"    {f:<12} too few to say")
                continue
            print(f"    {f:<12}{sp:>+8.2f}%  +/- {se:.2f}  t {t:+5.2f}   "
                  f"{'RESOLVED' if abs(t) >= BAR else 'inside the noise'}")

    passed = [f for f, v in verdicts.items()
              if v and abs(v[2]) >= BAR and v[3] == "RESOLVED"]
    print(f"\n  -> {len(passed)} of {len(FEATS)} clear |t| >= {BAR}: "
          f"{', '.join(passed) if passed else 'none'}")
    if not passed:
        print("     No weight moves. A feature that cannot beat its own margin")
        print("     of error has not earned a place in the score.")
    else:
        print("     Univariate only. These now need a BUCKET test before any")
        print("     weight moves -- rs had the highest t ever measured here and")
        print("     produced the worst of five books.")
    return 0


def _selftest():
    # split() must not treat the neutral majority as a side
    rows = [{"ann_tone": 1, "ret": 5.0}, {"ann_tone": -1, "ret": -5.0},
            {"ann_tone": 0, "ret": 99.0}, {"ann_tone": 0, "ret": -99.0}]
    hi, lo = split(rows, "ann_tone")
    assert len(hi) == 1 and len(lo) == 1, (hi, lo)
    assert all(abs(r["ret"]) == 5.0 for r in hi + lo), \
        "the neutral majority leaked into a side and would dominate the spread"

    # ann_flag compares flagged against NOT flagged, so the zeros ARE the low side
    rows = [{"ann_flag": 1.0, "ret": 1.0}, {"ann_flag": 0.0, "ret": 2.0},
            {"ann_flag": 0.0, "ret": 3.0}]
    hi, lo = split(rows, "ann_flag")
    assert len(hi) == 1 and len(lo) == 2, (hi, lo)

    # a continuous feature splits at its median
    rows = [{"ann_burst": v, "ret": 0.0} for v in (1, 2, 3, 4)]
    hi, lo = split(rows, "ann_burst")
    assert len(hi) == 2 and len(lo) == 2, (hi, lo)

    # measure(): direction and NaN guard
    import math
    hi = [{"ret": 2.0}] * 2 + [{"ret": 4.0}] * 2
    lo = [{"ret": 0.0}] * 2 + [{"ret": 2.0}] * 2
    sp, se, t = measure(hi, lo)
    assert abs(sp - 2.0) < 1e-9, sp
    assert measure([{"ret": 1.0}], lo)[2] != measure([{"ret": 1.0}], lo)[2], \
        "a one-sided sample produced a real t"

    # the live hold must be READ, not copied
    assert HOLD == selection.HOLD_DAYS, "HOLD drifted from the live rule"
    assert BAR >= 2.0, "the bar may be tightened, never relaxed"

    # every hypothesis must have declared a direction (None = deliberately two-sided)
    assert set(PREDICTED) == set(FEATS), "a feature has no pre-declared direction"
    print("announce_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
