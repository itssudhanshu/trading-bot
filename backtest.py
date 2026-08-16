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
RUIN_FLOOR = 0.20    # stop trading below 20% of starting equity
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
    rank_score: float = 0.0  # higher wins a contested slot


def rank_score(rule, sig, s, i):
    """Preference among same-day signals. Higher is taken first.

    Fundamental momentum belongs HERE, not as an AND-filter. As a boolean gate
    ("profitable last 4 quarters") it is true for most listed companies -- extra
    search dimensions with no selectivity, which is how epoch 6 overfit. As a
    ranking key it decides which of several simultaneous setups to take, which
    is the question fundamentals can actually answer.
    """
    if rule in ("rev_growth", "rev_accel"):
        rows = getattr(s, "fund", None)
        if not rows:
            return float("-inf")          # unranked, never preferred
        import fundamentals
        day = s.days[i].isoformat()
        v = (fundamentals.growth_yoy(rows, day) if rule == "rev_growth"
             else fundamentals.growth_accel(rows, day))
        return float("-inf") if v is None else v
    if rule == "rr":
        return sig.rr
    if rule == "turnover":
        return s.turnover[i]
    if rule == "deliv_pct":
        return s.deliv_pct[i]
    return 0.0


def simulate(sig, s, i, qty, max_hold=MAX_HOLD, rank="none"):
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
                     risk, reason, gross, cost, net, net / (risk * qty), k - j + 1,
                     rank_score(rank, sig, s, i))
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
    """Sequential equity with heat and concurrency limits.

    -> (curve, max_dd, admitted). `admitted` is the subset actually takeable
    under the invariants. For a spec generating more signals than the portfolio
    has capacity for, unconstrained expectancy describes trades you could never
    have taken -- the realizable number is computed over `admitted`.
    """
    # Contested slots go to the highest-ranked signal, not the earliest-listed.
    # `symbol` is a TIE-BREAKER, not decoration: entry_day and rank_score tie
    # constantly (rank "none" scores every trade 0.0), and a stable sort then
    # falls back on INPUT order -- which differs between the serial and parallel
    # paths, and between any two symbol iteration orders. Without a total order
    # the admitted subset is not reproducible, so neither is any gate that reads
    # it. This is the real cause of the L37 ranking divergence; the L34 heat
    # leak amplified it but was not it.
    trades = sorted(trades, key=lambda t: (t.entry_day, -t.rank_score, t.symbol))
    equity, peak, max_dd, curve = equity0, equity0, 0.0, []
    open_risk, open_by_day = 0.0, {}
    held, admitted = set(), []
    for t in trades:
        for d in [d for d in open_by_day if d <= t.entry_day]:
            # A LIST, not a single entry: several positions can share an exit
            # day, and a dict keyed on that day silently discards all but the
            # last. The lost position's risk is never returned to open_risk and
            # its symbol never leaves `held`, so the heat budget leaks for the
            # rest of the run and that symbol becomes permanently untradeable.
            # A spec holding N bars enters several positions and time-exits them
            # all on the same bar, so this is the common case, not an edge one.
            for r, sym in open_by_day.pop(d):
                open_risk -= r
                held.discard(sym)
        # Ruin guard. Without it the sim keeps trading a negative account and
        # reports drawdowns above 100%, which cannot happen to a long-only book.
        if equity <= RUIN_FLOOR * equity0:
            break

        # Size against CURRENT equity, not the starting figure. Real risk budgets
        # shrink as the account does; static sizing lets losses compound past
        # ruin and overstates drawdown.
        scale = equity / equity0
        qty = int(t.qty * scale)
        if qty < 1:
            continue
        risk_frac = (t.planned_risk * qty) / equity
        if t.symbol in held or open_risk + risk_frac > engine.MAX_PORTFOLIO_HEAT:
            continue                                  # would breach an invariant
        open_risk += risk_frac
        held.add(t.symbol)
        open_by_day.setdefault(t.exit_day, []).append((risk_frac, t.symbol))
        # ponytail: net scaled linearly with qty; the fixed brokerage component
        # is not re-derived. Fine while scale stays near 1 -- revisit if a study
        # runs deep drawdowns where the fixed leg matters.
        equity += t.net * (qty / t.qty)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        curve.append((t.exit_day, equity))
        admitted.append(t)
    return curve, max_dd, admitted


def summarise(trades, curve_dd, admitted=None):
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
        # What the portfolio could actually take. This is the number to rank on.
        "n_taken": len(admitted) if admitted is not None else len(trades),
        "capacity_ratio": (len(trades) / len(admitted)) if admitted else 1.0,
        "portfolio_expectancy": (statistics.fmean(t.net for t in admitted)
                                 if admitted else 0.0),
        "portfolio_total": sum(t.net for t in admitted) if admitted else 0.0,
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


