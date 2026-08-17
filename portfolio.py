#!/usr/bin/env python3
"""Paper portfolio: Rs 5,00,000 across three size clusters.

RULES (fixed here, measured not guessed)
  universe   20 per cluster: micro / small / mid, by median turnover
  selection  composite of 6-month RS + delivery% + liquidity,
             gated on close > its own 200-day average
  entry      next session's OPEN after selection (never the signal bar's close --
             you cannot trade a price that has already printed)
  stop       10% below entry, FIXED
  target     20% above entry
  time exit  15 trading days
  trailing   NONE

Why no trailing stop, despite it being requested: measured across six
configurations at both 3% and 10% stops, every trail LOWERED expectancy. It
lifts win rate (42% -> 48%) by converting losers into smaller losers, while
collapsing target hits from 20% to 8% -- it stops you out of the winners that
pay for everything else. "Guaranteed profit" per trade and positive expectancy
across trades turn out to be opposed here.

Why a 10% stop rather than the 3% asked for: at 3% the stop sits inside these
stocks' daily noise and is hit 70-77% of the time, giving -0.6%/trade. Widening
it is the single change that flips the book positive. Operator agreed 10% now,
3-5% later once entry timing improves -- which is the right order: a tight stop
needs a precise entry to survive.

Sizing: 2% of capital risked per position. With a 10% stop that is a Rs 1,00,000
position per Rs 10,000 risked -- five concurrent, fully invested.
"""
import json
import sys
from datetime import date

import clusters
import features

CAPITAL = 300_000    # the whole paper pocket, not a deployable target
RISK_PCT = 2.0          # of capital, per position
STOP_PCT = 10.0
TARGET_PCT = 20.0
HOLD_DAYS = 15
MAX_POSITIONS = 5

# Cash is a position. A fully-invested book has no capacity to add when a
# better setup appears mid-cycle, and no buffer when several correlated names
# gap down together.
#
# Open risk at a full book is DEPLOY_PCT * STOP_PCT / 100 = 7.5% of capital.
# engine.MAX_PORTFOLIO_HEAT (6%) is NOT a constraint on this path -- it is
# checked only inside engine's own signal function, which nothing here calls.
# An earlier comment cited it as though it bound this book; it never did, and
# quoting a guard that does not run is the failure this project keeps making.
# The real cap is arithmetic: 5 stocks x Rs 45k x 10% stop.
# 75% of Rs 3L over 5 stocks = Rs 45k each. The cap does less than its name
# suggests: average occupancy is 3.09 of 5, so the book is really ~46%
# invested, not 75%. Raising it from 60% was worth +2.7 points of CAGR for
# +4.6 of drawdown, measured, not assumed.
DEPLOY_PCT = 75.0


# How the deployable pot is split across names. Nominal R:R is identical for
# every stock (-10% / +20%), so it cannot differentiate; what does differ is
# how likely that fixed stop is to be hit by noise. A 10% stop on a 6%-daily-vol
# microcap is inside the noise; on a 2%-vol name it is a real signal. Sizing
# down the volatile names equalises what each position actually risks.
SIZING = "equal"


def size_mult(scheme, rank, vol_pct, med_vol_pct):
    """-> multiplier on the base position size. Mean ~1.0 across a full book,
    so total deployment is unchanged and only the SPLIT moves."""
    if scheme == "equal" or not scheme:
        return 1.0
    if scheme == "invvol":
        if not vol_pct or not med_vol_pct or vol_pct <= 0:
            return 1.0
        return max(0.5, min(2.0, med_vol_pct / vol_pct))
    if scheme == "conviction":            # rank 0 largest, decaying
        return max(0.5, min(2.0, 1.6 - 0.3 * rank))
    if scheme == "both":
        return (size_mult("invvol", rank, vol_pct, med_vol_pct)
                * size_mult("conviction", rank, vol_pct, med_vol_pct)) ** 0.5
    raise ValueError(f"unknown sizing scheme {scheme!r}")


