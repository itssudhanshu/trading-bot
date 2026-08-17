#!/usr/bin/env python3
"""Pivot the date-major corpus into per-symbol series, plus the indicator
primitives the strategy vocabulary is built from.

Stdlib only -- 1.98M bars load in under 7s, so pandas would buy nothing here.

Gaps are real: a symbol that did not trade on a date simply has no bar. Series
are per-symbol and self-aligned, so indicators are computed over the bars that
actually exist. A symbol that delists just stops -- which is the correct
point-in-time behaviour and the reason this beats screening today's index.
"""
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import universe

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"


@dataclass
class Series:
    symbol: str
    days: list = field(default_factory=list)
    open: list = field(default_factory=list)
    high: list = field(default_factory=list)
    low: list = field(default_factory=list)
    close: list = field(default_factory=list)
    volume: list = field(default_factory=list)
    turnover: list = field(default_factory=list)
    deliv_pct: list = field(default_factory=list)
    surveillance_known: list = field(default_factory=list)
    restricted: list = field(default_factory=list)   # ASM / GSM / F&O ban that day
    rs: dict = field(default_factory=dict)      # lookback -> [percentile per bar]
    fund: list = field(default_factory=list)    # as-of filing timeline (see fundamentals.py)

    def __len__(self):
        return len(self.days)

    def index_of(self, day: date):
        try:
            return self.days.index(day)
        except ValueError:
            return None


def trading_days(start=None, end=None):
    days = sorted(date.fromisoformat(p.name) for p in RAW.iterdir()
                  if p.is_dir() and (p / "bhavcopy_delivery.csv").exists())
    return [d for d in days if (start is None or d >= start) and (end is None or d <= end)]


# One cached corpus, keyed on the DATA rather than the clock. Building it costs
# ~18s and nothing cached it, so a Telegram command that touched the corpus
# twice -- most of them do, once in _lag_note and once in the body -- took 40
# seconds to answer. The long-running listener paid it on every message.
#
# The key includes the day count and the newest day, so a snapshot or a catchup
# invalidates it automatically. A time-based TTL would not: the whole point is
# that the corpus changes when NSE publishes, not when a timer expires.
# Only the last corpus is kept -- it is ~2M bars and a multi-entry cache here
# would trade an 18s wait for an unbounded resident set.
_CORPUS = None


def load_corpus(start=None, end=None, min_bars=200, require_master=True) -> dict:
    """-> {symbol: Series}, chronological. Symbols with too little history are
    dropped: an indicator seeded on 20 bars is noise, not signal.

    Refuses to build a corpus with no non-equity denylist available. That state
    is not visibly broken -- it loads, prints a plausible symbol count, and
    quietly returns a different universe (2,740 symbols instead of 2,486, the
    surplus being ETFs and liquid funds), so every downstream number is wrong
    with nothing to show for it. See L36.
    """
    days_now = trading_days(start, end)
    key = (start, end, min_bars, require_master,
           len(days_now), days_now[-1] if days_now else None)
    global _CORPUS
    if _CORPUS is not None and _CORPUS[0] == key:
        return _CORPUS[1]

    if require_master and universe.master_snapshot() is None:
        raise RuntimeError(
            "no snapshot holds equity_master.csv, so the non-equity denylist "
            "would be empty and ETFs would silently enter the corpus. "
            "backfill.py fetches bhavcopy only -- fetch EQUITY_L.csv into a "
            "snapshot directory that also has a bhavcopy:\n"
            "    python -c \"from snapshot import SOURCES, fetch, RAW; "
            "s,b = fetch(SOURCES['equity_master'][0]); "
            "(RAW/'<newest-day>'/'equity_master.csv').write_bytes(b)\"\n"
            "Pass require_master=False only for a deliberately unfiltered load."
        )
    out = {}
    for d in days_now:
        for sym, b in universe.load(d).items():
            s = out.get(sym)
            if s is None:
                s = out[sym] = Series(symbol=sym)
            s.days.append(d)
            s.open.append(b.open)
            s.high.append(b.high)
            s.low.append(b.low)
            s.close.append(b.close)
            s.volume.append(b.volume)
            s.turnover.append(b.turnover)
            s.deliv_pct.append(b.deliv_pct)
            s.surveillance_known.append(b.surveillance_known)
            s.restricted.append(b.restricted)
    out = {k: v for k, v in out.items() if len(v) >= min_bars}
    attach_fundamentals(out)
    out = attach_rs(out)
    _CORPUS = (key, out)
    return out


