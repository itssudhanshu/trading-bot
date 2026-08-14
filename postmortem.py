#!/usr/bin/env python3
"""Deterministic aggregation over search results and the live journal.

The split matters: this computes, an LLM interprets. Aggregation is arithmetic
and must be reproducible; turning "hold=60 doubles the target hit rate" into a
lesson is judgement. An LLM trawling raw JSON would do the arithmetic worse and
the judgement no better.

Output feeds lessons.md. Everything here is TRAIN-ONLY and is the maximum of
many trials -- see lessons.md L7. Nothing printed by this file is evidence of
edge; it is evidence about the SHAPE of the search space.
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CANDIDATES = ROOT / "data" / "candidates.jsonl"


def load(path=None):
    p = path or CANDIDATES
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _med(xs):
    return statistics.median(xs) if xs else float("nan")


def group_by(rows, keyfn):
    g = defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(r)
    return dict(g)


def table(rows, keyfn, label):
    g = group_by(rows, keyfn)
    out = [f"\n{label}", f"  {'value':<22} {'n':>4} {'medExp':>9} {'medTgt%':>8} {'medN':>6} {'best':>9}"]
    for k in sorted(g, key=str):
        rs = g[k]
        out.append(f"  {str(k):<22} {len(rs):>4} "
                   f"{_med([r['expectancy_after_costs'] for r in rs]):>+9,.0f} "
                   f"{_med([r.get('target_hit_rate', 0) for r in rs])*100:>7.1f}% "
                   f"{_med([r['n_trades'] for r in rs]):>6.0f} "
                   f"{max(r['expectancy_after_costs'] for r in rs):>+9,.0f}")
    return "\n".join(out)


def report(rows):
    if not rows:
        return "no candidates: run generator.py first"
    out = [f"candidates evaluated: {len(rows)}"]

    pos = [r for r in rows if r["expectancy_after_costs"] > 0]
    out.append(f"positive train expectancy: {len(pos)}/{len(rows)} "
               f"({len(pos)/len(rows)*100:.0f}%)")

    hits = [r.get("target_hit_rate", 0) for r in rows]
    out.append(f"target hit rate: median {_med(hits)*100:.1f}%  max {max(hits)*100:.1f}%")
    dead = sum(1 for h in hits if h < 0.02)
    out.append(f"specs reaching target <2% of the time: {dead}/{len(rows)}")

    out.append(table(rows, lambda r: r["spec"]["setup"], "by setup family"))
    out.append(table(rows, lambda r: r["spec"]["stop"]["rule"], "by stop rule"))
    out.append(table(rows, lambda r: r["spec"]["target"]["rule"], "by target rule"))
    out.append(table(rows, lambda r: r["spec"]["hold"]["max_bars"], "by holding horizon (bars)"))

    # L1 directly: does a longer horizon make the target reachable?
    byhold = group_by(rows, lambda r: r["spec"]["hold"]["max_bars"])
    if len(byhold) > 1:
        pairs = sorted((h, _med([r.get("target_hit_rate", 0) for r in rs]))
                       for h, rs in byhold.items())
        lo, hi = pairs[0], pairs[-1]
        out.append(f"\nL1 test -- horizon vs target reachability:")
        out.append(f"  hold={lo[0]:>3} bars -> {lo[1]*100:.1f}% hit    "
                   f"hold={hi[0]:>3} bars -> {hi[1]*100:.1f}% hit")
        out.append("  " + ("longer horizon helps reachability"
                           if hi[1] > lo[1] * 1.5 else
                           "horizon does NOT rescue the 3R target"))

    out.append("\nreminder: train-only, max-of-many-trials. Not evidence of edge (L7).")
    return "\n".join(out)


def journal_summary(path=None):
    import engine
    j = engine.Journal(path) if path else engine.Journal()
    rej = j.reject_counts()
    closed = j.positions("closed")
    lines = [f"\njournal: {len(closed)} closed, {len(j.positions('open'))} open, "
             f"{len(j.positions('pending'))} pending"]
    if rej:
        lines.append("  gate rejections: " + ", ".join(f"{k}={v}" for k, v in
                                                       sorted(rej.items(), key=lambda x: -x[1])[:6]))
    if closed:
        lines.append(f"  realised P&L: Rs {j.realised_pnl():+,.0f}")
        by = defaultdict(int)
        for p in closed:
            by[p["exit_reason"]] += 1
        lines.append(f"  exits: {dict(by)}")
    return "\n".join(lines)


def _selftest():
    rows = [
        {"spec": {"setup": "vcp", "stop": {"rule": "atr"}, "target": {"rule": "r_multiple"},
                  "hold": {"max_bars": 10}}, "expectancy_after_costs": -100.0,
         "n_trades": 120, "target_hit_rate": 0.01},
        {"spec": {"setup": "vcp", "stop": {"rule": "swing_low"}, "target": {"rule": "r_multiple"},
                  "hold": {"max_bars": 60}}, "expectancy_after_costs": 50.0,
         "n_trades": 200, "target_hit_rate": 0.20},
    ]
    r = report(rows)
    assert "candidates evaluated: 2" in r
    assert "positive train expectancy: 1/2" in r
    assert "longer horizon helps reachability" in r, r
    assert "Not evidence of edge" in r, "the caveat must always be printed"
    assert report([]).startswith("no candidates")

    # the ceiling case: horizon does not help
    flat = [dict(rows[0]), dict(rows[1])]
    flat[1] = {**rows[1], "target_hit_rate": 0.01}
    assert "does NOT rescue" in report(flat)
    print("postmortem selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(report(load()))
        try:
            print(journal_summary())
        except Exception as e:
            print(f"\n(journal unavailable: {e})")
