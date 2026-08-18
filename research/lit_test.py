#!/usr/bin/env python3
"""Hypotheses this project did NOT invent.

Every result in `lessons.md` came from testing our own ideas on our own data,
which is the position that inflates trial count fastest: the hypothesis and the
sample share an author. These come from the published cross-sectional equity
literature instead, so the sample is the only thing we contributed.

That is a weaker claim than it sounds -- published results carry their own
publication bias, and none of these were established on Indian microcaps. It is
still a better epistemic position than a self-generated grid, and it is the one
concrete thing worth taking from paperswithbacktest.com, whose dataset does not
cover NSE at all.

  1. SKIP-MONTH MOMENTUM (Jegadeesh & Titman 1993; standard "12-1" construction)
     Momentum is measured to t-1 month, not to t, because the most recent month
     carries SHORT-TERM REVERSAL -- an opposite-signed effect. Our `rs` runs to
     the signal day and so mixes the two. Prediction: skipping ~21 sessions
     should HELP, and the effect should be larger in illiquid names where
     reversal is strongest.

     Complication specific to us: the breakout trigger fires ON recent
     strength. Skipping the recent month in the score while triggering on it
     may be incoherent, or may be exactly the right division of labour --
     score for the persistent part, trigger for the timing. Either way it is
     measurable, and that ambiguity is the reason to test rather than adopt.

  2. MAX / LOTTERY EFFECT (Bali, Cakici & Whitelaw 2011)
     Stocks with an extreme single-day gain recently go on to UNDERPERFORM:
     lottery-like payoffs attract retail demand and get overpriced. The effect
     is reported strongest in small, illiquid, retail-held names -- which is a
     description of this entire universe. Prediction: dropping the top decile
     by 1-month MAX daily return should help.

     Against it: our score already rewards `near_high` and `rs`, both of which
     correlate with having had a big day. This may be removing the same names
     the score is trying to buy.

Both knobs default OFF in clusters.py. Nothing here changes the live book.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import statistics
import sys
from collections import defaultdict

import analysis
import clusters
import entry
import features
import portfolio
import simulate

BASE = dict(stop_pct=portfolio.STOP_PCT, target_pct=portfolio.TARGET_PCT,
            hold=portfolio.HOLD_DAYS, max_pos=5, refresh=5,
            trigger=portfolio.TRIGGER)

# (label, RS_SKIP, MAX_SCREEN) -- pre-registered before running.
VARIANTS = [
    ("baseline (no skip, no screen)", 0, None),
    ("skip 21d (1 month)",           21, None),
    ("skip 42d (2 months)",          42, None),
    ("drop top 10% by MAX",           0, 0.10),
    ("drop top 20% by MAX",           0, 0.20),
    ("skip 21d + drop 10% MAX",      21, 0.10),
]

_C = _D = None


def _one(item):
    label, skip, screen = item
    # Set the knobs INSIDE the worker: these are module globals and a fork pool
    # gives each child its own copy, so this cannot leak across variants.
    clusters.RS_SKIP = skip
    clusters.MAX_SCREEN = screen
    entry._CACHE.clear()
    r = simulate.run(_C, _D, **BASE)
    t = r["trades"]
    by = defaultdict(float)
    for x in t:
        by[f"{x['day'].year}-H{1 if x['day'].month <= 6 else 2}"] += x["ret"]
    st = analysis.stats(t)
    return {"label": label, "skip": skip, "screen": screen,
            "cagr": r["cagr"], "dd": r["maxdd"], "n": len(t),
            "win": sum(1 for x in t if x["ret"] > 0) / max(len(t), 1) * 100,
            "avg": st["mean"], "se": st["se"],
            "worst": min(by.values()) if by else float("nan"),
            "syms": len({x["sym"] for x in t}),
            "cluster": analysis.per_cluster(t), "_r": r}


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"literature-derived selection tests — {len(VARIANTS)} variants x "
          f"{len(_D)} sessions, at the live book's exit rules "
          f"({BASE['stop_pct']:g}/{BASE['target_pct']:g}/{BASE['hold']}d)\n")
    with mp.get_context("fork").Pool(min(len(VARIANTS), mp.cpu_count())) as p:
        res = p.map(_one, VARIANTS)

    print(f"  {'variant':<30}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>5}"
          f"{'per-trade':>12}{'std err':>9}{'worst blk':>11}{'syms':>6}")
    for x in res:
        print(f"  {x['label']:<30}{x['cagr']:>+8.2f}%{x['dd']:>7.1f}%"
              f"{x['win']:>5.0f}%{x['n']:>5}{x['avg']:>+11.2f}%"
              f"{x['se'] or 0:>8.2f}%{x['worst']:>+10.1f}%{x['syms']:>6}")

    ctl = res[0]
    print(f"\n  against the current score ({ctl['avg']:+.2f}% per trade, "
          f"n={ctl['n']}):")
    for x in res[1:]:
        if not (x["se"] and ctl["se"]):
            continue
        d = x["avg"] - ctl["avg"]
        se = (x["se"] ** 2 + ctl["se"] ** 2) ** 0.5
        t = d / se if se else 0.0
        print(f"    {x['label']:<30}{x['cagr'] - ctl['cagr']:>+7.2f} CAGR pts"
              f"{d:>+8.2f}% / trade  +/-{1.96 * se:>5.2f}  t{t:>+6.2f}  "
              f"{'RESOLVED' if abs(t) > 1.96 else 'inside the noise'}")

    print("\n  per cluster (micro / small), average per trade:")
    for x in res:
        c = x["cluster"]
        print(f"    {x['label']:<30}"
              f"micro {c.get('micro', {}).get('avg', 0):+.2f}% "
              f"(n={c.get('micro', {}).get('n', 0)})   "
              f"small {c.get('small', {}).get('avg', 0):+.2f}% "
              f"(n={c.get('small', {}).get('n', 0)})")

    print(f"\n  {analysis.trades_needed(analysis.BACKTEST_EDGE)} trades are "
          f"needed to resolve a {analysis.BACKTEST_EDGE:.1f}%/trade edge. "
          f"6 variants were run, so a single |t| near 2 is worth about half "
          f"what it looks like.")
    for x in res:
        simulate.keep(x["label"], x["_r"],
                      {**BASE, "rs_skip": x["skip"], "max_screen": x["screen"]},
                      batch="literature", track="cluster",
                      note="literature-derived selection test")
    return res


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # The knobs must actually reach the score, and must default to off.
        assert clusters.RS_SKIP == 0 and clusters.MAX_SCREEN is None
        from datetime import date, timedelta
        d0 = date(2024, 1, 1)
        days = [d0 + timedelta(days=k) for k in range(400)]
        corpus = {}
        for j in range(30):
            s = features.Series(f"S{j:02d}", list(days))
            for k in range(400):
                px = 100 + j * k * 0.02
                if j == 7 and k >= 380:
                    px *= 1.5              # a late spike: recent, not persistent
                s.close.append(px); s.high.append(px); s.low.append(px)
                s.open.append(px); s.volume.append(1000)
                s.turnover.append(1e6 * (j + 1)); s.deliv_pct.append(40.0 + j)
                s.surveillance_known.append(True); s.restricted.append(False)
            corpus[s.symbol] = s
        syms = [s for v in clusters.size_clusters(corpus).values() for s in v]

        base = clusters.score(corpus, syms, days[-1])
        clusters.RS_SKIP = 21
        skipped = clusters.score(corpus, syms, days[-1])
        clusters.RS_SKIP = 0
        assert base != skipped, "RS_SKIP changed nothing; the knob is not wired"
        if "S07" in base and "S07" in skipped:
            assert skipped["S07"] < base["S07"], \
                "skipping a month must REMOVE the late spike's contribution"

        clusters.MAX_SCREEN = 0.20
        screened = clusters.score(corpus, syms, days[-1])
        clusters.MAX_SCREEN = None
        # Assert the PROPERTY, not a count: the screen runs before the
        # 200-DMA gate, so the arithmetic between the two does not line up.
        assert set(screened) < set(base), "MAX_SCREEN dropped nobody"
        # and it must drop the right name -- S07 is the one with the spike
        if "S07" in base:
            assert "S07" not in screened, \
                "the biggest single-day gainer survived the lottery screen"
        assert clusters.score(corpus, syms, days[-1]) == base, \
            "knobs leaked; the control is no longer the control"
        print("lit_test selftest ok")
    else:
        main()
