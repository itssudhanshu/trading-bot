# CLAUDE.md — working agreement for this repo

## The approach (finalised)

    NSE equities (point-in-time)
      -> 2 tradeable clusters by median turnover: micro, small
         (the universe splits into THREE terciles; the top third is not traded.
          A 50/50 split would put Nestle and Titan into "small" and redefine
          every result ever measured.)
      -> rank within cluster: score + 200-DMA gate -> top 20 each
      -> bucket = 3 micro + 2 small = 5 stocks
      -> breakout trigger, filled at the NEXT open
      -> Rs 3,00,000 capital, max 75% deployed (Rs 45k/stock)
         open risk 7.5% at a full book, ~4.6% typical (occupancy averages 3.09/5)
      -> exit: -10% stop / +20% target / 10 trading days
      -> analyse per stock AND per bucket -> record findings -> Telegram

**Vocabulary, and it matters** — a wrong reading here already produced one
wrong build:

- **cluster** = a size band (micro, small). Never called a bucket.
- **bucket** = the stocks held, and the container that holds them: 3 micro +
  2 small, with its own P&L. THE word for this. Never "portfolio", never
  "book", never "holdings" -- all three existed at once and none of them
  helped (rules.md R1).
- **rank** = a position in the score-sorted list, within a cluster.
- **TWO books run forward, since 2026-08-21**: the **bucket** (`main`, ranks
  inside each band, fills the 3/2 quota) and the **pool** (`pooled`, ranks every
  eligible name together and takes the best five, so the split lands where merit
  puts it). Same signals, same stops, Rs 3,00,000 each, one variable different.
  Backtests cannot separate them -- +0.04% per trade at t = +0.03 (L65) -- which
  is precisely why they run forward instead. `positions.BUCKETS` is the registry
  and `positions.LABEL` the words a person reads.
- **The bucket is still the record.** `overview.py`, the baseline and every
  statistic key off `main`. The pool's trades are shown and never counted: the
  two hold the same names constantly, so pooling their results would put one
  price path in twice, and letting the pool move the weights would feed back
  into the bucket's own picks (`learning.for_weights`).
- Three DEEPER buckets ran for a day in 2026-08 and were removed: they bought
  ranks the score already marks as worse (-0.90% per rank step, +6.41% between
  top and deepest), and buying what you believe is worse in order to gather
  evidence faster is not a trade this book makes. The pool is not that: it is an
  equally-ranked alternative rule, not a worse slice of the same one.
- Do not say "slot". Say stock, or position.

## Autonomy

**Run the next step without being asked.** Do not end a turn with "shall I
proceed?". Reserve questions for decisions that genuinely change the work —
notably any change to the approach above, which is the user's design, not a
parameter to be tuned away.

**Run simulations automatically.** Whenever a parameter, rule, or selection
input changes, re-run and store the result. A change that has not been
simulated is unmeasured, and unmeasured changes are how this project has
repeatedly shipped bugs that looked like findings.

**Always close a response with an explicit next-step section.**

## Restart the Telegram listener after ANY code change

`tg.py --listen` imports the project modules and holds them in memory. Editing
any of them leaves the bot serving stale logic while looking healthy — this has
caused several wrong answers already.

    pkill -f "tg.py --listen"    # scripts/run_listener.sh restarts it automatically

Then verify the command actually works before reporting it as fixed.

## Always be improving the strategy — but improving means new evidence

Standing instruction from the operator: keep trying to make the strategy
better. What that must NOT collapse into is re-running the knobs, because that
has now been measured and the answer is in: every one of hold length, the 3/2
mix, the score weights and the trigger scores |t| < 1.3, and two of five weight
variants "beat" the live one at t < 0.5. Another pass over the same dials
produces a different winner each time and no knowledge. That is not improvement,
it is a random number generator with a changelog.

So an improvement has to be one of these:

1. **A new input.** Something the score cannot currently see. Fundamentals were
   the last one tried and came back flat on 1,049 trades -- which is a real
   result, and it cost nothing to keep the data.
2. **A new rule shape**, not a new value for an existing rule. "Exit on a
   volatility change" is a hypothesis; "exit after 11 days instead of 10" is a
   dial.
