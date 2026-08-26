# STATE — read this first

Handoff document. If you are a person or an assistant picking this up with no
chat history, this file plus `lessons.md` and `CLAUDE.md` is the context.

Last updated: 2026-08-26 — **H13 run and closed** (`src/research/candle_test.py`,
batch 20260826-candles-h13, L74): candlestick gates on the breakout signal bar
are inside the noise (strong_close +0.77% ± 1.64, t +0.47; bar was 2.6) — the
first of the four untouched price-action families from the operator's chart-pattern
review. TRIGGER stays `breakout`; remaining untested families are fair-value
gaps, retracement depth and swing structure.

Previous: 2026-08-25 — **H12 registered** (`src/research/impact_calibrate_test.py`,
batch 20260825-h12-calibrate): impact calibration off the forward book, the one
survivor of a TradingAgents/ABIDES leverage analysis (L73). The announcement
sentiment line it initially proposed was ALREADY CLOSED by L66/L68 — re-running
it is forbidden; sentiment.py's own docstring caveat is stale relative to
lessons.md and lessons wins. H12 compares closed MAIN paper trades (real opens,
true impact) against simulated arms at c ∈ {0, 0.5, 1, 2, 3}; gates at n=25
descriptive / n=100 verdict; no adoption path, rebaseline stays the operator's.
Currently 0 closed cohort trades → "too early". Same day: the morning fill left
`data/positions_record.sql` stale (audit trail 14 vs 13); regenerated via
`positions.export_record()`, audit 38/38.

Previous: 2026-08-24 — a THIRD book exists: the etf_trend fund book
(`src/strategies/etf_trend/`, `data/etf_trend/`, launchd label
`com.sudhanshu.tradingbot.etf_trend`; renamed from `trend` the same day, before
its first fill, so no ledger row moved). It trades liquid NSE funds with the
rules pre-registered in `src/research/trend_fund_test.py` — absolute trend gate
(close > SMA200, positive ~6-month return), SMA100 trend-break exit, −10%
stop, 5 equal seats, Rs 3L. **Its backtest FAILED its promotion bar**
(+1.04% ± 1.08% per trade; edge vs control t = +1.19, L70), so this book is a
FORWARD EVIDENCE GENERATOR, not a validated strategy: its rules are frozen at
their registered values and may not be tuned now that trades are accumulating.
It shares no ledger, order book or scheduler with the equity books; it runs
after market close on weekdays (18:45 + 19:45 IST) and is idempotent per
session. Status/queue: `scripts/run_etf_trend_paper.sh --status`. Same day also
closed the data-integrity family: unadjusted corporate actions touched ONE
recorded trade in six years (L71) and counterparty-less fills are measured by
`src/research/suspension_probe.py` with an inert-by-default `tradable` hook in
`simulate.run`.

A second filings source exists: BSE's published RSS announcements feed
(`src/core/bse_announcements.py`, agent job "bse" daily after 18:00), covering
the 228 symbols NSE's feed omits — forward-only by construction, gated at read
to empty-NSE-timeline symbols so dual-listed filings never count twice.
Fundamentals now display as a third channel on /sentiment (context, never
blended). Full probe record in L72a.

Previous: 2026-08-23 — the point-in-time non-equity gap (L69). The
denylist could only ever see funds that were still trading, so 87 delisted
ETFs sat inside the historical micro and small clusters. Removing them takes
the backtest from **+7.59% to +2.42% CAGR**. **The baseline has NOT been
re-recorded** — `audit.py` fails on it deliberately and `--rebaseline` is the
operator's call.

The three strategies were also renamed for what they do: `sprout` → `breakout`,
`thicket` → `sentiment`, `trellis` → `patterns`.
---

## The approach (this is the whole system)

    NSE equities, point-in-time
      -> rank the whole universe by turnover, keep the least-liquid 67%
         (clusters.TRADEABLE_PCT), split that into micro and small
      -> rank WITHIN each cluster, take the top 20 of each
      -> bucket = 3 micro + 2 small = 5 stocks
      -> entry: breakout trigger, filled at the NEXT session open
      -> exit: -10% stop / +20% target / 10 trading days
      -> analyse per stock and per bucket -> record findings -> Telegram

**Vocabulary.** A *cluster* is a size band (micro, small). A *bucket* is the
five stocks held and their combined P&L. Never swap these — the confusion
already caused one wrong build. Do not say "slot"; say stock or position. Never
"portfolio", "book" or "holdings" (rules.md R1). Plain-English definitions of
every term in this file are in `glossary.md`.

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

