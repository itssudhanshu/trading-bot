#!/usr/bin/env python3
"""The analyst dossier: everything knowable about one candidate, as of one date.

Adapted from the analyst team in TauricResearch/TradingAgents -- their four
channels (fundamentals, sentiment, news, technical), their per-channel report
shape, their data replaced. A fifth, `market`, was added because comparing the
two layers found a real absence rather than a difference of taste: every channel
here was about ONE STOCK, so nothing could say whether a name was strong or
merely floating on a strong tape. Their equivalent is FRED macro and prediction
markets; neither exists for this universe, so `market.py` computes it from the
corpus. What is NOT adapted is the part that turns four
reports into a rating, and the reason is the whole of this docstring.

THE SPLIT, WHICH IS THE POINT
-----------------------------
This module decides WHAT WAS VISIBLE. It does not decide what it means.

  assembled here      deterministic, reproducible, no model. Same symbol and
                      same date give the same dossier every time.
  judged elsewhere    `scripts/claude/agents/reviewer-*.md`. A model's reading,
                      recorded with the dossier that produced it, never fed back
                      into any measured result.

That split is already this repo's convention: `src/ops/sentiment.py` decides
what was visible and `skills/sentiment` decides what it means. The first version
of sentiment had it the other way round -- evidence fixed, a model judging -- and
the answer moved between runs, so it could never be measured against anything.
A debate between two models is that failure with a second speaker.

THERE IS NO COMPOSITE SCORE, DELIBERATELY
-----------------------------------------
Four channels are not averaged into one number here, and adding one later is the
change this file exists to make somebody argue for.

  - `pipeline.py` opens on it: a chain of agents is a summariser of summarisers,
    and a summary of a summary is where n and the error bar go to die.
  - Three of the four channels have already been MEASURED on this corpus and
    none of them cleared its bar. Fundamentals: four features, 1,049 trades,
    every CI straddling zero (|t| <= 0.89). Sentiment: eleven pre-registered
    hypotheses, none adopted -- `ann_tone` at t = 1.71 against a bar of 2.6, the
    graded text score flipping sign at t = -1.08. Averaging four readings, three
    of them measured flat, produces a number with no evidence behind it and a
    decimal point in front of it.
  - The fourth channel is not independent evidence at all. See `prior` below.

So a Dossier presents evidence and refuses to rank. Anything that wants a verdict
has to produce one out loud, in a place where it can be scored later.

`prior` MARKS THE CHANNEL THAT IS NOT NEW INFORMATION
-----------------------------------------------------
TradingAgents' market analyst reads MACD, RSI and the moving averages. Here that
IS the strategy: the score is 6-month RS + delivery% + liquidity, gated on the
200-day average and triggered on a 20-day breakout. A technical reading is
therefore a restatement of why the name is on the list, not a second opinion
about it, and `Reading.prior` says so on the row.

That distinction is the `rs` lesson, which cost this project a book: rs had the
highest t of any feature ever measured here (+1.40%, t = 3.07), and weighting it
up produced the WORST of five variants, because the 200-DMA gate and the
breakout trigger already captured it. Univariate significance is not marginal
value. A channel has to be significant AND independent, and the technical
channel is definitionally not the second one.

COVERAGE IS NOT A SCORE OF ZERO
-------------------------------
Every Reading carries `covered`, and an uncovered channel has `value=None` --
never 0.0, never "neutral". `features.rsi` already holds this line in its own
docstring ("None until seeded -- never 50 as a stand-in for unknown") and the
sentiment README holds it for whole channels: an absent channel scored as
neutral reads as "no view" while meaning "no data". On an NSE microcap that is
the common case, not the edge case.

NO BACKTEST MAY IMPORT THIS
---------------------------
The news channel reads `data/news/`, which accumulates forward and has no
history -- so a backtest touching this module would be reading the future. The
guarantee is asserted in `_selftest`, beside the thing it protects, the same way
`newswatch.py` asserts it.

    python3 src/ops/dossier.py SYMBOL [--day YYYY-MM-DD]
    python3 src/ops/dossier.py --selftest
"""
import argparse
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths

import features
import fundamentals
import market
import sentiment as _sentiment

ROOT = paths.ROOT

# Bounded readings, one scale for every channel so a person can compare rows
# without a conversion table. -1 is as bearish as a channel can read, +1 as
# bullish; None is no data and is printed as such.
BANDS = ((0.35, "positive"), (0.10, "leaning positive"), (-0.10, "mixed"),
         (-0.35, "leaning negative"), (-1.01, "negative"))


