#!/usr/bin/env python3
"""Daily paper-trading runner: the forward loop.

Forward paper trading is the ONLY renewable evidence. Every backtest, however
sliced, reuses the same fixed history -- compute creates no new information,
only the calendar does. So this runs from the day it is switched on, and its
value accrues with wall-clock time, not with how hard the search works.

Order matters. Exits are processed before entries, on the same bar, because a
position that stops out today frees the heat that a new entry would consume.
Doing it the other way silently over-allocates.

Shares engine.entry_fill / stop_fill / target_fill with backtest.py. If paper
and backtest used different fill logic, paper would be testing a system you do
not run.

    ./runner.py                 # today
    ./runner.py --date 2026-08-14 --plot
"""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import backtest
import engine
import features
import judge
import spec as specmod

ROOT = Path(__file__).resolve().parent
ACTIVE = ROOT / "data" / "active_specs.json"
EQUITY0 = 1_000_000.0
LOOKBACK_DAYS = 600          # enough bars to seed a 300-period indicator


def load_active():
    if ACTIVE.exists():
        return json.loads(ACTIVE.read_text())
    return [specmod.STAGE2_BREAKOUT]


def _bar_on(series, day):
    i = series.index_of(day)
    return (backtest._B(series, i), i) if i is not None else (None, None)


def process_exits(j, corpus, day, costs):
    """Close positions whose stop, target or time limit is hit today."""
    closed = []
    for p in j.positions("open"):
        s = corpus.get(p["symbol"])
        if s is None:
            continue
        b, i = _bar_on(s, day)
        if b is None:
            continue
        px = engine.stop_fill(p["stop"], b)
        reason = "stop"
        if px is None:
            px = engine.target_fill(p["target"], b)
            reason = "target"
        if px is None:
            held = len([d for d in s.days if p["entry_day"] <= str(d) <= str(day)])
            if held >= p["max_bars"]:
                px, reason = b.close, "time"
        if px is None:
            continue
        qty = p["qty"]
        slip = engine.slippage_bps(px * qty, b.turnover) / 10_000
        px *= (1 - slip)
        gross = (px - p["entry_px"]) * qty
        cost = (costs.charge(p["entry_px"] * qty, "BUY") + costs.charge(px * qty, "SELL"))
        j.close_position(p["id"], day, px, reason, gross - cost)
        closed.append((p["symbol"], reason, gross - cost))
    return closed


def process_entries(j, corpus, day, equity, costs):
    """Trigger or expire yesterday's pending signals. A stop order is good for
    one day: untriggered means the setup did not confirm, not 'try again'."""
    filled, expired = [], []
    open_risk = sum((p["entry_px"] - p["stop"]) * p["qty"] for p in j.positions("open")
                    if p["entry_px"]) / equity
    for p in j.positions("pending"):
        s = corpus.get(p["symbol"])
        b, i = _bar_on(s, day) if s else (None, None)
        if b is None:
            j.expire_position(p["id"], day)
            expired.append((p["symbol"], "no_bar"))
            continue
        # entry trigger is the signal bar's high plus buffer, recomputed here
        trigger = p["target"] and p["stop"]
        px = engine.entry_fill(p["entry_px"] or 0.0, b) if p["entry_px"] else None
        if px is None:
            j.expire_position(p["id"], day)
            expired.append((p["symbol"], "not_triggered"))
            continue
        risk_frac = (px - p["stop"]) * p["qty"] / equity
        if open_risk + risk_frac > engine.MAX_PORTFOLIO_HEAT:
            j.expire_position(p["id"], day)
            expired.append((p["symbol"], "portfolio_heat"))
            continue
        slip = engine.slippage_bps(px * p["qty"], b.turnover) / 10_000
        j.fill_entry(p["id"], day, px * (1 + slip))
        open_risk += risk_frac
        filled.append((p["symbol"], px))
    return filled, expired


def generate_pending(j, corpus, bd, day, equity, specs):
    """Signals off today's close, queued for tomorrow's open. Rejections are
    journalled too -- they are the record of what the gate actually blocked."""
    queued = []
    held = {p["symbol"] for p in j.positions("open")} | \
           {p["symbol"] for p in j.positions("pending")}
    for sp in specs:
        h = judge.spec_hash(sp)
        hold = sp.get("hold", {}).get("max_bars", backtest.MAX_HOLD)
        for sym, s in corpus.items():
            i = s.index_of(day)
            if i is None or sym in held:
                continue
            sig = specmod.evaluate(sp, specmod.Ctx(s, bd), i)
            if sig is None:
                continue
            qty, why = engine.gate(sig, backtest._B(s, i), equity, 0.0)
            j.signal(day, sig, qty, why)
            if why:
                continue
            pid = j.open_position(h, sig, day, qty, hold)
            j.db.execute("UPDATE positions SET entry_px=? WHERE id=?", (sig.entry, pid))
            j.db.commit()
            queued.append((sym, sig))
    return queued


