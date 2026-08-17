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

**Tradetron (tradetron.tech) is a better fit than any broker sandbox.**

| | |
|---|---|
| paper engine | replays real prices — minute OHLC, real option premiums — not a static fill |
| costs | brokerage, slippage, STT, exchange, SEBI, GST, stamp configured once; gross and net shown side by side |
| execution modes | can be run against best-case, worst-case or average fills |
| cost | **the FREE tier is enough**: 1 algo deployment, paper trading executions, and API to connect from other platforms |

**What we would and would not use, feature by feature:**

| feature | needed | why |
|---|---|---|
| private strategy | YES, 1 | the container: holds the instrument list, the condition that reads our signal, sizing and exits |
| public strategy | no | nothing is being published or sold |
| backtest on their platform | no | ours runs here over 1,695 sessions with our own cost and impact model |
| algo strategy deployment | YES, 1 | an undeployed strategy ignores the API entirely |
| stockbag deployment | no | stockbags rebalance to target weights; this book takes per-stock stop / target / time exits |
| paper trading executions | YES | the entire point |
| Live-Auto execution | no | real money. Its ABSENCE on Free is a safety property: the account cannot place a real order |
| trade execution notifications | no | Telegram already does this |
| API to connect from other platforms | YES | how this repo posts signals |

**A correction to the obvious mental model:** you cannot simply "post a paper
trade" through the API. The API sets a runtime VARIABLE; it is not an order
gateway. Tradetron's own API page says it lets you "control the strategy you
create at Tradetron". So a strategy must be built there and deployed in paper
mode, and our signal flips a variable its conditions read. The thinking stays
here; the container has to live there.

(An earlier version of this file claimed Free had 0 deployments and no API.
That was wrong. It came from a page summariser mis-reading the pricing table,
and I then "verified" it by asking the same summariser again, which is not
verification. The table itself is the source.)

The part that matters for THIS system is **API mode**: an external program
generates the signal and Tradetron only executes it. Their own documentation is
explicit that in API mode it "does not process that strategy for checking
conditions" -- the logic stays here.

That is the only workable shape. Tradetron's no-code builder cannot express
this strategy: ranking 1,258 stocks by a percentile composite, inside clusters
that are themselves defined by a rolling turnover percentile, is not a rule
builder's idea of a condition. Under API mode it does not have to be -- this
repo keeps doing the selection and sends the five symbols.

**The real prize is not paper trading, it is calibration.** Running both side
by side measures our fill assumptions against an engine that models slippage
and worst-case fills. `engine.IMPACT_C = 1.0` is currently an educated guess;
a few dozen paired fills would turn it into a measurement. That is worth more
than the paper P&L itself.

Caveats: it is still a simulation, not real orders; it adds a monthly cost and
an external dependency; and it creates a second P&L, so decide up front that
THIS engine stays authoritative and Tradetron is the check on it.

## Decision: do NOT use Tradetron (2026-08-17)

Two reasons, and the second corrects something I claimed earlier.

**1. It exposes the picks.** A "private" strategy is private from other users,
not from Tradetron. They would hold every pick in real time, the exit rules,
and the candidate universe. Only the ranking logic stays here -- but the picks
ARE its output. For a book trading thin micro-caps, the live pick list is the
sensitive asset, not the formula.

**2. The benefit was overstated.** I argued this would calibrate `IMPACT_C`
against something better than a textbook formula. That is wrong: Tradetron's
paper fills are simulated too. Calibrating our model against their model is two
guesses agreeing, not a measurement. Real fill data requires real orders; there
is no simulator shortcut.

What this repo already does is the part that matters -- fills at the actual NSE
opening price, the full cost stack, modelled impact. The remaining gap is order
lifecycle (rejections, partial fills, true slippage) and no simulator closes
it.

**Revisit only if** forward fills start looking implausible against the daily
range, or if the book ever needs intraday stop checking. Neither is true with
0 closed trades.

**The plan, if this is EVER taken further:**

1. Tradetron in API mode on the FREE tier. This repo picks the stocks after
   the close and posts signals; Tradetron executes on paper at real prices the
   next morning.

   The API is a plain GET:
   `https://api.tradetron.tech/api?auth-token=<token>&key=<var>&value=<val>`
   It sets a runtime VARIABLE. Conditions in the strategy read it with the
   `Get Runtime` keyword, so the logic stays here and Tradetron only executes.

   **The open question is dynamic instruments, and it is the one that decides
   whether this works at all.** Our five stocks change every rebalance out of
   1,258. Tradetron's own Amibroker guide hardcodes the instrument. But the
   keyword documentation says `Instrument Name` accepts "one instrument or a
   list of instruments", `Get Runtime` works "when the variable is fetching a
   particular string value", and with a list only "the ones whose condition is
   true" are entered. So the pieces exist; what is NOT documented publicly is
   whether an API-set variable can be keyed per instrument, e.g.
   `key=SIG_HAPPYFORGE`, and read back per stock inside a list strategy.

   Ask support exactly that before building anything. If yes, the shape is:
   deploy one strategy over the candidate list, post one signal per chosen
   symbol after the close. If no, Tradetron can only paper-trade a fixed
   symbol and is not usable for a rotating book.
2. Log both fills for every trade, ours and theirs, and compare. That
   calibrates IMPACT_C against something other than a textbook formula.
3. This engine stays the source of truth for P&L. Tradetron is the control,
   not the record.
4. `Dhan Sandbox` remains the free fallback for testing order plumbing alone;
   it fills at Rs 100 and cannot speak to profitability.

Whether to progress to real money is the user's decision, not a technical one.

## Daily operation

launchd runs `agent.py --once` hourly. On a weekday after 18:00 it does
`snapshot -> catchup -> pbook`. The Telegram listener runs via
`run_listener.sh` and must be restarted after ANY code change.

Retired work (the spec-search track: generator, pipeline, judge, holdout
ledger) is archived in `data/retired/` and deleted from the tree. It never
held a position.