**RECORDED: +7.59% CAGR, 31.0% max drawdown, 195 trades.** That is what
`data/breakout/baseline.json` still says, and it is **known to be wrong** as of
2026-08-20.

**MEASURED NOW: +2.42% CAGR, 32.5% max drawdown, 193 trades**, per trade
+1.07% +/- 1.12% (batch 20260820-nonequity3). The gap is L61: the non-equity
denylist was built from one snapshot, so it could only see funds that were
still trading, and 87 delisted ETFs stayed in the historical universe. 22 of
the recorded 195 trades were gold, silver and index ETFs, 16 of them in the
last block alone — **68% of the recorded CAGR was never this strategy's.**
Drawdown got slightly worse, so it was not a risk that paid; it was a
different asset class.

**The edge per trade is now +1.07%, and that is the number with teeth.** It
has gone 3.07% -> 2.15% -> 1.07% across two corrections, and trades needed to
resolve it scale with the square: 105 -> 213 -> **859**, about 30 years at
this book's recorded pace of ~29 trades a year (`overview.py` computes this
from the baseline rather than from an occupancy estimate -- see L61). Forward paper trades remain the only
thing that shortens that, and the count is still zero.

The recorded figure was NOT overwritten. `audit.py` fails on the drift on
purpose:

    [FAIL] baseline drift is proportionate to new data
           corpus grew 1 session(s); CAGR +7.59% -> +2.42% (moved 5.17)

(The message says "new data" because that check owns drift generally; the
cause here is the fix, not the extra session.)

Re-record it deliberately, in its own step, once the correction is accepted:

    python3 src/ops/audit.py --rebaseline

Everything below and everything in CLAUDE.md tagged `20260819-postlock` was
measured on the contaminated universe. `remeasure.py` has been re-run under
`20260820-nonequity3`; `trigger_test`, `rank_test`, `weight_test` and
`impact_test` have NOT, and their numbers should not be quoted until they are.

It is a BACKTEST either way. It is not evidence the approach works forward.

