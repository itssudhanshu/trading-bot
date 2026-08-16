#!/usr/bin/env python3
"""Full historical simulation of the cluster book, with variants.

Runs the ACTUAL rules end to end -- selection, per-cluster allocation, sizing,
gap-aware fills, costs, compounding -- rather than testing components in
isolation. Component tests hid an allocation bug that made 85% of trades micro
caps against a 2/2/1 design: each piece was right, the assembly was not.
"""
import statistics
import sys
from collections import defaultdict

import clusters
import features
import portfolio

COST = 0.4          # % round trip


def run(corpus, days, *, stop_pct=10.0, target_pct=20.0, hold=15, max_pos=5,
        capital=500_000, per_bucket=None, refresh=5, sector_cap=None,
        start_idx=300):
    equity = peak = capital
    maxdd = 0.0
    open_pos, closed = [], []
    for di in range(start_idx, len(days)):
        day = days[di]
        still = []
        for p in open_pos:
            s = corpus[p["sym"]]
            i = s.index_of(day)
            if i is None:
                still.append(p); continue
            held = len([d for d in s.days if p["entry_day"] < d <= day])
            px = why = None
            # Gap through a level fills at the open: worse on stops, better on
            # targets. This is where the realised stop cost exceeds nominal.
            if s.low[i] <= p["stop"]:
                px, why = min(p["stop"], s.open[i]), "stop"
            elif s.high[i] >= p["tgt"]:
                px, why = max(p["tgt"], s.open[i]), "target"
            elif held >= hold:
                px, why = s.close[i], "time"
            if px is None:
                still.append(p); continue
            gross = (px - p["entry"]) * p["qty"]
            cost = (p["entry"] + px) * p["qty"] * COST / 200
            equity += gross - cost
            closed.append({"ret": (px / p["entry"] - 1) * 100 - COST, "why": why,
                           "bkt": p["bkt"], "day": day, "sym": p["sym"]})
        open_pos = still
        peak = max(peak, equity)
        maxdd = max(maxdd, (peak - equity) / peak)

        room = max_pos - len(open_pos)
        if room > 0 and di % refresh == 0 and di + 1 < len(days):
            held_syms = {p["sym"] for p in open_pos}
            held_bkts = defaultdict(int)
            for p in open_pos:
                held_bkts[p["bkt"]] += 1
            rows = portfolio.allocate(
                portfolio.build(corpus, day, capital=equity), per_bucket)
            for r in rows:
                if room <= 0:
                    break
                if r["symbol"] in held_syms:
                    continue
                if sector_cap and held_bkts[r["bucket"]] >= sector_cap:
                    continue
                s = corpus[r["symbol"]]
                i = s.index_of(day)
                if i is None or i + 1 >= len(s):
                    continue
                e = s.open[i + 1]
                if not e:
                    continue
                qty, _ = portfolio.position_size(equity, e, stop_pct)
                if qty < 1:
                    continue
                open_pos.append({"sym": r["symbol"], "bkt": r["bucket"], "entry": e,
                                 "qty": qty, "stop": e * (1 - stop_pct / 100),
                                 "tgt": e * (1 + target_pct / 100),
                                 "entry_day": days[di + 1]})
                held_bkts[r["bucket"]] += 1
                room -= 1
    yrs = (days[-1] - days[start_idx]).days / 365.25
    return {"equity": equity, "capital": capital, "years": yrs,
            "total_pct": (equity / capital - 1) * 100,
            "cagr": ((equity / capital) ** (1 / yrs) - 1) * 100 if yrs > 0.5 else float("nan"),
            "maxdd": maxdd * 100, "trades": closed}


RESULTS = __import__("pathlib").Path(__file__).resolve().parent / "data" / "simulations.jsonl"


