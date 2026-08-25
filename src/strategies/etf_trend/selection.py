#!/usr/bin/env python3
"""Rules of the trend book: what ranks, what gates, what sizes a position.

PROVENANCE AND STATUS. Every rule here was pre-registered in
src/research/trend_fund_test.py before its backtest ran, and the backtest
FAILED its promotion bar (batch 20260824-trendfund2: +1.04% +/- 1.08% per
trade; edge vs its momentum-only control t = +1.19). The book exists to turn
that unresolved direction into forward evidence. Its rules are therefore
FROZEN at their registered values -- this file is the record of the bet, and
tuning it after forward trades start would destroy the evidence it exists to
collect.

THE RULES.
  universe   clusters.eligible(): liquid funds only (top 40 by as-of median
             turnover), >=200 sessions of history, no corporate action inside
             200 sessions.
  gate       absolute trend: close > SMA200 AND positive ~6-month return.
             A fund in a downtrend is not compensated for by rank -- it is
             simply not a candidate, and the book holds cash instead.
  rank       the same ~6-month return, best first. One number decides both
             entry and priority; there is no composite score to argue with.
  seats      MAX_POSITIONS equal-weight seats, sized risk-first then capped
             per name exactly like breakout sizes (RISK_PCT of capital,
             DEPLOY_PCT/MAX_POSITIONS rupee cap).
  exits      -10% hard stop from fill; trend break: close below SMA100 sells
             that close. No profit target, no flat time limit -- one idea,
             held while the trend lives.
  fills      next session's open, like every book in this repo.

Interface note: simulate.run() and any future audit drive this through the
same seam as breakout -- CAPITAL, HOLD_DAYS, build(), allocate(),
decorrelate(), size_mult(), position_size() -- so the shared harness needs
nothing strategy-specific.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))  # -> src/
import paths  # noqa: F401

import sys

import clusters

CAPITAL = clusters.CAPITAL
MAX_POSITIONS = clusters.MAX_POSITIONS
HOLD_DAYS = clusters.HOLD_DAYS        # no flat time exit; see docstring
STOP_PCT = clusters.STOP_PCT
TARGET_PCT = clusters.TARGET_PCT      # unused by this book's rules
DEPLOY_PCT = clusters.DEPLOY_PCT
RISK_PCT = clusters.RISK_PCT
TRIGGER = "none"                      # the trend gate IS the trigger


def build(corpus, as_of, capital=CAPITAL, trigger=None):
    """-> candidate rows, gated and ranked best-first. Cash if none qualify."""
    del trigger
    rows = []
    for sym, liq in clusters.eligible(corpus, as_of).items():
        s = corpus[sym]
        i = s.index_of(as_of)
        if i is None:
            continue
        # NOTE: no "must have a next bar" guard here. A backtest never asks
        # about the final bar (simulate.run checks di+1 < len(days) before
        # calling build), but the LIVE paper runner queues on the newest
        # session precisely because its fill happens in the NEXT one.
        if (i < len(s.restricted) and s.restricted[i]
                and i < len(s.surveillance_known) and s.surveillance_known[i]):
            continue
        if s.high[i] == s.low[i]:
            continue    # band-locked signal bar: no next-open fill exists (L58)
        m = clusters.trending(s, i)
        if m is None:
            continue
        ref = s.close[i]
        qty, risk = position_size(capital, ref)
        if qty < 1:
            continue
        rows.append({
            "cluster": clusters.asset_group(sym),
            "symbol": sym,
            "score": round(m * 100, 2),
            "ref_close": round(ref, 2),
            "entry_rule": "next session open",
            "stop": round(ref * (1 - STOP_PCT / 100), 2),
            "target": None,               # no target: trend-break governs
            "exit_rule": f"close < SMA{clusters.EXIT_SMA} or -{STOP_PCT:.0f}% stop",
            "qty": qty, "value": round(qty * ref), "risk": round(risk),
            "triggered": True,
            "why": f"above its {clusters.TREND_SMA}-session average",
        })
    rows.sort(key=lambda r: -r["score"])
    return rows


def allocate(rows, take_per_cluster=None, offset=0, max_pos=MAX_POSITIONS):
    """Best first, up to max_pos. Asset groups are reporting labels only --
    the registration deliberately has NO group quota: rotation is the point."""
    del take_per_cluster, offset
    return rows[:max_pos]


def decorrelate(rows, corpus, as_of, max_corr):
    """Pass-through. Funds tracking the same index are near-identical assets,
    but the liquidity cap already leaves one name per niche, and the
    registration did not include a correlation rule."""
    del corpus, as_of, max_corr
    return rows


def size_mult(scheme, rank, vol_pct, med_vol_pct):
    del rank, vol_pct, med_vol_pct
    return 1.0     # equal sizing, as registered


def position_size(capital, entry, stop_pct=STOP_PCT, mult=1.0,
                  max_pos=MAX_POSITIONS):
    """Risk-based qty first, then the per-name rupee cap -- breakout's exact
    arithmetic with this book's constants."""
    risk_rupees = capital * RISK_PCT / 100
    risk_per_share = entry * stop_pct / 100
    if risk_per_share <= 0:
        return 0, 0.0
    qty = int(risk_rupees / risk_per_share)
    cap_value = capital * DEPLOY_PCT / 100 / max_pos * mult
    qty = min(qty, int(cap_value / entry))
    return qty, qty * risk_per_share


