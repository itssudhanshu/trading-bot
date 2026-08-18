#!/usr/bin/env python3
"""Full historical simulation of the bucket, with variants.

Runs the ACTUAL rules end to end -- selection, per-cluster allocation, sizing,
gap-aware fills, costs, compounding -- rather than testing components in
isolation. Component tests hid an allocation bug that made 85% of trades micro
caps against a 2/2/1 design: each piece was right, the assembly was not.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

import paths
import statistics
import sys
from collections import defaultdict

import clusters
import engine
import features
import selection

# Real charges, not a flat percentage. A single round-trip number cannot be
# right for both a Rs 1L position and a Rs 5,000 one: brokerage and DP charges
# are FIXED, so their percentage cost explodes as size falls -- which is
# exactly the small-cap end where the measured edge lives.
COSTS = engine.Costs()


_ATR_CACHE = {}


def _atr_at(s, i, n=14):
    """ATR(14) on the signal bar. Cached per symbol -- recomputing a full
    Wilder series per candidate per session dominated the run otherwise."""
    a = _ATR_CACHE.get(s.symbol)
    if a is None:
        a = _ATR_CACHE[s.symbol] = features.atr(s.high, s.low, s.close, n)
    return a[i] if 0 <= i < len(a) else None


def _liq(s, i, win=60):
    """-> (median daily turnover, daily volatility %) at index i."""
    t = [x for x in s.turnover[max(0, i - win):i + 1] if x > 0]
    rets = []
    for k in range(max(1, i - 20), i + 1):
        p = s.close[k - 1]
        if p:
            rets.append(s.close[k] / p - 1.0)
    if not t or len(rets) < 5:
        return None, None
    return statistics.median(t), statistics.pstdev(rets) * 100
STCG = 0.20         # short-term capital gains on STT-paid equity; 15-day hold
                    # is always short term. Applied per financial year on NET
                    # realised gains, so losses offset -- taxing each winning
                    # trade in isolation would overstate the bill badly.


def run(corpus, days, *, stop_pct=10.0, target_pct=20.0, hold=15, max_pos=5,
        capital=None, take_per_cluster=None, refresh=5, cluster_cap=None,
        start_idx=300, trigger="none", offset=0, max_corr=None,
        impact_c=engine.IMPACT_C, sizing="equal", targets=None, stop_to=None,
        atr_stop=None):
    """`targets` = [(pct, fraction), ...]: a ladder of PARTIAL exits, each
    selling `fraction` of the original quantity at entry*(1 + pct/100).
    `stop_to` = (trigger_pct, new_stop_pct): once price touches
    entry*(1 + trigger_pct/100) the stop moves to entry*(1 + new_stop_pct/100).

    The two are INDEPENDENT on purpose. An earlier version coupled them into a
    single `scale` rule, which meant "sell half and move the stop" was the only
    thing that could be measured -- so a loss could not be attributed to the
    selling or to the stop move. They are different bets and get tested apart.

    A partial sell is a real order and pays its own brokerage, STT and DP
    charge -- laddering out of a Rs 45,000 position is not free, and that cost
    is a real part of what the test measures.

    `atr_stop` = k places the stop k x ATR(14) below the fill instead of a flat
    `stop_pct`. A fixed percentage asks a 6%-daily-vol microcap and a 2%-vol
    name to survive the same distance; ATR asks each to survive the same amount
    of ITS OWN noise. Position size still comes from `stop_pct`, so the two can
    be varied independently."""
    # Default to the real pocket rather than a hardcoded figure: a simulation
    # run at a different capital from the live bucket is not a test of the live
    # bucket, because position size drives the cost percentage.
    capital = selection.CAPITAL if capital is None else capital
    equity = peak = capital
    maxdd = 0.0
    open_pos, closed = [], []
    next_pid = 0                 # both legs of a scaled exit share one id
    fy_net, taxed = {}, set()
    occupancy = []
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
                # Label by WHICH stop fired. Without this a moved stop that is
                # hit the next day is indistinguishable from the original stop
                # in the exit mix, which is the one diagnostic that says
                # whether moving it did anything at all.
                px = min(p["stop"], s.open[i])
                why = "stop-moved" if p.get("moved") else "stop"
            else:
                # Everything below runs only if the ORIGINAL stop survived the
                # day. Order within the day is unknown, so the stop is always
                # read first and the worse reading is taken.
                if stop_to and not p.get("moved") and s.high[i] >= p["trig"]:
                    p["moved"] = True
                    p["stop"] = p["entry"] * (1 + stop_to[1] / 100)
                    # The moved stop can fire the SAME day: a name that touched
                    # +10% and closed back through entry did both. Pretending
                    # the new stop only becomes live tomorrow would book a free
                    # option nobody had.
                    if s.low[i] <= p["stop"]:
                        px, why = min(p["stop"], s.open[i]), "stop-moved"
                # Ladder rungs, cheapest first. A single wide day can take out
                # more than one, and it really would have.
                while px is None and p.get("rungs") and s.high[i] >= p["rungs"][0][0]:
                    lvl, frac = p["rungs"].pop(0)
                    sold = min(int(p["qty0"] * frac), p["qty"])
                    if sold < 1:
                        continue
                    _px = max(lvl, s.open[i])
                    _imp = (engine.impact_pct(sold * _px, *_liq(s, i), impact_c)
                            if impact_c else 0.0)
                    _px *= (1 - _imp / 100)
                    _buy, _sell = p["entry"] * sold, _px * sold
                    _cost = (COSTS.charge(_buy, "BUY") + COSTS.charge(_sell, "SELL"))
                    _net = (_sell - _buy) - _cost
                    equity += _net
                    _fy = day.year if day.month > 3 else day.year - 1
                    fy_net[_fy] = fy_net.get(_fy, 0.0) + _net
                    closed.append({"ret": _net / _buy * 100, "why": "partial",
                                   "clu": p["clu"], "day": day, "sym": p["sym"],
                                   "cost_pct": _cost / _buy * 100,
                                   "imp": p.get("imp_in", 0.0) + _imp,
                                   "pid": p["pid"], "net": _net, "buy": _buy,
                                   "held": held, "stop_dist": p.get("stop_dist")})
                    p["qty"] -= sold
                if p["qty"] < 1:
                    continue
                if px is None:
                    if s.high[i] >= p["tgt"]:
                        px, why = max(p["tgt"], s.open[i]), "target"
                    elif held >= hold:
                        px, why = s.close[i], "time"
            if px is None:
                still.append(p); continue
            # And you do not exit at the printed price either.
            imp_out = 0.0
            if impact_c:
                adv, vol = _liq(s, i)
                imp_out = engine.impact_pct(p["qty"] * px, adv, vol, impact_c)
            px *= (1 - imp_out / 100)
            buy_val, sell_val = p["entry"] * p["qty"], px * p["qty"]
            cost = COSTS.charge(buy_val, "BUY") + COSTS.charge(sell_val, "SELL")
            net = (sell_val - buy_val) - cost
            equity += net
            fy = day.year if day.month > 3 else day.year - 1   # India FY: Apr-Mar
            fy_net[fy] = fy_net.get(fy, 0.0) + net
            closed.append({"ret": net / buy_val * 100, "why": why,
                           "clu": p["clu"], "day": day, "sym": p["sym"],
                           "cost_pct": cost / buy_val * 100,
                           "imp": p.get("imp_in", 0.0) + imp_out,
                           "pid": p["pid"], "net": net, "buy": buy_val,
                           "stop_dist": p.get("stop_dist"), "held": held})
        open_pos = still
        occupancy.append(len(open_pos))
        # Settle the previous year's tax after 31 March, on net gains only.
        fy = day.year if day.month > 3 else day.year - 1
        for y in [k for k in fy_net if k < fy and k not in taxed]:
            equity -= max(fy_net[y], 0.0) * STCG
            taxed.add(y)
        peak = max(peak, equity)
        maxdd = max(maxdd, (peak - equity) / peak)

        room = max_pos - len(open_pos)
        if room > 0 and di % refresh == 0 and di + 1 < len(days):
            taken_n = 0
            held_syms = {p["sym"] for p in open_pos}
            held_clusters = defaultdict(int)
            for p in open_pos:
                held_clusters[p["clu"]] += 1
            rows = selection.allocate(
                selection.build(corpus, day, capital=equity, trigger=trigger),
                take_per_cluster, offset=offset)
            rows = selection.decorrelate(rows, corpus, day, max_corr)
            for r in rows:
                if room <= 0:
                    break
                if r["symbol"] in held_syms:
                    continue
                # Caps positions per SIZE BUCKET. There is no sector rule in
                # this system -- the corpus carries no industry classification.
                if cluster_cap and held_clusters[r["cluster"]] >= cluster_cap:
                    continue
                s = corpus[r["symbol"]]
                i = s.index_of(day)
                if i is None or i + 1 >= len(s):
                    continue
                e = s.open[i + 1]
                if not e:
                    continue
                vols = [v for v in (_liq(corpus[x["symbol"]],
                        corpus[x["symbol"]].index_of(day) or 0)[1] for x in rows)
                        if v]
                medvol = statistics.median(vols) if vols else None
                _, myvol = _liq(s, i)
                mult = selection.size_mult(sizing, taken_n, myvol, medvol)
                qty, _ = selection.position_size(equity, e, stop_pct, mult=mult)
                if qty < 1:
                    continue
                # You do not fill at the printed open. Pay impact on the way in;
                # stop and target hang off the price actually paid, as they
                # would off a real fill.
                imp = 0.0
                if impact_c:
                    adv, vol = _liq(s, i)
                    imp = engine.impact_pct(qty * e, adv, vol, impact_c)
                e_eff = e * (1 + imp / 100)
                _stop_px = e_eff * (1 - stop_pct / 100)
                if atr_stop:
                    a = _atr_at(s, i)
                    # No ATR yet means the stop distance is unknown. Skip the
                    # name rather than fall back to a flat percentage -- a
                    # silent fallback would make part of the ATR bucket a
                    # flat-stop bucket and the comparison meaningless.
                    if not a or e_eff - atr_stop * a <= 0:
                        continue
                    _stop_px = e_eff - atr_stop * a
                next_pid += 1
                open_pos.append({"pid": next_pid,
                                 "sym": r["symbol"], "clu": r["cluster"],
                                 "entry": e_eff, "qty": qty,
                                 "stop": _stop_px,
                                 "tgt": e_eff * (1 + target_pct / 100),
                                 "qty0": qty,
                                 "rungs": ([(e_eff * (1 + t / 100), f)
                                            for t, f in targets] if targets else None),
                                 "trig": (e_eff * (1 + stop_to[0] / 100)
                                          if stop_to else None),
                                 "stop_dist": (e_eff - _stop_px) / e_eff * 100,
                                 "entry_day": days[di + 1], "imp_in": imp})
                held_clusters[r["cluster"]] += 1
                taken_n += 1
                room -= 1
    equity -= sum(max(v, 0.0) for k, v in fy_net.items() if k not in taxed) * STCG
    yrs = (days[-1] - days[start_idx]).days / 365.25
    from collections import Counter as _Ctr
    _dist = _Ctr(occupancy)
    return {"occ_dist": {k: round(v / max(len(occupancy), 1) * 100, 1)
                         for k, v in sorted(_dist.items())},
            "occupancy": (statistics.fmean(occupancy) if occupancy else 0.0),
            "occ_full": (sum(1 for x in occupancy if x >= max_pos)
                         / max(len(occupancy), 1) * 100),
            "occ_empty": (sum(1 for x in occupancy if x == 0)
                          / max(len(occupancy), 1) * 100),
            "equity": equity, "capital": capital, "years": yrs,
            "total_pct": (equity / capital - 1) * 100,
            "cagr": ((equity / capital) ** (1 / yrs) - 1) * 100 if yrs > 0.5 else float("nan"),
            "maxdd": maxdd * 100, "trades": closed}


RESULTS = paths.DATA / "simulations.jsonl"


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
        ex[x["why"]].append(x["ret"]); bk[x["clu"]].append(x["ret"])
    row = {
        "at": _dt.now().isoformat(timespec="seconds"),
        "batch": batch or _dt.now().strftime("%Y%m%d-%H%M"),
        "variant": name, "track": track,
        "cagr": round(r["cagr"], 2), "maxdd": round(r["maxdd"], 1),
        "total_pct": round(r["total_pct"], 1), "equity": round(r["equity"]),
        "n": len(t),
        "win": round(sum(1 for x in t if x["ret"] > 0) / max(len(t), 1) * 100),
        "avg_stop": round(statistics.fmean(ex["stop"]), 2) if ex["stop"] else None,
        "mix": {b: len(bk[b]) for b in clusters.CLUSTERS},
        "exits": {k: len(v) for k, v in ex.items()},
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a") as f:
        f.write(_j.dumps(row) + "\n")
    return row


def load_results(limit=None, batch=None, track="cluster", include_void=False):
    """Reads ONE track by default.

    The bucket and the spec-search are different experiments with
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
        ex[x["why"]].append(x["ret"]); bk[x["clu"]].append(x["ret"])
    win = sum(1 for x in t if x["ret"] > 0) / n * 100
    print(f"  {name:<26} CAGR {r['cagr']:>+6.2f}%  DD {r['maxdd']:>5.1f}%  "
          f"n={n:>4}  win {win:>3.0f}%  "
          f"stop {statistics.fmean(ex['stop']) if ex['stop'] else 0:>+6.2f}%  "
          f"mix " + "/".join(f"{len(bk[b])}" for b in clusters.CLUSTERS))
    store(name, r, batch=BATCH)


