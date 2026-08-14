# Lessons

Structural findings that constrain the search space. The generator reads this
file. Entries are CONSTRAINTS and FAILURE MODES, never tuned parameters --
"lookback=47 worked" is overfitting; "3R is unreachable at this horizon" is a
property of the market.

Each entry carries its evidence and sample size. An entry without evidence is
an opinion and does not belong here.

---

## L1 — A 3R target is unreachable at a 3-6 week horizon (strong)
Across 67 Stage-2 breakout instances (2022-2026, NSE EQ), maximum favourable
excursion within 30 trading days:

    p50 +0.59R   p90 +1.18R   p95 +1.79R   max +2.33R

Not one instance reached 3R. Zero of 64 closed trades exited on target; 53 died
on the time stop, 11 on the protective stop.

**Consequence:** `engine.MIN_RR = 3.0` is satisfiable on paper and unreachable
in practice. It does not act as a risk control here -- it guarantees that no
winner is ever realised, converting every good trade into a flat time-stop exit.
The gate validates *planned* R:R; the market supplies the *achievable* one.

**Open decision for the operator:** MIN_RR is an invariant and deliberately not
searchable. Reconciling it needs one of: a tighter stop definition (see L2, which
does not work on its own), a longer holding horizon, or an explicit decision to
revise the 1:3 rule. That last one is the operator's call, not the generator's.

## L2 — Tightening stops raises 3R reachability but lowers expectancy (moderate)
Same signals, four stop definitions:

| stop rule              |  n | median stop | median MFE | hit 3R | expectancy |
|------------------------|----|-------------|------------|--------|------------|
| swing_low(10) - 0.5ATR | 64 | 14.7%       | +0.59R     |  0     | -Rs 140    |
| swing_low(5)           | 68 |  9.3%       | +0.93R     |  3     | -Rs 461    |
| ATR x 2.0              | 67 |  7.5%       | +1.19R     |  3     | -Rs 764    |
| ATR x 1.5              | 68 |  5.9%       | +1.54R     | 11     | -Rs 467    |

Tightening the stop shrinks R, so the same price move counts for more -- but
stop-out frequency rises faster than the reachability gain. All four are
negative. Do not propose "tighter stop" as a fix for L1 on its own.

## L3 — ETFs trade in the EQ series and must be excluded (settled)
340 of 2,463 EQ symbols are ETFs, index trackers and liquid funds (NIFTYBEES,
LIQUIDPLUS, AUTOBEES...). They produced 15 of 113 signals before exclusion.
A volatility-contraction breakout on a liquid debt fund is noise.

Excluded as a **denylist** (EQ symbols absent from NSE's company master), never
as an allowlist -- requiring master membership would drop every delisted company
from history and silently introduce survivorship bias.

## L4 — The Stage-2 spec is too restrictive to validate (strong)
94 signals from ~2.3M bar-opportunities (0.004%), ~24/year, stable across all
four years. Per walk-forward fold: n = 11-21.

At that density a single spec cannot clear the evidence bar. The generator's
objective is therefore **specs loose enough to be testable while still
selective**, not specs that maximise backtest return. A spec yielding under
~30 instances per fold should be rejected before its P&L is even examined.

## L5 — Methodology: the numbers above are diagnostic, not evidence of edge
Every run so far used the FULL corpus, with no train/val split, and four stop
variants were compared on the same data. That is enough to establish
*structural* facts (a target is unreachable, a universe is contaminated) which
are properties of the distribution and robust to selection.

It is NOT enough to conclude anything about edge. The expectancy figures above
must not be quoted as results. Performance claims require the walk-forward
harness, and a holdout consultation must be spent only on a spec that already
survived train/val.

## L6 — Selectivity needs an upper bound, not just a lower one (settled)
The first search ranked an `ema_pullback` spec first on train expectancy:
43,342 trades, +Rs 110/trade, from 400 symbols over 3 years -- 14% of all
bar-opportunities, 36 signals per symbol per year.

That is not a setup. With n that large a microscopic edge averages into a
positive number and outranks every genuine candidate; the portfolio simulation
would reject nearly all of it on heat and concurrency anyway, so the ranked
figure describes a strategy that cannot be traded.

L4 asked for specs "loose enough to be testable while still selective".
Only the first half had been implemented. Added
`MAX_SIGNALS_PER_SYMBOL_YEAR = 6.0`, screened before P&L like the lower bound.

**General form:** every screen expressed as a floor needs its ceiling stated
too, or the search walks to the boundary the floor does not defend.

## L7 — The generator ranks on train expectancy, which is triage only (standing)
`candidates.jsonl` is sorted by train expectancy so a human can look at the top
of a long list. That ordering is NOT evidence and must never be reported as a
result -- it is the maximum of many trials on one dataset, which is precisely
the quantity that overstates itself. Only the walk-forward folds, and finally a
holdout consultation, carry inferential weight.