def position_size(capital, entry, stop_pct=STOP_PCT, risk_pct=RISK_PCT, mult=1.0):
    """-> (qty, rupees_at_risk). Risk-based, then capped so one name cannot
    exceed its share of the book."""
    risk_rupees = capital * risk_pct / 100
    risk_per_share = entry * stop_pct / 100
    if risk_per_share <= 0:
        return 0, 0.0
    qty = int(risk_rupees / risk_per_share)
    cap_value = capital * DEPLOY_PCT / 100 / MAX_POSITIONS * mult
    qty = min(qty, int(cap_value / entry))
    return qty, qty * risk_per_share


# Percentile bands for prose. A rank is only meaningful against its cluster --
# "85th percentile on delivery" means among names of comparable turnover, not
# against the whole market.
def _why(r):
    """-> plain-language reason this name ranked where it did."""
    if not r:
        return "no rank detail"
    label = {"rs": "relative strength", "deliv": "delivery %",
             "liq": "liquidity", "near_high": "near its high"}
    strong = [label[f] for f, v in sorted(r.items(), key=lambda kv: -kv[1]) if v >= 70]
    weak = [label[f] for f, v in r.items() if v <= 30]
    parts = []
    if strong:
        parts.append("top-30% in " + ", ".join(strong))
    if weak:
        parts.append("weak on " + ", ".join(weak))
    if not parts:
        parts.append("mid-pack on every feature")
    return "; ".join(parts) + " (above 200-DMA, else excluded)"


# The bucket: 3 micro + 2 small = 5 stocks, drawn from the two tradeable
# clusters. Module-level so the generated Bucket Book reads the real mix
# instead of restating it in prose -- it was already describing 2/2/1 one
# minute after the design changed.
# "per_cluster" ranks inside each size band; "pooled" ranks every tradeable
# name against every other. Pooled makes the cluster split cosmetic, since the
# book then takes whatever ranks highest regardless of band.
# "pooled": every tradeable stock is ranked against every other and the book
# takes the best five outright. The size clusters then only decide WHO IS
# ELIGIBLE (the least-liquid 67%), not how the five are split.
#
# Measured against per-cluster 3/2: CAGR +16.61 vs +13.57, CAGR-per-drawdown
# 0.553 vs 0.471 -- but the worst half-year block is -119.4% against -83.6%.
# Better return and better risk-adjusted return, worse tail. Worst-block
# ranking is the one that has generalised in this project, so this is a
# deliberate trade, not a free win.
# "pooled" ranks every eligible stock against every other and takes the best
# five outright; "per_cluster" ranks inside each band and fills a 3/2 quota.
#
# Pooled was tried and REVERTED. It wins on headline return (+16.61% vs
# +13.57%) and on CAGR-per-drawdown (0.553 vs 0.471), and loses on everything
# else: worst half-year -119.4% vs -83.6%, best single symbol 15.4% of all
# gains vs 7.6%, 119 symbols traded vs 136, 2.54 stocks held vs 3.09, and
# -1,588 vs +5,349 replaying the last 30 sessions. Two wins out of seven, both
# of them the measure a best-of-N search inflates by construction.
RANKING = "per_cluster"

TAKE_PER_CLUSTER = {"micro": 3, "small": 2}

# Floor on positions held. When fewer names trigger than this, the shortfall is
# filled from the best-ranked candidates that did NOT trigger -- relaxing the
# timing rule, never reaching deeper down the ranking. Reaching deeper was
# tested and cost 4 points of CAGR.
#
# 0 = no floor. 1 is NOT the same as 0: it forces a position on days when
# nothing triggered, which the book would otherwise sit out. That distinction
# moved the result by 4 points and was nearly missed because the constant was
# mislabelled.
MIN_POSITIONS = 0

TRIGGER = "breakout"    # see trigger_test: near-identical CAGR to no trigger
                        # (+11.45 vs +12.53) but worst block -83.1% vs -120.5%.
                        # Ranked on worst block, which is the ranking that has
                        # generalised here, the control is LAST of seven.