def store(name, r, batch=None, track="cluster"):
    """Append one simulation result. Append-only and timestamped: a variant run
    weeks apart under different code is a DIFFERENT result, and overwriting
    would hide that the parameters or the engine moved underneath it."""
    import json as _j
    from collections import defaultdict as _dd
    from datetime import datetime as _dt
    t = r["trades"]
    ex, bk = _dd(list), _dd(list)
    for x in t:
        ex[x["why"]].append(x["ret"]); bk[x["bkt"]].append(x["ret"])
    row = {
        "at": _dt.now().isoformat(timespec="seconds"),
        "batch": batch or _dt.now().strftime("%Y%m%d-%H%M"),
        "variant": name, "track": track,
        "cagr": round(r["cagr"], 2), "maxdd": round(r["maxdd"], 1),
        "total_pct": round(r["total_pct"], 1), "equity": round(r["equity"]),
        "n": len(t),
        "win": round(sum(1 for x in t if x["ret"] > 0) / max(len(t), 1) * 100),
        "avg_stop": round(statistics.fmean(ex["stop"]), 2) if ex["stop"] else None,
        "mix": {b: len(bk[b]) for b in ("micro", "small", "mid")},
        "exits": {k: len(v) for k, v in ex.items()},
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a") as f:
        f.write(_j.dumps(row) + "\n")
    return row


def load_results(limit=None, batch=None, track="cluster", include_void=False):
    """Reads ONE track by default.

    The cluster book and the spec-search are different experiments with
    different universes, sizing and exits. Counting them together produces a
    number that describes neither -- the same blending CLAUDE.md forbids for
    regime blocks. `track=None` opts into the blend deliberately.
    """
    import json as _j
    if not RESULTS.exists():
        return []
    rows = [_j.loads(l) for l in RESULTS.read_text().splitlines() if l.strip()]
    if not include_void:
        rows = [r for r in rows if not r.get("void")]
    if track:
        rows = [r for r in rows if r.get("track", "cluster") == track]
    if batch:
        rows = [r for r in rows if r["batch"] == batch]
    return rows[-limit:] if limit else rows


def report(name, r):
    t = r["trades"]
    n = len(t)
    if not n:
        print(f"  {name:<26} no trades"); return
    ex = defaultdict(list); bk = defaultdict(list)
    for x in t:
        ex[x["why"]].append(x["ret"]); bk[x["bkt"]].append(x["ret"])
    win = sum(1 for x in t if x["ret"] > 0) / n * 100
    print(f"  {name:<26} CAGR {r['cagr']:>+6.2f}%  DD {r['maxdd']:>5.1f}%  "
          f"n={n:>4}  win {win:>3.0f}%  "
          f"stop {statistics.fmean(ex['stop']) if ex['stop'] else 0:>+6.2f}%  "
          f"mix " + "/".join(f"{len(bk[b])}" for b in ("micro", "small", "mid")))
    store(name, r, batch=BATCH)


WF_RESULTS = __import__("pathlib").Path(__file__).resolve().parent / "data" / "walkforward.jsonl"


def store_wf(res):
    """Persist a walk-forward verdict. `anti_predicts` is the field that matters:
    when the in-sample winner ranks last out-of-sample, tuning on in-sample
    results is worse than not tuning."""
    import json as _j
    from datetime import datetime as _dt
    n = len(res["out_sample"])
    row = {"at": _dt.now().isoformat(timespec="seconds"), "param": res["param"],
           "chosen": res["chosen"], "oos_rank": res["oos_rank_of_chosen"],
           "oos_of": n, "oos_best": res["oos_best"],
           # Three states, because two collapsed amber into green: the phone
           # showed a green tick for hold (rank 2/3) while wf_guard correctly
           # REFUSED it. A display that disagrees with the decision is worse
           # than no display.
           "anti_predicts": res["oos_rank_of_chosen"] >= n,
           "verdict": ("anti" if res["oos_rank_of_chosen"] >= n and n > 1
                       else "weak" if res["oos_rank_of_chosen"] > 1 else "ok"),
           "in_sample": {str(k): round(v["cagr"], 2) for k, v in res["in_sample"].items()},
           "out_sample": {str(k): round(v["cagr"], 2) for k, v in res["out_sample"].items()}}
    WF_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with WF_RESULTS.open("a") as f:
        f.write(_j.dumps(row) + "\n")
    return row


def load_wf(limit=None):
    import json as _j
    if not WF_RESULTS.exists():
        return []
    rows = [_j.loads(l) for l in WF_RESULTS.read_text().splitlines() if l.strip()]
    return rows[-limit:] if limit else rows


def wf_guard(param, values, corpus, days, **fixed):
    """-> (allowed, reason). A parameter may only be CHANGED if choosing it
    in-sample actually predicts out-of-sample.

    Measured on this book: the in-sample winner ranked LAST out-of-sample for
    target and stop, and 2nd of 3 for hold. Tuning against in-sample results was
    not merely useless, it was backwards -- so the tuning loop has to be able to
    refuse itself.
    """
    res = walk_forward(corpus, days, param, values, **fixed)
    store_wf(res)
    n = len(res["out_sample"])
    if res["oos_rank_of_chosen"] >= n and n > 1:
        return False, (f"{param}: in-sample winner {res['chosen']} ranked "
                       f"{res['oos_rank_of_chosen']}/{n} out-of-sample -- "
                       f"selection anti-predicts, keep the current value")
    if res["oos_rank_of_chosen"] > 1:
        return False, (f"{param}: in-sample winner {res['chosen']} ranked "
                       f"{res['oos_rank_of_chosen']}/{n} out-of-sample -- not "
                       f"good enough to justify a change")
    return True, f"{param}: {res['chosen']} won both in and out of sample"


def walk_forward(corpus, days, param, values, split=0.5, **fixed):
    """Choose `param` on the FIRST half, then test that choice on the second.

    Picking the best of eleven in-sample variants and reporting its CAGR is the
    best-of-N inflation this project keeps catching elsewhere. The only honest
    question is whether the winner on early data still wins on later data it was
    not chosen from.
    """
    cut = int(len(days) * split)
    early, late = days[:cut], days[cut:]
    out = {"param": param, "in_sample": {}, "out_sample": {}}
    for v in values:
        r = run(corpus, early, **{param: v}, **fixed)
        out["in_sample"][v] = {"cagr": r["cagr"], "maxdd": r["maxdd"],
                               "n": len(r["trades"])}
    best = max(out["in_sample"], key=lambda k: out["in_sample"][k]["cagr"])
    out["chosen"] = best
    for v in values:
        r = run(corpus, late, start_idx=250, **{param: v}, **fixed)
        out["out_sample"][v] = {"cagr": r["cagr"], "maxdd": r["maxdd"],
                                "n": len(r["trades"])}
    ranked = sorted(out["out_sample"], key=lambda k: -out["out_sample"][k]["cagr"])
    out["oos_rank_of_chosen"] = ranked.index(best) + 1
    out["oos_best"] = ranked[0]
    return out


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print("simulate selftest ok (logic shared with portfolio/clusters)")
        sys.exit()
    from datetime import datetime as _dt
    BATCH = _dt.now().strftime("%Y%m%d-%H%M")
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"CLUSTER BOOK SIMULATIONS  {days[300]} .. {days[-1]}  (Rs 5,00,000)")
    print(f"batch {BATCH}\n")
    print("  variant                    CAGR      DD     n   win    avg-stop  micro/small/mid")
    report("baseline 10/20/15d", run(corpus, days))
    report("stop 12%", run(corpus, days, stop_pct=12.0))
    report("stop 15%", run(corpus, days, stop_pct=15.0))
    report("target 15%", run(corpus, days, target_pct=15.0))
    report("target 25%", run(corpus, days, target_pct=25.0))
    report("hold 10d", run(corpus, days, hold=10))
    report("hold 25d", run(corpus, days, hold=25))
    report("3 positions", run(corpus, days, max_pos=3))
    report("8 positions", run(corpus, days, max_pos=8,
                              per_bucket={"micro": 3, "small": 3, "mid": 2}))
    report("cap 2/bucket", run(corpus, days, sector_cap=2))
    report("small+mid only", run(corpus, days,
                                 per_bucket={"small": 3, "mid": 2}))


