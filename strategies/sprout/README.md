# sprout

The one live strategy. **Sprout** = small companies just starting to move.

The name is deliberately not `breakout` or `momentum`: both of those are terms
already used *inside* this strategy (the entry trigger, the `rs` feature), and
`docs/rules.md` R1 forbids overloading a word already in use.

## What it does

    NSE equities, point-in-time
      -> split by median turnover into three size bands; the largest is never traded
      -> two tradeable clusters: micro, small
      -> rank inside each cluster: score + 200-day-average gate -> top 20 each
      -> hold 3 micro + 2 small = 5 stocks (one bucket)
      -> buy only on a breakout, filled at the NEXT session's open
      -> Rs 3,00,000 capital, at most 75% deployed (Rs 45,000 per stock)
      -> sell at -10% (stop), +20% (target), or after 10 trading days

Every term above is defined in `docs/glossary.md`.

## What lives here

| File | What it decides |
|---|---|
| `clusters.py` | The size bands, and the score (four features, percentile-ranked inside a cluster) |
| `selection.py` | The bucket: how many from each cluster, the exit rules, which trigger |
| `entry.py` | The breakout trigger itself |
| `learning.py` | The score weights, and the pass that proposes moving them |

Its data lives in `data/sprout/` — weights, the recorded baseline, the trade
ledger, and stored backtest results. Nothing outside this strategy writes there.

## What does NOT live here

Price history, the fill-and-cost engine, the backtest harness, the order book,
the Telegram bot, the audit. Those are shared and know nothing about any
particular strategy — which is what makes a second strategy possible.

## Current measured state

**+7.59% CAGR / 31.0% max drawdown / 195 trades / 47% win / +2.15% ± 1.08% per
trade**, batch `20260819-postlock`, impact constant c=1.0.

Not one of its settings is proven — every knob scores t < 0.5. The one claim
that clears its error bar is that the score's *ranking* works: −1.18% of return
per step down the rank list (t = −4.10). See `docs/performance-change.md`.

**Zero forward paper trades so far.** Nothing here is established.

## Running a second strategy

Copy this directory, change the rules, and point `STRATEGY` at it:

    STRATEGY=other python3 ops/audit.py

Only the active strategy is importable, and only its own `data/<name>/` is
written. The first strategy cannot be affected by the second — not its weights,
not its baseline, not its stored results.