def build(corpus, as_of, capital=CAPITAL, trigger=None):
    """-> list of candidate positions, best-scored first, across clusters.

    `trigger` gates WHETHER to buy today; the score only says what to buy.
    """
    import entry
    fn = entry.TRIGGERS[trigger or TRIGGER]
    # MARK, do not filter. Filtering here would drop untriggered names before
    # ranking, so allocate() would reach further down the list to fill its five
    # slots -- buying a worse name because it happened to trigger. Measured:
    # that variant returns +7.48% / 37.9% DD against +11.45% / 23.8% for
    # marking. Rank first, then require the trigger, then hold cash if the best
    # names are not ready.
    picks = (clusters.pick_pooled(corpus, as_of) if RANKING == "pooled"
             else clusters.pick(corpus, as_of, per_cluster=20))
    ranks = {}
    for b, syms in clusters.size_clusters(corpus, as_of).items():
        ranks.update(clusters.score(corpus, syms, as_of, with_ranks=True)[1])
    rows = []
    for cluster, lst in picks.items():
        for sym, score in lst:
            s = corpus[sym]
            i = s.index_of(as_of)
            if i is None:
                continue
            # Surveillance: ASM, GSM or an F&O ban means the exchange has
            # singled the stock out, usually with tighter price bands or
            # trade-to-trade settlement. universe.py has always computed this
            # and the corpus was dropping it, so a flagged name could be
            # ranked and bought. Only skip when the flag is KNOWN -- backfilled
            # history carries no surveillance lists, and treating unknown as
            # restricted would empty the universe.
            if (i < len(s.restricted) and s.restricted[i]
                    and i < len(s.surveillance_known) and s.surveillance_known[i]):
                continue
            ref = s.close[i]
            qty, risk = position_size(capital, ref)
            if qty < 1:
                continue
            rows.append({
                "cluster": cluster, "symbol": sym, "score": round(score, 1),
                "ref_close": round(ref, 2),
                "entry_rule": "next session open",
                "stop": round(ref * (1 - STOP_PCT / 100), 2),
                "target": round(ref * (1 + TARGET_PCT / 100), 2),
                "qty": qty, "value": round(qty * ref),
                "risk": round(risk),
                "exit_by": f"{HOLD_DAYS} trading days",
                "triggered": bool(fn(s, i)),
                "why": _why(ranks.get(sym, {})),
                "ranks": ranks.get(sym, {}),
            })
    rows.sort(key=lambda r: -r["score"])
    return rows


def _returns(s, i, n=60):
    """-> last n daily returns ending at index i."""
    a, out = max(1, i - n + 1), []
    for k in range(a, i + 1):
        p = s.close[k - 1]
        if p:
            out.append(s.close[k] / p - 1.0)
    return out


def _corr(a, b):
    """Pearson correlation. -> 0.0 when either series is flat or too short."""
    n = min(len(a), len(b))
    if n < 20:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / (va ** 0.5 * vb ** 0.5)


def decorrelate(rows, corpus, as_of, max_corr):
    """Drop a candidate that moves too closely with one already taken.

    The book had no diversification rule beyond cluster counts, and it showed:
    two of five positions were hospital chains. Sector labels do not exist in
    this corpus, but correlation is computable from the price history already
    on disk and captures the same risk without needing a classification.
    Order is preserved -- this only ever removes.
    """
    if not max_corr:
        return rows
    kept, series = [], []
    for r in rows:
        s = corpus.get(r["symbol"])
        i = s.index_of(as_of) if s else None
        if i is None:
            kept.append(r)
            continue
        rr = _returns(s, i)
        if any(abs(_corr(rr, prev)) > max_corr for prev in series):
            continue
        kept.append(r)
        series.append(rr)
    return kept


