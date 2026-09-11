# What changed, and why the numbers got worse

Plain-language record of the corrections made on 2026-08-19 and 2026-08-20. If
you only read one page about this project's honesty, read this one.

**There have now been FOUR of these, and they get smaller.** Part one is the
circuit-lock fix (halved the result). Part two is the ETF fix (took two thirds
of what was left). Part three is the fill-hole fix, three trades wide. Part four
found nothing at all, and that is the point: the first two teach you to expect a
bloodbath every time someone reads a fill assumption, and by part four that
expectation is simply wrong.

Terms used here are defined in `docs/glossary.md`. Evidence and workings are in
`docs/lessons.md` (L58, L59, L69, L98, L99).

---

# Part one — buying shares nobody was selling (2026-08-19)

---

## The one-line version

**Nothing about what the bot buys changed. What changed is how much money we
believe it makes — and the honest answer is about half of what we thought.**

---

## What was broken

Indian stocks have a daily price ceiling set by the exchange. When a small
stock slams into that ceiling, everybody wants to buy and **nobody is
selling** — there are no shares available at any price.

The backtest bought them anyway, and counted the profit.

The code to reject those days had existed for months. It was written
correctly. **Nothing ever called it.** About one in ten of the bot's simulated
purchases were on such a day, and every single one was an upper lock — the
un-buyable direction.

---

## The headline

| | Old (fantasy fills) | New (honest fills) |
|---|---|---|
| Yearly growth (CAGR) | +14.14% | **+7.59%** |
| Worst fall (maxDD) | 25.8% | **31.0%** |
| Trades | 232 | **195** |
| On Rs 3,00,000 | about Rs 42,000/year | **about Rs 23,000/year** |
| Worst moment | down about Rs 77,000 | **down about Rs 93,000** |

Lower return **and** a deeper hole. Both moved the wrong way. That is what
removing luck looks like — if only the return had fallen, it would be
suspicious.

---

## Then every past decision was re-checked

Every setting in this project had been chosen using the fantasy numbers, so
each one had to be re-run on the fixed engine.

| Decision | Old reasoning | New result | Changed? |
|---|---|---|---|
| Hold 10 days, not 15 | 10 looked better | 10 = +7.59%, 15 = +5.32% | **No.** Still better on both return and drawdown |
| 3 micro + 2 small | chosen at old capital and cost settings | 2/3 leads by 2.34 points | **No.** It has now given three different answers at three settings — that is a coin flip, not a finding |
| Only buy breakouts | kept **despite** losing a point of return, on a side argument about bad patches | breakout +7.59% vs no-trigger **−2.20%** | **The reason changed.** It now wins outright. The rule was right; the old excuse for it was propping up a measurement error |
| Count delivery 1.5× | claimed "+24.10% vs +12.66%" — an 11.4-point gap | +7.59% vs +4.61% — a **3.0-point** gap | **No.** Kept, but the bragging was four times too big |
| Buy only top ranks | top beat deepest by +6.41% | top beats deepest by **+6.63%**, and the trend is now clearly real (t moved from −2.56 to **−4.10**) | **Got stronger** |
| Trading costs | at c=1.0 they cost almost nothing | at c=1.0 they eat **36%** of the profit | **Much worse** |

**Not one rule was changed as a result.** Every gap is still inside the noise,
and re-deciding a setting on a fresh backtest is how you end up fitting dials
to one lucky path.

---

## The two things worth remembering

### 1. Every knob is a coin flip. The stock-picking is not.

All the settings — hold length, the 3/2 mix, the score weights — score t under
0.5. Unproven, all of them.

But "buying the top-ranked stocks beats buying further down the list" scores
**t = −4.10**, and every single deeper group now *loses* money. That is the
good outcome: the value is in the **picking**, not in dials that could be
fiddled until they looked good.

### 2. Five score weightings were tested. Two "beat" the live one.

That is the tell. A real effect does not produce two rival winners inside half
a margin of error.

