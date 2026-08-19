# What changed, and why the numbers got worse

Plain-language record of the correction made on 2026-08-19. If you only read
one page about this project's honesty, read this one.

Terms used here are defined in `docs/glossary.md`. Evidence and workings are in
`docs/lessons.md` (L58, L59).

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

## What still has not been established

**A backtest cannot prove this works.** It can only show that the rules were
not obviously broken on history that has already happened.

Only real forward picks can prove it, and that count is still **zero**. The
status report is built so that no number of good simulations can ever print a
YES — because no number of good simulations should.
