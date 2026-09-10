---
name: pipeline
description: Run the eight-agent review cycle over the forward buckets — data steward, trade auditor, pattern miner, rule proposer, sealed backtest validator, performance tracker, forward paper trade manager. Use when reviewing a batch of closed trades end to end, or when asked to run the pipeline, a review cycle, or "the agents".
---

# The eight-agent review cycle

One cycle turns closed forward trades into **at most one** pre-registered rule
proposal, and usually into none. The output is either one validated rule change,
one paper trade started, or "no actionable pattern at this n". **All three are
correct, and the third is the most common. That is the point.**

The pipeline's job is not to optimise parameters. It is to find new evidence.

## The split that makes this safe

`src/ops/pipeline.py` holds the invariants. The agents hold the judgement.

An agent can be argued out of a standard — that is what produced a weight table
where two of five variants "beat" the live bucket at t < 0.5. So the rules that
must not bend are not in any prompt. Same reasoning that keeps `engine.py`'s
risk invariants out of every search.

If a stage is blocked, **fix the payload — never work around the gate.** If a
payload passes and you can still see the fault, that is a gap in pipeline.py:
name the missing check rather than waving it through.

## Running it

```bash
python3 src/ops/pipeline.py --status
```

```bash
python3 src/ops/pipeline.py --open-cycle --batch 20260910
```

Then per stage: gate, dispatch the subagent, hand off. Give each agent the cycle
number, the previous stage's payload from `--show <stage>`, and **nothing about
what you hope it finds.** Require it to end with one fenced JSON object; write
that to the scratchpad and hand it off.

```bash
python3 src/ops/pipeline.py --gate data_steward
```

```bash
python3 src/ops/pipeline.py --handoff data_steward --file "$SCRATCH/data_steward.json"
```

| # | stage | subagent | waits on |
|---|---|---|---|
| 0 | `orchestrator` | `agent-0-orchestrator` | — |
| 1 | `data_steward` | `agent-1-data-steward` | orchestrator |
| 2 | `trade_auditor` | `agent-2-trade-auditor` | data_steward |
| 3 | `pattern_miner` | `agent-3-pattern-miner` | trade_auditor |
| 4 | `rule_proposer` | `agent-4-rule-proposer` | pattern_miner |
| 5 | `backtest_validator` | `agent-5-backtest-validator` | rule_proposer |
| 6 | `performance_tracker` | `agent-6-performance-tracker` | backtest_validator |
| 7 | `forward_manager` | `agent-7-forward-paper-trade-manager` | backtest_validator |

6 and 7 both hang off 5 and are independent of each other — dispatch them in one
message. That gating is also why **Agent 5 cannot be skipped.**

Agent 0 runs **twice**: once to open, once to close. The close is refused
without its summary — a cycle nobody summarised gets re-discovered and
re-decided differently.

```bash
python3 src/ops/pipeline.py --close-cycle --file "$SCRATCH/summary.json"
```

## When each cycle runs

| trigger | stages | output |
|---|---|---|
| new batch (monthly, or per N closed trades) | 0 → 1 → 2 → 3 → 4 → 5 → 0 | full cycle |
| Agent 5 returns INCONCLUSIVE | 7 (intake) | paper trade started |
| daily, while paper trades are active | 7 (tracking) | paper P&L update |
| post-application, weekly | 1 → 6 → 0 | confirm / reject / rollback |
| quarterly, every 4 batches | 0 (registry review) | retire stale checks, archive rules |

## Three stages that are not like the others

**Stage 4 is allowed to return nothing.** `no_valid_improvement` and
`no_actionable_pattern` are complete, successful results. Do not re-dispatch the
proposer asking for something better — a proposer that must produce a rule is a
noise search with a changelog. When it returns either, skip 5, 6 and 7 (their
gate is unmet) and close the cycle after 4.

Two measured baselines are read from disk, never from a document:
`data/breakout/baseline.json` (headline) and
`data/breakout/rank_slope_baseline.json` (the rank-depth slope, written by
`rank_test.py` with its own batch tag).

**Stage 5 is sealed.** It reads the pre-registered bar from the registry, not
from the proposer's narrative, and the bar cannot be renegotiated after the run.
Dispatch it with the hypothesis and the bar and **without** the pattern miner's
enthusiasm for it. It runs with `PYTHONPATH` stripped and reads
`data/breakout/baseline.json` at runtime rather than quoting a CAGR from any
document, CLAUDE.md included.

**Stage 7 refuses to loosen a bar.** If a queue entry's adoption bar differs
from what Agent 4 pre-registered, the handoff is rejected. Criteria may be
tightened, never loosened.

## Watching a run

Three layers, and they are three because each answers a different question.

**What is happening right now** — `pipeline_current.json`, overwritten at every
transition. A status board, not a ledger.

```bash
python3 src/ops/pipeline.py --current
```

It exits 1 when an agent's name has sat there for more than 5 minutes with
nothing moving, which is the only way a hung stage is visible. The same line
appears in `python3 src/ops/agent.py --status`, where the operator already
looks.

**Who ran, when, and what came out** — `pipeline_runs.jsonl`, append-only, one
line per invocation. Every agent logs its own start and finish; the gates log
`blocked` and `rejected` themselves.

```bash
tail -f data/breakout/pipeline_runs.jsonl
```

```bash
grep '"agent":"5"' data/breakout/pipeline_runs.jsonl | tail -5
```

**Telegram** — exactly three messages: cycle start, cycle end, and any gate
failure. Not per step; seven notifications a cycle is noise, and noise is how a
real warning gets missed. Pushing is **opt-in per command**, because sending is
outward-facing and neither a selftest nor a dry run may reach the operator:

```bash
python3 src/ops/pipeline.py --open-cycle --batch 20260910 --tg
```

Pass `--tg` on `--open-cycle`, on every `--handoff` (so a rejection is pushed),
and on `--close-cycle`. Leave it off for a rehearsal.

No dashboard, no web UI, no logging framework. A terminal and Telegram are the
whole surface.

## Afterwards, always

```bash
python3 src/ops/pipeline.py --vocab docs/lessons.md .claude/agents/*.md
```

```bash
python3 tests/run_selftests.py
```

The audit's headline must still reproduce the RECORDED baseline. If a rule was
applied, `--rule ... --set-status applied` prints the three follow-ups it
requires; carry them out rather than assuming they happened:

```bash
pkill -f "tg.py --listen"
```

```bash
python3 -c "import sys;sys.path.insert(0,'src');import paths,positions;print(positions.export_record())"
```

Append the cycle's finding to `docs/lessons.md` with its evidence and sample
size. A result nobody recorded gets re-discovered and re-decided differently.

## What this pipeline does not do

It does not tune parameters, pool the four buckets' P&L, relax any criterion,
touch risk invariants, rewrite a ledger, or produce a blended number. And it
cannot establish that the approach works — only forward paper trades can, and
that count is still zero.

**One thing it cannot do that the design assumed it could:** a subagent cannot
dispatch other subagents, so `src/ops/agent.py` — the scheduler — can trigger a
reminder that a cycle is due, but it cannot run one. This skill is the runtime,
in an interactive session. Anything else would be a job reporting healthy while
nothing ran, which is L91 exactly.