So **nothing was adopted** — including `deliv 2.0`, which showed +9.20% and a
*better* drawdown than the live setting. Chasing it would be fitting the dial
to one lucky path. The live weights stayed exactly as they were.

---

## Bugs found along the way

| Bug | Why it mattered |
|---|---|
| The hold length `15` was **typed as a number in six files** instead of read from the setting | The live bot switched to 10 days months earlier. So the published cost table and the rank study described a bucket that no longer existed. One test even said "the live bucket, exactly as it stands" directly above the wrong number |
| One test grid treated `15` as "the default, change nothing" | With the default now 10, that row ran a **10-day** hold under a label saying **15 days**, and silently became a second control |
| The audit could not record an engine change | It correctly shouted "the number moved!", but the re-record button only worked for *setting* changes, not engine fixes. It would have stayed red forever — and a permanently-red alarm is an alarm nobody looks at |
| The status page retyped `+10.85%` in prose | The stored file said something else. It now reads the file |
| The candidate list showed the same test five times | Those were five *re-runs*, not five candidates — and the most optimistic one sat at the top |
| "Worst cost: 24.77%" printed under a table whose live row was a different setting | 24.77% belonged to the pessimistic setting. The live one is **8.73%**. Both are printed now, each labelled with the setting that produced it |

All six are the same disease: **a number that was correct when written, copied
somewhere, and never re-checked against what it copied.**

---

## The uncomfortable part

The honest wait for real proof got **longer**:

| | Old | New |
|---|---|---|
| Average edge per trade | 3.07% | **2.15%** |
| Trades needed to prove it | about 105 | **about 213** |
| Time at this trading pace | about 1.5 years | **about 3.0 years** |

A smaller edge is harder to prove, so it needs more evidence. Overstating the
edge was quietly flattering the project twice: once on the return, and once on
how soon we would know.

---

# Part two — buying gold instead of companies (2026-08-20)

## The one-line version

**The bot is supposed to buy small Indian companies. For 11% of its trades it
was buying gold, silver and stock-market index funds instead — and most of the
recent profit came from exactly those.**

## What was broken

Alongside real company shares, the exchange lists things called **ETFs**. They
have a ticker and a price and look identical in the price file, but they are
not companies — they own a pile of gold, or a basket of the whole market.

The project already had a list of "these are not companies, never buy them".
It was built by comparing today's price file with today's official company
list. Anything trading today that the company list did not mention was a fund.

That works perfectly — **for a fund that still exists**. A fund that shut down
in 2022 is not in today's price file at all, so it never got onto the list, so
every historical test treated it as an ordinary small company.

The code even explained, carefully and correctly, why *companies* that shut
down must be kept: dropping them would only ever leave the survivors and
flatter the results. Funds quietly inherited that protection and should never
have had it.

## Why the obvious fix was not available

The natural answer is "use the company list from back then". **There isn't
one.** The project has 1,699 days of price files and only 7 days of company
lists, all from the same single week in August 2026. Nothing on disk records
what a 2021 ticker was.

So each old ticker had to be *worked out*, and the honest problem is that the
name alone is not enough:

| Ticker | What it actually is |
|---|---|
| DECNGOLD | Deccan Gold Mines — a **mining company** |
| GOLDENTOBC | Golden Tobacco — a **cigarette company** |
| JETFREIGHT | Jet Freight Logistics — the letters "ETF" happen to sit inside "freight" |
| PNBGILTS | PNB Gilts — a real **financial company** |

Deleting those from history would be the same survivorship mistake, pointing
the other way. So a second test was added: **does the thing actually move like
a fund?** A gold ETF's daily wiggles copy gold's almost exactly. A gold *miner*
does not — its shares move on drilling results and costs. Every one of the 12
misleading names above scores 0.51 or below on that test, where a real fund
scores 0.60 to 0.98.

