# STATE — read this first

Handoff document. If you are a person or an assistant picking this up on another
machine with no chat history, this file plus `lessons.md` is the context.
`README.md` explains what the system is; this explains where it stands and what
must not be broken.

Last updated: 2026-08-15 (epoch 3 recovered and verified; epoch 4 not yet run)

---

## Status

| | |
|---|---|
| Corpus | 1,695 trading days · 2,486 symbols · 2019-10-01 → 2026-08-14 |
| Modules | stdlib only, no pip install; every file has `--selftest` |
| Vocabulary | 21 predicates, 6 setup families (`spec.py`) |
| Lessons | 36 entries in `lessons.md`, each with evidence and sample size |
| Trial pool | 193 Sharpes · `E[max SR]` 0.3215 · git-tracked (rule 0) |
| Live paper trading | 0 closed trades — the runner works, the calendar is the limit |

### Holdout ledgers

    epoch 1   data/judge_ledger.json          5/50 spent   FAIL x5              RETIRED
    epoch 2+  data/judge_ledger_epoch2.json   7/50 spent   FAIL, PASS, FAIL x5  CURRENT

**All epoch 2-4 numbers are WITHDRAWN as measurements.** Two defects in
`portfolio_path` -- a heat leak when positions shared an exit day (L34) and a
non-total sort key that made admission depend on input order (L38) -- produced
`n_taken`, `exp`, `dd`, `capacity` and everything `report.stats` hands the judge.
Both are fixed with regression tests; epochs 3 and 4 are re-running. The 7
consultations stay SPENT: those hypotheses met the holdout, and refunding budget
for a computation bug would let any future error buy back trials.

Historical, on the old code -- do not quote these figures: `0c27a3a5754ce860` (rs_momentum,
epoch 3) returned +11.56% on the holdout with ALL FOUR blocks positive -- the
only candidate ever to profit in every regime. PSR 0.9951, so significant on its
own. It failed on DSR alone: SR 0.2475 against E[max SR] 0.3215 for N=193 trials.
That is what luck looks like at that trial count. More searching raises the bar
further; only fresh out-of-sample data can settle it.

Epoch 1 used a contiguous "last 12 months" holdout. That was a design error: it
put the whole bull market in train and the bear out of sample, so it tested
regime survival rather than edge (lessons L19). Its five specs are RETIRED and
must not be re-tested.

The current holdout is regime-stratified (`split.py`), four half-year blocks:

    2020-H2 BULL | 2023-H1 flat | 2025-H1 BEAR | 2026-H1 BEAR

The epoch-2 PASS (`cfe9788decd6afc8`, +6.31%) is **not** a working strategy: its
entire profit came from the one BULL block and it lost in both BEAR blocks. It
would FAIL today's tightened criteria. See L27/L28.

---

## The rules. Do not break these.

0. **The trial pool is cumulative.** `data/trial_sharpes.json` holds every
   candidate Sharpe ever tested against this holdout. Deflation uses all of
   them. Never reset it per search or per epoch -- that silently removes the
   multiple-testing correction (L31).

1. **The holdout budget is per-holdout, not per-machine and not per-epoch.**
   It lives in `data/judge_ledger_epoch2.json`, which is git-tracked on purpose.
   Two machines searching with separate ledgers = two budgets against the same
   data = the overfitting defence is gone, invisibly. Search on ONE machine, or
   commit/pull the ledger between runs. Data collection is safe on both.

2. **Criteria may be tightened, never loosened.** `judge._verdict` and
   `validate.py` thresholds are pre-registered. Tightening a test that let
   something through is defensible; relaxing one that rejected a candidate is
   how this discipline dies. If you change them, record it in `lessons.md` with
   the reasoning and the date, and re-run everything.

3. **Invariants in `engine.py` are never searched.** MIN_RR, portfolio heat,
   liquidity cap, surveillance exclusion, cost viability. A generator that can
   vary its own risk limits will discover that removing them improves backtest
   returns.