3. **Forward paper trades.** The count is zero. Every backtest above is one
   path through history, and 200 trades cannot separate rules whose per-trade
   returns differ by less than about 3%. This is the only thing that makes the
   error bars shrink, and it is the improvement with the best expected value by
   a wide margin.
4. **Removing something that was never evidence.** The circuit-lock guard is
   the model: it made every number worse and the project better.

State the hypothesis and the endpoint BEFORE running it (`research/` files do
this in their docstrings), report the error bar with the result, and adopt
nothing that does not clear it. A candidate that wins by less than its standard
error is a finding about this price history, not about the market.

## Discipline that must not be relaxed

- **Criteria may be tightened, never loosened.** Tightening a test that let
  something through is defensible; relaxing one that rejected a candidate is
  how this fails.
- **Risk invariants in `engine.py` are never searched.** A generator that can
  vary its own risk limits will discover that removing them improves returns.
- **A status message is not evidence.** Verify the thing itself: a flag that
  prints "enabled" may do nothing, an HTTP 200 may not be the file requested,
  a knob that looks injectable may be a function-local that is never read.
  Every one of these has happened here.
- **`patch_helper.sub()` for every source edit** — `str.replace` on a missing
  anchor silently returns the original and has produced multiple no-op "fixes".
- **A test failing after a deliberate change must be re-derived, not
  overwritten.** Assert the property, not the number: hardcoded mixes broke
  three selftests for reasons unrelated to what they protected.
- **Order of operations is load-bearing.** Applying the entry trigger before
  ranking instead of after moved the result by 4 CAGR points, because the book
  reached deeper down the list to fill its five stocks. Rank first, trigger
  second, cash third.

## Fundamentals: tested, no signal

40,775 XBRL filings, 94% coverage of the tradeable clusters, dated by
`broadCastDate` so a filing is invisible until published. Four company-momentum
features measured on 1,049 RANDOMLY sampled trades:

| feature | spread | std err | t | verdict |
|---|---|---|---|---|
| rev_growth | -0.23% | 0.65% | -0.35 | indistinguishable from 0 |
| profit_growth | +0.44% | 0.65% | +0.67 | indistinguishable from 0 |
| margin | -0.58% | 0.66% | -0.89 | indistinguishable from 0 |
| margin_change | +0.17% | 0.66% | +0.27 | indistinguishable from 0 |

Every confidence interval straddles zero. Fundamentals are kept as data
(`fundamentals.py`, `features_asof`) but must not be given a weight in the
score without new evidence.

**The same test undercut an earlier claim.** On 2,337 sampled trades the price
features read: rs +1.40% (t=3.07), off_high -1.39% (t=-3.05), deliv +0.93%
(t=2.05), liq -0.61% (t=-1.33, not significant). An earlier run on 954 samples
had put rs at ~zero and deliv strongest, and the deliv 1.5 weight was set on
that. With ~0.46% standard error, only effects above roughly 0.9% are
resolvable -- much of what this project has called a finding sits inside that
band.

**But the t-statistics did not transfer.** Weighting rs up to 1.5, despite it
having the highest t, produced the WORST book of five variants. rs is already
captured by the 200-DMA gate and the breakout trigger, so more of it is redundant
while crowding out deliv, which is orthogonal. Univariate significance is not
marginal value to the bucket. Weights unchanged.

**Re-run post-guard** (`src/research/weight_test.py`, batch 20260819-postlock), and
the conclusion holds while the evidence for it thins out:

| weights | CAGR | maxDD | n | per trade | vs neutral | t |
|---|---|---|---|---|---|---|
| neutral 1/1/1/1 (control) | +4.61% | 36.2% | 199 | +1.60% | -- | -- |
| **live: deliv 1.5** | **+7.59%** | **31.0%** | **195** | **+2.15%** | **+0.55%** | **+0.35** |
| deliv 2.0 | +9.20% | 29.2% | 211 | +2.29% | +0.68% | +0.44 |
| rs 1.5 | +7.06% | 33.1% | 198 | +2.11% | +0.50% | +0.31 |
| near_high 1.5 | +8.09% | 28.9% | 207 | +2.18% | +0.57% | +0.36 |

