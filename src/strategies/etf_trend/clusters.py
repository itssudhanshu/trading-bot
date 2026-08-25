#!/usr/bin/env python3
"""Fund universe, liquidity tiers and ranking inputs for the trend book.

STRATEGY CONTEXT (read this before tuning anything): the rules here were
PRE-REGISTERED in src/research/trend_fund_test.py BEFORE its backtest ran
(batch 20260824-trendfund2), and that backtest FAILED its promotion bar
(+1.04% +/- 1.08% per trade; edge vs control t = +1.19). This book exists to
generate FORWARD evidence for a directionally-consistent but unresolved
hypothesis, not because a backtest earned it. Every constant below is carried
over verbatim from that registration. Changing one after seeing forward
trades would make this book a noise search with a ledger.

UNIVERSE. Every symbol universe.non_equity_symbols() classifies as
non-equity -- still-trading funds through the snapshot union, delisted funds
through data/non_equity_history.json -- with OHLCV built from the raw
bhavcopies (turnover in lacs -> rupees, mirroring universe.load).

ELIGIBILITY on a date (all point-in-time):
  history    >= HISTORY_MIN sessions up to the date;
  split      no corporate-action bar inside SPLIT_LOOKBACK sessions. Raw
             closes are unadjusted; an action inside the momentum or trend
             window corrupts every feature this book reads. Detector is the
             split-audit rule: |1-day move| > 25%, previous bar inside bands,
             next close persists within 25%, and no calendar gap > 7 days to
             the previous bar (that last clause separates actions from
             suspension gaps -- L71).
  liquidity  median daily turnover over LIQ_WINDOW sessions ranks in the top
             UNIVERSE_TOP funds of the day.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))  # -> src/
import paths  # noqa: F401  -- puts the source dirs on sys.path

import csv
import io
import statistics
import sys
from datetime import date

import features
import universe
from paths import RAW

# --- pre-registered constants; see the docstring before touching ------------
CAPITAL = 300_000
MAX_POSITIONS = 5
DEPLOY_PCT = 75.0        # share of capital ever deployed, as the siblings
RISK_PCT = 2.0           # of capital at risk per position, as the siblings
STOP_PCT = 10.0
REFRESH = 5              # entry decision cadence, as the sibling books
HOLD_DAYS = 10 ** 6      # NO flat time exit; the trend break governs instead
TARGET_PCT = 10 ** 6     # no profit target either -- one idea per book

UNIVERSE_TOP = 40        # liquid core: top funds by as-of median turnover
LIQ_WINDOW = 250         # sessions behind the turnover median
HISTORY_MIN = 200        # sessions of history before any eligibility
TREND_SMA = 200          # entry gate: close above this average
MOM_WINDOW = 125         # entry gate & rank: ~6-month return
EXIT_SMA = 100           # trend-break exit: close below this average
SPLIT_LOOKBACK = 200     # sessions an action keeps a fund ineligible
SPLIT_JUMP_PCT = 25.0
PERSIST_PCT = 25.0
MAX_GAP_DAYS = 7


def asset_group(sym):
    """-> plain-language group the fund belongs to, for reporting only."""
    if "GOLD" in sym:
        return "metals"
    if "SILVER" in sym or "SILVE" in sym:
        return "metals"
    if any(t in sym for t in ("GILT", "GSEC", "SDL", "LIQUID", "EBBETF", "LIQ")):
        return "bond"
    return "index"


def fund_corpus():
    """-> (corpus, days) OHLCV Series for every classified non-equity symbol."""
    raw = {s: features.Series(s) for s in universe.non_equity_symbols()}
    for p in sorted(RAW.glob("*/bhavcopy_delivery.csv")):
        day_s = p.parent.name
        day = date.fromisoformat(day_s)
        sv_known = (RAW / day_s / "asm.json").exists()
        for r in csv.DictReader(io.StringIO(p.read_text(errors="replace")),
                                skipinitialspace=True):
            sym = (r.get("SYMBOL") or "").strip()
            if sym not in raw:
                continue
            if (r.get("SERIES") or "").strip() not in universe.TRADEABLE_SERIES:
                continue
            try:
                o, h = float(r["OPEN_PRICE"]), float(r["HIGH_PRICE"])
                lo, c = float(r["LOW_PRICE"]), float(r["CLOSE_PRICE"])
                vol = int(float(r.get("TTL_TRD_QNTY") or 0))
                to = float(r.get("TURNOVER_LACS") or 0) * 100_000
            except (KeyError, ValueError):
                continue
            if not (o > 0 and h > 0 and lo > 0 and c > 0):
                continue
            s = raw[sym]
            s.days.append(day)
            s.open.append(o)
            s.high.append(h)
            s.low.append(lo)
            s.close.append(c)
            s.volume.append(vol)
            s.turnover.append(to)
            try:
                s.deliv_pct.append(float((r.get("DELIV_PER") or "").strip()))
            except ValueError:
                s.deliv_pct.append(None)
            s.surveillance_known.append(sv_known)
            s.restricted.append(False)
    corpus = {s: v for s, v in raw.items() if len(v) >= HISTORY_MIN + 1}
    days = sorted({d for s in corpus.values() for d in s.days})
    return corpus, days


def _recent_action(s, i):
    """True if a corporate-action bar sits inside the lookback ending at i."""
    prev_move = None
    lo = max(1, i - SPLIT_LOOKBACK)
    for j in range(lo, i + 1):
        prev = s.close[j - 1]
        move = abs(s.close[j] / prev - 1.0) * 100 if prev else 0.0
        if (move > SPLIT_JUMP_PCT and (prev_move is None
                                       or prev_move <= SPLIT_JUMP_PCT)):
            nxt = s.close[j + 1] if j + 1 < len(s) else None
            gap_ok = (s.days[j] - s.days[j - 1]).days <= MAX_GAP_DAYS
            if (nxt and abs(nxt / s.close[j] - 1.0) * 100 <= PERSIST_PCT
                    and gap_ok):
                return True
        prev_move = move
    return False


def eligible(corpus, as_of):
    """-> {sym: median_turnover} passing history, split and liquidity gates."""
    med = []
    for sym, s in corpus.items():
        i = s.index_of(as_of)
        if i is None or i < HISTORY_MIN:
            continue
        if _recent_action(s, i):
            continue
        window = [x for x in s.turnover[max(0, i - LIQ_WINDOW + 1):i + 1]
                  if x > 0]
        if len(window) < LIQ_WINDOW // 2:
            continue
        med.append((statistics.median(window), sym))
    med.sort(reverse=True)
    return {sym: liq for liq, sym in med[:UNIVERSE_TOP]}


def sma(vals, i, n):
    if i + 1 < n:
        return None
    return sum(vals[i - n + 1:i + 1]) / n


def mom(s, i):
    j = i - MOM_WINDOW
    if j < 0 or not s.close[j]:
        return None
    return s.close[i] / s.close[j] - 1.0


def trending(s, i):
    """The absolute-trend gate: above the SMA200 AND up over ~6 months."""
    m = mom(s, i)
    avg = sma(s.close, i, TREND_SMA)
    if m is None or avg is None:
        return None
    return m if (s.close[i] > avg and m > 0) else None


def _mk(days, closes, highs=None, lows=None, turnover=None, symbol="X"):
    s = features.Series(symbol)
    for k, d in enumerate(days):
        px = closes[k]
        s.days.append(d)
        s.open.append(px)
        s.high.append(highs[k] if highs else px * 1.001)
        s.low.append(lows[k] if lows else px * 0.999)
        s.close.append(px)
        s.volume.append(1000)
        s.turnover.append(turnover[k] if turnover else 1e7)
        s.deliv_pct.append(50.0)
        s.surveillance_known.append(True)
        s.restricted.append(False)
    return s


def _selftest():
    """Gate selectivity, eligibility floors, split guard, group labels."""
    from datetime import timedelta
    d0 = date(2024, 1, 1)
    n = HISTORY_MIN + 80
    days = [d0 + timedelta(days=k) for k in range(n)]

    up = _mk(days, [100.0 * (1.0 + 0.002 * k) for k in range(n)], symbol="UPBEES")
    dn = _mk(days, [200.0 * (1.0 - 0.002 * k) for k in range(n)], symbol="DNBEES")
    flat = _mk(days, [100.0] * n, symbol="LIQUID1")

    i = n - 2   # a signal needs a next open to fill into
    m_up = trending(up, i)
    assert m_up is not None and m_up > 0.15, \
        f"a +0.2%/day uptrend must gate in with real momentum, got {m_up}"
    assert trending(dn, i) is None, "a downtrend must fail the gate"
    assert trending(flat, i) is None, "zero momentum must fail the gate"

    # A fresh corporate action blocks eligibility even for a perfect uptrend.
    def spl(k):
        px = 100.0 * (1.0 + 0.002 * k)
        if k >= n - 30:
            px *= 0.5                    # 1:2 split, unadjusted, persistent
        return px
    sp = _mk(days, [spl(k) for k in range(n)], symbol="GOLDETF")
    assert _recent_action(sp, n - 2), "detector missed the fixture's split"
    # The split poisons every lookback that spans it -- momentum over a
    # window containing the action reads ~-50% on an uptrending fund. That
    # distortion is exactly why eligibility must exclude the name entirely.
    assert trending(sp, n - 2) is None
    eli = eligible({"A": up, "B": sp}, days[n - 3])
    assert "A" in eli and "B" not in eli, sorted(eli)

    # Liquidity tiering: the low-turnover name drops out of the top-40 cut
    # when there are more names than seats in the core.
    many = {f"S{k:02d}": _mk(days, [100.0 + k] * n,
                             turnover=[1e6 * (k + 1)] * n,
                             symbol=f"S{k:02d}")
            for k in range(45)}
    got = eligible(many, days[-3])
    assert len(got) == UNIVERSE_TOP
    assert min(got.values()) >= 1e6 * 5, "least liquid names must be cut first"

    assert asset_group("GOLDBEES") == "metals"
    assert asset_group("SILVERIETF") == "metals"
    assert asset_group("EBBETF0425") == "bond"
    assert asset_group("NIFTYBEES") == "index"
    print("trend.clusters selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        c, ds = fund_corpus()
        print(f"{len(c)} funds, {ds[0]}..{ds[-1]} ({len(ds)} sessions)")
