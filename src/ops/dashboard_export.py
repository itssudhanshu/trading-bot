#!/usr/bin/env python3
"""Export one JSON snapshot of every recorded number the dashboard shows.

The dashboard is a static React build; it renders ONLY what this file read
from disk at export time. Nothing numeric is hardcoded in the frontend, and
nothing is restated here from memory: overview.state()/gates()/direction()
are reused verbatim, positions come from data/positions.db, backtests from
data/breakout/strategies.jsonl, the studied-trade ledger from
trade_features.jsonl. A missing source file raises -- a snapshot with a
silent zero in it is worse than no snapshot.

Usage:
    python3 src/ops/dashboard_export.py            # write snapshot.json
    python3 src/ops/dashboard_export.py --stdout   # print instead of write
    python3 src/ops/dashboard_export.py --selftest
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone

from paths import DATA as D, SDATA, ROOT

OUT = ROOT / "dashboard" / "public" / "data" / "snapshot.json"

POS_FIELDS = ["id", "symbol", "cluster", "status", "queued_on", "entry_day",
              "entry_px", "qty", "stop", "target", "exit_day", "exit_px",
              "exit_reason", "net", "origin", "bucket"]

CURVE_POINTS = 400


def _read_jsonl(p):
    return ([json.loads(l) for l in p.read_text().splitlines() if l.strip()]
            if p.exists() else [])


def _positions():
    """Both books from the live order book, split by status."""
    if not (D / "positions.db").exists():
        return {}
    con = sqlite3.connect(D / "positions.db")
    con.row_factory = sqlite3.Row
    rows = con.execute("select * from pos").fetchall()
    con.close()
    books = defaultdict(lambda: {"open": [], "pending": [], "closed": [],
                                 "void": []})
    for r in rows:
        b = r["bucket"] or "main"
        rec = {k: r[k] for k in POS_FIELDS}
        books[b][r["status"]].append(rec)
    for b in books:
        for st in books[b]:
            books[b][st].sort(key=lambda x: (x.get("entry_day") or "", x["id"]))
    return dict(books)


def _book_summary(books):
    """Counts and realised net per book. The pool is shown and never counted;
    only `main` contributes the headline realised figure."""
    s = {}
    for name, b in books.items():
        closed = [p for p in b["closed"] if p.get("net") is not None]
        s[name] = {
            "open": len(b["open"]), "pending": len(b["pending"]),
            "closed": len(b["closed"]), "void": len(b["void"]),
            "net": sum(p.get("net") or 0.0 for p in closed),
            "wins": sum(1 for p in closed if (p.get("net") or 0) > 0),
        }
    return s


def _backtests():
    """Newest row per (variant, batch) pair -- an older row for the same
    variant is a different engine run, not another candidate (the overview.py
    rule) -- plus every row grouped by batch so sweeps and lesson figures stay
    file-sourced."""
    rows = [r for r in _read_jsonl(SDATA / "strategies.jsonl")
            if r.get("status") == "candidate"]
    keep = ("variant", "batch", "at", "cagr", "maxdd", "n", "win", "params",
            "track", "note")
    newest = {}
    for r in sorted(rows, key=lambda r: r.get("at", "")):
        newest[(r.get("variant"), r.get("batch"))] = r
    variants = sorted(newest.values(),
                      key=lambda r: (r.get("batch", ""), r.get("at", "")))
    batches = defaultdict(list)
    for r in sorted(rows, key=lambda r: r.get("at", "")):
        batches[r.get("batch", "?")].append(r)
    slim = lambda rs: [{k: r.get(k) for k in keep} for r in rs]
    return slim(variants), {k: slim(v) for k, v in sorted(batches.items())}


def _agg(group):
    rs = [x.get("ret") for x in group if x.get("ret") is not None]
    return {"n": len(rs),
            "avg": sum(rs) / len(rs) if rs else None,
            "win": (sum(1 for r in rs if r > 0) / len(rs)) if rs else None}


def _trades():
    """Aggregates over the studied-trade ledger: research samples (entry
    features plus outcome), not forward fills."""
    rows = _read_jsonl(SDATA / "trade_features.jsonl")
    n = len(rows)
    if not n:
        return {"n": 0}

    # No score-vs-return aggregation here on purpose: score is a percentile
    # WITHIN a cluster, so bucketing the whole blended ledger by score compares
    # numbers nothing makes comparable, and rules.md forbids reporting a blend.
    # The selection claim lives in the rank-cohort test; its evidence travels
    # verbatim inside gates[].
    by_exit, by_cluster = defaultdict(list), defaultdict(list)
    for x in rows:
        by_exit[x.get("exit") or "?"].append(x)
        by_cluster[x.get("bucket") or "?"].append(x)

    curve = []
    ordered = sorted((x for x in rows if x.get("date")), key=lambda x: x["date"])
    cum, step = 0.0, -(-len(ordered) // CURVE_POINTS)  # ceil: caps point count
    for i, x in enumerate(ordered):
        cum += x.get("ret") or 0.0
        if i % step == 0 or i == len(ordered) - 1:
            curve.append({"date": x["date"], "cum": round(cum, 2)})

    return {"n": n,
            "win": sum(1 for x in rows if (x.get("ret") or 0) > 0) / n,
            "avg": sum(x.get("ret") or 0 for x in rows) / n,
            "by_exit": {k: _agg(v) for k, v in sorted(by_exit.items())},
            "by_cluster": {k: _agg(v) for k, v in sorted(by_cluster.items())},
            "curve": curve,
            # Per-trade slice so the UI can recompute every aggregate under its
            # own filters. Only the fields the filters touch -- features stay
            # in trade_features.jsonl.
            "rows": [{"date": x.get("date"), "ret": x.get("ret"),
                      "exit": x.get("exit"), "bucket": x.get("bucket")}
                     for x in ordered]}


REQUIRED_TOP = ["generated_at", "as_of", "approach", "direction", "gates",
                "books", "positions", "backtests", "batches", "trades",
                "occupancy"]
REQUIRED_APPROACH = ["mix", "capital", "deploy", "trigger", "stop", "target",
                     "hold", "days", "span"]


def validate(snap):
    """Schema assertions; refuse to emit anything that fails them."""
    missing = [k for k in REQUIRED_TOP if k not in snap]
    assert not missing, f"snapshot missing keys: {missing}"
    miss_a = [k for k in REQUIRED_APPROACH if k not in snap["approach"]]
    assert not miss_a, f"approach missing keys: {miss_a}"
    assert snap["direction"]["verdict"] and snap["direction"]["reasons"]
    assert isinstance(snap["gates"], list) and snap["gates"], "no gates"
    assert all(set(g) >= {"name", "verdict", "evidence"} for g in snap["gates"])
    assert isinstance(snap["backtests"], list)
    assert isinstance(snap["batches"], dict)
    t = snap["trades"]
    if t.get("n"):
        assert set(t) >= {"win", "avg", "by_exit", "by_cluster", "curve",
                          "rows"}
        assert len(t["curve"]) <= CURVE_POINTS + 1
        assert len(t["rows"]) == t["n"], "rows must cover the ledger"


def export():
    import overview
    s = overview.state()
    gates = overview.gates(s)
    verdict, reasons = overview.direction(s)

    books_raw = _positions()
    variants, batches = _backtests()
    findings = _read_jsonl(SDATA / "findings.jsonl")
    occ = json.loads((SDATA / "occupancy_baseline.json").read_text()) \
        if (SDATA / "occupancy_baseline.json").exists() else {}

    snap = {
        "generated_at":
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": str(date.today()),
        "approach": {k: s[k] for k in
                     ("mix", "tradeable", "capital", "deploy", "trigger",
                      "stop", "target", "hold", "days", "span", "gaps",
                      "n_sims", "sims_positive", "n_learn", "weights")},
        "bucket_counts": s["bucket"],
        "direction": {"verdict": verdict, "reasons": reasons},
        "gates": [{"name": nm, "verdict": v, "evidence": e}
                  for nm, v, e in gates],
        "books": _book_summary(books_raw),
        "positions": books_raw,
        "backtests": variants,
        "batches": batches,
        "trades": _trades(),
        "latest_finding": findings[-1] if findings else None,
        "occupancy": occ,
    }
    validate(snap)
    return snap


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        good = {
            "generated_at": "now", "as_of": "2026-01-01",
            "approach": {"mix": {"micro": 3}, "capital": 3e5, "deploy": 75.0,
                         "trigger": "breakout", "stop": 10.0, "target": 20.0,
                         "hold": 10, "days": 100, "span": ("a", "b")},
            "direction": {"verdict": "TOO EARLY TO SAY", "reasons": ["r"]},
            "gates": [{"name": "g", "verdict": "PASS", "evidence": "e"}],
            "books": {}, "positions": {}, "backtests": [], "batches": {},
            "trades": {"n": 0}, "occupancy": {},
        }
        validate(good)
        bad = dict(good)
        del bad["trades"]
        try:
            validate(bad)
        except AssertionError:
            pass
        else:
            raise SystemExit("validate() accepted a broken snapshot")
        print("dashboard_export selftest ok")
        return

    snap = export()
    text = json.dumps(snap, indent=1, sort_keys=False)
    if args.stdout:
        print(text)
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text + "\n")
        kb = OUT.stat().st_size / 1024
        print(f"wrote {OUT} ({kb:.0f} KB), generated_at {snap['generated_at']}")


if __name__ == "__main__":
    main()
