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
      -> exit: -10% stop / +20% target / 10 trading days
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

**+14.18% CAGR, 25.8% max drawdown, 231 trades** over 1696 sessions
(2019-10-01 to 2026-08-17), with impact at c=1.0, at the adopted 10-day hold.
It is a BACKTEST. It is not evidence the approach works forward.

Per trade: +2.96% +/- 1.93 (std err 0.98, t 3.01, n=231).

The 15-day hold this replaced gave +13.54% / 28.8% / 217 trades on the same
corpus. The change was adopted on L51/L52: 10 days beats 15 on CAGR, drawdown,
worst half-year block (-49.4 vs -83.6%) and CAGR-per-drawdown, with a per-trade
difference of -0.11% (t -0.07). `audit.py` now fails loudly if the exit rules
move without the baseline being re-recorded on purpose.

Occupancy: the book holds 2.83 stocks on average. Distribution:
  0 stocks:   1.8% of sessions
  1 stocks:  17.3% of sessions
  2 stocks:  22.6% of sessions
  3 stocks:  25.6% of sessions
  4 stocks:  19.9% of sessions
  5 stocks:  12.8% of sessions

A book holding 1 stock is normal, not broken.

## Live books — five, running in parallel

Trade count is the binding constraint: one book makes ~71 trades a year and
105 are needed before a 3%/trade edge is resolvable at all. Five books cut
"is there an edge?" from ~1.5 years to ~5 months.

| portfolio | what it is | counted together? |
|---|---|---|
| `main` | the record. STATE.md, `overview.py` and the audit key off THIS book only | ⭐ |
| `cohort1..3` | same rules, deeper slices of the same ranking. Disjoint by construction | yes |

**Every book runs identical rules.** There is no variant book and the audit
enforces it: a book with its own parameters is a competitor, side-by-side
competitors are a leaderboard, and a leaderboard gets picked from.

**The rank books multiply evidence without creating a choice.** Same rules,
different depth in the ranking, so their positions never overlap and their
trades pool as near-independent samples. They answer the question that matters
-- does the score rank? -- faster, and there is nothing to select between them.

**They are NOT a parameter search, and this is not a style preference.**
Comparing two parameter settings on RETURN needs 238 trades per arm (3.4 years)
for the largest gap ever measured here, 2,856 (40 years) for the ladder, and
162,554 for the 10d-vs-15d hold. Parallel books do not help: each arm still
needs its own sample. Running variants forward and adopting the leader would
contaminate the one evidence stream a search cannot reach (L47; PBO 0.929 in
L41). `audit.py` checks the books stay disjoint and that no variant book is
pooled.

**The tighter-stop question is a counterfactual, not a book.**
`pbook.shadow_stop()` asks whether a 5% stop would have been touched between a
real entry and its real exit, on the record book's own positions. That is exact
-- same entry price, same bars -- and needs no second order.

A `tight` BOOK was built for this and removed within the day. To be a paired
test it must enter the same name at the same price on the same day as main, and
a separately-queued book cannot: it queues when IT has room, so it entered a
position main had already held for a session and was 3.2% into. That is
chasing, not pairing, and it also put a duplicate order in `/next_orders` for a
name already live -- which is how it was caught.

The endpoint is a PROPORTION, resolvable in ~62 trades, where comparing the two
stops on RETURN would need 238 per arm. The simulator predicts 62% of positions
stop out at 5% against 37% at 10%. If forward reality disagrees, the fill and
gap model is wrong. It can never say which stop is better: once a tighter stop
fires the paths diverge, and that divergence is deliberately not modelled.