WF_RESULTS = paths.DATA / "walkforward.jsonl"


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

    Measured on this bucket: the in-sample winner ranked LAST out-of-sample for
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


def _selftest():
    """The scale-out path is money logic and had no check.

    One synthetic path, chosen so the property is unambiguous: every name runs
    +30% and then bleeds back down through both stops. The base bucket must give
    the whole move back at its -10% stop; the scaled bucket must bank half at the
    first target and stop the rest out near breakeven. Asserts the PROPERTY --
    partial booked, stop moved up, bucket ahead -- not the exact returns, which
    move with the cost stack.
    """
    from datetime import date, timedelta
    d0 = date(2024, 1, 1)
    days = [d0 + timedelta(days=k) for k in range(420)]
    corpus = {}
    for j in range(30):
        s = features.Series(f"S{j:02d}", list(days))
        for k in range(420):
            px = 100.0 + j * 0.001 * k              # near-flat, slight spread
            if k >= 301:
                px = 100.0 + min(k - 300, 10) * 3.0  # +30% over 10 sessions
            if k >= 311:
                px = max(130.0 - (k - 310), 60.0)    # then bleed 1/day, no gaps
            s.open.append(px); s.high.append(px); s.low.append(px)
            s.close.append(px); s.volume.append(1000)
            s.turnover.append(1e6 * (j + 1)); s.deliv_pct.append(40.0 + j)
            s.surveillance_known.append(True); s.restricted.append(False)
        corpus[s.symbol] = s

    kw = dict(start_idx=300, trigger="none", impact_c=0.0, stop_pct=10.0,
              target_pct=100.0, hold=60)
    base = run(corpus, days, **kw)
    sc = run(corpus, days, targets=[(10.0, 0.5)], stop_to=(10.0, 0.0), **kw)
    # The two rules must also work ALONE -- that separation is the point.
    only_t = run(corpus, days, targets=[(10.0, 0.5)], **kw)
    only_s = run(corpus, days, stop_to=(10.0, 0.0), **kw)

    assert base["trades"], "control took no trades; the fixture is broken"
    assert all(t["why"] == "stop" for t in base["trades"]), \
        [t["why"] for t in base["trades"]]
    parts = [t for t in sc["trades"] if t["why"] == "partial"]
    assert parts, "targets set but no partial was ever booked"
    assert all(t["ret"] > 0 for t in parts), parts
    rest = [t for t in sc["trades"] if t["why"].startswith("stop")]
    assert rest, "the remainder never exited"
    # The whole point: the moved stop must sit near entry, not 10% below it.
    assert all(t["ret"] > -2.0 for t in rest), \
        f"stop did not move up after the first target: {rest}"

    # targets alone must sell, and must NOT move the stop
    assert [t for t in only_t["trades"] if t["why"] == "partial"], "no partial"
    tail = [t for t in only_t["trades"] if t["why"].startswith("stop")]
    assert tail and all(t["ret"] < -9.0 for t in tail), \
        f"targets alone moved the stop; it must stay at -10%: {tail}"
    # stop_to alone must move the stop, and must NOT sell anything
    assert not [t for t in only_s["trades"] if t["why"] == "partial"], \
        "stop_to alone sold a partial; it must only move the stop"
    moved = [t for t in only_s["trades"] if t["why"] == "stop-moved"]
    assert moved and all(t["ret"] > -2.0 for t in moved), moved
    # and one position must still be ONE row when nothing is laddered
    assert len({t["pid"] for t in only_s["trades"]}) == len(only_s["trades"])
    assert all(t["ret"] < -9.0 for t in base["trades"]), base["trades"][:2]
    assert sc["equity"] > base["equity"], (sc["equity"], base["equity"])
    # A partial is a real order: it must pay its own way, not ride for free.
    assert all(t["cost_pct"] > 0 for t in parts), "partial sell paid no costs"
    print("simulate selftest ok (scale-out verified; rest shared with "
          "portfolio/clusters)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        sys.exit()
    from datetime import datetime as _dt
    BATCH = _dt.now().strftime("%Y%m%d-%H%M")
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"CLUSTER BOOK SIMULATIONS  {days[300]} .. {days[-1]}  "
          f"(Rs {selection.CAPITAL:,})")
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
                              take_per_cluster={"micro": 4, "small": 4}))
    report("cap 2/bucket", run(corpus, days, cluster_cap=2))
    report("small+mid only", run(corpus, days,
                                 take_per_cluster={"small": 5}))


# ---------------------------------------------------------------- keep/promote
STRATS = paths.DATA / "strategies.jsonl"

# A configuration is worth paper-trading only if it survives all four. Positive
# CAGR alone is what a search returns by construction -- out of N variants the
# best few are profitable whether or not anything real is there. The drawdown
# and trade-count bars are what stop the store filling with lucky 20-trade runs.
KEEP_CAGR, KEEP_DD, KEEP_N, KEEP_WIN = 5.0, 55.0, 150, 30.0


def keep(name, r, params, *, batch=None, note="", track="cluster"):
    """Store a configuration that cleared the promotion bar, with the exact
    parameters needed to replay it in the paper bucket.

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
    it is a shortlist of already-filtered configurations, and the paper bucket
    still only ever runs one of them at a time.
    """
    rows = load_strats()
    return max(rows, key=lambda r: r["cagr"]) if rows else None