A third test was needed after that, and only checking the output found it: bond
and cash funds copy nothing, because they barely move at all. So stillness
became the test instead — **the calmest real company on the exchange moves 1.4%
on an average day, and these move 0.1%.** Nothing that quiet is a share.

The whole method was checked against 2,568 companies and 343 funds that the
exchange itself labels today. It mislabels **none** of the companies.

## The headline

| | Old (funds included) | New (funds removed) |
|---|---|---|
| Yearly growth (CAGR) | +7.59% | **+2.42%** |
| Worst fall (maxDD) | 31.0% | **32.5%** |
| Trades | 195 | **193** |
| On Rs 3,00,000 | about Rs 23,000/year | **about Rs 7,300/year** |
| Average edge per trade | 2.15% | **1.07%** |

Again: lower return **and** a slightly deeper hole. Both the wrong way, which
is what removing luck looks like.

## Where the missing profit actually was

Split the history into four equal stretches:

| Stretch | Trades | How many were funds |
|---|---|---|
| Oct 2019 – Jun 2021 | 26 | 0 |
| Jun 2021 – Mar 2023 | 72 | 1 |
| Mar 2023 – Nov 2024 | 53 | 5 |
| **Nov 2024 – Aug 2026** | 44 | **16 — more than a third** |

**All of it is recent.** The last stretch is the gold and silver run of 2025–26,
and the bot was riding it through silver and gold ETFs. Four of those trades hit
the +20% target. Anyone looking at the recent record as proof the strategy was
working was looking at a commodity trade wearing a company's clothes.

## What it did to the past decisions — again

| Decision | After the first fix | After this one | Changed? |
|---|---|---|---|
| Hold 10 days, not 15 | 10 led by 2.27 points | 10 leads by **0.20** | **The gap vanished.** 10 days is kept for its smaller drawdown, nothing else |
| Only buy breakouts | won outright, +7.59% vs −2.20% | **+2.42% vs +2.58%** — it now *costs* a little | **Reverted.** It is a risk rule again: 32.5% worst fall against 43.0% |
| 3 micro + 2 small | 2/3 led by 2.34 | 2/3 leads by 4.05 | **No.** Fourth different answer in four settings. That is a coin, not a finding |
| Buy only top ranks | top beat deepest by +6.63%, t = −4.10 | **+5.64%, t = −3.95** | **Survived.** Barely moved |

**Not one rule was changed as a result** — same as last time, and for the same
reason.

## The one genuinely good news item

The headline fell by two thirds. The stock-picking claim did not move:
**−1.18% per step down the rank list became −1.12%.**

That matters more than it looks. If the ETFs had been *creating* the apparent
skill, removing them would have destroyed it. Instead they were inflating the
scoreboard while the picking held up underneath. The thing this project is
actually trying to establish is still standing; the money it claimed to make
was never real.

## The uncomfortable part, again

| | Original | After fix 1 | **After fix 2** |
|---|---|---|---|
| Average edge per trade | 3.07% | 2.15% | **1.07%** |
| Trades needed to prove it | about 105 | about 213 | **859** |
| Time at this trading pace | about 1.5 years | about 3.0 years | **about 30 years** |

Halving the edge roughly quadruples the evidence needed. **30 years is not a
plan** — it is the arithmetic saying that a book this small, trading this
rarely, cannot prove an edge this size by waiting. That is worth knowing now
rather than in ten years.

## What was NOT done

**The recorded number was left alone.** `data/breakout/baseline.json` still says
+7.59%, and the audit fails on the difference every time it runs. That is
deliberate: overwriting it is a decision for the operator, taken knowingly, not
a side effect of a bug fix.

*Added later: it was re-recorded three days afterwards, on 2026-08-23, as its own
deliberate step — +2.18% / 32.5% / 194 trades. It has since moved again; see part
three. This paragraph describes 20 August and is left as written.*


---

# Part three — buying at a price printed months later (2026-09-11)

---

## The one-line version

**Three trades in seven years were bought at a price that had not been printed
yet on the morning they claim to have been bought. Removing them costs 0.40
percentage points, and the useful part of this story is how SMALL that is.**

