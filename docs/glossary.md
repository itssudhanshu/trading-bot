# Glossary — every term this project uses, in plain English

No trading background needed. If a word appears in a Telegram message, a
report, or a code comment and you had to guess what it meant, it belongs here.

Written 2026-08-19. Terms are grouped by what they are *for*, not
alphabetically, because that is how you will need them.

---

## 1. Money and performance

| Term | Plain English |
|---|---|
| **CAGR** | The yearly growth rate, smoothed out. "+7.59%" means the money grew as if it gained 7.59% every year. It hides the bumpy ride completely. |
| **maxDD** / **DD** | *Maximum drawdown.* The worst fall from a high point before recovering. 31% on Rs 3,00,000 means at the ugliest moment you were down about Rs 93,000. |
| **n** | How many trades a number is based on. `n=195` means 195 complete buy-and-sell round trips. A small `n` means weak proof, whatever the number says. |
| **win %** | The share of trades that made money. 47% means fewer than half won — which is fine, *if* the winners are bigger than the losers. |
| **per trade** / **edge** | The average profit of a single trade, in percent. This is the real engine. CAGR is just this, repeated and compounded. |
| **worst block** | The worst six-month stretch, added up. Answers "how bad does a bad patch get?", which an average cannot. |
| **trade** / **round trip** | One buy plus its matching sell. Not two events — one. |
| **capital** | The money the bucket is allowed to use: Rs 3,00,000. |
| **deployed** | How much of the capital is actually in stocks right now. Capped at 75%, so at most Rs 2,25,000 — the rest stays cash on purpose. |
| **open risk** | The most the bucket could lose if every stop hit at once. About 7.5% with all five stocks held. |

## 2. Proof, and the difference between a number and evidence

This is the section that matters most in this project. Almost every mistake
here has been a number that was real arithmetic and still not evidence.

| Term | Plain English |
|---|---|
| **std err** (standard error) | The "give or take" on a number. `+2.15% ± 1.08%` means the true value is probably somewhere between about +0.1% and +4.3%. A number without this is half a number. |
| **t** | Signal divided by noise. Under 2 means "this could easily be luck". Over 2 means "probably real". Nearly every setting in this project scores under 0.5. |
| **RESOLVED** / **inside the noise** | "Inside the noise" = we genuinely cannot tell this apart from luck. "Resolved" = we can. The words are used literally, never as hedging. |
| **sd** (standard deviation) | How wildly individual trades scatter around the average. Here it is about 16%, which is enormous — and that is *why* proving anything needs roughly 200+ trades. |
| **gap vs live** | The difference between a test version and the version actually running. "+0.55% per trade" means the test earned half a percent more per trade than the live rules. |
| **control** / **neutral** | The deliberately boring version you compare against. For score weights, "neutral" means all four ingredients counted equally. |
| **backtest** | Replaying old price history to see what the rules *would* have done. Cheap, fast, and very easy to fool yourself with. |
| **forward paper trade** | Picking stocks today with no real money, then grading them weeks later. Slow, unglamorous, and the only thing that actually proves anything. Count so far: **zero**. |
| **monotone** | Moves in one steady direction — more of X gives more return, at every step. Very tempting. Often just a lucky path. |
| **non-monotonic** | Jumps around with no pattern (the middle setting best, both ends worse). A reliable signature of noise. |
| **promotion bar** | The internal test a variant must pass before it is even allowed to be called a "candidate". Passing it does not make it live. |
| **candidate** | A configuration that survived a backtest. A hypothesis, not a strategy. |
| **pre-registered** | The list of variants was written down *before* running them, so you cannot quietly drop the ones that looked bad. |
| **batch tag** | A label like `20260819-postlock` stamped on results, so you can tell which engine version produced a number. |

## 3. This project's own vocabulary

These have exact meanings here and must not be swapped for synonyms. Using
three words for one thing has already caused a wrong build.

| Term | Plain English |
|---|---|
| **bucket** | The five stocks held, and their combined profit and loss. There is exactly one bucket. Never call it a portfolio, a book, or holdings. |
| **cluster** | A size band of companies, by how much money trades in them daily. **micro** = smaller, **small** = a bit bigger. The largest third of the market is never traded at all. A cluster is not a bucket. |
| **rank** | Position in the score-sorted list within one cluster. Rank 1 = best. |
| **score** | A 0–100 number combining four measurements. Used only to sort stocks against others of similar size — it cannot say whether a stock is good in absolute terms. |
| **position** / **stock** | One holding. Never "slot". |
| **cohort** | A *group of ranks* tested together: cohort 0 = ranks 1–2, cohort 1 = ranks 3–4, and so on. Used to ask "does buying further down the list do worse?" (It does.) |
| **point-in-time** | Only using information that existed on the day being simulated. Prevents the commonest self-deception in backtesting: accidentally using tomorrow's news. |