def allocate(rows, take_per_cluster=None, offset=0):
    """Take the best from EACH cluster, not the best overall.

    Ranking all 60 candidates together and taking the top 5 returned five mid
    caps: liquidity is a scoring component, so the largest cluster wins it
    structurally and the three-cluster design collapses to one. The clusters
    exist to spread exposure across size bands; that only happens if the
    allocation is per band.
    """
    # 2 micro / 3 small / 0 mid. The mid cluster is still COMPUTED -- the three
    # turnover terciles are what define micro and small, so dropping mid from
    # the clustering would silently redefine both -- but no position is taken
    # from it. Attribution over 57 mid trades: -149.9% total, -2.63% per trade,
    # 35% win, the only negative cluster. All three no-mid mixes beat all three
    # with-mid mixes, and the controlled pair (2/2/0 vs 2/2/1) puts the mid
    # position alone at -1.32 points of CAGR.
    if RANKING == "pooled" and take_per_cluster is None:
        # Pooled ranking with a per-cluster quota would be neither one thing
        # nor the other: take the best five outright, whatever band they are in.
        out = [r for r in rows[:MAX_POSITIONS] if r.get("triggered", True)]
        if len(out) < MIN_POSITIONS:
            for r in rows[:MAX_POSITIONS]:
                if len(out) >= MIN_POSITIONS:
                    break
                if r not in out:
                    out.append(r)
        return out
    take_per_cluster = take_per_cluster or dict(TAKE_PER_CLUSTER)
    # ROUND-ROBIN, not cluster-blocks. Returning micro's picks first and then
    # slicing rows[:room] took two micro whenever only two slots were free and
    # never reached small or mid -- the book traded 178 micro / 28 small / 3 mid
    # against a 2/2/1 design. Interleaving means any prefix of the result is
    # still spread across clusters.
    if rows and not (set(take_per_cluster) & {r["cluster"] for r in rows}):
        raise ValueError(f"take_per_cluster {sorted(take_per_cluster)} matches none of the "
                         f"clusters present {sorted({r['cluster'] for r in rows})} "
                         "-- this would silently allocate nothing")
    # `offset` walks DOWN the ranking: offset 0 is the top 2 micro / 2 small /
    # 1 mid, offset 1 is the next 2/2/1, and so on. Running each cohort as its
    # own book turns "is the score real?" into a measurement -- if rank carries
    # information the books must decay with depth, and if they do not, the
    # ranking is decoration and the top book was luck.
    per = {b: [r for r in rows if r["cluster"] == b][offset * k:offset * k + k]
           for b, k in take_per_cluster.items()}
    out, depth = [], max(take_per_cluster.values())
    for d in range(depth):
        for b in take_per_cluster:
            if d < len(per[b]):
                out.append(per[b][d])
    # Trigger LAST, after the interleave. Dropping untriggered names earlier
    # changes which cluster supplies each position, because the round-robin
    # shortened lists -- same rules, different book (+9.05% vs +11.45%). The
    # trigger must remove candidates from the final order, never reorder it.
    ok = [r for r in out if r.get("triggered", True)]
    if len(ok) < MIN_POSITIONS:
        taken = {id(r) for r in ok}
        for r in out:                      # already in rank order
            if len(ok) >= MIN_POSITIONS:
                break
            if id(r) not in taken:
                ok.append(r)
    return ok