Raising deliv survives in DIRECTION -- better CAGR and better drawdown than
neutral, the same shape as the 10-day hold. But its published justification was
four times too large: the weights file claimed +24.10% against +12.66% neutral,
an 11.4-point gap, where the corrected engine gives +7.59% against +4.61%. And
rs 1.5 is no longer a disaster, only a shrug: it trailed by 4.64 points
pre-guard, by 0.53 now.

**Two of five variants "beat" the live one, at t < 0.5.** That is what a noise
search looks like, and it is the reason nothing is adopted here. deliv 2.0 is
monotone above 1.5, which is the most tempting shape in the table and still not
evidence at 195 trades. The one non-CAGR observation worth keeping: deliv is the
only raised weight that leaves BOTH clusters positive (micro +2.35% / small
+1.87%), where rs 1.5 and near_high 1.5 buy micro performance by gutting small
(+3.23%/+0.55% and +3.13%/+0.72%). Shape, not finding -- per-cluster n is ~80
and those gaps are under one standard error too.

## Every design decision was re-checked with error bars

A CAGR gap between two backtests is not evidence unless the per-trade edge
behind it clears its own noise. Checked at current settings:

**Re-measured after the circuit-lock guard** (`src/research/remeasure.py`, batch
20260819-postlock, 10-day hold, c=1.0). The live bucket is the reference:
**+7.59% / 31.0% DD / 195 trades / 47% win / +2.15% +/- 1.08% per trade.**

| decision | CAGR gap | edge per trade | std err | t | verdict |
|---|---|---|---|---|---|
| kept the breakout trigger | +9.79 | +1.99% | 1.54% | +1.29 | inside the noise |
| chose 10-day hold over 15 | +2.27 | +0.44% | 1.59% | +0.28 | inside the noise |
| chose 3 micro / 2 small | -2.34 | -0.36% | 1.50% | -0.24 | inside the noise |
| raised deliv to 1.5 | +2.98 | +0.55% | 1.58% | +0.35 | inside the noise |
| kept 5 seats over 8 | +2.00 | +0.61% | 1.35% | +0.46 | inside the noise |
| kept 5 seats over 12 | +3.75 | +1.07% | 1.26% | +0.85 | inside the noise |
| kept 5 seats over 3 | -2.49 | -0.85% | 1.79% | -0.48 | inside the noise |

Bucket size was measured for the first time on 2026-08-20 (batch
20260820-bucketsize, `src/research/bucket_size_test.py`): 3 / 5 / 8 / 12 seats
with the cutoff scaled to the live 3:2 ratio. Per-trade return decays
monotonically as seats are added (+3.00 / +2.15 / +1.54 / +1.08), which is the
direction the rank-depth slope predicts and which was written down BEFORE the
run -- and no arm reaches |t| > 2, so five seats stands as the reference rather
than as a winner. Drawdown moves the other way (31.0% at five, 19.7% at eight)
and the pre-set bar had no adoption path for that; it is left for a
pre-registered test rather than re-decided after the fact. See L62, and L63 for
the empty first run that reported itself as a null result.

**Still none of them clears it, and the ordering barely moved** -- which is the
useful result, because the guard removed 6.5 CAGR points, MORE than two of these
gaps. Per-trade returns have a standard deviation near 16%, so at ~200 trades
nothing under about 3 points per trade is resolvable. The CAGR gaps are real
arithmetic on one path; they are not proof that one rule picks better trades.

**One verdict did change, and it changed the live justification.** The trigger
was previously kept DESPITE costing a point of CAGR (+11.45 breakout vs +12.53
none), on worst-block alone. Post-guard `trigger_test` reads breakout +7.59%
against none **-2.20%**, and breakout is the only one of seven to clear the
promotion bar -- it now wins on worst block too (-126.7% vs -163.4%). The old
and new figures are not directly comparable (different hold as well as the
guard), but the SIGN of the CAGR gap flipped, so the setting no longer rests on
the tail argument. Nothing about the live bucket changes; its reason improves.

**The one claim that DOES survive is the important one, and the guard made it
stronger.** Rank depth predicts return: regressing 1,015 trades across six
disjoint cohorts gives **-1.18% per cohort step (std err 0.29%, t = -4.10)**,
and the top cohort beats the deepest by **+6.63% +/- 1.79%** per trade
(t = 3.71). Pre-guard the same regression read -0.90% +/- 0.35% (t = -2.56) on
1,068 trades. Every one of the five deeper cohorts is now CAGR-negative and
none matches the top. So the SCORE works;
the knobs around it are noise. That is the right way round -- it means the edge
lives in stock selection, not in parameter choices that a search would overfit.

