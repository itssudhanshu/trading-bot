---
name: sentiment
description: Use when asked how a stock is being talked about — sentiment, news, headlines, "what's the mood on X", or a sentiment check on today's picks. Reads this repo's own announcement and news archives, scores them, and reports per stock. A live view only; never an input to a measured result.
---

# Sentiment on a stock

Adapted from `sentiment-analysis` in
[tradeinsight-info/investment-analysis-skills](https://github.com/tradeinsight-info/investment-analysis-skills).
Their scoring rubric, signal bands and report shape are borrowed almost intact.
What changed is the data, and it had to change: their three channels are
NewsAPI, StockTwits and r/wallstreetbets, which are the right three for a US
large cap and carry essentially nothing on the NSE microcaps this book trades.
StockTwits returns 404 for every NSE symbol, RELIANCE included. An absent
channel scored as neutral reads as "no view" while meaning "no data", and those
are different facts.

## The sources, and what each is worth

Three channels feed this. Their weight in the answer is not equal and should not
be treated as equal.

**1 · What the company told the exchange** — `announcements.py`.
1,019,495 NSE corporate filings back to 2019, timestamped to the second, across
**2,640 symbols**. Complete, legally compelled, and already point-in-time: the
15:30 rule means a filing made at 22:56 does not count for the day it was made,
and 60% of them arrive after the close. **For a microcap this is the best channel
by a distance** and nothing else comes close. 23 categories carry a signed tone;
three (`Price movement`, `Spurt in Volume`, `News Verification`) are the exchange
demanding an explanation rather than the company volunteering one.

**2 · General market feeds** — five RSS feeds read directly with the standard
library: Economic Times (markets, stocks, IPO) and Mint (markets, companies).
They are market-wide. **They almost never name a microcap**, and most of what
they do produce is a wrap that lists a stock in passing — which scores 0.0.

**3 · Per-company search** — one query per candidate, through an aggregator whose
robots.txt permits it. This exists because channel 2 matched **zero** headlines
to a microcap on day one. It reaches the publishers that block us directly:
Moneycontrol, Business Standard, Financial Express, CNBCTV18, News18, Business
Today, NDTV Profit, The Hindu. Items from it carry the symbol whose query found
them, so attribution is exact rather than matched by name.

**The mix is lopsided and you should know it.** Within the NEWS archive --
channel 1 is a separate corpus and not counted here -- roughly **94% comes from
channel 2** (Economic Times and Mint) and only ~6% from the per-company channel.
So a stock with several channel-3 headlines has genuinely been written about; a
stock appearing only in channel 2 has probably just been listed in a market
wrap, and that is a 0.0 rather than coverage.

A browser fetcher (`crawl`) is available for pages that refuse a plain client.
No source currently needs it — the three Moneycontrol RSS feeds were tried and
turned out to be 850 days stale, so the block was never what stood in the way.

## The rule that comes before the steps

**Nothing this skill produces may feed a measured result.** Not a weight, not a
score, not a filter. Two reasons, both hard:

- The news channel has no history. It begins the day `newswatch` first ran, so
  any backtest reading it would be reading the future.
- The scoring below is a model's judgement. Ask twice, get two numbers. This
  repo's audit fails when a recorded number moves, and rightly.

sentiment already has the measured version: `ann_tone` in `clusters.py`, a frozen
category→sign table, deterministic, and currently switched off because it read
t = 1.71 against a bar of 2.6. **This skill is the operator's view of today. It
is not evidence and must never be quoted as any.**

## Step 1 — Get the evidence

Never assemble this by hand and never fetch it live. One command:

```bash
python3 src/ops/sentiment.py SYMBOL
```

For today's candidates:

```bash
STRATEGY=sentiment python3 src/ops/sentiment.py --picks
```

That script decides only **what was visible**, which is the half that must be
reproducible — it applies the 15:30 visibility rule, so an announcement filed at
22:56 does not count for the day it was filed. 60% of them arrive after the
close, so this is not a detail.

If the symbol is unknown to the equity master, say so and stop. Do not guess a
company name.

## Step 2 — Score the announcements

Each announcement gets −1.0 to +1.0. Score the category and the summary text
**together**: a benign category with an alarming summary scores below what the
category alone suggests.

| score | what it looks like |
|---|---|
| +0.8 to +1.0 | order won, buyback, open offer, bonus, capacity commissioned, debt cleared |
| +0.4 to +0.7 | dividend, positive results with growth, upgrade, new customer, expansion approved |
| 0.0 | procedural filing — trading window, newspaper publication, RTA update, routine AGM paperwork, "Updates" with no substance |
| −0.4 to −0.7 | resignation, director change under no clear cause, delayed filing, rating watch, weak results |
| −0.8 to −1.0 | insolvency proceedings, payment default, auditor resignation or qualification, fraud allegation, plant shutdown, trading suspension |

**Most announcements are 0.0 and that is correct.** "Updates" is the single
largest category in the corpus and usually means nothing. Scoring procedural
filings as mildly positive because they exist is how a sentiment score becomes a
filing-frequency score — and filing frequency was measured on 3,847 trades and
carries nothing (t = +1.19).

## Step 3 — Score the news headlines

Same −1.0 to +1.0 scale, using the source skill's bands:

- **+0.8 to +1.0** — record results, major upgrade, regulatory approval, significant partnership
- **+0.4 to +0.7** — positive guidance, target raise, solid quarter, share gains
- **0.0** — neutral mentions, routine coverage, a market wrap that merely lists the name
- **−0.4 to −0.7** — missed estimates, downgrade, minor regulatory concern
- **−0.8 to −1.0** — investigation, fraud allegation, severe miss, exit under fire

A market-wide wrap that happens to name the stock is **0.0**, not positive.

Two more that look like news and are not, both seen in real captures:

- **A quote or profile page** — "Yuken India Share Price", "Yuken India Ltd
  YUKEN". These are filtered out before they reach you; if one survives, score
  it 0.0 and say the filter missed it.
- **A broker target repeated across outlets** — "Buy X; target of Rs 2200: ICICI
  Securities" carried by three sites is *one* opinion, not three. Score it once
  and note the duplication, rather than letting repetition inflate the mean.

## Step 4 — Channel scores

The scoring is done for you, deterministically:

```bash
STRATEGY=sentiment python3 src/ops/sentiment.py --table
```

Each item scores in [−1, +1] — the frozen category table leading and a finance
lexicon adjusting, with negation handled ("not profitable" is negative). A
channel then aggregates as

```
score = 10 × (sum of items that carried signal) / (2 + how many there were)
```

**Not the plain mean, and the reason matters.** Averaging every item was tried
and made all ten candidates Neutral between +0.00 and +2.50, because ~90% of
filings are procedural and correctly score 0, so one insolvency notice among
thirteen became a thirteenth of a signal. A filing that says nothing is an
absence of observation, not an observation of neutrality. The `+2` makes the far
bands require agreement across several items: one item reaches +3.3, three reach
+6.0, six reach +7.5, and no single filing can produce a Very Bullish.

A channel with no items that carried signal has **no score** — not zero. Report
it as "no data".

## Step 5 — Composite

Announcements carry more weight than headlines here, because they are complete,
exchange-verified and legally compelled, while ~94% of the news archive is
market-wide coverage that mentions a microcap only in passing.

| available | announcements | news |
|---|---|---|
| both | 0.75 | 0.25 |
| announcements only | 1.00 | — |
| news only | — | 1.00 |
| neither | no composite |

**These weights are a judgement, not a measurement.** Nothing in this repo has
tested them and they must be described that way whenever the composite is shown.
Always print both channel scores beside the composite so the reader can ignore
the weighting entirely.

## Step 6 — Bands, and the base rate that decides how to read them

- **≥ +7.0** — Very Bullish
- **+3.0 to +7.0** — Bullish
- **−3.0 to +3.0** — Neutral
- **−7.0 to −3.0** — Bearish
- **≤ −7.0** — Very Bearish

**Bullish is close to the base rate here, and that changes what it means.**
Measured across ten candidates on one day: 18 scored items positive against
**2** negative — 90% positive — from a lexicon with 109 positive and 112
negative words that detects negatives correctly when they exist. The skew is in
the world, not the scale: companies announce good news and the coverage follows
them.

So read the labels asymmetrically:

- **Bullish** — the normal state. It means "nothing is wrong", not "something
  is right". Do not report it as a finding.
- **Neutral** — usually means *nothing said anything*, not that opinion was
  balanced. Check the signal count before reading anything into it.
- **Bearish or Very Bearish** — **rare, and therefore the informative case.**
  Two negative items in a sample of twenty is the level at which one is worth
  looking at directly. Always name the filing or headline driving it.

Note also that ~87 of 91 filings in that sample scored **silent** — procedural
paperwork that correctly says nothing. That is normal and is why the aggregation
ignores zeros rather than averaging them in.



## Step 7 — Report

Plain words, no jargon — a friend who has never bought a share must be able to
read it (`docs/rules.md` R2). Say "what the company told the exchange", not
"channel one".

```
## {SYMBOL} — {Company Name}
*What the company said, and what the papers said · as of {date}*

**{composite} / 10 — {band}**

| where it came from | score | how much | notes |
|---|---|---|---|
| Company told the exchange | {score} | {n} filings in 30 days | {n} of them procedural |
| Newspapers and market sites | {score or "no data"} | {n} headlines in 7 days | {archive note} |

*The two numbers above are the useful ones. The single score at the top blends
them 75/25, and that split has never been tested.*

### What actually happened
- [{score}] {date} — {plain description of the filing or headline}
- ...

### Worth knowing
{Anything a reader should not miss: no filings at all, an archive that does not
reach back far enough, a single filing driving the whole score.}
```

## Step 8 — Say when you cannot say

State these plainly rather than producing a confident number over thin air:

- **Fewer than 3 filings and no headlines** — "not enough to form a view".
- **The news archive starts after the date asked about** — the channel is
  *absent*, not quiet. Never let a reader infer silence means calm.
- **One filing driving the score** — say which one. A −0.9 insolvency notice in
  an otherwise empty month is a real signal; a single +0.5 dividend is not a
  mood.
- **A microcap with no filings at all** — normal, and explicitly **not** bad
  news. Microcaps file far less often than small caps; treating silence as
  negative would just be measuring size again.

## What to do with the answer

Nothing automatic. This informs a person looking at today's list. It does not
change a weight, does not filter a pick, and does not get written into any
ledger. If a sentiment input is ever to affect the bucket, it goes through
`skills/experiment` — pre-registered, with a control and an error bar — and it
uses the deterministic feature, not this.