---

## What was broken

A stock does not necessarily trade every day the market is open. It can be
suspended, it can be too thinly traded to print at all, it can be delisted and
come back years later.

The simulator bought at "the next available bar" for that stock, but wrote down
"bought tomorrow morning" in the trade record. While the stock trades every day,
those are the same thing. When there is a hole, they are not.

The worst case in the ledger is **MBAPL**. The signal fired on 27 May 2022. The
trade is recorded as bought on 30 May 2022. The price it was actually bought at
was the opening price of **6 February 2023** — eight months later — and that
price was 7.7% BELOW the closing price the signal was built on. Nobody could
have bought at that price on 30 May 2022, because it did not exist yet.

A third case is the same fault at its limit: **VENUSREM** signalled on 12 June
2026 and never printed another bar. The old code skipped it and quietly bought
the next name down the ranked list instead — a name the ranking itself says is
worse.

---

## The headline

| | CAGR | worst fall | trades |
|---|---|---|---|
| before: the stock's next available price | +1.91% | 32.5% | 195 |
| **after: tomorrow's price, or no purchase** | **+1.51%** | **35.3%** | **196** |

The trade count goes UP, which sounds backwards for a rule that only refuses
purchases. It is not: refusing one purchase frees the money for a later one.

---

## How rare this actually is

Two numbers, and confusing them would badly overstate this:

- **0.17%** of all the day-to-day price steps in the database skip at least one
  market day. Some skip years — one stock skips 1,452 market days.
- **Three** of them were ever reached by an actual purchase, and **two** of those
  became completed trades — 1% of 195.

The bucket only ever buys the top five of a ranked list, and a stock liquid
enough to be on that list rarely stops trading. So the flaw is everywhere in the
data and almost nowhere in the results.

---

## Why this one is reassuring rather than alarming

Parts one and two each removed a large slice of the recorded profit, and it
would be reasonable to read those and conclude that every fill assumption in
this project is hiding a disaster. This is the third one checked the same way,
and it is worth 0.40 points.

**The method is what carried over, not the size.** All three were found by
reading what the code assumes about how a purchase happens — never by a
statistical test. No statistical test could have found any of them: the
difference between the right answer and the wrong one here is smaller than the
noise in 195 trades, and it would be smaller than the noise even if it were
completely wrong.

---

## What was done about the recorded number

Unlike part two, the recorded number **was** updated, deliberately and as its own
step: `data/breakout/baseline.json` now reads **+1.51% / 35.3% / 196 trades**.

Worth noting: the audit would NOT have forced this. Fourteen new trading days had
been added since the last recording, and the audit allows the number to drift
when the data grows. The stale figure would have passed every day, indefinitely.
Re-recording was a decision, not something a failing check demanded.

---

## What still has not been established

**A backtest cannot prove this works.** It can only show that the rules were
not obviously broken on history that has already happened.

Only real forward picks can prove it, and that count is still **zero**. The
status report is built so that no number of good simulations can ever print a
YES — because no number of good simulations should.

---

# Part four — the two guards that cannot fire (2026-09-11)

---

## The one-line version

**Two more places in the fill path had the same flaw as part three, both were
fixed, and neither changed a single number — because on this data neither can
ever happen.** The fix is worth having anyway; it is the one that catches the
next corpus.

---

## What was broken

Part three fixed one of three ways the simulator could refuse to buy a share.
The other two still did the wrong thing with the seat: instead of leaving the
money in cash, they reached further down the ranked list and bought the next
name instead.

That is the same mistake part three fixed. At the moment the decision is made,
the bot cannot know that tomorrow's purchase will fail — so picking a
substitute *today* uses information it will not have until *tomorrow*. And the
substitute is a name the ranking already says is worse, by about 1.08% per step
down the list.

---

## The headline

**Nothing moved. At all.**