def _selftest():
    import statistics
    from datetime import date, timedelta

    d0 = date(2024, 1, 1)
    days = [d0 + timedelta(days=k) for k in range(clusters.HISTORY_MIN + 80)]
    n = len(days)

    def mk(sym, px_fn, to=1e7):
        s = __import__("features").Series(sym)
        for k, d in enumerate(days):
            px = px_fn(k)
            s.days.append(d)
            s.open.append(px)
            s.high.append(px * 1.001)
            s.low.append(px * 0.999)
            s.close.append(px)
            s.volume.append(1000)
            s.turnover.append(to)
            s.deliv_pct.append(50.0)
            s.surveillance_known.append(True)
            s.restricted.append(False)
        return s

    up = mk("UPBEES", lambda k: 100.0 * (1 + 0.002 * k))
    dn = mk("DNBEES", lambda k: 200.0 * (1 - 0.002 * k))
    hot = mk("HOTBEES", lambda k: 50.0 * (1 + 0.004 * k))
    corpus = {"UPBEES": up, "DNBEES": dn, "HOTBEES": hot}

    rows = build(corpus, days[-2])
    names = [r["symbol"] for r in rows]
    assert names == ["HOTBEES", "UPBEES"], \
        f"gate must exclude the downtrend and rank by momentum: {names}"
    assert rows[0]["score"] > rows[1]["score"]
    # The live runner queues on the NEWEST bar -- the final session must rank
    # exactly like any other, or the book never has a queue.
    assert [r["symbol"] for r in build(corpus, days[-1])] == names

    alloc = allocate(rows, max_pos=1)
    assert [r["symbol"] for r in alloc] == ["HOTBEES"]
    assert allocate(rows) == rows[:MAX_POSITIONS]

    qty, risk = position_size(CAPITAL, 100.0)
    assert qty == min(int(CAPITAL * RISK_PCT / 100 / (100 * STOP_PCT / 100)),
                      int(CAPITAL * DEPLOY_PCT / 100 / MAX_POSITIONS / 100.0))
    assert risk == qty * 100.0 * STOP_PCT / 100
    assert size_mult("equal", 0, None, None) == 1.0
    assert decorrelate([1, 2], {}, None, None) == [1, 2]

    row = rows[0]
    assert row["stop"] == round(row["ref_close"] * (1 - STOP_PCT / 100), 2)
    assert row["target"] is None and row["triggered"] is True
    # sanity: the whole book risks RISK_PCT x seats of capital at full occupancy
    tot_risk = sum(position_size(CAPITAL, r["ref_close"])[1] for r in rows)
    assert tot_risk <= CAPITAL * RISK_PCT / 100 * MAX_POSITIONS * 1.01
    print("trend.selection selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
