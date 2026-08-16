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

# Cash is a position. A fully-invested book has no capacity to add when a
# better setup appears mid-cycle, and no buffer when five correlated names gap
# down together. Deploying 60% of Rs 5L caps any single name at Rs 60k, which
# at a 10% stop puts total open risk at 6% -- exactly engine.MAX_PORTFOLIO_HEAT,
# arrived at independently.
DEPLOY_PCT = 60.0


def position_size(capital, entry, stop_pct=STOP_PCT, risk_pct=RISK_PCT):
    """-> (qty, rupees_at_risk). Risk-based, then capped so one name cannot
    exceed its share of the book."""
    risk_rupees = capital * risk_pct / 100
    risk_per_share = entry * stop_pct / 100
    if risk_per_share <= 0:
        return 0, 0.0
    qty = int(risk_rupees / risk_per_share)
    cap_value = capital * DEPLOY_PCT / 100 / MAX_POSITIONS
    qty = min(qty, int(cap_value / entry))
    return qty, qty * risk_per_share


# Percentile bands for prose. A rank is only meaningful against its cluster --
# "85th percentile on delivery" means among names of comparable turnover, not
# against the whole market.
def _why(r):
    """-> plain-language reason this name ranked where it did."""
    if not r:
        return "no rank detail"
    label = {"rs": "relative strength", "deliv": "delivery %",
             "liq": "liquidity", "near_high": "near its high"}
    strong = [label[f] for f, v in sorted(r.items(), key=lambda kv: -kv[1]) if v >= 70]
    weak = [label[f] for f, v in r.items() if v <= 30]
    parts = []
    if strong:
        parts.append("top-30% in " + ", ".join(strong))
    if weak:
        parts.append("weak on " + ", ".join(weak))
    if not parts:
        parts.append("mid-pack on every feature")
    return "; ".join(parts) + " (above 200-DMA, else excluded)"


TRIGGER = "breakout"    # see trigger_test: near-identical CAGR to no trigger
                        # (+11.45 vs +12.53) but worst block -83.1% vs -120.5%.
                        # Ranked on worst block, which is the ranking that has
                        # generalised here, the control is LAST of seven.


def build(corpus, as_of, capital=CAPITAL, trigger=None):
    """-> list of candidate positions, best-scored first, across clusters.

    `trigger` gates WHETHER to buy today; the score only says what to buy.
    """
    import entry
    fn = entry.TRIGGERS[trigger or TRIGGER]
    # MARK, do not filter. Filtering here would drop untriggered names before
    # ranking, so allocate() would reach further down the list to fill its five
    # slots -- buying a worse name because it happened to trigger. Measured:
    # that variant returns +7.48% / 37.9% DD against +11.45% / 23.8% for
    # marking. Rank first, then require the trigger, then hold cash if the best
    # names are not ready.
    picks = clusters.pick(corpus, as_of, per_cluster=20)
    ranks = {}
    for b, syms in clusters.size_buckets(corpus, as_of,
                                         names=clusters.BUCKET_NAMES).items():
        ranks.update(clusters.score(corpus, syms, as_of, with_ranks=True)[1])
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
                "triggered": bool(fn(s, i)),
                "why": _why(ranks.get(sym, {})),
                "ranks": ranks.get(sym, {}),
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
    # ROUND-ROBIN, not bucket-blocks. Returning micro's picks first and then
    # slicing rows[:room] took two micro whenever only two slots were free and
    # never reached small or mid -- the book traded 178 micro / 28 small / 3 mid
    # against a 2/2/1 design. Interleaving means any prefix of the result is
    # still spread across clusters.
    if rows and not (set(per_bucket) & {r["bucket"] for r in rows}):
        raise ValueError(f"per_bucket {sorted(per_bucket)} matches none of the "
                         f"buckets present {sorted({r['bucket'] for r in rows})} "
                         "-- this would silently allocate nothing")
    per = {b: [r for r in rows if r["bucket"] == b][:k] for b, k in per_bucket.items()}
    out, depth = [], max(per_bucket.values())
    for d in range(depth):
        for b in per_bucket:
            if d < len(per[b]):
                out.append(per[b][d])
    # Trigger LAST, after the interleave. Dropping untriggered names earlier
    # changes which bucket supplies each slot, because the round-robin walks
    # shortened lists -- same rules, different book (+9.05% vs +11.45%). The
    # trigger must remove candidates from the final order, never reorder it.
    return [r for r in out if r.get("triggered", True)]


def _selftest():
    q, risk = position_size(500_000, 100.0)
    # The DEPLOY_PCT cap binds before the risk rule at default parameters:
    # 60% of 5L over 5 names = Rs 60k, i.e. 600 shares at Rs 100, risking
    # Rs 6,000. The 2% risk rule would have allowed Rs 1L / 1,000 shares, so
    # risk_pct is currently DORMANT -- it only starts binding above a ~16.7%
    # stop. Kept deliberately: it is the backstop if stops ever widen.
    assert q == 600 and abs(risk - 6_000) < 1, (q, risk)
    cap = 500_000 * DEPLOY_PCT / 100 / MAX_POSITIONS
    assert abs(q * 100.0 - cap) < 100, (q * 100.0, cap)
    # a full book must leave cash on the table
    assert cap * MAX_POSITIONS <= 500_000 * 0.75, "book must not be fully invested"
    q2, _ = position_size(500_000, 10.0)
    assert q2 * 10.0 <= cap + 1, q2 * 10.0
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

    # ANY PREFIX must stay spread: this is what the slice bug violated
    for room in (1, 2, 3, 4):
        pre = allocate(fake)[:room]
        buckets = {r["bucket"] for r in pre}
        assert len(buckets) == min(room, 3), (room, [r["bucket"] for r in pre])
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