4. **The generator emits data, never code.** Specs are JSON over the bounded
   `spec.PREDICATES` vocabulary. No LLM-authored code executes in the money path.

5. **Never run a search against the holdout.** `generator.py` asserts the seal.
   `report.py --holdout` refuses specs absent from `promoted.jsonl`.

6. **Surveillance data cannot be refetched.** ASM/GSM/F&O-ban are published for
   the current day only. A missed session is permanent. Bhavcopy history IS
   refetchable (`backfill.py`), so a fresh machine does not need the 449 MB of
   `data/raw` transferred -- only the days collected live are irreplaceable.

   **Bhavcopy alone is not the corpus.** `backfill.py` fetches only bhavcopy.
   The universe also needs `equity_master.csv` in the newest snapshot, or the
   non-equity denylist is EMPTY and 254 ETFs and liquid funds enter the corpus
   with no error at all -- 2,740 symbols where the real universe is 2,486, and
   every number downstream moves with it (L36). `features.load_corpus` now
   refuses to build a corpus without it. A rebuilt machine is not ready until
   its search header prints `corpus 2486 symbols`.

---

## Where the work stopped

Epoch 3 = the SAME holdout as epoch 2 with tightened criteria (L29), not a new
holdout, so the ledger continues. Its 400-spec search (`seed 31`) completed and
five candidates were judged; all five FAILED. The ledger stands at 7/50.

**Epoch 3 is recovered.** A throwaway smoke test had destroyed its 193 trial
Sharpes (L32); re-running the deterministic seed reproduced them. Verified three
independent ways, not assumed:

  - 193 candidates, with the top-10 hashes and portfolio expectancies matching
    `data/search_epoch3_redo.log` line for line
  - the trial pool equals the recovered candidates' Sharpes exactly
  - `E[max SR] = 0.3215` recomputed at N=193 -- the exact figure recorded above
    for the near miss, to four decimals

`data/trial_sharpes.json` and `data/candidates_seed31.jsonl` are both committed.

### Epoch 4 (not yet run)

Same holdout, same ledger. Differs in METHOD, not just seed:
  - `cpcv.py` PBO over the candidate set -- already wired into
    `validate.py --shortlist N`, which prints it below the promotion table
  - `psearch.py` cross-checked by `xcheck.py` BEFORE it produces anything that
    spends budget

**Run it on the Mac.** This 6.9 GB Windows machine cannot. `psearch._init` has
every worker call `features.load_corpus()` in full, so six workers want ~20 GB;
and the serial path measured ~98 s/spec here against ~25 s/spec on the Mac,
putting a 400-spec search at ~10 hours. Bounding the memo (L35) made the
pipeline runnable here at all -- it did not make it fast.

### Autonomous pipeline

`pipeline.py` runs the whole next-step chain unattended:

    search (fresh unused seed) -> PBO gate -> validate -> report

It STOPS on any pre-registered condition: PBO > 0.5, budget exhausted, or
nothing promoted. It does NOT consult the holdout -- that spends a lifetime
budget of 50 and an unattended loop would drain it in two runs. `--consult`
exists for a human who means it and caps at 3 per run.

    python3 pipeline.py --cycles 2            # research only, no budget spent
    python3 pipeline.py --cycles 1 --consult  # deliberate, spends up to 3

Weekly via deploy/trading-bot-pipeline.plist (Sunday 02:00, no --consult).
State and seeds used: data/pipeline_state.json -- seeds are never reused, so a
cycle is always a fresh hypothesis set.

### Next actions, in order

0. On whatever machine runs it, confirm the header prints `corpus 2486
   symbols`. Anything else means the non-equity denylist is missing and nothing
   produced is comparable to any prior epoch (L36).
1. `python3 xcheck.py` -- must print AGREE before psearch is trusted with
   anything that spends budget.
2. Back up `data/trial_sharpes.json`, then
   `python3 generator.py -n 400 --seed 41 --parallel 6`.
   Seed 41 is unused; 31 and 42 are spent. The run APPENDS to the trial pool,
   and re-running any seed overwrites that seed's own `candidates_seed{N}.jsonl`
   archive -- a case L32's mitigation does not cover, and one that already cost
   the recovered set once during this session.