# ---------------------------------------------------------------- keep/promote
STRATS = __import__("pathlib").Path(__file__).resolve().parent / "data" / "strategies.jsonl"

# A configuration is worth paper-trading only if it survives all four. Positive
# CAGR alone is what a search returns by construction -- out of N variants the
# best few are profitable whether or not anything real is there. The drawdown
# and trade-count bars are what stop the store filling with lucky 20-trade runs.
KEEP_CAGR, KEEP_DD, KEEP_N, KEEP_WIN = 5.0, 55.0, 150, 30.0


def keep(name, r, params, *, batch=None, note="", track="cluster"):
    """Store a configuration that cleared the promotion bar, with the exact
    parameters needed to replay it in the paper book.

    Status is always 'candidate'. Nothing here is validated -- these are
    backtest survivors, and the whole project's evidence says a backtest
    survivor is a hypothesis, not a strategy. Promotion to 'paper' happens only
    after forward trades, which is the one evidence stream a search cannot
    contaminate.
    """
    import json as _j
    from datetime import datetime as _dt
    t = r["trades"]
    n = len(t)
    win = sum(1 for x in t if x["ret"] > 0) / max(n, 1) * 100
    fails = []
    if not (r["cagr"] > KEEP_CAGR):  fails.append(f"cagr {r['cagr']:.2f}<={KEEP_CAGR}")
    if not (r["maxdd"] < KEEP_DD):   fails.append(f"maxdd {r['maxdd']:.1f}>={KEEP_DD}")
    if n < KEEP_N:                   fails.append(f"n {n}<{KEEP_N}")
    if win < KEEP_WIN:               fails.append(f"win {win:.0f}<{KEEP_WIN}")
    if fails:
        return None
    row = {"at": _dt.now().isoformat(timespec="seconds"),
           "batch": batch or BATCH, "variant": name, "status": "candidate",
           "track": track,
           "cagr": round(r["cagr"], 2), "maxdd": round(r["maxdd"], 1),
           "n": n, "win": round(win), "note": note,
           "params": {k: v for k, v in sorted(params.items())}}
    STRATS.parent.mkdir(parents=True, exist_ok=True)
    with STRATS.open("a") as f:
        f.write(_j.dumps(row) + "\n")
    return row


def load_strats(status=None, track="cluster"):
    import json as _j
    if not STRATS.exists():
        return []
    rows = [_j.loads(l) for l in STRATS.read_text().splitlines() if l.strip()]
    if track:
        rows = [r for r in rows if r.get("track", "cluster") == track]
    return [r for r in rows if r.get("status") == status] if status else rows


def best_strategy():
    """-> the stored candidate with the best WORST-CASE evidence, or None.

    Ranked by CAGR only among rows that cleared `keep`. Ranking a search by its
    best result is what PBO measured at 0.75-0.86; this store is not a search,
    it is a shortlist of already-filtered configurations, and the paper book
    still only ever runs one of them at a time.
    """
    rows = load_strats()
    return max(rows, key=lambda r: r["cagr"]) if rows else None
