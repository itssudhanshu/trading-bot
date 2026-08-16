# Bucket Book

Generated 2026-08-16 18:33 · selection as of **2026-08-14**

---

## 1. The clusters

Stocks are split into three clusters by **median daily turnover** over a
trailing 250 sessions — not market cap. True float-adjusted cap needs a
share-count history this corpus does not have, and turnover is what actually
decides whether an order fills.

| cluster | stocks | median turnover band |
|---|---|---|
| **micro** | 629 | ₹0.3L – ₹14.4Cr |
| **small** | 629 | ₹44.9L – ₹56.7Cr |
| **mid** | 629 | ₹527.7L – ₹2,437.1Cr |

## 2. How a stock is scored

Within its own cluster each stock gets a percentile rank on four features,
combined into one weighted score. Ranks are **within cluster** — an 85th
percentile on delivery means among names of comparable turnover, not against
the whole market.

| feature | weight | what it means |
|---|---|---|
| `rs` | 1.00 | relative strength vs the market |
| `deliv` | 1.50 | delivery % — shares actually taken, not day-traded |
| `liq` | 1.00 | liquidity within the cluster |
| `near_high` | 1.00 | how close to its recent high |

`deliv` carries the extra weight because it is the one feature that survived
being measured on randomly-sampled trades (+1.22%) rather than on trades it
had itself helped select (−0.91%). The other three carry no reliable signal
once that bias is removed.

**One hard gate:** a stock below its 200-day moving average is *excluded*, not
scored down. No momentum rank can buy its way past a downtrend.

## 3. Entry logic

Current trigger: **`breakout`** — Confirmation: close takes out the prior 20-day high.

The score says *what* to buy. The trigger says *whether today is the day*.
Until recently there was no trigger at all: the book bought the top-ranked
name at the next open, unconditionally. Seven triggers were tested; `breakout`
was adopted because it costs ~1 point of CAGR but improves the worst
half-year block from −120.5% to −83.1%.

Order of operations matters and was measured:

1. Rank every stock inside its cluster
2. Take the top *k* per cluster (2 micro / 2 small / 1 mid), interleaved
3. **Then** require the trigger — drop what has not triggered
4. Hold cash for the slots that stay empty

Filtering before step 2 instead of after made the book reach further down the
ranking to fill five slots, buying worse names because they happened to
trigger: +7.48% against +11.45%. Rank first, trigger second, cash third.

Entry price is the **next session's open** after the signal — never the
signal day's close, which could not have been traded.

Exits: stop −10%, target +20%, or 15 trading days, whichever comes first. Gaps fill at the
open, which is worse than the stop on a gap down and better than the target on
a gap up.

## 4. Sizing and cash

- Capital **₹500,000**
- Deploy at most **60%** → ₹300,000 across 5 slots = ₹60,000 per name
- Total open risk at a 10% stop: **6%** of capital
- The rest stays in cash. A fully-invested book cannot add when a better setup
  appears, and has no buffer when correlated names gap together.

## 5. What is in the book right now (2026-08-14)

| stock | cluster | value | stop | target | why it was picked |
|---|---|---|---|---|---|
| **HAPPYFORGE** | small | ₹58,509 | 1880.6 | 2507.5 | top-30% in near its high, relative strength, liquidity, delivery % (above 200-DMA, else excluded) |

Deployed **₹58,509** of ₹500,000 (11.7%) — cash **₹441,491**

## 6. Ranked candidates that did NOT make it

| stock | cluster | triggered? | why |
|---|---|---|---|
| APOLLOHOSP | mid | no | top-30% in delivery %, near its high, liquidity, relative strength (above 200-DMA, else excluded) |
| DIVISLAB | mid | no | top-30% in delivery %, near its high, liquidity, relative strength (above 200-DMA, else excluded) |
| NESTLEIND | mid | no | top-30% in delivery %, liquidity, near its high (above 200-DMA, else excluded) |
| TITAN | mid | no | top-30% in near its high, liquidity, delivery %, relative strength (above 200-DMA, else excluded) |
| HCG | small | no | top-30% in delivery %, near its high, liquidity, relative strength (above 200-DMA, else excluded) |
| TAKE | micro | no | top-30% in near its high, delivery %, relative strength (above 200-DMA, else excluded) |
| SUNDRMFAST | small | no | top-30% in delivery %, near its high, relative strength (above 200-DMA, else excluded) |
| PIDILITIND | mid | no | top-30% in near its high, delivery %, liquidity (above 200-DMA, else excluded) |

---

_Costs modelled: brokerage, STT both sides, exchange txn, SEBI turnover, GST,
stamp duty, DP charges per sell, plus 20% STCG per financial year with losses
offset. There is no TDS on resident equity delivery._
