# CLAUDE.md — working agreement for this repo

## The approach (finalised)

    NSE equities (point-in-time)
      -> 2 tradeable clusters by median turnover: micro, small
         (the universe splits into THREE terciles; the top third is not traded.
          A 50/50 split would put Nestle and Titan into "small" and redefine
          every result ever measured.)
      -> POOLED rank across every eligible stock + 200-DMA gate
      -> bucket = the best 5 outright (clusters gate eligibility only)
      -> breakout trigger, filled at the NEXT open
      -> Rs 3,00,000 capital, max 75% deployed (Rs 45k/stock)
         open risk 7.5% at a full book, ~4.6% typical (occupancy averages 3.09/5)
      -> exit: -10% stop / +20% target / 15 trading days
      -> analyse per stock AND per bucket -> record findings -> Telegram

**Vocabulary, and it matters** — a wrong reading here already produced one
wrong build:

- **cluster** = a size band (micro, small). Never called a bucket.
- **bucket** = the 5-stock portfolio. Never called a cluster.
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
| **1.0** | **+13.57%** | the standing baseline at 75% deployment |
| 2.0 | +7.40% | 53% |
| 3.0 | +4.53% | 32% |

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
