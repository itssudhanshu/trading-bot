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
## L8 — A longer horizon DOES rescue 3R reachability (strong) — resolves L1
Across 91 evaluated specs, median target-hit rate by holding horizon:

    hold=10 bars -> 0.5%    hold=20 -> 3.9%    hold=30 -> 5.2%
    hold=45 bars -> 10.3%   hold=60 -> 15.0%

Monotonic and steep. L1 concluded 3R was unreachable; it was unreachable **at
30 bars**. The constraint was the horizon, not the R:R rule.

**The tension, precisely stated:** 60 bars is ~12 weeks, double the persona's
stated 3-day-to-6-week window. So the 1:3 R:R rule and the 6-week ceiling are
jointly infeasible on NSE equities -- each is reasonable alone. Resolving it
means extending the horizon past 6 weeks, or accepting a lower R:R. That is an
operator decision; the generator cannot relax an invariant and must not try.

## L9 — A position must be economically viable, not merely risk-sized (settled)
The seed-42 search surfaced a spec reporting avgR -4.28 alongside POSITIVE
Rs 987 expectancy. Both were computed correctly; the trades were nonsense.

When the liquidity cap sizes a position down to a few shares, fixed brokerage
(Rs 20/order, Rs 40 round trip) dwarfs the risk base. Real case: KOTAKMNC,
risk/share Rs 0.94, qty 1, Rs 45 booked loss -> R = -47.7. Worst instance in
that spec: R = -10,939.

Added `MAX_COST_RATIO = 0.10` to the gate -- round-trip costs must stay under
10% of the risk being taken. Effect on that spec: avgR -4.28 -> +0.25, R range
collapsed from -10,939..+9.6 to -6.9..+9.6, 517 unviable trades removed.

**One invariant killed three pathologies:** unviable sizing, illiquid
instruments that escaped classification, and the R-multiple blow-ups they
produced downstream. Prefer the invariant that makes a class of nonsense
impossible over the filter that catches today's instance of it.

## L10 — The non-equity denylist misses instruments that stopped trading (open)
L3's denylist is (EQ symbols trading TODAY) minus (company master). An ETF that
delisted before the master snapshot is present in historical bhavcopy, absent
from today's bhavcopy, and therefore never denied. KOTAKMNC, ICICI5GSEC,
ICICIINFRA and KOTAKCONS all reached the corpus this way.

The asymmetry is structural: for a symbol absent from today's master we cannot
tell a delisted COMPANY (must keep, or we get survivorship bias) from a delisted
ETF (must drop). NSE publishes no point-in-time instrument classification.

Largely neutralised by L9 -- these instruments are illiquid and now fail the
viability gate regardless of classification. Proper fix is to snapshot an ETF
list daily from today forward, accepting that history stays imperfect.

## L11 — Search results are invalidated by any gate change (standing)
`candidates.jsonl` is only valid for the gate that produced it. The L9 invariant
changed which signals are admissible, so every prior result is stale and must be
regenerated before it is read. Gate changes are not backward compatible; do not
compare candidate runs across them.
## L12 — Rank on portfolio-realizable expectancy, not unconstrained (settled)
The seed-42 rerun surfaced specs with 11,252 and 17,856 instances over three
years. Both cleared `MAX_SIGNALS_PER_SYMBOL_YEAR` (2.9/symbol-year, under the
6.0 ceiling) yet generate ~6,000 signals a year.

Capacity is the binding constraint: `MAX_PORTFOLIO_HEAT` 6% at
`RISK_PER_TRADE` 0.5% permits 12 concurrent positions, which at a 30-bar hold
is roughly 100 trades a year. A spec offering 6,000 exceeds capacity ~60x, so
its unconstrained expectancy describes trades that could never have been taken,
and the ones that WOULD be taken are an arbitrary chronological subset.

`backtest.portfolio_path` now returns the admitted subset, and results carry
`n_taken`, `capacity_ratio` and `portfolio_expectancy`. The generator ranks on
portfolio expectancy.

Instance count remains the EVIDENCE base (statistical power); portfolio
expectancy is the RETURN estimate. Conflating the two is what let a spec with
60x more signals than capacity reach the top of the table.

## L13 — Gap detectors must not report known-absent data (settled)
The first surveillance-gap report flagged 33 "unrecoverable" days -- the entire
backfilled history, which never had ASM/GSM by design. A detector that fires on
expected absence gets ignored, which costs more than having no detector.

Anchored on the earliest `asm.json`: only days after collection began can be
gaps. Applies generally -- alert on the difference between what SHOULD exist and
what does, never on the difference between what you WANT and what exists.
