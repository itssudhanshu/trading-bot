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
- **Endpoint:** mean realised return per trade, `stand-aside` verdicts against
  `proceed`, both arms on the same fills over the same window.
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

## 5. What is deliberately not built

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
