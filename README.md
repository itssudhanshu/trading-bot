# trading-bot

Systematic swing-trading research harness for Indian equities (NSE). Paper only
— nothing here places an order anywhere.

Stdlib Python, no dependencies. Every module carries a `--selftest` that fails
if its core logic breaks.

```bash
for f in *.py core/*.py bucket/*.py research/*.py ops/*.py; do python3 "$f" --selftest; done
```

## Layout

```
agent.py daily.py tg.py overview.py       entry points; launchd runs agent.py
paths.py                                   ROOT and DATA, defined once
core/       universe features clusters portfolio entry engine quotes fundamentals
book/       pbook learning analysis        the paper portfolios and their evidence
research/   simulate and the *_test.py experiments
ops/        snapshot backfill audit upstox_login patch_helper
docs/       lessons.md rules.md STATE.md
data/       raw/ plus state, results and logs
```

Modules import each other by bare name (`import features`) from anywhere:
`paths.py` puts the source directories on `sys.path`, and each moved module
loads it first, so any file can still be run directly for its selftest.

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
clusters.py    turnover terciles -> micro/small; composite score, 200-DMA gate
entry.py       breakout trigger, evaluated on the signal day
selection.py   rank -> interleave 3 micro / 2 small -> trigger -> size
engine.py      invariant gate, gap-aware fills, India cost stack, impact model
     |
simulate.py    the backtest, over the same portfolio/clusters code paths
positions.py   the bucket: queue -> fill at the next open -> exit
daily.py       daily driver (morning fill, evening step + re-select)
learning.py    per-trade feature ledger; proposes score weights on evidence
     |
agent.py       what is due right now; launchd calls it hourly
tg.py          Telegram: read-only reporting and the daily push
audit.py       cross-checks the real system, and mutation-tests its own checks
overview.py    the one honest status page; backtests cannot make it say YES
```

The spec-search track (`spec.py`, `generator.py`, `backtest.py`, `validate.py`,
`judge.py`, `runner.py`, `postmortem.py`, `tv.py`) is RETIRED and archived in
`data/retired/`. It never held a position; `docs/lessons.md` L1-L47 is its record.

## The three rules that matter

**1. Invariants are not searchable.** `engine.py` holds the risk rules — R:R
floor, surveillance exclusion, liquidity cap, portfolio heat, cost viability. A
generator that can vary its own risk limits will discover that removing them
improves backtest returns. Every optimiser does.

**2. Only forward trades are evidence.** `overview.py` encodes this: no number
of positive backtests can produce a YES, because a search returns some
positives by construction. It needs 30 closed PAPER trades to say anything.

**3. A gap between two backtests is not a finding.** Per-trade returns here have
a ~16% standard deviation, so at ~220 trades nothing under about 3 points per
trade is resolvable. Every design decision carries its own error bars in
`CLAUDE.md`; most of them sit inside the noise, and the one that does not —
rank depth predicts return — is the one worth keeping.

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
python3 clusters.py                  # today's selection, per cluster
python3 daily.py                     # evening: fill, exit, re-select
python3 daily.py --fill-live     # morning: fill pending at the open
python3 audit.py                     # 24 cross-checks against the real system
python3 overview.py                  # status, gates, and the honest verdict
```

Scheduling: use `deploy/*.plist` with launchd, not cron — cron skips jobs when
the machine sleeps, and a missed session is a permanent hole in the
point-in-time record.

## docs/lessons.md

Accumulated structural findings, each with evidence and sample size. This is the
system's memory and the constraint set on any future change. Entries are
failure modes and constraints, never tuned parameters: *"3R is unreachable at 30 bars"* is a
property of the market; *"lookback=47 worked"* is overfitting.

## Status

No strategy has established an edge. The recorded baseline is +14.18% CAGR,
25.8% max drawdown, 231 trades over 1,696 sessions with impact at c=1.0, and
`audit.py` fails if it stops reproducing -- or if the exit rules change without
the baseline being re-recorded deliberately (`--rebaseline`). It is a BACKTEST, and not evidence
the approach works forward. Forward paper trades closed: 0. Run `overview.py`
for the current figures rather than trusting this paragraph.

The harness has found several real defects in its own results — an unreachable
target rule, ETF contamination, a float-precision R:R rejection, R-multiple
blow-ups from unviable position sizes, and a ranking that measured trades the
portfolio could never take. That is the harness working as intended.

## Not included, deliberately

Live execution. Going live in India requires a SEBI-compliant broker API with
per-order algo tagging, static IP whitelisting, and order-rate limits — a
different system with different failure modes, none of which are paper-testable.
