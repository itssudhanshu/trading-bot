#!/usr/bin/env python3
"""Market state and an equal-weight benchmark, both point-in-time.

Closes the one gap in the analyst layer that was a real absence rather than a
difference of taste: every channel this project had was about ONE STOCK. There
was nothing that said what the market was doing on the day the pick was made,
and nothing to measure a trade against except its own raw return.

The framework this was compared with (TauricResearch/TradingAgents) fills the
first with FRED macro series and prediction markets, and the second with an
index -- its `benchmark_map` already lists `.NS -> ^NSEI`. Neither is available
here: FRED is US macro behind a key, and this project has no index series on
disk. What it does have is the whole cross-section, point-in-time, back to 2019.
So both are computed FROM THE CORPUS instead of fetched.

WHAT THIS IS NOT
----------------
**Not a published index.** `equal_weight_return` is an equal-weight buy-and-hold
return over symbols this project chose, which is not NIFTY and must never be
reported as though it were. It is a comparator built from the same universe the
bucket picks from, which is the property that makes it useful and also the
property that makes it unpublishable.

**Not a score input.** Nothing here may be given a weight in `selection.py`
without its own pre-registered test. Market state is an obvious thing to gate on
and gating on it has never been measured here; a regime filter that looks
sensible is exactly the shape of change this project has repeatedly shipped and
then found to be noise.

WHY `symbols` IS REQUIRED EVERYWHERE
------------------------------------
No function here defaults to "the whole corpus". Two reasons, both already paid
for once:

  - The corpus holds instruments this book does not trade. `universe.
    non_equity_symbols()` exists because 87 delisted ETFs sat inside the micro
    and small clusters and supplied 68% of the recorded CAGR (L69). A benchmark
    that quietly included them would repeat that with no ledger to catch it.
  - The universe splits into three turnover terciles and the most liquid third
    is discarded outright. Benchmarking a microcap book against a set that
    includes the names it refuses to buy measures the wrong thing.

So the caller passes the symbol list, and the strategy decides what its own
universe is. This module is shared and knows nothing about any strategy --
importing `clusters` here would resolve to whichever strategy `paths` happens to
have activated, which is the failure `paths.py` exists to prevent.

SURVIVORSHIP -- AND THE ARGUMENT BELOW IS MEASURED WRONG (L101)
--------------------------------------------------------------
A symbol whose series ends mid-window is not dropped -- it is carried at its
last printed close and counted in `n_truncated`. The rule was adopted on this
argument: dropping it would compute the benchmark over survivors only, which
flatters it exactly when the market was worst, and the whole point of a
benchmark is the bad windows.

**That argument is false on this corpus.** `benchmark_probe.py` C4, batch
`20260912-benchmarkprobe1`: dropping truncated names reads **-0.1643 +/-
0.0382pp** against carrying them (t = -4.30, n = 21 windows), and the gap is
NARROWER in the worst decile, not wider. Both halves of the prediction are
refuted. Carrying is not removing an upward bias, it is adding one -- plausibly
because a name that stops printing here is halted rather than dead, and L58
found the circuit locks were all UPPER locks. That mechanism is untested and is
a hypothesis, not a finding.

The rule stays, for two reasons that are not "it was right": re-choosing on one
post-hoc sample is churn, and the effect is 0.16pp against a p5/p95 spread of
-8.9/+18.0. It is also harmless to the thing this benchmark exists for -- H12
measures two arms against the SAME comparator, so a level bias cancels.

**Anything quoting this benchmark quotes `n_truncated` and that +0.16pp with
it.** A replacement rule is a fresh pre-registration.

    python3 src/core/market.py --selftest
"""
import argparse
import re
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths  # noqa: F401

import features

EMA_PERIOD = 50          # the window `features.breadth` uses; kept the same on purpose
RET_WINDOW = 20          # sessions, for the dispersion read


def _iso(day):
    return day.isoformat() if hasattr(day, "isoformat") else str(day)


def _idx_asof(s, day):
    """-> index of the last bar on or before `day`, or None.

    `Series.index_of` wants an exact trading date and returns None on a holiday
    or a symbol that did not print. A market read must not disappear because one
    name was suspended, so this walks back to the most recent bar instead.
    `str(date)` is ISO, so the comparison is correct for both a `date` and an
    already-stringified day without converting the whole column.
    """
    target = _iso(day)
    lo, hi = 0, len(s.days) - 1
    if hi < 0 or _iso(s.days[0]) > target:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _iso(s.days[mid]) <= target:
            lo = mid
        else:
            hi = mid - 1
    return lo


