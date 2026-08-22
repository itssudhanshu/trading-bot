# Lessons

Structural findings that constrain the search space. The generator reads this
file. Entries are CONSTRAINTS and FAILURE MODES, never tuned parameters --
"lookback=47 worked" is overfitting; "3R is unreachable at this horizon" is a
property of the market.

Each entry carries its evidence and sample size. An entry without evidence is
an opinion and does not belong here.

---

## L1 — A 3R target is unreachable at a 3-6 week horizon (strong)
Across 67 Stage-2 breakout instances (2022-2026, NSE EQ), maximum favourable
excursion within 30 trading days:

    p50 +0.59R   p90 +1.18R   p95 +1.79R   max +2.33R

Not one instance reached 3R. Zero of 64 closed trades exited on target; 53 died
on the time stop, 11 on the protective stop.

**Consequence:** `engine.MIN_RR = 3.0` is satisfiable on paper and unreachable
in practice. It does not act as a risk control here -- it guarantees that no
winner is ever realised, converting every good trade into a flat time-stop exit.
The gate validates *planned* R:R; the market supplies the *achievable* one.

**Open decision for the operator:** MIN_RR is an invariant and deliberately not
searchable. Reconciling it needs one of: a tighter stop definition (see L2, which
does not work on its own), a longer holding horizon, or an explicit decision to
revise the 1:3 rule. That last one is the operator's call, not the generator's.

## L2 — Tightening stops raises 3R reachability but lowers expectancy (moderate)
Same signals, four stop definitions:

| stop rule              |  n | median stop | median MFE | hit 3R | expectancy |
|------------------------|----|-------------|------------|--------|------------|
| swing_low(10) - 0.5ATR | 64 | 14.7%       | +0.59R     |  0     | -Rs 140    |
| swing_low(5)           | 68 |  9.3%       | +0.93R     |  3     | -Rs 461    |
| ATR x 2.0              | 67 |  7.5%       | +1.19R     |  3     | -Rs 764    |
| ATR x 1.5              | 68 |  5.9%       | +1.54R     | 11     | -Rs 467    |

Tightening the stop shrinks R, so the same price move counts for more -- but
stop-out frequency rises faster than the reachability gain. All four are
negative. Do not propose "tighter stop" as a fix for L1 on its own.

## L3 — ETFs trade in the EQ series and must be excluded (settled)
340 of 2,463 EQ symbols are ETFs, index trackers and liquid funds (NIFTYBEES,
LIQUIDPLUS, AUTOBEES...). They produced 15 of 113 signals before exclusion.
A volatility-contraction breakout on a liquid debt fund is noise.

