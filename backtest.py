#!/usr/bin/env python3
"""Cross-sectional walk-forward validator.

The unit of evidence is the SETUP INSTANCE across the universe, not the equity
curve of one portfolio path. At swing frequency a single path yields ~25 trades
a year -- far too few to separate edge from luck. Pooling instances across 2300
symbols is the only way to get statistical power out of four years of history.

Two simulations run over the same signals:
  - unconstrained: every instance traded independently -> the evidence
  - portfolio:     heat and concurrency limits applied  -> the drawdown

Emits exactly the dict judge.consult() consumes.
"""
import argparse
import json
import statistics
from dataclasses import dataclass, asdict
from datetime import date

import engine
import features
import spec as specmod

MAX_HOLD = 30        # ~6 weeks of trading days, the persona's upper bound
COSTS = engine.Costs()


class _B:
    """Minimal bar view for the engine's fill primitives."""
    __slots__ = ("open", "high", "low", "close", "turnover", "asm", "gsm", "fo_ban")

    def __init__(self, s, i):
        self.open, self.high = s.open[i], s.high[i]
        self.low, self.close = s.low[i], s.close[i]
        self.turnover = s.turnover[i]
        self.asm, self.gsm, self.fo_ban = "", "", False


@dataclass
class Trade:
    symbol: str
    signal_day: date
    entry_day: date
    exit_day: date
    entry_px: float
    exit_px: float
    qty: int
    planned_risk: float      # per share, at signal time
    exit_reason: str
    gross: float
    costs: float
    net: float
    r: float                 # realised R multiple, net of costs
    bars_held: int


def simulate(sig, s, i, qty, max_hold=MAX_HOLD):
    """Trade the signal from bar i. Entry attempted on i+1 only -- a stop order
    is good for the day; a signal that never triggers simply expires.

    Within a bar the stop is checked BEFORE the target. Daily bars cannot say
    which came first, and assuming the favourable order is the single largest
    source of fake edge in swing backtests.
    """
    j = i + 1
    if j >= len(s):
        return None
    entry_px = engine.entry_fill(sig.entry, _B(s, j))
    if entry_px is None:
        return None

    slip = engine.slippage_bps(entry_px * qty, s.turnover[j]) / 10_000
    entry_px *= (1 + slip)                       # buying: slippage pays up
    risk = sig.entry - sig.stop
    entry_day = s.days[j]

    for k in range(j, min(j + max_hold, len(s))):
        b = _B(s, k)
        px = engine.stop_fill(sig.stop, b)
        reason = "stop"
        if px is None:
            px = engine.target_fill(sig.target, b)
            reason = "target"
        if px is None and k == j + max_hold - 1:
            px, reason = s.close[k], "time"
        if px is None:
            continue

        slip_out = engine.slippage_bps(px * qty, s.turnover[k]) / 10_000
        px *= (1 - slip_out)                     # selling: slippage takes away
        gross = (px - entry_px) * qty
        cost = COSTS.charge(entry_px * qty, "BUY") + COSTS.charge(px * qty, "SELL")
        net = gross - cost
        return Trade(s.symbol, s.days[i], entry_day, s.days[k], entry_px, px, qty,
                     risk, reason, gross, cost, net, net / (risk * qty), k - j + 1)
    return None      # still open at the end of the corpus: not a realised trade


def generate(spec, corpus, breadth, equity=1_000_000.0):
    """-> chronological list of (i, series, signal, qty). Gate applied per instance."""
    out = []
    for s in corpus.values():
        c = specmod.Ctx(s, breadth)
        for i in range(len(s)):
            sig = specmod.evaluate(spec, c, i)
            if sig is None:
                continue
            qty, why = engine.gate(sig, _B(s, i), equity, 0.0)
            if not why:
                out.append((i, s, sig, qty))
    out.sort(key=lambda t: t[1].days[t[0]])
    return out


def portfolio_path(trades, equity0=1_000_000.0):
    """Sequential equity with heat and concurrency limits -> (curve, max_dd)."""
    trades = sorted(trades, key=lambda t: t.entry_day)
    equity, peak, max_dd, curve = equity0, equity0, 0.0, []
    open_risk, open_by_day = 0.0, {}
    held = set()
    for t in trades:
        for d in [d for d in open_by_day if d <= t.entry_day]:
            r, sym = open_by_day.pop(d)
            open_risk -= r
            held.discard(sym)
        risk_frac = (t.planned_risk * t.qty) / equity
        if t.symbol in held or open_risk + risk_frac > engine.MAX_PORTFOLIO_HEAT:
            continue                                  # would breach an invariant
        open_risk += risk_frac
        held.add(t.symbol)
        open_by_day[t.exit_day] = (risk_frac, t.symbol)
        equity += t.net
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        curve.append((t.exit_day, equity))
    return curve, max_dd


def summarise(trades, curve_dd):
    if not trades:
        return {"n_trades": 0, "expectancy_after_costs": 0.0, "max_dd": 1.0}
    rs = [t.r for t in trades]
    wins = [t for t in trades if t.net > 0]
    return {
        "n_trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "avg_r": statistics.fmean(rs),
        "median_r": statistics.median(rs),
        "expectancy_after_costs": statistics.fmean(t.net for t in trades),
        "total_costs": sum(t.costs for t in trades),
        "max_dd": curve_dd,
        "avg_bars_held": statistics.fmean(t.bars_held for t in trades),
        "exits": {r: sum(1 for t in trades if t.exit_reason == r)
                  for r in ("stop", "target", "time")},
    }


