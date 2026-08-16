#!/usr/bin/env python3
"""Paper portfolio: Rs 5,00,000 across three size clusters.

RULES (fixed here, measured not guessed)
  universe   20 per cluster: micro / small / mid, by median turnover
  selection  composite of 6-month RS + delivery% + liquidity,
             gated on close > its own 200-day average
  entry      next session's OPEN after selection (never the signal bar's close --
             you cannot trade a price that has already printed)
  stop       10% below entry, FIXED
  target     20% above entry
  time exit  15 trading days
  trailing   NONE

Why no trailing stop, despite it being requested: measured across six
configurations at both 3% and 10% stops, every trail LOWERED expectancy. It
lifts win rate (42% -> 48%) by converting losers into smaller losers, while
collapsing target hits from 20% to 8% -- it stops you out of the winners that
pay for everything else. "Guaranteed profit" per trade and positive expectancy
across trades turn out to be opposed here.

Why a 10% stop rather than the 3% asked for: at 3% the stop sits inside these
stocks' daily noise and is hit 70-77% of the time, giving -0.6%/trade. Widening
it is the single change that flips the book positive. Operator agreed 10% now,
3-5% later once entry timing improves -- which is the right order: a tight stop
needs a precise entry to survive.

Sizing: 2% of capital risked per position. With a 10% stop that is a Rs 1,00,000
position per Rs 10,000 risked -- five concurrent, fully invested.
"""
import json
import sys
from datetime import date

import clusters
import features

CAPITAL = 500_000
RISK_PCT = 2.0          # of capital, per position
STOP_PCT = 10.0
TARGET_PCT = 20.0
HOLD_DAYS = 15
MAX_POSITIONS = 5


def position_size(capital, entry, stop_pct=STOP_PCT, risk_pct=RISK_PCT):
    """-> (qty, rupees_at_risk). Risk-based, then capped so one name cannot
    exceed its share of the book."""
    risk_rupees = capital * risk_pct / 100
    risk_per_share = entry * stop_pct / 100
    if risk_per_share <= 0:
        return 0, 0.0
    qty = int(risk_rupees / risk_per_share)
    cap_value = capital / MAX_POSITIONS
    qty = min(qty, int(cap_value / entry))
    return qty, qty * risk_per_share


def build(corpus, as_of, capital=CAPITAL):
    """-> list of candidate positions, best-scored first, across clusters."""
    picks = clusters.pick(corpus, as_of, per_cluster=20)
    rows = []
    for bucket, lst in picks.items():
        for sym, score in lst:
            s = corpus[sym]
            i = s.index_of(as_of)
            if i is None:
                continue
            ref = s.close[i]
            qty, risk = position_size(capital, ref)
            if qty < 1:
                continue
            rows.append({
                "bucket": bucket, "symbol": sym, "score": round(score, 1),
                "ref_close": round(ref, 2),
                "entry_rule": "next session open",
                "stop": round(ref * (1 - STOP_PCT / 100), 2),
                "target": round(ref * (1 + TARGET_PCT / 100), 2),
                "qty": qty, "value": round(qty * ref),
                "risk": round(risk),
                "exit_by": f"{HOLD_DAYS} trading days",
            })
    rows.sort(key=lambda r: -r["score"])
    return rows


def allocate(rows, per_bucket=None):
    """Take the best from EACH cluster, not the best overall.

    Ranking all 60 candidates together and taking the top 5 returned five mid
    caps: liquidity is a scoring component, so the largest bucket wins it
    structurally and the three-cluster design collapses to one. The clusters
    exist to spread exposure across size bands; that only happens if the
    allocation is per band.
    """
    per_bucket = per_bucket or {"micro": 2, "small": 2, "mid": 1}
    out = []
    for bucket, k in per_bucket.items():
        out += [r for r in rows if r["bucket"] == bucket][:k]
    return out


def _selftest():
    q, risk = position_size(500_000, 100.0)
    # 2% of 5L = 10,000 risked; 10% stop on a Rs 100 share = Rs 10 per share
    assert q == 1000 and abs(risk - 10_000) < 1, (q, risk)
    # per-name cap binds on cheap shares: 5L/5 = 1L max value
    q2, _ = position_size(500_000, 10.0)
    assert q2 * 10.0 <= 100_000 + 1, q2 * 10.0
    assert position_size(500_000, 0)[0] == 0
    # a wider stop must reduce size, never increase it
    a, _ = position_size(500_000, 100.0, stop_pct=10.0)
    b, _ = position_size(500_000, 100.0, stop_pct=20.0)
    assert b < a, (a, b)

    # allocation must spread across clusters, not collapse into the richest one
    fake = ([{"bucket": "mid", "score": 90 - i, "symbol": f"M{i}"} for i in range(20)]
            + [{"bucket": "small", "score": 60 - i, "symbol": f"S{i}"} for i in range(20)]
            + [{"bucket": "micro", "score": 50 - i, "symbol": f"C{i}"} for i in range(20)])
    fake.sort(key=lambda r: -r["score"])
    book = allocate(fake)
    got = {}
    for r in book:
        got[r["bucket"]] = got.get(r["bucket"], 0) + 1
    assert got == {"micro": 2, "small": 2, "mid": 1}, got
    print("portfolio selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        c = features.load_corpus()
        days = sorted({d for s in c.values() for d in s.days})
        as_of = days[-1]
        rows = build(c, as_of)
        book = allocate(rows)
        print(f"PAPER PORTFOLIO  Rs {CAPITAL:,}   selection as of {as_of}")
        print(f"entry: next session open | stop {STOP_PCT}% | target {TARGET_PCT}% "
              f"| exit {HOLD_DAYS}d | no trail\n")
        print(f"  {'sym':<13}{'bkt':<7}{'ref':>9}{'entry':>8}{'stop':>9}{'target':>9}"
              f"{'qty':>7}{'value':>10}{'risk':>8}")
        for r in book:
            print(f"  {r['symbol']:<13}{r['bucket']:<7}{r['ref_close']:>9,.2f}"
                  f"{'open':>8}{r['stop']:>9,.2f}{r['target']:>9,.2f}"
                  f"{r['qty']:>7,}{r['value']:>10,}{r['risk']:>8,}")
        inv = sum(r["value"] for r in book)
        rsk = sum(r["risk"] for r in book)
        print(f"\n  deployed Rs {inv:,} of {CAPITAL:,} ({inv/CAPITAL*100:.0f}%)"
              f"   total at risk Rs {rsk:,} ({rsk/CAPITAL*100:.1f}%)")
        json.dump(book, open("data/paper_portfolio.json", "w"), indent=1, default=str)
        print(f"  written to data/paper_portfolio.json")
