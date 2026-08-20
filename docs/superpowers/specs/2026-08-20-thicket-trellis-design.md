# thicket and trellis — design

Date: 2026-08-20
Status: spec, awaiting operator review. No code written.

Two new strategies, each a behavioural clone of `sprout` at birth:

| strategy | what it adds | kind of experiment (CLAUDE.md) |
|---|---|---|
| **thicket** | NSE corporate announcements as a score input | a new input the score cannot see |
| **trellis** | chart patterns as an exit shape and a trigger | a new rule shape |

Neither is a knob. Hold length, the 3/2 mix, the score weights and the trigger
have all been measured and all sit at |t| < 1.3; another pass over them produces
a different winner each time and no knowledge.

---

## 0. The finding that shaped this design

NSE's corporate-announcements API answers **date-range** queries and reaches
back past the start of the corpus (2019-10-01). One week, market-wide, returns
2,168 rows, so the full history is roughly **770,000 announcements** — reachable
in ~356 weekly requests through the `snapshot.fetch` handshake that already
works for the XBRL backfill.

Every row is timestamped to the second. **60% of announcements land after the
15:30 close.**

That number is the whole reason this document is careful. Dating an announcement
by its calendar day — the obvious way — makes 60% of the signal information that
was not public when the trade was placed. It would not crash and it would not
look wrong. It would hand back a good CAGR built on knowledge nobody had, which
is precisely what the circuit-lock guard turned out to be (L58: about half the
CAGR was phantom).

Sentiment is therefore **backtestable**, not forward-only as first assumed.

The `desc` field is a bounded taxonomy — 62 distinct categories in a single
week, including `Resignation`, `Acquisition`, `Credit Rating`, `Dividend`,
`Corporate Insolvency Resolution Process`. So the sentiment rule can be a
published table a person can read and argue with, rather than a model whose
reasoning cannot be audited. Stdlib only; no scraping framework is needed.

---

## 1. Architecture

### 1.1 Two clones, not one strategy with two ideas

`paths.py` puts only the active strategy on `sys.path`, so one is live at a
time:

    STRATEGY=thicket python3 src/ops/audit.py
    STRATEGY=trellis python3 src/ops/audit.py

Each gets `src/strategies/<name>/` (rules) and `data/<name>/` (outputs).
Neither may ever write into `data/sprout/`: `strategies.jsonl` and
`trade_features.jsonl` are append-only and a mixed ledger cannot be un-mixed.

If both ideas lived in one strategy and it beat sprout, we could not say which
of them did it. Separating them is what makes each result attributable.

### 1.2 The clone contract

Each new strategy starts as a **behavioural clone of sprout**: same score, same
bucket, same exits, same trigger. Every new rule ships **off by default**,
switched on only by a test — the idiom `clusters.py` already uses for `RS_SKIP`
and `MAX_SCREEN`, which exist precisely so that a change to selection cannot
silently alter a live bucket and invalidate measurements taken before it.

Each clone is born with sprout's `weights.json` **copied verbatim** into its
own `data/<name>/` — `rs` 1.0, `deliv` 1.5, `liq` 1.0, `near_high` 1.0. Without
those learned weights the clone would not reproduce the baseline and the
acceptance test below could not pass. New features enter the score at **weight
0** and are moved only by a test that clears §6.2.

**Acceptance test, and it gates everything downstream:** with all new knobs off,
each clone must reproduce sprout's recorded baseline exactly —
**+7.59% CAGR / 31.0% max drawdown / 195 trades**. Not approximately. If it does
not, the fork is wrong and no finding built on it means anything. This runs in
the selftest sweep, not by hand.

### 1.3 The cost of cloning, and how it is contained

Three near-identical rule directories is real duplication, and a fix applied to
sprout will not propagate. This is accepted deliberately — sharing rule code
between strategies would mean changing sprout silently changes thicket, and
results recorded against thicket would stop being reproducible.

It is contained by `tests/diff_strategies.py`: prints every difference between
each clone and sprout, and **fails if a difference is not declared** in that
strategy's `DIVERGENCE` table. Drift becomes visible; divergence stays
deliberate.

### 1.4 What is shared

Announcement data is not a strategy's property. It is price-like data and lives
with the rest:

- `src/core/announcements.py` — sibling of `fundamentals.py`, same shape: fetch
  raw, store raw, parse later, date everything by when it became public.
- `data/announcements/raw/` — weekly JSON as fetched, never rewritten.
- `data/announcements/parsed/` — per-symbol as-of timelines.
- `data/news/` — the forward capture archive (§4).

---

## 2. The visibility rule

One function, and it carries the integrity of thicket:

