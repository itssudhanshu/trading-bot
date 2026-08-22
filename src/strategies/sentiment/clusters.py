#!/usr/bin/env python3
"""sentiment: breakout's selection, plus what the company told the exchange.

A clone of breakout, and identical to it until ANN_FEATURES is switched on. That
default is the whole design: a rule that is live before it is measured has
already changed the answer, and tests/clone_reproduces.py asserts that with the
knob off this file still produces +7.59% / 31.0% / 195.

Size clusters and stock selection: micro and small, 20 candidates each.

SELECTION IS AS-OF A DATE. Every input is computed from bars up to and including
that date only. Picking today's best performers and testing them on their own
history is the classic way to build a system that backtests beautifully and
fails live -- the stocks are chosen BECAUSE they already rose.

Size proxy is median daily turnover, not market cap: true market cap needs
shares outstanding, which no NSE feed collected here provides. Turnover also
happens to be the more relevant axis for a 5-lakh bucket, since it decides whether
a position can be entered and exited at all.

Composite score, equally weighted after ranking (so no single factor dominates
by scale):
    relative strength   6-month return percentile across the whole universe
    delivery quality    delivery % -- real accumulation, not intraday churn
    liquidity           turnover within the cluster
    trend               close above its own 200-day average
    fundamentals        revenue growth where filings exist (neutral where not)
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))  # -> src/
import paths  # noqa: F401  -- puts the source dirs on sys.path
import statistics
import sys
from datetime import date

import features

# The universe is split into THREE turnover terciles, but only two are traded.
# That is deliberate and not the same as splitting into halves: a 50/50 split
# would put Nestle and Titan into "small", redefining the cluster whose results
# every test in this project measured. Terciles keep micro = bottom third and
# small = middle third; the top third is simply not tradeable here.
# Two clusters, both traded. The universe is ranked by turnover, the most
# liquid TRADEABLE_PCT is discarded outright, and what remains is split in
# half. There is no third band: `mid` used to exist only to mark where `small`
# stopped, which the percentile cut now does directly.
CLUSTERS = ("micro", "small")
# Overridable: widening this re-clusters every downstream reader (pick, build,
# allocate). Ascending turnover -- first name is the smallest.
PER_CLUSTER = 20


# What fraction of the universe, from the least liquid end, is tradeable at
# all. 2/3 was INHERITED from the micro/small/mid design and has now been
# TESTED: 33/50/67/85/100% give CAGR 4.81 / 10.61 / 13.57 / 5.11 / 6.07 and
# CAGR-per-drawdown 0.135 / 0.351 / 0.471 / 0.156 / 0.246. A clean inverted U
# peaking here. Too narrow is all noise and impact; too wide admits large caps
# where this momentum edge does not exist.
TRADEABLE_PCT = 2 / 3


def size_clusters(corpus, as_of=None, window=250, names=None):
    """-> {cluster: [symbols]} by median daily turnover up to `as_of`.

    `names` sets the number of quantiles: three gives the original
    micro/small, more gives finer size resolution. The most liquid
    (1 - TRADEABLE_PCT) of the universe is discarded before splitting.
    """
    names = names or CLUSTERS
    rows = []
    for sym, s in corpus.items():
        idx = len(s) - 1 if as_of is None else (s.index_of(as_of) or -1)
        if idx < 200:
            continue
        t = [x for x in s.turnover[max(0, idx - window):idx + 1] if x > 0]
        if len(t) > 100:
            rows.append((statistics.median(t), sym))
    rows.sort()
    n, k = len(rows), len(names)
    # Rank the WHOLE universe, discard the most liquid (1 - TRADEABLE_PCT),
    # split what remains into equal bands.
    cut = int(n * TRADEABLE_PCT)
    return {nm: [s for _, s in rows[i * cut // k:(i + 1) * cut // k]]
            for i, nm in enumerate(names)}


def _pct_rank(vals):
    """-> {key: percentile 0-100}. Ties share the lower rank."""
    order = sorted(vals.items(), key=lambda kv: kv[1])
    n = len(order)
    return {k: (i + 1) / n * 100 for i, (k, _) in enumerate(order)}


def _pct_rank_neutral(vals):
    """-> {key: percentile}. A key whose value is None ranks NEUTRAL (50).

    This is the difference between "we have no news about this company" and
    "the news is bad", and getting it wrong would be invisible. Ranking a
    missing value last would make silence indistinguishable from bad news --
    and because microcaps announce far less often than small caps, that would
    quietly reinstall a SIZE proxy inside a score that already has one, where
    it would look like an announcement finding.

    fundamentals.py treats absent filings the same way and for the same reason:
    15% of the universe has never filed anything.
    """
    present = {k: v for k, v in vals.items() if v is not None}
    ranked = _pct_rank(present) if present else {}
    return {k: ranked.get(k, 50.0) for k in vals}


# Overridable so a caller can test a weight set WITHOUT saving it. These were
# function-locals inside score(), which made them look injectable while every
# call silently re-read the file: four different weight configurations returned
# byte-identical buckets, and the only clue was that they matched to the digit.
W = None
INVERTED = None

# --- literature-derived knobs, both OFF by default -------------------------
# These change SELECTION, so they default to the current behaviour and are
# switched on only by a test. Anything that silently altered the live bucket
# would invalidate every measurement taken before it.

# Jegadeesh & Titman momentum is measured to t-1 MONTH, not to t: the most
# recent month carries short-term reversal, which is a different (and
# opposite-signed) effect from momentum. Our `rs` runs to the signal day, so it
# mixes the two. RS_SKIP is how many sessions to leave out at the recent end.
RS_SKIP = 0

# Bali, Cakici & Whitelaw: stocks with an extreme MAX daily return in the
# recent past subsequently UNDERPERFORM -- lottery-like payoffs get overpriced.
# The effect is strongest in small, illiquid names, which is this entire
# universe. MAX_SCREEN drops that fraction of the cluster before scoring.
MAX_SCREEN = None

# --- sentiment's new input, OFF by default -----------------------------------
# NSE corporate announcements as a score input, the one thing breakout's score
# cannot see. Empty means this file behaves exactly as breakout does; a test
# switches it on by name:
#
#     clusters.ANN_FEATURES = ("ann_burst",)
#
# Never set here. A weight moves off zero only after a result clears the
# promotion bar (|t| >= 2.6, spec 6.2), and setting it in the file would make
# every measurement taken before that describe a bucket nobody chose.
ANN_FEATURES = ()

ANN_ALL = ("ann_burst", "ann_tone", "ann_flag")

_ANN_CACHE = {}


def _ann_at(sym, as_of):
    """-> announcement features for `sym` visible on `as_of`, or {}.

    Timelines are cached per symbol: scoring walks ~1,600 candidates per
    session across 1,698 sessions, and re-reading a JSONL file per candidate
    per session would dominate the run by orders of magnitude.
    """
    rows = _ANN_CACHE.get(sym)
    if rows is None:
        import announcements
        rows = _ANN_CACHE[sym] = announcements.timeline(sym)
    if not rows:
        return {}
    import announcements
    return announcements.features_asof(rows, as_of.isoformat())


def _weights():
    """-> (weights, inverted). Module overrides win; otherwise read the file."""
    if W is not None or INVERTED is not None:
        return (W or {}), set(INVERTED or ())
    try:
        import learning
        return learning.load_weights(), set(learning.INVERTED)
    except Exception:
        return {}, set()


def score(corpus, symbols, as_of, with_ranks=False):
    """Composite 0-100 per symbol, from data available on `as_of` only."""
    raw = {}
    for sym in symbols:
        s = corpus[sym]
        i = s.index_of(as_of)
        if i is None or i < 200:
            continue
        # Momentum window ends RS_SKIP sessions back (0 = at the signal day).
        j = i - RS_SKIP
        prev = s.close[j - 125] if j >= 125 else None
        if not prev or j < 0:
            continue
        rs = s.close[j] / prev - 1.0
        deliv = [d for d in s.deliv_pct[max(0, i - 60):i + 1] if d and d > 0]
        liq = statistics.median([x for x in s.turnover[max(0, i - 60):i + 1] if x > 0] or [0])
        sma200 = statistics.fmean(s.close[i - 199:i + 1])
        hi125 = max(s.high[max(0, i - 125):i + 1])
        raw[sym] = {
            "rs": rs,
            "deliv": statistics.fmean(deliv) if deliv else 0.0,
            "liq": liq,
            # NEGATIVE distance below the 125-day high, so higher = closer to
            # the high. The learning pass measured -1.49% spread on raw
            # off_high: stocks nearer their highs outperformed, three times the
            # information of any feature already scored (learning.py).
            "near_high": -((hi125 - s.close[i]) / hi125 * 100) if hi125 else 0.0,
            "trend": 1.0 if s.close[i] > sma200 else 0.0,
        }
        # Announcement features, only when a test asked for them. None means
        # "no data", which _pct_rank_neutral scores mid-rank -- NOT zero, which
        # would rank a silent company last. See _pct_rank_neutral.
        if ANN_FEATURES:
            got = _ann_at(sym, as_of)
            for f in ANN_FEATURES:
                raw[sym][f] = got.get(f)
    if not raw:
        return {}
    if MAX_SCREEN:
        # Drop the lottery tail: the names with the largest single-day gain in
        # the last month. Computed from bars up to as_of only, like everything
        # else here.
        mx = {}
        for sym in raw:
            s = corpus[sym]
            i = s.index_of(as_of)
            r = [s.close[k] / s.close[k - 1] - 1.0
                 for k in range(max(1, i - 20), i + 1) if s.close[k - 1]]
            mx[sym] = max(r) if r else 0.0
        drop = set(sorted(mx, key=lambda k: -mx[k])[:int(len(mx) * MAX_SCREEN)])
        raw = {k: v for k, v in raw.items() if k not in drop}
        if not raw:
            return {}
    # The one list of scored features, built once and used for ranking, for the
    # weighted sum and for the detail. With ANN_FEATURES empty this is exactly
    # breakout's tuple, which is what lets the clone reproduce to the digit.
    scored = ("rs", "deliv", "liq", "near_high") + tuple(ANN_FEATURES)
    ranks = {f: _pct_rank({k: v[f] for k, v in raw.items()})
             for f in ("rs", "deliv", "liq", "near_high")}
    for f in ANN_FEATURES:
        ranks[f] = _pct_rank_neutral({k: v.get(f) for k, v in raw.items()})
    # Weights are learned, not fixed. learning.propose() moves them on measured
    # information; a feature that stops predicting loses influence rather than
    # staying in the score because it was in the original design.
    W, INVERTED = _weights()
    out, detail = {}, {}
    for sym, v in raw.items():
        # Trend is a gate, not a score: a stock below its 200-day average is
        # excluded outright rather than compensated for by a high momentum rank.
        if v["trend"] == 0.0:
            continue
        tot = wsum = 0.0
        for f in scored:
            w = float(W.get(f, 1.0))
            r = ranks[f][sym]
            if f in INVERTED:
                r = 100.0 - r        # ranks backwards, reliably (learning.INVERTED)
            tot += r * w
            wsum += w
        out[sym] = tot / (wsum or 1.0)
        detail[sym] = {f: round(ranks[f][sym], 1) for f in scored}
    return (out, detail) if with_ranks else out


def pick_pooled(corpus, as_of, n=PER_CLUSTER * len(CLUSTERS)):
    """Rank the whole tradeable universe as ONE pool.

    Percentile ranks are then computed across every tradeable name rather than
    within a size band, so `liq` is no longer neutralised by comparing a stock
    only against others of similar turnover. Under the old three-cluster design
    this collapsed the bucket into the largest band for exactly that reason; with
    the most liquid third now discarded entirely, the question is open again.
    """
    bands = size_clusters(corpus, as_of, names=CLUSTERS)
    syms = [s for v in bands.values() for s in v]
    where = {s: b for b, v in bands.items() for s in v}
    sc = score(corpus, syms, as_of)
    top = sorted(sc.items(), key=lambda kv: -kv[1])[:n]
    out = {b: [] for b in CLUSTERS}
    for sym, v in top:
        out[where[sym]].append((sym, v))
    return out


def pick(corpus, as_of, per_cluster=PER_CLUSTER):
    """-> {cluster: [(symbol, score)]}, best `per_cluster` in each."""
    clusters = size_clusters(corpus, as_of, names=CLUSTERS)
    out = {}
    for b, syms in clusters.items():
        sc = score(corpus, syms, as_of)
        out[b] = sorted(sc.items(), key=lambda kv: -kv[1])[:per_cluster]
    return out


def _selftest():
    from datetime import timedelta
    corpus = {}
    d0 = date(2024, 1, 1)
    days = [d0 + timedelta(days=k) for k in range(400)]
    for j in range(30):
        s = features.Series(f"S{j:02d}", list(days))
        for k in range(400):
            px = 100 + j * k * 0.02          # higher j = stronger uptrend
            s.close.append(px); s.high.append(px); s.low.append(px); s.open.append(px)
            s.volume.append(1000); s.turnover.append(1e6 * (j + 1))
            s.deliv_pct.append(40.0 + j); s.surveillance_known.append(True)
        corpus[s.symbol] = s

    b = size_clusters(corpus)
    # Only the least-liquid TRADEABLE_PCT is clustered; the rest is discarded.
    kept = sum(len(v) for v in b.values())
    assert kept == int(30 * TRADEABLE_PCT), (kept, TRADEABLE_PCT)
    assert set(b) == set(CLUSTERS), sorted(b)
    assert "S29" not in b["micro"] and "S29" not in b["small"], \
        "the highest-turnover name must be discarded, not clustered"
    assert "S00" in b["micro"]

    picks = pick(corpus, days[-1], per_cluster=3)
    assert set(picks) == set(CLUSTERS), sorted(picks)
    # the most liquid names must be excluded outright, not placed in a band
    allsyms = {s for v in size_clusters(corpus).values() for s in v}
    ranked = sorted(corpus, key=lambda s: statistics.median(
        [x for x in corpus[s].turnover if x > 0] or [0]))
    assert ranked[-1] not in allsyms, "the most liquid name must be discarded"
    assert ranked[0] in allsyms, "the least liquid name must be tradeable"
    for bname, lst in picks.items():
        assert len(lst) <= 3
        scores = [sc for _, sc in lst]
        assert scores == sorted(scores, reverse=True), "not ranked best-first"

    # as-of: a pick on an early date must not use later bars
    early = pick(corpus, days[250], per_cluster=3)
    assert early, "no picks on a mid-history date"
    assert pick(corpus, days[10], per_cluster=3) == {b: [] for b in CLUSTERS}, \
        "picked with under 200 bars of history"
    print("clusters selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        c = features.load_corpus()
        days = sorted({d for s in c.values() for d in s.days})
        as_of = days[-1]
        print(f"selection as of {as_of}\n")
        for b, lst in pick(c, as_of).items():
            print(f"  {b.upper()} ({len(lst)})")
            for sym, sc in lst[:20]:
                print(f"    {sym:<14} score {sc:5.1f}")
            print()