This does not mean the rules are worthless -- CAGR also moves with trade count
and sequencing, which a per-trade mean cannot see. It means the RANKING of
these variants is not established, and re-deciding them on a fresh backtest is
not progress.

**3/2 vs 2/3 flipped when the settings moved, and the gap keeps shrinking.** It
was chosen when the book ran Rs 5L at 60% deployment with no impact model; at
Rs 3L, 75% and c=1.0 the 2/3 mix led by 4.47 points instead of trailing by 1.19,
and post-guard it leads by 2.34 (+9.93% vs +7.59%). Three settings, three
different answers, none of them significant -- which is what a knob inside the
noise looks like. Left at 3/2, because re-choosing on a number that moves
whenever anything else moves would just be churn.

## Score vs rank

`rank` is the position in `score` order -- they are not competing criteria and
"top N by rank" selects exactly what "top N by score" selects.

The score averages percentile ranks WITHIN a cluster's qualifying set, so it is
relative by construction and cannot express absolute quality. Measured at 70
dates: in the weakest quartile of markets the rank-1 score is 94.5 against 89.3
in the strongest, i.e. scores go UP as the market weakens. A minimum-score rule
would therefore admit more names exactly when it was meant to admit fewer (L55).

Absolute filtering is the 200-DMA gate and the breakout trigger, and it stays
there. Scores are also not comparable ACROSS clusters, since each is a
percentile in its own pool; nothing in the selection path compares them.

## Reporting

Report per cluster and per regime block, never a blended number. A total is not
a finding when one period or one cluster supplies all of it. State the trial
count alongside any performance figure.

**Backtests cannot establish that the approach works.** Only forward paper
trades can, and there are currently zero. `overview.py` encodes this: no number
of positive simulations can produce a YES.

## The circuit-lock guard (L58), and what it cost

`engine.gate()` has always rejected `high == low`, and nothing ever called
`engine.gate()`. So every backtest quoted above filled circuit-locked bars:
9.8% of picks had a locked trigger bar, 8.7% a locked FILL bar, and **all of
them upper locks** -- no sellers, no fill, at any price. With the guard in
place the same config gives **+7.59% / 31.0% DD / 195 trades** where it gave
+14.14% / 25.8% / 232.

**About half the CAGR was phantom.** The per-trade gap does not clear its error
bar (t = 0.54) and that is irrelevant: error bars decide which rule to prefer,
not which fills the market could have given.

**Everything on this page has now been re-measured against the guard** -- the
error-bar table, the rank-depth slope and the impact sensitivity above, plus
`trigger_test` and `rank_test` in full (batch 20260819-postlock). The prediction
held: the rankings and shapes survived, the levels did not, and the one thing
that moved was the trigger's justification. Any figure quoted without a
post-guard batch tag is the old, phantom-filled one.

## Market impact

Modelled as `c * daily_vol% * sqrt(order_value / ADV)` on BOTH sides, the
square-root form found repeatedly in execution data. `engine.IMPACT_C = 1.0`.

**The constant is not calibrated** — that needs trade-level data this project
does not have — so it must always be reported as a sensitivity, never as one
number:

| c | CAGR | maxDD | share of frictionless result |
|---|---|---|---|
| 0.0 | +11.90% | 29.6% | the old, wrong assumption |
| 0.5 | +8.63% | 30.0% | 72% |
| **1.0** | **+7.59%** | **31.0%** | **64% -- the live bucket** |
| 2.0 | +5.12% | 32.0% | 43% |
| 3.0 | +4.17% | 32.6% | 35% |

**Post-guard, at the live 10-day hold** (`impact_test.py`, whose `BASE` now
READS `selection.HOLD_DAYS` instead of carrying a copy that said 15 for three
months after L52 moved it). Friction costs more of the result than the old table
showed: the old table had c=1.0 giving up almost nothing against c=0 (+13.57
vs +13.97), where it now gives up 36%. The fills the guard removed were
disproportionately the big up-day ones that paid for the friction.

