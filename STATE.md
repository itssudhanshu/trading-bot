# STATE — read this first

Handoff document. If you are a person or an assistant picking this up on another
machine with no chat history, this file plus `lessons.md` is the context.
`README.md` explains what the system is; this explains where it stands and what
must not be broken.

Last updated: 2026-08-15 (epoch 4 in flight)

---

## Status

| | |
|---|---|
| Corpus | 1,695 trading days · 2,486 symbols · 2019-10-01 → 2026-08-14 |
| Modules | stdlib only, no pip install; every file has `--selftest` |
| Vocabulary | 21 predicates, 6 setup families (`spec.py`) |
| Lessons | 29 entries in `lessons.md`, each with evidence and sample size |
| Live paper trading | 0 closed trades — the runner works, the calendar is the limit |

### Holdout ledgers

    epoch 1   data/judge_ledger.json          5/50 spent   FAIL x5              RETIRED
    epoch 2+  data/judge_ledger_epoch2.json   7/50 spent   FAIL, PASS, FAIL x5  CURRENT

Best result so far, and why it still failed: `0c27a3a5754ce860` (rs_momentum,
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

---

## Where the work stopped

Epoch 3 = the SAME holdout as epoch 2 with tightened criteria (L29), not a new
holdout, so the ledger continues from 2/50. A 400-spec search (`seed 31`) was
running at the time of writing; `data/search_epoch3.log` has its progress.

### Epoch 4 (queued, requested)

Same holdout, same ledger. Differs in METHOD, not just seed:
  - `cpcv.py` PBO over the candidate set -- measures whether the ranking
    procedure generalises at all, independent of any single candidate
  - `psearch.py` (3.8x) cross-checked against a serial run on a shared seed
    BEFORE it produces anything that spends budget
Runs after epoch 3's result is known, since that result may change what epoch 4
should test.

### Next actions, in order

1. `python3 validate.py --shortlist 30` when the search finishes.
2. For anything promoted: holdout run with per-block breakdown, then
   `judge.consult(...)` -- this spends budget and is irreversible.
3. Report per-block, never a blended number. A total is not a finding when one
   block supplies it.

### The open decision that is not mine

`engine.MIN_RR = 3.0` has been implicated twice, independently:
  - L8: a 3R target needs a ~60-bar horizon, double the stated 6-week ceiling
  - L20: it blocks mean reversion, the family best suited to the bear blocks

It is the operator's risk rule. It has not been changed. If it is ever changed,
every prior result becomes incomparable and the ledger should start a new epoch
with a fresh holdout.

### Known traps

- Running more epochs mechanically against the same holdout raises N and
  therefore the DSR bar (`E[max SR]` grows: 3.26 at N=1000, 3.86 at N=10000).
  More searching makes passing harder, not easier. That is correct behaviour.
- `psearch.py` is 3.8x faster but has never produced a full production run.
  Cross-check it against a serial run on a shared seed before trusting output
  that will spend budget.
- The two local fixes in the cloned `tradingview-mcp` (`src/core/pine.js`) are
  not upstream. `tv update` will clobber them; backup at `pine.js.bak`.
