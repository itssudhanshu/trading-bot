#!/usr/bin/env python3
"""H8-H11: every remaining use of sentiment, pre-registered together.

COMMITTED BEFORE ANY OF THE FOUR HAS BEEN RUN. All four are declared here at
once, deliberately: "test everything and then decide" is how a noise search
produces a winner, and the only defence is to fix the family and the bar before
seeing any of it. Nothing may be added to this family afterwards, and a result
may not be read by stopping early on a win.

WHY THERE IS ANYTHING LEFT TO TEST
----------------------------------
Seven hypotheses have been spent and none adopted. Every one of them asked the
same question in a different way: does sentiment predict RETURN? H6 answered it
about as clearly as this corpus can -- the graded score flipped sign at t=-1.08,
and its correlation with 6-month momentum was +0.036, so it is independent
information that carries nothing about direction.

The four below do not re-ask that. They ask whether sentiment relates to RISK,
and whether it is useful somewhere other than in the ranking:

  H8   does it predict the VOLATILITY of the holding period?
  H9   does it predict the STOP-OUT RATE?
  H10  does sizing positions by sentiment strength beat equal weight?
  H11  does standing aside on extreme sentiment help?

H8/H9 matter to this book specifically because its exits are asymmetric: the
stop is -10% and the target +20%. Higher volatility raises the odds of touching
both, but the stop is half the distance away, so it lands on stops first. A
signal that moves risk without moving expected return is invisible to every test
run so far and would still change how the book should be run.

THE GATE, and it is what keeps the family honest
------------------------------------------------
H10 and H11 RUN ONLY IF H8 or H9 clears the bar. Sizing by a signal, or standing
aside on one, requires the signal to exist; doing either on a measured null is
sizing by noise and filtering on noise. So the family is 9 tests if the gate
closes and 11 if it opens.

The bar is set for ELEVEN either way -- the maximum the family can reach --
so a closed gate cannot later be used to argue for a looser threshold on what
already ran.

THE BAR
-------
|t| >= 2.84. Bonferroni across eleven: 0.05/11 = 0.00455. Tighter than every
previous bar in this project, because more has been asked of the same data.

THE CONTROL, AND THE PRIMARY COMPARISON, DECLARED IN ADVANCE
------------------------------------------------------------
Control is trades whose visible filings carried NO signal -- the neutral
majority, ~87 of every 91 filings. Not "no filings at all", which is a different
population (quieter, smaller companies) and would confound size with sentiment.

The PRIMARY comparison for H8 and H9 is NEGATIVE sentiment against neutral, and
the direction is declared now: negative sentiment RAISES volatility and RAISES
the stop-out rate. Positive-against-neutral is reported as description with no
adoption path, because declaring both directions primary would be two tests
wearing one name.

That prediction follows two things already measured here: H6 found only the
positive side ever moved on returns, and the literature the operator supplied
reports negative sentiment hitting volatility harder and faster than positive
sentiment lifts trends. If the prior is wrong it will be recorded as wrong, the
way H6's was.

WHAT WOULD MAKE ANY OF THIS ADOPTABLE
-------------------------------------
Nothing here, on its own. A pass licenses a fresh out-of-sample or forward test,
never a change to the live book on this data -- the same rule H7 carried, and for
the same reason: too much has now been asked of one price history.

    STRATEGY=sentiment python3 src/research/sentiment_risk_test.py
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

BATCH = "20260822-sentiment-risk"
BAR = 2.84                 # Bonferroni across eleven; see the docstring
GATE = "H10 and H11 run only if H8 or H9 clears the bar"

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
    """-> [{tone, vol, stopped, ret, clu}] over random symbol-dates.

    Same sampling as tone_test so the two are comparable, with two outcomes
    added: realised volatility over the holding window, and whether the trade
    exited on the stop.
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
            if not e:
                continue
            tl = _timeline(sym)
            if not tl:
                continue
            vis = A.visible(tl, iso, window=30)
            if not vis:
                continue
            # The score is the graded one, which is what the operator's tool
            # produces. None means every filing was procedural -- that is the
            # CONTROL population, not an excluded one.
            tone = A.aggregate([A.score_announcement(r, tone_of) for r in vis])

            stop, tgt = e * (1 - STOP / 100), e * (1 + TARGET / 100)
            px, stopped = s.close[min(i + HOLD, len(s) - 1)], False
            end = min(i + 1 + HOLD, len(s))
            for k in range(i + 1, end):
                if s.low[k] <= stop:
                    px, stopped = min(stop, s.open[k]), True
                    end = k + 1
                    break
                if s.high[k] >= tgt:
                    px = max(tgt, s.open[k])
                    end = k + 1
                    break
            # Realised volatility over the bars actually held, in percent.
            rets = [s.close[k] / s.close[k - 1] - 1.0
                    for k in range(i + 2, end) if s.close[k - 1]]
            vol = statistics.pstdev(rets) * 100 if len(rets) >= 3 else None
            out.append({"tone": tone, "vol": vol, "stopped": stopped,
                        "ret": (px / e - 1.0) * 100, "clu": where[sym]})
    return out


