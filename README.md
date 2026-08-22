# trading-bot

Systematic swing-trading research harness for Indian equities (NSE). Paper only
— nothing here places an order anywhere.

Stdlib Python, no dependencies. Every module carries a `--selftest` that fails
if its core logic breaks, and one command runs all of them plus the audit:

```bash
python3 tests/run_selftests.py
```

## Layout

```
src/paths.py                           ROOT, DATA and which strategy is live
src/strategies/breakout/                 THE STRATEGY: clusters selection entry learning
src/core/                              universe features engine live_source fundamentals
src/bucket/                            positions analysis -- the bucket and its evidence
src/research/                          simulate and the *_test.py experiments
src/ops/                               agent daily tg overview + snapshot backfill audit
tests/run_selftests.py                 runs every selftest and the audit
scripts/                               setup.sh run_listener.sh deploy/ claude/
docs/                                  glossary.md lessons.md rules.md STATE.md
data/breakout/                           the strategy's weights, baseline, trade ledger
data/                                  raw/ plus shared state and logs
.env.example                           the keys to fill in; .env is gitignored
```

Modules import each other by bare name (`import features`) from anywhere:
`src/paths.py` puts the source directories on `sys.path`, and each module loads
it first, so any file can still be run directly for its selftest. That file
lives in `src/` deliberately -- every module bootstraps with
`parents[1] / paths`, so it must resolve to the directory holding the source.

**One strategy at a time.** Only the active strategy's directory goes on
`sys.path`, so a second strategy that also defines `selection` cannot be reached
by accident -- `import selection` has exactly one answer. Choose it per command:

```bash
STRATEGY=other python3 src/ops/audit.py
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
clusters.py    turnover terciles -> micro/small; composite score, 200-DMA gate
entry.py       breakout trigger, evaluated on the signal day
selection.py   rank -> interleave 3 micro / 2 small -> trigger -> size
engine.py      invariant gate, gap-aware fills, India cost stack, impact model
     |
simulate.py    the backtest, over the same selection/clusters code paths
positions.py   the bucket: queue -> fill at the next open -> exit
daily.py       daily driver (morning fill, evening step + re-select)
learning.py    per-trade feature ledger; proposes score weights on evidence
     |
agent.py       what is due right now; launchd calls it hourly
tg.py          Telegram: ten read-only commands and the daily push
audit.py       cross-checks the real system, and mutation-tests its own checks
overview.py    the one honest status page; backtests cannot make it say YES
```

The spec-search track (`spec.py`, `generator.py`, `backtest.py`, `validate.py`,
`judge.py`, `runner.py`, `postmortem.py`, `tv.py`) is RETIRED and archived in
`data/retired/`. It never held a position; `docs/lessons.md` L1-L47 is its record.

## The three rules that matter

**1. Invariants are not searchable.** `engine.py` holds the risk rules — R:R
floor, surveillance exclusion, liquidity cap, bucket heat, cost viability. A
generator that can vary its own risk limits will discover that removing them
improves backtest returns. Every optimiser does.

**2. Only forward trades are evidence.** `overview.py` encodes this: no number
of positive backtests can produce a YES, because a search returns some
positives by construction. It needs 30 closed PAPER trades to say anything.

**3. A gap between two backtests is not a finding.** Per-trade returns here have
a ~16% standard deviation, so at ~200 trades nothing under about 3 points per
trade is resolvable. Every design decision carries its own error bars in
`CLAUDE.md`; most of them sit inside the noise, and the one that does not —
rank depth predicts return — is the one worth keeping.

**4. A fill the market could not have given is not a fill.** `engine.gate()`
rejects a bar where `high == low`, which on NSE means a price-band lock: at an
upper lock there are no sellers, so no buy fills at any price. Nothing called
that gate until 2026-08-19, and about half the recorded CAGR turned out to rest
on those bars. See `docs/performance-change.md`.

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
python3 src/ops/snapshot.py                     # today's capture
python3 src/ops/snapshot.py --catchup           # recover missed days, report what cannot be
python3 src/ops/backfill.py --years 4           # historical bars
python3 src/strategies/breakout/clusters.py       # today's selection, per cluster
python3 src/ops/daily.py                        # evening: fill, exit, re-select
python3 src/ops/daily.py --fill-live            # morning: fill pending at the open
python3 src/ops/audit.py                        # 35 cross-checks against the real system
python3 src/ops/overview.py                     # status, gates, and the honest verdict
python3 tests/run_selftests.py                  # every selftest, then the audit
```

Scheduling: use `scripts/deploy/*.plist` with launchd, not cron — cron skips
jobs when the machine sleeps, and a missed session is a permanent hole in the
point-in-time record. `src/ops/agent.py` is the only job that needs
scheduling; it works out what is due (snapshot, catchup, evening step, digest)
and runs it. Anything that SPAWNS another script goes through `paths.script()`,
never a hardcoded path -- a stale string in a subprocess call leaves the
scheduler reporting healthy while nothing runs.

**Install them under their Label, not their repo filename.** launchd finds a job
by Label, so `trading-bot-agent.plist` loads by path and then answers to nothing:
`launchctl list` shows no job and `/health` correctly reports that nothing is
scheduled. Both files also carry the absolute path of the script they run, so the
`src/` move broke both silently.

```bash
cp scripts/deploy/trading-bot-agent.plist    ~/Library/LaunchAgents/com.sudhanshu.tradingbot.agent.plist
cp scripts/deploy/trading-bot-telegram.plist ~/Library/LaunchAgents/com.sudhanshu.tradingbot.telegram.plist
rm -f ~/Library/LaunchAgents/trading-bot-agent.plist
launchctl unload ~/Library/LaunchAgents/com.sudhanshu.tradingbot.{agent,telegram}.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.sudhanshu.tradingbot.{agent,telegram}.plist
```

`audit.py` checks both installed plists afterwards: that launchd can parse each
one, that its filename matches its Label, and that every repo path it names still
exists. That check FAILS today, which is why the listener is down.

## docs/lessons.md

Accumulated structural findings, each with evidence and sample size. This is the
system's memory and the constraint set on any future change. Entries are
failure modes and constraints, never tuned parameters: *"3R is unreachable at 30 bars"* is a
property of the market; *"lookback=47 worked"* is overfitting.

## Status

No strategy has established an edge. `data/breakout/baseline.json` still records
**+7.59% CAGR, 31.0% max drawdown, 195 trades**, and that figure is known to be
wrong: the point-in-time non-equity denylist could only recognise a fund that
was still trading, so delisted ETFs sat in the historical universe and the
bucket bought 22 of them. Measured without them the same rules give **+2.42%
CAGR, 32.5% max drawdown, 193 trades** (L61, batch `20260820-nonequity3`), and
`audit.py` fails on the drift on purpose -- re-recording is a deliberate step
(`--rebaseline`). It is a BACKTEST either way, and not evidence the approach
works forward. Forward paper trades closed: 0. Run `overview.py` for the
current figures rather than trusting this paragraph.

The harness has found several real defects in its own results — an unreachable
target rule, ETF contamination, a float-precision R:R rejection, R-multiple
blow-ups from unviable position sizes, a ranking that measured trades the bucket
could never take, and a risk gate that was never called. That is the harness
working as intended -- the last of those cost half the headline number.

## Not included, deliberately

Live execution. Going live in India requires a SEBI-compliant broker API with
per-order algo tagging, static IP whitelisting, and order-rate limits — a
different system with different failure modes, none of which are paper-testable.
