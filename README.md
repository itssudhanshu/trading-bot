# trading-bot

Systematic swing-trading research harness for Indian equities (NSE). Paper only
— nothing here places an order anywhere.

Stdlib Python, no dependencies. Every module carries a `--selftest` that fails
if its core logic breaks.

```bash
for f in *.py; do python3 "$f" --selftest; done
```

## What this is for

Finding whether a swing setup has an edge, without fooling yourself. Most of the
code is not strategy logic — it is the machinery that stops a search from
selecting noise and reporting it as a discovery.

## Pipeline

```
snapshot.py    daily NSE capture (7 sources, raw bytes, hashed manifest)
backfill.py    historical bhavcopy; holidays detected by CONTENT, not URL
     |
universe.py    -> point-in-time universe, surveillance flags, ETF exclusion
features.py    -> per-symbol series + indicator primitives + market breadth
     |
spec.py        bounded predicate vocabulary; generator emits DATA, never code
engine.py      invariant gate, gap-aware fills, India cost stack, journal
     |
backtest.py    cross-sectional walk-forward, purge, portfolio capacity
generator.py   seeded search; screens testability + selectivity BEFORE P&L
validate.py    pre-registered promotion criteria
judge.py       sealed holdout; returns one bit and a budget count
     |
runner.py      daily forward paper loop (the only renewable evidence)
postmortem.py  deterministic aggregation -> lessons.md
tv.py          render signals on a live TradingView chart (review surface)
```

## The three rules that matter

**1. Invariants are not searchable.** `engine.py` holds the risk rules — R:R
floor, surveillance exclusion, liquidity cap, portfolio heat, cost viability. A
generator that can vary its own risk limits will discover that removing them
improves backtest returns. Every optimiser does.

**2. The holdout returns one bit.** `judge.py` answers PASS/FAIL plus budget
remaining — never a metric. A judge that returns a Sharpe gets hill-climbed
across runs, leaking the holdout one decimal at a time. Lifetime budget: 50
consultations. Re-testing an identical spec is free and returns the cached
verdict.

**3. Screen for evidence before looking at returns.** A spec with n=8 and a
beautiful backtest is not a finding. `generator.py` rejects on instance count
and signal frequency before computing P&L.

## Data honesty

- **Holidays**: NSE serves the *previous* session's file with HTTP 200 on a
  holiday. Validated by the trade date inside the file; the URL is not evidence.
- **Surveillance**: ASM/GSM/F&O-ban are published for the current day only.
  Backfilled bars carry `surveillance_known=False` — absence is never read as
  "was tradeable".
- **Survivorship**: non-companies are excluded by *denylist*, never by requiring
  membership in today's company master, which would delete every delisted name
  from history.
- **Gaps**: `snapshot.py --gaps` reports missing days, anchored to when
  collection began so known-absent history is not flagged.

## Running

```bash
python3 snapshot.py                  # today's capture
python3 snapshot.py --catchup        # recover missed days, report what cannot be
python3 backfill.py --years 4        # historical bars
python3 generator.py -n 200          # search (train only; holdout asserted sealed)
python3 validate.py                  # walk-forward promotion gate
python3 postmortem.py                # aggregate -> feeds lessons.md
python3 runner.py                    # daily paper loop
```

Scheduling: use `deploy/*.plist` with launchd, not cron — cron skips jobs when
the machine sleeps, and a missed session is a permanent hole in the
point-in-time record.

## lessons.md

Accumulated structural findings, each with evidence and sample size. This is the
system's memory and the generator's constraint set. Entries are failure modes
and constraints, never tuned parameters: *"3R is unreachable at 30 bars"* is a
property of the market; *"lookback=47 worked"* is overfitting.

## Status

No strategy has established an edge. The harness has found several real defects
in its own results — an unreachable target rule, ETF contamination, a
float-precision R:R rejection, R-multiple blow-ups from unviable position sizes,
and a ranking that measured trades the portfolio could never take. That is the
harness working as intended.

Judge budget spent: 0/50.

## Not included, deliberately

Live execution. Going live in India requires a SEBI-compliant broker API with
per-order algo tagging, static IP whitelisting, and order-rate limits — a
different system with different failure modes, none of which are paper-testable.
