#!/usr/bin/env python3
"""H6: does the filing TEXT carry what the category label does not — and is any
of it just momentum wearing a different hat?

PRE-REGISTERED 2026-08-22, before the graded score was run against any return.
Spec: docs/superpowers/specs/2026-08-20-sentiment-patterns-design.md, section 6.

WHY THIS IS NOT A RE-RUN OF H2
------------------------------
H2 measured `ann_tone`: a category label mapped to +1/0/-1 from a frozen table.
It read +1.24%, t = +1.71, against a bar of 2.6, and was not adopted. Re-running
the same feature would be a knob and is forbidden.

This is a different instrument on the same source. The graded scorer READS THE
FILING TEXT -- lexicon, negation, and the summary itself -- which `ann_tone`
never did. The text is an input the earlier test could not see, which is what
makes this legal. It also aggregates differently: items that say nothing are
excluded rather than averaged in, because a procedural filing is an absence of
observation, not an observation of neutrality.

The NEWS channel is excluded entirely. It has no history, so a backtest reading
it would be reading the future. Only filings are scored here.

TWO CONTROLS, AND THE SECOND IS THE POINT
-----------------------------------------
  category-only   `ann_tone` as H2 measured it, re-run on THIS sample so the two
                  are comparable. If the graded score does not beat it, the text
                  machinery buys nothing and the extra complexity is not paid for.

  momentum        the operator's actual question: is this determining trend, or
                  restating it? `rs` is the bucket's own 6-month momentum. A
                  sentiment score that correlates strongly with it is an ECHO --
                  filings follow price, coverage follows filings -- and would add
                  nothing to a score that already gates on the 200-day average
                  and triggers on a 20-day breakout.

That second control exists because of `rs` itself: it had the highest t of any
feature ever measured here, and weighting it up produced the WORST of five books,
because the gate and the trigger already captured it. Univariate significance is
not marginal value. A feature has to be significant AND independent.

THE ENDPOINT
------------
Mean per-trade return, top tercile minus bottom tercile by score, with standard
error and t, on randomly sampled symbol-dates -- never on trades the score helped
choose. Reported per size group. Entry is the next session's open and the exit
applies the live stop, target and hold, read from `selection`.

Plus Pearson correlation between the graded score and `rs` on the same sample.

THE BAR
-------
|t| >= 2.64. The spec's family was five tests at 2.6; this is the sixth, so
Bonferroni gives 0.05/6 = 0.00833. Tightened, never relaxed.

Adoption requires ALL FOUR:
  1. graded score edge clears |t| >= 2.64
  2. it beats category-only -- otherwise the text adds nothing
  3. |correlation with rs| < 0.30 -- otherwise it is momentum restated
  4. both size groups carry the same sign

    STRATEGY=sentiment python3 src/research/tone_test.py
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import random
import statistics
import sys

import announcements as A
import clusters
import features as F
import selection

BATCH = "20260822-sentiment-h6"
BAR = 2.64
MAX_ECHO = 0.30            # above this, the score is momentum restated

# Read the live rules. fund_test.py opens `HOLD, STOP, TARGET = 15, 10.0, 20.0`
# while the live hold has been 10 since L51/L52 -- the stale-copy shape of L60.
HOLD = selection.HOLD_DAYS
STOP = selection.STOP_PCT
TARGET = selection.TARGET_PCT

_TL = {}


def _timeline(sym):
    tl = _TL.get(sym)
    if tl is None:
        tl = _TL[sym] = A.timeline(sym)
    return tl


def sample(corpus, days, n_dates=90, per_date=45, seed=11):
    """-> [{graded, category, rs, ret, clu}] over random symbol-dates.

    Random, never the bucket's own picks. Measuring a feature on its own
    selections is what made `deliv` look backwards and cost 26 CAGR points.
    """
    rng = random.Random(seed)
    tone_of = A.load_tone()
    out = []
    step = max(1, (len(days) - 320) // n_dates)
    for di in range(300, len(days) - HOLD - 1, step):
        day = days[di]
        iso = day.isoformat()
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
            prev = s.close[i - 125] if i >= 125 else None
            if not e or not prev:
                continue
            tl = _timeline(sym)
            if not tl:
                continue
            vis = A.visible(tl, iso, window=30)
            if not vis:
                continue                      # nothing visible: no observation

            graded = A.aggregate([A.score_announcement(r, tone_of) for r in vis])
            if graded is None:
                continue                      # everything procedural; see H2
            # The category-only feature, exactly as H2 measured it: the most
            # recent visible category through the frozen table.
            cat = 0.0
            for r in vis:
                if r["desc"] in tone_of:
                    cat = float(tone_of[r["desc"]])
                    break

            stop, tgt = e * (1 - STOP / 100), e * (1 + TARGET / 100)
            px = s.close[min(i + HOLD, len(s) - 1)]
            for k in range(i + 1, min(i + 1 + HOLD, len(s))):
                if s.low[k] <= stop:
                    px = min(stop, s.open[k]); break
                if s.high[k] >= tgt:
                    px = max(tgt, s.open[k]); break
            out.append({"graded": graded, "category": cat,
                        "rs": (s.close[i] / prev - 1.0) * 100,
                        "ret": (px / e - 1.0) * 100, "clu": where[sym]})
    return out


def _spread(rows, key):
    """-> (spread, std err, t, n_hi, n_lo) for top third minus bottom third."""
    vals = sorted(r[key] for r in rows)
    if len(vals) < 30:
        return (float("nan"),) * 3 + (0, 0)
    lo_cut = vals[len(vals) // 3]
    hi_cut = vals[2 * len(vals) // 3]
    hi = [r["ret"] for r in rows if r[key] > hi_cut]
    lo = [r["ret"] for r in rows if r[key] < lo_cut]
    if len(hi) < 2 or len(lo) < 2:
        return (float("nan"),) * 3 + (len(hi), len(lo))
    mh, ml = statistics.fmean(hi), statistics.fmean(lo)
    se = (statistics.stdev(hi) ** 2 / len(hi)
          + statistics.stdev(lo) ** 2 / len(lo)) ** 0.5
    sp = mh - ml
    return sp, se, (sp / se if se else float("nan")), len(hi), len(lo)


def _corr(rows, a, b):
    xs = [r[a] for r in rows]
    ys = [r[b] for r in rows]
    if len(xs) < 3:
        return float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def _verdict(t):
    if t != t:
        return "not enough trades"
    return "RESOLVED" if abs(t) >= BAR else "inside the noise"


def main():
    if paths.STRATEGY != "sentiment":
        print(f"this test belongs to sentiment; STRATEGY={paths.STRATEGY}.")
        print("run:  STRATEGY=sentiment python3 src/research/tone_test.py")
        return 1

    corpus = F.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"batch {BATCH}   hold {HOLD}d / stop {STOP}% / target {TARGET}%   "
          f"bar |t| >= {BAR}\n")
    rows = sample(corpus, days)
    print(f"{len(rows)} randomly-sampled trades with a scoreable filing history\n")
    if len(rows) < 500:
        print("  WARNING: under 500 samples; read nothing below as a result.\n")
        return 1

    print(f"  {'feature':<14}{'spread':>9}{'std err':>9}{'t':>7}"
          f"{'n hi/lo':>12}   verdict")
    res = {}
    for k, label in (("category", "category only"), ("graded", "graded (+text)")):
        sp, se, t, nh, nl = _spread(rows, k)
        res[k] = (sp, se, t)
        print(f"  {label:<14}{sp:>+8.2f}%{se:>8.2f}%{t:>+7.2f}"
              f"{f'{nh}/{nl}':>12}   {_verdict(t)}")

    echo = _corr(rows, "graded", "rs")
    print(f"\nis it momentum restated?")
    print(f"  correlation(graded score, 6-month momentum) = {echo:+.3f}"
          f"   {'ECHO' if abs(echo) >= MAX_ECHO else 'independent'}"
          f"  (threshold {MAX_ECHO})")
    print(f"  correlation(category only, 6-month momentum) = "
          f"{_corr(rows, 'category', 'rs'):+.3f}")

    print("\nper size group (graded):")
    grp = {}
    for clu in ("micro", "small"):
        sub = [r for r in rows if r["clu"] == clu]
        sp, se, t, nh, nl = _spread(sub, "graded")
        grp[clu] = sp
        print(f"  {clu:<8}{sp:>+8.2f}%{se:>8.2f}%{t:>+7.2f}"
              f"   n={len(sub)}   {_verdict(t)}")

    sp_g, se_g, t_g = res["graded"]
    sp_c = res["category"][0]
    print("\nadoption check (all four required):")
    c1 = abs(t_g) >= BAR
    print(f"  1  edge clears |t| >= {BAR}        {sp_g:+.2f}% +/- {se_g:.2f}, "
          f"t {t_g:+.2f}   {'PASS' if c1 else 'FAIL'}")
    c2 = sp_g == sp_g and sp_c == sp_c and sp_g > sp_c
    print(f"  2  text beats category alone      {sp_g:+.2f}% vs {sp_c:+.2f}%"
          f"   {'PASS' if c2 else 'FAIL'}")
    c3 = echo == echo and abs(echo) < MAX_ECHO
    print(f"  3  not momentum restated          |r| {abs(echo):.3f} < {MAX_ECHO}"
          f"   {'PASS' if c3 else 'FAIL'}")
    c4 = all(v == v for v in grp.values()) and (
        (grp["micro"] > 0) == (grp["small"] > 0))
    print(f"  4  both size groups agree in sign micro {grp['micro']:+.2f}%, "
          f"small {grp['small']:+.2f}%   {'PASS' if c4 else 'FAIL'}")

    ok = c1 and c2 and c3 and c4
    print(f"\n  -> {'ADOPT' if ok else 'DO NOT ADOPT'}: "
          f"{'all four met' if ok else _verdict(t_g) if not c1 else 'a condition failed'}")
    if not ok:
        print("     ANN_FEATURES stays empty. A candidate that wins by less than")
        print("     its margin of error is a finding about this price history.")
    return 0


def _selftest():
    """The arithmetic, without a backtest."""
    import math
    # _spread: a feature that ranks returns perfectly must show a positive
    # spread; one that ranks them backwards, a negative one of the same size.
    rows = [{"x": i, "ret": float(i), "clu": "micro"} for i in range(90)]
    sp, se, t, nh, nl = _spread(rows, "x")
    assert sp > 0 and nh > 10 and nl > 10, (sp, nh, nl)
    back = [{"x": -r["x"], "ret": r["ret"], "clu": "micro"} for r in rows]
    sp2, _, _, _, _ = _spread(back, "x")
    assert abs(sp2 + sp) < 1e-9, "the spread is not antisymmetric"
    # too few rows must return NaN, never a confident zero
    assert _spread(rows[:5], "x")[0] != _spread(rows[:5], "x")[0]

    # _corr against known cases
    same = [{"a": i, "b": i} for i in range(20)]
    assert abs(_corr(same, "a", "b") - 1.0) < 1e-9
    opp = [{"a": i, "b": -i} for i in range(20)]
    assert abs(_corr(opp, "a", "b") + 1.0) < 1e-9
    flat = [{"a": i, "b": 5} for i in range(20)]
    assert _corr(flat, "a", "b") != _corr(flat, "a", "b"), \
        "a constant series produced a real correlation"

    assert BAR > 2.6, "the bar must be TIGHTER than the five-test family's"
    assert HOLD == selection.HOLD_DAYS, "HOLD drifted from the live rule"
    assert _verdict(BAR) == "RESOLVED" and _verdict(BAR - 0.01) == "inside the noise"
    # The echo control is not optional: without it a feature that merely
    # restates momentum passes on significance alone, which is the rs mistake.
    src = open(__file__).read()
    assert "MAX_ECHO" in src and "not momentum restated" in src, \
        "the momentum control is gone"
    print("tone_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