def run(day, plot=False, dry=False):
    j = engine.Journal(":memory:" if dry else None)
    costs = engine.Costs()
    corpus = features.load_corpus(start=day - timedelta(days=LOOKBACK_DAYS),
                                  end=day, min_bars=60)
    if not corpus:
        print(f"{day}: no data (holiday or snapshot missing)")
        return
    if all(s.index_of(day) is None for s in corpus.values()):
        print(f"{day}: not a trading day")
        return
    bd = features.breadth(corpus)
    equity = EQUITY0 + j.realised_pnl()

    closed = process_exits(j, corpus, day, costs)
    filled, expired = process_entries(j, corpus, day, equity, costs)
    queued = generate_pending(j, corpus, bd, day, equity, load_active())

    print(f"{day}  equity Rs {equity:,.0f}")
    print(f"  exits    {len(closed)}  " + ", ".join(f"{s}:{r}" for s, r, _ in closed[:5]))
    print(f"  entries  {len(filled)}  " + ", ".join(f"{s}@{p:.2f}" for s, p in filled[:5]))
    print(f"  expired  {len(expired)} " + ", ".join(f"{s}:{r}" for s, r in expired[:5]))
    print(f"  queued   {len(queued)}  " + ", ".join(s for s, _ in queued[:5]))
    print(f"  open     {len(j.positions('open'))}")

    if plot and queued:
        import tv
        if tv.connected():
            sym, sig = queued[0]
            r = tv.plot_signal(sig, symbol=f"NSE:{sym}")
            print(f"  plotted {sym} -> {r['ids']}")
        else:
            print("  plot skipped: TradingView not connected")


def _selftest():
    d0 = date(2024, 1, 1)

    def mkseries(sym, bars):
        s = features.Series(sym)
        for k, (o, h, l, c) in enumerate(bars):
            s.days.append(d0 + timedelta(days=k))
            s.open.append(o); s.high.append(h); s.low.append(l); s.close.append(c)
            s.volume.append(1000); s.turnover.append(1e9)
            s.deliv_pct.append(50.0); s.surveillance_known.append(True)
        return s

    j = engine.Journal(":memory:")
    costs = engine.Costs()
    s = mkseries("T", [(100, 105, 99, 104), (106, 112, 105, 110), (95, 96, 85, 88)])
    corpus = {"T": s}
    sig = engine.Signal("T", "x", entry=106.0, stop=100.0, target=124.0)

    pid = j.open_position("h", sig, s.days[0], 10, 30)
    j.db.execute("UPDATE positions SET entry_px=? WHERE id=?", (sig.entry, pid))
    j.db.commit()

    filled, expired = process_entries(j, corpus, s.days[1], 1_000_000.0, costs)
    assert len(filled) == 1 and not expired, (filled, expired)
    assert len(j.positions("open")) == 1

    closed = process_exits(j, corpus, s.days[2], costs)
    assert len(closed) == 1 and closed[0][1] == "stop", closed
    assert closed[0][2] < 0, "gap through stop must lose money"
    assert j.realised_pnl() < 0

    # a pending signal that never triggers expires; it does not linger
    j2 = engine.Journal(":memory:")
    s2 = mkseries("U", [(100, 105, 99, 104), (95, 97, 94, 96)])
    sig2 = engine.Signal("U", "x", entry=110.0, stop=100.0, target=140.0)
    p2 = j2.open_position("h", sig2, s2.days[0], 10, 30)
    j2.db.execute("UPDATE positions SET entry_px=? WHERE id=?", (110.0, p2))
    j2.db.commit()
    f2, e2 = process_entries(j2, {"U": s2}, s2.days[1], 1_000_000.0, costs)
    assert not f2 and e2 and e2[0][1] == "not_triggered", (f2, e2)
    assert j2.positions("pending") == []

    # heat ceiling blocks an entry that would breach it
    j3 = engine.Journal(":memory:")
    p3 = j3.open_position("h", sig, s.days[0], 100000, 30)
    j3.db.execute("UPDATE positions SET entry_px=? WHERE id=?", (sig.entry, p3))
    j3.db.commit()
    f3, e3 = process_entries(j3, corpus, s.days[1], 10_000.0, costs)
    assert e3 and e3[0][1] == "portfolio_heat", (f3, e3)
    print("runner selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", type=date.fromisoformat, default=date.today())
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest()
    else:
        run(a.date, a.plot, a.dry_run)