> An announcement is visible to the signal computed on day *i* only if its
> timestamp is **strictly before day *i*'s 15:30 close**. Anything later becomes
> visible on day *i+1*.

Deliberately conservative: a 15:29 announcement counts for day *i* even though
acting on it would be difficult, and the fill is still the *i+1* open, so the
tradeable gap is a full session.

`fundamentals.as_of()` already does this for filings using `broadCastDate`;
this is the same idea with an intraday cutoff added, because filings publish
once a quarter and announcements publish at 22:56 on a Thursday.

**Selftest asserts the 22:56 case rolls to the next session.** That is the case
that silently inflates every downstream number, so it is tested by name and not
merely by coverage.

---

## 3. thicket — announcements as a score input

### 3.1 Features

All three are computed from announcements visible under §2 only.

| feature | asks | why it is not already in the score |
|---|---|---|
| `ann_burst` | announcements in the last 20 sessions vs this company's own trailing rate | sign-free, no judgement; a quiet microcap that suddenly files five times is doing something |
| `ann_tone` | most recent category mapped to +1 / 0 / −1 from a **frozen published table** | an auditable rule, not a model |
| `ann_flag` | did NSE demand an explanation (`Price movement`, `News Verification`) in the last 20 sessions | an exchange-flagged anomaly, orthogonal to price |

`ann_burst` is measured against the company's **own** trailing rate, not the
market's. Absolute counts would make it a size proxy: larger companies announce
more, and the score already has a size axis.

### 3.2 Neutral, never zero

A stock with no visible announcement scores **neutral** — the mid-rank — not
zero. Microcaps announce less often than small caps, so scoring silence as bad
would smuggle a second size proxy into a score that already has one.
`fundamentals.py` handles missing filings the same way and for the same reason.

### 3.3 The category table is frozen before any return is computed

`data/announcements/tone_table.json`, committed in its own commit, before the
first measurement. Written afterwards it is a hindsight machine that will
"work" every time.

Categories not in the table score neutral. The table may be **extended** only
by a commit that predates the run using it.

---

## 4. Forward news capture — starts regardless

`src/ops/newswatch.py`, run daily by the existing scheduler, appending to
`data/news/YYYY-MM-DD.jsonl`.

Not on the critical path any more, and it still starts now: the announcements
feed says a company filed something, not what the market made of it, and that
archive only ever accumulates forward. Every day it is deferred is a day of data
that cannot be recovered later.

It is **never read by a backtest** — it cannot be, it has no history — and the
code must make that impossible rather than merely unlikely.

It reads published RSS/Atom feeds only, honours `robots.txt`, identifies itself
in its User-Agent, and rate-limits to one request per source per run. A daily
capture job is a long-lived thing pointed at somebody else's server; it behaves
like a polite client or it does not ship.

---

## 5. trellis — chart patterns

### 5.1 Structural time exit

The live exit is −10% stop / +20% target / 10 trading days, **no trailing**.
Trailing was measured across six configurations at both 3% and 10% stops and
every one lowered expectancy: it lifts win rate 42% → 48% while collapsing
target hits 20% → 8%, stopping the book out of the winners that pay for
everything else.

So a pattern exit must not be a disguised trailing stop. The shape tested is:

> At day 10, **hold while the up-structure is intact; exit when it breaks.**

"Intact" cannot be left to judgement — an undefined exit condition is the same
hindsight hole this document warns about for detectors. It is frozen here, in
advance, as **all three of**:

1. the close is above the 20-day EMA, and
2. the position has not made a lower high in the last 5 sessions, and
3. ATR(14) has not expanded by more than 50% against the entry-day reading.

Held past day 10, the position is re-checked daily and exits the next open when
any of the three fails. The −10% stop and +20% target continue to apply
unchanged; this replaces **only** the time exit.

Condition 3 is the volatility-change hypothesis CLAUDE.md names as a legitimate
new shape. Conditions 1 and 2 are structure, not distance — which is what keeps
this from collapsing into the trailing stop that was already measured and
rejected.

This is worth testing precisely because 10-vs-15 days measured as noise
(t = 0.28). If the honest answer is "it depends on the chart", no fixed number
could ever have found it — which is what makes this a new shape rather than
another value.

### 5.2 Named patterns as a trigger

Flag, ascending triangle, cup-and-handle — defined **geometrically on OHLC over
a window**, which is what makes them a different shape from the live trigger
(`close >= prior 20-day high`, a threshold on one bar) rather than a new value
of it.

Each detector ships with a selftest that fires on a **synthetically constructed**
example, the way `entry.py` already tests its triggers on a generated series.

**Detector definitions are frozen before any return is looked at.** Pattern
detectors are the easiest artefact in this field to accidentally tune until they
only fire on charts that already worked.