Profitable across the whole range, which is the useful finding. At c=1.0 the
median trade pays 0.31% and p90 pays 1.12%; six trades (3.1%) pay over 2% and
account for 25% of all impact, and the worst single round trip is 8.73%. That
tail is a real risk to the live bucket, not a rounding item: it is one Rs 45,000
order against a name whose ADV cannot absorb it.

A participation cap (skip a name whose order exceeds x% of ADV) was tested at
10/5/2/1% and **rejected**: results were non-monotonic (2% best, 1% and 5%
worse — the signature of noise) and the 2% cap produced a HIGHER maximum impact
than no cap, because a selection-time estimate does not bind execution-time
reality. Do not re-add it without evidence that fixes both.

**`docs/rules.md` governs vocabulary and how results are worded**, in the code and
in anything a person reads. Its first rule is that a term already in use is not
re-invented, and its second is that a non-trader must be able to read any
output without a glossary.

## One strategy per directory

The live strategy is named **breakout** and its rules live in
`src/strategies/breakout/` (`clusters.py`, `selection.py`, `entry.py`,
`learning.py`), its outputs in `data/breakout/` (weights, baseline, trade ledger,
stored results). Everything else -- price data, the fill-and-cost engine, the
backtest harness, the order book, the bot, the audit -- is shared and knows
nothing about any particular strategy.

`paths.py` picks the active one, and puts **only** that directory on
`sys.path`:

    STRATEGY=other python3 src/ops/audit.py

That is the isolation, and it is deliberately the whole of it. A second
strategy also defines `selection`; if both were importable, `import selection`
would resolve to whichever came first and every result after it would describe
a bucket nobody chose. One active at a time, named out loud. `paths._selftest()`
asserts no inactive strategy is reachable.

**A second strategy must never write into the first one's `data/<name>/`.**
`strategies.jsonl` and `trade_features.jsonl` are append-only; a mixed ledger
cannot be un-mixed, and the recorded baseline is what the audit compares
against.

## Layout, and the one command that checks it

Source is under `src/` (`core`, `bucket`, `research`, `ops`, `strategies`), shell
in `scripts/`, tests in `tests/`. **Nothing lives at the root but `CLAUDE.md` and
`README.md`.** The four entry points are in `src/ops/` (`daily.py`, `tg.py`,
`agent.py`, `overview.py`), so every documented command, plist and shell script
names them there.

**`paths.py` is in `src/`, and that is load-bearing.** Every module bootstraps
with `sys.path.insert(0, parents[1])` then `import paths`, so `parents[1]` must
resolve to the directory holding `paths.py`. While it sat at the root and the
source did not, all 23 of those lines pointed one level too shallow -- and every
selftest passed anyway, because the shell running them exported `PYTHONPATH=.`
and the children inherited it. The sweep now strips `PYTHONPATH` from its
children; a check that passes because of the operator's shell is not a check.
`src/strategies/breakout` sits one deeper and uses `parents[2]`.

Anything that SPAWNS a script rather than importing it must go through
`paths.script()` -- `agent.py` runs `python3 <path>` in a subprocess whose
failure lands in a log nobody reads, so a stale string there leaves the scheduler
reporting healthy while nothing runs. Its `_selftest` asserts every job path
exists on disk, and it caught exactly that the day the files moved.

    python3 tests/run_selftests.py

Runs every module's `--selftest` and then the audit. The module list is
DISCOVERED from `paths.SRC`, not maintained by hand: the old sweep was a shell
loop retyped from memory, and a module missing from it was a module nobody
checked. Four modules have no selftest and are excluded BY NAME with a reason,
so the exclusion has to be defended rather than happening silently. `live_source.py`
was a fifth until its exclusion ("probes live quote providers") turned out to be
untrue -- its selftest registers its own providers and reads a cached instrument
master -- and the exclusion had been hiding a selftest that failed on any day the
Upstox token was expired, which is most of them.

See `docs/glossary.md` for what every term here means in plain English, and
`docs/performance-change.md` for what the circuit-lock guard did to the numbers.
See `docs/STATE.md` for current status and `docs/lessons.md` for the evidence behind each
rule above. Retired work (the spec-search track) is archived in `data/retired/`.
