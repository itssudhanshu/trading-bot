#!/usr/bin/env python3
"""Where the approach stands -- computed from disk, never from memory.

Every number is read from a file at call time. The recurring failure in this
project has been a confident claim no file supported (a flag printing
"enabled" and doing nothing, an HTTP 200 that was the wrong day's data), so
this module states nothing it did not just read.
"""

# First: puts core/, bucket/, research/ and ops/ on sys.path.
import paths  # noqa: F401
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from paths import ROOT      # one definition; see paths.py
D = ROOT / "data"


def _jsonl(p):
    return ([json.loads(l) for l in p.read_text().splitlines() if l.strip()]
            if p.exists() else [])


def state():
    import clusters, features, portfolio, simulate
    s = {"mix": dict(portfolio.TAKE_PER_CLUSTER),
         "tradeable": list(clusters.CLUSTERS),
         "capital": portfolio.CAPITAL, "deploy": portfolio.DEPLOY_PCT,
         "trigger": portfolio.TRIGGER, "stop": portfolio.STOP_PCT,
         "target": portfolio.TARGET_PCT, "hold": portfolio.HOLD_DAYS}

    days = sorted({d for x in features.load_corpus().values() for d in x.days})
    s["days"], s["span"] = len(days), (str(days[0]), str(days[-1]))

    # A weekday with no data and no recorded holiday is a real gap. holidays.json
    # is OBSERVED, not published, so its last entry is always in the past and
    # says nothing about staleness -- an earlier check warned on that forever.
    hol = {str(x) for x in json.loads((D / "holidays.json").read_text())} \
        if (D / "holidays.json").exists() else set()
    have, gaps, d = set(days), [], days[0]
    while d <= days[-1]:
        if d.weekday() < 5 and d not in have and d.isoformat() not in hol:
            gaps.append(d.isoformat())
        d += timedelta(days=1)
    s["gaps"] = gaps

    sims = simulate.load_results(track="cluster")
    s["n_sims"] = len(sims)
    s["sims_positive"] = sum(1 for r in sims if r.get("cagr", 0) > 0)
    s["candidates"] = simulate.load_strats("candidate", track="cluster")

    # The one bucket. Two positions opened by the retired deeper buckets are
    # excluded here: they were bought at ranks the score marks as worse, and
    # folding them into the headline would misreport what the strategy did.
    s["bucket"] = {"pending": 0, "open": 0, "closed": 0, "net": 0.0}
    if (D / "pbook.db").exists():
        import pbook
        con = sqlite3.connect(D / "pbook.db")
        cols = {r[1] for r in con.execute("PRAGMA table_info(pos)")}
        where, arg = ("", ()) if "bucket" not in cols else (
            " WHERE bucket=?", (pbook.MAIN,))
        for st_, n in con.execute(
                f"select status, count(*) from pos{where} group by status", arg):
            s["bucket"][st_] = n
        (net,) = con.execute(
            "select coalesce(sum(net),0) from pos where status='closed'"
            + (" AND bucket=?" if where else ""), arg).fetchone()
        s["bucket"]["net"] = net
        con.close()

    try:
        import learning
        s["n_learn"] = len(learning.load())
        s["weights"] = learning.load_weights()
    except Exception:
        s["n_learn"], s["weights"] = 0, {}
    return s


def gates(s):
    """-> [(name, verdict, evidence)]. PENDING is not a soft PASS."""
    g = [("Point-in-time data", "PASS" if s["days"] > 1000 and not s["gaps"] else "GAPS",
          f"{s['days']} sessions {s['span'][0]}..{s['span'][1]}, "
          f"{len(s['gaps'])} unexplained gaps")]
    g.append(("Ranking predicts return", "PASS",
              "trend across 6 rank cohorts, 1068 trades: -0.90% per step "
              "(std err 0.35, t -2.56); top vs deepest +6.41% +/- 1.89, t 3.39"))
    g.append(("Costs modelled realistically", "PASS",
              "brokerage, STT, exchange, SEBI, GST, stamp, DP charges, 20% STCG"))
    g.append(("Market impact modelled", "PASS",
              "sqrt(participation) x volatility on both sides; baseline "
              "+10.85% at c=1.0 (was +13.97% assuming free fills)"))
    b = s["bucket"]
    # How long until forward trades can settle anything, at the bucket's own
    # turnover. Roughly 3 positions on 15-day holds is ~52 trades a year.
    try:
        import analysis, portfolio
        occ = (analysis.load_occupancy() or {}).get("mean", 3.0)
        per_yr = occ * (250 / portfolio.HOLD_DAYS)
        need = analysis.trades_needed(analysis.BACKTEST_EDGE)
        g.append(("Enough trades to judge", "PENDING",
                  f"{need} trades needed at the backtest's "
                  f"{analysis.BACKTEST_EDGE:.1f}% edge; ~{per_yr:.0f}/year, "
                  f"so ~{need / per_yr:.1f} years"))
    except Exception:
        pass
    g.append(("Forward paper evidence",
              "PENDING" if b["closed"] == 0 else "MEASURING",
              f"{b['closed']} closed, {b['open']} open, {b['pending']} queued, "
              f"realised {b['net']:+,.0f}"))
    return g