| | CAGR | worst drop | trades |
|---|---|---|---|
| before | +1.51% | 35.3% | 196 |
| after | **+1.51%** | **35.3%** | **196** |

That was **written down as the prediction before the test was run**, for two
reasons that could each be checked independently: one of the two conditions has
never once occurred in 2,818,047 daily price bars, and the other is switched off
in every live path. A prediction of exactly zero is the strongest kind available
— a single changed digit would have refuted it.

No re-recording was needed.

---

## The thing that was actually learned

The bucket picks five shares, and the ranked shortlist it picks from is also
five names long. **So when the bucket is empty there is no sixth name to fall
back to, and the flaw could not express itself even in principle.** It can only
bite when the bucket is partly full — fewer free seats than leftover candidates.

That explains something part three could only observe: its own "what if we let
it substitute" test came out identical, and this is the reason it had to.

---

## What going looking for it turned up

Re-running a neighbouring test to check this one exposed three numbers that part
three had left wrong. The one that matters:

**The project had been overstating how long it must wait before its results mean
anything.** The average gain per trade is used to work out how many trades are
needed before the number can be trusted. That figure had not been updated after
part three, and the relationship is not proportional — a 26% overstatement in
the gain becomes a 58% overstatement in the wait.

| | gain per trade | trades needed | at ~29 trades a year |
|---|---|---|---|
| what it said | 1.07% | 859 | ~30 years |
| **what is true** | **0.85%** | **1,362** | **~47 years** |

The code's own comment says overstating this "flatters the project twice — once
on the return, and once on how soon anyone could know." It had been doing
exactly that.

---

# All four, side by side

Four defects in the fill path, found across three weeks. Each one recorded a
purchase the market could not actually have supplied.

| | defect | CAGR before | CAGR after | cost | slope before | slope after |
|---|---|---|---|---|---|---|
| **L58** | circuit-locked bars filled (`high == low`) | +14.14% | +7.59% | **−6.55** | −0.90 (n=1,068) | −1.18 (n=1,015) |
| **L69** | delisted funds inside the equity universe | +7.59% | +2.42% | **−5.17** | −1.18 (n=1,015) | −1.12 (n=1,062) |
| **L98** | bought at the symbol's next PRINT, not the next SESSION | +1.91% | +1.51% | **−0.40** | −1.13 (n=1,078) | −1.08 (n=1,089) |
| **L99** | two refusals reached down the list instead of holding cash | +1.51% | +1.51% | **0.00** | −1.08 | −1.08 |

**The recorded level fell 12.63 points in total, from +14.14% to +1.51%** — of
which **12.12 is the four corrections** and 0.51 is ordinary movement that was
nobody's mistake: 0.24 when the baseline was re-recorded on 2026-08-23, and 0.27
of drift from fourteen new trading sessions. L98's row is quoted against its own
control (+1.91%) for that reason; measured against the *recorded* baseline of
the day it reads −0.67, and that version double-counts the drift.

**The slope moved 0.18 points across all four, against a standard error of
0.28.** Less than one standard error, over corrections that removed seven eighths
of the recorded return.

L99's slope is unchanged **by construction rather than by re-measurement**:
neither of its two conditions can occur in any cohort, so no cohort's trades can
differ. Every other row was measured.

## What that shape means

The rank-depth slope is the one claim that survived all four. Each time, the
*level* moved by far more than the *signal* did — which is what a real edge with
inflated measurement on top of it looks like. A phantom signal degrades when you
remove the phantom fills; this one did not.

That is the useful summary, and it is the reason to keep the individual lessons
rather than only the total: each one tells its own story, and the pattern only
appears when they are read side by side.

The current slope lives in `data/breakout/rank_slope_baseline.json` with a batch
tag, and `pipeline.py` reads it through `analysis.load_rank_slope()` rather than
carrying a copy. The selftest fixtures deliberately use a frozen literal and say
so — a test wired to a live measurement fails whenever the measurement moves,
for reasons that have nothing to do with what it protects.
