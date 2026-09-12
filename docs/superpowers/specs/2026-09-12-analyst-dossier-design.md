# Analyst Dossier and the Reviewer Layer — design, and the test it owes

**Date:** 2026-09-12
**Status:** Built, observation-only. Nothing in the selection or execution path reads it.
**Source:** TauricResearch/TradingAgents — their four analyst channels, their
bull/bear researcher structure, their risk/PM synthesis. Their data, their
aggregation and their decision authority all replaced; see §3.

## 1. What was actually adopted, and what was not

TradingAgents runs four LLM analysts (fundamentals, sentiment, news, technical)
into a bull/bear debate, a trader, a three-way risk debate and a portfolio
manager who emits Buy/Overweight/Hold/Underweight/Sell. The rating then drives
a simulated order.

Three of those four layers were adopted in shape. The fourth — the part where a
model's judgement reaches an order — was not, and that is the whole design
decision.

| their layer | here | why |
|---|---|---|
| four analysts | `src/ops/dossier.py`, **deterministic, no model** | same symbol + date must give the same evidence every time, or nothing downstream can ever be measured |
| bull / bear debate | `reviewer-1-bull`, `reviewer-2-bear`, **round 1 blind** | a debate's verdict that moves with speaking order cannot be scored against outcomes |
| risk debate | `reviewer-3-risk`, one reviewer, book-level | three risk personas arguing produces text, not a risk number; the invariants are in `engine.py` and are never searched |
| PM emits a rating that trades | `reviewer-4-verdict`, **recorded, never acted on** | §3 |
| decision log + reflection loop | **not ported — see §6** | their `memory.py` / `reflection.py`; this port omitted it, wrongly |

### 1a. Corrections to an earlier draft of this document

The first version of this spec was written after reading four files of the
source repo and was wrong about it in two places. Both are corrected above and
in §3, and the corrections matter because they remove two of the three things
this port first claimed as improvements:

- **"They aggregate model judgement over free-form prose."** Their sentiment
  analyst was *redesigned* for exactly the failure this repo's `sentiment.py`
  docstring describes: the old one had a social-media prompt with only a news
  tool and "led LLMs to fabricate Reddit/X/StockTwits content under prompt
  pressure (verified live)". The current one pre-fetches all three sources into
  the prompt with no tool-calling and uses structured output so the header is
  "deterministic across runs and providers". `market_data_validator.py` goes
  further -- a ground-truth OHLCV/indicator snapshot, explicitly "no LLM
  involved", which the market analyst is told to treat as the source of truth
  for any exact numeric claim, after an analyst was caught citing a
  "historically validated bounce" the data did not support. **The
  evidence-deterministic / judgement-separate split is theirs as much as ours.**

- **"They have no point-in-time discipline."** They do, in at least six places:
  `date_window.py` exists solely for look-ahead-safe filtering, and Alpha
  Vantage fundamentals, Reddit, StockTwits, yfinance news and the stockstats
  OHLCV loader each re-apply a cutoff. The memory log carries a `resolved:`
  date so a historical run cannot learn from an outcome that had not happened
  yet, with `test_memory_pointintime.py` asserting it.

## 2. The analyst channels already existed here, and three of four are measured null

This is the finding that shaped everything above. Porting an "analyst team" onto
this repo adds **no new input** — all four channels are already on disk, and
three have been through the error-bar protocol:

| channel | data here | measured |
|---|---|---|
| fundamentals | 40,775 XBRL filings, 94% cluster coverage, `broadCastDate`-dated | 4 features, 1,049 trades, every CI straddles zero, \|t\| <= 0.89 |
| sentiment | 1,019,495 announcement rows, second-dated | 11 pre-registered hypotheses, **none adopted**; `ann_tone` t=1.71 vs a bar of 2.6; graded text flipped sign at t=-1.08 |
| news | `data/news/`, forward-only from `newswatch.py` | unmeasurable by construction — no history |
| technical | `features.py`; RS, 200-DMA, breakout, delivery | **is the strategy** — not a second opinion |