def state(corpus, as_of, symbols, ema_period=EMA_PERIOD, window=RET_WINDOW):
    """-> what the market was doing on `as_of`, from `symbols` only.

    Every figure is causal: each symbol contributes its own bars up to `as_of`
    and nothing after. Returns {} when no symbol has enough history, rather than
    a breadth of 0.0 -- which would read as "nothing is above its average" when
    it means "nothing could be measured".
    """
    above = seen = 0
    rets = []
    for sym in symbols:
        s = corpus.get(sym)
        if s is None:
            continue
        i = _idx_asof(s, as_of)
        if i is None or i < ema_period:
            continue
        e = features.ema(s.close[:i + 1], ema_period)[-1]
        if e is None:
            continue
        seen += 1
        if s.close[i] > e:
            above += 1
        j = i - window
        if j >= 0 and s.close[j]:
            rets.append(s.close[i] / s.close[j] - 1.0)
    if not seen:
        return {}
    out = {
        "as_of": _iso(as_of),
        "n": seen,
        "breadth": round(above / seen, 4),
        "ema_period": ema_period,
    }
    if len(rets) >= 2:
        out[f"median_{window}d_return"] = round(100 * statistics.median(rets), 2)
        out["dispersion"] = round(100 * statistics.pstdev(rets), 2)
        out["window"] = window
    return out


def equal_weight_return(corpus, symbols, start, end):
    """-> the equal-weight buy-and-hold return over [start, end], and its counts.

    Buy-and-hold rather than daily-rebalanced, because the thing being measured
    is a book that buys five names and holds them for ten sessions. A
    daily-rebalanced index answers a question nobody here asks, and would need a
    rebalancing-cost assumption this project has no data to set.

    Returns {} when nothing is measurable. `n_used` against `n_asked` is part of
    the result and not a footnote: a benchmark over 40 of 900 names is a
    different claim from one over 880, and the reader cannot tell them apart
    from the return alone.
    """
    rets, truncated, used = [], 0, 0
    end_iso = _iso(end)
    for sym in symbols:
        s = corpus.get(sym)
        if s is None:
            continue
        i0 = _idx_asof(s, start)
        i1 = _idx_asof(s, end)
        if i0 is None or i1 is None or i1 <= i0:
            continue
        if not s.close[i0]:
            continue
        # Carried at its last print rather than dropped -- see SURVIVORSHIP.
        if _iso(s.days[i1]) < end_iso:
            truncated += 1
        rets.append(s.close[i1] / s.close[i0] - 1.0)
        used += 1
    if not rets:
        return {}
    return {
        "start": _iso(start), "end": end_iso,
        "return_pct": round(100 * statistics.fmean(rets), 4),
        "median_pct": round(100 * statistics.median(rets), 4),
        "n_used": used, "n_asked": len(symbols), "n_truncated": truncated,
    }


def alpha(stock_return_pct, benchmark_return_pct):
    """-> the stock's return less the benchmark's, both in percent.

    A named function for a subtraction, so there is exactly one definition of
    alpha in this repo rather than one per call site. Arithmetic difference, not
    a ratio and not a regression beta: with ~200 forward trades there is nothing
    to fit a beta on, and a beta estimated on noise would make the adjustment
    worse than not adjusting.
    """
    if stock_return_pct is None or benchmark_return_pct is None:
        return None
    return round(stock_return_pct - benchmark_return_pct, 4)


# --------------------------------------------------------------------------

def _fake_corpus(n_sym=12, n_bar=260, drift=0.001):
    """A synthetic cross-section. The selftest must not need data/raw/, which is
    gitignored and absent from a fresh clone."""
    corpus = {}
    days = [date(2024, 1, 1).toordinal() + k for k in range(n_bar)]
    days = [date.fromordinal(o) for o in days]
    for k in range(n_sym):
        px, closes = 100.0, []
        step = drift * (1 if k % 2 == 0 else -1)
        for _ in range(n_bar):
            px *= 1 + step
            closes.append(px)
        corpus[f"SYM{k:02d}"] = features.Series(
            symbol=f"SYM{k:02d}", days=list(days), open=list(closes),
            high=[c * 1.01 for c in closes], low=[c * 0.99 for c in closes],
            close=closes, volume=[1000] * n_bar, turnover=[1e6] * n_bar,
            deliv_pct=[40.0] * n_bar, surveillance_known=[False] * n_bar,
            restricted=[False] * n_bar, rs={}, fund=[])
    return corpus, days