def band(v):
    """-> the word for a bounded reading, or 'no data' for None."""
    if v is None:
        return "no data"
    for floor, word in BANDS:
        if v >= floor:
            return word
    return "negative"


def _clip(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


def _as_date(day):
    """-> `day` as a `date`, or today. Normalised at the channel boundary.

    `--day` arrives as a string and the sentiment channels do arithmetic on it
    (`day - timedelta(...)`), so passing it through unconverted raised
    `TypeError: unsupported operand type(s) for -: 'str' and 'timedelta'` --
    which the channel caught and reported as `channel unavailable`. A crash
    dressed as an absence of data is the worst of both: the dossier looked
    healthy and the two evidence channels were silently off on every CLI call.
    """
    if day is None:
        return date.today()
    if isinstance(day, date):
        return day
    return date.fromisoformat(str(day)[:10])


@dataclass
class Reading:
    """One channel's view of one candidate, as of one date.

    `covered` is the field that must be read first: False means the channel had
    nothing to say, which is different from having said nothing of note. `value`
    is None whenever `covered` is False, and the two are asserted consistent.

    `prior` marks a channel that restates the selection rule rather than adding
    to it -- see the module docstring. `backtest_safe` marks a channel with a
    real point-in-time history behind it; the news channel does not have one.
    """
    channel: str
    covered: bool
    value: float | None = None
    facts: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    note: str = ""
    prior: bool = False
    backtest_safe: bool = True

    def __post_init__(self):
        if not self.covered:
            assert self.value is None, \
                f"{self.channel}: an uncovered channel must not carry a value"
        if self.value is not None:
            assert -1.0 <= self.value <= 1.0, \
                f"{self.channel}: reading {self.value} outside [-1, 1]"

    @property
    def band(self):
        """-> the word for this reading.

        Three states, not two. A channel can be uncovered (`no data`), covered
        and scored (a band), or covered and DELIBERATELY unscored -- which is
        where `fundamental`, `sentiment` and `market` live, because each was
        measured flat or never measured at all. Rendering that third state as
        `no data` erased the difference between a channel with nothing to say
        and a channel this project has decided not to let vote.
        """
        if self.covered and self.value is None:
            return "reported, unscored"
        return band(self.value)


# --------------------------------------------------------------------------
# the four channels
# --------------------------------------------------------------------------

def technical(series, day=None):
    """The market analyst: the selection rule restated, and marked as such.

    RSI and MACD are here because TradingAgents' market analyst reads them and
    the operator asked for them. They are NOT part of this book's score and
    nothing here proposes that they become part of it -- `rsi_bounce_test.py`
    and the rest of `research/` is where that argument would have to be won.
    They are reported so a reviewer can see the same picture a discretionary
    trader would, next to the gate and the trigger that actually chose the name.
    """
    closes = list(series.close or [])
    days = list(series.days or [])
    if day is not None:
        iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
        keep = [i for i, d in enumerate(days) if str(d) <= iso]
        if not keep:
            return Reading("technical", covered=False, prior=True,
                           note="no bars on or before the as-of date")
        cut = keep[-1] + 1
        closes, days = closes[:cut], days[:cut]
        highs = list(series.high or [])[:cut]
        lows = list(series.low or [])[:cut]
    else:
        highs, lows = list(series.high or []), list(series.low or [])

    if len(closes) < 200:
        return Reading("technical", covered=False, prior=True,
                       note=f"{len(closes)} bars; the 200-day gate needs 200")

    px = closes[-1]
    sma200 = features.sma(closes, 200)[-1]
    sma50 = features.sma(closes, 50)[-1]
    rsi14 = features.rsi(closes, 14)[-1]
    hi20 = features.rolling_max(closes[:-1], 20)[-1] if len(closes) > 20 else None
    atr14 = features.atr(highs, lows, closes, 14)[-1] if highs and lows else None

    # The score's OWN four inputs, computed the way `clusters.py` computes them
    # -- 125 sessions for both rs and the high, 60 for delivery and turnover.
    # The first version of this channel reported RSI, MACD and a 250-day high
    # and omitted delivery and liquidity entirely: it carried the indicator menu
    # of the framework this was adapted from, not the inputs this book ranks on.
    # `deliv` is the weighted-up feature (1.5); leaving it out while claiming to
    # restate the selection rule was the whole defect.
    hi125 = max(highs[-126:]) if highs else None
    rs125 = (px / closes[-126] - 1.0) if len(closes) > 126 and closes[-126] else None
    dl = [d for d in (series.deliv_pct or [])[:len(closes)][-61:] if d and d > 0]
    to = [x for x in (series.turnover or [])[:len(closes)][-61:] if x and x > 0]

    # MACD, the standard 12/26/9. Written out rather than imported because
    # features.py carries no MACD and adding one there would put an indicator
    # the strategy does not use into the module the strategy reads.
    e12, e26 = features.ema(closes, 12), features.ema(closes, 26)
    macd_line = [(a - b) if (a is not None and b is not None) else None
                 for a, b in zip(e12, e26)]
    seeded = [m for m in macd_line if m is not None]
    sig = features.ema(seeded, 9)[-1] if len(seeded) >= 9 else None
    macd = macd_line[-1]

    facts = {
        "close": round(px, 2),
        "sma200": round(sma200, 2) if sma200 else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "above_200dma": bool(sma200 and px > sma200),        # THE gate
        "pct_above_200dma": round(100 * (px / sma200 - 1), 2) if sma200 else None,
        "breakout_20d": bool(hi20 and px > hi20),            # THE trigger
        # --- the four scored features ---
        "rs_125d_pct": round(100 * rs125, 2) if rs125 is not None else None,
        "deliv_60d_pct": round(statistics.fmean(dl), 2) if dl else None,
        "liq_60d_median_turnover": round(statistics.median(to), 0) if to else None,
        "near_high_125d": round(-((hi125 - px) / hi125 * 100), 2) if hi125 else None,
        # --- shown, never scored ---
        "rsi14": round(rsi14, 1) if rsi14 is not None else None,
        "atr14_pct": round(100 * atr14 / px, 2) if atr14 else None,
        "macd": round(macd, 3) if macd is not None else None,
        "macd_signal": round(sig, 3) if sig is not None else None,
        "macd_above_signal": bool(macd is not None and sig is not None and macd > sig),
    }

    # The reading is the GATE and the TRIGGER, which is what put the name here.
    # RSI and MACD do not move it: they are shown, not counted, because nothing
    # on this corpus has measured them and a number nobody measured must not be
    # allowed to vote.
    v = 0.0
    if facts["above_200dma"]:
        v += 0.5
    if facts["breakout_20d"]:
        v += 0.5
    ev = [f"close {facts['close']} vs 200-DMA {facts['sma200']} "
          f"({facts['pct_above_200dma']:+}%) -- THE gate" if facts["sma200"]
          else "no 200-DMA",
          f"20-day breakout: {'yes' if facts['breakout_20d'] else 'no'} -- THE trigger",
          f"scored: rs(125d) {facts['rs_125d_pct']}%, deliv(60d) "
          f"{facts['deliv_60d_pct']}% [weight 1.5], liq(60d median turnover) "
          f"{facts['liq_60d_median_turnover']}, near_high(125d) "
          f"{facts['near_high_125d']}%",
          f"RSI(14) {facts['rsi14']}, MACD {facts['macd']} vs signal "
          f"{facts['macd_signal']}, ATR14 {facts['atr14_pct']}% "
          f"(shown, not counted -- none is in the score)"]
    return Reading("technical", covered=True, value=_clip(v), facts=facts,
                   evidence=ev, prior=True,
                   note="restates the selection rule; not independent evidence")


def fundamental(series, day=None):
    """The fundamentals analyst, over the XBRL filings, dated by broadCastDate.

    Measured flat on this corpus: rev_growth, profit_growth, margin and
    margin_change over 1,049 randomly sampled trades, every confidence interval
    straddling zero at |t| <= 0.89. It is reported because a reviewer asked what
    the company's numbers look like, and because a red flag is a different
    question from an edge -- but `value` stays None and the channel does not
    vote. Giving it one would be giving a weight to a measured null.
    """
    rows = getattr(series, "fund", None) or []
    iso = (day.isoformat() if hasattr(day, "isoformat") else str(day)) \
        if day is not None else date.today().isoformat()
    if not rows:
        return Reading("fundamental", covered=False,
                       note="no filings visible on or before the as-of date")
    f = fundamentals.features_asof(rows, iso)
    if not f:
        # `features_asof` returns {} for two different reasons and the note must
        # not conflate them: nothing published yet, or published but fewer than
        # the five quarters a year-on-year comparison needs. The second is a
        # young listing, not a silent company.
        seen = sum(1 for r in rows
                   if r.get("visible_from") and r["visible_from"] <= iso)
        note = (f"no filing published on or before {iso}" if not seen else
                f"{seen} quarter(s) published by {iso}; the year-ago "
                f"comparison needs 5")
        return Reading("fundamental", covered=False, note=note)
    ev = [f"{k} {v:+.4f}" if isinstance(v, float) else f"{k} {v}"
          for k, v in sorted(f.items())]
    return Reading("fundamental", covered=True, value=None, facts=dict(f),
                   evidence=ev,
                   note="measured flat on 1,049 trades (|t| <= 0.89); "
                        "reported, deliberately unscored")


def sentiment_channel(symbol, day=None):
    """The sentiment analyst, over the exchange announcement corpus.

    `src/ops/sentiment.py` does the work; this wraps its composite onto the
    dossier's scale and carries its coverage flag through honestly. Eleven
    pre-registered hypotheses have been spent on this channel and none adopted,
    so like `fundamental` it is reported and does not vote.
    """
    try:
        s = _sentiment.stock_sentiment(symbol, _as_date(day))
    except Exception as e:                       # a channel outage is not a view
        return Reading("sentiment", covered=False,
                       note=f"channel unavailable: {type(e).__name__}: {e}")
    if not s:
        return Reading("sentiment", covered=False, note="no items in window")
    # These key names are `sentiment.stock_sentiment`'s, read off that function
    # rather than guessed. The first version of this channel guessed "items" and
    # "evidence", neither of which it returns, so the channel reported `no data`
    # on every symbol including strongly-scored ones -- and the selftest passed,
    # because it only ever exercised the empty path. Covered-path assertions
    # below are the fix for that, not the key names.
    comp = s.get("composite")
    n_sig = s.get("n_signal") or 0
    if comp is None:
        return Reading("sentiment", covered=False,
                       note="no items in window")
    if not n_sig:
        return Reading("sentiment", covered=False,
                       note=f"{s.get('n_announcements', 0)} filings and "
                            f"{s.get('n_news', 0)} headlines, none of which "
                            f"scored -- procedural filings are an absence of "
                            f"observation, not a neutral observation")
    ev = []
    for row in (s.get("top") or [])[:4]:
        try:
            score, source, text = row
            ev.append(f"{score:+.0f}  {source}: {text}")
        except (TypeError, ValueError):
            ev.append(str(row)[:120])
    return Reading("sentiment", covered=True, value=None,
                   facts={"composite": comp, "band": s.get("band"),
                          "n_signal": n_sig,
                          "n_announcements": s.get("n_announcements", 0),
                          "n_news": s.get("n_news", 0)},
                   evidence=ev,
                   note="11 hypotheses spent, none adopted "
                        "(ann_tone t=1.71 vs a bar of 2.6); unscored")


def news(symbol, day=None):
    """The news analyst. Forward-only, and that is not a limitation to fix.

    Nobody sells a complete, correctly-timestamped archive of Indian microcap
    press coverage, and scraping one into existence gets you whatever survived
    to today, dated by when you fetched it. `data/news/` accumulates forward
    from the day `newswatch.py` started. So this channel is `backtest_safe=False`
    and the module-level guard in `_selftest` is what keeps that true.
    """
    try:
        ev = _sentiment.news_evidence(symbol, _as_date(day))
    except Exception as e:
        return Reading("news", covered=False, backtest_safe=False,
                       note=f"channel unavailable: {type(e).__name__}: {e}")
    items = list(ev or [])
    if not items:
        return Reading("news", covered=False, backtest_safe=False,
                       note="no headlines in window (the common case on a microcap)")
    heads = []
    for it in items[:6]:
        heads.append(str(it.get("title") if isinstance(it, dict) else it)[:120])
    return Reading("news", covered=True, value=None, backtest_safe=False,
                   facts={"items": len(items)}, evidence=heads,
                   note="forward-only archive; no history, never backtested")


def market_channel(corpus, day=None, symbols=None):
    """The market analyst: what the cross-section was doing, not this stock.

    The gap this closes was a real absence rather than a difference of taste --
    every other channel here is about one stock, so nothing in the dossier could
    say whether a name was strong or merely floating on a strong tape. The
    framework this was adapted from fills the same slot with FRED macro series
    and prediction markets; neither exists for this universe, so the reading is
    computed from the corpus by `market.state`.

    `symbols` is the universe to read, and there is no default: benchmarking a
    microcap book against a set that includes the liquid tercile it refuses to
    buy measures the wrong thing, and the corpus still holds instruments this
    book does not trade (L69). `build()` passes the live tradeable clusters.

    Unscored, like the other two evidence channels. A regime gate is an obvious
    thing to build on top of this and has never been measured here; it would
    need its own pre-registered test before any of it could reach `selection`.
    """
    if corpus is None:
        return Reading("market", covered=False,
                       note="no corpus supplied -- pass one to build() to read "
                            "the market alongside the stock")
    if not symbols:
        return Reading("market", covered=False,
                       note="no universe supplied; this channel will not guess one")
    st = market.state(corpus, _as_date(day), symbols)
    if not st:
        return Reading("market", covered=False,
                       note="no symbol in the universe has enough history on "
                            "this date")
    ev = [f"breadth {100 * st['breadth']:.1f}% of {st['n']} names above their "
          f"own {st['ema_period']}-day EMA"]
    if "dispersion" in st:
        # Key built first: nesting the same quote inside an f-string is a 3.12+
        # feature and this repo runs on the interpreter it finds.
        med = st.get(f"median_{st['window']}d_return")
        ev.append(f"median {st['window']}-day return {med:+.2f}%, "
                  f"cross-sectional dispersion {st['dispersion']:.2f}%")
    ev.append("a tape reading, not a view on this stock -- the bucket has no "
              "regime rule and this does not create one")
    return Reading("market", covered=True, value=None, facts=dict(st),
                   evidence=ev,
                   note="computed from the corpus, not fetched; unscored and "
                        "not gated on")


CHANNELS = ("technical", "fundamental", "sentiment", "news", "market")


@dataclass
class Dossier:
    symbol: str
    as_of: str
    readings: dict

    @property
    def covered(self):
        return [c for c in CHANNELS
                if c in self.readings and self.readings[c].covered]

    @property
    def independent(self):
        """Covered channels that are not a restatement of the selection rule."""
        return [c for c in self.covered if not self.readings[c].prior]

    def render(self):
        """-> the markdown a reviewer reads. Coverage first, always."""
        out = [f"# {self.symbol} — analyst dossier, as of {self.as_of}", ""]
        miss = [c for c in CHANNELS if c not in self.covered]
        out.append(f"**Coverage:** {len(self.covered)}/{len(CHANNELS)} channels"
                   + (f" — no data from: {', '.join(miss)}" if miss else ""))
        out.append(f"**Independent of the selection rule:** "
                   f"{', '.join(self.independent) or 'none'}")
        out.append("")
        for c in CHANNELS:
            r = self.readings.get(c)
            if r is None:
                continue
            tags = []
            if r.prior:
                tags.append("PRIOR — not independent evidence")
            if not r.backtest_safe:
                tags.append("FORWARD-ONLY — no history")
            head = f"## {c}  ({r.band})"
            out.append(head)
            if tags:
                out.append(f"> {'; '.join(tags)}")
            if r.note:
                out.append(f"_{r.note}_")
            if not r.covered:
                out.append("")
                continue
            for e in r.evidence:
                out.append(f"- {e}")
            out.append("")
        out.append("_No composite score is produced here, deliberately — "
                   "see the module docstring._")
        return "\n".join(out)


def build(symbol, day=None, series=None, corpus=None):
    """-> a Dossier. `series` and `corpus` let a caller reuse what it has.

    The corpus is the expensive part (`features.load_corpus` reads every bar of
    every symbol), so a caller assembling five candidates loads it once and
    passes it in. Pass BOTH `corpus` and `series` and nothing is loaded; pass
    only `series` and the market channel reads `no corpus` rather than paying
    for one behind the caller's back.
    """
    iso = (day.isoformat() if hasattr(day, "isoformat") else str(day)) \
        if day is not None else date.today().isoformat()
    if series is None and corpus is None:
        corpus = features.load_corpus(end=iso)
    if series is None:
        series = corpus.get(symbol)
        if series is None:
            raise SystemExit(f"{symbol}: not in the corpus as of {iso}")
    return Dossier(symbol=symbol, as_of=iso, readings={
        "technical": technical(series, day),
        "fundamental": fundamental(series, day),
        "sentiment": sentiment_channel(symbol, day),
        "news": news(symbol, day),
        "market": market_channel(corpus, day, _tradeable(corpus, day)),
    })


def _tradeable(corpus, day):
    """-> the symbols the live strategy would rank on `day`, or None.

    Imported here rather than at module scope: `clusters` resolves to whichever
    strategy `paths` activated, and this module is in `ops/`, which is allowed
    to know that -- `market.py`, which is shared, deliberately is not.
    """
    if corpus is None:
        return None
    try:
        import clusters
        bands = clusters.size_clusters(corpus, day)
    except Exception:
        return None
    return [s for band in bands.values() for s in band]


# --------------------------------------------------------------------------

def _fake_series(n=320, up=True):
    """A synthetic corpus row. The selftest must not need data/raw/, which is
    gitignored and absent from a fresh clone -- a check that only runs on the
    operator's disk is not a check."""
    closes, highs, lows, days = [], [], [], []
    px = 100.0
    for i in range(n):
        px *= 1.002 if up else 0.998
        closes.append(px)
        highs.append(px * 1.01)
        lows.append(px * 0.99)
        days.append(f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}")
    return features.Series(symbol="TEST", days=days, open=list(closes),
                           high=highs, low=lows, close=closes,
                           volume=[1000] * n, turnover=[1e6] * n,
                           deliv_pct=[40.0] * n, surveillance_known=[False] * n,
                           restricted=[False] * n, rs={}, fund=[])


def _selftest():
    # --- bands and the coverage invariant -----------------------------------
    assert band(None) == "no data", "None must never read as a number"
    assert band(0.5) == "positive" and band(-0.5) == "negative"
    assert band(0.0) == "mixed"

    ok = False
    try:
        Reading("x", covered=False, value=0.0)
    except AssertionError:
        ok = True
    assert ok, "an uncovered channel carrying 0.0 is the failure this guards"

    ok = False
    try:
        Reading("x", covered=True, value=1.5)
    except AssertionError:
        ok = True
    assert ok, "a reading outside [-1, 1] must not be constructible"

    # --- technical: covered, and marked as the prior ------------------------
    r = technical(_fake_series(up=True))
    assert r.covered and r.prior, "the technical channel is the selection rule"
    assert r.facts["above_200dma"] is True, r.facts
    assert r.facts["rsi14"] is not None and r.facts["macd"] is not None
    assert r.value == 1.0, r.value

    d = technical(_fake_series(up=False))
    assert d.covered and d.facts["above_200dma"] is False, d.facts
    assert d.value == 0.0, d.value

    short = technical(_fake_series(n=150))
    assert not short.covered and short.value is None, \
        "under 200 bars the gate cannot be evaluated and must say so"

    # --- the as-of cut is a cut, not a filter -------------------------------
    s = _fake_series(n=320)
    cut = technical(s, day="2024-11-01")
    full = technical(s)
    assert cut.facts["close"] != full.facts["close"], \
        "the as-of date must actually truncate the series"

    # --- the technical channel must carry the SCORE's inputs ----------------
    # Not the indicator menu of the framework this was adapted from. The first
    # version reported RSI/MACD/a 250-day high and omitted delivery -- the
    # feature carrying the 1.5 weight -- while claiming to restate the rule.
    for key in ("rs_125d_pct", "deliv_60d_pct", "liq_60d_median_turnover",
                "near_high_125d"):
        assert key in r.facts and r.facts[key] is not None, \
            f"the scored feature {key} is missing from the technical channel"
    assert r.facts["deliv_60d_pct"] == 40.0, r.facts["deliv_60d_pct"]

    # --- unscored channels stay unscored ------------------------------------
    f = fundamental(_fake_series())
    assert not f.covered and f.value is None, "no filings is not a neutral read"

    # --- the sentiment channel must survive a COVERED result ----------------
    # This is the assertion whose absence let the channel ship dead: it bound to
    # key names `stock_sentiment` does not return, so every symbol read `no
    # data`, and a selftest that only exercised the empty path passed anyway. An
    # uncovered-only test cannot tell a working channel from a broken one.
    real = _sentiment.stock_sentiment
    try:
        _sentiment.stock_sentiment = lambda sym, day=None: {
            "symbol": "TESTCO", "company": "Test Co", "as_of": "2026-09-11",
            "announcement_score": 6.0, "news_score": 2.0, "composite": 5.0,
            "band": "Bullish", "n_announcements": 7, "n_news": 3, "n_signal": 5,
            "top": [(8.0, "Order win", "bags Rs 40 crore order")]}
        sr = sentiment_channel("TESTCO", "2026-09-11")
        assert sr.covered, "a scored sentiment result must read as covered"
        assert sr.facts["n_signal"] == 5 and sr.facts["composite"] == 5.0, sr.facts
        assert sr.evidence and "Order win" in sr.evidence[0], sr.evidence
        assert sr.value is None, "sentiment reports; it does not vote"

        # Items present but none scoring is NOT the same as nothing in window,
        # and must not read as a neutral observation.
        _sentiment.stock_sentiment = lambda sym, day=None: {
            "composite": 0.0, "band": "Neutral", "n_announcements": 4,
            "n_news": 0, "n_signal": 0, "top": []}
        q = sentiment_channel("TESTCO", "2026-09-11")
        assert not q.covered and "none of which scored" in q.note, q.note
    finally:
        _sentiment.stock_sentiment = real

    # --- a dossier refuses to produce a composite ---------------------------
    # --- a string date must not crash a channel into "unavailable" ----------
    # The CLI passes --day as a string; the sentiment channels do date
    # arithmetic on it. Before normalisation both reported `channel
    # unavailable: TypeError`, which renders identically to a quiet market.
    assert _as_date("2024-11-01") == date(2024, 11, 1)
    assert _as_date(date(2024, 11, 1)) == date(2024, 11, 1)
    assert _as_date(None) == date.today()
    for ch in (sentiment_channel, news):
        r_str = ch("TESTCO", "2024-11-01")
        assert "TypeError" not in (r_str.note or ""), r_str.note

    # --- covered-and-unscored is a third state, not "no data" ---------------
    assert Reading("x", covered=True, value=None).band == "reported, unscored"
    assert Reading("x", covered=False).band == "no data"
    assert Reading("x", covered=True, value=0.8).band == "positive"

    # --- the market channel refuses to guess a universe ---------------------
    mc = _fake_series()
    assert not market_channel(None, "2024-06-01", ["A"]).covered, \
        "no corpus is not a market reading"
    assert not market_channel({"X": mc}, "2024-06-01", []).covered, \
        "an empty universe must not be read as the whole corpus"
    fake_corpus = {f"S{i}": _fake_series(up=(i % 2 == 0)) for i in range(8)}
    mk = market_channel(fake_corpus, "2024-11-01", sorted(fake_corpus))
    assert mk.covered and mk.value is None, "market reports; it does not vote"
    assert mk.facts["n"] == 8 and mk.facts["breadth"] == 0.5, mk.facts
    assert not mk.prior, "the market is not a restatement of the stock's rank"

    dos = Dossier("TEST", "2024-06-01", {
        "technical": technical(_fake_series()),
        "fundamental": fundamental(_fake_series()),
    })
    assert not hasattr(dos, "composite") and not hasattr(dos, "score"), \
        "a composite score is the thing this module exists to refuse"
    assert dos.independent == [], \
        "technical alone is the prior; it is not independent evidence"
    txt = dos.render()
    assert "Coverage: 1/5" in txt.replace("**", ""), txt[:300]
    assert "PRIOR" in txt and "No composite score" in txt

    # --- THE guarantee: no backtest can read this ---------------------------
    # The news channel has no history, so anything reading this module during a
    # backtest would be reading the future. Asserted beside the thing it
    # protects, the same way newswatch.py asserts it.
    pat = re.compile(r"^\s*(?:import\s+dossier|from\s+dossier\s+import)",
                     re.MULTILINE)
    offenders = []
    for dsub in ("src/research", "src/strategies"):
        for p in sorted((ROOT / dsub).rglob("*.py")):
            if pat.search(p.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, \
        f"a backtest imports the forward news channel, which has no history: {offenders}"

    print("dossier selftest ok (5 channels; no composite; no backtest imports it)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("symbol", nargs="?")
    ap.add_argument("--day", default=None, help="as-of date, YYYY-MM-DD")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if not a.symbol:
        ap.error("a symbol is required (or --selftest)")
    print(build(a.symbol.upper(), a.day).render())


if __name__ == "__main__":
    main()
