#!/usr/bin/env python3
"""Per-stock and per-bucket attribution.

Per-stock numbers are reported but must not be used to pick stocks. A symbol
contributes one or two trades over six years, so its win rate is noise; acting
on it would teach the book to chase whatever got lucky. Features recur across
thousands of trades and can be measured -- symbols cannot. What per-stock data
IS good for is concentration: if a handful of names produced the entire
result, the strategy is one lucky streak wearing a track record.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

import paths
import statistics
from collections import defaultdict


def per_stock(trades):
    """-> [{symbol, n, total, avg, wins}] sorted by total contribution."""
    by = defaultdict(list)
    for t in trades:
        sym = t.get("sym") or t.get("symbol")
        if sym:
            by[sym].append(t["ret"])
    out = [{"symbol": s, "n": len(v), "total": sum(v),
            "avg": statistics.fmean(v), "wins": sum(1 for x in v if x > 0)}
           for s, v in by.items()]
    return sorted(out, key=lambda r: -r["total"])


def per_cluster(trades):
    by = defaultdict(list)
    for t in trades:
        by[t.get("clu") or t.get("cluster") or "?"].append(t["ret"])
    return {c: {"n": len(v), "total": sum(v), "avg": statistics.fmean(v),
                "wins": sum(1 for x in v if x > 0)} for c, v in by.items()}


def concentration(trades):
    """How much of the total gain came from the best few names?

    A strategy whose entire P&L sits in 3 of 300 symbols has not been shown to
    work; it has been shown to have held three good stocks.
    """
    rows = per_stock(trades)
    gains = [r["total"] for r in rows if r["total"] > 0]
    total = sum(gains)
    if not total:
        return {"n_symbols": len(rows), "top1": 0.0, "top3": 0.0,
                "top10pct": 0.0, "winners": 0}
    gains.sort(reverse=True)
    k = max(1, len(rows) // 10)
    return {"n_symbols": len(rows),
            "top1": gains[0] / total * 100,
            "top3": sum(gains[:3]) / total * 100,
            "top10pct": sum(gains[:k]) / total * 100,
            "winners": len(gains)}


def report(trades, label=""):
    L = [f"ATTRIBUTION {label}".rstrip(), "=" * 58, ""]
    c = concentration(trades)
    L.append(f"  {len(trades)} trades across {c['n_symbols']} symbols "
             f"({c['winners']} profitable)")
    L.append(f"  share of all gains from the single best symbol : {c['top1']:.1f}%")
    L.append(f"  from the best 3 symbols                        : {c['top3']:.1f}%")
    L.append(f"  from the best 10% of symbols                   : {c['top10pct']:.1f}%")
    L.append("")
    L.append("  BY CLUSTER")
    for cl, v in sorted(per_cluster(trades).items()):
        L.append(f"    {cl:<7} n={v['n']:<5} total {v['total']:>+9.1f}%  "
                 f"avg {v['avg']:>+6.2f}%  win {v['wins']/max(v['n'],1)*100:>3.0f}%")
    rows = per_stock(trades)
    L += ["", "  BEST 8 SYMBOLS"]
    for r in rows[:8]:
        L.append(f"    {r['symbol']:<12} n={r['n']:<3} total {r['total']:>+8.1f}%  "
                 f"avg {r['avg']:>+6.2f}%")
    L += ["", "  WORST 8 SYMBOLS"]
    for r in rows[-8:]:
        L.append(f"    {r['symbol']:<12} n={r['n']:<3} total {r['total']:>+8.1f}%  "
                 f"avg {r['avg']:>+6.2f}%")
    L += ["", "  Per-symbol figures are for review only. With one or two trades",
          "  per name they carry no predictive weight and must not feed selection."]
    return "\n".join(L)


def _selftest():
    t = [{"sym": "A", "ret": 10.0, "clu": "micro"},
         {"sym": "A", "ret": -2.0, "clu": "micro"},
         {"sym": "B", "ret": 5.0, "clu": "small"},
         {"sym": "C", "ret": -3.0, "clu": "small"}]
    ps = per_stock(t)
    assert ps[0]["symbol"] == "A" and ps[0]["n"] == 2, ps[0]
    assert abs(ps[0]["total"] - 8.0) < 1e-9
    c = concentration(t)
    assert abs(c["top1"] - 8.0 / 13.0 * 100) < 1e-6, c
    assert c["n_symbols"] == 3 and c["winners"] == 2, c
    pc = per_cluster(t)
    assert pc["micro"]["n"] == 2 and abs(pc["micro"]["total"] - 8.0) < 1e-9
    assert set(pc) <= {"micro", "small"}, pc
    # a book whose gains all sit in one name must report 100%
    assert concentration([{"sym": "X", "ret": 5.0, "clu": "m"}])["top1"] == 100.0
    print("analysis selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()


# ------------------------------------------------------------------ findings
FINDINGS = paths.DATA / "findings.jsonl"


def record(label, trades, extra=None):
    """Append one dated finding: what the book did, and what it means.

    Append-only. A finding recorded under one set of rules is not comparable to
    one recorded under another, so each row carries the configuration that
    produced it rather than assuming today's constants applied.
    """
    import json
    from datetime import datetime
    import portfolio
    c = concentration(trades)
    row = {"at": datetime.now().isoformat(timespec="seconds"),
           "label": label,
           "config": {"mix": dict(portfolio.TAKE_PER_CLUSTER),
                      "capital": portfolio.CAPITAL,
                      "deploy_pct": portfolio.DEPLOY_PCT,
                      "trigger": portfolio.TRIGGER,
                      "stop": portfolio.STOP_PCT, "target": portfolio.TARGET_PCT,
                      "hold": portfolio.HOLD_DAYS},
           "n": len(trades),
           "by_cluster": {k: {"n": v["n"], "total": round(v["total"], 2),
                              "avg": round(v["avg"], 3), "wins": v["wins"]}
                          for k, v in per_cluster(trades).items()},
           "concentration": {k: round(v, 2) for k, v in c.items()},
           "stats": {k: (round(v, 3) if isinstance(v, float) else v)
                     for k, v in stats(trades).items()},
           "occupancy_baseline": (load_occupancy() or {}).get("mean"),
           "top": [{"symbol": r["symbol"], "n": r["n"], "total": round(r["total"], 2)}
                   for r in per_stock(trades)[:5]],
           "bottom": [{"symbol": r["symbol"], "n": r["n"], "total": round(r["total"], 2)}
                      for r in per_stock(trades)[-5:]]}
    if extra:
        row.update(extra)
    FINDINGS.parent.mkdir(parents=True, exist_ok=True)
    with FINDINGS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def load_findings(limit=None):
    import json
    if not FINDINGS.exists():
        return []
    rows = [json.loads(l) for l in FINDINGS.read_text().splitlines() if l.strip()]
    return rows[-limit:] if limit else rows


OCC_BASELINE = paths.DATA / "occupancy_baseline.json"


def save_occupancy(dist, mean, config):
    """Store how often the book holds n stocks, historically.

    Without this, a book showing one stock looks broken. It is not: one stock
    is the normal state 14% of the time. The baseline is what turns "we only
    hold 1" into "we hold 1, which happens about one session in seven".
    """
    import json
    OCC_BASELINE.parent.mkdir(parents=True, exist_ok=True)
    OCC_BASELINE.write_text(json.dumps(
        {"dist": {str(k): v for k, v in dist.items()}, "mean": round(mean, 2),
         "config": config}, indent=1))
    return OCC_BASELINE


def load_occupancy():
    import json
    if not OCC_BASELINE.exists():
        return None
    d = json.loads(OCC_BASELINE.read_text())
    d["dist"] = {int(k): v for k, v in d["dist"].items()}
    return d


# ------------------------------------------------------------------ error bars
# Mean return per trade in the historical baseline, and the spread around it.
# The reference point for "how many trades before this means anything".
BACKTEST_EDGE = 3.07
TRADE_SD = 16.0


def stats(trades):
    """-> mean return per trade, its standard error, 95% CI, t, n.

    A result without an error bar is a number, not a finding. Re-checking this
    project's own design decisions showed all three were inside their noise
    while looking like 3-point CAGR gaps, so live results get the error bar
    from the first trade rather than after someone asks.
    """
    import math
    import statistics as st
    rets = [t["ret"] for t in trades if t.get("ret") is not None]
    n = len(rets)
    if n < 2:
        return {"n": n, "mean": rets[0] if rets else 0.0, "se": None,
                "lo": None, "hi": None, "t": None, "significant": False}
    m = st.fmean(rets)
    se = st.pstdev(rets) / math.sqrt(n)
    t = m / se if se else 0.0
    return {"n": n, "mean": m, "se": se, "lo": m - 1.96 * se, "hi": m + 1.96 * se,
            "t": t, "significant": abs(t) > 1.96}


def trades_needed(edge_pct, sd_pct=TRADE_SD):
    """-> how many trades before an edge of `edge_pct` per trade is detectable.

    16% is the measured standard deviation of trade returns in this book. The
    answer is large and that is the point: a handful of good weeks proves
    nothing, and knowing the number up front stops us reading one.
    """
    if not edge_pct:
        return None
    return int((1.96 * sd_pct / abs(edge_pct)) ** 2) + 1


def verdict(trades, sd_pct=TRADE_SD):
    """-> one plain sentence about what the results so far can support."""
    s = stats(trades)
    if s["n"] < 2:
        return f"{s['n']} trade so far. Nothing can be concluded."
    if s["significant"]:
        d = "profitable" if s["mean"] > 0 else "losing"
        return (f"{s['n']} trades, {s['mean']:+.2f}% each "
                f"[{s['lo']:+.2f}, {s['hi']:+.2f}] — measurably {d}.")
    # Quote the sample size against the BACKTEST's edge, not the observed one.
    # A lucky first five trades averaging +6.6% would otherwise report "23
    # trades to tell", which flatters exactly the sample that needs caution.
    need = trades_needed(BACKTEST_EDGE, sd_pct)
    tail = (f" At the backtest's {BACKTEST_EDGE:.1f}% edge it takes about "
            f"{need} trades to tell.")
    return (f"{s['n']} trades, {s['mean']:+.2f}% each "
            f"[{s['lo']:+.2f}, {s['hi']:+.2f}] — inside the noise.{tail}")
