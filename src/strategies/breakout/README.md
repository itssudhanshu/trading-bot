# breakout

The one live strategy. **Breakout** = small companies just starting to move.

**Renamed from `sprout` on 2026-08-23, reversing an earlier decision recorded
right here.** That decision said the name must not be `breakout`, because the
word was already the entry trigger and R1 forbids overloading a term in use.

The operator overruled it, and the argument is better than the one it replaced.
R1 forbids two names for one thing, or one name for two DIFFERENT things -- the
`rank2`-beside-`rank 5` failure it was written for. Here the meaning is the same
at both levels: this strategy trades breakouts, and its trigger detects one. A
word used twice for one concept is not a collision, it is consistency, and
`sprout` had the real defect -- it told a reader nothing (R2).

Checked rather than assumed: `import sentiment` still resolves to
`src/ops/sentiment.py` and not to the strategy directory of the same name, and
`paths._selftest` reports the right active strategy with two inactive.

The sibling strategies renamed with it: `thicket` -> `sentiment`,
`trellis` -> `patterns`.

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

Its data lives in `data/breakout/` — weights, the recorded baseline, the trade
ledger, and stored backtest results. Nothing outside this strategy writes there.

## What does NOT live here

Price history, the fill-and-cost engine, the backtest harness, the order book,
the Telegram bot, the audit. Those are shared and know nothing about any
particular strategy — which is what makes a second strategy possible.

## Current measured state

**+2.42% CAGR / 32.5% max drawdown / 193 trades / 46% win / +1.07% ± 1.12% per
trade**, batch `20260820-nonequity3`, impact constant c=1.0.

`data/breakout/baseline.json` still records the older +7.59% / 31.0% / 195 and
the audit fails on the difference on purpose: the point-in-time non-equity
denylist could only see funds that were still trading, so delisted ETFs sat in
the historical universe and the bucket bought 22 of them (L61). Re-recording
the baseline is a deliberate separate step.

Not one of its settings is proven — every knob scores t < 0.6. The one claim
that clears its error bar is that the score's *ranking* works: −1.12% of return
per step down the rank list (t = −3.95), and it survived both corrections
almost unchanged. See `docs/performance-change.md`.

**Zero forward paper trades so far.** Nothing here is established.

## Running a second strategy

Copy this directory, change the rules, and point `STRATEGY` at it:

    STRATEGY=other python3 src/ops/audit.py

Only the active strategy is importable, and only its own `data/<name>/` is
written. The first strategy cannot be affected by the second — not its weights,
not its baseline, not its stored results.