def walk_forward_folds(days, n_folds=4, purge=MAX_HOLD):
    """Expanding-window folds. `purge` trading days are dropped from the tail of
    each training block: a trade signalled just before the test period is still
    open inside it, so its outcome leaks across the boundary.
    """
    days = sorted(days)
    size = len(days) // (n_folds + 1)
    out = []
    for f in range(1, n_folds + 1):
        cut = size * f
        train, test = days[:cut], days[cut:cut + size]
        if purge and len(train) > purge:
            train = train[:-purge]
        if test:
            out.append((train, test))
    return out


def run(spec, corpus, breadth, equity=1_000_000.0):
    sigs = generate(spec, corpus, breadth, equity)
    hold = spec.get("hold", {}).get("max_bars", MAX_HOLD)
    trades = [t for t in (simulate(sig, s, i, q, hold) for i, s, sig, q in sigs) if t]
    curve, dd = portfolio_path(trades, equity)
    res = summarise(trades, dd)
    res["n_signals"] = len(sigs)
    res["unfilled_or_open"] = len(sigs) - len(trades)
    return res, trades


def _selftest():
    from datetime import timedelta

    def series(bars):
        s = features.Series("T")
        d0 = date(2024, 1, 1)
        for k, (o, h, l, c) in enumerate(bars):
            s.days.append(d0 + timedelta(days=k))
            s.open.append(o); s.high.append(h); s.low.append(l); s.close.append(c)
            s.volume.append(1000); s.turnover.append(1e9)
            s.deliv_pct.append(50.0); s.surveillance_known.append(True)
        return s

    sig = engine.Signal("T", "x", entry=100.0, stop=90.0, target=130.0)

    # clean winner: triggers, then reaches target
    s = series([(99, 99, 98, 99), (100, 105, 99, 104), (120, 131, 119, 130)])
    t = simulate(sig, s, 0, 10)
    assert t and t.exit_reason == "target", t
    assert t.r > 2.0, t.r                       # 3R gross, less costs and slippage
    assert t.net < t.gross, "costs must reduce net"

    # THE ambiguity case: one bar spans both stop and target. Stop must win.
    s2 = series([(99, 99, 98, 99), (100, 105, 99, 104), (100, 135, 85, 120)])
    t2 = simulate(sig, s2, 0, 10)
    assert t2.exit_reason == "stop", f"target taken on an ambiguous bar: {t2.exit_reason}"
    assert t2.r < 0, t2.r

    # gap through the stop fills at the open, well below the stop
    s3 = series([(99, 99, 98, 99), (100, 105, 99, 104), (80, 82, 79, 81)])
    t3 = simulate(sig, s3, 0, 10)
    assert t3.exit_reason == "stop" and t3.exit_px < 90.0, t3
    assert t3.r < -1.0, f"gap loss must exceed 1R, got {t3.r}"

    # never triggers -> no trade, not a zero-P&L trade
    s4 = series([(99, 99, 98, 99), (95, 97, 94, 96), (95, 97, 94, 96)])
    assert simulate(sig, s4, 0, 10) is None

    # time stop closes at the last bar's close
    flat = [(99, 99, 98, 99), (100, 105, 99, 104)] + [(104, 105, 103, 104)] * (MAX_HOLD + 2)
    t5 = simulate(sig, series(flat), 0, 10)
    assert t5.exit_reason == "time" and t5.bars_held == MAX_HOLD, t5

    # purge removes the tail of each training block
    days = [date(2024, 1, 1) + timedelta(days=k) for k in range(500)]
    folds = walk_forward_folds(days, n_folds=4, purge=30)
    assert len(folds) == 4
    for train, test in folds:
        assert max(train) < min(test), "train must precede test"
        gap = (min(test) - max(train)).days
        assert gap >= 30, f"purge gap too small: {gap}"

    # portfolio must refuse to breach heat
    big = [Trade("A%d" % k, days[0], days[0], days[20], 100, 101, 1000,
                 100.0, "target", 1000, 10, 990, 0.01, 5) for k in range(20)]
    _, dd = portfolio_path(big, 1_000_000.0)
    assert dd >= 0.0
    print("backtest selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest()
    else:
        corpus = features.load_corpus()
        bd = features.breadth(corpus)
        res, trades = run(specmod.STAGE2_BREAKOUT, corpus, bd)
        if a.json:
            print(json.dumps(res, indent=2, default=str))
        else:
            print(f"signals {res['n_signals']}  trades {res['n_trades']}  "
                  f"unfilled/open {res['unfilled_or_open']}")
            print(f"win rate   {res['win_rate']*100:.1f}%")
            print(f"avg R      {res['avg_r']:+.2f}   median R {res['median_r']:+.2f}")
            print(f"expectancy Rs {res['expectancy_after_costs']:+,.0f}/trade "
                  f"(costs Rs {res['total_costs']:,.0f} total)")
            print(f"max DD     {res['max_dd']*100:.1f}%")
            print(f"exits      {res['exits']}   avg hold {res['avg_bars_held']:.1f} bars")
            days = sorted({d for s in corpus.values() for d in s.days})
            print("\nwalk-forward folds (by signal date):")
            for n, (train, test) in enumerate(walk_forward_folds(days, a.folds), 1):
                sub = [t for t in trades if test[0] <= t.signal_day <= test[-1]]
                if sub:
                    print(f"  fold {n}  {test[0]}..{test[-1]}  n={len(sub):3d}  "
                          f"avgR={statistics.fmean(t.r for t in sub):+.2f}  "
                          f"win={sum(1 for t in sub if t.net>0)/len(sub)*100:.0f}%")
                else:
                    print(f"  fold {n}  {test[0]}..{test[-1]}  n=0")