# --- indicator primitives -------------------------------------------------
# All return a list aligned to the input, with None where there is not yet
# enough history. Callers must treat None as "unknown", never as zero.

def sma(xs, n):
    out, run = [None] * len(xs), 0.0
    for i, x in enumerate(xs):
        run += x
        if i >= n:
            run -= xs[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def ema(xs, n):
    """Seeded with the SMA of the first n bars, then standard smoothing."""
    out = [None] * len(xs)
    if len(xs) < n:
        return out
    prev = sum(xs[:n]) / n
    out[n - 1] = prev
    k = 2.0 / (n + 1)
    for i in range(n, len(xs)):
        prev = xs[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def true_range(high, low, close):
    out = [None] * len(high)
    for i in range(len(high)):
        if i == 0:
            out[i] = high[i] - low[i]
        else:
            pc = close[i - 1]
            out[i] = max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc))
    return out


def atr(high, low, close, n=14):
    """Wilder's smoothing, the standard for ATR-based stops."""
    tr = true_range(high, low, close)
    out = [None] * len(tr)
    if len(tr) < n + 1:
        return out
    prev = sum(tr[1:n + 1]) / n
    out[n] = prev
    for i in range(n + 1, len(tr)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def rolling_max(xs, n):
    """Trailing window INCLUDING the current bar. For a breakout test compare
    against index i-1, or today's own high trivially satisfies it."""
    return [max(xs[max(0, i - n + 1):i + 1]) if i >= n - 1 else None
            for i in range(len(xs))]


def rolling_min(xs, n):
    return [min(xs[max(0, i - n + 1):i + 1]) if i >= n - 1 else None
            for i in range(len(xs))]


def zscore(xs, n):
    """Trailing z-score ending at i. None when the window is flat (std 0),
    because 'infinitely unusual' is not a useful signal."""
    out = [None] * len(xs)
    for i in range(n - 1, len(xs)):
        w = xs[i - n + 1:i + 1]
        sd = statistics.pstdev(w)
        if sd > 0:
            out[i] = (xs[i] - statistics.fmean(w)) / sd
    return out


def rsi(closes, n=14):
    """Wilder's RSI. None until seeded -- never 50 as a stand-in for unknown."""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(ch, 0.0)) / n
        al = (al * (n - 1) + max(-ch, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def slope_up(xs, lookback):
    """True where xs[i] > xs[i-lookback]. None-safe."""
    out = [None] * len(xs)
    for i in range(lookback, len(xs)):
        a, b = xs[i], xs[i - lookback]
        out[i] = (a > b) if (a is not None and b is not None) else None
    return out


RS_LOOKBACKS = (20, 60, 125, 250)      # ~1m, 3m, 6m, 12m momentum windows


def attach_rs(corpus, lookbacks=RS_LOOKBACKS):
    """Attach cross-sectional relative-strength percentiles to each Series.

    RS rank is the classic momentum input and the biggest gap in the vocabulary:
    "up 20% in three months" means nothing until you know the other 2,299 names
    were up 30%. Computed per date across the whole universe -- the same
    cross-sectional access pattern breadth needs, and the reason a per-symbol
    data source cannot serve this system.

    Stored on the Series so no call site has to thread another argument through.
    """
    for lb in lookbacks:
        by_day = {}
        for s_ in corpus.values():
            r = [None] * len(s_)
            for i in range(lb, len(s_)):
                prev = s_.close[i - lb]
                if prev:
                    r[i] = s_.close[i] / prev - 1.0
            s_._ret = getattr(s_, "_ret", {})
            s_._ret[lb] = r
            for i, d in enumerate(s_.days):
                if r[i] is not None:
                    by_day.setdefault(d, []).append((r[i], s_.symbol))

        ranks = {}
        for d, pairs in by_day.items():
            pairs.sort()
            n = len(pairs)
            ranks[d] = {sym: (k + 1) / n * 100 for k, (_, sym) in enumerate(pairs)}

        for s_ in corpus.values():
            s_.rs = getattr(s_, "rs", {})
            s_.rs[lb] = [ranks.get(d, {}).get(s_.symbol) for d in s_.days]
    return corpus


def attach_fundamentals(corpus):
    """Attach each symbol's as-of filing timeline. Missing is normal -- 15% of
    the universe has no filings at all and banks use a taxonomy the parser does
    not read, so predicates must treat absence as UNKNOWN (None), never as a
    failed test. Absent data that reads as False would silently exclude a
    seventh of the universe while looking like selectivity."""
    try:
        import fundamentals
    except Exception:
        return corpus
    for sym, s in corpus.items():
        s.fund = fundamentals.timeline(sym)
    return corpus


def breadth(corpus, period=50) -> dict:
    """Cross-sectional market health: fraction of symbols above their own EMA,
    per date. This is the one measure that needs the whole universe at once --
    exactly the access pattern a per-symbol data source cannot serve."""
    above, total = {}, {}
    for s in corpus.values():
        e = ema(s.close, period)
        for i, d in enumerate(s.days):
            if e[i] is None:
                continue
            total[d] = total.get(d, 0) + 1
            if s.close[i] > e[i]:
                above[d] = above.get(d, 0) + 1
    return {d: above.get(d, 0) / n for d, n in total.items() if n}


def _selftest():
    from datetime import timedelta

    # The corpus cache must invalidate when NSE publishes a new day. A cache
    # that never invalidates serves yesterday's prices to a live book, which
    # would be far worse than the 18s it saves.
    global RAW, _CORPUS
    import tempfile
    _oraw, _ocache = RAW, _CORPUS
    try:
        with tempfile.TemporaryDirectory() as td:
            RAW = Path(td)
            _CORPUS = None
            assert trading_days() == [], "fixture is not empty"
            k1 = (None, None, 200, False, 0, None)
            _CORPUS = (k1, {"sentinel": 1})
            assert load_corpus(require_master=False) == {"sentinel": 1}, \
                "cache did not answer for an unchanged corpus"
            # publish a day; the key must change and the sentinel must not survive
            d = RAW / "2026-01-02"
            d.mkdir()
            (d / "bhavcopy_delivery.csv").write_text(
                "SYMBOL,SERIES,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,"
                "PREV_CLOSE,TTL_TRD_QNTY,TURNOVER_LACS,DELIV_QTY,DELIV_PER\n")
            got = load_corpus(require_master=False)
            assert got != {"sentinel": 1}, \
                "a new trading day did not invalidate the corpus cache"
    finally:
        RAW, _CORPUS = _oraw, _ocache

    xs = [1, 2, 3, 4, 5, 6, 7, 8]
    assert sma(xs, 3)[:3] == [None, None, 2.0], sma(xs, 3)[:3]
    assert sma(xs, 3)[-1] == 7.0

    e = ema(xs, 3)
    assert e[:2] == [None, None] and e[2] == 2.0          # seed = mean(1,2,3)
    assert abs(e[3] - (4 * 0.5 + 2.0 * 0.5)) < 1e-12      # k = 2/(3+1) = 0.5

    h = [10, 11, 12, 11, 13]
    l = [9, 10, 10, 9, 11]
    c = [9.5, 10.5, 11.5, 10, 12.5]
    tr = true_range(h, l, c)
    assert tr[0] == 1                                     # first bar: h-l
    assert tr[2] == max(12 - 10, abs(12 - 10.5), abs(10 - 10.5)) == 2.0

    assert rolling_max([1, 5, 3, 2], 2) == [None, 5, 5, 3]
    assert rolling_min([1, 5, 3, 2], 2) == [None, 1, 3, 2]

    z = zscore([1, 1, 1, 1], 4)
    assert z[-1] is None, "flat window must be None, not a divide-by-zero"
    z2 = zscore([1, 2, 3, 10], 4)
    assert z2[-1] > 1.0, z2

    r = rsi([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25], 14)
    assert r[:14] == [None] * 14, "RSI must be None before it is seeded"
    assert r[14] == 100.0, r[14]                       # all gains, no losses
    down = rsi(list(range(30, 10, -1)), 14)
    assert down[15] < 5.0, down[15]                    # all losses
    assert slope_up([1, 2, 3, 4], 2) == [None, None, True, True]
    assert slope_up([4, 3, 2, 1], 2)[-1] is False

    # ATR must be None until it has n+1 bars, never 0
    a = atr(h, l, c, n=3)
    assert a[:3] == [None, None, None] and a[3] is not None, a

    # breadth over a 2-symbol toy corpus
    d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
    up = Series("UP", [d1, d2], close=[1, 100])
    dn = Series("DN", [d1, d2], close=[1, 1])
    for s in (up, dn):
        s.high, s.low, s.open = s.close, s.close, s.close
    b = breadth({"UP": up, "DN": dn}, period=2)
    assert b[d2] == 0.5, b                                 # one of two above EMA

    # RS rank: the stronger name must rank above the weaker one on the same date
    days3 = [date(2024, 1, 1) + timedelta(days=k) for k in range(30)]
    strong, weak = Series("STRONG", list(days3)), Series("WEAK", list(days3))
    for k in range(30):
        strong.close.append(100 + k * 5)
        weak.close.append(100 + k * 0.1)
    for s_ in (strong, weak):
        s_.high, s_.low, s_.open = s_.close, s_.close, s_.close
    attach_rs({"STRONG": strong, "WEAK": weak}, lookbacks=(20,))
    assert strong.rs[20][-1] > weak.rs[20][-1], (strong.rs[20][-1], weak.rs[20][-1])
    assert strong.rs[20][-1] == 100.0, strong.rs[20][-1]   # top of two
    assert strong.rs[20][5] is None, "no rank before the lookback fills"

    # --- a corpus must never be built without a non-equity denylist ----------
    # The failure this guards is silent, not loud: an absent equity_master.csv
    # yields an EMPTY denylist, and an empty denylist looks exactly like one
    # with nothing to exclude. A rebuilt machine got 2,740 symbols instead of
    # 2,486 and every downstream number moved with it (L36).
    real = universe.master_snapshot
    try:
        universe.master_snapshot = lambda: None
        try:
            load_corpus()
            raise AssertionError("load_corpus built a corpus with no denylist source")
        except RuntimeError as e:
            assert "equity_master" in str(e), e
        # the opt-out must still work, for a deliberately unfiltered load
        assert load_corpus.__defaults__[-1] is True, "require_master must default to on"
    finally:
        universe.master_snapshot = real

    print("features selftest ok")


if __name__ == "__main__":
    import sys
    import time
    if "--selftest" in sys.argv:
        _selftest()
    else:
        t0 = time.time()
        c = load_corpus()
        print(f"{len(c)} symbols with >=200 bars, loaded in {time.time()-t0:.1f}s")
        longest = max(c.values(), key=len)
        print(f"longest series: {longest.symbol} ({len(longest)} bars)")
        b = breadth(c)
        recent = sorted(b)[-5:]
        print("breadth (% above 50 EMA):")
        for d in recent:
            print(f"  {d}  {b[d]*100:5.1f}%")