So the dossier reports all four and **scores none of them**. `technical` carries
`prior=True` (it restates why the name is on the list — the `rs` lesson: highest
t of any feature measured here, worst of five books when weighted up, because the
gate and trigger already held it). `fundamental` and `sentiment` carry
`value=None` — reporting a measured null with a weight on it would be the exact
move CLAUDE.md forbids. `news` carries `backtest_safe=False` and a selftest
guard, copied from `newswatch.py`, asserting no module under `research/` or
`strategies/` imports the dossier.

There is deliberately **no composite score**. Four channels averaged into one
number, three of them null, is `pipeline.py`'s opening paragraph happening
again: a summariser of summarisers, where n and the error bar go to die.

## 3. Why the verdict does not gate an order

**They resolve outcomes; what they do not do is measure them.** Their loop is
real and this port initially missed it entirely: `store_decision` appends a
`pending` entry, and the next run on that ticker calls `_resolve_pending_entries`,
fetches the realised return **and the alpha against a benchmark**, records the
holding days, and asks the model for a 2-4 sentence reflection that is stored
and re-injected into future prompts as `past_context`.

That is an outcome loop. It is not a measurement: there is no `n`, no standard
error, no control arm and no bar a result must clear. Its mechanism is prose
from past trades re-entering a prompt -- which on this repo's evidence is the
thing to be most careful about, since a model can be argued out of a standard
and that is precisely what produced a weight table where two of five variants
"beat" the live bucket at t < 0.5. H12 below is the part that is genuinely
additive, and it is additive *to their loop*, not in place of it.

A reviewer layer that approved or rejected fills would be the largest unmeasured
change ever made to this book, and it would destroy the evidence needed to
measure it: the trades it blocked are the counterfactual.

So the order is queued by `daily.py` and filled by `positions.step` exactly as
before, and the verdict is written beside it. `stand-aside` stands nothing
aside. What accumulates is paired data — a judgement, and what the trade then
did — which is the only shape from which this layer could ever earn authority.

## 4. Pre-registration — the test this owes, declared before any verdict exists

**H12. Does the recorded verdict separate forward outcomes?**

- **Population:** every forward equity fill in `main` carrying a verdict, from
  the first one recorded. No later exclusions; `etf_trend` is out (it is a
  separate strategy directory and never enters the equity pipeline).
- **Endpoint:** mean realised **alpha** per trade -- the trade's return less the
  equal-weight return of the tradeable clusters over the same window --
  `stand-aside` verdicts against `proceed`, both arms on the same fills. Raw
  return is reported beside it, never instead of it.

  **The proxy now exists** (`src/core/market.py`, `equal_weight_return`), which
  it did not when this section was first written. It is not an index: it is an
  equal-weight buy-and-hold return over the symbols this project trades,
  computed from the corpus. Three properties make it usable and one makes it
  unpublishable:

  - the universe is the caller's, never a default -- benchmarking a microcap
    book against the liquid tercile it refuses to buy measures the wrong thing,
    and a silent default would re-open L69;
  - a name whose series ends mid-window is **carried at its last print and
    counted** in `n_truncated`, not dropped, because dropping it computes the
    benchmark over survivors exactly when the market was worst;
  - `n_used` against `n_asked` travels with every result, since a benchmark over
    40 of 900 names is a different claim from one over 880.

  What has NOT been done: nothing has checked how this proxy behaves on the real
  corpus, because `data/raw` is absent from this checkout. Before H12 quotes a
  single alpha figure, the proxy needs its own sanity pass -- coverage counts per
  window, and the equal-weight series plotted against a known NSE index over the
  same period. A benchmark nobody has looked at is not a benchmark.
- **Control:** `proceed-with-note` is reported separately and is not pooled with
  either arm. Pooling it after seeing the split is how a three-grade scale
  becomes a two-grade scale that wins.
- **Bar:** the gap must clear **2 standard errors**. Per-trade returns here have
  a standard deviation near 16%, so this needs roughly 200 trades per arm before
  it can resolve anything under ~3% — state the achieved power with the result,
  and report `n` per arm every time either figure is quoted.
