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

**Headlines come from the publishers you would expect** — Moneycontrol, Business
Standard, Financial Express, CNBCTV18, Economic Times, Mint — reached through an
aggregator whose robots.txt permits it, because most of those sites either
disallow us or return 403 to a non-browser agent. Each item records which
publisher it came from, and each is tagged with the symbol whose own query
retrieved it, so attribution is exact rather than matched by name.

## The rule that comes before the steps

**Nothing this skill produces may feed a measured result.** Not a weight, not a
score, not a filter. Two reasons, both hard:

- The news channel has no history. It begins the day `newswatch` first ran, so
  any backtest reading it would be reading the future.
- The scoring below is a model's judgement. Ask twice, get two numbers. This
  repo's audit fails when a recorded number moves, and rightly.

thicket already has the measured version: `ann_tone` in `clusters.py`, a frozen
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
STRATEGY=thicket python3 src/ops/sentiment.py --picks
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

```
announcement_score = mean(announcement scores) × 10
news_score         = mean(news scores) × 10
```

A channel with no items has **no score** — not zero. Report it as "no data".

## Step 5 — Composite

Announcements carry more weight than headlines here, because they are complete,
exchange-verified and legally compelled, while the news archive is a general
markets feed that mentions a microcap only occasionally.

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

## Step 6 — Bands

- **≥ +7.0** — very positive
- **+3.0 to +7.0** — positive
- **−3.0 to +3.0** — neutral
- **−7.0 to −3.0** — negative
- **≤ −7.0** — very negative

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