Excluded as a **denylist** (EQ symbols absent from NSE's company master), never
as an allowlist -- requiring master membership would drop every delisted company
from history and silently introduce survivorship bias.

## L4 — The Stage-2 spec is too restrictive to validate (strong)
94 signals from ~2.3M bar-opportunities (0.004%), ~24/year, stable across all
four years. Per walk-forward fold: n = 11-21.

At that density a single spec cannot clear the evidence bar. The generator's
objective is therefore **specs loose enough to be testable while still
selective**, not specs that maximise backtest return. A spec yielding under
~30 instances per fold should be rejected before its P&L is even examined.

## L5 — Methodology: the numbers above are diagnostic, not evidence of edge
Every run so far used the FULL corpus, with no train/val split, and four stop
variants were compared on the same data. That is enough to establish
*structural* facts (a target is unreachable, a universe is contaminated) which
are properties of the distribution and robust to selection.

It is NOT enough to conclude anything about edge. The expectancy figures above
must not be quoted as results. Performance claims require the walk-forward
harness, and a holdout consultation must be spent only on a spec that already
survived train/val.

## L6 — Selectivity needs an upper bound, not just a lower one (settled)
The first search ranked an `ema_pullback` spec first on train expectancy:
43,342 trades, +Rs 110/trade, from 400 symbols over 3 years -- 14% of all
bar-opportunities, 36 signals per symbol per year.

That is not a setup. With n that large a microscopic edge averages into a
positive number and outranks every genuine candidate; the portfolio simulation
would reject nearly all of it on heat and concurrency anyway, so the ranked
figure describes a strategy that cannot be traded.

L4 asked for specs "loose enough to be testable while still selective".
Only the first half had been implemented. Added
`MAX_SIGNALS_PER_SYMBOL_YEAR = 6.0`, screened before P&L like the lower bound.

**General form:** every screen expressed as a floor needs its ceiling stated
too, or the search walks to the boundary the floor does not defend.

## L7 — The generator ranks on train expectancy, which is triage only (standing)
`candidates.jsonl` is sorted by train expectancy so a human can look at the top
of a long list. That ordering is NOT evidence and must never be reported as a
result -- it is the maximum of many trials on one dataset, which is precisely
the quantity that overstates itself. Only the walk-forward folds, and finally a
holdout consultation, carry inferential weight.
## L8 — A longer horizon DOES rescue 3R reachability (strong) — resolves L1
Across 91 evaluated specs, median target-hit rate by holding horizon:

    hold=10 bars -> 0.5%    hold=20 -> 3.9%    hold=30 -> 5.2%
    hold=45 bars -> 10.3%   hold=60 -> 15.0%

Monotonic and steep. L1 concluded 3R was unreachable; it was unreachable **at
30 bars**. The constraint was the horizon, not the R:R rule.

**The tension, precisely stated:** 60 bars is ~12 weeks, double the persona's
stated 3-day-to-6-week window. So the 1:3 R:R rule and the 6-week ceiling are
jointly infeasible on NSE equities -- each is reasonable alone. Resolving it
means extending the horizon past 6 weeks, or accepting a lower R:R. That is an
operator decision; the generator cannot relax an invariant and must not try.

## L9 — A position must be economically viable, not merely risk-sized (settled)
The seed-42 search surfaced a spec reporting avgR -4.28 alongside POSITIVE
Rs 987 expectancy. Both were computed correctly; the trades were nonsense.

When the liquidity cap sizes a position down to a few shares, fixed brokerage
(Rs 20/order, Rs 40 round trip) dwarfs the risk base. Real case: KOTAKMNC,
risk/share Rs 0.94, qty 1, Rs 45 booked loss -> R = -47.7. Worst instance in
that spec: R = -10,939.

Added `MAX_COST_RATIO = 0.10` to the gate -- round-trip costs must stay under
10% of the risk being taken. Effect on that spec: avgR -4.28 -> +0.25, R range
collapsed from -10,939..+9.6 to -6.9..+9.6, 517 unviable trades removed.

**One invariant killed three pathologies:** unviable sizing, illiquid
instruments that escaped classification, and the R-multiple blow-ups they
produced downstream. Prefer the invariant that makes a class of nonsense
impossible over the filter that catches today's instance of it.

## L10 — The non-equity denylist misses instruments that stopped trading (open)
L3's denylist is (EQ symbols trading TODAY) minus (company master). An ETF that
delisted before the master snapshot is present in historical bhavcopy, absent
from today's bhavcopy, and therefore never denied. KOTAKMNC, ICICI5GSEC,
ICICIINFRA and KOTAKCONS all reached the corpus this way.

The asymmetry is structural: for a symbol absent from today's master we cannot
tell a delisted COMPANY (must keep, or we get survivorship bias) from a delisted
ETF (must drop). NSE publishes no point-in-time instrument classification.

Largely neutralised by L9 -- these instruments are illiquid and now fail the
viability gate regardless of classification. Proper fix is to snapshot an ETF
list daily from today forward, accepting that history stays imperfect.

## L11 — Search results are invalidated by any gate change (standing)
`candidates.jsonl` is only valid for the gate that produced it. The L9 invariant
changed which signals are admissible, so every prior result is stale and must be
regenerated before it is read. Gate changes are not backward compatible; do not
compare candidate runs across them.
## L12 — Rank on portfolio-realizable expectancy, not unconstrained (settled)
The seed-42 rerun surfaced specs with 11,252 and 17,856 instances over three
years. Both cleared `MAX_SIGNALS_PER_SYMBOL_YEAR` (2.9/symbol-year, under the
6.0 ceiling) yet generate ~6,000 signals a year.

Capacity is the binding constraint: `MAX_PORTFOLIO_HEAT` 6% at
`RISK_PER_TRADE` 0.5% permits 12 concurrent positions, which at a 30-bar hold
is roughly 100 trades a year. A spec offering 6,000 exceeds capacity ~60x, so
its unconstrained expectancy describes trades that could never have been taken,
and the ones that WOULD be taken are an arbitrary chronological subset.

`backtest.portfolio_path` now returns the admitted subset, and results carry
`n_taken`, `capacity_ratio` and `portfolio_expectancy`. The generator ranks on
portfolio expectancy.

Instance count remains the EVIDENCE base (statistical power); portfolio
expectancy is the RETURN estimate. Conflating the two is what let a spec with
60x more signals than capacity reach the top of the table.

## L13 — Gap detectors must not report known-absent data (settled)
The first surveillance-gap report flagged 33 "unrecoverable" days -- the entire
backfilled history, which never had ASM/GSM by design. A detector that fires on
expected absence gets ignored, which costs more than having no detector.

Anchored on the earliest `asm.json`: only days after collection began can be
gaps. Applies generally -- alert on the difference between what SHOULD exist and
what does, never on the difference between what you WANT and what exists.
## L14 — An over-subscribed spec needs a selection rule to be a strategy (settled)
`validate.py` rejects specs whose instance count far exceeds portfolio capacity
as "under-specified". That diagnosis implies its own fix.

When 6,000 signals a year compete for ~100 slots, which ones get taken was
decided by list order -- chronological accident, not strategy. The result was
therefore not reproducible in spirit: reorder the input and the P&L changes.

Added `rank.by` to the vocabulary (`rr`, `turnover`, `deliv_pct`, `none`).
`portfolio_path` now sorts contested slots by `(entry_day, -rank_score)`, so
the spec itself decides its preference among simultaneous signals.

This turns a rejected class into an evaluable one, and adds a real search
dimension: "which breakout do I take when six fire at once" is a genuine
strategy question that the vocabulary previously could not express.

**Watch for the general case:** any place the harness breaks a tie arbitrarily
is a hidden parameter. Either the spec sets it or the result is not reproducible.
## L15 — Holdout warm-up is not lookahead; loading only holdout bars is a bug (settled)
The first holdout report returned "no trades taken". The spec was fine; the
harness loaded ONLY holdout bars, so a 200-period SMA had no history to seed
from and ~200 of 245 sessions were unusable.

Warm-up from pre-holdout bars is correct: seeding an indicator is how the
indicator is defined. The seal forbids SEARCHING on holdout data, not knowing
prices existed before it. `report.py` now loads two years of warm-up and
restricts SIGNALS, not bars, to the holdout window.

**Failure mode to watch:** the bug reported zero trades rather than raising.
A silent empty result reads as "the strategy does not trade here", which is a
plausible-looking wrong answer -- the most expensive kind.

## L16 — Guard the holdout in code, because discipline slips (settled)
While smoke-testing the reporting path I ran an unpromoted spec against the
holdout "just to check the plumbing", and thereby learned a holdout result for a
hypothesis that had never earned one. Small leak, entirely self-inflicted, by
the person who wrote the rule.

`report.py --holdout` now refuses any spec absent from `promoted.jsonl` unless
`--force-holdout` is passed explicitly. The smoke test belonged on train data.

**General form:** a rule that lives only in intent gets broken by the person who
wrote it, during unrelated work, without noticing. If a boundary matters, the
tooling has to hold it. This is the same principle as the sealed judge returning
one bit -- applied to the author instead of the agent.
## L17 — First holdout consultation: FAIL (spec 750dec7c0f7f56a6), 1/50 spent
`ema_pullback`, promoted on four positive walk-forward folds (+2836, +3665,
+184, +346), all with >=30 taken trades.

Holdout (2025-08-15 onward, never searched): +0.75% return, 26 trades, profit
factor 1.15, expectancy +Rs 289/trade, max DD 2.4%.

Superficially positive. It failed on evidence -- 26 taken trades against
`judge.MIN_TRADES = 30` -- and the concentration check shows the verdict was
right on the merits too:

    total P&L +Rs 7,512    best single month +Rs 23,124    without it -Rs 15,612
    months positive: 1/4

The entire out-of-sample profit is one month. Nothing about the other three
suggests an edge. A less disciplined harness would have reported "+0.75% out of
sample, profit factor 1.15" as a success.

**Consequence:** report concentration alongside every result. A total is not a
finding when one period supplies all of it. This spec is now burned -- it is in
the ledger, and its holdout result must not steer any further search.

## L18 — Relative strength was the vocabulary's biggest hole (settled)
The persona named institutional sector rotation and structural momentum as core
edges, and the vocabulary had no way to express either. "Up 20% in three months"
is meaningless until you know the rest of the universe was up 30%.

Added `rs_rank_above(lookback, pct)` -- cross-sectional percentile of trailing
return across the whole universe, at 20/60/125/250-day windows -- plus
`close_near_high` for breakout quality, and an `rs_momentum` family.

Cross-sectional again: like breadth, RS rank needs the entire universe on each
date, which is exactly what a per-symbol data source cannot serve and why the
date-major bhavcopy corpus is the right substrate.
## L19 — The holdout is a bear market and the whole vocabulary is long-only (STRONG, structural)
Fold-by-fold market conditions across train, plus the holdout:

    fold 1  2023-03..2023-10   breadth 67.5%   median stock  +27.9%
    fold 2  2023-10..2024-06   breadth 59.7%   median stock  +12.3%
    fold 3  2024-06..2025-01   breadth 53.1%   median stock   +2.5%
    fold 4  2025-01..2025-08   breadth 40.4%   median stock  -11.0%
    HOLDOUT 2025-08..2026-08   breadth 39.6%   median stock   -9.6%

A monotonic decay from broad bull to broad bear, with the holdout a continuation
of fold 4. In the 30-spec walk-forward table, fold 4 was negative in **27 of 30**
specs. That is not thirty strategies failing independently -- it is one regime.

Every setup in the vocabulary (stage2_breakout, vcp, ema_pullback, rs_momentum)
is long-only momentum, structurally long beta. When the median stock falls ~10%,
no amount of searching inside that class produces a profitable out-of-sample
result. Three holdout consultations confirm it: -7.92%, -6.42%, and +0.75%
(which was one month).

**Two consequences.**

1. *A design flaw of mine.* Choosing "most recent 12 months" as the holdout put
   the entire bull market in train and the bear out of sample. That maximally
   confounds strategy quality with regime: a contiguous holdout tests "does this
   survive the next regime", not "does this have an edge". I picked it before
   knowing the regime split; it was still the wrong call, and the promotion
   criterion "positive in 3 of 4 folds" compounds it by selecting specs that
   worked in the bull and are already failing in the newest fold.

2. *"Positive in 3 of 4 folds" ignores recency.* A spec positive in folds 1-3 and
   negative in fold 4 is worse than the reverse, and the criterion cannot see the
   difference. Not changing it mid-flight -- noted for the next pre-registration.

**Relative performance, stated without spin:** the surviving spec returned +0.75%
while the median stock fell 9.6%. That is ~10pp of relative performance, and it
is NOT evidence of edge -- 26 trades, one profitable month. Reporting it as
market-beating would be exactly the self-deception this harness exists to stop.

## L20 — The 1:3 R:R invariant restricts which strategy FAMILIES are expressible
Mean reversion typically runs lower reward:risk than 3:1 and higher hit rates.
With `MIN_RR = 3.0` unsearchable, a whole class of setups that could work in the
holdout's regime cannot even be proposed.

Combined with L8 (3R needs a 60-bar horizon, double the persona's 6-week window),
the 1:3 floor is now constraining the system in two independent ways. This is the
second time the evidence has pointed at the same operator decision.
## L21 — Five holdout consultations, five failures. Search stopped. (conclusive)
    spec        setup            holdout   trades   PF     verdict
    750dec7c    ema_pullback      +0.75%      26   1.15    FAIL (n<30, 1 month)
    9d1de347    ema_pullback      -7.92%      80   0.34    FAIL
    83660821    rs_momentum       -6.42%      66   0.79    FAIL
    c4fbf480    ema_pullback      -4.74%      29   0.57    FAIL
    32ffca50    vcp               -4.98%      59   0.79    FAIL

All five cleared walk-forward on train first. `c4fbf480` was positive in ALL
FOUR train folds including fold 4, the bear fold that killed 27 of 30 specs --
the single best structural reason to expect bear-holdout survival. It still lost
4.74%.

Roughly 1,000 specs searched across 6 families and 21 predicates. Budget 5/50.

**Stopping is the correct action, not a failure to try harder.** Every further
seed is another draw from a distribution whose out-of-sample mean is negative;
running enough of them WILL eventually produce a spec that looks good on the
holdout, and that spec will be noise dressed as a discovery. The budget exists
precisely to make that expensive.

**What is actually blocked, in order of leverage:**

1. `MIN_RR = 3.0` -- blocks mean reversion (lower R:R by nature) and needs a
   60-bar horizon (L8, L20). The regime best suited to the holdout is the one
   class the invariant cannot express. Operator decision.
2. The contiguous holdout confounds regime with quality (L19). A regime-stratified
   or purged-random split would test edge rather than regime survival.
3. Long-only cannot profit from a -9.6% median stock. Short exposure in India
   needs F&O -- different instruments, margin, and expiry mechanics; not a
   parameter change.

None of these is fixed by more searching, which is why the search stopped.
## L22 — Epoch 2: seven years of history and a regime-stratified split (design change)
Fixes the design error named in L19. Two changes, both made before any epoch-2
result was examined.

**More history.** Probed NSE's delivery-bhavcopy archive to its floor: 2019-09-16
returns 404, 2019-10-01 returns 200. Backfilled to that floor -- 1,695 trading
days, 2,486 symbols, 2019-10 to 2026-08. A legacy format reaches 2015 but carries
no delivery column, so it was not used; delivery % is central here.

`backfill.ARCHIVE_START` now clamps requests. Without it, every pre-archive
session 404s and gets recorded as a holiday -- silently poisoning holidays.json
with hundreds of real trading days.

**Enough cycles to stratify.** Seven years contains five BULL, five BEAR and four
flat half-years, so holdout blocks can be drawn from EACH regime class:

    train    885 days   BULL 2021-H1, 2022-H2, 2023-H2, 2024-H1
                        BEAR 2020-H1, 2022-H1, 2024-H2, 2025-H2
    holdout  491 days   BULL 2020-H2 | flat 2023-H1 | BEAR 2025-H1, 2026-H1
    purged   319 days   60-day bands around every boundary, dropped from TRAIN only

Both sides now contain bull and bear, so the question becomes "does this work
across regimes" rather than "did it survive one transition".

Blocks came from a seeded draw (`_choose_holdout(seed=0)`), then frozen. The
draw's first output differed from what I had hand-written; I took the draw. A
deterministic choice overridden when its result looks inconvenient is just
hand-picking wearing a seed.

**Honest limits of this change.**
- It was made after five failures. The justification is the regime confound
  measured in L19, not the failures themselves -- but the ordering is what it is
  and is recorded here.
- Epoch 1's five specs are RETIRED, not re-tested. Two of their holdout blocks
  are now holdout blocks in epoch 2; re-testing them would be reusing known
  results.
- Epoch 1's ledger is preserved unchanged. Epoch 2 gets its own budget.
- 2025-H2 and 2026-H2 moved from old-holdout into train. I have seen aggregate
  behaviour there. The search is seeded and programmatic, so this does not steer
  it, but it is not zero.
## L23 — The MCP bug was querySelector, and I misdiagnosed it three times (settled)
`pine set` failed with "Could not open Pine Editor" while the editor was open
and working. Three wrong diagnoses before the right one:

  1. "selector mismatch"      -- a guess stated as a finding
  2. "detached stub, editor lives in another CDP target" -- measured a 0x0
     element WHILE THE PANEL WAS CLOSED and treated that as structural evidence
  3. "the panel isn't really open" -- falsified when the user opened it and the
     element still measured 0x0

Actual cause: with the editor open, TradingView renders TWO nodes matching
`.monaco-editor.pine-editor-monaco` -- a 0x0 placeholder and the live 520x693
instance. `document.querySelector` returns the first. The MCP walked the
placeholder, found no React fiber, and threw. On the live element the walk
succeeds immediately (fiber depth 1, monacoEnv depth 11, 5 editors).

Fixed by selecting the node with non-zero width. Local edit to a cloned repo;
`tv update` will clobber it.

**The pattern, again:** trusting a status instead of verifying the thing. The
MCP trusted querySelector's first hit; I trusted `ui panel ... open` returning
success. Same error as an HTTP 200 that is not the file you asked for (L-holiday)
and a promotion criterion counting signals instead of trades (L12).
`tv.editor_mounted()` now checks for a RENDERED editor, not a command's return.

## L24 — Pine templates verified against the compiler, not against memory (settled)
`pine.py`'s 21 translations were written from my own knowledge of Pine syntax --
precisely where an LLM hallucinates deprecated or invented functions.

With the MCP bridge working, TradingView's own compiler is available as ground
truth, so `pine.verify_all()` now compiles a minimal script per predicate and
reports real compiler errors. Result: **16/16 translatable templates compile
clean on v6**, 5 are not expressible by design (delivery %, breadth, RS rank,
surveillance -- data TradingView does not have).

This is stronger than a syntax-validating helper: it is the actual compiler that
will run the script, on the actual TradingView build installed here.

**Where it still falls short:** the compiler says a construct is wrong, never
what is right. For Pine features not yet used here (arrays, matrices,
request.security, strategy()), a documentation source would genuinely help --
the compile loop catches the error but cannot author the fix.
## L25 — Deflated Sharpe Ratio implemented; the trial count is now priced in
L7 warned in prose that "the ranking is the max of many trials". `dsr.py` now
computes it, from Bailey & Lopez de Prado (2014), verified against two sources
rather than written from memory (the first extraction garbled E[max SR] into a
Cornish-Fisher expansion; it was not used):

    E[max SR] ~= sqrt(V[SR]) * ((1-g)*Z^-1[1-1/N] + g*Z^-1[1-1/(N*e)])
    DSR        = PSR evaluated at SR* = E[max SR]

With unit variance across trials, the best Sharpe expected from pure noise is:

    N=10 -> 1.58    N=100 -> 2.53    N=1000 -> 3.26    N=10000 -> 3.86

This project has searched roughly 1,000 specs. **Any candidate whose Sharpe is
not meaningfully above the N=1000 bar is indistinguishable from the luckiest
coin flip.** That is now computable per candidate instead of being a caveat
paragraph.

Two implementation notes that decide whether the number is honest:
- `trial_sharpes` must include EVERY candidate tested, not the survivors.
  Passing survivors understates V[SR], understates E[max SR], and flatters
  precisely the figure being deflated.
- Sharpe is per-period, not annualised. Annualising one side of the comparison
  inflates the DSR silently.

## L26 — Reviewed external sources; CPCV is the one worth adopting
`Bhala-Srinivash/nse-trading-skills` is real but is nine `SKILL.md` prompt
frameworks (RSI divergence, Fibonacci, position sizing, multi-timeframe), not
executable code. The techniques are largely already expressible in `spec.py` as
testable predicates, and it uses yfinance/Groww for data, which cannot serve the
cross-sectional access pattern this system needs. Nothing to adopt.

The literature finding that matters: **Combinatorial Purged Cross-Validation
(CPCV)** is reported to beat walk-forward on both Probability of Backtest
Overfitting and DSR, by generating many train/test PATHS rather than one
chronological sequence, each purged and embargoed.

Relevant because `validate.py` currently runs a single expanding-window
walk-forward -- exactly the method CPCV is reported to dominate. Epoch 2's block
split is a step in that direction (multiple blocks, purged) but still evaluates
one path. Candidate for epoch 3; not changing mid-epoch.
## L27 — Epoch 2, first PASS: and the stratified holdout immediately earned itself
`cfe9788decd6afc8`, turtle_soup (false-breakdown reclaim), hold 45, rank turnover.
All four train folds positive with 36-64 taken trades each.

    HOLDOUT total  +6.31%   94 trades   win 49%   PF 1.51   maxDD 4.0%
    JUDGE: PASS   (epoch-2 budget 1/50)

First PASS in the project. It is also not what it looks like, and the per-block
breakdown is what shows it:

    2020-H2  BULL   45 trades   +Rs 89,963
    2023-H1  flat   21 trades   +Rs 12,225
    2025-H1  BEAR   14 trades   -Rs 24,252
    2026-H1  BEAR   13 trades   -Rs 13,366
    ------------------------------------------
    total                       +Rs 63,086
    without the BULL block      -Rs 26,877

Profits in rising and flat markets, loses in BOTH bear blocks. The entire result
is one bull half-year. Epoch 1 would have reported "+6.31% out of sample, profit
factor 1.51, max drawdown 4%" and that would have been a lie of omission --
exactly the confound L19 was written to remove. **The stratified holdout paid for
itself on its first use.**

Statistically it does not survive either:

    per-trade Sharpe 0.132 (n=94)   skew +1.58   kurt 6.01
    PSR vs zero = 0.9204  -- below the 0.95 threshold BEFORE any deflation

Deflating for ~1,000 trials can only lower it further.

## L28 — The judge criteria are regime-blind and significance-blind (open, my error)
`judge._verdict` tests: n_trades >= 30, expectancy > 0, max_dd <= 0.25. All three
passed. None of them asks the two questions that actually mattered here:

  1. Is the result consistent ACROSS regimes, or supplied by one block?
  2. Is the Sharpe distinguishable from the best of N trials?

I built a regime-stratified holdout in epoch 2 and then judged it with criteria
written for epoch 1's contiguous holdout. The split got better; the test did not.

Not changing the criteria mid-epoch -- that is the rule, and the PASS stands as
the formal result of the pre-registered test. For epoch 3, pre-register:
  - per-block expectancy positive in >= 3 of 4 holdout blocks
  - PSR vs zero > 0.95, and DSR > 0.95 against the full trial count
  - record each candidate's trade-level Sharpe at search time, so V[SR] across
    trials is available -- it is not currently stored, which is why the DSR
    above could only be illustrated, not computed
## L29 — Epoch 3: same holdout, same budget, stricter test
**The holdout blocks are UNCHANGED.** So this is not a new epoch in the sense
that matters, and it does NOT get a fresh ledger. The budget limits total
consultations against a given holdout; resetting it while reusing the same data
would defeat its entire purpose. Epoch 3 continues from **2/50**.

What changed is the test, per L28, pre-registered before this search:

    MIN_POSITIVE_BLOCKS = 3   of 4 holdout blocks, by P&L
    MIN_PSR = 0.95            significance before multiple-testing correction
    MIN_DSR = 0.95            significance after deflating by the trial count

All three TIGHTEN. That direction matters: tightening a test that let something
through is defensible; loosening one that rejected a candidate is how this
discipline dies. `judge._verdict` now also returns WHICH criteria failed, so a
FAIL is diagnostic rather than a bare verdict.

**Validated against the case that motivated it.** Re-running epoch 2's PASS
through the new criteria (via `_verdict` directly, spending no budget):

    old verdict: PASS
    new verdict: FAIL
      - only 2/4 blocks positive
      - PSR<=0.95
      - no trial Sharpes supplied (cannot deflate)

The tightened test catches exactly what the old one missed, and for the reasons
the per-block breakdown identified.

Also: `generator` now records `trade_sharpe` for every evaluated candidate, so
V[SR] across trials is finally available and the DSR can be computed rather than
illustrated.

**Two fixture bugs found while testing the new criteria**, both mine, both the
same shape -- a test that depended on luck rather than on the thing being tested:
  - a random "marginal" series happened to draw a high Sharpe and passed
  - the deflation case used trial Sharpes with std 0.05, giving E[max SR] ~ 0.16,
    which a genuine SR of 1.84 rightly survived. That was the code working.
Both replaced with deterministic fixtures whose properties are asserted.
## L30 — PBO: a test of the SEARCH, not of any candidate (built, epoch 4)
Everything else here judges a candidate. `cpcv.py` judges the procedure: across
all combinations of train/test blocks, how often does the best-in-train spec
land BELOW median out-of-sample?

    omega  = rank/(N+1)   lambda = ln(omega/(1-omega))
    PBO    = fraction of combinations with lambda < 0

PBO > 0.5 means the selection process is fitting noise, and NO result from it is
trustworthy however good that result's own numbers look. This is the missing
diagnostic: DSR asks "is this candidate's Sharpe beyond the best of N draws";
PBO asks "does my ranking procedure generalise at all". A search can produce a
candidate with an acceptable DSR while still having PBO ~ 0.7, and that
combination means the one good-looking spec was luck.

Cheap because per-block P&L is computed once per spec; only the block accounting
is combinatorial. Trades spanning a block boundary are PURGED, not attributed --
a position opened in train and closed in test leaks across the split.

Requested epoch 4 after I argued against running epochs mechanically. The
concern was noted and overruled, which is the operator's call. It is worth doing
IF the epoch differs in method rather than only in seed -- so epoch 4 adds PBO
over the candidate set, and validates `psearch.py` against a serial run before
letting it produce anything that spends budget.

**The cost is real and should be stated:** every additional search raises the
trial count N, and E[max SR] with it (3.26 at N=1000, 3.86 at N=10000). More
searching makes the DSR bar higher, not lower. That is correct behaviour, not a
bug, and it is why "run more epochs" is not a strategy for finding an edge.
## L31 — N for deflation is cumulative across searches, not per search (settled)
`dsr.deflated_sharpe` was being handed one search's trial Sharpes. That resets
the multiple-testing correction every time a new search runs against the same
holdout -- structurally identical to resetting the consultation budget per epoch,
and with the same effect: unlimited hidden trials, reported honestly.

Epochs 1-4 test roughly 1,800 specs against these four blocks. The correction
must see all of them. `dsr.record_trials()` now appends to
`data/trial_sharpes.json`, git-tracked for the same reason the ledger is.

**The consequence is the point, not a side effect:** each additional search
raises E[max SR] and makes every candidate harder to clear. Epoch 3's near miss
(SR 0.248 against a bar of 0.3215, all four blocks positive) gets FURTHER from
passing as epoch 4 runs. Searching more cannot rescue a candidate; only fresh
out-of-sample data can, which means forward paper trading and the calendar.

## L32 — A throwaway test run destroyed a 400-spec run's trial data (settled)
An 8-spec parallel smoke test overwrote `data/candidates.jsonl`, deleting epoch
3's 193 trial Sharpes. `generator.py` rewrites that file every run -- acceptable
for a convenience snapshot, wrong for the only copy of anything feeding the
multiple-testing correction.

Recoverable only because the search is seeded and deterministic: re-running seed
31 reproduces the identical candidate set. That property was designed in for
reproducibility and paid for itself as disaster recovery.

Fixed: every run now also writes `candidates_seed{N}.jsonl`, which nothing
clobbers, and appends to the cumulative trial pool as it goes rather than
depending on a later read of a mutable file.

**General form:** if losing a file would cost hours of compute or corrupt a
statistical correction, no routine operation may overwrite it. "The next run
regenerates it" is only true when the run is deterministic AND cheap.

## L33 — The parallel search path is verified, not assumed (settled)
`psearch.py` is 3.8x faster and feeds a judge whose verdicts spend budget. A
silent divergence -- one dropped symbol partition, a mis-merged spec index --
would produce plausible results that are simply wrong.

`xcheck.py` compares it against the serial path on a shared seed: identical
candidate sets, then signal-for-signal set comparison per spec. Result: exact
agreement across 6 specs including ones with 7,073 and 15,586 signals.

Also fixed during integration: `backtest.run` regenerated every signal serially,
discarding the speedup the parallel path had just paid for. It now accepts
precomputed signals.

## L34 — Positions sharing an exit day silently lose their risk release (open)
`portfolio_path` tracked open positions in a dict keyed on exit day:

    open_by_day[t.exit_day] = (risk_frac, t.symbol)

Two positions open at once with the same exit day collide on that key. The
second write destroys the first, so only one is ever popped: the lost position's
risk is never returned to `open_risk`, and its symbol never leaves `held`. The
heat budget leaks monotonically for the rest of the run, and that symbol is
untradeable from then on.

This is not an edge case. A spec with `hold.max_bars = N` that enters several
positions on one day time-exits them all on the same bar.

**Evidence.** Identical trades; the only difference is whether two ALREADY
CLOSED positions shared an exit day:

    distinct exit days: 5/6 later trades admitted
    SHARED exit day   : 4/6 later trades admitted

**Why the selftest missed it.** The fixture already builds 20 positions that all
exit on `days[20]` -- exactly the colliding shape -- but asserts only
`len(adm) < len(big)`, i.e. that the heat ceiling binds on ENTRY. Nothing enters
after that date, so the book is never required to empty and the leak cannot be
observed.

**Blast radius.** Everything downstream of `admitted`: `portfolio_expectancy`
(the train ranking), `validate.fold_stats` (`n_taken`, `exp`, `dd`, `capacity`
-- the promotion gates), and `report.holdout_run` -> `stats(taken, ...)`, which
is what the judge sees. All seven recorded verdicts rest on it. NOT affected:
`trade_sharpe` and the trial pool, PBO block P&L, and every unconstrained
statistic -- those are computed over the full trade list.

Direction matters and is not uniform. The promotion GATES are conservative:
leaked heat lowers `n_taken` into the 30-trade floor and inflates
`capacity_ratio` into the 3.0x cap, so the bug rejects candidates rather than
admitting them. The expectancy and return FIGURES move in no fixed direction,
because which trades are admitted changes, not only how many.

**Left unfixed deliberately.** The patch is two lines -- `setdefault(...)
.append(...)` and a loop over the popped list -- plus a regression test. But
applying it changes `portfolio_expectancy` for every spec and makes epoch 3 and
epoch 4 incomparable, the same standard STATE.md sets for MIN_RR. Operator
decision, 2026-08-15: document now, fix as a separate deliberate step. Epoch 4
ran WITHOUT the fix, matching the recovered epoch 3 baseline.

## L35 — An unbounded memo is a machine limit, not an optimisation (settled)
`Ctx._m` memoised indicators per symbol on `(indicator, period)` with no cap,
because "indicator cost is paid once for the whole search" is the right trade
when memory is free. It is not free. The generator samples periods continuously,
so nearly every spec introduces new keys, and one key costs

    48.3 KB per array x 2,486 symbols = 117 MB

A 30-spec `validate` run reached 3.9 GB and roughly four specs in 60 minutes on
a 6.9 GB machine, paging throughout, and could not have finished. The 400-spec
search needs an order of magnitude more. This never surfaced on the machine the
code was written on.

Fixed with a 16-entry LRU. Eviction is numerically invisible -- an evicted array
recomputes to the same values -- and the cap only has to exceed ONE spec's
working set, because `signals_for` walks every bar of a symbol for a single spec
before moving on. That working set is at most 8 keys measured across the 400
specs of seed 31, and bounded near 9 by construction (at most 6 conditions plus
entry, stop and target indicators), so 16 never evicts inside a spec -- it drops
the previous spec's indicators instead. Memory is bounded at ~1.8 GB.

Verified numerically, not assumed -- and the first attempt was misleading.
Re-running seed 31 serially under the LRU did NOT reproduce the recovered set:
5 of 12 specs differed. Every differing field was an output of `portfolio_path`,
while every indicator-derived statistic matched exactly. The recovered set came
from the PARALLEL path and the check was serial, so two variables moved at once
and the newly added one looked guilty. That is L37, not this.

Isolated properly, with one variable:
  - all 9 indicators x 8 periods recomputed under `MEMO=1`, so every single call
    evicts: 151,200 list elements compared, zero mismatches
  - a 40-spec search over identical data, `MEMO=16` vs unbounded: 54 fields
    compared across every evaluated spec, zero differences

The memoised functions are pure functions of the series, so an evicted array
recomputes to the same values by construction. The tests confirm the code
matches that reasoning; they are not the reason to believe it.

**General form:** a cache with no eviction policy is a memory leak with good
manners. It is invisible on the machine it was written on and fatal on a smaller
one, and "compute it once" stops being an optimisation the moment the working
set stops fitting in RAM.

## L36 — A refetched corpus is not the same corpus (settled)
Rule 6 says bhavcopy history is refetchable, so a fresh machine does not need
`data/raw` transferred. That is true of bhavcopy and false of the corpus.

`backfill.py` fetches ONE file per day, `bhavcopy_delivery.csv`. The universe
also depends on `equity_master.csv`, which `non_equity_symbols()` uses to build
the non-equity denylist -- ETFs, liquid funds, index trackers, all listed in the
EQ series and none of them companies. It looks for the newest snapshot holding
BOTH that master and a bhavcopy. A backfilled machine has no master in any
snapshot, so the loop falls through and the denylist is the EMPTY SET.

Nothing fails. The corpus loads, reports a plausible symbol count, and is wrong:

    2,740 symbols   refetched, no master  <- 254 ETFs and funds
    2,486 symbols   the real universe

This is the ETF contamination 373a3b6 already fixed once, reintroduced through
the data layer rather than the code.

**How it was caught, which is the uncomfortable part.** Not by a test -- by the
search header printing `corpus 2740 symbols` where every committed log said
2486. Had the generator not printed that line, a full epoch would have run on a
contaminated universe and produced entirely plausible candidates. The first
suspect was the LRU of L35, which had just been introduced: two variables moved
at once, and the innocent one looked guilty.

**Fixed.** `universe.master_snapshot()` exposes the newest snapshot holding both
files, and `features.load_corpus()` refuses to build a corpus when there is
none, naming the missing file and the command that fetches it. Single-day
`load()` keeps the permissive behaviour -- fixtures legitimately have no master,
and the selftest asserts that path.

**Rule 6 should read:** bhavcopy history is refetchable, and surveillance state
is not; a rebuilt machine ALSO needs `equity_master.csv` in the newest snapshot
before any corpus it builds is the same corpus.

**General form:** an empty denylist and a denylist with nothing to exclude are
the same value and opposite meanings. Every filter built from optional data
needs to distinguish "nothing matched" from "nothing was loaded", or it degrades
to permissive exactly when its input is missing -- and permissive failures do
not announce themselves.

## L37 — xcheck proves the signals agree, not that the ranking does (open)
`xcheck.py` compares psearch against the serial path as SIGNAL SETS per spec,
`{(symbol, bar_index)}`. It printed AGREE across 6 specs, and the signals really
do agree. The RANKING does not.

Re-running seed 31 serially and comparing against the committed recovered set,
which came from the parallel path:

    spec               n_taken        portfolio_expectancy
    93bb7f49939203a6    93 -> 98      +1,668 -> -3
    a27b6716e2cce8fe   119 -> 141        +12 -> +74
    61d01392e9e6cee0    81 -> 91      +1,265 -> +1,857

`n_trades`, `avg_r`, `win_rate`, `expectancy_after_costs` and `trade_sharpe` are
identical in every case. Only `portfolio_path` outputs move. The LRU of L35 was
in the serial run and was ruled out separately: forced eviction reproduces every
indicator bit for bit.

**Cause.** `portfolio_path` sorts on `(entry_day, -rank_score)` with Python's
stable sort, so trades tied on both keys keep INPUT order -- and when
`rank.by == "none"`, every same-day trade ties. Serial input order comes from
iterating `corpus.items()`; the parallel path merges per-symbol partitions and
produces a different order over the same set. Different order means different
trades admitted once heat binds, and a different expectancy. The L34 exit-day
collision amplifies it, since which position owns a colliding key is also an
artifact of order.

**Consequence.** `portfolio_expectancy` is the ranking metric and the promotion
input, so the shortlist is path-dependent. Epoch 3's two runs disagree about
their own top ten: `de68c9273654b3db`, which SPENT A BUDGET UNIT, is in the
serial ranking and absent from the parallel one. The committed baseline is the
parallel run, so epoch 4 must use `--parallel` to be comparable to it.

Not affected: the trial pool. `trade_sharpe` is computed over all trades, so the
DSR side of the ledger is path-independent, which is why the recovery verified
exactly (`E[max SR]` 0.3215 at N=193) even though the rankings did not.

**Open.** A deterministic total order -- appending `symbol` as a final tiebreak
in the sort -- removes the ambiguity for one line of code. But it changes every
existing `portfolio_expectancy`, carrying the same comparability cost as L34.
Decide it together with L34, not separately: they touch the same function and
would otherwise force two rounds of "every prior result is now incomparable".
## L38 — L37's real cause was a non-total sort key, not the heat leak (settled)
Fixed L34 first (positions sharing an exit day now each release their risk;
regression test verified by re-introducing the collision in a copy and watching
it fire). Re-ran xcheck expecting the ranking divergence to disappear. It did
not -- `n_taken` still differed by ONE trade.

One trade is a tie-break, not a logic error. `portfolio_path` sorted on
`(entry_day, -rank_score)`. Both tie constantly -- `rank: "none"` scores every
trade 0.0 -- and Python's stable sort then falls back on INPUT order, which
differs between the serial and parallel paths and between any two symbol
iteration orders. The ordering was never total, so the admitted subset was never
reproducible, and neither was any gate reading it.

Fixed by adding `t.symbol` as a final tie-breaker. `xcheck` now agrees on
signals AND ranking. The L34 leak amplified the symptom; it was not the cause.

**Two lessons, and the second is the uncomfortable one.**
- A comparison that passes tells you only what it compared. `xcheck` compared
  signal SETS and printed AGREE while the rankings disagreed. It was not wrong,
  it was narrow -- and its confident output made it look sufficient.
- Fixing the first bug you find and assuming it explains the symptom is how the
  second one survives. The heat leak was real, was mine, and was NOT the answer.

## L39 — Prior results invalidated as measurements; budget stays spent
L34 and L38 both change `portfolio_path`, which produces `n_taken`, `exp`, `dd`
and `capacity` -- the promotion gates -- and `report.stats(taken, ...)`, which is
exactly what the judge reads. Every number in epochs 2-4 was computed on that
code. They are withdrawn as measurements and epochs 3-4 are being re-run.

**The 7 consultations stay spent.** The hypotheses were tested against the
holdout; that information leaked whether or not the arithmetic behind it was
right. Refunding budget for a computation bug would let any future error buy
back trials, which is precisely the accounting the budget exists to prevent.

The trial pool IS reset and rebuilt: re-running seeds 31 and 41 tests the SAME
hypotheses, so appending would double-count 395 specs and inflate E[max SR]
against work never done. Backed up first -- L32 was learned the hard way.
## L40 — The heat leak was suppressing the gates by ~6x (settled, quantified)
Same seed, same signals, only `portfolio_path` fixed (L34 + L38):

    median n_taken       ~50-90  ->  296
    median capacity      40-100x ->  5.8x

L34 predicted the direction -- leaked heat blocks later entries, so `n_taken`
falls and `capacity_ratio` (instances/taken) inflates -- and said the gates were
therefore CONSERVATIVE, rejecting candidates rather than admitting them. The
magnitude was not predicted: roughly 6x on both.

Every promotion decision in epochs 2-4 was made against gates reading those
numbers. `MIN_TRADES_PER_FOLD = 30` on a suppressed `n_taken` and
`MAX_CAPACITY_RATIO = 3.0` on an inflated ratio rejected specs that should have
been evaluated. The recorded verdicts are not merely imprecise; a different set
of candidates reached the judge at all.

**Consequence for reading any earlier result:** "only N/4 folds had >=30 taken
trades" and "capacity ratio 65x > 3.0x" were the two most common rejection
reasons across every epoch. Both were the bug talking. The FAILs that cited
expectancy, block consistency, PSR or DSR stand on firmer ground -- those are
computed over the full trade list or the admitted subset's returns -- but any
rejection whose stated reason was trade count or capacity should be treated as
uninformative rather than as evidence against that spec.
## L41 — PBO = 0.929. The search does not generalise. Budget NOT spent. (decisive)
First PBO run, on epoch 4's fixed candidates:

    specs 30   blocks 10   paths 252 (= C(10,5))
    PBO = 0.929    median lambda = -1.056
    -> SEARCH IS OVERFITTING

Across 252 train/test block combinations, the best-in-sample spec lands BELOW
median out-of-sample 92.9% of the time. Not 50% (coin flip on noise) -- 93%.
Train performance is not merely uninformative about test performance here, it
ANTI-predicts it. Median lambda -1.056 says the typical train-winner sits near
the 26th percentile out of sample.

Seven specs were promoted by walk-forward in the same run. **No consultation was
spent on any of them.** A candidate selected by a procedure with PBO 0.93 is not
evidence, whatever its own numbers say, and testing seven of them would burn
7/43 of the remaining budget to learn nothing while raising the trial count for
everything after.

This is the stop condition, pre-registered in L30 before the number existed, and
restated in STATE.md before this run. Honouring it when it fires and is
inconvenient is the entire point; a rule obeyed only when cheap is not a rule.

**Caveat, stated because it cuts against the finding.** PBO is computed on the
top-30 SHORTLIST, which was already selected by train expectancy, not on all 202
candidates. Lopez de Prado's construction assumes the full trial set. On a
pre-selected set the reading is "among the 30 best train performers, does the
best of any subset generalise" -- a narrower question. If the 30 were equivalent
noise, PBO would sit near 0.5; 0.93 means something systematic, not that the
statistic is inapplicable. Computing it over all 202 needs per-block P&L for
every candidate, which the search does not currently retain. That is the fix, and
until it exists this number is directionally sound and not precisely calibrated.

**What this does NOT say.** It does not say no edge exists in NSE equities. It
says THIS procedure -- rank ~200 specs by train portfolio expectancy, shortlist
30, walk forward -- selects specs whose train performance does not survive. More
epochs of the same procedure cannot fix that; they are the thing measured.
## L42 — Idempotence belongs in the store, not in the caller's memory (settled)
The trial pool was a flat list that `record_trials` appended to. Re-running a
seed therefore double-counted the same hypotheses -- inflating E[max SR] against
work never done -- and the only defence was remembering to reset it first. I hit
that hazard twice in one session (L32's lost archive, then the L34/L38 re-runs),
and remembering worked once.

Now keyed by seed: `{seed: [sharpes]}`, so re-running a seed REPLACES its entry.
Re-runs are routine here -- a bug fix, a lost file, a machine rebuild -- and a
correctness property that depends on operator recall is not a property.

The old flat format migrates on first write, so the 395 already recorded survive.

## L43 — PBO on a shortlist answers a narrower question than intended
L41 flagged this against its own finding: PBO 0.929 was computed on the top-30
by train expectancy, while Lopez de Prado's construction assumes the FULL trial
set. On a pre-selected set it asks "among the 30 best train performers, does the
best of any subset generalise" -- suggestive, not calibrated.

`generator.screen` now records per-block P&L for every evaluated candidate, so
PBO runs over all ~200. Both seeds are re-running to capture it.

The prediction, recorded BEFORE the number exists: PBO over the full set should
come in LOWER than 0.929, because the shortlist is the most train-overfit slice
of the candidate pool and excludes the mediocre specs that dilute it. If it comes
in near or above 0.93 anyway, the procedure is worse than the shortlist reading
suggested, not better. Writing the prediction down first is the only way that
number can surprise me.
## L44 — 3R is NOT unreachable. L1/L8/L20 blamed the wrong thing. (correction)
Measured MFE against the R:R floor across hold horizons and stop widths, on the
Stage-2 setup over train blocks:

    hold  atr   n   >=1.5R    >=2R    >=3R
      30  1.5  45    48.9%   37.8%   28.9%
      30  2.5  45    31.1%   24.4%    6.7%
      45  1.5  45    57.8%   51.1%   46.7%
      45  2.5  45    48.9%   44.4%   11.1%
      60  1.5  45    66.7%   60.0%   55.6%
      60  2.5  45    55.6%   53.3%   17.8%

**3R is hit 28.9% of the time at 30 bars with a 1.5-ATR stop, and 55.6% at 60
bars.** L1 concluded it was unreachable (0 of 64 trades) and L8 concluded it
needed a 60-bar horizon. Both measured the `swing_low(10) - 0.5*ATR` stop, whose
median width was 13.6% of price. With a stop that wide, 3R demands a ~40-84%
move; with a 1.5-ATR stop it demands a fraction of that.

**The binding constraint was the STOP RULE, never the R:R floor.** The vocabulary
has contained ATR stops the whole time, and the generator samples them ~50% of
the time, so the search space was never actually blocked -- only the swing_low
half of it was.

I told the operator three times that MIN_RR was the highest-leverage lever and
recommended a decision on it. That recommendation was wrong. It rested on L1 and
L8, both of which held the stop rule fixed while varying the thing they blamed.
L2 came closest -- it varied stops and saw MFE rise -- but concluded expectancy
worsened, on the pre-L34/L38 `portfolio_path`, so its verdict is void too.

**Caveat, so this correction is not oversold:** n=45 per row, one setup family,
train blocks only. It is enough to retire "3R is unreachable" and not enough to
claim a working configuration.

**The general failure:** three lessons agreed with each other and were all wrong
in the same direction, because each inherited the previous one's fixed variable
instead of re-testing it. Agreement between findings that share an assumption is
not corroboration -- it is the same measurement repeated.
## L45 — The selector was broken, not the candidates. Rank on the WORST block. (major)
PBO by ranking metric, seed 31, all 193 evaluated candidates, 252 paths:

    metric          PBO   med lambda   verdict
    sum           0.754       -1.169   OVERFITTING
    consistency   0.679       -1.412   OVERFITTING
    median        0.440       +0.697   generalises
    n_positive    0.266       +1.514   generalises
    min           0.159       +1.412   generalises

Ranking candidates by SUM of block P&L overfits badly. Ranking by the WORST
block (`min`) gives PBO 0.159 -- the train winner lands ABOVE median
out-of-sample 84% of the time. `n_positive` (how many blocks were profitable) is
nearly as good at 0.266.

**Same candidates. Same data. Only the choice of what "best" means.** L41
concluded "this procedure selects specs whose train performance does not
survive" and implied the candidate pool was noise. That was wrong: the pool
contains specs that generalise, and summing P&L across blocks systematically
failed to find them.

**Why summing fails, in hindsight.** A sum is dominated by its largest term, so
it rewards the spec with one enormous block over the spec that was positive in
all of them. That is precisely the concentration failure I kept catching by hand
-- epoch 2's PASS (+6.31%, all of it from one BULL block), epoch 3's near miss --
and the ranking was actively selecting for it while the judge was rejecting it.
The stratified holdout of epoch 2 was built to demand regime robustness; the
ranking metric was quietly optimising the opposite.

**What changes.** `min` and `n_positive` both select for robustness directly, and
both are strictly more conservative than `sum`. Adopting one changes which specs
promote, so every promotion decision to date is superseded -- not because the
numbers were wrong (L34/L38 already handled that) but because a different, better
question is now being asked of them.

**Caveat:** measured on one seed's 193 candidates. Seed 41 is still running and
will either replicate this or not. `min` is also a severe selector -- it may
prefer mediocre-but-consistent specs over good ones, which is acceptable here
(the judge still tests profitability) but is a real trade, not a free win.
## L46 — Epoch 5 pre-registration: rank by MIN block P&L
Chosen BEFORE the search that will be judged by it. Evidence, both seeds:

    metric        seed31   seed41
    min            0.159    0.254   <- chosen
    median         0.440    0.397
    n_positive     0.266    0.556   <- did not replicate; rejected
    sum            0.754    0.798   <- the incumbent, overfits

`min` scores best on both independent candidate sets and is the only metric
whose ranking question matches the judge's: the judge demands >=3 of 4 holdout
blocks positive, and `min` ranks on how the WORST block did. Ranking and judging
finally ask the same thing. Under `sum` they were opposed -- the ranking rewarded
one huge block while the judge rejected exactly that shape.

`n_positive` is rejected despite a good seed-31 score: 0.266 -> 0.556 across
seeds is not a finding, and adopting it would have been picking the best number
from one sample.

**Known cost, stated up front.** `min` is severe. It prefers a spec that is flat
everywhere to one that is strongly positive in three blocks and slightly negative
in the fourth. That is a real trade, not a free win. It is acceptable here only
because the judge still independently tests expectancy, PSR and DSR -- the
ranking's job is to surface candidates likely to pass, not to decide.

**Supersedes:** every promotion decision to date was made under `sum`. Those
seven epoch-4 promotions are withdrawn unconsulted; the budget stays at 7/50
because none of them was ever tested against the holdout.

## L47 — Parameter tuning on this book ANTI-predicts (decisive)
Walk-forward: choose each parameter on the first half of history, then rank all
values on the second half.

    param        chose (IS)   rank out-of-sample   OOS winner
    target_pct      15%           3 of 3              20%
    stop_pct        15%           3 of 3              10%
    hold            25d           2 of 3              15d

The in-sample winner ranked LAST twice and second-worst once. Tuning against
in-sample results does not merely fail to help here -- it reliably selects the
worse setting.

That invalidates the eleven-variant table run an hour earlier as a basis for
choosing anything. `target 15%` at +12.39% and `target 25%` at +13.04% are the
top two of eleven trials on one dataset; the walk-forward says that ranking is
noise with the sign flipped.

**Why, visible in the same numbers:** every variant lost ~25% CAGR in 2020-2023
and made ~+30-39% in 2023-2026. A 64-point regime swing dwarfs every parameter
difference, so the "best" in-sample value is whichever lost least in a bad
regime -- a different property from making most in a good one.

**Encoded, not just noted.** `simulate.wf_guard()` refuses a parameter change
unless the in-sample winner also wins out-of-sample, and every test is stored to
`data/walkforward.jsonl` (`/wf` on Telegram). The tuning loop can now refuse
itself, which is the only defence against a process that is confidently wrong.

Live book stays at 10% / 20% / 15 days -- the operator's specification, not a
tuned value. It also happens to be the out-of-sample winner on all three axes,
which is noted and NOT acted upon: one out-of-sample period is one observation.

## L48 — Selection-conditioned measurement: the learning loop rediscovered its own rule
`deliv` showed a consistent NEGATIVE spread (-0.97 -> -0.70 across halves) and
passed every test available: large effect, stable sign, split-checked. Inverting
it took the book from +6.37% CAGR / 48% DD to **-19.92% / 89% DD**.

The measurement was conditioned on selection. Those 2,758 trades were chosen
partly BY delivery, so the spread says "among stocks already picked for high
delivery, the even-higher ones did worse" -- a fact about the selected sample,
not about the universe. Change the population and it inverts.

Consistency across halves cannot detect this, because both halves share the same
selection rule. A split-check confirms a sign is STABLE; it cannot confirm a
relationship is CAUSAL.

**Fixed:** `learning.INVERTED` is empty and documented. `propose()` no longer
auto-inverts; it flags a consistent negative spread as selection-conditioned and
requires an unconditioned test first. Without that, the loop keeps rediscovering
its own selection criteria and acting on them backwards.

**The general trap:** never measure a feature's information on trades that
feature helped select. To test `deliv` honestly, generate trades chosen WITHOUT
it, then measure. Everything else in the ledger has the same defect to a lesser
degree -- `rs` and `liq` are also selection inputs, which is a further reason
their sign flips between halves.

**What was gained anyway:** reverting to neutral weights (all 1.0) gives
+12.66% CAGR / 38.9% DD, better than the +6.37% / 48.2% the tuned weights
produced. Four hours of weight learning ended up worse than not learning at all,
and the honest response is to say so and keep the neutral weights.

## L49 — The 5% stop is not a parameter choice, it is a bet on entry precision (decisive)

Requested: cut the hold to 6-8 sessions and the stop to 5%. Fourteen
pre-registered variants, `exit_test.py`, compared per POSITION (a scaled exit
books two rows; counting them as two trades would corrupt the comparison).

**Factorial, so each change is attributable:**

| variant | CAGR | maxDD | win | per-trade vs baseline | worst block |
|---|---|---|---|---|---|
| baseline 10 / 20 / 15d | +13.54% | 28.8% | 49% | — | -83.6% |
| stop 5% only | +0.04% | 26.4% | 27% | -2.79% (t -2.10) | -55.5% |
| hold 7d only | +8.40% | 25.2% | 51% | -1.35% (t -1.00) | -41.2% |
| both (the goal) | -0.20% | 25.9% | 32% | -2.88% (t -2.24) | -50.5% |

**The stop is the whole effect; the hold is nearly free.** Cutting the hold
costs 5 CAGR points that sit inside the noise and IMPROVES both drawdown and
the worst half-year block, which is the ranking that has generalised here
(L45/L46). Cutting the stop halves the win rate.

**The mechanism is visible in the exit mix, which is not a noisy statistic.**
Stops go from 85 of 217 exits to 181 of 294 -- 62% of positions stopped out.
A 5% stop sits inside these microcaps' daily range, so it is hit by noise
before the thesis can resolve. This is the same finding as the 3% test
(70-77% stopped), one notch wider.

**A volatility-scaled stop says what distance the book can actually carry:**

| stop | median distance | CAGR | worst block |
|---|---|---|---|
| 1.5 x ATR | 6.4% | +2.89% | -44.0% |
| 2.0 x ATR | 8.5% | +5.15% | -64.8% |
| 2.5 x ATR | 10.7% | +7.28% | -62.2% |
| 3.0 x ATR | 12.8% | +7.33% | -67.0% |

At 2.5x ATR the median stop is 10.7% -- the current fixed 10% is already the
right distance, arrived at by a different route. Every ATR variant is inside
the noise against the baseline (t -1.15 to -1.83), so ATR is not an
improvement; it is a MEASUREMENT of what these names need, and the answer is
roughly 10%.

**So `portfolio.py`'s standing note is now evidence, not a plan:** "a tight
stop needs a precise entry to survive". 5% is unreachable at current entry
quality. It becomes reachable only by making entries that need less room --
better timing, not a smaller number.

## L50 — Multiple targets and a moved stop lose money the same way a trail does

Requested alongside L49: book part of the position at a first target, then move
the stop up under the rest. Implemented in `simulate.run(scale=...)` with the
partial charged its own brokerage, STT and DP -- a scale-out of a Rs 45,000
position is two orders, not one.

| variant | CAGR | win | target hits | worst block |
|---|---|---|---|---|
| baseline | +13.54% | 49% | 70 | -83.6% |
| + T1 10%, half out, stop to breakeven | +5.84% | 55% | 58 | -111.1% |
| + T1 10%, half out, stop to +5% | +5.37% | 56% | 47 | -97.3% |

**Win rate rises and the book gets worse** -- exactly the pattern `portfolio.py`
recorded for trailing stops, reached by a different mechanism. Moving the stop
up converts losers into smaller losers (49% -> 55-56% win) while cutting full
target hits from 70 to 47-58. The winners pay for everything else here, and
both rules sell half of every winner at +10% on the way to +20%.

**The tail gets worse, not better,** which is the opposite of the intuition
that motivates a breakeven stop: -111.1% worst block against -83.6%. A stop at
breakeven is still a stop, and it fires during the pullback that precedes the
move -- at which point the position is closed and the recovery happens without
it.

Per-trade the difference is inside the noise (t -1.14, -1.31). The CAGR gap and
the worst-block gap point the same way as the mechanism, so this is recorded as
REJECTED rather than unresolved -- but the honest statement is that one path
cannot separate -7.7 CAGR points from luck at this trade count.

At the 5%/7d settings every scale variant is resolved-negative (t -2.90 to
-3.25). Nothing here rescues the tight stop.

## L51 — The three exit rules, decoupled. Dose-response beats the t-statistic. (decisive)

L50 tested "sell half AND move the stop" as one rule, so a loss could not be
attributed. `simulate.run` now takes `targets` (a ladder of partial exits) and
`stop_to` (move the stop once a trigger is touched) as INDEPENDENT arguments,
and each was measured alone.

**Every comparison here is inside its per-trade error bar** (|t| < 1.5). At
~220 trades and a 16% per-trade standard deviation, nothing under ~3 points
per trade is resolvable, and none of these clear it. What DOES carry
information is dose-response: each rule was run at several intensities, and
the cost tracks how often the rule fires.

### Moving the stop to entry — the damage is proportional to how often it acts

| trigger | times the moved stop fired | CAGR | maxDD | worst block |
|---|---|---|---|---|
| never (baseline) | — | +13.54% | 28.8% | -83.6% |
| at +15% | 10 | +13.13% | 29.1% | -83.8% |
| at +10% | 34 | +8.27% | **33.2%** | **-121.5%** |
| at +5% | 110 | +4.41% | 23.7% | -79.0% |

Monotone: 10 -> 34 -> 110 firings gives -0.42 -> -5.28 -> -9.13 CAGR points.
A rule whose cost scales cleanly with its own activity is not a noise artefact;
this project rejects ideas for being NON-monotonic (the ADV participation cap,
the position floor), and the same standard has to be applied when the gradient
points down.

**It makes the book riskier, which is the opposite of the intent.** At the
+10% trigger, maximum drawdown rises 28.8 -> 33.2% and the worst half-year
block nearly halves again, -83.6 -> -121.5%. Win rate FALLS, 49 -> 40%,
because a breakeven exit is not a win after costs.

**The mechanism:** a pullback to entry after a +10% run is ordinary behaviour
in these names, not a warning. Target hits drop 70 -> 58, so twelve positions
that would have paid +20% were closed at zero instead. Removing the winners
removes the thing that repairs the equity curve, and the drawdown gets worse
even though each individual trade was "protected".

### Multiple targets alone — a clean intervention, and it also costs

The ladder does not change WHEN a position exits, only how much is left: the
stop/target/time mix is identical to baseline (85 / 70 / 62) in every pure
ladder variant. That makes this the cleanest comparison in the file.

| ladder | partial orders | CAGR | maxDD | worst block | win |
|---|---|---|---|---|---|
| none (baseline) | 0 | +13.54% | 28.8% | -83.6% | 49% |
| half at +15% | 83 | +9.92% | 28.7% | -93.4% | 51% |
| half at +10% | 108 | +9.39% | 29.1% | -75.8% | 51% |
| third at +7 and +14 | 221 | +8.15% | **24.6%** | **-57.8%** | 53% |
| quarter at +5/+10/+15 | 340 | +6.57% | 25.4% | -64.2% | 54% |

Monotone again: 83 -> 340 partial orders gives -3.62 -> -6.98 CAGR points.
Every rung sells part of a winner below the target and pays its own brokerage,
STT and DP charge on the way out.

**This is the only one of the three that behaves like the operator expected.**
Two rungs cut the worst block -83.6 -> -57.8% and drawdown 28.8 -> 24.6%, for
5.4 CAGR points. It buys real tail protection with real return. It is still
worse on CAGR-per-drawdown (0.331 vs 0.470), so it is a trade, not a free win.

### Combining them is the worst option tested

"Half at +10% and move the stop": +4.63% CAGR, 32.9% drawdown, -120.9% worst
block. The ladder's tail benefit is destroyed by the stop move, which fires on
the same pullbacks.

## L52 — Shorter holds buy a large tail improvement with return that is not resolvable

Isolated properly this time: the stop stays at 10%, only the clock moves. (The
earlier 6d/8d runs in L49 were at a 5% stop and so measured the stop.)

| hold | CAGR | maxDD | worst block | CAGR/DD | per-trade vs base |
|---|---|---|---|---|---|
| 5d | +7.45% | **19.5%** | **-21.2%** | 0.382 | -1.58% (t -1.17) |
| 6d | +5.86% | 24.2% | -38.2% | 0.242 | -1.83% (t -1.39) |
| 7d | +8.40% | 25.2% | -41.2% | 0.333 | -1.35% (t -1.00) |
| 8d | +9.47% | 25.8% | -51.7% | 0.367 | -1.16% (t -0.85) |
| 10d | +14.18% | 25.8% | -49.4% | **0.550** | -0.11% (t -0.07) |
| 12d | +15.10% | 28.6% | -70.8% | 0.528 | +0.09% (t +0.06) |
| 15d (current) | +13.54% | 28.8% | -83.6% | 0.470 | — |
| 20d | +16.18% | 27.6% | -86.3% | 0.586 | +0.73% (t +0.45) |

**Nothing here is statistically resolvable** -- every t is inside +/-1.5. But
two structural facts are not statistics:

**1. The tail shortens with the clock, hard.** Worst half-year block runs
-21.2% at 5 days to -86.3% at 20. That is monotone across eight settings and
is the metric this project has found generalises (L45/L46).

**2. When winners pay says what a short hold forfeits.** Of the baseline's 70
target hits: 49% land by day 5, 63% by day 7, 70% by day 8, 83% by day 10,
94% by day 12. The MEDIAN target lands on day 6.

So a 8-day book collects 70% of the winners and carries roughly 60% of the
tail risk. A 10-day book collects 83% and has the best CAGR-per-drawdown of
any setting tested. The 6-8 day window the operator asked for is defensible;
10 days is where the risk-adjusted number actually peaks, and the two are one
step apart.

**Note the disagreement between metrics, because it decides the answer.**
CAGR-per-drawdown favours 10-20 days; worst-block favours 5-8. They are
measuring different risks -- one the average path, the other the bad one.

## L53 — Two published cross-sectional effects, neither transfers (and one is a trap)

Motivated by paperswithbacktest.com, whose value to this project is NOT its
dataset (it has no NSE coverage at all) but its premise: test hypotheses
someone else pre-registered, so the hypothesis and the sample stop sharing an
author. Both knobs live in `clusters.py`, default OFF, tested by `lit_test.py`
at the live exit rules (10/20/10d).

| variant | CAGR | maxDD | worst block | symbols | per-trade vs base |
|---|---|---|---|---|---|
| baseline | +14.18% | 25.8% | -49.4% | 143 | — |
| skip 21d | +10.39% | 25.0% | -101.7% | 127 | -0.58% (t -0.43) |
| skip 42d | +11.05% | **16.2%** | -55.2% | 123 | -0.26% (t -0.18) |
| drop top 10% MAX | +14.31% | 25.3% | **-126.7%** | 129 | +0.46% (t +0.33) |
| drop top 20% MAX | +15.23% | 24.4% | **-122.7%** | 118 | +0.93% (t +0.62) |

**1. Skip-month momentum (Jegadeesh & Titman) does not transfer.** Measuring
momentum to t-1 month instead of t costs 3-4 CAGR points and doubles the worst
block at 21 days. The likely reason is specific to this book: the breakout
trigger fires ON recent strength, so removing the recent month from the score
puts the ranking and the timing rule in disagreement. The literature's
construction assumes the score is the whole system; here it is not.

**2. The MAX/lottery screen is the interesting failure.** On the two numbers
most people would look at it WINS -- CAGR +15.23% and +0.93% per trade at the
20% screen, win rate 50 -> 53%, drawdown slightly better. On the metric this
project has repeatedly found generalises, it is a disaster: worst half-year
block -122.7% against -49.4%.

**The mechanism is breadth.** The screen removes 25 of 143 traded symbols. A
book of five names drawn from a shallower pool concentrates, and concentration
is what turns a bad half-year into a very bad one. The screen is removing names
the score wanted -- `rs` and `near_high` both correlate with having had a big
up-day, so "drop the biggest one-day gainers" and "buy strength" fight.

**This is the third time in this project that a positive headline number came
with a worse tail** (pooled ranking, the breakeven stop, now the MAX screen).
Ranking candidates on CAGR would have adopted all three.

Neither knob is adopted. Both stay in the tree, defaulted off, because the
negative result is worth keeping and re-deriving it later would cost another
six backtests.

**Standing conclusion:** published US large-cap cross-sectional effects are
hypotheses here, not findings, and this book's own structure (a trigger, a
5-name bucket, a shallow pool) decides whether they survive. That is the same
lesson as the rs t-statistic in CLAUDE.md: univariate significance elsewhere is
not marginal portfolio value here.

## L54 — More books buy evidence; more parameters buy nothing. The arithmetic decides.

The operator's instinct was right and the reason is worth writing down: with
0 closed forward trades and ~71 a year from one book, the project is starved of
the only evidence a search cannot contaminate. The proposal was to run several
books at different entries, targets and stops "just for learning".

Half of that works and half cannot, and the split is not a matter of taste:

| question | trades needed | 1 book | 4 books |
|---|---|---|---|
| is the per-trade edge > 0? | 105 | 1.5y | **0.4y** |
| stop 10% vs 5% | 238/arm | 3.4y | 3.4y |
| ladder on vs off | 2,856/arm | 40y | 40y |
| hold 10d vs 15d | 162,554/arm | never | never |

**Parallel books speed up the aggregate question and do nothing for
comparisons.** Pooling works because n grows; a comparison needs each ARM to
reach its own n, and adding arms feeds none of them faster. The hold row is the
one to remember: the change adopted in L52 can never be validated forward, so
it rests on the backtest permanently and should be described that way.

**Pooling is only legitimate if the books are independent.** Books running
different parameters on the same universe hold overlapping positions, so their
trades are correlated and pooling overstates the evidence. Rank cohorts do not
have this problem: cohort k takes ranks 3k..3k+2 micro and 2k..2k+1 small, so
the positions are disjoint BY CONSTRUCTION. That is why the parallel books are
cohorts and not variants.

**The one variant that earns its place measures a proportion, not a mean.**
`tight` holds the same names as main with a 5% stop, paired on identical price
paths, and its endpoint is the stop-hit RATE -- 62% predicted at 5% against 37%
at 10%, resolvable in ~62 trades because a proportion's standard error is
sqrt(p(1-p)/n), not 16%/sqrt(n). It cannot say which stop is better and is
barred from promotion on P&L. It can say whether the simulator is lying, which
is worth more: `IMPACT_C` is a guess and the gap-fill model has never met a
real gap.

**What made this dangerous rather than merely useless:** five books with
different parameters, reported side by side, is a leaderboard. A leaderboard
gets picked from. The design constraint that makes it safe is that the pooled
books have nothing to choose between -- they run identical rules.

## L55 — The score cannot express absolute quality, so a score threshold is inverted

Asked why the bucket takes a top-N quota rather than requiring a minimum score.
First, a clarification that is not a quibble: rank and score are not competing
criteria. Rank IS the position in score order, so "top 3 by rank" and "top 3 by
score" select the same names. The bucket is already built on score.

The real proposal -- require a minimum SCORE, so a weak market produces a
smaller book -- cannot work, and the reason is structural.

`clusters.score` averages PERCENTILE ranks computed within the cluster's
qualifying set. A percentile is relative by construction: the best name scores
high whether ten names qualified or four hundred. Sampled at 70 dates across
the full history, split by how many micro names were above their 200-DMA:

| | weakest quartile | strongest quartile |
|---|---|---|
| names qualifying | 146 | 413 |
| rank-1 score | **94.5** | 89.3 |
| rank-3 score | **89.0** | 85.7 |

**Scores are HIGHER when the market is weaker.** Fewer survivors means a more
selected pool, and the top of a selected pool still ranks at the top of it. A
threshold set to bind in a bad market binds harder in a good one. It would
admit more names precisely when it was meant to admit fewer.

**The absolute filtering already exists and lives where it can work:** the
200-DMA gate collapses the pool from 421 names to 56 between regimes, and the
breakout trigger is why occupancy averages 2.83 of 5 rather than 5. The score
orders what qualified; the gate and the trigger decide whether to be in the
market. Do not move that responsibility onto a percentile.

**A minor artifact found while checking:** ties are broken by sort order alone.
On 2026-08-17, SHAHALLOYS and WORTHPERI both scored 76.2 and took micro ranks 3
and 4 -- so one entered the bucket and the other went to the next cohort on
nothing but list position. Harmless between adjacent cohorts, but it means a
rank boundary is not always a real distinction.

## L56 — Four portfolios fit the ranking, and deepening it would change nothing

Running four paper portfolios off one ranked list raised an obvious worry: the
deepest reaches micro rank 12, and on 2026-08-17 only 13 micro names survived
the 200-day-average gate, the surveillance flags and the sizing cap. One name
of headroom.

**Measured rather than watched.** Across 94 sessions sampled from 2020-12-17:

| cluster | min | median | max | sessions short of what four portfolios need |
|---|---|---|---|---|
| micro | 13 | 20 | 20 | 0 of 94 |
| small | 19 | 20 | 20 | 0 of 94 |

It has never starved. The median sits at the `PER_CLUSTER = 20` cap, so the
binding constraint is normally the cap itself, not the filters; 2026-08-17 was
an outlier where seven of the top twenty were dropped.

**The obvious fix is a no-op, which is why it was checked before being made.**
Raising `PER_CLUSTER` from 20 to 30 changed the picks of NONE of the four
portfolios on five sampled dates. Four portfolios reach at most micro rank 12
and small rank 8, both well inside the top twenty, so a deeper list only adds
names nobody reaches. It would help solely in the case where fewer than twelve
survive the filters, which has not happened.

So: no change. The margin is real but has never been consumed, and the remedy
is available and provably safe if it ever is.

**Turned into an alarm instead of a habit.** `agent.attention()` now fires when
any cluster's surviving candidate count falls below what the portfolios need,
naming the fix. It is silent today at 13 against 12, and was verified to fire
by adding a fifth portfolio (15 places needed, 13 available). A monitor that
has never fired is not known to work.

## L57 — Live quote sources: ten checked, two work, and the diagnosis method is the reusable part

A morning spent finding a source for today's opening price. Recording the
RESULTS so they are not re-derived, and the METHOD so the next candidate takes
ten minutes instead of a morning.

### The criterion

Not "does it have an API". The book fills unattended every weekday, so:
**can it authenticate with no person at the keyboard?** Everything else --
price quality, cost, coverage -- is secondary and mostly equivalent.

### Results

| source | unattended | outcome |
|---|---|---|
| **Yahoo chart API** | yes | **WORKS.** 220/220 daily opens matched the bhavcopy exactly |
| **Upstox** | no (daily token) | **WORKS.** Filled GMMPFAUDLR at 1053.00, SAHYADRI at 388.00 |
| ICICI Breeze | no | their FAQ: daily key "required as per SEBI regulations" |
| Zerodha Kite | no | daily login, plus Rs 2,000/month |
| Angel One SmartAPI | yes, via TOTP | needs trading password + TOTP seed on disk |
| 5paisa Xstream | probably | same trade |
| Groww | unknown | docs never say how tokens are issued |
| nseindia.com/api/* | NO | edge-blocked, see method below |
| parse.bot | yes | third-party scraper, free tier 200 calls/month |
| 0xramm/Indian-Stock-Market-API | — | Yahoo underneath, plain HTTP, bare IP, offline when tested |
| yfinance | — | wraps the SAME Yahoo hosts; cannot bypass their rate limit |

**SEBI mandates daily re-authentication for broker APIs.** Regulatory, not
technical. It is why no official broker route runs unattended, and why the
TOTP ones only do so by storing a full trading credential on disk -- which for
a system that merely READS prices is a bad trade at any price.

### The method, which is the part worth keeping

**To tell a header problem from a TLS problem from an IP problem, use three
probes.** For nseindia.com:

    curl (OpenSSL, HTTP/2)        -> 403     two different TLS stacks
    python urllib (HTTP/1.1)      -> 403     both refused
    curl -> nsearchives host      -> 200     same IP, same second

Two unrelated TLS stacks refused while a sibling host serves us from the same
address rules out both headers and IP reputation, leaving TLS/HTTP2
fingerprinting and a JS-computed cookie. Nothing that speaks plain HTTP gets
past that, which is why no library -- yfinance included -- can help.

**To tell a session problem from an IP problem**, probe the website and the API
host separately. Yahoo, while rate-limited:

    finance.yahoo.com             -> 200     site fine, cookie issued
    query1.../v1/test/getcrumb    -> 429     API host limited

The site working while the API host refuses proves the limit is on the API
host for our address. A fresh cookie or a wrapper changes nothing.

### Four Upstox bugs, three of which looked identical

Any of these alone produced "no data" and would have been blamed on the token:

1. **Cloudflare 403 error 1010** -- urllib sends "Python-urllib/3.x" and is
   banned before Upstox sees the request. A User-Agent is mandatory.
2. **Instrument keys are ISIN-based** -- NSE_EQ|INE330T01021, never
   NSE_EQ|HAPPYFORGE.
3. **An empty value reads as a set key** -- "UPSTOX_ACCESS_TOKEN=" with
   nothing after it passed every "is it configured?" check.
4. **Tokens expire daily ~03:30 IST** -- the first one supplied was six days
   stale. Upstox answers 401 "Invalid token", which reads as a paste error.
   `live_source.token_hours_left()` now decodes the JWT `exp` and says so.

### Standing conclusion

Yahoo for unattended fills, Upstox when a fresh token happens to exist, and the
official bhavcopy as the source of truth for everything. The morning path only
changes WHEN a fill is recorded, never at what price -- the open is fixed at
09:15. Do not spend another morning on this without new evidence that Yahoo is
unreliable under NORMAL load, which is 2-5 requests once a day, not the 300-bar
sweep that got us rate-limited.

## L58 — The circuit-lock guard was written, tested, and never called (decisive)

`engine.gate()` rejects `bar.high == bar.low` and has since it was written.
`engine.entry_fill()` returns `None` on the same condition. Both are covered by
asserts in `engine._selftest()`, and both pass.

**Nothing calls either function.** A grep for `gate(` and `entry_fill(` outside
`engine.py` returns only those selftests. `selection.py:69` says so out loud
about the neighbouring heat check — *"engine.MAX_PORTFOLIO_HEAT (6%) is NOT a
constraint on this path -- it is checked only inside engine's own signal
function, which nothing here calls"* — so the fact was written down and never
followed to its conclusion: `circuit_locked`, `asm`, `gsm`, `fo_ban`,
`costs_exceed_risk` and `portfolio_heat` were all unreachable from both
`daily.py` and `research/simulate.py`. `simulate.py:211` fills at
`e = s.open[i + 1]` unconditionally.

This is the exact failure mode CLAUDE.md names as **"a status message is not
evidence"**, in its most expensive form: not a flag that prints "enabled" while
doing nothing, but a guard with a *passing test* while doing nothing. The test
proved the function was correct. Nothing proved the function was reached.

### How often it mattered

357 picks, `selection.build` + `allocate` on the live config, every third
session from 2021:

| | share of picks | std err |
|---|---|---|
| trigger bar locked (the breakout the score saw) | 9.8% | ±1.6% |
| fill bar locked (the next open the book claims to buy at) | 8.7% | ±1.5% |
| locked at the LOWER band | **0.0%** | — |

**Every lock was an UPPER lock — not one lower lock in 357 picks.** That is not
luck, it is structural: the score selects momentum and the breakout trigger
requires a 20-day-high close, so a lower-circuit name can never qualify. The
only band this book ever meets is the one with no sellers at it. So roughly
**one fill in eleven was physically impossible**, and those were the strongest
names in the sample rather than a random ninth of it.

### What it was worth

Same config either way (`stop 10 / target 20 / hold 10 / max_pos 5 / refresh 5 /
breakout / impact_c 1.0`):

| | CAGR | maxDD | n | win | per trade |
|---|---|---|---|---|---|
| filling locked bars (every number this project has quoted) | +14.14% | 25.8% | 232 | 50% | +2.94% ±0.98% |
| locked bars untriggered | **+7.59%** | **31.0%** | 195 | 47% | +2.15% ±1.08% |

**About half the backtested CAGR came from fills that could not have been got.**
Drawdown gets *worse* once they are removed, which is the tell: the phantom
fills were disproportionately winners, so they were flattering the path twice
over.

### The error bars do NOT get a vote here, and that is the point

The per-trade gap is +0.79% against a combined standard error near 1.46%, so
t ≈ 0.54 — comfortably inside the noise band that CLAUDE.md correctly uses to
refuse re-deciding knobs. **That verdict does not apply to this change.** The
noise discipline exists to stop the project preferring rule A over rule B on
one path's arithmetic. This is not a preference between two rules: one of them
books trades that the market could not have given us at any price. The lower
number is not the better-tested number, it is the *true* one, and it would stay
the true one at t = 0.

Read the other way round, this is the more uncomfortable finding: a 6.5-point
CAGR difference that a per-trade test cannot resolve means the per-trade test
could not have found this bug either. Only reading the fill assumption could.

### Why the fixture could never catch it

`simulate._selftest()` built its 30-name synthetic corpus with
`s.open.append(px); s.high.append(px); s.low.append(px)` — **every bar one
price.** The fixture represented thirty stocks that were band-locked every
session for 420 sessions, so adding the guard made it take zero trades and trip
its own `"control took no trades; the fixture is broken"` assert. The message
was accurate; the fixture *was* broken, and had been the whole time. A fixture
that cannot represent a real bar cannot test what happens on one. Re-derived
with a ±0.1% range, and the locked case is now an explicit assertion rather
than an accident of the fixture.

### The fix, and where it had to go

One line, at `selection.build`'s `"triggered"` key:

    "triggered": bool(fn(s, i)) and not locked,

That is the single choke point every trigger in `entry.TRIGGERS` passes through,
and both `daily.py` and `simulate.py` reach it through `selection.build`, so one
edit covers all seven triggers and both paths. **MARKED untriggered, not
filtered** — filtering would let `allocate()` reach further down the list and
buy a worse name, the same trap the note above `picks` already documents. The
bucket holds cash instead.

`high == low` remains a *proxy* for the true 2/5/10/20% price band, the same
ceiling `engine.py:142` already admits. Upgrading it needs the NSE price-band
file. The proxy also catches a single-print illiquid day, which is un-buyable
at a knowable price for a different reason, so it is a tightening either way.

### What it cost live

VCL was queued for 2026-08-20 on a trigger bar that was an upper circuit lock:
`O=H=L=C 1.94`, +4.86%, 129 trades, and **nine of the previous twenty bars
locked at a single price** on an unbroken run from 1.17 (08-04) to 1.94 (08-19)
at +4.3% to +5.0% a day. The order was Rs 44,998 against an ADV of Rs 779,450
— 5.8% of ADV, at a tick worth 0.52% of the price, with 1.17% of modelled
impact per side against a median trade's 0.30%. `deliv 100.0%` was not
conviction, it was the lock.

Voided as `pos` id 6, which is what `status='void'` exists for: an order that
should never have been placed is not a trade, and recording a return for it
would have put the first number into the forward evidence that no decision
produced. Under the guard VCL is no longer triggered and `allocate()` returns
only a name already held, so the correct answer for 2026-08-20 is **no new
pick**.

## L59 — Re-measuring after the guard: the levels moved, the rankings did not

L58 said the prediction to check was "the RANKINGS and the shapes are likely to
survive, the levels are not". Every table in `CLAUDE.md` has now been re-run
against the guard (batch `20260819-postlock`). The prediction held, with one
exception that matters.

### The knobs, re-measured

`research/remeasure.py` runs the live bucket and each variant, and reports the
per-trade edge with its error bar as well as the CAGR gap — because a CAGR gap
on one path is arithmetic, not evidence.

| variant | CAGR | maxDD | n | win | per trade | vs live | t |
|---|---|---|---|---|---|---|---|
| **live: 3/2, 10d, breakout** | **+7.59%** | 31.0% | 195 | 47% | +2.15% +/- 1.08% | — | — |
| hold 15d (the old rule) | +5.32% | 34.8% | 192 | 45% | +1.71% +/- 1.17% | +0.44% +/- 1.59% | +0.28 |
| mix 2 micro / 3 small | +9.93% | 31.4% | 206 | 50% | +2.52% +/- 1.04% | -0.36% +/- 1.50% | -0.24 |
| no trigger | -2.20% | 39.5% | 235 | 48% | +0.17% +/- 1.10% | +1.99% +/- 1.54% | +1.29 |

Not one of them resolves per trade. The 10-day hold is confirmed in DIRECTION
(better CAGR *and* better drawdown than 15), which is all L52 ever claimed. The
2/3 mix still leads, by 2.34 points where it led by 4.47 pre-guard and trailed by
1.19 before that — a knob that gives a different answer every time anything else
moves is a knob inside the noise. **Nothing was re-decided.**

### The exception: the trigger's justification changed

`selection.py` carried this comment for months:

    TRIGGER = "breakout"    # see trigger_test: near-identical CAGR to no trigger
                            # (+11.45 vs +12.53) but worst block -83.1% vs -120.5%.

So the trigger was kept *despite* costing a point of CAGR, on the tail argument
alone. Post-guard `trigger_test` reads:

| trigger | CAGR | maxDD | n | worst block |
|---|---|---|---|---|
| **breakout** | **+7.59%** | 31.0% | 195 | -126.7% |
| pullback | +2.67% | 22.6% | 201 | -99.0% |
| vol+breakout | +2.61% | 16.5% | 141 | -45.8% |
| not_overbought | +0.46% | 28.2% | 308 | -129.1% |
| rsi_band | -1.83% | 34.7% | 360 | -150.7% |
| none (control) | -2.20% | 39.5% | 235 | -163.4% |
| volume | -3.52% | 46.7% | 210 | -129.3% |

Breakout is now the only one of seven to clear the promotion bar, and it wins on
worst block as well. The old and new numbers are not directly comparable — the
hold moved too — but **the sign of the CAGR gap flipped**, so the live setting
no longer rests on the tail. The lesson is not "the trigger got better". It is
that a rule justified by a *secondary* criterion while losing on the primary one
was a rule resting on a measurement error.

### The claim that survives got stronger

Rank depth against per-trade return, regressed over the trades themselves
(1,015 trades, six disjoint cohorts):

| | slope per cohort step | std err | t | top - deepest |
|---|---|---|---|---|
| pre-guard | -0.90% | 0.35% | -2.56 | +6.41% +/- 1.89% |
| **post-guard** | **-1.18%** | **0.29%** | **-4.10** | **+6.63% +/- 1.79%** |

Every one of the five deeper cohorts is now CAGR-negative, and none matches the
top. Removing un-buyable fills made the score's edge easier to see, not harder,
which is the outcome consistent with the edge being real: phantom fills were
noise added to every cohort equally, and noise only ever flattens a slope.

`rank_test.py` now prints this regression itself. It was previously computed
ad hoc, which is why `CLAUDE.md` could carry the pre-guard figure for months
after the engine changed underneath it.

### Friction costs more than the old table showed

| c | CAGR | maxDD | share of frictionless |
|---|---|---|---|
| 0.0 | +11.90% | 29.6% | the old, wrong assumption |
| 0.5 | +8.63% | 30.0% | 72% |
| **1.0** | **+7.59%** | **31.0%** | **64%** |
| 2.0 | +5.12% | 32.0% | 43% |
| 3.0 | +4.17% | 32.6% | 35% |

The old table had c=1.0 giving up essentially nothing against c=0 (+13.57 vs
+13.97). It now gives up 36%, because the fills the guard removed were
disproportionately the big up-day ones that paid for the friction. Median trade
0.31%, p90 1.12%, worst single round trip **8.73%** — six trades (3.1%) pay over
2% and account for 25% of all impact. Still profitable at every c tested, which
remains the useful finding.

### The defect underneath all of it: a copied constant

Five places carried `hold=15` as a literal, months after L52 made the live hold
10 — `impact_test.BASE`, `rank_test.BASE`, `trigger_test.BASE`,
`exit_test.BASE`, `learning.unconditioned_test`, and `simulate.run`'s own
default. So the published impact sensitivity and the rank-depth slope described
a bucket that had stopped existing, and `exit_test.py` said "the live bucket,
exactly as it stands" directly above a hold that was not the live one.

`exit_test` had it worst: its factorial grid tested `{} if h == 15` — treating 15
as "the baseline, pass nothing". With the default at 10 that row ran a 10-day
hold under a `hold 15d` label *and* became a second control. Every one of these
now READS `selection.HOLD_DAYS`, and the grid's control test reads it too, with
an assert that the control passes no overrides.

**A hardcoded copy of a live constant goes stale silently; a read cannot.** This
is the same failure shape as L58 — code that was correct when written, and was
never re-checked against the thing it was a copy of.

### `--rebaseline` could not record an engine change

`audit.py` compares the headline against `data/baseline.json` and refuses to
absorb a change as drift. Correct — it caught this immediately:

    [FAIL] the recorded baseline still reproduces
           CAGR +7.59% vs +14.14%, n=195 vs 232, same 1698 sessions

But `--rebaseline` only wrote in the branch where `config` (stop/target/hold)
had changed. The guard changed neither — it changed *which fills the engine
believes in* — so the failure landed in the same-sessions branch, which had no
way to re-record. The audit would have failed forever until someone hand-edited
the JSON, and a permanently-red check is a check nobody reads. The flag is the
deliberate act; it does not need a blessed branch.

### The last knob: deliv 1.5 survives, its justification does not

`data/selection_weights.json` was the last thing still carrying a pre-guard
number, and it was carrying it as a *reason*:

    "deliv +50%: unconditioned spread +1.22% on 954 randomly-sampled trades;
     confirmed by simulation (+24.10% CAGR / 27.0% DD vs +12.66% / 38.9% neutral)"

Both halves were unsound before this re-run. The spread came from 954 samples,
and a later 2,337-sample run of the same test read deliv +0.93% and put `rs`
highest instead — at ~0.46% standard error those are the same measurement twice.
The simulation was pre-guard, so its fills included bars no buyer could have
traded.

`research/weight_test.py`, five pre-registered variants, batch 20260819-postlock:

| weights | CAGR | maxDD | n | per trade | vs neutral | t | worst blk |
|---|---|---|---|---|---|---|---|
| neutral 1/1/1/1 (control) | +4.61% | 36.2% | 199 | +1.60% ± 1.16% | — | — | -115.9% |
| **live: deliv 1.5** | **+7.59%** | **31.0%** | **195** | **+2.15% ± 1.08%** | **+0.55% ± 1.58%** | **+0.35** | -126.7% |
| deliv 2.0 | +9.20% | 29.2% | 211 | +2.29% ± 1.03% | +0.68% ± 1.55% | +0.44 | -126.9% |
| rs 1.5 | +7.06% | 33.1% | 198 | +2.11% ± 1.17% | +0.50% ± 1.65% | +0.31 | -119.1% |
| near_high 1.5 | +8.09% | 28.9% | 207 | +2.18% ± 1.05% | +0.57% ± 1.57% | +0.36 | -89.0% |

The live row reproduces the recorded baseline to the digit (+7.59% / 31.0% / 195),
which is the check that the harness is measuring the live bucket and not something
adjacent to it.

**The decision survives in direction and only in direction.** deliv 1.5 beats
neutral on CAGR *and* on drawdown — same shape as the 10-day hold, and the same
verdict: kept because both axes agree, not because anything resolved. t = +0.35.

**The published gap was four times too big.** 11.4 points claimed, 3.0 points
measured. Nobody inflated it; the engine did, by filling circuit-locked bars, and
the note was never re-derived after L58 landed.

**Two of five variants beat the live one, at t < 0.5.** That is the diagnostic.
A knob with a real effect does not produce two rival winners inside half a
standard error, and `deliv 2.0` being monotone above 1.5 is the most tempting
shape in the table precisely because monotone-and-insignificant is what a lucky
path looks like. The 2% participation cap was rejected for the mirror-image
reason (non-monotonic, 2% best, 1% and 5% worse). Neither shape is evidence;
they are the two ways noise presents.

**One observation worth keeping, stated as shape rather than finding.** deliv is
the only raised weight that leaves both clusters positive — micro +2.35% / small
+1.87%, against rs 1.5 at +3.23%/+0.55% and near_high 1.5 at +3.13%/+0.72%. Both
of those buy micro performance by gutting small, which would make the 3/2 mix a
worse container for them. Per-cluster n is ~80, so those gaps are inside one
standard error as well; it is a reason to prefer the weight already in place, not
a reason to have chosen it.

**The re-measurement programme is now complete.** Every number in CLAUDE.md was
measured against the corrected engine, and the pattern held in all four places it
was tested: the levels moved a long way, the rankings barely moved, and the only
claim that gained strength was the one about rank depth — which is the claim that
says the edge is in *selection* rather than in any of these knobs.

## L60 — The same value, copied and never re-checked: six instances

Not a strategy finding. A defect *class*, written down because it has now
produced six separate outages in this project and every one of them looked
healthy from the outside.

The shape is always identical: **a value that was correct when it was written,
copied or scoped somewhere, and never re-checked against the thing it refers
to.** No bug is introduced at the moment of the change. The reference simply
stops matching reality, and nothing asks it to prove otherwise.

| where | the stale reference | how it presented |
|---|---|---|
| `impact_test.py` | `BASE` carried `hold=15` after the live value moved to 10 | a friction table for a bucket nobody runs |
| `agent.py` job paths | `"ops/snapshot.py"` after the `src/` move | subprocess rc=2 into a log nobody reads |
| 23 module bootstraps | `parents[1]` while `paths.py` sat at the root | **every selftest passed**, because the operator's shell exported `PYTHONPATH=.` |
| `tg.py --listen` watch set | `Path(__file__).parent` after `tg.py` moved into `src/ops/` | watched 10 files of 31; edits to `selection.py` served stale logic |
| both installed plists | absolute paths to the deleted root `tg.py` / `agent.py` | listener dead, scheduler never registered |
| `scripts/*.sh` | `cd "$(dirname "$0")"` lands in `scripts/`, where the source no longer is | `setup.sh` looped over an EMPTY `*.py` glob and printed no failures |

**What does NOT catch it:** a status message. The third row is the important
one — a check that passes because of the shell that invoked it is not a check,
and it hid a broken bootstrap in all 23 places at once. The sixth row is the
same shape one turn worse: `setup.sh`'s selftest loop did not report a failure
because it ran no tests at all. **A loop over an empty glob is indistinguishable
from a loop in which everything passed**, and this is the fresh-machine
bootstrap — the first person to trust it is the person with nothing else to
compare against.

**What does catch it:** a check that resolves the reference and asserts the
target exists. `paths._selftest` now asserts `parents[1]` lands on `src/`;
`agent._selftest` asserts every job path is on disk; `tg._selftest` asserts the
six critical modules are in the watch set; `audit.py` now parses both installed
plists with `plutil` — launchd's own parser, because Python's expat rejects the
`--` inside their XML comments and launchd does not care — and asserts every
repo path they name still exists.

`audit.py` also resolves every path inside `scripts/*.sh` **from the directory
that script cd's to**, which is the only way the sixth row is visible: the paths
were correct, and correct relative to a root the script never stood in. It found
three stale paths in `setup.sh` on the run that introduced it.

**A seventh was found by inspection rather than by an outage, and is listed
apart from the six for that reason.** `instrument_keys()` refetched the Upstox
master only when a requested symbol was MISSING, so a symbol that keeps its name
over a new ISIN — amalgamation, re-listing — kept its old key forever. A stale
key resolves to nothing, and an empty quote presents as "no token", which is the
error this project has already chased twice. It is bounded at
`MAX_AGE_DAYS = 30` now, and a refetch that fails keeps the cached keys rather
than dropping to nothing. **No instance of this was ever observed.** It is
written down because the shape is the class — not because it cost anything, and
the difference between a measured outage and a shape spotted while reading is
exactly the distinction this file exists to hold.

**A second, smaller lesson inside the same fix.** `/health` ticked
"✅ Scheduler agent — last ran 4 min ago" four lines above its own
"no launchd job registered -- nothing runs on a schedule". Both were true: the
heartbeat proves the agent *ran*, not that anything will run it again, and
`agent.py --once` typed by hand stamps the same file. Worse, `beat()` was called
inside `once()`, and `_selftest` calls `once()` — so running the test suite made
`/health` claim the scheduler was alive. **A liveness stamp that the test suite
can write is not evidence of liveness.** `beat()` moved to `__main__`, and the
tick now requires a registered job as well as a fresh stamp.


---

## L61 — A second strategy, and the control arm that stopped a false finding

Two new strategies were built as behavioural clones of sprout: **thicket**
(NSE corporate announcements as a score input) and **trellis** (chart patterns
as an exit shape and a trigger). Both are born identical to sprout, with every
new rule shipped off by default, and both reproduce sprout to the digit with
the knobs off — 7.59 / 31.0 / 195, exactly, on the same corpus.

**The operator set one condition: sprout must not be impacted.** That was
written into a design document first, which makes it an intention, not a rule.
It is now `tests/sprout_untouched.py` in the selftest sweep: sprout's four rule
files, its learned weights and its recorded headline are hashed against a
committed manifest; no file may be added to `sprout/` either; no module under
`strategies/` or `research/` may import the live order book; and a non-sprout
`STRATEGY` must resolve `paths.SDATA` outside `data/sprout`. The behavioural
half is left to `audit.py`, which already re-runs the backtest — that is the
check that catches a *shared* module edited so sprout buys something different,
which leaves every hash intact and every import clean.

### The finding: H4, and why it is a negative

The live exit is flat: −10% / +20% / 10 days, no trailing. 10-vs-15 days
measured at t = 0.28, inside the noise, and every later pass over that dial
produced a different winner. So the hypothesis was that the answer is not a
number at all — hold past day 10 while the up-structure is intact.

| arm | CAGR | maxDD | n | per trade | vs flat | t |
|---|---|---|---|---|---|---|
| flat (control) | +7.59% | 31.0% | 195 | +2.15% | — | — |
| flat, 30 days | +12.96% | 31.3% | 185 | +3.57% | +1.42% | 0.85 |
| **structural** | **+8.96%** | **32.5%** | **193** | **+2.49%** | **+0.34%** | **0.22** |

**Read alone, the structural exit looks like a win.** CAGR +8.96 against +7.59,
the exit mix shifting 83 time exits into 68 structural ones, target hits rising
43 → 55 as winners run instead of being cut on day 10. That is the number
somebody adopts.

**The long-flat arm says otherwise, and it is the whole lesson.** Holding 30
days flat — no structure, no chart reading, a dial already known to be inside
the noise — gained **+1.42% per trade where the structural rule gained +0.34%.**
Four times as much, from the dumber rule. The structural exit is not finding
structure; it is a worse way of holding longer, and holding longer is not
established either.

That arm existed because "a structural win could be nothing more than holding
longer" was foreseeable *before* the run, and was written into the
pre-registration. Without it this would have been trellis's first finding.
**A control that only rules out doing nothing is not enough. The arm that
matters is the stupid version of your own idea.**

Also failing: the worst half-year block got worse (−139.7% vs −126.7%, both
2022-H1), and per size group both sit inside the noise (micro t +0.06, small
t +0.31). Bar was |t| ≥ 2.6, fixed before the run — the usual |t| > 2 tightened
across five pre-registered tests, because testing five things at the usual bar
means roughly one wins by luck, which is what "two of five weight variants beat
live at t < 0.5" already looked like.

### The 60% that would have made everything work

NSE stamps every corporate announcement to the second, and **60% of them arrive
after the 15:30 close** — measured, 1,292 of 2,168 rows in one week. Dated by
calendar day, the obvious way, 60% of the signal becomes information nobody had
when the trade was placed. It raises no error and looks entirely normal; it
simply returns a good number built on knowledge that did not exist yet. This is
the circuit-lock shape again (L58) in a new place.

So visibility is the spine of `announcements.py`, not a detail: visible to
session *i* only if timestamped strictly before *i*'s close, else it rolls to
*i+1*, weekends and holidays too. The selftest asserts the 22:56 case by name.
On the real one-month validation slice, **65% of rows rolled forward** — the
rule doing work on real data, not just on fixtures. 1,019,495 announcements
across 360 weeks, 99.96% parsed, 2,640 symbols.

### Two smaller ones, both the same shape

**A feed can return HTTP 200 and be dead.** The first `newswatch` run archived
19 items from two Moneycontrol feeds dated *848 and 849 days old*. Both returned
200. Nothing errored, and the run reported "15 items, 15 new" while ingesting
April 2024. A status code is not evidence the source is alive, exactly as it was
not evidence the bhavcopy was the requested day. The fix judges a feed on its
newest item, which generalises to the next feed that dies.

**A gate that compares against a stored number drifts.** `clone_reproduces.py`
compared clones against `data/sprout/baseline.json` and broke the first time it
mattered: the daily agent fetched a session mid-afternoon, the corpus went 1698
→ 1699, and the gate would have failed on something unrelated to whether the
fork was clean. It now re-runs sprout in a child process and compares
like-for-like. Two backtests instead of one, and it cannot drift.

### H1–H3: announcements, and the number that shows why the bar goes first

Measured on **3,833 randomly sampled trades** — sampled at random, never from
the bucket, because measuring a feature on its own selections is what made
`deliv` look backwards and cost 26 CAGR points.

| feature | spread | std err | t | verdict |
|---|---|---|---|---|
| ann_burst | −0.06% | 0.32% | −0.18 | inside the noise |
| **ann_tone** | **+1.80%** | **0.82%** | **+2.20** | **inside the noise** |
| ann_flag | −0.90% | 0.68% | −1.32 | inside the noise |

**`ann_tone` is the whole lesson.** At t = +2.20 it clears this project's usual
|t| > 2 bar. The bar for this work is **2.6** — the usual one tightened by
Bonferroni across five pre-registered tests — and that number was written into
the spec and into two test modules on 2026-08-20, *before the backfill had
finished downloading* and long before any return was computed.

Set the bar after seeing +2.20 and announcements are thicket's first finding.
Set it before and they are not. Same data, same arithmetic, opposite
conclusion; the only difference is which was written first. That is the
argument for pre-registration demonstrated on this project's own data instead of
asserted. **The criterion does not now get relaxed** — "it nearly passed" is the
exact circumstance the rule exists for.

`ann_burst` is flat everywhere. How *often* a company files carries nothing, and
3,833 trades is enough to say so with some confidence.

### A hypothesis, explicitly not a finding

Decomposing `ann_tone` **after** seeing its result — so this is post-hoc and
gets no protection from the pre-registered bar:

| group | n | mean return | vs neutral | t |
|---|---|---|---|---|
| neutral (reference) | 3,217 | −0.20% | — | — |
| positive tone | 255 | +1.57% | **+1.77%** | **+2.76** |
| negative tone | 361 | −0.23% | −0.03% | −0.05 |

The entire H2 spread is the **positive** side. Dividend, bonus, buyback, split,
open offer precede outperformance; insolvency, default, resignation and auditor
changes carry *nothing* — those names appear to be priced for it already, or the
damage is already visible in the price features the score reads.

The +2.76 is above 2.6 and **must not be treated as passing.** Splitting a
two-sided test into halves and keeping the better half is a way to manufacture
significance, not to find it: the subgroup was chosen after the answer was
known, so it needs a stiffer penalty than the pre-registered bar, not the same
one.

**Then the sample was redrawn, and the number moved.** Rebasing onto main added
two sessions to the corpus, which shifted the sampler's stride and therefore
redrew every sampled symbol-date — an accidental but genuine independent draw of
the same measurement:

| feature | first draw (n=3,833) | redraw (n=3,847) |
|---|---|---|
| ann_burst | −0.06%, t = −0.18 | +0.37%, t = +1.19 |
| **ann_tone** | **+1.80%, t = +2.20** | **+1.24%, t = +1.71** |
| ann_flag | −0.90%, t = −1.32 | −0.29%, t = −0.40 |

The two tone estimates differ by 0.56% against a standard error near 0.8% — so
the error bar was honest, and **t = 2.20 was not a stable signal.** Redraw the
sample and it is 1.71. Adopt at the usual |t| > 2 and this feature would have
been in the score on Monday and out of it on Tuesday, with nothing about the
market having changed.

That is what the pre-registered 2.6 was protecting against, demonstrated twice
in two days: once by the bar refusing the number, and once by the number
refusing to stay put.

What the tone result is instead is a sharp, cheap, falsifiable hypothesis for
the future:
*positive corporate actions predict; bad news does not.* And it cannot be
confirmed on this corpus — any re-test would use the same price history that
generated it. **The only thing that can settle it is forward paper trades, of
which this project has still closed zero.** Which is where CLAUDE.md said the
highest expected value was before any of this work started.

### H5: patterns, and the arm that cleared instead of killing

Flag, ascending triangle and cup-and-handle, frozen in their own commit before
this test was written. Control is `breakout`, the incumbent, not `none`.

| trigger | CAGR | maxDD | n | per trade | vs breakout | t |
|---|---|---|---|---|---|---|
| breakout (control) | +7.34% | 31.0% | 196 | +2.09% | — | — |
| **pattern** | +8.75% | 33.9% | 214 | +2.51% | **+0.43%** | **0.21** |
| none (looseness ref) | −2.43% | 39.5% | 236 | +0.12% | −1.97% | −1.28 |
| flag | +1.76% | 40.4% | 199 | +1.05% | −1.04% | −0.63 |
| asc_triangle | +5.23% | **10.4%** | 145 | +1.83% | −0.25% | −0.19 |
| cup_handle | +4.17% | **10.0%** | **30** | **+8.50%** | +6.42% | 0.65 |

Not adopted: t = 0.21 against a bar of 2.6.

**The looseness arm behaved backwards, which is worth more than if it had
behaved as expected.** `pattern` fires on 17.6% of bars against breakout's 3.9%,
so the obvious failure mode was that any gain is just a looser filter reaching
deeper down the ranked list — the one penalty this project has actually resolved
(−1.18% per cohort, t = −4.10). Instead `none`, maximally loose, *lost* 1.97%
per trade. Looseness is plainly harmful here, so pattern's small edge is not
made of it. The arm was put in expecting it to kill the hypothesis; it cleared
it, and the hypothesis died on its own error bar anyway.

**cup_handle is the most dangerous number produced anywhere in this work:**
+8.50% per trade, four times the control — on **n = 30**. Its standard error
puts it at t = 0.65. Thirty draws from a distribution with 16% per-trade spread
will throw up a mean like that routinely. It was registered as description with
no adoption path *before* it was run, which is the only reason it is a curiosity
here rather than trellis's headline.

**And one thing is left deliberately undecided.** asc_triangle and cup_handle
both show ~10% maximum drawdown against breakout's 31%. That is a large move in
the direction this book cares most about — and there is **no pre-registered
adoption path for drawdown**, the same hole L62 records for bucket size.
Deciding it now would be picking the criterion after seeing the number, which is
the one habit this file exists to prevent. It wants its own pre-registered test.

---

## L62 — Bucket size measured for the first time: monotone, and still inside the noise

Five seats had never been compared to anything. `simulations.jsonl` held six
runs and not one recorded `max_pos`, so the number was an assumption nobody had
examined rather than a decision anybody had made. Measured at 3 / 5 / 8 / 12
seats with the per-cluster cutoff scaled to the live 3:2 ratio, so the arms
differ in SIZE and not in mix (`src/research/bucket_size_test.py`, batch
20260820-bucketsize, hold 10d, c=1.0).

| seats | mix | CAGR | maxDD | occ | n | per trade | vs live | t |
|---|---|---|---|---|---|---|---|---|
| 3 | 2/1 | +10.08% | 27.4% | 1.36 | 132 | +3.00% +/- 1.43% | +0.85% | +0.48 |
| **5 (live)** | **3/2** | **+7.59%** | **31.0%** | **3.10** | **195** | **+2.15% +/- 1.08%** | -- | -- |
| 8 | 5/3 | +5.59% | 19.7% | 4.47 | 318 | +1.54% +/- 0.81% | -0.61% | -0.46 |
| 12 | 7/5 | +3.83% | 20.2% | 6.03 | 462 | +1.08% +/- 0.65% | -1.07% | -0.85 |

**Nothing clears the bar. Largest |t| is 0.85, so every arm is inside the
noise and the live 5 stands.** The bar was fixed in the module docstring before
the first run: |t| > 2, monotone, both clusters improving, maxDD not worsening
by more than 3 points.

**The shape is the tempting part, and it is still a shape.** Per-trade return
decays monotonically as seats are added -- 3.00 > 2.15 > 1.54 > 1.08 -- which is
exactly what was PREDICTED in writing before the run, and it agrees with the one
result here that is resolved: rank depth at -1.18% per cohort step (t = -4.10,
n = 1,015). Each added seat is filled from further down a ranking that decays.
Two independent measurements pointing the same way is worth more than one, and
it is still not |t| > 2. This project has been burned by exactly this shape
before: `deliv 2.0` was monotone above 1.5 and was not adopted either.

**The monotonicity is not clean everywhere, and reporting only the total would
hide that.** Micro decays monotonically (+2.40 / +2.35 / +1.20 / +0.88) and the
2019-2021 block does too (+8.03 / +5.36 / +4.15 / +3.63). Small does NOT: it
reads +4.14 / +1.87 / +2.10 / +1.41, so the LIVE five seats is small's
second-worst arm. 2022-2023 is negative at every seat count.

**What the bar could not see, stated because the honest move is to leave it for
a pre-registered test rather than re-decide now.** Drawdown improves sharply
with more seats: 31.0% at five against 19.7% at eight, eleven points. Condition
(d) only forbade maxDD WORSENING; there was no adoption path for maxDD
improving, so the bar cannot adopt on it and must not be given a new clause
after seeing the number -- criteria may be tightened, never loosened. Note also
that maxDD is one number off one path with no error bar, so an eleven-point gap
is not resolvable either. If concentration-vs-drawdown is worth testing, it
needs its own hypothesis and its own bar, written first.

**One confound, left in deliberately.** The 3-seat arm deploys ~60% where the
others deploy ~75%, because the 2% per-trade risk rule binds before the
deployment cap once the slice grows. That is the risk invariant working, and
CLAUDE.md forbids searching risk invariants, so it was not relaxed to tidy the
comparison. The 3-seat row answers "3 seats AND less money deployed".

### 8 seats at 4/4, asked afterwards -- and the cleanest mix test yet run

The operator proposed re-cutting the 8-seat bucket as 4 micro / 4 small rather
than the 5/3 the ladder used. Run as a POST-HOC arm: excluded from the monotone
check and from the promotion bar, because an arm added after seeing the table is
one more comparison against the same reference, and adding arms until one looks
good is the search this file exists to avoid.

| arm | CAGR | maxDD | occ | n | per trade | vs live | t |
|---|---|---|---|---|---|---|---|
| 8 @ 4/4 | +5.58% | 21.5% | 4.35 | 311 | +1.59% +/- 0.79% | -0.56% | -0.42 |
| 8 @ 5/3 | +5.59% | 19.7% | 4.47 | 318 | +1.54% +/- 0.81% | -0.61% | -0.46 |

Inside the noise against the live bucket, like everything else here. But the
comparison worth keeping is the one the ladder could never make, because every
ladder arm holds the mix ratio fixed:

**8 @ 4/4 against 8 @ 5/3 -- same seats, same deployment, mix ONLY -- is
+0.05% per trade at t = +0.05, on n=311 vs n=318.**

Those are the two largest samples in the experiment and the difference is
indistinguishable from exactly zero. CAGR agrees: 5.58% against 5.59%. This is
the tightest test of the cluster mix this project has run -- the 3/2 vs 2/3
comparison was confounded by settings that moved at the same time and gave a
different sign on each of three occasions (t = -0.24 most recently). Holding
seats and deployment constant and moving only the mix moves nothing.

So the mix is not a lever. Whatever the bucket contributes, it is not coming
from how the seats are divided between micro and small; it comes from how MANY
seats there are, which is the axis that moved every number in the table above.
Per cluster the 4/4 arm reads micro +1.44% +/- 1.21% (n=169) and small +1.77%
+/- 0.95% (n=142), against the live +2.35% (n=115) and +1.87% (n=80): giving
micro one fewer seat did not help small.

### The bug that would have inverted this result

`selection.position_size` capped a name at `capital * DEPLOY_PCT / MAX_POSITIONS`
reading the MODULE constant, while `simulate.run` accepted `max_pos` as an
argument. So `max_pos=12` produced twelve slices each sized for a five-seat
book: **180% of equity deployed**, and nothing in the buy loop checks cash. The
larger arms would have won by running more money and it would have read as a
finding about concentration. `position_size` now takes `max_pos`; the default is
the live constant and the live path is provably unchanged (identical qty across
seven prices; audit CAGR moved 0.00 with the corpus one session larger).
`selection._selftest` now asserts total deployment stays within DEPLOY_PCT at
1, 3, 5, 8, 12 and 20 seats, and that the slice shrinks as seats are added.

`simulate.py`'s own demo had been printing a 120%-deployed "8 positions" row for
as long as that demo has existed.

## L63 — A selftest written from the same assumption as the code proves only that the assumption is self-consistent

The first bucket-size run reported **n=0 on all four arms** and printed
"nothing clears the bar; no arm reached |t| > 2". That is indistinguishable from
a real null result, and it was nothing of the kind: `_pct()` guessed the trade
record's keys as `pct`/`entry`/`exit`, and the real ones are `ret`/`clu`/`day`.
No return parsed, every arm empty, and the promotion logic dutifully concluded
that nothing was significant.

**The selftest passed.** It fed `_pct()` dictionaries shaped the way the guess
assumed -- `{"pct": 4.2}`, `{"entry": 100.0, "exit": 110.0}` -- so it confirmed
the code matched its author's belief and said nothing about the producer. A test
whose fixtures are invented by the same person, in the same sitting, from the
same misreading, cannot fail.

This is L60's family: a reference correct in the author's head, never re-checked
against the thing it refers to. The difference is that L60's instances were
caught by something downstream; this one produced a plausible FINDING, which is
worse. It is also the same shape as `setup.sh` looping over an empty glob and
reporting no failures, fixed the same day -- **an empty result and a passing
result look identical unless something refuses to accept empty.**

Fixed two ways, and the second matters more than the first:
1. The fixture is now a verbatim record copied off a real `simulate.run`, so if
   that contract moves the test fails instead of the experiment.
2. `measure()` RAISES when an arm produces trades but zero parsed returns. No
   data can no longer be reported as a null result, by construction rather than
   by the reader noticing.

## L64 — Drawdown vs concentration: the first thing to clear a pre-set bar, and it is still a decision, not an answer

L62 left this open deliberately. Eight seats showed 19.7% drawdown against the
live 31.0%, and the bucket-size bar had no adoption path for drawdown IMPROVING
-- only a veto on it worsening -- so the question was parked rather than
re-decided after the fact. Asked properly here (`src/research/drawdown_test.py`,
batch 20260820-drawdown).

**The 31.0 vs 19.7 gap was never evidence.** maxDD is one number off one path
with no error bar. The fix is the whole design: split the equity curve into
DISJOINT six-month blocks, compute drawdown inside each with the peak resetting
at the block start, and compare arms over the same calendar blocks so the test
is PAIRED and regime cancels out. `simulate.run` now returns the curve; it did
not before, and nothing in the loop reads it.

| seats | pathDD | mean block DD | vs live | std err | t | median | win% | LOO t |
|---|---|---|---|---|---|---|---|---|
| **5 (live)** | 31.0% | 6.82% | -- | -- | -- | -- | -- | -- |
| 8 | 19.7% | 5.41% | -1.41% | 0.77 | -1.84 | -0.73% | 67% | -1.72 |
| **12** | 20.2% | **4.82%** | **-2.01%** | 0.84 | **-2.40** | -1.65% | **75%** | **-2.61** |

**Twelve seats clears every condition of the bar set before the run:** |t| > 2,
median agreeing with the mean in sign, monotone across 5/8/12, and no resolved
return cost. Eight seats does not (t = -1.84).

**It is not one episode, and that was the alternative the design was built to
catch.** 2022H1 dominates in magnitude (22.5% at five seats against 12.8% at
twelve, a -9.7 point difference), but dropping it makes the statistic STRONGER,
not weaker: LOO t = -2.61 against -2.40. Nine of twelve blocks improved. A
single-episode artefact behaves the opposite way -- it inflates the variance it
is measured against and collapses when removed.

### Three reasons to hold this loosely, stated because the result is favourable

1. **Twelve blocks is twelve observations.** At df=11 the 5% critical value is
   2.201, so t = -2.40 is p ~ 0.035. It clears; it does not clear comfortably.
2. **Multiplicity was not pre-registered, and it should have been.** Two arms
   were tested against the same reference. A Bonferroni threshold for two
   comparisons at 5% is |t| > 2.49, which **twelve seats does not reach** on the
   headline statistic (it does on the leave-one-out, -2.61). The bar as written
   was cleared and the bar as written is what governs -- criteria may be
   tightened, never loosened, and that cuts both ways: a correction invented
   after seeing the number is not a correction, it is a veto. Recorded as a
   defect in the pre-registration, to be fixed in the NEXT one.
3. **The return cost is unresolved but the point estimate is a halving.** Twelve
   seats earns +1.08% per trade against +2.15% (t = -0.85, inside the noise) and
   CAGR +3.83% against +7.59%. Condition (d) tested per-trade return, which is
   the right statistical endpoint, and CAGR has no error bar so it cannot be
   tested at all. But halving the headline is what an operator actually
   experiences, and no t-statistic makes that disappear.

**This does not contradict rank depth; it is the same finding seen from the
other side.** Twelve seats earns less per trade exactly as the -1.18%-per-cohort
slope predicts, because the extra seats are filled from further down. What the
extra seats buy is diversification of idiosyncratic risk. Both are true at once,
and the choice between them is a preference, not a fact.

**Nothing was changed.** `MAX_POSITIONS` is still 5. How much drawdown the book
should accept is the operator's design decision and not an output of a backtest
-- CLAUDE.md is explicit that the approach is the user's design, not a parameter
to be tuned away. Clearing the bar earns the question a hearing with evidence
attached; it does not earn an edit.

### What the selftest found out about the bar itself

Building the guard exposed three things worth keeping:

- **Condition (b) is a backstop, not the primary defence.** A lone outlier
  inflates the variance it is measured against, so a one-episode fixture fails
  condition (a) on its own and never reaches the leave-one-out check. Useful to
  know before trusting a guard to do work the t-statistic already did.
- **A real bug in that guard**: a zero-variance leave-one-out set was treated as
  a FAILURE, when a perfectly consistent remainder is the strongest possible
  pass. It would have rejected genuine effects.
- **A divide-by-zero** when both arms had zero variance, in the code that
  decides whether the bar is met.

The selftest now checks all four directions: a one-episode fixture is rejected,
a consistent effect carrying one extra-bad block SURVIVES (a guard that rejects
every series with a worst block rejects every real series), and a resolved
return cost vetoes even convincing drawdown.

## L65 — Pooled ranking: the case against it was made of phantom fills, and it still is not adopted

The operator asked whether the 3/2 quota should go, letting merit take all five
seats -- 5/0 if small looks stronger than micro, 0/5 if not. That is exactly
`RANKING = "pooled"`, which already exists, was already tried, and was already
reverted. Re-run post-guard (`src/research/pooled_test.py`, batch
20260820-pooled) because every number behind the revert predated the
circuit-lock guard by three days, and CLAUDE.md's rule is that any figure
without a post-guard tag is the old, phantom-filled one.

| measure | per_cluster 3/2 | pooled | better |
|---|---|---|---|
| CAGR | +7.59% | **+8.42%** | pooled |
| maxDD, whole path | 31.0% | **30.0%** | pooled |
| per trade | +2.15% +/- 1.08% (n=195) | **+2.19% +/- 1.05%** (n=207) | pooled |
| worst 6-month block | -22.5% | **-21.7%** | pooled |
| top-1 share of gains | 11.2% | **9.5%** | pooled |
| distinct symbols | **124** | 120 | per_cluster |
| stocks held | **3.10** | 2.11 | per_cluster |

**The prediction written before the run was wrong, and wrong where it counted.**
It said pooled would reproduce its pre-guard shape: higher CAGR, WORSE tail,
HIGHER concentration. The tail and the concentration both REVERSED. Pre-guard,
the entire case against pooled rested on those two -- worst half-year -119.4%
against -83.6%, best single symbol 15.4% of all gains against 7.6%. Post-guard
pooled wins both, and wins 5 of 7 measures overall.

**Look at what happened to the magnitudes.** A worst half-year block of -119.4%
is not a market event, it is a bucket filled at prices no seller offered. The
guard removed the circuit-locked bars -- 9.8% of picks had a locked trigger bar,
8.7% a locked FILL bar, every one an upper lock -- and those were
disproportionately the violent up-day fills. They inflated a few names enormously
(concentration) and produced block swings above 100% (tail). Strip them and both
measures come back to earth: -21.7% against -22.5%, 9.5% against 11.2%. **The
argument that killed pooled was an artefact of the same bug L58 found.**

**And pooled is still not adopted, for a better reason than before.** The bar
required the per-trade edge to be RESOLVED in pooled's favour. It is +0.04% at
**t = +0.03**, which is as close to exactly nothing as this project has
measured. Paired block drawdown is +0.08% at t = +0.12. The CAGR gap shrank from
+3.04 points pre-guard to +0.83. Condition (c) also failed on distinct symbols,
120 against 124, though that margin is trivial and would not carry the decision
alone.

So the verdict is unchanged and its reason is replaced: not "pooled loses 5 of 7"
but "the two are indistinguishable, and a live rule is not swapped on a t of
0.03". The stale justification in `selection.py` has been rewritten, because a
recorded reason that no longer matches reality is the L60 defect and this one was
still being quoted.

### Pooled at 8 and 12 seats, asked afterwards

Post-hoc arms, excluded from the bar. Measurable at all only because
`allocate()` now honours an injected seat count -- it read the module constant,
so a pooled bucket asked for eight seats allocated five and the arm would have
been MISLABELLED rather than merely wrong. Same defect `position_size` carried,
found the same day, in the same function family.

| arm | CAGR | maxDD | worst blk | top-1 | syms | occ | n | per trade | t | paired block DD |
|---|---|---|---|---|---|---|---|---|---|---|
| per_cluster @5 | +7.59% | 31.0% | -22.5% | 11.2% | 124 | 3.10 | 195 | +2.15% +/- 1.08% | -- | -- |
| pooled @5 | +8.42% | 30.0% | -21.7% | 9.5% | 120 | 2.11 | 207 | +2.19% +/- 1.05% | +0.03 | +0.08 (t=+0.12) |
| **pooled @8** | +5.31% | 27.0% | **-16.8%** | **8.1%** | **178** | 4.06 | 322 | +1.47% +/- 0.78% | -0.51 | **-1.27 (t=-2.36)** |
| pooled @12 | +1.19% | **20.3%** | **-11.1%** | **6.1%** | **262** | 6.07 | 480 | +0.52% +/- 0.65% | -1.29 | -1.40 (t=-1.42) |

Pooling at eight improves every risk measure at once -- worst block, top-1
share, distinct symbols, whole-path drawdown -- and its paired block drawdown
reads t = -2.36. Return falls with it: CAGR +5.31% against +7.59%, per trade
+1.47% against +2.15% (t = -0.51, unresolved). At twelve the return collapses to
CAGR +1.19% while the risk measures keep improving.

**This is the same axis as L64, seen through a different rule.** Whether seats
are filled by quota or by pooled merit, adding them trades return for drawdown
in the same direction and roughly the same proportion. That consistency is worth
more than either individual result, and it says the effect belongs to
CONCENTRATION, not to how the seats are divided -- which is exactly what the
mix null (+0.05% at t = +0.05) already implied.

### The multiplicity across the whole day, which no single file records

Nine non-reference arms were compared against the same live bucket today across
three experiments: bucket size at 3/8/12, bucket 8 at 4/4, drawdown at 8/12, and
pooled at 5/8/12. A Bonferroni threshold at 5% for nine comparisons is
**|t| > 2.77**.

Both of the day's "RESOLVED" results sit below it -- L64's twelve-seat drawdown
at t = -2.40 and pooled@8 at t = -2.36. **Neither survives a correction for how
many questions were asked.** Each file registered its own multiplicity honestly
within itself, and no file could see the total, which is how a day of disciplined
experiments still ends up over-claiming.

What survives is not any single t. It is that every arm, on both rules, moved
drawdown and return in the same direction by roughly the same amount. A shape
repeated across nine arms is a different kind of evidence from one arm at
t = 2.4, and it is the only kind this day produced.

**Two smaller things worth keeping.**

The fear that pooling collapses the bucket into one band did not materialise at
this scale: pooled traded 91 micro against 116 small, a 44/56 split, not 0/100.
The structural tilt is real -- pooling stops neutralising `liq`, so the more
liquid band gains seats regardless of whether it is having a good week, and the
pooled top ten on 2026-08-20 was 9 small / 1 micro against 8 / 2 -- but it is a
tilt, not a collapse.

Pooled holds **2.11** names against 3.10. That cuts directly against L64, where
more concurrent positions resolvably reduced block drawdown (t = -2.40). Pooled
gets a slightly better whole-path maxDD while holding a third fewer names, which
is the one genuinely puzzling number in the table and is not explained here.
---

## L66 — Announcement sentiment: independent of momentum, and still empty

**H6, pre-registered and committed before the graded scorer met a single
return.** Two questions in one test: does reading the filing TEXT add what the
category label cannot, and is any of it just momentum wearing a different hat?

Measured on **1,792 randomly sampled trades** with a scoreable filing history.

| feature | spread | std err | t | verdict |
|---|---|---|---|---|
| category only (`ann_tone`, H2's feature) | +1.24% | 0.73% | +1.71 | inside the noise |
| **graded (category + filing text)** | **−0.86%** | **0.79%** | **−1.08** | inside the noise |

**Adding the text flipped the sign.** Not "helped less than hoped" — reversed.
Whatever the lexicon reads in a filing summary is noise, and noise added to an
already-unresolved signal degrades it. That is consistent with something
measured earlier and not connected at the time: **87 of 91 filings score
silent**, because the corpus is overwhelmingly procedural boilerplate ("has
informed the Exchange regarding…"). The four that do score are a general finance
lexicon reading formal legal phrasing, and it reads it badly.

### The prediction that was written down, and was wrong

Before the run, in the file and in the message announcing it: *condition 3 is the
one most likely to fail — if announcement sentiment tracks momentum, it tells you
what the price already told you.*

    correlation(graded score, 6-month momentum)  = +0.036
    correlation(category only, 6-month momentum) = −0.018

**Essentially zero.** The echo hypothesis is refused outright, and the stated
prior was simply wrong. The value of having written it down first is that it
cannot now be quietly reframed as what was expected all along.

### Which makes this a cleaner answer than a failure

The two hypotheses are separable, and both are refused:

- **Is it redundant with momentum?** No — r = 0.036. It is genuinely
  independent information.
- **Does it predict returns?** No — t = −1.08, and the sign flips once the text
  is read.

So announcement sentiment is not a worse version of momentum. It is orthogonal
to momentum **and carries no return information in this universe**. That is a
stronger statement than "it did not clear the bar", and it closes the line of
enquiry rather than leaving it open for a differently-tuned retry.

`ANN_FEATURES` stays empty. The scorer stays as an operator's view — where it
reads today's filings for a person, which was always its better use.

### What survives

The category-only arm reproduced H2 to the digit (+1.24%, t = +1.71, 264/357).
Same seed and same sampling, so this is consistency rather than independent
confirmation — but it does mean the tercile split and the sign split pick out the
same groups, and neither measurement is an artefact of how the split was drawn.

---

## L67 — asc_triangle's drawdown: structurally real, mostly exposure, not adopted

**H7, pre-registered.** The awkward part was written into the file before the
run: `asc_triangle` was tested *because* its H5 drawdown looked good — 10.4%
against breakout's 31.0% — which is choosing what to test after seeing which arm
won. Two structural corrections made it legal anyway: every trigger was measured
rather than the one that looked good, and the bar was Bonferroni-corrected
across all ten comparisons to **|t| ≥ 2.81**, the tightest in this project.

Method imported from `drawdown_test.py`, not rebuilt: disjoint six-month blocks,
drawdown computed inside each, arms compared over the same calendar blocks so
the comparison is paired and regime drops out.

### asc_triangle vs breakout, 12 blocks

| | |
|---|---|
| mean block-drawdown difference | **−3.92% ± 1.62, t = −2.41** |
| median difference | −2.35% (same sign as the mean) |
| blocks improved | **83%** |
| leave-one-out \|t\| | **2.82** — *strengthens* when the biggest block is dropped |
| per-trade return cost | +1.83% vs +2.09%, t = −0.19 |

**Conditions (b) and (d) pass convincingly.** This is emphatically *not* one bad
episode: five of six blocks improved, the median agrees with the mean, and
removing the largest-magnitude block makes the statistic stronger rather than
collapsing it. That is the opposite of the failure mode L62 warned about.

### And it is still not adopted, on two conditions that both failed narrowly

    a  |t| 2.41 against a bar of 2.81          FAIL
    c  correlation(occupancy, block DD) 0.712  FAIL (threshold 0.70)

t = 2.41 would clear this project's usual |t| > 2. The bar was 2.81 *because ten
arms were searched*, and it was fixed before the run. r = 0.712 misses its
threshold by 0.012. **Moving either number now is the single most tempting act
available and would invalidate the whole exercise.** They stay.

### The exposure story, visible by eye

| trigger | occupancy | mean block DD |
|---|---|---|
| cup_handle | 0.37 | 2.15 |
| asc_triangle | 2.46 | 2.98 |
| pullback | 2.67 | 6.15 |
| breakout | 3.10 | 6.90 |
| none | 4.31 | 9.42 |

Hold less, draw down less. **A trigger buys comfort by not trading, and that is
available free at any time without a pattern detector.** The prediction written
down before the run — that condition (c) would be the one to fail — was correct
this time; the equivalent prediction for H6 was wrong. Both are on the record.

### The residual, recorded as a question and NOT as a finding

Exposure explains about half the variance (r² ≈ 0.51), not all of it. And one
row does not fit the trend: `vol+breakout` holds **fewer** positions than
asc_triangle (1.99 vs 2.46) and draws down **more** (5.26 vs 2.98). So
asc_triangle sits below the exposure line rather than on it, which the exposure
story alone does not explain.

That is interesting and it is **not a result**. Building a residual statistic now
— after seeing which arm beat the trend — is the post-hoc spiral this whole file
exists to resist. It is written down as a hypothesis for a fresh, separately
pre-registered test, on the explicit understanding that noticing it here buys it
nothing.

---

## L68 — Sentiment and risk: the gate closed, and two near-misses are what noise looks like

**H8–H11, all four pre-registered in one commit before any ran**, with the bar
fixed at |t| ≥ 2.84 for the maximum family of eleven, and a gate: H10 (sizing)
and H11 (stand-aside) would run only if H8 or H9 cleared. On 3,702 sampled
trades — 961 negative, 723 positive, 1,910 neutral control.

| hypothesis | diff | std err | t | verdict |
|---|---|---|---|---|
| H8 negative → volatility | −0.056% | 0.057% | −0.98 | inside the noise |
| **H9 negative → stop-out rate** | **−4.16pp** | 1.65pp | **−2.52** | inside the noise |

**Gate CLOSED.** H10 and H11 were not run and the family stays at nine tests.
That is the gate doing its job: sizing by a signal, or standing aside on one,
needs the signal to exist, and building either after this would have been sizing
by noise.

### The prior was wrong again, and the direction is the interesting part

Written down before the run: *negative sentiment RAISES volatility and RAISES
the stop-out rate* — following H6 and the literature on negative asymmetry.

Both came back **opposite**. Negative-sentiment names stopped out **less** often
than the neutral majority: 21.2% against 25.4%.

Three predictions have now been recorded in advance and read back: wrong on H6
(momentum would explain it — r came back 0.036), right on H7 (exposure would),
wrong here. That record is only worth having because each was written first, and
two of three being wrong is the honest reason to distrust a stated mechanism
that has not been measured.

### The internal inconsistency, which argues for noise

H9 moved and H8 did not. If negative sentiment reduced stop-outs by reducing
volatility, H8 would show it — and H8 is flat at t = −0.98. H6 separately found
negative tone carries nothing on **return** (−0.03%, t = −0.05).

So the claim would have to be: same volatility, same mean return, materially
fewer stop-outs. That is not impossible — it would require a differently shaped
return distribution — but it has no mechanism behind it and no supporting
channel. Absent one, the simplest reading is that it is noise.

### Two near-misses are what noise looks like at nine tests

The largest |t| values this family has produced are **2.52 (H9)** and **2.41
(H7)**, against bars of 2.84 and 2.81.

That pattern is not a run of tantalising almost-findings. Across nine two-sided
tests under a true null, the largest |t| observed is expected to land around
2.1–2.3, and values near 2.5 are entirely ordinary. **Seeing the top two results
sit just under a correctly-set bar is the signature of a bar that is doing its
job, not of an effect being narrowly missed.** The temptation to read them the
other way is exactly why the bars were fixed in advance.

Nine hypotheses. Nothing adopted. `ANN_FEATURES` stays empty, `TRIGGER` stays
`breakout`, and sizing stays equal-weight.
