# STATE — read this first

Handoff document. If you are a person or an assistant picking this up with no
chat history, this file plus `lessons.md` and `CLAUDE.md` is the context.

Last updated: 2026-08-16 — approach finalised, repo reduced to the single track.

---

## The approach (this is the whole system)

    NSE equities, point-in-time
      -> rank the whole universe by turnover, keep the least-liquid 67%
         (clusters.TRADEABLE_PCT), split that into micro and small
      -> rank WITHIN each cluster, take the top 20 of each
      -> bucket = 3 micro + 2 small = 5 stocks
      -> rank within cluster, keep the top 20 of each
      -> bucket = 3 micro + 2 small = 5 stocks
      -> entry: breakout trigger, filled at the NEXT session open
      -> exit: -10% stop / +20% target / 15 trading days
      -> analyse per stock and per bucket -> record findings -> Telegram

**Vocabulary.** A *cluster* is a size band (micro, small). A *bucket* is the
5-stock portfolio. Never swap these — the confusion already caused one wrong
build. Do not say "slot"; say stock or position.

## Money

| | |
|---|---|
| capital | Rs 300,000 |
| deploy cap | 75% = Rs 225,000 |
| per stock | Rs 45,000 (equal weight) |
| risk backstop | 2.0% of capital, a hard Rs 60,000 cap per position |
| open risk, full book | 7.5% of capital |

`engine.MAX_PORTFOLIO_HEAT` does NOT constrain this path — it is checked only
inside an engine function nothing here calls. Do not cite it as a guard.

## Costs

Brokerage, STT both sides, exchange txn, SEBI, GST, stamp duty, DP charge
Rs 15.93/sell, plus 20% STCG per financial year with losses offset.
Market impact `c * daily_vol% * sqrt(order/ADV)` on both sides, c=1.0.
There is no TDS on resident equity delivery.

## Historical baseline

**+13.57% CAGR, 28.8% max drawdown, 217 trades** over 1695 sessions
(2019-10-01 to 2026-08-14), with impact at c=1.0. It is a BACKTEST. It is not
evidence the approach works forward.

Occupancy: the book holds 3.09 stocks on average. Distribution:
  0 stocks:   1.2% of sessions
  1 stocks:  14.3% of sessions
  2 stocks:  19.9% of sessions
  3 stocks:  21.9% of sessions
  4 stocks:  24.3% of sessions
  5 stocks:  18.5% of sessions

A book holding 1 stock is normal, not broken.

## Live book

{"pending": 1} — capital Rs 300,000.

## What has been tested and REJECTED

Do not re-add these without evidence that addresses the stated reason.

| idea | result | why rejected |
|---|---|---|
| correlation cap on holdings | +8.99% at 0.7, +7.87% at 0.3 | monotonically worse, and drawdown rose too |
| position floor (min 2/3/4) | +11.45 / +12.86 / +10.71% | all worse than no floor; non-monotonic = noise |
| no trigger, always hold 5 | +8.88%, DD 31.4% | 2 pts less return, 7 more drawdown |
| scan every 1-3 sessions | -1.21% to +11.12% | drawdown tripled at daily scanning |
| unequal sizing (invvol / conviction) | 0.435 / 0.431 CAGR-DD | equal weight wins risk-adjusted |
| constant total exposure | impossible | needs Rs 225k in one name; risk rule caps at Rs 60k |
| participation cap on ADV | non-monotonic | 2% cap gave HIGHER max impact than no cap |
| mid cluster in the bucket | -149.9% over 57 trades | the only negative cluster |
| widening/narrowing the tradeable universe | 33%: +4.81, 50%: +10.61, 85%: +5.11, 100%: +6.07 | inverted U peaking at the current 67% |
| pooled ranking (all stocks in one pool) | +16.61% CAGR, 0.553 CAGR/DD | wins headline return, loses tail (-119.4%), concentration (15.4% in one name), breadth (119 vs 136 symbols) and the recent 30-session replay |

Five consecutive negative results. The design is at a local optimum; further
parameter search mostly inflates selection bias. Trial count is ~40 on this
book, and a best-of-40 figure is inflated by construction.

## What is NOT established

- **No forward evidence.** 0 closed paper trades. This is the only
  stream a search cannot contaminate, and it is empty.
- The impact constant is uncalibrated; profitable across c=0.5..3.0, but that
  is a range, not a measurement.

## Brokers for paper trading — researched 2026-08-17

**No Indian broker offers realistic paper trading through an API.** Checked
before choosing one:

| broker | paper trading via API | verdict |
|---|---|---|
| Zerodha (Kite Connect) | none — no sandbox at all | confirmed on their own developer forum |
| Dhan | Sandbox, free, no account needed | fills EVERY order at Rs 100, no live quotes, capital resets daily; their docs say "performance cannot be benchmarked here" |
| Angel One, Upstox, Fyers, Alice Blue, Shoonya, Pocketful | free live APIs | paper trading is web-based and manual, not API-driven |

So a broker cannot replace this book's own fill engine. What the engine already
does is the part that matters: it fills at the REAL opening price from the
official NSE bhavcopy, charges the full cost stack, and models market impact.

What a broker would add is execution realism the engine cannot invent --
rejections, partial fills, margin blocks, true slippage -- and intraday
visibility. Those need REAL orders; every simulator, ours included, is guessing.

**The plan, if this is taken further:**

1. `Dhan Sandbox` to prove the plumbing: that the code can place, track,
   modify and cancel orders correctly. Free, no account. It cannot tell us
   anything about whether the strategy makes money.
2. A free live-data API (any of the above) for intraday prices, so a stop can
   be checked against real ticks rather than the daily low.
3. This engine stays the source of truth for P&L. No sandbox will give an
   honest number.

Whether to progress to real money is the user's decision, not a technical one.

## Daily operation

launchd runs `agent.py --once` hourly. On a weekday after 18:00 it does
`snapshot -> catchup -> pbook`. The Telegram listener runs via
`run_listener.sh` and must be restarted after ANY code change.

Retired work (the spec-search track: generator, pipeline, judge, holdout
ledger) is archived in `data/retired/` and deleted from the tree. It never
held a position.