Capital is Rs 300,000 per book, notional. They are alternative hypothetical
portfolios, not a Rs 15,00,000 book -- five books trading these microcaps
simultaneously would move the prices they are measuring.

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
| stop 5% (fixed) | +0.04% CAGR, 27% win | 62% of positions stop out; 5% is inside these names' daily range (L49) |
| hold 6-8 sessions WITH a 5% stop | -1.93 to -0.15% | the stop is the effect, not the hold |
| target 10% or 15% at a 5% stop | -4.81 / -6.98% | a tighter target does not rescue a stop hit by noise |
| ATR-scaled stops (1.5-3.0x) | +2.89 to +7.33% | all inside the noise vs baseline; 2.5x ATR = a 10.7% median stop, i.e. what the book already uses (L49) |
| stop to entry at half the target | +8.27%, maxDD 33.2% | RISKIER, not safer: worst block -121.5% vs -83.6%, win 49->40%. Damage is monotone in how often it fires: 10/34/110 firings cost 0.4/5.3/9.1 CAGR points (L51) |
| multiple targets (ladder) | +9.92 to +6.57% | monotone in rung count: 83->340 partial orders cost 3.6->7.0 CAGR points. Two rungs DO cut the tail (-57.8% vs -83.6%) -- a real trade, not a free win (L51) |
| ladder AND stop move together | +4.63%, maxDD 32.9% | worst of everything tested; the stop move fires on the same pullbacks that the ladder was protecting (L51) |
| skip-month momentum (21d / 42d) | +10.39 / +11.05% | 3-4 CAGR points worse; the breakout trigger already fires on recent strength, so skipping it in the score puts ranking and timing in disagreement (L53) |
| MAX / lottery screen (drop top 10-20%) | +14.31 / +15.23% | LOOKS like a win on CAGR and per-trade; worst block -126.7% vs -49.4% because it cuts breadth 143 -> 118 symbols (L53) |
| pooled ranking (all stocks in one pool) | +16.61% CAGR, 0.553 CAGR/DD | wins headline return, loses tail (-119.4%), concentration (15.4% in one name), breadth (119 vs 136 symbols) and the recent 30-session replay |

**Shortening the hold alone is the one change that did NOT fail**, and it is
now measured across eight settings at the current 10% stop (L52). Nothing is
statistically resolvable, but the tail shortens monotonically with the clock:
worst half-year block runs -21.2% at 5 days to -86.3% at 20. Of the baseline's
70 target hits, 70% land by day 8 and 83% by day 10; the median lands on day 6.

CAGR-per-drawdown peaks at 10 days (0.550 vs 0.470 at the current 15). The
6-8 day window asked for is defensible on tail risk; 10 days is where the
risk-adjusted number peaks. Not adopted -- the hold is the operator's design.

Five consecutive negative results. The design is at a local optimum; further
parameter search mostly inflates selection bias. Trial count is ~40 on this
book, and a best-of-40 figure is inflated by construction.

## What is NOT established

- **No forward evidence.** 0 closed paper trades. This is the only
  stream a search cannot contaminate, and it is empty.
- The impact constant is uncalibrated; profitable across c=0.5..3.0, but that
  is a range, not a measurement.

## Live quote sources — researched 2026-08-18

The morning fill needs one thing the evening bhavcopy cannot give: today's
opening price, this morning. Eight sources were checked against one criterion --
can it authenticate UNATTENDED, every weekday, without a person at the keyboard?

| source | auth | unattended | verdict |
|---|---|---|---|
| **Yahoo chart API** | none | YES | **in use.** 220/220 daily opens matched the official bhavcopy exactly |
| Upstox | OAuth browser redirect, expires ~03:30 IST | no | works, but a manual step every morning |
| ICICI Breeze | daily session key | no | their FAQ: daily regeneration is "required as per SEBI regulations" |
| Zerodha Kite | daily manual login | no | plus Rs 2,000/month |
| Angel One SmartAPI | clientcode + PIN + TOTP | YES | the only broker route that scripts, but see below |
| 5paisa Xstream | TOTP | probably | same trade-off as Angel One |
| Groww | bearer token, method undocumented | unknown | docs do not say how tokens are issued |
| nseindia.com/api/* | none | NO | blocked at their edge; see below |
| parse.bot | API key, free tier 200 calls/month | YES | third-party scraper, not an official feed |
| 0xramm/Indian-Stock-Market-API | none | — | Yahoo underneath, plain HTTP from a bare IP, and offline when tested |

**SEBI mandates daily re-authentication for broker APIs.** That is regulatory,
not technical, and it is why every official route needs a person each morning.
TOTP brokers get around it only in the sense that the second factor is a secret
you hold -- which means storing a trading password and TOTP seed on disk. For a
system that only needs to READ prices, that trades a full trading session for
eight hours of earlier visibility. Not done.

**NSE's own API is not reachable and will not be made reachable.** Both curl
(OpenSSL, HTTP/2) and urllib are refused with 403 from www.nseindia.com while
nsearchives.nseindia.com serves us 200 from the same IP in the same second --
so it is neither our address nor our headers. It is TLS/HTTP2 fingerprinting
plus a JS-computed cookie. Getting past it needs JA3 spoofing, a headless
browser or residential proxies, which is deliberately defeating an access
control. Not built.

**The archives host still works and is the source of truth.** Every price in
the 1,696-session corpus, and every evening fill, comes from the official
bhavcopy there. A morning quote source only changes WHEN a fill is recorded,
never at what price -- the day's open is fixed at 09:15.

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
