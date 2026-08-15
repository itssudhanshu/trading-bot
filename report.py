#!/usr/bin/env python3
"""Full portfolio reporting for one spec over a date range.

Used for the holdout run: the search never saw those bars, so simulating there
is a genuine out-of-sample paper result -- the closest honest substitute for
forward paper trading, which is bounded by the calendar and cannot be hurried.

Reports the portfolio-constrained path (heat, concurrency, ranking), not the
unconstrained instance set. The constrained path is what you could have traded.
"""
import argparse
import json
import statistics
from collections import defaultdict
from datetime import date, timedelta

from pathlib import Path

import backtest
import engine
import features
import judge
import split

ROOT = Path(__file__).resolve().parent

EQUITY0 = 1_000_000.0


def simulate_portfolio(spec, corpus, bd, equity0=EQUITY0, start_day=None, allowed=None):
    """-> (trades, taken, curve, dd). Chronological, invariants enforced.

    `start_day` restricts SIGNALS to on/after that date while leaving earlier
    bars in the corpus for indicator warm-up. Seeding a 200-period SMA from
    pre-holdout bars is not lookahead -- it is how the indicator is defined. The
    seal forbids SEARCHING on holdout data, not knowing that prices existed
    before it. Without warm-up the first ~200 holdout bars are unusable and the
    run silently reports zero trades.
    """
    sigs = backtest.generate(spec, corpus, bd, equity0)
    if start_day:
        sigs = [(i, s, sig, q) for i, s, sig, q in sigs if s.days[i] >= start_day]
    if allowed is not None:
        sigs = [(i, s, sig, q) for i, s, sig, q in sigs if s.days[i] in allowed]
    hold = spec.get("hold", {}).get("max_bars", backtest.MAX_HOLD)
    rank = spec.get("rank", {}).get("by", "none")
    trades = [t for t in (backtest.simulate(sig, s, i, q, hold, rank)
                          for i, s, sig, q in sigs) if t]
    curve, dd, taken = backtest.portfolio_path(trades, equity0)
    return trades, taken, curve, dd


def stats(taken, curve, dd, equity0=EQUITY0, span_days=None):
    if not taken:
        return {"n": 0}
    wins = [t for t in taken if t.net > 0]
    losses = [t for t in taken if t.net <= 0]
    total = sum(t.net for t in taken)
    gross_win = sum(t.net for t in wins)
    gross_loss = -sum(t.net for t in losses)

    # exposure: fraction of calendar days with at least one position open
    dayset = set()
    for t in taken:
        d = t.entry_day
        while d <= t.exit_day:
            dayset.add(d)
            d += timedelta(days=1)
    # +1: a position opened and closed on the same day occupies one day, so the
    # span is inclusive of both endpoints. Without it exposure exceeds 100%.
    span = span_days or ((max(t.exit_day for t in taken)
                          - min(t.entry_day for t in taken)).days + 1)

    # peak concurrency
    events = sorted([(t.entry_day, 1) for t in taken] + [(t.exit_day, -1) for t in taken])
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)

    years = span / 365.25
    end_equity = equity0 + total
    return {
        "n": len(taken),
        "start_equity": equity0,
        "end_equity": end_equity,
        "total_return_pct": total / equity0 * 100,
        "cagr_pct": ((end_equity / equity0) ** (1 / years) - 1) * 100 if years > 0.2 else float("nan"),
        "win_rate": len(wins) / len(taken),
        "avg_win": statistics.fmean(t.net for t in wins) if wins else 0.0,
        "avg_loss": statistics.fmean(t.net for t in losses) if losses else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else float("inf"),
        "expectancy": total / len(taken),
        "avg_r": statistics.fmean(t.r for t in taken),
        "max_dd_pct": dd * 100,
        "peak_concurrent": peak,
        "exposure_pct": len(dayset) / span * 100,
        "avg_hold_bars": statistics.fmean(t.bars_held for t in taken),
        "total_costs": sum(t.costs for t in taken),
        "exits": {r: sum(1 for t in taken if t.exit_reason == r)
                  for r in ("stop", "target", "time")},
    }


def monthly(taken):
    m = defaultdict(float)
    for t in taken:
        m[f"{t.exit_day.year}-{t.exit_day.month:02d}"] += t.net
    return dict(sorted(m.items()))


