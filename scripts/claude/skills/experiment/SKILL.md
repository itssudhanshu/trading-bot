---
name: experiment
description: Use when measuring anything in this repo — a new score input, a new rule shape, or re-checking a decision. Enforces pre-registration, error bars, and the promotion bar, so a result cannot be decided after seeing it.
---

# Running an experiment on the bucket

This repo's failure mode is not bad code. It is **deciding what a number means
after seeing it**. Every rule below exists because that already happened here.

## Before you write any code

**1. Is this even a legal experiment?** It must be one of four things
(CLAUDE.md). If it is none of them, stop and say so:

| kind | example | not this |
|---|---|---|
| a new input the score cannot see | fundamentals, index membership, sector breadth | — |
| a new rule *shape* | "exit on a volatility change" | "exit after 11 days instead of 10" |
| forward paper trades | the count is 0; this is the highest-value work available | — |
| removing something that was never evidence | the circuit-lock guard | — |

Re-running an existing knob is **not** an experiment. Hold length, the 3/2 mix,
the score weights and the trigger have all been measured and all sit at |t| <
1.3. Another pass produces a different winner each time and no knowledge.

**2. Write the hypothesis and the endpoint into the module docstring, first.**
Copy the shape of `src/research/weight_test.py`: what is being questioned, why
the previous number is suspect, what the CONTROL is, and what result would
change the decision. A docstring written after the run is a rationalisation.

**3. Name the control explicitly.** It is whatever the live setting was a
decision *against*, not "the live setting". `weight_test.py` controls on neutral
1/1/1/1 because raising `deliv` was a decision against neutral.

## Writing it

- Live in `src/research/<name>_test.py`. Bootstrap with
  `parents[1]` then `import paths`.
- `BATCH = "<yyyymmdd>-<tag>"` and store it with every result. A figure without
  a batch tag cannot be compared to anything.
- **Read the live constants, never copy them.** `BASE` must read
  `selection.HOLD_DAYS`, not `15`. `impact_test.py` carried a copy that said 15
  for three months after the live value moved to 10.
- Set variant constants **inside each fork** so a variant cannot leak into the
  live weights file or into its siblings.
- Never vary anything in `engine.py`. Risk invariants are not searchable: a
  generator that can move its own risk limits will discover that removing them
  improves returns.

## Reporting it

- Print `mean ± std err` and `t` for every arm. **A CAGR gap is not a result.**
- Per-trade sd is ~16%, so at ~200 trades nothing under ~3 points per trade is
  resolvable. `|t| > 2` is RESOLVED; anything else is `inside the noise`, and
  say so in those words.
- Report **per cluster and per regime block**, never one blended number. A total
  is not a finding when one period or one cluster supplied all of it.
- State `n` next to every performance figure.

## Deciding

- **Adopt nothing that wins by less than its standard error.** That is a finding
  about this price history, not about the market.
- Two of five weight variants "beat" the live one at t < 0.5. That is what a
  noise search looks like. It is why nothing was adopted.
- Univariate significance is not marginal value to the bucket: `rs` had the
  highest t of any feature and weighting it up produced the worst of five books,
  because the 200-DMA gate and the breakout trigger already capture it.
- **Criteria may be tightened, never loosened.** Tightening a test that let
  something through is defensible. Relaxing one that rejected a candidate is how
  this fails.

## Afterwards, always

```bash
python3 tests/run_selftests.py
```

- The audit's headline must still read `CAGR +7.59% vs +7.59%, n=195 vs 195`.
  If a change moved it deliberately, re-record the baseline with
  `python3 src/ops/audit.py --rebaseline` — deliberately, in its own step.
- A test that fails after a deliberate change is **re-derived, not
  overwritten**. Assert the property, not the number.
- Write the finding into `docs/lessons.md` with its evidence and sample size,
  and update the table in `CLAUDE.md` if a verdict moved. A result nobody
  recorded gets re-discovered and re-decided differently.
- Restart the Telegram listener if any source changed. It watches every
  importable module and exits on a change, so launchd picks it up — verify the
  listener line in `/health` afterwards rather than assuming.

## The thing that is always true

Backtests cannot establish that the approach works. `overview.py` encodes this:
no number of positive simulations can produce a YES. Forward paper trades closed:
0. If the choice is between another backtest and starting the forward count,
start the forward count.