This replaced **+14.14% / 25.8% / 232** on the same corpus -- the figure the
audit itself printed when the guard broke the recorded baseline. (+14.18% /
n=143 appears in L51/L52 and is a DIFFERENT measurement, taken at the capital
and cost settings of the time; it is not this baseline's predecessor.) The difference is
not a rule change: it is 8.7% of fills that were taken on circuit-locked bars
where no seller existed at any price. Drawdown got WORSE while return halved,
which is how you can tell the removed fills were disproportionately winners.

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

## Where the code lives (2026-08-19)

The strategy is named **breakout**. Its RULES are the four files in
`src/strategies/breakout/` -- `clusters.py` (size bands and the score),
`selection.py` (the bucket and the exit rules), `entry.py` (the breakout
trigger), `learning.py` (the weights). Its OUTPUTS are `data/breakout/` --
`weights.json`, `baseline.json`, `trade_features.jsonl`, `strategies.jsonl`,
`simulations.jsonl`, `findings.jsonl`, `occupancy_baseline.json`.

Everything else is shared and strategy-agnostic: price data
(`src/core/features`), the fill-and-cost engine (`src/core/engine`), the
backtest harness (`src/research/simulate`), the order book (`positions.db`), the
Telegram bot, the audit.

All source moved under `src/` on 2026-08-20. Nothing is left at the root but
`CLAUDE.md` and `README.md`: the four entry points are `src/ops/` (`daily.py`,
`tg.py`, `agent.py`, `overview.py`), shell is `scripts/`, and `paths.py` sits in
`src/`.

**`src/paths.py` is where it is for a reason.** Every module bootstraps with
`sys.path.insert(0, parents[1])` then `import paths`, so `parents[1]` from
`src/core`, `src/bucket`, `src/research` and `src/ops` has to land on the
directory holding `paths.py`. While that file sat at the root and the source did
not, all 23 of those lines pointed one level too shallow -- and every selftest
still passed, because the shell running them exported `PYTHONPATH=.` and the
children inherited it. Moving the file fixed 23 modules with no edits;
`src/strategies/breakout` is one deeper and uses `parents[2]`. The sweep now
strips `PYTHONPATH` from its children so it can never pass on the operator's
shell again:

    python3 tests/run_selftests.py

`paths.py` picks the active strategy and puts ONLY its directory on `sys.path`:

    STRATEGY=other python3 src/ops/audit.py

Two strategies both define `selection`; if both were importable, `import
selection` would resolve to whichever came first and every number after that
would describe a bucket nobody chose. `paths._selftest()` asserts that no
inactive strategy is reachable. No import statement anywhere changed when the
files moved -- that is what `paths.py` was built for.

Anything that SPAWNS a script rather than importing it goes through
`paths.script()`. `agent.py` runs `python3 <path>` in a subprocess whose failure
lands in a log nobody reads, so a stale string leaves the scheduler reporting
healthy while nothing runs; its `_selftest` asserts every job path exists on
disk, and it caught exactly this the day the files moved.

**The order book is deliberately NOT strategy-scoped.** `positions.db` is real
money and one bucket. If a second strategy ever trades forward, that is a
decision to take deliberately, with the `origin` column, not by a folder move.

## The live books — two, `main` and `pooled`

Since **2026-08-21** two books run forward side by side inside breakout, on the
same signals, the same stops and Rs 3,00,000 each:

| key | shown as | rule |
|---|---|---|
| `main` | **bucket** | ranks inside each size band, fills a 3/2 quota |
| `pooled` | **pool** | ranks every eligible name together, takes the best five |

One variable differs -- how the five seats are allotted -- so a divergence has
one cause. They run forward because no backtest can separate them: +0.04% per
trade at t = +0.03 (L65). The pool's first order was KENNAMET, queued
2026-08-21, filling at Monday's open.

**`main` is still the record.** `overview.py`, the recorded baseline and every
statistic key off it; the pool's trades are shown and never counted, and
`learning.for_weights()` keeps the pool out of the weights so it cannot feed
back into the bucket's own picks. The audit enforces that nothing is queued
outside the REGISTERED buckets -- widened from `{main}` on 2026-08-21, since the
pool's first order would otherwise have failed it, and still rejecting any
unknown name.

**A multi-bucket variant was explored and removed within the day.** The
arithmetic that motivated it is real: one bucket makes ~71 trades a year and
~105 are needed before a 3%/trade edge is resolvable, so several DISJOINT
cohort buckets -- same rules, deeper slices of the same ranking -- would pool
as near-independent samples and cut time-to-evidence from ~1.5 years to ~5
months (L54). They were never a parameter search: every bucket ran identical
rules, so there was nothing to select between them, and adopting a leader would
contaminate the one evidence stream a search cannot reach (L47; PBO 0.929 in
L41).

**But the deeper cohorts buy ranks the score already marks as worse** --
-1.12% per rank step, +5.64% between the top cohort and the deepest across six
disjoint cohorts (1,062 trades, batch 20260820-nonequity3; -1.18% and +6.63%
post-guard, -0.90% and +6.41% pre-guard -- two data corrections and the slope
has barely moved) -- and knowingly trading picks you believe are
worse in order to gather evidence faster is not a trade this book makes. So
they were removed (L56). `overview.py` still carries the note "two positions
opened by the retired deeper buckets"; the cohort machinery now survives only
in `src/research/rank_test.py`, which slices cohorts to MEASURE that rank decay,
never to trade them.

**The tighter-stop question is a counterfactual, not a bucket.**
`positions.shadow_stop()` asks whether a 5% stop would have been touched between a
real entry and its real exit, on the record bucket's own positions. That is exact
-- same entry price, same bars -- and needs no second order.

A `tight` BOOK was built for this and removed within the day. To be a paired
test it must enter the same name at the same price on the same day as main, and
a separately-queued book cannot: it queues when IT has room, so it entered a
position main had already held for a session and was 3.2% into. That is
chasing, not pairing, and it also put a duplicate order in `/pending_orders` for a
name already live -- which is how it was caught.

The endpoint is a PROPORTION, resolvable in ~62 trades, where comparing the two
stops on RETURN would need 238 per arm. The simulator predicts 62% of positions
stop out at 5% against 37% at 10%. If forward reality disagrees, the fill and
gap model is wrong. It can never say which stop is better: once a tighter stop
fires the paths diverge, and that divergence is deliberately not modelled.

Capital is Rs 300,000, notional. Even if the cohort buckets ever returned, they
could not be run as a single Rs 15,00,000 book -- several buckets trading these
microcaps simultaneously would move the very prices they are measuring.

## The order record is append-only (2026-08-19)

`data/positions.db` holds ONE table, `pos`, with a `status`, and three views to
read it by the names the operator uses:

| view | rows |
|---|---|
| `pending_orders` | `status='pending'` -- queued, enters at the next open |
| `open_orders` | `status='open'` -- running |
| `closed_orders` | `status='closed'` -- exited on a rule. THE forward evidence |

**Three separate tables were asked for and cannot work.** A pending order that
fills has to leave `pending_orders` and appear in `open_orders`, and leaving a
table is a DELETE -- which the no-delete rule forbids. One table with a status
and three views gives the three names without ever moving a row.

Four rules live in the DATABASE -- triggers and one index -- not in
`positions.py`, because the file is also opened by the sqlite3 CLI and by ops
scripts, and a rule enforced in Python is a rule the next writer does not
inherit:

- `pos_no_delete` -- a position may be EDITED, never deleted.
- `pos_log` + `pos_log_ins`/`pos_log_upd` -- every insert and every edit
  snapshotted as JSON, forever, and itself undeletable. Append-only without an
  edit trail is half a guarantee: a row that can be blanked is a delete in
  disguise. The trigger SQL is generated from `PRAGMA table_info`, so a column
  added by migration cannot quietly fall out of the trail.
- `ux_pos_live` -- UNIQUE(symbol) WHERE status IN ('pending','open'). One live
  row per symbol, whatever the bucket. Re-entry AFTER an exit is still allowed.
- `status='void'` -- a fourth status, for an order that should never have been
  placed. It appears in NONE of the three views and in none of `summary()`'s
  counts, so a mistake never contributes a return to the forward evidence.

**What this was built for.** The `pbook.db -> positions.db` rename left an empty
database. Three open positions stayed behind in the old file, and because the
new one had no memory of them the daily run bought HAPPYFORGE a SECOND time --
already open since 2026-08-17 at 2,131.20, bought again on 2026-08-19 at
2,280.00, 7% higher. The dedup in `queue()` could not see it: it only ever
consulted rows that were in the file. That is why the rule is now an index.

`src/ops/restore_orphans.py` recovered the three positions into the one bucket,
dropped the `third`/`fourth` labels and retired the duplicate as `void`. It
copies prices from the old file rather than restating them, is idempotent, and
asserts the mix, the position count and the deployment cap before committing --
rolling back if any of them fails.

`origin` records which ranking produced a position: NULL for the score's own
picks, `rank-cohort` for the two recovered from the retired deeper buckets. The
bucket label is gone, but those two bought ranks the score marks as worse
(-1.12%/step), so when they close they must not read as evidence for a
selection that did not make them. Nothing filters on it yet.

The bucket now holds 4 of 5: HAPPYFORGE, GMMPFAUDLR (small), SAHYADRI, YUKEN
(micro), Rs 179,501 of the Rs 225,000 cap. Still 0 closed trades.

## Two more strategies exist, and neither changes anything (2026-08-21)

`sentiment` and `patterns` live beside `breakout` under `src/strategies/`, with their
own `data/<name>/`. **Both are behavioural clones of breakout with every new rule
switched off**, so neither alters the live bucket, and `tests/clone_reproduces.py`
asserts each still reproduces whatever breakout produces *right now* — measured
side by side in child processes rather than against a number recorded weeks ago.

| strategy | what it adds | switch | state |
|---|---|---|---|
| sentiment | NSE corporate announcements as a score input | `clusters.ANN_FEATURES` | off; nothing cleared the bar |
| patterns | structural exit, and named chart patterns | `selection.STRUCTURAL_EXIT`, `TRIGGER` | off; nothing cleared the bar |

**The operator's condition was that breakout must not be impacted**, and that is
enforced rather than promised: `tests/breakout_untouched.py` runs in the sweep and
hashes breakout's rules, weights and headline, refuses a file being added to
`breakout/`, checks no strategy or research module can *reach* the live order book,
and confirms a non-breakout `STRATEGY` resolves its data outside `data/breakout`.

### The data that arrived with them

`src/core/announcements.py` — **1,019,495 NSE announcements**, 360 weeks,
2,640 symbols, 99.96% parsed, in `data/announcements/` (gitignored, ~1 GB,
refetchable via `backfill()`). `data/announcements/tone_table.json` is the frozen
category→sign table and **is** tracked: it is evidence.

**60% of announcements arrive after the 15:30 close.** Dated by calendar day —
the obvious way — that fraction of the signal is information nobody had when the
trade was placed. `announcements.visible_from()` is the whole point of the file
and the selftest asserts the 22:56 case by name. On real data 65% roll forward.

`src/ops/newswatch.py` runs daily from the scheduler, appending market headlines
to `data/news/`. It has no history, no backtest may read it, and its selftest
asserts no research or strategy module imports it. It exists only to accumulate.

### The `sentiment` skill (2026-08-22)

`scripts/claude/skills/sentiment/SKILL.md`, installed with the `cp` in
`scripts/claude/README.md`. Adapted from `sentiment-analysis` in
[tradeinsight-info/investment-analysis-skills](https://github.com/tradeinsight-info/investment-analysis-skills)
— their rubric, bands and report shape; their data replaced, because their three
channels (NewsAPI, StockTwits, r/wallstreetbets) carry essentially nothing on an
NSE microcap. The channels here are the announcement corpus and `data/news/`.

**`src/ops/sentiment.py` decides what was VISIBLE; the skill decides what it
MEANS.** The first is reproducible — same date in, same evidence out, 15:30 rule
applied. The second is a model's judgement and is not, which is why the skill
may never feed a measured result and why its selftest asserts no research or
strategy module imports it.

    python3 src/ops/sentiment.py 20MICRONS
    STRATEGY=sentiment python3 src/ops/sentiment.py --picks

It is an operator's view of today. `ann_tone` is the measured version.

### Five hypotheses, five negatives

Pre-registered with the bar at **|t| ≥ 2.6** (the usual 2.0 tightened across five
tests) *before* any data was downloaded. See L61.

| | result | |
|---|---|---|
| ann_burst | +0.37%, t +1.19 | how often a company files carries nothing |
| ann_tone | +1.24%, t +1.71 | read +2.20 on the first draw; a resampling moved it |
| ann_flag | −0.29%, t −0.40 | inside the noise |
| structural exit | +0.33%, t 0.22 | holding 30 days flat gained 4× as much |
| patterns | +0.43%, t 0.21 | `none` lost 1.97%, so it is not just looseness |
| candle gates on the signal bar (H13, 2026-08-26) | strong_close +0.77% ± 1.64, t +0.47 | L74; a 20-day-high close is in its bar's top half 83% of the time — barely a filter; engulf/inside too rare at breakouts to test (n=9/25) |

Nothing adopted. The one live lead is post-hoc and unconfirmable here: the whole
tone effect is **positive** corporate actions (+1.77% vs neutral, n=255), with
bad news carrying nothing. Settling it forward needs ~400 trades — over a decade
at this book's rate.

**An open gap, stated so it is not decided by accident.** `asc_triangle` ran at
**10.4% max drawdown against breakout's 31.0%** (n=145), and `cup_handle` at
10.0% (n=30). There is no pre-registered adoption path for drawdown — the same
hole L62 records for bucket size — so it is left undecided rather than claimed.

## What has been tested and REJECTED

Do not re-add these without evidence that addresses the stated reason.

**Every CAGR in this table is a pre-guard level and none of them may be
quoted.** (L58: 8.7% of fills were on circuit-locked bars.) Each was compared
against a baseline of +14.14%, not the +7.59% above -- so a row reading "+8.99%"
meant *5 points worse than live* when it was written, and read against today's
live number it would say the opposite. Subtracting the difference does not fix
that: the guard did not remove a constant, it removed the big up-day fills, and
it removed a different share of them from every variant.

What survives is the **ranking and the reason**, which is what a rejection
actually is. Re-run a row before using its number for anything. The four knobs
that WERE re-run post-guard are in L59, and all four kept their ordering --
which is the evidence that the rankings here are probably still good, and no
evidence at all about the levels.

| idea | result | why rejected |
|---|---|---|
| correlation cap on holdings | +8.99% at 0.7, +7.87% at 0.3 | monotonically worse, and drawdown rose too |
| position floor (min 2/3/4) | +11.45 / +12.86 / +10.71% | all worse than no floor; non-monotonic = noise |
| no trigger, always hold 5 | **+2.58%** (L61 batch; -2.20% post-guard, +8.88% pre) | the one row here that has been re-run twice, and it has given a different verdict each time. It now roughly TIES the +2.42% live bucket on return and loses badly on risk: 43.0% drawdown against 32.5%, on 291 trades against 193. The trigger is a risk rule again |
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

launchd runs `src/ops/agent.py --once` hourly. On a weekday after 18:00 it does
`snapshot -> catchup -> pbook`. The Telegram listener runs under launchd as
`com.sudhanshu.tradingbot.telegram` (KeepAlive) and must be restarted after ANY
code change:

    launchctl kickstart -k gui/$(id -u)/com.sudhanshu.tradingbot.telegram

`scripts/run_listener.sh` is the by-hand fallback only.

**Both plists were reinstalled and the stale-path item is closed.** They named
the deleted root `tg.py` and `agent.py` after the `src/` move, and the agent one
was installed under its repo filename rather than its Label, so `launchctl list`
found nothing even with the file present. Both now resolve, both are registered,
and `audit.py` passes 35 of 35 -- it was the one failing check, and it was
failing about a real thing.

Retired work (the spec-search track: generator, pipeline, judge, holdout
ledger) is archived in `data/retired/` and deleted from the tree. It never
held a position.