def _mean_t(a, b):
    """-> (diff, std err, t) for mean(a) - mean(b), independent samples."""
    a = [x for x in a if x is not None]
    b = [x for x in b if x is not None]
    if len(a) < 2 or len(b) < 2:
        return (float("nan"),) * 3
    se = (statistics.stdev(a) ** 2 / len(a)
          + statistics.stdev(b) ** 2 / len(b)) ** 0.5
    d = statistics.fmean(a) - statistics.fmean(b)
    return d, se, (d / se if se else float("nan"))


def _prop_t(a, b):
    """-> (diff, std err, t) for two proportions. a, b are lists of bools."""
    if len(a) < 5 or len(b) < 5:
        return (float("nan"),) * 3
    pa, pb = sum(a) / len(a), sum(b) / len(b)
    se = (pa * (1 - pa) / len(a) + pb * (1 - pb) / len(b)) ** 0.5
    d = pa - pb
    return d * 100, se * 100, (d / se if se else float("nan"))


def _verdict(t):
    if t != t:
        return "not enough trades"
    return "RESOLVED" if abs(t) >= BAR else "inside the noise"


def main():
    if paths.STRATEGY != "sentiment":
        print(f"this test belongs to sentiment; STRATEGY={paths.STRATEGY}.")
        print("run:  STRATEGY=sentiment python3 src/research/sentiment_risk_test.py")
        return 1

    corpus = F.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"batch {BATCH}   hold {HOLD}d / stop {STOP}% / target {TARGET}%   "
          f"bar |t| >= {BAR}\n")
    rows = sample(corpus, days)

    neg = [r for r in rows if r["tone"] is not None and r["tone"] < 0]
    pos = [r for r in rows if r["tone"] is not None and r["tone"] > 0]
    neu = [r for r in rows if r["tone"] is None]
    print(f"{len(rows)} sampled trades   "
          f"negative {len(neg)} · positive {len(pos)} · "
          f"neutral (control) {len(neu)}\n")
    if len(neg) < 30 or len(neu) < 30:
        print("  too few in a group to say anything. Reporting nothing.")
        return 1

    print(f"  {'hypothesis':<34}{'diff':>10}{'std err':>9}{'t':>7}   verdict")

    # --- H8: volatility -----------------------------------------------------
    d8, se8, t8 = _mean_t([r["vol"] for r in neg], [r["vol"] for r in neu])
    print(f"  {'H8  negative -> volatility':<34}{d8:>+9.3f}%{se8:>8.3f}%"
          f"{t8:>+7.2f}   {_verdict(t8)}")

    # --- H9: stop-out rate --------------------------------------------------
    d9, se9, t9 = _prop_t([r["stopped"] for r in neg],
                          [r["stopped"] for r in neu])
    print(f"  {'H9  negative -> stop-out rate':<34}{d9:>+9.2f}pp{se9:>7.2f}pp"
          f"{t9:>+7.2f}   {_verdict(t9)}")

    # --- description only, no adoption path --------------------------------
    dp8, sp8, tp8 = _mean_t([r["vol"] for r in pos], [r["vol"] for r in neu])
    dp9, sp9, tp9 = _prop_t([r["stopped"] for r in pos],
                            [r["stopped"] for r in neu])
    print(f"\n  description only (no adoption path):")
    print(f"  {'    positive -> volatility':<34}{dp8:>+9.3f}%{sp8:>8.3f}%"
          f"{tp8:>+7.2f}")
    print(f"  {'    positive -> stop-out rate':<34}{dp9:>+9.2f}pp{sp9:>7.2f}pp"
          f"{tp9:>+7.2f}")

    print(f"\nrates: negative {sum(r['stopped'] for r in neg)/len(neg)*100:.1f}%"
          f" · neutral {sum(r['stopped'] for r in neu)/len(neu)*100:.1f}%"
          f" · positive {sum(r['stopped'] for r in pos)/max(len(pos),1)*100:.1f}%"
          f"  stopped out")

    # --- per size group -----------------------------------------------------
    print("\nper size group (H9, negative vs neutral):")
    for clu in ("micro", "small"):
        n2 = [r["stopped"] for r in neg if r["clu"] == clu]
        u2 = [r["stopped"] for r in neu if r["clu"] == clu]
        d, se, t = _prop_t(n2, u2)
        print(f"  {clu:<8}{d:>+8.2f}pp{se:>7.2f}pp{t:>+7.2f}"
              f"   n={len(n2)}/{len(u2)}   {_verdict(t)}")

    # --- the gate -----------------------------------------------------------
    passed = [n for n, t in (("H8", t8), ("H9", t9))
              if t == t and abs(t) >= BAR]
    print(f"\ngate: {GATE}")
    if passed:
        print(f"  OPEN -- {', '.join(passed)} cleared. H10 (sentiment-scaled")
        print("  sizing) and H11 (stand aside on extremes) are now worth")
        print("  building; they were deliberately not built in advance.")
    else:
        print("  CLOSED -- neither H8 nor H9 cleared the bar, so sizing by this")
        print("  signal or filtering on it would be sizing by noise. H10 and")
        print("  H11 are NOT run, and the family stays at nine tests.")

    # --- direction, against the prediction written down first ---------------
    print("\nthe prior, recorded before the run: negative sentiment RAISES both.")
    for name, d, t in (("volatility", d8, t8), ("stop-out rate", d9, t9)):
        if d != d:
            continue
        print(f"  {name:<16}{'as predicted' if d > 0 else 'OPPOSITE to the prediction'}"
              f"  ({d:+.3f}, t {t:+.2f})")
    return 0