## 4. What goes into the score

| Term | Plain English |
|---|---|
| **rs** | *Relative strength* — how much the price rose over the last six months. |
| **deliv** | *Delivery percentage* — out of all shares traded, how many were actually **kept** (paid for and taken home) rather than flipped the same day. High delivery means real investors are buying, not day-traders churning. |
| **near_high** | How close the price is to its recent high. Closer is better. |
| **liq** | *Liquidity* — how much money trades in the stock on a normal day. Higher means easier to get in and out. |
| **200-DMA gate** | A pass/fail filter, not a score: the price must be above its own 200-day average, or the stock is thrown out entirely. |
| **breakout trigger** | Only buy if the stock has just pushed above its recent trading range. Buying strength, not hope. |
| **weight** | How much one ingredient counts in the score. `deliv 1.5` means delivery counts one and a half times as much as the others. |
| **percentile rank** | "Better than 85% of comparable stocks." Always measured inside one cluster, so scores from different clusters cannot be compared. |

## 5. Exits

| Term | Plain English |
|---|---|
| **stop** | Sell at −10%. Cuts a loss before it becomes a hole. |
| **target** | Sell at +20%. Takes the win rather than hoping for more. |
| **hold** | Sell after 10 trading days regardless. Frees the money for a better idea. |
| **shadow stop** | A stop price recorded and checked by the bot itself, so an exit is not dependent on the broker's order surviving. |
| **fill** | Actually getting the shares. An order is not a fill. |
| **next open** | Orders are placed on the next session's opening price, never the price that produced the signal — you cannot buy at a price you only learned about after the close. |

## 6. Costs and the market fighting back

| Term | Plain English |
|---|---|
| **friction** | Everything that eats the return besides the price move: brokerage, taxes, and your own order moving the price. |
| **market impact** | Your buy order itself pushes the price up against you. A big order in a thinly-traded stock is punished hardest. |
| **c** | The dial controlling how much impact hurts. `c=0` is a fantasy (free trading). `c=1.0` is the standard textbook assumption and what this project uses. `c=3.0` is pessimistic. It cannot be calibrated without real broker fill data, so it is always reported as a **range**, never one number. |
| **ADV** | *Average daily volume* — normal daily trading activity in a stock. Your order size compared to ADV decides how much impact you pay. |
| **participation** | Your order as a share of a stock's normal daily volume. 2% is polite; 20% is shouting. |
| **circuit lock** / **upper lock** | India's exchange caps how far a stock may move in one day. When it slams into the ceiling, buyers queue and **nobody is selling** — you cannot buy at any price. On a chart it shows as a day where the high equals the low. |
| **pre-guard** / **post-guard** | Before and after the fix that stopped the backtest pretending it bought those unbuyable days. **Every "pre-guard" number in this project's history was too good.** |
| **STT / STCG / DP charges** | Indian trading taxes and fees. All are modelled; none are optional. |

## 7. Data and plumbing

| Term | Plain English |
|---|---|
| **corpus** | The full stored price history the simulations read — about 2,491 stocks over 1,698 trading days. |
| **bhavcopy** | The official daily price file NSE publishes. The raw ingredient for everything. |
| **session** / **trading day** | One day the market was open. Weekends and holidays do not count. |
| **universe** | Every stock eligible to be considered before any filtering. |
| **selftest** | A small check inside a file that fails loudly if the logic in that file breaks. Run before trusting anything. |
| **audit** | The 30-check sweep that confirms the whole system still behaves as documented, including that the headline result still reproduces. |
| **baseline** | The recorded headline number. If a code change moves it, the audit fails on purpose, and re-recording must be a deliberate act. |
| **heartbeat** | A timestamp the Telegram bot writes before every check, so "is it alive?" can be answered by evidence instead of by the bot's own opinion. |
| **listener** | The always-on process that answers Telegram commands. |

---

## The one rule this glossary exists to protect

A number is not evidence until you know its **give or take** and which
**engine version** produced it. Everything above is vocabulary for saying that
precisely — see `docs/rules.md` for how results must be worded, and
`docs/performance-change.md` for what happened the last time this rule was
broken.