- **Secondary, and pre-declared so it cannot be promoted after the fact:**
  calibration. Bin `CONFIDENCE` and compare the hit rate per bin. A reviewer at
  0.9 on everything is a column of noise, and this is where that shows.
- **Adoption path:** none. A positive H12 does **not** let the verdict gate an
  order; it earns the right to pre-register a separate, explicit test of a
  gating rule. Nothing here may be adopted on H12 alone.
- **Declared failure modes, written down now:** (a) verdicts cluster on
  `proceed` and the arms never fill; (b) coverage is so thin on microcaps that
  every verdict rests on the prior channel alone, in which case this measures
  the score, not the review.

## 5. The market channel, and what it is not

The analyst comparison found one gap that was an absence rather than a
difference of taste: **every channel was about one stock.** Nothing in the
dossier could distinguish a name that was strong from one floating on a strong
tape. TradingAgents fills that slot with FRED macro series and Polymarket
prediction markets; neither is available for this universe, so `market.state`
computes the equivalent from the cross-section already on disk -- breadth
against each name's own 50-day EMA, the median 20-day return, and the
cross-sectional dispersion.

`market.py` lives in `src/core/` and **must not import a strategy**. `clusters`
resolves to whichever strategy `paths` activated, so a shared module binding to
it would silently describe a universe nobody chose -- the failure `paths.py`
exists to prevent. A selftest asserts it on import lines rather than by
substring search, after the first version tripped on its own banned-list
literal.

The channel is **unscored and ungated**. A regime filter is the obvious thing to
build next and has never been measured here; it would need its own
pre-registration, and "the tape was weak" is precisely the kind of explanation
that survives in hindsight regardless of whether it predicted anything.

## 6. What is deliberately not built

- **No wiring into `daily.py`.** Capture has real value — a day not captured is
  a day that cannot be recovered, `newswatch.py`'s argument — but wiring a new
  module into the live scheduler is a change to live behaviour and is the
  operator's call, not a side effect of adding a research capability.
- **No review ledger schema yet.** It would be written before the first verdict
  exists, guessing at the fields, and `strategies.jsonl` and
  `trade_features.jsonl` are append-only: a mixed ledger cannot be un-mixed.
- **No trader agent.** TradingAgents' trader sizes the position. Sizing here is
  Rs 45,000 a stock against a 10% stop, an `engine.py` invariant, and a layer
  whose job is to vary it is a layer that will discover that varying it helps.
  This one is a deliberate omission and stands.

- **No `REVIEW` escape hatch yet, and that is a gap, not a choice.** Their
  `rating.py` returns `REVIEW` when a decision carries no parseable rating,
  "so a parsing failure is visible instead of masquerading as a tradeable
  neutral `Hold`". `reviewer-4-verdict`'s output block has no defined behaviour
  for a malformed emission, which is the same defect. It should take the same
  fix: an unparseable verdict is `REVIEW`, is excluded from both H12 arms, and
  its count is reported -- a silently-dropped verdict biases the very
  measurement H12 exists to run.

## 7. The omission worth fixing first: outcome resolution

The reflection loop was left out of this port because it was not read. It is
the half that makes a recorded verdict worth recording, and this repo has
everything it needs for a deterministic version of it:

- `positions.db` already holds closed forward trades with `exit_day`,
  `exit_px`, `exit_reason` and `net`, append-only with voids-not-deletes.
- The resolution date is knowable exactly -- it is `exit_day` -- so the
  point-in-time rule their `resolved:` tag encodes (#1251) is available here
  without inference.

The difference in what gets stored is the whole argument. Theirs stores a
model's prose lesson and re-reads it into the next prompt. **A port here should
store the numbers and nothing else**: verdict, confidence, coverage, then the
realised outcome when the trade closes. Prose that re-enters a later prompt is
an unmeasured channel through which a model teaches itself, and the recorded
figure is what H12 needs. If a qualitative reflection is ever wanted, it goes in
a field nothing reads back.