def _selftest():
    corpus, days = _fake_corpus()
    syms = sorted(corpus)

    # --- state: half up, half down, so breadth must be 0.5 ------------------
    st = state(corpus, days[-1], syms)
    assert st["n"] == 12, st
    assert st["breadth"] == 0.5, st["breadth"]
    assert st["dispersion"] > 0, st
    assert "median_20d_return" in st, st

    # --- coverage is not a reading -----------------------------------------
    assert state(corpus, days[10], syms) == {}, \
        "too little history must return {}, never a breadth of 0.0"
    assert state(corpus, days[-1], []) == {}, "no symbols is not a market read"
    assert state({}, days[-1], syms) == {}, "an empty corpus is not a market read"

    # --- a symbol absent from the corpus is skipped, not counted -----------
    st2 = state(corpus, days[-1], syms + ["NOSUCH"])
    assert st2["n"] == 12, "an unknown symbol must not enter the denominator"

    # --- equal-weight return: symmetric drift nets to about zero -----------
    ew = equal_weight_return(corpus, syms, days[-21], days[-1])
    assert ew["n_used"] == 12 and ew["n_asked"] == 12, ew
    assert ew["n_truncated"] == 0, ew
    assert abs(ew["return_pct"]) < 0.5, ew["return_pct"]

    # --- an all-up set must read positive, and beat the mixed one ----------
    up_corpus, up_days = _fake_corpus(n_sym=6, drift=0.002)
    for k in range(1, 6, 2):                       # make every symbol rise
        s = up_corpus[f"SYM{k:02d}"]
        s.close = list(up_corpus["SYM00"].close)
    up = equal_weight_return(up_corpus, sorted(up_corpus), up_days[-21], up_days[-1])
    assert up["return_pct"] > 3.0, up

    # --- a truncated symbol is carried and COUNTED, not dropped ------------
    short = corpus["SYM00"]
    short.days = short.days[:-10]
    short.close = short.close[:-10]
    ew2 = equal_weight_return(corpus, syms, days[-21], days[-1])
    assert ew2["n_used"] == 12, "a delisted name must stay in the benchmark"
    assert ew2["n_truncated"] == 1, ew2
    assert equal_weight_return(corpus, [], days[-21], days[-1]) == {}

    # --- a window with no room returns {} rather than 0.0 ------------------
    assert equal_weight_return(corpus, syms, days[-1], days[-1]) == {}, \
        "start == end is not a zero return, it is no measurement"

    # --- alpha is a subtraction with one definition ------------------------
    assert alpha(12.0, 4.0) == 8.0
    assert alpha(-3.0, 4.0) == -7.0
    assert alpha(None, 4.0) is None and alpha(1.0, None) is None

    # --- _idx_asof walks back over a hole ----------------------------------
    s = corpus["SYM01"]
    i = _idx_asof(s, date(2024, 1, 15))
    assert i is not None and _iso(s.days[i]) <= "2024-01-15", s.days[i]
    assert _idx_asof(s, date(2020, 1, 1)) is None, "before the first bar is None"
    assert _idx_asof(s, date(2030, 1, 1)) == len(s.days) - 1, "after the last bar"

    # --- shared means shared: no strategy may be imported here -------------
    # Matched on IMPORT LINES, not on a substring search: the first version
    # searched the raw text and tripped on its own banned-list literal, which is
    # the same shape of bug as a guard that passes because of the shell it runs
    # under. A name inside a string or a docstring is not an import.
    src = Path(__file__).read_text(encoding="utf-8")
    banned = re.compile(
        r"^\s*(?:import|from)\s+(clusters|selection|entry|learning)\b",
        re.MULTILINE)
    hit = banned.search(src)
    assert hit is None, \
        f"market.py is shared and must not bind to a strategy ({hit.group(0).strip()!r})"

    print("market selftest ok (state, equal-weight benchmark, alpha; "
          "no strategy imports)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--day", default=None, help="as-of date, YYYY-MM-DD")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    # A live read needs the strategy's own universe, which this module refuses
    # to guess. The caller that has one is dossier.py.
    print("market.py is a library: state() and equal_weight_return() both take "
          "the symbol list from the caller.\nSee `python3 src/ops/dossier.py "
          "SYMBOL` for a market read against the live tradeable clusters.")


if __name__ == "__main__":
    main()
