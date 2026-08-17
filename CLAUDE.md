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
- **bucket** = the 5-stock portfolio. Never called a cluster.
- **rank** = a position in the score-sorted list, within a cluster.
- **cohort** = a SLICE of ranks that one paper book takes. Never "rank1/2/3":
  a book named `rank2` printed beside a name sitting at rank 5 put two
  meanings of "rank" on one line saying different numbers, which is how this
  was caught. `cohort2` takes ranks 7-9 micro and 5-6 small.
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

    pkill -f "tg.py --listen"    # run_listener.sh restarts it automatically

Then verify the command actually works before reporting it as fixed.

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
having the highest t, produced the WORST book of five variants (+8.93% against
+13.57%). rs is already captured by the 200-DMA gate and the breakout trigger,
so more of it is redundant while crowding out deliv, which is orthogonal.
Univariate significance is not marginal portfolio value. Weights unchanged.

## Every design decision was re-checked with error bars

A CAGR gap between two backtests is not evidence unless the per-trade edge
behind it clears its own noise. Checked at current settings:

These were measured at the 15-day hold. The hold is now 10 (L52); the verdicts
below are about the OTHER knobs and none of them was re-decided by that change.

| decision | CAGR gap | edge per trade | std err | t | verdict |
|---|---|---|---|---|---|
| adopted the breakout trigger | +2.91 | +0.05% | 2.22% | 0.02 | inside the noise |
| chose 3 micro / 2 small | -4.47 | -0.57% | 1.51% | -0.37 | inside the noise |
| raised deliv to 1.5 | +2.98 | +0.53% | 1.58% | 0.33 | inside the noise |

**None of them clears it.** Per-trade returns have a standard deviation near
16%, so with ~220 trades nothing under about 3 points per trade is resolvable.
The CAGR gaps are real arithmetic on one path; they are not proof that one rule
picks better trades than another.

**The one claim that DOES survive is the important one.** Rank depth predicts
return: regressing 1,068 trades across six disjoint cohorts gives -0.90% per
cohort step (std err 0.35%, t = -2.56, CI [-1.59, -0.21]), and the top cohort
beats the deepest by +6.41% +/- 1.89% per trade (t = 3.39). So the SCORE works;
the knobs around it are noise. That is the right way round -- it means the edge
lives in stock selection, not in parameter choices that a search would overfit.

This does not mean the rules are worthless -- CAGR also moves with trade count
and sequencing, which a per-trade mean cannot see. It means the RANKING of
these variants is not established, and re-deciding them on a fresh backtest is
not progress.

**3/2 vs 2/3 flipped when the settings moved.** It was chosen when the book ran
Rs 5L at 60% deployment with no impact model; at Rs 3L, 75% and c=1.0, the 2/3
mix leads by 4.47 points instead of trailing by 1.19. Neither gap is
significant. Left at 3/2 because re-choosing on a number that is inside the
noise would just be churn.

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

## Market impact

Modelled as `c * daily_vol% * sqrt(order_value / ADV)` on BOTH sides, the
square-root form found repeatedly in execution data. `engine.IMPACT_C = 1.0`.

**The constant is not calibrated** — that needs trade-level data this project
does not have — so it must always be reported as a sensitivity, never as one
number:

| c | CAGR | share of frictionless result |
|---|---|---|
| 0.0 | +13.97% | the old, wrong assumption |
| 0.5 | +11.60% | 83% |
| **1.0** | **+13.57%** | the then-baseline at 75% deployment |
| 2.0 | +7.40% | 53% |
| 3.0 | +4.53% | 32% |

**Measured at the 15-day hold, which is no longer the book's rule** (10 days
since L52). The shape of the sensitivity is what matters here and that does not
depend on the hold; the absolute figures are the old configuration's. Re-run
`impact_test.py` before quoting any single number from this table.

Profitable across the whole range, which is the useful finding. At c=1.0 the
median trade pays 0.30% and p90 pays 1.02%; five trades (2.3%) pay over 2% and
account for 22% of all impact.

A participation cap (skip a name whose order exceeds x% of ADV) was tested at
10/5/2/1% and **rejected**: results were non-monotonic (2% best, 1% and 5%
worse — the signature of noise) and the 2% cap produced a HIGHER maximum impact
than no cap, because a selection-time estimate does not bind execution-time
reality. Do not re-add it without evidence that fixes both.

See `STATE.md` for current status and `lessons.md` for the evidence behind each
rule above. Retired work (the spec-search track) is archived in `data/retired/`.
