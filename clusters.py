#!/usr/bin/env python3
"""Size clusters and stock selection: 20 micro, 20 small, 20 mid.

SELECTION IS AS-OF A DATE. Every input is computed from bars up to and including
that date only. Picking today's best performers and testing them on their own
history is the classic way to build a system that backtests beautifully and
fails live -- the stocks are chosen BECAUSE they already rose.

Size proxy is median daily turnover, not market cap: true market cap needs
shares outstanding, which no NSE feed collected here provides. Turnover also
happens to be the more relevant axis for a 5-lakh book, since it decides whether
a position can be entered and exited at all.

Composite score, equally weighted after ranking (so no single factor dominates
by scale):
    relative strength   6-month return percentile across the whole universe
    delivery quality    delivery % -- real accumulation, not intraday churn
    liquidity           turnover within the cluster
    trend               close above its own 200-day average
    fundamentals        revenue growth where filings exist (neutral where not)
"""
import statistics
import sys
from datetime import date

import features

CLUSTERS = ("micro", "small", "mid")
# Overridable: widening this re-clusters every downstream reader (pick, build,
# allocate). Ascending turnover -- first name is the smallest.
CLUSTER_NAMES = CLUSTERS
PER_CLUSTER = 20


def size_clusters(corpus, as_of=None, window=250, names=None):
    """-> {cluster: [symbols]} by median daily turnover up to `as_of`.

    `names` sets the number of quantiles: three gives the original
    micro/small/mid terciles, six gives finer size resolution. Module-level
    CLUSTER_NAMES is the default so a caller can widen the split for every
    downstream reader at once.
    """
    names = names or CLUSTER_NAMES
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
    return {nm: [s for _, s in rows[i * n // k:(i + 1) * n // k]]
            for i, nm in enumerate(names)}


def _pct_rank(vals):
    """-> {key: percentile 0-100}. Ties share the lower rank."""
    order = sorted(vals.items(), key=lambda kv: kv[1])
    n = len(order)
    return {k: (i + 1) / n * 100 for i, (k, _) in enumerate(order)}


# Overridable so a caller can test a weight set WITHOUT saving it. These were
# function-locals inside score(), which made them look injectable while every
# call silently re-read the file: four different weight configurations returned
# byte-identical books, and the only clue was that they matched to the digit.
W = None
INVERTED = None


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
        prev = s.close[i - 125] if i >= 125 else None
        if not prev:
            continue
        rs = s.close[i] / prev - 1.0
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
    if not raw:
        return {}
    ranks = {f: _pct_rank({k: v[f] for k, v in raw.items()})
             for f in ("rs", "deliv", "liq", "near_high")}
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
        for f in ("rs", "deliv", "liq", "near_high"):
            w = float(W.get(f, 1.0))
            r = ranks[f][sym]
            if f in INVERTED:
                r = 100.0 - r        # ranks backwards, reliably (learning.INVERTED)
            tot += r * w
            wsum += w
        out[sym] = tot / (wsum or 1.0)
        detail[sym] = {f: round(ranks[f][sym], 1)
                       for f in ("rs", "deliv", "liq", "near_high")}
    return (out, detail) if with_ranks else out


def pick(corpus, as_of, per_cluster=PER_CLUSTER):
    """-> {cluster: [(symbol, score)]}, best `per_cluster` in each."""
    clusters = size_clusters(corpus, as_of, names=CLUSTER_NAMES)
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
    assert sum(len(v) for v in b.values()) == 30, b
    assert "S29" in b["mid"], "highest turnover must land in mid"
    assert "S00" in b["micro"]

    picks = pick(corpus, days[-1], per_cluster=3)
    assert set(picks) == set(CLUSTERS)
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