3. `python3 validate.py --shortlist 30` -- promotion table, then PBO.
   **If PBO > 0.5, stop.** The search is fitting noise and no candidate from it
   is evidence, however good that candidate's own numbers look.
4. For anything promoted: holdout run with per-block breakdown, then
   `judge.consult(...)` -- this spends budget and is irreversible.
5. Report per-block, never a blended number. A total is not a finding when one
   block supplies it.

### The open decisions that are not mine

**`portfolio_path` drops concurrent positions that share an exit day** (L34).
Two open positions with the same `exit_day` collide on a dict key; the second
destroys the first, so its risk is never returned to the heat budget and its
symbol is never released. The heat leak is monotonic for the rest of the run.

The patch is two lines and a regression test, and it is deliberately NOT
applied. Applying it changes `portfolio_expectancy` for every spec, which makes
epoch 3 and epoch 4 incomparable -- the same standard set below for MIN_RR.
Operator decision, 2026-08-15: document now, fix as a separate deliberate step.
Epoch 4 must therefore run WITHOUT it, matching the recovered epoch 3 baseline.

What it touches: the train ranking, the walk-forward promotion gates, and every
holdout verdict recorded so far. What it does NOT touch: the trial pool and DSR,
PBO block P&L, and every unconstrained statistic -- all computed over the full
trade list rather than the admitted subset. The promotion gates fail
conservatively; the expectancy figures move in no fixed direction.

**`engine.MIN_RR = 3.0`** has been implicated twice, independently:
  - L8: a 3R target needs a ~60-bar horizon, double the stated 6-week ceiling
  - L20: it blocks mean reversion, the family best suited to the bear blocks

It is the operator's risk rule. It has not been changed. If it is ever changed,
every prior result becomes incomparable and the ledger should start a new epoch
with a fresh holdout.

### If a comparison passes, check what it compared

`xcheck.py` compared signal SETS, printed AGREE, and the RANKINGS disagreed. I
cited it as proof the parallel path was trustworthy. It now compares n_taken,
portfolio_expectancy and capacity_ratio as well, and only then says AGREE.

Two failures worth carrying forward, both from this session:
  - fixing the first bug found and assuming it explains the symptom is how the
    second one survives (L38: the heat leak was real, and was not the cause)
  - a test whose fixture cannot exhibit the bug will pass forever (L34: 20
    positions shared an exit day and the assertion never required the book to
    empty)

### Known traps

- Running more epochs mechanically against the same holdout raises N and
  therefore the DSR bar (`E[max SR]` grows: 3.26 at N=1000, 3.86 at N=10000).
  More searching makes passing harder, not easier. That is correct behaviour.
- `psearch.py` is 3.8x faster but has never produced a full production run.
  Cross-check it against a serial run on a shared seed before trusting output
  that will spend budget. Note what `xcheck.py` actually compares: SIGNAL SETS
  per spec. It does not compare `n_taken` or `portfolio_expectancy`, and those
  DO differ between the serial and parallel paths -- `portfolio_path` sorts
  trades by `(entry_day, -rank_score)` with a stable sort, so tied trades keep
  input order, and the parallel merge orders them differently. AGREE from
  xcheck means the signals match, not that the ranking will.

- Re-running a seed overwrites that seed's own `candidates_seed{N}.jsonl`.
  L32 added the archive so a DIFFERENT run could not clobber it; the same seed
  still can, and did during the epoch 3 recovery session. Copy the recovered
  artifacts outside the repo before re-running anything.

- Anything that loads the corpus holds it in RAM for the whole run, and the
  indicator memo adds ~117 MB per distinct (indicator, period) before L35's
  bound. Budget ~3.5 GB for a bounded run; the unbounded version needed more
  than 16 GB for 30 specs.
- The two local fixes in the cloned `tradingview-mcp` (`src/core/pine.js`) are
  not upstream. `tv update` will clobber them; backup at `pine.js.bak`.
