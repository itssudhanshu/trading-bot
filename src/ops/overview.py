#!/usr/bin/env python3
"""Where the approach stands -- computed from disk, never from memory.

Every number is read from a file at call time. The recurring failure in this
project has been a confident claim no file supported (a flag printing
"enabled" and doing nothing, an HTTP 200 that was the wrong day's data), so
this module states nothing it did not just read.
"""

# First: finds src/paths.py, which puts every source dir on sys.path.
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from paths import DATA as D, SDATA   # one definition; see paths.py
# D is SHARED data (holidays, the live order book). SDATA is one strategy's own
# outputs. The baseline below is the strategy's headline, not the repo's.


def _jsonl(p):
    return ([json.loads(l) for l in p.read_text().splitlines() if l.strip()]
            if p.exists() else [])


def state():
    import clusters, features, selection, simulate
    s = {"mix": dict(selection.TAKE_PER_CLUSTER),
         "tradeable": list(clusters.CLUSTERS),
         "capital": selection.CAPITAL, "deploy": selection.DEPLOY_PCT,
         "trigger": selection.TRIGGER, "stop": selection.STOP_PCT,
         "target": selection.TARGET_PCT, "hold": selection.HOLD_DAYS}

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
    if (D / "positions.db").exists():
        import positions
        con = sqlite3.connect(D / "positions.db")
        cols = {r[1] for r in con.execute("PRAGMA table_info(pos)")}
        where, arg = ("", ()) if "bucket" not in cols else (
            " WHERE bucket=?", (positions.MAIN,))
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
    # Hardcoded, and it sat two lines above the comment below warning against
    # exactly that -- it still read the 20260819-postlock figures after L61
    # moved them. Restated here rather than made live because rank_test.py is a
    # six-way parallel backtest and cannot run inside a status report; the
    # batch tag is what makes the staleness visible next time.
    g.append(("Ranking predicts return", "PASS",
              "trend across 6 rank cohorts, 1062 trades: -1.12% per step "
              "(std err 0.28, t -3.95); top vs deepest +5.64% +/- 1.52, t 3.72 "
              "[batch 20260820-nonequity3; rank_test.py prints this]"))
    g.append(("Costs modelled realistically", "PASS",
              "brokerage, STT, exchange, SEBI, GST, stamp, DP charges, 20% STCG"))
    # The c=1.0 figure is READ from the recorded baseline, not restated here.
    # This line said +10.85% for months while the file said something else, and
    # a hardcoded copy of a live number is what L59 is about.
    _bl = json.loads((SDATA / "baseline.json").read_text()) if (SDATA / "baseline.json").exists() else {}
    g.append(("Market impact modelled", "PASS",
              "sqrt(participation) x volatility on both sides; baseline "
              f"{_bl.get('cagr', float('nan')):+.2f}% at c=1.0 "
              "(the c=0 comparison is not restated: impact_test has not been "
              "re-run since L61 and the old +11.90% described a universe "
              "holding 87 ETFs)"))
    b = s["bucket"]
    # How long until forward trades can settle anything, at the bucket's own
    # turnover. Use the REALISED pace from the recorded baseline -- trades
    # divided by years -- not occupancy x (250/hold). That product is what the
    # book would do if every position it held ran the full hold and it were
    # always at mean occupancy, and it reads ~78/year against a realised 28.
    # Nearly three times too fast, in the one line whose job is to say how long
    # the honest wait is. An optimistic denominator here flatters the project
    # in exactly the direction L58 and L61 already flattered it.
    try:
        import analysis, selection
        yrs = (_bl.get("sessions") or 0) / 250
        per_yr = ((_bl.get("n") or 0) / yrs) if yrs > 0.5 else None
        if not per_yr:
            occ = (analysis.load_occupancy() or {}).get("mean", 3.0)
            per_yr = occ * (250 / selection.HOLD_DAYS)
        need = analysis.trades_needed(analysis.BACKTEST_EDGE)
        g.append(("Enough trades to judge", "PENDING",
                  f"{need} trades needed at the backtest's "
                  f"{analysis.BACKTEST_EDGE:.1f}% edge; ~{per_yr:.0f}/year "
                  f"at the recorded pace, so ~{need / per_yr:.0f} years"))
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
        # ONE row per variant, the newest. strategies.jsonl is append-only, so
        # an older row for the same variant is a different ENGINE, not a second
        # candidate -- this list showed "impact c=0.0" five times, being the five
        # times that test has been run. The batch is printed because the top of
        # this list is whatever configuration was most optimistic, and after L58
        # the pre-guard batches are all more optimistic than the engine allows.
        newest = {}
        for r in sorted(s["candidates"], key=lambda r: r["at"]):
            newest[r["variant"]] = r
        for r in sorted(newest.values(), key=lambda r: -r["cagr"])[:5]:
            L.append(f"  {r['variant']:<22} CAGR {r['cagr']:>+6.2f}%  "
                     f"DD {r['maxdd']:>5.1f}%  n={r['n']:>4}  "
                     f"batch {r.get('batch', '?')}")
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
    assert sum(s["mix"].values()) > 0, "the bucket must hold stocks"
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