def render(name, spec, trades, taken, curve, dd, span_days=None):
    st = stats(taken, curve, dd, span_days=span_days)
    out = [f"\n{'='*70}", f"{name}", f"{'='*70}"]
    if not st["n"]:
        out.append("no trades taken")
        return "\n".join(out), st
    out += [
        f"instances generated : {len(trades):,}   taken by portfolio: {st['n']:,}",
        f"starting equity     : Rs {st['start_equity']:>14,.0f}",
        f"ending equity       : Rs {st['end_equity']:>14,.0f}",
        f"total return        : {st['total_return_pct']:>+14.2f}%",
        f"CAGR                : {st['cagr_pct']:>+14.2f}%",
        "",
        f"trades              : {st['n']}",
        f"win rate            : {st['win_rate']*100:.1f}%",
        f"avg win / avg loss  : Rs {st['avg_win']:+,.0f} / Rs {st['avg_loss']:+,.0f}",
        f"profit factor       : {st['profit_factor']:.2f}",
        f"expectancy/trade    : Rs {st['expectancy']:+,.0f}",
        f"average R           : {st['avg_r']:+.2f}",
        "",
        f"max drawdown        : {st['max_dd_pct']:.1f}%",
        f"peak concurrent     : {st['peak_concurrent']} positions",
        f"exposure            : {st['exposure_pct']:.1f}% of days",
        f"avg hold            : {st['avg_hold_bars']:.1f} bars",
        f"total costs paid    : Rs {st['total_costs']:,.0f}",
        f"exits               : {st['exits']}",
    ]
    return "\n".join(out), st


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", help="path to a spec json (default: top promoted)")
    ap.add_argument("--holdout", action="store_true", help="run on the sealed holdout")
    ap.add_argument("--force-holdout", action="store_true",
                    help="look at holdout for a spec that was never promoted")
    a = ap.parse_args()

    if a.spec:
        spec = json.loads(open(a.spec).read())
    else:
        rows = [json.loads(l) for l in open("data/promoted.jsonl").read().splitlines() if l.strip()]
        if not rows:
            print("nothing promoted; run validate.py")
            return
        spec = rows[0]["spec"]

    # Casual holdout access is the leak that discipline alone does not stop: it
    # took one "just testing the plumbing" run to learn a holdout result for a
    # spec that had never been promoted. Guard it in code, not in intent.
    if a.holdout and not a.force_holdout:
        promoted_specs = []
        pth = ROOT / "data" / "promoted.jsonl"
        if pth.exists():
            promoted_specs = [judge.spec_hash(json.loads(l)["spec"])
                              for l in pth.read_text().splitlines() if l.strip()]
        if judge.spec_hash(spec) not in promoted_specs:
            print("REFUSED: this spec has not been promoted by validate.py.\n"
                  "The holdout is for specs that already survived walk-forward.\n"
                  "Run on train instead (drop --holdout), or pass --force-holdout\n"
                  "if you intend to spend the seal on an unpromoted hypothesis.")
            return

    # Full corpus always: holdout blocks are interleaved, and indicators need
    # continuity across every boundary. Only SIGNALS are restricted.
    corpus = features.load_corpus()
    all_days = sorted({d for s in corpus.values() for d in s.days})
    tr, ho = split.split_days(all_days)
    if a.holdout:
        allowed, label = set(ho), f"HOLDOUT blocks {', '.join(split.HOLDOUT_BLOCKS)} (out of sample)"
    else:
        allowed, label = set(tr), "TRAIN blocks (in sample)"

    bd = features.breadth(corpus)
    trades, taken, curve, dd = simulate_portfolio(spec, corpus, bd, allowed=allowed)
    text, st = render(label, spec, trades, taken, curve, dd)
    print(text)
    if taken:
        print("\nmonthly P&L (Rs):")
        for k, v in monthly(taken).items():
            print(f"  {k}  {v:>+12,.0f}")


def _selftest():
    d0 = date(2024, 1, 1)
    t = backtest.Trade("A", d0, d0, d0 + timedelta(days=10), 100, 130, 100,
                       10.0, "target", 3000, 100, 2900, 2.9, 10)
    t2 = backtest.Trade("B", d0, d0, d0 + timedelta(days=5), 100, 90, 100,
                        10.0, "stop", -1000, 100, -1100, -1.1, 5)
    st = stats([t, t2], [], 0.05, span_days=365)
    assert st["n"] == 2 and st["win_rate"] == 0.5, st
    assert abs(st["expectancy"] - 900.0) < 1e-6, st
    assert st["profit_factor"] > 2.5, st
    assert st["peak_concurrent"] == 2, st
    assert st["exposure_pct"] <= 100.0, f"exposure above 100% is impossible: {st}"

    # single same-day trade: one day of exposure over a one-day span
    solo = backtest.Trade("S", d0, d0, d0, 100, 101, 10, 1.0, "target",
                          10, 1, 9, 0.9, 1)
    assert stats([solo], [], 0.0)["exposure_pct"] == 100.0, stats([solo], [], 0.0)
    assert st["exits"]["target"] == 1 and st["exits"]["stop"] == 1
    assert stats([], [], 0.0)["n"] == 0
    m = monthly([t, t2])
    assert m["2024-01"] == 1800.0, m

    # signals before start_day are excluded, but their bars still seed indicators
    ser = features.Series("W")
    for k in range(400):
        px = 100 + k * 0.5
        ser.days.append(date(2024, 1, 1) + timedelta(days=k))
        ser.open.append(px); ser.high.append(px + 1)
        ser.low.append(px - 1); ser.close.append(px)
        ser.volume.append(1000); ser.turnover.append(1e9)
        ser.deliv_pct.append(50.0); ser.surveillance_known.append(True)
    cutoff = date(2024, 1, 1) + timedelta(days=390)
    import spec as specmod
    allsigs = backtest.generate(specmod.STAGE2_BREAKOUT, {"W": ser}, {})
    late = [x for x in allsigs if ser.days[x[0]] >= cutoff]
    assert len(late) <= len(allsigs), "filter must not add signals"
    print("report selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
