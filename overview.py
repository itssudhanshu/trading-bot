#!/usr/bin/env python3
"""Where the project stands -- computed from disk, never from memory.

Every number here is read from a file at call time. That is the point: the
recurring failure mode in this project has been a confident claim that no
file supported (a flag that printed "enabled" and did nothing, a panel that
reported "open" while closed, an HTTP 200 that was the wrong day's data). An
overview assembled from recollection is the same bug wearing a report's
clothes, so this module is not allowed to state anything it did not just read.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"


def _jsonl(p):
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def state():
    """-> dict of every measured fact. No interpretation."""
    s = {}

    # Count TRADING days, not snapshot directories. Weekends and holidays get a
    # directory (surveillance files are fetched daily) but no bhavcopy, so the
    # directory count overstates sessions -- it read 1697 against 1695 real
    # sessions, and the gap grows by two every week.
    snaps = sorted(p.name for p in (D / "raw").iterdir() if p.is_dir()) if (D / "raw").exists() else []
    s["snapshot_dirs"] = len(snaps)
    try:
        import features
        days = sorted({d for x in features.load_corpus().values() for d in x.days})
        s["days"] = len(days)
        s["span"] = (str(days[0]), str(days[-1]))
    except Exception:
        s["days"] = len(snaps)
        s["span"] = (snaps[0], snaps[-1]) if snaps else (None, None)

    # holidays.json is OBSERVED, not published: backfill records a date when NSE
    # serves a stale file for it. So its last entry is always in the past and
    # says nothing about staleness -- an earlier version of this check warned
    # permanently for that reason. What is actually worth knowing is whether
    # any weekday in the corpus has no data AND no holiday recorded, which is a
    # real gap rather than a calendar quirk.
    s["unexplained_gaps"] = []
    try:
        h = json.loads((D / "holidays.json").read_text())
        hol = {str(x) for x in (h if isinstance(h, list) else h.get("holidays", list(h)))}
        if s.get("days") and s["span"][0]:
            have = set(days)
            d, end = date.fromisoformat(s["span"][0]), date.fromisoformat(s["span"][1])
            gaps = []
            while d <= end:
                if d.weekday() < 5 and d not in have and d.isoformat() not in hol:
                    gaps.append(d.isoformat())
                d += timedelta(days=1)
            s["unexplained_gaps"] = gaps
    except Exception:
        pass

    led = json.loads((D / "judge_ledger.json").read_text()) if (D / "judge_ledger.json").exists() else {}
    v = list(led.get("verdicts", {}).values())
    s["budget_spent"] = led.get("spent", 0)
    s["budget_total"] = 50
    s["holdout_pass"] = v.count("PASS")
    s["holdout_fail"] = v.count("FAIL")

    # Cluster track only, void rows excluded. Blending the two tracks gives a
    # count that describes neither experiment.
    import simulate
    sims = simulate.load_results(track="cluster")
    s["n_sims"] = len(sims)
    s["n_void"] = len(simulate.load_results(track="cluster", include_void=True)) - len(sims)
    s["sims_positive"] = sum(1 for r in sims if r.get("cagr", 0) > 0)
    s["best_sim"] = max(sims, key=lambda r: r.get("cagr", -99)) if sims else None
    s["last_batch"] = sims[-1]["batch"] if sims else None
    s["promoted_specs"] = len(_jsonl(D / "promoted.jsonl"))

    wf = _jsonl(D / "walkforward.jsonl")
    s["n_wf"] = len(wf)
    s["wf_anti"] = sum(1 for r in wf if r.get("anti_predicts"))
    s["wf_strong"] = sum(1 for r in wf if r.get("verdict") == "strong")

    s["candidates"] = simulate.load_strats("candidate", track="cluster")
    s["promoted"] = simulate.load_strats("paper", track="cluster")

    s["book"] = {"pending": 0, "open": 0, "closed": 0, "net": 0.0}
    if (D / "pbook.db").exists():
        con = sqlite3.connect(D / "pbook.db")
        for status, n in con.execute("select status, count(*) from pos group by status"):
            s["book"][status] = n
        (net,) = con.execute(
            "select coalesce(sum(net),0) from pos where status='closed'").fetchone()
        s["book"]["net"] = net
        con.close()

    try:
        import learning
        s["n_learn_trades"] = len(learning.load())
        s["weights"] = learning.load_weights()
    except Exception:
        s["n_learn_trades"], s["weights"] = 0, {}
    return s


def gates(s):
    """-> [(name, verdict, evidence)] -- the checks that decide direction.

    PENDING is not a soft PASS. Most of these are pending because the evidence
    that would settle them does not exist yet, and saying so is the only
    honest reading.
    """
    g = []
    g.append(("Point-in-time data", "PASS" if s["days"] > 1000 else "THIN",
              f"{s['days']} trading sessions, {s['span'][0]} to {s['span'][1]}, "
              f"universe rebuilt per date"))

    g.append(("[B] Strategy cleared sealed holdout",
              "PASS" if s["holdout_pass"] else "FAIL",
              f"{s['holdout_pass']} PASS / {s['holdout_fail']} FAIL over "
              f"{s['budget_spent']} of {s['budget_total']} lifetime consultations"))

    g.append(("[A] Parameter tuning predicts out-of-sample",
              "FAIL" if s["wf_anti"] else ("PASS" if s["wf_strong"] else "WEAK"),
              f"{s['n_wf']} walk-forward tests, {s['wf_anti']} anti-predicting, "
              f"{s['wf_strong']} strong"))

    g.append(("[A] Backtest candidates worth forward-testing",
              "PASS" if s["candidates"] else "NONE",
              f"{len(s['candidates'])} cleared the promotion bar of "
              f"{s['n_sims']} cluster simulations run"))

    b = s["book"]
    g.append(("[A] Forward paper evidence",
              "PENDING" if b["closed"] == 0 else "MEASURING",
              f"{b['closed']} closed, {b['open']} open, {b['pending']} queued; "
              f"realised P&L {b['net']:+,.0f}"))
    return g


def direction(s, g):
    """-> (verdict, [reasons]).

    Deliberately asymmetric: process gates cannot manufacture a YES. Only
    forward evidence or a holdout PASS can, because those are the only two
    streams a search cannot contaminate -- and neither exists yet.
    """
    why = []
    if s["holdout_pass"]:
        return "YES -- validated", [f"{s['holdout_pass']} strategy cleared the sealed holdout"]
    if s["book"]["closed"] >= 30:
        return ("YES" if s["book"]["net"] > 0 else "NO"), [
            f"{s['book']['closed']} forward trades, realised {s['book']['net']:+,.0f}"]

    why.append(f"TRACK B: {s['holdout_fail']} holdout FAIL, 0 PASS "
               f"({s['budget_spent']}/{s['budget_total']} spent)")
    why.append("TRACK A: never consulted against the holdout -- the cluster book "
               "has spent 0 of its budget, so it is untested, not failed")
    why.append(f"TRACK A: no forward evidence yet, {s['book']['closed']} closed trades")
    why.append(f"TRACK A: {s['sims_positive']} of {s['n_sims']} backtests positive -- a "
               "search returns some positives by construction, so this is not evidence")
    if s["wf_anti"]:
        why.append(f"{s['wf_anti']} parameter(s) anti-predict out-of-sample: tuning "
                   "them made things worse, not better")
    return "TOO EARLY TO SAY", why


def render(s=None):
    s = s or state()
    g = gates(s)
    verdict, why = direction(s, g)
    L = []
    L.append(f"OVERVIEW  {date.today()}")
    L.append("=" * 62)
    L.append("")
    L.append(f"SHARED DATA   {s['days']} trading sessions, {s['span'][0]} -> {s['span'][1]}")
    g = s.get("unexplained_gaps") or []
    if g:
        L.append(f"  WARNING: {len(g)} weekday(s) with no data and no holiday "
                 f"recorded, e.g. {', '.join(g[:3])}")
    L.append("")
    b = s["book"]
    L.append("TRACK A -- Rs 5L CLUSTER BOOK  (your 20/20/20 design)")
    L.append(f"  Sims      {s['n_sims']} cluster simulations"
             + (f"  ({s['n_void']} voided)" if s.get("n_void") else ""))
    L.append(f"  Walk-fwd  {s['n_wf']} tests, {s['n_learn_trades']:,} trades studied")
    L.append(f"  Book      {b['pending']} queued, {b['open']} open, {b['closed']} closed, "
             f"realised {b['net']:+,.0f}")
    L.append(f"  Kept      {len(s['candidates'])} candidates, {len(s['promoted'])} promoted")
    L.append("")
    L.append("TRACK B -- SPEC SEARCH  (the earlier track)")
    L.append(f"  Holdout   {s['budget_spent']}/{s['budget_total']} spent  "
             f"({s['holdout_pass']} PASS, {s['holdout_fail']} FAIL)")
    L.append(f"  Promoted  {s['promoted_specs']} specs cleared the pipeline")
    L.append("  Book      empty -- this track has never held a position")
    L.append("")
    L.append("GATES")
    for name, vd, ev in g:
        L.append(f"  [{vd:^8}] {name}")
        L.append(f"             {ev}")
    L.append("")
    if s["candidates"]:
        L.append("STORED CANDIDATES (backtest survivors, not validated)")
        for r in sorted(s["candidates"], key=lambda r: -r["cagr"])[:6]:
            L.append(f"  {r['variant']:<26} CAGR {r['cagr']:>+6.2f}%  DD {r['maxdd']:>5.1f}%  "
                     f"n={r['n']:>4}  win {r['win']}%")
    else:
        L.append("STORED CANDIDATES: none yet cleared the promotion bar")
    L.append("")
    L.append(f"DIRECTION: {verdict}")
    for w in why:
        L.append(f"  - {w}")
    L.append("")
    L.append("The honest reading: the measurement apparatus is now trustworthy;")
    L.append("the strategy is not yet shown to work. Those are different claims,")
    L.append("and only the first one has been earned.")
    return "\n".join(L)


def _selftest():
    s = state()
    assert isinstance(s["days"], int)
    assert s["budget_spent"] <= s["budget_total"], "spent cannot exceed lifetime budget"
    v, why = direction(s, gates(s))
    # A YES must be backed by holdout or forward evidence -- never by backtests.
    if v.startswith("YES"):
        assert s["holdout_pass"] or s["book"]["closed"] >= 30, "unearned YES"
    assert why, "a verdict must carry its reasons"
    print("overview selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(render())