def run(spec, corpus, breadth, equity=1_000_000.0, allowed=None, presignals=None):
    """`presignals` skips regeneration. Without it the parallel search would
    generate every signal fast, then regenerate it serially inside the backtest
    -- discarding the speedup it just paid for."""
    sigs = presignals if presignals is not None else generate(spec, corpus, breadth, equity)
    if allowed is not None and presignals is None:
        sigs = [(i, s, sg, q) for i, s, sg, q in sigs if s.days[i] in allowed]
    hold = spec.get("hold", {}).get("max_bars", MAX_HOLD)
    rank = spec.get("rank", {}).get("by", "none")
    trades = [t for t in (simulate(sig, s, i, q, hold, rank) for i, s, sig, q in sigs) if t]
    curve, dd, admitted = portfolio_path(trades, equity)
    res = summarise(trades, dd, admitted)
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

    # rank decides who gets a contested slot
    dd0, dd1 = date(2024, 1, 1), date(2024, 1, 21)
    hi = Trade("HI", dd0, dd0, dd1, 100, 101, 100, 100.0,
               "target", 1000, 10, 990, 0.01, 5, rank_score=9.0)
    lo = Trade("LO", dd0, dd0, dd1, 100, 101, 100, 100.0,
               "target", 1000, 10, 990, 0.01, 5, rank_score=1.0)
    _, _, adm = portfolio_path([lo, hi] * 12, 1_000_000.0)
    assert adm, "fixture must admit something or it tests nothing"
    assert adm[0].symbol == "HI", f"higher rank must win the first slot, got {adm[0].symbol}"

    # ordering must be TOTAL: shuffling the input must not change the outcome
    import random as _rnd
    tied = [Trade(f"T{k:02d}", dd0, dd0, dd1, 100, 101, 100, 100.0, "target",
                  1000, 10, 990, 0.01, 5, rank_score=0.0) for k in range(12)]
    _, _, a1 = portfolio_path(list(tied), 1_000_000.0)
    shuffled = list(tied)
    _rnd.Random(7).shuffle(shuffled)
    _, _, a2 = portfolio_path(shuffled, 1_000_000.0)
    assert [t.symbol for t in a1] == [t.symbol for t in a2], (
        f"admission depends on input order: {[t.symbol for t in a1]} vs "
        f"{[t.symbol for t in a2]}")

    assert rank_score("rr", engine.Signal("X", "s", 100, 90, 130), None, 0) == 3.0
    assert rank_score("none", engine.Signal("X", "s", 100, 90, 130), None, 0) == 0.0

    # purge removes the tail of each training block
    days = [date(2024, 1, 1) + timedelta(days=k) for k in range(500)]
    folds = walk_forward_folds(days, n_folds=4, purge=30)
    assert len(folds) == 4
    for train, test in folds:
        assert max(train) < min(test), "train must precede test"
        gap = (min(test) - max(train)).days
        assert gap >= 30, f"purge gap too small: {gap}"

    # portfolio must refuse to breach heat
    # 20 concurrent trades, each risking 1% of equity: the 6% heat ceiling
    # admits ~6 of them, so capacity binds and the rest are never taken.
    big = [Trade("A%d" % k, days[0], days[0], days[20], 100, 101, 100,
                 100.0, "target", 1000, 10, 990, 0.01, 5) for k in range(20)]
    _, dd, adm = portfolio_path(big, 1_000_000.0)
    assert dd >= 0.0

    # drawdown can never exceed 100%: a long-only book cannot lose more than it has
    ruinous = [Trade("R%d" % k, days[0], days[0] + timedelta(days=k),
                     days[0] + timedelta(days=k + 1), 100, 1, 100, 100.0,
                     "stop", -90_000, 100, -90_000, -9.0, 1) for k in range(200)]
    _, dd_r, adm_r = portfolio_path(ruinous, 1_000_000.0)
    assert dd_r <= 1.0, f"drawdown above 100% is impossible, got {dd_r*100:.0f}%"
    assert len(adm_r) < len(ruinous), "ruin guard must stop trading"
    assert len(adm) < len(big), "heat ceiling must refuse some of 20 concurrent trades"

    # REGRESSION (L34): positions sharing an exit day must each release their
    # risk. The old fixture had all 20 exit on one day but only asserted that
    # the heat ceiling binds on ENTRY -- nothing entered afterwards, so the book
    # was never required to empty and the leak could not be observed.
    d_entry, d_exit = date(2024, 1, 1), date(2024, 1, 10)
    later = date(2024, 2, 1)
    shared = [Trade(f"E{k}", d_entry, d_entry, d_exit, 100, 101, 100, 100.0,
                    "time", 0, 0, 0.0, 0.0, 7) for k in range(4)]
    after = [Trade(f"L{k}", later, later, later + timedelta(days=5), 100, 101, 100,
                   100.0, "target", 500, 10, 490, 0.05, 5) for k in range(6)]
    _, _, adm2 = portfolio_path(shared + after, 1_000_000.0)
    admitted_later = [t for t in adm2 if t.symbol.startswith("L")]
    assert len(admitted_later) == 6, (
        f"closed positions sharing an exit day leaked heat: only "
        f"{len(admitted_later)}/6 later trades admitted")

    # over-capacity: realizable expectancy is computed on the admitted subset
    r = summarise(big, dd, adm)
    assert r["n_taken"] == len(adm) and r["capacity_ratio"] > 1.0, r
    assert r["n_trades"] == len(big), "instance count stays the evidence base"
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