def _selftest():
    cap_each = 500_000 * DEPLOY_PCT / 100 / MAX_POSITIONS
    q, risk = position_size(500_000, 100.0)
    # The DEPLOY_PCT cap binds before the risk rule at default parameters:
    # 60% of 5L over 5 names = Rs 60k, i.e. 600 shares at Rs 100, risking
    # Rs 6,000. The 2% risk rule would have allowed Rs 1L / 1,000 shares, so
    # risk_pct is currently DORMANT -- it only starts binding above a ~16.7%
    # stop. Kept deliberately: it is the backstop if stops ever widen.
    # Derive from DEPLOY_PCT rather than hardcode: this assertion has now
    # broken twice for a deliberate design change rather than a defect.
    assert abs(q * 100.0 - cap_each) < 100, (q * 100.0, cap_each)
    assert abs(risk - cap_each * STOP_PCT / 100) < 1, (risk, cap_each)
    cap = cap_each
    # a full book must leave cash on the table
    assert cap * MAX_POSITIONS <= 500_000 * 0.75, "book must not be fully invested"
    q2, _ = position_size(500_000, 10.0)
    assert q2 * 10.0 <= cap + 1, q2 * 10.0
    assert position_size(500_000, 0)[0] == 0
    # a wider stop must reduce size, never increase it
    a, _ = position_size(500_000, 100.0, stop_pct=10.0)
    b, _ = position_size(500_000, 100.0, stop_pct=20.0)
    assert b < a, (a, b)

    # allocation must spread across clusters, not collapse into the richest one
    # `small` deliberately carries the highest scores: a book that ranked
    # globally would be all small, and the per-cluster split is what stops it.
    fake = ([{"cluster": "small", "score": 90 - i, "symbol": f"S{i}"} for i in range(20)]
            + [{"cluster": "micro", "score": 50 - i, "symbol": f"C{i}"} for i in range(20)])
    fake.sort(key=lambda r: -r["score"])
    book = allocate(fake)
    got = {}
    for r in book:
        got[r["cluster"]] = got.get(r["cluster"], 0) + 1
    # Assert the PROPERTY, not a hardcoded mix: the fixture gives `mid` the
    # highest scores, so a book that ranked globally would be all mid. What
    # must hold is that the configured mix is honoured exactly, whatever it is
    # -- hardcoding the numbers made this fail every time the mix changed, for
    # a reason unrelated to what it was protecting.
    if RANKING == "pooled":
        # Pooled ranking takes the best five outright, so one cluster CAN
        # supply the whole book. That is the design, not the slice bug -- but
        # it is a real concentration the 3/2 split used to prevent, so assert
        # it deliberately rather than letting it pass unremarked.
        assert len(book) == MAX_POSITIONS, len(book)
        assert [r["symbol"] for r in book] == [r["symbol"] for r in fake[:5]], \
            "pooled must take the top five by score, in order"
    else:
        expected = dict(TAKE_PER_CLUSTER)
        assert got == expected, (got, expected)
        assert max(got.values()) < len(book), "one cluster must not supply the whole book"
        assert sum(got.values()) == len(book)

    # ANY PREFIX must stay spread -- only meaningful under per-cluster
    # ranking, which is the mode that promises a spread at all.
    if RANKING != "pooled":
        n_clusters = len(TAKE_PER_CLUSTER)
        for room in (1, 2, 3, 4):
            pre = allocate(fake)[:room]
            seen = {r["cluster"] for r in pre}
            assert len(seen) == min(room, n_clusters), (room, [r["cluster"] for r in pre])
    print("portfolio selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        c = features.load_corpus()
        days = sorted({d for s in c.values() for d in s.days})
        as_of = days[-1]
        rows = build(c, as_of)
        book = allocate(rows)
        print(f"PAPER PORTFOLIO  Rs {CAPITAL:,}   selection as of {as_of}")
        print(f"entry: next session open | stop {STOP_PCT}% | target {TARGET_PCT}% "
              f"| exit {HOLD_DAYS}d | no trail\n")
        print(f"  {'sym':<13}{'clu':<7}{'ref':>9}{'entry':>8}{'stop':>9}{'target':>9}"
              f"{'qty':>7}{'value':>10}{'risk':>8}")
        for r in book:
            print(f"  {r['symbol']:<13}{r['cluster']:<7}{r['ref_close']:>9,.2f}"
                  f"{'open':>8}{r['stop']:>9,.2f}{r['target']:>9,.2f}"
                  f"{r['qty']:>7,}{r['value']:>10,}{r['risk']:>8,}")
        inv = sum(r["value"] for r in book)
        rsk = sum(r["risk"] for r in book)
        print(f"\n  deployed Rs {inv:,} of {CAPITAL:,} ({inv/CAPITAL*100:.0f}%)"
              f"   total at risk Rs {rsk:,} ({rsk/CAPITAL*100:.1f}%)")
        json.dump(book, open("data/paper_portfolio.json", "w"), indent=1, default=str)
        print(f"  written to data/paper_portfolio.json")