---

## 6. Pre-registration

Batch tags: `20260820-thicket`, `20260820-trellis`. A figure without a batch tag
cannot be compared to anything.

### 6.1 Hypotheses, controls, endpoints

Controls are named as *what the live setting was a decision against*, not "the
live setting".

| # | hypothesis | control | endpoint |
|---|---|---|---|
| H1 | high `ann_burst` earns a different forward 10-day return than low | score without `ann_burst` | per-trade spread, top vs bottom tercile, ≥1,000 randomly sampled trades |
| H2 | positive-`ann_tone` entries outperform negative-`ann_tone` entries | score without `ann_tone` | per-trade spread, ≥1,000 sampled trades |
| H3 | NSE-flagged names (`ann_flag`) **underperform** — direction stated in advance | score without `ann_flag` | per-trade spread, ≥1,000 sampled trades |
| H4 | a structural time exit beats the fixed 10-day exit | live fixed exit (−10/+20/10, no trailing) | per-trade edge + worst six-month block, full backtest |
| H5 | a named-pattern trigger beats the incumbent trigger | **`breakout`**, not `none` | per-trade edge + worst six-month block, full backtest |

### 6.2 Promotion bar — fixed now, before any data is pulled

**Five pre-registered tests.** Test five things at the usual bar and roughly
one of them "wins" by luck alone — which is exactly the noise search that already
produced "two of five weight variants beat the live one at t < 0.5". So the bar
has to be raised in proportion to how many things are being asked (a Bonferroni
correction):

    usual bar 0.05, split five ways -> 0.01   ->   |t| >= 2.6

Nothing is adopted below that. This is a tightening of the repo's usual |t| > 2
and is therefore permitted; it may be tightened further and never relaxed.

**Both gates must pass, in order:**

1. **Univariate gate** (cheap, run first): the feature predicts, at |t| ≥ 2.6 on
   ≥1,000 randomly sampled trades.
2. **Bucket gate**: the per-trade edge over the control clears |t| ≥ 2.6, **and**
   both size groups stay positive, **and** the worst six-month block is no worse
   than the control's.

A feature passing gate 1 and failing gate 2 is **not adopted**. This is the `rs`
precedent: `rs` had the highest t of any feature measured and weighting it up
produced the worst of five books, because the 200-DMA gate and the breakout
trigger already capture it. Univariate significance is not marginal value to the
bucket.

Results are reported **per size group and per regime block**, with `n` beside
every figure, and anything under |t| = 2.6 is written down in those words:
*inside the noise*.

### 6.3 Kill criteria, stated in advance

- Either clone fails to reproduce +7.59% / 31.0% / 195 with knobs off → **stop
  everything**; the fork is wrong and nothing downstream means anything.
- Announcement coverage below 60% of the tradeable universe → H1–H3 reported
  **untestable**, not run on a biased subset.
- Any detector or tone-table edit dated after the run that used it → that run is
  **void**, re-run under a new batch tag.

---

## 7. Risks

| risk | mitigation |
|---|---|
| the 60% after-hours trap | §2, tested by name on the 22:56 case |
| coverage bias — microcaps announce less | neutral, never zero (§3.2) |
| pattern detectors tuned into hindsight machines | frozen definitions, synthetic selftests (§5.2) |
| testing five things against ~200 trades is a search | Bonferroni bar fixed in advance (§6.2) |
| ~770k announcements is ~0.5 GB raw | parse-and-keep only the ~8 needed fields; PDFs stay on NSE |
| three near-identical rule directories drift | `diff_strategies.py` fails on undeclared divergence (§1.3) |

---

## 8. Explicitly not in scope

- Playwright, Chromium, or any scraping framework. The repo is stdlib-only by
  design and the announcements API is JSON over `urllib`.
- Free-text sentiment scoring of `attchmntText`. The category table is the
  first cut; a lexicon adds many degrees of freedom for unclear gain and can be
  proposed later as its own pre-registered experiment.
- Any change to `sprout`, to `engine.py` risk invariants, or to the live bucket.

---

## 9. Implementation order

Three plans, executed in order. Each ends with `python3 tests/run_selftests.py`
green and the audit headline still reading `+7.59% vs +7.59%, n=195 vs 195`.

1. **Foundation** — `announcements.py` + visibility rule + a one-month slice to
   validate the parser, then the full backfill; `newswatch.py` starts capturing.
2. **thicket** — clone, acceptance test, tone table frozen, H1–H3.
3. **trellis** — clone, acceptance test, detectors frozen, H4–H5.

The one-month slice comes before the 356-week backfill deliberately: validating
a parser on 0.5 GB you already pulled is how you find out you must pull it
again.