def _selftest():
    # --- the arithmetic ----------------------------------------------------
    a = [1.0] * 50 + [3.0] * 50
    b = [0.0] * 50 + [2.0] * 50
    d, se, t = _mean_t(a, b)
    assert abs(d - 1.0) < 1e-9 and t > 0, (d, t)
    assert _mean_t(b, a)[0] < 0, "the subtraction is the wrong way round"
    assert _mean_t([1.0], b)[2] != _mean_t([1.0], b)[2], \
        "one observation produced a real t"
    # None values are dropped, not counted as zero
    assert abs(_mean_t([1.0, None, 1.0], [0.0, 0.0])[0] - 1.0) < 1e-9

    # proportions
    d, se, t = _prop_t([True] * 30 + [False] * 70, [True] * 10 + [False] * 90)
    assert abs(d - 20.0) < 1e-9 and t > 0, (d, t)
    assert _prop_t([True] * 3, [True] * 3)[2] != _prop_t([True] * 3, [True] * 3)[2], \
        "too few observations produced a real t"

    # --- the bar and the gate ----------------------------------------------
    assert BAR > 2.81, "this bar must be tighter than every earlier one"
    assert _verdict(BAR) == "RESOLVED" and _verdict(BAR - 0.01) == "inside the noise"
    src = open(__file__).read()
    assert "H10 and H11 run only if" in src, "the gate is gone"
    assert "no adoption path" in src, "the positive arm lost its no-adoption label"
    assert "never a change to the live book" in src, \
        "the caveat that a pass licenses only a fresh test is gone"
    # The control must be the neutral MAJORITY, not "no filings at all" --
    # those are quieter, smaller companies and would confound size with tone.
    assert "not \"no filings at all\"" in src or "Not \"no filings at all\"" in src, \
        "the control population is no longer defined"
    print("sentiment_risk_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