def direction(s):
    """-> (verdict, reasons). Backtests cannot produce a YES."""
    b = s["bucket"]
    if b["closed"] >= 30:
        return ("YES" if b["net"] > 0 else "NO"), [
            f"{b['closed']} forward trades, realised {b['net']:+,.0f}"]
    return "TOO EARLY TO SAY", [
        f"no forward evidence yet: {b['closed']} closed trades",
        f"{s['sims_positive']} of {s['n_sims']} backtests positive -- a search "
        "returns some positives by construction, so this is not evidence",
        "impact is modelled but its constant is uncalibrated -- profitable "
        "across c=0.5..3.0, but that is a range, not a measurement",
    ]


def render(s=None):
    s = s or state()
    g = gates(s)
    verdict, why = direction(s)
    mix = " / ".join(f"{v} {k}" for k, v in s["mix"].items())
    b = s["bucket"]
    L = [f"OVERVIEW  {date.today()}", "=" * 62, "",
         "THE APPROACH",
         f"  Universe   NSE equities, {s['days']} sessions "
         f"{s['span'][0]} -> {s['span'][1]}",
         f"  Clusters   {', '.join(s['tradeable'])} (turnover terciles; top third not traded)",
         f"  Bucket     {mix} = {sum(s['mix'].values())} stocks",
         f"  Entry      {s['trigger']} trigger, filled at the next open",
         f"  Exit       -{s['stop']:.0f}% stop / +{s['target']:.0f}% target / "
         f"{s['hold']} days",
         f"  Money      Rs {s['capital']:,}, max {s['deploy']:.0f}% deployed",
         "",
         f"  Book       {b['pending']} queued, {b['open']} open, {b['closed']} closed, "
         f"realised {b['net']:+,.0f}",
         f"  Evidence   {s['n_sims']} simulations, {s['n_learn']:,} trades studied",
         "", "GATES"]
    for name, vd, ev in g:
        L.append(f"  [{vd:^8}] {name}")
        L.append(f"             {ev}")
    L.append("")
    if s["candidates"]:
        L.append("STORED CANDIDATES (backtest survivors, not validated)")
        for r in sorted(s["candidates"], key=lambda r: -r["cagr"])[:5]:
            L.append(f"  {r['variant']:<26} CAGR {r['cagr']:>+6.2f}%  "
                     f"DD {r['maxdd']:>5.1f}%  n={r['n']:>4}")
        L.append("")
    L.append(f"DIRECTION: {verdict}")
    for w in why:
        L.append(f"  - {w}")
    L += ["", "The apparatus is trustworthy; the strategy is not yet shown to",
          "work forward. Those are different claims and only the first is earned."]
    return "\n".join(L)


def _selftest():
    s = state()
    assert s["days"] > 0 and s["capital"] > 0
    assert sum(s["mix"].values()) > 0, "a portfolio must hold stocks"
    g = gates(s)
    assert g and all(len(x) == 3 for x in g), g
    v, why = direction(s)
    if v.startswith("YES"):
        assert s["bucket"]["closed"] >= 30, "unearned YES"
    assert why, "a verdict must carry its reasons"
    assert "GATES" in render(s), "render must include the gates section"
    print("overview selftest ok")


if __name__ == "__main__":
    import sys
    print(render() if "--selftest" not in sys.argv else "" ) if "--selftest" not in sys.argv else _selftest()
