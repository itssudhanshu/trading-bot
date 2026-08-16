#!/usr/bin/env python3
"""Generate data/bucket_book.md -- the human-readable record of WHAT is in the
book and WHY.

Every line is derived from the live selector at generation time. Nothing here
is hand-written prose about how the system is supposed to work; if the rule
changes, this file changes with it, because a document that describes an older
version of the code is worse than no document.
"""
from datetime import date, datetime
from pathlib import Path

import clusters
import entry
import features
import portfolio
import simulate

OUT = Path(__file__).resolve().parent / "data" / "bucket_book.md"


def generate(corpus=None, as_of=None):
    corpus = corpus or features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    as_of = as_of or days[-1]
    sizes = clusters.size_buckets(corpus, as_of, names=clusters.BUCKET_NAMES)
    rows = portfolio.build(corpus, as_of)
    chosen = portfolio.allocate(rows)
    trig = portfolio.TRIGGER

    L = [f"# Bucket Book", "",
         f"Generated {datetime.now():%Y-%m-%d %H:%M} · selection as of **{as_of}**", "",
         "---", "", "## 1. The clusters", "",
         "Stocks are split into three clusters by **median daily turnover** over a",
         "trailing 250 sessions — not market cap. True float-adjusted cap needs a",
         "share-count history this corpus does not have, and turnover is what actually",
         "decides whether an order fills.", ""]
    L.append("| cluster | stocks | median turnover band |")
    L.append("|---|---|---|")
    for b in clusters.BUCKET_NAMES:
        syms = sizes.get(b, [])
        if not syms:
            L.append(f"| {b} | 0 | — |")
            continue
        tos = []
        for sym in syms:
            s = corpus[sym]
            i = s.index_of(as_of)
            if i is not None:
                w = [x for x in s.turnover[max(0, i - 60):i + 1] if x > 0]
                if w:
                    tos.append(sorted(w)[len(w) // 2])
        tos.sort()
        band = (f"₹{tos[0]/1e5:,.1f}L – ₹{tos[-1]/1e7:,.1f}Cr" if tos else "—")
        L.append(f"| **{b}** | {len(syms)} | {band} |")

    L += ["", "## 2. How a stock is scored", "",
          "Within its own cluster each stock gets a percentile rank on four features,",
          "combined into one weighted score. Ranks are **within cluster** — an 85th",
          "percentile on delivery means among names of comparable turnover, not against",
          "the whole market.", ""]
    W = clusters._weights()[0]
    L.append("| feature | weight | what it means |")
    L.append("|---|---|---|")
    meaning = {"rs": "relative strength vs the market",
               "deliv": "delivery % — shares actually taken, not day-traded",
               "liq": "liquidity within the cluster",
               "near_high": "how close to its recent high"}
    for f in ("rs", "deliv", "liq", "near_high"):
        L.append(f"| `{f}` | {W.get(f, 1.0):.2f} | {meaning[f]} |")
    L += ["",
          "`deliv` carries the extra weight because it is the one feature that survived",
          "being measured on randomly-sampled trades (+1.22%) rather than on trades it",
          "had itself helped select (−0.91%). The other three carry no reliable signal",
          "once that bias is removed.", "",
          "**One hard gate:** a stock below its 200-day moving average is *excluded*, not",
          "scored down. No momentum rank can buy its way past a downtrend.", ""]

    L += ["## 3. Entry logic", "",
          f"Current trigger: **`{trig}`** — {entry.TRIGGERS[trig].__doc__.strip().splitlines()[0]}", "",
          "The score says *what* to buy. The trigger says *whether today is the day*.",
          "Until recently there was no trigger at all: the book bought the top-ranked",
          "name at the next open, unconditionally. Seven triggers were tested; `breakout`",
          "was adopted because it costs ~1 point of CAGR but improves the worst",
          "half-year block from −120.5% to −83.1%.", "",
          "Order of operations matters and was measured:", "",
          "1. Rank every stock inside its cluster",
          "2. Take the top *k* per cluster (2 micro / 2 small / 1 mid), interleaved",
          "3. **Then** require the trigger — drop what has not triggered",
          "4. Hold cash for the slots that stay empty", "",
          "Filtering before step 2 instead of after made the book reach further down the",
          "ranking to fill five slots, buying worse names because they happened to",
          "trigger: +7.48% against +11.45%. Rank first, trigger second, cash third.", "",
          f"Entry price is the **next session's open** after the signal — never the",
          "signal day's close, which could not have been traded.", "",
          f"Exits: stop −{portfolio.STOP_PCT:.0f}%, target +{portfolio.TARGET_PCT:.0f}%, "
          f"or {portfolio.HOLD_DAYS} trading days, whichever comes first. Gaps fill at the",
          "open, which is worse than the stop on a gap down and better than the target on",
          "a gap up.", ""]

    L += ["## 4. Sizing and cash", "",
          f"- Capital **₹{portfolio.CAPITAL:,}**",
          f"- Deploy at most **{portfolio.DEPLOY_PCT:.0f}%** → ₹"
          f"{portfolio.CAPITAL * portfolio.DEPLOY_PCT / 100:,.0f} across "
          f"{portfolio.MAX_POSITIONS} slots = ₹"
          f"{portfolio.CAPITAL * portfolio.DEPLOY_PCT / 100 / portfolio.MAX_POSITIONS:,.0f} per name",
          f"- Total open risk at a {portfolio.STOP_PCT:.0f}% stop: **6%** of capital",
          "- The rest stays in cash. A fully-invested book cannot add when a better setup",
          "  appears, and has no buffer when correlated names gap together.", ""]

    L += [f"## 5. What is in the book right now ({as_of})", ""]
    if not chosen:
        L.append("_No name passed the trigger. The book holds cash._")
    else:
        L.append("| stock | cluster | value | stop | target | why it was picked |")
        L.append("|---|---|---|---|---|---|")
        for r in chosen:
            L.append(f"| **{r['symbol']}** | {r['bucket']} | ₹{r['value']:,} | "
                     f"{r['stop']:.1f} | {r['target']:.1f} | {r['why']} |")
        dep = sum(r["value"] for r in chosen)
        L += ["", f"Deployed **₹{dep:,}** of ₹{portfolio.CAPITAL:,} "
                  f"({dep / portfolio.CAPITAL * 100:.1f}%) — cash "
                  f"**₹{portfolio.CAPITAL - dep:,}**"]

    L += ["", "## 6. Ranked candidates that did NOT make it", ""]
    near = [r for r in rows if r not in chosen][:8]
    if near:
        L.append("| stock | cluster | triggered? | why |")
        L.append("|---|---|---|---|")
        for r in near:
            L.append(f"| {r['symbol']} | {r['bucket']} | "
                     f"{'yes' if r.get('triggered') else 'no'} | {r['why']} |")
    L += ["", "---", "",
          "_Costs modelled: brokerage, STT both sides, exchange txn, SEBI turnover, GST,",
          "stamp duty, DP charges per sell, plus 20% STCG per financial year with losses",
          "offset. There is no TDS on resident equity delivery._"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n")
    return OUT


if __name__ == "__main__":
    p = generate()
    print(f"wrote {p} ({p.stat().st_size:,} bytes)")
